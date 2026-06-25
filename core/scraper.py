"""
Scraper 模組（向後相容層）

此模組封裝了新的核心爬蟲模組，並提供與舊版 API 完全相容的介面。
包含 smart_search 等高階搜尋邏輯。
"""
import re
import time
from pathlib import Path

from core.logger import get_logger
from core.config import load_config

logger = get_logger(__name__)
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional, List, Dict, Union, Any, Callable, Type

# 引入新版爬蟲模組
from core.scrapers import (
    JavBusScraper, JAV321Scraper, JavDBScraper,
    MissAVScraper,
    FC2Scraper, AVSOXScraper,
    D2PassScraper, HEYZOScraper, TokyoHotScraper, DMMScraper,
    ThePornDBScraper,
    Video, ScraperConfig, BaseScraper
)
from core.scrapers.utils import (
    extract_number as _new_extract_number,
    FUZZY_SEARCH_SOURCES,
    METATUBE_CENSORED,
    METATUBE_DATE_UNCENSORED,
    METATUBE_UNCENSORED,
)
from core.filename_identity import build_source_queries, parse_media_identity
from core.maker_mapping import get_maker_by_prefix, load_prefix_mapping, normalize_maker_name
from core.source_merger import merge_results
from core.metadata_corrections import apply_metadata_overrides
from core.source_config import validate_source_id
from core.source_settings import get_enabled_source_ids, get_all_source_ids_ordered

# 63c metatube routing imports（CD-63c-1 / CD-63c-2 / CD-63c-3）
from core.metatube.client import MetatubeHttpClient, pick_movie_result
from core.metatube.mapper import map_movie_info
from core.metatube.state import metatube_state
from core.metatube.errors import MetatubeUnavailable, MetatubeNotFound, MetatubeAuthError


# ============ 全域設定 ============

MAX_WORKERS = 2
REQUEST_DELAY = 0.3

# 爬蟲優先順序
# 角色降級（TASK-61a-3）：auto fan-out 已改讀 get_enabled_source_ids()，
# explicit dispatch 已改用 SOURCE_TO_SCRAPER map。此常數目前已無呼叫者（dead），
# 依 plan-61 61a-3 DoD 保留為 legacy/fallback 參照，不再是 search_jav() 的 routing 來源。
SCRAPER_CLASSES: List[Type[BaseScraper]] = [
    JavBusScraper, JAV321Scraper, JavDBScraper, MissAVScraper,
    FC2Scraper, AVSOXScraper,
    D2PassScraper, HEYZOScraper,
]

# JavBus 語系對應表（zh-CN 無簡中版，沿用繁中 zh-tw）
_LOCALE_TO_JAVBUS = {"zh-TW": "zh-tw", "zh-CN": "zh-tw", "ja": "ja", "en": "en"}


_AVSOX_UNCENSORED_PREFIXES = {"MKBD", "MKD"}


def _is_avsox_uncensored_number(value: str) -> bool:
    text = str(value or "").strip().upper()
    match = re.fullmatch(r"([A-Z]{2,7})-[A-Z]\d{2,4}", text)
    return bool(match and match.group(1) in _AVSOX_UNCENSORED_PREFIXES)


def _get_javbus_lang() -> str:
    """從 config 讀取 locale 並轉換為 JavBus lang code"""
    try:
        config = load_config()
        locale = config.get('general', {}).get('locale', 'zh-TW')
        return _LOCALE_TO_JAVBUS.get(locale, "zh-tw")
    except Exception as e:
        logger.warning("[i18n] 讀取 locale config 失敗，使用預設語系: %s", e)
        return "zh-tw"


# ============ 輔助函數 (與舊版相容) ============

def extract_number(filename: str) -> Optional[str]:
    """從檔名提取番號 (Delegate to new utils)"""
    return _new_extract_number(filename)


def normalize_number(number: str) -> str:
    """標準化番號格式"""
    from core.filename_identity import normalize_work_number

    normalized = normalize_work_number(number)
    if normalized:
        return normalized
    return JavBusScraper().normalize_number(number)


def is_number_format(s: str) -> bool:
    """判斷是否為完整番號格式 (如 SONE-001, ABC-123, SONE-103-UC)"""
    s = s.strip()
    # 清理常見後綴（需有分隔符，避免誤刪 JUC-123 等合法前綴）
    s = re.sub(
        r'[-_](UC|UNCEN|UNCENSORED|LEAK|LEAKED)(?=[-_.\s]|$)',
        '', s, flags=re.IGNORECASE
    )
    identity = parse_media_identity(s)
    if identity.canonical_number and _is_avsox_uncensored_number(identity.canonical_number):
        return True
    return bool(re.match(r'^[a-zA-Z]+-?\d{3,}$', s))


def is_partial_number(s: str) -> bool:
    """判斷是否為部分番號 (如 SONE-0, IPZZ-03)"""
    match = re.match(r'^([a-zA-Z]+)-?(\d{1,2})$', s.strip())
    return bool(match)


def is_prefix_only(s: str) -> bool:
    """判斷是否為純前綴 (如 IPZZ, SONE)"""
    s = s.strip().upper()
    return bool(re.match(r'^[A-Z]{2,6}$', s))


def sort_results_by_date(results: List[Dict[str, Any]], reverse: bool = True) -> List[Dict[str, Any]]:
    """按發行日期排序搜尋結果"""
    def sort_key(item: Dict[str, Any]) -> tuple[str, str]:
        date = str(item.get('date', '') or '0000-00-00')
        number = str(item.get('number', ''))
        return (date, number)

    return sorted(results, key=sort_key, reverse=reverse)


def expand_partial_number(partial: str) -> List[str]:
    """展開部分番號"""
    match = re.match(r'^([a-zA-Z]+)-?(\d+)$', partial.strip())
    if not match:
        return [partial]

    prefix, num = match.groups()
    prefix = prefix.upper()

    if len(num) >= 3:
        return [f"{prefix}-{num}"]

    candidates = []
    for i in range(10):
        full_num = num + str(i)
        while len(full_num) < 3:
            full_num = '0' + full_num
        candidates.append(f"{prefix}-{full_num}")
    return candidates


# ============ 63c metatube internal carrier keys + strip helper ============

_INTERNAL_NFO_KEYS = ('_summary', '_rating', '_actress_aliases', '_actress_profiles')


def strip_internal_nfo_keys(result_dict: dict) -> dict:
    """移除 internal NFO carrier 鍵（_summary / _rating），回傳 shallow copy。

    保留 _source / _mode / _all_variant_ids 等前端所需 _ 前綴鍵。
    （spec §161 enforcement，CD-63c-5）
    """
    return {k: v for k, v in result_dict.items() if k not in _INTERNAL_NFO_KEYS}


# ============ 63c _MetatubeShim（CD-63c-3）============

class _MetatubeShim:
    """metatube provider 的 scraper-compatible shim（CD-63c-3）。

    讓 metatube provider 能插入現有 source_to_scraper 架構，
    使用相同的 .search() 介面，不改 search_jav() 的 scraper 迭代邏輯。
    """
    def __init__(self, provider: str, base_url: str, token: str) -> None:
        self.source = f'metatube:{provider}'
        self._provider = provider
        self._client = MetatubeHttpClient(base_url, token)

    def search(self, number: str) -> 'Video | None':
        try:
            results = self._client.search(self._provider, number)
            picked = pick_movie_result(results)
            if not picked:
                return None
            info = self._client.get_info(self._provider, picked['id'])
            if not info:
                return None
            video = map_movie_info(info)
            # routing 期 success → mark available（lazy liveness）
            metatube_state.mark_available(self.source)
            return video
        except MetatubeUnavailable:
            metatube_state.mark_failed(self.source)
            raise
        except MetatubeNotFound:
            # 404 = 番號不在此源 = 不算失敗（spec §5.3 / CD-63a-6）
            return None
        except MetatubeAuthError:
            # Token 錯誤：不 mark_failed（連線層問題，非 provider 問題）
            logger.warning('metatube auth error for %s', self.source)
            return None
        except Exception:
            logger.exception('metatube shim unexpected error for %s', self.source)
            return None


# ============ 核心搜尋函數 ============

def _is_dmm_enabled(proxy_url: str) -> bool:
    """空字串 → False；'direct' / 真 proxy → True"""
    return bool(proxy_url and proxy_url.strip())


def _dmm_proxy_url(proxy_url: str) -> str:
    """'direct'（大小寫不敏感）→ ''（直連）；其他 → 原值"""
    if not proxy_url:
        return ''
    if proxy_url.strip().lower() == 'direct':
        return ''
    return proxy_url


def _get_theporndb_token(config: dict | None = None) -> str:
    if config is None:
        try:
            config = load_config()
        except Exception as exc:
            logger.error("[Search] 讀取 ThePornDB 設定失敗: %s", exc)
            return ""
    for source in config.get("sources", []) or []:
        if not isinstance(source, dict) or source.get("id") != "theporndb":
            continue
        cfg = source.get("config") if isinstance(source.get("config"), dict) else {}
        return str(cfg.get("api_token") or cfg.get("token") or "").strip()
    return ""


def _is_theporndb_keyword_enabled() -> bool:
    try:
        config = load_config()
    except Exception as exc:
        logger.error("[Search] 讀取 ThePornDB 設定失敗: %s", exc)
        return False
    if not _get_theporndb_token(config):
        return False
    for source in config.get("sources", []) or []:
        if not isinstance(source, dict) or source.get("id") != "theporndb":
            continue
        return bool(source.get("enabled")) and not bool(source.get("manual_only")) and not bool(source.get("is_beta"))
    return False


VALID_JAVBUS_LANGS = {'zh-tw', 'ja', 'en'}


def _search_query_options(raw_number: str, canonical_number: str) -> tuple:
    """Return (identity, try_all_aliases, max_queries_per_source)."""
    identity = parse_media_identity(raw_number)
    if not identity.canonical_number:
        identity = parse_media_identity(canonical_number)

    try:
        config = load_config()
        search_cfg = config.get("search", {}) if isinstance(config, dict) else {}
    except Exception as exc:
        logger.error("[Search] 讀取搜尋策略設定失敗: %s", exc)
        search_cfg = {}

    try_all_aliases = bool(search_cfg.get("try_all_aliases", True))
    try:
        max_queries = int(search_cfg.get("max_queries_per_source", 3))
    except (TypeError, ValueError):
        max_queries = 3
    max_queries = max(1, min(max_queries, 10))
    return identity, try_all_aliases, max_queries


def _dedupe_queries(values: list[str]) -> list[str]:
    out: list[str] = []
    for value in values:
        query = str(value or "").strip()
        if query and query not in out:
            out.append(query)
    return out


def _date_style_exact_key(value: str) -> str:
    text = str(value or "").strip().upper()
    match = re.fullmatch(r"(\d{6})([-_])(\d{2,3})", text)
    if not match:
        return ""
    return f"DATE:{match.group(1)}{match.group(2)}{match.group(3)}"


_COMPACT_DISTINCT_PREFIXES = {"RED"}


def _compact_distinct_exact_key(value: str) -> str:
    text = str(value or "").strip().upper()
    match = re.fullmatch(r"([A-Z]{2,7})([-_]?)(\d{2,5})", text)
    if not match or match.group(1) not in _COMPACT_DISTINCT_PREFIXES:
        return ""
    separator = "COMPACT" if not match.group(2) else "SEPARATED"
    return f"{separator}:{match.group(1)}{match.group(2)}{match.group(3)}"


def _number_match_keys(value: str, source_id: str = "") -> set[str]:
    identity = parse_media_identity(value)
    candidates = [
        str(value or "").strip(),
        identity.canonical_number or "",
        identity.search_number or "",
        identity.display_number or "",
        identity.raw_match or "",
    ]
    source = str(source_id or "").lower()
    if source != "d2pass" or not _date_style_exact_key(identity.canonical_number or value):
        candidates.extend(identity.number_aliases)

    keys: set[str] = set()
    for candidate in candidates:
        text = str(candidate or "").strip().upper()
        if not text:
            continue
        date_key = _date_style_exact_key(text)
        if date_key:
            keys.add(date_key)
            continue
        compact_distinct_key = _compact_distinct_exact_key(text)
        if compact_distinct_key:
            keys.add(compact_distinct_key)
        else:
            compact = re.sub(r"[^A-Z0-9]+", "", text)
            if compact:
                keys.add(compact)
    return keys


def _video_number(video) -> str:
    if isinstance(video, dict):
        return str(video.get("number") or "")
    return str(getattr(video, "number", "") or "")


def _video_maker(video) -> str:
    if isinstance(video, dict):
        return str(video.get("maker") or "")
    return str(getattr(video, "maker", "") or "")


def _identity_text_key(value: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "", str(value or "").upper())


_STRICT_PREFIX_MAKER_SOURCES = {"jav321"}


def _video_matches_known_prefix_maker(video, identity, source_id: str) -> bool:
    if str(source_id or "").lower() not in _STRICT_PREFIX_MAKER_SOURCES:
        return True

    number = str(getattr(identity, "canonical_number", "") or "")
    match = re.match(r"^([A-Z]+)", number, flags=re.IGNORECASE)
    if not match:
        return True

    expected = load_prefix_mapping().get(match.group(1).upper(), "")
    actual = _video_maker(video)
    if not expected or not actual:
        return True

    expected_key = _identity_text_key(normalize_maker_name(expected))
    actual_key = _identity_text_key(normalize_maker_name(actual))
    if not expected_key:
        return True
    if not actual_key:
        return False
    if expected_key == actual_key:
        return True
    if len(expected_key) >= 5 and expected_key in actual_key:
        return True
    if len(actual_key) >= 5 and actual_key in expected_key:
        return True
    return False


def _video_matches_numbered_query(video, source_id: str, query: str,
                                  canonical_number: str, identity) -> bool:
    if not getattr(identity, "canonical_number", None):
        return True

    expected_keys = _number_match_keys(query, source_id)
    if not expected_keys:
        expected_keys = _number_match_keys(canonical_number, source_id)
    actual_keys = _number_match_keys(_video_number(video), source_id)
    if not (expected_keys and actual_keys and expected_keys & actual_keys):
        return False
    return _video_matches_known_prefix_maker(video, identity, source_id)


_CENSORED_EXACT_BUILTINS = {"dmm", "javbus", "jav321", "javdb", "missav"}
_FC2_EXACT_BUILTINS = {"fc2", "avsox", "javdb", "missav"}
_HEYZO_EXACT_BUILTINS = {"heyzo", "avsox", "javdb", "missav"}
_DATE_UNCENSORED_EXACT_BUILTINS = {"d2pass", "heyzo", "fc2", "avsox", "javdb", "missav"}
_TOKYO_HOT_EXACT_BUILTINS = {"tokyohot", "d2pass", "javdb", "missav"}
_AVSOX_UNCENSORED_EXACT_BUILTINS = {"avsox", "javdb", "missav"}
_TITLE_EXACT_BUILTINS = {"theporndb", "javdb"}
_FC2_METATUBE_PROVIDERS = {"fc2", "fc2ppvdb", "fc2hub"}
_DATE_METATUBE_PROVIDERS = {p.lower() for p in METATUBE_DATE_UNCENSORED}
_CENSORED_METATUBE_PROVIDERS = {p.lower() for p in METATUBE_CENSORED}
_UNCENSORED_METATUBE_PROVIDERS = {p.lower() for p in METATUBE_UNCENSORED}
_WESTERN_SITE_HINTS = {
    "blacked",
    "brazzers",
    "deeper",
    "digitalplayground",
    "evilangel",
    "mofos",
    "naughtyamerica",
    "private",
    "realitykings",
    "teamskeet",
    "tushy",
    "vixen",
    "wicked",
}
_EXACT_UNCENSORED_KINDS = {"fc2", "heyzo", "date_uncensored", "tokyo_hot", "avsox_uncensored"}


def _query_kind(identity) -> str:
    number = (getattr(identity, "canonical_number", None) or "").upper()
    if not number:
        return "title"
    if number.startswith("FC2-PPV-"):
        return "fc2"
    if number.startswith("HEYZO-"):
        return "heyzo"
    if re.match(r"^[NK]-\d{4}$", number):
        return "tokyo_hot"
    if _is_avsox_uncensored_number(number):
        return "avsox_uncensored"
    if re.match(r"^\d{6}[-_]\d{2,3}$", number):
        return "date_uncensored"
    return "censored"


def _metatube_provider_id(source_id: str) -> str:
    if not source_id.startswith("metatube:"):
        return ""
    return source_id.split(":", 1)[1].strip().lower()


def _source_allowed_for_query_kind(source_id: str, kind: str) -> bool:
    if source_id.startswith("metatube:"):
        provider = _metatube_provider_id(source_id)
        if kind == "fc2":
            return provider in _FC2_METATUBE_PROVIDERS
        if kind == "heyzo":
            return provider == "heyzo"
        if kind == "tokyo_hot":
            return provider == "tokyo-hot"
        if kind == "avsox_uncensored":
            return False
        if kind == "date_uncensored":
            return provider in _DATE_METATUBE_PROVIDERS
        if kind == "title":
            return False
        # Unknown Metatube providers stay available for censored-style exact
        # searches to preserve legacy custom provider behavior.
        return provider in _CENSORED_METATUBE_PROVIDERS or provider not in _UNCENSORED_METATUBE_PROVIDERS

    allowed = {
        "censored": _CENSORED_EXACT_BUILTINS,
        "fc2": _FC2_EXACT_BUILTINS,
        "heyzo": _HEYZO_EXACT_BUILTINS,
        "date_uncensored": _DATE_UNCENSORED_EXACT_BUILTINS,
        "tokyo_hot": _TOKYO_HOT_EXACT_BUILTINS,
        "avsox_uncensored": _AVSOX_UNCENSORED_EXACT_BUILTINS,
        "title": _TITLE_EXACT_BUILTINS,
    }.get(kind, _CENSORED_EXACT_BUILTINS)
    return source_id in allowed


def _filter_auto_sources_for_query(enabled_sids: list[str], identity) -> list[str]:
    """Keep source-mode broad, but route each query to compatible exact sources."""
    kind = _query_kind(identity)
    return [sid for sid in enabled_sids if _source_allowed_for_query_kind(sid, kind)]


def _javdb_available_for_query(query: str) -> bool:
    identity = parse_media_identity(query)
    enabled_sids = get_enabled_source_ids(availability_map=metatube_state.availability_map())
    return "javdb" in _filter_auto_sources_for_query(enabled_sids, identity)


def _source_available_for_query(query: str, source_id: str) -> bool:
    identity = parse_media_identity(query)
    enabled_sids = get_enabled_source_ids(availability_map=metatube_state.availability_map())
    return source_id in _filter_auto_sources_for_query(enabled_sids, identity)


def _try_javdb_first(
    query: str,
    proxy_url: str = '',
    status_callback: Optional[Callable[[str, str], None]] = None,
) -> Optional[Dict[str, Any]]:
    if not _javdb_available_for_query(query):
        return None
    if status_callback:
        status_callback('javdb', 'searching')
    return search_jav(query, source='javdb', proxy_url=proxy_url)


def _try_missav_first(
    query: str,
    proxy_url: str = '',
    status_callback: Optional[Callable[[str, str], None]] = None,
) -> Optional[Dict[str, Any]]:
    if not _source_available_for_query(query, "missav"):
        return None
    if status_callback:
        status_callback('missav', 'searching')
    return search_jav(query, source='missav', proxy_url=proxy_url)


def _looks_western_title_query(query: str) -> bool:
    text = str(query or "").strip()
    if not text:
        return False
    if re.search(r"[\u3040-\u30ff\u3400-\u9fff]", text):
        return False
    tokens = [t.lower() for t in re.findall(r"[a-z0-9]+", text)]
    if len(tokens) < 4:
        return False
    has_site = any(t in _WESTERN_SITE_HINTS for t in tokens)
    has_scene_date = bool(re.search(r"(?<!\d)\d{2}[._ -]\d{2}[._ -]\d{2}(?!\d)", text))
    return has_site or has_scene_date


def _queries_for_source(identity, source_id: str, canonical_number: str,
                        raw_number: str,
                        try_all_aliases: bool, max_queries: int) -> list[str]:
    if source_id == "theporndb":
        return _dedupe_queries([
            raw_number,
            getattr(identity, "raw_stem", ""),
            canonical_number,
        ])[:max_queries]
    if source_id == "javdb" and not identity.canonical_number:
        return _dedupe_queries([
            raw_number,
            getattr(identity, "raw_stem", ""),
        ])[:max_queries]
    if source_id == "missav" and not identity.canonical_number:
        return _dedupe_queries([
            raw_number,
            getattr(identity, "raw_stem", ""),
        ])[:max_queries]
    if try_all_aliases and identity.canonical_number:
        queries = build_source_queries(identity, source_id)
    else:
        queries = [canonical_number]
    if not queries:
        queries = [canonical_number]
    return queries[:max_queries]


def _search_scraper_with_queries(scraper, source_id: str, canonical_number: str,
                                 raw_number_or_identity,
                                 identity=None, try_all_aliases: bool = True,
                                 max_queries: int = 3):
    if identity is None:
        identity = raw_number_or_identity
        raw_number = canonical_number
    else:
        raw_number = raw_number_or_identity
    for query in _queries_for_source(
        identity, source_id, canonical_number, raw_number, try_all_aliases, max_queries
    ):
        video = scraper.search(query)
        if video and _video_matches_numbered_query(video, source_id, query, canonical_number, identity):
            return video
        if video:
            logger.debug(
                "[Search] rejected %s result for %s: returned number %s",
                source_id,
                query,
                _video_number(video),
            )
    return None


def _load_metadata_override_config() -> Optional[dict]:
    try:
        return load_config()
    except Exception:
        logger.warning("[Search] 載入 metadata_overrides 失敗")
        return None


def _override_only_search_result(raw_number: str, number: str) -> Optional[Dict[str, Any]]:
    base: Dict[str, Any] = {
        "number": number,
        "title": "",
        "actors": [],
        "date": "",
        "maker": "",
        "cover": "",
        "tags": [],
        "source": "override",
        "url": "",
        "director": "",
        "duration": None,
        "label": "",
        "series": "",
        "sample_images": [],
    }
    result, override_fields = apply_metadata_overrides(
        base,
        [raw_number, number],
        _load_metadata_override_config(),
    )
    if not override_fields:
        return None
    if not (result.get("title") or result.get("original_title")):
        return None
    if not result.get("date") and result.get("release_date"):
        result["date"] = result["release_date"]
    result["source"] = "override"
    result["_source"] = "override"
    result["_summary"] = ""
    result["_rating"] = None
    result["_actress_aliases"] = {}
    result["_actress_profiles"] = []
    result["_metadata_override_fields"] = override_fields
    return result


def search_jav(number: str, source: str = 'auto', proxy_url: str = '',
               javbus_lang: Optional[str] = None,
               _skip_sources: Optional[set[str]] = None) -> Optional[Dict[str, Any]]:
    """
    搜尋 JAV 資訊（向後相容函數）
    """
    all_data: Dict[str, Video] = {}

    # 標準化番號
    raw_number = number
    number = normalize_number(number)
    identity, try_all_aliases, max_queries = _search_query_options(raw_number, number)

    # 來源 id 驗證（TASK-61a-3）：改用 validate_source_id() 取代舊 VALID_SOURCES set。
    # 'auto' 與 builtin id 通過；其餘 → return None（保留「未知來源不 raise」契約）。
    if not validate_source_id(source):
        logger.warning(f"[Search] 未知來源: {source}")
        return None

    # DMM 需要日本 IP（proxy 或 direct），有啟用才建立
    dmm_config = ScraperConfig(proxy_url=_dmm_proxy_url(proxy_url)) if _is_dmm_enabled(proxy_url) else None

    # javbus_lang 校驗 + config fallback（auto 與 explicit javbus 共用）
    if javbus_lang is not None and javbus_lang not in VALID_JAVBUS_LANGS:
        logger.warning("[Search] 無效的 javbus_lang: %s，fallback 到 config", javbus_lang)
        javbus_lang = None
    _javbus_lang = javbus_lang if javbus_lang is not None else _get_javbus_lang()

    # 來源 id → scraper factory（無參數 callable，回 scraper instance list）。
    # DMM 與 JavBus 是攜帶 closure 參數的特例：
    #   - dmm：proxy-gated，dmm_config 為 None（無 proxy）時回 []（不建立）。
    #   - javbus：帶校驗後的 lang。
    # explicit 指定來源與 auto fan-out 共用同一份定義。
    source_to_scraper = {
        'dmm': lambda: [DMMScraper(dmm_config)] if dmm_config else [],
        'javbus': lambda: [JavBusScraper(lang=_javbus_lang)],
        'jav321': lambda: [JAV321Scraper()],
        'javdb': lambda: [JavDBScraper(ScraperConfig(proxy_url=_dmm_proxy_url(proxy_url)))],
        'missav': lambda: [MissAVScraper(ScraperConfig(proxy_url=_dmm_proxy_url(proxy_url)))],
        'd2pass': lambda: [D2PassScraper()],
        'heyzo': lambda: [HEYZOScraper()],
        'tokyohot': lambda: [TokyoHotScraper()],
        'fc2': lambda: [FC2Scraper()],
        'avsox': lambda: [AVSOXScraper()],
        'theporndb': lambda: [ThePornDBScraper(api_token=_get_theporndb_token())],
    }

    # 63c：動態注入 metatube provider（CD-63c-2）
    # availability_map 的 False entry 仍加進 source_to_scraper——
    # get_enabled_source_ids(availability_map) 已在上一層排除不可達的 source，
    # 不需 double-gate（explicit picker 選當前 probe-failed provider 也應能試打）。
    if metatube_state.is_connected:
        _mt_url = metatube_state.base_url or ''
        _mt_token = metatube_state.token or ''
        for _mt_name, _mt_avail in metatube_state.availability_map().items():
            _mt_provider = _mt_name[len('metatube:'):]
            # 用 default arg 固定 closure variable capture（風險點 a）
            source_to_scraper[_mt_name] = (
                lambda _pname=_mt_provider, _url=_mt_url, _tok=_mt_token:
                    [_MetatubeShim(_pname, _url, _tok)]
            )

    # 決定要跑哪些爬蟲（auto vs. explicit）
    logger.info(f"[Search] {number} 使用來源: {source}")
    if source == 'auto':
        # auto fan-out（CD-63c-4）：
        # - builtin：循序執行（維持既有行為）
        # - metatube：defer 到 ThreadPoolExecutor 並行（bounded parallel fan-out）
        # - 結果以 enabled_sids 順序重建 all_data（保全 user-drag merge 優先度）
        # get_enabled_source_ids 傳入 availability_map 讓 metatube gate 生效（🔴 CRITICAL）
        query_kind = _query_kind(identity)
        enabled_sids = get_enabled_source_ids(availability_map=metatube_state.availability_map())
        enabled_sids = _filter_auto_sources_for_query(enabled_sids, identity)
        if query_kind in _EXACT_UNCENSORED_KINDS:
            uncensored_sids = [
                sid for sid in _get_uncensored_sources(number)
                if _source_allowed_for_query_kind(sid, query_kind)
            ]
            enabled_sids = list(dict.fromkeys([*uncensored_sids, *enabled_sids]))
        skip_sources = set(_skip_sources or set())
        if skip_sources:
            enabled_sids = [sid for sid in enabled_sids if sid not in skip_sources]
        results_by_source: Dict[str, Video] = {}
        metatube_shims = []  # list of (sid, shim) for parallel dispatch

        def run_source(sid: str) -> Optional[Video]:
            factory = source_to_scraper.get(sid)
            if not factory:
                return None
            found: Optional[Video] = None
            for scraper in factory():
                try:
                    scraper_name = scraper.__class__.__name__
                    logger.debug("[Search] trying %s...", scraper_name)
                    v = _search_scraper_with_queries(
                        scraper, sid, number, raw_number, identity, try_all_aliases, max_queries
                    )
                    if v:
                        results_by_source[v.source] = v
                        found = v
                        logger.debug("[Search] %s found result", scraper_name)
                        break
                except Exception as e:
                    logger.debug("[Search] %s error: %s", scraper_name, e)
                    continue
            return found

        standard_fanout_sids = []
        if query_kind in _EXACT_UNCENSORED_KINDS:
            for sid in enabled_sids:
                if run_source(sid):
                    enabled_sids = [sid]
                    break

        priority_sids = () if query_kind in _EXACT_UNCENSORED_KINDS else ('javdb', 'missav')
        for priority_sid in priority_sids:
            if priority_sid not in enabled_sids:
                continue
            priority_result = run_source(priority_sid)
            if priority_result:
                enabled_sids = [priority_sid]
                break
            enabled_sids = [sid for sid in enabled_sids if sid != priority_sid]

        if query_kind not in _EXACT_UNCENSORED_KINDS:
            standard_fanout_sids = enabled_sids

        if results_by_source:
            enabled_sids = [sid for sid in enabled_sids if sid in results_by_source]

        for sid in ([] if results_by_source else standard_fanout_sids):
            factory = source_to_scraper.get(sid)
            if not factory:
                continue
            if sid.startswith('metatube:'):
                metatube_shims.extend((sid, s) for s in factory())  # defer
            else:
                for scraper in factory():  # builtin：循序，維持既有行為
                    try:
                        scraper_name = scraper.__class__.__name__
                        logger.debug(f"[Search] 嘗試 {scraper_name}...")
                        v = _search_scraper_with_queries(
                            scraper, sid, number, raw_number, identity, try_all_aliases, max_queries
                        )
                        if v:
                            results_by_source[v.source] = v
                            logger.debug(f"[Search] {scraper_name} 找到結果")
                    except Exception as e:
                        logger.debug(f"[Search] {scraper_name} 錯誤: {e}")
                        continue

        # metatube subset：bounded parallel
        if metatube_shims:
            with ThreadPoolExecutor(max_workers=min(len(metatube_shims), 5)) as ex:
                futs = [
                    (
                        sid,
                        ex.submit(
                            _search_scraper_with_queries,
                            shim,
                            sid,
                            number,
                            raw_number,
                            identity,
                            try_all_aliases,
                            max_queries,
                        ),
                    )
                    for sid, shim in metatube_shims
                ]
                for sid, fut in futs:  # 按 user order 收（submit 順序 = user order；非 as_completed）
                    try:
                        v = fut.result()
                        if v:
                            results_by_source[v.source] = v
                    except Exception:
                        continue

        # rebuild all_data 按 enabled_sids（user-drag）順序，保全 merge 優先度契約
        # v.source == sid，對 builtin 和 metatube 均成立（mapper 設 source='metatube:{provider}'）
        all_data = {
            sid: results_by_source[sid]
            for sid in enabled_sids
            if sid in results_by_source
        }
    else:
        # explicit 單一來源 dispatch（CD-63c-6）。未知 id 理論上已被 validate_source_id 攔截，
        # factory 缺失時回空 list（行為等同舊 dead-else fallback）。
        factory = source_to_scraper.get(source)
        scrapers = factory() if factory else []

        for scraper in scrapers:
            try:
                scraper_name = scraper.__class__.__name__
                logger.debug(f"[Search] 嘗試 {scraper_name}...")
                video = _search_scraper_with_queries(
                    scraper, source, number, raw_number, identity, try_all_aliases, max_queries
                )
                if video:
                    all_data[video.source] = video
                    logger.debug(f"[Search] {scraper_name} 找到結果")
            except Exception as e:
                logger.debug(f"[Search] {scraper_name} 錯誤: {e}")
                continue

    if not all_data:
        override_result = _override_only_search_result(raw_number, number)
        if override_result:
            logger.info("[Search] %s 使用 metadata override", number)
            return override_result
        logger.info(f"[Search] {number} 無結果")
        return None

    # 合併邏輯（TASK-61a-6 / CD-61-9）：
    # - explicit 單一來源（source != 'auto'）：整包贏，不走 merger（語意顯式化）。
    # - auto fan-out：呼叫 pure merger。封面跟 user_order（CD-plan-65-2）。
    if source != 'auto':
        # 單一來源直通：該來源資料原封不動
        main_video = next(iter(all_data.values()))
    else:
        # auto path: merge winner = first source in Active Row drag-sort order
        # (get_enabled_source_ids order); DMM Top-1 shortcut removed in feature/65.
        user_order = list(all_data.keys())  # already in get_enabled_source_ids() / drag order
        main_video = merge_results(all_data, user_order)

    # 補全 maker
    if not main_video.maker:
        maker = get_maker_by_prefix(number)
        if maker:
            main_video = main_video.model_copy(update={'maker': maker})

    result = main_video.to_legacy_dict()
    result, override_fields = apply_metadata_overrides(
        result,
        [raw_number, number, main_video.number],
        _load_metadata_override_config(),
    )
    result['_source'] = main_video.source  # 保留內部欄位
    result['_summary'] = main_video.summary  # 63c 新增（NFO 用，不入 DB，CD-63c-5）
    result['_rating'] = main_video.rating    # 63c 新增（NFO 用，已排除於 to_legacy_dict）
    result['_actress_aliases'] = main_video.actress_aliases
    result['_actress_profiles'] = main_video.actress_profiles
    if override_fields:
        result['_metadata_override_fields'] = override_fields
    logger.info(f"[Search] {number} 完成，來源: {main_video.source}")
    return result


def search_jav_single_source(number: str, source: str, proxy_url: str = '') -> Optional[Dict[str, Any]]:
    """指定單一來源搜尋"""
    return search_jav(number, source=source, proxy_url=proxy_url)


def search_partial(partial: str,
                   status_callback: Optional[Callable[[str, str], None]] = None,
                   result_callback: Optional[Callable[[int, Any], None]] = None,
                   discovery_only: bool = False) -> List[Dict[str, Any]]:
    """局部搜尋"""
    candidates = expand_partial_number(partial)
    results = []

    if status_callback:
        status_callback('javbus', 'searching')

    # Seed callback: 通知前端準備 skeleton grid
    if candidates and result_callback:
        result_callback(-1, candidates)

    if discovery_only:
        if status_callback:
            status_callback('done', f'found:{len(candidates)}')
        return [{'number': num, 'title': ''} for num in candidates]

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        # 記錄 slot index 以支援 result_callback 正確定位
        futures = {}
        for idx, num in enumerate(candidates):
            future = executor.submit(search_jav, num, 'javbus')
            futures[future] = (idx, num)

        for future in as_completed(futures):
            idx, num = futures[future]
            try:
                data = future.result()
                if data and data.get('title'):
                    results.append(data)
                    if result_callback:
                        result_callback(idx, data)
            except Exception:
                logger.error('search_partial: %s failed', num)
            time.sleep(REQUEST_DELAY)

    if status_callback:
        status_callback('done', f'found:{len(results)}')

    return sort_results_by_date(results)


def search_prefix(prefix: str, limit: int = 20, offset: int = 0, status_callback: Optional[Callable[[str, str], None]] = None, result_callback: Optional[Callable[[int, Any], None]] = None, discovery_only: bool = False) -> List[Dict[str, Any]]:
    """前綴搜尋"""
    results = []
    prefix = prefix.strip().upper()

    if status_callback:
        status_callback('javbus', 'searching')

    try:
        scraper = JavBusScraper(lang=_get_javbus_lang())
        start_page = (offset // 30) + 1
        skip_in_page = offset % 30
        pages_needed = ((limit + skip_in_page) // 30) + 2

        all_ids: List[str] = []
        for page in range(start_page, start_page + pages_needed):
            ids = scraper.get_ids_from_search(prefix, page=page, search_type=1)
            if ids:
                all_ids.extend(ids)
                if len(all_ids) >= limit + skip_in_page:
                    break
            else:
                break

        if not all_ids:
            if status_callback:
                status_callback('javbus', 'found:0')
            return []

        target_ids = all_ids[skip_in_page:][:limit]

        if status_callback:
            status_callback('javbus', f'found:{len(target_ids)}')

        if discovery_only:
            if status_callback:
                status_callback('done', f'found:{len(target_ids)}')
            return [{'number': num, 'title': ''} for num in target_ids]

        if status_callback:
            status_callback('javbus', 'fetching_details')

        if target_ids and result_callback:
            result_callback(-1, target_ids)

        completed_count = 0
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {}
            for idx, num in enumerate(target_ids):
                future = executor.submit(search_jav, num, 'javbus')
                futures[future] = (idx, num)

            for future in as_completed(futures):
                idx, num = futures[future]
                completed_count += 1
                if status_callback:
                    status_callback('javbus', f'details:{completed_count}/{len(target_ids)}')
                try:
                    data = future.result()
                    if data and data.get('title'):
                        results.append(data)
                        if result_callback:
                            result_callback(idx, data)
                except Exception:
                    logger.error('search_prefix: %s failed', num)
                time.sleep(REQUEST_DELAY)

    except Exception as e:
        logger.error('search_prefix failed: %s', e)

    if status_callback:
        status_callback('done', f'found:{len(results)}')

    return sort_results_by_date(results)


def _dmm_keyword_search_progressive(
    dmm_scraper,
    query: str,
    limit: int,
    status_callback,
    result_callback,
    offset: int = 0,
) -> Optional[List[Dict[str, Any]]]:
    """DMM keyword search with progressive enrichment (mirrors JavBus pattern).

    Returns a list of result dicts on success, or None if DMM returned nothing
    (caller should fall through to JavBus).
    """
    pairs = dmm_scraper.search_by_keyword_with_ids(query, limit=limit, offset=offset)
    if not pairs:
        return None

    # Seed: frontend renders skeleton cards immediately
    if result_callback:
        seed_ids = [video.number for _, video in pairs]
        result_callback(-1, seed_ids)

    results = [None] * len(pairs)  # pre-allocate to preserve seed order
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {}
        for idx, (content_id, shallow) in enumerate(pairs):
            future = executor.submit(dmm_scraper._fetch_by_id, content_id)
            futures[future] = (idx, content_id, shallow)

        for future in as_completed(futures):
            idx, content_id, shallow = futures[future]
            try:
                video = future.result()
                if video is None:
                    video = shallow
            except Exception:
                logger.error('DMM enrichment failed: %s', content_id)
                video = shallow
            data = video.to_legacy_dict()
            results[idx] = data  # slot-indexed, not append
            if result_callback:
                result_callback(idx, data)

    if status_callback:
        status_callback('done', f'found:{len(results)}')
    return results


def _javbus_keyword_search(
    name: str,
    limit: int,
    offset: int,
    status_callback: Optional[Callable[[str, str], None]],
    result_callback: Optional[Callable[[int, Any], None]],
    discovery_only: bool = False,
) -> List[Dict[str, Any]]:
    """JavBus keyword search — extracted from search_actress.

    Returns list[dict] (empty list on failure / no ids).
    JavDB fallback is NOT included; fuzzy fallback is limited to FUZZY_SEARCH_SOURCES.
    """
    try:
        if status_callback:
            status_callback('javbus', 'searching')

        scraper = JavBusScraper(lang=_get_javbus_lang())
        start_page = (offset // 30) + 1
        skip_in_page = offset % 30
        pages_needed = ((limit + skip_in_page) // 30) + 2

        all_ids = []
        for page in range(start_page, start_page + pages_needed):
            ids = scraper.get_ids_from_search(name, page=page)
            if ids:
                all_ids.extend(ids)
                if len(all_ids) >= limit + skip_in_page:
                    break
            else:
                break

        if all_ids:
            all_ids = all_ids[skip_in_page:]
            target_ids = all_ids[:limit]

            if status_callback:
                status_callback('javbus', f'found:{len(target_ids)}')

            if discovery_only:
                if status_callback:
                    status_callback('done', f'found:{len(target_ids)}')
                return [{'number': num, 'title': ''} for num in target_ids]

            if target_ids and result_callback:
                result_callback(-1, target_ids)

            results = []
            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                futures = {}
                for idx, num in enumerate(target_ids):
                    future = executor.submit(search_jav, num, 'javbus')
                    futures[future] = (idx, num)

                for future in as_completed(futures):
                    idx, num = futures[future]
                    try:
                        data = future.result()
                        if data and data.get('title'):
                            results.append(data)
                            if result_callback:
                                result_callback(idx, data)
                    except Exception:
                        logger.error('_javbus_keyword_search: %s failed', num)

            if status_callback:
                status_callback('done', f'found:{len(results)}')
            return sort_results_by_date(results)

    except Exception as e:
        logger.error('_javbus_keyword_search failed: %s', e)

    return []


def _fuzzy_one(
    source: str,
    query: str,
    limit: int,
    offset: int,
    proxy_url: str,
    status_callback: Optional[Callable[[str, str], None]],
    result_callback: Optional[Callable[[int, Any], None]],
    discovery_only: bool = False,
) -> List[Dict[str, Any]]:
    """統一 adapter：將兩個 keyword 入口歸一為 list[dict]（空 → []，永不回 None）。

    Caller must already have confirmed DMM is enabled before passing 'dmm' here.

    discovery_only semantics: only javbus has a stub-return mode; all other sources
    perform full enrichment and therefore must NOT participate when discovery_only=True.
    """
    if discovery_only and source not in ('javbus', 'theporndb'):
        return []

    if source == 'dmm':
        if status_callback:
            status_callback('dmm', 'searching')
        dmm_config = ScraperConfig(proxy_url=_dmm_proxy_url(proxy_url))
        dmm_scraper = DMMScraper(dmm_config)
        result = _dmm_keyword_search_progressive(
            dmm_scraper, query, limit, status_callback, result_callback, offset=offset
        )
        result = result if result is not None else []
        for d in result:
            d['_source'] = 'dmm'  # 鏡像 javbus（search_jav L354）：模糊結果補內部來源標記
        return result

    if source == 'javbus':
        return _javbus_keyword_search(
            query, limit, offset, status_callback, result_callback,
            discovery_only=discovery_only,
        )
    if source == 'theporndb':
        if status_callback:
            status_callback('theporndb', 'searching')
        scraper = ThePornDBScraper(api_token=_get_theporndb_token())
        videos = scraper.search_by_keyword(query, limit=limit, offset=offset)
        if discovery_only:
            results = [
                {'number': v.number, 'title': v.title, '_source': 'theporndb'}
                for v in videos
            ]
            if status_callback:
                status_callback('done', f'found:{len(results)}')
            return results
        results = []
        for idx, video in enumerate(videos):
            data = video.to_legacy_dict()
            data['_source'] = 'theporndb'
            data['_summary'] = video.summary
            data['_rating'] = video.rating
            data['_actress_aliases'] = video.actress_aliases
            data['_actress_profiles'] = video.actress_profiles
            results.append(data)
            if result_callback:
                result_callback(idx, data)
        if status_callback:
            status_callback('done', f'found:{len(results)}')
        return results

    if source == 'javdb':
        if status_callback:
            status_callback('javdb', 'searching')
        scraper = JavDBScraper(ScraperConfig(proxy_url=_dmm_proxy_url(proxy_url)))
        if _looks_western_title_query(query):
            video = scraper.search(query)
            videos = [video] if video else []
        else:
            videos = scraper.search_by_keyword(query, limit=limit)
        results = []
        for idx, video in enumerate(videos[:limit]):
            data = video.to_legacy_dict()
            data['_source'] = 'javdb'
            results.append(data)
            if result_callback:
                result_callback(idx, data)
        if status_callback:
            status_callback('done', f'found:{len(results)}')
        return results

    if source == 'missav':
        if status_callback:
            status_callback('missav', 'searching')
        scraper = MissAVScraper(ScraperConfig(proxy_url=_dmm_proxy_url(proxy_url)))
        videos = scraper.search_by_keyword(query, limit=limit)
        results = []
        for idx, video in enumerate(videos[:limit]):
            data = video.to_legacy_dict()
            data['_source'] = 'missav'
            results.append(data)
            if result_callback:
                result_callback(idx, data)
        if status_callback:
            status_callback('done', f'found:{len(results)}')
        return results

    return []


def _fuzzy_search_chain(
    query: str,
    limit: int = 20,
    offset: int = 0,
    proxy_url: str = '',
    status_callback: Optional[Callable[[str, str], None]] = None,
    result_callback: Optional[Callable[[int, Any], None]] = None,
    discovery_only: bool = False,
) -> List[Dict[str, Any]]:
    """Active-Row-ordered fuzzy fallback chain (CD-plan-65-5).

    Iterates get_all_source_ids_ordered() ∩ FUZZY_SEARCH_SOURCES in order.
    Stops at first non-empty result. Passes result_callback only to the first
    actually-dispatched source (seed rule). Returns [] when chain is exhausted.
    """
    chain = [s for s in get_all_source_ids_ordered() if s in FUZZY_SEARCH_SOURCES]
    if _looks_western_title_query(query):
        chain = [s for s in chain if s in ('theporndb', 'javdb')]
    for priority_sid in reversed(('javdb', 'missav')):
        if priority_sid in chain:
            chain = [priority_sid] + [s for s in chain if s != priority_sid]
    first_dispatched = False
    for source in chain:
        if source == 'dmm' and not _is_dmm_enabled(proxy_url):
            continue  # 不可達，跳過（不算 dispatched）
        if source == 'theporndb' and not _is_theporndb_keyword_enabled():
            continue
        cb = result_callback if (
            not first_dispatched
            or source == 'javbus'
            or (source == 'dmm' and chain[:1] == ['javdb'])
        ) else None
        results = _fuzzy_one(
            source, query, limit, offset, proxy_url, status_callback, cb,
            discovery_only=discovery_only,
        )
        first_dispatched = True  # 第一個實際發動後，後續 seed 不送
        if results:
            return results
    return []


def search_actress(
    name: str,
    limit: int = 20,
    offset: int = 0,
    status_callback: Optional[Callable[[str, str], None]] = None,
    result_callback: Optional[Callable[[int, Any], None]] = None,
    proxy_url: str = '',
    discovery_only: bool = False,
) -> List[Dict[str, Any]]:
    """女優搜尋 — thin wrapper delegating to _fuzzy_search_chain."""
    return _fuzzy_search_chain(
        name,
        limit=limit,
        offset=offset,
        proxy_url=proxy_url,
        status_callback=status_callback,
        result_callback=result_callback,
        discovery_only=discovery_only,
    )


def search_jav321_keyword(keyword: str, limit: int = 20, status_callback: Optional[Callable[[str, str], None]] = None) -> List[Dict[str, Any]]:
    """Jav321 關鍵字搜尋"""
    if status_callback:
        status_callback('jav321', 'searching')

    scraper = JAV321Scraper()
    videos = scraper.search_by_keyword(keyword, limit=limit)
    results = [v.to_legacy_dict() for v in videos]

    if status_callback:
        status_callback('jav321', f'found:{len(results)}')

    return results


def get_all_variant_ids(number: str) -> List[str]:
    """獲取變體 ID"""
    number = normalize_number(number)
    variant_ids = []

    try:
        scraper = JavBusScraper(lang=_get_javbus_lang())
        ids = scraper.get_ids_from_search(number, page=1, search_type=0)
        if ids:
            number_normalized = number.upper().replace('-', '')
            for id in ids:
                base_id = id.split('_')[0]
                if base_id.upper().replace('-', '') == number_normalized:
                    variant_ids.append(id)
            variant_ids.sort(reverse=True)
    except Exception as e:
        logger.error('get_all_variant_ids failed: %s', e)

    return variant_ids


def search_by_variant_id(variant_id: str, base_number: str) -> Optional[Dict[str, Any]]:
    """搜索變體"""
    try:
        scraper = JavBusScraper(lang=_get_javbus_lang())
        video = scraper._fetch_by_id(variant_id)
        if video:
            result = video.to_legacy_dict()
            # 用 base_number 覆蓋（保持與舊邏輯一致）
            result['number'] = base_number
            # 補 maker
            if not result.get('maker'):
                result['maker'] = get_maker_by_prefix(base_number)
            result['_source'] = 'javbus'
            result['_variant_id'] = variant_id
            return result
    except Exception as e:
        logger.error('search_by_variant_id failed: %s', e)
    return None


def _get_uncensored_sources(search_term: str) -> list[str]:
    """
    根據番號前綴決定無碼來源搜尋順序（spec US4 staged promotion，CD-63c-8）。

    先取 Active Row 中 enabled + available 且符合對應能力的 metatube 無碼 provider，
    prepend 到 builtin 清單前；fallback builtin 順序不變：
    - FC2 前綴 → metatube(FC2/FC2PPVDB/fc2hub) + ['fc2', 'avsox']
    - HEYZO 前綴 → metatube(HEYZO) + ['heyzo', 'avsox']
    - N/K 短编号（TOKYO-HOT）→ metatube:TOKYO-HOT + ['tokyohot', 'd2pass']
    - 其他（D2Pass 日期格式等）→ metatube(日期型 11) + ['d2pass', 'heyzo', 'fc2', 'avsox']

    無任何 metatube 無碼源啟用 → mt_pick=[] → 回傳純 builtin（與 B1 行為一致）。
    """
    # metatube_state / get_enabled_source_ids 皆已 module-level import（63c-1，line 29/34）
    from core.scrapers.utils import METATUBE_DATE_UNCENSORED

    # enabled + available + !manual_only 的 metatube 來源（按 order，含 availability gate）
    avail_map = metatube_state.availability_map()
    mt_enabled = [
        sid for sid in get_enabled_source_ids(availability_map=avail_map)
        if sid.startswith('metatube:')
    ]

    term_lower = search_term.lower().strip()
    if term_lower.startswith('fc2'):
        builtin = ['fc2', 'avsox']
        mt_pick = [s for s in mt_enabled
                   if s[len('metatube:'):] in ('FC2', 'FC2PPVDB', 'fc2hub')]
    elif term_lower.startswith('heyzo'):
        builtin = ['heyzo', 'avsox']
        mt_pick = [s for s in mt_enabled if s == 'metatube:HEYZO']
    elif re.match(r'^[nk]-?\d{4}$', term_lower, flags=re.IGNORECASE):
        builtin = ['tokyohot', 'd2pass']
        mt_pick = [s for s in mt_enabled if s.lower() == 'metatube:tokyo-hot']
    elif _is_avsox_uncensored_number(normalize_number(search_term)):
        builtin = ['avsox']
        mt_pick = []
    else:
        builtin = ['d2pass', 'heyzo', 'fc2', 'avsox']
        mt_pick = [s for s in mt_enabled
                   if s[len('metatube:'):] in METATUBE_DATE_UNCENSORED]

    return mt_pick + builtin


def _try_uncensored_exact_first(
    query: str,
    proxy_url: str = '',
    status_callback: Optional[Callable[[str, str], None]] = None,
) -> Optional[Dict[str, Any]]:
    extracted = _new_extract_number(query)
    if not extracted:
        return None

    for unc_source in _get_uncensored_sources(extracted):
        if status_callback:
            status_callback(unc_source, 'searching')
        result = search_jav(extracted, source=unc_source, proxy_url=proxy_url)
        if result:
            result['_mode'] = 'uncensored'
            return result
    return None


def _try_generic_exact_fallback(
    query: str,
    proxy_url: str = '',
    status_callback: Optional[Callable[[str, str], None]] = None,
) -> Optional[Dict[str, Any]]:
    result = _try_javdb_first(query, proxy_url=proxy_url, status_callback=status_callback)
    if result:
        result['_mode'] = 'uncensored'
        return result

    result = _try_missav_first(query, proxy_url=proxy_url, status_callback=status_callback)
    if result:
        result['_mode'] = 'uncensored'
        return result
    return None


def smart_search(query: str, limit: int = 20, offset: int = 0, status_callback: Optional[Callable[[str, str], None]] = None, uncensored_mode: bool = False, proxy_url: str = '', result_callback: Optional[Callable[[int, Any], None]] = None, discovery_only: bool = False) -> List[Dict[str, Any]]:
    """
    智慧搜尋：自動判斷搜尋類型並執行

    Args:
        query: 搜尋關鍵字
        limit: 結果數量限制
        offset: 分頁偏移
        status_callback: 狀態回調函數
        uncensored_mode: 無碼提示模式（先試專用源，沒命中會繼續廣搜）
    """
    query = query.strip()

    if not query or len(query) < 2:
        return []

    # 無碼提示模式：D2Pass → HEYZO → FC2 → AVSOX 先試精確源。
    # 沒命中時不中止；繼續走一般 fan-out / fuzzy，讓聚合源也有機會命中。
    if uncensored_mode:
        if status_callback:
            status_callback('mode', 'uncensored')
        result = _try_uncensored_exact_first(query, proxy_url=proxy_url, status_callback=status_callback)
        if result:
            if status_callback:
                status_callback('done', 'found:1')
            return [result]
        result = _try_generic_exact_fallback(query, proxy_url=proxy_url, status_callback=status_callback)
        if result:
            if status_callback:
                status_callback('done', 'found:1')
            return [result]

    # 0. 無碼特殊處理 - 自動偵測（FC2 / HEYZO / 日期-編號格式）
    query_kind = _query_kind(parse_media_identity(query))
    is_uncensored = (
        query.lower().strip().startswith('fc2') or
        query.lower().strip().startswith('heyzo') or
        re.match(r'^\d{6}-\d{2,}$', query) or
        re.match(r'^\d{6}_\d{2,}$', query) or
        re.match(r'^[nk]-?\d{4}$', query, flags=re.IGNORECASE) or
        query_kind in {"fc2", "heyzo", "date_uncensored", "tokyo_hot", "avsox_uncensored"}
    )
    if is_uncensored:
        if status_callback:
            status_callback('mode', 'uncensored')
        result = _try_uncensored_exact_first(query, proxy_url=proxy_url, status_callback=status_callback)
        if result:
            if status_callback:
                status_callback('done', 'found:1')
            return [result]
        result = _try_generic_exact_fallback(query, proxy_url=proxy_url, status_callback=status_callback)
        if result:
            if status_callback:
                status_callback('done', 'found:1')
            return [result]

    # 1. 精確搜尋
    if is_number_format(query):
        query = normalize_number(query)
        if offset > 0:
            return []

        javdb_result = _try_javdb_first(query, proxy_url=proxy_url, status_callback=status_callback)
        if javdb_result:
            javdb_result['_mode'] = 'exact'
            if status_callback:
                status_callback('done', 'found:1')
            return [javdb_result]

        missav_result = _try_missav_first(query, proxy_url=proxy_url, status_callback=status_callback)
        if missav_result:
            missav_result['_mode'] = 'exact'
            if status_callback:
                status_callback('done', 'found:1')
            return [missav_result]

        # Rule 4b（CD-61-19）：JavBus variant probe 僅在 JavBus 在 Active Row 啟用時觸發。
        # JavBus 停用 → 跳過 variant 探查 + 不發 javbus status（靜默降級），落一般 search_jav。
        if 'javbus' in get_enabled_source_ids():
            if status_callback:
                status_callback('javbus', 'searching')

            # 嘗試找變體
            variant_ids = get_all_variant_ids(query)
            if variant_ids:
                first = variant_ids[0]
                # 用 variant id 搜
                res = search_by_variant_id(first, query)
                if res:
                    res['_all_variant_ids'] = variant_ids
                    if status_callback: status_callback('done', 'found:1')
                    return [res]

        # 一般搜尋
        res = search_jav(query, proxy_url=proxy_url, _skip_sources={'javdb', 'missav'})
        results = [res] if res else []
        if status_callback: status_callback('done', f'found:{len(results)}')
        for r in results: r['_mode'] = 'exact'
        return results

    # 2. 局部搜尋
    elif is_partial_number(query):
        if offset > 0: return []
        results = search_partial(query, status_callback=status_callback, result_callback=result_callback, discovery_only=discovery_only)
        for r in results: r['_mode'] = 'partial'
        return results

    # 3. 前綴搜尋
    elif is_prefix_only(query):
        results = search_prefix(query, limit=limit, offset=offset, status_callback=status_callback, result_callback=result_callback, discovery_only=discovery_only)
        mode = 'prefix'

        if not results:
             # Fallback to actress（不透傳 result_callback：prefix 的 seed 已送出，
             # actress fallback 不可送第二個 seed，避免 slot index 錯位）
             if status_callback: status_callback('mode', 'actress')
             results = search_actress(query, limit=limit, status_callback=status_callback, proxy_url=proxy_url)
             if results: mode = 'actress'

        if not results:
             # Fallback to keyword（search_jav321_keyword 無 as_completed，不透傳 result_callback）
             if status_callback: status_callback('mode', 'keyword')
             results = search_jav321_keyword(query, limit=limit, status_callback=status_callback)
             if results: mode = 'keyword'

        for r in results: r['_mode'] = mode
        return results

    # 4. 女優/關鍵字搜尋
    else:
        # CD-plan-65-5: chain = get_all_source_ids_ordered() ∩ FUZZY_SEARCH_SOURCES.
        # Chain result (including []) is final — "Active Row order is truth".
        # No post-chain fallback: a hardcoded jav321 fallback here would bypass Active Row
        # order and run jav321 a second time if it was already in the chain.
        results = search_actress(
            query,
            limit=limit,
            offset=offset,
            proxy_url=proxy_url,
            status_callback=status_callback,
            result_callback=result_callback if not discovery_only else None,
            discovery_only=discovery_only,
        )
        for r in results: r['_mode'] = 'actress'
        return results

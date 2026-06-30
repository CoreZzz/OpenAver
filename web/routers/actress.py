"""
女優 API Router — /api/actresses

端點：
    POST   /api/actresses/favorite          收藏女優
    GET    /api/actresses/photo/{name}      取得本地照片（binary）
    GET    /api/actresses/{name}            查詢已收藏女優
    DELETE /api/actresses/{name}            刪除已收藏女優

注意：photo/{name} 必須定義在 {name} 之前，否則 FastAPI 會將 "photo" 解析為 {name}。
"""

import asyncio
import json
import random
import re
import time
from typing import Optional, List
from urllib.parse import quote

from fastapi import APIRouter
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel
from core.config import load_config
from core.maker_mapping import load_prefix_mapping

from core.database import ActressRepository, AliasRepository, VideoRepository, Actress, init_db
from core.actress_photo import download_actress_photo, get_local_photo_path, delete_local_photo, crop_video_cover, GFRIENDS_DIR
from core.organizer import sanitize_filename
from core.path_utils import to_file_uri, uri_to_fs_path, coerce_to_file_uri, is_path_under_dir
from core.scrapers.actress.orchestrator import (
    get_cached_profile,
    get_actress_profile,
    _compute_age_from_birth as _compute_age,
    _cache as _actress_cache,
    _normalize_name as _normalize_actress_name,
)
from core.logger import get_logger
from web.routers.showcase import _group_videos_by_work

logger = get_logger(__name__)

router = APIRouter(prefix="/api/actresses", tags=["actresses"])

_JAPANESE_NAME_VARIANTS = str.maketrans({
    "亚": "亜",
    "樱": "桜",
    "优": "優",
    "爱": "愛",
    "纱": "紗",
    "绘": "絵",
})


# ---------------------------------------------------------------------------
# Request model
# ---------------------------------------------------------------------------

class FavoriteRequest(BaseModel):
    name: str
    makers: Optional[List[str]] = None


class SetActressPhotoRequest(BaseModel):
    source: str                          # "graphis"|"gfriends"|"javdb"|"wiki"|"minnano"|"local_crop"
    url: Optional[str] = None            # 雲端來源：照片 URL（必填）
    video_path: Optional[str] = None     # local_crop：影片 file:/// URI（必填）
    crop_spec: Optional[str] = "v1"      # local_crop：裁切規格（預設 v1）


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _safe_int(v) -> Optional[int]:
    """帶單位字串（如 '80cm'）或 None → int 或 None"""
    if v is None:
        return None
    try:
        stripped = re.sub(r"[^\d]", "", str(v))
        return int(stripped) if stripped else None
    except (ValueError, TypeError):
        return None


def _flatten_aliases(raw) -> list:
    """
    將 aliases 欄位統一轉為純字串 list。

    minnano scraper 回傳 dict list（每筆含 ja/hiragana/romaji），
    wiki scraper 回傳純字串 list。
    前端需要純字串 list，此 helper 統一兩種格式。

    Args:
        raw: list of dict | list of str | None

    Returns:
        list of str（空 list 若 raw 為 None 或 []）
    """
    if not raw:
        return []
    return [a.get("ja", "") if isinstance(a, dict) else str(a) for a in raw]


def _is_missing_profile_value(value) -> bool:
    return value is None or value == "" or value == []


def _add_unique_name(names: list, value) -> None:
    if value is None:
        return
    value = str(value).strip()
    if value and value not in names:
        names.append(value)


def _profile_name_variants(value) -> list:
    value = str(value or "").strip()
    if not value:
        return []
    cleaned = re.sub(r"\s*[（(][^（）()]*[）)]\s*$", "", value).strip()
    if cleaned and cleaned != value:
        return [cleaned]
    return [value]


def _add_profile_name(names: list, value) -> None:
    for variant in _profile_name_variants(value):
        _add_unique_name(names, variant)


def _add_profile_names(names: list, raw) -> None:
    for alias in _flatten_aliases(raw):
        _add_profile_name(names, alias)


def _collect_profile_names(profile: Optional[dict], requested_name: str = "") -> list:
    names = []
    _add_profile_name(names, requested_name)
    if not profile:
        return names

    _add_profile_name(names, profile.get("name"))
    _add_profile_name(names, profile.get("name_ja"))

    text = profile.get("text") or {}
    if isinstance(text, dict):
        _add_profile_name(names, text.get("name"))
        _add_profile_name(names, text.get("name_ja"))
        _add_profile_names(names, text.get("aliases"))
        _add_profile_names(names, text.get("other_names"))
        _add_profile_names(names, text.get("also_known_as"))
        _add_profile_names(names, text.get("aka"))

    all_sources = profile.get("all_sources") or {}
    if isinstance(all_sources, dict):
        for source in all_sources.values():
            if not isinstance(source, dict):
                continue
            _add_profile_name(names, source.get("name"))
            _add_profile_name(names, source.get("name_ja"))
            _add_profile_names(names, source.get("aliases"))
            _add_profile_names(names, source.get("other_names"))
            _add_profile_names(names, source.get("also_known_as"))
            _add_profile_names(names, source.get("aka"))

    return names


def _find_existing_favorite_by_names(
    names: list,
    repo: ActressRepository,
    alias_repo: AliasRepository,
) -> Optional[Actress]:
    for candidate in names:
        try:
            if repo.exists(candidate):
                return repo.get_by_name(candidate)
        except Exception as e:
            logger.warning("[actress] favorite lookup failed for %r: %s", candidate, e)

    for candidate in names:
        try:
            resolved_names = alias_repo.resolve(candidate)
        except Exception as e:
            logger.warning("[actress] alias resolve failed for %r: %s", candidate, e)
            continue

        for resolved_name in resolved_names:
            try:
                if repo.exists(resolved_name):
                    return repo.get_by_name(resolved_name)
            except Exception as e:
                logger.warning(
                    "[actress] resolved favorite lookup failed for %r: %s",
                    resolved_name,
                    e,
                )

    return None


def _merge_unique_strings(*groups) -> list:
    merged = []
    for group in groups:
        if not group:
            continue
        for item in group:
            _add_unique_name(merged, item)
    return merged


def _build_actress_from_profile(
    profile: dict,
    fallback_name: str,
    aliases: Optional[list] = None,
) -> Actress:
    text = profile.get("text") or {}
    name = profile.get("name") or fallback_name
    alias_names = aliases if aliases is not None else [
        candidate
        for candidate in _collect_profile_names(profile, fallback_name)
        if candidate != name
    ]
    alias_names = [alias for alias in _merge_unique_strings(alias_names) if alias != name]

    return Actress(
        name=name,
        name_en=profile.get("name_en"),
        birth=text.get("birth"),
        height=text.get("height"),
        cup=text.get("cup"),
        bust=_safe_int(text.get("bust")),
        waist=_safe_int(text.get("waist")),
        hip=_safe_int(text.get("hip")),
        hometown=text.get("hometown"),
        hobby=text.get("hobby"),
        aliases=alias_names,
        agency=text.get("agency"),
        debut_work=text.get("debut_work"),
        tags=text.get("tags") or [],
        nickname=text.get("nickname"),
        blog_url=text.get("blog_url"),
        official_url=text.get("official_url"),
        photo_source=profile.get("photo_source"),
        primary_text_source=profile.get("primary_text_source"),
    )


def _merge_actress_profile(existing: Actress, incoming: Actress) -> Actress:
    for field_name in (
        "name_en", "birth", "height", "cup", "bust", "waist", "hip",
        "hometown", "hobby", "agency", "debut_work", "nickname",
        "blog_url", "official_url", "photo_source", "primary_text_source",
    ):
        if _is_missing_profile_value(getattr(existing, field_name, None)):
            incoming_value = getattr(incoming, field_name, None)
            if not _is_missing_profile_value(incoming_value):
                setattr(existing, field_name, incoming_value)

    existing.aliases = [
        alias
        for alias in _merge_unique_strings(
            existing.aliases,
            [incoming.name],
            incoming.aliases,
        )
        if alias != existing.name
    ]
    existing.tags = _merge_unique_strings(existing.tags, incoming.tags)
    return existing


def _sync_favorite_aliases(alias_repo: AliasRepository, actress: Actress) -> list:
    try:
        sync_result = alias_repo.sync_from_favorite(
            actress.name, actress.aliases or []
        )
        skipped_aliases = sync_result.get("skipped_aliases", [])
        if skipped_aliases:
            logger.warning("[actress] alias sync skipped: %s", skipped_aliases)
        return skipped_aliases
    except Exception as e:
        logger.warning("[actress] alias sync failed (non-blocking): %s", e)
        return []


def _merged_aliases(actress: Actress, alias_repo: Optional[AliasRepository] = None) -> list:
    merged = []

    def add_alias(alias: str):
        if alias and alias != actress.name and alias not in merged:
            merged.append(alias)

    for alias in actress.aliases or []:
        add_alias(alias)

    if alias_repo is not None:
        record = alias_repo.get_by_primary(actress.name)
        if record is None:
            record = alias_repo.find_by_alias(actress.name)
        if record is not None and isinstance(getattr(record, "primary_name", None), str):
            add_alias(record.primary_name)
            for alias in record.aliases or []:
                add_alias(alias)

    return merged


def _actress_to_response(
    actress: Actress,
    video_count: int = 0,
    alias_repo: Optional[AliasRepository] = None,
) -> dict:
    """將 Actress dataclass 轉為 API response dict"""
    local_path = get_local_photo_path(actress.name)
    if local_path is not None:
        photo_url = f"/api/actresses/photo/{quote(actress.name)}"
    else:
        photo_url = None

    return {
        "name": actress.name,
        "name_en": actress.name_en,
        "birth": actress.birth,
        "age": _compute_age(actress.birth),
        "height": actress.height,
        "cup": actress.cup,
        "bust": actress.bust,
        "waist": actress.waist,
        "hip": actress.hip,
        "hometown": actress.hometown,
        "hobby": actress.hobby,
        "aliases": _merged_aliases(actress, alias_repo),
        "agency": actress.agency,
        "debut_work": actress.debut_work,
        "tags": actress.tags or [],
        "nickname": actress.nickname,
        "blog_url": actress.blog_url,
        "official_url": actress.official_url,
        "photo_url": photo_url,
        "photo_source": actress.photo_source,
        "primary_text_source": actress.primary_text_source,
        "created_at": actress.created_at.isoformat() if actress.created_at else None,
        "video_count": video_count,
        "is_favorite": True,
    }


def _count_actress_work_cards(
    name: str,
    alias_repo: Optional[AliasRepository] = None,
) -> int:
    alias_repo = alias_repo or AliasRepository()
    try:
        names = list(alias_repo.resolve(name))
        videos = VideoRepository().get_videos_by_actress_names(names)
        scoped_videos = _filter_uncensored_gallery_videos(videos)
        scoped_count = _count_work_cards_for_videos(scoped_videos)
        if scoped_count > 0:
            return scoped_count
        return _count_work_cards_for_videos(videos)
    except Exception as e:
        logger.error("[actress] work-card count failed for %r: %s", name, e)
        return 0


def _count_work_cards_for_videos(videos: list) -> int:
    return len(_group_videos_by_work(videos))


def _uncensored_gallery_dir_uris(config: Optional[dict] = None) -> list[str]:
    """Return gallery directories explicitly labelled as uncensored.

    An empty result means no scope is configured, so callers keep legacy
    all-library behavior in tests and unlabelled installs.
    """
    try:
        cfg = config if config is not None else load_config()
    except Exception as e:
        logger.warning("[actress] load gallery scope failed: %s", e)
        return []

    gallery = cfg.get("gallery") if isinstance(cfg, dict) else {}
    if not isinstance(gallery, dict):
        return []

    directories = gallery.get("directories") or []
    labels = gallery.get("directory_labels") or {}
    path_mappings = gallery.get("path_mappings") or {}
    if not isinstance(labels, dict):
        return []

    dir_uris: list[str] = []
    for directory in directories:
        directory_text = str(directory or "").strip()
        if not directory_text:
            continue
        label = str(labels.get(directory_text, "") or "").strip().lower()
        if label != "uncensored":
            continue
        try:
            dir_uri = coerce_to_file_uri(directory_text, path_mappings)
        except Exception:
            try:
                dir_uri = to_file_uri(directory_text, path_mappings)
            except Exception as e:
                logger.warning("[actress] invalid gallery directory skipped: %s", e)
                continue
        if dir_uri not in dir_uris:
            dir_uris.append(dir_uri)
    return dir_uris


def _filter_uncensored_gallery_videos(videos: list, dir_uris: Optional[list[str]] = None) -> list:
    dir_uris = _uncensored_gallery_dir_uris() if dir_uris is None else dir_uris
    if not dir_uris:
        return videos

    scoped = []
    for video in videos:
        raw_path = str(getattr(video, "path", "") or "").strip()
        if not raw_path:
            continue
        try:
            video_uri = coerce_to_file_uri(raw_path)
        except Exception as e:
            logger.warning("[actress] video path scope check failed path=%s: %s", raw_path, e)
            continue
        if any(is_path_under_dir(video_uri, dir_uri) for dir_uri in dir_uris):
            scoped.append(video)
    return scoped


def _build_local_actress_from_videos(
    name: str,
    alias_repo: AliasRepository,
) -> Optional[Actress]:
    """
    Build a minimal favorite from local library data when cloud profile lookup
    cannot find the actress. This keeps typo-only names out unless a local video
    already lists the actress exactly or through an alias group.
    """
    primary_name = name
    aliases: list[str] = []

    try:
        record = alias_repo.get_by_primary(name)
        if record is None:
            record = alias_repo.find_by_alias(name)

        if record is not None:
            primary_name = record.primary_name
            aliases = [
                alias for alias in (record.aliases or [])
                if alias and alias != primary_name
            ]
            if name != primary_name and name not in aliases:
                aliases.append(name)
            query_names = _merge_unique_strings([primary_name], aliases)
        else:
            query_names = list(alias_repo.resolve(name))
    except Exception as e:
        logger.warning("[actress] local favorite alias lookup failed for %r: %s", name, e)
        query_names = [name]

    try:
        videos = VideoRepository().get_videos_by_actress_names(query_names)
        videos = _filter_uncensored_gallery_videos(videos)
    except Exception as e:
        logger.warning("[actress] local favorite video lookup failed for %r: %s", name, e)
        return None

    if not videos:
        return None

    return Actress(
        name=primary_name,
        aliases=[alias for alias in aliases if alias != primary_name],
        photo_source="local_crop" if any(video.cover_path for video in videos) else None,
        primary_text_source="local",
    )


# ---------------------------------------------------------------------------
# 端點一：POST /api/actresses/favorite — 收藏女優
# ---------------------------------------------------------------------------

@router.post("/favorite")
def add_favorite(req: FavoriteRequest):
    """
    收藏女優。

    流程：
    1. 檢查是否已收藏 → 409
    2. 嘗試從 cache 取得 profile（不打網路）
    3. cache miss → 呼叫 orchestrator 重新抓取
    4. 組裝 Actress → DB save → 下載照片
    5. 回傳 200 with actress data
    """
    name = req.name.strip()
    if not name:
        return JSONResponse(
            status_code=422,
            content={"error": "invalid_name", "message": "name 不可為空"}
        )

    init_db()
    repo = ActressRepository()
    alias_repo = AliasRepository()

    # 1. 已收藏檢查 → 409
    if repo.exists(name):
        existing = repo.get_by_name(name)
        video_count = _count_actress_work_cards(existing.name, alias_repo) if existing else 0
        return JSONResponse(
            status_code=409,
            content={
                "error": "already_exists",
                "actress": _actress_to_response(existing, video_count, alias_repo),
            }
        )

    existing_by_alias = _find_existing_favorite_by_names([name], repo, alias_repo)
    if existing_by_alias is not None:
        video_count = _count_actress_work_cards(existing_by_alias.name, alias_repo)
        return JSONResponse(
            status_code=409,
            content={
                "error": "already_exists",
                "actress": _actress_to_response(existing_by_alias, video_count, alias_repo),
            }
        )

    # 2. 番號前綴 → 片商名轉換（前端傳 SSIS，gfriends 需要 S1）
    resolved_makers = None
    if req.makers:
        prefix_map = load_prefix_mapping()
        seen = set()
        ordered = []
        for p in req.makers:
            maker = prefix_map.get(p.upper())
            if maker and maker not in seen:
                seen.add(maker)
                ordered.append(maker)
        resolved_makers = ordered or None

    # 3. cache hit — 不打網路
    profile = get_cached_profile(name)

    # 4. cache miss → 重新抓取
    if profile is None:
        result = get_actress_profile(name, makers=resolved_makers)
        if result.data is None:
            local_actress = _build_local_actress_from_videos(name, alias_repo)
            if local_actress is not None:
                existing = _find_existing_favorite_by_names(
                    [name, local_actress.name, *(local_actress.aliases or [])],
                    repo,
                    alias_repo,
                )
                if existing is not None:
                    video_count = _count_actress_work_cards(existing.name, alias_repo)
                    return JSONResponse(
                        status_code=409,
                        content={
                            "error": "already_exists",
                            "actress": _actress_to_response(existing, video_count, alias_repo),
                        },
                    )

                repo.save(local_actress)
                actress = repo.get_by_name(local_actress.name) or local_actress
                logger.info("[actress] local favorite created: %s", actress.name)
                skipped_aliases = _sync_favorite_aliases(alias_repo, actress)
                video_count = _count_actress_work_cards(actress.name, alias_repo)
                return JSONResponse(
                    status_code=200,
                    content={
                        "success": True,
                        "actress": _actress_to_response(actress, video_count, alias_repo),
                        "photo_downloaded": False,
                        "skipped_aliases": skipped_aliases,
                    },
                )

            if result.timed_out:
                return JSONResponse(
                    status_code=504,
                    content={"error": "timeout", "message": "Scraper 超時"}
                )
            return JSONResponse(
                status_code=404,
                content={"error": "not_found", "message": "查無此女優"}
            )
        profile = result.data

    # 4. 組裝 Actress dataclass
    profile_names = _collect_profile_names(profile, name)
    existing = _find_existing_favorite_by_names(profile_names, repo, alias_repo)
    if existing is not None:
        profile_aliases = [
            candidate for candidate in profile_names if candidate != existing.name
        ]
        incoming = _build_actress_from_profile(profile, name, profile_aliases)
        existing = _merge_actress_profile(existing, incoming)
        repo.save(existing)
        existing = repo.get_by_name(existing.name) or existing

        skipped_aliases = _sync_favorite_aliases(alias_repo, existing)
        photo_downloaded = False
        if get_local_photo_path(existing.name) is None:
            photo_downloaded = download_actress_photo(
                existing.name, profile.get("photo_url"), profile.get("photo_source")
            )

        video_count = _count_actress_work_cards(existing.name, alias_repo)
        return JSONResponse(
            status_code=409,
            content={
                "error": "already_exists",
                "message": "已合并到已有女优",
                "merged": True,
                "actress": _actress_to_response(existing, video_count, alias_repo),
                "photo_downloaded": photo_downloaded,
                "skipped_aliases": skipped_aliases,
            },
        )

    profile_aliases = [
        candidate
        for candidate in profile_names
        if candidate != (profile.get("name") or name)
    ]
    actress = _build_actress_from_profile(profile, name, profile_aliases)

    # DB save（ON CONFLICT DO UPDATE）
    repo.save(actress)
    actress = repo.get_by_name(actress.name) or actress  # re-read for created_at
    logger.info("[actress] 收藏女優：%s", actress.name)

    # Sync aliases to actress_aliases table
    skipped_aliases = _sync_favorite_aliases(alias_repo, actress)

    # 5. 下載照片（photo_url 可能為 None，函數內部已有 guard）
    photo_downloaded = download_actress_photo(
        actress.name, profile.get("photo_url"), profile.get("photo_source")
    )

    video_count = _count_actress_work_cards(actress.name, alias_repo)
    return JSONResponse(
        status_code=200,
        content={
            "success": True,
            "actress": _actress_to_response(actress, video_count, alias_repo),
            "photo_downloaded": photo_downloaded,
            "skipped_aliases": skipped_aliases,
        }
    )


# ---------------------------------------------------------------------------
# 端點四：GET /api/actresses/photo/{name} — 本地照片 binary response
# NOTE：必須定義在 GET /{name} 之前！
# ---------------------------------------------------------------------------

@router.get("/photo/{name}")
def get_actress_photo(name: str):
    """
    取得女優本地照片（binary image response）。
    FastAPI 自動 decode URL-encoded path parameter。
    """
    path = get_local_photo_path(name)
    if path is None:
        return Response(b"", status_code=404)

    _MIME_MAP = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".png": "image/png", ".webp": "image/webp", ".gif": "image/gif",
    }
    media_type = _MIME_MAP.get(path.suffix.lower(), "image/jpeg")

    return Response(
        content=path.read_bytes(),
        media_type=media_type,
    )


# ---------------------------------------------------------------------------
# 端點五：GET /api/actresses — 列出所有已收藏女優（含 video_count）
# NOTE：必須定義在 GET /{name} 之前！
# ---------------------------------------------------------------------------

@router.get("")
def list_actresses():
    """
    列出所有已收藏女優，每筆含 video_count 和 created_at。
    """
    init_db()
    repo = ActressRepository()
    alias_repo = AliasRepository()
    actresses = repo.get_all()
    result = []
    for actress in actresses:
        video_count = _count_actress_work_cards(actress.name, alias_repo)
        result.append(_actress_to_response(actress, video_count, alias_repo))
    return JSONResponse(
        status_code=200,
        content={
            "success": True,
            "actresses": result,
            "total": len(result),
        }
    )


# ---------------------------------------------------------------------------
# Helper functions for photo-candidates SSE endpoint
# 注意：必須放 module-level，不可嵌套在 generator 內
# ---------------------------------------------------------------------------

def _fetch_source_for_name(
    candidate_name: str,
    source: str,
    makers: Optional[list[str]] = None,
) -> Optional[str]:
    if source == "graphis":
        from core.scrapers.actress.graphis import scrape_graphis_photo
        r = scrape_graphis_photo(candidate_name)
        return r.get("prof_url") if r and r.get("prof_url") else None
    if source == "gfriends":
        from core.scrapers.actress.gfriends import lookup_gfriends
        return lookup_gfriends(candidate_name, makers)
    if source == "wiki":
        from core.scrapers.actress.wiki_ja import scrape_wiki_ja
        r = scrape_wiki_ja(candidate_name)
        return r.get("photo_url") if r and r.get("photo_url") else None
    if source == "javdb":
        from core.scrapers.javdb import scrape_javdb_actress_photo
        return scrape_javdb_actress_photo(candidate_name)
    if source == "minnano":
        from core.scrapers.actress.minnano_av import scrape_minnano_av
        r = scrape_minnano_av(candidate_name)
        return r.get("photo_url") if r and r.get("photo_url") else None
    return None


def _fetch_source_photo_candidates(name: str, source: str) -> list[dict]:
    results = []
    seen_urls = set()
    names = _actress_cloud_name_candidates(name)
    makers = _infer_gfriends_makers(name) if source == "gfriends" else None

    for candidate_name in names:
        try:
            url = _fetch_source_for_name(candidate_name, source, makers)
        except Exception as e:
            logger.warning(
                "[actress] cloud photo fetch failed source=%s name=%s: %s",
                source,
                candidate_name,
                e,
            )
            continue
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        results.append({
            "source": source,
            "query_name": candidate_name,
            "thumb_url": _proxy_image_url(url),
            "full_url": url,
        })

    return results


def _fetch_single_source(name: str, source: str) -> Optional[str]:
    """
    從指定雲端來源抓取女優照片 URL。
    同步函數（用 asyncio.to_thread 呼叫）。

    Returns:
        URL str 或 None
    """
    try:
        candidates = _fetch_source_photo_candidates(name, source)
        return candidates[0]["full_url"] if candidates else None
    except Exception as e:
        logger.warning("[actress] _fetch_single_source 失敗 source=%s: %s", source, e)
        return None


def _actress_cloud_name_candidates(name: str) -> list[str]:
    candidates = []

    def add(value: Optional[str]):
        value = str(value or "").strip()
        if value and value not in candidates:
            candidates.append(value)

    add(name)

    try:
        for alias in AliasRepository().resolve(name):
            add(alias)
    except Exception as e:
        logger.warning("[actress] cloud name alias resolve failed: %s", e)

    for candidate in list(candidates):
        add(candidate.translate(_JAPANESE_NAME_VARIANTS))

    return candidates


def _infer_gfriends_makers(actress_name: str) -> Optional[list[str]]:
    """
    Infer makers from local videos so the picker can query gfriends with the same
    kind of maker hints used during initial favorite creation.
    """
    try:
        alias_repo = AliasRepository()
        names = list(alias_repo.resolve(actress_name))
    except Exception as e:
        logger.warning("[actress] gfriends maker alias resolve failed: %s", e)
        names = [actress_name]

    try:
        videos = VideoRepository().get_videos_by_actress_names(names)
        videos = _filter_uncensored_gallery_videos(videos)
    except Exception as e:
        logger.warning("[actress] gfriends maker video lookup failed: %s", e)
        return None

    prefix_map = load_prefix_mapping()
    seen = set()
    makers = []

    def add_maker(maker: Optional[str]):
        maker = str(maker or "").strip()
        if maker and maker not in seen:
            seen.add(maker)
            makers.append(maker)

    for video in videos:
        add_maker(getattr(video, "maker", None))
        number = str(getattr(video, "number", "") or "")
        match = re.match(r"^([A-Za-z]+)", number)
        if match:
            add_maker(prefix_map.get(match.group(1).upper()))

    return makers or None


def _proxy_image_url(url: str) -> str:
    return f"/api/proxy-image?url={quote(url, safe='')}"


def _order_photo_candidates(candidates: list[dict], name_order: list[str]) -> list[dict]:
    grouped = {}
    for candidate in candidates:
        query_name = candidate.get("query_name") or ""
        grouped.setdefault(query_name, []).append(candidate)

    ordered_names = [name for name in name_order if name in grouped]
    ordered_names.extend(name for name in grouped.keys() if name not in ordered_names)

    ordered = []
    while any(grouped.get(name) for name in ordered_names):
        for name in ordered_names:
            items = grouped.get(name) or []
            if items:
                ordered.append(items.pop(0))
    return ordered


def _get_random_videos_with_covers(actress_name: str, count: int) -> list:
    """
    取得女優隨機影片（有封面的）。

    使用 AliasRepository.resolve 展開 alias set，以 get_videos_by_actress_names
    多名查詢，涵蓋所有 alias 標記的影片。無 alias 時 resolve 回 {actress_name}，
    行為等價舊版。雲端路徑不受影響（仍只用 primary name）。

    Returns:
        Video list（最多 count 筆，有 cover_path 且非空）
    """
    try:
        init_db()
        repo = VideoRepository()
        alias_repo = AliasRepository()
        names = list(alias_repo.resolve(actress_name))  # 雙向展開；無 alias 時回 {actress_name}
        videos = repo.get_videos_by_actress_names(names)
        videos = _filter_uncensored_gallery_videos(videos)
        with_covers = [v for v in videos if v.cover_path]
        random.shuffle(with_covers)
        return with_covers[:count]
    except Exception as e:
        logger.warning("[actress] _get_random_videos_with_covers 失敗: %s", e)
        return []


# ---------------------------------------------------------------------------
# 端點七：GET /api/actresses/{name}/photo-candidates — SSE 候選照片串流
# NOTE：必須定義在 GET /{name} 之前！
# ---------------------------------------------------------------------------

@router.get("/{name}/photo-candidates")
async def list_photo_candidates(name: str):
    """
    SSE 串流回傳女優候選照片（最多 6 張）。
    雲端 0–3 張並行抓取 + 本機影片封面 crop 補足至 6 張。
    actress 不存在 → JSONResponse 404。
    """
    init_db()
    repo = ActressRepository()
    alias_repo = AliasRepository()
    actress = repo.get_by_name(name)
    if actress is None:
        actress = _find_existing_favorite_by_names([name], repo, alias_repo)
    if actress is None:
        return JSONResponse(
            status_code=404,
            content={"error": "not_found"}
        )

    search_name = actress.name or name
    cloud_sources = ["gfriends", "javdb", "graphis", "wiki", "minnano"]

    async def generate():
        total = 0
        max_cloud = 10

        # 雲端並行抓取
        async def fetch_source(src: str):
            try:
                candidates = await asyncio.wait_for(
                    asyncio.to_thread(_fetch_source_photo_candidates, search_name, src),
                    timeout=25.0,
                )
                return candidates
            except Exception:
                return []

        if cloud_sources:
            tasks = [asyncio.ensure_future(fetch_source(src)) for src in cloud_sources]
            cloud_candidates = []
            for coro in asyncio.as_completed(tasks):
                cloud_candidates.extend(await coro)

            if cloud_candidates:
                name_order = _actress_cloud_name_candidates(search_name)
                for candidate in _order_photo_candidates(cloud_candidates, name_order):
                    if total >= max_cloud:
                        break
                    event_data = json.dumps(candidate)
                    yield f"event: candidate\ndata: {event_data}\n\n"
                    total += 1

        # 本機 crop 補足
        needed = 6 - total
        if needed > 0:
            local_videos = await asyncio.to_thread(
                _get_random_videos_with_covers, search_name, needed
            )
            for video in local_videos:
                # Fix 1 (T2): cover_path 在 DB 存 file:/// URI，crop endpoint 需要 FS path
                cover_fs_path = uri_to_fs_path(str(video.cover_path)) if video.cover_path else ""
                if not cover_fs_path:
                    # skip broken candidate，避免送空路徑的 URL
                    continue
                encoded_path = quote(cover_fs_path)
                crop_url = f"/api/actresses/actress-crop?path={encoded_path}&spec=v1"
                # Fix 2 (T2): video.path 在 DB 已是 file:/// URI（gallery_scanner.scan_file 透過 to_file_uri 寫入）
                # 若萬一是 FS path（legacy / 異常），coerce_to_file_uri 做 idempotent 轉換
                try:
                    video_path_uri = coerce_to_file_uri(str(video.path))
                except Exception as e:
                    logger.warning("[actress] coerce_to_file_uri 失敗 path=%s: %s", video.path, e)
                    video_path_uri = str(video.path)
                event_data = json.dumps({
                    "source": "local_crop",
                    "video_path": video_path_uri,
                    "thumb_url": crop_url,
                    "full_url": crop_url,
                })
                yield f"event: candidate\ndata: {event_data}\n\n"
                total += 1

        # done event
        done_data = json.dumps({"total": total})
        yield f"event: done\ndata: {done_data}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# 端點八：GET /api/actresses/actress-crop — on-demand 封面 crop
# NOTE：必須定義在 GET /{name} 之前！
# ---------------------------------------------------------------------------

@router.get("/actress-crop")
async def actress_crop(path: str, spec: str = "v1"):
    """
    對指定本機封面圖做 crop，回傳 JPEG bytes。
    path: 本機 FS 路徑（URL-encoded）；若傳入 file:/// URI 也接受（防禦性轉換）
    spec: crop 規格版本（預設 v1）
    """
    # Fix 2 (T2): uri_to_fs_path 已 idempotent（非 URI 直接 normalize_path），直接呼叫
    fs_path = uri_to_fs_path(path)
    # Security: cover_path 必須是 DB 中某個 video 的 cover_path（防任意檔案讀取）
    init_db()
    video_repo = VideoRepository()
    if not video_repo.is_known_cover_path(fs_path):
        return Response(b"", status_code=403)
    result = await asyncio.to_thread(crop_video_cover, fs_path, spec)
    if result is None:
        return Response(b"", status_code=404)
    return Response(content=result, media_type="image/jpeg")


# ---------------------------------------------------------------------------
# 端點九：POST /api/actresses/{name}/photo — 設定女優照片
# NOTE：必須定義在 GET /{name} 之前！
# ---------------------------------------------------------------------------

CLOUD_SOURCES = {"graphis", "gfriends", "javdb", "wiki", "minnano"}


@router.post("/{name}/photo")
async def set_actress_photo(name: str, req: SetActressPhotoRequest):
    """
    設定女優照片。
    - 雲端來源（gfriends/javdb/graphis/wiki/minnano）：下載並覆蓋本機照片
    - local_crop：從影片封面 crop 後寫入 GFRIENDS_DIR
    覆蓋時先 glob 刪舊副檔名，再寫入新圖。
    更新 DB photo_source 欄位，回傳帶 cache-bust timestamp 的新 photo_url。
    """
    init_db()
    repo = ActressRepository()
    alias_repo = AliasRepository()
    actress = _find_existing_favorite_by_names([name], repo, alias_repo)
    if actress is None:
        return JSONResponse(status_code=404, content={"error": "not_found"})
    actress_name = actress.name

    if req.source not in CLOUD_SOURCES and req.source != "local_crop":
        return JSONResponse(status_code=400, content={"error": "unknown_source"})

    if req.source in CLOUD_SOURCES:
        if not req.url:
            return JSONResponse(status_code=400, content={"error": "url_required"})
        ok = await asyncio.to_thread(download_actress_photo, actress_name, req.url, req.source)
        if not ok:
            return JSONResponse(status_code=500, content={"error": "download_failed"})

    elif req.source == "local_crop":
        if not req.video_path:
            return JSONResponse(status_code=400, content={"error": "video_path_required"})
        # file:/// URI → FS path（禁止手動 strip）
        video_fs_path = uri_to_fs_path(req.video_path)
        requested_video_keys = {
            str(req.video_path),
            video_fs_path,
        }
        try:
            requested_video_keys.add(coerce_to_file_uri(video_fs_path))
        except Exception as e:
            logger.warning("[actress] local_crop coerce request path 失敗 path=%s: %s", video_fs_path, e)
        # 從 DB 取該影片的 cover_path
        video_repo = VideoRepository()
        try:
            actress_names = list(alias_repo.resolve(actress_name))
        except Exception as e:
            logger.warning("[actress] local_crop alias resolve 失敗 name=%s: %s", actress_name, e)
            actress_names = [actress_name]
        videos = video_repo.get_videos_by_actress_names(actress_names)
        videos = _filter_uncensored_gallery_videos(videos)
        # Fix 3 (T3): v.path 在 DB 存 file:/// URI（gallery_scanner 用 to_file_uri 寫入），
        # 比對前雙邊都正規化為 FS path，避免 URI vs FS path 永遠 fail
        match = next(
            (
                v for v in videos
                if str(v.path) in requested_video_keys
                or uri_to_fs_path(str(v.path)) in requested_video_keys
            ),
            None,
        )
        if match is None or not match.cover_path:
            return JSONResponse(status_code=404, content={"error": "video_or_cover_not_found"})
        # Fix 3 (T3): match.cover_path 也是 URI，傳給 crop_video_cover 前先轉 FS path
        cover_fs_path = uri_to_fs_path(str(match.cover_path)) if match.cover_path else ""
        if not cover_fs_path:
            return JSONResponse(status_code=404, content={"error": "video_or_cover_not_found"})
        # crop → bytes
        crop_bytes = await asyncio.to_thread(
            crop_video_cover, cover_fs_path, req.crop_spec or "v1"
        )
        if crop_bytes is None:
            return JSONResponse(status_code=500, content={"error": "crop_failed"})
        # glob 刪舊副檔名 + 寫入
        safe = sanitize_filename(actress_name)
        GFRIENDS_DIR.mkdir(parents=True, exist_ok=True)
        for old in GFRIENDS_DIR.glob(f"{safe}.*"):
            old.unlink()
        (GFRIENDS_DIR / f"{safe}.jpg").write_bytes(crop_bytes)

    # 更新 photo_source + 回傳
    actress.photo_source = req.source
    repo.save(actress)

    t = int(time.time())
    photo_url = f"/api/actresses/photo/{quote(actress_name)}?t={t}"
    return JSONResponse(status_code=200, content={
        "photo_url": photo_url,
        "photo_source": req.source,
    })


# ---------------------------------------------------------------------------
# 端點二：GET /api/actresses/{name} — 查詢已收藏女優
# ---------------------------------------------------------------------------

@router.get("/{name}")
def get_actress(name: str):
    """
    查詢已收藏的女優資料。
    """
    init_db()
    repo = ActressRepository()
    alias_repo = AliasRepository()
    actress = _find_existing_favorite_by_names([name], repo, alias_repo)
    if actress is None:
        return JSONResponse(
            status_code=404,
            content={"error": "not_found"}
        )

    video_count = _count_actress_work_cards(actress.name, alias_repo)
    return JSONResponse(
        status_code=200,
        content={
            "actress": _actress_to_response(actress, video_count, alias_repo),
            "is_favorite": True,
        }
    )


# ---------------------------------------------------------------------------
# 端點三：DELETE /api/actresses/{name} — 刪除已收藏女優
# ---------------------------------------------------------------------------

@router.delete("/{name}")
def delete_actress(name: str):
    """
    刪除已收藏的女優（DB + 本地照片）。
    """
    init_db()
    repo = ActressRepository()

    if not repo.exists(name):
        return JSONResponse(
            status_code=404,
            content={"error": "not_found"}
        )

    repo.delete_by_name(name)
    delete_local_photo(name)  # idempotent，不需檢查回傳值
    logger.info("[actress] 刪除女優：%s", name)

    return JSONResponse(
        status_code=200,
        content={"success": True}
    )

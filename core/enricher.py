"""
enricher.py - 舊片原地補完（NFO / 封面 / 劇照），絕對不搬移、不改名、不建目錄
"""

import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from core.database import (
    Actress,
    ActressRepository,
    AliasRepository,
    Video,
    VideoRepository,
    get_connection,
)
from core.logger import get_logger
from core.nfo_updater import parse_nfo
from core.organizer import download_image, find_subtitle_files, generate_nfo
from core.path_utils import to_file_uri, uri_to_fs_path
from core.scraper import search_jav
from core.sidecar_paths import resolve_sidecar_paths
from core.title_placeholders import is_filename_placeholder_title
from core.filename_identity import parse_media_identity
from core.metadata_corrections import apply_metadata_overrides, sanitize_actor_names

logger = get_logger(__name__)

VALID_MODES = {"fill_missing", "db_to_sidecar", "refresh_full"}

_FILL_MISSING_REQUIRED = ["title", "actresses", "maker", "director", "series", "label", "tags", "release_date"]
_DATE_STYLE_NUMBER_RE = re.compile(r"^(\d{6})([-_])(\d{2,3})$")


def _sidecar_meta(number: str, meta: dict) -> dict:
    data = dict(meta or {})
    data["number"] = number
    data["num"] = number
    return data


def _search_query_from_path(path: str) -> str:
    identity = parse_media_identity(path)
    return identity.search_number or identity.canonical_number or identity.raw_stem.strip()


def _date_style_compact_key(value: str) -> str:
    identity = parse_media_identity(value)
    number = identity.canonical_number or str(value or "").strip().upper()
    match = _DATE_STYLE_NUMBER_RE.match(number)
    if not match:
        return ""
    return f"{match.group(1)}{match.group(3)}"


def _prefer_path_date_separator(number: str, fs_path: str) -> str:
    path_identity = parse_media_identity(fs_path)
    path_number = path_identity.canonical_number or ""
    current_identity = parse_media_identity(number)
    current_number = current_identity.canonical_number or str(number or "").strip()
    if not path_number or path_number == current_number:
        return str(number or "").strip()
    if (
        _DATE_STYLE_NUMBER_RE.match(path_number)
        and _DATE_STYLE_NUMBER_RE.match(current_number)
        and _date_style_compact_key(path_number) == _date_style_compact_key(current_number)
    ):
        return path_number
    return str(number or "").strip()


def _ensure_parent(path: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)


def _is_remote_url(value: str) -> bool:
    text = str(value or "").strip().lower()
    return text.startswith("http://") or text.startswith("https://")


def _sidecar_section(config: dict | None) -> dict:
    if not isinstance(config, dict):
        return {}
    raw = config.get("sidecar") if "sidecar" in config else config
    return raw if isinstance(raw, dict) else {}


def _render_sidecar_name(template: str, number: str) -> str:
    return (
        str(template or "")
        .replace("{num}", number)
        .replace("{number}", number)
        .strip()
    )


def _centralized_sidecar_cover_for_nfo(nfo_path: Path, sidecar: dict, number: str) -> str:
    cover_names = [
        _render_sidecar_name(str(sidecar.get("cover_filename") or "cover.jpg"), number),
        _render_sidecar_name(str(sidecar.get("poster_filename") or "poster.jpg"), number),
        _render_sidecar_name(str(sidecar.get("fanart_filename") or "fanart.jpg"), number),
        "cover.jpg",
        "poster.jpg",
        "fanart.jpg",
        f"{number}.jpg",
    ]
    for name in dict.fromkeys(name for name in cover_names if name):
        candidate = nfo_path.parent / name
        if candidate.is_file():
            return str(candidate)
    return ""


def _discover_existing_centralized_sidecar(number: str, sidecar_config: dict | None) -> dict[str, str]:
    sidecar = _sidecar_section(sidecar_config)
    if sidecar.get("mode") != "centralized":
        return {}

    root_dir = str(sidecar.get("root_dir") or "").strip()
    if not root_dir:
        return {}

    try:
        root = Path(uri_to_fs_path(root_dir))
    except Exception:
        root = Path(root_dir)
    if not root.is_dir():
        return {}

    number = str(number or "").strip()
    if not number:
        return {}

    nfo_names = {
        f"{number}.nfo",
        _render_sidecar_name(str(sidecar.get("nfo_filename") or "{num}.nfo"), number),
    }
    nfo_names = {name for name in nfo_names if name}

    nfo_paths: List[Path] = []
    seen_paths: set[str] = set()
    for name in sorted(nfo_names):
        try:
            matches = sorted(root.rglob(name), key=lambda p: str(p).lower())
        except OSError as exc:
            logger.warning("centralized sidecar discovery failed for %s: %s", number, exc)
            return {}
        for match in matches:
            key = str(match).lower()
            if key not in seen_paths:
                seen_paths.add(key)
                nfo_paths.append(match)

    if not nfo_paths:
        return {}

    nfo_path = nfo_paths[0]
    cover_path = ""
    for candidate in nfo_paths:
        candidate_cover = _centralized_sidecar_cover_for_nfo(candidate, sidecar, number)
        if candidate_cover:
            nfo_path = candidate
            cover_path = candidate_cover
            break
    if not cover_path:
        cover_path = _centralized_sidecar_cover_for_nfo(nfo_path, sidecar, number)

    return {"nfo_path": str(nfo_path), "cover_path": cover_path}


@dataclass
class EnrichResult:
    success: bool
    nfo_written: bool
    cover_written: bool
    extrafanart_written: int
    fields_filled: List[str]
    source_used: str
    error: Optional[str]


def _nfo_to_meta(root: ET.Element) -> dict:
    def _text(tag: str) -> str:
        elem = root.find(tag)
        return (elem.text or "").strip() if elem is not None else ""

    actors = [
        (n.text or "").strip()
        for a in root.findall("actor")
        for n in [a.find("name")]
        if n is not None and n.text
    ]
    tags = [(e.text or "").strip() for e in root.findall("tag") if e.text]
    set_elem = root.find("set")
    series = ""
    if set_elem is not None:
        n_elem = set_elem.find("name")
        series = (n_elem.text or "").strip() if n_elem is not None else ""

    runtime_text = _text("runtime")
    duration = int(runtime_text) if runtime_text.isdigit() else None

    return {
        "title": _text("title"),
        "original_title": _text("originaltitle"),
        "actresses": actors,
        "maker": _text("studio"),
        "director": _text("director"),
        "series": series,
        "label": _text("label"),
        "tags": tags,
        "release_date": _text("premiered"),
        "duration": duration,
        "cover_url": "",
        "url": _text("website"),
    }


def _video_to_meta(video: Video) -> dict:
    return {
        "title": video.title,
        "original_title": video.original_title,
        "actresses": video.actresses or [],
        "maker": video.maker,
        "director": video.director,
        "series": video.series or "",
        "label": video.label,
        "tags": video.tags or [],
        "release_date": video.release_date,
        "duration": video.duration,
        "cover_url": video.cover_path,
        "url": "",
        "sample_images": video.sample_images or [],
    }


def _scraper_to_meta(data: dict) -> dict:
    return {
        "title": data.get("title", ""),
        "original_title": data.get("original_title", ""),
        "actresses": data.get("actors", []),
        "maker": data.get("maker", ""),
        "director": data.get("director", ""),
        "series": data.get("series", ""),
        "label": data.get("label", ""),
        "tags": data.get("tags", []),
        "release_date": data.get("date", ""),
        "duration": data.get("duration"),
        "cover_url": data.get("cover", ""),
        "url": data.get("url", ""),
        "sample_images": data.get("sample_images", []),
        # 63c-5（CD-63c-5）：唯一 summary/rating 流入 meta 的 crossing point。
        # _ 前綴 carrier（search_jav 注入）在此去前綴轉 canonical key，流入 NFO writer。
        "summary": data.get("_summary", ""),
        "rating": data.get("_rating"),
        "actress_aliases": data.get("_actress_aliases", {}),
        "actress_profiles": data.get("_actress_profiles", []),
    }


def _missing_fields(meta: dict, number: str = "") -> List[str]:
    missing = []
    if not meta.get("title") or is_filename_placeholder_title(meta.get("title", ""), number):
        missing.append("title")
    if not meta.get("actresses"):
        missing.append("actresses")
    if not meta.get("maker"):
        missing.append("maker")
    if not meta.get("director"):
        missing.append("director")
    if not meta.get("series"):
        missing.append("series")
    if not meta.get("label"):
        missing.append("label")
    if not meta.get("tags"):
        missing.append("tags")
    if not meta.get("release_date"):
        missing.append("release_date")
    return missing


def _merge_meta(base: dict, supplement: dict, number: str = "") -> tuple:
    """合併 base + supplement，回傳 (merged, fields_filled)"""
    merged = dict(base)
    filled = []
    for key in _FILL_MISSING_REQUIRED:
        if key == "title":
            if (
                (not merged.get(key) or is_filename_placeholder_title(merged.get(key, ""), number))
                and supplement.get(key)
            ):
                merged[key] = supplement[key]
                filled.append(key)
            continue
        if not merged.get(key) and supplement.get(key):
            merged[key] = supplement[key]
            filled.append(key)
    if (not merged.get("cover_url") or not _is_remote_url(merged.get("cover_url", ""))) and supplement.get("cover_url"):
        merged["cover_url"] = supplement["cover_url"]
    if merged.get("sample_images") is None and supplement.get("sample_images"):
        merged["sample_images"] = supplement["sample_images"]
    elif not merged.get("sample_images") and supplement.get("sample_images"):
        merged["sample_images"] = supplement["sample_images"]
    # 63c-5：summary/rating 從 supplement（scraper meta）透傳。base 通常是 DB/NFO meta
    # 無此欄（intentionally NOT carried），fill-if-empty 語意：base 有值不覆蓋。
    if not merged.get("summary") and supplement.get("summary"):
        merged["summary"] = supplement["summary"]
    if merged.get("rating") is None and supplement.get("rating") is not None:
        merged["rating"] = supplement["rating"]
    if supplement.get("actress_aliases"):
        merged["actress_aliases"] = supplement["actress_aliases"]
    if supplement.get("actress_profiles"):
        merged["actress_profiles"] = supplement["actress_profiles"]
    return merged, filled


def _first_video_by_number(repo: VideoRepository, number: str) -> Optional[Video]:
    db_hits = repo.get_by_numbers([number])
    videos = db_hits.get(number, [])
    if not isinstance(videos, list):
        return None
    return videos[0] if videos else None


def _get_video_by_path(repo: VideoRepository, path_uri: str) -> Optional[Video]:
    video = repo.get_by_path(path_uri)
    return video if isinstance(video, Video) else None


def _choose_text(new_value, old_value: str = "", number: str = "", placeholder_sensitive: bool = False) -> str:
    text = str(new_value or "").strip()
    if text and not (placeholder_sensitive and is_filename_placeholder_title(text, number)):
        return text
    return str(old_value or "").strip()


def _choose_list(new_value, old_value=None) -> List[str]:
    if isinstance(new_value, list) and new_value:
        return new_value
    if isinstance(old_value, list):
        return old_value
    return []


def _write_nfo(
    fs_path: str,
    number: str,
    meta: dict,
    write_nfo: bool,
    overwrite_existing: bool,
    has_subtitle: bool,
    user_tags: List[str] = None,
    sidecar_config: dict = None,
) -> bool:
    if not write_nfo:
        return False

    sidecar_paths = resolve_sidecar_paths(fs_path, _sidecar_meta(number, meta), sidecar_config)
    nfo_path = sidecar_paths.nfo_path

    if os.path.exists(nfo_path) and not overwrite_existing:
        return False
    _ensure_parent(nfo_path)

    # 若未傳入 user_tags，從 DB 讀取現有值（確保不被覆蓋）
    if user_tags is None:
        repo = VideoRepository()
        path_uri = to_file_uri(fs_path)
        existing = repo.get_by_path(path_uri)
        user_tags = existing.user_tags if existing else []

    generate_nfo(
        number=number,
        title=meta.get("title", ""),
        original_title=meta.get("original_title", ""),
        actors=meta.get("actresses", []),
        tags=meta.get("tags", []),
        date=meta.get("release_date", ""),
        maker=meta.get("maker", ""),
        url=meta.get("url", ""),
        has_subtitle=has_subtitle,
        output_path=nfo_path,
        director=meta.get("director", ""),
        duration=meta.get("duration"),
        series=meta.get("series", ""),
        label=meta.get("label", ""),
        user_tags=user_tags,
        # 63c-5：canonical key（無 _ 前綴，已於 _scraper_to_meta crossing 去前綴）。
        # DB/NFO base meta 無此欄 → default 空 plot / 無 rating tag。
        summary=meta.get("summary", ""),
        rating=meta.get("rating"),
        thumb_filename=Path(sidecar_paths.cover_path).name,
        poster_filename=Path(sidecar_paths.poster_path).name,
        fanart_filename=Path(sidecar_paths.fanart_path).name,
    )
    return True


def _sync_nfo_metadata_overrides(nfo_path: Path, meta: dict, fields: list[str]) -> bool:
    if not nfo_path.exists():
        return False

    modified = False
    try:
        tree = ET.parse(nfo_path)
        root = tree.getroot()

        def set_text(tag: str, value: str) -> None:
            nonlocal modified
            text = str(value or "").strip()
            if not text:
                return
            elem = root.find(tag)
            if elem is None:
                elem = ET.SubElement(root, tag)
            if (elem.text or "").strip() != text:
                elem.text = text
                modified = True

        def set_series_name(value: str) -> None:
            nonlocal modified
            text = str(value or "").strip()
            if not text:
                return
            set_elem = root.find("set")
            if set_elem is None:
                set_elem = ET.SubElement(root, "set")
            name_elem = set_elem.find("name")
            if name_elem is None:
                name_elem = ET.SubElement(set_elem, "name")
            if (name_elem.text or "").strip() != text:
                name_elem.text = text
                modified = True

        if "title" in fields:
            set_text("title", meta.get("title", ""))

        if "original_title" in fields:
            set_text("originaltitle", meta.get("original_title", ""))

        if any(field in fields for field in ("actors", "actresses")):
            desired_actors = _list_strings(meta.get("actresses"))
            current_actors = [
                (name.text or "").strip()
                for actor in root.findall("actor")
                for name in [actor.find("name")]
                if name is not None and name.text
            ]
            if current_actors != desired_actors:
                actor_insert_at = len(root)
                for idx, child in enumerate(list(root)):
                    if child.tag in {"tag", "genre", "num", "release", "cover", "website", "uniqueid"}:
                        actor_insert_at = idx
                        break
                for actor in list(root.findall("actor")):
                    root.remove(actor)
                    if actor_insert_at > 0:
                        actor_insert_at -= 1
                for offset, actor_name in enumerate(desired_actors):
                    actor_elem = ET.Element("actor")
                    name_elem = ET.SubElement(actor_elem, "name")
                    name_elem.text = actor_name
                    role_elem = ET.SubElement(actor_elem, "role")
                    role_elem.text = ""
                    root.insert(actor_insert_at + offset, actor_elem)
                modified = True

        if "maker" in fields:
            set_text("studio", meta.get("maker", ""))

        if any(field in fields for field in ("date", "release_date")):
            release_date = str(meta.get("release_date") or meta.get("date") or "").strip()
            if release_date:
                set_text("premiered", release_date)
                set_text("release", release_date)
                if len(release_date) >= 4 and release_date[:4].isdigit():
                    set_text("year", release_date[:4])

        if "director" in fields:
            set_text("director", meta.get("director", ""))

        if "label" in fields:
            set_text("label", meta.get("label", ""))

        if "series" in fields:
            set_series_name(meta.get("series", ""))

        if modified:
            ET.indent(tree, space="  ")
            tree.write(nfo_path, encoding="utf-8", xml_declaration=True)
        return modified
    except Exception as exc:
        logger.warning("NFO metadata override sync failed for %s: %s", nfo_path, exc)
        return False


def _write_cover(
    fs_path: str,
    cover_url: str,
    write_cover: bool,
    overwrite_existing: bool,
    number: str = "",
    meta: dict = None,
    sidecar_config: dict = None,
) -> bool:
    if not write_cover:
        return False
    if not cover_url:
        return False
    if not _is_remote_url(cover_url):
        return False

    sidecar_paths = resolve_sidecar_paths(fs_path, _sidecar_meta(number, meta or {}), sidecar_config)
    cover_path = sidecar_paths.cover_path
    if os.path.exists(cover_path) and not overwrite_existing:
        return False
    _ensure_parent(cover_path)

    return download_image(cover_url, cover_path)


def _resolve_extrafanart_dir(
    fs_path: str,
    number: str,
    meta: dict = None,
    sidecar_config: dict = None,
) -> str:
    sidecar_paths = resolve_sidecar_paths(fs_path, _sidecar_meta(number, meta or {}), sidecar_config)
    discovered_sidecar = _discover_existing_centralized_sidecar(number, sidecar_config)
    discovered_nfo = discovered_sidecar.get("nfo_path", "")
    if discovered_nfo:
        return str(Path(discovered_nfo).parent / Path(sidecar_paths.extrafanart_dir).name)
    return sidecar_paths.extrafanart_dir


def _write_extrafanart(
    fs_path: str,
    sample_images: List[str],
    write_extrafanart: bool,
    number: str = "",
    meta: dict = None,
    sidecar_config: dict = None,
) -> List[str]:
    if not write_extrafanart or not sample_images:
        return []

    extrafanart_dir = Path(_resolve_extrafanart_dir(fs_path, number, meta or {}, sidecar_config))
    os.makedirs(str(extrafanart_dir), exist_ok=True)

    written_uris: List[str] = []
    for i, url in enumerate(sample_images):
        dest = str(extrafanart_dir / f"fanart{i+1}.jpg")
        try:
            if download_image(url, dest):
                written_uris.append(to_file_uri(dest))
        except Exception as e:
            logger.warning("extrafanart %d 下載失敗: %s", i + 1, e)
    return written_uris


def _existing_sidecar_cover_path(sidecar_paths) -> str:
    """Return the first existing local cover candidate for current sidecar config."""
    for candidate in (
        sidecar_paths.cover_path,
        sidecar_paths.poster_path,
        sidecar_paths.fanart_path,
    ):
        if candidate and os.path.exists(candidate):
            return candidate
    return ""


def _db_sync_local_assets(
    repo: VideoRepository,
    fs_path: str,
    local_cover_path: str = "",
    nfo_mtime: float = 0.0,
    written_uris: List[str] = None,
) -> None:
    """Sync local sidecar assets without rewriting scraped metadata fields."""
    updates = []
    values = []
    if local_cover_path and os.path.exists(local_cover_path):
        updates.append("cover_path = ?")
        values.append(to_file_uri(local_cover_path))
    if nfo_mtime:
        updates.append("nfo_mtime = ?")
        values.append(nfo_mtime)

    path_uri = to_file_uri(fs_path)
    if updates:
        conn = None
        try:
            conn = get_connection(repo.db_path)
            conn.execute(
                f"UPDATE videos SET {', '.join(updates)}, updated_at = CURRENT_TIMESTAMP WHERE path = ?",
                [*values, path_uri],
            )
            conn.commit()
        except Exception as e:
            logger.warning("DB local asset sync 失敗: %s", e)
        finally:
            if conn:
                conn.close()

    if written_uris:
        repo.update_sample_images(path_uri, written_uris)


def _list_strings(value) -> List[str]:
    if not isinstance(value, list):
        return []
    out: List[str] = []
    for item in value:
        text = str(item or "").strip()
        if text and text not in out:
            out.append(text)
    return out


def _sync_actress_metadata(meta: dict) -> None:
    profiles = meta.get("actress_profiles") or []
    alias_map = meta.get("actress_aliases") or {}
    if not profiles and not alias_map:
        return

    actress_repo = ActressRepository()
    alias_repo = AliasRepository()
    synced_names: set[str] = set()

    for profile in profiles:
        if not isinstance(profile, dict):
            continue
        name = str(profile.get("name") or "").strip()
        if not name:
            continue
        aliases = _list_strings(profile.get("aliases"))
        actress = Actress(
            name=name,
            name_en=str(profile.get("name_en") or "").strip() or None,
            birth=str(profile.get("birth") or "").strip() or None,
            height=profile.get("height"),
            cup=str(profile.get("cup") or "").strip() or None,
            bust=profile.get("bust"),
            waist=profile.get("waist"),
            hip=profile.get("hip"),
            hometown=str(profile.get("hometown") or "").strip() or None,
            aliases=aliases,
            tags=_list_strings(profile.get("tags")),
            nickname=str(profile.get("nickname") or "").strip() or None,
            official_url=str(profile.get("official_url") or "").strip() or None,
            photo_source=str(profile.get("photo_source") or "").strip() or None,
            primary_text_source=str(profile.get("primary_text_source") or "theporndb").strip() or "theporndb",
        )
        try:
            actress_repo.save(actress)
            alias_repo.sync_from_favorite(name, aliases, source="theporndb")
            synced_names.add(name)
        except Exception as exc:
            logger.warning("ThePornDB actress sync failed for %s: %s", name, exc)

    if isinstance(alias_map, dict):
        for raw_name, raw_aliases in alias_map.items():
            name = str(raw_name or "").strip()
            if not name or name in synced_names:
                continue
            aliases = _list_strings(raw_aliases)
            if not aliases:
                continue
            try:
                alias_repo.sync_from_favorite(name, aliases, source="theporndb")
            except Exception as exc:
                logger.warning("ThePornDB alias sync failed for %s: %s", name, exc)


def enrich_single(  # noqa: ranker-invalidate (only updates nfo_mtime, not a corpus field; corpus writes go via _db_upsert → repo.upsert which already has invalidate)
    file_path: str,
    number: str,
    mode: str = "fill_missing",
    write_nfo: bool = True,
    write_cover: bool = True,
    write_extrafanart: bool = False,
    overwrite_existing: bool = False,
    proxy_url: str = "",
    source: Optional[str] = None,
    javbus_lang: Optional[str] = None,
    scraper_data: Optional[dict] = None,
    sidecar_config: dict = None,
) -> EnrichResult:
    _empty = EnrichResult(
        success=False,
        nfo_written=False,
        cover_written=False,
        extrafanart_written=0,
        fields_filled=[],
        source_used="",
        error=None,
    )

    if mode not in VALID_MODES:
        _empty.error = f"不支援的 mode: {mode}（合法值：fill_missing, db_to_sidecar, refresh_full）"
        return _empty

    try:
        fs_path = uri_to_fs_path(file_path)
    except Exception:
        fs_path = file_path

    if not os.path.exists(fs_path):
        _empty.error = "檔案不存在"
        return _empty

    number = str(number or "").strip()
    if not number:
        number = _search_query_from_path(fs_path)
    else:
        number = _prefer_path_date_separator(number, fs_path)
    if not number:
        _empty.error = "缺少可搜尋的番號或檔名"
        return _empty

    repo = VideoRepository()
    path_uri = to_file_uri(fs_path)
    existing_record = _get_video_by_path(repo, path_uri)
    meta: dict = {}
    source_used = ""
    fields_filled: List[str] = []
    discovered_sidecar: dict[str, str] = {}
    metadata_override_fields: List[str] = []
    skip_new_nfo_for_override_only = False

    if mode == "refresh_full":
        if scraper_data is None:
            scraper_data = search_jav(number, proxy_url=proxy_url,
                                      source=source or 'auto', javbus_lang=javbus_lang)
        if not scraper_data:
            _empty.error = f"找不到 {number} 的資料"
            return _empty
        number = str(scraper_data.get("number") or number).strip() or number
        meta = _scraper_to_meta(scraper_data)
        source_used = scraper_data.get("source", "scraper") or "scraper"

    elif mode == "db_to_sidecar":
        video = existing_record or _first_video_by_number(repo, number)
        if not video:
            _empty.error = f"DB 中找不到 {number} 的資料"
            return _empty
        meta = _video_to_meta(video)
        source_used = "db"

    else:
        if existing_record:
            meta = _video_to_meta(existing_record)
            source_used = "db"
        else:
            video = _first_video_by_number(repo, number)
            if video:
                meta = _video_to_meta(video)
                source_used = "db"
            else:
                nfo_p = Path(resolve_sidecar_paths(fs_path, _sidecar_meta(number, meta), sidecar_config).nfo_path)
                if nfo_p.exists():
                    _, root = parse_nfo(str(nfo_p))
                    if root is not None:
                        meta = _nfo_to_meta(root)
                        source_used = "nfo"

        discovered_sidecar = _discover_existing_centralized_sidecar(number, sidecar_config)
        if discovered_sidecar:
            discovered_nfo = discovered_sidecar.get("nfo_path", "")
            if discovered_nfo:
                _, root = parse_nfo(discovered_nfo)
                if root is not None:
                    nfo_meta = _nfo_to_meta(root)
                    meta, nfo_fields_filled = _merge_meta(meta, nfo_meta, number=number)
                    if str(nfo_meta.get("maker") or "").strip():
                        meta["maker"] = str(nfo_meta.get("maker") or "").strip()
                    fields_filled.extend(f for f in nfo_fields_filled if f not in fields_filled)
            if source_used in ("", "db", "nfo"):
                source_used = "sidecar"

        meta, metadata_override_fields = apply_metadata_overrides(
            meta,
            [number, file_path, fs_path],
            sidecar_config,
        )
        if metadata_override_fields:
            fields_filled.extend(f for f in metadata_override_fields if f not in fields_filled)
            if source_used in ("", "db", "nfo"):
                source_used = "override"

        missing = _missing_fields(meta, number=number)
        sidecar_paths = resolve_sidecar_paths(fs_path, _sidecar_meta(number, meta), sidecar_config)
        local_cover = _existing_sidecar_cover_path(sidecar_paths) or discovered_sidecar.get("cover_path", "")
        cover_url = meta.get("cover_url", "")
        needs_cover_data = bool(write_cover and not local_cover and not _is_remote_url(cover_url))
        sidecar_satisfies_requested_assets = bool(discovered_sidecar.get("nfo_path")) and (
            not write_cover or bool(local_cover)
        )
        metadata_missing_needs_scraper = bool(missing)
        if sidecar_satisfies_requested_assets:
            core_missing_fields = {"title", "actresses"}
            effective_missing = [
                field
                for field in missing
                if not (field == "actresses" and field in metadata_override_fields)
            ]
            metadata_missing_needs_scraper = any(field in core_missing_fields for field in effective_missing)
        needs_remote_cover = needs_cover_data and not sidecar_satisfies_requested_assets
        if metadata_missing_needs_scraper or needs_remote_cover:
            if scraper_data is None:
                scraper_data = search_jav(number, proxy_url=proxy_url,
                                          source=source or 'auto', javbus_lang=javbus_lang)
            if not scraper_data:
                if not discovered_sidecar:
                    if not metadata_override_fields:
                        _empty.error = f"找不到 {number} 的資料"
                        return _empty
                    skip_new_nfo_for_override_only = True
            else:
                number = str(scraper_data.get("number") or number).strip() or number
                supplement = _scraper_to_meta(scraper_data)
                meta, fields_filled = _merge_meta(meta, supplement, number=number)
                source_used = scraper_data.get("source", "scraper") or "scraper"

    meta, later_override_fields = apply_metadata_overrides(
        meta,
        [number, file_path, fs_path],
        sidecar_config,
    )
    if later_override_fields:
        metadata_override_fields = later_override_fields
        fields_filled.extend(f for f in later_override_fields if f not in fields_filled)
        if source_used in ("", "db", "nfo"):
            source_used = "override"

    has_subtitle = bool(find_subtitle_files(fs_path))

    # 讀取 DB 現有 user_tags，在 NFO 寫出和 DB upsert 時保留
    path_uri = to_file_uri(fs_path)
    preserved_user_tags = existing_record.user_tags if existing_record else []

    nfo_written = False
    try:
        nfo_written = _write_nfo(
            fs_path=fs_path,
            number=number,
            meta=meta,
            write_nfo=write_nfo and not skip_new_nfo_for_override_only,
            overwrite_existing=overwrite_existing,
            has_subtitle=has_subtitle,
            user_tags=preserved_user_tags,
            sidecar_config=sidecar_config,
        )
    except PermissionError:
        _empty.error = "NFO 寫入失敗，請確認目錄寫入權限"
        return _empty

    cover_url = meta.get("cover_url", "")
    cover_written = _write_cover(
        fs_path=fs_path,
        cover_url=cover_url,
        write_cover=write_cover,
        overwrite_existing=overwrite_existing,
        number=number,
        meta=meta,
        sidecar_config=sidecar_config,
    )

    written_uris = _write_extrafanart(
        fs_path=fs_path,
        sample_images=meta.get("sample_images", []),
        write_extrafanart=write_extrafanart,
        number=number,
        meta=meta,
        sidecar_config=sidecar_config,
    )
    extrafanart_written = len(written_uris)
    sidecar_paths = resolve_sidecar_paths(fs_path, _sidecar_meta(number, meta), sidecar_config)
    nfo_path = Path(sidecar_paths.nfo_path)
    if not nfo_path.exists() and discovered_sidecar.get("nfo_path"):
        nfo_path = Path(discovered_sidecar["nfo_path"])
    nfo_sync_fields: list[str] = []
    for field in [*metadata_override_fields, *fields_filled]:
        if field not in nfo_sync_fields:
            nfo_sync_fields.append(field)
    if nfo_sync_fields and write_nfo and _sync_nfo_metadata_overrides(nfo_path, meta, nfo_sync_fields):
        nfo_written = True
    nfo_mtime = nfo_path.stat().st_mtime if nfo_path.exists() else 0.0
    local_cover = _existing_sidecar_cover_path(sidecar_paths) or discovered_sidecar.get("cover_path", "")

    # DB upsert 在寫檔後執行，才能知道本地封面路徑
    # db_to_sidecar 不打 scraper 也不更新 DB（metadata 不變）
    if mode in ("refresh_full", "fill_missing") and source_used not in ("db", "nfo", ""):
        _db_upsert(repo, number, fs_path, meta, local_cover_path=local_cover,
                   nfo_mtime=nfo_mtime, written_uris=written_uris)
    elif mode in ("refresh_full", "fill_missing"):
        _db_sync_local_assets(
            repo,
            fs_path,
            local_cover_path=local_cover,
            nfo_mtime=nfo_mtime,
            written_uris=written_uris,
        )

    # nfo_mtime 獨立更新：不論 mode/source，只要 NFO 存在就同步 DB
    # 避免 analysis 永遠視為 missing_nfo
    if nfo_path.exists():
        conn = None
        try:
            path_uri = to_file_uri(fs_path)
            conn = get_connection(repo.db_path)
            conn.execute(
                "UPDATE videos SET nfo_mtime = ? WHERE path = ? AND (nfo_mtime IS NULL OR nfo_mtime = 0)",
                (nfo_mtime, path_uri),
            )
            conn.commit()
        except Exception as e:
            logger.warning("nfo_mtime 更新失敗 (%s): %s", number, e)
        finally:
            if conn:
                conn.close()

    return EnrichResult(
        success=True,
        nfo_written=nfo_written,
        cover_written=cover_written,
        extrafanart_written=extrafanart_written,
        fields_filled=fields_filled,
        source_used=source_used,
        error=None,
    )


def _db_upsert(
    repo: VideoRepository, number: str, fs_path: str, meta: dict,
    local_cover_path: str = "",
    nfo_mtime: float = 0.0,
    written_uris: List[str] = None,
) -> None:
    """更新 DB 記錄。fs_path 必須是已解析的 FS 路徑（非 file:/// URI）。"""
    try:
        path_uri = to_file_uri(fs_path)

        # 讀取現有記錄以保留 cover_path 和 user_tags
        existing = repo.get_by_path(path_uri)

        # cover_path 只存本地 file:/// URI
        # 若有本地封面路徑則轉 URI；否則保留 DB 既有值（透過傳空字串讓 upsert 不覆蓋）
        cover_uri = ""
        if local_cover_path and os.path.exists(local_cover_path):
            cover_uri = to_file_uri(local_cover_path)
        elif existing and existing.cover_path:
            cover_uri = existing.cover_path

        # 保留 DB 既有 user_tags（不被 scraper 覆蓋）
        preserved_user_tags = existing.user_tags if existing else []

        # §b1 / Codex P1: 只有磁碟真寫出 extrafanart 檔案才更新 DB sample_images；
        # 使用 written_uris（local file:/// URIs），不寫 scraper 遠端 URL
        if written_uris:
            sample_imgs = written_uris
        else:
            sample_imgs = existing.sample_images if existing else []

        video = Video(
            path=path_uri,
            number=number or (existing.number if existing else None),
            title=_choose_text(
                meta.get("title", ""),
                existing.title if existing else "",
                number=number,
                placeholder_sensitive=True,
            ),
            original_title=_choose_text(
                meta.get("original_title", ""),
                existing.original_title if existing else "",
            ),
            actresses=_choose_list(
                sanitize_actor_names(meta.get("actresses", []), maker=meta.get("maker", ""), number=number),
                sanitize_actor_names(
                    existing.actresses if existing else [],
                    maker=existing.maker if existing else "",
                    number=number or (existing.number if existing else ""),
                ),
            ),
            maker=_choose_text(meta.get("maker", ""), existing.maker if existing else ""),
            director=_choose_text(meta.get("director", ""), existing.director if existing else ""),
            series=_choose_text(meta.get("series", ""), existing.series if existing else "") or None,
            label=_choose_text(meta.get("label", ""), existing.label if existing else ""),
            tags=_choose_list(meta.get("tags", []), existing.tags if existing else []),
            user_tags=preserved_user_tags,
            sample_images=sample_imgs,
            duration=meta.get("duration") if meta.get("duration") is not None else (existing.duration if existing else None),
            size_bytes=existing.size_bytes if existing else 0,
            cover_path=cover_uri,
            release_date=_choose_text(meta.get("release_date", ""), existing.release_date if existing else ""),
            mtime=existing.mtime if existing else 0.0,
            nfo_mtime=nfo_mtime,
        )
        repo.upsert(video)
        _sync_actress_metadata(meta)
    except Exception as e:
        logger.warning("DB upsert 失敗: %s", e)


def _db_upsert_samples_only(repo: VideoRepository, fs_path: str, sample_images: list) -> None:
    """只更新 DB 的 sample_images 欄位（不觸碰其他欄位）。"""
    path_uri = to_file_uri(fs_path)
    repo.update_sample_images(path_uri, sample_images)


def fetch_samples_only(
    file_path: str,
    number: str,
    proxy_url: str = "",
    sidecar_config: dict = None,
) -> EnrichResult:
    """只補抓劇照：呼叫 scraper → 下載 extrafanart → 更新 DB sample_images。
    不寫 NFO / cover / 其他欄位。
    """
    _empty = EnrichResult(
        success=False,
        nfo_written=False,
        cover_written=False,
        extrafanart_written=0,
        fields_filled=[],
        source_used="",
        error=None,
    )

    try:
        fs_path = uri_to_fs_path(file_path)
    except Exception:
        fs_path = file_path

    if not os.path.exists(fs_path):
        logger.warning("[fetch_samples_only] 檔案不存在: %s", fs_path)
        _empty.error = "檔案不存在"
        return _empty

    number = str(number or "").strip()
    if not number:
        number = _search_query_from_path(fs_path)
    else:
        number = _prefer_path_date_separator(number, fs_path)
    if not number:
        _empty.error = "缺少可搜尋的番號或檔名"
        return _empty

    meta = search_jav(number, proxy_url=proxy_url,
                      source="auto", javbus_lang=None)
    if not meta:
        logger.warning("[fetch_samples_only] 找不到資料: %s", number)
        _empty.error = f"找不到 {number} 的資料"
        return _empty

    number = _prefer_path_date_separator(str(meta.get("number") or number).strip() or number, fs_path)
    sample_images = meta.get("sample_images", [])
    sidecar_meta = _sidecar_meta(number, _scraper_to_meta(meta))
    written_uris = _write_extrafanart(
        fs_path,
        sample_images,
        write_extrafanart=True,
        number=number,
        meta=sidecar_meta,
        sidecar_config=sidecar_config,
    )

    if written_uris:
        repo = VideoRepository()
        _db_upsert_samples_only(repo, fs_path, written_uris)

    logger.info("[fetch_samples_only] %s: %d samples downloaded", number, len(written_uris))
    return EnrichResult(
        success=True,
        nfo_written=False,
        cover_written=False,
        extrafanart_written=len(written_uris),
        fields_filled=[],
        source_used=meta.get("source", ""),
        error=None,
    )


def resolve_nfo_cover_paths(file_path: str, sidecar_config: dict = None, metadata: dict = None) -> tuple:
    """由影片 file_path 推導目標 NFO / cover 的 FS 路徑。

    復用 enrich_single / _write_nfo / _write_cover 的同一套路徑邏輯：
    先以 uri_to_fs_path() 解析（fallback 原值），再 with_suffix。
    回傳 (nfo_path, cover_path)，兩者皆為當前環境 FS 字串路徑。

    ⚠️ 路徑邏輯必須與 `_write_nfo`（with_suffix(".nfo")）/ `_write_cover`
    （with_suffix(".jpg")）保持同步——62a-1 的 refresh_full 分裂守衛
    （web/routers/scraper.py enrich_single_endpoint）靠本函數判斷檔案是否已存在。
    若 writer 改了 cover 命名（poster.jpg / .png / fanart 等）或 fs_path 推導，
    本函數要一起改，否則守衛會悄悄檢查錯路徑（false-allow 重現分裂 / false-block 打爆缺封面 quick-enrich）。
    """
    try:
        fs_path = uri_to_fs_path(file_path)
    except Exception:
        fs_path = file_path
    sidecar_paths = resolve_sidecar_paths(fs_path, metadata or {}, sidecar_config)
    return sidecar_paths.nfo_path, sidecar_paths.cover_path

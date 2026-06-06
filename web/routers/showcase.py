"""
Showcase API 路由 - 影片展示資料端點

端點：
- GET /api/showcase/videos        — 取得所有影片資料（供 Showcase 頁面客戶端渲染）
- GET /api/showcase/video?path=   — 取得單筆影片資料（供 T3 enrich 後刷新卡片）
"""

from pathlib import Path
import re
from urllib.parse import quote

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from core.database import VideoRepository, get_db_path, init_db
from core.directory_labels import (
    DEFAULT_DIRECTORY_LABEL,
    configured_directories_from_config,
    directory_label_for_path,
)
from core.filename_identity import MediaIdentity, parse_media_identity
from core.path_utils import uri_to_fs_path
from core.logger import get_logger
from core.config import load_config

logger = get_logger(__name__)

router = APIRouter(prefix="/api/showcase", tags=["showcase"])


def _gallery_image_param(path_or_uri: str) -> str:
    """Return the filesystem path shape expected by /api/gallery/image."""
    local_path = uri_to_fs_path(path_or_uri)
    if local_path.startswith("\\\\"):
        return "//" + local_path.lstrip("\\").replace("\\", "/")
    return local_path


def _path_filename(path: str) -> str:
    try:
        return Path(uri_to_fs_path(path)).name
    except Exception as exc:
        logger.error("Showcase 路徑檔名解析失敗: %s", exc)
        return Path(str(path or "")).name


def _path_stem(path: str) -> str:
    try:
        return Path(uri_to_fs_path(path)).stem
    except Exception as exc:
        logger.error("Showcase 路徑檔名解析失敗: %s", exc)
        return Path(str(path or "")).stem


def _identity_for_video(v) -> MediaIdentity:
    filename = _path_filename(v.path)
    identity = parse_media_identity(filename)
    if not identity.canonical_number and v.number:
        identity = parse_media_identity(str(v.number))
    return identity


def _work_key_for(v, identity: MediaIdentity | None = None) -> str:
    identity = identity or _identity_for_video(v)
    return identity.work_key or identity.canonical_number or str(v.number or "") or str(v.path)


def _display_number_for(v, identity: MediaIdentity) -> str:
    return (
        identity.display_number
        or identity.work_key
        or identity.canonical_number
        or str(v.number or "")
    )


def _display_title_for(v, identity: MediaIdentity) -> str:
    title = str(v.title or "")
    display_number = _display_number_for(v, identity)
    if not title or not display_number:
        return title

    title_text = title.strip()
    title_without_prefix = re.sub(r"^\[[A-Z0-9][A-Z0-9-]*\]\s*", "", title_text, flags=re.IGNORECASE)
    generated_titles = {_path_stem(v.path), str(v.number or ""), identity.raw_stem}
    if title_text in generated_titles or title_without_prefix in generated_titles:
        return display_number

    return title


def _part_sort_key(part: str | None) -> tuple[int, int | str]:
    if not part:
        return (0, 0)
    value = str(part).upper()
    if value.startswith("CD") and value[2:].isdigit():
        return (1, int(value[2:]))
    if value.isdigit():
        return (1, int(value))
    if len(value) == 1 and "A" <= value <= "Z":
        return (1, ord(value) - ord("A") + 1)
    return (2, value)


def _video_group_sort_key(item: tuple) -> tuple:
    v, identity = item
    return (_part_sort_key(identity.part_index), str(v.path or ""))


def _video_metadata_score(v, identity: MediaIdentity) -> int:
    score = 0
    title = _display_title_for(v, identity)
    if title and title != _display_number_for(v, identity):
        score += 2
    for value in (
        v.original_title,
        v.actresses,
        v.maker,
        v.release_date,
        v.tags,
        v.director,
        v.duration,
        v.series,
        v.label,
        v.sample_images,
    ):
        if value:
            score += 1
    return score


def _work_primary_sort_key(item: tuple) -> tuple:
    v, identity = item
    return (
        0 if v.cover_path else 1,
        0 if (v.nfo_mtime or 0) > 0 else 1,
        -_video_metadata_score(v, identity),
        _video_group_sort_key(item),
    )


def _serialize_work_file(
    v,
    identity: MediaIdentity,
    directory_label: str = DEFAULT_DIRECTORY_LABEL,
) -> dict:
    return {
        "path": v.path,
        "filename": _path_filename(v.path),
        "number": identity.canonical_number or v.number or "",
        "directory_label": directory_label,
        "work_key": _work_key_for(v, identity),
        "part_index": identity.part_index,
        "variant_flags": {
            "subtitle_cn": identity.variant_flags.subtitle_cn,
            "cracked": identity.variant_flags.cracked,
        },
        "variant_label": identity.variant_label,
        "size": v.size_bytes,
        "mtime": int(v.mtime) if v.mtime else 0,
        "_cover_path": v.cover_path or "",
        "_has_cover": bool(v.cover_path),
        "_has_nfo": (v.nfo_mtime or 0) > 0,
    }


def _public_work_files(files: list[dict]) -> list[dict]:
    return [
        {key: value for key, value in file.items() if not str(key).startswith("_")}
        for file in files
    ]


def _group_videos_by_work(videos: list, video_labels: dict[str, str] | None = None) -> list[tuple]:
    video_labels = video_labels or {}
    groups: dict[str, list[tuple]] = {}
    for video in videos:
        identity = _identity_for_video(video)
        groups.setdefault(_work_key_for(video, identity), []).append((video, identity))

    grouped = []
    for items in groups.values():
        ordered = sorted(items, key=_video_group_sort_key)
        primary_video, primary_identity = sorted(items, key=_work_primary_sort_key)[0]
        files = [
            _serialize_work_file(
                video,
                identity,
                video_labels.get(video.path, DEFAULT_DIRECTORY_LABEL),
            )
            for video, identity in ordered
        ]
        grouped.append((primary_video, primary_identity, files))

    return grouped


def _serialize_video(
    v,
    path_mappings: dict,
    identity: MediaIdentity | None = None,
    files: list[dict] | None = None,
    directory_label: str = DEFAULT_DIRECTORY_LABEL,
) -> dict:
    """將 Video ORM 物件序列化為前端 JSON dict（列表端點與單筆端點共用）"""
    identity = identity or _identity_for_video(v)
    work_size = sum(int(file.get("size") or 0) for file in files) if files else v.size_bytes
    work_files = files or [_serialize_work_file(v, identity, directory_label)]
    work_cover_path = v.cover_path or next(
        (file.get("_cover_path") for file in work_files if file.get("_cover_path")),
        "",
    )
    work_has_cover = bool(work_cover_path) or any(file.get("_has_cover") for file in work_files)
    work_has_nfo = (v.nfo_mtime or 0) > 0 or any(file.get("_has_nfo") for file in work_files)
    cover_url = ""
    if work_cover_path:
        local_path = _gallery_image_param(work_cover_path)
        cover_url = f"/api/gallery/image?path={quote(local_path, safe='')}"

    sample_urls = []
    for img_uri in (v.sample_images or []):
        local_path = _gallery_image_param(img_uri)
        sample_urls.append(f"/api/gallery/image?path={quote(local_path, safe='')}")

    return {
        "path": v.path,                                          # file:/// URI（開啟影片用）
        "title": _display_title_for(v, identity),
        "original_title": v.original_title,
        "actresses": ','.join(v.actresses) if v.actresses else '',  # 逗號分隔字串
        "number": _display_number_for(v, identity),
        "directory_label": directory_label,
        "maker": v.maker,
        "release_date": v.release_date,
        "tags": ','.join(v.tags) if v.tags else '',              # 逗號分隔字串
        "size": work_size,
        "cover_url": cover_url,                                  # /api/gallery/image?path=...
        "mtime": int(v.mtime) if v.mtime else 0,                 # Unix timestamp 整數
        "director": v.director or '',
        "duration": v.duration,                                  # Optional[int]，None 時前端 x-show 隱藏
        "series": v.series or '',
        "label": v.label or '',
        "sample_images": sample_urls,
        "user_tags": v.user_tags or [],              # list[str]，空時回空 list
        "has_cover": work_has_cover,
        "has_nfo": work_has_nfo,
        "work_key": _work_key_for(v, identity),
        "part_index": identity.part_index,
        "variant_flags": {
            "subtitle_cn": identity.variant_flags.subtitle_cn,
            "cracked": identity.variant_flags.cracked,
        },
        "variant_label": identity.variant_label,
        "files": _public_work_files(work_files),
        "file_count": len(work_files),
    }


def _get_configured_dirs(config: dict) -> tuple[list, dict]:
    """從 config 取出 configured directories 與 path_mappings（列表與單筆端點共用）"""
    return configured_directories_from_config(config)


@router.get("/videos")
async def get_videos():
    """取得所有影片資料（用於 Showcase 頁面客戶端渲染）"""
    try:
        db_path = get_db_path()

        # 空庫情境：資料庫檔案不存在
        if not db_path.exists():
            return JSONResponse({
                "success": True,
                "videos": [],
                "total": 0,
                "total_files": 0,
            })

        init_db(db_path)  # 確保 schema 存在（防止半毀損 DB）
        repo = VideoRepository(db_path)
        repaired = repo.repair_missing_file_stats()
        if repaired:
            logger.info("Showcase repaired %d missing file stat rows", repaired)

        # 只取「當前設定資料夾」底下的記錄（DB 保留全部當 cache）
        config = load_config()
        configured_dirs, path_mappings = _get_configured_dirs(config)

        video_labels = {}
        all_videos = []
        for video in repo.get_all():
            label = directory_label_for_path(video.path, configured_dirs)
            if label is None:
                continue
            video_labels[video.path] = label
            all_videos.append(video)

        grouped_videos = _group_videos_by_work(all_videos, video_labels)
        videos_json = [
            _serialize_video(
                v,
                path_mappings,
                identity=identity,
                files=files,
                directory_label=video_labels.get(v.path, DEFAULT_DIRECTORY_LABEL),
            )
            for v, identity, files in grouped_videos
        ]

        return JSONResponse({
            "success": True,
            "videos": videos_json,
            "total": len(videos_json),
            "total_files": len(all_videos),
        })

    except Exception as e:
        logger.error("取得影片資料失敗: %s", e)
        return JSONResponse({
            "success": False,
            "error": "取得影片資料失敗",
            "videos": [],
            "total": 0
        }, status_code=500)


@router.get("/video")
async def get_video(path: str = Query(..., description="file:/// URI")):
    """取得單筆影片資料（用於 T3 refreshVideoData enrich 後刷新卡片）"""
    try:
        db_path = get_db_path()
        if not db_path.exists():
            return JSONResponse({"success": False, "error": "video not found"}, status_code=404)

        init_db(db_path)
        repo = VideoRepository(db_path)
        repo.repair_missing_file_stats(paths=[path])

        config = load_config()
        configured_dirs, path_mappings = _get_configured_dirs(config)

        requested_label = directory_label_for_path(path, configured_dirs)
        if requested_label is None:
            return JSONResponse({"success": False, "error": "video not found"}, status_code=404)

        v = repo.get_by_path(path)
        if v is None:
            return JSONResponse({"success": False, "error": "video not found"}, status_code=404)

        video_labels = {}
        all_videos = []
        for video in repo.get_all():
            label = directory_label_for_path(video.path, configured_dirs)
            if label is None:
                continue
            video_labels[video.path] = label
            all_videos.append(video)
        identity = _identity_for_video(v)
        work_key = _work_key_for(v, identity)
        files = []
        for candidate in all_videos:
            candidate_identity = _identity_for_video(candidate)
            if _work_key_for(candidate, candidate_identity) == work_key:
                files.append((candidate, candidate_identity))
        files = [
            _serialize_work_file(
                video,
                file_identity,
                video_labels.get(video.path, DEFAULT_DIRECTORY_LABEL),
            )
            for video, file_identity in sorted(files, key=_video_group_sort_key)
        ]

        return JSONResponse({
            "success": True,
            "video": _serialize_video(
                v,
                path_mappings,
                identity=identity,
                files=files,
                directory_label=requested_label,
            ),
        })

    except Exception as e:
        logger.error("取得單筆影片失敗: %s", e)
        return JSONResponse({"success": False, "error": "取得影片資料失敗"}, status_code=500)

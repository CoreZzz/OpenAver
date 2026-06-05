"""Resolve metadata sidecar paths for video files.

The resolver is intentionally pure: it does not create directories, write files,
or download assets.  Writers in organizer/enricher can call this module to keep
NFO/image path decisions in one place.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any, Mapping

from core.path_utils import to_file_uri, uri_to_fs_path


DEFAULT_SIDECAR_CONFIG: dict[str, Any] = {
    "mode": "alongside",
    "root_dir": "",
    "layout": "{maker}/{num}",
    "nfo_filename": "{num}.nfo",
    "cover_filename": "cover.jpg",
    "poster_filename": "poster.jpg",
    "fanart_filename": "fanart.jpg",
    "extrafanart_dir": "extrafanart",
}

_ILLEGAL_PATH_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_SEPARATOR_RE = re.compile(r"[\\/]+")


@dataclass(frozen=True)
class SidecarPaths:
    mode: str
    base_dir: str
    nfo_path: str
    cover_path: str
    poster_path: str
    fanart_path: str
    extrafanart_dir: str

    def as_uri_dict(self, path_mappings: dict | None = None) -> dict[str, str]:
        """Return all paths as file URIs using core.path_utils."""
        return {
            "base_dir": to_file_uri(self.base_dir, path_mappings),
            "nfo_path": to_file_uri(self.nfo_path, path_mappings),
            "cover_path": to_file_uri(self.cover_path, path_mappings),
            "poster_path": to_file_uri(self.poster_path, path_mappings),
            "fanart_path": to_file_uri(self.fanart_path, path_mappings),
            "extrafanart_dir": to_file_uri(self.extrafanart_dir, path_mappings),
        }


def resolve_sidecar_paths(
    video_path: str,
    metadata: Mapping[str, Any] | None = None,
    config: Mapping[str, Any] | None = None,
) -> SidecarPaths:
    """Resolve NFO/image paths for a video.

    Args:
        video_path: Native filesystem path or file URI.
        metadata: Scraper/DB metadata.  Recognized keys include number, num,
            canonical_number, title, maker, actors, actor, and date.
        config: Either the full app config dict or a sidecar section dict.

    Returns:
        SidecarPaths with native filesystem paths.
    """
    video_fs_path = uri_to_fs_path(video_path)
    video = Path(video_fs_path)
    metadata = metadata or {}
    sidecar = _sidecar_config(config)

    if sidecar["mode"] == "centralized" and str(sidecar.get("root_dir", "")).strip():
        return _centralized_paths(video, metadata, sidecar)

    return _alongside_paths(video)


def _alongside_paths(video: Path) -> SidecarPaths:
    base_dir = video.parent
    stem = video.stem
    return SidecarPaths(
        mode="alongside",
        base_dir=str(base_dir),
        nfo_path=str(base_dir / f"{stem}.nfo"),
        cover_path=str(base_dir / f"{stem}.jpg"),
        poster_path=str(base_dir / f"{stem}-poster.jpg"),
        fanart_path=str(base_dir / f"{stem}-fanart.jpg"),
        extrafanart_dir=str(base_dir / "extrafanart"),
    )


def _centralized_paths(
    video: Path,
    metadata: Mapping[str, Any],
    sidecar: Mapping[str, Any],
) -> SidecarPaths:
    data = _template_data(video, metadata)
    root = Path(uri_to_fs_path(str(sidecar["root_dir"])))
    layout_parts = _render_layout(str(sidecar.get("layout") or "{num}"), data)
    base_dir = root.joinpath(*layout_parts) if layout_parts else root

    nfo_name = _render_filename(sidecar.get("nfo_filename"), data, f"{data['num']}.nfo")
    cover_name = _render_filename(sidecar.get("cover_filename"), data, "cover.jpg")
    poster_name = _render_filename(sidecar.get("poster_filename"), data, "poster.jpg")
    fanart_name = _render_filename(sidecar.get("fanart_filename"), data, "fanart.jpg")
    extrafanart_name = _sanitize_component(
        _render_template(str(sidecar.get("extrafanart_dir") or "extrafanart"), data),
        "extrafanart",
    )

    return SidecarPaths(
        mode="centralized",
        base_dir=str(base_dir),
        nfo_path=str(base_dir / nfo_name),
        cover_path=str(base_dir / cover_name),
        poster_path=str(base_dir / poster_name),
        fanart_path=str(base_dir / fanart_name),
        extrafanart_dir=str(base_dir / extrafanart_name),
    )


def _sidecar_config(config: Mapping[str, Any] | None) -> dict[str, Any]:
    if not config:
        return dict(DEFAULT_SIDECAR_CONFIG)

    raw = config.get("sidecar") if "sidecar" in config else config
    if not isinstance(raw, Mapping):
        return dict(DEFAULT_SIDECAR_CONFIG)

    sidecar = dict(DEFAULT_SIDECAR_CONFIG)
    sidecar.update(raw)
    if sidecar.get("mode") not in {"alongside", "centralized"}:
        sidecar["mode"] = "alongside"
    return sidecar


def _template_data(video: Path, metadata: Mapping[str, Any]) -> dict[str, str]:
    number = (
        metadata.get("canonical_number")
        or metadata.get("number")
        or metadata.get("num")
        or video.stem
    )
    actors = metadata.get("actors") or metadata.get("actresses") or []
    if isinstance(actors, str):
        actors = [actors]
    actor = metadata.get("actor") or (actors[0] if actors else "")
    date = str(metadata.get("date") or metadata.get("release_date") or "")

    return {
        "num": str(number),
        "number": str(number),
        "stem": video.stem,
        "title": str(metadata.get("title") or ""),
        "maker": str(metadata.get("maker") or ""),
        "actor": str(actor or ""),
        "actors": ", ".join(str(a) for a in actors if a),
        "date": date,
        "year": date[:4] if len(date) >= 4 else "",
        "month": date[5:7] if len(date) >= 7 else "",
        "day": date[8:10] if len(date) >= 10 else "",
    }


def _render_layout(template: str, data: Mapping[str, str]) -> list[str]:
    parts: list[str] = []
    for raw_part in _SEPARATOR_RE.split(template):
        rendered = _render_template(raw_part, data)
        cleaned = _sanitize_component(rendered, "")
        if cleaned:
            parts.append(cleaned)
    if not parts:
        parts.append(_sanitize_component(data.get("num", ""), "unknown"))
    return parts


def _render_filename(template: Any, data: Mapping[str, str], fallback: str) -> str:
    rendered = _render_template(str(template or fallback), data)
    return _sanitize_component(rendered, fallback)


def _render_template(template: str, data: Mapping[str, str]) -> str:
    rendered = template
    for key, value in data.items():
        rendered = rendered.replace("{" + key + "}", value)
    return rendered


def _sanitize_component(value: str, fallback: str) -> str:
    cleaned = _ILLEGAL_PATH_CHARS.sub(" ", value)
    cleaned = re.sub(r"\s+", " ", cleaned).strip().rstrip(". ")
    return cleaned or fallback

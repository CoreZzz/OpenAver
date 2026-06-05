"""Helpers for per-scan-directory content labels.

The gallery config keeps directories as a plain list for backward
compatibility, while ``directory_labels`` stores metadata keyed by the original
configured path.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from core.path_utils import is_path_under_dir, to_file_uri


DIRECTORY_LABEL_CENSORED = "censored"
DIRECTORY_LABEL_UNCENSORED = "uncensored"
DEFAULT_DIRECTORY_LABEL = DIRECTORY_LABEL_CENSORED
VALID_DIRECTORY_LABELS = {
    DIRECTORY_LABEL_CENSORED,
    DIRECTORY_LABEL_UNCENSORED,
}


@dataclass(frozen=True)
class ConfiguredDirectory:
    raw_path: str
    uri: str
    label: str


def normalize_directory_label(value: Any) -> str:
    """Normalize a directory content label to the persisted enum."""
    text = str(value or "").strip().lower()
    if text == DIRECTORY_LABEL_UNCENSORED:
        return DIRECTORY_LABEL_UNCENSORED
    if text == DIRECTORY_LABEL_CENSORED:
        return DIRECTORY_LABEL_CENSORED
    return DEFAULT_DIRECTORY_LABEL


def normalize_directory_labels(
    directories: list[str],
    labels: Mapping[str, Any] | None,
) -> dict[str, str]:
    """Return labels only for currently configured directories."""
    labels = labels or {}
    return {
        path: normalize_directory_label(labels.get(path))
        for path in directories
        if isinstance(path, str) and path
    }


def configured_directories_from_config(
    config: Mapping[str, Any],
) -> tuple[list[ConfiguredDirectory], dict]:
    """Build configured directory URI + label records from app config."""
    gallery = config.get("gallery", {}) if isinstance(config, Mapping) else {}
    if not isinstance(gallery, Mapping):
        return [], {}

    directories = gallery.get("directories", [])
    if not isinstance(directories, list):
        directories = []

    labels = gallery.get("directory_labels", {})
    if not isinstance(labels, Mapping):
        labels = {}

    path_mappings = gallery.get("path_mappings", {})
    if not isinstance(path_mappings, dict):
        path_mappings = {}

    configured: list[ConfiguredDirectory] = []
    for path in directories:
        if not isinstance(path, str) or not path.strip():
            continue
        try:
            uri = to_file_uri(path, path_mappings)
        except ValueError:
            continue
        label = labels.get(path, labels.get(uri))
        configured.append(ConfiguredDirectory(
            raw_path=path,
            uri=uri,
            label=normalize_directory_label(label),
        ))

    configured.sort(key=lambda item: len(item.uri), reverse=True)
    return configured, path_mappings


def directory_label_for_path(
    path: str,
    configured_dirs: list[ConfiguredDirectory],
) -> str | None:
    """Return the label for the configured directory containing ``path``."""
    for directory in configured_dirs:
        if is_path_under_dir(path, directory.uri):
            return directory.label
    return None

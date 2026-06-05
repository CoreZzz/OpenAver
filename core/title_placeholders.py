"""Helpers for detecting filename-derived placeholder titles."""

from __future__ import annotations

import re

from core.filename_identity import parse_media_identity

_VARIANT_TOKENS = {"C", "U", "UC"}
_QUALITY_TOKENS = {"4K", "8K", "HD", "FHD", "UHD", "SD", "VR", "1080P", "720P", "2160P", "60FPS"}


def _canonical_number(value: str) -> str:
    identity = parse_media_identity(value)
    return identity.canonical_number or str(value or "").strip().upper()


def _title_suffix_token_allowed(token: str) -> bool:
    token = str(token or "").upper()
    return (
        token in _VARIANT_TOKENS
        or token in _QUALITY_TOKENS
        or bool(
            re.fullmatch(r"\d{1,2}", token)
            or re.fullmatch(r"[A-Z]", token)
            or re.fullmatch(r"CD\d{1,2}", token)
        )
    )


def _strip_bracketed_number_prefix(title: str, canonical_number: str) -> str:
    current = str(title or "").strip()
    while True:
        match = re.match(r"^\[([^\]]+)\]\s*(.*)$", current)
        if not match:
            return current
        if _canonical_number(match.group(1)) != canonical_number:
            return current
        current = match.group(2).strip()


def is_filename_placeholder_title(title: str, number: str) -> bool:
    """Return True when title is only a number plus file variant markers."""

    if not title or not number:
        return False

    canonical_number = _canonical_number(number)
    candidate = _strip_bracketed_number_prefix(title, canonical_number)
    identity = parse_media_identity(candidate)
    if identity.canonical_number != canonical_number:
        return False

    match_start = identity.raw_stem.upper().find(identity.raw_match.upper())
    if match_start < 0:
        return False
    prefix = identity.raw_stem[:match_start]
    if re.sub(r"[\s\[\]\(\)_\-.]+", "", prefix):
        return False

    return all(_title_suffix_token_allowed(token) for token in identity.raw_tokens)

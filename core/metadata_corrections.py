"""Local metadata corrections for confirmed cross-release identities."""

from __future__ import annotations

from copy import deepcopy
import re
from typing import Iterable

from core.filename_identity import parse_media_identity


BUILTIN_METADATA_OVERRIDES = [
    {
        "numbers": ["FC2-PPV-1313698", "FC2-1313698", "PPPD-206"],
        "actresses": ["菅野松雪"],
    },
]

BUILTIN_TITLE_ACTRESS_RULES = [
    {
        "maker": "ザ・流し屋",
        "title_contains": ["杏ちゃん"],
        "actresses": ["水希杏"],
        "number_prefixes": ["FC2-", "FC2PPV"],
    },
]

UNKNOWN_ACTRESS_NAMES = {
    "",
    "-",
    "--",
    "---",
    "----",
    "N/A",
    "NA",
    "LOGIN",
    "NONE",
    "NULL",
    "UNKNOWN",
    "UNK",
    "不明",
    "未知",
    "不詳",
    "匿名",
    "素人",
    "なし",
    "ない",
    "ないない",
    "ログイン",
    "ログインする",
    "会員ログイン",
    "不明女優",
    "女優不明",
    "出演者不明",
}

_DATE_STYLE_NUMBER_RE = re.compile(r"^\d{6}[-_]\d{2,3}$")


def _dedupe(values: Iterable[str]) -> list[str]:
    out: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in out:
            out.append(text)
    return out


def _is_fc2_keys(number_keys: set[str]) -> bool:
    return any(key.startswith("FC2-") or key.startswith("FC2PPV") for key in number_keys)


def sanitize_actor_names(values: Iterable[str], maker: str = "", number: str = "") -> list[str]:
    actors = [
        name
        for name in _dedupe(values or [])
        if name.strip().upper() not in UNKNOWN_ACTRESS_NAMES
    ]
    maker_text = str(maker or "").strip()
    if maker_text and actors == [maker_text] and _is_fc2_keys(_number_keys(number)):
        return []
    return actors


def _number_keys(value: str) -> set[str]:
    text = str(value or "").strip()
    if not text:
        return set()

    keys = {text.upper()}
    identity = parse_media_identity(text)
    candidates = [
        identity.canonical_number,
        identity.search_number,
        identity.display_number,
        identity.raw_match,
    ]
    if not _DATE_STYLE_NUMBER_RE.match(identity.canonical_number or ""):
        candidates.extend(identity.number_aliases)

    for candidate in candidates:
        if candidate:
            keys.add(str(candidate).strip().upper())

    for key in list(keys):
        if key.startswith("FC2-PPV-"):
            digits = key.rsplit("-", 1)[-1]
            keys.add(f"FC2-{digits}")
            keys.add(f"FC2PPV-{digits}")
            keys.add(f"FC2PPV{digits}")
        elif key.startswith("FC2-"):
            digits = key.rsplit("-", 1)[-1]
            if digits.isdigit():
                keys.add(f"FC2-PPV-{digits}")
                keys.add(f"FC2PPV-{digits}")
                keys.add(f"FC2PPV{digits}")
    return keys


def _override_matches(override: dict, number_keys: set[str]) -> bool:
    override_keys: set[str] = set()
    for number in override.get("numbers") or []:
        override_keys.update(_number_keys(str(number or "")))
    return bool(number_keys & override_keys)


def _configured_overrides(config: dict | None) -> list[dict]:
    if not isinstance(config, dict):
        return []
    raw = config.get("metadata_overrides", [])
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def _apply_title_actress_rules(corrected: dict, number_keys: set[str]) -> list[str]:
    changed: list[str] = []
    title = str(corrected.get("title") or corrected.get("original_title") or "").strip()
    maker = str(corrected.get("maker") or "").strip()
    if not title:
        return []

    for rule in BUILTIN_TITLE_ACTRESS_RULES:
        prefixes = tuple(str(prefix or "").upper() for prefix in rule.get("number_prefixes") or [])
        if prefixes and not any(key.startswith(prefixes) for key in number_keys):
            continue
        rule_maker = str(rule.get("maker") or "").strip()
        if rule_maker and maker != rule_maker:
            continue
        needles = [str(value or "").strip() for value in rule.get("title_contains") or []]
        if needles and not any(needle and needle in title for needle in needles):
            continue

        actresses = sanitize_actor_names(rule.get("actresses") or rule.get("actors") or [])
        if not actresses:
            continue
        current = sanitize_actor_names(corrected.get("actresses") or corrected.get("actors") or [])
        if current and current != ["杏"]:
            continue
        for field in ("actors", "actresses"):
            if corrected.get(field) != actresses:
                corrected[field] = actresses
                changed.append(field)
    return changed


def apply_metadata_overrides(
    meta: dict,
    numbers: str | Iterable[str],
    config: dict | None = None,
) -> tuple[dict, list[str]]:
    """Apply built-in and configured metadata overrides to a metadata dict."""

    corrected = deepcopy(meta or {})
    changed: list[str] = []
    if isinstance(numbers, str):
        number_values = [numbers]
    else:
        number_values = list(numbers or [])

    number_keys: set[str] = set()
    for number in [*number_values, corrected.get("number", "")]:
        number_keys.update(_number_keys(str(number or "")))
    if not number_keys:
        for field in ("actors", "actresses"):
            if field in corrected:
                sanitized = sanitize_actor_names(corrected.get(field) or [])
                if corrected.get(field) != sanitized:
                    corrected[field] = sanitized
                    changed.append(field)
        return corrected, _dedupe(changed)

    number_hint = next(
        (key for key in number_keys if key.startswith("FC2-") or key.startswith("FC2PPV")),
        next(iter(number_keys)),
    )
    for field in ("actors", "actresses"):
        if field in corrected:
            sanitized = sanitize_actor_names(
                corrected.get(field) or [],
                maker=str(corrected.get("maker") or ""),
                number=number_hint,
            )
            if corrected.get(field) != sanitized:
                corrected[field] = sanitized
                changed.append(field)

    changed.extend(_apply_title_actress_rules(corrected, number_keys))

    overrides = [*BUILTIN_METADATA_OVERRIDES, *_configured_overrides(config)]
    for override in overrides:
        if not _override_matches(override, number_keys):
            continue

        actresses = sanitize_actor_names(override.get("actresses") or override.get("actors") or [])
        if actresses:
            if corrected.get("actors") != actresses:
                corrected["actors"] = actresses
                changed.append("actors")
            if corrected.get("actresses") != actresses:
                corrected["actresses"] = actresses
                changed.append("actresses")

        for field in (
            "maker",
            "title",
            "original_title",
            "date",
            "release_date",
            "director",
            "series",
            "label",
            "url",
        ):
            value = str(override.get(field) or "").strip()
            if value and corrected.get(field) != value:
                corrected[field] = value
                changed.append(field)

        cover_url = str(override.get("cover_url") or override.get("cover") or "").strip()
        if cover_url:
            for field in ("cover_url", "cover"):
                if corrected.get(field) != cover_url:
                    corrected[field] = cover_url
                    changed.append(field)

        if override.get("duration") not in (None, ""):
            try:
                duration = int(override.get("duration"))
            except (TypeError, ValueError):
                duration = None
            if duration is not None and corrected.get("duration") != duration:
                corrected["duration"] = duration
                changed.append("duration")

        tags = _dedupe(override.get("tags") or [])
        if tags:
            merged_tags = _dedupe([*(corrected.get("tags") or []), *tags])
            if corrected.get("tags") != merged_tags:
                corrected["tags"] = merged_tags
                changed.append("tags")

    return corrected, _dedupe(changed)

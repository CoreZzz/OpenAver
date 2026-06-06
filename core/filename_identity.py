"""Unified filename identity parsing for media files.

This module is the single place for turning messy filenames or user-entered
numbers into a canonical work number plus file-variant metadata.  It is kept
dependency-free so low-level scraper and scanner code can import it safely.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
import re
from typing import Optional


_NOISE_PREFIXES = {
    "MOVIE",
    "RANDOM",
    "SAMPLE",
    "UC",
    "VIDEO",
    "VACATION",
    "HD",
    "FHD",
    "UHD",
    "SD",
    "VR",
    "TUTORIAL",
}

_QUALITY_TOKENS = {
    "4K",
    "8K",
    "HD",
    "FHD",
    "UHD",
    "SD",
    "VR",
    "1080P",
    "720P",
    "2160P",
    "60FPS",
}

_DATE_STYLE_NUMBER_RE = re.compile(r"^\d{6}[-_]\d{2,3}$")

_SINGLE_LETTER_PREFIXES = {
    "N",
}

_COMPACT_DISTINCT_PREFIXES = {
    "RED",
}

_PREFIX_MIN_DIGITS = {
    "ZMIN": 3,
}


@dataclass(frozen=True)
class VariantFlags:
    """Structured file-variant markers parsed from filename suffixes."""

    subtitle_cn: bool = False
    cracked: bool = False


@dataclass(frozen=True)
class MediaIdentity:
    """Canonical work identity plus source-specific query hints."""

    raw_name: str
    raw_stem: str
    raw_match: str = ""
    canonical_number: Optional[str] = None
    search_number: Optional[str] = None
    display_number: Optional[str] = None
    number_aliases: list[str] = field(default_factory=list)
    source_queries: dict[str, list[str]] = field(default_factory=dict)
    part_index: Optional[str] = None
    variant_flags: VariantFlags = field(default_factory=VariantFlags)
    variant_label: str = ""
    raw_tokens: list[str] = field(default_factory=list)
    work_key: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class _NumberMatch:
    raw: str
    canonical: str
    start: int
    end: int
    kind: str
    fc2_digits: Optional[str] = None


def _prefixed_number(prefix: str, digits: str) -> str:
    prefix = str(prefix or "").upper()
    digits = str(digits or "")
    width = _PREFIX_MIN_DIGITS.get(prefix, 0)
    if width and digits.isdigit():
        digits = digits.zfill(width)
    return f"{prefix}-{digits}"


def filename_stem(value: str) -> str:
    """Return the basename without the final extension for POSIX/Windows paths."""

    if not value:
        return ""
    leaf = re.split(r"[\\/]", str(value))[-1]
    if "." in leaf:
        return leaf.rsplit(".", 1)[0]
    return leaf


def normalize_work_number(value: str) -> Optional[str]:
    """Normalize a raw number or filename into the canonical work number."""

    return parse_media_identity(value).canonical_number


def build_search_candidates(identity: MediaIdentity) -> list[str]:
    """Return de-duplicated query candidates for generic search."""

    candidates: list[str] = []
    for value in [identity.search_number, *identity.number_aliases]:
        if value and value not in candidates:
            candidates.append(value)
    return candidates


def build_source_queries(identity: MediaIdentity, source_id: str) -> list[str]:
    """Return source-specific query candidates.

    FC2-like sources often accept the numeric id directly, while aggregator
    sources tend to work better with a full FC2 prefix.  Other numbers currently
    use the generic canonical query.
    """

    if not identity.canonical_number:
        return []

    sid = (source_id or "default").lower()
    if sid == "d2pass" and _DATE_STYLE_NUMBER_RE.match(identity.canonical_number):
        return _dedupe([identity.search_number or identity.canonical_number])

    if not identity.canonical_number.startswith("FC2-PPV-"):
        return build_search_candidates(identity)

    digits = identity.canonical_number.rsplit("-", 1)[-1]
    fc2ppv_compact = f"FC2PPV-{digits}"
    fc2_short = f"FC2-{digits}"

    if sid == "fc2":
        return _dedupe([identity.canonical_number, fc2ppv_compact, digits])
    if sid == "avsox":
        return _dedupe([fc2_short, identity.canonical_number, fc2ppv_compact])
    if sid.startswith("metatube:"):
        provider = sid.split(":", 1)[1]
        if provider in {"fc2", "fc2ppvdb", "fc2hub"}:
            return _dedupe([identity.canonical_number, fc2_short, fc2ppv_compact, digits])
    if sid in {"javdb", "default"}:
        return _dedupe([identity.canonical_number, fc2_short, fc2ppv_compact])

    return build_search_candidates(identity)


def parse_media_identity(value: str) -> MediaIdentity:
    """Parse filename/user input into canonical number and variant metadata."""

    raw_name = str(value or "")
    stem = filename_stem(raw_name)
    match = _find_number_match(stem)

    if not match:
        return MediaIdentity(raw_name=raw_name, raw_stem=stem)

    tokens = _suffix_tokens(stem[match.end :])
    flags = _variant_flags(raw_name, tokens)
    part = _part_index(tokens)
    label = _variant_label(flags, part)
    aliases = _number_aliases(match)

    identity = MediaIdentity(
        raw_name=raw_name,
        raw_stem=stem,
        raw_match=match.raw,
        canonical_number=match.canonical,
        search_number=match.canonical,
        display_number=match.canonical,
        number_aliases=aliases,
        source_queries={},
        part_index=part,
        variant_flags=flags,
        variant_label=label,
        raw_tokens=tokens,
        work_key=match.canonical,
    )
    source_queries = {
        "default": build_source_queries(identity, "default"),
        "fc2": build_source_queries(identity, "fc2"),
        "javdb": build_source_queries(identity, "javdb"),
        "avsox": build_source_queries(identity, "avsox"),
        "metatube:FC2": build_source_queries(identity, "metatube:FC2"),
    }
    return replace(identity, source_queries=source_queries)


def _find_number_match(stem: str) -> Optional[_NumberMatch]:
    upper, offset = _strip_leading_web_prefix(stem.upper())

    # FC2 aliases: FC2PPV-1234567 / FC2-1234567 / FC2-PPV-1234567 / FC2PPV1234567
    fc2 = re.search(r"(?<![A-Z0-9])FC2[-_\s]?(?:PPV[-_\s]?)?(\d{3,8})(?![A-Z0-9])", upper)
    if fc2:
        digits = fc2.group(1)
        return _NumberMatch(
            raw=stem[offset + fc2.start() : offset + fc2.end()],
            canonical=f"FC2-PPV-{digits}",
            start=offset + fc2.start(),
            end=offset + fc2.end(),
            kind="fc2",
            fc2_digits=digits,
        )

    # 041417-413 / 120415_201 style uncensored numbers.
    date_style = re.search(r"(?<![A-Z0-9])(\d{6}[-_]\d{2,3})(?![A-Z0-9])", upper)
    if date_style:
        return _NumberMatch(
            raw=stem[offset + date_style.start() : offset + date_style.end()],
            canonical=date_style.group(1),
            start=offset + date_style.start(),
            end=offset + date_style.end(),
            kind="date",
        )

    heyzo = re.search(r"(?<![A-Z0-9])HEYZO[-_\s]?(\d{3,5})(?![A-Z0-9])", upper)
    if heyzo:
        return _NumberMatch(
            raw=stem[offset + heyzo.start() : offset + heyzo.end()],
            canonical=f"HEYZO-{heyzo.group(1)}",
            start=offset + heyzo.start(),
            end=offset + heyzo.end(),
            kind="heyzo",
        )

    # T28-103 and similar letter+digit prefixes.
    mixed = re.search(r"(?<![A-Z0-9])([A-Z]+\d+)[-_](\d{2,5})(?![A-Z0-9])", upper)
    if mixed:
        return _NumberMatch(
            raw=stem[offset + mixed.start() : offset + mixed.end()],
            canonical=_prefixed_number(mixed.group(1), mixed.group(2)),
            start=offset + mixed.start(),
            end=offset + mixed.end(),
            kind="mixed",
        )

    # Some older/catalog-specific codes use compact and hyphenated forms for
    # different works.  Do not normalize RED155 into RED-155.
    compact_distinct = re.search(r"(?<![A-Z0-9])([A-Z]{2,7})(\d{2,5})(?![A-Z0-9])", upper)
    if compact_distinct:
        prefix = compact_distinct.group(1)
        if prefix in _COMPACT_DISTINCT_PREFIXES:
            return _NumberMatch(
                raw=stem[offset + compact_distinct.start() : offset + compact_distinct.end()],
                canonical=f"{prefix}{compact_distinct.group(2)}",
                start=offset + compact_distinct.start(),
                end=offset + compact_distinct.end(),
                kind="compact_distinct",
            )

    repeated_general = re.search(
        r"(?<![A-Z0-9])([A-Z]{2,7})[-_]?(\d{2,5})(?=[A-Z]{2,7}[-_]?\d{2,5})",
        upper,
    )
    if repeated_general:
        prefix = repeated_general.group(1)
        if prefix not in _NOISE_PREFIXES:
            return _NumberMatch(
                raw=stem[offset + repeated_general.start() : offset + repeated_general.end()],
                canonical=_prefixed_number(prefix, repeated_general.group(2)),
                start=offset + repeated_general.start(),
                end=offset + repeated_general.end(),
                kind="general",
            )

    # 123ABC-456 / 123ABC456 style amateur labels.
    digit_prefix = re.search(r"(?<![A-Z0-9])(\d{3}[A-Z]{3,5})[-_]?(\d{2,5})(?![A-Z0-9])", upper)
    if digit_prefix:
        return _NumberMatch(
            raw=stem[offset + digit_prefix.start() : offset + digit_prefix.end()],
            canonical=_prefixed_number(digit_prefix.group(1), digit_prefix.group(2)),
            start=offset + digit_prefix.start(),
            end=offset + digit_prefix.end(),
            kind="digit_prefix",
        )

    general = re.search(r"(?<![A-Z0-9])([A-Z]{2,7})[-_]?(\d{2,5})(?![A-Z0-9])", upper)
    if general:
        prefix = general.group(1)
        if prefix in _NOISE_PREFIXES:
            return None
        return _NumberMatch(
            raw=stem[offset + general.start() : offset + general.end()],
            canonical=_prefixed_number(prefix, general.group(2)),
            start=offset + general.start(),
            end=offset + general.end(),
            kind="general",
        )

    single_letter = re.search(r"(?<![A-Z0-9])([A-Z])[-_]?(\d{3,5})(?![A-Z0-9])", upper)
    if single_letter and single_letter.group(1) in _SINGLE_LETTER_PREFIXES:
        return _NumberMatch(
            raw=stem[offset + single_letter.start() : offset + single_letter.end()],
            canonical=f"{single_letter.group(1)}-{single_letter.group(2)}",
            start=offset + single_letter.start(),
            end=offset + single_letter.end(),
            kind="single_letter",
        )

    compact_variant = re.search(
        r"(?<![A-Z0-9])([A-Z]{2,7})[-_]?(\d{2,5})(?=[A-Z]{1,10}(?:$|[^A-Z0-9]))",
        upper,
    )
    if compact_variant:
        prefix = compact_variant.group(1)
        if prefix not in _NOISE_PREFIXES:
            return _NumberMatch(
                raw=stem[offset + compact_variant.start() : offset + compact_variant.end()],
                canonical=_prefixed_number(prefix, compact_variant.group(2)),
                start=offset + compact_variant.start(),
                end=offset + compact_variant.end(),
                kind="general",
            )

    return None


def _strip_leading_web_prefix(stem: str) -> tuple[str, int]:
    """Drop common downloader/site prefixes before matching the work number."""
    match = re.match(r"^[A-Z0-9.-]+\.(?:COM|NET|ORG|TV)@", stem, flags=re.IGNORECASE)
    if not match:
        return stem, 0
    return stem[match.end():], match.end()


def _number_aliases(match: _NumberMatch) -> list[str]:
    if match.kind != "fc2" or not match.fc2_digits:
        if match.kind == "date":
            separator_alias = (
                match.canonical.replace("_", "-", 1)
                if "_" in match.canonical
                else match.canonical.replace("-", "_", 1)
            )
            return _dedupe([match.canonical, separator_alias])
        if match.kind == "single_letter":
            return _dedupe([match.canonical, re.sub(r"[^A-Z0-9]+", "", match.raw.upper())])
        return [match.canonical]

    digits = match.fc2_digits
    return _dedupe(
        [
            f"FC2-PPV-{digits}",
            f"FC2PPV-{digits}",
            f"FC2PPV{digits}",
            f"FC2-{digits}",
            digits,
        ]
    )


def _suffix_tokens(suffix: str) -> list[str]:
    if not suffix:
        return []
    return [t.upper() for t in re.split(r"[^A-Za-z0-9]+", suffix) if t]


def _variant_flags(raw_name: str, tokens: list[str]) -> VariantFlags:
    upper_tokens = set(tokens)
    subtitle = (
        "C" in upper_tokens
        or "UC" in upper_tokens
        or "中文字幕" in raw_name
        or "中字" in raw_name
        or "字幕" in raw_name
    )
    cracked = "U" in upper_tokens or "UC" in upper_tokens
    return VariantFlags(subtitle_cn=subtitle, cracked=cracked)


def _part_index(tokens: list[str]) -> Optional[str]:
    for token in tokens:
        if token in {"C", "U", "UC"} or token in _QUALITY_TOKENS:
            continue
        if re.fullmatch(r"\d{1,2}", token):
            return token
        if re.fullmatch(r"[A-Z]", token):
            return token
        if re.fullmatch(r"CD\d{1,2}", token):
            return token
    return None


def _variant_label(flags: VariantFlags, part: Optional[str]) -> str:
    labels: list[str] = []
    if flags.cracked and flags.subtitle_cn:
        labels.append("破解+中文字幕")
    elif flags.cracked:
        labels.append("破解")
    elif flags.subtitle_cn:
        labels.append("中文字幕")
    if part:
        labels.append(f"Part {part}")
    return " / ".join(labels)


def _dedupe(values: list[Optional[str]]) -> list[str]:
    out: list[str] = []
    for value in values:
        if value and value not in out:
            out.append(value)
    return out

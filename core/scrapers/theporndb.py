"""ThePornDB scraper.

This adapter intentionally keeps ThePornDB details behind the regular
BaseScraper/Video interface. It does not persist anything by itself; aliases and
performer profiles are carried as private metadata for write-side flows.
"""
from __future__ import annotations

import hashlib
import re
import time
from typing import Any, Optional

import requests

from core.logger import get_logger

from .base import BaseScraper
from .models import Actress, ScraperConfig, Video
from .utils import rate_limit

logger = get_logger(__name__)


FEMALE_GENDERS = {"FEMALE", "TRANSGENDER_FEMALE"}
_AUTH_FAILURE_TTL_SECONDS = 15 * 60
_AUTH_FAILURES: dict[str, float] = {}


def _token_fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _auth_failure_active(token: str) -> bool:
    if not token:
        return False
    fp = _token_fingerprint(token)
    expires_at = _AUTH_FAILURES.get(fp)
    if not expires_at:
        return False
    if expires_at <= time.monotonic():
        _AUTH_FAILURES.pop(fp, None)
        return False
    return True


def _remember_auth_failure(token: str) -> None:
    if token:
        _AUTH_FAILURES[_token_fingerprint(token)] = time.monotonic() + _AUTH_FAILURE_TTL_SECONDS


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _dedupe_strings(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in values:
        value = _clean_text(raw)
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _best_image(*values: Any) -> str:
    for value in values:
        if isinstance(value, dict):
            for key in ("full", "large", "medium", "small"):
                candidate = _clean_text(value.get(key))
                if candidate:
                    return candidate
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    candidate = _clean_text(item.get("url"))
                    if candidate:
                        return candidate
        else:
            candidate = _clean_text(value)
            if candidate:
                return candidate
    return ""


def _duration_minutes(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        duration = float(value)
    except (TypeError, ValueError):
        return None
    if duration <= 0:
        return None
    # ThePornDB exposes seconds for scenes. Keep tiny values as minutes if an
    # upstream response is already minute-based.
    if duration > 300:
        return int(round(duration / 60))
    return int(round(duration))


def _gender_key(performer: dict[str, Any]) -> str:
    extras = performer.get("extras") if isinstance(performer.get("extras"), dict) else {}
    raw = extras.get("gender") or performer.get("gender") or ""
    return re.sub(r"[^A-Z_]+", "_", str(raw).strip().upper()).strip("_")


def _is_female_performer(performer: dict[str, Any]) -> bool:
    return _gender_key(performer) in FEMALE_GENDERS


def _parse_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    match = re.search(r"\d+", str(value))
    if not match:
        return None
    try:
        return int(match.group(0))
    except ValueError:
        return None


def _parse_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_measurements(value: Any) -> tuple[Optional[int], Optional[int], Optional[int]]:
    text = _clean_text(value)
    if not text:
        return None, None, None
    nums = re.findall(r"\d+", text)
    if len(nums) < 3:
        return None, None, None
    try:
        return int(nums[0]), int(nums[1]), int(nums[2])
    except ValueError:
        return None, None, None


def _performer_profile(performer: dict[str, Any]) -> dict[str, Any]:
    extras = performer.get("extras") if isinstance(performer.get("extras"), dict) else {}
    name = _clean_text(performer.get("name") or performer.get("full_name"))
    aliases = _dedupe_strings(performer.get("aliases") or [])
    full_name = _clean_text(performer.get("full_name"))
    if full_name and full_name != name:
        aliases = _dedupe_strings([*aliases, full_name])

    bust, waist, hip = _parse_measurements(extras.get("measurements"))
    tags = _dedupe_strings([
        extras.get("nationality"),
        extras.get("ethnicity"),
        extras.get("hair_colour"),
        extras.get("eye_colour"),
    ])
    links = extras.get("links") if isinstance(extras.get("links"), dict) else {}
    official_url = ""
    for value in links.values():
        official_url = _clean_text(value)
        if official_url:
            break

    return {
        "name": name,
        "name_en": name,
        "birth": _clean_text(extras.get("birthday")),
        "height": _parse_int(extras.get("height")),
        "cup": _clean_text(extras.get("cupsize")),
        "bust": bust,
        "waist": waist or _parse_int(extras.get("waist")),
        "hip": hip or _parse_int(extras.get("hips")),
        "hometown": _clean_text(extras.get("birthplace")),
        "aliases": aliases,
        "tags": tags,
        "nickname": _clean_text(performer.get("slug")),
        "official_url": official_url,
        "photo_source": _best_image(
            performer.get("image"),
            performer.get("face"),
            performer.get("thumbnail"),
            performer.get("posters"),
        ),
        "primary_text_source": "theporndb",
    }


class ThePornDBScraper(BaseScraper):
    """ThePornDB scene/movie scraper."""

    BASE_URL = "https://api.theporndb.net"

    def __init__(self, config: Optional[ScraperConfig] = None, api_token: str = ""):
        super().__init__(config)
        self.api_token = api_token.strip()
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": self.config.user_agent,
            "Accept": "application/json",
        })
        if self.api_token:
            self._session.headers["Authorization"] = f"Bearer {self.api_token}"

    def _get_source_name(self) -> str:
        return "theporndb"

    @property
    def configured(self) -> bool:
        return bool(self.api_token) and not _auth_failure_active(self.api_token)

    def _get_json(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self.configured:
            return {}
        url = f"{self.BASE_URL}{path}"
        try:
            response = self._session.get(url, params=params or {}, timeout=self.config.timeout)
        except requests.RequestException as exc:
            logger.warning("ThePornDB request failed for %s: %s", path, exc)
            return {}
        if response.status_code in (401, 403):
            _remember_auth_failure(self.api_token)
            logger.warning("ThePornDB auth failed")
            return {}
        if response.status_code == 404:
            return {}
        if response.status_code != 200:
            logger.warning("ThePornDB HTTP %s for %s", response.status_code, path)
            return {}
        try:
            data = response.json()
        except ValueError:
            logger.warning("ThePornDB returned invalid JSON for %s", path)
            return {}
        return data if isinstance(data, dict) else {}

    def _list(self, path: str, query: str, limit: int = 20, offset: int = 0) -> list[dict[str, Any]]:
        page = max(1, (offset // max(1, limit)) + 1)
        params = {
            "q": query,
            "title": query,
            "per_page": max(1, min(limit, 100)),
            "page": page,
        }
        data = self._get_json(path, params)
        items = data.get("data") or []
        return [item for item in items if isinstance(item, dict)]

    def _fetch_detail(self, path: str, identifier: str) -> Optional[dict[str, Any]]:
        if not identifier:
            return None
        data = self._get_json(f"{path}/{identifier}")
        item = data.get("data")
        return item if isinstance(item, dict) else None

    def _candidate_score(self, item: dict[str, Any], query: str) -> tuple[int, str]:
        q = query.strip().lower()
        normalized_q = re.sub(r"[^a-z0-9]+", "", q)
        fields = [
            _clean_text(item.get("sku")),
            _clean_text(item.get("external_id")),
            _clean_text(item.get("title")),
            _clean_text(item.get("slug")),
            _clean_text(item.get("url")),
        ]
        normalized_fields = [re.sub(r"[^a-z0-9]+", "", f.lower()) for f in fields if f]
        if normalized_q and any(f == normalized_q for f in normalized_fields):
            return (100, _clean_text(item.get("date")))
        if q and any(q == f.lower() for f in fields if f):
            return (90, _clean_text(item.get("date")))
        if normalized_q and any(normalized_q in f for f in normalized_fields):
            return (70, _clean_text(item.get("date")))
        words = [w for w in re.split(r"\W+", q) if len(w) >= 3]
        title = _clean_text(item.get("title")).lower()
        word_score = sum(1 for w in words if w in title)
        return (word_score, _clean_text(item.get("date")))

    def _pick_best(self, items: list[dict[str, Any]], query: str) -> Optional[dict[str, Any]]:
        if not items:
            return None
        return max(items, key=lambda item: self._candidate_score(item, query))

    def _item_to_video(self, item: dict[str, Any], fallback_number: str = "") -> Optional[Video]:
        title = _clean_text(item.get("title"))
        if not title:
            return None

        raw_performers = item.get("performers") or []
        female_performers = [
            p for p in raw_performers
            if isinstance(p, dict) and _is_female_performer(p)
        ]
        actresses = [
            Actress(name=name)
            for name in _dedupe_strings([
                p.get("name") or p.get("full_name")
                for p in female_performers
            ])
        ]

        actress_aliases: dict[str, list[str]] = {}
        actress_profiles: list[dict[str, Any]] = []
        for performer in female_performers:
            profile = _performer_profile(performer)
            name = profile.get("name")
            if not name:
                continue
            actress_profiles.append(profile)
            if profile.get("aliases"):
                actress_aliases[name] = profile["aliases"]

        site = item.get("site") if isinstance(item.get("site"), dict) else {}
        network = site.get("network") if isinstance(site.get("network"), dict) else {}
        parent = site.get("parent") if isinstance(site.get("parent"), dict) else {}
        directors = item.get("directors") or []
        director_names = _dedupe_strings([
            d.get("name")
            for d in directors
            if isinstance(d, dict)
        ])

        tags = []
        for tag in item.get("tags") or []:
            if not isinstance(tag, dict):
                continue
            tags.append(tag.get("name"))
            for parent_tag in tag.get("parents") or []:
                if isinstance(parent_tag, dict):
                    tags.append(parent_tag.get("name"))

        number = (
            _clean_text(item.get("sku"))
            or _clean_text(item.get("external_id"))
            or _clean_text(item.get("slug"))
            or _clean_text(item.get("id"))
            or fallback_number
        )

        cover = _best_image(
            item.get("poster"),
            item.get("posters"),
            item.get("poster_image"),
            item.get("image"),
            item.get("back_image"),
            item.get("background"),
            item.get("background_back"),
        )
        sample_images = _dedupe_strings([
            _best_image(item.get("background")),
            _best_image(item.get("background_back")),
            _clean_text(item.get("image")),
            _clean_text(item.get("back_image")),
            _clean_text(item.get("poster_image")),
        ])
        sample_images = [url for url in sample_images if url and url != cover]

        return Video(
            number=number,
            title=title,
            actresses=actresses,
            date=_clean_text(item.get("date")),
            maker=_clean_text(site.get("name")),
            cover_url=cover,
            tags=_dedupe_strings(tags),
            source=self.source_name,
            detail_url=_clean_text(item.get("url")),
            director=", ".join(director_names),
            duration=_duration_minutes(item.get("duration")),
            label=_clean_text(network.get("name") or parent.get("name")),
            series="",
            sample_images=sample_images,
            rating=_parse_float(item.get("rating")),
            summary=_clean_text(item.get("description")),
            actress_aliases=actress_aliases,
            actress_profiles=actress_profiles,
        )

    def search(self, number: str) -> Optional[Video]:
        query = number.strip()
        if not query or not self.configured:
            return None

        candidates: list[tuple[str, dict[str, Any]]] = []
        for path in ("/scenes", "/movies"):
            for item in self._list(path, query, limit=10):
                candidates.append((path, item))

        best_pair = None
        if candidates:
            best_pair = max(candidates, key=lambda pair: self._candidate_score(pair[1], query))

        if best_pair is None:
            return None

        path, item = best_pair
        identifier = _clean_text(item.get("id") or item.get("slug"))
        detail = self._fetch_detail(path, identifier) if identifier else None
        video = self._item_to_video(detail or item, fallback_number=query)
        if video:
            rate_limit(self.config.delay)
        return video

    def search_by_keyword(self, keyword: str, limit: int = 20, offset: int = 0) -> list[Video]:
        query = keyword.strip()
        if not query or not self.configured:
            return []
        videos: list[Video] = []
        for path in ("/scenes", "/movies"):
            for item in self._list(path, query, limit=limit, offset=offset):
                video = self._item_to_video(item, fallback_number=query)
                if video:
                    videos.append(video)
                if len(videos) >= limit:
                    return videos
        return videos

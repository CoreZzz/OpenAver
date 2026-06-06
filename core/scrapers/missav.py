"""MissAV scraper."""
from __future__ import annotations

import re
from typing import Optional
from urllib.parse import quote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from core.logger import get_logger

from .base import BaseScraper
from .models import Actress, ScraperConfig, Video
from .utils import rate_limit, strip_number_prefix

logger = get_logger(__name__)

try:
    from curl_cffi import requests as curl_requests
    CURL_CFFI_AVAILABLE = True
except ImportError:  # pragma: no cover - depends on optional runtime package
    curl_requests = None
    CURL_CFFI_AVAILABLE = False


class MissAVScraper(BaseScraper):
    """Scraper for MissAV CN pages.

    MissAV is used as a high-coverage fallback behind JavDB. The live site may
    present a Cloudflare challenge, so network failures are treated as misses.
    """

    BASE_URL = "https://missav.ai"
    LANG_PATH = "/cn"

    FIELD_LABELS = {
        "number": ("番号", "番號", "识别码", "識別碼", "品番", "ID"),
        "title": ("标题", "標題", "タイトル", "Title"),
        "date": ("发行日期", "發行日期", "発売日", "Release Date"),
        "actresses": ("女优", "女優", "演员", "演員", "JAV Idols"),
        "tags": ("类型", "類型", "类别", "類別", "ジャンル", "Genre"),
        "maker": ("发行商", "發行商", "制作商", "製作商", "メーカー", "Studio", "Maker"),
        "director": ("导演", "導演", "監督", "Director"),
        "label": ("标籤", "标签", "標籤", "レーベル", "Label"),
        "series": ("系列", "シリーズ", "Series"),
    }
    _ALL_LABELS = {
        label
        for labels in FIELD_LABELS.values()
        for label in labels
    }

    def _get_source_name(self) -> str:
        return "missav"

    @staticmethod
    def _identity_key(value: str) -> str:
        return re.sub(r"[^A-Z0-9]+", "", str(value or "").upper())

    @staticmethod
    def _clean_text(value: object) -> str:
        text = str(value or "").replace("\xa0", " ")
        return re.sub(r"\s+", " ", text).strip()

    @staticmethod
    def _is_challenge_html(html: str) -> bool:
        lower = str(html or "").lower()
        return (
            "just a moment" in lower
            or "challenge-platform" in lower
            or "cf-browser-verification" in lower
        )

    def _detail_url_for_number(self, number: str) -> str:
        slug = quote(str(number or "").strip().lower())
        return f"{self.BASE_URL}{self.LANG_PATH}/{slug}"

    def _absolute_url(self, url: str, base: str | None = None) -> str:
        value = str(url or "").strip()
        if not value:
            return ""
        if value.startswith("//"):
            return "https:" + value
        return urljoin(base or self.BASE_URL, value)

    def _get_html(self, url: str) -> Optional[str]:
        headers = {
            "User-Agent": self.config.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,zh-TW;q=0.8,ja;q=0.7,en;q=0.6",
            "Referer": f"{self.BASE_URL}/",
        }
        proxy_url = str(self.config.proxy_url or "").strip()
        proxies = None
        if proxy_url and proxy_url.lower() != "direct":
            proxies = {"http": proxy_url, "https": proxy_url}

        try:
            if CURL_CFFI_AVAILABLE and curl_requests is not None:
                response = curl_requests.get(
                    url,
                    impersonate="chrome120",
                    headers=headers,
                    timeout=self.config.timeout,
                    proxies=proxies,
                )
            else:
                response = requests.get(
                    url,
                    headers=headers,
                    timeout=self.config.timeout,
                    proxies=proxies,
                )
            if response.status_code != 200:
                return None
            html = str(response.text or "")
            if self._is_challenge_html(html):
                return None
            return html
        except Exception as exc:
            logger.debug("MissAV request failed for %s: %s", url, exc)
            return None

    def search(self, number: str) -> Optional[Video]:
        original_query = str(number or "").strip()
        if not original_query:
            return None

        normalized = self.normalize_number(original_query)
        structured_query = self.validate_number(normalized)

        try:
            if structured_query:
                video = self._fetch_detail(self._detail_url_for_number(normalized), normalized)
                if video and self._identity_key(video.number) == self._identity_key(normalized):
                    return video

            queries = self._dedupe([normalized, original_query])
            for query in queries:
                for matched_number, detail_url in self._search_candidates(query, limit=5):
                    if structured_query and self._identity_key(matched_number) != self._identity_key(normalized):
                        continue
                    video = self._fetch_detail(detail_url, matched_number or normalized)
                    if video:
                        if structured_query and self._identity_key(video.number) != self._identity_key(normalized):
                            continue
                        return video
            return None
        except Exception as exc:
            logger.warning("MissAV search failed for %s: %s", original_query, exc)
            return None

    def search_by_keyword(self, keyword: str, limit: int = 20) -> list[Video]:
        query = str(keyword or "").strip()
        if not query:
            return []

        results: list[Video] = []
        seen: set[str] = set()
        try:
            candidates = self._search_candidates(query, limit=limit)
            for matched_number, detail_url in candidates:
                key = self._identity_key(matched_number or detail_url)
                if key in seen:
                    continue
                seen.add(key)
                video = self._fetch_detail(detail_url, matched_number)
                if video:
                    results.append(video)
                if len(results) >= limit:
                    break
            return results
        except Exception as exc:
            logger.warning("MissAV keyword search failed for %s: %s", query, exc)
            return []

    def _fetch_detail(self, detail_url: str, fallback_number: str) -> Optional[Video]:
        html = self._get_html(detail_url)
        if not html:
            return None
        soup = BeautifulSoup(html, "html.parser")
        video = self._parse_detail_page(soup, fallback_number, detail_url)
        if video:
            rate_limit(self.config.delay)
        return video

    def _search_candidates(self, keyword: str, limit: int = 20) -> list[tuple[str, str]]:
        query = str(keyword or "").strip()
        if not query:
            return []

        urls = [
            f"{self.BASE_URL}{self.LANG_PATH}/search/{quote(query)}",
            f"{self.BASE_URL}/search/{quote(query)}",
        ]
        candidates: list[tuple[str, str]] = []
        seen_urls: set[str] = set()

        for url in urls:
            html = self._get_html(url)
            if not html:
                continue
            soup = BeautifulSoup(html, "html.parser")
            for anchor in soup.select("a[href]"):
                detail_url = self._candidate_detail_url(anchor.get("href", ""))
                if not detail_url or detail_url in seen_urls:
                    continue
                seen_urls.add(detail_url)
                text = anchor.get_text(" ", strip=True)
                number = self._number_from_candidate(detail_url, text)
                candidates.append((number, detail_url))
                if len(candidates) >= limit:
                    return candidates
        return candidates

    def _candidate_detail_url(self, href: str) -> str:
        absolute = self._absolute_url(href)
        parsed = urlparse(absolute)
        if parsed.scheme != "https" or parsed.hostname not in {"missav.ai", "www.missav.ai"}:
            return ""
        path = parsed.path.rstrip("/")
        if not path.startswith(f"{self.LANG_PATH}/"):
            return ""
        slug = path.rsplit("/", 1)[-1]
        if not slug or slug in {"search", "dm247"}:
            return ""
        if "." in slug:
            return ""
        return f"{self.BASE_URL}{path}"

    def _number_from_candidate(self, detail_url: str, text: str) -> str:
        parsed = urlparse(detail_url)
        slug = parsed.path.rstrip("/").rsplit("/", 1)[-1]
        haystack = f"{slug} {text}".upper()

        fc2 = re.search(r"FC2[-_ ]?(?:PPV[-_ ]?)?(\d{5,9})", haystack)
        if fc2:
            return f"FC2-PPV-{fc2.group(1)}"

        match = re.search(r"\b([A-Z]{2,10})[-_ ]?(\d{2,7})\b", haystack)
        if match:
            return self.normalize_number(f"{match.group(1)}-{match.group(2)}")

        return self.normalize_number(slug.replace("_", "-"))

    def _parse_detail_page(self, soup: BeautifulSoup, fallback_number: str, detail_url: str) -> Optional[Video]:
        lines = [
            self._clean_text(line)
            for line in soup.get_text("\n").splitlines()
            if self._clean_text(line)
        ]

        number = self._field_from_lines(lines, "number") or fallback_number
        number = self.normalize_number(number)

        title = self._field_from_lines(lines, "title") or self._page_title(soup)
        title = self._clean_title(title, number)

        cover_url = self._extract_cover_url(soup, detail_url)
        date = self._extract_date(self._field_from_lines(lines, "date"))
        maker = self._field_from_lines(lines, "maker")
        director = self._field_from_lines(lines, "director")
        label = self._field_from_lines(lines, "label")
        series = self._field_from_lines(lines, "series")
        tags = self._split_values(self._field_from_lines(lines, "tags"))
        actresses = [
            Actress(name=name)
            for name in self._split_values(self._field_from_lines(lines, "actresses"))
        ]
        sample_images = self._extract_sample_images(soup, detail_url, cover_url)

        if not title and not cover_url:
            return None

        return Video(
            number=number,
            title=title,
            actresses=actresses,
            date=date,
            maker=maker,
            cover_url=cover_url,
            tags=tags,
            source=self.source_name,
            detail_url=detail_url,
            director=director,
            label=label,
            series=series,
            sample_images=sample_images,
        )

    def _field_from_lines(self, lines: list[str], field: str) -> str:
        labels = self.FIELD_LABELS[field]
        for index, line in enumerate(lines):
            for label in labels:
                match = re.match(rf"^{re.escape(label)}\s*[:：]\s*(.*)$", line, flags=re.IGNORECASE)
                if match:
                    value = self._clean_text(match.group(1))
                    if value:
                        return value
                    return self._next_field_value(lines, index)

                normalized_line = line.rstrip(":：").strip()
                if normalized_line.lower() == label.lower():
                    return self._next_field_value(lines, index)
        return ""

    def _next_field_value(self, lines: list[str], index: int) -> str:
        for value in lines[index + 1:index + 4]:
            clean = value.rstrip(":：").strip()
            if clean in self._ALL_LABELS:
                return ""
            if clean:
                return value
        return ""

    def _page_title(self, soup: BeautifulSoup) -> str:
        selectors = [
            'meta[property="og:title"]',
            'meta[name="twitter:title"]',
            "h1",
            "title",
        ]
        for selector in selectors:
            elem = soup.select_one(selector)
            if not elem:
                continue
            value = elem.get("content") if elem.name == "meta" else elem.get_text(" ", strip=True)
            value = self._clean_text(value)
            if value:
                return value
        return ""

    def _clean_title(self, title: str, number: str) -> str:
        value = self._clean_text(title)
        value = re.sub(r"\s*[-|]\s*MissAV.*$", "", value, flags=re.IGNORECASE)
        return strip_number_prefix(value, number)

    def _extract_cover_url(self, soup: BeautifulSoup, detail_url: str) -> str:
        selectors = [
            'meta[property="og:image"]',
            'meta[name="twitter:image"]',
            "video[poster]",
            "img[data-src]",
            "img[src]",
        ]
        for selector in selectors:
            elem = soup.select_one(selector)
            if not elem:
                continue
            value = (
                elem.get("content")
                or elem.get("poster")
                or elem.get("data-src")
                or elem.get("src")
            )
            url = self._absolute_url(str(value or ""), detail_url)
            if url:
                return url
        return ""

    def _extract_sample_images(self, soup: BeautifulSoup, detail_url: str, cover_url: str) -> list[str]:
        images: list[str] = []
        seen = {cover_url} if cover_url else set()
        for elem in soup.select("img[data-src], img[src], a[href]"):
            value = elem.get("data-src") or elem.get("src") or elem.get("href") or ""
            url = self._absolute_url(str(value), detail_url)
            if not url or url in seen:
                continue
            if not re.search(r"\.(?:jpe?g|png|webp)(?:\?|$)", url, flags=re.IGNORECASE):
                continue
            seen.add(url)
            images.append(url)
        return images

    @staticmethod
    def _extract_date(value: str) -> str:
        match = re.search(r"(\d{4}-\d{2}-\d{2})", str(value or ""))
        return match.group(1) if match else ""

    @classmethod
    def _split_values(cls, value: str) -> list[str]:
        text = cls._clean_text(value)
        if not text:
            return []
        parts = re.split(r"[,，、/;；]+", text)
        return [part.strip() for part in parts if part.strip()]

    @staticmethod
    def _dedupe(values: list[str]) -> list[str]:
        out: list[str] = []
        for value in values:
            clean = str(value or "").strip()
            if clean and clean not in out:
                out.append(clean)
        return out

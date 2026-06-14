"""TOKYO-HOT scraper."""
import re
from typing import Optional
from urllib.parse import quote, urljoin

import requests
from bs4 import BeautifulSoup

from core.logger import get_logger

from .base import BaseScraper
from .models import Actress, ScraperConfig, Video
from .utils import rate_limit

logger = get_logger(__name__)


class TokyoHotScraper(BaseScraper):
    """Scrape public TOKYO-HOT product pages for N/K short ids."""

    BASE_URL = "https://my.tokyo-hot.com"
    FIELD_ALIASES = {
        "model": ("Model", "出演者", "モデル"),
        "play": ("Play", "プレイ内容"),
        "tags": ("Tags", "タグ"),
        "theme": ("Theme", "Series", "シリーズ", "ジャンル"),
        "label": ("Label", "レーベル"),
        "release_date": ("Release Date", "配信開始日"),
        "duration": ("Duration", "収録時間"),
        "product_id": ("Product ID", "作品番号"),
    }

    def __init__(self, config: Optional[ScraperConfig] = None):
        super().__init__(config)
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": self.config.user_agent,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.9,ja;q=0.8,zh-TW;q=0.7",
        })

    def _get_source_name(self) -> str:
        return "tokyohot"

    def _normalize_tokyohot_number(self, number: str) -> Optional[str]:
        value = str(number or "").strip()
        match = re.match(r"^([nk])[-_]?(\d{4})$", value, flags=re.IGNORECASE)
        if not match:
            return None
        return f"{match.group(1).lower()}{match.group(2)}"

    def _canonical_number(self, compact: str) -> str:
        return f"{compact[0].upper()}-{compact[1:]}"

    def _get_html(self, url: str) -> Optional[str]:
        try:
            resp = self._session.get(url, timeout=self.config.timeout)
        except requests.Timeout:
            logger.debug("TOKYO-HOT timeout for %s", url)
            return None
        except Exception as exc:
            logger.debug("TOKYO-HOT request failed for %s: %s", url, exc)
            return None

        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            logger.debug("TOKYO-HOT HTTP %s for %s", resp.status_code, url)
            return None
        if not resp.encoding:
            resp.encoding = "utf-8"
        return resp.text

    def _info_pairs(self, soup: BeautifulSoup) -> dict[str, object]:
        info: dict[str, object] = {}
        for dt in soup.select(".info dt"):
            key = dt.get_text(" ", strip=True).rstrip(":：")
            dd = dt.find_next_sibling("dd")
            if not key or dd is None:
                continue
            links = [a.get_text(" ", strip=True) for a in dd.select("a") if a.get_text(" ", strip=True)]
            text = dd.get_text(" ", strip=True)
            info[key] = links or text
        return info

    def _detail_url_from_search(self, compact: str) -> Optional[str]:
        search_url = f"{self.BASE_URL}/product/?q={quote(compact)}"
        html = self._get_html(search_url)
        if not html:
            return None

        soup = BeautifulSoup(html, "html.parser")
        for link in soup.select('a[href^="/product/"]'):
            href = link.get("href") or ""
            text = link.get_text(" ", strip=True).lower()
            img_values = " ".join(
                value
                for img in link.select("img")
                for value in (img.get("alt"), img.get("title"), img.get("src"))
                if value
            ).lower()
            haystack = f"{href} {text} {img_values}".lower()
            if compact in haystack:
                return urljoin(self.BASE_URL, href)
        return None

    def _resolve_detail_url(self, compact: str) -> Optional[str]:
        direct_url = f"{self.BASE_URL}/product/{compact}/"
        html = self._get_html(direct_url)
        if html and self._page_product_id(BeautifulSoup(html, "html.parser")) == compact:
            return direct_url
        return self._detail_url_from_search(compact)

    def _page_product_id(self, soup: BeautifulSoup) -> str:
        pairs = self._info_pairs(soup)
        product_id = self._first_pair_value(pairs, *self.FIELD_ALIASES["product_id"])
        return str(product_id or "").strip().lower()

    def _parse_date(self, value: object) -> str:
        text = str(value or "").strip()
        match = re.match(r"^(\d{4})/(\d{2})/(\d{2})$", text)
        if match:
            return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
        return text

    def _parse_duration(self, value: object) -> Optional[int]:
        text = str(value or "").strip()
        match = re.match(r"^(?:(\d{1,2}):)?(\d{1,2}):(\d{2})$", text)
        if not match:
            return None
        hours = int(match.group(1) or 0)
        minutes = int(match.group(2))
        return hours * 60 + minutes

    def _pair_values(self, pairs: dict[str, object], *keys: str) -> list[str]:
        for key in keys:
            value = pairs.get(key)
            if isinstance(value, list):
                return [str(item).strip() for item in value if str(item).strip()]
            text = str(value or "").strip()
            if text:
                return [text]
        return []

    def _first_pair_value(self, pairs: dict[str, object], *keys: str) -> str:
        values = self._pair_values(pairs, *keys)
        return values[0] if values else ""

    def _first_text(self, soup: BeautifulSoup, *selectors: str) -> str:
        for selector in selectors:
            node = soup.select_one(selector)
            if node:
                text = node.get_text(" ", strip=True)
                if text:
                    return text
        return ""

    def _cover_url(self, soup: BeautifulSoup) -> str:
        package = soup.select_one(".movie .package a[href]")
        if package and package.get("href"):
            return urljoin(self.BASE_URL, package["href"])

        poster = soup.select_one(".movie video[poster]")
        if poster and poster.get("poster"):
            return urljoin(self.BASE_URL, poster["poster"])

        image = soup.select_one('meta[property="og:image"]')
        return urljoin(self.BASE_URL, image.get("content", "")) if image else ""

    def _sample_images(self, soup: BeautifulSoup) -> list[str]:
        images: list[str] = []
        for link in soup.select(".scap a[href], .vcap a[href]"):
            href = link.get("href")
            if not href:
                continue
            url = urljoin(self.BASE_URL, href)
            if url not in images:
                images.append(url)
        return images

    def _parse_detail(self, html: str, detail_url: str, compact: str) -> Optional[Video]:
        soup = BeautifulSoup(html, "html.parser")
        pairs = self._info_pairs(soup)
        product_id = self._first_pair_value(pairs, *self.FIELD_ALIASES["product_id"]).lower()
        product_id = product_id or compact
        if product_id != compact:
            logger.debug("TOKYO-HOT product id mismatch: requested=%s got=%s", compact, product_id)
            return None

        title = self._first_text(soup, ".pagetitle h2", "#main .contents h2")
        if not title:
            og_title = soup.select_one('meta[property="og:title"]')
            title = (og_title.get("content", "").split("|", 1)[0].strip() if og_title else "")
        if not title:
            return None

        model_names = self._pair_values(pairs, *self.FIELD_ALIASES["model"])

        tags: list[str] = []
        for field in ("play", "tags", "theme"):
            values = self._pair_values(pairs, *self.FIELD_ALIASES[field])
            for value in values:
                if value and value not in tags:
                    tags.append(value)

        label = self._first_pair_value(pairs, *self.FIELD_ALIASES["label"])
        summary = self._first_text(soup, "#main .contents .sentence")

        return Video(
            number=self._canonical_number(compact),
            title=title,
            actresses=[Actress(name=name) for name in model_names if name],
            date=self._parse_date(
                self._first_pair_value(pairs, *self.FIELD_ALIASES["release_date"])
            ),
            maker=label or "Tokyo-Hot",
            cover_url=self._cover_url(soup),
            tags=tags,
            source=self.source_name,
            detail_url=detail_url,
            label=label,
            duration=self._parse_duration(
                self._first_pair_value(pairs, *self.FIELD_ALIASES["duration"])
            ),
            sample_images=self._sample_images(soup),
            summary=summary,
        )

    def search(self, number: str) -> Optional[Video]:
        compact = self._normalize_tokyohot_number(number)
        if not compact:
            logger.debug("TOKYO-HOT invalid number format: %s", number)
            return None

        detail_url = self._resolve_detail_url(compact)
        if not detail_url:
            return None

        rate_limit(self.config.delay)
        html = self._get_html(detail_url)
        if not html:
            return None
        return self._parse_detail(html, detail_url, compact)

    def search_by_keyword(self, keyword: str, limit: int = 20) -> list[Video]:
        result = self.search(keyword)
        return [result] if result else []

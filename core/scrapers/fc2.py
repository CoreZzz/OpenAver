"""FC2 爬蟲（使用 FC2Hub/javten.com）"""
import json
import math
import re
import requests
from typing import Optional

from core.logger import get_logger

logger = get_logger(__name__)
from lxml import etree
from .base import BaseScraper
from .models import Actress, Video, ScraperConfig
from .utils import rate_limit


class FC2Scraper(BaseScraper):
    """
    FC2 爬蟲（使用 FC2Hub 鏡像站）

    優點：
    - 不需要登入
    - 有封面、預覽圖
    - 有簡介、賣家資訊

    注意：
    - 使用第三方鏡像站
    - 無發售日期

    參考：mdcx/crawlers/fc2hub.py
    """

    BASE_URL = "https://javten.com"

    def __init__(self, config: Optional[ScraperConfig] = None):
        super().__init__(config)
        self._session = requests.Session()
        self._session.headers.update({
            'User-Agent': self.config.user_agent,
            'Accept': 'text/html,application/xhtml+xml',
            'Accept-Language': 'ja,en;q=0.9',
        })

    def _get_source_name(self) -> str:
        return "fc2"

    def _normalize_fc2_number(self, number: str) -> str:
        """
        正規化 FC2 番號

        Examples:
            FC2-PPV-1234567 → 1234567
            FC2PPV-1234567  → 1234567
            FC2-1234567     → 1234567
            fc2ppv1234567   → 1234567
            1234567         → 1234567
        """
        number = number.upper().strip()
        # 移除各種 FC2 前綴
        number = re.sub(r'^FC2[-_]?PPV[-_]?', '', number)
        number = re.sub(r'^FC2[-_]?', '', number)
        number = number.replace('-', '').replace('_', '')
        return number

    def _get_title(self, html) -> str:
        """取得標題"""
        result = html.xpath("//h1/text()")
        # 第二個 h1 是標題（第一個是番號）
        return result[1].strip() if len(result) > 1 else ""

    def _get_number_from_page(self, html) -> str:
        """從頁面取得番號"""
        result = html.xpath("//h1/text()")
        return result[0].strip() if result else ""

    def _json_ld_objects(self, html) -> list[dict]:
        """Parse JSON-LD blocks from javten pages."""
        objects: list[dict] = []
        for raw in html.xpath("//script/text()"):
            text = str(raw or "").strip()
            if not text.startswith(("{", "[")):
                continue
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                continue

            items = data if isinstance(data, list) else [data]
            for item in items:
                if not isinstance(item, dict):
                    continue
                graph = item.get("@graph")
                if isinstance(graph, list):
                    objects.extend(obj for obj in graph if isinstance(obj, dict))
                objects.append(item)
        return objects

    def _movie_json_ld(self, html) -> dict:
        for item in self._json_ld_objects(html):
            raw_type = item.get("@type")
            types = raw_type if isinstance(raw_type, list) else [raw_type]
            if any(str(value).lower() == "movie" for value in types):
                return item
        return {}

    def _normalize_date(self, value: str) -> str:
        text = str(value or "").strip().replace("/", "-")
        match = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})", text)
        if not match:
            return ""
        year, month, day = match.groups()
        return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"

    def _duration_minutes(self, value: str) -> Optional[int]:
        text = str(value or "").strip().upper()
        if not text:
            return None

        match = re.match(
            r"^PT(?:(?P<h>\d+(?:\.\d+)?)H)?(?:(?P<m>\d+(?:\.\d+)?)M)?(?:(?P<s>\d+(?:\.\d+)?)S)?$",
            text,
        )
        if match:
            hours = float(match.group("h") or 0)
            minutes = float(match.group("m") or 0)
            seconds = float(match.group("s") or 0)
            total_seconds = hours * 3600 + minutes * 60 + seconds
            return int(math.ceil(total_seconds / 60)) if total_seconds > 0 else None

        clock = re.match(r"^(?:(?P<h>\d+):)?(?P<m>\d{1,2}):(?P<s>\d{2})$", text)
        if clock:
            hours = int(clock.group("h") or 0)
            minutes = int(clock.group("m") or 0)
            seconds = int(clock.group("s") or 0)
            total_seconds = hours * 3600 + minutes * 60 + seconds
            return int(math.ceil(total_seconds / 60)) if total_seconds > 0 else None

        digits = re.search(r"\d+", text)
        return int(digits.group(0)) if digits else None

    def _json_ld_image(self, movie: dict) -> str:
        image = movie.get("image") if isinstance(movie, dict) else ""
        if isinstance(image, list):
            image = image[0] if image else ""
        return self._downloadable_image_url(str(image or ""))

    def _json_ld_tags(self, movie: dict) -> list[str]:
        raw = movie.get("genre") if isinstance(movie, dict) else []
        if isinstance(raw, str):
            raw = [raw]
        if not isinstance(raw, list):
            return []
        return [str(tag or "").strip() for tag in raw if str(tag or "").strip()]

    def _json_ld_actresses(self, movie: dict) -> list[Actress]:
        names: list[str] = []

        def add(value) -> None:
            if isinstance(value, dict):
                value = value.get("name")
            text = str(value or "").strip()
            if text and text not in names:
                names.append(text)

        actor = movie.get("actor") if isinstance(movie, dict) else []
        if isinstance(actor, (str, dict)):
            actor = [actor]
        if isinstance(actor, list):
            for item in actor:
                add(item)

        description = str(movie.get("description") or "") if isinstance(movie, dict) else ""
        for match in re.finditer(
            r"[「『\"“]([一-龯々〆ヵヶぁ-んァ-ンー]{2,12}?)(?=(?:ちゃん|ち[.…．]|さん|くん|君|氏))",
            description,
        ):
            add(match.group(1))

        return [Actress(name=name) for name in names]

    def _absolute_url(self, url: str) -> str:
        url = str(url or "").strip()
        return f"https:{url}" if url.startswith("//") else url

    def _downloadable_image_url(self, url: str) -> str:
        """Convert FC2 raw storage images into the public thumbnail endpoint."""
        url = self._absolute_url(url)
        if re.match(r"^https?://storage\d+\.contents\.fc2\.com/file/", url):
            storage_path = re.sub(r"^https?://", "", url)
            return f"https://contents-thumbnail2.fc2.com/w1000/{storage_path}"
        return url

    def _gallery_image_candidates(self, html) -> list[str]:
        urls: list[str] = []
        for xpath in (
            '//a[@data-fancybox="gallery"]//img/@data-src',
            '//a[@data-fancybox="gallery"]//img/@src',
            '//a[@data-fancybox="gallery"]/@href',
            '//meta[@property="og:image"]/@content',
            '//meta[@name="twitter:image"]/@content',
        ):
            for url in html.xpath(xpath):
                urls.append(self._downloadable_image_url(url))
        return self._dedupe_urls(urls)

    def _fc2_storage_images(self, html) -> list[str]:
        urls: list[str] = []
        for xpath in (
            '//a[@data-fancybox="gallery"]//img/@data-src',
            '//a[@data-fancybox="gallery"]//img/@src',
        ):
            for url in html.xpath(xpath):
                text = self._downloadable_image_url(url)
                if re.match(
                    r"^https?://(?:contents-thumbnail2\.fc2\.com/w\d+/)?storage\d+\.contents\.fc2\.com/file/",
                    text,
                ):
                    urls.append(text)
        return self._dedupe_urls(urls)

    def _dedupe_urls(self, urls: list[str]) -> list[str]:
        out: list[str] = []
        for url in urls:
            text = str(url or "").strip()
            if text and text not in out:
                out.append(text)
        return out

    def _get_cover(self, html) -> str:
        """取得封面"""
        candidates = self._gallery_image_candidates(html)
        if candidates:
            return candidates[0]
        return ""

    def _get_extrafanart(self, html) -> list[str]:
        """取得額外劇照"""
        result = html.xpath('//div[@style="padding: 0"]/a/@href')
        urls = [self._downloadable_image_url(u) for u in result]
        urls.extend(self._fc2_storage_images(html))
        return self._dedupe_urls(urls)

    def _get_studio(self, html) -> str:
        """取得賣家（作為片商）"""
        result = html.xpath('//div[@class="col-8"]/text()')
        return result[0].strip() if result else ""

    def _get_tags(self, html) -> list[str]:
        """取得標籤"""
        result = html.xpath('//p[@class="card-text"]/a[contains(@href, "/tag/")]/text()')
        return [tag.strip() for tag in result if tag.strip()]

    def _get_outline(self, html) -> str:
        """取得簡介"""
        result = html.xpath('//div[@class="col des"]//text()')
        text = "".join(result).strip()
        # 清理文字
        text = text.replace("\\n", " ").replace("・", "").strip()
        return text

    def _is_uncensored(self, tags: list[str], title: str) -> bool:
        """判斷是否無碼"""
        text = " ".join(tags) + " " + title
        return any(kw in text for kw in ["無修正", "无修正", "uncensored"])

    def _search_url(self, fc2_number: str) -> Optional[str]:
        """搜尋並取得詳情頁 URL"""
        search_url = f"{self.BASE_URL}/search?kw={fc2_number}"

        try:
            resp = self._session.get(search_url, timeout=self.config.timeout)
            if resp.status_code != 200:
                return None

            html = etree.fromstring(resp.content, etree.HTMLParser())

            # 找符合番號的連結（在 <a> 標籤中）
            urls = html.xpath(f"//a[contains(@href, 'id{fc2_number}')]/@href")

            if not urls:
                return None

            # 優先選擇日文版（排除 /tw/, /ko/, /en/）
            non_jp_langs = ["/tw/", "/ko/", "/en/"]
            for url in urls:
                if all(lang not in url for lang in non_jp_langs):
                    return url

            # 若都沒有，返回第一個
            return urls[0]

        except Exception as e:
            logger.debug(f"FC2 search URL failed for {fc2_number}: {e}")
            return None

    def search(self, number: str) -> Optional[Video]:
        """
        搜尋影片資訊

        Args:
            number: 番號（如 FC2-PPV-1234567）

        Returns:
            Video 物件，找不到返回 None
        """
        # 正規化番號
        fc2_number = self._normalize_fc2_number(number)

        try:
            # 搜尋取得詳情頁 URL
            detail_url = self._search_url(fc2_number)
            if not detail_url:
                return None

            # 取得詳情頁
            resp = self._session.get(detail_url, timeout=self.config.timeout)
            if resp.status_code != 200:
                return None

            html = etree.fromstring(resp.content, etree.HTMLParser())
            movie = self._movie_json_ld(html)

            # 取得標題
            title = self._get_title(html)
            if not title and movie.get("name"):
                title = str(movie.get("name") or "").strip()
            if not title:
                return None

            # 取得各項資訊
            cover_url = self._get_cover(html) or self._json_ld_image(movie)
            studio = self._get_studio(html) or str(movie.get("director") or "").strip()
            tags = self._get_tags(html) or self._json_ld_tags(movie)
            outline = self._get_outline(html)
            is_uncensored = self._is_uncensored(tags, title)
            sample_images = self._get_extrafanart(html)
            actresses = self._json_ld_actresses(movie)
            release_date = self._normalize_date(str(movie.get("datePublished") or ""))
            duration = self._duration_minutes(str(movie.get("duration") or ""))

            # 移除無修正標籤（已用其他方式表示）
            tags = [t for t in tags if t not in ["無修正", "无修正"]]

            video = Video(
                number=f"FC2-PPV-{fc2_number}",
                title=title,
                actresses=actresses,
                date=release_date,
                maker=studio,
                cover_url=cover_url,
                tags=tags,
                source=self.source_name,
                detail_url=detail_url,
                duration=duration,
                sample_images=sample_images,
                summary=outline,
            )

            # 節流
            rate_limit(self.config.delay)

            return video

        except requests.Timeout:
            raise TimeoutError(f"FC2 request timeout for {number}")
        except Exception as e:
            logger.warning(f"FC2 search failed for {number}: {e}")
            return None

    def search_by_keyword(self, keyword: str, limit: int = 20) -> list[Video]:
        """
        關鍵字搜尋

        Args:
            keyword: 搜尋關鍵字（會被當作番號處理）
            limit: 最大結果數（FC2 只返回 1 筆）

        Returns:
            Video 列表（最多 1 筆）
        """
        result = self.search(keyword)
        return [result] if result else []


# 測試用
if __name__ == "__main__":
    scraper = FC2Scraper()

    print("=== FC2 番號正規化測試 ===")
    test_numbers = [
        ("FC2-PPV-1723984", "1723984"),
        ("FC2PPV1723984", "1723984"),
        ("FC2-1723984", "1723984"),
        ("1723984", "1723984"),
    ]
    for num, expected in test_numbers:
        result = scraper._normalize_fc2_number(num)
        status = "✓" if result == expected else "✗"
        print(f"{status} {num} → {result} (expected: {expected})")

    print("\n=== API 測試 ===")
    # 測試一個已知存在的 FC2 番號
    video = scraper.search("FC2-PPV-1723984")
    if video:
        print(f"番號: {video.number}")
        print(f"標題: {video.title[:50]}..." if len(video.title) > 50 else f"標題: {video.title}")
        print(f"片商: {video.maker}")
        print(f"標籤: {video.tags[:5]}...")
        print(f"封面: {video.cover_url[:60]}..." if video.cover_url else "封面: (無)")
    else:
        print("✗ 搜尋失敗")

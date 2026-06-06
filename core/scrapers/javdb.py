"""JavDB 爬蟲"""
import re
from typing import Optional
from urllib.parse import quote, urljoin

from core.logger import get_logger

logger = get_logger(__name__)
from bs4 import BeautifulSoup
from .base import BaseScraper
from .models import Video, Actress, ScraperConfig
from .utils import rate_limit, strip_number_prefix

# 嘗試載入 curl_cffi
try:
    from curl_cffi import requests as curl_requests
    CURL_CFFI_AVAILABLE = True
except ImportError:
    CURL_CFFI_AVAILABLE = False


class JavDBScraper(BaseScraper):
    """
    JavDB 爬蟲

    優點：
    - 資料最完整（有 maker）
    - Tag 豐富

    缺點：
    - 封面有浮水印
    - 需 curl_cffi 偽造 TLS 指紋
    """

    def _get_source_name(self) -> str:
        return "javdb"

    @staticmethod
    def _identity_key(value: str) -> str:
        return re.sub(r'[^A-Z0-9]+', '', str(value or '').upper())

    def _unstructured_uid_matches_query(self, uid: str, query: str) -> bool:
        uid_key = self._identity_key(uid)
        query_key = self._identity_key(query)
        if not uid_key or not query_key:
            return False
        if len(uid_key) >= 5 and uid_key in query_key:
            return True
        if len(query_key) >= 8 and query_key in uid_key:
            return True
        return False

    def _get_html(self, url: str) -> Optional[str]:
        """使用 curl_cffi 發送請求（偽造 Chrome TLS 指紋）"""
        if not CURL_CFFI_AVAILABLE:
            return None

        try:
            request_kwargs = {
                "impersonate": "chrome120",
                "headers": {
                    "User-Agent": self.config.user_agent,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "zh-TW,zh;q=0.9,ja;q=0.8,en;q=0.7",
                    "Referer": "https://javdb.com/",
                },
                "timeout": 30,
            }
            proxy_url = str(self.config.proxy_url or "").strip()
            if proxy_url and proxy_url.lower() != "direct":
                request_kwargs["proxies"] = {
                    "http": proxy_url,
                    "https": proxy_url,
                }
            response = curl_requests.get(url, **request_kwargs)

            if response.status_code == 200:
                return str(response.text)
            logger.debug("JavDB request returned HTTP %s for %s", response.status_code, url)
        except Exception as e:
            logger.debug(f"JavDB request failed for {url}: {e}")

        return None

    @staticmethod
    def _normalize_actor_name(value: str) -> str:
        return re.sub(r"\s+", "", str(value or "")).lower()

    @staticmethod
    def _image_url_from_element(elem) -> str:
        if elem is None:
            return ""
        for attr in ("src", "data-src", "data-original", "data-lazy-src"):
            value = elem.get(attr)
            if value:
                url = str(value).strip()
                if url and not url.startswith("data:"):
                    return url
        return ""

    @staticmethod
    def _absolute_javdb_url(value: str) -> str:
        value = str(value or "").strip()
        if not value:
            return ""
        if value.startswith("//"):
            return f"https:{value}"
        return urljoin("https://javdb.com/", value)

    @staticmethod
    def _is_probable_actor_image(url: str) -> bool:
        if not url:
            return False
        url_lower = url.lower()
        if any(token in url_lower for token in ("placeholder", "blank", "noimage")):
            return False
        return any(host in url_lower for host in ("javdb.com", "jdbstatic.com"))

    def _actor_name_matches(self, actual: str, expected: str) -> bool:
        actual_key = self._normalize_actor_name(actual)
        expected_key = self._normalize_actor_name(expected)
        if not actual_key or not expected_key:
            return False
        return (
            actual_key == expected_key
            or expected_key in actual_key
            or actual_key in expected_key
        )

    def _actor_image_from_detail(self, actor_path: str) -> Optional[str]:
        detail_url = self._absolute_javdb_url(actor_path)
        if not detail_url:
            return None
        html = self._get_html(detail_url)
        if not html:
            return None

        soup = BeautifulSoup(html, "html.parser")
        selectors = [
            ".actor-section img",
            ".actor-avatar img",
            ".avatar-box img",
            ".photo-frame img",
            ".profile img",
            ".actor-info img",
            ".star-box img",
            "img",
        ]
        for selector in selectors:
            for img in soup.select(selector):
                url = self._absolute_javdb_url(self._image_url_from_element(img))
                if self._is_probable_actor_image(url):
                    return url
        return None

    def search_actress_photo(self, name: str) -> Optional[str]:
        """
        Search JavDB actor results and return the actor profile image URL.
        """
        name = str(name or "").strip()
        if not name:
            return None

        try:
            url = f"https://javdb.com/search?f=actor&q={quote(name)}"
            html = self._get_html(url)
            if not html:
                return None

            soup = BeautifulSoup(html, "html.parser")
            links = soup.select('a[href^="/actors/"], a[href^="/actor/"]')
            for link in links:
                container = link.find_parent(class_=re.compile(r"(actor|avatar|star|item|box)"))
                search_node = container or link
                label_parts = [
                    link.get_text(" ", strip=True),
                    link.get("title", ""),
                ]
                img = search_node.select_one("img") if hasattr(search_node, "select_one") else None
                if img is not None:
                    label_parts.extend([img.get("alt", ""), img.get("title", "")])
                label = " ".join(str(part or "") for part in label_parts).strip()
                if not self._actor_name_matches(label, name):
                    continue

                image_url = self._absolute_javdb_url(self._image_url_from_element(img))
                if self._is_probable_actor_image(image_url):
                    return image_url

                detail_url = self._actor_image_from_detail(str(link.get("href", "")))
                if detail_url:
                    return detail_url

            return None

        except Exception as e:
            logger.warning("JavDB actress photo search failed for %s: %s", name, e)
            return None

    def search(self, number: str) -> Optional[Video]:
        """
        搜尋影片資訊

        Args:
            number: 番號

        Returns:
            Video 物件或 None
        """
        original_query = str(number or '').strip()
        number = self.normalize_number(original_query)
        structured_query = self.validate_number(number)
        search_query = number if structured_query else original_query

        try:
            # 先搜尋取得列表
            search_url = f"https://javdb.com/search?q={quote(search_query)}&f=all"
            html = self._get_html(search_url)

            if not html:
                return None

            soup = BeautifulSoup(html, 'html.parser')

            # 找到精確匹配的番號
            detail_path = None
            matched_uid = ''
            number_upper = self._identity_key(number)
            matches: list[tuple[str, str]] = []

            for item in soup.select('.movie-list .item')[:5]:
                uid_elem = item.select_one('.video-title strong')
                uid = uid_elem.text.strip() if uid_elem else ''
                uid_normalized = self._identity_key(uid)
                link_elem = item.select_one('a[href^="/v/"]')
                if not link_elem:
                    continue
                if structured_query and uid_normalized != number_upper:
                    continue
                if not structured_query and not self._unstructured_uid_matches_query(uid, original_query):
                    continue
                matches.append((uid, str(link_elem['href'])))

            if matches:
                if not structured_query:
                    unique_uids = {self._identity_key(uid) for uid, _path in matches}
                    if len(unique_uids) > 1:
                        return None
                matched_uid, detail_path = matches[0]

            if not detail_path:
                return None

            # 獲取詳情頁
            detail_url = f"https://javdb.com{detail_path}"
            detail_html = str(self._get_html(detail_url) or "")

            if not detail_html:
                return None

            soup = BeautifulSoup(detail_html, 'html.parser')

            # 標題（用 get_text(separator=' ') 把嵌入換行轉空格，再剝番號前綴）
            title_elem = soup.select_one('.video-detail h2, .title.is-4')
            title = title_elem.get_text(separator=' ', strip=True) if title_elem else ''
            video_number = self.normalize_number(matched_uid) if matched_uid else search_query
            title = strip_number_prefix(title, video_number)

            # 封面
            cover_elem = soup.select_one('.video-cover img, .column-video-cover img')
            cover_url = str(cover_elem.get('src', '')) if cover_elem else ''

            # 解析資訊面板
            date = ''
            maker = ''
            actresses = []
            tags = []

            for panel in soup.select('.panel-block'):
                label = panel.select_one('strong')
                value = panel.select_one('.value')

                if not label:
                    continue

                label_text = label.text.strip()

                # 日期
                if '日期' in label_text and value:
                    date = value.text.strip()

                # 片商（排除「發行日期」避免把日期誤判為片商）
                if ('片商' in label_text or '製作' in label_text or '發行' in label_text) and '日期' not in label_text:
                    if value:
                        maker = value.text.strip()

                # 演員（只抓女優）
                if '演員' in label_text:
                    for a in panel.select('a'):
                        name = a.text.strip()
                        if not name:
                            continue

                        # 檢查性別標記
                        next_elem = a.find_next_sibling()
                        
                        # 跳過男優
                        classes: list[str] = []
                        if next_elem and hasattr(next_elem, 'get'):
                            cls_val = next_elem.get('class')
                            if isinstance(cls_val, list):
                                classes = [str(c) for c in cls_val]
                            else:
                                classes = [str(cls_val)] if cls_val else []
                        
                        if 'male' in classes and 'female' not in classes:
                            continue

                        actresses.append(Actress(name=name))

                # 標籤
                if '類別' in label_text:
                    tag_elems = panel.select('a')
                    tags = [t.text.strip() for t in tag_elems if t.text.strip()]

            if not title and not cover_url:
                return None

            # DMM 圖片：ps.jpg → pl.jpg（小圖 → 大圖）
            if cover_url:
                cover_url = str(cover_url).replace('ps.jpg', 'pl.jpg').replace('/pt/', '/pl/')

            video = Video(
                number=video_number,
                title=title,
                actresses=actresses,
                date=date,
                maker=maker,
                cover_url=cover_url,
                tags=tags,
                source=self.source_name,
                detail_url=detail_url,
            )

            rate_limit(self.config.delay)

            return video

        except Exception as e:
            logger.warning(f"JavDB search failed for {number}: {e}")
            return None

    def search_by_keyword(self, keyword: str, limit: int = 20) -> list[Video]:
        """
        關鍵字搜尋

        Args:
            keyword: 搜尋關鍵字
            limit: 最大結果數

        Returns:
            Video 列表
        """
        try:
            url = f"https://javdb.com/search?q={quote(keyword)}&f=all"
            html = self._get_html(url)

            if not html:
                return []

            soup = BeautifulSoup(html, 'html.parser')
            results = []

            for item in soup.select('.movie-list .item')[:limit]:
                try:
                    uid_elem = item.select_one('.video-title strong')
                    number = uid_elem.text.strip() if uid_elem else ''

                    if not number:
                        continue

                    # 遞迴呼叫 search() 取得完整資訊
                    video = self.search(number)
                    if video:
                        results.append(video)

                except Exception as e:
                    logger.debug(f"JavDB keyword search item failed: {e}")
                    continue

            return results

        except Exception as e:
            logger.warning(f"JavDB keyword search failed for {keyword}: {e}")
            return []


def _configured_proxy_url() -> str:
    try:
        from core.config import load_config
        proxy_url = str((load_config().get("search") or {}).get("proxy_url") or "").strip()
    except Exception as e:
        logger.warning("JavDB actress photo proxy config lookup failed: %s", e)
        return ""
    if proxy_url.lower() == "direct":
        return ""
    return proxy_url


def scrape_javdb_actress_photo(name: str, proxy_url: Optional[str] = None) -> Optional[str]:
    effective_proxy = _configured_proxy_url() if proxy_url is None else str(proxy_url or "").strip()
    if effective_proxy.lower() == "direct":
        effective_proxy = ""
    return JavDBScraper(ScraperConfig(proxy_url=effective_proxy)).search_actress_photo(name)

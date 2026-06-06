"""JAV321 爬蟲"""
import re
from typing import Optional

from core.logger import get_logger

logger = get_logger(__name__)
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from core.maker_mapping import load_prefix_mapping, normalize_maker_name
from .base import BaseScraper
from .models import Video, Actress
from .utils import get_html, post_html, rate_limit, strip_number_prefix


def _force_https(url: str) -> str:
    """DMM 圖片回傳 http://，但 /api/proxy-image SSRF 白名單強制 https。
    這裡統一升 https，否則前端封面 / 劇照會被 proxy 403 擋掉顯示不出。"""
    if url and url.startswith('http://'):
        return 'https://' + url[len('http://'):]
    return url


def _find_next_a_before_next_b(b_tag):
    """找 <b> 後面、下一個 <b> 之前的第一個 <a>，避免跨欄位誤抓。"""
    for sib in b_tag.next_siblings:
        if getattr(sib, 'name', None) == 'b':
            break  # 到了下一個欄位，停止
        if getattr(sib, 'name', None) == 'a':
            return sib
    return None


def _text_after_b_before_next_b(b_tag) -> str:
    parts: list[str] = []
    for sib in b_tag.next_siblings:
        if getattr(sib, 'name', None) == 'b':
            break
        if getattr(sib, 'name', None) == 'br':
            continue
        if hasattr(sib, 'get_text'):
            text = sib.get_text(' ', strip=True)
        else:
            text = str(sib)
        text = text.strip()
        if text:
            parts.append(text)
    return re.sub(r'^[\s:：]+', '', ' '.join(parts)).strip()


def _field_text(col9, label_name: str) -> str:
    if not col9:
        return ''
    for b_tag in col9.find_all('b'):
        if b_tag.get_text(strip=True) == label_name:
            return _text_after_b_before_next_b(b_tag)
    return ''


def _heading_title_text(title_elem) -> str:
    if not title_elem:
        return ''
    soup = BeautifulSoup(str(title_elem), 'html.parser')
    for small in soup.select('small'):
        small.decompose()
    return soup.get_text(' ', strip=True)


def _identity_key(value: str) -> str:
    """Return a loose comparison key for JAV numbers and maker names."""
    return re.sub(r'[^A-Z0-9]+', '', str(value or '').upper())


_COMPACT_DISTINCT_PREFIXES = {'RED'}


def _number_identity_key(value: str) -> str:
    text = str(value or '').strip().upper()
    match = re.fullmatch(r'([A-Z]{2,7})([-_]?)(\d{2,5})', text)
    if match and match.group(1) in _COMPACT_DISTINCT_PREFIXES:
        separator = 'COMPACT' if not match.group(2) else 'SEPARATED'
        return f"{separator}:{match.group(1)}{match.group(2)}{match.group(3)}"
    return _identity_key(text)


def _find_number_in_text(text: str) -> str:
    match = re.search(r'(?<![A-Z0-9])([A-Z]{1,10}[-_]?\d{1,6})(?![A-Z0-9])', str(text or ''), flags=re.IGNORECASE)
    return match.group(1).upper() if match else ''


def _heading_matches_number(title_elem, number: str) -> bool:
    """JAV321 sometimes returns fuzzy detail pages; verify the displayed number."""
    if not title_elem:
        return False

    candidates = [small.get_text(' ', strip=True) for small in title_elem.select('small')]
    candidates.append(title_elem.get_text(' ', strip=True))

    for text in candidates:
        displayed_number = _find_number_in_text(text)
        if displayed_number:
            return _number_identity_key(displayed_number) == _number_identity_key(number)
    return False


def _maker_matches_prefix(number: str, maker: str) -> bool:
    """Reject cross-maker collisions when a known prefix mapping exists."""
    if not maker:
        return True

    match = re.match(r'^([A-Z]+)', str(number or ''), flags=re.IGNORECASE)
    if not match:
        return True

    expected = load_prefix_mapping().get(match.group(1).upper(), '')
    if not expected:
        return True

    return _identity_key(normalize_maker_name(maker)) == _identity_key(expected)


class JAV321Scraper(BaseScraper):
    """
    JAV321 爬蟲

    優點：
    - 資料完整
    - 封面完整（非裁切）
    - 穩定性高
    """

    def _get_source_name(self) -> str:
        return "jav321"

    def search(self, number: str) -> Optional[Video]:
        """
        搜尋影片資訊

        Args:
            number: 番號

        Returns:
            Video 物件或 None
        """
        number = self.normalize_number(number)

        if not self.validate_number(number):
            raise ValueError(f"Invalid number format: {number}")

        try:
            # POST 搜尋
            search_url = 'https://www.jav321.com/search'
            html = post_html(search_url, data={'sn': number}, timeout=self.config.timeout)

            if not html:
                return None

            # 檢查是否直接跳轉到詳情頁
            if '/video/' in html and '<h3>' in html:
                detail_html = html
            else:
                # 解析搜尋結果
                soup = BeautifulSoup(html, 'html.parser')
                link = soup.select_one('.row a[href*="/video/"]')

                if not link:
                    return None

                detail_url = urljoin('https://www.jav321.com', str(link.get('href')))
                dh = get_html(detail_url, timeout=self.config.timeout)
                detail_html = dh if dh else ""
                
                if not detail_html:
                    return None

            # 解析詳情頁
            soup = BeautifulSoup(detail_html, 'html.parser')
            col9 = soup.select_one('.col-md-9')

            # 番號欄位是結構化證據；標題和 <small> 僅可作 fallback。
            title_elem = soup.select_one('h3')
            product_number = self.normalize_number(_field_text(col9, '品番'))
            if product_number and _number_identity_key(product_number) != _number_identity_key(number):
                logger.debug(
                    "JAV321 detail rejected for %s: product number mismatch (%s)",
                    number,
                    product_number,
                )
                return None
            if not product_number and not _heading_matches_number(title_elem, number):
                raw_title = title_elem.get_text(separator=' ', strip=True) if title_elem else ''
                logger.debug("JAV321 detail rejected for %s: heading mismatch (%s)", number, raw_title)
                return None

            display_number = product_number or number
            title = _heading_title_text(title_elem)
            title = strip_number_prefix(title, display_number)
            if not title and title_elem:
                title = title_elem.get_text(separator=' ', strip=True)
            if not title:
                logger.debug("JAV321 detail rejected for %s: heading mismatch (%s)", number, title)
                return None

            # 封面（轉換成完整版）
            img_elem = soup.select_one('.col-md-3 img')
            cover_url = img_elem.get('src', '') if img_elem else ''
            if cover_url and not str(cover_url).startswith('http'):
                cover_url = urljoin('https://www.jav321.com', str(cover_url))
            # DMM 圖片：ps.jpg → pl.jpg（小圖 → 大圖）
            if cover_url:
                cover_url = str(cover_url).replace('ps.jpg', 'pl.jpg').replace('/pt/', '/pl/')
                cover_url = _force_https(cover_url)

            # 女優（去重）
            actresses = []
            seen_names = set()
            for a in soup.select('a[href*="/star/"]'):
                name = a.get_text(strip=True)
                if name and name not in seen_names:
                    actresses.append(Actress(name=name))
                    seen_names.add(name)

            # 日期
            date = ''
            date_match = re.search(r'(\d{4}-\d{2}-\d{2})', detail_html)
            if date_match:
                date = date_match.group(1)

            # 標籤
            tags = []
            for a in soup.select('a[href*="/genre/"]'):
                tag = a.get_text(strip=True)
                if tag:
                    tags.append(tag)

            # 補齊欄位：maker、duration、series（從 .col-md-9 解析）
            maker = ''
            duration: Optional[int] = None
            series = ''

            if col9:
                for b in col9.find_all('b'):
                    label = b.get_text(strip=True)
                    if label == 'メーカー':
                        a_tag = _find_next_a_before_next_b(b)
                        maker = a_tag.get_text(strip=True) if a_tag else ''
                    elif label == '収録時間':
                        sibling = b.next_sibling
                        if sibling:
                            text = str(sibling)
                            m = re.search(r'(\d+)', text)
                            if m:
                                duration = int(m.group(1))
                    elif label == 'シリーズ':
                        a_tag = _find_next_a_before_next_b(b)
                        series = a_tag.get_text(strip=True) if a_tag else ''

            if not _maker_matches_prefix(display_number, maker):
                logger.debug("JAV321 detail rejected for %s: maker mismatch (%s)", number, maker)
                return None

            # sample_images：跳過封面（href 結尾 /0）
            sample_images = []
            for a_tag in soup.select('a[href*="/snapshot/"]'):
                href = a_tag.get('href', '')
                if href.endswith('/0'):
                    continue
                img = a_tag.find('img')
                if img:
                    src = img.get('src', '')
                    if src and src.startswith('http'):
                        sample_images.append(_force_https(src))
                    elif src:
                        sample_images.append(urljoin('https://www.jav321.com', src))

            if not title and not cover_url:
                return None

            video = Video(
                number=display_number,
                title=title,
                actresses=actresses,
                date=date,
                maker=maker,
                cover_url=str(cover_url) if cover_url else "",
                tags=tags,
                source=self.source_name,
                detail_url=f'https://www.jav321.com/video/{number.lower()}',
                duration=duration,
                series=series,
                sample_images=sample_images,
            )

            rate_limit(self.config.delay)

            return video

        except Exception as e:
            logger.warning(f"JAV321 search failed for {number}: {e}")
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
            search_url = 'https://www.jav321.com/search'
            html = post_html(search_url, data={'sn': keyword}, timeout=self.config.timeout)

            if not html:
                return []

            soup = BeautifulSoup(html, 'html.parser')
            results = []

            for item in soup.select('.row .item')[:limit]:
                try:
                    # 提取番號
                    link = item.select_one('a[href*="/video/"]')
                    if not link:
                        continue

                    href = str(link.get('href', ''))
                    number_match = re.search(r'/video/([^/]+)', href)
                    if not number_match:
                        continue

                    number = number_match.group(1).upper()

                    # 遞迴呼叫 search() 取得完整資訊
                    video = self.search(number)
                    if video:
                        results.append(video)

                except Exception as e:
                    logger.debug(f"JAV321 keyword search item failed: {e}")
                    continue

            return results

        except Exception as e:
            logger.warning(f"JAV321 keyword search failed for {keyword}: {e}")
            return []

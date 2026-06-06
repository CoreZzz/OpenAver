"""
test_fc2_scraper.py - FC2 爬蟲單元測試（TASK-36-T9）

測試策略：
- 全 mock，不連網
- Mock scraper._session.get 回傳 inline HTML fixture
- rate_limit 也 mock 掉（避免 sleep）
"""

import pytest
from unittest.mock import patch, MagicMock


# ============================================================
# HTML Fixtures
# ============================================================

SEARCH_HTML = """\
<html><body>
<a href="/id1723984">FC2-PPV-1723984</a>
</body></html>
"""

# Detail page with extrafanart
FULL_FIELDS_HTML = """\
<html><body>
<h1>FC2-1723984</h1>
<h1>テストタイトル</h1>
<div class="col-8">テスト賣家</div>
<a data-fancybox="gallery" href="//pics.example.com/cover.jpg">
  <img src="//pics.example.com/thumb.jpg">
</a>
<div style="padding: 0">
  <a href="//pics.example.com/gallery/001.jpg"><img src="//pics.example.com/gallery/001s.jpg"></a>
  <a href="//pics.example.com/gallery/002.jpg"><img src="//pics.example.com/gallery/002s.jpg"></a>
</div>
<p class="card-text">
  <a href="/tag/amateur">アマチュア</a>
</p>
</body></html>
"""

# Detail page without extrafanart
NO_GALLERY_HTML = """\
<html><body>
<h1>FC2-1723984</h1>
<h1>テストタイトル</h1>
<div class="col-8">テスト賣家</div>
<a data-fancybox="gallery" href="//pics.example.com/cover.jpg">
  <img src="//pics.example.com/thumb.jpg">
</a>
<p class="card-text">
  <a href="/tag/amateur">アマチュア</a>
</p>
</body></html>
"""

EXPIRED_MAIN_WITH_STORAGE_SAMPLE_HTML = """\
<html><body>
<h1>FC2-1723984</h1>
<h1>テストタイトル</h1>
<div class="col-8">テスト賣家</div>
<a data-fancybox="gallery" href="https://storage14000.contents.fc2.com/file/349/34883644/1570439183.78.jpg">
  <img src="https://storage15000.contents.fc2.com/file/349/34883644/1570438816.99.jpg">
</a>
<p class="card-text">
  <a href="/tag/amateur">アマチュア</a>
</p>
</body></html>
"""


# ============================================================
# Helpers
# ============================================================

def make_response(html: str, status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = html
    resp.content = html.encode("utf-8")
    return resp


def run_search(scraper, detail_html: str, number: str = "FC2-PPV-1723984"):
    """
    Mock _search_url to bypass search page, then mock detail GET.
    """
    detail_resp = make_response(detail_html)
    with patch.object(scraper, "_search_url", return_value="https://javten.com/id1723984"):
        scraper._session.get = MagicMock(return_value=detail_resp)
        return scraper.search(number)


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def scraper():
    from core.scrapers.fc2 import FC2Scraper
    with patch("core.scrapers.fc2.rate_limit"):
        s = FC2Scraper()
        yield s


# ============================================================
# Tests
# ============================================================

class TestFullFields:
    """happy path: sample_images 有 URL（list[str]）"""

    def test_sample_images_present(self, scraper):
        video = run_search(scraper, FULL_FIELDS_HTML)
        assert video is not None
        assert len(video.sample_images) == 2

    def test_sample_images_absolute_url(self, scraper):
        video = run_search(scraper, FULL_FIELDS_HTML)
        assert video is not None
        for url in video.sample_images:
            assert url.startswith("https://")

    def test_seller_is_not_used_as_actress(self, scraper):
        video = run_search(scraper, FULL_FIELDS_HTML)
        assert video is not None
        assert video.maker
        assert video.actresses == []

    def test_fc2_storage_sample_is_used_as_downloadable_cover(self, scraper):
        video = run_search(scraper, EXPIRED_MAIN_WITH_STORAGE_SAMPLE_HTML)
        assert video is not None
        assert video.cover_url == (
            "https://contents-thumbnail2.fc2.com/w1000/"
            "storage15000.contents.fc2.com/file/349/34883644/1570438816.99.jpg"
        )
        assert video.sample_images == [video.cover_url]


class TestNoGallery:
    """無 extrafanart → sample_images=[]"""

    def test_no_extrafanart_empty_list(self, scraper):
        video = run_search(scraper, NO_GALLERY_HTML)
        assert video is not None
        assert video.sample_images == []

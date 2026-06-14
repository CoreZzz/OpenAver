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
<html><head><meta charset="utf-8"></head><body>
<a href="/id1723984">FC2-PPV-1723984</a>
</body></html>
"""

# Detail page with extrafanart
FULL_FIELDS_HTML = """\
<html><head><meta charset="utf-8"></head><body>
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

JSON_LD_FULL_FIELDS_HTML = """\
<html><head><meta charset="utf-8"></head><body>
<h1>FC2-PPV-2240347</h1>
<h1>杏ちゃん 生中出し【無】超敏感大量潮吹きの天然系ほんわか美人</h1>
<div class="col-8">ザ・流し屋</div>
<a data-fancybox="gallery" href="https://storage59000.contents.fc2.com/file/378/37758831/1632318733.65.jpg">
  <img src="https://storage59000.contents.fc2.com/file/378/37758831/1632318733.65.jpg">
</a>
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "Movie",
  "name": "杏ちゃん 生中出し【無】超敏感大量潮吹きの天然系ほんわか美人",
  "description": "天然系ほんわか美人「水希杏ち…」のサンプル必見",
  "image": "https://storage30000.contents.fc2.com/file/378/37758831/1632391662.54.jpg",
  "identifier": ["FC2-PPV-2240347", "FC2-2240347", "2240347"],
  "datePublished": "2021/09/23",
  "duration": "PT44M26S",
  "actor": [],
  "genre": ["ハメ撮り", "無修正", "美人"],
  "director": "ザ・流し屋"
}
</script>
</body></html>
"""

JSON_LD_ACTOR_HTML = """\
<html><head><meta charset="utf-8"></head><body>
<h1>FC2-PPV-2240348</h1>
<h1>テストタイトル</h1>
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "Movie",
  "name": "テストタイトル",
  "datePublished": "2021-10-31",
  "duration": "PT1H22M39S",
  "actor": [{"@type": "Person", "name": "水希杏"}, "別名テスト"],
  "genre": "無修正"
}
</script>
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

RELATED_STORAGE_ONLY_HTML = """\
<html><body>
<h1>FC2-1723984</h1>
<h1>Test Title</h1>
<div class="col-8">Test Studio</div>
<img src="https://storage99999.contents.fc2.com/file/999/99999999/related.jpg">
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

    def test_page_wide_storage_image_is_not_used_as_cover(self, scraper):
        video = run_search(scraper, RELATED_STORAGE_ONLY_HTML)
        assert video is not None
        assert video.cover_url == ""
        assert video.sample_images == []

    def test_json_ld_date_duration_and_description_actress(self, scraper):
        video = run_search(scraper, JSON_LD_FULL_FIELDS_HTML, "FC2-PPV-2240347")
        assert video is not None
        assert video.number == "FC2-PPV-2240347"
        assert video.date == "2021-09-23"
        assert video.duration == 45
        assert [a.name for a in video.actresses] == ["水希杏"]
        assert video.cover_url == (
            "https://contents-thumbnail2.fc2.com/w1000/"
            "storage59000.contents.fc2.com/file/378/37758831/1632318733.65.jpg"
        )
        assert "無修正" not in video.tags

    def test_json_ld_actor_and_duration_are_parsed(self, scraper):
        video = run_search(scraper, JSON_LD_ACTOR_HTML, "FC2-PPV-2240348")
        assert video is not None
        assert video.date == "2021-10-31"
        assert video.duration == 83
        assert [a.name for a in video.actresses] == ["水希杏", "別名テスト"]


class TestNoGallery:
    """無 extrafanart → sample_images=[]"""

    def test_no_extrafanart_empty_list(self, scraper):
        video = run_search(scraper, NO_GALLERY_HTML)
        assert video is not None
        assert video.sample_images == []

"""
test_heyzo_scraper.py - HEYZO 爬蟲單元測試（TASK-36-T9）

測試策略：
- 全 mock，不連網
- Mock scraper._session.get 回傳 inline HTML + JSON-LD fixture
- rate_limit 也 mock 掉（避免 sleep）
"""

import json
import pytest
from unittest.mock import patch, MagicMock


# ============================================================
# JSON-LD base (embedded in HTML)
# ============================================================

BASE_JSON_LD = {
    "@context": "http://schema.org",
    "@type": "Movie",
    "name": "テストタイトル",
    "dateCreated": "2023-01-17T00:00:00+09:00",
    "image": "//www.heyzo.com/contents/3000/0783/images/player_thumbnail.jpg",
    "actor": {"@type": "Person", "name": "Test Actress"},
    "aggregateRating": {"ratingValue": "4.2", "reviewCount": "100"},
}


def make_html(json_ld: dict, table_extra: str = "", gallery_html: str = "") -> bytes:
    """Build an en.heyzo.com-like HTML page with JSON-LD and optional table rows.

    Reflects actual HEYZO DOM: table.movieInfo uses <td><td> pairs (no <th>).
    Duration lives in table.downloads, not table.movieInfo.
    """
    json_str = json.dumps(json_ld)
    html = f"""\
<html><head>
<meta charset="utf-8">
<script type="application/ld+json">{json_str}</script>
</head><body>
<table class="movieInfo">
  <tbody>
    <tr><td>Series</td><td>テストシリーズ</td></tr>
    <tr><td>Type</td><td><a href="/type/1">美乳</a></td></tr>
  </tbody>
</table>
{table_extra}
{gallery_html}
</body></html>
"""
    return html.encode("utf-8")


GALLERY_HTML = """\
<script>var dir_gallery = "/contents/3000/0783/gallery/";</script>
<div class="sample-images yoxview">
  <h1>Gallery</h1>
  <script>document.write('<img src="'+dir_gallery+'thumbnail_001.jpg">');</script>
  <script>document.write('<img src="'+dir_gallery+'thumbnail_002.jpg">');</script>
</div>
"""

DURATION_JS = """\
<script>
heyzo.duration = function(){
  var o = Object();
  o = {"full":"01:07:57","1":"00:19:33"};
  return o;
};
</script>
"""

INVALID_DURATION_JS = """\
<script>
heyzo.duration = function(){
  var o = Object();
  o = {"full":"--:--:--"};
  return o;
};
</script>
"""

FULL_FIELDS_CONTENT = make_html(
    BASE_JSON_LD,
    table_extra=DURATION_JS,
    gallery_html=GALLERY_HTML,
)

NO_DURATION_CONTENT = make_html(
    BASE_JSON_LD,
    table_extra='',
    gallery_html=GALLERY_HTML,
)

INVALID_DURATION_CONTENT = make_html(
    BASE_JSON_LD,
    table_extra=INVALID_DURATION_JS,
    gallery_html=GALLERY_HTML,
)

NO_SERIES_CONTENT = make_html(
    BASE_JSON_LD,
    table_extra=DURATION_JS,
    gallery_html=GALLERY_HTML,
).replace("テストシリーズ".encode("utf-8"), b"-----")

NO_GALLERY_CONTENT = make_html(
    BASE_JSON_LD,
    table_extra=DURATION_JS,
    gallery_html="",
)


# ============================================================
# Helpers
# ============================================================

def make_response(content: bytes, status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.content = content
    resp.text = content.decode("utf-8")
    resp.url = "https://en.heyzo.com/moviepages/0783/index.html"
    return resp


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def scraper():
    from core.scrapers.heyzo import HEYZOScraper
    with patch("core.scrapers.heyzo.rate_limit"):
        s = HEYZOScraper()
        yield s


# ============================================================
# Tests
# ============================================================

class TestFullFields:
    """happy path: series, duration, sample_images all correct"""

    def test_series(self, scraper):
        scraper._session.get = MagicMock(return_value=make_response(FULL_FIELDS_CONTENT))
        video = scraper.search("HEYZO-0783")
        assert video is not None
        assert video.series == "テストシリーズ"

    def test_duration_hhmm_ss_to_minutes(self, scraper):
        """01:07:57 → 67 分鐘（1*60 + 7）"""
        scraper._session.get = MagicMock(return_value=make_response(FULL_FIELDS_CONTENT))
        video = scraper.search("HEYZO-0783")
        assert video is not None
        assert video.duration == 67
        assert isinstance(video.duration, int)

    def test_sample_images_absolute_url(self, scraper):
        scraper._session.get = MagicMock(return_value=make_response(FULL_FIELDS_CONTENT))
        video = scraper.search("HEYZO-0783")
        assert video is not None
        assert len(video.sample_images) == 2
        for url in video.sample_images:
            assert url.startswith("https://")


class TestNoDuration:
    """Duration 欄位缺失 → duration=None"""

    def test_duration_none(self, scraper):
        scraper._session.get = MagicMock(return_value=make_response(NO_DURATION_CONTENT))
        video = scraper.search("HEYZO-0783")
        assert video is not None
        assert video.duration is None


class TestInvalidDuration:
    """Duration 格式異常（--:--:--）→ duration=None"""

    def test_invalid_duration_none(self, scraper):
        scraper._session.get = MagicMock(return_value=make_response(INVALID_DURATION_CONTENT))
        video = scraper.search("HEYZO-0783")
        assert video is not None
        assert video.duration is None


class TestNoGallery:
    """無 gallery → sample_images=[]"""

    def test_no_gallery_empty_list(self, scraper):
        scraper._session.get = MagicMock(return_value=make_response(NO_GALLERY_CONTENT))
        video = scraper.search("HEYZO-0783")
        assert video is not None
        assert video.sample_images == []


class TestNoSeries:
    """Series 欄位顯示 '-----' placeholder → series 應為空字串"""

    def test_series_placeholder_is_empty(self, scraper):
        scraper._session.get = MagicMock(return_value=make_response(NO_SERIES_CONTENT))
        video = scraper.search("HEYZO-0783")
        assert video is not None
        assert video.series == ""


# ============================================================
# Mock Data (from test_new_scrapers.py)
# ============================================================

HEYZO_EN_HTML = b"""
<html>
<head>
<meta charset="utf-8">
<script type="application/ld+json">
{
    "@type": "Movie",
    "name": "Slim Beauty's Seduction",
    "actor": {"@type": "Person", "name": "Airi Mashiro"},
    "dateCreated": "2015-01-17T00:00:00+09:00",
    "image": "//en.heyzo.com/contents/3000/0783/images/player_thumbnail_450.jpg",
    "aggregateRating": {"ratingValue": "4.22", "reviewCount": "56"}
}
</script>
</head>
<body>
<table class="movieInfo">
<tr><td>Series</td><td>Premium Collection</td></tr>
<tr><td>Type</td><td><a>Cute</a> <a>Slender</a></td></tr>
</table>
</body>
</html>
"""

HEYZO_JA_HTML = """
<html>
<head>
<meta charset="utf-8">
<script type="application/ld+json">
{
    "@type": "Movie",
    "name": "続々生中～快感にのけぞる絶品ボディ～",
    "actor": {"@type": "Person", "name": "立花瑠莉"},
    "dateCreated": "2018-02-03T00:00:00+09:00",
    "duration": "PT0H53M32S",
    "image": "//www.heyzo.com/contents/3000/1660/images/player_thumbnail.jpg",
    "aggregateRating": {"ratingValue": 4.6, "reviewCount": 22}
}
</script>
</head>
<body>
<table class="movieInfo">
<tr><td>シリーズ</td><td>続々生中</td></tr>
<tr><td>女優タイプ</td><td><a>美乳</a> <a>巨乳</a></td></tr>
<tr><td>タグキーワード</td><td><a>騎乗位</a> <a>中出し</a></td></tr>
</table>
</body>
</html>
""".encode("utf-8")

HEYZO_JA_NO_JSON_HTML = """
<html>
<head>
<meta charset="utf-8">
<title>さくらみな 【さくらみな】 さくらみながお漏らししちゃった！ - 無修正アダルト動画 HEYZO</title>
</head>
<body>
<table class="movieInfo">
<tr><td>公開日</td><td>2024-05-02</td></tr>
<tr><td>出演</td><td>さくらみな</td></tr>
<tr><td>シリーズ</td><td>-----</td></tr>
<tr><td>女優タイプ</td><td><a>AV女優</a> <a>スレンダー</a></td></tr>
<tr><td>タグキーワード</td><td><a>お漏らし</a></td></tr>
</table>
<script>
heyzo.duration = function(){
  return {"full":"00:20:41"};
};
</script>
</body>
</html>
""".encode("utf-8")


def _make_mock_resp(status_code=200, json_data=None, content=None):
    """Build a MagicMock that mimics requests.Response."""
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    if json_data is not None:
        mock_resp.json = lambda: json_data
    if content is not None:
        mock_resp.content = content
    return mock_resp


# ============================================================
# Tests merged from integration/test_new_scrapers.py TestHEYZOScraper
# ============================================================

class TestHEYZOIntegration:
    """HEYZO scraper tests (merged from test_new_scrapers.py)"""

    @pytest.fixture
    def scraper(self):
        from core.scrapers.heyzo import HEYZOScraper
        return HEYZOScraper()

    def test_heyzo_success(self, scraper):
        """HEYZO-0783 搜尋成功，解析 JSON-LD 所有欄位"""
        mock_resp = _make_mock_resp(status_code=200, content=HEYZO_EN_HTML)

        with patch.object(scraper._session, 'get', return_value=mock_resp):
            with patch('core.scrapers.heyzo.rate_limit'):
                video = scraper.search("HEYZO-0783")

        assert video is not None
        assert video.number == "HEYZO-0783"
        assert video.title == "Slim Beauty's Seduction"
        assert len(video.actresses) == 1
        assert video.actresses[0].name == "Airi Mashiro"
        assert video.date == "2015-01-17"
        assert video.source == "heyzo"
        assert video.maker == "HEYZO"
        assert video.rating == 4.22

    def test_heyzo_prefers_japanese_page(self, scraper):
        """HEYZO should use Japanese official metadata when available."""
        mock_resp = _make_mock_resp(status_code=200, content=HEYZO_JA_HTML)

        with patch.object(scraper._session, 'get', return_value=mock_resp):
            with patch('core.scrapers.heyzo.rate_limit'):
                video = scraper.search("HEYZO-1660")

        assert video is not None
        assert video.number == "HEYZO-1660"
        assert video.title == "続々生中～快感にのけぞる絶品ボディ～"
        assert [a.name for a in video.actresses] == ["立花瑠莉"]
        assert video.date == "2018-02-03"
        assert video.cover_url == "https://www.heyzo.com/contents/3000/1660/images/player_thumbnail.jpg"
        assert video.series == "続々生中"
        assert video.duration == 53
        assert video.tags == ["美乳", "巨乳", "騎乗位", "中出し"]
        assert video.rating == 4.6

    def test_heyzo_japanese_html_fallback_without_json_ld(self, scraper):
        """Japanese HEYZO pages without JSON-LD should not fall back to English."""
        mock_resp = _make_mock_resp(status_code=200, content=HEYZO_JA_NO_JSON_HTML)

        with patch.object(scraper._session, 'get', return_value=mock_resp):
            with patch('core.scrapers.heyzo.rate_limit'):
                video = scraper.search("HEYZO-3312")

        assert video is not None
        assert video.number == "HEYZO-3312"
        assert video.title == "さくらみながお漏らししちゃった！"
        assert [a.name for a in video.actresses] == ["さくらみな"]
        assert video.date == "2024-05-02"
        assert video.cover_url == "https://www.heyzo.com/contents/3000/3312/images/player_thumbnail.jpg"
        assert video.series == ""
        assert video.duration == 20
        assert video.tags == ["AV女優", "スレンダー", "お漏らし"]

    def test_heyzo_strip_prefix(self, scraper):
        """_extract_heyzo_num 正確提取數字 ID（純邏輯，不需 mock）"""
        assert scraper._extract_heyzo_num("HEYZO-0783") == "0783"
        assert scraper._extract_heyzo_num("heyzo-1031") == "1031"
        assert scraper._extract_heyzo_num("0783") == "0783"
        assert scraper._extract_heyzo_num("INVALID") is None

    def test_heyzo_table_tags(self, scraper):
        """_extract_table_data 從 HTML table 提取 series 和 tags"""
        result = scraper._extract_table_data(HEYZO_EN_HTML)
        assert result['series'] == "Premium Collection"
        assert result['tags'] == ["Cute", "Slender"]

    def test_heyzo_not_found(self, scraper):
        """404 回應時 search 回傳 None"""
        mock_resp = _make_mock_resp(status_code=404)

        with patch.object(scraper._session, 'get', return_value=mock_resp):
            video = scraper.search("HEYZO-9999")

        assert video is None

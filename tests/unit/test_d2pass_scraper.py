"""
test_d2pass_scraper.py - D2Pass 爬蟲單元測試（TASK-36-T9）

測試策略：
- 全 mock，不連網
- Mock scraper._session.get 回傳 JSON
- rate_limit 也 mock 掉（避免 sleep）
"""

import json
import pytest
import requests
from unittest.mock import patch, MagicMock


# ============================================================
# JSON Fixtures
# ============================================================

FULL_FIELDS_JSON = {
    "Status": True,
    "Title": "テストタイトル",
    "TitleEn": "Test Title",
    "ActressesJa": ["テスト女優"],
    "ActressesEn": ["Test Actress"],
    "ActressesList": {},
    "ThumbHigh": "https://www.1pondo.tv/assets/sample/120415_201/str.jpg",
    "MovieThumb": "",
    "UCNAME": ["フェラ", "ハメ撮り"],
    "UCNAMEEn": [],
    "Release": "2023-04-15",
    "AvgRating": "4.5",
    "Series": "テストシリーズ",
    "Duration": 3661,
    "SampleImages": [
        "https://www.1pondo.tv/assets/sample/120415_201/gallery/001.jpg",
        "https://www.1pondo.tv/assets/sample/120415_201/gallery/002.jpg",
    ],
}

NO_EXTRA_FIELDS_JSON = {
    "Status": True,
    "Title": "テストタイトル",
    "TitleEn": "Test Title",
    "ActressesJa": ["テスト女優"],
    "ActressesEn": [],
    "ActressesList": {},
    "ThumbHigh": "https://www.1pondo.tv/assets/sample/120415_201/str.jpg",
    "MovieThumb": "",
    "UCNAME": ["フェラ"],
    "UCNAMEEn": [],
    "Release": "2023-04-15",
    "AvgRating": None,
    # No Series, Duration, SampleImages keys
}

SERIES_EN_ONLY_JSON = {
    "Status": True,
    "Title": "テストタイトル",
    "TitleEn": "Test Title",
    "ActressesJa": ["テスト女優"],
    "ActressesEn": ["Test Actress"],
    "ActressesList": {},
    "ThumbHigh": "https://www.1pondo.tv/assets/sample/120415_201/str.jpg",
    "MovieThumb": "",
    "UCNAME": [],
    "UCNAMEEn": [],
    "Release": "2023-04-15",
    "AvgRating": None,
    "SeriesEn": "Test Series En",  # Only SeriesEn; no Series or SeriesJa
    "Duration": 3661,
    "SampleImages": [],
}

DURATION_STRING_JSON = {
    "Status": True,
    "Title": "テストタイトル",
    "ActressesJa": ["テスト女優"],
    "ActressesEn": [],
    "ActressesList": {},
    "ThumbHigh": "https://www.1pondo.tv/assets/sample/120415_201/str.jpg",
    "MovieThumb": "",
    "UCNAME": [],
    "UCNAMEEn": [],
    "Release": "2023-04-15",
    "AvgRating": None,
    "Series": "",
    "Duration": "3661",  # Duration as string
    "SampleImages": None,  # null in JSON
}


# ============================================================
# Helpers
# ============================================================

def make_json_response(data: dict, status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = data
    return resp


def run_search(scraper, json_data: dict, number: str = "120415_201"):
    scraper._session.get = MagicMock(return_value=make_json_response(json_data))
    return scraper.search(number)


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def scraper():
    from core.scrapers.d2pass import D2PassScraper
    with patch("core.scrapers.d2pass.rate_limit"):
        s = D2PassScraper()
        yield s


# ============================================================
# Tests
# ============================================================

class TestFullFields:
    """happy path: JSON has Series/Duration/SampleImages"""

    def test_series(self, scraper):
        video = run_search(scraper, FULL_FIELDS_JSON)
        assert video is not None
        assert video.series == "テストシリーズ"

    def test_duration_seconds_to_minutes(self, scraper):
        """3661 秒 → 61 分鐘（3661 // 60）"""
        video = run_search(scraper, FULL_FIELDS_JSON)
        assert video is not None
        assert video.duration == 61
        assert isinstance(video.duration, int)

    def test_sample_images(self, scraper):
        video = run_search(scraper, FULL_FIELDS_JSON)
        assert video is not None
        assert len(video.sample_images) == 2
        assert all(url.startswith("https://") for url in video.sample_images)


class TestNoExtraFields:
    """JSON 無 Series/Duration/SampleImages → 預設值"""

    def test_series_empty(self, scraper):
        video = run_search(scraper, NO_EXTRA_FIELDS_JSON)
        assert video is not None
        assert video.series == ""

    def test_duration_none(self, scraper):
        video = run_search(scraper, NO_EXTRA_FIELDS_JSON)
        assert video is not None
        assert video.duration is None

    def test_sample_images_empty(self, scraper):
        video = run_search(scraper, NO_EXTRA_FIELDS_JSON)
        assert video is not None
        assert video.sample_images == []


class TestDurationString:
    """Duration 為字串 → 正確轉換；SampleImages 為 null → []"""

    def test_duration_string_to_int(self, scraper):
        video = run_search(scraper, DURATION_STRING_JSON)
        assert video is not None
        assert video.duration == 61

    def test_sample_images_null_to_empty(self, scraper):
        video = run_search(scraper, DURATION_STRING_JSON)
        assert video is not None
        assert video.sample_images == []


class TestSeriesEnOnly:
    """JSON 只有 SeriesEn（無 Series/SeriesJa）→ series 正確讀取"""

    def test_series_en_fallback(self, scraper):
        video = run_search(scraper, SERIES_EN_ONLY_JSON)
        assert video is not None
        assert video.series == "Test Series En"


# caribbeancom gallery HTML fixture
CARIBBEANCOM_GALLERY_HTML = """\
<html><body>
<div class="gallery">
  <a href="/moviepages/070116-197/images/l/001.jpg"><img src="/moviepages/070116-197/images/s/001.jpg"></a>
  <a href="/moviepages/070116-197/images/l/002.jpg"><img src="/moviepages/070116-197/images/s/002.jpg"></a>
  <a href="/moviepages/070116-197/images/l/003.jpg"><img src="/moviepages/070116-197/images/s/003.jpg"></a>
</div>
</body></html>
"""

# JSON without SampleImages (triggers HTML gallery fetch for caribbeancom)
CARIBBEANCOM_JSON = {
    "Status": True,
    "Title": "テストタイトル",
    "ActressesJa": ["テスト女優"],
    "ActressesEn": [],
    "ActressesList": {},
    "ThumbHigh": "https://www.caribbeancom.com/moviepages/070116-197/images/l_l.jpg",
    "MovieThumb": "",
    "UCNAME": ["中出し"],
    "UCNAMEEn": [],
    "Release": "2016-07-01",
    "AvgRating": "4.0",
    "Duration": 3661,
}


class TestCaribbeancomGallery:
    """caribbeancom: JSON 無 SampleImages → HTML fallback 取 gallery"""

    def test_gallery_from_html(self, scraper):
        json_resp = make_json_response(CARIBBEANCOM_JSON)
        html_resp = MagicMock()
        html_resp.status_code = 200
        html_resp.text = CARIBBEANCOM_GALLERY_HTML

        # First call = JSON API, second call = HTML page
        scraper._session.get = MagicMock(side_effect=[json_resp, html_resp])
        video = scraper.search("070116-197")
        assert video is not None
        assert len(video.sample_images) == 3
        assert video.sample_images[0] == "https://www.caribbeancom.com/moviepages/070116-197/images/l/001.jpg"

    def test_gallery_html_404_returns_empty(self, scraper):
        json_resp = make_json_response(CARIBBEANCOM_JSON)
        html_resp = MagicMock()
        html_resp.status_code = 404

        scraper._session.get = MagicMock(side_effect=[json_resp, html_resp])
        video = scraper.search("070116-197")
        assert video is not None
        assert video.sample_images == []

    def test_1pondo_no_gallery_fetch(self, scraper):
        """1pondo は gallery fetch しない（会員限定）"""
        json_resp = make_json_response(NO_EXTRA_FIELDS_JSON)
        scraper._session.get = MagicMock(return_value=json_resp)
        video = scraper.search("031226_001")
        assert video is not None
        assert video.sample_images == []
        # Only 1 call (JSON API), no HTML fetch
        assert scraper._session.get.call_count == 1


# HTML fixture for JSON-404 fallback tests
CARIBBEANCOM_FULL_HTML = """\
<html><body>
<h1>洗練された大人のいやし亭 ～身も心もチンポも癒されてください～</h1>
<ul class="movie-info">
  <li><span>出演</span> <a href="/search_act/6706/1.html">上原亜衣</a></li>
  <li><span>再生時間</span> <span>01:01:01</span></li>
  <li><span>シリーズ</span> <a href="/series/960/index.html">洗練された大人のいやし亭</a></li>
  <li><span>タグ</span> <a>オリジナル動画</a> <a>美乳</a></li>
</ul>
<div class="gallery">
  <a href="/moviepages/070116-197/images/l/001.jpg"></a>
  <a href="/moviepages/070116-197/images/l/002.jpg"></a>
  <a href="/moviepages/070116-197/images/l/003.jpg"></a>
</div>
</body></html>
"""


def make_404_response() -> MagicMock:
    resp = MagicMock()
    resp.status_code = 404
    return resp


def make_html_response(html: str) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.text = html
    return resp


class TestCaribbeancomHtmlFallback:
    """caribbeancom: JSON 404 → HTML fallback 直接解析完整 Video"""

    def test_html_fallback_when_json_404(self, scraper):
        """JSON 404 → HTML fallback 成功 → Video 有 gallery + duration + series"""
        # caribbeancom JSON 404，HTML 成功
        json_404 = make_404_response()
        html_resp = make_html_response(CARIBBEANCOM_FULL_HTML)

        scraper._session.get = MagicMock(side_effect=[json_404, html_resp])
        video = scraper.search("070116-197")

        assert video is not None
        assert video.title == "洗練された大人のいやし亭 ～身も心もチンポも癒されてください～"
        assert video.duration == 61  # 01:01:01 → 1*60+1 = 61
        assert video.series == "洗練された大人のいやし亭"
        assert len(video.actresses) == 1
        assert video.actresses[0].name == "上原亜衣"
        assert "オリジナル動画" in video.tags
        assert "美乳" in video.tags
        assert len(video.sample_images) == 3
        assert video.sample_images[0] == "https://www.caribbeancom.com/moviepages/070116-197/images/l/001.jpg"
        assert video.cover_url == "https://www.caribbeancom.com/moviepages/070116-197/images/l_l.jpg"

    def test_html_fallback_extracts_pacopacomama_cover(self, scraper):
        html = """\
<html><body>
<h1>Test Title</h1>
<script>
var sample_image = 'https://www.pacopacomama.com/assets/sample/072220_001/'+'l_hd.jpg';
</script>
</body></html>
"""
        scraper._session.get = MagicMock(side_effect=[
            make_404_response(),
            make_404_response(),
            make_html_response(html),
        ])

        video = scraper.search("072220_001")

        assert video is not None
        assert video.cover_url == "https://www.pacopacomama.com/assets/sample/072220_001/l_hd.jpg"

    def test_html_fallback_filters_login_link_from_actresses(self, scraper):
        """出演區塊混入導覽登入連結時，不把ログイン當女優。"""
        html = """\
<html><body>
<h1>テストタイトル</h1>
<ul class="movie-info">
  <li><span>出演</span> <a href="/login/">ログイン</a> <a href="/signup/">無料会員登録</a> <a href="/search_act/6706/1.html">上原亜衣</a></li>
</ul>
</body></html>
"""
        scraper._session.get = MagicMock(side_effect=[make_404_response(), make_html_response(html)])

        video = scraper.search("070116-197")

        assert video is not None
        assert [a.name for a in video.actresses] == ["上原亜衣"]

    def test_html_fallback_extracts_nested_actor_name(self, scraper):
        """Caribbeancom 演員連結文字包在 itemprop=name span 時也能解析。"""
        html = """\
<html><body>
<h1>テストタイトル</h1>
<ul class="movie-info">
  <li class="movie-spec">
    <span class="spec-title">出演</span>
    <span class="spec-content">
      <a class="spec__tag" itemprop="actor" href="/search_act/6096/1.html"><span itemprop="name">小早川怜子</span></a>
    </span>
  </li>
</ul>
</body></html>
"""
        scraper._session.get = MagicMock(side_effect=[make_404_response(), make_html_response(html)])

        video = scraper.search("102318-778")

        assert video is not None
        assert [a.name for a in video.actresses] == ["小早川怜子"]

    def test_html_fallback_extracts_unlinked_actor_span(self, scraper):
        """Caribbeancom 演員是無連結 spec__tag span 時也能解析。"""
        html = """\
<html><body>
<h1>テストタイトル</h1>
<ul class="movie-info">
  <li class="movie-spec">
    <span class="spec-title">出演</span>
    <span class="spec-content">
      <span class="spec__tag" itemprop="actor" itemscope itemtype="http://schema.org/Person">小早川怜子</span>
    </span>
  </li>
</ul>
</body></html>
"""
        scraper._session.get = MagicMock(
            side_effect=[make_404_response(), make_404_response(), make_html_response(html)]
        )

        video = scraper.search("072220_001")

        assert video is not None
        assert [a.name for a in video.actresses] == ["小早川怜子"]

    def test_html_fallback_404(self, scraper):
        """JSON 404 + HTML 也 404 → 返回 None（continue 到下個 site）"""
        json_404 = make_404_response()
        html_404 = make_404_response()

        # caribbeancom JSON 404 → HTML 404 → 1pondo JSON 404 → 10musume JSON 404
        # 全部 None → search returns None
        scraper._session.get = MagicMock(side_effect=[json_404, html_404, json_404, json_404])
        video = scraper.search("070116-197")
        assert video is None

    def test_html_fallback_dedup_gallery(self, scraper):
        """Gallery 圖片去重（相同編號不重複）"""
        html_with_dups = """\
<html><body>
<h1>テストタイトル</h1>
<ul class="movie-info">
  <li><span>再生時間</span> <span>00:30:00</span></li>
</ul>
<div class="gallery">
  <a href="/moviepages/070116-197/images/l/001.jpg"></a>
  <a href="/moviepages/070116-197/images/s/001.jpg"></a>
  <a href="/moviepages/070116-197/images/l/001.jpg"></a>
  <a href="/moviepages/070116-197/images/l/002.jpg"></a>
</div>
</body></html>
"""
        json_404 = make_404_response()
        html_resp = make_html_response(html_with_dups)

        scraper._session.get = MagicMock(side_effect=[json_404, html_resp])
        video = scraper.search("070116-197")

        assert video is not None
        # images/l/001.jpg appears twice, but should be deduped
        assert len(video.sample_images) == 2
        assert video.sample_images[0] == "https://www.caribbeancom.com/moviepages/070116-197/images/l/001.jpg"
        assert video.sample_images[1] == "https://www.caribbeancom.com/moviepages/070116-197/images/l/002.jpg"

    def test_html_fallback_no_title_returns_none(self, scraper):
        """HTML 無 <h1> → fallback 返回 None"""
        html_no_title = "<html><body><p>no title here</p></body></html>"
        json_404 = make_404_response()
        html_resp = make_html_response(html_no_title)

        scraper._session.get = MagicMock(side_effect=[json_404, html_resp, json_404, json_404])
        video = scraper.search("070116-197")
        assert video is None


# ============================================================
# Mock Data (from test_new_scrapers.py)
# ============================================================

SAMPLE_1PONDO_JSON = {
    "Status": True,
    "Title": "目覚ましフェラ",
    "TitleEn": "Morning Blowjob",
    "ActressesJa": ["一ノ瀬アメリ"],
    "Release": "2014-12-04",
    "ThumbHigh": "https://www.1pondo.tv/assets/sample/120415_201/str.jpg",
    "UCNAME": ["美尻", "69"],
    "AvgRating": 4.5,
}


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
# Tests merged from integration/test_new_scrapers.py TestD2PassScraper
# ============================================================

class TestD2PassIntegration:
    """D2Pass scraper tests (merged from test_new_scrapers.py)"""

    @pytest.fixture
    def scraper(self):
        from core.scrapers.d2pass import D2PassScraper
        return D2PassScraper()

    def test_d2pass_1pondo_success(self, scraper):
        """1Pondo 番號搜尋成功"""
        mock_resp = _make_mock_resp(status_code=200, json_data=SAMPLE_1PONDO_JSON)

        with patch.object(scraper._session, 'get', return_value=mock_resp):
            with patch('core.scrapers.d2pass.rate_limit'):
                video = scraper.search("120415_201")

        assert video is not None
        assert video.number == "120415_201"
        assert video.title == "目覚ましフェラ"
        assert video.source == "d2pass"
        assert len(video.actresses) == 1
        assert video.actresses[0].name == "一ノ瀬アメリ"
        assert video.date == "2014-12-04"
        assert "美尻" in video.tags

    def test_d2pass_caribbeancom_success(self, scraper):
        """Caribbeancom 番號（hyphen 格式）搜尋成功"""
        carib_json = {
            "Status": True,
            "Title": "キャットウォーク ...",
            "ActressesJa": ["鈴木さとみ"],
            "Release": "2009-07-14",
            "UCNAME": [],
        }
        mock_resp = _make_mock_resp(status_code=200, json_data=carib_json)

        with patch.object(scraper._session, 'get', return_value=mock_resp) as mock_get:
            with patch('core.scrapers.d2pass.rate_limit'):
                video = scraper.search("071409-113")

        assert video is not None
        assert video.source == "d2pass"
        assert video.title == "キャットウォーク ..."
        assert len(video.actresses) == 1
        assert video.actresses[0].name == "鈴木さとみ"
        # 驗證第一次呼叫的 URL 包含 caribbeancom
        first_call_url = mock_get.call_args_list[0][0][0]
        assert "caribbeancom" in first_call_url

    def test_d2pass_10musume_success(self, scraper):
        """10musume 番號（底線 2-digit suffix）搜尋成功"""
        musume_json = {
            "Status": True,
            "Title": "素人AV面接 ...",
            "ActressesJa": ["堀川麻紀"],
            "Release": "2012-09-28",
            "UCNAME": [],
        }
        mock_resp = _make_mock_resp(status_code=200, json_data=musume_json)

        with patch.object(scraper._session, 'get', return_value=mock_resp) as mock_get:
            with patch('core.scrapers.d2pass.rate_limit'):
                video = scraper.search("082912_01")

        assert video is not None
        assert video.source == "d2pass"
        assert video.title == "素人AV面接 ..."
        assert len(video.actresses) == 1
        assert video.actresses[0].name == "堀川麻紀"
        # 驗證第一次呼叫的 URL 包含 10musume
        first_call_url = mock_get.call_args_list[0][0][0]
        assert "10musume" in first_call_url

    def test_d2pass_site_detection(self, scraper):
        """_detect_site_order 根據番號格式回傳正確順序（純邏輯，不需 mock）"""
        assert scraper._detect_site_order("071409-113")[0] == "caribbeancom"
        assert scraper._detect_site_order("120415_201")[0] == "1pondo"
        assert scraper._detect_site_order("082912_01")[0] == "10musume"

    def test_d2pass_not_found(self, scraper):
        """全部 site 皆 404 時 search 回傳 None"""
        mock_resp = _make_mock_resp(status_code=404)

        with patch.object(scraper._session, 'get', return_value=mock_resp):
            video = scraper.search("999999_999")

        assert video is None

    def test_d2pass_timeout(self, scraper):
        """_session.get raise Timeout → _fetch_json catches it → search returns None"""
        with patch.object(scraper._session, 'get', side_effect=requests.Timeout):
            video = scraper.search("120415_201")

        assert video is None

    def test_d2pass_caribbeancom_cover_fallback(self, scraper):
        """Caribbeancom ThumbHigh=null 時，自動構造封面 URL"""
        carib_json = {
            "Status": True,
            "Title": "テスト動画",
            "ActressesJa": ["テスト"],
            "Release": "2024-02-09",
            "UCNAME": [],
        }
        mock_resp = _make_mock_resp(status_code=200, json_data=carib_json)

        with patch.object(scraper._session, 'get', return_value=mock_resp):
            with patch('core.scrapers.d2pass.rate_limit'):
                video = scraper.search("020924-001")

        assert video is not None
        assert video.title == "テスト動画"
        assert len(video.actresses) == 1
        assert video.actresses[0].name == "テスト"
        assert video.cover_url == "https://www.caribbeancom.com/moviepages/020924-001/images/l_l.jpg"

    def test_d2pass_1pondo_cover_fallback(self, scraper):
        """1Pondo ThumbHigh=null 時，自動構造封面 URL"""
        pondo_json = {
            "Status": True,
            "Title": "テスト動画",
            "ActressesJa": ["テスト"],
            "Release": "2024-04-23",
            "UCNAME": [],
        }
        mock_resp = _make_mock_resp(status_code=200, json_data=pondo_json)

        with patch.object(scraper._session, 'get', return_value=mock_resp):
            with patch('core.scrapers.d2pass.rate_limit'):
                video = scraper.search("042324_001")

        assert video is not None
        assert video.title == "テスト動画"
        assert len(video.actresses) == 1
        assert video.actresses[0].name == "テスト"
        assert video.cover_url == "https://www.1pondo.tv/assets/sample/042324_001/str.jpg"

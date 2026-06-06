from core.scrapers.javdb import JavDBScraper, scrape_javdb_actress_photo
from core.scrapers.models import ScraperConfig


class _FakeResponse:
    status_code = 200
    text = "<html></html>"


def _search_html(uid: str = "BLACKED-161226") -> str:
    return f"""
    <html><body>
      <div class="movie-list">
        <div class="item">
          <a href="/v/test-detail">
            <div class="video-title"><strong>{uid}</strong></div>
          </a>
        </div>
      </div>
    </body></html>
    """


def _search_html_many(*uids: str) -> str:
    items = "\n".join(
        f"""
        <div class="item">
          <a href="/v/{uid.lower()}">
            <div class="video-title"><strong>{uid}</strong></div>
          </a>
        </div>
        """
        for uid in uids
    )
    return f'<html><body><div class="movie-list">{items}</div></body></html>'


def _detail_html(uid: str = "BLACKED-161226") -> str:
    return f"""
    <html><body>
      <div class="video-detail"><h2>{uid} Lena Gets Her Groove Back</h2></div>
      <div class="video-cover"><img src="https://c0.jdbstatic.com/covers/test.jpg"></div>
      <div class="panel-block"><strong>日期:</strong><span class="value">2016-12-26</span></div>
      <div class="panel-block"><strong>片商:</strong><span class="value">Blacked</span></div>
      <div class="panel-block">
        <strong>演員:</strong>
        <span class="value">
          <a>Lena Paul</a><span class="female"></span>
          <a>Example Male</a><span class="male"></span>
        </span>
      </div>
      <div class="panel-block">
        <strong>類別:</strong>
        <span class="value"><a>欧美</a><a>高清</a></span>
      </div>
    </body></html>
    """


def _actor_search_html(name: str, image_url: str = "") -> str:
    img = f'<img src="{image_url}" alt="{name}">' if image_url else ""
    return f"""
    <html><body>
      <div class="actors">
        <div class="actor-box">
          <a href="/actors/test-actor" title="{name}">
            {img}
            <span>{name}</span>
          </a>
        </div>
      </div>
    </body></html>
    """


def _actor_detail_html(image_url: str) -> str:
    return f"""
    <html><body>
      <div class="avatar-box">
        <div class="photo-frame"><img src="{image_url}"></div>
      </div>
    </body></html>
    """


def test_javdb_title_query_uses_first_search_result(monkeypatch):
    scraper = JavDBScraper()

    def fake_get_html(url: str):
        if "/search" in url:
            return _search_html()
        return _detail_html()

    monkeypatch.setattr(scraper, "_get_html", fake_get_html)

    video = scraper.search("Blacked.16.12.26.Lena.Paul.And.Angela.White")

    assert video is not None
    assert video.number == "BLACKED-161226"
    assert video.title == "Lena Gets Her Groove Back"
    assert [a.name for a in video.actresses] == ["Lena Paul"]
    assert video.maker == "Blacked"
    assert video.tags == ["欧美", "高清"]


def test_javdb_single_letter_number_matches_compact_uid(monkeypatch):
    scraper = JavDBScraper()

    def fake_get_html(url: str):
        if "/search" in url:
            return _search_html("N0808")
        return _detail_html("N0808")

    monkeypatch.setattr(scraper, "_get_html", fake_get_html)

    video = scraper.search("N-0808")

    assert video is not None
    assert video.number == "N-0808"
    assert video.title == "Lena Gets Her Groove Back"


def test_javdb_title_query_rejects_unrelated_western_uid(monkeypatch):
    scraper = JavDBScraper()
    detail_calls = []

    def fake_get_html(url: str):
        if "/search" in url:
            return _search_html("BLACKED-190912")
        detail_calls.append(url)
        return _detail_html("BLACKED-190912")

    monkeypatch.setattr(scraper, "_get_html", fake_get_html)

    video = scraper.search("Blacked.16.12.26.Lena.Paul.And.Angela.White")

    assert video is None
    assert detail_calls == []


def test_javdb_title_query_treats_multiple_matching_uids_as_ambiguous(monkeypatch):
    scraper = JavDBScraper()
    detail_calls = []

    def fake_get_html(url: str):
        if "/search" in url:
            return _search_html_many("BLACKED-161226", "BLACKED-161227")
        detail_calls.append(url)
        return _detail_html("BLACKED-161226")

    monkeypatch.setattr(scraper, "_get_html", fake_get_html)

    video = scraper.search("Blacked.16.12.26.Lena.Paul.Blacked.16.12.27.Lena.Paul")

    assert video is None
    assert detail_calls == []


def test_javdb_uses_proxy_from_scraper_config(monkeypatch):
    captured = {}

    def fake_get(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return _FakeResponse()

    monkeypatch.setattr("core.scrapers.javdb.CURL_CFFI_AVAILABLE", True)
    monkeypatch.setattr("core.scrapers.javdb.curl_requests.get", fake_get)

    scraper = JavDBScraper(ScraperConfig(proxy_url="http://proxy.test:8080"))
    html = scraper._get_html("https://javdb.com/search?q=SONE-205&f=all")

    assert html == "<html></html>"
    assert captured["proxies"] == {
        "http": "http://proxy.test:8080",
        "https": "http://proxy.test:8080",
    }


def test_javdb_actress_photo_from_actor_search_card(monkeypatch):
    scraper = JavDBScraper()
    name = "\u4e09\u4e0a\u60a0\u4e9c"
    image_url = "https://c0.jdbstatic.com/actors/mikami-yua.jpg"

    monkeypatch.setattr(scraper, "_get_html", lambda url: _actor_search_html(name, image_url))

    assert scraper.search_actress_photo(name) == image_url


def test_javdb_actress_photo_falls_back_to_actor_detail(monkeypatch):
    scraper = JavDBScraper()
    name = "\u4e09\u4e0a\u60a0\u4e9c"
    image_url = "https://c1.jdbstatic.com/actors/mikami-yua.jpg"
    requested_urls = []

    def fake_get_html(url: str):
        requested_urls.append(url)
        if "/search" in url:
            return _actor_search_html(name)
        return _actor_detail_html(image_url)

    monkeypatch.setattr(scraper, "_get_html", fake_get_html)

    assert scraper.search_actress_photo(name) == image_url
    assert requested_urls == [
        "https://javdb.com/search?f=actor&q=%E4%B8%89%E4%B8%8A%E6%82%A0%E4%BA%9C",
        "https://javdb.com/actors/test-actor",
    ]


def test_javdb_actress_photo_ignores_unmatched_actor(monkeypatch):
    scraper = JavDBScraper()
    image_url = "https://c0.jdbstatic.com/actors/other.jpg"
    monkeypatch.setattr(
        scraper,
        "_get_html",
        lambda url: _actor_search_html("\u4ed6\u306e\u5973\u512a", image_url),
    )

    assert scraper.search_actress_photo("\u4e09\u4e0a\u60a0\u4e9c") is None


def test_scrape_javdb_actress_photo_uses_configured_proxy(monkeypatch):
    captured = {}

    monkeypatch.setattr(
        "core.config.load_config",
        lambda: {"search": {"proxy_url": "http://proxy.test:8080"}},
    )

    def fake_search(self, name):
        captured["proxy_url"] = self.config.proxy_url
        captured["name"] = name
        return "https://c0.jdbstatic.com/actors/test.jpg"

    monkeypatch.setattr(JavDBScraper, "search_actress_photo", fake_search)

    result = scrape_javdb_actress_photo("Alice")

    assert result == "https://c0.jdbstatic.com/actors/test.jpg"
    assert captured == {
        "proxy_url": "http://proxy.test:8080",
        "name": "Alice",
    }


def test_scrape_javdb_actress_photo_direct_proxy_means_no_proxy(monkeypatch):
    captured = {}
    monkeypatch.setattr("core.config.load_config", lambda: {"search": {"proxy_url": "direct"}})

    def fake_search(self, name):
        captured["proxy_url"] = self.config.proxy_url
        return "https://c0.jdbstatic.com/actors/test.jpg"

    monkeypatch.setattr(JavDBScraper, "search_actress_photo", fake_search)

    assert scrape_javdb_actress_photo("Alice") == "https://c0.jdbstatic.com/actors/test.jpg"
    assert captured["proxy_url"] == ""

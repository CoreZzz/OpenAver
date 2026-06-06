from types import SimpleNamespace

from core.scrapers.missav import MissAVScraper
from core.scrapers.models import ScraperConfig


DETAIL_HTML = """
<html>
  <head>
    <meta property="og:image" content="https://missav.ai/cover/snos143.jpg">
    <meta property="og:title" content="SNOS-143 MissAV Fallback Title - MissAV">
  </head>
  <body>
    <h1>SNOS-143 MissAV Fallback Title</h1>
    <div class="info">
      <span>发行日期:</span><span>2025-03-20</span>
      <span>番号:</span><span>SNOS-143</span>
      <span>标题:</span><span>SNOS-143 Original Detail Title</span>
      <span>女优:</span><span>渚あいり, 橋本ありな</span>
      <span>类型:</span><span>高清, 单体作品</span>
      <span>发行商:</span><span>S1 NO.1 STYLE</span>
      <span>导演:</span><span>Test Director</span>
      <span>标籤:</span><span>S1 Label</span>
    </div>
    <img src="https://missav.ai/sample/snos143-1.jpg">
  </body>
</html>
"""


def test_missav_direct_detail_parses_expected_fields(monkeypatch):
    monkeypatch.setattr("core.scrapers.missav.rate_limit", lambda *a, **kw: None)

    requested = []

    def fake_get_html(url):
        requested.append(url)
        return DETAIL_HTML

    scraper = MissAVScraper()
    monkeypatch.setattr(scraper, "_get_html", fake_get_html)

    video = scraper.search("SNOS-143")

    assert video is not None
    assert requested == ["https://missav.ai/cn/snos-143"]
    assert video.source == "missav"
    assert video.number == "SNOS-143"
    assert video.title == "Original Detail Title"
    assert [a.name for a in video.actresses] == ["渚あいり", "橋本ありな"]
    assert video.date == "2025-03-20"
    assert video.maker == "S1 NO.1 STYLE"
    assert video.director == "Test Director"
    assert video.label == "S1 Label"
    assert video.tags == ["高清", "单体作品"]
    assert video.cover_url == "https://missav.ai/cover/snos143.jpg"
    assert video.sample_images == ["https://missav.ai/sample/snos143-1.jpg"]
    assert video.detail_url == "https://missav.ai/cn/snos-143"


def test_missav_search_falls_back_to_search_candidates(monkeypatch):
    monkeypatch.setattr("core.scrapers.missav.rate_limit", lambda *a, **kw: None)
    search_html = """
    <html><body>
      <a href="/cn/sone-103">SONE-103 Some result</a>
    </body></html>
    """
    requested = []
    detail_calls = 0

    def fake_get_html(url):
        nonlocal detail_calls
        requested.append(url)
        if url == "https://missav.ai/cn/sone-103":
            detail_calls += 1
            if detail_calls == 1:
                return None
            return DETAIL_HTML.replace("SNOS-143", "SONE-103")
        if "/search/" in url:
            return search_html
        return None

    scraper = MissAVScraper()
    monkeypatch.setattr(scraper, "_get_html", fake_get_html)

    video = scraper.search("SONE-103")

    assert video is not None
    assert video.number == "SONE-103"
    assert requested[0] == "https://missav.ai/cn/sone-103"
    assert "https://missav.ai/cn/search/SONE-103" in requested
    assert requested[-1] == "https://missav.ai/cn/sone-103"


def test_missav_get_html_uses_proxy_with_curl_cffi(monkeypatch):
    import core.scrapers.missav as missav_mod

    captured = {}

    def fake_get(url, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        return SimpleNamespace(status_code=200, text="<html>ok</html>")

    monkeypatch.setattr(missav_mod, "CURL_CFFI_AVAILABLE", True)
    monkeypatch.setattr(missav_mod, "curl_requests", SimpleNamespace(get=fake_get))

    scraper = MissAVScraper(ScraperConfig(proxy_url="http://127.0.0.1:8080"))
    html = scraper._get_html("https://missav.ai/cn/snos-143")

    assert html == "<html>ok</html>"
    assert captured["url"] == "https://missav.ai/cn/snos-143"
    assert captured["kwargs"]["impersonate"] == "chrome120"
    assert captured["kwargs"]["proxies"] == {
        "http": "http://127.0.0.1:8080",
        "https": "http://127.0.0.1:8080",
    }


def test_missav_get_html_rejects_cloudflare_challenge(monkeypatch):
    import core.scrapers.missav as missav_mod

    def fake_get(url, **kwargs):
        return SimpleNamespace(status_code=200, text="<title>Just a moment...</title>")

    monkeypatch.setattr(missav_mod, "CURL_CFFI_AVAILABLE", True)
    monkeypatch.setattr(missav_mod, "curl_requests", SimpleNamespace(get=fake_get))

    scraper = MissAVScraper()

    assert scraper._get_html("https://missav.ai/cn/snos-143") is None

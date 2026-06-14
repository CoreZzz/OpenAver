from core.scrapers.tokyohot import TokyoHotScraper


class FakeResponse:
    def __init__(self, status_code: int, text: str):
        self.status_code = status_code
        self.text = text
        self.encoding = "utf-8"


class FakeSession:
    def __init__(self, responses: dict[str, FakeResponse]):
        self.responses = responses
        self.calls: list[str] = []
        self.headers = {}

    def get(self, url: str, timeout: int):
        self.calls.append(url)
        return self.responses.get(url, FakeResponse(404, ""))


SEARCH_HTML = """
<html><body>
  <a href="/product/20950/">
    <img src="https://my.cdn.tokyo-hot.com/media/20950/list_image/n0783/220x124_default.jpg" alt="n0783">
    An Ideal Meat Urinal (Product ID: n0783)
  </a>
</body></html>
"""


DETAIL_HTML = """
<html><head>
  <meta property="og:title" content="An Ideal Meat Urinal | Tokyo-Hot">
</head><body>
  <div class="pagetitle"><h2>An Ideal Meat Urinal</h2></div>
  <div class="movie">
    <video poster="https://my.cdn.tokyo-hot.com/media/20950/list_image/n0783/820x462_default.jpg"></video>
    <ul class="control">
      <li class="package"><a href="https://my.cdn.tokyo-hot.com/media/20950/jacket/n0783.jpg">Jacket</a></li>
    </ul>
  </div>
  <div id="main"><div class="contents">
    <h2>An Ideal Meat Urinal</h2>
    <div class="sentence">Plot text.</div>
    <div class="infowrapper"><dl class="info">
      <dt>Model</dt><dd><a href="/cast/5948/">Ren Azumi</a></dd>
      <dt>Play</dt><dd><a href="/product/?type=play&amp;filter=SM">SM</a></dd>
      <dt>Tags</dt><dd><a href="/product/?type=tag&amp;filter=Tag One">Tag One</a></dd>
      <dt>Theme</dt><dd><a href="/product/?type=genre&amp;filter=Tokyo Hot Exclusive">Tokyo Hot Exclusive</a></dd>
      <dt>Label</dt><dd><a href="/product/?vendor=Tokyo-Hot">Tokyo-Hot</a></dd>
      <dt>Release Date</dt><dd>2012/09/25</dd>
      <dt>Duration</dt><dd>01:58:50</dd>
      <dt>Product ID</dt><dd>n0783</dd>
    </dl></div>
    <div class="scap">
      <a href="https://my.cdn.tokyo-hot.com/media/20950/scap/001/640x480_wlimited.jpg"></a>
    </div>
  </div></div>
</body></html>
"""


JAPANESE_DETAIL_HTML = """
<html><body>
  <div class="pagetitle"><h2>あずみ恋東熱見納め3穴カン</h2></div>
  <div class="movie">
    <ul class="control">
      <li class="package"><a href="https://my.cdn.tokyo-hot.com/media/20950/jacket/n0783.jpg">Jacket</a></li>
    </ul>
  </div>
  <div id="main"><div class="contents">
    <div class="sentence">紹介文。</div>
    <dl class="info">
      <dt>出演者</dt><dd><a href="/cast/5948/">Ren Azumi</a></dd>
      <dt>プレイ内容</dt><dd><a href="/product/?type=play&amp;filter=SM">SM</a></dd>
      <dt>タグ</dt><dd><a href="/product/?type=tag&amp;filter=Tag One">Tag One</a></dd>
      <dt>シリーズ</dt><dd><a href="/product/?type=genre&amp;filter=Tokyo Hot Exclusive">Tokyo Hot Exclusive</a></dd>
      <dt>レーベル</dt><dd><a href="/product/?vendor=Tokyo-Hot">Tokyo-Hot</a></dd>
      <dt>配信開始日</dt><dd>2012/09/25</dd>
      <dt>収録時間</dt><dd>01:58:50</dd>
      <dt>作品番号</dt><dd>n0783</dd>
    </dl>
  </div></div>
</body></html>
"""


def test_tokyohot_search_short_id_parses_detail(monkeypatch):
    monkeypatch.setattr("core.scrapers.tokyohot.rate_limit", lambda *_args, **_kwargs: None)
    scraper = TokyoHotScraper()
    fake = FakeSession({
        "https://my.tokyo-hot.com/product/n0783/": FakeResponse(404, ""),
        "https://my.tokyo-hot.com/product/?q=n0783": FakeResponse(200, SEARCH_HTML),
        "https://my.tokyo-hot.com/product/20950/": FakeResponse(200, DETAIL_HTML),
    })
    scraper._session = fake

    video = scraper.search("N-0783")

    assert video is not None
    assert video.number == "N-0783"
    assert video.title == "An Ideal Meat Urinal"
    assert [a.name for a in video.actresses] == ["Ren Azumi"]
    assert video.date == "2012-09-25"
    assert video.maker == "Tokyo-Hot"
    assert video.label == "Tokyo-Hot"
    assert video.duration == 118
    assert video.cover_url == "https://my.cdn.tokyo-hot.com/media/20950/jacket/n0783.jpg"
    assert video.tags == ["SM", "Tag One", "Tokyo Hot Exclusive"]
    assert video.sample_images == [
        "https://my.cdn.tokyo-hot.com/media/20950/scap/001/640x480_wlimited.jpg"
    ]
    assert video.summary == "Plot text."
    assert fake.calls == [
        "https://my.tokyo-hot.com/product/n0783/",
        "https://my.tokyo-hot.com/product/?q=n0783",
        "https://my.tokyo-hot.com/product/20950/",
    ]


def test_tokyohot_search_parses_japanese_detail_aliases(monkeypatch):
    monkeypatch.setattr("core.scrapers.tokyohot.rate_limit", lambda *_args, **_kwargs: None)
    scraper = TokyoHotScraper()
    fake = FakeSession({
        "https://my.tokyo-hot.com/product/n0783/": FakeResponse(404, ""),
        "https://my.tokyo-hot.com/product/?q=n0783": FakeResponse(200, SEARCH_HTML),
        "https://my.tokyo-hot.com/product/20950/": FakeResponse(200, JAPANESE_DETAIL_HTML),
    })
    scraper._session = fake

    video = scraper.search("n0783")

    assert video is not None
    assert video.title == "あずみ恋東熱見納め3穴カン"
    assert [a.name for a in video.actresses] == ["Ren Azumi"]
    assert video.date == "2012-09-25"
    assert video.duration == 118
    assert video.label == "Tokyo-Hot"
    assert video.tags == ["SM", "Tag One", "Tokyo Hot Exclusive"]
    assert video.summary == "紹介文。"


def test_tokyohot_rejects_non_short_id():
    scraper = TokyoHotScraper()
    assert scraper.search("FC2-PPV-1234567") is None

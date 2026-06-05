"""Search query alias retry behavior."""

from core.filename_identity import parse_media_identity
from core import scraper


class FakeScraper:
    def __init__(self, hit_query: str):
        self.hit_query = hit_query
        self.calls: list[str] = []

    def search(self, query: str):
        self.calls.append(query)
        if query == self.hit_query:
            return {"number": query}
        return None


def test_search_scraper_with_source_queries_retries_until_hit():
    identity = parse_media_identity("FC2PPV-1234567.mp4")
    fake = FakeScraper("FC2-1234567")

    result = scraper._search_scraper_with_queries(
        fake,
        "javdb",
        "FC2-PPV-1234567",
        identity,
        try_all_aliases=True,
        max_queries=3,
    )

    assert result == {"number": "FC2-1234567"}
    assert fake.calls == [
        "FC2-PPV-1234567",
        "FC2-1234567",
    ]


def test_search_scraper_respects_try_all_aliases_false():
    identity = parse_media_identity("FC2PPV-1234567.mp4")
    fake = FakeScraper("FC2-1234567")

    result = scraper._search_scraper_with_queries(
        fake,
        "javdb",
        "FC2-PPV-1234567",
        identity,
        try_all_aliases=False,
        max_queries=3,
    )

    assert result is None
    assert fake.calls == ["FC2-PPV-1234567"]

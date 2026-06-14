"""Search query alias retry behavior."""

from core.filename_identity import parse_media_identity
from core import scraper


class FakeScraper:
    def __init__(self, hit_query: str, result: dict | None = None):
        self.hit_query = hit_query
        self.result = result
        self.calls: list[str] = []

    def search(self, query: str):
        self.calls.append(query)
        if query == self.hit_query:
            return self.result or {"number": query}
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


def test_search_scraper_d2pass_keeps_date_separator_exact():
    identity = parse_media_identity("102318_778.mp4")
    fake = FakeScraper("102318-778")

    result = scraper._search_scraper_with_queries(
        fake,
        "d2pass",
        "102318_778",
        identity,
        try_all_aliases=True,
        max_queries=3,
    )

    assert result is None
    assert fake.calls == ["102318_778"]
    assert identity.canonical_number == "102318_778"


def test_search_scraper_d2pass_single_letter_uses_compact_site_id_first():
    identity = parse_media_identity("n0783.mp4")
    fake = FakeScraper("n0783")

    result = scraper._search_scraper_with_queries(
        fake,
        "d2pass",
        "N-0783",
        identity,
        try_all_aliases=True,
        max_queries=3,
    )

    assert result == {"number": "n0783"}
    assert fake.calls == ["n0783"]
    assert identity.canonical_number == "N-0783"


def test_search_scraper_javdb_retries_date_separator_alias_without_changing_canonical():
    identity = parse_media_identity("102318_778.mp4")
    fake = FakeScraper("102318-778")

    result = scraper._search_scraper_with_queries(
        fake,
        "javdb",
        "102318_778",
        identity,
        try_all_aliases=True,
        max_queries=3,
    )

    assert result == {"number": "102318-778"}
    assert fake.calls == ["102318_778", "102318-778"]
    assert identity.canonical_number == "102318_778"


def test_search_scraper_rejects_number_only_in_returned_title():
    identity = parse_media_identity("PRED-002")
    fake = FakeScraper(
        "PRED-002",
        {
            "number": "RED-155",
            "title": "PRED-002 appears in this title but is not the work id",
        },
    )

    result = scraper._search_scraper_with_queries(
        fake,
        "javdb",
        "PRED-002",
        identity,
        try_all_aliases=True,
        max_queries=3,
    )

    assert result is None
    assert fake.calls == ["PRED-002"]


def test_search_scraper_accepts_matching_returned_number():
    identity = parse_media_identity("PRED-002")
    fake = FakeScraper(
        "PRED-002",
        {
            "number": "PRED-002",
            "title": "Returned title",
        },
    )

    result = scraper._search_scraper_with_queries(
        fake,
        "javdb",
        "PRED-002",
        identity,
        try_all_aliases=True,
        max_queries=3,
    )

    assert result == {"number": "PRED-002", "title": "Returned title"}
    assert fake.calls == ["PRED-002"]


def test_search_scraper_rejects_hyphenated_red_for_compact_red():
    identity = parse_media_identity("RED155")
    fake = FakeScraper(
        "RED155",
        {
            "number": "RED-155",
            "title": "Wrong hyphenated RED work",
        },
    )

    result = scraper._search_scraper_with_queries(
        fake,
        "javdb",
        "RED155",
        identity,
        try_all_aliases=True,
        max_queries=3,
    )

    assert result is None
    assert fake.calls == ["RED155"]


def test_search_scraper_rejects_known_prefix_maker_mismatch():
    identity = parse_media_identity("PRED-002")
    fake = FakeScraper(
        "PRED-002",
        {
            "number": "PRED-002",
            "title": "Returned title",
            "maker": "アルファーインターナショナル",
        },
    )

    result = scraper._search_scraper_with_queries(
        fake,
        "jav321",
        "PRED-002",
        identity,
        try_all_aliases=True,
        max_queries=3,
    )

    assert result is None
    assert fake.calls == ["PRED-002"]


def test_search_scraper_accepts_known_prefix_compatible_maker():
    identity = parse_media_identity("PRED-002")
    fake = FakeScraper(
        "PRED-002",
        {
            "number": "PRED-002",
            "title": "Returned title",
            "maker": "プレミアム",
        },
    )

    result = scraper._search_scraper_with_queries(
        fake,
        "javdb",
        "PRED-002",
        identity,
        try_all_aliases=True,
        max_queries=3,
    )

    assert result == {"number": "PRED-002", "title": "Returned title", "maker": "プレミアム"}
    assert fake.calls == ["PRED-002"]


def test_javdb_title_query_uses_raw_filename_when_no_number():
    identity = parse_media_identity("Blacked.16.12.26.Lena.Paul.mp4")
    fake = FakeScraper("Blacked.16.12.26.Lena.Paul.mp4")

    result = scraper._search_scraper_with_queries(
        fake,
        "javdb",
        "Blacked.16.12.26.Lena.Paul.mp4",
        "Blacked.16.12.26.Lena.Paul.mp4",
        identity,
        try_all_aliases=True,
        max_queries=3,
    )

    assert result == {"number": "Blacked.16.12.26.Lena.Paul.mp4"}
    assert fake.calls[0] == "Blacked.16.12.26.Lena.Paul.mp4"

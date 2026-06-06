from core.scrapers.theporndb import ThePornDBScraper


class FakeResponse:
    def __init__(self, data, status_code=200):
        self._data = data
        self.status_code = status_code

    def json(self):
        return self._data


class FakeSession:
    def __init__(self, responses):
        self.headers = {}
        self.responses = responses
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append({"url": url, "params": params or {}, "timeout": timeout})
        for key, response in self.responses.items():
            if url.endswith(key):
                if isinstance(response, FakeResponse):
                    return response
                return FakeResponse(response)
        return FakeResponse({}, status_code=404)


def test_theporndb_sets_bearer_auth_header():
    scraper = ThePornDBScraper(api_token="secret-token")

    assert scraper._session.headers["Authorization"] == "Bearer secret-token"


def test_theporndb_no_token_skips_requests():
    scraper = ThePornDBScraper()
    fake = FakeSession({"/scenes": {"data": [{"id": "scene-1"}]}})
    scraper._session = fake

    assert scraper.search("Some Title") is None
    assert fake.calls == []


def test_theporndb_scene_mapping_filters_male_and_keeps_female_aliases():
    list_item = {
        "id": "scene-1",
        "title": "Wicked Scene",
        "date": "2024-02-03",
    }
    detail_item = {
        "id": "scene-1",
        "sku": "WICKED-1",
        "title": "Wicked Scene",
        "description": "Plot text",
        "rating": "4.5",
        "date": "2024-02-03",
        "url": "https://site.example/scenes/wicked",
        "poster": {"large": "https://img.example/poster.jpg"},
        "background": "https://img.example/bg.jpg",
        "duration": 3600,
        "performers": [
            {
                "name": "Jane Doe",
                "full_name": "Jane A. Doe",
                "aliases": ["Janie"],
                "slug": "jane-doe",
                "image": "https://img.example/jane.jpg",
                "extras": {
                    "gender": "FEMALE",
                    "birthday": "1990-01-02",
                    "height": "170 cm",
                    "cupsize": "D",
                    "measurements": "34-24-35",
                    "nationality": "American",
                    "links": {"official": "https://jane.example"},
                },
            },
            {
                "name": "John Doe",
                "aliases": ["Johnny"],
                "extras": {"gender": "MALE"},
            },
        ],
        "site": {
            "name": "Example Site",
            "network": {"name": "Example Network"},
        },
        "tags": [
            {"name": "Blonde", "parents": [{"name": "Feature"}]},
        ],
        "directors": [{"name": "Director One"}],
    }
    fake = FakeSession({
        "/scenes": {"data": [list_item]},
        "/scenes/scene-1": {"data": detail_item},
        "/movies": {"data": []},
    })
    scraper = ThePornDBScraper(api_token="token")
    scraper._session = fake

    video = scraper.search("Wicked Scene")

    assert video is not None
    assert video.number == "WICKED-1"
    assert video.title == "Wicked Scene"
    assert video.summary == "Plot text"
    assert video.rating == 4.5
    assert video.duration == 60
    assert video.maker == "Example Site"
    assert video.label == "Example Network"
    assert video.director == "Director One"
    assert video.tags == ["Blonde", "Feature"]
    assert video.cover_url == "https://img.example/poster.jpg"
    assert video.sample_images == ["https://img.example/bg.jpg"]
    assert [a.name for a in video.actresses] == ["Jane Doe"]
    assert video.actress_aliases == {"Jane Doe": ["Janie", "Jane A. Doe"]}
    assert video.actress_profiles[0]["height"] == 170
    assert video.actress_profiles[0]["bust"] == 34
    assert video.actress_profiles[0]["waist"] == 24
    assert video.actress_profiles[0]["hip"] == 35
    assert video.actress_profiles[0]["official_url"] == "https://jane.example"


def test_theporndb_keyword_search_maps_movies_too():
    movie_item = {
        "id": "movie-1",
        "external_id": "MOV-1",
        "title": "Movie Result",
        "site": {"name": "Movie Site"},
        "performers": [{"name": "Mary", "extras": {"gender": "TRANSGENDER_FEMALE"}}],
    }
    fake = FakeSession({
        "/scenes": {"data": []},
        "/movies": {"data": [movie_item]},
    })
    scraper = ThePornDBScraper(api_token="token")
    scraper._session = fake

    results = scraper.search_by_keyword("Movie", limit=5)

    assert len(results) == 1
    assert results[0].number == "MOV-1"
    assert [a.name for a in results[0].actresses] == ["Mary"]


def test_theporndb_auth_failure_suppresses_repeated_requests():
    import core.scrapers.theporndb as tpdb

    tpdb._AUTH_FAILURES.clear()
    first = ThePornDBScraper(api_token="bad-token")
    first_session = FakeSession({
        "/scenes": FakeResponse({}, status_code=401),
        "/movies": {"data": [{"id": "movie-1", "title": "Should Not Request"}]},
    })
    first._session = first_session

    assert first.search("Blacked Scene") is None
    assert len(first_session.calls) == 1

    second = ThePornDBScraper(api_token="bad-token")
    second_session = FakeSession({"/scenes": {"data": [{"id": "scene-1"}]}})
    second._session = second_session

    assert second.search("Blacked Scene") is None
    assert second_session.calls == []
    tpdb._AUTH_FAILURES.clear()

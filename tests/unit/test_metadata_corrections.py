from core.metadata_corrections import apply_metadata_overrides, sanitize_actor_names
from core.scrapers.models import Video


def test_builtin_override_corrects_fc2_ppv_1313698_actress():
    corrected, fields = apply_metadata_overrides(
        {"number": "FC2-1313698", "actors": ["ないない"], "maker": "ないない"},
        "FC2-PPV-1313698",
    )

    assert corrected["actors"] == ["菅野松雪"]
    assert corrected["actresses"] == ["菅野松雪"]
    assert corrected["maker"] == "ないない"
    assert "actors" in fields


def test_title_rule_maps_zanagashiya_an_chan_to_mizuki_an():
    corrected, fields = apply_metadata_overrides(
        {
            "number": "FC2-9999999",
            "title": "清楚美人杏ちゃん サンプルタイトル",
            "maker": "ザ・流し屋",
            "actors": [],
        },
        "FC2-PPV-9999999",
    )

    assert corrected["actors"] == ["水希杏"]
    assert corrected["actresses"] == ["水希杏"]
    assert {"actors", "actresses"}.issubset(fields)


def test_unknown_actor_placeholders_are_removed():
    assert sanitize_actor_names(["ないない", "不明", "unknown", "ログイン", "菅野松雪"]) == ["菅野松雪"]


def test_fc2_seller_name_is_not_kept_as_actress():
    corrected, fields = apply_metadata_overrides(
        {"number": "FC2-PPV-1234567", "actors": ["ジローの動画"], "maker": "ジローの動画"},
        "FC2-PPV-1234567",
    )

    assert corrected["actors"] == []
    assert "actors" in fields


def test_builtin_override_also_matches_original_censored_number():
    corrected, fields = apply_metadata_overrides(
        {"actors": ["不明"]},
        "PPPD-206",
    )

    assert corrected["actors"] == ["菅野松雪"]
    assert corrected["actresses"] == ["菅野松雪"]
    assert "actresses" in fields


def test_configured_override_extends_builtin_rules():
    corrected, fields = apply_metadata_overrides(
        {"actors": ["Wrong"], "tags": ["old"]},
        "ABC-123",
        {
            "metadata_overrides": [
                {
                    "numbers": ["ABC-123"],
                    "actresses": ["Actor"],
                    "tags": ["corrected"],
                }
            ]
        },
    )

    assert corrected["actors"] == ["Actor"]
    assert corrected["actresses"] == ["Actor"]
    assert corrected["tags"] == ["old", "corrected"]
    assert fields == ["actors", "actresses", "tags"]


def test_configured_override_can_supply_cover_url_and_duration():
    corrected, fields = apply_metadata_overrides(
        {"cover": "", "duration": None},
        "PRED-002",
        {
            "metadata_overrides": [
                {
                    "numbers": ["PRED-002"],
                    "cover_url": "https://pics.dmm.co.jp/digital/video/pred00002/pred00002pl.jpg",
                    "duration": 120,
                }
            ]
        },
    )

    assert corrected["cover_url"] == "https://pics.dmm.co.jp/digital/video/pred00002/pred00002pl.jpg"
    assert corrected["cover"] == "https://pics.dmm.co.jp/digital/video/pred00002/pred00002pl.jpg"
    assert corrected["duration"] == 120
    assert fields == ["cover_url", "cover", "duration"]


def test_configured_override_replaces_same_number_collision_metadata():
    corrected, fields = apply_metadata_overrides(
        {
            "number": "DWD-072",
            "title": "高身長痴女 綺麗なお姉さんの美脚とデカ尻 ～トールマニア～ 青山葵",
            "actors": ["青山葵"],
            "maker": "Dogma",
            "date": "2012-10-19",
            "cover": "https://pics.dmm.co.jp/digital/video/dwd00072/dwd00072pl.jpg",
        },
        "DWD-072",
        {
            "metadata_overrides": [
                {
                    "numbers": ["DWD-072"],
                    "title": "投稿個人撮影 キモ男ヲタ復讐動画 サエグサモモカ編",
                    "actresses": ["斉藤みゆ"],
                    "maker": "玉屋レーベル",
                    "date": "2019-12-28",
                    "release_date": "2019-12-28",
                    "url": "https://www.dmm.co.jp/digital/videoa/-/detail/=/cid=h_580dwd00072/",
                    "cover_url": "https://pics.dmm.co.jp/digital/video/h_580dwd00072/h_580dwd00072pl.jpg",
                }
            ]
        },
    )

    assert corrected["title"] == "投稿個人撮影 キモ男ヲタ復讐動画 サエグサモモカ編"
    assert corrected["actors"] == ["斉藤みゆ"]
    assert corrected["actresses"] == ["斉藤みゆ"]
    assert corrected["maker"] == "玉屋レーベル"
    assert corrected["date"] == "2019-12-28"
    assert corrected["release_date"] == "2019-12-28"
    assert corrected["url"] == "https://www.dmm.co.jp/digital/videoa/-/detail/=/cid=h_580dwd00072/"
    assert corrected["cover"] == "https://pics.dmm.co.jp/digital/video/h_580dwd00072/h_580dwd00072pl.jpg"
    assert "title" in fields
    assert "cover" in fields


def test_configured_override_keeps_date_style_separator_distinct():
    config = {
        "metadata_overrides": [
            {
                "numbers": ["041021_001"],
                "title": "3P中出後吞下精液",
                "actresses": ["新城由衣", "椎名明日香"],
            }
        ]
    }

    unchanged, unchanged_fields = apply_metadata_overrides(
        {"number": "041021-001", "actors": ["櫻井えみ"]},
        "041021-001",
        config,
    )
    assert unchanged["actors"] == ["櫻井えみ"]
    assert "title" not in unchanged
    assert unchanged_fields == []

    corrected, fields = apply_metadata_overrides(
        {"number": "041021_001", "actors": ["椎名あすか"]},
        "041021_001",
        config,
    )

    assert corrected["title"] == "3P中出後吞下精液"
    assert corrected["actors"] == ["新城由衣", "椎名明日香"]
    assert corrected["actresses"] == ["新城由衣", "椎名明日香"]
    assert fields == ["actors", "actresses", "title"]


def test_search_jav_applies_metadata_override_to_scraper_result(mocker):
    from core.scraper import search_jav

    fake_video = Video(
        number="FC2-1313698",
        title="FC2 Title",
        actresses=[],
        maker="ないない",
        source="fc2",
    )
    fake_scraper = mocker.Mock()
    fake_scraper.search.return_value = fake_video
    mocker.patch("core.scraper.FC2Scraper", return_value=fake_scraper)

    result = search_jav("FC2-1313698", source="fc2")

    assert result["actors"] == ["菅野松雪"]
    assert result["_metadata_override_fields"] == ["actors", "actresses"]


def test_search_jav_can_return_configured_override_without_scraper_hit(mocker):
    from core.scraper import search_jav

    fake_scraper = mocker.Mock()
    fake_scraper.search.return_value = None
    mocker.patch("core.scraper.JavBusScraper", return_value=fake_scraper)
    mocker.patch(
        "core.scraper.load_config",
        return_value={
            "metadata_overrides": [
                {
                    "numbers": ["PRED-002"],
                    "title": "中出しお義姉さんの誘惑",
                    "actresses": ["美竹すず"],
                    "maker": "プレミアム",
                    "release_date": "2017-07-25",
                    "duration": 120,
                    "cover_url": "https://pics.dmm.co.jp/digital/video/pred00002/pred00002pl.jpg",
                }
            ]
        },
    )

    result = search_jav("PRED-002", source="javbus")

    assert result["source"] == "override"
    assert result["title"] == "中出しお義姉さんの誘惑"
    assert result["actors"] == ["美竹すず"]
    assert result["cover"] == "https://pics.dmm.co.jp/digital/video/pred00002/pred00002pl.jpg"
    assert result["date"] == "2017-07-25"
    assert result["duration"] == 120

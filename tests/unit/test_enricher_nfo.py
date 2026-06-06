"""63c-5: enricher summary/rating crossing — _scraper_to_meta / _merge_meta / _write_nfo（CD-63c-5）。"""
from pathlib import Path
from unittest.mock import patch

from core.enricher import (
    _missing_fields,
    _merge_meta,
    _scraper_to_meta,
    _write_cover,
    _write_extrafanart,
    _write_nfo,
)


# ─── _scraper_to_meta crossing point（_ 前綴 → canonical）───

def test_scraper_to_meta_crosses_summary_rating():
    meta = _scraper_to_meta({
        "_summary": "plot text",
        "_rating": 3.5,
        "_actress_aliases": {"Alice": ["A"]},
        "_actress_profiles": [{"name": "Alice"}],
        "title": "T",
    })
    assert meta["summary"] == "plot text"
    assert meta["rating"] == 3.5
    assert meta["actress_aliases"] == {"Alice": ["A"]}
    assert meta["actress_profiles"] == [{"name": "Alice"}]
    # crossing 後不再有 _ 前綴鍵
    assert "_summary" not in meta
    assert "_rating" not in meta
    assert "_actress_aliases" not in meta
    assert "_actress_profiles" not in meta


def test_scraper_to_meta_defaults_no_metatube():
    meta = _scraper_to_meta({})
    assert meta["summary"] == ""
    assert meta["rating"] is None
    assert meta["actress_aliases"] == {}
    assert meta["actress_profiles"] == []


# ─── _merge_meta 透傳 ───

def test_merge_meta_carries_summary_rating_from_supplement():
    base = {"title": "T"}  # DB/NFO base 無 summary
    supplement = _scraper_to_meta({
        "_summary": "plot",
        "_rating": 4.0,
        "_actress_aliases": {"Alice": ["A"]},
        "_actress_profiles": [{"name": "Alice"}],
    })
    merged, _ = _merge_meta(base, supplement)
    assert merged["summary"] == "plot"
    assert merged["rating"] == 4.0
    assert merged["actress_aliases"] == {"Alice": ["A"]}
    assert merged["actress_profiles"] == [{"name": "Alice"}]


def test_merge_meta_base_summary_not_overwritten():
    base = {"summary": "kept", "rating": 2.0}
    supplement = {"summary": "new", "rating": 5.0}
    merged, _ = _merge_meta(base, supplement)
    assert merged["summary"] == "kept"  # fill-if-empty：base 有值不覆蓋
    assert merged["rating"] == 2.0


def test_missing_fields_treats_filename_title_as_missing():
    assert "title" in _missing_fields({"title": "AVOP-460-1"}, number="AVOP-460")
    assert "title" in _missing_fields({"title": "[AVOP-460]AVOP-460-1"}, number="AVOP-460")
    assert "title" in _missing_fields({"title": "FC2PPV-1234567-1"}, number="FC2-PPV-1234567")


def test_merge_meta_replaces_filename_title_from_scraper():
    merged, filled = _merge_meta(
        {"title": "AVOP-460-1"},
        {"title": "Scraped Title"},
        number="AVOP-460",
    )
    assert merged["title"] == "Scraped Title"
    assert "title" in filled


def test_merge_meta_keeps_real_existing_title():
    merged, filled = _merge_meta(
        {"title": "Actual Local Title"},
        {"title": "Scraped Title"},
        number="AVOP-460",
    )
    assert merged["title"] == "Actual Local Title"
    assert "title" not in filled


def test_merge_meta_replaces_local_cover_path_with_remote_scraper_cover():
    merged, _ = _merge_meta(
        {"cover_url": "file:///D:/Metadata/041021-001/cover.jpg"},
        {"cover_url": "https://www.1pondo.tv/moviepages/041021_001/images/str.jpg"},
    )

    assert merged["cover_url"] == "https://www.1pondo.tv/moviepages/041021_001/images/str.jpg"


# ─── _write_nfo 讀 canonical key 傳 generate_nfo ───

def test_write_nfo_passes_canonical_summary_rating(tmp_path):
    fs_path = str(tmp_path / "vid.mp4")
    meta = {"summary": "plot text", "rating": 3.5, "title": "T"}
    with patch("core.enricher.generate_nfo") as mock_gen:
        mock_gen.return_value = True
        _write_nfo(fs_path, "ABC-123", meta, write_nfo=True,
                   overwrite_existing=True, has_subtitle=False, user_tags=[])
    _, kwargs = mock_gen.call_args
    assert kwargs["summary"] == "plot text"
    assert kwargs["rating"] == 3.5


def test_write_nfo_builtin_defaults(tmp_path):
    """builtin meta（無 summary/rating 鍵）→ generate_nfo(summary='', rating=None)。"""
    fs_path = str(tmp_path / "vid.mp4")
    meta = {"title": "T"}
    with patch("core.enricher.generate_nfo") as mock_gen:
        mock_gen.return_value = True
        _write_nfo(fs_path, "ABC-123", meta, write_nfo=True,
                   overwrite_existing=True, has_subtitle=False, user_tags=[])
    _, kwargs = mock_gen.call_args
    assert kwargs["summary"] == ""
    assert kwargs["rating"] is None


def test_write_nfo_meta_has_no_underscore_keys(tmp_path):
    """regression：_write_nfo 收到的 meta 不含 _summary/_rating（whitelist 已在
    _scraper_to_meta crossing 截斷）。"""
    meta = _scraper_to_meta({"_summary": "x", "_rating": 1.0})
    assert "_summary" not in meta and "_rating" not in meta


def test_write_nfo_uses_centralized_sidecar_paths(tmp_path):
    fs_path = str(tmp_path / "media" / "vid.mp4")
    sidecar_root = tmp_path / "Metadata"
    config = {
        "sidecar": {
            "mode": "centralized",
            "root_dir": str(sidecar_root),
            "layout": "{maker}/{num}",
            "nfo_filename": "{num}.nfo",
            "cover_filename": "cover.jpg",
            "poster_filename": "poster.jpg",
            "fanart_filename": "fanart.jpg",
        }
    }
    meta = {"title": "T", "maker": "Studio"}

    with patch("core.enricher.generate_nfo") as mock_gen:
        mock_gen.return_value = True
        _write_nfo(
            fs_path,
            "ABC-123",
            meta,
            write_nfo=True,
            overwrite_existing=True,
            has_subtitle=False,
            user_tags=[],
            sidecar_config=config,
        )

    _, kwargs = mock_gen.call_args
    assert Path(kwargs["output_path"]) == sidecar_root / "Studio" / "ABC-123" / "ABC-123.nfo"
    assert kwargs["thumb_filename"] == "cover.jpg"
    assert kwargs["poster_filename"] == "poster.jpg"
    assert kwargs["fanart_filename"] == "fanart.jpg"


def test_write_cover_uses_centralized_sidecar_paths(tmp_path):
    fs_path = str(tmp_path / "media" / "vid.mp4")
    sidecar_root = tmp_path / "Metadata"
    config = {
        "sidecar": {
            "mode": "centralized",
            "root_dir": str(sidecar_root),
            "layout": "{num}",
            "cover_filename": "cover.jpg",
        }
    }

    with patch("core.enricher.download_image", return_value=True) as mock_download:
        assert _write_cover(
            fs_path,
            "https://example.test/cover.jpg",
            write_cover=True,
            overwrite_existing=True,
            number="ABC-123",
            meta={},
            sidecar_config=config,
        ) is True

    assert Path(mock_download.call_args.args[1]) == sidecar_root / "ABC-123" / "cover.jpg"


def test_enrich_single_syncs_existing_sidecar_cover_for_complete_db_meta(tmp_path):
    from core.database import Video, VideoRepository, init_db
    from core.enricher import enrich_single
    from core.path_utils import to_file_uri

    db_path = tmp_path / "test.db"
    init_db(db_path)
    repo = VideoRepository(db_path)
    video_path = tmp_path / "media" / "ABC-123.mp4"
    video_path.parent.mkdir()
    video_path.write_bytes(b"video")
    sidecar_root = tmp_path / "Metadata"
    cover_path = sidecar_root / "Studio" / "ABC-123" / "cover.jpg"
    cover_path.parent.mkdir(parents=True)
    cover_path.write_bytes(b"cover")
    path_uri = to_file_uri(str(video_path))

    repo.upsert(Video(
        path=path_uri,
        number="ABC-123",
        title="Real Title",
        actresses=["Actor"],
        maker="Studio",
        director="Director",
        series="Series",
        label="Label",
        tags=["Tag"],
        release_date="2024-01-01",
        cover_path="",
        size_bytes=123,
        mtime=456.0,
    ))

    config = {
        "sidecar": {
            "mode": "centralized",
            "root_dir": str(sidecar_root),
            "layout": "{maker}/{num}",
            "cover_filename": "cover.jpg",
        }
    }
    with patch("core.enricher.VideoRepository", return_value=repo):
        result = enrich_single(
            file_path=path_uri,
            number="ABC-123",
            mode="fill_missing",
            write_nfo=False,
            write_cover=True,
            sidecar_config=config,
        )

    fetched = repo.get_by_path(path_uri)
    assert result.success is True
    assert result.source_used == "db"
    assert fetched.cover_path == to_file_uri(str(cover_path))
    assert fetched.size_bytes == 123
    assert fetched.mtime == 456.0


def test_enrich_single_discovers_centralized_sidecar_when_db_maker_missing(tmp_path):
    from core.database import Video, VideoRepository, init_db
    from core.enricher import enrich_single
    from core.path_utils import to_file_uri

    db_path = tmp_path / "test.db"
    init_db(db_path)
    repo = VideoRepository(db_path)
    video_path = tmp_path / "media" / "ABC-123-1.mp4"
    video_path.parent.mkdir()
    video_path.write_bytes(b"video")
    sidecar_root = tmp_path / "Metadata"
    wrong_sidecar_dir = sidecar_root / "A Prefix" / "ABC-123"
    wrong_sidecar_dir.mkdir(parents=True)
    (wrong_sidecar_dir / "ABC-123.nfo").write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<movie>
  <title>Wrong Prefix Title</title>
  <studio>A Prefix</studio>
  <num>ABC-123</num>
</movie>
""",
        encoding="utf-8",
    )
    sidecar_dir = sidecar_root / "Studio" / "ABC-123"
    sidecar_dir.mkdir(parents=True)
    nfo_path = sidecar_dir / "ABC-123.nfo"
    nfo_path.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<movie>
  <title>Real NFO Title</title>
  <studio>Studio</studio>
  <premiered>2024-01-01</premiered>
  <actor><name>Actor</name></actor>
  <tag>Tag</tag>
  <num>ABC-123</num>
</movie>
""",
        encoding="utf-8",
    )
    cover_path = sidecar_dir / "cover.jpg"
    cover_path.write_bytes(b"cover")
    path_uri = to_file_uri(str(video_path))

    repo.upsert(Video(
        path=path_uri,
        number="ABC-123",
        title="ABC-123-1",
        maker="A Prefix",
        cover_path="",
        nfo_mtime=0.0,
        size_bytes=123,
        mtime=456.0,
    ))

    config = {
        "sidecar": {
            "mode": "centralized",
            "root_dir": str(sidecar_root),
            "layout": "{maker}/{num}",
            "nfo_filename": "{num}.nfo",
            "cover_filename": "cover.jpg",
        }
    }
    with patch("core.enricher.VideoRepository", return_value=repo), \
         patch("core.enricher.search_jav", return_value=None) as mock_search:
        result = enrich_single(
            file_path=path_uri,
            number="ABC-123",
            mode="fill_missing",
            write_nfo=True,
            write_cover=True,
            sidecar_config=config,
        )

    fetched = repo.get_by_path(path_uri)
    assert result.success is True
    assert result.source_used == "sidecar"
    mock_search.assert_not_called()
    assert fetched.title == "Real NFO Title"
    assert fetched.maker == "Studio"
    assert fetched.cover_path == to_file_uri(str(cover_path))
    assert fetched.nfo_mtime == nfo_path.stat().st_mtime
    assert fetched.size_bytes == 123
    assert fetched.mtime == 456.0


def test_enrich_single_searches_when_existing_sidecar_missing_actress(tmp_path):
    import xml.etree.ElementTree as ET

    from core.database import Video, VideoRepository, init_db
    from core.enricher import enrich_single
    from core.path_utils import to_file_uri

    db_path = tmp_path / "test.db"
    init_db(db_path)
    repo = VideoRepository(db_path)
    video_path = tmp_path / "media" / "ABC-123.mp4"
    video_path.parent.mkdir()
    video_path.write_bytes(b"video")

    sidecar_root = tmp_path / "Metadata"
    sidecar_dir = sidecar_root / "Studio" / "ABC-123"
    sidecar_dir.mkdir(parents=True)
    nfo_path = sidecar_dir / "ABC-123.nfo"
    nfo_path.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<movie>
  <title>Existing Title</title>
  <studio>Studio</studio>
  <premiered>2024-01-01</premiered>
  <tag>Tag</tag>
  <num>ABC-123</num>
</movie>
""",
        encoding="utf-8",
    )
    (sidecar_dir / "cover.jpg").write_bytes(b"cover")
    path_uri = to_file_uri(str(video_path))
    repo.upsert(Video(
        path=path_uri,
        number="ABC-123",
        title="Existing Title",
        maker="Studio",
        actresses=[],
        tags=["Tag"],
        release_date="2024-01-01",
        cover_path="",
        nfo_mtime=0.0,
    ))
    config = {
        "sidecar": {
            "mode": "centralized",
            "root_dir": str(sidecar_root),
            "layout": "{maker}/{num}",
            "nfo_filename": "{num}.nfo",
            "cover_filename": "cover.jpg",
        }
    }
    scraper_data = {
        "number": "ABC-123",
        "title": "Scraper Title",
        "actors": ["Actor"],
        "maker": "Studio",
        "date": "2024-01-01",
        "tags": ["Tag"],
        "cover": "https://example.test/cover.jpg",
        "source": "d2pass",
    }

    with patch("core.enricher.VideoRepository", return_value=repo), \
         patch("core.enricher.search_jav", return_value=scraper_data) as mock_search:
        result = enrich_single(
            file_path=path_uri,
            number="ABC-123",
            mode="fill_missing",
            write_nfo=True,
            write_cover=True,
            sidecar_config=config,
        )

    fetched = repo.get_by_path(path_uri)
    actors = [
        (name.text or "").strip()
        for actor in ET.parse(nfo_path).getroot().findall("actor")
        for name in [actor.find("name")]
        if name is not None
    ]
    assert result.success is True
    assert result.source_used == "d2pass"
    mock_search.assert_called_once()
    assert fetched.actresses == ["Actor"]
    assert actors == ["Actor"]


def test_enrich_single_applies_builtin_metadata_override_to_existing_sidecar(tmp_path):
    import xml.etree.ElementTree as ET

    from core.database import Video, VideoRepository, init_db
    from core.enricher import enrich_single
    from core.path_utils import to_file_uri

    db_path = tmp_path / "test.db"
    init_db(db_path)
    repo = VideoRepository(db_path)
    video_path = tmp_path / "media" / "FC2PPV-1313698.mp4"
    video_path.parent.mkdir()
    video_path.write_bytes(b"video")

    sidecar_root = tmp_path / "Metadata"
    sidecar_dir = sidecar_root / "ないない" / "FC2-1313698"
    sidecar_dir.mkdir(parents=True)
    nfo_path = sidecar_dir / "FC2-1313698.nfo"
    nfo_path.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<movie>
  <title>FC2 Title</title>
  <studio>ないない</studio>
  <actor><name>ないない</name></actor>
  <num>FC2-1313698</num>
</movie>
""",
        encoding="utf-8",
    )
    cover_path = sidecar_dir / "cover.jpg"
    cover_path.write_bytes(b"cover")
    path_uri = to_file_uri(str(video_path))

    repo.upsert(Video(
        path=path_uri,
        number="FC2-1313698",
        title="FC2 Title",
        actresses=["ないない"],
        maker="ないない",
        cover_path="",
        nfo_mtime=0.0,
        size_bytes=123,
        mtime=456.0,
    ))

    config = {
        "sidecar": {
            "mode": "centralized",
            "root_dir": str(sidecar_root),
            "layout": "{maker}/{num}",
            "nfo_filename": "{num}.nfo",
            "cover_filename": "cover.jpg",
        }
    }
    with patch("core.enricher.VideoRepository", return_value=repo), \
         patch("core.enricher.search_jav", return_value=None) as mock_search:
        result = enrich_single(
            file_path=path_uri,
            number="FC2-1313698",
            mode="fill_missing",
            write_nfo=True,
            write_cover=True,
            sidecar_config=config,
        )

    fetched = repo.get_by_path(path_uri)
    root = ET.parse(nfo_path).getroot()
    actors = [
        (name.text or "").strip()
        for actor in root.findall("actor")
        for name in [actor.find("name")]
    ]
    assert result.success is True
    assert result.source_used == "sidecar"
    mock_search.assert_not_called()
    assert fetched.actresses == ["菅野松雪"]
    assert fetched.cover_path == to_file_uri(str(cover_path))
    assert actors == ["菅野松雪"]


def test_enrich_single_syncs_configured_title_override_to_existing_sidecar(tmp_path):
    import xml.etree.ElementTree as ET

    from core.database import Video, VideoRepository, init_db
    from core.enricher import enrich_single
    from core.path_utils import to_file_uri

    db_path = tmp_path / "test.db"
    init_db(db_path)
    repo = VideoRepository(db_path)
    video_path = tmp_path / "media" / "041021_001.mp4"
    video_path.parent.mkdir()
    video_path.write_bytes(b"video")

    sidecar_root = tmp_path / "Metadata"
    sidecar_dir = sidecar_root / "041021_001"
    sidecar_dir.mkdir(parents=True)
    nfo_path = sidecar_dir / "041021_001.nfo"
    nfo_path.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<movie>
  <title>[041021_001]3Pで中出しされたザーメンをごっくん</title>
  <originaltitle></originaltitle>
  <studio></studio>
  <premiered>2021-04-10</premiered>
  <actor><name>新城由衣</name></actor>
  <actor><name>椎名あすか</name></actor>
  <num>041021_001</num>
</movie>
""",
        encoding="utf-8",
    )
    cover_path = sidecar_dir / "cover.jpg"
    cover_path.write_bytes(b"cover")
    path_uri = to_file_uri(str(video_path))

    repo.upsert(Video(
        path=path_uri,
        number="041021-001",
        title="[041021_001]3Pで中出しされたザーメンをごっくん",
        actresses=["新城由衣", "椎名あすか"],
        cover_path="",
        nfo_mtime=0.0,
    ))

    config = {
        "sidecar": {
            "mode": "centralized",
            "root_dir": str(sidecar_root),
            "layout": "{num}",
            "nfo_filename": "{num}.nfo",
            "cover_filename": "cover.jpg",
        },
        "metadata_overrides": [
            {
                "numbers": ["041021_001"],
                "title": "3P中出後吞下精液",
                "original_title": "3Pで中出しされたザーメンをごっくん",
                "actresses": ["新城由衣", "椎名明日香"],
            }
        ],
    }
    with patch("core.enricher.VideoRepository", return_value=repo), \
         patch("core.enricher.search_jav", return_value=None) as mock_search:
        result = enrich_single(
            file_path=path_uri,
            number="041021-001",
            mode="fill_missing",
            write_nfo=True,
            write_cover=True,
            sidecar_config=config,
        )

    fetched = repo.get_by_path(path_uri)
    root = ET.parse(nfo_path).getroot()
    actors = [
        (name.text or "").strip()
        for actor in root.findall("actor")
        for name in [actor.find("name")]
    ]
    assert result.success is True
    mock_search.assert_not_called()
    assert fetched.number == "041021_001"
    assert fetched.title == "3P中出後吞下精液"
    assert fetched.original_title == "3Pで中出しされたザーメンをごっくん"
    assert fetched.actresses == ["新城由衣", "椎名明日香"]
    assert (root.findtext("title") or "").strip() == "3P中出後吞下精液"
    assert (root.findtext("originaltitle") or "").strip() == "3Pで中出しされたザーメンをごっくん"
    assert actors == ["新城由衣", "椎名明日香"]


def test_enrich_single_removes_fc2_seller_actor_from_existing_sidecar(tmp_path):
    import xml.etree.ElementTree as ET

    from core.database import Video, VideoRepository, init_db
    from core.enricher import enrich_single
    from core.path_utils import to_file_uri

    db_path = tmp_path / "test.db"
    init_db(db_path)
    repo = VideoRepository(db_path)
    video_path = tmp_path / "media" / "FC2PPV-9999999.mp4"
    video_path.parent.mkdir()
    video_path.write_bytes(b"video")

    sidecar_root = tmp_path / "Metadata"
    sidecar_dir = sidecar_root / "ジローの動画" / "FC2-9999999"
    sidecar_dir.mkdir(parents=True)
    nfo_path = sidecar_dir / "FC2-9999999.nfo"
    nfo_path.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<movie>
  <title>FC2 Title</title>
  <studio>ジローの動画</studio>
  <actor><name>ジローの動画</name></actor>
  <num>FC2-9999999</num>
</movie>
""",
        encoding="utf-8",
    )
    (sidecar_dir / "cover.jpg").write_bytes(b"cover")
    path_uri = to_file_uri(str(video_path))

    repo.upsert(Video(
        path=path_uri,
        number="FC2-9999999",
        title="FC2 Title",
        actresses=["ジローの動画"],
        maker="ジローの動画",
        cover_path="",
        nfo_mtime=0.0,
    ))

    config = {
        "sidecar": {
            "mode": "centralized",
            "root_dir": str(sidecar_root),
            "layout": "{maker}/{num}",
            "nfo_filename": "{num}.nfo",
            "cover_filename": "cover.jpg",
        }
    }
    with patch("core.enricher.VideoRepository", return_value=repo), \
         patch("core.enricher.search_jav", return_value=None) as mock_search:
        result = enrich_single(
            file_path=path_uri,
            number="FC2-9999999",
            mode="fill_missing",
            write_nfo=True,
            write_cover=True,
            sidecar_config=config,
        )

    fetched = repo.get_by_path(path_uri)
    root = ET.parse(nfo_path).getroot()
    assert result.success is True
    mock_search.assert_not_called()
    assert fetched.actresses == []
    assert root.findall("actor") == []


def test_enrich_single_applies_override_without_creating_placeholder_nfo_when_scraper_missing(tmp_path):
    from core.database import Video, VideoRepository, init_db
    from core.enricher import enrich_single
    from core.path_utils import to_file_uri

    db_path = tmp_path / "test.db"
    init_db(db_path)
    repo = VideoRepository(db_path)
    video_path = tmp_path / "media" / "PPPD-206.mp4"
    video_path.parent.mkdir()
    video_path.write_bytes(b"video")
    path_uri = to_file_uri(str(video_path))

    repo.upsert(Video(
        path=path_uri,
        number="PPPD-206",
        title="PPPD-206",
        actresses=[],
        maker="Oppai",
        cover_path="",
        nfo_mtime=0.0,
    ))

    sidecar_root = tmp_path / "Metadata"
    config = {
        "sidecar": {
            "mode": "centralized",
            "root_dir": str(sidecar_root),
            "layout": "{maker}/{num}",
            "nfo_filename": "{num}.nfo",
            "cover_filename": "cover.jpg",
        }
    }

    with patch("core.enricher.VideoRepository", return_value=repo), \
         patch("core.enricher.search_jav", return_value=None) as mock_search:
        result = enrich_single(
            file_path=path_uri,
            number="PPPD-206",
            mode="fill_missing",
            write_nfo=True,
            write_cover=True,
            sidecar_config=config,
        )

    fetched = repo.get_by_path(path_uri)
    assert result.success is True
    assert result.source_used == "override"
    assert result.nfo_written is False
    mock_search.assert_called_once()
    assert fetched.actresses == ["菅野松雪"]
    assert fetched.nfo_mtime == 0.0
    assert not (sidecar_root / "Oppai" / "PPPD-206" / "PPPD-206.nfo").exists()


def test_enrich_single_upsert_uses_existing_sidecar_cover_when_download_skipped(tmp_path):
    from core.database import Video, VideoRepository, init_db
    from core.enricher import enrich_single
    from core.path_utils import to_file_uri

    db_path = tmp_path / "test.db"
    init_db(db_path)
    repo = VideoRepository(db_path)
    video_path = tmp_path / "media" / "ABC-123.mp4"
    video_path.parent.mkdir()
    video_path.write_bytes(b"video")
    sidecar_root = tmp_path / "Metadata"
    cover_path = sidecar_root / "Studio" / "ABC-123" / "cover.jpg"
    cover_path.parent.mkdir(parents=True)
    cover_path.write_bytes(b"cover")
    path_uri = to_file_uri(str(video_path))

    repo.upsert(Video(
        path=path_uri,
        number="ABC-123",
        title="Old Title",
        maker="Studio",
        cover_path="",
        size_bytes=321,
        mtime=654.0,
    ))

    scraper_data = {
        "number": "ABC-123",
        "title": "New Title",
        "actors": ["Actor"],
        "cover": "https://example.test/cover.jpg",
        "date": "2024-01-01",
        "maker": "Studio",
        "director": "Director",
        "series": "Series",
        "label": "Label",
        "tags": ["Tag"],
        "duration": 120,
        "source": "javbus",
    }
    config = {
        "sidecar": {
            "mode": "centralized",
            "root_dir": str(sidecar_root),
            "layout": "{maker}/{num}",
            "cover_filename": "cover.jpg",
        }
    }
    with patch("core.enricher.VideoRepository", return_value=repo), \
         patch("core.enricher.download_image") as mock_download:
        result = enrich_single(
            file_path=path_uri,
            number="ABC-123",
            mode="refresh_full",
            write_nfo=False,
            write_cover=True,
            overwrite_existing=False,
            scraper_data=scraper_data,
            sidecar_config=config,
        )

    fetched = repo.get_by_path(path_uri)
    assert result.success is True
    assert result.cover_written is False
    mock_download.assert_not_called()
    assert fetched.cover_path == to_file_uri(str(cover_path))
    assert fetched.size_bytes == 321
    assert fetched.mtime == 654.0


def test_write_extrafanart_uses_centralized_sidecar_paths(tmp_path):
    fs_path = str(tmp_path / "media" / "vid.mp4")
    sidecar_root = tmp_path / "Metadata"
    config = {
        "sidecar": {
            "mode": "centralized",
            "root_dir": str(sidecar_root),
            "layout": "{num}",
            "extrafanart_dir": "samples",
        }
    }

    with patch("core.enricher.download_image", return_value=True):
        uris = _write_extrafanart(
            fs_path,
            ["https://example.test/1.jpg", "https://example.test/2.jpg"],
            write_extrafanart=True,
            number="ABC-123",
            meta={},
            sidecar_config=config,
        )

    assert len(uris) == 2
    assert "Metadata/ABC-123/samples/fanart1.jpg" in uris[0].replace("\\", "/")

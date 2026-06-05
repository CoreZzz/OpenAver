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
    meta = _scraper_to_meta({"_summary": "plot text", "_rating": 3.5, "title": "T"})
    assert meta["summary"] == "plot text"
    assert meta["rating"] == 3.5
    # crossing 後不再有 _ 前綴鍵
    assert "_summary" not in meta
    assert "_rating" not in meta


def test_scraper_to_meta_defaults_no_metatube():
    meta = _scraper_to_meta({})
    assert meta["summary"] == ""
    assert meta["rating"] is None


# ─── _merge_meta 透傳 ───

def test_merge_meta_carries_summary_rating_from_supplement():
    base = {"title": "T"}  # DB/NFO base 無 summary
    supplement = _scraper_to_meta({"_summary": "plot", "_rating": 4.0})
    merged, _ = _merge_meta(base, supplement)
    assert merged["summary"] == "plot"
    assert merged["rating"] == 4.0


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

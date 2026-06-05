from pathlib import Path

from core.path_utils import to_file_uri
from core.sidecar_paths import resolve_sidecar_paths


def test_alongside_paths_match_existing_layout(tmp_path):
    video = tmp_path / "SONE-103-C.mp4"
    paths = resolve_sidecar_paths(str(video))

    assert paths.mode == "alongside"
    assert Path(paths.base_dir) == tmp_path
    assert Path(paths.nfo_path) == tmp_path / "SONE-103-C.nfo"
    assert Path(paths.cover_path) == tmp_path / "SONE-103-C.jpg"
    assert Path(paths.poster_path) == tmp_path / "SONE-103-C-poster.jpg"
    assert Path(paths.fanart_path) == tmp_path / "SONE-103-C-fanart.jpg"
    assert Path(paths.extrafanart_dir) == tmp_path / "extrafanart"


def test_centralized_paths_use_root_layout_and_stable_filenames(tmp_path):
    video = tmp_path / "media" / "sone103.mp4"
    root = tmp_path / "Metadata"
    config = {
        "sidecar": {
            "mode": "centralized",
            "root_dir": str(root),
            "layout": "{maker}/{num}",
            "nfo_filename": "{num}.nfo",
            "cover_filename": "cover.jpg",
            "poster_filename": "poster.jpg",
            "fanart_filename": "fanart.jpg",
            "extrafanart_dir": "extrafanart",
        }
    }
    metadata = {"number": "SONE-103", "maker": "S1"}

    paths = resolve_sidecar_paths(str(video), metadata, config)

    base_dir = root / "S1" / "SONE-103"
    assert paths.mode == "centralized"
    assert Path(paths.base_dir) == base_dir
    assert Path(paths.nfo_path) == base_dir / "SONE-103.nfo"
    assert Path(paths.cover_path) == base_dir / "cover.jpg"
    assert Path(paths.poster_path) == base_dir / "poster.jpg"
    assert Path(paths.fanart_path) == base_dir / "fanart.jpg"
    assert Path(paths.extrafanart_dir) == base_dir / "extrafanart"


def test_centralized_layout_sanitizes_path_components(tmp_path):
    video = tmp_path / "AB:12.mp4"
    root = tmp_path / "Metadata"
    config = {
        "mode": "centralized",
        "root_dir": str(root),
        "layout": "{maker}/{num}/{title}",
        "nfo_filename": "{num}.nfo",
    }
    metadata = {
        "number": "AB:12",
        "maker": "A/B",
        "title": "bad<title>*name",
    }

    paths = resolve_sidecar_paths(str(video), metadata, config)

    assert Path(paths.base_dir) == root / "A B" / "AB 12" / "bad title name"
    assert Path(paths.nfo_path).name == "AB 12.nfo"


def test_file_uri_input_uses_path_utils(tmp_path):
    video = tmp_path / "FC2-PPV-1234567.mp4"
    paths = resolve_sidecar_paths(to_file_uri(str(video)))

    assert Path(paths.nfo_path) == tmp_path / "FC2-PPV-1234567.nfo"
    assert paths.as_uri_dict()["nfo_path"].startswith("file:///")

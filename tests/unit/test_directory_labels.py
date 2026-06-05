from core.directory_labels import (
    configured_directories_from_config,
    directory_label_for_path,
    normalize_directory_label,
    normalize_directory_labels,
)
from core.path_utils import to_file_uri


def test_normalize_directory_label_defaults_to_censored():
    assert normalize_directory_label("uncensored") == "uncensored"
    assert normalize_directory_label("censored") == "censored"
    assert normalize_directory_label("unknown") == "censored"
    assert normalize_directory_label(None) == "censored"


def test_normalize_directory_labels_only_keeps_current_directories():
    labels = {
        "D:/A": "uncensored",
        "D:/Removed": "uncensored",
    }

    result = normalize_directory_labels(["D:/A", "D:/B"], labels)

    assert result == {
        "D:/A": "uncensored",
        "D:/B": "censored",
    }


def test_directory_label_for_path_uses_configured_directory(tmp_path):
    censored = tmp_path / "censored"
    uncensored = tmp_path / "uncensored"
    censored.mkdir()
    uncensored.mkdir()
    config = {
        "gallery": {
            "directories": [str(censored), str(uncensored)],
            "directory_labels": {str(uncensored): "uncensored"},
            "path_mappings": {},
        }
    }

    configured, _ = configured_directories_from_config(config)

    assert directory_label_for_path(
        to_file_uri(str(censored / "SONE-103.mp4")), configured
    ) == "censored"
    assert directory_label_for_path(
        to_file_uri(str(uncensored / "FC2-PPV-1234567.mp4")), configured
    ) == "uncensored"


def test_directory_label_prefers_deeper_configured_directory(tmp_path):
    root = tmp_path / "media"
    child = root / "uncensored"
    child.mkdir(parents=True)
    config = {
        "gallery": {
            "directories": [str(root), str(child)],
            "directory_labels": {
                str(root): "censored",
                str(child): "uncensored",
            },
            "path_mappings": {},
        }
    }

    configured, _ = configured_directories_from_config(config)

    assert directory_label_for_path(
        to_file_uri(str(child / "HEYZO-1234.mp4")), configured
    ) == "uncensored"

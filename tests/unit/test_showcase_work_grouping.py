from types import SimpleNamespace

from core.path_utils import to_file_uri
from web.routers.actress import _count_work_cards_for_videos
from web.routers.showcase import _group_videos_by_work, _serialize_video


def _video(path, number="SONE-103", title="Test", size=100, duration=None,
           cover_path="", nfo_mtime=0):
    return SimpleNamespace(
        path=to_file_uri(str(path)),
        number=number,
        title=title,
        original_title="",
        actresses=[],
        maker="S1",
        release_date="",
        tags=[],
        size_bytes=size,
        cover_path=cover_path,
        mtime=0,
        director="",
        duration=duration,
        series="",
        label="",
        sample_images=[],
        user_tags=[],
        nfo_mtime=nfo_mtime,
    )


def test_showcase_groups_part_files_by_work_key(tmp_path):
    videos = [
        _video(tmp_path / "SONE-103-a.mp4"),
        _video(tmp_path / "SONE-103-1.mp4"),
    ]

    grouped = _group_videos_by_work(videos)

    assert len(grouped) == 1
    _, identity, files = grouped[0]
    assert identity.work_key == "SONE-103"
    assert {file["part_index"] for file in files} == {"1", "A"}


def test_actress_video_count_uses_work_card_count(tmp_path):
    videos = [
        _video(tmp_path / "SONE-103-a.mp4"),
        _video(tmp_path / "SONE-103-1.mp4"),
        _video(tmp_path / "SONE-104.mp4", number="SONE-104"),
    ]

    assert _count_work_cards_for_videos(videos) == 2


def test_showcase_serialized_video_exposes_files_and_variant_flags(tmp_path):
    videos = [
        _video(tmp_path / "SONE-103.mp4"),
        _video(tmp_path / "SONE-103-UC.mp4"),
    ]
    primary, identity, files = _group_videos_by_work(videos)[0]

    data = _serialize_video(primary, {}, identity=identity, files=files)

    assert data["work_key"] == "SONE-103"
    assert data["file_count"] == 2
    assert len(data["files"]) == 2
    uc_file = next(file for file in data["files"] if file["filename"].endswith("-UC.mp4"))
    assert uc_file["variant_flags"] == {"subtitle_cn": True, "cracked": True}


def test_showcase_serialized_group_uses_work_number_for_card_name(tmp_path):
    videos = [
        _video(
            tmp_path / "AVOP-460-1.mp4",
            number="AVOP-460-1",
            title="[AVOP-460]AVOP-460-1",
            size=475,
            duration=121,
        ),
        _video(tmp_path / "AVOP-460-2.mp4", number="AVOP-460-2", title="AVOP-460-2", size=604),
    ]
    primary, identity, files = _group_videos_by_work(videos)[0]

    data = _serialize_video(primary, {}, identity=identity, files=files)

    assert data["work_key"] == "AVOP-460"
    assert data["number"] == "AVOP-460"
    assert data["title"] == "AVOP-460"
    assert data["size"] == 1079
    assert data["file_count"] == 2
    assert [file["part_index"] for file in data["files"]] == ["1", "2"]
    assert data["files"][0]["filename"] == "AVOP-460-1.mp4"
    assert "duration" not in data["files"][0]


def test_showcase_group_prefers_part_with_persisted_assets(tmp_path):
    cover = to_file_uri(str(tmp_path / "AVOP-460.jpg"))
    videos = [
        _video(
            tmp_path / "AVOP-460-1.mp4",
            number="AVOP-460",
            title="AVOP-460",
            nfo_mtime=123.0,
        ),
        _video(
            tmp_path / "AVOP-460-2.mp4",
            number="AVOP-460",
            title="Scraped Title",
            cover_path=cover,
        ),
    ]
    primary, identity, files = _group_videos_by_work(videos)[0]

    data = _serialize_video(primary, {}, identity=identity, files=files)

    assert data["work_key"] == "AVOP-460"
    assert data["has_cover"] is True
    assert data["has_nfo"] is True
    assert data["cover_url"].startswith("/api/gallery/image?path=")
    assert [file["part_index"] for file in data["files"]] == ["1", "2"]
    assert all("_cover_path" not in file for file in data["files"])


def test_showcase_serialized_video_exposes_directory_label(tmp_path):
    videos = [
        _video(tmp_path / "censored" / "SONE-103.mp4"),
        _video(tmp_path / "uncensored" / "FC2-PPV-1234567.mp4", number="FC2-PPV-1234567"),
    ]
    labels = {
        videos[0].path: "censored",
        videos[1].path: "uncensored",
    }
    primary, identity, files = _group_videos_by_work(videos, labels)[0]

    data = _serialize_video(
        primary,
        {},
        identity=identity,
        files=files,
        directory_label=labels[primary.path],
    )

    assert data["directory_label"] == labels[primary.path]
    assert all(file["directory_label"] in {"censored", "uncensored"} for file in data["files"])

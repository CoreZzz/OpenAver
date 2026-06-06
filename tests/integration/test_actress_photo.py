"""
測試 /api/actresses/{name}/photo-candidates 本機候選 alias 展開（TASK-58a-A2）

涵蓋 4 個 case：
1. 有 alias 的女優（primary 查）→ 展開多名
2. 有 alias 的女優（alias 查，bob）→ 雙向展開
3. 無 alias → resolve 回 {primary} 單名，行為不退化
4. 雲端路徑只收到 primary name（不被 alias 污染）
"""

import json
import pytest
from unittest.mock import patch, MagicMock, call


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _parse_sse(response_text: str) -> list:
    """解析 SSE response，回傳所有 (event_name, data) tuple list。"""
    events = []
    current_event = None
    for line in response_text.strip().split('\n'):
        if line.startswith('event: '):
            current_event = line[7:].strip()
        elif line.startswith('data: '):
            try:
                data = json.loads(line[6:])
                events.append((current_event, data))
            except json.JSONDecodeError:
                pass
    return events


def _make_mock_actress(name="alice"):
    """建立 mock Actress object。"""
    actress = MagicMock()
    actress.name = name
    actress.photo_source = "graphis"
    return actress


def _make_mock_video(path: str, cover_path: str):
    """建立 mock Video object（有 cover_path）。"""
    video = MagicMock()
    video.path = path
    video.cover_path = cover_path
    return video


# ---------------------------------------------------------------------------
# Case 1: 有 alias 的女優（primary 查）→ 展開多名
# ---------------------------------------------------------------------------

def test_local_candidates_alias_expand_primary(client):
    """
    primary 查 'alice'，resolve 回 {"alice", "bob", "cody"}
    → get_videos_by_actress_names 收到包含三名的 list
    """
    mock_actress = _make_mock_actress("alice")

    # resolve("alice") → {"alice", "bob", "cody"}
    mock_resolve = MagicMock(return_value={"alice", "bob", "cody"})

    # get_videos_by_actress_names 回傳空（只驗 call args）
    mock_get_videos = MagicMock(return_value=[])

    with patch('web.routers.actress.ActressRepository') as mock_actress_repo_cls, \
         patch('web.routers.actress.AliasRepository') as mock_alias_repo_cls, \
         patch('web.routers.actress.VideoRepository') as mock_video_repo_cls, \
         patch('web.routers.actress.init_db'), \
         patch('web.routers.actress._fetch_single_source', return_value=None):

        # ActressRepository().get_by_name("alice") → mock_actress
        mock_actress_repo_cls.return_value.get_by_name.return_value = mock_actress

        # AliasRepository().resolve("alice") → {"alice", "bob", "cody"}
        mock_alias_repo_cls.return_value.resolve = mock_resolve

        # VideoRepository().get_videos_by_actress_names → []
        mock_video_repo_cls.return_value.get_videos_by_actress_names = mock_get_videos

        response = client.get("/api/actresses/alice/photo-candidates")
        assert response.status_code == 200

    # resolve 應以 "alice" 呼叫一次
    mock_resolve.assert_called_once_with("alice")

    # get_videos_by_actress_names 應以包含三名的 list 呼叫
    assert mock_get_videos.called
    called_names = mock_get_videos.call_args[0][0]  # positional arg
    assert set(called_names) == {"alice", "bob", "cody"}


# ---------------------------------------------------------------------------
# Case 2: 有 alias 的女優（alias 查 bob）→ 雙向展開
# ---------------------------------------------------------------------------

def test_local_candidates_alias_expand_via_alias(client):
    """
    alias 查 'bob'，resolve 雙向解析回 {"alice", "bob", "cody"}
    → get_videos_by_actress_names 收到包含三名的 list
    """
    mock_actress = _make_mock_actress("bob")

    mock_resolve = MagicMock(return_value={"alice", "bob", "cody"})
    mock_get_videos = MagicMock(return_value=[])

    with patch('web.routers.actress.ActressRepository') as mock_actress_repo_cls, \
         patch('web.routers.actress.AliasRepository') as mock_alias_repo_cls, \
         patch('web.routers.actress.VideoRepository') as mock_video_repo_cls, \
         patch('web.routers.actress.init_db'), \
         patch('web.routers.actress._fetch_single_source', return_value=None):

        mock_actress_repo_cls.return_value.get_by_name.return_value = mock_actress
        mock_alias_repo_cls.return_value.resolve = mock_resolve
        mock_video_repo_cls.return_value.get_videos_by_actress_names = mock_get_videos

        response = client.get("/api/actresses/bob/photo-candidates")
        assert response.status_code == 200

    # resolve 應以 "bob" 呼叫（雙向解析發生在 resolve 內部）
    mock_resolve.assert_called_once_with("bob")

    called_names = mock_get_videos.call_args[0][0]
    assert set(called_names) == {"alice", "bob", "cody"}


# ---------------------------------------------------------------------------
# Case 3: 無 alias → resolve 回 {primary} 單名，行為不退化
# ---------------------------------------------------------------------------

def test_local_candidates_no_alias_single_name(client):
    """
    無 alias 的女優 'dana'，resolve 回 {"dana"}
    → get_videos_by_actress_names(["dana"]) 呼叫，行為等價舊版
    → 舊版的 get_videos_by_actress 不應被呼叫
    """
    mock_actress = _make_mock_actress("dana")

    mock_resolve = MagicMock(return_value={"dana"})
    mock_get_videos = MagicMock(return_value=[])
    mock_get_videos_single = MagicMock(return_value=[])  # 舊版，不應被呼叫

    with patch('web.routers.actress.ActressRepository') as mock_actress_repo_cls, \
         patch('web.routers.actress.AliasRepository') as mock_alias_repo_cls, \
         patch('web.routers.actress.VideoRepository') as mock_video_repo_cls, \
         patch('web.routers.actress.init_db'), \
         patch('web.routers.actress._fetch_single_source', return_value=None):

        mock_actress_repo_cls.return_value.get_by_name.return_value = mock_actress
        mock_alias_repo_cls.return_value.resolve = mock_resolve
        mock_video_repo_cls.return_value.get_videos_by_actress_names = mock_get_videos
        mock_video_repo_cls.return_value.get_videos_by_actress = mock_get_videos_single

        response = client.get("/api/actresses/dana/photo-candidates")
        assert response.status_code == 200

    mock_resolve.assert_called_once_with("dana")

    # 新版：應呼叫 get_videos_by_actress_names，不是舊版 get_videos_by_actress
    assert mock_get_videos.called
    called_names = mock_get_videos.call_args[0][0]
    assert set(called_names) == {"dana"}

    # 舊版不應被呼叫
    mock_get_videos_single.assert_not_called()


# ---------------------------------------------------------------------------
# Case 4: 雲端路徑只收到 primary name（不被 alias 污染）
# ---------------------------------------------------------------------------

def test_cloud_sources_use_primary_name_only(client):
    """
    雲端 scraper（gfriends / javdb / graphis / wiki / minnano）的 _fetch_single_source
    應收到 URL path param（即 "alice"），不被 alias set 污染。
    """
    mock_actress = _make_mock_actress("alice")
    # photo_source=None → 所有雲端都在 cloud_sources
    mock_actress.photo_source = None

    mock_resolve = MagicMock(return_value={"alice", "bob", "cody"})
    mock_get_videos = MagicMock(return_value=[])

    # 追蹤 _fetch_single_source 的呼叫
    fetch_calls = []

    def mock_fetch(name, src):
        fetch_calls.append((name, src))
        return None

    with patch('web.routers.actress.ActressRepository') as mock_actress_repo_cls, \
         patch('web.routers.actress.AliasRepository') as mock_alias_repo_cls, \
         patch('web.routers.actress.VideoRepository') as mock_video_repo_cls, \
         patch('web.routers.actress.init_db'), \
         patch('web.routers.actress._fetch_single_source', side_effect=mock_fetch):

        mock_actress_repo_cls.return_value.get_by_name.return_value = mock_actress
        mock_alias_repo_cls.return_value.resolve = mock_resolve
        mock_video_repo_cls.return_value.get_videos_by_actress_names = mock_get_videos

        response = client.get("/api/actresses/alice/photo-candidates")
        assert response.status_code == 200

    # 雲端呼叫的 name 參數必須全是 "alice"（URL param），不可是 alias
    for name_arg, src_arg in fetch_calls:
        assert name_arg == "alice", (
            f"雲端 scraper '{src_arg}' 收到 '{name_arg}'，應只收到 'alice'（primary/URL param）"
        )

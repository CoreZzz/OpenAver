"""Unit tests for core/metatube/state.py — MetatubeConnectionState singleton.

Covers:
1. connect bulk-true
2. disconnect bulk-false (keys preserved)
3. mark_failed single
4. mark_available restore
5. availability_map returns copy
6. is_available unknown id → False
7. repeated connect resets
8. thread-safe parallel mark_failed/mark_available (ThreadPoolExecutor)
9. availability_map no race during concurrent marks
"""
import pytest
from concurrent.futures import ThreadPoolExecutor

from core.metatube.state import MetatubeConnectionState


# ---------------------------------------------------------------------------
# Fixture: fresh state instance per test (avoids module-singleton pollution)
# ---------------------------------------------------------------------------

@pytest.fixture
def state():
    """Return a fresh MetatubeConnectionState for each test."""
    return MetatubeConnectionState()


# ---------------------------------------------------------------------------
# 1. connect bulk-true
# ---------------------------------------------------------------------------

def test_connect_bulk_true(state):
    state.connect('http://host', 'tok', ['FANZA', 'HEYZO'])
    assert state.is_available('metatube:FANZA') is True
    assert state.is_available('metatube:HEYZO') is True
    assert state.is_connected is True
    assert state.provider_count == 2
    assert state.base_url == 'http://host'
    assert state.token == 'tok'


# ---------------------------------------------------------------------------
# 2. disconnect bulk-false (keys preserved)
# ---------------------------------------------------------------------------

def test_disconnect_bulk_false_keys_preserved(state):
    state.connect('http://host', 'tok', ['FANZA', 'HEYZO'])
    state.disconnect()

    assert state.is_available('metatube:FANZA') is False
    assert state.is_available('metatube:HEYZO') is False
    assert state.is_connected is False
    assert state.base_url is None
    assert state.token is None

    # Keys must still be present in the map (for UI grey capsules)
    m = state.availability_map()
    assert 'metatube:FANZA' in m
    assert 'metatube:HEYZO' in m
    assert m['metatube:FANZA'] is False
    assert m['metatube:HEYZO'] is False


# ---------------------------------------------------------------------------
# 3. mark_failed single (does not affect other keys)
# ---------------------------------------------------------------------------

def test_mark_failed_single(state):
    state.connect('http://host', 'tok', ['FANZA', 'HEYZO'])
    state.mark_failed('metatube:FANZA')
    assert state.is_available('metatube:FANZA') is False
    assert state.is_available('metatube:HEYZO') is True  # unaffected


# ---------------------------------------------------------------------------
# 4. mark_available restores after mark_failed
# ---------------------------------------------------------------------------

def test_mark_available_restore(state):
    state.connect('http://host', 'tok', ['FANZA'])
    state.mark_failed('metatube:FANZA')
    assert state.is_available('metatube:FANZA') is False
    state.mark_available('metatube:FANZA')
    assert state.is_available('metatube:FANZA') is True


# ---------------------------------------------------------------------------
# 5. availability_map returns a copy (mutate does not affect internal state)
# ---------------------------------------------------------------------------

def test_availability_map_returns_copy(state):
    state.connect('http://host', 'tok', ['FANZA'])
    result = state.availability_map()
    assert result['metatube:FANZA'] is True

    # Mutate the returned dict
    result['metatube:FANZA'] = False

    # Internal state must be unchanged
    assert state.is_available('metatube:FANZA') is True


# ---------------------------------------------------------------------------
# 6. is_available unknown id → False
# ---------------------------------------------------------------------------

def test_is_available_unknown_id_false(state):
    # Before any connect — empty _availability
    assert state.is_available('metatube:UNKNOWN') is False

    # After connect with known providers — unknown id still False
    state.connect('http://host', 'tok', ['FANZA'])
    assert state.is_available('metatube:UNKNOWN') is False


# ---------------------------------------------------------------------------
# 7. repeated connect resets (bulk-true overwrites failed state)
# ---------------------------------------------------------------------------

def test_repeated_connect_resets(state):
    state.connect('http://host1', 'tok1', ['FANZA'])
    state.mark_failed('metatube:FANZA')
    assert state.is_available('metatube:FANZA') is False

    # Second connect with updated URL and extended provider list
    state.connect('http://host2', 'tok2', ['FANZA', 'HEYZO'])
    assert state.is_available('metatube:FANZA') is True   # bulk-true overwrote
    assert state.is_available('metatube:HEYZO') is True
    assert state.base_url == 'http://host2'
    assert state.provider_count == 2


# ---------------------------------------------------------------------------
# 8a. Thread-safe: parallel toggle (mark_failed then mark_available) → all True
# ---------------------------------------------------------------------------

def test_parallel_toggle_all_available(state):
    providers = [f'P{i}' for i in range(20)]
    state.connect('http://host', '', providers)

    def toggle(name):
        sid = f'metatube:{name}'
        state.mark_failed(sid)
        state.mark_available(sid)

    with ThreadPoolExecutor(max_workers=10) as ex:
        list(ex.map(toggle, providers))

    for n in providers:
        assert state.is_available(f'metatube:{n}') is True


# ---------------------------------------------------------------------------
# 8b. Thread-safe: parallel mark_failed → all False
# ---------------------------------------------------------------------------

def test_parallel_mark_failed_all_false(state):
    providers = [f'Q{i}' for i in range(20)]
    state.connect('http://host', '', providers)

    def fail(name):
        state.mark_failed(f'metatube:{name}')

    with ThreadPoolExecutor(max_workers=10) as ex:
        list(ex.map(fail, providers))

    for n in providers:
        assert state.is_available(f'metatube:{n}') is False


# ---------------------------------------------------------------------------
# 9. availability_map does not raise during concurrent marks
# ---------------------------------------------------------------------------

def test_availability_map_no_race_concurrent(state):
    providers = [f'R{i}' for i in range(30)]
    state.connect('http://host', '', providers)

    exceptions = []

    def worker(name):
        try:
            sid = f'metatube:{name}'
            state.mark_failed(sid)
            _ = state.availability_map()  # concurrent snapshot
            state.mark_available(sid)
        except Exception as e:
            exceptions.append(e)

    with ThreadPoolExecutor(max_workers=15) as ex:
        list(ex.map(worker, providers))

    assert exceptions == [], f"Unexpected exceptions: {exceptions}"


# ---------------------------------------------------------------------------
# Extra: mark_failed / mark_available on unknown source_id (no raise)
# ---------------------------------------------------------------------------

def test_mark_unknown_source_id_no_raise(state):
    # Should not raise — just writes to dict
    state.mark_failed('metatube:NEWONE')
    assert state.is_available('metatube:NEWONE') is False
    state.mark_available('metatube:NEWONE')
    assert state.is_available('metatube:NEWONE') is True


# ---------------------------------------------------------------------------
# Extra: provider_count after disconnect is 0
# ---------------------------------------------------------------------------

def test_provider_count_after_disconnect(state):
    state.connect('http://host', 'tok', ['FANZA', 'HEYZO', 'FC2'])
    assert state.provider_count == 3
    state.disconnect()
    assert state.provider_count == 0


# ---------------------------------------------------------------------------
# Fix 2 (P1b): reconnect removes stale providers
# ---------------------------------------------------------------------------

def test_reconnect_removes_stale_providers(state):
    """connect() 清舊 _availability，重連到不含舊 provider 的 server 後不殘留"""
    state.connect('http://host', '', ['FANZA', 'HEYZO'])
    # 重連到只有 JavBus 的 server
    state.connect('http://host', '', ['JavBus'])

    # 舊 provider 不應在 map 裡（因 connect 清空再重建）
    availability = state.availability_map()
    assert 'metatube:FANZA' not in availability
    assert 'metatube:HEYZO' not in availability
    # 新 provider 應在 map 且為 True
    assert availability.get('metatube:JavBus') is True

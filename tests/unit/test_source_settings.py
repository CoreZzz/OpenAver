"""Unit tests for core/source_settings.py business-layer helpers (TASK-61a-1b).

Covers Runtime Auto Pool filtering (`get_enabled_source_ids`) and the
uncensored-mode single-source-of-truth (`is_uncensored_mode_effective`).
Also covers `get_all_source_ids_ordered()` (TASK-65a-1) and the
`FUZZY_SEARCH_SOURCES` constant from `core.scrapers.utils`.
"""
import pytest

from core import source_settings
from core.scrapers.utils import CENSORED_SOURCES, FUZZY_SEARCH_SOURCES


# ---------------------------------------------------------------------------
# get_enabled_source_ids
# ---------------------------------------------------------------------------

def _patch_config(monkeypatch, fake_config):
    monkeypatch.setattr(
        'core.source_settings.load_config', lambda: fake_config
    )


def test_enabled_filter_excludes_disabled(monkeypatch):
    _patch_config(monkeypatch, {
        'sources': [
            {'id': 'dmm', 'type': 'builtin', 'enabled': True, 'order': 0, 'manual_only': False},
            {'id': 'javbus', 'type': 'builtin', 'enabled': False, 'order': 1, 'manual_only': False},
        ]
    })
    assert source_settings.get_enabled_source_ids() == ['dmm']


def test_order_sorting(monkeypatch):
    _patch_config(monkeypatch, {
        'sources': [
            {'id': 'c', 'type': 'builtin', 'enabled': True, 'order': 5, 'manual_only': False},
            {'id': 'a', 'type': 'builtin', 'enabled': True, 'order': 1, 'manual_only': False},
            {'id': 'b', 'type': 'builtin', 'enabled': True, 'order': 3, 'manual_only': False},
        ]
    })
    assert source_settings.get_enabled_source_ids() == ['a', 'b', 'c']


def test_missing_sources_returns_empty(monkeypatch):
    _patch_config(monkeypatch, {'search': {}})
    assert source_settings.get_enabled_source_ids() == []


def test_empty_sources_returns_empty(monkeypatch):
    _patch_config(monkeypatch, {'sources': []})
    assert source_settings.get_enabled_source_ids() == []


def test_manual_only_excluded_even_when_enabled(monkeypatch):
    _patch_config(monkeypatch, {
        'sources': [
            {'id': 'javlibrary', 'type': 'builtin', 'enabled': True, 'order': 0, 'manual_only': True},
            {'id': 'dmm', 'type': 'builtin', 'enabled': True, 'order': 1, 'manual_only': False},
        ]
    })
    assert source_settings.get_enabled_source_ids() == ['dmm']


def test_availability_none_includes_all_enabled_incl_metatube(monkeypatch):
    _patch_config(monkeypatch, {
        'sources': [
            {'id': 'dmm', 'type': 'builtin', 'enabled': True, 'order': 0, 'manual_only': False},
            {'id': 'mt1', 'type': 'metatube', 'enabled': True, 'order': 1, 'manual_only': False},
        ]
    })
    assert source_settings.get_enabled_source_ids(None) == ['dmm', 'mt1']


def test_populated_map_excludes_unavailable_metatube_keeps_builtin(monkeypatch):
    _patch_config(monkeypatch, {
        'sources': [
            {'id': 'dmm', 'type': 'builtin', 'enabled': True, 'order': 0, 'manual_only': False},
            {'id': 'mt1', 'type': 'metatube', 'enabled': True, 'order': 1, 'manual_only': False},
        ]
    })
    # mt1 absent from map -> excluded; builtin dmm bypasses gate even though absent.
    assert source_settings.get_enabled_source_ids({'mt1': False}) == ['dmm']
    assert source_settings.get_enabled_source_ids({}) == ['dmm']


def test_populated_map_keeps_available_metatube(monkeypatch):
    _patch_config(monkeypatch, {
        'sources': [
            {'id': 'dmm', 'type': 'builtin', 'enabled': True, 'order': 0, 'manual_only': False},
            {'id': 'mt1', 'type': 'metatube', 'enabled': True, 'order': 1, 'manual_only': False},
        ]
    })
    assert source_settings.get_enabled_source_ids({'mt1': True}) == ['dmm', 'mt1']


def test_malformed_entries_do_not_crash(monkeypatch):
    _patch_config(monkeypatch, {
        'sources': [
            {},  # no keys at all
            {'id': 'dmm', 'type': 'builtin', 'enabled': True},  # no order/manual_only
        ]
    })
    assert source_settings.get_enabled_source_ids() == ['dmm']


def test_source_mode_censored_includes_censored_even_if_disabled(monkeypatch):
    _patch_config(monkeypatch, {
        'search': {'source_mode': 'censored'},
        'sources': [
            {'id': 'dmm', 'type': 'builtin', 'enabled': False, 'order': 0, 'manual_only': False},
            {'id': 'fc2', 'type': 'builtin', 'enabled': True, 'order': 1, 'manual_only': False},
        ],
    })
    assert source_settings.get_enabled_source_ids() == ['dmm']


def test_source_mode_uncensored_includes_uncensored_even_if_disabled(monkeypatch):
    _patch_config(monkeypatch, {
        'search': {'source_mode': 'uncensored'},
        'sources': [
            {'id': 'javbus', 'type': 'builtin', 'enabled': True, 'order': 0, 'manual_only': False},
            {'id': 'fc2', 'type': 'builtin', 'enabled': False, 'order': 1, 'manual_only': False},
            {'id': 'avsox', 'type': 'builtin', 'enabled': True, 'order': 2, 'manual_only': False},
        ],
    })
    assert source_settings.get_enabled_source_ids() == ['fc2', 'avsox']


def test_source_mode_all_ignores_enabled_but_excludes_manual_beta(monkeypatch):
    _patch_config(monkeypatch, {
        'search': {'source_mode': 'all'},
        'sources': [
            {'id': 'dmm', 'type': 'builtin', 'enabled': False, 'order': 0, 'manual_only': False},
            {'id': 'javbus', 'type': 'builtin', 'enabled': True, 'order': 1, 'manual_only': True},
            {'id': 'javdb', 'type': 'builtin', 'enabled': True, 'order': 2, 'manual_only': False, 'is_beta': True},
            {'id': 'fc2', 'type': 'builtin', 'enabled': False, 'order': 3, 'manual_only': False},
        ],
    })
    assert source_settings.get_enabled_source_ids() == ['dmm', 'fc2']


def test_source_mode_custom_uses_custom_ids_in_source_order(monkeypatch):
    _patch_config(monkeypatch, {
        'search': {'source_mode': 'custom', 'custom_source_ids': ['fc2', 'dmm']},
        'sources': [
            {'id': 'dmm', 'type': 'builtin', 'enabled': False, 'order': 0, 'manual_only': False},
            {'id': 'javbus', 'type': 'builtin', 'enabled': True, 'order': 1, 'manual_only': False},
            {'id': 'fc2', 'type': 'builtin', 'enabled': False, 'order': 2, 'manual_only': False},
        ],
    })
    assert source_settings.get_enabled_source_ids() == ['dmm', 'fc2']


def test_source_mode_max_sources_per_search_caps_result(monkeypatch):
    _patch_config(monkeypatch, {
        'search': {'source_mode': 'all', 'max_sources_per_search': 2},
        'sources': [
            {'id': 'dmm', 'type': 'builtin', 'enabled': True, 'order': 0, 'manual_only': False},
            {'id': 'javbus', 'type': 'builtin', 'enabled': True, 'order': 1, 'manual_only': False},
            {'id': 'fc2', 'type': 'builtin', 'enabled': True, 'order': 2, 'manual_only': False},
        ],
    })
    assert source_settings.get_enabled_source_ids() == ['dmm', 'javbus']


def test_invalid_source_mode_falls_back_to_enabled(monkeypatch):
    _patch_config(monkeypatch, {
        'search': {'source_mode': 'surprise'},
        'sources': [
            {'id': 'dmm', 'type': 'builtin', 'enabled': False, 'order': 0, 'manual_only': False},
            {'id': 'fc2', 'type': 'builtin', 'enabled': True, 'order': 1, 'manual_only': False},
        ],
    })
    assert source_settings.get_search_source_mode() == 'enabled'
    assert source_settings.get_enabled_source_ids() == ['fc2']


# ---------------------------------------------------------------------------
# is_uncensored_mode_effective
# ---------------------------------------------------------------------------

def test_uncensored_derive_all_censored_disabled_true():
    config = {
        'sources': [
            {'id': 'dmm', 'type': 'builtin', 'enabled': False},
            {'id': 'javbus', 'type': 'builtin', 'enabled': False},
            {'id': 'jav321', 'type': 'builtin', 'enabled': False},
            {'id': 'javdb', 'type': 'builtin', 'enabled': False},
            {'id': 'fc2', 'type': 'builtin', 'enabled': True},
        ]
    }
    assert source_settings.is_uncensored_mode_effective(config) is True


def test_uncensored_derive_one_censored_enabled_false():
    config = {
        'sources': [
            {'id': 'dmm', 'type': 'builtin', 'enabled': True},
            {'id': 'javbus', 'type': 'builtin', 'enabled': False},
            {'id': 'jav321', 'type': 'builtin', 'enabled': False},
            {'id': 'javdb', 'type': 'builtin', 'enabled': False},
        ]
    }
    assert source_settings.is_uncensored_mode_effective(config) is False


def test_uncensored_derive_censored_absent_treated_disabled_true():
    # No censored builtins present in sources at all -> none enabled -> True.
    config = {
        'sources': [
            {'id': 'fc2', 'type': 'builtin', 'enabled': True},
            {'id': 'avsox', 'type': 'builtin', 'enabled': True},
        ]
    }
    assert source_settings.is_uncensored_mode_effective(config) is True


def test_uncensored_fallback_legacy_true():
    config = {'search': {'uncensored_mode_enabled': True}}
    assert source_settings.is_uncensored_mode_effective(config) is True


def test_uncensored_fallback_legacy_false():
    config = {'search': {'uncensored_mode_enabled': False}}
    assert source_settings.is_uncensored_mode_effective(config) is False


def test_uncensored_fallback_legacy_absent_false():
    assert source_settings.is_uncensored_mode_effective({}) is False
    assert source_settings.is_uncensored_mode_effective({'search': {}}) is False


def test_uncensored_empty_sources_uses_legacy():
    # Empty sources list -> fallback to legacy key, NOT derive.
    config = {'sources': [], 'search': {'uncensored_mode_enabled': True}}
    assert source_settings.is_uncensored_mode_effective(config) is True
    config2 = {'sources': [], 'search': {'uncensored_mode_enabled': False}}
    assert source_settings.is_uncensored_mode_effective(config2) is False


# ---------------------------------------------------------------------------
# get_all_source_ids_ordered  (TASK-65a-1)
# ---------------------------------------------------------------------------

def test_all_sources_includes_disabled(monkeypatch):
    """Key difference vs get_enabled_source_ids: disabled entries still returned."""
    _patch_config(monkeypatch, {
        'sources': [
            {'id': 'dmm', 'enabled': True, 'order': 0},
            {'id': 'javbus', 'enabled': False, 'order': 1},
        ]
    })
    result = source_settings.get_all_source_ids_ordered()
    assert result == ['dmm', 'javbus']


def test_all_sources_sort_by_order_ascending(monkeypatch):
    """Entries are returned sorted by 'order' ascending, regardless of input order."""
    _patch_config(monkeypatch, {
        'sources': [
            {'id': 'c', 'enabled': True, 'order': 5},
            {'id': 'a', 'enabled': False, 'order': 1},
            {'id': 'b', 'enabled': True, 'order': 3},
        ]
    })
    assert source_settings.get_all_source_ids_ordered() == ['a', 'b', 'c']


def test_all_sources_missing_sources_key_returns_empty(monkeypatch):
    """Config without 'sources' key -> returns []."""
    _patch_config(monkeypatch, {'search': {}})
    assert source_settings.get_all_source_ids_ordered() == []


def test_all_sources_empty_sources_returns_empty(monkeypatch):
    """sources: [] -> returns []."""
    _patch_config(monkeypatch, {'sources': []})
    assert source_settings.get_all_source_ids_ordered() == []


def test_all_sources_malformed_non_dict_entries_skipped(monkeypatch):
    """Non-dict entries (int, None, str) are skipped without crashing."""
    _patch_config(monkeypatch, {
        'sources': [
            42,
            None,
            'bad_entry',
            {'id': 'dmm', 'enabled': True, 'order': 0},
        ]
    })
    assert source_settings.get_all_source_ids_ordered() == ['dmm']


def test_all_sources_missing_order_key_treated_as_zero(monkeypatch):
    """Entry without 'order' key sorts as order=0, no crash."""
    _patch_config(monkeypatch, {
        'sources': [
            {'id': 'b', 'enabled': True, 'order': 2},
            {'id': 'a', 'enabled': True},  # no 'order' key -> treated as 0
        ]
    })
    result = source_settings.get_all_source_ids_ordered()
    assert result == ['a', 'b']


def test_all_sources_manual_only_still_returned(monkeypatch):
    """manual_only: True entries are NOT filtered out (unlike get_enabled_source_ids)."""
    _patch_config(monkeypatch, {
        'sources': [
            {'id': 'javlibrary', 'enabled': True, 'order': 0, 'manual_only': True},
            {'id': 'dmm', 'enabled': True, 'order': 1, 'manual_only': False},
        ]
    })
    result = source_settings.get_all_source_ids_ordered()
    assert result == ['javlibrary', 'dmm']


def test_all_sources_metatube_disabled_still_returned(monkeypatch):
    """type='metatube' disabled entries are still returned (no availability gate)."""
    _patch_config(monkeypatch, {
        'sources': [
            {'id': 'dmm', 'type': 'builtin', 'enabled': True, 'order': 0},
            {'id': 'mt1', 'type': 'metatube', 'enabled': False, 'order': 1},
        ]
    })
    result = source_settings.get_all_source_ids_ordered()
    assert result == ['dmm', 'mt1']


# ---------------------------------------------------------------------------
# FUZZY_SEARCH_SOURCES constant  (TASK-65a-1)
# ---------------------------------------------------------------------------

def test_fuzzy_search_sources_contains_correct_two():
    """FUZZY_SEARCH_SOURCES must contain exactly the 2 fuzzy-capable censored sources (TASK-65g)."""
    assert sorted(FUZZY_SEARCH_SOURCES) == sorted(['javbus', 'dmm'])


def test_fuzzy_search_sources_excludes_avsox():
    """avsox is uncensored-only — must not be in FUZZY_SEARCH_SOURCES."""
    assert 'avsox' not in FUZZY_SEARCH_SOURCES


def test_fuzzy_search_sources_excludes_fake_fuzzy_sources():
    """fc2, heyzo, d2pass do keyword-as-id lookup, not real fuzzy search — excluded."""
    assert 'fc2' not in FUZZY_SEARCH_SOURCES
    assert 'heyzo' not in FUZZY_SEARCH_SOURCES
    assert 'd2pass' not in FUZZY_SEARCH_SOURCES


def test_fuzzy_search_sources_is_independent_constant():
    """FUZZY_SEARCH_SOURCES must be a distinct object from CENSORED_SOURCES, not reused."""
    assert FUZZY_SEARCH_SOURCES is not CENSORED_SOURCES


def test_uncensored_source_mode_true():
    assert source_settings.is_uncensored_mode_effective({
        'search': {'source_mode': 'uncensored'},
        'sources': [
            {'id': 'dmm', 'type': 'builtin', 'enabled': True},
        ],
    }) is True


def test_all_source_mode_not_uncensored():
    assert source_settings.is_uncensored_mode_effective({
        'search': {'source_mode': 'all', 'uncensored_mode_enabled': True},
    }) is False

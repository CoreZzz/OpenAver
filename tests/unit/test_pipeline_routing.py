"""
TestPipeline + TestUnknownSource — Pipeline routing 測試
（搬自 tests/integration/test_new_scrapers.py TestPipeline + TestUnknownSource）

mock scraper.search，驗證路由邏輯（不含 TestClient 測試）
"""
import pytest
from unittest.mock import patch, MagicMock

from core.scrapers.d2pass import D2PassScraper
from core.scrapers.heyzo import HEYZOScraper
from core.scrapers.dmm import DMMScraper
from core.scrapers.javbus import JavBusScraper
from core.scrapers.models import Video
from core.scrapers.utils import SOURCE_ORDER
from core.scraper import search_jav, smart_search


@pytest.fixture(autouse=True)
def _no_rate_limit(monkeypatch):
    """跳過 rate_limit / REQUEST_DELAY sleep，加速測試"""
    monkeypatch.setattr("core.scrapers.dmm.rate_limit", lambda *a, **kw: None)
    monkeypatch.setattr("core.scraper.time.sleep", lambda *a: None)


# ============================================================
# Helper
# ============================================================

def _make_video(source: str, number: str = "TEST-001") -> Video:
    return Video(
        number=number,
        title="Test Title",
        actresses=[],
        date="2024-01-01",
        maker="Test Maker",
        cover_url="",
        tags=[],
        source=source,
        detail_url="https://example.com",
    )


# ============================================================
# TestPipeline (13 tests — excluding test_exact_mode_passes_primary_source)
# ============================================================

class TestPipeline:
    """Pipeline routing 測試（mock scraper.search，驗證路由邏輯）"""

    @pytest.fixture(autouse=True)
    def _all_sources_enabled(self, monkeypatch):
        """隔離 ambient web/config.json 的啟用來源集合。

        TASK-61a-3 起 search_jav(source='auto') 改讀 get_enabled_source_ids()
        →（讀 live web/config.json）決定 fan-out 來源。本檔的 merge-priority
        測試假設 8 個 builtin 來源（含 dmm/javbus）全部啟用；若開發者把有碼來源
        停用（例如開無碼模式），dmm/javbus 會被排除 → search_jav 回 None →
        assertion 觸發 TypeError，測試變得 config-coupled 且不確定。

        此 fixture 把 search_jav 實際呼叫的 core.scraper.get_enabled_source_ids
        monkeypatch 成回傳全部 8 個 builtin id（canonical 順序），讓 merge
        測試只驗證 MERGER 在所有來源可用時的行為，與環境 config 無關。
        """
        monkeypatch.setattr(
            "core.scraper.get_enabled_source_ids",
            lambda availability_map=None: list(SOURCE_ORDER),
        )

    def test_uncensored_detection_d2pass(self):
        """日期_底線格式番號 → 自動走無碼路徑 → D2PassScraper 被呼叫"""
        mock_video = _make_video("d2pass", "120415_201")

        with patch.object(D2PassScraper, 'search', return_value=mock_video) as mock_d2:
            with patch('core.scrapers.dmm.rate_limit'):
                results = smart_search("120415_201")

        assert len(results) == 1
        assert results[0]['_mode'] == 'uncensored'
        mock_d2.assert_called()

    def test_uncensored_detection_heyzo(self):
        """HEYZO- 前綴番號 → 自動走無碼路徑 → HEYZOScraper 被呼叫"""
        mock_video = _make_video("heyzo", "HEYZO-0783")

        with patch.object(D2PassScraper, 'search', return_value=None):
            with patch.object(HEYZOScraper, 'search', return_value=mock_video) as mock_heyzo:
                with patch('core.scrapers.dmm.rate_limit'):
                    results = smart_search("HEYZO-0783")

        assert len(results) == 1
        assert results[0]['_mode'] == 'uncensored'
        mock_heyzo.assert_called()

    def test_uncensored_mode_uses_new_sources(self):
        """uncensored_mode=True → D2PassScraper 和 HEYZOScraper 都被嘗試"""
        with patch.object(D2PassScraper, 'search', return_value=None) as mock_d2:
            with patch.object(HEYZOScraper, 'search', return_value=None) as mock_heyzo:
                with patch.object(DMMScraper, 'search', return_value=None):
                    with patch('core.scrapers.dmm.rate_limit'):
                        # FC2 / AVSOX 也需要 mock 避免真實網路請求
                        from core.scrapers.fc2 import FC2Scraper
                        from core.scrapers.avsox import AVSOXScraper
                        with patch.object(FC2Scraper, 'search', return_value=None):
                            with patch.object(AVSOXScraper, 'search', return_value=None):
                                smart_search("SONE-205", uncensored_mode=True)

        mock_d2.assert_called()
        mock_heyzo.assert_called()

    def test_dmm_top1_when_proxy(self):
        """primary_source='dmm' + proxy_url + 番號格式 → DMM Top-1 shortcut 被觸發"""
        mock_video = _make_video("dmm", "SONE-205")

        with patch.object(DMMScraper, 'search', return_value=mock_video) as mock_dmm:
            with patch('core.scrapers.dmm.rate_limit'):
                results = smart_search("SONE-205", proxy_url="http://proxy:8080", primary_source="dmm")

        mock_dmm.assert_called()
        assert len(results) >= 1
        assert results[0]['_mode'] == 'exact'

    def test_uncensored_mode_fast_path_fc2(self):
        """uncensored_mode=True + FC2 前綴 → D2PassScraper 不被呼叫"""
        mock_video = _make_video("fc2", "FC2-PPV-1234567")

        from core.scrapers.fc2 import FC2Scraper
        from core.scrapers.avsox import AVSOXScraper

        with patch.object(D2PassScraper, 'search', return_value=None) as mock_d2:
            with patch.object(HEYZOScraper, 'search', return_value=None):
                with patch.object(FC2Scraper, 'search', return_value=mock_video):
                    with patch.object(AVSOXScraper, 'search', return_value=None):
                        with patch('core.scrapers.dmm.rate_limit'):
                            results = smart_search("FC2-PPV-1234567", uncensored_mode=True)

        assert len(results) == 1
        mock_d2.assert_not_called()

    def test_primary_source_javbus_skips_dmm_shortcut(self):
        """primary_source='javbus'（預設）→ 不走 DMM Top-1 shortcut，走 search_jav(auto)"""
        mock_video = _make_video("javbus", "SONE-205")
        with patch('core.scraper.search_jav', return_value=mock_video.to_legacy_dict()) as mock_sj:
            with patch.object(DMMScraper, 'search') as mock_dmm:
                with patch('core.scraper.get_all_variant_ids', return_value=[]):
                    results = smart_search("SONE-205", proxy_url="http://proxy:8080", primary_source="javbus")
        # DMM shortcut should NOT be called directly
        mock_dmm.assert_not_called()
        # search_jav(auto) should be called
        mock_sj.assert_called()

    def test_dmm_top1_when_proxy_primary_dmm(self):
        """primary_source='dmm' + proxy → DMM Top-1 shortcut"""
        mock_video = _make_video("dmm", "SONE-205")
        with patch.object(DMMScraper, 'search', return_value=mock_video) as mock_dmm:
            with patch('core.scrapers.dmm.rate_limit'):
                results = smart_search("SONE-205", proxy_url="http://proxy:8080", primary_source="dmm")
        mock_dmm.assert_called()
        assert len(results) >= 1
        assert results[0]['_mode'] == 'exact'

    def test_primary_source_dmm_no_proxy_fallback(self):
        """primary_source='dmm' + 無 proxy → search_jav(auto) 不含 DMM"""
        mock_video = _make_video("javbus", "SONE-205")
        with patch('core.scraper.search_jav', return_value=mock_video.to_legacy_dict()) as mock_sj:
            with patch('core.scraper.get_all_variant_ids', return_value=[]):
                results = smart_search("SONE-205", proxy_url="", primary_source="dmm")
        # Should still work via search_jav(auto)
        mock_sj.assert_called()

    def test_merge_winner_first_in_order_dmm(self, monkeypatch):
        """merge text-winner = first successful source in drag-sort order (get_enabled_source_ids order).

        Class fixture sets SOURCE_ORDER (dmm first) as the enabled order.
        With dmm first in order + dmm returning data → winner _source == 'dmm'.
        This is ORDER-driven, NOT primary_source-driven (CD-61-14: primary_source
        no longer overrides merge winner; DMM Top-1 privilege lives in smart_search
        Rule 4a, not in search_jav auto-merge).
        """
        from core.scrapers.jav321 import JAV321Scraper
        from core.scrapers.javdb import JavDBScraper
        from core.scrapers.fc2 import FC2Scraper
        from core.scrapers.avsox import AVSOXScraper
        dmm_video = _make_video("dmm", "SONE-205")
        javbus_video = _make_video("javbus", "SONE-205")

        # Class autouse fixture already monkeypatches get_enabled_source_ids → SOURCE_ORDER
        # (dmm is first in SOURCE_ORDER) — no override needed here.
        with patch.object(DMMScraper, 'search', return_value=dmm_video), \
             patch.object(JavBusScraper, 'search', return_value=javbus_video), \
             patch.object(JAV321Scraper, 'search', return_value=None), \
             patch.object(JavDBScraper, 'search', return_value=None), \
             patch.object(FC2Scraper, 'search', return_value=None), \
             patch.object(AVSOXScraper, 'search', return_value=None), \
             patch('core.scrapers.dmm.rate_limit'):
            result = search_jav("SONE-205", proxy_url="http://proxy:8080")

        assert result['_source'] == 'dmm'

    def test_merge_winner_first_in_order_javbus(self, monkeypatch):
        """merge text-winner = first successful source in drag-sort order.

        Override enabled order so javbus is FIRST (dmm absent / after javbus).
        With javbus first in order + javbus returning data → winner _source == 'javbus',
        even though dmm also returns data.
        This proves drag-order determines the merge winner, NOT primary_source.
        """
        from core.scrapers.jav321 import JAV321Scraper
        from core.scrapers.javdb import JavDBScraper
        from core.scrapers.fc2 import FC2Scraper
        from core.scrapers.avsox import AVSOXScraper
        dmm_video = _make_video("dmm", "SONE-205")
        javbus_video = _make_video("javbus", "SONE-205")

        # Override the class fixture: javbus first, dmm SECOND (still fanned out + returns
        # data) — proves first-in-order beats a later successful source, not just absence.
        javbus_first_order = ['javbus', 'dmm', 'jav321', 'javdb', 'fc2', 'avsox', 'heyzo']
        monkeypatch.setattr(
            "core.scraper.get_enabled_source_ids",
            lambda availability_map=None: javbus_first_order,
        )

        with patch.object(DMMScraper, 'search', return_value=dmm_video), \
             patch.object(JavBusScraper, 'search', return_value=javbus_video), \
             patch.object(JAV321Scraper, 'search', return_value=None), \
             patch.object(JavDBScraper, 'search', return_value=None), \
             patch.object(FC2Scraper, 'search', return_value=None), \
             patch.object(AVSOXScraper, 'search', return_value=None), \
             patch('core.scrapers.dmm.rate_limit'):
            result = search_jav("SONE-205", proxy_url="http://proxy:8080")

        assert result['_source'] == 'javbus'

    def test_get_fuzzy_source_dmm_no_proxy(self):
        """primary_source='dmm' + 無 proxy → fallback to javbus"""
        from core.scraper import _get_fuzzy_source
        assert _get_fuzzy_source('dmm', '') == 'javbus'
        assert _get_fuzzy_source('dmm', None) == 'javbus'
        assert _get_fuzzy_source('dmm', 'http://proxy') == 'dmm'
        assert _get_fuzzy_source('javbus', '') == 'javbus'
        assert _get_fuzzy_source('javbus', 'http://proxy') == 'javbus'

    def test_search_actress_dmm_routing(self):
        """search_actress(primary_source='dmm', proxy_url=...) → DMM search_by_keyword_with_ids 先被呼叫"""
        from core.scraper import search_actress

        mock_video = _make_video("dmm", "SONE-205")
        mock_pairs = [("sone00205", mock_video)]

        with patch.object(DMMScraper, 'search_by_keyword_with_ids', return_value=mock_pairs) as mock_dmm_kw, \
             patch.object(DMMScraper, '_fetch_by_id', return_value=mock_video), \
             patch.object(JavBusScraper, 'get_ids_from_search', return_value=[]) as mock_jb, \
             patch('core.scrapers.dmm.rate_limit'):
            results = search_actress(
                "未歩なな",
                limit=10,
                primary_source='dmm',
                proxy_url='http://test-proxy:8080',
            )

        mock_dmm_kw.assert_called_once()
        # JavBus should NOT be called since DMM returned results
        mock_jb.assert_not_called()
        assert len(results) == 1
        assert results[0]['source'] == 'dmm'

    def test_search_actress_dmm_fallback_to_javbus(self):
        """search_actress(primary_source='dmm') → DMM 無結果時 fallback 到 JavBus"""
        from core.scraper import search_actress
        from core.scrapers.javdb import JavDBScraper

        # DMM returns nothing → should fall through to JavBus path
        with patch.object(DMMScraper, 'search_by_keyword_with_ids', return_value=[]) as mock_dmm_kw, \
             patch.object(JavBusScraper, 'get_ids_from_search', return_value=[]) as mock_jb, \
             patch.object(JavDBScraper, 'search_by_keyword', return_value=[]) as mock_javdb_kw:
            results = search_actress(
                "未歩なな",
                limit=10,
                primary_source='dmm',
                proxy_url='http://test-proxy:8080',
            )

        mock_dmm_kw.assert_called_once()
        # After DMM returns nothing, JavBus path should be tried
        mock_jb.assert_called()


# ============================================================
# TestUnknownSource (2 mock-only tests)
# ============================================================

class TestUnknownSource:
    """未知 source 驗證測試 — 確保 JavGuru 等已移除來源明確失敗"""

    def test_search_jav_unknown_source_returns_none(self):
        """search_jav 傳入未知來源（如 'javguru'）→ 立即返回 None，不走 auto mode"""
        # 確認完全不呼叫任何 scraper
        with patch.object(JavBusScraper, 'search', return_value=None) as mock_jb:
            with patch.object(DMMScraper, 'search', return_value=None) as mock_dmm:
                result = search_jav("SONE-205", source="javguru")

        assert result is None
        mock_jb.assert_not_called()
        mock_dmm.assert_not_called()

    def test_search_jav_unknown_source_no_fallback(self):
        """未知來源不應 fallback 到 auto mode — 即使 scraper 能找到結果也應被攔截"""
        mock_video = Video(
            number="SONE-205",
            title="Should Not Appear",
            actresses=[],
            date="2024-01-01",
            maker="Test",
            cover_url="",
            tags=[],
            source="javbus",
            detail_url="https://example.com",
        )

        with patch.object(JavBusScraper, 'search', return_value=mock_video):
            result = search_jav("SONE-205", source="javguru")

        assert result is None

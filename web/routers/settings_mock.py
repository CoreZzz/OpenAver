"""
Settings Mock router — Visual POC.

feature/64-settings-help-ux-polish, TASK-64b-0 (repurposed from feature/61).

Purpose: provide a clickable HTML prototype at `/settings-mock` so the user can
pin down the NEW single-column Settings IA before the real `settings.html`
rework (Phase B). It demonstrates: one long scrolling page (no tab panels),
a sticky quick-jump nav with scroll-spy highlight, 進階 settings collapsed
inline within each section, and the Metatube connection area gated behind an
enable toggle. Mock data only — no config.json read, no DB write. Hidden from
sidebar nav; not registered in capabilities.

⚠️ Lifecycle: this route is removed in Phase D (TASK-64d-1) once the real
single-column `settings.html` ships. Kept only as the Phase A/B POC surface.

⚠️ No real endpoints/tokens: per epic §1.6 local-only rule, the connection
form uses a neutral placeholder (`http://你的-metatube:8080`) and an empty
token. Never hardcode a real dev server address or bearer token here.
"""

from fastapi import APIRouter, Request

from core.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="", tags=["settings-mock"])


# 8 builtin sources — order mirrors the real feature's SourceConfig.
# Source of truth for the real feature is `core/source_config.py::get_builtin_sources()`.
# This list is a POC mock only.
_MOCK_BUILTIN_SOURCES = [
    {"id": "javbus", "name": "JavBus", "is_censored": True, "order": 0},
    {"id": "jav321", "name": "Jav321", "is_censored": True, "order": 1},
    {"id": "javdb", "name": "JavDB", "is_censored": True, "order": 2},
    {"id": "dmm", "name": "DMM", "is_censored": True, "order": 3, "requires_proxy": True},
    {"id": "d2pass", "name": "D2Pass", "is_censored": False, "order": 4},
    {"id": "heyzo", "name": "HEYZO", "is_censored": False, "order": 5},
    {"id": "fc2", "name": "FC2", "is_censored": False, "order": 6},
    {"id": "avsox", "name": "AVSOX", "is_censored": False, "order": 7},
]

# Metatube provider preview — illustrative names for the POC visual only.
# Lands in the Parts Bin (Section 2 › Metatube connection area) once connected;
# user manually promotes into the Active Row (cap 10).
_MOCK_METATUBE_SOURCES = [
    {"id": "mt_fanza", "name": "FANZA"},
    {"id": "mt_mgs", "name": "MGS"},
    {"id": "mt_duga", "name": "DUGA"},
    {"id": "mt_sod", "name": "SOD"},
    {"id": "mt_1pondo", "name": "1Pondo"},
    {"id": "mt_10musume", "name": "10musume"},
    {"id": "mt_caribbeancom", "name": "Caribbeancom"},
    {"id": "mt_heyzo", "name": "HEYZO"},
    {"id": "mt_fc2", "name": "FC2"},
    {"id": "mt_pacopacomama", "name": "Pacopacomama"},
    {"id": "mt_muramura", "name": "Muramura"},
    {"id": "mt_tokyohot", "name": "Tokyo-Hot"},
    {"id": "mt_kin8", "name": "Kin8tengoku"},
    {"id": "mt_naturalhigh", "name": "NaturalHigh"},
    {"id": "mt_xcity", "name": "X-City"},
    {"id": "mt_h4610", "name": "H4610"},
    {"id": "mt_gachinco", "name": "Gachinco"},
    {"id": "mt_javbus", "name": "JavBus"},
    {"id": "mt_arzon", "name": "Arzon"},
    {"id": "mt_avbase", "name": "AVBase"},
    {"id": "mt_aventertainments", "name": "AV-E"},
    {"id": "mt_fc2hub", "name": "FC2Hub"},
    {"id": "mt_jav321", "name": "Jav321"},
    {"id": "mt_javdb", "name": "JavDB"},
    {"id": "mt_njav", "name": "NJav"},
    {"id": "mt_prestige", "name": "Prestige"},
    {"id": "mt_sehuatang", "name": "色花堂"},
    {"id": "mt_tameikegoro", "name": "Tameike Goro"},
    {"id": "mt_xslist", "name": "XsList"},
    {"id": "mt_javlibrary", "name": "JavLibrary"},
]


@router.get("/settings-mock")
async def settings_mock_page(request: Request):
    """Visual POC for the new single-column Settings IA (TASK-64b-0)."""
    # 延遲 import 避免 circular
    from web.app import get_common_context, templates

    context = get_common_context(request)
    # 故意傳一個不存在於 sidebar 的 page key — base.html `{% if page == ... %}active`
    # 不會 match 任何 nav item，達成「隱藏於正常導航」的視覺效果。
    context["page"] = "settings-mock"
    context["mock_builtin_sources"] = _MOCK_BUILTIN_SOURCES
    context["mock_metatube_sources"] = _MOCK_METATUBE_SOURCES
    # Cap=10 — mirrors real core/source_config.py::MAX_ENABLED_SOURCES.
    context["mock_tier1_cap"] = 10
    # Connection form placeholders — NEUTRAL only (epic §1.6 local-only rule).
    # The "連線" button is a pure Alpine state flip — no real HTTP.
    context["mock_metatube_url_placeholder"] = "http://你的-metatube:8080"
    context["mock_metatube_token_placeholder"] = ""

    return templates.TemplateResponse(request, "settings_mock.html", context)

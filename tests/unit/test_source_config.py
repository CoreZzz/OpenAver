"""TASK-61a-1: SourceConfig 模型 + helper + validator 單元測試"""
from core.scrapers.utils import SOURCE_ORDER
from core.source_config import (
    MAX_ENABLED_SOURCES,
    SourceConfig,
    build_metatube_sources,
    get_builtin_sources,
    get_source_enum,
    render_name,
    validate_source_id,
)


# ---------------------------------------------------------------------------
# MAX_ENABLED_SOURCES 常數
# ---------------------------------------------------------------------------
def test_max_enabled_sources_constant():
    assert MAX_ENABLED_SOURCES == 10


# ---------------------------------------------------------------------------
# render_name 雙模式
# ---------------------------------------------------------------------------
def test_render_name_builtin_uses_display_name_key():
    s = SourceConfig(
        id='javbus',
        type='builtin',
        display_name_key='JavBus',
        display_name_raw='ignored',
    )
    assert render_name(s) == 'JavBus'


def test_render_name_metatube_uses_display_name_raw():
    s = SourceConfig(
        id='mt-foo',
        type='metatube',
        display_name_key='ignored',
        display_name_raw='Some External Provider',
    )
    assert render_name(s) == 'Some External Provider'


# ---------------------------------------------------------------------------
# get_builtin_sources
# ---------------------------------------------------------------------------
def test_get_builtin_sources_count():
    assert len(get_builtin_sources()) == 10


def test_get_builtin_sources_ids_match_source_order():
    ids = [s.id for s in get_builtin_sources()]
    assert ids == SOURCE_ORDER


def test_get_builtin_sources_all_manual_only_false():
    assert all(s.manual_only is False for s in get_builtin_sources())


def test_get_builtin_sources_all_type_builtin():
    assert all(s.type == 'builtin' for s in get_builtin_sources())


def test_get_builtin_sources_enabled_defaults():
    by_id = {s.id: s for s in get_builtin_sources()}
    assert by_id['theporndb'].enabled is False
    assert all(s.enabled is True for sid, s in by_id.items() if sid != 'theporndb')


def test_get_builtin_sources_all_not_beta():
    assert all(s.is_beta is False for s in get_builtin_sources())


def test_get_builtin_sources_order_values():
    orders = [s.order for s in get_builtin_sources()]
    assert orders == list(range(10))


def test_get_builtin_sources_excludes_auto():
    ids = [s.id for s in get_builtin_sources()]
    assert 'auto' not in ids


def test_get_builtin_sources_display_name_key_is_brand():
    by_id = {s.id: s for s in get_builtin_sources()}
    assert by_id['javbus'].display_name_key == 'JavBus'
    assert by_id['dmm'].display_name_key == 'DMM'
    assert by_id['missav'].display_name_key == 'MissAV'
    assert by_id['theporndb'].display_name_key == 'ThePornDB'


def test_get_builtin_sources_theporndb_opt_in_config():
    by_id = {s.id: s for s in get_builtin_sources()}
    tpdb = by_id['theporndb']
    assert tpdb.enabled is False
    assert tpdb.is_censored is False
    assert tpdb.requires_proxy is False
    assert tpdb.config == {'api_token': ''}


# ---------------------------------------------------------------------------
# validate_source_id
# ---------------------------------------------------------------------------
def test_validate_source_id_known_builtins():
    for sid in SOURCE_ORDER:
        assert validate_source_id(sid) is True


def test_validate_source_id_auto():
    assert validate_source_id('auto') is True


def test_validate_source_id_unknown():
    assert validate_source_id('foobar') is False


def test_validate_source_id_empty():
    assert validate_source_id('') is False


# ---------------------------------------------------------------------------
# manual_only default
# ---------------------------------------------------------------------------
def test_manual_only_defaults_false():
    s = SourceConfig(id='x', type='builtin', display_name_key='X')
    assert s.manual_only is False


# ---------------------------------------------------------------------------
# is_censored computed field
# ---------------------------------------------------------------------------
def test_is_censored_builtin_censored():
    s = SourceConfig(id='dmm', type='builtin', display_name_key='DMM')
    assert s.is_censored is True


def test_is_censored_builtin_censored_javbus():
    s = SourceConfig(id='javbus', type='builtin', display_name_key='JavBus')
    assert s.is_censored is True


def test_is_censored_builtin_censored_missav():
    s = SourceConfig(id='missav', type='builtin', display_name_key='MissAV')
    assert s.is_censored is True


def test_is_censored_builtin_uncensored_fc2():
    s = SourceConfig(id='fc2', type='builtin', display_name_key='FC2')
    assert s.is_censored is False


def test_is_censored_builtin_uncensored_d2pass():
    s = SourceConfig(id='d2pass', type='builtin', display_name_key='D2Pass')
    assert s.is_censored is False


def test_is_censored_builtin_uncensored_theporndb():
    s = SourceConfig(id='theporndb', type='builtin', display_name_key='ThePornDB')
    assert s.is_censored is False


def test_is_censored_builtin_unknown_id_conservative():
    s = SourceConfig(id='mystery', type='builtin', display_name_key='Mystery')
    assert s.is_censored is True


def test_is_censored_metatube_uncensored():
    s = SourceConfig(
        id='mt-1',
        type='metatube',
        display_name_key='',
        display_name_raw='MT One',
        config={'censored_type': 'uncensored'},
    )
    assert s.is_censored is False


def test_is_censored_metatube_censored():
    s = SourceConfig(
        id='mt-2',
        type='metatube',
        display_name_key='',
        display_name_raw='MT Two',
        config={'censored_type': 'censored'},
    )
    assert s.is_censored is True


def test_is_censored_metatube_missing_censored_type_conservative():
    s = SourceConfig(
        id='mt-3',
        type='metatube',
        display_name_key='',
        display_name_raw='MT Three',
        config={},
    )
    assert s.is_censored is True


def test_is_censored_metatube_invalid_censored_type_conservative():
    s = SourceConfig(
        id='mt-4',
        type='metatube',
        display_name_key='',
        display_name_raw='MT Four',
        config={'censored_type': 'banana'},
    )
    assert s.is_censored is True


# ---------------------------------------------------------------------------
# get_source_enum（TASK-61a-4）
# ---------------------------------------------------------------------------
def test_get_source_enum_without_auto_matches_source_order():
    assert get_source_enum() == list(SOURCE_ORDER)
    assert get_source_enum(include_auto=False) == list(SOURCE_ORDER)


def test_get_source_enum_without_auto_excludes_auto():
    assert 'auto' not in get_source_enum(include_auto=False)


def test_get_source_enum_with_auto_prepends_auto():
    assert get_source_enum(include_auto=True) == ['auto', *SOURCE_ORDER]
    assert get_source_enum(include_auto=True)[0] == 'auto'


def test_get_source_enum_returns_list():
    assert isinstance(get_source_enum(), list)
    assert isinstance(get_source_enum(include_auto=True), list)


# ---------------------------------------------------------------------------
# A. display_name_key=None 可建構（TASK-63a-1 CD-63a-1）
# ---------------------------------------------------------------------------
def test_display_name_key_none_explicit():
    """display_name_key=None 時不應 raise（改 Optional 後合法）"""
    s = SourceConfig(
        id='metatube:FANZA',
        type='metatube',
        display_name_raw='FANZA',
        display_name_key=None,
    )
    assert s.display_name_key is None


def test_display_name_key_omitted_defaults_none():
    """display_name_key omitted → default None，不應 raise"""
    s = SourceConfig(
        id='metatube:X',
        type='metatube',
        display_name_raw='X',
    )
    assert s.display_name_key is None


# ---------------------------------------------------------------------------
# B. requires_proxy default / builtin DMM / builtin 其餘（CD-63a-3）
# ---------------------------------------------------------------------------
def test_requires_proxy_default_false():
    """新建 SourceConfig 不帶 requires_proxy → 預設 False"""
    s = SourceConfig(id='x', type='metatube', display_name_raw='X')
    assert s.requires_proxy is False


def test_requires_proxy_dmm_true():
    """get_builtin_sources() 中 id='dmm' → requires_proxy is True"""
    by_id = {s.id: s for s in get_builtin_sources()}
    assert by_id['dmm'].requires_proxy is True


def test_requires_proxy_other_builtins_false():
    """get_builtin_sources() 中非 DMM builtin → requires_proxy is False"""
    by_id = {s.id: s for s in get_builtin_sources()}
    for sid in ('javbus', 'jav321', 'javdb', 'missav', 'd2pass', 'heyzo', 'fc2', 'avsox', 'theporndb'):
        assert by_id[sid].requires_proxy is False, f"{sid} should have requires_proxy=False"


# ---------------------------------------------------------------------------
# C. validate_source_id metatube 放行（63c，CD-63c-1）
# ---------------------------------------------------------------------------
def test_validate_source_id_metatube_fanza_true():
    """63c 放行：metatube:FANZA → True"""
    assert validate_source_id('metatube:FANZA') is True


def test_validate_source_id_metatube_anything_true():
    """任何 metatube:<非空> 開頭的 id 在 63c 放行 → True"""
    assert validate_source_id('metatube:anything') is True


def test_validate_source_id_metatube_empty_suffix_false():
    """metatube:（空後綴）→ False（非空後綴守衛）"""
    assert validate_source_id('metatube:') is False


def test_validate_source_id_metatube_no_colon_false():
    """'metatube'（無冒號）→ False"""
    assert validate_source_id('metatube') is False


def test_validate_source_id_auto_not_regressed():
    """'auto' 不回歸，仍應為 True"""
    assert validate_source_id('auto') is True


def test_validate_source_id_builtins_not_regressed():
    """8 個 builtin id 不回歸，全應為 True"""
    for sid in SOURCE_ORDER:
        assert validate_source_id(sid) is True


# ---------------------------------------------------------------------------
# D. build_metatube_sources() 各欄正確（CD-63a-5）
# ---------------------------------------------------------------------------
def test_build_metatube_sources_basic_fields():
    """build_metatube_sources 回傳 3 筆，各欄正確"""
    results = build_metatube_sources(['FANZA', 'HEYZO', 'UnknownX'])
    assert len(results) == 3
    for s in results:
        assert s.type == 'metatube'
        assert s.enabled is False
        assert s.manual_only is False
        assert s.id == f'metatube:{s.display_name_raw}'


def test_build_metatube_sources_censored_type_fanza():
    """FANZA → censored_type='censored'"""
    by_name = {s.display_name_raw: s for s in build_metatube_sources(['FANZA', 'HEYZO', 'UnknownX'])}
    assert by_name['FANZA'].config['censored_type'] == 'censored'


def test_build_metatube_sources_censored_type_heyzo():
    """HEYZO → censored_type='uncensored'"""
    by_name = {s.display_name_raw: s for s in build_metatube_sources(['FANZA', 'HEYZO', 'UnknownX'])}
    assert by_name['HEYZO'].config['censored_type'] == 'uncensored'


def test_build_metatube_sources_censored_type_unknown_conservative():
    """UnknownX（不在 map）→ censored_type='censored'（保守）"""
    by_name = {s.display_name_raw: s for s in build_metatube_sources(['FANZA', 'HEYZO', 'UnknownX'])}
    assert by_name['UnknownX'].config['censored_type'] == 'censored'


# ---------------------------------------------------------------------------
# E. canonical order（不吃輸入順序）（CD-63a-5）
# ---------------------------------------------------------------------------
def test_canonical_order_fanza_before_heyzo():
    """FANZA 在 METATUBE_PROVIDER_ORDER 靠前 → FANZA.order < HEYZO.order"""
    by_name = {s.display_name_raw: s for s in build_metatube_sources(['HEYZO', 'FANZA'])}
    assert by_name['FANZA'].order < by_name['HEYZO'].order


def test_canonical_order_unknown_alphabetical():
    """未知 provider AAA / ZZZ → AAA.order < ZZZ.order（字母序末尾）"""
    by_name = {s.display_name_raw: s for s in build_metatube_sources(['ZZZ', 'AAA'])}
    assert by_name['AAA'].order < by_name['ZZZ'].order


def test_canonical_order_known_before_unknown():
    """已知 FANZA 排在未知 ZZZ 前"""
    by_name = {s.display_name_raw: s for s in build_metatube_sources(['FANZA', 'ZZZ'])}
    assert by_name['FANZA'].order < by_name['ZZZ'].order


# ---------------------------------------------------------------------------
# F. is_censored computed 驗 builder 填值正確（CD-63a-4）
# ---------------------------------------------------------------------------
def test_is_censored_metatube_config_censored_true():
    """metatube instance with censored_type='censored' → is_censored True"""
    s = SourceConfig(
        id='metatube:T1',
        type='metatube',
        display_name_raw='T1',
        config={'censored_type': 'censored'},
    )
    assert s.is_censored is True


def test_is_censored_metatube_config_uncensored_false():
    """metatube instance with censored_type='uncensored' → is_censored False"""
    s = SourceConfig(
        id='metatube:T2',
        type='metatube',
        display_name_raw='T2',
        config={'censored_type': 'uncensored'},
    )
    assert s.is_censored is False


def test_is_censored_builder_fanza_true():
    """build_metatube_sources(['FANZA']) → FANZA.is_censored is True"""
    sources = build_metatube_sources(['FANZA'])
    assert sources[0].is_censored is True


def test_is_censored_builder_heyzo_false():
    """build_metatube_sources(['HEYZO']) → HEYZO.is_censored is False"""
    sources = build_metatube_sources(['HEYZO'])
    assert sources[0].is_censored is False


# ---------------------------------------------------------------------------
# G. requires_proxy derives on reconstruct from stored dict（Fix 1 / P1a）
# ---------------------------------------------------------------------------

def test_requires_proxy_derives_on_reconstruct_from_stored_dict_dmm():
    """舊 dict（無 requires_proxy key）重建 → builtin dmm 應 derive True"""
    old_dict = {
        'id': 'dmm',
        'type': 'builtin',
        'display_name_key': 'DMM',
        # 故意不含 requires_proxy key（模擬舊 config.json 存的 entry）
    }
    s = SourceConfig(**old_dict)
    assert s.requires_proxy is True


def test_requires_proxy_derives_on_reconstruct_from_stored_dict_javbus():
    """舊 dict（無 requires_proxy key）重建 → builtin javbus 應 derive False"""
    old_dict = {
        'id': 'javbus',
        'type': 'builtin',
        'display_name_key': 'JavBus',
        # 故意不含 requires_proxy key
    }
    s = SourceConfig(**old_dict)
    assert s.requires_proxy is False


def test_requires_proxy_metatube_explicit_false_not_derived():
    """metatube source 傳 requires_proxy=False 不被 derive 邏輯影響（type != builtin）"""
    s = SourceConfig(
        id='metatube:FANZA',
        type='metatube',
        display_name_raw='FANZA',
        requires_proxy=False,
        config={'censored_type': 'censored'},
    )
    assert s.requires_proxy is False

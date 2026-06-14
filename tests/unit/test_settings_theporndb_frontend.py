import json
from pathlib import Path


ROOT = Path(__file__).parent.parent.parent
SETTINGS_HTML = ROOT / "web" / "templates" / "settings.html"
STATE_CONFIG_JS = ROOT / "web" / "static" / "js" / "pages" / "settings" / "state-config.js"
LOCALE_ZH_TW = ROOT / "locales" / "zh_TW.json"


def _html():
    return SETTINGS_HTML.read_text(encoding="utf-8")


def _js():
    return STATE_CONFIG_JS.read_text(encoding="utf-8")


def _locale():
    return json.loads(LOCALE_ZH_TW.read_text(encoding="utf-8"))


def test_theporndb_token_field_rendered():
    html = _html()

    assert 'x-model="form.thePornDbToken"' in html
    assert "settings.sources.theporndb_token_label" in html
    assert "settings.sources.theporndb_token_hint" in html
    assert "settings.sources.theporndb_token_placeholder" in html


def test_theporndb_token_state_saved_into_source_config():
    js = _js()

    assert "thePornDbToken: ''" in js
    assert "api_token: this.form.thePornDbToken.trim()" in js
    assert "isThePornDbAvailable" in js
    assert "theporndb_token_required" in js


def test_theporndb_source_mode_is_opt_in_uncensored():
    js = _js()

    assert "UNCENSORED_SOURCES: ['d2pass', 'heyzo', 'tokyohot', 'fc2', 'avsox', 'theporndb']" in js
    assert "s.id !== 'theporndb' || this.isThePornDbAvailable()" in js


def test_theporndb_locale_keys_exist():
    source_keys = _locale()["settings"]["sources"]

    for key in [
        "theporndb_token_label",
        "theporndb_token_hint",
        "theporndb_token_placeholder",
        "theporndb_token_required",
    ]:
        assert source_keys.get(key)

"""61c-3: 無碼模式 computed getter/setter 的後端 round-trip 驗證。

`uncensoredMode` 本身是前端 computed（JS getter/setter），無法在 pytest 直接執行。
本檔在 PERSISTENCE / round-trip 層驗證那些操作的「持久化結果」：
  - frontend saveConfig 把 4 個有碼來源 enabled=false + mirror uncensored_mode_enabled=True 一併 PUT
    → GET 回來時 sources 狀態與 mirror 一致，且後端 is_uncensored_mode_effective 同口徑回 True。
  - 重新啟用任一有碼來源（如 javbus）→ mirror False + effective False。

對齊 CD-61-7b：mirror 持續寫入，與 sources 推導保持一致（never diverge）。
"""
from core import source_settings

CENSORED = ['dmm', 'javbus', 'jav321', 'javdb', 'missav']
UNCENSORED = ['d2pass', 'heyzo', 'fc2', 'avsox']


def _set_enabled(config, predicate):
    """依 predicate(id)->bool 設定每個 source 的 enabled。"""
    for s in config["sources"]:
        s["enabled"] = predicate(s["id"])


def test_uncensored_on_roundtrip_mirror_and_sources(client):
    """無碼模式 ON：4 有碼 disabled + mirror=True；無碼 4 個維持 enabled；effective=True。"""
    config = client.get("/api/config").json()["data"]

    # 模擬 frontend setter(true)：停用 4 個有碼來源；無碼來源保持啟用。
    _set_enabled(config, lambda sid: sid not in CENSORED)
    # 模擬 saveConfig mirror：uncensored_mode_enabled = this.uncensoredMode (= True)
    config["search"]["uncensored_mode_enabled"] = True

    assert client.put("/api/config", json=config).status_code == 200

    saved = client.get("/api/config").json()["data"]
    by_id = {s["id"]: s for s in saved["sources"]}

    # 4 個有碼全 disabled
    for sid in CENSORED:
        assert by_id[sid]["enabled"] is False, f"{sid} should be disabled"
    # 無碼 4 個維持啟用
    for sid in UNCENSORED:
        assert by_id[sid]["enabled"] is True, f"{sid} should stay enabled"

    # mirror 一致
    assert saved["search"]["uncensored_mode_enabled"] is True
    # 後端單一真理來源同口徑（all censored disabled → True）
    assert source_settings.is_uncensored_mode_effective(saved) is True


def test_reenable_one_censored_turns_mirror_off(client):
    """重新啟用任一有碼（javbus）→ getter 回 false 對應：mirror=False + effective=False。"""
    config = client.get("/api/config").json()["data"]

    # 起點：無碼模式 ON（4 有碼 disabled）
    _set_enabled(config, lambda sid: sid not in CENSORED)
    config["search"]["uncensored_mode_enabled"] = True
    client.put("/api/config", json=config)

    # 重新啟用 javbus（模擬點有碼 pill）→ frontend getter 變 False → saveConfig 寫 mirror=False
    config = client.get("/api/config").json()["data"]
    for s in config["sources"]:
        if s["id"] == "javbus":
            s["enabled"] = True
    config["search"]["uncensored_mode_enabled"] = False  # computed mirror
    client.put("/api/config", json=config)

    saved = client.get("/api/config").json()["data"]
    by_id = {s["id"]: s for s in saved["sources"]}

    assert by_id["javbus"]["enabled"] is True
    assert saved["search"]["uncensored_mode_enabled"] is False
    assert source_settings.is_uncensored_mode_effective(saved) is False

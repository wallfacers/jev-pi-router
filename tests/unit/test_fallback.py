"""兜底状态机单测（FR-009/010；data-model.md 状态转移；quickstart V4）。"""

import json

from jev_pi_router.fallback import BreakerRegistry, quality_escalated


def test_breaker_opens_after_threshold():
    clock = {"now": 1000.0}
    registry = BreakerRegistry(threshold=3, cooldown_sec=300, now_fn=lambda: clock["now"])
    assert registry.available("deepseek")
    assert registry.record_failure("deepseek", "timeout", ts="t1") == []
    assert registry.record_failure("deepseek", "timeout", ts="t2") == []
    events = registry.record_failure("deepseek", "timeout", ts="t3")
    assert [e["type"] for e in events] == ["breaker_open"]
    assert not registry.available("deepseek")


def test_breaker_half_open_after_cooldown_then_closes():
    clock = {"now": 1000.0}
    registry = BreakerRegistry(threshold=1, cooldown_sec=300, now_fn=lambda: clock["now"])
    registry.record_failure("glm", "http_5xx", ts="t1")
    assert not registry.available("glm")
    clock["now"] = 1301.0                      # 冷却到期
    assert registry.available("glm")           # open → half_open
    events = registry.record_success("glm", ts="t4")
    assert [e["type"] for e in events] == ["breaker_close"]
    assert registry.available("glm") and registry.state["glm"].state == "closed"


def test_breaker_half_open_failure_reopens():
    clock = {"now": 1000.0}
    registry = BreakerRegistry(threshold=2, cooldown_sec=300, now_fn=lambda: clock["now"])
    registry.record_failure("relay", "quota", ts="t1")
    registry.record_failure("relay", "quota", ts="t2")
    clock["now"] = 1400.0
    assert registry.available("relay")         # half_open
    events = registry.record_failure("relay", "quota", ts="t3")
    assert [e["type"] for e in events] == ["breaker_open"]   # half_open 失败立即重开
    assert not registry.available("relay")


def test_fault_transfer_budget():
    # 契约：max_attempts=2 → 同档换厂商 2 次内成功（这里验证状态机不阻碍转移语义）
    clock = {"now": 0.0}
    registry = BreakerRegistry(threshold=3, cooldown_sec=300, now_fn=lambda: clock["now"])
    registry.record_failure("deepseek", "timeout", ts="t1")   # 1 次失败不开断
    assert registry.available("deepseek")      # 仍可作 fallback 目标


def test_quality_escalation_threshold():
    assert not quality_escalated(0, 2)
    assert not quality_escalated(1, 2)
    assert quality_escalated(2, 2)             # 2 轮 review_reject → 升级（FR-010）
    assert quality_escalated(3, 2)


# ── v1.4 条目级滑窗冷却（api_ref 粒度；bug 2026-09-24 relay 空响应死循环）────

API_REF = "relay/cmd-deepseek-v4.1-flash"


def _window_registry(clock, **overrides):
    params = {"threshold": 3, "cooldown_sec": 300, "window_sec": 3600,
              "window_failures": 2, "api_ref_cooldown_sec": 300,
              "now_fn": lambda: clock["now"]}
    params.update(overrides)
    return BreakerRegistry(**params)


def test_api_ref_cooldown_after_window_failures():
    """滑窗内 2 次失败（默认阈值）→ 条目冷却；1 次仅降权不冷却。"""
    clock = {"now": 1000.0}
    registry = _window_registry(clock)
    assert registry.record_failure("relay", "empty_response", ts="t1", api_ref=API_REF) == []
    assert registry.api_ref_available(API_REF)          # 1 次：可用（仅降权）
    assert registry.demotion_set() == {API_REF}
    events = registry.record_failure("relay", "empty_response", ts="t2", api_ref=API_REF)
    assert [e["type"] for e in events] == ["api_ref_cooldown"]
    assert events[0]["to_model"] == API_REF and events[0]["trigger"] == "empty_response"
    assert not registry.api_ref_available(API_REF)
    assert API_REF not in registry.demotion_set()       # 冷却中已被排除，不再降权


def test_api_ref_cooldown_duplicate_failure_no_reevent():
    """冷却中重复失败不重复开断/发事件（避免事件风暴）。"""
    clock = {"now": 1000.0}
    registry = _window_registry(clock)
    registry.record_failure("relay", "empty_response", ts="t1", api_ref=API_REF)
    registry.record_failure("relay", "empty_response", ts="t2", api_ref=API_REF)
    events = registry.record_failure("relay", "empty_response", ts="t3", api_ref=API_REF)
    assert "api_ref_cooldown" not in [e["type"] for e in events]   # 仅 vendor 级 breaker_open


def test_api_ref_cooldown_expires_but_window_persists():
    """冷却到期回池但窗口内仍有失败 → 仍降权；再 1 次失败即立即再冷却（惯犯越犯越短命）。"""
    clock = {"now": 1000.0}
    registry = _window_registry(clock)
    registry.record_failure("relay", "empty_response", ts="t1", api_ref=API_REF)
    registry.record_failure("relay", "empty_response", ts="t2", api_ref=API_REF)
    clock["now"] = 1400.0                        # 冷却到期（300s）
    assert registry.api_ref_available(API_REF)   # 回池
    assert API_REF in registry.demotion_set()    # 但窗口内失败记录未滚出 → 降权
    events = registry.record_failure("relay", "timeout", ts="t3", api_ref=API_REF)
    types = [e["type"] for e in events]              # 两级并存：vendor 熔断 + 条目冷却
    assert "api_ref_cooldown" in types               # 1 次即再冷却
    assert "breaker_open" in types


def test_api_ref_window_rolls_off():
    """滑窗外的时间戳滚出，不累积（t0 与 t0+window 之后的失败不算同窗）。"""
    clock = {"now": 1000.0}
    registry = _window_registry(clock)
    registry.record_failure("relay", "timeout", ts="t1", api_ref=API_REF)
    clock["now"] = 1000.0 + 3600 + 1
    assert registry.record_failure("relay", "timeout", ts="t2", api_ref=API_REF) == []
    assert registry.api_ref_available(API_REF)


def test_api_ref_success_with_ref_recovers():
    """带 api_ref 的成功回报 → api_ref_recover 事件 + 清窗 + 解冷。"""
    clock = {"now": 1000.0}
    registry = _window_registry(clock)
    registry.record_failure("relay", "empty_response", ts="t1", api_ref=API_REF)
    registry.record_failure("relay", "empty_response", ts="t2", api_ref=API_REF)
    events = registry.record_success("relay", ts="t3", api_ref=API_REF)
    assert [e["type"] for e in events] == ["api_ref_recover"]
    assert registry.api_ref_available(API_REF)
    assert registry.demotion_set() == set()      # 窗口已清


def test_api_ref_success_without_ref_keeps_entry_state():
    """不带 api_ref 的成功只闭合 vendor 级——vendor 级成功不能证明该端点恢复。"""
    clock = {"now": 1000.0}
    registry = _window_registry(clock)
    registry.record_failure("relay", "empty_response", ts="t1", api_ref=API_REF)
    registry.record_failure("relay", "empty_response", ts="t2", api_ref=API_REF)
    events = registry.record_success("relay", ts="t3")
    assert "api_ref_recover" not in [e["type"] for e in events]
    assert not registry.api_ref_available(API_REF)      # 条目冷却保留


def test_demotion_set_prunes_expired_and_excludes_cooldown():
    """demotion_set：窗口外滚出、冷却中排除、冷却过期但窗口内保留。"""
    clock = {"now": 1000.0}
    registry = _window_registry(clock)
    registry.record_failure("relay", "timeout", ts="t1", api_ref=API_REF)
    other = "glm/glm-5.3-flash"
    registry.record_failure("glm", "timeout", ts="t2", api_ref=other)
    registry.record_failure("glm", "timeout", ts="t3", api_ref=other)   # other 冷却
    assert registry.demotion_set() == {API_REF}
    clock["now"] = 1000.0 + 3600 + 1
    assert registry.demotion_set() == set()             # 全部滚出/过期


def test_state_roundtrip_backward_compat(tmp_path):
    """state.json：breakers 键形状不变（既有断言依赖）、api_refs 新增顶层键、旧格式可加载。"""
    clock = {"now": 1000.0}
    registry = _window_registry(clock)
    registry.record_failure("relay", "timeout", ts="t1", api_ref=API_REF)
    registry.save()
    state_path = tmp_path / "home" / "state.json"
    data = json.loads(state_path.read_text(encoding="utf-8"))
    assert set(data["breakers"]["relay"]) == {"consecutive_failures", "state", "open_until"}
    assert data["api_refs"][API_REF]["failures"] == [1000.0]

    state_path.write_text(json.dumps({                    # 旧格式（v1.3，无 api_refs）
        "breakers": {"glm": {"consecutive_failures": 1, "state": "closed", "open_until": 0.0}}}),
        encoding="utf-8")
    legacy = _window_registry(clock)
    legacy.load()
    assert legacy.api_refs == {}                          # 缺失键静默为空
    assert legacy.state["glm"].consecutive_failures == 1


def test_save_sweeps_expired_api_ref_state(tmp_path):
    """save() 前全量清扫：窗口外且冷却已过的条目删除（state.json 有界）。"""
    clock = {"now": 1000.0}
    registry = _window_registry(clock)
    registry.record_failure("relay", "timeout", ts="t1", api_ref=API_REF)
    clock["now"] = 1000.0 + 3600 + 400
    registry.save()
    data = json.loads((tmp_path / "home" / "state.json").read_text(encoding="utf-8"))
    assert data["api_refs"] == {}

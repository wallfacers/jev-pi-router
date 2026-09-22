"""兜底状态机单测（FR-009/010；data-model.md 状态转移；quickstart V4）。"""

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

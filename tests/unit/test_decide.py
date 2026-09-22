"""decide() 兜底编排集成测试（S4 修复回归：双读兼容/熔断闭合/迁移落盘/事件回带）。"""

import json
import os
from copy import deepcopy

from jev_pi_router.decide import decide


def _req(sample_request, **overrides):
    request = deepcopy(sample_request)
    request.update(overrides)
    return request


def _failures(n=3, vendor="deepseek", trigger="timeout"):
    return [{"vendor": vendor, "trigger": trigger}] * n


def test_vendor_failures_dual_read_from_history(sample_request, config_path):
    """旧模板：故障放 history.vendor_failures 也必须生效（schema 兼容，S4-1）。"""
    request = _req(sample_request, history={
        "review_fail_count": 0, "previous_models": [],
        "vendor_failures": _failures(),
    })
    response = decide(request, engine="rules", config_path=config_path)
    types = [e["type"] for e in response["fallback_events"]]
    assert "breaker_open" in types
    assert (response["chosen"] or {}).get("vendor") != "deepseek"


def test_response_carries_fallback_events(sample_request, config_path):
    """响应体回带 fallback_events（S4 观测，不必翻日志）。"""
    response = decide(_req(sample_request, vendor_failures=_failures()),
                      engine="rules", config_path=config_path)
    assert response["fallback_events"]


def test_vendor_success_closes_breaker(sample_request, config_path):
    """vendor_success 上报 → breaker_close 事件、厂商恢复可派（S4-2）。"""
    decide(_req(sample_request, vendor_failures=_failures()),
           engine="rules", config_path=config_path)
    response = decide(_req(sample_request, vendor_success=[{"vendor": "deepseek"}]),
                      engine="rules", config_path=config_path)
    assert "breaker_close" in [e["type"] for e in response["fallback_events"]]
    # 熔断闭合后 deepseek 重回可派池（flash 池首选）
    assert (response["chosen"] or {}).get("vendor") == "deepseek"


def test_half_open_migration_persisted(sample_request, config_path):
    """冷却到期后决策即持久化 open→half_open（S4-2 迁移落盘）。"""
    decide(_req(sample_request, vendor_failures=_failures()),
           engine="rules", config_path=config_path)
    state_path = os.path.join(os.environ["JEV_PI_ROUTER_HOME"], "state.json")
    state = json.loads(open(state_path, encoding="utf-8").read())
    state["breakers"]["deepseek"]["open_until"] = 0.0  # 已过期
    json.dump(state, open(state_path, "w", encoding="utf-8"))
    decide(_req(sample_request), engine="rules", config_path=config_path)
    state = json.loads(open(state_path, encoding="utf-8").read())
    assert state["breakers"]["deepseek"]["state"] == "half_open"

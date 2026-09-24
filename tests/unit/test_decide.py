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


# ── 002 US1：qianwenai 四模型按档位进入路由池（FR-003/010、SC-001）──────────────────

QNA_FLASH_PREV = ["deepseek/deepseek-flash", "glm/glm-5.3-flash",
                  "relay/cmd-deepseek-v4.1-flash", "opencode-go/deepseek-flash"]


def test_implement_dispatches_to_qna_flash(sample_request, config_path):
    """US1-2：排除既有 flash 条目后，implement 派发到 qianwenai/qwen3.8-flash（池顺序轮询）。"""
    request = _req(sample_request, history={"review_fail_count": 0, "previous_models": QNA_FLASH_PREV})
    response = decide(request, engine="rules", config_path=config_path)
    assert response["chosen"]["api_ref"] == "qianwenai/qwen3.8-flash"
    assert response["chosen"]["pool"] == "flash"


def test_plan_dispatches_to_qna_strong(sample_request, config_path):
    """US1-3：排除既有强条目后，plan 派发到 qianwenai/qwen3.8-max（顶级档与 mimo 同权）。"""
    request = _req(sample_request, role="plan", task_class_hint="design",
                   history={"review_fail_count": 0,
                            "previous_models": ["mimo/mimo-v2.6-pro", "glm/glm-5.3"]})
    response = decide(request, engine="rules", config_path=config_path)
    assert response["chosen"]["api_ref"] == "qianwenai/qwen3.8-max"
    fb_refs = {e["api_ref"] for e in response["fallback_order"]}
    assert "qianwenai/glm-5.3" in fb_refs   # qianwenai 强条目进入同池兜底轮换


def test_default_order_unbiased_qna_in_rotation(sample_request, config_path):
    """FR-008/SC-006 平权：默认派发首选仍为既有条目，qianwenai 全量出现在 fallback 轮换。"""
    response = decide(sample_request, engine="rules", config_path=config_path)
    assert response["chosen"]["api_ref"] == "deepseek/deepseek-flash"   # 既有池首不动
    fb_refs = {e["api_ref"] for e in response["fallback_order"]}
    assert {"qianwenai/qwen3.8-flash", "qianwenai/deepseek-v4.1-flash"} <= fb_refs


def test_disabled_qna_entry_skipped_others_unaffected(sample_request, config_path, tmp_path):
    """US1-4：qianwenai 单条目 enabled=false 不参与派发，其余 qianwenai 条目不受影响。"""
    import yaml
    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    for item in data["flash_pool"]:
        if item["api_ref"] == "qianwenai/qwen3.8-flash":
            item["enabled"] = False
    cfg = tmp_path / "router.config.yaml"
    cfg.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    request = _req(sample_request, history={"review_fail_count": 0, "previous_models": QNA_FLASH_PREV})
    response = decide(request, engine="rules", config_path=cfg)
    assert response["chosen"]["api_ref"] == "qianwenai/deepseek-v4.1-flash"   # 跳过停用条目轮到下一条


# ── 002 review 修复回归 ─────────────────────────────────────────────────────

def test_jev_ref_carries_cost_and_cache_signal():
    """review F1：Jev 候选引用须携带 cost_hint/cache_passthrough（否则恒渲染默认值，信号失效）。"""
    from jev_pi_router.config import ModelEntry
    from jev_pi_router.decide import jev_ref
    e = ModelEntry(vendor="qianwenai", model="qwen3.8-flash", api_ref="qianwenai/qwen3.8-flash",
                   pool="flash", cost_hint=0.15, cache_passthrough="full", family="qwen3.8-flash")
    ref = jev_ref(e)
    assert ref["cost_hint"] == 0.15 and ref["cache_passthrough"] == "full"
    assert ref["family"] == "qwen3.8-flash" and ref["api_ref"] == "qianwenai/qwen3.8-flash"


# ── v1.4 空响应触发器 + api_ref 滑窗冷却（bug 2026-09-24 relay 死循环回归）────

RELAY_REF = "relay/cmd-deepseek-v4.1-flash"
# 排除既有前四条 flash，只剩 relay 与 qianwenai/deepseek-v4.1-flash（同 family）竞争
BEFORE_RELAY = ["deepseek/deepseek-flash", "glm/glm-5.3-flash",
                "opencode-go/deepseek-flash", "qianwenai/qwen3.8-flash"]


def test_empty_response_window_cooldown_excludes_entry(sample_request, config_path):
    """empty_response ×2（带 api_ref）→ 条目冷却事件 + 该条目退出 chosen/fallback_order。"""
    request = _req(sample_request, vendor_failures=[
        {"vendor": "relay", "api_ref": RELAY_REF, "trigger": "empty_response"}] * 2)
    response = decide(request, engine="rules", config_path=config_path)
    assert "api_ref_cooldown" in [e["type"] for e in response["fallback_events"]]
    assert (response["chosen"] or {}).get("api_ref") != RELAY_REF
    assert RELAY_REF not in {e["api_ref"] for e in response["fallback_order"]}


def test_single_error_demotes_relay_to_same_family_alternative(sample_request, config_path):
    """bug 主场景回归：relay 1 次失败（旧模板不带 api_ref → vendor/model 合成）→ 降权，
    同 family 健康替代 qianwenai/deepseek-v4.1-flash 被优先选中（修复前会选 relay）。"""
    request = _req(sample_request,
                   vendor_failures=[{"vendor": "relay", "model": "cmd-deepseek-v4.1-flash",
                                     "trigger": "error"}],
                   history={"review_fail_count": 0, "previous_models": BEFORE_RELAY})
    response = decide(request, engine="rules", config_path=config_path)
    assert response["chosen"]["api_ref"] == "qianwenai/deepseek-v4.1-flash"
    assert "近期失败条目已降权" in response["rationale"]


def test_no_failure_state_keeps_pool_order(sample_request, config_path):
    """降权恒等性守卫：无故障状态时池序完全不变（池首不动）。"""
    response = decide(_req(sample_request), engine="rules", config_path=config_path)
    assert response["chosen"]["api_ref"] == "deepseek/deepseek-flash"


def test_legacy_failures_without_ref_stay_vendor_level(sample_request, config_path):
    """旧模板兼容：无 api_ref 且无 model → 仅 vendor 级熔断，不产生任何条目级状态。"""
    response = decide(_req(sample_request, vendor_failures=_failures()),
                      engine="rules", config_path=config_path)
    assert "breaker_open" in [e["type"] for e in response["fallback_events"]]
    state = json.loads(open(os.path.join(os.environ["JEV_PI_ROUTER_HOME"], "state.json"),
                            encoding="utf-8").read())
    assert state["api_refs"] == {}


def test_rate_limit_not_counted_in_window(sample_request, config_path):
    """429 分流：rate_limit 不计入条目滑窗（重试即可），条目照常在池内。"""
    request = _req(sample_request, vendor_failures=[
        {"vendor": "relay", "api_ref": RELAY_REF, "trigger": "rate_limit"}] * 2)
    response = decide(request, engine="rules", config_path=config_path)
    assert response["fallback_events"] == []
    assert RELAY_REF in {e["api_ref"] for e in response["fallback_order"]}


def test_vendor_success_with_api_ref_restores_entry(sample_request, config_path):
    """带 api_ref 的成功回报 → api_ref_recover + 条目重回可派轮换。"""
    decide(_req(sample_request, vendor_failures=[
        {"vendor": "relay", "api_ref": RELAY_REF, "trigger": "empty_response"}] * 2),
        engine="rules", config_path=config_path)
    response = decide(_req(sample_request, vendor_success=[{"vendor": "relay", "api_ref": RELAY_REF}]),
                      engine="rules", config_path=config_path)
    assert "api_ref_recover" in [e["type"] for e in response["fallback_events"]]
    assert RELAY_REF in {e["api_ref"] for e in response["fallback_order"]}


def test_pool_exhaustion_demotes_but_does_not_exclude(sample_request, config_path):
    """兜底不穿透排除：flash 池全条目冷却 → chosen 仍非 None（不破坏 CLI 契约）。"""
    import yaml
    refs = [i["api_ref"] for i in
            yaml.safe_load(config_path.read_text(encoding="utf-8"))["flash_pool"]]
    failures = [{"vendor": r.split("/")[0], "api_ref": r, "trigger": "empty_response"}
                for r in refs for _ in range(2)]
    response = decide(_req(sample_request, vendor_failures=failures),
                      engine="rules", config_path=config_path)
    assert response["chosen"] is not None
    assert response["chosen"]["pool"] == "flash"

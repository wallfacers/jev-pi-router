"""decide() 兜底编排集成测试（S4 修复回归：双读兼容/熔断闭合/迁移落盘/事件回带）。"""

import json
import os
from copy import deepcopy

import pytest

from jev_pi_router.decide import UsageError, decide


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


# ── review 修复回归：Jev reviewer 消费（A）/ family 注入健壮化（B）────────────

def _fake_jev(pick_reviewer=None):
    def fake(task_brief, risk_tags, implement_candidates, reviewer_candidates, timeout_ms=2000):
        out = {"task_class": "implement", "complexity": "low"}
        if pick_reviewer is not None:
            out["reviewer_ref"] = pick_reviewer   # 显式指定（含模拟 Jev 异常越池返回）
        elif reviewer_candidates:
            out["reviewer_ref"] = reviewer_candidates[0]["api_ref"]
        return out
    return fake


def test_jev_reviewer_accepted_when_valid(sample_request, config_path, monkeypatch):
    """fix A：Jev 的合法 reviewer 直接采纳（此前被静默丢弃）。"""
    from jev_pi_router import jev_client
    monkeypatch.setattr(jev_client, "decide", _fake_jev("glm/glm-5.3"))
    request = _req(sample_request,
                   implementer={"vendor": "deepseek", "model": "deepseek-flash",
                                "api_ref": "deepseek/deepseek-flash"})
    response = decide(request, engine="jev", config_path=config_path)
    rp = response["review_plan"]
    assert rp["code_reviewer"]["api_ref"] == "glm/glm-5.3"
    assert rp["degrade"] is False
    assert "[pairing-corrected]" not in response["rationale"]


def test_jev_same_origin_reviewer_corrected(sample_request, config_path, monkeypatch):
    """fix A/002 契约 §4：Jev 返回同源 reviewer → 丢弃、三级重选、[pairing-corrected]。"""
    from jev_pi_router import jev_client
    monkeypatch.setattr(jev_client, "decide", _fake_jev("glm/glm-5.3"))  # 与 producer 同 family
    request = _req(sample_request,
                   implementer={"vendor": "qianwenai", "model": "glm-5.3",
                                "api_ref": "qianwenai/glm-5.3"})
    response = decide(request, engine="jev", config_path=config_path)
    rp = response["review_plan"]
    assert "[pairing-corrected]" in response["rationale"]
    assert rp["code_reviewer"]["api_ref"] == "mimo/mimo-v2.6-pro"   # 真异源重选
    assert rp["degrade"] is False


def test_jev_same_origin_reviewer_fallback_degrades(sample_request, config_path, monkeypatch, tmp_path):
    """fix A：真异源枯竭时 Jev 非法选择的重选落到同源兜底（degrade + 归因 same_origin）。"""
    import yaml
    from jev_pi_router import jev_client
    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    data["strong_pool"] = [i for i in data["strong_pool"]
                           if i["api_ref"] in ("glm/glm-5.3", "qianwenai/glm-5.3")]
    cfg = tmp_path / "router.config.yaml"
    cfg.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    monkeypatch.setattr(jev_client, "decide", _fake_jev("glm/glm-5.3"))
    request = _req(sample_request,
                   implementer={"vendor": "qianwenai", "model": "glm-5.3",
                                "api_ref": "qianwenai/glm-5.3"})
    response = decide(request, engine="jev", config_path=cfg)
    rp = response["review_plan"]
    assert "[pairing-corrected]" in response["rationale"]
    assert rp["degrade"] is True and rp["degrade_reason"] == "same_origin"
    assert rp["code_reviewer"]["api_ref"] == "glm/glm-5.3"
    assert "degrade_same_origin" in [e["type"] for e in response["fallback_events"]]


def test_jev_reviewer_not_requested_without_producer(sample_request, config_path, monkeypatch):
    """fix A：无 implementer 时 reviewer 问项不再发起（答案无消费方）。"""
    from jev_pi_router import jev_client
    seen = {}

    def fake(task_brief, risk_tags, implement_candidates, reviewer_candidates, timeout_ms=2000):
        seen["reviewers"] = reviewer_candidates
        return {"task_class": "implement", "complexity": "low"}

    monkeypatch.setattr(jev_client, "decide", fake)
    decide(sample_request, engine="jev", config_path=config_path)   # implementer=None
    assert seen["reviewers"] == []


def test_producer_family_injection_loose_match(sample_request, config_path):
    """fix B：api_ref 缺失/大小写混乱/别名时按 vendor/model 兜底注入 family。"""
    request = _req(sample_request,
                   implementer={"vendor": "QianwenAI", "model": "GLM-5.3"})
    response = decide(request, engine="rules", config_path=config_path)
    assert response["review_plan"]["implementer"]["family"] == "glm-5.3"
    request = _req(sample_request,
                   implementer={"vendor": "qianwenai", "model": "glm-5.3",
                                "api_ref": "qianwenai/glm-5.3-alias"})
    response = decide(request, engine="rules", config_path=config_path)
    assert response["review_plan"]["implementer"]["family"] == "glm-5.3"


# ── 003 修复3：complexity 二值契约（宁高勿低）──────────────────────────────

def test_jev_complexity_non_low_normalized_high(sample_request, config_path, monkeypatch):
    """Jev 返回越界/三值 complexity（medium）→ 防御归一为 high（永不落到已废弃档位）。"""
    from jev_pi_router import jev_client
    monkeypatch.setattr(jev_client, "decide",
                        lambda brief, tags, impl, rev, timeout_ms=2000:
                        {"task_class": "implement", "complexity": "medium"})
    response = decide(sample_request, engine="jev", config_path=config_path)
    assert response["complexity"] == "high"


# ── R2-5/R2-7：complexity 宽容归一不整体 fail-open；quota_until 非数值走 UsageError ──


def test_jev_complexity_out_of_range_degrades_field_only(sample_request, config_path, monkeypatch):
    """R2-5：真实 jev_client 路径返回 complexity="medium" → 只降级该字段为 high，
    engine=jev、fail_open=False（不因单一软字段把整条决策翻成 fallback）。"""
    from jev_pi_router import jev_client

    def fake_post(body, timeout_s):
        questions = body["questions"]
        answers = {"task_class": {"choice": "implement"}, "complexity": {"choice": "medium"}}
        for qid in ("implement_model", "reviewer"):   # 其余问项给合法答案，隔离单一字段行为
            if qid in questions:
                answers[qid] = {"choice": next(iter(questions[qid]["criteria"]))}
        return {"answers": answers}

    monkeypatch.setattr(jev_client, "_post", fake_post)
    response = decide(sample_request, engine="jev", config_path=config_path)
    assert response["complexity"] == "high"
    assert response["task_class"] == "implement"
    assert response["engine"] == "jev" and response["fail_open"] is False


def test_jev_task_class_out_of_range_still_fail_open(sample_request, config_path, monkeypatch):
    """R2-5：task_class 越界仍严格校验 → 整体 fail-open 回退规则（硬派发键不可容忍）。"""
    from jev_pi_router import jev_client
    monkeypatch.setattr(jev_client, "_post", lambda body, timeout_s: {"answers": {
        "task_class": {"choice": "bugfix"}, "complexity": {"choice": "low"}}})
    response = decide(sample_request, engine="jev", config_path=config_path)
    assert response["engine"] == "rules" and response["fail_open"] is True
    assert response["task_class"] == "implement"          # 规则基线值


def test_quota_until_non_numeric_raises_usage_error(sample_request, config_path):
    """R2-7：quota_until 为日期字符串 → UsageError（CLI exit 3），不冒泡 ValueError traceback。"""
    request = _req(sample_request, vendor_failures=[
        {"vendor": "deepseek", "trigger": "quota", "quota_until": "2027-01-01"}])
    with pytest.raises(UsageError, match="quota_until"):
        decide(request, engine="rules", config_path=config_path)


# ── R3-4：输入校验加固（非法字段→UsageError，不冒泡 KeyError/ValueError traceback）──


def test_review_fail_count_non_numeric_raises_usage_error(sample_request, config_path):
    """R3-4：history.review_fail_count 非数值 → UsageError（CLI exit 3）。"""
    request = _req(sample_request, history={"review_fail_count": "abc", "previous_models": []})
    with pytest.raises(UsageError, match="review_fail_count"):
        decide(request, engine="rules", config_path=config_path)


def test_vendor_failures_entry_missing_vendor_raises_usage_error(sample_request, config_path):
    """R3-4：vendor_failures 条目缺 "vendor" 键 → UsageError（CLI exit 3），不冒泡 KeyError。"""
    request = _req(sample_request, vendor_failures=[{}])
    with pytest.raises(UsageError, match="vendor_failures"):
        decide(request, engine="rules", config_path=config_path)


def test_vendor_unlock_entry_missing_vendor_raises_usage_error(sample_request, config_path):
    """R3-4：vendor_unlock 条目缺 "vendor" 键同样走 UsageError（与 failures 同口径）。"""
    request = _req(sample_request, vendor_unlock=[{"reason": "manual"}])
    with pytest.raises(UsageError, match="vendor_unlock"):
        decide(request, engine="rules", config_path=config_path)


# ── R4：vendor_failures 容器级校验 + vendor_success 条目校验补测 ────────────────────


@pytest.mark.parametrize("value", [5, True])
def test_vendor_failures_container_non_iterable_raises_usage_error(value, sample_request, config_path):
    """R4-1：vendor_failures 容器为非法形状（如 5/true）→ UsageError（CLI exit 3），不冒泡 TypeError。"""
    request = _req(sample_request, vendor_failures=value)
    with pytest.raises(UsageError, match="vendor_failures"):
        decide(request, engine="rules", config_path=config_path)


def test_vendor_success_entry_missing_vendor_raises_usage_error(sample_request, config_path):
    """R4-2：vendor_success 条目缺 "vendor" 键 → UsageError（CLI exit 3），不冒泡 KeyError。"""
    request = _req(sample_request, vendor_success=[{}])
    with pytest.raises(UsageError, match="vendor_success"):
        decide(request, engine="rules", config_path=config_path)


# ── R5-1：非法输入形状守卫（非对象 request/history/previous_models → UsageError）──────


def test_non_object_request_raises_usage_error():
    """R5-1：request 顶层非对象（如 stdin 合法 JSON 5）→ UsageError（CLI exit 3），
    不冒泡 request.get 的 AttributeError。"""
    with pytest.raises(UsageError, match="JSON 对象"):
        decide(5)


def test_history_non_object_raises_usage_error(sample_request, config_path):
    """R5-1：history=5（非对象）→ UsageError（CLI exit 3），不冒泡 history.get 的 AttributeError。"""
    request = _req(sample_request, history=5)
    with pytest.raises(UsageError, match="history"):
        decide(request, engine="rules", config_path=config_path)


def test_previous_models_non_array_raises_usage_error(sample_request, config_path):
    """R5-1：history.previous_models=5（`or []` 不生效）→ UsageError，不冒泡 set() 的 TypeError。"""
    request = _req(sample_request, history={"review_fail_count": 0, "previous_models": 5})
    with pytest.raises(UsageError, match="previous_models"):
        decide(request, engine="rules", config_path=config_path)


def test_previous_models_non_array_escalated_raises_usage_error(sample_request, config_path):
    """R5-1：escalated 分支（review_fail_count=2，会迭代 previous_models）下值为 5 →
    同一守卫 UsageError，不冒泡迭代的 TypeError。"""
    request = _req(sample_request, history={"review_fail_count": 2, "previous_models": 5})
    with pytest.raises(UsageError, match="previous_models"):
        decide(request, engine="rules", config_path=config_path)

"""决策 CLI 契约测试（contracts/decision-cli.md；--engine rules 零网络；退出码语义）。"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
DECIDE = ROOT / "bin" / "jev-pi-decide"


def run_cli(request, config, engine="rules", env_extra=None):
    env = dict(os.environ)
    env["JEV_PI_ROUTER_HOME"] = str(Path(os.environ["JEV_PI_ROUTER_HOME"]))
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(DECIDE), "--engine", engine, "--config", str(config)],
        input=json.dumps(request), capture_output=True, text=True, env=env, timeout=30,
    )


def test_rules_mode_smoke(config_path, sample_request):
    """quickstart V1：规则模式实现类任务 → flash 池 + 异源 reviewer 契约字段齐全。"""
    result = run_cli(sample_request, config_path)
    assert result.returncode == 0, result.stderr
    response = json.loads(result.stdout)
    for key in ("task_class", "complexity", "chosen", "review_plan", "fallback_order",
                "engine", "fail_open", "rationale"):
        assert key in response
    assert response["chosen"]["pool"] == "flash"
    assert response["engine"] == "rules" and response["fail_open"] is False


def test_review_pairing_cross_vendor_contract(config_path):
    """quickstart V3：glm 实现 → reviewer 必须非 glm。"""
    request = {
        "task_ref": "t-pair", "task_brief": "review the diff", "role": "review",
        "task_class_hint": "implement",
        "candidates": {"strong": [], "flash": []},
        "implementer": {"vendor": "glm", "model": "glm-5.3-flash"},
        "risk_tags": [], "history": {"review_fail_count": 0, "previous_models": []},
    }
    result = run_cli(request, config_path)
    response = json.loads(result.stdout)
    reviewer = response["review_plan"]["code_reviewer"]
    assert reviewer and reviewer["vendor"] != "glm"


def test_quality_upgrade_forces_strong_and_switch_vendor(config_path):
    """FR-010：review 连败 2 次 → 强模型且换厂商。"""
    request = {
        "task_ref": "t-up", "task_brief": "rewrite the failed impl", "role": "implement",
        "task_class_hint": "implement", "candidates": {"strong": [], "flash": []},
        "implementer": {"vendor": "glm", "model": "glm-5.3-flash"},
        "risk_tags": [],
        "history": {"review_fail_count": 2, "previous_models": ["glm/glm-5.3-flash"]},
    }
    result = run_cli(request, config_path)
    response = json.loads(result.stdout)
    assert response["chosen"]["pool"] == "strong"
    assert response["chosen"]["vendor"] != "glm"


# ── 002 US2：qianwenai 互审同源约束 / 额度封禁 / 熔断（FR-004/005/006）────────

def _review_request(task_ref="t-002-rev", producer=None):
    return {
        "task_ref": task_ref, "task_brief": "review the diff", "role": "review",
        "task_class_hint": "implement", "candidates": {"strong": [], "flash": []},
        "implementer": producer or {"vendor": "qianwenai", "model": "glm-5.3"},
        "risk_tags": [], "history": {"review_fail_count": 0, "previous_models": []},
    }


def _impl_request(task_ref="t-002-impl"):
    return {
        "task_ref": task_ref, "task_brief": "实现 xxx 功能代码", "role": "implement",
        "task_class_hint": "implement", "candidates": {"strong": [], "flash": []},
        "implementer": None, "risk_tags": [],
        "history": {"review_fail_count": 0, "previous_models": []},
    }


def test_same_origin_reviewer_excluded(config_path):
    """① US2-2/3：producer=qianwenai/glm-5.3 → reviewer 真异源 mimo，绝不同源 glm。"""
    response = json.loads(run_cli(_review_request(), config_path).stdout)
    reviewer = response["review_plan"]["code_reviewer"]
    assert reviewer["api_ref"] == "mimo/mimo-v2.6-pro"
    assert response["review_plan"]["degrade"] is False
    assert response["review_plan"]["degrade_reason"] == ""


def test_same_origin_fallback_with_warning(config_path):
    """② US2-4：封禁 mimo 后仅剩同源 glm/glm-5.3 → 兜底复核 + same_origin 告警留痕。"""
    request = _review_request(task_ref="t-002-so")
    request["vendor_failures"] = [{"vendor": "mimo", "trigger": "quota"}]
    response = json.loads(run_cli(request, config_path).stdout)
    rp = response["review_plan"]
    assert rp["code_reviewer"]["api_ref"] == "glm/glm-5.3"
    assert rp["degrade"] is True and rp["degrade_reason"] == "same_origin"
    assert "degrade_same_origin" in [e["type"] for e in response["fallback_events"]]


def test_quota_block_and_unlock_lifecycle(config_path):
    """③④ US2-6/FR-005/SC-004：quota 封禁 qianwenai → 可选集/轮换剔除；解锁即恢复。"""
    before = json.loads(run_cli(_impl_request("t-002-q0"), config_path).stdout)
    assert any("qianwenai/" in e["api_ref"] for e in before["fallback_order"])

    blocked = json.loads(run_cli({**_impl_request("t-002-q1"),
                                  "vendor_failures": [{"vendor": "qianwenai", "trigger": "quota"}]},
                                 config_path).stdout)
    assert blocked["chosen"]["vendor"] != "qianwenai"
    assert not any("qianwenai/" in e["api_ref"] for e in blocked["fallback_order"])
    assert "qianwenai" in json.dumps(blocked["quota_hints"])

    unlocked = json.loads(run_cli({**_impl_request("t-002-q2"),
                                   "vendor_unlock": [{"vendor": "qianwenai", "reason": "manual"}]},
                                  config_path).stdout)
    assert any("qianwenai/" in e["api_ref"] for e in unlocked["fallback_order"])   # 同次调用即恢复


def test_breaker_lifecycle_independent_of_quota(config_path):
    """⑤ FR-006/analyze G1：qianwenai 连续故障→熔断换商→成功闭合；与额度封禁相互独立。"""
    fails = [{"vendor": "qianwenai", "trigger": "timeout"}]
    events = []
    for i in range(3):   # threshold=3
        events += [e["type"] for e in json.loads(
            run_cli({**_impl_request(f"t-002-b{i}"), "vendor_failures": fails},
                    config_path).stdout)["fallback_events"]]
    assert "breaker_open" in events
    after = json.loads(run_cli(_impl_request("t-002-b3"), config_path).stdout)
    assert not any("qianwenai/" in e["api_ref"] for e in after["fallback_order"])   # 熔断剔除轮换
    closed = json.loads(run_cli({**_impl_request("t-002-b4"),
                                 "vendor_success": [{"vendor": "qianwenai"}]}, config_path).stdout)
    assert "breaker_close" in [e["type"] for e in closed["fallback_events"]]
    qfile = Path(os.environ["JEV_PI_ROUTER_HOME"]) / "quotas.json"
    quotas = json.loads(qfile.read_text(encoding="utf-8")) if qfile.exists() else {}
    assert "qianwenai" not in quotas.get("quotas", {})        # 独立：timeout 不产生额度封禁


def test_api_ref_cooldown_persists_across_cli_invocations(config_path):
    """v1.4：两次进程调用间 api_ref 滑窗落盘生效（empty_response 死循环场景，仿 ⑤ 模式）。"""
    ref = "relay/cmd-deepseek-v4.1-flash"
    failure = {"vendor": "relay", "api_ref": ref, "trigger": "empty_response"}
    first = json.loads(run_cli({**_impl_request("t-002-ar0"), "vendor_failures": [failure]},
                               config_path).stdout)
    assert ref in {e["api_ref"] for e in first["fallback_order"]}    # 1 次：仅降权，仍在池
    second = json.loads(run_cli({**_impl_request("t-002-ar1"), "vendor_failures": [failure]},
                                config_path).stdout)
    assert "api_ref_cooldown" in [e["type"] for e in second["fallback_events"]]
    assert ref not in {e["api_ref"] for e in second["fallback_order"]}
    assert second["chosen"]["api_ref"] != ref
    state = json.loads((Path(os.environ["JEV_PI_ROUTER_HOME"]) / "state.json")
                       .read_text(encoding="utf-8"))
    assert state["api_refs"][ref]["cooldown_until"] > 0              # 滑窗状态跨进程持久化


def test_fail_open_when_jev_unreachable(config_path, sample_request):
    """quickstart V2 / SC-004：Jev 不可用 → engine=rules、fail_open=true、exit 0。"""
    result = run_cli(sample_request, config_path, engine="auto", env_extra={
        "TYPESAFE_API_KEY": "sk-invalid", "TYPESAFE_ENDPOINT": "https://127.0.0.1:1/v1/systemone",
    })
    assert result.returncode == 0, result.stderr
    response = json.loads(result.stdout)
    assert response["engine"] == "rules" and response["fail_open"] is True


def test_exit_3_on_bad_json(config_path):
    result = subprocess.run(
        [sys.executable, str(DECIDE), "--engine", "rules", "--config", str(config_path)],
        input="{not json", capture_output=True, text=True, timeout=30)
    assert result.returncode == 3


def test_exit_3_on_missing_fields(config_path):
    result = run_cli({"task_ref": "x"}, config_path)
    assert result.returncode == 3


def test_exit_2_on_bad_config(tmp_path, sample_request):
    bad = tmp_path / "bad.yaml"
    bad.write_text("strong_pool: []\nflash_pool: []\n", encoding="utf-8")
    result = run_cli(sample_request, bad)
    assert result.returncode == 2


def test_log_record_written(config_path, sample_request):
    home = Path(os.environ["JEV_PI_ROUTER_HOME"])
    run_cli(sample_request, config_path)
    lines = (home / "decisions.jsonl").read_text(encoding="utf-8").strip().splitlines()
    record = json.loads(lines[-1])
    assert record["v"] == 1 and record["task_ref"] == sample_request["task_ref"]


def test_reviewer_never_penetrates_full_quota_block(config_path):
    """review F3（002 契约 §4）：强池全部额度封禁 → 复核兜底不穿透封禁，
    无 reviewer 并显式 corrected 标记，而非派回被封厂商。"""
    request = _review_request("t-002-pb", producer={"vendor": "qianwenai", "model": "glm-5.3"})
    request["vendor_failures"] = [{"vendor": v, "trigger": "quota"}
                                  for v in ("mimo", "glm", "qianwenai")]
    response = json.loads(run_cli(request, config_path).stdout)
    rp = response["review_plan"]
    assert rp["code_reviewer"] is None and rp["degrade"] is False
    assert "[pairing-corrected]" in response["rationale"]

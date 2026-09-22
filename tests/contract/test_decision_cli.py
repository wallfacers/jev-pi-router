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

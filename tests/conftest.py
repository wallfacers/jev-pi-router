import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 测试隔离：日志/状态写入临时目录，不污染 ~/.jev-pi-router
@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("JEV_PI_ROUTER_HOME", str(tmp_path / "home"))
    yield tmp_path


@pytest.fixture()
def config_path():
    return ROOT / "router.config.yaml.example"


@pytest.fixture()
def sample_request():
    """contracts/decision-cli.md 的最小合法请求。"""
    return {
        "task_ref": "t-test-001",
        "task_brief": "为 config.py 增加 schema 校验并补单元测试",
        "role": "implement",
        "task_class_hint": None,
        "candidates": {"strong": [], "flash": []},
        "implementer": None,
        "risk_tags": [],
        "history": {"review_fail_count": 0, "previous_models": []},
    }

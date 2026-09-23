"""jev-pi-doctor 体检警告单测（002 R9；review F2/F6 回归）。"""

import importlib.machinery
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCTOR_PATH = ROOT / "bin" / "jev-pi-doctor"


def load_doctor_module():
    loader = importlib.machinery.SourceFileLoader("jev_pi_doctor", str(DOCTOR_PATH))
    spec = importlib.util.spec_from_loader("jev_pi_doctor", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


doctor = load_doctor_module()

ENTRIES = [
    {"vendor": "glm", "model": "glm-5.3", "api_ref": "glm/glm-5.3", "family": "glm-5.3"},
    {"vendor": "qianwenai", "model": "glm-5.3", "api_ref": "qianwenai/glm-5.3", "family": "glm-5.3"},
]


def test_no_warnings_when_manifest_covers_and_families_consistent(tmp_path):
    manifest = tmp_path / "pc.json"
    manifest.write_text(json.dumps({"promptCache": {e["api_ref"]: {"short": 300, "long": 3600}
                                                    for e in ENTRIES}}), encoding="utf-8")
    assert doctor._check_warnings(ENTRIES, str(manifest)) == []


def test_corrupt_manifest_single_warn_no_flood(tmp_path):
    """review F2/F6：非 UTF-8 / 非法 JSON / 非 dict 清单 → 单条 WARN，不逐条刷屏、不崩溃。"""
    manifest = tmp_path / "pc.json"
    manifest.write_bytes(b"\x80\x81\x82")     # UnicodeDecodeError 路径
    warns = doctor._check_warnings(ENTRIES, str(manifest))
    assert len(warns) == 1 and "无法读取" in warns[0]
    manifest.write_text("[1, 2, 3]", encoding="utf-8")   # 合法 JSON 但非 dict
    warns = doctor._check_warnings(ENTRIES, str(manifest))
    assert len(warns) == 1 and "无法读取" in warns[0]
    assert doctor._check_warnings(ENTRIES, str(tmp_path / "nope.json")) == \
        [f"WARN 无法读取缓存声明清单 {tmp_path / 'nope.json'}（跳过逐条缺口检查）"]


def test_missing_key_and_family_mismatch_warned(tmp_path):
    manifest = tmp_path / "pc.json"
    manifest.write_text(json.dumps({"promptCache": {"glm/glm-5.3": {}}}), encoding="utf-8")
    warns = doctor._check_warnings(ENTRIES, str(manifest))
    assert any("qianwenai/glm-5.3 缺少缓存声明" in w for w in warns)
    entries = [dict(ENTRIES[0]), {**ENTRIES[1], "family": ""}]   # 一方未声明 family
    assert any("疑似同源" in w for w in doctor._check_warnings(entries, str(manifest)))


# ── 003 修复4：--config 默认值为仓库根绝对路径（环境变量仍优先）──────────────

def test_default_config_is_absolute_repo_root(monkeypatch):
    """未传 --config 且无环境变量时，默认指向仓库根 router.config.yaml（绝对路径）。"""
    captured = {}

    def fake_check(config):
        captured["config"] = config
        return 0

    monkeypatch.setattr(doctor, "cmd_check", fake_check)
    monkeypatch.delenv("JEV_PI_ROUTER_CONFIG", raising=False)
    monkeypatch.setattr(sys, "argv", ["jev-pi-doctor", "check"])
    doctor.main()
    default = Path(captured["config"])
    assert default.is_absolute()
    assert default == ROOT / "router.config.yaml"


def test_env_config_still_wins_over_default(monkeypatch):
    """环境变量 JEV_PI_ROUTER_CONFIG 优先级高于仓库根默认路径。"""
    captured = {}

    def fake_check(config):
        captured["config"] = config
        return 0

    monkeypatch.setattr(doctor, "cmd_check", fake_check)
    monkeypatch.setenv("JEV_PI_ROUTER_CONFIG", "/tmp/custom-router.yaml")
    monkeypatch.setattr(sys, "argv", ["jev-pi-doctor", "check"])
    doctor.main()
    assert captured["config"] == "/tmp/custom-router.yaml"


def test_empty_env_config_falls_back_to_default(monkeypatch):
    """R2-1：JEV_PI_ROUTER_CONFIG="" 视为未设置（与 config.py 同语义），不把空串当作路径。"""
    captured = {}

    def fake_check(config):
        captured["config"] = config
        return 0

    monkeypatch.setattr(doctor, "cmd_check", fake_check)
    monkeypatch.setenv("JEV_PI_ROUTER_CONFIG", "")
    monkeypatch.setattr(sys, "argv", ["jev-pi-doctor", "check"])
    doctor.main()
    assert captured["config"] == doctor.DEFAULT_CONFIG
    assert Path(captured["config"]).is_absolute()

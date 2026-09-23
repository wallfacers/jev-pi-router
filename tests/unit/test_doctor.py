"""jev-pi-doctor 体检警告单测（002 R9；review F2/F6 回归）。"""

import importlib.machinery
import importlib.util
import json
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

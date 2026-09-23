"""bin/jev-pi-stats 单测（chosen.api_ref 调用次数统计）。"""

import importlib.machinery
import importlib.util
import json
from pathlib import Path

from jev_pi_router.log import log_path

ROOT = Path(__file__).resolve().parents[2]
STATS_PATH = ROOT / "bin" / "jev-pi-stats"


def load_stats_module():
    loader = importlib.machinery.SourceFileLoader("jev_pi_stats", str(STATS_PATH))
    spec = importlib.util.spec_from_loader("jev_pi_stats", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


stats = load_stats_module()


def rec(api_ref):
    return {"chosen": {"api_ref": api_ref}}


def test_count_api_refs_counts_each_model():
    records = [rec("deepseek/deepseek-flash"), rec("glm/glm-5.3"), rec("deepseek/deepseek-flash")]
    assert stats.count_api_refs(records) == {"deepseek/deepseek-flash": 2, "glm/glm-5.3": 1}


def test_render_table_sorts_by_count_desc():
    table = stats.render_table({"a/x": 1, "b/y": 3, "c/z": 3})
    body = table.splitlines()[1:]
    assert [line.split()[0] for line in body] == ["b/y", "c/z", "a/x"]


def test_render_table_ties_break_by_api_ref_ascending():
    """count 相同时按 api_ref 升序（而非插入序）：插入序 c/z 在 b/y 之前。"""
    table = stats.render_table({"c/z": 3, "b/y": 3})
    body = table.splitlines()[1:]
    assert [line.split()[0] for line in body] == ["b/y", "c/z"]


def test_render_table_aligns_columns():
    table = stats.render_table({"deepseek/deepseek-flash": 10, "glm/glm-5.3": 2})
    assert table.splitlines() == [
        "api_ref                  count",
        "deepseek/deepseek-flash  10",
        "glm/glm-5.3              2",
    ]


def test_empty_log_prints_header_only(isolated_home):
    table = stats.render_table(stats.count_api_refs(stats.iter_records()))
    assert table.splitlines() == ["api_ref  count"]


def test_counts_records_from_default_log_path(isolated_home):
    path = log_path()
    path.parent.mkdir(parents=True, exist_ok=True)   # log_path 为只读语义，写测试自行建目录
    path.write_text(json.dumps(rec("glm/glm-5.3")) + "\n", encoding="utf-8")
    assert stats.count_api_refs(stats.iter_records()) == {"glm/glm-5.3": 1}


def test_missing_api_ref_falls_back_to_question_mark():
    records = [{"chosen": {"vendor": "x"}}, {"chosen": None}, {}]
    assert stats.count_api_refs(records) == {"?": 3}


def test_qianwenai_counted_as_regular_vendor(isolated_home):
    """002 FR-009：qianwenai 作为普通厂商进入统计分组；无 qianwenai 数据时零值正常（上方空日志用例覆盖）。"""
    path = log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(rec("qianwenai/qwen3.8-flash")), json.dumps(rec("qianwenai/qwen3.8-flash")),
             json.dumps(rec("glm/glm-5.3"))]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    counts = stats.count_api_refs(stats.iter_records())
    assert counts == {"qianwenai/qwen3.8-flash": 2, "glm/glm-5.3": 1}
    assert stats.render_table(counts).splitlines()[1].startswith("qianwenai/qwen3.8-flash")

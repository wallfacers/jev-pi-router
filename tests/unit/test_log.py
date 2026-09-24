"""决策日志单测（contracts/decision-log-schema.md 三条不变量）。"""

import json

import pytest

from jev_pi_router.log import LogError, append_decision, iter_records, session_hash, validate_record


def base_record(**overrides):
    record = {
        "v": 1, "decision_id": "abc", "ts": "2026-09-22T10:00:00+08:00",
        "session_id_hash": session_hash("session-1"), "task_ref": "t1", "role": "implement",
        "task_class": "implement", "complexity": "low",
        "chosen": {"vendor": "deepseek", "model": "deepseek-flash", "api_ref": "deepseek/deepseek-flash", "pool": "flash"},
        "review_plan": {"code_reviewer": None, "plan_reviewers": [], "degrade": False},
        "engine": "rules", "fail_open": False, "rationale": "ok", "fallback_events": [],
    }
    record.update(overrides)
    return record


def test_append_and_iterate(isolated_home):
    path = append_decision(base_record())
    assert path.exists()
    records = list(iter_records())
    assert len(records) == 1 and records[0]["decision_id"] == "abc"


def test_session_hash_stable_and_short():
    assert session_hash("session-1") == session_hash("session-1")
    assert len(session_hash("session-1")) == 12 and session_hash("session-1") != session_hash("session-2")
    assert session_hash(None) == ""


def test_invariant_degrade_requires_event():
    record = base_record(review_plan={"code_reviewer": None, "plan_reviewers": [], "degrade": True})
    with pytest.raises(LogError, match="不变量1"):
        validate_record(record)
    record["fallback_events"] = [{"type": "degrade_single_vendor", "trigger": "explicit",
                                  "from_model": None, "to_model": None, "attempt": 1, "ts": ""}]
    validate_record(record)


def test_invariant_fail_open_requires_rules_engine():
    with pytest.raises(LogError, match="不变量2"):
        validate_record(base_record(engine="jev", fail_open=True))
    validate_record(base_record(engine="rules", fail_open=True))


# ── 002 R8：不变量1 扩展（同源降级事件留痕，向后兼容）─────────────────────────

def test_invariant_degrade_accepts_same_origin_event():
    """degrade=true + degrade_same_origin 事件 ⇒ 通过（002 FR-004 兜底留痕）。"""
    validate_record(base_record(
        review_plan={"code_reviewer": None, "plan_reviewers": [], "degrade": True,
                     "degrade_reason": "same_origin"},
        fallback_events=[{"type": "degrade_same_origin", "trigger": "explicit", "ts": ""}]))


def test_invariant_degrade_still_rejects_silent():
    """degrade=true 但无任何降级事件 ⇒ 仍拒绝（无静默降级语义不放松）。"""
    with pytest.raises(LogError, match="不变量1"):
        validate_record(base_record(
            review_plan={"code_reviewer": None, "plan_reviewers": [], "degrade": True,
                         "degrade_reason": "same_origin"},
            fallback_events=[{"type": "quota_block", "trigger": "quota", "ts": ""}]))


def test_legacy_record_without_degrade_reason_replays():
    """历史记录（无 degrade_reason 字段、仅 degrade_single_vendor）重放校验不报错（R8 兼容）。"""
    validate_record(base_record(
        review_plan={"code_reviewer": None, "plan_reviewers": [], "degrade": True},
        fallback_events=[{"type": "degrade_single_vendor", "trigger": "explicit", "ts": ""}]))


def test_required_fields_present():
    record = base_record()
    del record["chosen"]
    with pytest.raises(LogError, match="缺少字段"):
        validate_record(record)
    with pytest.raises(LogError, match="schema 版本"):
        validate_record(base_record(v=2))


def test_iter_records_missing_log_does_not_create_home(isolated_home):
    """只读路径无 mkdir 副作用：日志/目录不存在时返回空且不创建 home 目录。"""
    home = isolated_home / "home"
    assert not home.exists()
    assert list(iter_records()) == []
    assert not home.exists()


def test_append_writes_json_line(isolated_home):
    path = append_decision(base_record())
    line = path.read_text(encoding="utf-8").strip().splitlines()[-1]
    assert json.loads(line)["task_ref"] == "t1"


def test_invariant_reason_event_pairing_enforced():
    """review F7：degrade_reason 与事件类型必须配对，错配拒绝落盘。"""
    plan = {"code_reviewer": None, "plan_reviewers": [], "degrade": True, "degrade_reason": "same_origin"}
    with pytest.raises(LogError, match="degrade_same_origin"):
        validate_record(base_record(review_plan=plan, fallback_events=[
            {"type": "degrade_single_vendor", "trigger": "explicit", "ts": ""}]))
    with pytest.raises(LogError, match="degrade_single_vendor"):
        validate_record(base_record(
            review_plan={**plan, "degrade_reason": "single_vendor"},
            fallback_events=[{"type": "degrade_same_origin", "trigger": "explicit", "ts": ""}]))
    validate_record(base_record(review_plan=plan, fallback_events=[
        {"type": "degrade_same_origin", "trigger": "explicit", "ts": ""}]))    # 正确配对通过


def test_invariant_degrade_reason_events_mutually_exclusive():
    """归因唯一（002 I1）：degrade_reason 与另一降级事件同时出现 ⇒ 拒绝落盘。"""
    plan = {"code_reviewer": None, "plan_reviewers": [], "degrade": True, "degrade_reason": "same_origin"}
    with pytest.raises(LogError, match="归因唯一"):
        validate_record(base_record(review_plan=plan, fallback_events=[
            {"type": "degrade_same_origin", "trigger": "explicit", "ts": ""},
            {"type": "degrade_single_vendor", "trigger": "explicit", "ts": ""}]))
    with pytest.raises(LogError, match="归因唯一"):
        validate_record(base_record(
            review_plan={**plan, "degrade_reason": "single_vendor"},
            fallback_events=[
                {"type": "degrade_single_vendor", "trigger": "explicit", "ts": ""},
                {"type": "degrade_same_origin", "trigger": "explicit", "ts": ""}]))

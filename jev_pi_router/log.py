"""JSONL 决策日志（contracts/decision-log-schema.md；FR-011）。

- 每次路由决策 append 一行（无论 engine 路径）；session id 只存哈希。
- 三条不变量在写入前强制校验：
  1. review_plan.degrade=true ⇒ 至少一个 degrade_single_vendor 事件（SC-002 无静默降级）；
  2. fail_open=true ⇔ engine="rules"（可观测性对账）；
  3. schema 版本 v==1、必备字段齐全。
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

REQUIRED_FIELDS = ("v", "decision_id", "ts", "task_ref", "role", "task_class", "complexity",
                   "chosen", "review_plan", "engine", "fail_open", "rationale", "fallback_events")


class LogError(Exception):
    pass


def home_dir(mkdir: bool = True) -> Path:
    """路由 home 目录；mkdir=False 时纯只读解析（不产生创建目录副作用）。"""
    path = Path(os.environ.get("JEV_PI_ROUTER_HOME", Path.home() / ".jev-pi-router"))
    if mkdir:
        path.mkdir(parents=True, exist_ok=True)
    return path


def log_path() -> Path:
    """日志路径（只读语义；写路径由 append_decision 自行创建父目录）。"""
    return home_dir(mkdir=False) / "decisions.jsonl"


def session_hash(session_id: str | None) -> str:
    if not session_id:
        return ""
    return hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:12]


def validate_record(record: dict) -> None:
    missing = [k for k in REQUIRED_FIELDS if k not in record]
    if missing:
        raise LogError(f"决策记录缺少字段: {missing}")
    if record["v"] != 1:
        raise LogError(f"未知 schema 版本: {record['v']}")
    if record["fail_open"] and record["engine"] != "rules":
        raise LogError("不变量2 违反: fail_open=true 时 engine 必须为 rules")
    events = record.get("fallback_events") or []
    if record.get("review_plan", {}).get("degrade") and not any(
        e.get("type") == "degrade_single_vendor" for e in events
    ):
        raise LogError("不变量1 违反: review_plan.degrade=true 必须伴随 degrade_single_vendor 事件")


def append_decision(record: dict, path: Path | None = None) -> Path:
    validate_record(record)
    target = path or log_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    return target


def iter_records(path: Path | None = None):
    """迭代日志记录；日志缺失时静默返回空（只读，不创建 home 目录）。"""
    target = path or log_path()
    if not target.exists():
        return
    with target.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)

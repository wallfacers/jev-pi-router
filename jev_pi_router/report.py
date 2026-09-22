"""决策日志聚合报表（FR-011 / SC-001/002；contracts/decision-cli.md report 节）。"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone

from .log import iter_records


def aggregate(days: int | None = None) -> dict:
    cutoff = None
    if days:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    models = Counter()
    engines = Counter()
    fail_open = 0
    total = 0
    event_types = Counter()
    code_review = {"total": 0, "cross_vendor": 0, "degrade": 0}
    plan_review = {"total": 0, "mutual": 0}

    for record in iter_records():
        if cutoff:
            try:
                if datetime.fromisoformat(record["ts"]) < cutoff:
                    continue
            except (KeyError, ValueError):
                pass
        total += 1
        chosen = record.get("chosen") or {}
        models[chosen.get("api_ref", "?")] += 1
        engines[record.get("engine", "?")] += 1
        fail_open += bool(record.get("fail_open"))
        for event in record.get("fallback_events") or []:
            event_types[event.get("type", "?")] += 1

        review_plan = record.get("review_plan") or {}
        producer_is_code = record.get("task_class") != "design"
        if review_plan.get("code_reviewer") and producer_is_code:
            code_review["total"] += 1
            code_review["degrade"] += bool(review_plan.get("degrade"))
            reviewer = review_plan["code_reviewer"]
            producer = review_plan.get("implementer") or {}
            if producer.get("vendor") and reviewer.get("vendor") != producer.get("vendor"):
                code_review["cross_vendor"] += 1
        for pair in review_plan.get("plan_reviewers") or []:
            plan_review["total"] += 1
            author, reviewer = pair.get("author") or {}, pair.get("reviewer") or {}
            if author.get("vendor") and reviewer.get("vendor") and author["vendor"] != reviewer["vendor"]:
                plan_review["mutual"] += 1

    return {
        "total_decisions": total,
        "models": dict(models),
        "engines": dict(engines),
        "fail_open_count": fail_open,
        "fallback_events": dict(event_types),
        "code_review": code_review,
        "plan_review": plan_review,
    }


def render(stats: dict) -> str:
    lines = [
        f"决策总数: {stats['total_decisions']}",
        f"fail-open: {stats['fail_open_count']}",
        f"引擎路径: {stats['engines']}",
        f"模型分布: {stats['models']}",
        f"兜底事件: {stats['fallback_events'] or '{}'}",
        f"代码 review: {stats['code_review']}",
        f"计划互审: {stats['plan_review']}",
    ]
    return "\n".join(lines)

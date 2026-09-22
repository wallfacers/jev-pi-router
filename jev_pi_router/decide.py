"""决策编排（contracts/decision-cli.md 的实现；FR-005/006/011）。

流程：加载配置（每次决策重新加载，FR-013）→ 过滤候选（熔断/历史）→ 规则基线 →
Jev 覆盖（auto/jev，失败 fail-open）→ 质量升级判定 → review 配对（硬约束后置修正）→
写决策日志 → 返回 response。

扩展字段（契约向后兼容的可选项）：
- request["vendor_failures"]: [{"vendor","trigger"}] —— 调用方上报的厂商故障，
  用于熔断计数与 fault_transfer 事件（一次性 CLI 的状态来源）。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from . import jev_client, rules
from .config import ConfigError, RouterConfig, load_config
from .fallback import BreakerRegistry, make_event, quality_escalated
from .log import append_decision, session_hash

REQUIRED_REQUEST = ("task_ref", "task_brief", "role")


class UsageError(Exception):
    """请求 JSON 非法（CLI 退出码 3）。"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def decide(request: dict, engine: str = "auto", config_path=None, timeout_ms: int | None = None) -> dict:
    missing = [k for k in REQUIRED_REQUEST if not request.get(k)]
    if missing:
        raise UsageError(f"请求缺少字段: {missing}")
    if engine not in ("auto", "rules", "jev"):
        raise UsageError(f"--engine 非法: {engine}")

    cfg: RouterConfig = load_config(config_path)          # FR-013：每次决策重载
    ts = _now_iso()
    fallback_events: list = []
    rationale_parts: list = []

    registry = BreakerRegistry(threshold=cfg.fallback.breaker_failures,
                               cooldown_sec=cfg.fallback.breaker_cooldown_sec)
    registry.load()
    for failure in request.get("vendor_failures") or []:
        trigger = failure.get("trigger", "explicit")
        fallback_events.extend(registry.record_failure(failure["vendor"], trigger, ts=ts))
    registry.save()
    if fallback_events:
        rationale_parts.append("熔断事件已记录")

    history = request.get("history") or {}
    previous_refs = set(history.get("previous_models") or [])
    producer = request.get("implementer") or None        # review 场景：产出者 {vendor, model}

    def available(pool):
        return [e for e in rules.order_pool(pool, cfg.auto.enabled)
                if registry.available(e.vendor) and e.api_ref not in previous_refs]

    strong = available(cfg.strong) or rules.order_pool(cfg.strong, cfg.auto.enabled)
    flash = available(cfg.flash) or rules.order_pool(cfg.flash, cfg.auto.enabled)

    # ── 质量升级（FR-010）──────────────────────────────────────
    escalated = quality_escalated(int(history.get("review_fail_count", 0)), cfg.fallback.quality_review_fails)
    pool_name = rules.role_pool(request["role"], cfg.roles)
    avoid_vendors = set()
    if escalated:
        pool_name = "strong"
        if cfg.fallback.switch_vendor and producer:
            avoid_vendors.add(producer.get("vendor"))
        for ref in history.get("previous_models") or []:
            if "/" in ref:
                avoid_vendors.add(ref.split("/")[0])
        fallback_events.append(make_event("quality_upgrade", "review_reject", to_model="strong", ts=ts))
        rationale_parts.append(f"质量升级：review 连败{history.get('review_fail_count')}次，强模型换厂商重做")

    pool = strong if pool_name == "strong" else flash

    # ── 规则基线 ──────────────────────────────────────────────
    task_class = rules.classify(request["task_brief"], request.get("task_class_hint"), request["role"])
    complexity = rules.complexity(request.get("risk_tags"))
    chosen = rules.pick(pool, avoid_refs=previous_refs, avoid_vendors=avoid_vendors) or rules.pick(pool)
    engine_used, fail_open = "rules", False

    # ── Jev 覆盖（FR-005；失败 fail-open，FR-006）────────────────
    if engine in ("auto", "jev") and not escalated:
        reviewer_pool = [e for e in strong if not producer or e.vendor != producer.get("vendor")]
        try:
            jev = jev_client.decide(
                request["task_brief"], request.get("risk_tags") or [],
                [e.as_ref() for e in (flash if pool_name == "flash" else strong)][:5],
                [e.as_ref() for e in reviewer_pool][:5],
                timeout_ms or cfg.engine.timeout_ms,
            )
            task_class = jev.get("task_class", task_class)
            complexity = jev.get("complexity", complexity)
            implement_ref = jev.get("implement_ref")
            if implement_ref and pool_name == "flash":
                match = next((e for e in pool if e.api_ref == implement_ref), None)
                if match is not None:
                    chosen = match
            engine_used = "jev"
        except jev_client.JevError as exc:
            if engine == "jev" and not cfg.engine.fail_open:
                raise
            fail_open = True
            rationale_parts.append(f"Jev fail-open：{exc}")
    elif engine == "rules":
        rationale_parts.append("规则引擎模式（离线）")

    # ── review 配对（FR-007/008；硬约束后置修正）─────────────────
    review_plan = {"code_reviewer": None, "plan_reviewers": [], "degrade": False, "implementer": producer}
    corrected = False
    if producer:
        allow_degrade = cfg.review.degrade_to_single_vendor
        reviewer_ref, degrade = rules.code_reviewer(producer, strong, allow_degrade)
        if task_class == "design":
            review_plan["plan_reviewers"] = [{"author": producer, "reviewer": reviewer_ref}] if reviewer_ref else []
        else:
            review_plan["code_reviewer"] = reviewer_ref
        review_plan["degrade"] = degrade
        if degrade:
            fallback_events.append(make_event("degrade_single_vendor", "explicit", ts=ts))
            rationale_parts.append("强池仅单厂商可用，review 降级（已标记）")
        if reviewer_ref and not rules.pairing_valid(reviewer_ref, producer):
            corrected = True
    if corrected or (producer and not review_plan["code_reviewer"] and not review_plan["plan_reviewers"]
                     and not review_plan["degrade"]):
        if not review_plan["degrade"]:
            corrected = True
    if corrected:
        rationale_parts.insert(0, "[pairing-corrected]")

    fb = rules.fallback_order(pool, chosen)

    chosen_ref = chosen.as_ref() if chosen else None
    rationale = "；".join(rationale_parts) if rationale_parts else (
        f"{'设计' if task_class == 'design' else '实现' if task_class == 'implement' else '杂务'}类"
        f"{'' if complexity == 'low' else '高风险'}任务，按{pool_name}池顺序派发"
    )
    response = {
        "task_class": task_class,
        "complexity": complexity,
        "chosen": chosen_ref,
        "review_plan": review_plan,
        "fallback_order": fb,
        "engine": engine_used,
        "fail_open": fail_open,
        "rationale": rationale[:200],
    }

    record = {
        "v": 1,
        "decision_id": uuid.uuid4().hex,
        "ts": ts,
        "session_id_hash": session_hash(request.get("session_id")),
        "task_ref": request["task_ref"],
        "role": request["role"],
        "task_class": task_class,
        "complexity": complexity,
        "chosen": chosen_ref,
        "review_plan": review_plan,
        "engine": engine_used,
        "fail_open": fail_open,
        "rationale": response["rationale"],
        "fallback_events": fallback_events,
    }
    append_decision(record)                              # FR-011：无论路径必写
    return response


__all__ = ["decide", "ConfigError", "UsageError"]

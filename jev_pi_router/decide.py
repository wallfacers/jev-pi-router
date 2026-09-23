"""决策编排（contracts/decision-cli.md 的实现；FR-005/006/011）。

流程：加载配置（每次决策重新加载，FR-013）→ 过滤候选（熔断/历史）→ 规则基线 →
Jev 覆盖（auto/jev，失败 fail-open）→ 质量升级判定 → review 配对（硬约束后置修正）→
写决策日志 → 返回 response。

扩展字段（契约向后兼容的可选项）：
- request["vendor_failures"]: [{"vendor","trigger"}] —— 调用方上报的厂商故障
  （顶层为准；兼容旧 history.vendor_failures）；request["vendor_success"] 上报成功闭合熔断。
  用于熔断计数与 fault_transfer 事件（一次性 CLI 的状态来源）。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from . import jev_client, rules
from .config import ConfigError, RouterConfig, load_config
from .fallback import BreakerRegistry, make_event, quality_escalated
from .quota import QuotaRegistry
from .log import append_decision, session_hash

REQUIRED_REQUEST = ("task_ref", "task_brief", "role")


class UsageError(Exception):
    """请求 JSON 非法（CLI 退出码 3）。"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def jev_ref(entry) -> dict:
    """Jev 候选描述引用：as_ref + cost_hint/cache_passthrough（review F1 修复——
    否则 jev_client 渲染恒为默认值 1.0/unknown，002 R4/R5 的平权与缓存档位信号到不了 Jev）。"""
    ref = entry.as_ref()
    ref["cost_hint"] = entry.cost_hint
    ref["cache_passthrough"] = entry.cache_passthrough
    return ref


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
    quotas = QuotaRegistry()
    quotas.load()
    history = request.get("history") or {}

    fallback_events.extend(quotas.expire_due(ts=ts))    # 到期套餐额度自动解锁（quota_unlock: auto）

    # 成功回报：闭合熔断（breaker_close 事件，CLI 路径可达）
    successes = request.get("vendor_success") or []
    if isinstance(successes, dict):
        successes = [successes]
    for success in successes:
        fallback_events.extend(registry.record_success(success["vendor"], ts=ts))

    # 人工解锁：套餐重置/重置卡/活动提前重置（quota_unlock: <reason>，可提前覆盖 quota_until）
    unlocks = request.get("vendor_unlock") or []
    if isinstance(unlocks, dict):
        unlocks = [unlocks]
    for item in unlocks:
        fallback_events.extend(quotas.unlock(item["vendor"], reason=item.get("reason", "manual"),
                                             key_id=item.get("key_id", ""), ts=ts))

    # 故障回报：顶层 vendor_failures 为准（skill 模板 v2），兼容旧 history.vendor_failures
    failures = request.get("vendor_failures") or history.get("vendor_failures") or []
    if isinstance(failures, dict):
        failures = [failures]
    for failure in failures:
        trigger = failure.get("trigger", "explicit")
        if trigger == "quota":            # 套餐/周限额额度尽 → 立即封禁，不计熔断（S12）
            fallback_events.extend(quotas.block(
                failure["vendor"], quota_until=float(failure.get("quota_until") or 0.0),
                reason=failure.get("reason", "quota"), key_id=failure.get("key_id", ""), ts=ts))
        elif trigger == "rate_limit":     # 并发/瞬时 429 → 重试即可，不计不封
            continue
        else:
            fallback_events.extend(registry.record_failure(failure["vendor"], trigger, ts=ts))
    if fallback_events:
        rationale_parts.append("熔断/额度事件已记录")

    previous_refs = set(history.get("previous_models") or [])
    producer = request.get("implementer") or None        # review 场景：产出者 {vendor, model}
    if producer and not producer.get("family"):
        # 002 R3：按 api_ref 从两池解析 producer 的 family 注入；查不到 = 独立条目（向后兼容）。
        # api_ref 大小写不敏感 + vendor/model 兜底匹配，防调用方拼写差异导致同源注入静默失败。
        entries = cfg.strong + cfg.flash
        prod_ref = (producer.get("api_ref") or f"{producer.get('vendor')}/{producer.get('model')}").lower()
        fam_entry = next((e for e in entries if e.api_ref.lower() == prod_ref), None)
        if fam_entry is None:
            pv = (producer.get("vendor") or "").lower()
            pm = (producer.get("model") or "").lower()
            fam_entry = next((e for e in entries if e.vendor.lower() == pv and e.model.lower() == pm), None)
        if fam_entry is not None:
            producer = {**producer, "family": fam_entry.family}

    def available(pool):
        return [e for e in rules.order_pool(pool, cfg.auto.enabled)
                if registry.available(e.vendor) and quotas.available(e.vendor)
                and e.api_ref not in previous_refs]

    strong = available(cfg.strong) or rules.order_pool(cfg.strong, cfg.auto.enabled)
    flash = available(cfg.flash) or rules.order_pool(cfg.flash, cfg.auto.enabled)
    # 002 契约 §4（review F3）：复核配对只从真正可用（未封禁/未熔断/未轮换过）的强条目中选择，
    # 不随派发的池枯竭兜底回退全池——兜底不得穿透封禁；枯竭时无 reviewer，走 corrected 显式标记。
    strong_for_review = available(cfg.strong)
    registry.save()      # 持久化 available() 触发的 open→half_open 迁移（S4 观测缺口修复）
    quotas.save()

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
    jev_reviewer_ref = None
    if engine in ("auto", "jev") and not escalated:
        reviewer_pool = [e for e in strong_for_review if not producer
                         or (e.vendor != producer.get("vendor") and not rules.same_origin(e, producer))]
        try:
            jev = jev_client.decide(
                request["task_brief"], request.get("risk_tags") or [],
                [jev_ref(e) for e in (flash if pool_name == "flash" else strong)][:5],
                # 无 producer 时不做 review 配对，不问 reviewer（问项答案无消费方，白耗一轮）
                [jev_ref(e) for e in reviewer_pool][:5] if producer else [],
                timeout_ms or cfg.engine.timeout_ms,
            )
            task_class = jev.get("task_class", task_class)
            complexity = jev.get("complexity", complexity)
            implement_ref = jev.get("implement_ref")
            if implement_ref and pool_name == "flash":
                match = next((e for e in pool if e.api_ref == implement_ref), None)
                if match is not None:
                    chosen = match
            reviewer_pick = jev.get("reviewer_ref")
            if reviewer_pick and producer:
                # Jev 的 reviewer 选择参与最终配对（此前被静默丢弃）；仅采自真正可用强条目
                match = next((e for e in strong_for_review if e.api_ref == reviewer_pick), None)
                if match is not None:
                    jev_reviewer_ref = match.as_ref()
            engine_used = "jev"
        except jev_client.JevError as exc:
            if engine == "jev" and not cfg.engine.fail_open:
                raise
            fail_open = True
            rationale_parts.append(f"Jev fail-open：{exc}")
    elif engine == "rules":
        rationale_parts.append("规则引擎模式（离线）")

    # ── review 配对（FR-007/008；硬约束后置修正）─────────────────
    review_plan = {"code_reviewer": None, "plan_reviewers": [], "degrade": False,
                   "degrade_reason": "", "implementer": producer}
    corrected = False
    if producer:
        allow_degrade = cfg.review.degrade_to_single_vendor
        # 001 契约"配对硬约束后置覆盖"：Jev 选择通过硬约束则直接采纳；违反（如同源 reviewer）
        # 则丢弃并走 rules 三级降级重选，rationale 标记 [pairing-corrected]（002 R3/契约 §4）。
        if jev_reviewer_ref and rules.pairing_valid(jev_reviewer_ref, producer):
            reviewer_ref, degrade = jev_reviewer_ref, False
        else:
            if jev_reviewer_ref:
                corrected = True
            reviewer_ref, degrade = rules.code_reviewer(producer, strong_for_review, allow_degrade)
        if task_class == "design":
            review_plan["plan_reviewers"] = [{"author": producer, "reviewer": reviewer_ref}] if reviewer_ref else []
        else:
            review_plan["code_reviewer"] = reviewer_ref
        review_plan["degrade"] = degrade
        if degrade:
            # 002 R2/R8 归因唯一：层级②同源兜底必为厂商异源、层级③单厂商降级必为同厂商。
            # 此结构不变量由 test_rules_pairing ②③ 层级用例守卫（review F4：签名保持二元组以
            # 兼容 001 既有解包断言，归因推导在调用方；重排层级须同步更新两侧测试）。
            reason = ("same_origin" if (reviewer_ref or {}).get("vendor") != producer.get("vendor")
                      else "single_vendor")
            review_plan["degrade_reason"] = reason
            fallback_events.append(make_event("degrade_same_origin" if reason == "same_origin"
                                              else "degrade_single_vendor", "explicit", ts=ts))
            rationale_parts.append("真异源候选枯竭，同源模型兜底复核（已标记）" if reason == "same_origin"
                                   else "强池仅单厂商可用，review 降级（已标记）")
    if (producer and not review_plan["code_reviewer"] and not review_plan["plan_reviewers"]
            and not review_plan["degrade"]):
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
        "fallback_events": fallback_events,   # 观测：熔断/升级/降级事件随响应体回带（S4）
        "quota_hints": quotas.hints(),        # 封禁厂商提示：自动解锁时间/重置卡余量（S12.1）
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

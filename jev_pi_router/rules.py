"""规则引擎：角色→池、任务分类、复杂度、跨厂商配对、兜底顺序、auto 排序（FR-002/007/008/012）。

Jev fail-open 时本模块提供完整的确定性回退（FR-006）；Jev 结果的配对约束也在此强制修正。
"""

from __future__ import annotations

RISKY_TAGS = {"security", "auth", "authorization", "concurrency", "migration", "performance", "public_api"}


def role_pool(role: str, roles_cfg: dict) -> str:
    return roles_cfg.get(role, "strong" if role != "implement" else "flash")


def classify(task_brief: str, task_class_hint: str | None, role: str) -> str:
    """确定性默认分类（fail-open 回退）：hint 优先，否则按角色。"""
    if task_class_hint in ("design", "implement", "chore"):
        return task_class_hint
    if role == "implement":
        return "implement"
    if role in ("plan", "decision"):
        return "design"
    return "chore"


def complexity(risk_tags: list | None) -> str:
    tags = {t.lower() for t in (risk_tags or [])}
    return "high" if tags & RISKY_TAGS else "low"


def order_pool(entries: list, auto_enabled: bool = False) -> list:
    """auto 模式按（可用性, cache 亲和, 低成本, 低延迟）排序；否则保持配置顺序。"""
    enabled = [e for e in entries if e.enabled]
    if not auto_enabled:
        return enabled

    def key(entry):
        rank = {"full": 3, "partial": 2, "unknown": 1, "none": 0}[entry.cache_passthrough]
        latency = entry.latency_hint if entry.latency_hint is not None else float("inf")
        return (-rank, entry.cost_hint, latency)

    return sorted(enabled, key=key)


def pick(pool: list, avoid_refs: set | frozenset = (), avoid_vendors: set | frozenset = ()):
    """选第一个可用条目（FR-010 升级时用 avoid_vendors 换厂商）。"""
    for entry in pool:
        if entry.api_ref in avoid_refs or entry.vendor in avoid_vendors:
            continue
        return entry
    return None


def fallback_order(pool: list, chosen) -> list:
    return [e.as_ref() for e in pool if e is not chosen]


def _entry_ref(entry) -> dict:
    return entry.as_ref() if hasattr(entry, "as_ref") else dict(entry)


def code_reviewer(producer: dict, strong: list, allow_degrade: bool = True) -> tuple:
    """FR-007 + 002 FR-004（R2 三级）：reviewer 须与产出者厂商异源且非 family 同源。

    层级：① 真异源（正常）→ ② 厂商异源但 family 同源兜底（allow_degrade）→
    ③ 仅剩同厂商单厂商降级（allow_degrade，既有行为）。②/③ 均 degrade=True，
    归因由调用方按派回条目 vendor 是否等于 producer.vendor 推导（②必异商/③必同商）。
    返回 (reviewer_ref | None, degrade: bool)——签名保持 001 兼容（SC-002）。
    """
    candidates = [e for e in strong if e.enabled]
    pv = producer.get("vendor")
    for entry in candidates:
        if entry.vendor != pv and not same_origin(entry, producer):
            return _entry_ref(entry), False
    if allow_degrade:
        for entry in candidates:
            if entry.vendor != pv:                # family 同源但厂商异源：兜底（②）
                return _entry_ref(entry), True
        if candidates:
            return _entry_ref(candidates[0]), True  # 单厂商降级（③）
    return None, False


def plan_reviewer(author: dict, strong: list, allow_degrade: bool = True) -> tuple:
    """FR-008：计划/决策互审——reviewer 与作者异源强厂商（双向由两次调用成立）。"""
    return code_reviewer(author, strong, allow_degrade)


def _family(obj) -> str:
    """family 取值的 dict/ModelEntry 双形态适配（producer 为请求字典，候选为 ModelEntry）。"""
    return (obj.get("family") if isinstance(obj, dict) else getattr(obj, "family", "")) or ""


def same_origin(a, b) -> bool:
    """002 FR-004/R1：双方 family 均非空且相等 ⇒ 同底层模型（同源）。"""
    fam = _family(a)
    return bool(fam) and fam == _family(b)


def pairing_valid(reviewer_ref: dict | None, producer: dict) -> bool:
    """配对硬约束校验（Jev 结果后置覆盖用）：厂商异源且非 family 同源（002 R3）。"""
    return (bool(reviewer_ref) and reviewer_ref.get("vendor") != producer.get("vendor")
            and not same_origin(reviewer_ref, producer))

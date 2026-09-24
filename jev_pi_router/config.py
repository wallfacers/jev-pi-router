"""配置加载与校验（contracts/config-schema.md；data-model.md 实体）。

FR-001/013：两池非空、增删厂商只改配置；每次决策重新加载（decide 每次调用 load_config）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

CACHE_RANK = {"full": 3, "partial": 2, "unknown": 1, "none": 0}
VALID_CACHE = set(CACHE_RANK)
ROLES = ("orchestrator", "plan", "decision", "review", "fallback_arbiter", "implement")


class ConfigError(Exception):
    """配置缺失/非法（CLI 退出码 2）。"""


@dataclass(frozen=True)
class ModelEntry:
    vendor: str
    model: str
    api_ref: str
    pool: str
    cost_hint: float = 1.0
    latency_hint: float | None = None
    cache_passthrough: str = "unknown"
    enabled: bool = True
    family: str = ""            # 同源组标识（002 FR-004/R1）：空 = 独立；两条目同非空且相等 ⇒ 同源

    def as_ref(self) -> dict:
        ref = {"vendor": self.vendor, "model": self.model, "api_ref": self.api_ref, "pool": self.pool}
        if self.family:
            ref["family"] = self.family
        return ref


@dataclass(frozen=True)
class ReviewCfg:
    code: str = "cross_vendor_strong"
    plan_decision: str = "mutual_strong"
    max_rounds: int = 2
    degrade_to_single_vendor: bool = True


@dataclass(frozen=True)
class EngineCfg:
    primary: str = "jev"
    fallback: str = "rules"
    timeout_ms: int = 2000
    fail_open: bool = True


@dataclass(frozen=True)
class FallbackCfg:
    fault_transfer_max_attempts: int = 2
    quality_review_fails: int = 2
    escalate_to: str = "strong"
    switch_vendor: bool = True
    breaker_failures: int = 3
    breaker_cooldown_sec: int = 300
    breaker_window_sec: int = 3600            # v1.4 api_ref 滑窗长度
    breaker_window_failures: int = 2          # 滑窗内失败数阈值 → 条目冷却
    breaker_api_ref_cooldown_sec: int = 300   # 条目冷却时长


@dataclass(frozen=True)
class AutoCfg:
    enabled: bool = False
    probe_interval_min: int = 30
    signals: tuple = ("availability", "cost", "latency", "cache")


@dataclass(frozen=True)
class BModeCfg:
    allow_per_turn_switch_on_fresh_session: bool = True


@dataclass
class RouterConfig:
    strong: list
    flash: list
    roles: dict
    review: ReviewCfg
    engine: EngineCfg
    fallback: FallbackCfg
    auto: AutoCfg
    b_mode: BModeCfg
    source_path: str = ""

    def pool(self, name: str) -> list:
        return self.strong if name == "strong" else self.flash


def _parse_entries(raw: list, pool: str, errors: list) -> list:
    entries = []
    for item in raw or []:
        missing = [k for k in ("vendor", "model", "api_ref") if not item.get(k)]
        if missing:
            errors.append(f"{pool} 条目缺少字段 {missing}: {item}")
            continue
        cp = item.get("cache_passthrough", "unknown")
        if cp not in VALID_CACHE:
            errors.append(f"{item['api_ref']} cache_passthrough 非法: {cp}（应为 {sorted(VALID_CACHE)}）")
            continue
        fam = item.get("family")
        entries.append(
            ModelEntry(
                vendor=item["vendor"],
                model=item["model"],
                api_ref=item["api_ref"],
                pool=pool,
                cost_hint=float(item.get("cost_hint", 1.0)),
                latency_hint=item.get("latency_hint"),
                cache_passthrough=cp,
                enabled=bool(item.get("enabled", True)),
                family=fam if isinstance(fam, str) else "",   # 非字符串/缺失按缺省容错（contracts/config-schema.md §1）
            )
        )
    return entries


def load_config(path: str | os.PathLike | None = None) -> RouterConfig:
    """加载并校验 router.config.yaml（FR-013 校验规则见 contracts/config-schema.md）。"""
    resolved = Path(path or os.environ.get("JEV_PI_ROUTER_CONFIG", "router.config.yaml"))
    if not resolved.exists():
        raise ConfigError(f"配置文件不存在: {resolved}")
    try:
        raw = yaml.safe_load(resolved.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"YAML 解析失败: {exc}") from exc

    errors: list[str] = []
    strong = _parse_entries(raw.get("strong_pool"), "strong", errors)
    flash = _parse_entries(raw.get("flash_pool"), "flash", errors)
    if not strong:
        errors.append("strong_pool 为空（FR-001）")
    if not flash:
        errors.append("flash_pool 为空（FR-001）")

    roles_raw = raw.get("roles") or {}
    roles = {}
    for role in ROLES:
        pool_name = roles_raw.get(role, "strong" if role != "implement" else "flash")
        if pool_name not in ("strong", "flash"):
            errors.append(f"roles.{role} 非法: {pool_name}")
        roles[role] = pool_name
    if roles.get("implement") != "flash":
        errors.append("roles.implement 必须为 flash（FR-003）")

    review_raw = raw.get("review") or {}
    review = ReviewCfg(
        code=review_raw.get("code", "cross_vendor_strong"),
        plan_decision=review_raw.get("plan_decision", "mutual_strong"),
        max_rounds=int(review_raw.get("max_rounds", 2)),
        degrade_to_single_vendor=bool(review_raw.get("degrade_to_single_vendor", True)),
    )
    if review.max_rounds < 1:
        errors.append("review.max_rounds 必须 ≥ 1")

    engine_raw = raw.get("decision_engine") or {}
    engine = EngineCfg(
        primary=engine_raw.get("primary", "jev"),
        fallback=engine_raw.get("fallback", "rules"),
        timeout_ms=int(engine_raw.get("timeout_ms", 2000)),
        fail_open=bool(engine_raw.get("fail_open", True)),
    )
    if engine.primary not in ("jev", "rules"):
        errors.append(f"decision_engine.primary 非法: {engine.primary}")
    if not engine.fail_open:
        errors.append("decision_engine.fail_open 必须为 true 方可投产（FR-006）")

    fb_raw = raw.get("fallback") or {}
    ft = fb_raw.get("fault_transfer") or {}
    qu = fb_raw.get("quality_upgrade") or {}
    br = fb_raw.get("breaker") or {}
    fallback = FallbackCfg(
        fault_transfer_max_attempts=int(ft.get("max_attempts", 2)),
        quality_review_fails=int(qu.get("review_fails", 2)),
        escalate_to=qu.get("escalate_to", "strong"),
        switch_vendor=bool(qu.get("switch_vendor", True)),
        breaker_failures=int(br.get("consecutive_failures", 3)),
        breaker_cooldown_sec=int(br.get("cooldown_sec", 300)),
        breaker_window_sec=int(br.get("window_sec", 3600)),
        breaker_window_failures=int(br.get("window_failures", 2)),
        breaker_api_ref_cooldown_sec=int(br.get("api_ref_cooldown_sec", 300)),
    )
    for name, value in (("window_sec", fallback.breaker_window_sec),
                        ("window_failures", fallback.breaker_window_failures),
                        ("api_ref_cooldown_sec", fallback.breaker_api_ref_cooldown_sec)):
        if value < 1:
            errors.append(f"fallback.breaker.{name} 必须 ≥ 1（当前 {value}）")
    if not 1 <= fallback.quality_review_fails <= review.max_rounds:
        errors.append(
            f"fallback.quality_upgrade.review_fails({fallback.quality_review_fails}) "
            f"必须在 [1, review.max_rounds={review.max_rounds}] 区间"
        )

    auto_raw = raw.get("auto_mode") or {}
    auto = AutoCfg(
        enabled=bool(auto_raw.get("enabled", False)),
        probe_interval_min=int(auto_raw.get("probe_interval_min", 30)),
        signals=tuple(auto_raw.get("signals") or ("availability", "cost", "latency", "cache")),
    )
    b_raw = raw.get("b_mode") or {}
    b_mode = BModeCfg(allow_per_turn_switch_on_fresh_session=bool(b_raw.get("allow_per_turn_switch_on_fresh_session", True)))

    if errors:
        raise ConfigError("; ".join(errors))
    return RouterConfig(
        strong=strong,
        flash=flash,
        roles=roles,
        review=review,
        engine=engine,
        fallback=fallback,
        auto=auto,
        b_mode=b_mode,
        source_path=str(resolved),
    )

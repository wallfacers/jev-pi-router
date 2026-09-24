"""双轨兜底状态机（FR-009/010；状态转移见 data-model.md）。

- 故障转移：同档换厂商（decide 输出 fallback_order，调用方重派）；同厂商连续 N 次失败熔断。
- 质量升级：同一实现连续 N 轮 review_reject → 强模型换厂商重做。
- 熔断状态持久化到 $JEV_PI_ROUTER_HOME/state.json（CLI 每次调用是新进程）。
- v1.4 条目级滑窗冷却（api_ref）：与 vendor 级熔断互补——滑窗内累计 N 次失败即冷却该
  条目（relay 单条端点间歇故障不至因连续计数被成功清零而永远够不着阈值）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from .log import home_dir

# 已知 trigger 值参考集（与 specs/001-pi-model-routing/contracts/decision-log-schema.md
# FallbackEvent.trigger 同步）。trigger 是调用方可扩展的开集，实现按 vendor_failures[].trigger
# 原样透传、不做运行时校验；本集合仅作文档锚点，不参与任何运行行为。
# v1.4 新增 empty_response（零内容零 token 退化循环，见 contracts/decision-cli.md v1.4 节）。
TRIGGERS = {"timeout", "http_5xx", "auth", "quota", "rate_limit", "review_reject",
            "explicit", "auto", "manual", "reset_card", "activity", "empty_response"}


def make_event(type_: str, trigger: str, from_model: str | None = None, to_model: str | None = None,
               attempt: int = 1, ts: str = "") -> dict:
    return {"type": type_, "from_model": from_model, "to_model": to_model,
            "trigger": trigger, "attempt": attempt, "ts": ts}


@dataclass
class Breaker:
    consecutive_failures: int = 0
    state: str = "closed"            # closed | open | half_open
    open_until: float = 0.0          # epoch 秒


@dataclass
class ApiRefState:
    """按 api_ref 的滑窗失败记录（v1.4）。

    failures 为窗口内失败的 epoch 秒时间戳（惰性 prune）；cooldown_until >0 = 冷却中。
    与 vendor 级 Breaker 互补：Breaker 计连续失败（成功即清零），本状态计窗口累计。
    """

    failures: list = field(default_factory=list)
    cooldown_until: float = 0.0


@dataclass
class BreakerRegistry:
    """按 vendor 的熔断器（closed --N 连败--> open --冷却--> half_open --成功--> closed）。

    另含 v1.4 条目级滑窗冷却：同一 api_ref 在 window_sec 内累计 window_failures 次
    熔断类失败 → 该条目冷却 api_ref_cooldown_sec（成功回报即时解除）。
    """

    threshold: int = 3
    cooldown_sec: int = 300
    window_sec: float = 3600.0       # api_ref 滑窗长度
    window_failures: int = 2         # 滑窗内失败数阈值 → 条目冷却
    api_ref_cooldown_sec: int = 300  # 条目冷却时长
    now_fn: callable = None          # 测试注入
    state: dict = field(default_factory=dict)
    api_refs: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.now_fn is None:
            import time
            self.now_fn = time.time

    # ── 持久化 ────────────────────────────────────────────────
    def _state_file(self):
        return home_dir() / "state.json"

    def load(self) -> None:
        path = self._state_file()
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            self.state = {vendor: Breaker(**spec) for vendor, spec in data.get("breakers", {}).items()}
            # 旧 state.json 无 "api_refs" 键 → 自然兼容（v1.4 新增顶层键，breakers 形状不变）
            self.api_refs = {ref: ApiRefState(failures=[float(t) for t in (spec.get("failures") or [])],
                                              cooldown_until=float(spec.get("cooldown_until") or 0.0))
                             for ref, spec in (data.get("api_refs") or {}).items()}

    def save(self) -> None:
        path = self._state_file()
        self._sweep()
        data = {"breakers": {v: vars(b) for v, b in self.state.items()},
                "api_refs": {r: vars(s) for r, s in self.api_refs.items()}}
        path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

    # ── 条目级滑窗（v1.4）─────────────────────────────────────
    def _prune(self, st: ApiRefState) -> None:
        """丢弃滑窗外的时间戳（惰性调用；正确性由调用点保证）。"""
        cutoff = self.now_fn() - self.window_sec
        st.failures = [t for t in st.failures if t >= cutoff]

    def _sweep(self) -> None:
        """写盘前全量清扫：窗口内无失败且冷却已过的条目删除（state.json 有界）。"""
        now = self.now_fn()
        cutoff = now - self.window_sec
        for ref in list(self.api_refs):
            st = self.api_refs[ref]
            if not any(t >= cutoff for t in st.failures) and now >= st.cooldown_until:
                del self.api_refs[ref]

    # ── 状态转移 ──────────────────────────────────────────────
    def _transition(self, vendor: str) -> list:
        """冷却到期 closed 迁移检查，返回事件。"""
        breaker = self.state.setdefault(vendor, Breaker())
        events = []
        now = self.now_fn()
        if breaker.state == "open" and now >= breaker.open_until:
            breaker.state = "half_open"
        return events

    def available(self, vendor: str) -> bool:
        self._transition(vendor)
        return self.state[vendor].state != "open"

    def record_failure(self, vendor: str, trigger: str, ts: str = "", api_ref: str = "") -> list:
        breaker = self.state.setdefault(vendor, Breaker())
        self._transition(vendor)
        events = []
        breaker.consecutive_failures += 1
        if breaker.state == "half_open":
            breaker.state = "open"
            breaker.open_until = self.now_fn() + self.cooldown_sec
            breaker.consecutive_failures = 1
            events.append(make_event("breaker_open", trigger, ts=ts))
        elif breaker.state == "closed" and breaker.consecutive_failures >= self.threshold:
            breaker.state = "open"
            breaker.open_until = self.now_fn() + self.cooldown_sec
            events.append(make_event("breaker_open", trigger, ts=ts))
        if api_ref:                          # v1.4：条目级滑窗（与 vendor 级互补）
            events.extend(self._record_api_ref_failure(api_ref, trigger, ts))
        return events

    def _record_api_ref_failure(self, api_ref: str, trigger: str, ts: str) -> list:
        """滑窗累计失败；窗口内达阈值 → 条目冷却。冷却中重复失败不重复开断/发事件。"""
        now = self.now_fn()
        st = self.api_refs.setdefault(api_ref, ApiRefState())
        st.failures.append(now)
        self._prune(st)
        if len(st.failures) >= self.window_failures and now >= st.cooldown_until:
            st.cooldown_until = now + self.api_ref_cooldown_sec
            return [make_event("api_ref_cooldown", trigger, to_model=api_ref, ts=ts)]
        return []

    def record_success(self, vendor: str, ts: str = "", api_ref: str = "") -> list:
        breaker = self.state.setdefault(vendor, Breaker())
        self._transition(vendor)
        events = []
        if breaker.state != "closed":       # half_open 正常闭合；open 下成功回报=强制闭合
            events.append(make_event("breaker_close", "explicit", ts=ts))
        breaker.state = "closed"
        breaker.consecutive_failures = 0
        breaker.open_until = 0.0
        if api_ref:                          # v1.4：条目级成功回报（清窗 + 解除冷却）
            events.extend(self._record_api_ref_success(api_ref, ts))
        return events

    def _record_api_ref_success(self, api_ref: str, ts: str) -> list:
        """成功即清窗；原在冷却中则解除并产生 api_ref_recover（无记录为幂等空操作）。"""
        st = self.api_refs.get(api_ref)
        if st is None:
            return []
        events = []
        if self.now_fn() < st.cooldown_until:
            events.append(make_event("api_ref_recover", "explicit", to_model=api_ref, ts=ts))
        st.cooldown_until = 0.0
        st.failures = []
        return events

    def api_ref_available(self, api_ref: str) -> bool:
        """条目级可用性（纯判断；到期清零随 save 落盘，模式同 _transition 迁移留痕）。"""
        st = self.api_refs.get(api_ref)
        if st is None:
            return True
        if st.cooldown_until and self.now_fn() >= st.cooldown_until:
            st.cooldown_until = 0.0
        return not st.cooldown_until

    def demotion_set(self) -> set:
        """滑窗内有失败记录且未在冷却中的条目集合（用于选路降权）。

        冷却中的条目已被 available 排除，无需降权；冷却已过但窗口内仍有失败记录的
        条目包含在内——间歇故障条目持续被压制，只有健康条目不可用时才会被选中。
        """
        now = self.now_fn()
        refs = set()
        for ref, st in self.api_refs.items():
            if st.cooldown_until and now < st.cooldown_until:
                continue
            self._prune(st)
            if st.failures:
                refs.add(ref)
        return refs


def quality_escalated(review_fail_count: int, threshold: int) -> bool:
    """FR-010：连续 N 轮 review_reject → 升级强模型（调用方负责换厂商）。"""
    return review_fail_count >= threshold

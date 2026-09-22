"""双轨兜底状态机（FR-009/010；状态转移见 data-model.md）。

- 故障转移：同档换厂商（decide 输出 fallback_order，调用方重派）；同厂商连续 N 次失败熔断。
- 质量升级：同一实现连续 N 轮 review_reject → 强模型换厂商重做。
- 熔断状态持久化到 $JEV_PI_ROUTER_HOME/state.json（CLI 每次调用是新进程）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from .log import home_dir

TRIGGERS = {"timeout", "http_5xx", "quota", "rate_limit", "review_reject", "explicit"}


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
class BreakerRegistry:
    """按 vendor 的熔断器（closed --N 连败--> open --冷却--> half_open --成功--> closed）。"""

    threshold: int = 3
    cooldown_sec: int = 300
    now_fn: callable = None          # 测试注入
    state: dict = field(default_factory=dict)

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

    def save(self) -> None:
        path = self._state_file()
        data = {"breakers": {v: vars(b) for v, b in self.state.items()}}
        path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

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

    def record_failure(self, vendor: str, trigger: str, ts: str = "") -> list:
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
        return events

    def record_success(self, vendor: str, ts: str = "") -> list:
        breaker = self.state.setdefault(vendor, Breaker())
        self._transition(vendor)
        events = []
        if breaker.state == "half_open":
            events.append(make_event("breaker_close", "explicit", ts=ts))
        breaker.state = "closed"
        breaker.consecutive_failures = 0
        breaker.open_until = 0.0
        return events


def quality_escalated(review_fail_count: int, threshold: int) -> bool:
    """FR-010：连续 N 轮 review_reject → 升级强模型（调用方负责换厂商）。"""
    return review_fail_count >= threshold

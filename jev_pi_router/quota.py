"""供应商套餐额度封禁（S12，Quota）——与熔断分离：额度耗尽不是故障。

429 两种语义（派发纪律见 SKILL.md）：
- rate_limit（并发/瞬时限流）→ 重试即可，不进入任何状态（decide.py 直接忽略）；
- quota（套餐/周限额额度尽）→ 立即封禁该厂商，不再派发，除非：
  ① 带 quota_until（重置时间戳）到期自动解锁（quota_unlock: auto）；
  ② vendor_unlock 回报 / jev-pi-doctor unlock 人工解锁（quota_unlock: manual）；
  不带 quota_until = 无限期封禁（用户语义：下次不选了，除非解锁）。

持久化 ~/.jev-pi-router/quotas.json，按厂商记录（key_id 仅留痕，不参与路由）。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass

from .fallback import make_event
from .log import home_dir


@dataclass
class Quota:
    reason: str = "quota"
    blocked_until: float = 0.0      # >0 到期自动解锁；0 = 无限期，需人工解锁
    key_id: str = ""                # 同厂商多 key 时的留痕标识（不参与路由）
    blocked_at: str = ""


class QuotaRegistry:
    def __init__(self, now_fn=None):
        self.now_fn = now_fn or time.time
        self.quotas: dict = {}

    def _state_file(self):
        return home_dir() / "quotas.json"

    def load(self) -> None:
        path = self._state_file()
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            self.quotas = {vendor: Quota(**spec) for vendor, spec in data.get("quotas", {}).items()}

    def save(self) -> None:
        path = self._state_file()
        data = {"quotas": {v: vars(q) for v, q in self.quotas.items()}}
        path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

    def available(self, vendor: str) -> bool:
        """纯判断（到期回收在 expire_due 统一做，保证事件可留痕）。"""
        return vendor not in self.quotas

    def expire_due(self, ts: str = "") -> list:
        due = [v for v, q in self.quotas.items() if q.blocked_until and self.now_fn() >= q.blocked_until]
        events = []
        for vendor in due:
            del self.quotas[vendor]
            events.append(make_event("quota_unlock", "auto", to_model=vendor, ts=ts))
        return events

    def block(self, vendor: str, quota_until: float = 0.0, reason: str = "quota",
              key_id: str = "", ts: str = "") -> list:
        self.quotas[vendor] = Quota(reason=reason, blocked_until=float(quota_until or 0.0),
                                    key_id=key_id or "", blocked_at=ts)
        return [make_event("quota_block", reason, to_model=vendor, ts=ts)]

    def unlock(self, vendor: str, ts: str = "") -> list:
        if self.quotas.pop(vendor, None) is None:
            return []
        return [make_event("quota_unlock", "manual", to_model=vendor, ts=ts)]


__all__ = ["Quota", "QuotaRegistry"]

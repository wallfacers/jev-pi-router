"""供应商套餐额度封禁（S12，Quota）——与熔断分离：额度耗尽不是故障。

429 两种语义（派发纪律见 SKILL.md）：
- rate_limit（并发/瞬时限流）→ 重试即可，不进入任何状态（decide.py 直接忽略）；
- quota（套餐/周限额额度尽）→ 立即封禁该厂商，不再派发，除非：
  ① 带 quota_until（重置时间戳）到期自动解锁（quota_unlock: auto）；
  ② 人工解锁（quota_unlock: <reason>）——**任何时刻都可提前解锁**，覆盖 quota_until，
     覆盖"活动提前重置/不到时间即重置"的场景（S12.1）：
     - reason=manual：套餐自然重置后人工确认；
     - reason=reset_card：使用重置卡，扣减该厂商重置卡余额（若有，事件带 cards_left；
       无卡也放行——用户可能在厂商侧已用卡，fail-open 不拦人）；
     - reason=activity：活动重置，不扣卡；
  不带 quota_until = 无限期封禁（用户语义：下次不选了，除非解锁）。

重置卡台账：cards（按厂商），doctor cards --add N 记入；解锁时自动扣减。
持久化 ~/.jev-pi-router/quotas.json：{"quotas": {...}, "cards": {...}}，
按厂商记录（key_id 仅留痕，不参与路由）。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime

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
        self.cards: dict = {}

    def _state_file(self):
        return home_dir() / "quotas.json"

    def load(self) -> None:
        path = self._state_file()
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            self.quotas = {vendor: Quota(**spec) for vendor, spec in data.get("quotas", {}).items()}
            self.cards = {v: int(n) for v, n in data.get("cards", {}).items()}

    def save(self) -> None:
        path = self._state_file()
        data = {"quotas": {v: vars(q) for v, q in self.quotas.items()}, "cards": self.cards}
        path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

    # ── 重置卡台账 ────────────────────────────────────────────
    def cards_left(self, vendor: str) -> int:
        return max(0, int(self.cards.get(vendor, 0)))

    def add_cards(self, vendor: str, n: int) -> int:
        balance = max(0, self.cards_left(vendor) + int(n))
        self.cards[vendor] = balance
        return balance

    # ── 状态 ──────────────────────────────────────────────────
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

    def unlock(self, vendor: str, reason: str = "manual", key_id: str = "", ts: str = "") -> list:
        """人工解锁：始终可提前覆盖 quota_until（活动/重置卡提前重置场景）。

        reason=reset_card 时扣减重置卡余额（有则扣，无则放行），事件附 cards_left。
        未封禁的厂商解锁为幂等空操作。
        """
        if self.quotas.pop(vendor, None) is None:
            return []
        event = make_event("quota_unlock", reason or "manual", to_model=vendor, ts=ts)
        if reason == "reset_card":
            left = self.cards_left(vendor)
            if left > 0:
                left -= 1
                self.cards[vendor] = left
            event["cards_left"] = left
        if key_id:
            event["key_id"] = key_id
        return [event]

    def hints(self) -> list:
        """封禁中厂商的人话提示（自动解锁时间/重置卡余量），供主 Agent 询问用户。"""
        out = []
        for vendor, q in sorted(self.quotas.items()):
            left = self.cards_left(vendor)
            parts = [f"{vendor} 额度封禁中（{q.reason}）"]
            if q.blocked_until:
                parts.append(datetime.fromtimestamp(q.blocked_until).strftime("%Y-%m-%d %H:%M") + " 自动解锁")
            if left > 0:
                parts.append(f"可用重置卡解锁（余{left}张）")
            else:
                parts.append("套餐重置/活动/重置卡后可人工解锁")
            out.append("，".join(parts))
        return out


__all__ = ["Quota", "QuotaRegistry"]

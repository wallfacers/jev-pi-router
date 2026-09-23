"""S12 套餐额度封禁：quota 429 封禁/自动解锁/人工解锁，rate_limit 瞬时不限流。"""

import time
from copy import deepcopy

from jev_pi_router.decide import decide


def _req(sample_request, **overrides):
    request = deepcopy(sample_request)
    request.update(overrides)
    return request


def _types(response):
    return [e["type"] for e in response["fallback_events"]]


def test_quota_429_blocks_vendor_immediately(sample_request, config_path):
    """套餐额度 429 一次即封禁（不等 3 连败），不再选中该厂商。"""
    response = decide(_req(sample_request,
                           vendor_failures=[{"vendor": "deepseek", "trigger": "quota"}]),
                      engine="rules", config_path=config_path)
    assert "quota_block" in _types(response)
    assert (response["chosen"] or {}).get("vendor") != "deepseek"


def test_quota_indefinite_without_until(sample_request, config_path):
    """无 quota_until = 无限期封禁，后续决策持续跳过（除非解锁）。"""
    decide(_req(sample_request,
                vendor_failures=[{"vendor": "deepseek", "trigger": "quota"}]),
           engine="rules", config_path=config_path)
    response = decide(_req(sample_request), engine="rules", config_path=config_path)
    assert (response["chosen"] or {}).get("vendor") != "deepseek"


def test_rate_limit_429_is_transient(sample_request, config_path):
    """并发/瞬时 429 重试即可：不计熔断、不封禁、不影响后续派发。"""
    response = decide(_req(sample_request,
                           vendor_failures=[{"vendor": "deepseek", "trigger": "rate_limit"}] * 3),
                      engine="rules", config_path=config_path)
    assert not response["fallback_events"]
    assert (response["chosen"] or {}).get("vendor") == "deepseek"


def test_quota_auto_unlock_at_until(sample_request, config_path):
    """带 quota_until：到期自动解锁（quota_unlock: auto），厂商回到可派池。"""
    decide(_req(sample_request,
                vendor_failures=[{"vendor": "deepseek", "trigger": "quota",
                                  "quota_until": time.time() - 1}]),
           engine="rules", config_path=config_path)
    response = decide(_req(sample_request), engine="rules", config_path=config_path)
    assert "quota_unlock" in _types(response)
    assert (response["chosen"] or {}).get("vendor") == "deepseek"


def test_quota_manual_unlock_via_request(sample_request, config_path):
    """vendor_unlock 人工解锁（quota_unlock: manual），厂商回到首选。"""
    decide(_req(sample_request,
                vendor_failures=[{"vendor": "deepseek", "trigger": "quota"}]),
           engine="rules", config_path=config_path)
    response = decide(_req(sample_request, vendor_unlock=[{"vendor": "deepseek"}]),
                      engine="rules", config_path=config_path)
    assert "quota_unlock" in _types(response)
    assert (response["chosen"] or {}).get("vendor") == "deepseek"


# ---- S12.1 重置卡/活动提前重置 ----

def test_early_manual_unlock_overrides_future_until(sample_request, config_path):
    """人工解锁可提前覆盖未来的 quota_until（活动提前重置）。"""
    decide(_req(sample_request,
                vendor_failures=[{"vendor": "deepseek", "trigger": "quota",
                                  "quota_until": time.time() + 86400}]),
           engine="rules", config_path=config_path)
    response = decide(_req(sample_request, vendor_unlock=[{"vendor": "deepseek", "reason": "activity"}]),
                      engine="rules", config_path=config_path)
    assert "quota_unlock" in _types(response)
    assert (response["chosen"] or {}).get("vendor") == "deepseek"


def test_reset_card_unlock_consumes_card(sample_request, config_path):
    """reset_card 解锁扣减 1 张卡，事件 trigger=reset_card 且带 cards_left。"""
    from jev_pi_router.quota import QuotaRegistry
    decide(_req(sample_request,
                vendor_failures=[{"vendor": "deepseek", "trigger": "quota"}]),
           engine="rules", config_path=config_path)
    registry = QuotaRegistry()
    registry.load()                       # 先 load 再改，避免覆盖已有封禁状态
    registry.add_cards("deepseek", 2)
    registry.save()

    response = decide(_req(sample_request, vendor_unlock=[{"vendor": "deepseek", "reason": "reset_card"}]),
                      engine="rules", config_path=config_path)
    events = [e for e in response["fallback_events"] if e["type"] == "quota_unlock"]
    assert events and events[0]["trigger"] == "reset_card"
    assert events[0]["cards_left"] == 1
    registry = QuotaRegistry()
    registry.load()
    assert registry.cards_left("deepseek") == 1


def test_reset_card_without_cards_still_unlocks(sample_request, config_path):
    """无卡也放行（用户在厂商侧已用卡，fail-open 不拦人），事件 cards_left=0。"""
    decide(_req(sample_request,
                vendor_failures=[{"vendor": "deepseek", "trigger": "quota"}]),
           engine="rules", config_path=config_path)
    response = decide(_req(sample_request, vendor_unlock=[{"vendor": "deepseek", "reason": "reset_card"}]),
                      engine="rules", config_path=config_path)
    events = [e for e in response["fallback_events"] if e["type"] == "quota_unlock"]
    assert events and events[0]["cards_left"] == 0
    assert (response["chosen"] or {}).get("vendor") == "deepseek"


def test_activity_unlock_keeps_cards(sample_request, config_path):
    """activity（活动重置）解锁不扣卡。"""
    from jev_pi_router.quota import QuotaRegistry
    decide(_req(sample_request,
                vendor_failures=[{"vendor": "deepseek", "trigger": "quota"}]),
           engine="rules", config_path=config_path)
    registry = QuotaRegistry()
    registry.load()
    registry.add_cards("deepseek", 2)
    registry.save()
    decide(_req(sample_request, vendor_unlock=[{"vendor": "deepseek", "reason": "activity"}]),
           engine="rules", config_path=config_path)
    registry = QuotaRegistry()
    registry.load()
    assert registry.cards_left("deepseek") == 2


def test_quota_hints_when_blocked(sample_request, config_path):
    """quota_hints 提示自动解锁时间/重置卡余量（供主 Agent 询问用户）。"""
    from jev_pi_router.quota import QuotaRegistry
    registry = QuotaRegistry()
    registry.load()
    registry.add_cards("deepseek", 1)
    registry.save()
    response = decide(_req(sample_request,
                           vendor_failures=[{"vendor": "deepseek", "trigger": "quota",
                                             "quota_until": time.time() + 86400}]),
                      engine="rules", config_path=config_path)
    hints = response["quota_hints"]
    assert hints and "deepseek" in hints[0]
    assert "重置卡" in hints[0] and "余1张" in hints[0]
    assert "自动解锁" in hints[0]


def test_quota_hint_includes_year(sample_request, config_path):
    """003 修复2：自动解锁提示带年份，跨年封禁时不会误导为同年。"""
    import datetime
    until = datetime.datetime(2030, 6, 1, 12, 0).timestamp()
    response = decide(_req(sample_request,
                           vendor_failures=[{"vendor": "deepseek", "trigger": "quota",
                                             "quota_until": until}]),
                      engine="rules", config_path=config_path)
    assert "2030-" in response["quota_hints"][0]

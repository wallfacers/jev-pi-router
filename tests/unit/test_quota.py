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

"""auto 模式评分单测（FR-012；cache 权重序 full > partial > unknown > none）。"""

from jev_pi_router.config import ModelEntry
from jev_pi_router import rules


def entry(vendor, model, **kw):
    return ModelEntry(vendor=vendor, model=model, api_ref=f"{vendor}/{model}", pool="flash", **kw)


def test_auto_off_preserves_config_order():
    pool = [entry("opencode-go", "deepseek-flash", cache_passthrough="none"),
            entry("deepseek", "deepseek-flash", cache_passthrough="full")]
    assert [e.vendor for e in rules.order_pool(pool, auto_enabled=False)] == ["opencode-go", "deepseek"]


def test_auto_on_sorts_by_cache_passthrough():
    pool = [entry("opencode-go", "deepseek-flash", cache_passthrough="none", cost_hint=0.1),
            entry("glm", "glm-5.3-flash", cache_passthrough="unknown", cost_hint=0.15),
            entry("relay", "cmd-deepseek-v4.1-flash", cache_passthrough="partial", cost_hint=0.1),
            entry("deepseek", "deepseek-flash", cache_passthrough="full", cost_hint=0.15)]
    order = [e.vendor for e in rules.order_pool(pool, auto_enabled=True)]
    assert order == ["deepseek", "relay", "glm", "opencode-go"]


def test_auto_cost_tiebreak_same_cache_rank():
    pool = [entry("glm", "glm-5.3-flash", cost_hint=0.5, cache_passthrough="unknown"),
            entry("relay", "cmd-deepseek-v4.1-flash", cost_hint=0.1, cache_passthrough="unknown")]
    order = [e.vendor for e in rules.order_pool(pool, auto_enabled=True)]
    assert order == ["relay", "glm"]


def test_disabled_entries_excluded():
    pool = [entry("deepseek", "deepseek-flash", enabled=False, cache_passthrough="full"),
            entry("glm", "glm-5.3-flash", cache_passthrough="unknown")]
    order = [e.vendor for e in rules.order_pool(pool, auto_enabled=True)]
    assert order == ["glm"]


# ── 002 US3：qianwenai 按 full 档参与 auto 排序（FR-007/008、SC-005）────────────

def test_auto_qianwenai_full_tier_with_cost_tiebreak():
    pool = [entry("opencode-go", "deepseek-flash", cache_passthrough="full", cost_hint=0.1),
            entry("qianwenai", "qwen3.8-flash", cache_passthrough="full", cost_hint=0.15),
            entry("qianwenai", "deepseek-v4.1-flash", cache_passthrough="full", cost_hint=0.1),
            entry("glm", "glm-5.3-flash", cache_passthrough="unknown", cost_hint=0.15)]
    order = [e.api_ref for e in rules.order_pool(pool, auto_enabled=True)]
    assert order == ["opencode-go/deepseek-flash", "qianwenai/deepseek-v4.1-flash",
                     "qianwenai/qwen3.8-flash", "glm/glm-5.3-flash"]   # full 档在前、cost 平权破序


def test_auto_qianwenai_not_demoted_for_missing_info():
    """SC-005：声明 full 后，qianwenai 不再因信息缺失被 unknown 档条目压后。"""
    pool = [entry("glm", "glm-5.3-flash", cache_passthrough="unknown", cost_hint=0.01),
            entry("qianwenai", "qwen3.8-flash", cache_passthrough="full", cost_hint=0.15)]
    order = [e.vendor for e in rules.order_pool(pool, auto_enabled=True)]
    assert order == ["qianwenai", "glm"]

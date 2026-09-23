"""跨厂商配对硬约束单测（FR-007/008/US2-3；quickstart V3）。"""

from jev_pi_router.config import ModelEntry
from jev_pi_router import rules


def entry(vendor, model, pool="strong", **kw):
    return ModelEntry(vendor=vendor, model=model, api_ref=f"{vendor}/{model}", pool=pool, **kw)


STRONG = [entry("mimo", "mimo-v2.6-pro"), entry("glm", "glm-5.3")]


def test_code_reviewer_cross_vendor():
    producer = {"vendor": "glm", "model": "glm-5.3-flash"}
    reviewer, degrade = rules.code_reviewer(producer, STRONG)
    assert reviewer["vendor"] == "mimo" and degrade is False
    assert rules.pairing_valid(reviewer, producer)


def test_code_reviewer_never_same_vendor():
    producer = {"vendor": "mimo", "model": "mimo-v2.6-flash"}
    reviewer, _ = rules.code_reviewer(producer, STRONG)
    assert reviewer["vendor"] == "glm"


def test_single_vendor_degrades_with_flag():
    only_mimo = [entry("mimo", "mimo-v2.6-pro"), entry("mimo", "mimo-v2.6-flash")]
    producer = {"vendor": "glm", "model": "glm-5.3-flash"}
    reviewer, degrade = rules.code_reviewer(producer, only_mimo)
    # 仍有异源（glm 实现 vs mimo reviewer）→ 正常配对
    assert degrade is False
    producer = {"vendor": "mimo", "model": "mimo-v2.6-flash"}
    reviewer, degrade = rules.code_reviewer(producer, only_mimo)
    assert degrade is True and reviewer["vendor"] == "mimo"


def test_degrade_disabled_returns_none():
    only_mimo = [entry("mimo", "mimo-v2.6-pro")]
    reviewer, degrade = rules.code_reviewer({"vendor": "mimo", "model": "x"}, only_mimo, allow_degrade=False)
    assert reviewer is None and degrade is False


def test_plan_mutual_review_cross_vendor():
    author = {"vendor": "mimo", "model": "mimo-v2.6-pro"}
    reviewer, _ = rules.plan_reviewer(author, STRONG)
    assert reviewer["vendor"] == "glm"
    # 反向（glm 产 mimo 审）→ 双向互审成立
    back, _ = rules.plan_reviewer({"vendor": "glm", "model": "glm-5.3"}, STRONG)
    assert back["vendor"] == "mimo"


def test_pairing_valid_rejects_same_vendor():
    producer = {"vendor": "glm", "model": "glm-5.3-flash"}
    assert not rules.pairing_valid({"vendor": "glm", "model": "glm-5.3"}, producer)
    assert not rules.pairing_valid(None, producer)


# ── 002 US2：底层模型同源约束（FR-004 / R2 三级 / 归因唯一 I1）────────────────

FAM_STRONG = [
    entry("mimo", "mimo-v2.6-pro"),
    entry("glm", "glm-5.3", family="glm-5.3"),
    entry("qianwenai", "qwen3.8-max", family="qwen3.8-max"),
    entry("qianwenai", "glm-5.3", family="glm-5.3"),
]
QNA_GLM_PRODUCER = {"vendor": "qianwenai", "model": "glm-5.3", "family": "glm-5.3"}


def test_same_origin_pair_never_reviews_each_other():
    """①正常层级：跳过同源 glm/glm-5.3，选真异源 mimo。"""
    reviewer, degrade = rules.code_reviewer(QNA_GLM_PRODUCER, FAM_STRONG)
    assert reviewer["vendor"] == "mimo" and degrade is False
    assert rules.pairing_valid(reviewer, QNA_GLM_PRODUCER)


def test_same_origin_fallback_when_different_vendor_exhausted():
    """②同源兜底（含 I1 归因唯一场景）：仅剩同源对 glm/glm-5.3——厂商异源 ⇒
    decide 侧按 vendor != producer.vendor 推导为 same_origin 而非 single_vendor。"""
    pool = [entry("glm", "glm-5.3", family="glm-5.3")]
    reviewer, degrade = rules.code_reviewer(QNA_GLM_PRODUCER, pool)
    assert reviewer["vendor"] == "glm" and degrade is True


def test_single_vendor_degrade_when_all_foreign_exhausted():
    """③单厂商降级（既有行为）：池内仅剩 producer 同厂商条目。"""
    pool = [e for e in FAM_STRONG if e.vendor == "qianwenai"]
    reviewer, degrade = rules.code_reviewer(QNA_GLM_PRODUCER, pool)
    assert degrade is True and reviewer["vendor"] == "qianwenai"


def test_same_origin_blocked_when_degrade_disabled():
    """allow_degrade=False：真异源枯竭时同源也不兜底 → None（FR-004 原则互斥）。"""
    pool = [entry("glm", "glm-5.3", family="glm-5.3")]
    reviewer, degrade = rules.code_reviewer(QNA_GLM_PRODUCER, pool, allow_degrade=False)
    assert reviewer is None and degrade is False


def test_no_family_entries_keep_legacy_pairing():
    """未声明 family 的条目配对行为与 001 一致（SC-002 兼容承诺）。"""
    producer = {"vendor": "glm", "model": "glm-5.3-flash"}
    reviewer, degrade = rules.code_reviewer(producer, FAM_STRONG)
    assert reviewer["vendor"] == "mimo" and degrade is False   # family 空 ⇒ 独立，正常异源


def test_pairing_valid_rejects_same_origin():
    """R3：pairing_valid 拒绝同源配对（Jev 后置修正用）。"""
    assert not rules.pairing_valid({"vendor": "glm", "model": "glm-5.3", "family": "glm-5.3"},
                                   QNA_GLM_PRODUCER)
    assert rules.pairing_valid({"vendor": "mimo", "model": "mimo-v2.6-pro"}, QNA_GLM_PRODUCER)

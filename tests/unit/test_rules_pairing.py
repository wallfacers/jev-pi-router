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

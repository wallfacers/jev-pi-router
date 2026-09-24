"""池枯竭显式化（003 修复1）：兜底不得穿透封禁/熔断；轮换复位允许。

派发池 = available（未封禁/未熔断/未轮换）空则回退 eligible（仅未封禁/未熔断）；
两者均空 → chosen=null、pool_exhausted=true、fallback_order=[]、pool_exhausted 事件。
"""

import json
import os
from copy import deepcopy
from pathlib import Path

from jev_pi_router.config import load_config
from jev_pi_router.decide import decide


def _req(sample_request, **overrides):
    request = deepcopy(sample_request)
    request.update(overrides)
    return request


def _flash_vendors(config_path):
    return sorted({e.vendor for e in load_config(config_path).flash})


def _types(response):
    return [e["type"] for e in response["fallback_events"]]


def _last_record():
    path = Path(os.environ["JEV_PI_ROUTER_HOME"]) / "decisions.jsonl"
    return json.loads(path.read_text(encoding="utf-8").strip().splitlines()[-1])


def test_all_flash_vendors_quota_banned_pool_exhausted(sample_request, config_path):
    """全部 flash 厂商额度封禁（逐个上报）→ 显式池枯竭，不派发被封厂商、不静默降级。"""
    for vendor in _flash_vendors(config_path):
        decide(_req(sample_request, vendor_failures=[{"vendor": vendor, "trigger": "quota"}]),
               engine="rules", config_path=config_path)
    response = decide(_req(sample_request), engine="rules", config_path=config_path)
    assert response["chosen"] is None
    assert response["pool_exhausted"] is True
    assert response["fallback_order"] == []
    assert "pool_exhausted" in _types(response)
    assert "池枯竭" in response["rationale"]
    assert "不可用" in response["rationale"]      # R2-4：中性措辞（与停用条目同因）
    # 日志写入不抛错：末条记录 chosen=null 可解析（log.py 必备字段值可空）
    assert _last_record()["chosen"] is None


def test_all_flash_entries_disabled_pool_exhausted(sample_request, config_path, tmp_path):
    """全部 flash 条目 enabled=false（非封禁/熔断）同样判枯竭并产生 pool_exhausted 事件：
    R2-4 措辞“不可用（封禁/熔断/停用）”的依据。"""
    import yaml

    data = yaml.safe_load(Path(config_path).read_text(encoding="utf-8"))
    for item in data["flash_pool"]:
        item["enabled"] = False
    cfg = tmp_path / "router.config.yaml"
    cfg.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    response = decide(_req(sample_request), engine="rules", config_path=cfg)
    assert response["chosen"] is None
    assert response["pool_exhausted"] is True
    assert "pool_exhausted" in _types(response)


def test_all_flash_vendors_breaker_open_pool_exhausted(sample_request, config_path):
    """全部 flash 厂商熔断开启（每厂商 3 次 timeout）→ 同样显式池枯竭。"""
    for vendor in _flash_vendors(config_path):
        decide(_req(sample_request,
                    vendor_failures=[{"vendor": vendor, "trigger": "timeout"}] * 3),
               engine="rules", config_path=config_path)
    response = decide(_req(sample_request), engine="rules", config_path=config_path)
    assert response["chosen"] is None
    assert response["pool_exhausted"] is True
    assert response["fallback_order"] == []
    assert "pool_exhausted" in _types(response)
    assert _last_record()["chosen"] is None


def test_rotation_only_resets_to_first_eligible(sample_request, config_path):
    """仅轮换枯竭（previous_models 覆盖全部 flash，无封禁/熔断）→ 回退首条 eligible，非池枯竭。"""
    refs = [e.api_ref for e in load_config(config_path).flash]
    request = _req(sample_request, history={"review_fail_count": 0, "previous_models": refs})
    response = decide(request, engine="rules", config_path=config_path)
    assert response["chosen"]["api_ref"] == refs[0]
    assert response["pool_exhausted"] is False
    assert "pool_exhausted" not in _types(response)


def test_rotation_reset_never_revives_banned_vendor(sample_request, config_path):
    """轮换复位与额度封禁组合：previous_models 覆盖全部 flash 且另有 1 厂商 quota 封禁 →
    复位只能落到未封禁的 eligible 条目，绝不复活被封厂商，且不判池枯竭。"""
    cfg = load_config(config_path)
    refs = [e.api_ref for e in cfg.flash]
    banned = cfg.flash[0].vendor
    banned_refs = {e.api_ref for e in cfg.flash if e.vendor == banned}
    decide(_req(sample_request, vendor_failures=[{"vendor": banned, "trigger": "quota"}]),
           engine="rules", config_path=config_path)          # 上报封禁并落盘
    request = _req(sample_request, history={"review_fail_count": 0, "previous_models": refs})
    response = decide(request, engine="rules", config_path=config_path)
    assert response["pool_exhausted"] is False
    chosen = response["chosen"]
    assert chosen is not None
    assert chosen["vendor"] != banned                       # 复位不穿透封禁
    assert chosen["api_ref"] not in banned_refs
    for entry in response["fallback_order"]:
        assert entry["vendor"] != banned                    # 轮换候选同样剔除被封厂商


def test_strong_pool_exhausted_after_quality_upgrade(sample_request, config_path):
    """强池枯竭：review_fail_count=2 触发 quality_upgrade 走 strong 池，强厂商全额度封禁 →
    chosen=null、pool_exhausted=true、fallback_order=[]，且不误派 flash 条目。"""
    cfg = load_config(config_path)
    for vendor in sorted({e.vendor for e in cfg.strong}):
        decide(_req(sample_request, vendor_failures=[{"vendor": vendor, "trigger": "quota"}]),
               engine="rules", config_path=config_path)
    request = _req(sample_request, history={"review_fail_count": 2, "previous_models": []})
    response = decide(request, engine="rules", config_path=config_path)
    assert response["chosen"] is None
    assert response["pool_exhausted"] is True
    assert response["fallback_order"] == []
    assert "pool_exhausted" in _types(response)
    assert "quality_upgrade" in _types(response)             # 确认确实走了 strong 池路径
    assert _last_record()["chosen"] is None


def test_pool_exhausted_skips_jev_call(sample_request, config_path, monkeypatch):
    """池枯竭时不调用 Jev（候选为空问不出结果，且失败会把记录翻成 fail_open=true）。"""
    from jev_pi_router import jev_client

    calls = []
    monkeypatch.setattr(jev_client, "decide",
                        lambda *args, **kwargs: calls.append(args) or {"task_class": "implement"})
    for vendor in _flash_vendors(config_path):
        decide(_req(sample_request, vendor_failures=[{"vendor": vendor, "trigger": "quota"}]),
               engine="rules", config_path=config_path)
    response = decide(_req(sample_request), engine="jev", config_path=config_path)
    assert calls == []
    assert response["pool_exhausted"] is True
    assert response["engine"] == "rules" and response["fail_open"] is False
    assert response["task_class"] == "implement"               # rules 基线值保留
    assert response["complexity"] == "low"

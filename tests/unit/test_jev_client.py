"""Jev 客户端 API key 解析单测（FR-005/FR-006：key 缺失时干净降级，不发空 Bearer 吃 401）。"""

import urllib.request

import pytest

from jev_pi_router import jev_client
from jev_pi_router.jev_client import (JevError, _api_key, _endpoint, _headers, _model_id,
                                      _normalize_complexity, _validate_choice)


@pytest.fixture()
def no_env_keys(monkeypatch):
    """清空环境 key，并把 .env 兜底路径指向不存在的文件（避免读宿主机真实配置）。"""
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.delenv("AI_GATEWAY_API_KEY", raising=False)
    return monkeypatch


def forbid_urlopen(monkeypatch):
    """把 urlopen 换成哨兵：被调用即记录并失败（用于断言未发出任何 HTTP 请求）。"""
    calls = []

    def _sentinel(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("不应发出 HTTP 请求")

    monkeypatch.setattr(urllib.request, "urlopen", _sentinel)
    return calls


def test_api_key_env_var_takes_precedence(no_env_keys, tmp_path):
    no_env_keys.setenv("TYPESAFE_API_KEY", "sk-env")
    no_env_keys.setattr(jev_client, "JEV_ULTRAFAST_ENV_FILE", tmp_path / "missing.env")
    assert _api_key() == "sk-env"


def test_api_key_uses_ai_gateway_fallback(no_env_keys, tmp_path):
    no_env_keys.setenv("AI_GATEWAY_API_KEY", "sk-gateway")
    no_env_keys.setattr(jev_client, "JEV_ULTRAFAST_ENV_FILE", tmp_path / "missing.env")
    assert _api_key() == "sk-gateway"


def test_api_key_falls_back_to_env_file(no_env_keys, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# jev 配置\n\nOTHER_KEY=1\nTYPESAFE_API_KEY=\"sk-from-file\"\nTYPESAFE_API_KEY=sk-after\n",
        encoding="utf-8",
    )
    no_env_keys.setattr(jev_client, "JEV_ULTRAFAST_ENV_FILE", env_file)
    assert _api_key() == "sk-from-file"
    assert _headers("jev-latest")["Authorization"] == "Bearer sk-from-file"


def test_api_key_missing_raises_before_any_request(no_env_keys, tmp_path):
    no_env_keys.setattr(jev_client, "JEV_ULTRAFAST_ENV_FILE", tmp_path / "missing.env")
    calls = forbid_urlopen(no_env_keys)
    with pytest.raises(JevError, match="Jev key 未配置"):
        jev_client._post({"model": "jev-latest"}, 0.1)
    assert calls == []


def test_decide_missing_key_raises_cleanly_without_request(no_env_keys, tmp_path):
    """空 key 的 decide 调用直接 JevError（供 decide.py fail-open 回退规则）。"""
    no_env_keys.setattr(jev_client, "JEV_ULTRAFAST_ENV_FILE", tmp_path / "missing.env")
    calls = forbid_urlopen(no_env_keys)
    with pytest.raises(JevError, match="Jev key 未配置"):
        jev_client.decide("brief", [], [], [], timeout_ms=2000)
    assert calls == []


# ---- S3 修复：endpoint/model 与 key 同源解析（进程环境 > .env 文件） ----

def test_endpoint_and_model_fall_back_to_env_file(no_env_keys, tmp_path):
    no_env_keys.delenv("TYPESAFE_ENDPOINT", raising=False)
    no_env_keys.delenv("TYPESAFE_MODEL", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "TYPESAFE_ENDPOINT=https://ai-gateway.example/v4/ai/evaluation-model\n"
        "TYPESAFE_MODEL=typesafe-ai/jev\n",
        encoding="utf-8",
    )
    no_env_keys.setenv("TYPESAFE_API_KEY", "sk-env")   # _headers 会解析 key
    no_env_keys.setattr(jev_client, "JEV_ULTRAFAST_ENV_FILE", env_file)
    assert _endpoint() == "https://ai-gateway.example/v4/ai/evaluation-model"
    assert _model_id() == "typesafe-ai/jev"
    headers = _headers("typesafe-ai/jev")
    assert headers["ai-model-id"] == "typesafe-ai/jev"          # gateway 协议头带上
    assert headers["ai-gateway-protocol-version"] == "0.0.1"


def test_process_env_overrides_env_file(no_env_keys, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("TYPESAFE_ENDPOINT=https://file.example/x\nTYPESAFE_MODEL=file-model\n", encoding="utf-8")
    no_env_keys.setattr(jev_client, "JEV_ULTRAFAST_ENV_FILE", env_file)
    no_env_keys.setenv("TYPESAFE_ENDPOINT", "https://env.example/y")
    no_env_keys.setenv("TYPESAFE_MODEL", "env-model")
    assert _endpoint() == "https://env.example/y"
    assert _model_id() == "env-model"


def test_native_endpoint_has_no_gateway_headers(no_env_keys, tmp_path):
    no_env_keys.setenv("TYPESAFE_API_KEY", "sk-env")
    no_env_keys.delenv("TYPESAFE_ENDPOINT", raising=False)
    no_env_keys.setattr(jev_client, "JEV_ULTRAFAST_ENV_FILE", tmp_path / "missing.env")
    headers = _headers("jev-latest")
    assert "ai-gateway-protocol-version" not in headers


def test_api_key_precedence_pinned(no_env_keys, tmp_path):
    """钉死顺序：TYPESAFE_API_KEY > AI_GATEWAY_API_KEY > .env 文件（P2）。"""
    env_file = tmp_path / ".env"
    env_file.write_text("TYPESAFE_API_KEY=sk-file\n", encoding="utf-8")
    no_env_keys.setattr(jev_client, "JEV_ULTRAFAST_ENV_FILE", env_file)
    no_env_keys.setenv("AI_GATEWAY_API_KEY", "sk-gateway")
    no_env_keys.setenv("TYPESAFE_API_KEY", "sk-env")
    assert _api_key() == "sk-env"
    no_env_keys.delenv("TYPESAFE_API_KEY")
    assert _api_key() == "sk-gateway"


def test_api_key_whitespace_only_env_falls_through(no_env_keys, tmp_path):
    """空白 env 值不得拦截后续优先级（P2：逐值 strip 后再判空）。"""
    env_file = tmp_path / ".env"
    env_file.write_text("TYPESAFE_API_KEY=sk-file\n", encoding="utf-8")
    no_env_keys.setattr(jev_client, "JEV_ULTRAFAST_ENV_FILE", env_file)
    no_env_keys.setenv("TYPESAFE_API_KEY", "   ")
    no_env_keys.setenv("AI_GATEWAY_API_KEY", "  sk-gateway  ")
    assert _api_key() == "sk-gateway"
    no_env_keys.delenv("AI_GATEWAY_API_KEY")
    assert _api_key() == "sk-file"


# ── R2-5：complexity 宽容归一（只降级该字段；task_class 等硬键仍严格）─────────

def test_validate_choice_still_strict_for_hard_keys():
    """task_class/implement_ref/reviewer_ref 走的严格校验不变：越界即 JevError。"""
    criteria = {"design": "…", "implement": "…"}
    assert _validate_choice({"choice": "design"}, criteria) == "design"
    with pytest.raises(JevError, match="非法选项"):
        _validate_choice({"choice": "medium"}, criteria)


def test_normalize_complexity_maps_out_of_range_to_high():
    """complexity 越界/缺失归一为 high（宁高勿低），不抛错。"""
    criteria = {"low": "低风险", "high": "高风险"}
    assert _normalize_complexity({"choice": "low"}, criteria) == "low"
    assert _normalize_complexity({"choice": "high"}, criteria) == "high"
    assert _normalize_complexity({"choice": "medium"}, criteria) == "high"   # 已废弃三值档
    assert _normalize_complexity({}, criteria) == "high"                       # 缺失
    assert _normalize_complexity({"choice": None}, criteria) == "high"

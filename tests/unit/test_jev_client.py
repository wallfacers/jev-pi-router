"""Jev 客户端 API key 解析单测（FR-005/FR-006：key 缺失时干净降级，不发空 Bearer 吃 401）。"""

import urllib.request

import pytest

from jev_pi_router import jev_client
from jev_pi_router.jev_client import JevError, _api_key, _headers


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

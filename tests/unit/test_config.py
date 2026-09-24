"""config 加载/校验单测（contracts/config-schema.md 校验规则表）。"""

from pathlib import Path

import pytest

from jev_pi_router.config import ConfigError, load_config


def write_cfg(tmp_path, text):
    path = tmp_path / "router.config.yaml"
    path.write_text(text, encoding="utf-8")
    return path


BASE = """
strong_pool:
  - {vendor: mimo, model: mimo-v2.6-pro, api_ref: mimo/mimo-v2.6-pro}
  - {vendor: glm, model: glm-5.3, api_ref: glm/glm-5.3}
flash_pool:
  - {vendor: deepseek, model: deepseek-flash, api_ref: deepseek/deepseek-flash}
roles: {implement: flash}
decision_engine: {fail_open: true}
"""


def test_load_valid(config_path):
    cfg = load_config(config_path)
    # 计数为下限语义：001 FR-013"增删厂商只改配置"，示例池随特性扩容（002 起 4/6）
    assert len(cfg.strong) >= 2 and len(cfg.flash) >= 4
    assert {e.api_ref for e in cfg.strong} >= {"mimo/mimo-v2.6-pro", "glm/glm-5.3"}
    assert cfg.roles["implement"] == "flash"
    assert cfg.engine.fail_open is True
    assert cfg.fallback.quality_review_fails <= cfg.review.max_rounds


def test_disabled_entries_filtered(config_path):
    text = config_path.read_text(encoding="utf-8").replace(
        "    cache_passthrough: full       # api.deepseek.com 官方直连，厂商侧缓存已知存在\n    enabled: true",
        "    cache_passthrough: full\n    enabled: false", 1)
    import tempfile, pathlib
    with tempfile.TemporaryDirectory() as tmp:
        p = pathlib.Path(tmp) / "c.yaml"
        p.write_text(text, encoding="utf-8")
        cfg = load_config(p)
        assert all(e.vendor != "deepseek" or not e.enabled for e in cfg.flash)


def test_empty_strong_pool_rejected(tmp_path):
    path = write_cfg(tmp_path, BASE.replace("strong_pool:\n  - {vendor: mimo, model: mimo-v2.6-pro, api_ref: mimo/mimo-v2.6-pro}\n  - {vendor: glm, model: glm-5.3, api_ref: glm/glm-5.3}", "strong_pool: []"))
    with pytest.raises(ConfigError, match="strong_pool"):
        load_config(path)


def test_implement_role_must_be_flash(tmp_path):
    path = write_cfg(tmp_path, BASE.replace("roles: {implement: flash}", "roles: {implement: strong}"))
    with pytest.raises(ConfigError, match="implement"):
        load_config(path)


def test_fail_open_must_be_true(tmp_path):
    path = write_cfg(tmp_path, BASE.replace("fail_open: true", "fail_open: false"))
    with pytest.raises(ConfigError, match="fail_open"):
        load_config(path)


def test_review_fails_within_max_rounds(tmp_path):
    path = write_cfg(tmp_path, BASE + "review: {max_rounds: 1}\nfallback: {quality_upgrade: {review_fails: 2}}\n")
    with pytest.raises(ConfigError, match="review_fails"):
        load_config(path)


def test_bad_cache_passthrough_rejected(tmp_path):
    path = write_cfg(tmp_path, BASE.replace(
        "- {vendor: deepseek, model: deepseek-flash, api_ref: deepseek/deepseek-flash}",
        "- {vendor: deepseek, model: deepseek-flash, api_ref: deepseek/deepseek-flash, cache_passthrough: warm}"))
    with pytest.raises(ConfigError, match="cache_passthrough"):
        load_config(path)


def test_missing_file_rejected(tmp_path):
    with pytest.raises(ConfigError, match="不存在"):
        load_config(tmp_path / "nope.yaml")


# ── 002：family 同源组字段（contracts/config-schema.md §1、R1）──────────────

FAMILY_CFG = """
strong_pool:
  - {vendor: glm, model: glm-5.3, api_ref: glm/glm-5.3, family: glm-5.3}
  - {vendor: qianwenai, model: glm-5.3, api_ref: qianwenai/glm-5.3, family: glm-5.3}
  - {vendor: qianwenai, model: broken, api_ref: qianwenai/broken, family: 123}
  - {vendor: qianwenai, model: emptyfam, api_ref: qianwenai/emptyfam, family: ""}
flash_pool:
  - {vendor: deepseek, model: deepseek-flash, api_ref: deepseek/deepseek-flash}
roles: {implement: flash}
decision_engine: {fail_open: true}
"""


def test_family_declared_parsed(tmp_path):
    cfg = load_config(write_cfg(tmp_path, FAMILY_CFG))
    by_ref = {e.api_ref: e for e in cfg.strong}
    assert by_ref["glm/glm-5.3"].family == "glm-5.3"
    assert by_ref["qianwenai/glm-5.3"].family == "glm-5.3"


def test_family_tolerant_defaults(tmp_path):
    cfg = load_config(write_cfg(tmp_path, FAMILY_CFG))
    by_ref = {e.api_ref: e for e in cfg.strong}
    assert by_ref["qianwenai/broken"].family == ""       # 非字符串容错为空，不报 ConfigError
    assert by_ref["qianwenai/emptyfam"].family == ""     # 显式空串 = 独立
    assert cfg.flash[0].family == ""               # 未声明条目缺省空


def test_as_ref_carries_family_only_when_present(tmp_path):
    cfg = load_config(write_cfg(tmp_path, FAMILY_CFG))
    refs = {e.api_ref: e.as_ref() for e in cfg.strong + cfg.flash}
    assert refs["glm/glm-5.3"]["family"] == "glm-5.3"
    assert "family" not in refs["qianwenai/broken"]
    assert "family" not in refs["deepseek/deepseek-flash"]


def test_legacy_config_without_family_unchanged(tmp_path):
    cfg = load_config(write_cfg(tmp_path, BASE))
    assert all(e.family == "" for e in cfg.strong + cfg.flash)   # R1 兼容承诺：旧配置零变化


# ── v1.4：api_ref 滑窗冷却参数（contracts/config-schema.md §breaker）──────────

def test_breaker_window_defaults(config_path):
    cfg = load_config(config_path)
    assert cfg.fallback.breaker_window_sec == 3600
    assert cfg.fallback.breaker_window_failures == 2
    assert cfg.fallback.breaker_api_ref_cooldown_sec == 300


def test_breaker_window_overrides(tmp_path):
    path = write_cfg(tmp_path, BASE + "fallback: {breaker: {window_sec: 600, window_failures: 1, api_ref_cooldown_sec: 60}}\n")
    cfg = load_config(path)
    assert cfg.fallback.breaker_window_sec == 600
    assert cfg.fallback.breaker_window_failures == 1      # =1 合法（单次失败即冷却）
    assert cfg.fallback.breaker_api_ref_cooldown_sec == 60


@pytest.mark.parametrize("key", ["window_sec", "window_failures", "api_ref_cooldown_sec"])
def test_breaker_window_invalid_rejected(tmp_path, key):
    path = write_cfg(tmp_path, BASE + f"fallback: {{breaker: {{{key}: 0}}}}\n")
    with pytest.raises(ConfigError, match=key):
        load_config(path)


def test_promptcache_manifest_covers_all_pool_entries():
    """002 data-model 一致性约束 4：promptcache 清单键集合 ⊇ 示例池全部 api_ref。"""
    import json
    from pathlib import Path

    import yaml

    root = Path(__file__).resolve().parents[2]
    data = yaml.safe_load((root / "router.config.yaml.example").read_text(encoding="utf-8"))
    refs = {item["api_ref"] for pool in ("strong_pool", "flash_pool") for item in data.get(pool) or []}
    keys = set(json.loads((root / "pi/models.promptcache.json").read_text(encoding="utf-8"))["promptCache"])
    assert refs <= keys


def test_deepseek_flash_pair_shares_family():
    """review 修复 D：deepseek-flash 双渠道同底层模型对声明同 family（实质同源互斥）。"""
    from pathlib import Path

    import yaml

    root = Path(__file__).resolve().parents[2]
    data = yaml.safe_load((root / "router.config.yaml.example").read_text(encoding="utf-8"))
    fams = {item["api_ref"]: item.get("family", "")
            for pool in ("strong_pool", "flash_pool") for item in data.get(pool) or []}
    assert fams["deepseek/deepseek-flash"] == "deepseek-flash"
    assert fams["opencode-go/deepseek-flash"] == "deepseek-flash"


# ── 003 修复4：默认配置路径为仓库根绝对路径（与 cwd 无关）────────────────────

def test_default_config_is_repo_root_absolute(tmp_path, monkeypatch):
    """无显式 path/env 时 load_config() 解析到 REPO_ROOT 下的 router.config.yaml（cwd 无关）。

    仓库根 router.config.yaml 被 gitignore，fresh clone 不存在——故把 REPO_ROOT 指向 tmp_path
    并在其中写入配置，断言默认路径拼装语义（绝对路径 + cwd 无关）而非依赖本机文件。
    """
    import jev_pi_router.config as config_module

    monkeypatch.setattr(config_module, "REPO_ROOT", tmp_path)
    monkeypatch.delenv("JEV_PI_ROUTER_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "router.config.yaml").write_text(BASE, encoding="utf-8")
    cfg = load_config()
    assert cfg.strong and cfg.flash
    assert Path(cfg.source_path).is_absolute()
    assert Path(cfg.source_path) == tmp_path / "router.config.yaml"


def test_empty_env_config_treated_as_unset(tmp_path, monkeypatch):
    """空字符串 JEV_PI_ROUTER_CONFIG 视为未设置（与 config.py 语义一致，不报 open("") 错）。"""
    import jev_pi_router.config as config_module

    monkeypatch.setattr(config_module, "REPO_ROOT", tmp_path)
    (tmp_path / "router.config.yaml").write_text(BASE, encoding="utf-8")
    monkeypatch.setenv("JEV_PI_ROUTER_CONFIG", "")
    assert Path(load_config().source_path) == tmp_path / "router.config.yaml"


def test_explicit_path_still_wins_over_default(tmp_path, monkeypatch):
    """显式 path 参数优先级高于默认仓库根路径。"""
    monkeypatch.delenv("JEV_PI_ROUTER_CONFIG", raising=False)
    path = write_cfg(tmp_path, BASE)
    assert Path(load_config(path).source_path) == path


def test_env_config_wins_over_repo_root_default(tmp_path, monkeypatch):
    """R3-3：env > 默认分支直接用例——JEV_PI_ROUTER_CONFIG（A）胜过 REPO_ROOT 下的配置（B）。

    A/B 是两个不同路径才有判别力：若误走默认分支，source_path 会落在 elsewhere 而非 A。
    """
    import jev_pi_router.config as config_module

    env_cfg = tmp_path / "env" / "custom-router.config.yaml"   # A：env 指定的配置
    env_cfg.parent.mkdir()
    env_cfg.write_text(BASE, encoding="utf-8")
    repo = tmp_path / "elsewhere"                               # B：仓库根默认配置（内容不同）
    repo.mkdir()
    (repo / "router.config.yaml").write_text(BASE.replace("deepseek-flash", "other-flash"), encoding="utf-8")
    monkeypatch.setenv("JEV_PI_ROUTER_CONFIG", str(env_cfg))
    monkeypatch.setattr(config_module, "REPO_ROOT", repo)
    assert Path(load_config().source_path) == env_cfg

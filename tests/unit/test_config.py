"""config 加载/校验单测（contracts/config-schema.md 校验规则表）。"""

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
    assert len(cfg.strong) == 2 and len(cfg.flash) == 4
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

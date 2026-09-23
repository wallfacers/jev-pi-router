# Contract: router.config.yaml 增量（002 · qianwenai 接入 + family 字段）

本文件是 `specs/001-pi-model-routing/contracts/config-schema.md` 的**增量契约**：仅描述 002 特性新增/变更部分，未提及的字段与校验规则维持 001 契约不变。唯一配置源约定不变（FR-001/002：qianwenai 接入纯配置，零厂商专属代码）。

## 1. 新增通用可选字段：`family`（同源组标识）

适用于两池所有条目，非 qianwenai 专属（R1）：

```yaml
- vendor: glm
  model: glm-5.3
  api_ref: glm/glm-5.3
  family: glm-5.3          # 新增：同源组标识（底层模型归属）
  cost_hint: 1.0
  cache_passthrough: unknown
  enabled: true
```

**语义**：
- 缺省/空串 = 独立条目，配对行为与 001 完全一致；
- 两条目 `family` 均非空且相等 ⇒ 同源，跨厂商 review 配对原则上互斥（FR-004）；
- 解析容错：非字符串值按空处理，不报 ConfigError。

**校验规则增量**：无新增硬性校验（family 不参与两池非空/roles/review 校验）；doctor check 提供两项警告级检查（见 §4）。

## 2. strong_pool 增量（尾部追加，R7）

```yaml
strong_pool:
  # ── 既有条目不动：mimo/mimo-v2.6-pro、glm/glm-5.3（glm 条目补 family: glm-5.3）──
  - vendor: qianwenai
    model: qwen3.8-max
    api_ref: qianwenai/qwen3.8-max
    family: qwen3.8-max
    cost_hint: 1.0              # 平权：与全部强池条目同值（R4）
    cache_passthrough: full     # pi 侧已声明 promptCache；probe 可校准（R5）
    enabled: true
  - vendor: qianwenai
    model: glm-5.3
    api_ref: qianwenai/glm-5.3
    family: glm-5.3             # 与 glm/glm-5.3 同源（R1）
    cost_hint: 1.0
    cache_passthrough: full
    enabled: true
```

## 3. flash_pool 增量（尾部追加，R7）

```yaml
flash_pool:
  # ── 既有条目不动：deepseek、glm-5.3-flash、relay（补 family: deepseek-v4.1-flash）、opencode-go ──
  - vendor: qianwenai
    model: qwen3.8-flash
    api_ref: qianwenai/qwen3.8-flash
    family: qwen3.8-flash
    cost_hint: 0.15             # 平权：与直连 flash 档同级（R4）
    cache_passthrough: full
    enabled: true
  - vendor: qianwenai
    model: deepseek-v4.1-flash
    api_ref: qianwenai/deepseek-v4.1-flash
    family: deepseek-v4.1-flash # 与 relay/cmd-deepseek-v4.1-flash 同源（R1）
    cost_hint: 0.1              # 平权：与同源条目同值（R4）
    cache_passthrough: full
    enabled: true
```

**既有条目补声明汇总**（其余条目不加 family）：

| api_ref | family |
|---------|--------|
| glm/glm-5.3 | glm-5.3 |
| relay/cmd-deepseek-v4.1-flash | deepseek-v4.1-flash |

## 4. doctor check 警告级检查增量（R9）

`jev-pi-doctor check` 在既有"api_ref 已注册于 pi models.json"之外追加（warning 不改变退出码语义）：

| 检查 | 触发条件 | 输出 |
|------|----------|------|
| promptcache 缺口 | router.config.yaml 条目的 api_ref 在 `pi/models.promptcache.json` 无键 | `WARN <api_ref> 缺少缓存声明清单条目` |
| 疑似同源未声明 | 两条目 model 名去除渠道前缀（如 `cmd-`）后相同，但 family 不一致或一方未声明 | `WARN <ref_a> 与 <ref_b> 疑似同源未声明 family` |

## 5. pi/models.promptcache.json 增量（R6）

```json
"qianwenai/qwen3.8-max":         {"short": 300, "long": 3600},
"qianwenai/qwen3.8-flash":       {"short": 300, "long": 3600},
"qianwenai/deepseek-v4.1-flash": {"short": 300, "long": 3600},
"qianwenai/glm-5.3":             {"short": 300, "long": 3600}
```

取值与 pi 侧 `~/.pi/agent/models.json` qianwenai 各模型 `promptCache` 声明一致（FR-007）。

## 6. 兼容性承诺

- 不含 family 字段的旧配置解析结果与 001 完全一致（缺省空串）；
- qianwenai 条目全部 enabled=false 或整商封禁时，两池仍非空，校验通过（FR-010）；
- `router.config.yaml.example` 与本契约同步更新。

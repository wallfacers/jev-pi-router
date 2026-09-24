# Phase 1 Data Model: qianwenai 厂商模型接入（002）

本特性为增量：仅扩展既有实体字段与新增数据条目，无新实体、无存储结构迁移。基线见 `specs/001-pi-model-routing/data-model.md`。

## 1. ModelEntry（模型池条目）——字段增量

| 字段 | 类型 | 缺省 | 变化 | 说明 |
|------|------|------|------|------|
| vendor | str | 必填 | 不变 | 厂商名；qianwenai 为新厂商值 |
| model | str | 必填 | 不变 | 模型名 |
| api_ref | str | 必填 | 不变 | `vendor/model` 统一引用 |
| pool | str | 必填 | 不变 | strong / flash |
| cost_hint | float | 1.0 | 不变 | qianwenai 取同级平权值（R4） |
| latency_hint | float\|None | None | 不变 | |
| cache_passthrough | str | unknown | 不变 | qianwenai 初值 full（R5，probe 可校准） |
| enabled | bool | True | 不变 | |
| **family** | **str** | **""（新增）** | **新增** | 同源组标识（R1）。空 = 独立条目；两条目 family 均非空且相等 ⇒ 同源 |

**校验规则**（config.py `_parse_entries`）：
- `family` 可选；非字符串或空串按缺省 `""` 处理（容错不报错，与既有可选字段风格一致）；
- family 不参与两池非空、roles、review 等既有校验——既有校验规则零变化；
- `as_ref()` 输出增加 `family` 键（空串时省略），供 Jev 候选描述与日志留痕。

**新增 4 条实例**（R4/R5/R7，追加于池尾部）：

| api_ref | pool | cost_hint | cache_passthrough | family |
|---------|------|-----------|-------------------|--------|
| qianwenai/qwen3.8-max | strong | 1.0 | full | qwen3.8-max |
| qianwenai/glm-5.3 | strong | 1.0 | full | glm-5.3 |
| qianwenai/qwen3.8-flash | flash | 0.15 | full | qwen3.8-flash |
| qianwenai/deepseek-v4.1-flash | flash | 0.1 | full | deepseek-v4.1-flash |

**既有条目 family 补声明**（R1，同源对的另一半）：

| api_ref | family |
|---------|--------|
| glm/glm-5.3 | glm-5.3 |
| relay/cmd-deepseek-v4.1-flash | deepseek-v4.1-flash |
| deepseek/deepseek-flash | deepseek-flash |
| opencode-go/deepseek-flash | deepseek-flash |

其余既有条目（mimo、glm/glm-5.3-flash 等）不声明 family（独立），行为零变化。
（deepseek-flash 双渠道对为 review 修复轮次补充：同底层模型实质同源互斥优先于最小变更。）

## 2. review_plan（决策响应内嵌结构）——字段增量

| 字段 | 类型 | 变化 | 说明 |
|------|------|------|------|
| code_reviewer | ref\|None | 不变 | |
| plan_reviewers | list | 不变 | |
| degrade | bool | 不变 | true = 非正常配对（同源兜底或单厂商降级） |
| **degrade_reason** | **str** | **新增** | `""` / `"same_origin"` / `"single_vendor"`（R2/R8）；旧消费方忽略该字段不受影响 |
| implementer | ref | 不变 | |

## 3. fallback_events（事件流）——新增事件类型

| type | trigger | 语义 |
|------|---------|------|
| degrade_same_origin（新增） | explicit | 异源候选枯竭，同源模型兜底参与复核（R8） |
| degrade_single_vendor（既有） | explicit | 强池仅单厂商可用，降级复核 |
| quota_block / quota_unlock / breaker_open / breaker_close / quality_upgrade（既有） | — | 不变；qianwenai 作为普通厂商值天然纳入 |

**状态转移（复核配对选择，R2 三级）**：

```text
producer 产出
  ├─ 存在 vendor 异源且 family 非同源的可用强条目 → 正常配对（degrade=false）
  ├─ 否，allow_degrade 且存在 vendor 异源但 family 同源的可用条目
  │     → 同源兜底（degrade=true, reason=same_origin, 事件 degrade_same_origin）
  ├─ 否，allow_degrade 且强池有任意可用条目
  │     → 单厂商降级（degrade=true, reason=single_vendor, 事件 degrade_single_vendor）
  └─ 否（allow_degrade=false 或池枯竭） → 无 reviewer（corrected 标记，既有语义）
```

## 4. promptCache 声明清单（pi/models.promptcache.json）——数据增量

新增 4 键（R6），值与 pi 侧 `~/.pi/agent/models.json` qianwenai 模型声明一致：

```json
"qianwenai/qwen3.8-max":          {"short": 300, "long": 3600},
"qianwenai/qwen3.8-flash":        {"short": 300, "long": 3600},
"qianwenai/deepseek-v4.1-flash":  {"short": 300, "long": 3600},
"qianwenai/glm-5.3":              {"short": 300, "long": 3600}
```

## 5. Quota / Breaker / 统计报表——零结构变化

- `QuotaRegistry` 按厂商维度存储（`~/.jev-pi-router/quotas.json`），`qianwenai` 作为新厂商键天然纳入：quota 语义 429 → 整商封禁；auto/manual/reset_card/activity 解锁路径全部复用（FR-005）。
- 熔断（breaker）按 api_ref 维度，qianwenai 条目天然纳入（FR-006）。
- 决策日志 schema 版本维持 `v:1`（新增字段向后兼容，不升版本）；stats/report 将 qianwenai 作为普通厂商分组，无 qianwenai 历史数据时正常输出零值（FR-009）。

## 一致性约束（测试锚点）

1. 两池所有条目 api_ref 全局唯一（既有约束，qianwenai 4 条不与既有冲突）。
2. family 同源的条目对必须跨 ≥2 个 vendor 才有意义；同 vendor 内 family 相同不产生新行为（vendor 异源判定先行）。
3. `degrade=true ⇔ degrade_reason 非空 ⇔ 事件流含 degrade_same_origin 或 degrade_single_vendor`（log 不变量1 扩展，R8）。
4. promptcache 清单键集合 ⊇ router.config.yaml 全部 api_ref（doctor check 警告项，R9）。

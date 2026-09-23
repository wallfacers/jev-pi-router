# Contract: 决策日志/响应 Schema 增量（002 · 同源降级留痕）

本文件是 `specs/001-pi-model-routing/contracts/decision-log-schema.md` 的**增量契约**。schema 版本维持 `v: 1`（仅向后兼容的新增字段/事件值，不升版本）。

## 1. review_plan 新增字段：`degrade_reason`

```json
"review_plan": {
  "code_reviewer": {"vendor": "glm", "model": "glm-5.3", "api_ref": "glm/glm-5.3", "pool": "strong"},
  "plan_reviewers": [],
  "implementer": {"vendor": "qianwenai", "model": "glm-5.3", "api_ref": "qianwenai/glm-5.3", "pool": "strong"},
  "degrade": true,
  "degrade_reason": "same_origin"
}
```

| 值 | 语义 | 伴随事件 |
|----|------|----------|
| `""`（缺省） | 正常异源配对 | 无 |
| `"same_origin"` | 异源候选枯竭，同源模型兜底复核（FR-004） | `degrade_same_origin` |
| `"single_vendor"` | 强池仅单厂商可用降级（既有行为） | `degrade_single_vendor` |

**兼容性**：旧记录无 `degrade_reason` 字段，消费方（stats/report）按 `""` 处理；`degrade` 布尔语义不变（true = 非正常配对）。

## 2. fallback_events 新增事件类型：`degrade_same_origin`

```json
{"type": "degrade_same_origin", "trigger": "explicit", "ts": "2026-09-23T10:00:00+08:00"}
```

- 事件结构与既有事件一致（type/trigger/from_model/to_model/ts）；
- 与 `degrade_single_vendor` 互斥出现（三级降级层级中 same_origin 优先于 single_vendor 命中，见 data-model §3），归因唯一；
- stats/report 按事件 type 分组统计，`degrade_same_origin` 作为新分组值出现，无历史数据时输出零值不报错（FR-009）。

## 3. log 不变量1 扩展（R8）

| # | 原不变量 | 扩展后 |
|---|----------|--------|
| 1 | `review_plan.degrade=true` ⇒ 事件含 `degrade_single_vendor` | `review_plan.degrade=true` ⇒ 事件含 `degrade_single_vendor` **或** `degrade_same_origin`；且新记录（含 `degrade_reason`）reason 与事件类型必须配对（review F7 修复） |

- 不变量2（fail_open ⇒ engine=rules）不变；
- 历史 JSONL 记录重放校验：旧记录 degrade=true 时必已有 `degrade_single_vendor`，满足扩展后不变量——**向后兼容，无需迁移**。

## 4. 配对硬约束修正语义增量（decide 路径）

- Jev 候选 reviewer 池过滤条件：`vendor 异源` → `vendor 异源且 family 非同源`（R3）；
- `pairing_valid(reviewer, producer)`：`reviewer.vendor != producer.vendor` **且** 非 family 同源；
- producer（`request["implementer"]` 字典）的 family 由 decide 按 api_ref 从两池条目解析注入；无法解析时视为独立条目（仅厂商名判定，向后兼容，R3）；
- Jev 返回同源 reviewer 时走三级降级重选，`rationale` 前缀 `[pairing-corrected]` 语义不变；
- quota 封禁厂商（含 qianwenai）在候选过滤阶段即被剔除，封禁期间的同源兜底不选被封厂商条目（额度耗尽不是故障，兜底也不得穿透封禁）。

## 5. 决策 CLI（jev-pi-decide）契约

请求/响应外层结构不变（001 contracts/decision-cli.md）；响应体 `review_plan`、`fallback_events`、`rationale`、`quota_hints` 按上述增量携带新值。退出码语义不变（0 正常 / 2 配置错误）。

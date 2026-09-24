# Contract: 决策日志（JSONL，contracts/decision-log-schema.md）

Phase 1 输出。每次路由决策追加一行 JSON（只增不改），路径 `~/.jev-pi-router/decisions.jsonl`（FR-011）。session 标识只存哈希不存原文。

## Record Schema（RouteDecision，一行一条）

| 字段 | 类型 | 说明 |
|------|------|------|
| v | int | schema 版本，初始 1 |
| decision_id | string | uuid |
| ts | string | ISO 8601 |
| session_id_hash | string | 会话 id 的 SHA-256 前 12 位（不存原文） |
| task_ref | string | 任务关联键 |
| role | enum | orchestrator \| plan \| decision \| review \| fallback_arbiter \| implement |
| task_class | enum | design \| implement \| chore |
| complexity | enum | low \| medium \| high |
| chosen | object | {vendor, model, api_ref, pool} |
| review_plan | object | {code_reviewer?, plan_reviewers[], degrade}（同 decision-cli response） |
| engine | enum | jev \| rules |
| fail_open | bool | 是否发生 Jev→rules 回退（FR-006） |
| rationale | string | ≤200 字符；配对修正时含 `[pairing-corrected]` |
| fallback_events | array | FallbackEvent[]（见 data-model.md）；无则 `[]` |

## FallbackEvent（内嵌对象）

| 字段 | 类型 | 说明 |
|------|------|------|
| type | enum | fault_transfer \| quality_upgrade \| breaker_open \| breaker_close \| degrade_single_vendor \| pool_exhausted \| api_ref_cooldown \| api_ref_recover |
| from_model | string\|null | api_ref |
| to_model | string\|null | api_ref；无迁移为 null；条目级事件（api_ref_cooldown / api_ref_recover）承载 api_ref |
| trigger | enum | timeout \| http_5xx \| quota \| rate_limit \| review_reject \| explicit \| empty_response |
| attempt | int | 从 1 起 |
| ts | string | ISO 8601 |

## 示例

```json
{"v":1,"decision_id":"b1c2...","ts":"2026-09-22T16:31:02+08:00","session_id_hash":"a9f3c2211b4e","task_ref":"t-001","role":"implement","task_class":"implement","complexity":"low","chosen":{"vendor":"deepseek","model":"deepseek-flash","api_ref":"deepseek/deepseek-flash","pool":"flash"},"review_plan":{"code_reviewer":{"vendor":"mimo","model":"mimo-v2.6-pro","api_ref":"mimo/mimo-v2.6-pro"},"plan_reviewers":[],"degrade":false},"engine":"jev","fail_open":false,"rationale":"实现类低复杂度，派 flash；异源 reviewer 选 mimo","fallback_events":[]}
{"v":1,"decision_id":"d3e4...","ts":"2026-09-22T16:33:40+08:00","session_id_hash":"a9f3c2211b4e","task_ref":"t-001","role":"implement","task_class":"implement","complexity":"low","chosen":{"vendor":"glm","model":"glm-5.3-flash","api_ref":"glm/glm-5.3-flash","pool":"flash"},"review_plan":{"code_reviewer":{"vendor":"mimo","model":"mimo-v2.6-pro","api_ref":"mimo/mimo-v2.6-pro"},"plan_reviewers":[],"degrade":false},"engine":"rules","fail_open":true,"rationale":"Jev 超时 fail-open；同档转移后选 glm-flash","fallback_events":[{"type":"fault_transfer","from_model":"deepseek/deepseek-flash","to_model":"glm/glm-5.3-flash","trigger":"timeout","attempt":2,"ts":"2026-09-22T16:33:40+08:00"},{"type":"breaker_open","from_model":null,"to_model":null,"trigger":"timeout","attempt":3,"ts":"2026-09-22T16:33:40+08:00"}]}
```

## 消费方

- `bin/jev-pi-report`（聚合：模型分布、engine 统计、fail_open 率、转移/升级/熔断/降级计数、review 覆盖率——支撑 SC-001/002 的验证）。
- 人审 / grep / jq 管道（JSONL 只增、行独立、无外键）。

## 不变量

- 一行 = 一次完整裁决（含 fail-open）；不允许跳过写日志（FR-011）。
- `review_plan.degrade=true` 的记录必须至少含一个 `degrade_single_vendor` 事件（SC-002 "0 次静默跳过"）。
- `engine="rules" ∧ fail_open=true` ⇔ 本次 Jev 路径失败（可观测性对账）。

## 扩展（v1.4，条目级滑窗冷却）

- 事件 type 新增 `api_ref_cooldown`（条目滑窗达阈值开冷）/ `api_ref_recover`
  （`vendor_success` 带 api_ref 解除冷却）；`to_model` 承载 api_ref，与 vendor 级事件区分。
  自然冷却到期不产生事件。示例：

  ```json
  {"type":"api_ref_cooldown","from_model":null,"to_model":"relay/cmd-deepseek-v4.1-flash","trigger":"empty_response","attempt":1,"ts":"2026-09-24T15:30:00+08:00"}
  ```

- trigger 新增 `empty_response`（客户端判定：响应 content 全空 + usage 全零；详见
  contracts/decision-cli.md v1.4 节）。未知 trigger 仍按普通熔断计数容错。
- 消费方无需改动：report/stats 按 type 泛型分组，新 type 自动作为新分组值出现。

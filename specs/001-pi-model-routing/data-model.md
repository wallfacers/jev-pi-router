# Data Model: PI 多厂商模型路由

Phase 1 输出。实体来源于 spec Key Entities；校验规则溯源至 FR；含状态机转移。

## Entities

### ModelEntry（模型条目）
| 字段 | 类型 | 说明 |
|------|------|------|
| vendor | string | 厂商标识（mimo / glm / deepseek / relay / opencode-go …），异源配对的判别键 |
| model | string | 模型 id（如 glm-5.3-flash） |
| api_ref | string | pi 内引用，`provider/model`（如 `glm/glm-5.3-flash`） |
| pool | enum: strong \| flash | 所属池（FR-001） |
| cost_hint | number | 相对成本权重（auto 模式信号，默认 1.0） |
| latency_hint | number? | 相对延迟权重（可空） |
| cache_passthrough | enum: full \| partial \| none \| unknown | 中转缓存透传实测结果（默认 unknown；R8 协议测定） |
| enabled | bool | 人工开关 |

**校验**：`api_ref` 必须已注册于 pi `models.json`（由 `jev-pi-doctor` 校验）；两池均非空（FR-001）；同一 vendor 可有多条目。

### ModelPool（模型池）
- `strong_pool` / `flash_pool`：ModelEntry 的**有序**集合；顺序即路由优先级。
- 手动模式按配置顺序；auto 模式按（可用性，成本，延迟，cache_passthrough）加权重排（FR-012）。

### Role（角色）
- 枚举：`orchestrator`（主控）、`plan`、`decision`、`review`、`fallback_arbiter`、`implement`。
- 绑定（FR-002）：`implement` → flash_pool；其余 → strong_pool。

### ReviewPairing（review 配对）
| 规则 | 约束 | 源 |
|------|------|-----|
| code_review | `reviewer.vendor != implementer.vendor` 且 reviewer ∈ strong_pool | FR-007 |
| plan_review | 互审对（author → reviewer）双方 ∈ strong_pool 且 `author.vendor != reviewer.vendor`，双向成立 | FR-008 |
| degrade | strong_pool 可用 vendor < 2 时降级单厂商 review，必须产生 `degrade_single_vendor` 事件并显式标记 | US2-3 / SC-002 |

**硬约束后置覆盖**：即使 Jev 返回违反上述约束的配对，rules 层强制修正并记录修正痕迹（契约见 contracts/decision-cli.md）。

### RouteDecision（路由决策记录 → 决策日志）
decision_id（uuid）、ts、session_id_hash（哈希不存原文）、task_ref、task_class（design \| implement \| chore）、complexity（low \| high）、role、chosen（vendor/model/api_ref；池枯竭时为 null）、review_plan、engine（jev \| rules）、fail_open、fallback_events[]。（完整 schema 见 contracts/decision-log-schema.md）

### FallbackEvent（兜底事件）
| 字段 | 说明 |
|------|------|
| type | fault_transfer \| quality_upgrade \| breaker_open \| breaker_close \| degrade_single_vendor \| degrade_same_origin \| pool_exhausted \| quota_block \| quota_unlock \| api_ref_cooldown \| api_ref_recover |
| from_model / to_model | 迁移两端（api_ref），无迁移则 to_model 为 null；条目级事件（api_ref_cooldown / api_ref_recover）承载 api_ref |
| trigger | timeout \| http_5xx \| auth \| quota \| rate_limit \| review_reject \| explicit \| auto \| manual \| reset_card \| activity \| empty_response |
| attempt | 第几次尝试（1 起） |
| ts | ISO 时间戳 |

（`trigger` 为调用方可扩展的开集，此枚举为已知值集合；实现按 `vendor_failures[].trigger` 原样透传。）

### BreakerState（熔断状态，运行时态，按 vendor 键）
- 字段：consecutive_failures、state（closed \| open \| half_open）、open_until。
- 校验：冷却/阈值参数来自配置（默认：连续 3 次失败开断、冷却 300s，FR-009）。

### ApiRefWindowState（条目级滑窗状态，运行时态，按 api_ref 键；v1.4）
- 字段：failures（窗口内失败的 epoch 秒时间戳列表，惰性 prune）、cooldown_until（>0 = 冷却中）。
- 语义：与 vendor 级 BreakerState 互补——后者计**连续**失败（成功即清零），本状态计**滑窗累计**
  （成功带 api_ref 才清窗）；粒度更细（relay 单条端点退化不至连坐同厂商其他条目）。
- 校验：窗口/阈值参数来自配置（默认：`window_sec` 3600 内 `window_failures` 2 次失败 → 冷却
  `api_ref_cooldown_sec` 300s）；仅熔断计数类失败计入（rate_limit/quota 分流不计）；
  未达阈值仅**降权**（排序后置，仍可选中）；无失败记录时池序完全不变。
- 持久化：state.json 顶层键 `api_refs`（`breakers` 键形状不变，旧文件自然兼容）；
  save 前全量清扫窗口外且冷却已过的条目（状态有界）。

### QualityEscalationState（质量升级计数，按 task_ref 键）
- 字段：review_fail_count、escalated、escalated_to。
- 语义：review_reject 计数；达阈值（默认 2，FR-010）触发强模型（换厂商）重做，置 escalated=true。

## State Transitions

### 熔断器（BreakerState，per vendor）
```
closed --(连续 N 次失败，默认 3)--> open --(冷却到期，默认 300s)--> half_open
half_open --(1 次成功)--> closed
half_open --(1 次失败)--> open（重新计冷却）
```
开断/恢复各产生 `breaker_open` / `breaker_close` 事件（FR-009）。

### 条目级滑窗冷却（ApiRefWindowState，per api_ref；v1.4）
```
窗口内失败 < window_failures  → 降权（排序后置，仍可选中）
窗口内失败 >= window_failures → api_ref_cooldown（冷却 api_ref_cooldown_sec，退池）
冷却 → 到期 → 回池但窗口内仍有失败记录 → 持续降权 → 再 1 次失败即再冷却
冷却中 + vendor_success(带 api_ref) → api_ref_recover（清窗 + 立即回池）
```
自然冷却到期不产生事件（与 `open→half_open` 迁移一致）；两级惩罚并存（同一失败可同时触发
vendor 熔断与条目冷却，事件可区分：条目级 to_model 承载 api_ref）。

### 质量升级（QualityEscalationState，per task_ref）
```
pending --(1st review_reject)--> retry_1 --(2nd review_reject)--> escalated
escalated → 由 strong_pool 中**非原实现厂商**的模型重做（FR-010）
强模型产出再失败 → 另一强厂商接管；池穷尽 → pool_exhausted（显式失败 + 成本放大标记）
```

## Relationships

```text
ModelEntry  * ── 1 ModelPool（strong_pool / flash_pool）
Role        1 ── 1 ModelPool（implement→flash，其余→strong）
RouteDecision * ── 1 ModelEntry(chosen)
RouteDecision 1 ── * FallbackEvent
ModelEntry  1 ── 0..1 BreakerState（按 vendor 共享）
ModelEntry  1 ── 0..1 ApiRefWindowState（按 api_ref 键，v1.4；精确到条目）
RouteDecision 1 ── 0..1 QualityEscalationState（按 task_ref 共享）
```

## Validation Rules Index（FR 溯源）

- FR-001 → 两池非空；FR-002 → Role→Pool 绑定表；FR-003 → implement 产出者即独立执行者（子代理），主会话模型不出现在 implement 记录的 chosen 中
- FR-007 → code_review 异源约束；FR-008 → plan_review 双向互审约束
- FR-009 → BreakerState 转移 + fault_transfer 事件；FR-010 → QualityEscalationState 转移 + quality_upgrade 事件
- FR-011 → RouteDecision/FallbackEvent 全量留痕；FR-012/013 → 全部池/阈值/配对均可由配置改变
- v1.4（bug 2026-09-24）→ ApiRefWindowState 转移 + api_ref_cooldown/api_ref_recover 事件；
  条目级粒度与滑窗累计弥补 vendor 级连续计数对间歇故障的盲区；降权不排除（池序有故障时变化、
  无故障时恒等）

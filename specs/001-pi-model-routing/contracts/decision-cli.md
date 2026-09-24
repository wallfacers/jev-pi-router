# Contract: 路由决策 CLI（jev-pi-decide / jev-pi-report / jev-pi-doctor）

Phase 1 输出。面向调用方（pi 主 Agent / skill / 测试）的命令行契约。

## 1. `bin/jev-pi-decide` — 路由裁决（FR-005/006）

```bash
echo '<request JSON>' | bin/jev-pi-decide [--engine auto|rules|jev] [--timeout-ms N]
```

- `--engine auto`（默认）：先 Jev，失败 fail-open 回 rules；`rules`：纯离线，零网络调用；`jev`：强制 Jev（测试用）。
- stdin 一个 request JSON，stdout 一个 response JSON。**stdout 永远是合法 JSON**（含 fail-open 情形）。

### Request（stdin）

```json
{
  "task_ref": "t-001",
  "task_brief": "为 config.py 加 schema 校验并补单测",
  "role": "implement",
  "task_class_hint": null,
  "candidates": {
    "strong":  [{"vendor": "mimo", "model": "mimo-v2.6-pro", "api_ref": "mimo/mimo-v2.6-pro"}],
    "flash":   [{"vendor": "deepseek", "model": "deepseek-flash", "api_ref": "deepseek/deepseek-flash"}]
  },
  "implementer": null,
  "risk_tags": [],
  "history": {"review_fail_count": 0, "previous_models": []}
}
```

| 字段 | 必填 | 说明 |
|------|------|------|
| task_ref | ✅ | 任务标识（日志关联键） |
| task_brief | ✅ | ≤2000 字符任务摘要（Jev 决策输入；超长截断） |
| role | ✅ | orchestrator \| plan \| decision \| review \| fallback_arbiter \| implement |
| task_class_hint | | 调用方可预判；Jev 可覆盖 |
| candidates | | 未使用（池来自配置；保留字段仅作向后兼容，实现不读取） |
| implementer | review 场景必填 | 产出者 vendor/model（FR-007 配对依据） |
| risk_tags | | security / auth / concurrency / migration / performance 等（影响复杂度分级） |
| history | | review_fail_count（质量升级判定）、previous_models（避免重派同一失败模型） |

### Response（stdout）

```json
{
  "task_class": "implement",
  "complexity": "low",
  "chosen": {"vendor": "deepseek", "model": "deepseek-flash", "api_ref": "deepseek/deepseek-flash", "pool": "flash"},
  "review_plan": {
    "code_reviewer": {"vendor": "mimo", "model": "mimo-v2.6-pro", "api_ref": "mimo/mimo-v2.6-pro"},
    "plan_reviewers": [],
    "degrade": false
  },
  "fallback_order": [{"vendor": "glm", "model": "glm-5.3-flash", "api_ref": "glm/glm-5.3-flash"}],
  "engine": "jev",
  "fail_open": false,
  "pool_exhausted": false,
  "rationale": "明确的实现类子任务，低复杂度，派 flash；异源 reviewer 选 mimo"
}
```

### 语义约定

1. **fail-open（FR-006）**：Jev 超时/异常 → `engine: "rules"`、`fail_open: true`、**exit 0**，用规则结果作答；任务不中断。
2. **配对硬约束后置覆盖（FR-007/008）**：无论 Jev 返回什么，`code_reviewer.vendor != implementer.vendor`、plan 互审双向异源由 rules 层强制；发生修正时 `rationale` 注明 `[pairing-corrected]`。
3. **质量升级输入**：`history.review_fail_count >= 阈值(默认 2)` → response 的 `chosen` 直接落在 strong_pool 且 vendor != 之前的实现厂商（FR-010）。
4. **退出码**：`0` 成功（含 fail-open）；`2` 配置缺失/非法（stderr 说明）；`3` 参数/输入 JSON 非法（含 argparse 用法错误：`--engine` 非法值、`--timeout-ms` 非数值等，统一按 3 退出并输出 usage + error JSON）。
5. 每次调用追加一条 RouteDecision 到决策日志（contracts/decision-log-schema.md），无论引擎路径。
6. **池枯竭（pool_exhausted）**：候选池全部不可用，即 `available` 与 `eligible` 均空（封禁/熔断/停用）时，`chosen: null`、顶层 `pool_exhausted: true`、`fallback_order: []`，并产生 `pool_exhausted` 事件；此时不再调用 Jev（候选为空问不出可派发结果），`task_class`/`complexity` 取规则基线值。调用方**须显式告知用户池枯竭**并等待人工介入（`vendor_unlock` 解锁 / 等待熔断冷却），**不得静默降级**。正常路径 `pool_exhausted: false`。

## 2. `bin/jev-pi-report` — 决策报表（FR-011 / SC-001/002）

```bash
bin/jev-pi-report [--days N] [--json]
```

stdout 输出：模型分布（按 chosen.api_ref）、engine 路径统计（jev/rules/fail_open）、fault_transfer / quality_upgrade / breaker / degrade 计数、review 覆盖率（异源 %、互审 %）。`--json` 输出机器可读聚合。exit 0。

## 3. `bin/jev-pi-doctor` — 健康探针 + 缓存透传实测（research R8）

```bash
bin/jev-pi-doctor check                 # 校验配置中全部 api_ref 已注册于 pi models.json
bin/jev-pi-doctor probe <api_ref> [--write-back]
```

`probe` 协议：同一 ≥1000-token 稳定前缀连发两次请求，比较第二次响应 usage 的 `cache_read_input_tokens` / `cache_creation_input_tokens`：

```json
{
  "api_ref": "relay/cmd-deepseek-v4.1-flash",
  "cache_passthrough": "full",
  "evidence": {"second_cache_read_tokens": 1024, "first_cache_read_tokens": 0, "ttft_delta_ms": -420},
  "checked_at": "2026-09-22T16:30:00+08:00"
}
```

| 判定 | 条件 |
|------|------|
| full | 第二次出现非零 `cache_read_input_tokens` |
| partial | 字段缺失/为 0 但第二次 TTFT 显著下降（≥30%） |
| none | 两者皆无 |

`--write-back` 将结果写回 `router.config.yaml` 对应条目的 `cache_passthrough`（影响路由权重排序：full > partial > unknown > none）。exit 0；探针请求失败 exit 1 并输出 error 字段。

## 扩展字段（v1.1，e2e 测试修订）

- `request.vendor_failures`（顶层，**canonical**）：`[{"vendor","trigger"}]`——调用方上报的厂商
  故障，驱动熔断/转移。兼容旧位置 `history.vendor_failures`（双读，顶层优先）。
- `request.vendor_success`（顶层）：`[{"vendor"}]`——执行成功回报，闭合熔断并产生
  `breaker_close` 事件。
- `response.fallback_events`：本次决策产生的事件数组（breaker_open / breaker_close /
  quality_upgrade / degrade_single_vendor 等），随响应体回带，便于调用方观测，
  无需翻查 decisions.jsonl。

## 扩展字段（v1.2，套餐额度场景 S12）

`request.vendor_failures[].trigger` 语义分流：
- `"rate_limit"`：并发/瞬时 429 → 重试即可，**不计入熔断、不封禁**；
- `"quota"`：套餐/周限额额度尽 → **立即封禁该厂商**（`quota_block` 事件，一次即封，不等 3 连败），
  可带 `quota_until`（epoch 秒，到期自动解锁 `quota_unlock: auto`）与 `key_id`（同厂商多 key 留痕）；
  不带 `quota_until` = 无限期封禁，需人工解锁；
- 其它（timeout / http_5xx / auth / explicit）→ 熔断计数（原行为）。

`request.vendor_unlock`：`[{"vendor"}]`——套餐重置后人工解锁（`quota_unlock: manual`）。
人工解锁亦可用 `bin/jev-pi-doctor unlock <vendor>`；`bin/jev-pi-doctor quotas` 查看封禁表。
封禁状态持久化 `~/.jev-pi-router/quotas.json`（按厂商，key_id 仅留痕不参与路由）。

## 扩展字段（v1.3，重置卡/活动提前重置 S12.1）

- `request.vendor_unlock[]` 项支持 `reason`（manual|reset_card|activity|…，默认 manual，写入
  事件 `trigger`）与 `key_id`（留痕）。**人工解锁任何时刻可提前覆盖 `quota_until`**——覆盖
  "活动提前重置/不到重置时间即重置"的场景。
- `reason:"reset_card"` 扣减该厂商重置卡余额（有则扣 1 张，无则放行，fail-open 不拦人），
  事件附 `cards_left`。重置卡台账持久化 `quotas.json.cards`，
  `bin/jev-pi-doctor cards <vendor> --add N` 记入/查询（负数扣减）。
- `response.quota_hints`：存在封禁厂商时给出提示字符串数组（封禁原因/自动解锁时间/重置卡
  余量），供主 Agent 主动询问用户"是否用重置卡解锁"。

## 扩展字段（v1.4，空响应/条目级滑窗冷却）

背景：单体端点间歇性退化（空响应重试死循环，`bugs/2026-09-24-…`）在 vendor 级连续计数下
够不着阈值——一次成功即清零，且 relay 这类聚合网关单条 api_ref 坏不代表整个 vendor 坏。
故引入**条目级（api_ref）滑窗冷却**，与 vendor 级熔断互补并存，粒度不同、事件可区分。

### 请求侧

- `request.vendor_failures[]` / `request.vendor_success[]` 项新增**可选** `api_ref` 字段
  （`provider/model` 格式）。缺失但带 `model` 时由路由器按 `f"{vendor}/{model}"` 合成
  （依赖 `api_ref == vendor/model` 约定；显式字段优先）；两者都缺失 → 回退 vendor 级行为
  （向后兼容，旧模板零改动即获条目级粒度）。
- `trigger` 新增规范值 **`empty_response`**：客户端判定 = 本次 dispatch 中 assistant 响应
  `content` 全空 **且** usage 全零（input/output/total tokens 均 0）；重试次数（如
  auto_retry ≥3）仅作佐证，不作为上报门槛。该 trigger 计入熔断计数与条目滑窗。
- **未知 trigger（如 `"error"`）按普通熔断计数失败容错**（不报错），既有行为落成文字。

### 语义（两级惩罚关系）

| 层级 | 键 | 触发条件 | 效果 |
|------|-----|----------|------|
| vendor 级熔断 | vendor | **连续** `breaker.consecutive_failures` 次失败（成功即清零） | 该厂商全部条目退池至冷却到期 |
| 条目级滑窗 | api_ref | `window_sec` 内累计 `window_failures` 次**熔断计数类**失败 | 该 api_ref 冷却 `api_ref_cooldown_sec`，冷却期不进池 |

- 滑窗只计熔断计数类失败（timeout / http_5xx / empty_response / 未知 trigger / explicit 等）；
  `rate_limit`（重试即可）与 `quota`（走额度封禁）维持既有分流，**不计入滑窗**（避免双重惩罚）。
- **未达滑窗阈值**（窗口内有失败但不足 N 次）→ 该条目仅**降权**（排序排到健康条目之后，
  仍可被选中）——"同家族有可用替代且近期出错时优先替代"由此自然成立；
  无故障状态时池序完全不变。
- 冷却到期回池，但窗口内失败记录未滚出前持续降权（间歇故障条目持续被压制）。
- `vendor_success` 带 api_ref → 清空该条目滑窗 + 立即解除冷却（`api_ref_recover` 事件）；
  **不带 api_ref → 只闭合 vendor 级**（vendor 级成功不能证明该具体端点恢复）。
- 池枯竭兜底：**排除不穿透**（保持 chosen 非 None，不破坏 CLI 契约）但**降权穿透**——
  兜底时仍优先健康条目，坏条目只在别无选择时被选中。复核（review_plan）走严格过滤，
  永不穿透。

### 响应侧

`response.fallback_events` 新增两个 type（`to_model` 承载 api_ref，vendor 级事件 to_model 为
null/vendor，两级可区分）：
- `api_ref_cooldown`：条目滑窗达阈值 → 冷却开（trigger 为实际失败类型）；冷却中重复失败
  不重复开断/发事件；
- `api_ref_recover`：`vendor_success` 带 api_ref 且原在冷却中 → 解除。

自然冷却到期**不产生事件**（与 vendor 级 `open→half_open` 迁移一致）；`rationale` 在有条目
被降权时附 "近期失败条目已降权"。

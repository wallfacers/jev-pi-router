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
| candidates | ✅ | 当前可用池（已剔除熔断条目；来自配置） |
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
  "rationale": "明确的实现类子任务，低复杂度，派 flash；异源 reviewer 选 mimo"
}
```

### 语义约定

1. **fail-open（FR-006）**：Jev 超时/异常 → `engine: "rules"`、`fail_open: true`、**exit 0**，用规则结果作答；任务不中断。
2. **配对硬约束后置覆盖（FR-007/008）**：无论 Jev 返回什么，`code_reviewer.vendor != implementer.vendor`、plan 互审双向异源由 rules 层强制；发生修正时 `rationale` 注明 `[pairing-corrected]`。
3. **质量升级输入**：`history.review_fail_count >= 阈值(默认 2)` → response 的 `chosen` 直接落在 strong_pool 且 vendor != 之前的实现厂商（FR-010）。
4. **退出码**：`0` 成功（含 fail-open）；`2` 配置缺失/非法（stderr 说明）；`3` 参数/输入 JSON 非法。
5. 每次调用追加一条 RouteDecision 到决策日志（contracts/decision-log-schema.md），无论引擎路径。

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

# Quickstart: 验证指南（002 · qianwenai 厂商接入）

Phase 1 输出。端到端验证场景，对应 spec 的 Acceptance Scenarios 与 Success Criteria。只含运行/验证步骤；字段与事件定义见 [contracts/](./contracts/)，实体增量见 [data-model.md](./data-model.md)。

## 前置条件

- 001 特性 quickstart 前置条件全部满足（Python 3.11+、`pip install -e ".[dev]"`、pi 供应商注册）
- pi 侧 qianwenai 厂商已注册且 4 模型启用（`~/.pi/agent/models.json` 含 `qianwenai`，`settings.json` 启用 `qianwenai/*`——现状已满足）
- `router.config.yaml` 已含 qianwenai 4 条目与 family 声明（contracts/config-schema.md §2/§3）

## V0. 全量回归 + 新增单测（SC-002 / FR-002）

```bash
pytest tests/ -v
```

**期望**：既有测试**零修改断言**全部通过；新增测试覆盖 family 解析、三级降级配对、pairing_valid 同源判定、qianwenai 平权排序、日志不变量扩展。

## V1. doctor 体检（FR-001 / FR-007 / R9）

```bash
bin/jev-pi-doctor check
```

**期望**：
- qianwenai 4 条 api_ref 均通过"已注册于 pi models.json"检查；
- 无 `WARN ... 缺少缓存声明清单条目`（promptcache 清单已覆盖 10/10 条目）；
- 无 `WARN ... 疑似同源未声明 family`（glm-5.3 与 deepseek-v4.1-flash 两对已显式声明）；
- 反向验证：临时删除 promptcache 清单中 `qianwenai/glm-5.3` 键再 check → 出现对应 WARN（缺口可被体检发现），验证后还原。

## V2. qianwenai 进入派发可选集（US1 / SC-001 / FR-003）

```bash
# implement 派发：用 previous_models 排除既有 flash 条目，观察轮到 qianwenai
echo '{"task_ref":"v2a","task_brief":"实现 xxx 功能代码","role":"implement",
       "history":{"previous_models":["deepseek/deepseek-flash","glm/glm-5.3-flash",
       "relay/cmd-deepseek-v4.1-flash","opencode-go/deepseek-flash"]}}' \
  | bin/jev-pi-decide --engine rules
```

**期望**：`chosen.api_ref == "qianwenai/qwen3.8-flash"`（池顺序首个可用）；`chosen.pool == "flash"`。

```bash
# 强模型派发：排除既有强条目
echo '{"task_ref":"v2b","task_brief":"设计 xxx 架构方案","role":"plan",
       "history":{"previous_models":["mimo/mimo-v2.6-pro","glm/glm-5.3"]}}' \
  | bin/jev-pi-decide --engine rules
```

**期望**：`chosen.api_ref == "qianwenai/qwen3.8-max"`；`fallback_order` 含 `qianwenai/glm-5.3`。

**平权验证（FR-008/SC-006）**：不带 previous_models 的默认派发仍首选既有条目（mimo/deepseek，配置顺序不变）；qianwenai 通过 fallback_order 参与轮换。

## V3. 同源约束与三级降级（US2-2/3/4 / FR-004 / SC-003）

```bash
# ① 正常：producer=qianwenai/glm-5.3，reviewer 应为非 qianwenai 且非同源（mimo）
echo '{"task_ref":"v3a","task_brief":"复核代码","role":"review",
       "implementer":{"vendor":"qianwenai","model":"glm-5.3","api_ref":"qianwenai/glm-5.3"}}' \
  | bin/jev-pi-decide --engine rules
```

**期望**：`review_plan.code_reviewer.api_ref == "mimo/mimo-v2.6-pro"`；`degrade == false`、`degrade_reason == ""`。**绝不**选 `glm/glm-5.3`（同源）。

```bash
# ② 同源兜底：封禁 mimo 后重试同请求（quota 语义，一次调用即封）
echo '{"task_ref":"v3b","task_brief":"复核代码","role":"review",
       "implementer":{"vendor":"qianwenai","model":"glm-5.3","api_ref":"qianwenai/glm-5.3"},
       "vendor_failures":[{"vendor":"mimo","trigger":"quota"}]}' \
  | bin/jev-pi-decide --engine rules
```

**期望**：`code_reviewer.api_ref == "glm/glm-5.3"`；`degrade == true`、`degrade_reason == "same_origin"`；`fallback_events` 含 `degrade_same_origin` 事件；rationale 说明降级原因。

```bash
# ③ 单厂商降级：再封禁 glm（异源+同源均枯竭，仅剩 qianwenai 自身）
echo '{"task_ref":"v3c", ..., "vendor_failures":[{"vendor":"mimo","trigger":"quota"},
       {"vendor":"glm","trigger":"quota"}]}' | bin/jev-pi-decide --engine rules
```

**期望**：`degrade_reason == "single_vendor"`、事件为 `degrade_single_vendor`（既有行为保留，两类告警可区分归因）。

```bash
# ④ 归因唯一（analyze I1）：封禁 mimo + qianwenai 自身 → 仅剩 glm/glm-5.3（异源但同源对）
echo '{"task_ref":"v3d", ..., "vendor_failures":[{"vendor":"mimo","trigger":"quota"},
       {"vendor":"qianwenai","trigger":"quota"}]}' | bin/jev-pi-decide --engine rules
```

**期望**：reviewer = `glm/glm-5.3`，`degrade_reason == "same_origin"`（**不是** single_vendor，即使强池实际仅剩单一异源厂商），事件为 `degrade_same_origin`。

```bash
# 清理：解锁 mimo/glm/qianwenai
bin/jev-pi-doctor unlock mimo --reason manual && bin/jev-pi-doctor unlock glm --reason manual && bin/jev-pi-doctor unlock qianwenai --reason manual
```

## V4. qianwenai 额度封禁与解锁（US2-6 / FR-005 / SC-004）

```bash
# 封禁 qianwenai（quota 语义 429 上报）
echo '{"task_ref":"v4a","task_brief":"实现代码","role":"implement",
       "vendor_failures":[{"vendor":"qianwenai","trigger":"quota","quota_until":0}]}' \
  | bin/jev-pi-decide --engine rules
bin/jev-pi-doctor quotas        # 期望：qianwenai 在封禁表，无限期（quota_until=0）
```

**期望**：后续任意派发 0 次选中 qianwenai（V2 请求重放 → chosen 回落到既有 flash 条目）；封禁不占熔断计数。

```bash
# 解锁恢复
bin/jev-pi-doctor unlock qianwenai --reason reset_card   # 或 manual/activity
echo '<V2 的 implement 请求>' | bin/jev-pi-decide --engine rules
```

**期望**：解锁后**下一次决策调用**即恢复 qianwenai 可选（SC-004）；`quota_hints` 携带解锁信息。

```bash
# 熔断路径（FR-006 / US2 场景 5，analyze G1）：qianwenai 连续失败（explicit 语义，非 quota）
for i in 1 2 3; do echo '{"task_ref":"v4b'$i'","task_brief":"实现代码","role":"implement",
  "vendor_failures":[{"vendor":"qianwenai","trigger":"explicit"}]}' | bin/jev-pi-decide --engine rules; done
```

**期望**：第 3 次后 `breaker_open` 事件出现，后续派发同档换商（0 次选中 qianwenai）；`vendor_success` 上报或冷却期后恢复；熔断计数与 quota 封禁状态相互独立（qianwenai 未在 quotas 封禁表中）。

## V5. 日志不变量与契约测试（FR-009 / R8 / SC-003）

```bash
pytest tests/contract tests/unit/test_log*.py -v
tail -3 ~/.jev-pi-router/decisions.jsonl | python3 -m json.tool
```

**期望**：扩展后不变量1 通过（degrade=true ⇒ `degrade_single_vendor` 或 `degrade_same_origin`）；V3 产生的记录含 `review_plan.degrade_reason` 与对应事件；旧日志记录重放校验不报错；stats/report 对 qianwenai 正常分组、无 qianwenai 数据日不报错：

```bash
bin/jev-pi-stats && bin/jev-pi-report
```

## V6. 缓存声明与 probe 校准（US3 / FR-007 / SC-005，可选真实调用）

```bash
python3 -c "import json; d=json.load(open('pi/models.promptcache.json'))['promptCache']; \
  assert all(k in d for k in ['qianwenai/qwen3.8-max','qianwenai/qwen3.8-flash','qianwenai/deepseek-v4.1-flash','qianwenai/glm-5.3']); \
  print('promptcache 4/4 覆盖，值与 pi 侧一致:', {k: d[k] for k in d if k.startswith('qianwenai/')})"

# 真实校准（消耗 qianwenai 套餐额度，两次 ≥1000-token 请求）：
bin/jev-pi-doctor probe qianwenai/qwen3.8-max --write-back
```

**期望**：清单断言通过；probe 实测 `cache_passthrough` 与初值 `full` 一致（不一致时以实测回填，R5）。auto 模式排序中 qianwenai 按 full 档参与（rank 3），不因信息缺失降级。

## 验收对照表

| 场景 | 覆盖 |
|------|------|
| V0 | SC-002、FR-002 |
| V1 | FR-001/007、R9 体检 |
| V2 | US1 全部、SC-001、FR-003/008 |
| V3 | US2-1/2/3/4、FR-004、SC-003 |
| V4 | US2-6、FR-005/010、SC-004 |
| V5 | FR-009、R8 不变量、US3-3 |
| V6 | US3-1/2、FR-007、SC-005 |

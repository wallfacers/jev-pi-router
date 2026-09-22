# Quickstart: 验证指南（jev-pi-router）

Phase 1 输出。端到端验证场景，对应 spec 的 Acceptance Scenarios 与 Success Criteria。本文件只含运行/验证步骤，实现细节见 [data-model.md](./data-model.md) 与 [contracts/](./contracts/)。

## 前置条件

- Python 3.11+、pytest、PyYAML
- pi 已注册供应商（`~/.pi/agent/models.json` 含 mimo / glm / deepseek / relay / opencode-go——现状已满足）
- `AI_GATEWAY_API_KEY` 已在环境（`.bashrc`，Jev 决策通道）
- 模块可导入：`PYTHONPATH=.` 或 `pip install -e .`

## 安装与配置

```bash
cd jev-pi-router
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
cp router.config.yaml.example router.config.yaml   # 默认池即 spec Assumptions 的默认值
bin/jev-pi-doctor check                            # 校验 api_ref 全部已注册于 pi
```

## 验证场景

### V1. 规则模式决策冒烟（US1 / FR-002/005）

```bash
echo '{"task_ref":"v1","task_brief":"实现 config.py 的 schema 校验","role":"implement","candidates":{...}}' \
  | bin/jev-pi-decide --engine rules
```
**期望**：`chosen.pool == "flash"`；`review_plan.code_reviewer.vendor != chosen.vendor`；exit 0；stdout 为合法 JSON（契约：[contracts/decision-cli.md](./contracts/decision-cli.md)）。

### V2. fail-open（US1-3 / SC-004）

```bash
AI_GATEWAY_API_KEY=sk-invalid echo '{...}' | bin/jev-pi-decide --engine auto
```
**期望**：exit 0；`engine == "rules"` 且 `fail_open == true`；任务不中断。

### V3. 异源配对硬约束（US2 / SC-002）

以 `implementer: {vendor: glm, model: glm-5.3-flash}` 发起 `role: review` 决策。
**期望**：`code_reviewer.vendor != "glm"`；若 Jev 返回违规配对，`rationale` 含 `[pairing-corrected]`。强池仅启用一家时：`review_plan.degrade == true` 且日志含 `degrade_single_vendor` 事件（US2-3）。

### V4. 兜底状态机（US3 / SC-003，FR-009/010）

```bash
pytest tests/unit/test_fallback.py -v
```
**期望**：覆盖转移（1 次失败 → 同档换厂商，≤2 次重试内成功）、熔断（3 连败 → open，300s 后 half_open）、质量升级（2 轮 review_reject → strong + 换厂商）、池穷尽（`pool_exhausted` 显式失败）。状态转移定义：[data-model.md](./data-model.md#state-transitions)。

### V5. 契约测试 + 日志不变量（FR-011 / SC-002）

```bash
pytest tests/contract tests/unit -v
```
**期望**：decision-cli 请求/响应 schema、日志三条不变量（见 [contracts/decision-log-schema.md](./contracts/decision-log-schema.md#不变量)）全部通过。

### V6. 中转缓存透传实测（Assumptions 实施期验证项，research R8）

```bash
bin/jev-pi-doctor probe relay/cmd-deepseek-v4.1-flash --write-back
bin/jev-pi-doctor probe opencode-go/deepseek-flash --write-back
```
**期望**：输出 `cache_passthrough ∈ {full, partial, none}` 及证据（协议：[contracts/decision-cli.md](./contracts/decision-cli.md#3-jev-pi-doctor)）；结果写回配置，路由权重按 full > partial > unknown > none 重排。

### V7. pi 端到端冒烟（US1 / US2，主场景）

1. 在 pi 会话中安装 `pi/skills/jev-pi-router` 技能，`/new` 会话发起一个真实小任务（如"给 X 加一个 CLI 子命令 + 单测"）。
2. **期望观察**：主会话（强模型）产出计划并被另一强厂商子代理互审 → flash 子代理（`subagent` 指定 flash 模型）实现代码 → 异源强模型子代理 review → 主会话合入；主会话模型全程未切换。
3. `bin/jev-pi-report --days 1`：**期望** code 异源覆盖 100%、plan 互审覆盖 100%、fail_open 计数与实际一致（SC-002/004）。

### V8. B 模式 /new 会话切换（US4-3 / FR-004）

`/new` 会话中逐轮使用不同模型。
**期望**：允许（冷缓存无沉没成本）；对照：进行中会话尝试切模型应被技能约定拒绝并提示缓存代价（SC-006）。

### V9. 配置化验证（US4 / SC-005，FR-013）

从 `flash_pool` 移除某厂商后直接发起实现任务。
**期望**：该厂商不再被派发，零代码改动；`bin/jev-pi-doctor check` 仍通过。

## 端到端验收对账（SC → 场景）

| SC | 验证场景 |
|----|----------|
| SC-001（成本 -40%） | V7 + `jev-pi-report` 成本聚合（与全强模型基线对照） |
| SC-002（review 覆盖 100%） | V3 / V5 / V7 |
| SC-003（故障自恢复 ≥90%） | V4 / V7（注入故障演练） |
| SC-004（fail-open 100%） | V2 |
| SC-005（配置 0 代码改动） | V9 |
| SC-006（会话内 0 模型切换） | V7 / V8 |

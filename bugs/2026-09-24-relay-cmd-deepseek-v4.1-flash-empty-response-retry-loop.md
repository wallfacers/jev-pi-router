# Bug: relay/cmd-deepseek-v4.1-flash 空响应重试死循环 — 路由器无此故障模式，继续选中该 api_ref

- **Date**: 2026-09-24 ~15:05 CST
- **Severity**: High（烧掉一次 dispatch 的全部工时，需维护者人工中断）
- **Context**: engram 仓库 051 T19 评测流水线，`task_class=implement` 的热修小片派发（pi-subagent run `3bef7da6`，worker 角色）。

## 现象

派发到 `relay/cmd-deepseek-v4.1-flash` 后 ~98 秒内：

- 子 agent 4 个 turn、6 次消息尝试，**所有 assistant 响应 `content:[]`、usage 全零**（input/output/totalTokens = 0）；
- pi-subagents 层 auto_retry **≥3 次**（`auto_retry_end attempt:3`）；
- 零有效工具动作、零文件改动（中断后 `git diff` 为空）；
- 维护者在 UI 观察到「死循环」，人工下令中断（我方 interrupt → `stopReason:"aborted"`）。

## 证据

- events.jsonl：`/tmp/pi-subagents-uid-1000/async-subagent-runs/3bef7da6-5d43-4103-9917-7a64137c0fde/events.jsonl`（`message_end` 事件：content 空 + usage 全零 + auto_retry 序列）
- status.json：同目录（`durationMs: 98235`，`state: paused`，tokens null）
- 同日早些时候同一 api_ref 已有一次 `trigger:error`（litellm-500 连接错误，run `5588304c`）——路由器按「瞬时故障」处理，几分钟后同一任务类型**再次选中 relay**。

## 根因（两层）

1. **Provider 层**：relay（litellm 聚合）的 `cmd-deepseek-v4.1-flash` 端点间歇性返回空/退化响应（当日既有 500 连接错误又有空响应），端点不稳定。
2. **Router 层（本项目缺口）**：
   - 故障分类里没有 `empty_response` / `degenerate_loop` 触发器——客户端无法把「跑完零内容零 token + 多次重试」报告为一次供应商失败；
   - `trigger:error` 的瞬时故障惩罚太轻，没有同 api_ref 滑窗连续失败升级（cooldown/ban）；
   - 存在同家族替代模型（`qianwenai/deepseek-v4.1-flash` 随后零偏离完成同一任务）时，router 未优先规避近期出错方。

## 建议修复

1. 新增失败触发器 `empty_response`（客户端在 dispatch 结束时报：content 空 / token=0 / retry≥N）。
2. 滑窗连续失败策略：同一 `api_ref` 在 X 小时内 2 次任意类型失败 → 冷却降权（vendor 级或 api_ref 级，视配置粒度）。
3. 同家族有可用替代且近期有 error 记录时，优先替代（本次 qianwenai 完胜）。

## 临时处置（已执行）

维护者人工禁用该 api_ref；任务重派 `qianwenai/deepseek-v4.1-flash` 成功。后续 router payload 中该 vendor 记 `trigger:error`（附注 infinite-loop / maintainer-banned）。

## 修复（已执行，2026-09-24）

三项建议修复全部落地（契约 decision-cli.md **v1.4**）：

1. **`empty_response` 触发器**：`fallback.py` `TRIGGERS` 新增该值；客户端判定 = 响应 `content`
   全空 **且** usage 全零（input/output/total 均为 0），**首次即报**、不以重试次数为门槛
   （重试次数仅佐证）。SKILL.md 增对应上报纪律（英文 Dual-Track Failover 节）。未知 trigger
   （如本报告的 `"error"`）维持按普通熔断计数容错。
2. **api_ref 条目级滑窗冷却**：`vendor_failures[]`/`vendor_success[]` 新增可选 `api_ref` 字段
   （缺失带 `model` 时由路由器合成 `vendor/model`——**旧模板零改动即获条目级粒度**，本报告的
   `trigger:error` payload 因此自动生效）。同 api_ref 在 `window_sec`（默认 3600s）内累计
   `window_failures`（默认 2）次熔断类失败 → 冷却 `api_ref_cooldown_sec`（默认 300s）退池；
   带 api_ref 的成功回报即时解除（`api_ref_recover`）。配置键在 `fallback.breaker` 下。
   `rate_limit`/`quota` 维持既有分流**不计入滑窗**（避免双重惩罚）。
3. **同家族替代优先**：未达冷却阈值（窗口内有失败记录）的条目**降权**（稳定分区排到健康条目
   之后，仍可选中）——"同家族有可用替代且近期出错时优先替代"由此成立；**无故障状态池序完全
   不变**。池枯竭兜底降权穿透、排除不穿透（保持 chosen 非 None）。

两级惩罚并存：vendor 级熔断（连续 `consecutive_failures`=3，成功清零）与条目级滑窗（窗口累计，
粒度精确到端点）互补，事件可区分（条目级 `to_model` 承载 api_ref）。新增事件
`api_ref_cooldown`（开）/ `api_ref_recover`（解冷）；自然到期不产事件。

**验证**：新增 22 个用例（滑窗状态机 9 + 决策集成 7 + 配置 3 + CLI 跨进程持久化 1 +
既有套件），全套 **122 个测试通过**（原有 100 个零改动）。端到端复现本报告场景：
relay 报 1 次失败即降权切同 family 替代 `qianwenai/deepseek-v4.1-flash`；第 2 次触发
`api_ref_cooldown` 完全出池；state.json 新增顶层 `api_refs`（`breakers` 键形状不变，向后兼容）。

**遗留**：未提供 `doctor` 侧条目冷却的人工查看/解除命令（冷却自动到期 + `vendor_success`
可即时解除）；`trigger` 白名单仍无代码强制校验（纯契约锚点，保持既有容错）。

---
*Reported by*: engram 051 T19 主管会话（pi agent，mimo/mimo-v2.6-pro）。

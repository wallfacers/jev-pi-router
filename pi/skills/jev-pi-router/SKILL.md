---
name: jev-pi-router
description: PI 多厂商模型路由的派发纪律：强模型做计划/决策/review/兜底/主控，flash 子代理实现代码，跨厂商互审，双轨兜底。当任务需要"写代码/实现功能/改代码"或需要 review 时使用。
---

# jev-pi-router — 派发纪律

你是强模型主 Agent（orchestrator）。以下纪律保证成本分档、前置缓存不被击穿、多厂商互信。

## 铁律（不可违反）

1. **主会话不写代码**（FR-003）：具体代码实现一律以 `subagent` 派发给 **flash 模型**执行者
   （`model: "provider/id"`，来自 flash 池：deepseek/deepseek-flash、glm/glm-5.3-flash、
   relay/cmd-deepseek-v4.1-flash、opencode-go/deepseek-flash）。
2. **主会话不切模型**（FR-004）：会话进行中禁止切换主模型（前置缓存保护）。例外：`/new`
   干净会话可按轮切换（B 模式，须用户显式开启）。如遇强模型故障必须换主模型，先向用户
   显式提示缓存代价。
3. **代码 review 必须异源**（FR-007）：reviewer 子代理必须来自与实现者**不同厂商**的强模型，
   且用 fresh context（不携带实现者的思考过程）。
4. **计划/决策互审**（FR-008）：计划、架构决策由另一强厂商复核（A 产 B 审、B 产 A 审），
   互审结论随产物留痕。
5. **每次派发先问路由决策器**（绝对路径，任意目录可用）：

   ```bash
   echo '{"task_ref":"<id>","task_brief":"<≤2000字摘要>","role":"implement|review|plan|...",
         "implementer":{"vendor":"<产出者厂商>","model":"<...>"}或null,
         "risk_tags":[],"history":{"review_fail_count":0,"previous_models":[],
         "vendor_failures":[{"vendor":"<故障厂商>","trigger":"timeout"}]}}' \
     | ~/workspace/github/jev-pi-router/.venv/bin/python \
       ~/workspace/github/jev-pi-router/bin/jev-pi-decide \
       --config ~/workspace/github/jev-pi-router/router.config.yaml
   ```

   按返回的 `chosen` / `review_plan` / `fallback_order` 执行；`fail_open: true` 表示 Jev
   不可用、结果来自规则回退，正常继续（FR-006）。

## 双轨兜底（FR-009/010）

- **故障转移**：执行者报错（超时/配额/限流/5xx）→ 把故障写入 `vendor_failures` 重派
  `fallback_order[0]`；主会话无感继续。同一厂商连续失败由决策器自动熔断。
- **质量升级**：实现连续 **2 轮 review 不过** → 重新派发但 `history.review_fail_count` 置 2，
  决策器会返回强模型**换厂商**的 chosen（quality_upgrade 事件自动留痕）。
- 强模型执行失败 → 换另一强厂商（fallback_order 里的 strong 条目）。
- 池穷尽 → 显式告知用户 `pool_exhausted`，不要静默降级交付低质量产出。
- review 意见自相矛盾 → 你（主 Agent）仲裁，结论写入任务总结。

## review 派发模板

- 代码 review 子代理任务描述只含：改动 diff/文件清单、验收标准、风险标签——**不含**实现者的
  推理过程（独立性）。
- 计划互审子代理任务描述只含：计划全文 + 目标——由另一强厂商指出缺口。

## 观测

- 决策日志自动写入 `~/.jev-pi-router/decisions.jsonl`（FR-011），无需手工记录。
- 需要汇总时运行 `~/workspace/github/jev-pi-router/.venv/bin/python ~/workspace/github/jev-pi-router/bin/jev-pi-report --days 1`。

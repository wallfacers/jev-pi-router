# V7 / V8 用户验收手册（jev-pi-router）

> V1~V6、V9 已自动验证通过；本手册是你（人工）验收 V7（pi 端到端）和 V8（B 模式）的步骤。
> 对应 spec 场景：US1/US2（V7）、US4-3（V8）；判定指标：SC-002（review 覆盖 100%）、SC-006（会话内 0 模型切换）。

## 已就绪的环境（本轮已安装，无需操作）

- [x] `router.config.yaml` 就位，`cache_passthrough` 已实测回填：relay=`partial`、opencode-go=`full`、deepseek=`full`
- [x] promptCache 已合入 `~/.pi/agent/models.json`（8 个模型）
- [x] `~/.pi/agent/settings.json` → `cacheWarming: "idle"`（缓存保活）
- [x] 路由技能已安装 `~/.pi/agent/skills/jev-pi-router/`（绝对路径调用，任意目录可用）
- 备份：`~/.pi/agent/{models,settings}.json.bak-jev-pi`（回滚：覆盖回去即可）

## V7 — pi 端到端（预计 5 分钟）

1. **新开 pi 会话**（任意项目目录均可），输入测试任务（示例，可换）：

   > 使用 jev-pi-router 路由纪律完成以下任务：给 ~/workspace/github/jev-pi-router 加一个
   > `bin/jev-pi-stats` 小脚本（输出决策日志里各厂商的调用次数），并补一个 pytest 用例。

2. **期望观察**（对照技能纪律逐条看）：
   - 主 Agent（强模型，如 mimo-v2.6-pro）先调 `jev-pi-decide` 拿路由（implement → flash 池）；
   - **写代码**派给 flash 子代理（`subagent` 的 `model` 参数应为 `deepseek/deepseek-flash` / `glm/glm-5.3-flash` / `relay/cmd-deepseek-v4.1-flash` / `opencode-go/deepseek-flash` 之一）；
   - **代码 review** 派给**非实现厂商**的强模型子代理（如 deepseek 实现 → mimo 或 glm review），fresh context；
   - **计划/决策**被另一强厂商互审（mimo ↔ glm 双向）；
   - 主会话模型**全程未切换**。

3. **判定**（任务完成后跑）：

   ```bash
   ~/workspace/github/jev-pi-router/.venv/bin/python \
     ~/workspace/github/jev-pi-router/bin/jev-pi-report --days 1
   ```

   - ✅ `代码 review: cross_vendor` 占比 100%（SC-002）
   - ✅ `计划互审: mutual` ≥ 1（如产生了计划）
   - ✅ `模型分布` 里实现任务落在 flash 池条目
   - ✅ 主会话 0 模型切换（SC-006，观察 pi 状态栏模型名不变）

## V8 — B 模式（/new 会话逐轮切换，预计 1 分钟）

1. `/new` 新会话中逐轮使用不同模型（如第 1 轮 mimo-v2.6-pro、第 2 轮 glm-5.3）：
   - ✅ **应放行**（冷缓存无沉没成本）。
2. 在一个**进行中**的长会话里要求主 Agent 切换自己的模型：
   - ✅ **应拒绝**并提示缓存代价（技能 FR-004 纪律）；如确需切换，须向你显式提示后才做。

## 判定汇总模板（测完可直接回贴给我）

| 场景 | 结果（✅/❌） | 备注 |
|------|--------------|------|
| V7 flash 子代理写码 | | |
| V7 异源 review | | |
| V7 计划互审 | | |
| V7 主会话 0 切换 | | |
| V8 /new 切换放行 | | |
| V8 进行中会话拒绝切换 | | |

## 故障排查

- 决策器报"配置文件不存在" → 确认命令带了 `--config ~/workspace/github/jev-pi-router/router.config.yaml`。
- Jev fail-open 频繁 → 检查 `AI_GATEWAY_API_KEY`/`TYPESAFE_*` 环境变量（`.bashrc`）。
- 想看每次路由的细节 → `tail -f ~/.jev-pi-router/decisions.jsonl`。
- 回滚 pi 侧配置 → `cp ~/.pi/agent/{models,settings}.json.bak-jev-pi ~/.pi/agent/`（分别覆盖）。

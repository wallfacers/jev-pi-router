# Research: PI 多厂商模型路由（jev-pi-router）

Phase 0 输出。每项：**Decision / Rationale / Alternatives considered**。所有 Technical Context 未知项已消解，无 NEEDS CLARIFICATION 残留。

## R1. 派发机制：pi 原生子代理（`subagent` + `model` 参数）

- **Decision**: 主会话固定强模型；"代码实现"以 `subagent` 工具派发给 flash 模型执行（`model: "provider/id"` 指定），review/互审同样走子代理。派发纪律由 `pi/skills/jev-pi-router/SKILL.md` 约定。
- **Rationale**: pi 的 subagent 工具原生支持按子代理指定模型——零插件即用；子代理独立上下文 = 独立前置缓存边界，与"缓存边界对齐角色边界"策略天然吻合（FR-003/004）；上下文隔离还带来厂商间互审的"独立性"语义（fresh-context reviewer 无实现者上下文污染）。
- **Alternatives considered**:
  - 会话内 `/model` 逐轮切换：每次切换击穿主会话前置缓存（pi 官方文档：模型切换即停止缓存预热、缓存作废），与 FR-004 冲突——仅保留在 `/new` 冷会话（B 模式）。
  - 本地代理（127.0.0.1:8081 中转）按请求换后端：对话虽连续，但后端模型切换同样击穿各厂商 KV 前缀，且角色语义（谁在写代码）在代理层不可见，无法做异源 review 配对。
  - pi extension 拦截每条请求做路由：能力最强但属于二期（开发/调试成本高，MVP 不需要）。

## R2. 决策器：Jev typed-choice（经 Vercel AI Gateway）+ 规则引擎 fail-open（混合）

- **Decision**: 角色→池映射、异源配对默认序、兜底顺序走本地规则（`rules.py`）；模糊任务分类、复杂度分级、reviewer 配对裁决调用 Jev（`typesafe-ai/jev`，Vercel AI Gateway `experimental_evaluate`，choice/boolean/score 原语）；任何 Jev 错误 fail-open 回退规则（FR-005/006）。
- **Rationale**: 与澄清结论（Q2=混合）一致；Jev 是 evaluation 型模型（max_tokens:0），单次决策一个紧凑评估请求，不进入任何对话上下文、不冲缓存；`.bashrc` 已有 `AI_GATEWAY_API_KEY`，本地 `jev-ultrafast/jev_ultrafast/model.py` 有现成 Python 网关适配可复用；规则层保证确定性行为可测试、离线可用。
- **Alternatives considered**:
  - 纯规则：零成本但任务分类靠关键词/标签，模糊任务误判率高，"多厂商配对"无法按上下文权衡（用户明确要 Jev）。
  - 纯 Jev（jev-codex-router 模式，四问并发）：每次派发都有外部调用与延迟，MVP 阶段收益/成本不划算；保留为二期演进（R6）。
  - TypeSafe 原生 API（`jev-latest`）：与网关 id 不通用（已知坑），且失去 gateway 统一 key；放弃。

## R3. 实现语言：Python 3.11+

- **Decision**: 决策 CLI、状态机、报表、探针全部 Python 3.11+（stdlib + PyYAML + pytest）。
- **Rationale**: 复用 jev-ultrafast 的 Python 网关适配层（R2）；本机 Python 3.11+ 现成；工具是 CLI/库形态，无性能瓶颈。
- **Alternatives considered**: TypeScript/pi extension——pi extension 是二期形态（R1），MVP 用 Python 避免 npm 工程链；混合双语言（CLI Python + 抽 extension TS）过早拆分，拒绝。

## R4. 配置格式：YAML（`router.config.yaml`）

- **Decision**: 单一 YAML 文件承载模型池、角色映射、review 配对、兜底/熔断参数、auto 模式开关（schema 见 contracts/config-schema.md）。
- **Rationale**: 人可编辑、可注释（FR-012/013 强调"改配置不改代码"）；单文件单处可审；PyYAML 单依赖可接受。
- **Alternatives considered**: TOML（stdlib tomllib 只读、零依赖，但嵌套列表表达配对规则繁琐）；JSON（无注释，人机工程差）；env 变量（结构化能力不足）。

## R5. 前置缓存保护策略（核心约束的落地设计）

- **Decision**: 三层组合——① 主会话强模型**永不中途切换**（非 /new）；② 代码实现/异源 review 全部走子代理，缓存失效被隔离在角色边界；③ 为自定义模型在 pi models.json 声明 `promptCache: {short, long}` 并启用 `cacheWarming: "idle"`，让 pi 原生保活。路由权重纳入 `cache_passthrough` 属性（由 R8 实测定标）。
- **Rationale**: pi 官方文档确认：模型切换即停止预热且缓存作废；KV 前缀跟模型权重绑定，无跨模型共享（jev-codex-router 亦确认）。因此"少切换 + 切换点放角色边界 + 单模型内保活"是该约束下的最优解。jev-codex-router 的 route lease / cache 亲和进决策列为二期（与 spec Assumptions 一致）。
- **Alternatives considered**: 逐轮切换 + lease 抑制切换（仍会周期性击穿主上下文，拒绝，仅 /new 会话保留）；代理层 sticky 路由（角色不可见，R1 已拒）；跨模型 KV 复用（物理不可行，Non-Goal）。

## R6. 二期演进项（明确不在本期）

- **Decision**: route lease（one_call/tool_chain/user_turn）、cache 亲和信号（实测复用率）进 Jev 决策、pi extension 化（`cache_warming_decision` 覆盖、请求级拦截）——全部二期。
- **Rationale**: 与 spec Assumptions 的 Non-Goals/二期范围一致；MVP 用"角色边界隔离"已满足 SC-006。
- **Alternatives considered**: 一期全做——超出必要复杂度，拒绝。

## R7. 兜底状态机参数（默认值）

- **Decision**: 故障转移：同档池内按序换厂商，最多 2 次重试（SC-003 对齐）；熔断：同厂商连续 3 次失败停用 5 分钟（可配）；质量升级：同一实现连续 2 轮 review 不通过 → 强模型（换厂商）重做；强模型失败 → 另一强厂商接管；池穷尽 → 显式失败/升级并标记成本放大。触发器=超时/HTTP 5xx/配额/限流/显式拒绝。
- **Rationale**: 默认值直接对齐 SC-003/FR-009/010 与澄清 Q4（双轨）；参数入配置（R4）可调。
- **Alternatives considered**: 无限重试（成本不可控）；仅告警不升级（违背 Q4 双轨结论）。

## R8. 中转通道（relay / opencode-go）缓存透传实测方法

- **Decision**: `jev-pi-doctor probe <model>` 实施 A/B 实测：同一 1000+ token 前缀连续两次请求，比较两次响应 usage 的 `cache_read_input_tokens` / `cache_creation_input_tokens`（Anthropic messages 协议字段）。第二次命中缓存 → `cache_passthrough: full`；字段缺失/为 0 但延迟显著下降 → `partial`；无任何迹象 → `none`。结果写回配置的 `cache_passthrough` 属性，路由权重按 `full > partial > unknown > none` 排序。
- **Rationale**: Assumptions 已声明该项为实施期验证；给出可重复的实测协议让"缓存权重"从猜测变为数据（做法类似 jev-codex-router 的实测复用率统计）。
- **Alternatives considered**: 读中转源码判断（relay 是本地 codex-proxy/LiteLLM，可查但上游行为仍需实测确认）；直接假设透传（不诚实，风险大）。

## R9. 观测：JSONL 决策日志 + 报表

- **Decision**: 每次路由决策 append 一条 JSONL（schema 见 contracts/decision-log-schema.md）到 `~/.jev-pi-router/decisions.jsonl`；`jev-pi-report` 输出模型分布、fail-open 次数、转移/升级/熔断计数、降级告警（FR-011 / SC-002 的"显式标记"）。
- **Rationale**: 可审计是 FR-011 硬性要求；JSONL 只增不改、零依赖、可 grep/管道；结构借鉴 jev-codex-router 的 `jev-router-live.jsonl` + report 脚本实践。
- **Alternatives considered**: SQLite（过度）；只 stdout 不落盘（无法事后审计 SC-001/002 的统计验证）。

# jev-pi-router — PI 多厂商模型路由（计划）

> 状态：**澄清完成**（4/4 关键问题已确认，2026-09-22）。可直接实施，或先 `/speckit-specify` 蒸馏成正式 spec。

## 背景

PI（pi-coding-agent）本身无大小模型路由能力（PI-PROFILES.md："Pi 无大小模型之分"）。
本模块为 PI 提供任务级模型路由：**多厂商融合、跨厂商互相 review、不绑死厂商**。
无现成可插拔方案（GitHub 调研：pi-jev 系列均为小型实验项目），组合自研：
pi 原生机制（subagent `model` 参数 + cacheWarming + promptCache）+ Jev 决策 + jev-codex-router 缓存策略思想。

## 目标（已确认）

1. 强模型（默认 `mimo/mimo-v2.6-pro`、`glm/glm-5.3`）负责：写计划、做决策、review、兜底、主 Agent。
2. flash 模型（`deepseek/deepseek-flash`、`glm/glm-5.3-flash`、`relay/cmd-deepseek-v4.1-flash`、`opencode-go/deepseek-flash`）负责：实现具体代码。
3. 强模型组合**不写死**：配置可改，后续提供自动模式/自动探测。
4. 核心诉求：多厂商融合、多厂商协助相互 review、不绑死厂商。

## 架构（Q1 已确认：子 Agent 分工为主）

```
用户 → PI 主会话（强模型 mimo-v2.6-pro / glm-5.3，可配置/自动探测）
         │  主控 · 写计划 · 决策 · review · 兜底 —— 主会话模型永不中途切换
         ├─ 计划(强) ──▶ 强模型互审（mimo ↔ glm，另一厂商）
         ├─ 决策/配对 ─▶ Jev typed-choice（fail-open → 规则表）
         ├─ 实现(代码) ─▶ flash 子 Agent（deepseek-flash / glm-5.3-flash /
         │                relay cmd-deepseek-v4.1-flash / opencode-go deepseek-flash）
         ├─ 代码 review ─▶ 异源厂商强模型子 Agent
         └─ 兜底仲裁：故障转移（同档换厂商）/ 质量升级（2 次不过 → 强模型重做）
```

- 子 Agent 独立上下文 = 独立前置缓存边界，缓存失效被隔离在角色切换点上。
- **B 模式（主会话逐轮切换）仅在 `/new` 干净会话支持**：冷缓存下切换无沉没成本。

## 路由决策器（Q2 已确认：混合）

- **角色→模型映射走配置表**（确定性部分不调外部服务）：计划/决策/review/兜底/主 Agent → 强模型池；实现代码 → flash 池。
- **Jev typed-choice**（`typesafe-ai/jev`，经 Vercel AI Gateway `experimental_evaluate`，复用 jev-ultrafast 封装）裁决三类模糊判断：
  1. 子任务分类（设计/实现/杂务）与复杂度分级；
  2. 跨厂商 review 配对（选哪个厂商做 reviewer）；
  3. （二期）route lease 与 cache 亲和加权。
- **fail-open**：Jev 超时/报错 → 回退纯规则映射，任务不中断（对齐 jev-codex-router 思想）。
- 决策调用是独立 evaluation 请求（max_tokens:0），**不进入任何模型的对话上下文**，不冲击前置缓存。

## 多厂商交叉 review（Q3 已确认：双层）

- **代码层异源 review**：flash 实现产出 → 必须由**不同厂商**的强模型 review 一遍（如 deepseek-flash 实现 → mimo/glm review；glm-5.3-flash 实现 → mimo review），抓单厂商盲点。
- **计划/决策层强模型互审**：mimo ↔ glm 双向复核高杠杆产物（计划、架构决策），"相互 review" 双向成立。
- reviewer 配对由 Jev typed-choice 裁决（输入：任务类型、实现者厂商、风险标签），配置表提供默认配对兜底。
- 不绑死厂商：模型池/配对规则全部在配置文件，增删厂商不动代码。

## 兜底链（Q4 已确认：双轨）

- **故障转移**：触发 = 超时 / HTTP 5xx / 配额 / 限流 → 同档池内换下一个厂商重试（健康度优先），主会话无感；同一厂商连续 N 次失败熔断 M 分钟。
- **质量升级**：flash 实现两轮 review 不过 → 升级强模型重做（换厂商）；强模型失败 → 另一强模型接管。
- 由主 Agent（强模型）仲裁；Jev 可用 `score` 问题原语辅助判定"是否质量不达标"。

## 前置缓存策略

- 主会话强模型**永不中途切换**（主上下文 prefix 永不击穿）；flash 实现走子 Agent，缓存边界与角色边界对齐。
- 为自定义模型在 `~/.pi/agent/models.json` 声明 `promptCache: {short, long}`，启用 pi 原生 `cacheWarming: "idle"`（模型切换/压缩会停预热——所以才不在主会话内切换）。
- 借鉴 jev-codex-router：route lease（one_call / tool_chain / user_turn）减少切换、cache 亲和信号（实测复用率）进路由决策（二期）。
- ⚠️ Phase 0 实测：`relay`（127.0.0.1:8081）与 `opencode-go` 是否透传 `cache_control` / prompt_cache_key——不透传则该通道无缓存增益，路由权重下调。

## 自动模式 / 自动探测（二期设计草案）

- 默认强模型对来自配置（`strong_pool`），手动可改。
- auto 模式：启动时健康探针（轻量 1-token 探测）+ 运行时信号（错误率、TTFT、cache 复用率、成本表）→ 选择强模型对与 flash 派发顺序；信号可喂给 Jev 决策。
- 不绑死厂商：一切池子/配对/权重都是配置数据，不是代码。

## 配置形态（草案）

```yaml
strong_pool: [mimo/mimo-v2.6-pro, glm/glm-5.3]
flash_pool:  [deepseek/deepseek-flash, glm/glm-5.3-flash,
              relay/cmd-deepseek-v4.1-flash, opencode-go/deepseek-flash]
roles:
  plan: strong            # 写计划
  decision: strong        # 做决策
  review: strong          # review（配对规则见下）
  fallback_arbiter: strong# 兜底仲裁
  implement: flash        # 实现代码
review:
  code: cross_vendor_strong     # 代码：异源强模型审一遍
  plan_decision: mutual_strong  # 计划/决策：mimo ↔ glm 互审
decision_engine: { primary: jev, fallback: rules, fail_open: true }
fallback:
  fault_transfer: same_tier_next_vendor
  quality_upgrade: 2_review_fails_then_strong
```

## 交付形态

- **MVP**：pi 原生组合——角色提示词/skill + 子 Agent `model` 参数派发 + models.json `promptCache` 声明 + `cacheWarming: "idle"` + Jev 决策脚本（复用 jev-ultrafast）。
- **二期**：pi extension（路由钩子、`cache_warming_decision` 覆盖、route lease、cache 亲和进决策）。
- **观测**：决策日志 JSONL + report 脚本（借鉴 jev-codex-router `jev-router-live.jsonl` / `report_routing.py`，统计模型分布、cache 读写、升级/转移次数）。

## 实施阶段

1. **Phase 0（实测）**：relay / opencode-go 缓存透传实测；models.json 补 `promptCache` 声明。
2. **Phase 1（MVP）**：配置 + 角色子 Agent 派发 + Jev 决策脚本 + 双层 review + 双轨兜底跑通一个真实任务。
3. **Phase 2**：自动探测 + B 模式（/new 会话逐轮切换）+ 决策日志/report。
4. **Phase 3**：pi extension 化（route lease + cache 亲和进决策）。

## Clarifications

### Session 2026-09-22

- Q: 路由应该在哪一层发生——靠什么机制把"写代码"交给 flash 模型、把写计划/决策/review 留给强模型？ → A: 子 Agent 分工为主（主会话固定强模型，实现类派 flash 子 Agent，独立缓存边界）；主会话逐轮切换（B）仅在 /new 干净会话场景支持；强模型组合后续可配置，提供自动模式/自动探测；核心目标：多厂商融合、多厂商协助相互 review、不绑死厂商。
- Q: 每一步"该用哪个模型/哪个厂商"由什么裁决？ → A: 混合（C）——角色→模型走配置表；Jev typed-choice 裁决模糊任务分类、复杂度分级、跨厂商 review 配对；fail-open 回退规则。
- Q: "多厂商协助相互 review" 具体怎么配对，审几道？ → A: 双层（B）——代码层异源 review（flash 实现 → 不同厂商强模型审一遍）+ 计划/决策层强模型互审（mimo ↔ glm 双向）。
- Q: "兜底"具体指什么——失败时要自动恢复到什么程度？ → A: 双轨（C）——故障转移（超时/配额/报错同档换厂商重试）+ 质量升级（flash 两次不过 review → 强模型重做）。

## 待定（Open / Deferred）

- relay / opencode-go 缓存透传实测（Phase 0，技术验证非需求歧义）。
- 自动探测的信号权重与成本表来源（二期设计细节）。
- 量化验收指标（成本节省目标、质量基线）→ 建议实施期以决策日志数据校准。

# Tasks: PI 多厂商模型路由（jev-pi-router）

**Input**: Design documents from `/specs/001-pi-model-routing/`

**Prerequisites**: plan.md (required), spec.md (required for user stories), research.md, data-model.md, contracts/

**Tests**: 已包含——plan.md Testing 节明确要求 pytest（单测 + 契约测试 + 集成测试），quickstart V4/V5 依赖测试套件。

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1, US2, US3, US4)
- Include exact file paths in descriptions

## Path Conventions

- 单项目结构（plan.md Structure Decision）：`bin/`、`jev_pi_router/`、`pi/`、`tests/` 位于仓库根（`jev-pi-router/`）；文档路径以 `specs/001-pi-model-routing/` 为相对根。

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: 项目初始化与基础结构

- [x] T001 Create project structure per implementation plan in `pyproject.toml`, `jev_pi_router/__init__.py`, `bin/`, `tests/unit/`, `tests/contract/`, `tests/integration/` (per specs/001-pi-model-routing/plan.md Source Code tree)
- [x] T002 [P] Create default configuration `router.config.yaml.example` per specs/001-pi-model-routing/contracts/config-schema.md（默认池 = spec Assumptions 默认值）
- [x] T003 [P] Configure pytest and dev dependencies in `pyproject.toml`（[dev] extras: pytest、PyYAML）and `tests/conftest.py`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: 所有 User Story 共同依赖的决策底座（实体/配置、日志、CLI 骨架）

**⚠️ CRITICAL**: 本阶段完成前不得开始任何 User Story

- [x] T004 [P] Implement entity types and config loader/validator in `jev_pi_router/config.py`（ModelEntry/ModelPool/Role per specs/001-pi-model-routing/data-model.md；校验规则 per contracts/config-schema.md；FR-001/013 每次决策重新加载配置）
- [x] T005 [P] Implement JSONL decision log writer in `jev_pi_router/log.py`（RouteDecision/FallbackEvent schema + 三条不变量 per contracts/decision-log-schema.md；session_id_hash 不存原文；FR-011）
- [x] T006 Implement decision CLI skeleton in `bin/jev-pi-decide`（stdin/stdout JSON 契约、`--engine auto|rules|jev`、退出码 0/2/3 per contracts/decision-cli.md；engine 可插拔接口）
- [x] T007 [P] Write unit tests for config and log in `tests/unit/test_config.py`, `tests/unit/test_log.py`

**Checkpoint**: Foundation ready — user story implementation can now begin in parallel

---

## Phase 3: User Story 1 - 按任务类型自动分档派发 (Priority: P1) 🎯 MVP

**Goal**: 计划/决策/review/兜底/主控走强模型，代码实现派 flash 子代理；模糊任务由 Jev 分类；决策服务故障 fail-open 不中断

**Independent Test**: 用真实"新增小功能"任务验收：计划/决策由强模型产出、代码由 flash 子代理产出、全程无人工切模型；对照全程强模型基线成本可统计下降（quickstart V1/V2 可独立验证）

### Tests for User Story 1

- [x] T008 [P] [US1] Contract tests for decision CLI request/response schema in `tests/contract/test_decision_cli.py`（contracts/decision-cli.md schema；`--engine rules` 断言零网络调用）

### Implementation for User Story 1

- [x] T009 [P] [US1] Implement role→pool mapping and candidate filtering in `jev_pi_router/rules.py`（FR-002：implement→flash_pool、其余→strong_pool；task_class/complexity 默认规则；剔除熔断条目）
- [x] T010 [P] [US1] Implement Jev typed-choice client in `jev_pi_router/jev_client.py`（复用 jev-ultrafast/jev_ultrafast/model.py 网关适配；classification + complexity choice 问题原语；FR-005）
- [x] T011 [US1] Wire engine orchestration and fail-open into `jev_pi_router/decide.py` + `bin/jev-pi-decide`（Jev 失败→engine=rules、fail_open=true、exit 0；每次调用写 RouteDecision 日志；FR-005/006/011；depends: T006, T009, T010）
- [x] T012 [P] [US1] Author implement-dispatch discipline in `pi/skills/jev-pi-router/SKILL.md`（implement→flash 子代理派发；主会话不写代码；非 /new 会话主会话不切模型；FR-003/004）
- [x] T013 [US1] Validate quickstart V1+V2 in specs/001-pi-model-routing/quickstart.md（规则模式冒烟 + fail-open 验证）

**Checkpoint**: User Story 1 独立可测——分档派发 + fail-open 全链路可用

---

## Phase 4: User Story 2 - 多厂商交叉 review (Priority: P2)

**Goal**: 代码 review 异源厂商强制、计划/决策强厂商双向互审、单厂商枯竭显式降级

**Independent Test**: 人为让 flash 产出带隐蔽缺陷的实现，验收异源强模型 reviewer 指出缺陷；一份计划被另一强厂商互审留痕；强池单厂商时降级有显式标记（quickstart V3）

### Tests for User Story 2

- [x] T014 [P] [US2] Unit tests for pairing constraints in `tests/unit/test_rules_pairing.py`（FR-007 异源硬约束、FR-008 双向互审、degrade 标记、`[pairing-corrected]` 后置覆盖）

### Implementation for User Story 2

- [x] T015 [US2] Implement cross-vendor pairing and degrade handling in `jev_pi_router/rules.py`（code_review: reviewer.vendor != implementer.vendor 且 ∈ strong_pool（FR-007）；plan/decision 双向互审（FR-008）；strong 可用 vendor<2 → degrade=true + degrade_single_vendor 事件（US2-3）；Jev 配对违反约束时强制修正）
- [x] T016 [P] [US2] Add reviewer-pairing questions to `jev_pi_router/jev_client.py`（risk_tags 输入、reviewer 配对 choice 裁决；FR-005 配对半部）
- [x] T017 [P] [US2] Extend `pi/skills/jev-pi-router/SKILL.md` with review dispatch discipline（异源 reviewer 用 fresh-context 子代理；计划互审双向 A 产 B 审；review 意见回主 Agent 仲裁）
- [x] T018 [US2] Validate quickstart V3 in specs/001-pi-model-routing/quickstart.md（异源配对 + 降级标记）

**Checkpoint**: US1 + US2 均独立可用——产出可信度闭环成立

---

## Phase 5: User Story 3 - 故障与质量双轨兜底 (Priority: P2)

**Goal**: 故障转移（同档换厂商 + 熔断）与质量升级（2 轮 review 不过 → 强模型换厂商重做）自动恢复

**Independent Test**: 模拟厂商超时/配额故障验收 2 次重试内自动换厂商完成；模拟持续不合格 flash 产出验收两轮 review 后升级强模型重做（quickstart V4）

### Tests for User Story 3

- [x] T019 [P] [US3] State-machine unit tests in `tests/unit/test_fallback.py`（fault_transfer≤2 次、breaker 3 连败→open→300s→half_open→closed、quality_upgrade 2 轮 review_reject→strong 换厂商、pool_exhausted 显式失败——覆盖 data-model.md 全部状态转移）

### Implementation for User Story 3

- [x] T020 [US3] Implement fallback state machines in `jev_pi_router/fallback.py`（BreakerState + QualityEscalationState 转移 per specs/001-pi-model-routing/data-model.md；FallbackEvent 生成；FR-009/010；参数读 router.config.yaml fallback 节）
- [x] T021 [US3] Integrate fallback into `jev_pi_router/decide.py`（response.fallback_order 输出、history.review_fail_count 升级判定→chosen 落 strong 且换厂商、FallbackEvent 随 RouteDecision 写日志；depends: T020, T011）
- [x] T022 [P] [US3] Extend `pi/skills/jev-pi-router/SKILL.md` with fallback arbitration discipline（执行故障→带 trigger 重派；连续 2 轮 review 不过→升级强模型；互审矛盾→主 Agent 仲裁）
- [x] T023 [US3] Validate quickstart V4 in specs/001-pi-model-routing/quickstart.md（兜底状态机全场景）

**Checkpoint**: 三轨（派发/复核/兜底）全部独立可测

---

## Phase 6: User Story 4 - 配置化模型池与自动模式 (Priority: P3)

**Goal**: 改配置即增删厂商/调池（0 代码改动）；auto 模式按可用性/成本/延迟/缓存自动选强模型组合；B 模式 /new 会话逐轮切换

**Independent Test**: 从 flash_pool 移除某厂商验收不再派发（quickstart V9）；开启 auto 模式验收自动探测并选定可用强模型组合；/new 会话逐轮切换验收通过、进行中会话被拒（quickstart V8）

### Tests for User Story 4

- [x] T024 [P] [US4] Unit tests for auto-mode scoring in `tests/unit/test_rules_auto.py`（signals 权重重排、cache_passthrough 权重序 full>partial>unknown>none、enabled:false 时保持配置顺序）

### Implementation for User Story 4

- [x] T025 [P] [US4] Implement auto-mode signal scoring and pool reordering in `jev_pi_router/rules.py`（FR-012；signals: availability/cost/latency/cache per router.config.yaml auto_mode 节；probe 间隔缓存探针结果）
- [x] T026 [P] [US4] Implement `bin/jev-pi-doctor` check command（校验配置全部 api_ref 已注册于 ~/.pi/agent/models.json；契约 per contracts/decision-cli.md doctor 节）
- [x] T027 [P] [US4] Add b_mode fresh-session convention to `pi/skills/jev-pi-router/SKILL.md`（FR-004：/new 会话允许逐轮切换模型；进行中会话拒绝并提示缓存代价）
- [x] T028 [US4] Validate quickstart V8+V9 in specs/001-pi-model-routing/quickstart.md（B 模式切换 + 配置 0 代码改动生效）

**Checkpoint**: 全部 User Story 独立可测并组合可用

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: 跨故事的观测、实测校准与安装交付

- [x] T029 [P] Implement report aggregation in `jev_pi_router/report.py` + `bin/jev-pi-report`（FR-011：模型分布、engine/fail_open 统计、转移/升级/熔断/degrade 计数、review 覆盖率；`--json`；契约 per contracts/decision-cli.md report 节）
- [x] T030 [P] Implement probe command and `--write-back` in `bin/jev-pi-doctor`（research R8：同一 ≥1000-token 前缀 A/B 两次请求，按 usage.cache_read_input_tokens 判定 full/partial/none，回填 router.config.yaml cache_passthrough）
- [x] T031 [P] Create pi install assets `pi/models.promptcache.json` and install guide `README.md`（promptCache 声明合入 ~/.pi/agent/models.json、settings `cacheWarming: "idle"`；research R5）
- [x] T032 Run full test suite in `tests/`（pytest unit+contract+integration 全绿；quickstart V5）
- [ ] T033 Validate quickstart V6+V7 in specs/001-pi-model-routing/quickstart.md（中转缓存透传实测 + pi 端到端冒烟 + SC 对账表）

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: 无依赖——可立即开始
- **Foundational (Phase 2)**: 依赖 Setup 完成——**阻塞全部 User Story**
- **User Stories (Phase 3~6)**: 全部依赖 Foundational 完成；随后可并行（或按 P1→P2→P2→P3 顺序）
- **Polish (Phase 7)**: 依赖全部所需 User Story 完成

### User Story Dependencies

- **US1 (P1)**: 仅依赖 Foundational——与其他 Story 无依赖（MVP 主体）
- **US2 (P2)**: 仅依赖 Foundational；与 US1 共用 rules.py/jev_client.py 但按函数分区，独立可测（配对在 review 场景下自洽）
- **US3 (P2)**: 仅依赖 Foundational；升级判定消费 review 结果（与 US2 的事件语义对齐），但状态机独立可测
- **US4 (P3)**: 仅依赖 Foundational；auto 模式是 rules.py 的扩展策略，独立开关

### Within Each User Story

- 测试先行（本项目含测试任务：先写并确认失败，再实现）
- 规则/客户端（models 层）→ 编排/集成（decide 层）→ 派发约定（skill）→ quickstart 验证

### Parallel Opportunities

- Phase 1：T002、T003 并行
- Phase 2：T004、T005、T007 并行（T006 依赖 T004/T005 的接口约定后串行）
- 各 Story 内 [P] 任务并行（不同文件）
- Foundational 完成后，US1~US4 可由不同人/子代理并行推进（注意 rules.py、jev_client.py、SKILL.md 同文件任务跨 Story 串行）

---

## Parallel Example: User Story 1

```bash
# 测试与实现底座并行（不同文件）：
Task: "Contract tests in tests/contract/test_decision_cli.py"
Task: "role→pool mapping in jev_pi_router/rules.py"
Task: "Jev typed-choice client in jev_pi_router/jev_client.py"
Task: "implement-dispatch discipline in pi/skills/jev-pi-router/SKILL.md"
# 随后串行收口：
Task: "engine orchestration + fail-open in jev_pi_router/decide.py（依赖前四者）"
```

## Parallel Example: User Story 2

```bash
Task: "pairing constraint tests in tests/unit/test_rules_pairing.py"
Task: "reviewer-pairing questions in jev_pi_router/jev_client.py"
Task: "review dispatch discipline in pi/skills/jev-pi-router/SKILL.md"
# 随后串行收口：
Task: "cross-vendor pairing in jev_pi_router/rules.py"
```

---

## Implementation Strategy

### MVP First（仅 User Story 1）

1. 完成 Phase 1: Setup
2. 完成 Phase 2: Foundational（关键——阻塞所有 Story）
3. 完成 Phase 3: US1（T008~T013）
4. **STOP and VALIDATE**：quickstart V1+V2 独立验证通过
5. 此时已具备最小可用路由（分档派发 + fail-open）——MVP 交付点

### Incremental Delivery

1. Setup + Foundational → 底座就绪
2. + US1 → V1/V2 验证 → **MVP 可演示**（成本收益主体）
3. + US2 → V3 验证 → 多厂商互信闭环（差异化价值）
4. + US3 → V4 验证 → 无人值守韧性
5. + US4 → V8/V9 验证 → 可演进性
6. Polish → V5/V6/V7 全量验收 → SC 对账表勾完

### 同文件串行提醒

- `jev_pi_router/rules.py`：T009 → T015 → T025 串行（跨 US1/US2/US4）
- `jev_pi_router/jev_client.py`：T010 → T016 串行
- `pi/skills/jev-pi-router/SKILL.md`：T012 → T017 → T022 → T027 串行（或拆分小节合并提交）
- `bin/jev-pi-doctor`：T026 → T030 串行

---

## Notes

- [P] 任务 = 不同文件、无未完成依赖
- 每个任务完成后提交一次（或按 Checkpoint 提交）
- 各 Checkpoint 处独立验证该 Story（对应 quickstart 场景）
- SC 对账：SC-001←V7+report、SC-002←V3/V5/V7、SC-003←V4/V7、SC-004←V2、SC-005←V9、SC-006←V7/V8（见 quickstart.md 对账表）

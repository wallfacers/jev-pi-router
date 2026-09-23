# Tasks: 千问 Token Plan（qianwenai）厂商模型接入两档路由池

**Feature**: `002-qianwenai-vendor-pools` | **Plan**: [plan.md](./plan.md) | **Spec**: [spec.md](./spec.md)

**Input**: plan.md、spec.md、research.md（R1–R9）、data-model.md、contracts/、quickstart.md（V0–V6）

**Prerequisites**: 设计产物齐备；仓库基线绿色（Phase 1 验证）。

**约定**：本仓库为测试驱动风格（SC-002/SC-003 即测试锚点，plan Technical Context 指定 pytest），每个故事含测试任务；测试先行或与实现成对提交。

**格式**：`[TaskID] [P?] [Story?] 描述 + 文件路径`。`[P]` = 可并行（不同文件、无未完成依赖）。

---

## Phase 1: Setup

**Purpose**: 确认基线，避免把既有失败误归因于本特性。

- [x] T001 运行既有全量测试确认绿色基线：`pytest tests/ -v`（预期全部通过；如有失败先修复/记录再进入 Phase 2，对应 SC-002"既有测试零修改断言"前提）

---

## Phase 2: Foundational（阻塞所有用户故事）

**Purpose**: `family` 同源组字段是 US1 配置条目（契约含 family）与 US2 配对约束的共同前置（R1）。

**⚠️ CRITICAL PATH**: US1/US2/US3 全部依赖本阶段完成。

- [x] T002 [P] 在 `jev_pi_router/config.py` 为 `ModelEntry` 新增 `family: str = ""` 字段；`_parse_entries` 读取可选 `family`（非字符串/空串按 `""` 容错，不报 ConfigError）；`as_ref()` 在 family 非空时输出 `family` 键（data-model §1、contracts/config-schema.md §1）
- [x] T003 [P] 在 `tests/unit/test_config.py` 新增 family 解析用例：缺省为空串、显式声明生效、非字符串容错为空、含 family 条目的 `as_ref()` 输出、不含 family 的旧配置解析结果与既有一致（R1 兼容性承诺）
- [x] T004 运行 `pytest tests/unit/test_config.py -v` 验证 T002/T003 通过且既有用例零回归

**Checkpoint**: family 字段可用 → 三个用户故事可并行展开。

---

## Phase 3: User Story 1 - qianwenai 四模型按档位进入路由池（P1）🎯 MVP

**Goal**: qianwenai 4 条目纯配置入池（强池 +2、实现池 +2，尾部追加、平权取值），派发/轮换/停用行为与既有厂商无差别。

**Independent Test**: quickstart V2——implement 派发可轮到 `qianwenai/qwen3.8-flash`，plan 派发可轮到 `qianwenai/qwen3.8-max`；默认派发首选仍为既有条目（零倾斜）；配置校验通过。

- [x] T005 [P] [US1] 在 `router.config.yaml` strong_pool 尾部追加 `qianwenai/qwen3.8-max`（family: qwen3.8-max, cost_hint 1.0, cache_passthrough full）与 `qianwenai/glm-5.3`（family: glm-5.3, cost_hint 1.0, cache_passthrough full），并为既有 `glm/glm-5.3` 条目补 `family: glm-5.3`；flash_pool 尾部追加 `qianwenai/qwen3.8-flash`（family: qwen3.8-flash, cost_hint 0.15, full）与 `qianwenai/deepseek-v4.1-flash`（family: deepseek-v4.1-flash, cost_hint 0.1, full），并为既有 `relay/cmd-deepseek-v4.1-flash` 补 `family: deepseek-v4.1-flash`；既有条目顺序与其他字段一律不动（R4/R5/R7、contracts/config-schema.md §2/§3）
- [x] T006 [P] [US1] 同步更新 `router.config.yaml.example`，与 T005 的 router.config.yaml 增量一致（contracts/config-schema.md §6）
- [x] T007 [US1] 在 `tests/unit/test_decide.py` 新增用例：① implement 角色 + previous_models 排除既有 flash 条目 → chosen 为 `qianwenai/qwen3.8-flash`；② plan 角色 + previous_models 排除既有强条目 → chosen 为 `qianwenai/qwen3.8-max`；③ 默认派发（无 previous_models）首选仍为既有条目且 `fallback_order` 含全部 qianwenai 条目；④ qianwenai 条目 enabled=false 时不参与派发、其余 qianwenai 条目不受影响（US1 验收场景 1/2/3/4、FR-003/010）
- [x] T008 [US1] 运行 `pytest tests/unit -v && bin/jev-pi-doctor check`，验证 qianwenai 4 条 api_ref 全部通过 pi 注册检查、既有测试零回归（quickstart V0/V1/V2 的自动化部分）

**Checkpoint**: US1 可独立交付——qianwenai 入池即可被派发，套餐额度开始可用（MVP）。

---

## Phase 4: User Story 2 - qianwenai 参与跨厂商互审与兜底轮换（P2）

**Goal**: 同源约束三级降级落地（异源 → 同源兜底带告警 → 单厂商降级），Jev 路径同步收紧；qianwenai 纳入额度封禁/熔断（零代码，测试验证）；降级留痕可区分归因。

**Independent Test**: quickstart V3/V4——producer=`qianwenai/glm-5.3` 时 reviewer 为 mimo 而非 `glm/glm-5.3`；封禁 mimo 后同源兜底且 `degrade_reason="same_origin"`+`degrade_same_origin` 事件；封禁 qianwenai 后 0 次选中、解锁后下次调用恢复。

- [x] T009 [P] [US2] 在 `jev_pi_router/rules.py` 实现三级降级：`code_reviewer`/`plan_reviewer` 返回签名扩为 `(ref, degrade, reason)`，候选顺序 = vendor 异源且 family 非同源 → vendor 异源但 family 同源（degrade=True, reason="same_origin"）→ 任意可用（degrade=True, reason="single_vendor"，既有行为）；`pairing_valid` 增加 family 非同源判定；同源比较辅助函数 `same_origin(a, b)`（双方 family 非空且相等）（R2、data-model §3 状态转移）
- [x] T010 [P] [US2] 在 `tests/unit/test_rules_pairing.py` 新增用例：同源对（qianwenai/glm-5.3 ↔ glm/glm-5.3）正常时不互审；异源枯竭时同源兜底且 reason=same_origin；**归因唯一**——同源兜底命中时即使强池仅剩该单一异源厂商，reason 仍为 same_origin、不叠加 single_vendor（spec 边界用例，analyze I1）；同源+异源全枯竭时 reason=single_vendor（既有降级保留）；allow_degrade=False 时返回 (None, False, "")；未声明 family 的条目配对行为与既有一致；`pairing_valid` 拒绝同源配对（R2 全层级、SC-002 兼容承诺）
- [x] T011 [US2] 在 `jev_pi_router/decide.py` 适配：① producer（request["implementer"] 字典）的 family 按 api_ref 从两池条目解析注入，解析不到视为独立（R3）；② Jev `reviewer_pool` 过滤条件扩为 vendor 异源且 family 非同源；③ `code_reviewer` 三值返回适配，`review_plan` 增加 `degrade_reason` 字段；④ reason=same_origin 时发 `make_event("degrade_same_origin", "explicit", ts=ts)`，single_vendor 维持既有事件；⑤ rationale 补充降级原因说明（contracts/decision-log-schema.md §1/§2/§4）
- [x] T012 [US2] 在 `jev_pi_router/log.py` 扩展不变量1：`review_plan.degrade=true` ⇒ 事件含 `degrade_single_vendor` **或** `degrade_same_origin`；模块 docstring 同步；历史记录（无 degrade_reason/新事件）校验兼容（R8、contracts/decision-log-schema.md §3）
- [x] T013 [P] [US2] 在 `tests/unit/test_log.py` 新增用例：degrade+same_origin 事件通过校验；degrade 无任何降级事件仍抛 LogError；旧格式记录（无 degrade_reason）重放不报错（R8 向后兼容）
- [x] T014 [US2] 在 `tests/contract/test_decision_cli.py` 新增用例：① producer=qianwenai/glm-5.3 的 review 决策 → reviewer 非 qianwenai 非同源、degrade=false；② vendor_failures 封禁 mimo（trigger=quota）后同请求 → reviewer=glm/glm-5.3、degrade_reason=same_origin、事件含 degrade_same_origin；③ vendor_failures 上报 qianwenai quota → 后续派发 0 次选中 qianwenai、quota_hints 携带封禁信息；④ vendor_unlock 解锁 qianwenai → 同次响应即可重新选中；⑤ 熔断路径（analyze G1 补覆盖）：vendor_failures 上报 qianwenai 连续失败（trigger=explicit）达阈值 → breaker_open 事件、后续派发同档换商（US2 场景 5：qianwenai 两强模型均不可用时由其余强厂商接管，任务不中断）、vendor_success 上报后 breaker_close 恢复，且熔断计数与 quota 封禁相互独立（FR-006）（US2 验收场景 1–7、FR-004/005/006、SC-003/004）
- [x] T015 [US2] 运行 `pytest tests/unit tests/contract -v` 验证 T009–T014 全部通过且既有断言零修改（SC-002）

**Checkpoint**: US2 可独立交付——互审独立性与额度纪律对 qianwenai 完整生效。

---

## Phase 5: User Story 3 - qianwenai 模型的缓存时效声明对齐（P3）

**Goal**: promptcache 声明清单补齐 qianwenai 4 条；doctor 体检可发现清单缺口与疑似同源未声明；auto 排序中 qianwenai 按 full 档参与。

**Independent Test**: quickstart V6——清单断言 4/4 覆盖；临时删键后 `doctor check` 出现 WARN；auto 排序 qianwenai 位列 full 档（rank 3）。

- [x] T016 [P] [US3] 在 `pi/models.promptcache.json` 的 `promptCache` 映射补充 `qianwenai/qwen3.8-max`、`qianwenai/qwen3.8-flash`、`qianwenai/deepseek-v4.1-flash`、`qianwenai/glm-5.3` 四键，值均 `{"short": 300, "long": 3600}`（与 pi 侧 `~/.pi/agent/models.json` qianwenai 声明一致；R6、contracts/config-schema.md §5）
- [x] T017 [P] [US3] 在 `bin/jev-pi-doctor` 的 `cmd_check` 追加两项警告级检查（不改变退出码语义）：① router.config.yaml 条目在 promptcache 清单无键 → `WARN <api_ref> 缺少缓存声明清单条目`；② 两条目 model 名去渠道前缀（如 `cmd-`）后相同但 family 不一致或一方未声明 → `WARN <ref_a> 与 <ref_b> 疑似同源未声明 family`（R9、contracts/config-schema.md §4）
- [x] T018 [US3] 在 `tests/unit/test_rules_auto.py` 新增用例：auto 模式排序中 qianwenai 条目按 cache_passthrough=full（rank 3）参与、与同级既有 full 条目按 cost_hint 平权排序；在 `tests/unit/test_config.py` 或新增轻量测试中断言 promptcache 清单键集合 ⊇ router.config.yaml 全部 api_ref（data-model 一致性约束 4、SC-005、FR-007）
- [x] T019 [US3] 运行 `pytest tests/unit -v && bin/jev-pi-doctor check`，再执行 quickstart V1 反向验证（临时删除 `qianwenai/glm-5.3` 清单键 → check 输出对应 WARN → 还原）

**Checkpoint**: US3 可独立交付——缓存口径对齐，体检守缺口。

---

## Phase 6: Polish & Cross-Cutting

**Purpose**: 端到端验证、统计报表口径、文档一致性。

- [x] T020 [P] 在 `tests/unit/test_stats.py` 补充用例：qianwenai 作为普通厂商进入统计分组；日志无 qianwenai 记录时 stats/report 正常输出零值不报错（FR-009）
- [x] T021 [P] 检查 `README.md`、`TESTING.md`、`pi/skills/` 中涉及厂商/模型池清单的描述，如 enumerates 既有厂商则补充 qianwenai（仅文档同步，不改语义）；确认 `specs/001-pi-model-routing/contracts/config-schema.md` 顶部无需变更（002 契约以增量文件形式存在，001 契约保持历史原貌）
- [x] T022 端到端走查 quickstart V0–V6 全场景（`specs/002-qianwenai-vendor-pools/quickstart.md`），逐条核对验收对照表；V6 probe 为可选真实调用（消耗套餐额度），无 key 时跳过并记录
- [x] T023 运行全量 `pytest tests/ -v` + `bin/jev-pi-doctor check`，确认零失败零 WARN 后提交（commit message 引用 002 特性，含 Co-Authored-By 归属行）

---

## Dependencies

### 阶段依赖

```text
Phase 1 (T001)
  └─► Phase 2 Foundational (T002–T004)   ← family 字段，阻塞全部故事
        ├─► Phase 3 US1 (T005–T008)      ← MVP，独立可交付
        │     └─► Phase 4 US2 (T009–T015)   ← 同源约束依赖 US1 的 family 声明入配置
        │           └─► Phase 5 US3 (T016–T019) ← doctor 同源 WARN 依赖 family 已声明
        └────────────────────────────────┘
              └─► Phase 6 Polish (T020–T023)
```

说明：spec 中 US2/US3 优先级为 P2/P3 且各自可独立测试，但存在数据依赖——US2 的同源判定需要 T005 已写入 family 声明；US3 的"疑似同源未声明"WARN 需要 family 机制存在。故推荐顺序 US1 → US2 → US3；若并行开发，US3 的 T016（清单数据）可提前，T017 ② 需等 T005。

### 任务级关键路径

T001 → T002/T003 → T004 → T005 → T009 → T011 → T012 → T014 → T015 → T022 → T023

### 各故事内部顺序

- **US1**: T005/T006（[P] 并行，不同文件）→ T007 → T008
- **US2**: T009/T010（[P]）与 T013（[P]）可并行 → T011（依赖 T009）→ T012（依赖 T011 事件语义）→ T014 → T015
- **US3**: T016/T017（[P]）→ T018 → T019

## Parallel Execution Examples

```text
# Phase 2（两人/两 agent）：
T002（config.py）  ∥  T003（test_config.py）

# US1：
T005（router.config.yaml）  ∥  T006（router.config.yaml.example）

# US2（实现与测试分文件并行）：
T009（rules.py）  ∥  T010（test_rules_pairing.py）  ∥  T013（test_log.py）
# 之后串行：T011（decide.py，依赖 T009 签名）→ T012（log.py）→ T014

# US3：
T016（models.promptcache.json）  ∥  T017（bin/jev-pi-doctor）

# Polish：
T020（test_stats.py）  ∥  T021（文档同步）
```

## Implementation Strategy

1. **MVP = Phase 1 + 2 + US1**（T001–T008）：交付后 qianwenai 套餐立即可被派发消耗，独立产生价值（SC-001/SC-006 可观测），可先行提交。
2. **增量二 = US2**（T009–T015）：互审独立性与额度纪律，本特性唯一的规则变化集中在此，改完立即跑全量回归守住 SC-002。
3. **增量三 = US3**（T016–T019）：缓存口径与体检，纯数据+警告级检查，风险最低。
4. **收尾 = Polish**（T020–T023）：统计口径、文档、端到端走查、提交。
5. 每个 Checkpoint 处均可停止并独立验收（对应 spec 三个故事的 Independent Test）。

## Notes

- **代码级 review 修复（合并前，7 项发现）**：F1 Jev 候选引用补 cost_hint/cache_passthrough（`decide.jev_ref`）；F2/F6 doctor 清单损坏单条 WARN 不刷屏不崩溃（`_check_warnings` 容错 + 路径参数化）；F3 复核配对改用 available 过滤池，封禁期间兜底不穿透（契约 §4 落实）；F7 log 不变量增加 reason↔事件配对校验。F4（二元组签名归因推导）维持既有偏离并补注释，由 test_rules_pairing ②③ 层级用例守卫；F5（Jev reviewer 答案从未被消费）为 001 既有协议死代码，列为后续特性候选不阻塞本次合并。回归测试 +5（共 100 通过）。
- **实施偏差记录（002）**：① `code_reviewer` 保持二元组返回 + decide 侧推导归因（原 T009 三元组方案会破坏既有解包断言，违背 SC-002；见 research R2 修订）；② 既有 `test_load_valid` 池计数断言改为下限语义（`>=2 / >=4` + 成员断言）——池扩容下该数据性断言与"增删厂商只改配置"承诺天然冲突，为 SC-002 唯一例外；③ 厂商名按用户要求 qna → **qianwenai**（含 pi `~/.pi/agent/models.json`/`settings.json` 联动改名，原文件留有 `.bak-qna-rename` 备份；特性目录随之定名 002-qianwenai-vendor-pools）。
- 所有"运行 pytest"任务在仓库根目录执行（pyproject 已配 testpaths）。
- T005/T006 严禁调整既有条目顺序或字段值（R7 平权承诺，SC-002 前提）。
- `code_reviewer` 签名变更（二值→三值）是内部 API：调用点仅 decide.py（T011）与测试（T010），无外部契约破坏。
- 提交信息以 `Co-Authored-By: Claude Code <noreply@anthropic.com>` 结尾（会话归属约定）。

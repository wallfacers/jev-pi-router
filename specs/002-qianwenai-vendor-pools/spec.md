# Feature Specification: 千问 Token Plan（qianwenai）厂商模型接入两档路由池

**Feature Branch**: `002-qianwenai-vendor-pools`

**Created**: 2026-09-23

**Status**: Draft

**Input**: User description: "看下当前，项目的场景，pi的配置其实我当前加了，qianwenai厂商，qna,千问，它的套餐，有，qwen3.8-max、qwen3.8-flash、deepseek-v4.1-flash、glm-5.3 这些模型，还是一样的，qwen3.8-max 和glm-5.3这种作为顶级模型和mimo-2.6pro一样，其他的写代码你加进来"

## 背景与现状 *(context)*

开发者已在 pi 侧完成 `qianwenai`（qianwenai，千问 Token Plan 套餐）厂商接入：4 个模型（`qwen3.8-max`、`qwen3.8-flash`、`deepseek-v4.1-flash`、`glm-5.3`）均已在 pi 的厂商与模型清单中启用，且各自带有提示缓存（prompt cache）时效声明（短缓存 300 秒、长缓存 3600 秒）、1M 上下文；套餐内调用不按量计费。

jev-pi-router 侧（001 特性建立的两档路由体系）尚未纳入 qianwenai：强模型池与实现模型池、跨厂商 review 配对、故障/额度兜底轮换、缓存透传声明清单中都没有 qianwenai 条目。本特性的目标是把 qianwenai 的 4 个模型按用户指定的档位纳入既有路由体系，**规则与既有厂商完全一致**（"还是一样的"）：

| 模型 | 档位 | 对齐对象 |
|------|------|----------|
| `qianwenai/qwen3.8-max` | 强模型池（顶级） | 与 `mimo/mimo-v2.6-pro` 同档 |
| `qianwenai/glm-5.3` | 强模型池（顶级） | 与 `glm/glm-5.3` 同档 |
| `qianwenai/qwen3.8-flash` | 实现模型池（写代码） | 与既有 flash 档模型同档 |
| `qianwenai/deepseek-v4.1-flash` | 实现模型池（写代码） | 与既有 flash 档模型同档 |

## User Scenarios & Testing *(mandatory)*

### User Story 1 - qianwenai 四模型按档位进入路由池（Priority: P1）

开发者已完成 pi 侧 qianwenai 套餐接入，希望路由系统立即能使用这 4 个模型：`qwen3.8-max` 与 `glm-5.3` 作为顶级强模型参与计划、决策、复核、兜底、总控；`qwen3.8-flash` 与 `deepseek-v4.1-flash` 作为实现档模型承接代码编写任务。接入过程应当只是"增补配置条目"，不需要为 qianwenai 编写任何专门的厂商适配逻辑——这本身就是 001 特性"增删厂商只改配置"承诺的验证。

**Why this priority**: 这是本特性的全部核心价值。qianwenai 套餐已付费开通，模型不进入路由池就等于额度闲置；且它是后续故事（互审、兜底、缓存）的前提。

**Independent Test**: 更新路由配置后运行一次决策：implement 角色的任务能派发到 qianwenai 的 flash 档模型，plan/review 角色的任务能派发到 qianwenai 的强模型；配置校验通过（两池非空、条目字段合法）；全程无任何代码改动即生效。

**Acceptance Scenarios**:

1. **Given** 路由配置已加入 qianwenai 的 4 个模型条目，**When** 执行配置校验，**Then** 校验通过，强模型池含 `qianwenai/qwen3.8-max`、`qianwenai/glm-5.3`，实现池含 `qianwenai/qwen3.8-flash`、`qianwenai/deepseek-v4.1-flash`
2. **Given** 一个 implement 角色（写代码）的派发请求，**When** 路由裁决且 qianwenai 未被封禁/熔断，**Then** `qianwenai/qwen3.8-flash` 或 `qianwenai/deepseek-v4.1-flash` 可被选中，行为与既有实现档模型无差别
3. **Given** 一个 plan/decision/review 角色的派发请求，**When** 路由裁决，**Then** `qianwenai/qwen3.8-max`、`qianwenai/glm-5.3` 可作为顶级强模型被选中，地位与 `mimo/mimo-v2.6-pro`、`glm/glm-5.3` 等同
4. **Given** 配置中某个 qianwenai 条目被标记停用（enabled=false），**When** 路由裁决，**Then** 该模型不参与派发，其余 qianwenai 模型不受影响

---

### User Story 2 - qianwenai 参与跨厂商互审与兜底轮换（Priority: P2）

开发者希望 qianwenai 模型加入后，既有的多厂商协作规则原样适用，并且复核独立性因"底层模型同源约束"而更实质：qianwenai flash 模型写的代码由非 qianwenai 厂商且非同底层模型的强模型复核；qianwenai 强模型可以复核其他厂商的产出，也可以被其他强厂商复核；仅当所有真正异源候选枯竭时，同源模型才可带告警兜底参与；qianwenai 某模型故障或被熔断时，同档自动换下一家厂商重试；qianwenai 套餐额度耗尽时按既有"套餐额度封禁"机制整体封禁 qianwenai 厂商，解锁后自动恢复。

**Why this priority**: 互审与兜底是 001 体系的核心纪律，新厂商若游离于其外会破坏"不绑死厂商"与"产出不被单一厂商自我背书"的保证；qianwenai 的接入首次让"同一底层模型多渠道"同时出现在两池中，同源约束在此落地；但它依赖 P1 先入池。

**Independent Test**: 构造 qianwenai flash 模型产出代码的派发序列，验证 reviewer 为非 qianwenai 且非同源的强模型；构造 `qianwenai/glm-5.3` 产出并让其余强厂商全部封禁，验证 `glm/glm-5.3` 兜底复核且记录带降级告警；模拟 qianwenai 连续失败触发熔断，验证同档换厂商重试且冷却后恢复；模拟 qianwenai 套餐额度耗尽（quota 语义 429），验证 qianwenai 全厂商被立即封禁不再派发、解锁事件后恢复。

**Acceptance Scenarios**:

1. **Given** `qianwenai/qwen3.8-flash` 完成了代码实现，**When** 进入代码 review，**Then** reviewer 是非 qianwenai 厂商的强模型（异源约束按厂商名判定，与既有规则一致）
2. **Given** `qianwenai/qwen3.8-max` 产出了计划，**When** 进入互审，**Then** 由另一强厂商复核，互审结论随产物留痕
3. **Given** `qianwenai/glm-5.3` 产出待复核，**When** 配对 reviewer，**Then** 不选择与其同底层模型的 `glm/glm-5.3`，而是选择真正异源的强模型（如 mimo 或 qwen 系）
4. **Given** 强模型池中除 `glm/glm-5.3` 外全部不可用（额度封禁/熔断/停用），**When** `qianwenai/glm-5.3` 的产出仍需复核，**Then** 允许同源的 `glm/glm-5.3` 兜底参与，但复核记录带显式降级告警标记，不静默
5. **Given** qianwenai 某模型连续失败达到熔断阈值，**When** 再次派发，**Then** qianwenai 被临时停用、同档换其他厂商重试，冷却期后自动恢复
6. **Given** qianwenai 返回套餐额度耗尽（quota 语义），**When** 路由处理该信号，**Then** qianwenai 厂商被整体封禁（额度耗尽不是故障，不占用熔断重试预算），支持到期自动解锁、人工解锁、重置卡解锁，与既有厂商行为完全一致
7. **Given** 强模型池中 qianwenai 两个强模型均不可用（封禁/熔断/停用），**When** 需要强模型派发或互审，**Then** 由其余强厂商接管，任务不中断

---

### User Story 3 - qianwenai 模型的缓存时效声明对齐（Priority: P3）

开发者希望路由系统掌握 qianwenai 4 个模型的提示缓存透传信息（pi 侧已声明：短缓存 300 秒、长缓存 3600 秒），使"模型前置缓存"这一 001 核心目标对 qianwenai 同样成立：auto 模式排序、缓存亲和决策、doctor 观测都能把 qianwenai 模型与既有厂商模型放在同一口径下比较，而不是因信息缺失被保守排后。

**Why this priority**: 不影响基本派发正确性（缺失时按 unknown 保守处理即可），但影响 auto 模式收益与缓存决策质量；可独立延后交付。

**Independent Test**: 检查路由系统的缓存声明清单包含 qianwenai 4 个模型的条目且时效与 pi 侧声明一致；auto 模式排序时 qianwenai 模型按声明的缓存档位参与（不因信息缺失被默认降级）。

**Acceptance Scenarios**:

1. **Given** 缓存声明清单已补充 qianwenai 4 个模型，**When** 路由读取缓存透传信息，**Then** 得到的时效与 pi 侧声明一致（短 300 秒/长 3600 秒），而非 unknown 兜底值
2. **Given** auto 模式开启，**When** 对实现池排序，**Then** qianwenai flash 模型按其缓存档位与成本提示参与排序，规则与其他厂商一致
3. **Given** qianwenai 某模型未在缓存声明清单中登记，**When** 路由读取，**Then** 按 unknown 保守处理并可在体检（doctor）中发现该缺口，不报错不中断

---

### Edge Cases

- **同底层模型、不同渠道**：`qianwenai/glm-5.3` 与 `glm/glm-5.3`、`qianwenai/deepseek-v4.1-flash` 与 `relay/cmd-deepseek-v4.1-flash` 为同源模型对，按 FR-004 原则上不互为 reviewer，仅异源枯竭时带告警兜底。
- **同源组标识缺失**：既有条目未声明同源组时按"各自独立"处理（不追溯猜测），仅对显式声明同源的条目生效约束；体检可提示疑似同源但未声明的模型对。
- **qianwenai 整商不可用**：qianwenai 4 个模型全部被封禁/熔断时，两池仍非空（其余厂商兜底），配置校验与派发均不受影响。
- **强模型池排序稳定性**：qianwenai 两个强模型平权入池后，非 auto 模式下派发顺序按配置顺序，qianwenai 插入位置只影响轮换起点，不影响任何模型的可选性。
- **同源兜底与单厂商降级的归因**：两类降级按层级互斥——命中同源兜底时即以 same_origin 归因留痕（即使此时强池实际仅剩单一异源厂商可用），不重复叠加 single_vendor 标记；两类告警经事件类型与降级原因字段可区分、可归因（analyze I1 修订：由"同时留痕"改为"归因唯一"，与三级降级设计一致）。
- **统计与报表口径**：决策日志、stats、report 中 qianwenai 条目作为新厂商出现，历史数据无 qianwenai 记录时对比报表不应报错。
- **多模态差异**：`qwen3.8-max`、`qianwenai/glm-5.3` 支持图像输入，`qwen3.8-flash`、`deepseek-v4.1-flash` 仅文本；当前路由不感知输入模态，含图任务的模态适配由 pi 侧模型能力约束兜底（沿用现状，不在本特性范围内扩展）。

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: 路由配置 MUST 支持将 `qianwenai/qwen3.8-max`、`qianwenai/glm-5.3` 登记为强模型池条目，`qianwenai/qwen3.8-flash`、`qianwenai/deepseek-v4.1-flash` 登记为实现模型池条目，字段结构与既有条目兼容（厂商、模型、引用、成本提示、缓存透传、启用开关；为支持 FR-004 可增加**通用**的同源组标识可选字段，对所有厂商可用，非 qianwenai 专属）。
- **FR-002**: qianwenai 模型的接入 MUST 纯配置完成，不引入任何 qianwenai 专属代码分支——既有"增删厂商只改配置"的约束在 qianwenai 上得到验证（FR-004 的同源约束是厂商无关的通用配对规则，不违反本条）。
- **FR-003**: qianwenai 条目 MUST 完整参与既有派发纪律：角色→池映射、池内顺序选择、avoid 引用/厂商换商重试，行为与其他厂商条目无差别。
- **FR-004**: 跨厂商 review 配对 MUST 在"厂商名异源"之上引入**底层模型同源约束**（通用规则，适用于所有厂商条目）：同底层模型的条目（如 `qianwenai/glm-5.3` 与 `glm/glm-5.3`、`qianwenai/deepseek-v4.1-flash` 与 `relay/cmd-deepseek-v4.1-flash`）原则上不互为 reviewer；仅当同档内所有真正异源候选均不可用（额度封禁/熔断/停用）时，同源条目方可兜底参与复核，且 MUST 带显式降级告警标记留痕（沿用既有 degrade 语义），不静默。
- **FR-005**: qianwenai MUST 纳入既有套餐额度封禁机制：quota 语义的额度耗尽信号触发 qianwenai 整商封禁，支持到期自动解锁、人工解锁、重置卡解锁；额度封禁与熔断状态相互独立。
- **FR-006**: qianwenai MUST 纳入既有熔断机制：连续失败达阈值即临时停用、同档换商、冷却自动恢复。
- **FR-007**: 路由系统的缓存时效声明清单 MUST 补充 qianwenai 4 个模型的条目，取值与 pi 侧已声明的缓存时效一致；未登记条目按 unknown 保守处理且可被体检发现。
- **FR-008**: qianwenai 条目 MUST 与既有同级模型**完全平权**：成本提示取同档既有条目的同级值，不因套餐边际成本低而倾斜；非 auto 模式按配置顺序轮换，auto 模式按既有排序键（缓存、成本、延迟）自然参与，不引入 qianwenai 专属优先级。
- **FR-009**: 决策日志、统计与报表 MUST 将 qianwenai 作为普通厂商纳入，无 qianwenai 历史数据时不报错。
- **FR-010**: qianwenai 任一条目停用（enabled=false）或整商不可用时，两池 MUST 仍满足非空校验，派发不中断。

### Key Entities

- **模型池条目（ModelEntry）**: 厂商名、模型名、统一引用（vendor/model）、所属池（strong/flash）、成本提示、缓存透传档位、启用开关、同源组标识（可选，通用字段，标记底层模型归属，用于 FR-004 配对约束）。本特性新增 4 条 qianwenai 条目，并为 `qianwenai/glm-5.3`↔`glm/glm-5.3`、`qianwenai/deepseek-v4.1-flash`↔`relay/cmd-deepseek-v4.1-flash` 声明同源组。
- **缓存时效声明**: 按统一引用（vendor/model）登记的提示缓存时效，供 auto 排序与缓存亲和决策使用。本特性新增 qianwenai 4 条。
- **套餐额度封禁记录（Quota）**: 按厂商维度的封禁状态、解锁时间、重置卡台账。qianwenai 作为新厂商天然纳入，无结构变化。

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: qianwenai 4 个模型全部出现在路由决策的可选集中：implement 任务派发结果中可观测到 qianwenai flash 模型被选中，plan/review 任务派发结果中可观测到 qianwenai 强模型被选中（真实或模拟派发各至少 1 次验证）。
- **SC-002**: qianwenai 接入本身零厂商专属逻辑分支——仅配置与数据条目增补；唯一的规则变化（同源约束）是厂商无关的通用配对规则，对既有条目默认无行为变化，既有全量测试不修改断言即通过。
- **SC-003**: qianwenai 产出代码的 review 100% 由非 qianwenai 厂商强模型执行；同源模型对（如 `qianwenai/glm-5.3`↔`glm/glm-5.3`）互审次数为 0，除非异源候选枯竭的兜底情形，且兜底情形 100% 带降级告警标记（抽样决策日志验证）。
- **SC-004**: 模拟 qianwenai 额度耗尽后，后续派发 0 次选中 qianwenai；解锁后恢复选中的延迟不超过下一次决策调用。
- **SC-005**: 缓存声明清单覆盖 qianwenai 4/4 模型，auto 模式排序中 qianwenai 模型不因信息缺失被降为 unknown 档。
- **SC-006**: qianwenai 套餐额度得到实际利用：接入后一周内决策日志中 qianwenai 模型被派发占比 > 0，且平权配置下 qianwenai 与同级既有模型的派发量分布无系统性偏斜（若长期为 0 说明排序或封禁配置有问题）。

## Assumptions

- pi 侧 qianwenai 厂商与 4 个模型的接入（含 API 凭据、缓存时效声明）已完成且可用，本特性只做路由侧纳管，不改 pi 全局配置。
- "还是一样的"指路由/互审/兜底/额度规则对 qianwenai 与既有厂商完全一致，qianwenai 无任何专属行为；经澄清确认的两项决定：① 互审配对引入厂商无关的底层模型同源约束（枯竭时同源可带告警兜底）；② qianwenai 在同档池内与既有模型完全平权，不做成本倾斜。
- qianwenai 为套餐（Token Plan）计费，套餐内调用边际成本趋近于零，但按平权原则成本提示仍取同档既有条目的同级值。
- 同源组以显式配置声明为准，系统不自动推断底层模型归属；疑似同源未声明的模型对仅由体检提示，不参与配对约束。
- 既有厂商名维度的异源判定、熔断、额度封禁、重置卡、统计报表机制（001 特性及 S12 演进）保持不变，本特性不重构。
- 多模态（图像输入）路由感知不在本特性范围内。

## Dependencies

- 依赖 001 特性（pi-model-routing）已交付的两池路由、跨厂商配对、熔断/兜底机制。
- 依赖 S12 已交付的套餐额度封禁与重置卡机制。
- 依赖 pi 侧 `qianwenai` 厂商配置（`~/.pi/agent/models.json`、`settings.json`）持续有效。

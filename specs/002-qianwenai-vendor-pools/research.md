# Phase 0 Research: qianwenai 厂商模型接入两档路由池

对应 spec 澄清结论：Q1=引入底层模型同源约束（枯竭时可带告警兜底），Q2=与既有同级完全平权。

## R1 同源识别机制：配置声明 `family` 通用可选字段

**Decision**: `ModelEntry` 增加通用可选字段 `family`（同源组标识，字符串，缺省空 = 独立）。同源判定：两条目 `family` 均非空且相等 ⇒ 同源。配置中为 `glm/glm-5.3`、`qianwenai/glm-5.3` 声明 `family: glm-5.3`；为 `relay/cmd-deepseek-v4.1-flash`、`qianwenai/deepseek-v4.1-flash` 声明 `family: deepseek-v4.1-flash`。

**Rationale**: spec 假设明确"同源组以显式配置声明为准，系统不自动推断"；字段厂商无关，满足 FR-002（qianwenai 零专属分支）；未声明条目（mimo、deepseek、opencode-go 等）行为零变化，满足 SC-002。

**实施后补充**：`deepseek/deepseek-flash` 与 `opencode-go/deepseek-flash` 同底层模型对亦补声明 `family: deepseek-flash`（review 修复轮次，实质同源互斥优先于"行为零变化"）。

**Alternatives considered**:
- 按 model 名相似度自动推断同源——被拒：误判风险高（`mimo-v2.6-pro` vs `mimo-v2.6-flash` 同前缀不同档），且违反 spec"不自动推断"假设。
- 维持仅厂商名判定——被拒：用户澄清明确要求实质异源。
- 独立的同源映射表文件——被拒：新增配置源违反"唯一配置源"约定（001 config-schema），且两处维护易漂移。

## R2 配对降级层级：vendor 异源 → family 异源 → 兜底

**Decision**: `rules.code_reviewer` / `plan_reviewer` 选择顺序改为三级：
1. `vendor != producer.vendor` 且非 family 同源 → 正常返回 `(ref, degrade=False, reason="")`；
2. 无①时，若 `allow_degrade` 且存在 `vendor != producer.vendor` 但 family 同源的候选 → 返回 `(ref, degrade=True, reason="same_origin")`；
3. 无①②时，若 `allow_degrade` 且池非空 → 返回首个候选（可能同 vendor）→ `(ref, degrade=True, reason="single_vendor")`（既有行为）；
4. `allow_degrade=False` 或池空 → `(None, False, "")`。

`degrade` 布尔语义保持"非正常配对"以兼容 log 不变量1 的触发条件。**实施修订（SC-002）**：`code_reviewer` 返回签名保持 `(ref, degrade)` 二元组不变——扩为三元组会破坏 `test_rules_pairing.py` 既有 6 处解包断言；归因改由 decide 侧推导（层级②必为厂商异源、层级③必为同厂商，`reviewer.vendor != producer.vendor` 即 same_origin，二者无损区分），`review_plan.degrade_reason` 字段语义不变。

**Rationale**: 满足 FR-004（同源原则不互审 + 枯竭兜底带告警）与边界用例"归因唯一：same_origin 优先，不叠加 single_vendor，两类告警可区分"（analyze I1 修订后口径）——reason 区分②③，事件类型区分留痕（R8）。既有单厂商降级路径完全保留。

**Alternatives considered**:
- 同源枯竭直接视为"无异源候选"跳到③——被拒：用户明确"其他顶级模型都到额度了，同源也不是不可以参与"，②优先于③保留复核而非跳过。
- degrade 改枚举字符串替换布尔——被拒：破坏 log.py 不变量与既有测试断言（SC-002 要求既有测试不改）。

## R3 Jev 路径同步收紧：候选池过滤 + 硬约束后置修正

**Decision**: `decide.py` 中传给 Jev 的 `reviewer_pool` 过滤条件由 `e.vendor != producer.vendor` 扩为"vendor 异源且非 family 同源"；`rules.pairing_valid` 同步增加 family 判定（`reviewer.vendor != producer.vendor` 且非 family 同源），保持"Jev 结果硬约束后置修正"语义——Jev 若返回同源 reviewer，按 R2 降级层级重选并标记 corrected。

**producer family 解析**：review 场景的 producer 来自 `request["implementer"]`（字典 `{vendor, model[, api_ref]}`，非 ModelEntry），其 family 由 decide 在两池条目中按 `api_ref`（缺失时以 `vendor/model` 拼接）查得后注入字典，再传入 rules 层比较；查不到对应条目（如已下线的 producer）时 family 视为空 = 独立，仅按厂商名判定——保持向后兼容。

**Rationale**: 001 约定 Jev 结果的配对约束在 rules 层强制修正；只改 rules 不改候选池会让 Jev 持续收到无效候选、修正率虚高。

**Alternatives considered**: 仅后置修正不过滤候选——被拒：浪费 Jev 决策位（候选截断 [:5]），且修正路径成为常态而非异常。

## R4 cost_hint：平权取同档同级值

**Decision**: `qianwenai/qwen3.8-max` = 1.0、`qianwenai/glm-5.3` = 1.0（与全部强池条目同值）；`qianwenai/qwen3.8-flash` = 0.15（与 deepseek/glm 直连 flash 同级）；`qianwenai/deepseek-v4.1-flash` = 0.1（与其同源条目 `relay/cmd-deepseek-v4.1-flash` 同值）。

**Rationale**: 用户澄清"与既有同级完全平权"；同源条目取同值可避免 auto 排序在两条同源条目间产生人为偏斜。套餐边际成本低的事实不进入 cost_hint（FR-008 明确不倾斜）。

**Alternatives considered**: qianwenai 全取 0（套餐免费）——被拒：违反平权澄清，且会让 auto 排序永远首选 qianwenai，变相绑定单一厂商。

## R5 cache_passthrough 初值：`full`，doctor probe 校准兜底

**Decision**: router.config.yaml 中 qianwenai 4 条目 `cache_passthrough: full`。依据：qianwenai 为千问官方 Token Plan 直连渠道（anthropic-messages API），pi 侧 models.json 已由用户声明 promptCache（short 300/long 3600）；quickstart 提供 `jev-pi-doctor probe --write-back` 实测校准步骤，若实测不符以实测为准回填。

**Rationale**: SC-005 要求 auto 排序中 qianwenai 不因信息缺失被降为 unknown 档；pi 侧已有明确缓存声明，unknown 保守值反而制造信息缺失。既有 mimo/glm 条目维持 unknown 不动（平权原则不追溯改既有值）。

**Alternatives considered**:
- 初值 unknown + 仅靠 probe 回填——被拒：SC-005 不满足，且用户 pi 侧声明已构成依据。
- 同步把 mimo/glm 也改 full——被拒：超出本特性范围，无新证据不动既有条目。

## R6 promptcache 声明清单补充

**Decision**: `pi/models.promptcache.json` 的 `promptCache` 映射补充 4 键：`qianwenai/qwen3.8-max`、`qianwenai/qwen3.8-flash`、`qianwenai/deepseek-v4.1-flash`、`qianwenai/glm-5.3`，值均 `{"short": 300, "long": 3600}`，与 pi 侧 `~/.pi/agent/models.json` qianwenai 各模型已声明的 promptCache 完全一致（FR-007）。

**Rationale**: 该清单是仓库内可分发的缓存声明源（001 R5 约定：合并入 pi models.json）；用户本机已配好，此步保证仓库与 pi 侧一致、新环境可复现。

**Alternatives considered**: 不动清单只依赖用户本机配置——被拒：FR-007 明确要求补充，且换机/协作时缺口会重现。

## R7 池内插入位置：追加尾部，不动既有条目

**Decision**: strong_pool 在 glm 之后追加 qianwenai 两条（顺序：mimo → glm → qianwenai/qwen3.8-max → qianwenai/glm-5.3）；flash_pool 尾部追加 qianwenai 两条（qwen3.8-flash → deepseek-v4.1-flash）。既有条目顺序、字段一律不动。

**Rationale**: 非 auto 模式"配置顺序 = 优先级"（001 契约），平权澄清（Q2）要求不倾斜；尾部追加使既有派发首选行为零变化（SC-002），同时 qianwenai 仍进入轮换（fallback_order 覆盖全池，SC-006 可达）。

**Alternatives considered**: qianwenai 前置（套餐优先消耗）——被拒：Q2 明确平权，前置即事实倾斜。

## R8 降级留痕：新事件类型 + review_plan.degrade_reason + log 不变量1 扩展

**Decision**:
- `decide.py`：reason="same_origin" 时发 `make_event("degrade_same_origin", "explicit", ts=ts)`；reason="single_vendor" 时维持既有 `degrade_single_vendor` 事件。`review_plan` 增加 `degrade_reason` 字段（`""` / `"same_origin"` / `"single_vendor"`），`degrade` 布尔保留。
- `log.py` 不变量1 扩展为：`review_plan.degrade=true` ⇒ 事件中存在 `degrade_single_vendor` **或** `degrade_same_origin`。旧日志记录（无 degrade_reason、无新事件）校验逻辑不受影响——不变量只在 degrade=true 时触发，历史记录已满足原事件要求。
- 两类降级同时成立时（同源兜底且实为单厂商可用），按 R2 层级只会命中其一（②先于③），归因由 reason 唯一确定。spec 边界用例已修订对齐（analyze I1，方案 a）：归因唯一，same_origin 优先，不叠加 single_vendor 标记。

**Rationale**: 满足 FR-004"显式降级告警标记留痕"与边界用例可区分归因；向后兼容历史 JSONL（SC-002 既有测试不改）。

**Alternatives considered**: 复用 degrade_single_vendor 事件加 detail 字段——被拒：事件 type 是统计/报表分组键，混用会污染既有口径（FR-009）。

## R9 doctor 体检增强：promptcache 缺口 + 疑似同源提示

**Decision**: `bin/jev-pi-doctor` 的 `cmd_check` 追加两项**警告级**检查（不改变退出码语义，warning 不致 fail）：
1. router.config.yaml 中任一条目在 `pi/models.promptcache.json` 无对应键 → 提示缓存声明缺口（FR-007"未登记可被体检发现"）；
2. 两条目 model 名尾部相同（去渠道前缀后，如 `cmd-`）但 family 声明不一致或一方未声明 → 提示"疑似同源未声明"（边界用例）。qianwenai 4 条已在 pi models.json 注册，既有"api_ref 已注册于 pi"检查天然通过。

**Rationale**: 均为轻量只读检查，复用 cmd_check 现有输出格式；把 spec 的"体检可发现"落地为可执行断言点。

**Alternatives considered**: 新增独立子命令——被拒：check 语义即"配置健康检查"，拆命令增加使用成本。

## 未决项

无。spec 中两处 NEEDS CLARIFICATION 已由用户澄清并回填（FR-004/FR-008），本文件 R1–R9 覆盖全部技术未知项。

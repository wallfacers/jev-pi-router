# Implementation Plan: 千问 Token Plan（qianwenai）厂商模型接入两档路由池

**Branch**: `002-qianwenai-vendor-pools` | **Date**: 2026-09-23 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/002-qianwenai-vendor-pools/spec.md`

## Summary

将 qianwenai（qianwenai 千问 Token Plan 套餐）的 4 个模型纳入既有两档路由池：`qianwenai/qwen3.8-max`、`qianwenai/glm-5.3` 入强模型池（cost_hint 1.0，与 mimo-v2.6-pro 同档），`qianwenai/qwen3.8-flash`、`qianwenai/deepseek-v4.1-flash` 入实现池（cost_hint 0.15/0.1，与同级平权）。接入以纯配置增补为主，验证"增删厂商只改配置"；唯一的规则变化是引入**厂商无关的底层模型同源约束**（通用可选字段 `family` + 配对规则两级降级 + Jev 候选池/硬约束同步收紧），使 `qianwenai/glm-5.3`↔`glm/glm-5.3`、`qianwenai/deepseek-v4.1-flash`↔`relay/cmd-deepseek-v4.1-flash` 等同源模型对原则上不互为 reviewer，仅在真正异源候选枯竭时带 `degrade_same_origin` 告警兜底。缓存侧补充 `pi/models.promptcache.json` qianwenai 4 条声明（short 300/long 3600，与 pi 侧一致）。

## Technical Context

**Language/Version**: Python ≥ 3.11（与 001 特性一致）

**Primary Dependencies**: PyYAML ≥ 6.0（无新增依赖）；pi CLI（`~/.pi/agent/models.json` 已含 qianwenai 厂商与 4 模型，本特性不改 pi 全局配置）

**Storage**: `router.config.yaml`（池条目+family）、`router.config.yaml.example`、`pi/models.promptcache.json`（缓存声明清单）、`~/.jev-pi-router/`（决策日志 JSONL、quotas.json——结构均不变，仅新增事件类型与 review_plan 字段）

**Testing**: pytest ≥ 8.0（`tests/unit/`、`tests/contract/`；沿用既有 test_config / test_rules_pairing / test_decision_cli 风格）

**Target Platform**: Linux / WSL2（开发者本机 CLI）

**Project Type**: CLI 工具 + 库（`jev_pi_router/` 包 + `bin/jev-pi-*` 入口）

**Performance Goals**: 决策路径延迟不劣化（同源过滤为 O(池大小) 内存判断，无新增 I/O）

**Constraints**: 既有全量测试不修改断言即通过（SC-002）；qianwenai 零厂商专属代码分支（FR-002）；日志不变量向后兼容历史记录（旧记录无 degrade_reason 仍可通过校验）

**Scale/Scope**: 强池 2→4 条、实现池 4→6 条；改动文件 ≈ 8 个（config.py、rules.py、decide.py、log.py、doctor、2 个 yaml、promptcache.json）+ 测试

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

`.specify/memory/constitution.md` 为未填充模板，无项目专属 gate。按仓库既有工程约定（001 特性沉淀）作为事实约束检查：

| 约定 | 符合性 |
|------|--------|
| 配置驱动：增删厂商只改配置（001 FR-013） | ✅ qianwenai 接入纯配置；`family` 为通用可选字段，非 qianwenai 专属 |
| fail-open：决策服务不可用回退规则引擎 | ✅ 同源约束在 rules 层实现，Jev 路径经同一硬约束后置修正 |
| 无静默降级：降级必须留痕（log 不变量1） | ✅ 新增 `degrade_same_origin` 事件类型并扩展不变量1 |
| 契约文档随特性归档（specs/NNN/contracts/） | ✅ 本特性产出 config-schema 与 decision-log-schema 增量契约 |
| 测试先行、既有测试不回归 | ✅ SC-002/SC-003 固化为验收断言 |

**Gate 结果**: PASS（Phase 0 前与 Phase 1 后各评估一次，均无违例，Complexity Tracking 留空）

## Project Structure

### Documentation (this feature)

```text
specs/002-qianwenai-vendor-pools/
├── plan.md              # 本文件
├── research.md          # Phase 0：R1–R9 决策
├── data-model.md        # Phase 1：实体与字段增量
├── quickstart.md        # Phase 1：端到端验证指南
├── contracts/
│   ├── config-schema.md        # 001 同名契约的增量（qianwenai 条目 + family 字段）
│   └── decision-log-schema.md  # 001 同名契约的增量（degrade_same_origin / degrade_reason）
├── checklists/requirements.md
└── tasks.md             # Phase 2 输出（/speckit-tasks 生成，非本命令）
```

### Source Code (repository root)

```text
jev_pi_router/
├── config.py            # ModelEntry 增加 family 字段；_parse_entries 读取可选 family
├── rules.py             # code_reviewer 两级降级（same_origin/single_vendor）；pairing_valid 增加 family 判定
├── decide.py            # Jev reviewer_pool 按 vendor+family 过滤；review_plan 增加 degrade_reason；新事件降级留痕
├── log.py               # 不变量1 扩展：degrade=true ⇒ degrade_single_vendor 或 degrade_same_origin 事件
├── quota.py             # 不改（厂商维度封禁天然覆盖 qianwenai）
├── fallback.py          # 不改
└── jev_client.py        # 不改（候选描述随 as_ref 自动携带）

bin/
└── jev-pi-doctor        # cmd_check 增加两项提示：promptcache 清单缺口、疑似同源未声明

pi/
└── models.promptcache.json   # 补充 qianwenai 4 条（short 300 / long 3600）

router.config.yaml            # strong_pool +2、flash_pool +2（含 family 声明）
router.config.yaml.example    # 同步

tests/
├── unit/
│   ├── test_config.py            # family 解析/缺省/非法值容错
│   ├── test_rules_pairing.py     # 同源约束、两级降级、pairing_valid family 判定
│   └── test_rules_auto.py        # auto 排序含 qianwenai 条目（平权）
└── contract/
    └── test_decision_cli.py      # qianwenai 派发、degrade_same_origin 事件、日志不变量
```

**Structure Decision**: 沿用单项目布局（包 + bin CLI + 配置/数据文件在仓库根与 `pi/`），无新目录；所有改动落在既有文件内，不新增模块。

## Complexity Tracking

> 无 Constitution 违例，本节留空。

（同源约束是用户在 spec 澄清中明确要求的通用配对规则增强，非过度设计；以可选字段 + 两级降级实现，未声明 family 的既有条目行为零变化。）

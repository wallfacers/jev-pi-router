# Implementation Plan: PI 多厂商模型路由（jev-pi-router）

**Branch**: `001-pi-model-routing` | **Date**: 2026-09-22 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/001-pi-model-routing/spec.md`

## Summary

为 pi 提供任务级多厂商模型路由：强模型（mimo/glm 池）负责计划/决策/review/兜底/主控，flash 模型（deepseek/glm-flash/relay/opencode-go 池）以独立上下文子代理实现代码；混合裁决（配置表 + Jev typed-choice + fail-open）；双层跨厂商 review（代码异源、计划/决策强厂商互审）；双轨兜底（故障转移 + 质量升级 + 熔断）；主会话零切换保护前置缓存，/new 会话支持逐轮切换。技术路线：Python 决策 CLI + router.config.yaml + pi 原生子代理派发（详见 research.md）。

## Technical Context

**Language/Version**: Python 3.11+（决策 CLI/状态机/报表，复用 jev-ultrafast 网关适配层）；pi 集成资产为 YAML/JSON/Markdown（skills）；二期 pi extension 用 TypeScript

**Primary Dependencies**: pi（pi-coding-agent，子代理 `model` 参数、`cacheWarming`、models.json `promptCache`）；TypeSafe Jev 经 Vercel AI Gateway `experimental_evaluate`（choice/boolean/score 问题原语）；PyYAML（配置解析）；pytest

**Storage**: 纯文件——`router.config.yaml`（配置，人可编辑）；JSONL 决策日志（`~/.jev-pi-router/decisions.jsonl`，只增不改）；无数据库

**Testing**: pytest（rules/fallback/config 单测 + 决策 CLI JSON 契约测试 + Jev fail-open 集成测试）；quickstart.md 端到端验证场景

**Target Platform**: Linux (WSL2) 单机上的 pi coding agent 环境；无服务部署、无网络监听

**Project Type**: cli 工具 + pi 集成配置/skills 包（单项目）

**Performance Goals**: 决策延迟——Jev 路径 <2s、规则路径 <50ms；决策调用不增加任何对话模型的输入 token（独立 evaluation 请求）；报表生成 <1s（千条日志）

**Constraints**: 非 /new 会话主会话 0 模型切换（FR-004）；决策服务故障必须 fail-open（FR-006）；单厂商故障须在 2 次重试内恢复（SC-003）；配置变更 0 代码改动（FR-013）

**Scale/Scope**: 单机单用户；默认 5 供应商 / 9 模型条目；单任务 3~10 次派发；日志规模 ~1k 条/周

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

`.specify/memory/constitution.md` 为未填写的占位模板（10 处 `[PRINCIPLE_n]` 未定义），视为**项目宪法未启用**：

| Gate | 状态 | 说明 |
|------|------|------|
| 原则合规 | N/A | 无已确立原则 |
| 测试先行 | N/A | 无宪法强制；项目仍按 pytest 测试设计实施（见 Testing） |
| API 兼容 | N/A | 全新项目，无既有公共 API |
| 复杂度上限 | ✅ 通过 | 单项目结构，无多包/多服务拆分（见 Complexity Tracking：无违规） |

**Phase 1 后复核**：设计产物（data-model / contracts / quickstart）未引入宪法冲突（宪法未启用）；复杂度无新增违规。✅

## Project Structure

### Documentation (this feature)

```text
specs/001-pi-model-routing/
├── plan.md              # 本文件（/speckit.plan 输出）
├── research.md          # Phase 0 输出
├── data-model.md        # Phase 1 输出
├── quickstart.md        # Phase 1 输出
├── contracts/           # Phase 1 输出
│   ├── decision-cli.md
│   ├── config-schema.md
│   └── decision-log-schema.md
└── tasks.md             # Phase 2 输出（/speckit.tasks 创建，本命令不创建）
```

### Source Code (repository root)

```text
jev-pi-router/
├── router.config.yaml            # 模型池/角色/配对/兜底配置（FR-001/012/013）
├── bin/
│   ├── jev-pi-decide             # 路由决策 CLI：stdin JSON → stdout JSON（契约见 contracts/decision-cli.md）
│   ├── jev-pi-report             # 决策日志报表（模型分布/转移/升级/降级统计）
│   └── jev-pi-doctor             # 厂商健康探针 + 中转缓存透传实测
├── jev_pi_router/
│   ├── __init__.py
│   ├── config.py                 # 配置加载/校验（schema 见 contracts/config-schema.md）
│   ├── rules.py                  # 规则引擎：角色→池映射、异源配对、兜底顺序（Jev fail-open 回退）
│   ├── jev_client.py             # Jev typed-choice 封装（复用 jev_ultrafast.model 网关适配）
│   ├── fallback.py               # 双轨兜底状态机：故障转移/质量升级/熔断（FR-009/010）
│   ├── log.py                    # JSONL 决策日志（schema 见 contracts/decision-log-schema.md）
│   └── report.py                 # 报表聚合
├── pi/                           # pi 侧集成资产
│   ├── skills/jev-pi-router/SKILL.md   # 主 Agent 路由技能（派发约定与角色纪律）
│   └── models.promptcache.json   # 合入 ~/.pi/agent/models.json 的 promptCache 声明片段
└── tests/
    ├── unit/                     # rules / fallback / config 单测
    ├── contract/                 # 决策 CLI JSON in/out 契约测试
    └── integration/              # Jev fail-open、报表、doctor 探针
```

**Structure Decision**: 单项目 CLI + pi 集成资产。理由：核心产物是"决策器 + 配置 + 派发约定"，pi 原生子代理承担执行，无需服务进程；`bin/` 三个入口分别对应决策（FR-005）、观测（FR-011）、实测/探针（Assumptions 中转验证项）；`pi/` 只放需要安装到 pi 环境的资产，与工具代码分离。

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

无违规（宪法未启用；单项目结构未超出必要复杂度）。

# Contract: router.config.yaml（配置 Schema）

Phase 1 输出。唯一配置源（FR-001/012/013）：增删厂商、改池、配对、兜底参数均只改此文件。

## 完整示例

```yaml
version: 1

# ── 模型池（有序；顺序 = 手动模式路由优先级）────────────────────────
strong_pool:
  - vendor: mimo
    model: mimo-v2.6-pro
    api_ref: mimo/mimo-v2.6-pro
    cost_hint: 1.0
    cache_passthrough: unknown    # jev-pi-doctor probe --write-back 回填
    enabled: true
  - vendor: glm
    model: glm-5.3
    api_ref: glm/glm-5.3
    cost_hint: 1.0
    cache_passthrough: unknown
    enabled: true

flash_pool:
  - vendor: deepseek
    model: deepseek-flash
    api_ref: deepseek/deepseek-flash
    cost_hint: 0.15
    cache_passthrough: full       # api.deepseek.com 官方直连，厂商缓存已知
    enabled: true
  - vendor: glm
    model: glm-5.3-flash
    api_ref: glm/glm-5.3-flash
    cost_hint: 0.15
    cache_passthrough: unknown
    enabled: true
  - vendor: relay
    model: cmd-deepseek-v4.1-flash
    api_ref: relay/cmd-deepseek-v4.1-flash
    cost_hint: 0.1
    cache_passthrough: unknown    # 中转，R8 实测项
    enabled: true
  - vendor: opencode-go
    model: deepseek-flash
    api_ref: opencode-go/deepseek-flash
    cost_hint: 0.1
    cache_passthrough: unknown    # 中转，R8 实测项
    enabled: true

# ── 角色 → 池绑定（FR-002）──────────────────────────────────────
roles:
  orchestrator: strong
  plan: strong
  decision: strong
  review: strong
  fallback_arbiter: strong
  implement: flash

# ── 跨厂商 review（FR-007/008）──────────────────────────────────
review:
  code: cross_vendor_strong        # 代码：异源强模型审一遍
  plan_decision: mutual_strong     # 计划/决策：强厂商双向互审
  max_rounds: 2                    # review 轮数上限（质量升级阈值同源）
  degrade_to_single_vendor: true   # 强池只剩单厂商时降级 + 显式标记（US2-3）

# ── 决策引擎（FR-005/006，research R2）────────────────────────────
decision_engine:
  primary: jev                     # jev | rules
  fallback: rules
  timeout_ms: 2000
  fail_open: true                  # 强制开启（FR-006）；false 仅测试用

# ── 双轨兜底（FR-009/010，research R7）───────────────────────────
fallback:
  fault_transfer:
    max_attempts: 2                # 同档换厂商重试上限（SC-003）
  quality_upgrade:
    review_fails: 2                # 连续 N 轮 review 不过 → 升级
    escalate_to: strong
    switch_vendor: true            # 升级必须换厂商
  breaker:
    consecutive_failures: 3        # 连续失败 N 次开断（vendor 级）
    cooldown_sec: 300              # 冷却窗口
    window_sec: 3600               # v1.4：api_ref 条目级滑窗长度（秒）
    window_failures: 2             # v1.4：滑窗内 N 次熔断类失败 → 该 api_ref 冷却
    api_ref_cooldown_sec: 300      # v1.4：api_ref 冷却时长（vendor_success 带 api_ref 即时解除）

# ── 自动模式 / 自动探测（FR-012，US4）────────────────────────────
auto_mode:
  enabled: false                   # true 时按信号自动重排池序/选强模型对
  probe_interval_min: 30
  signals: [availability, cost, latency, cache]

# ── B 模式（FR-004，US4-3）──────────────────────────────────────
b_mode:
  allow_per_turn_switch_on_fresh_session: true   # 仅 /new 会话生效
```

## 字段校验规则

| 规则 | 源 |
|------|-----|
| `strong_pool`、`flash_pool` 非空 | FR-001 |
| 每个 `api_ref` 必须注册于 pi `~/.pi/agent/models.json`（`jev-pi-doctor check` 验证） | 部署前提 |
| `vendor` 为异源配对判别键：同 vendor 多条目视为"同源" | FR-007 |
| `roles.*` ∈ {strong, flash}；`implement` 必须 = flash（FR-003） | FR-002/003 |
| `decision_engine.fail_open` 必须为 `true` 方可投产 | FR-006 |
| `review.max_rounds` ≥ 1；`quality_upgrade.review_fails` ≤ `review.max_rounds` | FR-010 一致性 |
| `cache_passthrough` ∈ {full, partial, none, unknown}；路由权重序 full > partial > unknown > none | R8 |
| `fallback.breaker.window_sec` / `window_failures` / `api_ref_cooldown_sec` 均 ≥ 1（v1.4 条目级滑窗；`window_failures=1` = 单次失败即冷却，合法但激进） | v1.4 |
| 配置变更即时生效（每次决策重新加载） | FR-013 |

## pi 侧配套（非本文件，安装时合并）

- `pi/models.promptcache.json` → 合并入 `~/.pi/agent/models.json`，为各模型声明 `promptCache: {short, long}`（启用 cacheWarming，research R5）。
- `~/.pi/settings.json` → `"cacheWarming": "idle"`。

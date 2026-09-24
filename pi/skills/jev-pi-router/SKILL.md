---
name: jev-pi-router
description: Multi-vendor model routing dispatch discipline for PI. Strong models handle planning/decision/review/fallback/orchestration; flash sub-agents implement code; cross-vendor mutual review; dual-track failover. Use when the task involves "writing code / implementing features / modifying code" or requires code review.
---

# jev-pi-router — Dispatch Discipline

You are the strong-model main Agent (orchestrator). The following discipline ensures cost-tiered dispatch, prompt-cache integrity, and multi-vendor trust.

## Repo Root (resolve once per session)

The repo sits at **different absolute paths on different machines** — never hardcode it. Resolve the
root once, then use `$JEV_PI_ROUTER_ROOT` in every router command. Precedence: an already-exported
`JEV_PI_ROUTER_ROOT` wins; otherwise probe the known checkouts (`~/project/jev-pi-router`,
`~/workspace/github/jev-pi-router`):

```bash
export JEV_PI_ROUTER_ROOT="${JEV_PI_ROUTER_ROOT:-$(ls -d ~/project/jev-pi-router ~/workspace/github/jev-pi-router 2>/dev/null | head -1)}"
[ -d "$JEV_PI_ROUTER_ROOT" ] || echo "router repo not found — export JEV_PI_ROUTER_ROOT manually" >&2
```

## Hard Rules (Never Violate)

1. **Main session never writes code** (FR-003): all concrete code implementation must be dispatched as `subagent` tasks to **flash-model** executors
   (`model: "provider/id"`, from the flash pool: deepseek/deepseek-flash, glm/glm-5.3-flash,
   relay/cmd-deepseek-v4.1-flash, opencode-go/deepseek-flash, qianwenai/qwen3.8-flash,
   qianwenai/deepseek-v4.1-flash).
2. **Main session never switches models** (FR-004): switching the main model mid-session is prohibited (prompt-cache protection). Exception: a `/new` clean session may switch per turn (B mode, requires explicit user opt-in). If the strong model fails and a model switch is unavoidable, explicitly inform the user of the cache cost first.
3. **Code review must be cross-vendor** (FR-007): the reviewer sub-agent must be a strong model from a **different vendor** than the implementer — and, since 002, not the same underlying model reached through another channel (entries declaring an identical `family` are same-origin and are mutually excluded from reviewing each other unless all truly-foreign candidates are exhausted; the router marks such fallbacks with `degrade_reason: "same_origin"`) — and must use a fresh context (no carry-over of the implementer's reasoning).
4. **Plan/decision mutual review** (FR-008): plans and architectural decisions must be reviewed by a strong model from another vendor (A produces → B reviews, B produces → A reviews), subject to the same-origin exclusion of rule 3. The review conclusion is recorded alongside the artifact.
5. **Every dispatch must first consult the router decision engine** (via `$JEV_PI_ROUTER_ROOT` from §Repo Root, works from any directory):

   ```bash
   echo '{"task_ref":"<id>","task_brief":"<≤2000-char summary>","role":"implement|review|plan|...",
         "implementer":{"vendor":"<producer vendor>","model":"<...>"} or null,
         "risk_tags":[],"history":{"review_fail_count":0,"previous_models":[]},
         "vendor_failures":[{"vendor":"<failed vendor>","api_ref":"<provider/model>","trigger":"timeout"}],
         "vendor_success":[{"vendor":"<recently succeeded vendor>","api_ref":"<provider/model>"}]}' \
     | "$JEV_PI_ROUTER_ROOT/.venv/bin/python" \
       "$JEV_PI_ROUTER_ROOT/bin/jev-pi-decide" \
       --config "$JEV_PI_ROUTER_ROOT/router.config.yaml"
   ```

   Execute according to the returned `chosen` / `review_plan` / `fallback_order`; `fail_open: true` means Jev is unavailable and the result comes from the rule-based fallback — proceed normally (FR-006).

## Dual-Track Failover (FR-009/010)

- **Fault transfer**: executor reports an error (timeout/quota/throttle/5xx) → write the failure into the **top-level** `vendor_failures` (always include `api_ref` — `provider/model` — so the entry-level window can pinpoint the exact endpoint rather than the whole vendor) and re-dispatch to `fallback_order[0]`; the main session continues without disruption. Consecutive failures from the same vendor are automatically circuit-broken by the decision engine; after a **successful** execution, include top-level `vendor_success` on the next request to close the breaker (breaker_close is logged) — with `api_ref` it also clears that entry's sliding window and lifts its cooldown (api_ref_recover).
- **Empty / degenerate response (v1.4)**: if a dispatch ends with every assistant response having empty `content` **and** all-zero usage (input/output/total tokens = 0) — e.g. an auto-retry death loop — report `trigger:"empty_response"` with both `vendor` and `api_ref`. Zero-content zero-token is a provider failure on its own: report it on the **first** occurrence, never gated on retry count (the retry count is corroborating evidence only). Never let such a loop stay inside the sub-agent's own retry logic — it must be reported to the router. Effect: one report demotes that entry (healthier same-family alternatives are picked first); a second failure within `window_sec` cools the exact `api_ref` and takes it out of the pool.
- **429 triage (critical)**: ① Concurrency/transient rate limiting ("rate limit"/"concurrent"/retry-after in seconds) → `trigger:"rate_limit"`, retry as-is, does not count as a failure and does not ban the vendor; ② Plan/weekly quota exhausted ("quota"/"billing"/"usage limit"/`x-ratelimit-reset` spanning days) → `trigger:"quota"` (optionally include `quota_until` reset timestamp and `key_id`): that vendor is **immediately banned and will not be selected** on subsequent requests. If `quota_until` is provided, auto-unlock when it expires; otherwise ban indefinitely. After the user confirms the plan has been reset, unlock via `vendor_unlock:[{"vendor":"..."}]` or `bin/jev-pi-doctor unlock <vendor>` (run `bin/jev-pi-doctor quotas` to view the ban table).
  - **Early reset (event/reset card, S12.1)**: the plan has not reached its reset time but the user has early-reset it via an **event or reset card** (e.g. Codex event/reset card) → use `vendor_unlock:[{"vendor":"...","reason":"reset_card"}]` (or `reason:"activity"`) to **unlock immediately without waiting for quota_until**; `reset_card` deducts a card (`bin/jev-pi-doctor cards <vendor> --add N` to record cards / check balance; proceed even without cards). The decision response's `quota_hints` indicates auto-unlock times and remaining reset cards for each banned vendor — **when you see hints, proactively ask the user "would you like to unlock with a reset card?"**, then issue `vendor_unlock` only after user consent.
- **Quality escalation**: implementation fails review **2 consecutive rounds** → re-dispatch with `history.review_fail_count` set to 2; the decision engine will return a `chosen` that switches to a **different-vendor strong model** (quality_upgrade event is automatically logged).
- Strong-model execution failure → switch to another strong vendor (strong entries in `fallback_order`).
- Pool exhausted → explicitly inform the user of `pool_exhausted`; do not silently degrade to low-quality output.
- Review opinions contradict each other → you (the main Agent) arbitrate; record the conclusion in the task summary.

## Review Dispatch Template

- Code review sub-agent task description must contain only: the change diff / file list, acceptance criteria, and risk tags — **not** the implementer's reasoning process (independence).
- Plan mutual-review sub-agent task description must contain only: the full plan text + objectives — the other strong vendor identifies gaps.

## Observability

- Decision logs are automatically written to `~/.jev-pi-router/decisions.jsonl` (FR-011); no manual recording needed.
- For a summary report, run `"$JEV_PI_ROUTER_ROOT/.venv/bin/python" "$JEV_PI_ROUTER_ROOT/bin/jev-pi-report" --days 1`.

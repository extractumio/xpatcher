# Product Owner Review: xpatcher v1 Design Specification

**Reviewer:** Product Owner perspective
**Date:** 2026-03-29
**Spec Version:** 1.2 + Missing Components Docs 13-17
**Documents Reviewed:** All 17 design documents, consolidated review, UX review, questions for PO

---

## VERDICT: Reduce Scope

The architecture is sound. The engineering team has done exceptional design work, and the review process was thorough -- all 7 critical issues, 14 major issues, and 16 missing components have been resolved at the spec level. The design is internally consistent and well-reasoned.

However, the scope of what is called "v1" is not a v1. It is a v1.5 or v2 wearing a v1 label. The specification describes a system with 16 pipeline stages, 8 agents, 11 Pydantic models, 37 semantic validation rules, 16 error types, 11 configuration sub-models, 3 configuration layers, tiered quality gates, an expert panel, gap detection with recursive re-entry, a cancellation workflow, a rollback system, an upgrade mechanism, a `doctor` command, and 6 user-facing documents. The implementation roadmap estimates 9+ weeks with a minimum team of 3 people.

This level of specification is a strength if the goal is completeness. It is a risk if the goal is to ship something useful and iterate. The single most important question is: **can a user get value from xpatcher before all 16 stages, 8 agents, and 37 validation rules are implemented?** The answer is obviously yes -- the core value loop is `plan -> approve -> execute -> test -> review`, which is 5-6 stages and 4 agents. Everything else is refinement.

The recommendation is to ship a focused v1 in 4-5 weeks, then expand to the current spec as v1.1/v1.2 based on real usage data.

---

## 1. VALUE PROPOSITION CLARITY

### What xpatcher does that Claude Code alone does not

The spec (Section 1) articulates this clearly: xpatcher adds **structured pipeline orchestration** to Claude Code's raw capabilities. Specifically:

- **Planning before coding**: Claude Code starts coding immediately. xpatcher forces a plan-review-approve cycle before any code is written. This is the single most valuable feature -- it prevents the "agent coded for 20 minutes and went in the wrong direction" problem.
- **Adversarial review isolation**: The reviewer cannot see the executor's reasoning. This is structurally better than asking Claude Code "now review what you just wrote."
- **Iteration caps with escalation**: Claude Code has no concept of "this is not converging, stop." xpatcher does.
- **Crash recovery and resumability**: A 45-minute pipeline that fails at minute 40 can resume. With Claude Code alone, you start over.
- **Auditable artifacts**: Every decision is recorded in YAML. With Claude Code, the conversation is the only record.

This is a strong value proposition. But it is buried in architectural language. The executive summary (Section 1) reads like a system design document, not a pitch. A user reading it should understand within 30 seconds: "xpatcher adds planning, review, and quality gates to Claude Code so your AI-generated code is actually production-ready."

### Target user

The spec is ambiguous about who the target user is. It mentions:
- "Developer workstation, personal use" (Section 2.3.1)
- "Shared CI/CD server, team use" (Section 2.3.1)
- "Server-wide installation" (Section 2.3.1)

These are three very different user profiles with different needs. For v1, the target should be crystal clear: **a single developer on their workstation using Claude Code who wants better-than-conversational code quality for multi-file features**. CI/CD and team use are v2.

---

## 2. V1 SCOPE ASSESSMENT: Too Ambitious

The current v1 scope is unrealistic for a first release. Here is the complexity inventory:

| Category | Count | Concern |
|----------|-------|---------|
| Pipeline stages | 16 | 10 would deliver core value |
| Agent definitions | 8 | 4 would deliver core value |
| Pydantic models (agent output) | 7 + 4 dispatcher | 6 would suffice |
| Semantic validation rules | 37 | 10-15 would catch real bugs |
| Error types in taxonomy | 16 | 5-6 would cover 90% of cases |
| Config sub-models | 11 | 4-5 with sensible defaults |
| Quality gate profiles | 3 tiers | 1 ("standard") for v1 |
| CLI commands | 10+ | 5-6 for v1 |
| User documentation | 6 documents | 2 (quickstart + CLI reference) |

The spec is designed as if it will be built once and never revised. In practice, half of the design decisions will need adjustment after the first 10 real pipeline runs. Over-specifying now means over-building now, which means delayed learning.

### What "done" looks like for the current v1

The implementation roadmap estimates 9+ weeks across 5 phases. With the "missing components" additions (docs 13-17), the total estimated new code is ~4,200 lines on top of the core dispatcher. This does not include the 120-180 unit tests, 30-50 integration tests, and 9-15 E2E tests specified in doc 16. A realistic timeline for the full spec is 12-16 weeks for a 2-3 person team.

---

## 3. SCOPE RECOMMENDATIONS: What to Cut for v1

### v1-core (ship in weeks 1-5)

**Keep:**
- Stages 1-5: Intent capture, planning, plan review, plan fix, plan approval (the planning loop is the core value)
- Stages 6-9: Task breakdown, task review, task fix, prioritization (DAG ordering)
- Stage 11: Task execution (sequential, on feature branch)
- Stage 12-13: Per-task quality loop (test + review, max 3 iterations)
- Stage 16: Completion (push branch, create PR)
- Agents: Planner (Opus), Executor (Sonnet), Reviewer (Opus), Tester (Sonnet) -- 4 agents
- CLI: `xpatcher start`, `xpatcher resume`, `xpatcher status`, `xpatcher cancel`
- TUI: Basic progress display with elapsed timers (no log streaming)
- Signal handling: Ctrl+C graceful shutdown
- Pydantic validation: Schema validation only (Stage 1-2 of the validation pipeline). Skip the 37 semantic validation rules.
- Config: `config.yaml` with model assignments and iteration caps. No `.xpatcher.yaml` project overrides. No 11-sub-model `XpatcherConfig`.
- Documentation: Quickstart guide only

**Cut from v1-core (move to v1.1 or v2):**

| Feature | Rationale for deferral |
|---------|----------------------|
| Stage 14: Gap detection | Valuable but not essential for first value delivery. Gaps are caught by review already. |
| Stage 15: Documentation generation | Nice-to-have. Users can update docs themselves. |
| Simplifier agent | Optional even in the current spec (`autoSimplify: false` by default). |
| Gap Detector agent | Goes with Stage 14. |
| Technical Writer agent | Goes with Stage 15. |
| Explorer agent | Convenience, not pipeline-critical. Users have Claude Code already. |
| Expert panel | The consolidated review flagged this as over-engineering. A single planner with the Critical Thinking Protocol is sufficient for v1. |
| 37 semantic validation rules | Schema validation catches structural errors. Semantic validation (file exists, commit hash valid) is defense-in-depth that can wait. |
| 16-type error taxonomy | Start with 3 categories: retry, escalate, abort. Refine based on what errors actually occur. |
| 11-sub-model XpatcherConfig | Start with a flat config dict. Formalize the model after the config surface stabilizes. |
| 3-layer config resolution | CLI flags + global `config.yaml` only. No `.xpatcher.yaml` per-project overrides until users ask for them. |
| Tiered quality profiles (lite/standard/thorough) | Use "standard" for everything in v1. |
| Collusion prevention metrics | Log approval rates, but skip the alerting thresholds. |
| Mutation testing gate | Already marked optional. |
| LLM test auditor | Overlaps with reviewer. |
| Session lineage tracking | Fresh sessions with context bridges are simpler. |
| `xpatcher skip` | Users can `xpatcher cancel` and restart. Skip is a usability improvement, not a must-have. |
| `xpatcher pending` | Single-feature-at-a-time means only one pipeline can be pending. Just check `xpatcher status`. |
| `xpatcher logs` (structured query) | Users can `cat` the JSONL files. Structured query is Phase 4-5. |
| `xpatcher rollback` | Users can `git revert` manually. |
| `xpatcher upgrade` / `xpatcher uninstall` | Manual process is fine for early adopters. |
| `xpatcher doctor` | The preflight check covers the critical path. |
| Gap re-entry protocol (recursive) | Complex. If gap detection is deferred, so is re-entry. |
| Cancellation workflow (7-step cleanup) | `Ctrl+C` + graceful shutdown handles 95% of cases. Formal cancellation with agent termination and git cleanup can wait. |
| PostToolUse audit hooks | Logs capture tool calls already via `--output-format stream-json`. |
| Lifecycle hooks | Nice for debugging, not essential. |

### v1.1 (weeks 6-8, informed by v1 usage)

- Gap detection (Stage 14) + gap detector agent
- `xpatcher skip` with DAG restructuring
- `xpatcher logs` with structured query
- Agent log streaming in TUI (`--verbose`)
- `.xpatcher.yaml` project-level config overrides
- CLI reference documentation
- Semantic validation (the rules that prove useful based on real failures)

### v2 (weeks 9+, informed by v1.1 usage)

- Expert panel for planning
- Documentation generation (Stage 15) + tech writer agent
- Simplifier agent
- Parallel execution with worktrees
- Rollback command
- Upgrade/uninstall mechanism
- Mutation testing
- Tiered quality profiles
- Cost tracking and budget enforcement
- CI/CD headless mode

---

## 4. USER EXPERIENCE CONCERNS

### 4.1 The happy path is well-designed

The journey from `xpatcher start "Add OAuth2 support"` through plan approval to the completion summary box is genuinely well thought out. The TUI mockups are clear, the plan approval prompt has good structure, and the completion output gives the user everything they need. If the pipeline succeeds, the UX is strong.

### 4.2 The plan approval gate is the highest-risk UX moment

This is where xpatcher either earns trust or loses the user. The user sees a plan for the first time and must decide: approve, reject, or defer. The current design shows:

```
[1] Approve and begin execution
[2] Request changes (opens editor for feedback)
[3] Reject and restart planning
[4] View full plan details
```

Problems:
- **Option [4] behavior is unspecified.** Does it print YAML? Open an editor? Render a summary? A user who does not know how to read raw YAML (most users) is stuck.
- **No estimated cost or time.** The user approves a plan that might take 10 minutes and cost $2, or 2 hours and cost $30. They have no way to know.
- **No plan diff for v2+ plans.** If this is plan-v2 (after a review iteration), the user cannot see what changed from v1 without manually diffing files.
- **No partial approval.** If 11 of 12 tasks look right but 1 is wrong, the user must reject the entire plan.

Recommendation for v1: Option [4] should print a human-readable summary (not raw YAML) showing: goal, number of tasks, files that will be modified, estimated complexity, and any open questions. Cost/time estimates can wait for v1.1 when there is calibration data.

### 4.3 Failure recovery requires too much knowledge

The failure output (Section 3.6) is well-structured, but the recovery path assumes the user understands:
- How to read YAML task files
- What "review oscillation" means
- How to inspect agent logs in JSONL format
- When to skip vs. retry vs. fix manually

For v1, failure recovery should be simpler:
1. Show what failed and why in plain language
2. Offer 3 options: retry the failed task, skip it and continue, or cancel the pipeline
3. If the user chooses retry, use a fresh session with the error context

### 4.4 The 16-stage pipeline is invisible complexity

Users do not need to know there are 16 stages. They need to know: "planning... executing... reviewing... done." The TUI should show 4-5 user-visible phases, not 16 internal stages. The internal stages are important for the dispatcher but confusing for the user. "Stage 9: Prioritization" and "Stage 10: Execution Graph" are internal bookkeeping that should happen silently.

### 4.5 Feature slug derivation is unspecified (minor issue m5 from UX review)

The UX review correctly flagged this. When a user runs `xpatcher start "Replace JWT auth with session-based auth"`, how does the system derive `auth-redesign` as the feature slug? This slug becomes the directory name, branch name, and human identifier for the entire pipeline. If it is wrong (too long, confusing, duplicates an existing slug), the user is stuck with it.

Recommendation: The dispatcher generates a candidate slug, displays it, and allows the user to override before proceeding.

---

## 5. HUMAN GATE DESIGN

### 5.1 Plan approval (Stage 5): Keep as hard gate

This is the right call. The plan is where xpatcher earns its value. Approving a bad plan wastes significant time and money. However, the 2-hour soft timeout (from the resolved UX issue) should be configurable down to 0 (no timeout) for users who want to step away and come back tomorrow.

### 5.2 Final completion (Stage 16): Make this a soft gate or remove it

The current design requires human approval at Stage 16 after the pipeline has already completed all work, pushed the branch, and optionally created a PR. What exactly is the user approving? The code is already on the branch. The PR is already created. The user can review the PR through normal git workflow.

Recommendation: Stage 16 should be informational ("here is what was done, here is the PR link") with no gate. The PR review IS the human gate for completion. Adding a gate before the PR review is redundant.

### 5.3 Task review soft gate (30 minutes): Remove for v1

The spec describes a 30-minute window where the user can optionally intervene in task reviews. In practice, a 30-minute window on each task in a 12-task pipeline means the user must be attentive for 6+ hours. Nobody will use this. For v1, task reviews should be fully automated. If the review finds issues, the fix iteration loop handles it. If the loop exhausts iterations, escalate.

### 5.4 Gap detection re-entry gates: Defer with gap detection

If gap detection is deferred to v1.1, so are its approval gates.

---

## 6. FAILURE EXPERIENCE

### 6.1 What is well-handled

- **Malformed agent output**: The 3-stage validation pipeline with same-session retry is well-designed. Saving debug files for post-hoc inspection is the right call.
- **Iteration cap exhaustion**: Moving stuck tasks to `tasks/todo/` and suggesting skip/retry/fix is reasonable.
- **Pipeline interruption**: The graceful/force Ctrl+C design with crash recovery is solid.

### 6.2 What is under-specified

- **API key not configured**: The most common first-run failure. The spec does not describe what happens when the first `claude -p` invocation fails because there is no API key. The error should say: "Claude Code API key not configured. Run `claude` to set up your account, then try again."
- **Rate limiting mid-pipeline**: If the API returns 429 during task 8 of 12, what happens? The error taxonomy (doc 13) classifies this as transient with exponential backoff, but the user should see: "API rate limit reached. Pausing for 60 seconds. Pipeline will resume automatically."
- **Disk space exhaustion**: Not addressed. Agent logs and YAML artifacts accumulate. For a 12-task pipeline with verbose logging, `.xpatcher/` could reach hundreds of megabytes.
- **Git state corruption**: If an agent leaves uncommitted changes and the pipeline crashes, what happens on resume? The spec says tasks in `RUNNING` are reset to `READY`, but does not specify whether the working tree is cleaned first.

### 6.3 Error message quality

The UX review correctly noted that error messages are exemplified for pipeline-level failures (the blocked/failed output boxes are excellent) but not for operational failures (installation, API, git, config). For v1, the 5 most common errors need clear messages: API key missing, rate limit, git not clean, tests not found, and agent timeout.

---

## 7. CONFIGURATION COMPLEXITY

The full spec describes 3 configuration layers (CLI > project > global) with 11 sub-models in `XpatcherConfig`:

- `ModelConfig` (10 model assignments)
- `ConcurrencyConfig`
- `IterationConfig`
- `QualityTiersConfig`
- `GateConfig`
- `TimeoutConfig`
- `SessionConfig`
- `PathsConfig`
- `ScopeCreepConfig`
- `SimplificationConfig`
- `RetryConfig`

**This is too much configuration for v1.** No user should need to think about `ScopeCreepConfig` or `SessionConfig` before running their first pipeline.

Recommendation for v1:
- A single `config.yaml` with 3 sections: `models` (which model for each agent), `iterations` (max retry counts), and `gates` (which are human-blocking).
- All other values use hardcoded sensible defaults.
- No `.xpatcher.yaml` project-level overrides.
- No `--config` flag.
- If a user needs to change something, they edit `~/xpatcher/config.yaml`. One file, one location.

Expand the configuration surface in v1.1 when users report actual needs.

---

## 8. DOCUMENTATION PLAN ASSESSMENT

The documentation plan (doc 16) specifies 6 documents for v1:

| Document | Priority | Assessment |
|----------|----------|------------|
| Quickstart Guide | P0 | Essential. Ship with v1. |
| CLI Reference | P0 | Essential. Can be auto-generated from command definitions. |
| Configuration Guide | P1 | Defer to v1.1. With simplified config, the quickstart guide covers it. |
| Pipeline Walkthrough | P1 | Defer to v1.1. Users learn by running, not reading. |
| Troubleshooting Guide | P1 | Defer to v1.1. Write this after collecting real failure modes. |
| Architecture Overview | P2 | Defer to v2. The design spec serves this purpose for now. |

The acceptance criteria for the Quickstart Guide are realistic and well-scoped: "A developer who has never used xpatcher can install it and run a pipeline from this guide alone, without referring to any other document."

Recommendation: Ship v1 with Quickstart Guide + CLI Reference only. Write the troubleshooting guide during v1.1 based on actual support questions.

---

## 9. COST MANAGEMENT: Deferred to v2 -- NOT Acceptable Without Mitigation

The spec defers cost budgets, token tracking, and cost estimates to v2 (Section 8.4). This is the most significant product risk in the current design.

**The problem:** A single pipeline run invokes multiple Opus and Sonnet sessions. Based on the model assignments:

| Agent | Model | Typical Invocations per Pipeline | Est. Cost per Invocation |
|-------|-------|----------------------------------|--------------------------|
| Planner | Opus 1M | 1-3 (intent + plan + fix) | $3-8 |
| Expert panel | Sonnet x 2-7 | 2-7 (if enabled) | $0.50-1 each |
| Executor | Sonnet | 6-12 (one per task) | $1-3 each |
| Reviewer | Opus | 6-12 (one per task) | $2-5 each |
| Tester | Sonnet | 6-12 (one per task) | $1-2 each |
| Gap Detector | Opus | 1-2 | $2-5 |
| Tech Writer | Sonnet | 1 | $0.50-1 |

**Conservative estimate for a 6-task feature:** $20-40 in API costs.
**Complex feature (12 tasks, review iterations, expert panel):** $50-100+.

A user who runs xpatcher on their first feature without understanding the cost implications could be shocked. The "no cost tracking in v1" decision means there is literally no visibility into spend.

**Minimum viable cost mitigation for v1 (not full tracking):**

1. **Show an estimate before plan approval.** At the plan approval gate, display: "Estimated API cost for this plan: $15-25 (based on 8 tasks, ~40 agent invocations)." This is a rough estimate based on task count x model cost, not precise tracking.
2. **Show running total in the completion summary.** The Claude CLI returns `usage` data in the JSON envelope (Section 7.7, `envelope.get("usage")`). Sum the input/output tokens across all invocations and display a cost estimate in the completion box.
3. **Add a `max_cost` config option.** Default to $50. If cumulative estimated cost exceeds this, pause the pipeline and ask the user to confirm.

This is not the full cost tracking system described in the over-engineering concerns. It is 3 simple additions that prevent bill shock.

---

## 10. COMPETITIVE POSITIONING

### The landscape

| Tool | Approach | Strengths | Weaknesses |
|------|----------|-----------|------------|
| **Claude Code** (raw) | Single conversation | Fast, flexible, no setup | No planning, no review isolation, no resumability |
| **Cursor / Windsurf** | IDE-integrated AI | Good UX, visual diff | Limited to editor context, no multi-file pipelines |
| **GitHub Copilot Workspace** | Planning + execution | GitHub integration, PR-native | Limited to GitHub, less control over agents |
| **Devin** | Fully autonomous agent | End-to-end autonomy | Expensive, opaque, trust issues |
| **SWE-agent / SWE-bench tools** | Academic/benchmark | Research-grade, configurable | Not production-oriented, requires setup |
| **xpatcher** | Pipeline orchestration over Claude Code | Planning gates, adversarial review, resumability, audit trail | Requires Claude Code, CLI-only, no IDE integration |

### xpatcher's unique advantage

xpatcher is positioned as the "trust layer" for AI-generated code. Its unique selling points:

1. **You approve the plan before any code is written.** No other tool does this as a first-class concept with structured review.
2. **Review is structurally independent from execution.** The reviewer literally cannot see the executor's reasoning.
3. **Everything is auditable.** YAML artifacts, JSONL logs, structured commit messages. You can reconstruct what happened and why.
4. **It uses YOUR existing Claude Code setup.** No new accounts, no new APIs, no vendor lock-in beyond Anthropic.

### Positioning risk

The biggest positioning risk is: **why not just use Claude Code with a good prompt?** A skilled user can tell Claude Code "first plan, then implement, then review" and get 70% of xpatcher's value. xpatcher's answer must be: "You get the remaining 30% -- structural review isolation, crash recovery, iteration caps, and an audit trail -- and you get it reliably, every time, without remembering to ask for it."

If xpatcher takes 12-16 weeks to build its full spec before anyone can use it, the "just use Claude Code with a prompt" approach wins on time-to-value.

---

## BUSINESS RISKS

### Risk 1: Claude Code CLI dependency (HIGH)

xpatcher's entire architecture depends on `claude -p` (headless mode) with specific flags: `--agent`, `--resume`, `--output-format json`, `--plugin-dir`. The spec acknowledges (Open Questions OQ-1, OQ-2) that `--agent` and `--plugin-dir` may not exist yet. If Anthropic changes the CLI interface, deprecates headless mode, or removes the `--resume` flag, xpatcher breaks.

Mitigation: Build against the documented CLI interface. Maintain a compatibility test suite that runs against each new Claude Code release. Accept the platform risk -- it is the same risk any tool built on an SDK accepts.

### Risk 2: Cost surprise (HIGH)

Covered in Section 9 above. Without cost visibility, users will either overspend unknowingly or abandon the tool after one expensive pipeline.

### Risk 3: Time to first value (MEDIUM)

The current spec takes 9+ weeks to reach "Phase 5: Packaging and Distribution." A user cannot even install xpatcher until Phase 5. This is backwards. Phase 1 should produce something a user can run, even if it is rough.

Mitigation: The reduced scope recommended above gets to a usable tool in 4-5 weeks.

### Risk 4: Agent output reliability (MEDIUM)

The entire pipeline depends on agents producing valid YAML output. The malformed output recovery mechanism (same-session retry, 2 attempts) is well-designed, but the fundamental question is: how often do Claude models produce well-structured YAML output on the first attempt? If the failure rate is >20%, the pipeline will feel sluggish and unreliable.

Mitigation: Empirical data needed. Run 20 pipeline invocations on sample projects during Phase 2 and measure first-attempt YAML validity rates. If below 80%, consider switching to JSON output (more reliably structured) or using structured output mode if/when Claude supports it.

### Risk 5: Review quality variance (MEDIUM)

The adversarial review design is structurally sound, but the reviewer's actual catch rate depends on prompt quality. If the reviewer rubber-stamps everything (first-pass approval >80%), the review stage adds cost without value. If the reviewer is too aggressive (first-pass approval <40%), every task takes 3 iterations.

Mitigation: Track first-pass approval rate from the first real pipeline run. Tune prompts based on data. The spec's KPI target of 60% first-pass approval is reasonable but needs validation.

---

## QUESTIONS FOR THE ENGINEERING TEAM

### Architecture

1. **Has anyone tested `claude -p --agent <name>` with a plugin-defined agent?** The spec depends on this working but lists it as an open question (OQ-1). If this flag does not exist, what is the fallback? Injecting the agent prompt into the `-p` prompt is a significant degradation in separation of concerns.

2. **What is the measured YAML output validity rate for Claude models?** Run 20 test invocations with the planner and executor prompts against a sample project. Measure: (a) first-attempt valid YAML rate, (b) YAML-that-also-passes-schema-validation rate, (c) average fix attempts needed. This data determines whether the 2-attempt retry budget is sufficient.

3. **How large is a typical `.xpatcher/` directory after a 12-task pipeline?** Measure: YAML artifacts, JSONL logs, debug files. Is there a retention/cleanup concern?

### Implementation

4. **Can the implementation roadmap be restructured so that a user can run a pipeline (even a basic one) by the end of Phase 2?** The current Phase 1 builds the foundation; Phase 2 builds the core pipeline; but nothing is runnable until Phase 4-5 when the TUI and packaging are done. A basic `xpatcher start` that works but has no TUI (just prints to stdout) would be valuable earlier.

5. **What is the plan for testing xpatcher itself during development?** Doc 16 specifies 120-180 unit tests, 30-50 integration tests, and 9-15 E2E tests. Are these written alongside the implementation or afterward? Writing tests after the fact is a known anti-pattern.

6. **Is there an alternative to file polling for agent completion?** The spec chose file polling (2-second interval) over WebSocket/IPC. Has anyone measured the latency impact? For a 12-task pipeline with 3 iterations each, that is 36+ poll cycles at 2-second intervals = 72 seconds of pure waiting. The `subprocess.run()` approach in `ClaudeSession.invoke()` already blocks until completion -- is the file polling for inter-agent coordination only?

### Product

7. **Who are the first 3 users (besides Greg)?** Having specific users with specific projects and specific feature requests to test against will ground the design in reality. "Sample projects" in E2E tests are useful but do not replace real usage.

8. **What is the success metric for v1?** Suggested: "A user can run xpatcher against their own project, approve a plan, and get a PR with working code in under 1 hour, with no manual intervention beyond plan approval, for a medium-complexity feature (4-8 tasks)."

9. **Is there a plan for collecting feedback from early users?** The spec is thorough but entirely forward-looking. There should be a mechanism for users to report what worked, what did not, and what they wish existed. Even a simple `xpatcher feedback` command that opens a GitHub issue template would suffice.

---

## SUMMARY

| Dimension | Assessment |
|-----------|------------|
| Value proposition | Strong, but needs clearer articulation for users |
| Architecture | Excellent. File-based coordination, adversarial review, iteration caps are all sound. |
| v1 scope | Too large. Cut to core planning + execution + review loop. |
| User experience | Happy path is good. Failure recovery and plan approval need work. |
| Human gates | Plan approval: keep. Completion: remove (PR is the gate). Task review soft gate: remove. |
| Cost visibility | Unacceptable to ship with zero cost awareness. Add estimate + running total. |
| Configuration | Too complex. Start with 1 file, 3 sections, sensible defaults. |
| Documentation | Quickstart + CLI reference for v1. The rest after real usage. |
| Competitive position | Strong differentiators (planning gates, review isolation, auditability). Time-to-value is the risk. |
| Over-engineering | The consolidated review identified 6 concerns. The expert panel and semantic validation were partially addressed (panel uses team mode now, validation rules are defined). But the sheer volume of specification is itself an over-engineering signal. |

**The path forward:** Cut scope aggressively, ship in 4-5 weeks, collect real usage data, expand based on evidence. The architecture supports this -- sequential execution, no worktrees, no parallel agents, no expert panel, no gap detection means the core pipeline is straightforward to implement. Everything deferred is additive, not architectural -- it can be added later without rework.

---

*End of Product Owner review.*

# xpatcher Design Specification -- Product Manager Review

**Date:** 2026-03-29
**Reviewer:** Product Manager
**Spec Version:** 1.2 (Final Draft, 2026-03-28) + Docs 13-17 (2026-03-29)
**Documents Reviewed:** All 17 design documents + consolidated expert review

---

## VERDICT: Needs Refinement

The architecture is sound and the spec is unusually thorough. The expert review panel did excellent work resolving 7 critical issues, 14 major issues, 11 inconsistencies, and 16 missing components. The design is implementable.

However, the project as specified is **too large for the proposed timeline and team.** The 5-phase / 9-week roadmap underestimates integration risk, prompt engineering iteration, and the compounding cost of building 8 agents, 16 pipeline stages, a full TUI, and operational tooling simultaneously. The spec conflates "v1" with "feature-complete" -- it tries to ship everything except parallel execution and cost tracking.

**The path forward is to cut scope aggressively for a true MVP, prove the core loop works on real codebases, then expand.** Below is a concrete plan.

---

## 1. ROADMAP FEASIBILITY

### 1.1 Current Plan Assessment

| Phase | Scope | Weeks | Deliverables | Feasibility |
|-------|-------|-------|--------------|-------------|
| 1: Foundation | Plugin loading, session.py, state.py, schemas.py | 2 | 6 | Feasible. Low risk. |
| 2: Core Pipeline | Planner, executor, reviewer, context builder, plan-execute-review loop | 2 | 6 | High risk. Prompt engineering alone could consume 2 weeks. |
| 3: Quality Gates | Tester, simplifier, gap detector, tech writer, hooks, per-task quality loop, doc stage | 2 | 8 | Too much. 4 new agents + hooks + quality loop integration in 2 weeks is unrealistic. |
| 4: TUI, Logging, Polish | Rich TUI, log streaming, parallel.py, worktree management, retry.py, resume, DAG scheduling, cost tracking | 2 | 12 | Overloaded. This phase alone is 4-6 weeks of work. Worktree management is v2 but is listed here. |
| 5: Packaging | install.sh, auto-detection, config overrides, pipeline skill, log command, integration tests, pip packaging, user docs | 1+ | 8 | Compressed. Integration tests and user docs take time. |

**Total: 40 deliverables across 9 weeks with 2-3 engineers.**

### 1.2 Realistic Estimate

Based on the actual scope of each deliverable (including prompt iteration, integration testing, and debugging):

| Phase | Realistic Duration | Notes |
|-------|-------------------|-------|
| 1: Foundation | 2 weeks | On track. This is straightforward Python scaffolding. |
| 2: Core Pipeline | 4-5 weeks | Prompt engineering is empirical. Each agent needs 5-10 iterations to produce reliable YAML output. The plan-execute-review loop has subtle integration points. |
| 3: Quality Gates | 3-4 weeks | The tester and gap detector are non-trivial. Simplification and documentation are lower risk but still need prompt work. |
| 4: TUI + Polish | 3-4 weeks | Rich TUI is a full sub-project. Log streaming with async subprocess management is tricky. |
| 5: Packaging + Docs | 2-3 weeks | Integration testing on real codebases always surfaces surprises. |

**Realistic total: 14-18 weeks (3.5-4.5 months), not 9 weeks.**

### 1.3 Phases With Highest Slippage Risk

1. **Phase 2 (Core Pipeline):** The planner agent is the most unpredictable deliverable in the entire project. Getting an LLM to reliably produce valid, well-decomposed YAML plans with correct dependency graphs is hard. The expert panel adds complexity. First-time prompt engineering efforts routinely take 3x the optimistic estimate.

2. **Phase 4 (TUI + Polish):** This is a grab-bag of unrelated features (TUI, logging, retry, resume, DAG, cost tracking). It will expand because each feature interacts with the others in ways that only become apparent during integration.

3. **Phase 3 (Quality Gates):** Four new agents in two weeks is one agent per 2.5 working days. Each agent needs prompt writing, output schema validation, integration with the dispatcher, and testing against real code.

---

## 2. DEPENDENCY MAPPING

### 2.1 Current Phase Dependencies

```
Phase 1 (Foundation)
    |
    v
Phase 2 (Core Pipeline) --- depends on Phase 1
    |
    v
Phase 3 (Quality Gates) --- depends on Phase 2
    |
    v
Phase 4 (TUI + Polish) --- depends on Phase 3 partially
    |
    v
Phase 5 (Packaging) --- depends on Phase 4
```

### 2.2 Analysis

The phases are strictly sequential, which is both a strength (clear ordering) and a weakness (no parallelism possible, critical path = total path).

### 2.3 Parallelization Opportunities

| Work Item | Can Start In | Currently Blocked Until |
|-----------|-------------|------------------------|
| install.sh + CLI wrapper | Day 1 | Phase 5 |
| Pydantic schemas (all 11 models) | Day 1 | Phase 1 (Step 6) |
| PreToolUse hook | Phase 1 | Phase 3 |
| TUI prototype (progress panel, no real data) | Phase 1 | Phase 4 |
| Agent prompts (planner, executor, reviewer) | Phase 1 | Phase 2 |
| Tester, simplifier, gap-detector, tech-writer prompts | Phase 2 | Phase 3 |
| User documentation (quickstart draft) | Phase 2 | Phase 5 |
| Integration test harness + sample projects | Phase 2 | Phase 5 |

**Recommendation:** If the team has 2 engineers, split as follows:
- **Engineer A (Backend):** Dispatcher core, state machine, session management, DAG, main dispatch loop
- **Engineer B (Integration):** Schemas, agent prompts, hooks, artifact validation, installer, config

This allows Phase 1 and parts of Phase 2/3 to proceed in parallel.

---

## 3. MVP DEFINITION

### 3.1 The Absolute Minimum Viable Product

The core value proposition of xpatcher is: **"Describe a feature, get a working implementation with tests on a feature branch."** Everything else is optimization.

**MVP scope (4-6 weeks with 2 engineers):**

| Include | Exclude (Defer to v1.1+) |
|---------|--------------------------|
| Intent capture (Stage 1) | Expert panel (use solo planner) |
| Planning with solo planner (Stage 2) | Simplifier agent |
| Plan review + fix loop (Stages 3-4) | Gap detector |
| Plan approval gate (Stage 5) | Technical writer |
| Task breakdown (Stage 6) | TUI (use simple print-based progress) |
| Task execution -- sequential (Stage 11) | Log streaming |
| Basic testing via executor (not separate tester agent) | Tiered quality profiles |
| Basic review (Stage 12) | Oscillation detection |
| Fix iteration (Stage 13) | Rollback command |
| Pipeline completion (Stage 16) | Upgrade/uninstall commands |
| CLI: start, resume, status, cancel | CLI: skip, pending, logs, doctor |
| Pydantic validation (schema only, defer semantic) | Semantic validation (37 rules) |
| File-based state + crash recovery | Cost tracking |
| Git branch creation + task commits | Worktrees / parallel execution |
| config.yaml (basic model + timeout config) | Full 11-sub-model config system |
| PreToolUse hook (read-only enforcement) | PostToolUse + lifecycle hooks |
| install.sh | pip packaging |

### 3.2 What This MVP Delivers

A user can:
1. Run `xpatcher start "Add OAuth2 support"` from their project directory
2. Get a structured plan, review it, approve it
3. Watch tasks execute sequentially with basic terminal output
4. Get a feature branch with commits, basic review, and test results
5. Resume after interruption

What they cannot do yet: get simplification, gap detection, documentation updates, rich TUI, parallel execution, or operational tooling.

### 3.3 Why This Cut Works

The simplifier, gap detector, and tech writer are **post-processing stages**. They add quality but are not load-bearing. A developer can simplify, detect gaps, and write docs themselves from the feature branch output. The core loop (plan -> execute -> test -> review -> fix) is what creates value.

The TUI is a presentation layer. Simple `print()` statements with color codes and elapsed time deliver 80% of the user experience at 10% of the implementation cost.

---

## 4. RISK/REWARD PER FEATURE

| Feature | Effort (engineer-weeks) | Value | Risk | Verdict |
|---------|------------------------|-------|------|---------|
| **Core pipeline (plan-execute-review)** | 6-8 | Critical -- the product | High (prompt reliability) | Must build. Is the product. |
| **Expert panel** | 2-3 | Medium -- better plans | Medium (subagent coordination) | Defer. Solo planner with multi-perspective checklist prompt covers 80%. |
| **Simplifier** | 1-2 | Low-Medium -- cleaner code | Low (uses native /simplify) | Defer. Nice-to-have. Developer can /simplify manually. |
| **Gap detector** | 2-3 | Medium -- catches misses | Medium (cross-cutting analysis is hard for LLMs) | Defer to v1.1. Can be done manually by reviewing the PR. |
| **Tech writer** | 1-2 | Low -- docs updates | Low | Defer. Developer writes docs. |
| **TUI (Rich-based)** | 3-4 | Medium -- user experience | Low-Medium (UI work is predictable) | Defer to v1.1. Use print-based progress for MVP. |
| **Rollback command** | 1-2 | Medium -- safety net | Low | Defer. `git revert` works manually. |
| **Tester (separate agent)** | 2-3 | High -- test quality | Medium (test generation reliability) | Include in MVP as part of quality loop, but can be merged with executor's testing for MVP. |
| **Per-task quality loop** | 2-3 | High -- core quality mechanism | Medium | Include. The test-review-fix loop is what makes xpatcher output reliable. |
| **Semantic validation (37 rules)** | 2-3 | Medium -- correctness | Low | Defer. Schema validation catches most issues. Semantic validation is v1.1 polish. |
| **DAG scheduler** | 1-2 | Medium -- correct ordering | Low | Include in MVP (sequential execution respects DAG order). |
| **Signal handling** | 0.5-1 | High -- reliability | Low | Include. Without Ctrl+C handling, users lose state. |
| **Config system (full)** | 1-2 | Low -- flexibility | Low | Defer full 11-sub-model system. Implement flat config.yaml with essential keys. |

---

## 5. TECHNICAL DEBT ANALYSIS

### 5.1 Sequential -> Parallel: Is It Really Additive?

The spec claims (Section 2.6.1): "Parallel execution is additive -- the architecture supports it without a rewrite."

**This is partially true but understates the integration cost.** The things that change:

| Component | Sequential (v1) | Parallel (v2) | Migration Cost |
|-----------|-----------------|---------------|----------------|
| DAG scheduler | Returns tasks in order; run one at a time | Must return batches; manage semaphore | Low (design already supports batches) |
| State management | Single writer, no contention | Multiple writers, needs locking | Low (locking already implemented) |
| Git strategy | Direct commits to feature branch | Worktrees + merge protocol | **High** -- merge conflict resolution, integration testing, worktree lifecycle |
| TUI | Single active task | Multiple active tasks, tab switching | Medium -- UI rework for multi-task view |
| Session management | One session at a time | Multiple concurrent sessions | Medium -- session isolation, resource management |
| Pipeline state | Simple linear progression | Batch-level tracking, partial completion | Medium |
| Error handling | One failure path | Multiple simultaneous failures, cascading blocks | **High** -- need to handle partial batch failures |

**Verdict:** The dispatcher core (state machine, DAG, schemas) transitions cleanly. Git worktree management and multi-failure handling are substantial new work -- probably 4-6 weeks for a reliable implementation. This is not a rewrite, but it is not a weekend project either.

### 5.2 Other Debt Items

| v1 Decision | v2 Cost | Severity |
|-------------|---------|----------|
| No cost tracking | Adding token counting, budget enforcement, cost estimation retroactively means instrumenting every agent invocation | Medium |
| Print-based progress (MVP) -> Rich TUI | If print statements are scattered through the codebase, migrating to Rich panels requires a rendering abstraction | Low if planned (use a `Reporter` interface from day 1) |
| Schema validation only (no semantic) | Agent outputs that pass schema but are logically invalid (referencing nonexistent files) will slip through | Low -- add as a plugin to existing validation pipeline |
| Flat config -> full config system | Must migrate existing config references to nested structure | Low if the flat config uses the same key names |
| Solo planner -> expert panel | Adding subagent spawning to the planner prompt changes its behavior significantly; need re-tuning | Medium |

### 5.3 Recommendation

Introduce two abstractions in MVP to reduce future debt:
1. **`Reporter` interface** -- all user-facing output goes through this. MVP implementation: prints to stdout. v1.1: Rich TUI. v2: web dashboard.
2. **`AgentInvoker` interface** -- all Claude CLI invocations go through this. MVP implementation: subprocess.run. v2: connection pooling, concurrent sessions.

---

## 6. TEAM SIZING

### 6.1 Spec Claim: "2 engineers + 1 prompt engineer"

| Role | Assessment |
|------|-----------|
| Python Backend Engineer x2 | Realistic for the dispatcher core. But the spec lists 40 deliverables -- at 2 engineers, each person owns 20 deliverables. That is a very high cognitive load with many context switches. |
| Prompt Engineer x1 | Underestimated. Prompt engineering for 8 agents with structured YAML output is a full-time job for 4-6 weeks. This person needs to: write prompts, test them against real codebases, iterate on output format reliability, tune model selection, validate adversarial framing, and debug edge cases. |
| QA (part-time) | Underestimated for full spec. Adequate for MVP. |
| Tech Writer (part-time) | Adequate if docs are truly Phase 5. |

### 6.2 Realistic Team for Full Spec (14-18 weeks)

- 2 backend engineers (full-time)
- 1 prompt engineer / AI specialist (full-time for 8 weeks, then part-time)
- 1 QA / test engineer (half-time, weeks 6-18)
- Total: 3-3.5 FTE

### 6.3 Realistic Team for MVP (4-6 weeks)

- 1 backend engineer (full-time) -- dispatcher, state, CLI
- 1 integration engineer who is also the prompt engineer (full-time) -- agents, schemas, hooks, prompt tuning
- Total: 2 FTE

The 2-person MVP team works because the scope cut removes 4 agents (simplifier, gap detector, tech writer, expert panel subagents), the TUI, and all operational tooling. What remains is tractable.

---

## 7. SUCCESS METRICS (KPIs from Appendix B)

### 7.1 Measurability Assessment

| Metric | Target | Measurable Day 1? | Instrumentation Needed |
|--------|--------|--------------------|------------------------|
| Task success rate (no human intervention) | >85% | No | Counter in pipeline-state.yaml: tasks completed vs total. Easy. |
| First-pass review approval rate | >60% | No | Counter per task: was first review verdict "approve"? Must be logged. |
| Average iterations per task | <2.5 | Yes | Already tracked in pipeline-state.yaml iterations field. |
| Cost per task | Project-specific | No | Token counting per agent invocation. Deferred to v2. Not measurable day 1. |
| Pipeline throughput (tasks/hour) | Project-specific | Yes | Timestamps already in pipeline-state.yaml. |
| Integration success rate (parallel merges) | >90% | N/A for v1 | v2 only. |
| Human gate latency | <4 hours | Yes | Timestamps on gate arrival + approval. Already specified. |
| Regression rate (per completed task) | <5% | Partially | Need to log regression failures distinctly from other test failures. |
| Mutation test kill rate | >70% | No | Mutation testing is optional/deferred. Not measurable without external tooling. |
| Agent context utilization | <70% | No | Requires token counting from Claude CLI output. The `usage` field in the JSON envelope may provide this. Needs validation. |

### 7.2 Day 1 Metrics Recommendation

For MVP, track only these 4 metrics (all derivable from pipeline-state.yaml without additional instrumentation):

1. **Task success rate**: completed tasks / total tasks (excluding skipped)
2. **Average iterations per task**: mean of per-task iteration counts
3. **Pipeline wall-clock time**: total elapsed from start to completion
4. **Human gate wait time**: time spent in `waiting_for_human` state

Add review approval rate and token usage in v1.1 when the tooling matures.

---

## 8. GO-TO-MARKET

### 8.1 Launch Strategy

**Phase 1: Internal dogfooding (weeks 1-6 of MVP)**
- Build xpatcher using xpatcher (self-hosting) as soon as core pipeline works
- Target: the xpatcher codebase itself + 2-3 internal Extractum projects
- Goal: find the failure modes before anyone else does

**Phase 2: Private alpha (weeks 6-10)**
- Invite 5-10 developers who:
  - Work on Python or TypeScript projects (best-supported stacks)
  - Have medium-complexity feature requests (not trivial, not architectural rewrites)
  - Are willing to file detailed bug reports
- Provide: Quickstart guide, CLI reference, direct Slack channel for support
- Collect: pipeline completion rate, time savings vs manual, failure reasons, user feedback

**Phase 3: Public beta / open source (week 12+)**
- Open the repository
- Publish: Quickstart, CLI Reference, Configuration Guide, Architecture Overview
- Blog post: "How we automated SDLC with Claude Code"
- Target: Claude Code power users, AI-assisted development community

### 8.2 Onboarding the First 10 Users

| Step | Action | Goal |
|------|--------|------|
| 1 | Screen candidates: must have a real feature request, not a toy project | Avoid "tire kickers" who will not give useful feedback |
| 2 | Pair-install: do the first install together (30 min call) | Catch install friction, build relationship |
| 3 | First pipeline together: run their first `xpatcher start` while screen-sharing | Observe where they get confused, what the plan approval UX feels like |
| 4 | Solo pipeline: they run one independently | Measure success rate without hand-holding |
| 5 | Debrief interview (30 min) | What worked, what failed, what confused them, would they use it again |
| 6 | Iterate on prompts/UX based on feedback | Close the loop before the next user |

### 8.3 Internal Tool vs. Open Source

**Start as an internal tool, open-source when stable.** Reasons:
- LLM-dependent tools have unpredictable failure modes that embarrass you publicly
- Prompt tuning requires real-world iteration that is easier with known users
- The Claude CLI dependency means the user base is already self-selected (Claude Code users)
- Open-sourcing too early means supporting issues you have not encountered yet

**Open-source trigger:** When 5 internal/alpha users have completed 10+ pipelines each with >80% task success rate.

---

## 9. DRY-RUN: First 2 Weeks of Development

### 9.1 Day 1 Briefing for 2 Engineers

"We are building xpatcher, an SDLC automation pipeline. We have a detailed spec but we are building the MVP first. The MVP is: plan a feature, execute tasks, review code, fix issues, produce a feature branch. No TUI, no simplifier, no gap detector, no parallel execution.

Week 1: Foundation. Week 2: First end-to-end pipeline run on a sample project."

### 9.2 Week 1 Plan

| Day | Engineer A (Backend) | Engineer B (Integration) |
|-----|---------------------|--------------------------|
| Mon | Set up repo structure, pyproject.toml, venv, CI | Write all Pydantic schemas (PlanOutput, ExecutionOutput, ReviewOutput, TestOutput, ArtifactBase). Write PipelineStateModel. |
| Tue | Implement PipelineStateFile (atomic write, locking). Implement PipelineStage enum + TaskState enum with transition validation. | Write planner.md agent prompt. Test it manually with `claude -p` against a sample project. Iterate on YAML output format. |
| Wed | Implement ClaudeSession (invoke, _extract_yaml, parse envelope). Implement ArtifactValidator (Stages 1-2 only). | Continue planner prompt iteration. Start executor.md prompt. Test executor against a trivial task. |
| Thu | Implement core dispatch loop skeleton: init pipeline, run stage, transition, persist state. | Write reviewer.md prompt. Test reviewer against executor output. Write PreToolUse hook (read-only enforcement). |
| Fri | Implement IntentCaptureStage. Wire intent -> planning transition. First `xpatcher start` that produces intent.yaml. | Write install.sh. Create bin/xpatcher wrapper. Create plugin.json, settings.json. First `xpatcher` command that loads the plugin. |

### 9.3 Week 2 Plan

| Day | Engineer A (Backend) | Engineer B (Integration) |
|-----|---------------------|--------------------------|
| Mon | Implement planning stage (invoke planner, validate output, write plan-v1.yaml). Implement plan review stage (invoke reviewer, validate output). | Test plan output quality on 3 different sample projects. Iterate planner prompt. Start tuning reviewer to produce actionable findings. |
| Tue | Implement plan review-fix loop with iteration cap. Implement plan approval gate (blocking prompt in terminal). | Continue prompt iteration. Write config.yaml with model assignments and iteration caps. |
| Wed | Implement task breakdown stage (planner produces tasks). Implement task file management (todo/in-progress/done folders). | Write tester.md prompt (basic). Test it manually. Integrate tester into quality check flow. |
| Thu | Implement task execution stage (invoke executor per task). Implement per-task quality loop (test -> review -> fix). Wire git branch creation + task commits. | End-to-end test: run full pipeline on sample Python project. Debug failures. |
| Fri | Implement pipeline completion (summary output, git push, optional PR creation). Basic signal handling (Ctrl+C saves state). | End-to-end test: second sample project (TypeScript). Fix prompt issues. Document known limitations. |

### 9.4 Predicted First Blockers

| Blocker | When | Impact | Mitigation |
|---------|------|--------|------------|
| **Planner YAML output is unreliable** | Day 2-3 | Plan validation fails 40-60% of the time initially. Planner wraps YAML in markdown code blocks, adds prose, uses wrong field names. | ArtifactValidator's multi-strategy extraction (already specified). Aggressive prompt iteration. Add "do NOT wrap in code blocks" instruction 3 times. |
| **`--agent` flag may not exist in Claude CLI** | Day 1 | Cannot select agent per invocation. | Fallback: inject agent system prompt via `-p` flag content. Less clean but functional. |
| **`--plugin-dir` flag may not exist** | Day 1 | Cannot point to core installation plugin directory. | Fallback: symlink `.claude-plugin/` into project directory before invocation. |
| **Executor modifies files outside task scope** | Day 4-5 | Executor changes files not in the task's file_scope. Review catches this but wastes an iteration. | Strengthen executor prompt: "ONLY modify files listed in the task. If you need to change other files, report it as a deviation." PreToolUse hook cannot enforce file scope (it does not know the task). |
| **Review-fix loop oscillation** | Week 2 | Reviewer finds issue A, executor fixes A but introduces B, reviewer finds B, executor fixes B but reintroduces A. | Oscillation detection (hash set of finding IDs). Already specified. Must be implemented early. |
| **Executor does not commit** | Day 4-5 | Executor makes changes but forgets to `git commit`. The spec says commit is part of the executor's checklist, but LLMs skip steps. | Post-execution check in dispatcher: if `git status` shows uncommitted changes, prompt the executor to commit in a follow-up turn. |

---

## 10. MILESTONE DEFINITIONS

### 10.1 Milestone: "Phase 1 Done"

**Definition:** The foundation is proven and a human can invoke agents through the dispatcher.

**Concrete checkpoints:**

- [ ] `xpatcher start "test feature"` creates `.xpatcher/<feature>/` directory, writes `pipeline-state.yaml` with stage `intent_capture`, writes `intent.yaml`
- [ ] `xpatcher status` reads pipeline-state.yaml and prints current stage, elapsed time, and task counts
- [ ] `xpatcher resume <id>` reads state and continues from the saved stage
- [ ] `ClaudeSession.invoke()` successfully calls `claude -p` with a prompt, receives JSON envelope, and extracts the agent's text output
- [ ] `ArtifactValidator.validate()` correctly accepts valid PlanOutput YAML and rejects invalid YAML with specific error messages
- [ ] `PipelineStateFile` handles concurrent reads (status query during write) without corruption
- [ ] Pipeline state survives `kill -9` of the dispatcher process and is resumable
- [ ] All Pydantic models for MVP artifacts (plan, execution, review, test, pipeline-state) pass unit tests with edge cases

**Not required for Phase 1 Done:**
- Agents do not need to produce high-quality output yet (that is Phase 2 prompt tuning)
- TUI does not need to look good (print statements are fine)
- No git operations needed yet

### 10.2 Milestone: "v1 (MVP) Ready"

**Definition:** A developer can use xpatcher to implement a real feature on a real codebase and get a useful feature branch.

**Concrete checkpoints:**

- [ ] **End-to-end on Python project:** `xpatcher start "Add input validation to user registration endpoint"` on a Flask/FastAPI app produces a feature branch with working code, passing tests, and review approval -- without human intervention after plan approval
- [ ] **End-to-end on TypeScript project:** Same test on an Express/Next.js app
- [ ] **End-to-end on minimal project:** Same test on a project with <10 files
- [ ] **Task success rate:** >70% of tasks complete without human intervention across 10 pipeline runs (lower bar than KPI target of 85% -- this is MVP)
- [ ] **Average iterations:** <3.0 per task across 10 runs
- [ ] **Crash recovery:** Kill the dispatcher mid-execution, resume, pipeline completes successfully
- [ ] **Plan approval gate:** Human can view plan, request changes, see revised plan, approve -- full plan review cycle works
- [ ] **Stuck task handling:** When a task hits max iterations, it is marked STUCK, the pipeline continues with other tasks, and the completion summary reports which tasks are stuck and why
- [ ] **install.sh works on macOS and Ubuntu 22.04** from a clean environment with Python 3.10+ and Claude CLI installed
- [ ] **Quickstart guide exists** and a new user can follow it to completion in <10 minutes (excluding pipeline runtime)

**Not required for v1 Ready:**
- Expert panel
- Simplifier, gap detector, tech writer
- Rich TUI (print-based progress is acceptable)
- Parallel execution
- Cost tracking
- Semantic validation
- Rollback, upgrade, uninstall commands
- pip packaging
- >85% task success rate (that is a tuning target, not a launch gate)

### 10.3 Milestone: "v1.1 Ready" (Post-MVP)

**Definition:** The quality and UX layers that make xpatcher feel polished.

**Concrete checkpoints:**

- [ ] Tester agent runs as a separate stage (not embedded in executor)
- [ ] Gap detector runs after all tasks complete and catches at least 1 real gap in 50% of pipeline runs
- [ ] Tech writer updates at least one doc file per pipeline run where doc changes are warranted
- [ ] Rich TUI with progress panel, elapsed times, and task-level detail
- [ ] Expert panel activates for complex features (>8 tasks) and produces measurably better plans (A/B test vs solo planner)
- [ ] Task success rate >80% across 20 pipeline runs
- [ ] Semantic validation catches at least 1 real error per 10 pipeline runs that schema validation missed
- [ ] `xpatcher skip`, `xpatcher pending`, `xpatcher logs` commands all functional

### 10.4 Milestone: "v2 Ready"

- [ ] Parallel execution within batches (git worktrees)
- [ ] Cost tracking and budget enforcement
- [ ] Simplifier agent as a post-processing stage
- [ ] Rollback, upgrade, uninstall commands
- [ ] pip packaging
- [ ] Agent context checkpointing for long tasks

---

## CRITICAL PATH ANALYSIS

The critical path for MVP is:

```
ClaudeSession.invoke()        [2 days]
    |
    v
Planner prompt + validation   [5 days -- longest single item, prompt iteration]
    |
    v
Plan review loop              [2 days]
    |
    v
Executor prompt + validation  [3 days]
    |
    v
Reviewer prompt               [2 days]
    |
    v
Quality loop integration      [3 days]
    |
    v
Task execution pipeline       [3 days]
    |
    v
End-to-end testing + fixes    [5 days]
    |
    v
Install + docs                [3 days]
                               --------
                               28 working days = ~5.5 weeks
```

With 2 engineers working in parallel (Backend on dispatcher, Integration on prompts/schemas), the wall-clock time compresses to **4-5 weeks** because prompt work and dispatcher work proceed concurrently after ClaudeSession is available.

**The single longest pole is planner prompt reliability.** If the planner cannot produce valid, well-structured YAML plans within 3 prompt iterations (including malformed output recovery), the entire downstream pipeline has nothing to work with.

---

## RISK REGISTER

| # | Risk | Likelihood | Impact | Mitigation | Owner |
|---|------|-----------|--------|------------|-------|
| R1 | Planner YAML output unreliable (>30% malformed) | High | Critical -- blocks entire pipeline | Multi-strategy YAML extraction. Aggressive prompt engineering. Consider using `--output-format structured` if Claude CLI supports it. | Prompt Engineer |
| R2 | Claude CLI `--agent` flag does not exist | Medium | High -- changes invocation model | Test on Day 1. Fallback: inject system prompt via `-p`. Document in spec as Open Question OQ-1. | Backend Engineer |
| R3 | Executor makes changes outside task scope | High | Medium -- wastes review iterations | Stronger prompt guardrails. PostToolUse hook logs all file writes. Dispatcher checks git diff against task file_scope. | Prompt Engineer |
| R4 | Review-fix oscillation on real codebases | Medium | Medium -- tasks get stuck | Implement oscillation detection (finding hash set) in Week 2. If oscillation detected, escalate with "change strategy" prompt. | Backend Engineer |
| R5 | API rate limits constrain throughput | Medium | Medium -- pipeline slows | Even MVP runs 3-5 agent invocations per task. For a 10-task pipeline, that is 30-50 API calls. Measure actual throughput in Week 2 and adjust. | Backend Engineer |
| R6 | Scope creep: team adds v1.1 features to MVP | High | High -- delays launch | This document defines the MVP cut. Do not add expert panel, TUI, gap detector, or semantic validation to MVP. Review scope weekly. | Product Manager |
| R7 | Claude model behavior changes between versions | Medium | High -- prompts break | Pin model IDs in config.yaml for production. Test prompts quarterly against new model releases. | Prompt Engineer |
| R8 | File-based state corruption on crash | Low | High -- pipeline unrecoverable | Atomic writes already specified (PipelineStateFile). Add checksum validation on read. | Backend Engineer |
| R9 | Prompt engineering takes 3x longer than estimated | Medium | High -- delays Phase 2 | Start prompt work in parallel with Phase 1 foundation. Do not block prompt iteration on dispatcher readiness. Use `claude -p` directly for early iteration. | Prompt Engineer |
| R10 | First 10 users have codebases that expose edge cases | High | Medium -- pipeline fails on real projects | Dogfood on 3+ internal projects first. Have a "known limitations" document. Prioritize fixes for the most common failure modes. | Product Manager |

---

## SUMMARY OF RECOMMENDATIONS

1. **Cut scope to MVP.** Ship plan-execute-review-fix on a feature branch. Defer simplifier, gap detector, tech writer, expert panel, TUI, and operational tooling.

2. **Extend timeline.** The 9-week full spec is unrealistic. Plan for 4-5 weeks to MVP, 10-12 weeks to v1.1 (with quality layers), 16-20 weeks to full spec.

3. **Start prompt engineering on Day 1.** Do not wait for the dispatcher to be ready. Use `claude -p` directly to iterate on planner, executor, and reviewer prompts against real codebases.

4. **Validate Claude CLI flags immediately.** `--agent` and `--plugin-dir` are load-bearing assumptions. If they do not exist, the invocation model changes significantly. Test this before writing any dispatcher code.

5. **Add a Reporter interface** from day 1 so the MVP's print-based progress can be swapped for Rich TUI without refactoring.

6. **Define "done" as a pipeline run, not a feature list.** The milestone is not "we wrote tester.md" -- it is "10 pipelines completed with >70% task success rate on real codebases."

7. **Do not open-source until 5 alpha users have 10+ successful pipelines each.** LLM-dependent tools fail in ways that are embarrassing and hard to reproduce.

---

*End of Product Manager review.*

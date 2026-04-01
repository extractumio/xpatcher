# xpatcher Design Specification -- Project Management Review

**Date:** 2026-03-29
**Reviewer Role:** Project Manager
**Documents Reviewed:** All 17 design documents (01-17) + consolidated review (00) + 7 expert reports
**Focus:** Execution planning, resource allocation, risk management, sprint planning

---

## VERDICT: Needs Work

The specification is architecturally thorough and the review team has resolved all 7 critical and 14 major issues. The design is ready for implementation at the *technical specification* level. However, the **project plan is not ready for execution** -- the roadmap in Section 11 presents 40 deliverables across 5 phases in a flat table format with no effort estimates, no dependency graph between deliverables, no definition of done per phase, and no resource assignment. A team starting Monday would not know what to build first, how to split work, or when to stop and assess.

The gap is bridgeable. The spec quality is high enough that a 2-3 day planning sprint can produce a workable execution plan. This review provides that plan.

---

## 1. WORK BREAKDOWN STRUCTURE ANALYSIS

### 1.1 The 40 Deliverables: Estimability Assessment

The roadmap lists 40 deliverables with a "Test" column but no effort estimate, no size indicator, and no acceptance criteria beyond the test description. I have classified each deliverable by estimability.

**Well-defined (can estimate with high confidence): 22 of 40**

These have clear scope boundaries and the spec provides enough detail to code from:

| # | Deliverable | Est. Effort | Confidence |
|---|-------------|-------------|------------|
| 1 | `.claude-plugin/` directory with `plugin.json` | 0.5d | High |
| 2 | `explorer.md` agent | 0.5d | High |
| 3 | `/xpatcher:status` skill | 0.5d | High |
| 5 | `state.py` (PipelineState) | 2d | High |
| 6 | `schemas.py` (Pydantic models) | 2d | High |
| 7 | `planner.md` agent + `/xpatcher:plan` skill | 1d | High |
| 8 | `executor.md` agent + `/xpatcher:execute` skill | 1d | High |
| 9 | `reviewer.md` agent + `/xpatcher:review` skill | 1d | High |
| 13 | `tester.md` agent + `/xpatcher:test` skill | 1d | High |
| 14 | `simplifier.md` agent + `/xpatcher:simplify` skill | 1d | High |
| 15 | `gap-detector.md` agent + `/xpatcher:detect-gaps` skill | 1d | High |
| 16 | `tech-writer.md` agent + `/xpatcher:update-docs` skill | 1d | High |
| 17 | PreToolUse hooks | 1d | High |
| 21 | `tui.py` (Rich-based TUI) | 3d | High |
| 22 | Per-stage elapsed time tracking | 0.5d | High |
| 23 | Agent log capture to JSONL | 1d | High |
| 29 | PostToolUse hooks (audit logging) | 1d | High |
| 30 | Lifecycle hooks | 1d | High |
| 33 | `install.sh` | 1d | High |
| 35 | `.xpatcher.yaml` project-level config | 1d | High |
| 39 | `pip install` packaging | 1d | High |
| 40 | User-facing documentation | 5d | High |

**Underestimated or poorly scoped (need decomposition): 12 of 40**

| # | Deliverable | Stated Phase | Why Underestimated | Real Effort |
|---|-------------|-------------|-------------------|-------------|
| 4 | `session.py` (ClaudeSession) | Phase 1 | This is not just "invoke `claude -p`." It includes session lifecycle, `--resume` semantics, output format parsing (`stream-json`), timeout handling, error classification, and the session registry from Section 7.8. This is the single most critical integration point. | 4-5d |
| 10 | `context/builder.py` (prompt assembly) | Phase 2 | Section 7.9 defines per-agent prompt structures, artifact injection, `MissingArtifactError` handling, and build-time schema injection. This is a complex templating system, not a simple string builder. | 3d |
| 11 | Plan-execute-review pipeline in `core.py` | Phase 2 | The main dispatch loop coordinates state transitions, artifact I/O, session management, human gates, and error handling. This is the heart of the system. | 5-7d |
| 12 | Review-fix loop with iteration cap | Phase 2 | Includes oscillation detection (hash comparison), strategy switching on repeated failure, and escalation logic. | 2d |
| 18 | Acceptance criteria verification in dispatcher | Phase 3 | Running external test commands, parsing exit codes, handling timeouts, mapping results back to task states. More complex than "harness runs test commands." | 2d |
| 19 | Per-task quality loop (Stages 12-13) | Phase 3 | Orchestrates tester, reviewer, simplifier in sequence with conditional branching, revert logic, and re-test after simplification. | 3d |
| 24 | Agent log streaming in TUI | Phase 4 | Async subprocess reading, stream-json parsing, multiplexed display for parallel agents, keyboard switching. | 2d |
| 25 | `parallel.py` (thread pool) | Phase 4 | v1 is sequential, so this is a v2 deliverable. But even v1 needs async subprocess management for agent invocation. Mislabeled. | 2d (v2) |
| 26 | Git worktree management | Phase 4 | Explicitly deferred to v2 (Section 2.6.1). Should be removed from the Phase 4 table. | 0d (v1) / 5d (v2) |
| 28 | State persistence + `resume_pipeline()` | Phase 4 | This is not a Phase 4 item. Resume logic is needed from the very first pipeline run. Underestimated by placement, not by scope. | 2d (Phase 1) |
| 31 | Cost tracking + budget enforcement | Phase 4 | Section 8.4 explicitly says "deferred to v2." This deliverable contradicts the spec. | 0d (v1) |
| 32 | DAG-based task scheduling with critical path | Phase 4 | For v1 sequential, this is straightforward topological sort. For v2 with concurrency, it is significantly more complex. v1 estimate: 2d. | 2d |

**Research/spike required (cannot estimate until explored): 6 of 40**

| # | Deliverable | Why Spike Needed |
|---|-------------|-----------------|
| 4 | ClaudeSession (partially) | Must empirically validate Claude Code CLI flags: `--agent`, `--resume`, `--output-format stream-json`, `--plugin-dir`. Behavior may differ from docs. |
| 20 | Documentation stage (Stage 15) | Depends on how reliably the tech-writer agent produces output. Doc 14 defines a 4-attempt retry with fresh session, but real-world behavior is unknown. |
| 34 | Project auto-detection | Detecting project stack, test framework, linter -- this is heuristic work that needs iteration. |
| 36 | `/xpatcher:pipeline` skill (full pipeline) | This is just a thin shell wrapper calling `xpatcher start`. Trivial if the dispatcher works. |
| 37 | `xpatcher logs` command | Straightforward file querying. Should be easy but depends on log format stability. |
| 38 | Integration tests on sample projects | Unbounded effort. Depends entirely on how the pipeline behaves on real repos. This is where schedule risk lives. |

### 1.2 Total Effort Estimate

| Phase | Spec Estimate | My Estimate (v1 only) | Notes |
|-------|--------------|----------------------|-------|
| Phase 1 (Foundation) | 2 weeks | 2-3 weeks | Deliverable 4 (ClaudeSession) is the bottleneck. Add spike for CLI validation. |
| Phase 2 (Core Pipeline) | 2 weeks | 3-4 weeks | `core.py` dispatch loop is 5-7 days alone. Prompt assembly is 3 days. |
| Phase 3 (Quality Gates) | 2 weeks | 2-3 weeks | Realistic if Phase 2 quality loop code is solid. |
| Phase 4 (TUI, Logging, Polish) | 2 weeks | 2 weeks | After removing v2-only items (25, 26, 31). |
| Phase 5 (Packaging) | 1 week+ | 2-3 weeks | Integration testing (38) is open-ended. User docs (40) need 5d. |
| **Total** | **9 weeks** | **12-15 weeks** | **With a 2+1 team. 10-12 with a 3+1 team.** |

The roadmap's 9-week estimate is optimistic by 30-60%.

---

## 2. CRITICAL PATH ANALYSIS

### 2.1 The Longest Dependency Chain

The critical path runs through the following deliverables:

```
CLI validation spike (Claude Code flags)
    |
    v
ClaudeSession (#4) -- must be solid before any agent can be invoked
    |
    v
PipelineState (#5) + Pydantic schemas (#6) -- parallel with #4's later stages
    |
    v
Prompt assembly / context builder (#10) -- agents cannot be invoked without prompts
    |
    v
Planner agent + plan skill (#7) -- first real agent invocation
    |
    v
Plan-execute-review pipeline / core.py (#11) -- the dispatch loop
    |
    v
Review-fix loop (#12) -- depends on #11 working
    |
    v
Per-task quality loop (#19) -- depends on #12 pattern
    |
    v
Acceptance criteria verification (#18) -- depends on quality loop structure
    |
    v
Gap detection integration (#20, pipeline stage 15)
    |
    v
Integration tests on sample projects (#38) -- end-to-end validation
```

**Critical path length: ~8-10 weeks** (assuming one person on the critical path).

### 2.2 Where Parallelism Helps

The following work streams are independent and can proceed in parallel:

**Stream A (Critical Path): Dispatcher Core** -- one senior Python engineer
- ClaudeSession -> PipelineState -> core.py -> quality loop -> gap detection integration

**Stream B: Agent Definitions + Prompt Engineering** -- one prompt engineer
- All 8 agent markdown files (#2, 7, 8, 9, 13, 14, 15, 16)
- All 9 skill definitions
- Prompt testing and tuning against real codebases
- Can start Day 1, delivers continuously, final tuning in Phase 3-4

**Stream C: Plugin Infrastructure + TUI** -- one DevOps/platform engineer
- Plugin structure (#1), hooks (#17, 29, 30), install.sh (#33)
- TUI (#21, 22, 24), packaging (#39)
- Can start Day 1 for plugin structure; TUI blocked on dispatcher emitting events

**Stream D: Schemas + Validation** -- can be split from Stream A
- Pydantic models (#6), semantic validator (Doc 13 Component 3)
- Config schema (Doc 13 Component 8), error taxonomy (Doc 13 Component 9)
- Can start Day 1, provides models that other streams consume

---

## 3. RISK REGISTER

The spec's Section 10 covers 5 technical risks. This register adds 12 project-level risks.

### 3.1 Project-Level Risks

| ID | Risk | Probability | Impact | Mitigation |
|----|------|-------------|--------|------------|
| PR-1 | **Claude Code CLI flag changes** -- The design depends on `claude -p`, `--output-format stream-json`, `--resume`, and potentially `--agent` and `--plugin-dir`. These flags are not part of a stable API. Any change breaks the ClaudeSession layer. | High | Critical | Build a thin adapter layer around all CLI invocations. Abstract flag construction into a single function. Add a CLI compatibility test that runs on every CI build. Pin Claude Code CLI version in `xpatcher doctor`. |
| PR-2 | **API rate limits constrain development velocity** -- During development, the team will invoke Claude models hundreds of times per day for testing. Rate limits may throttle development before the product even ships. | Medium | High | Request elevated API limits from Anthropic for development. Use recorded fixtures (golden snapshots) for all unit/integration tests. Reserve live API calls for E2E tests only. Budget 1-2 E2E test runs per PR. |
| PR-3 | **Scope creep through "missing components"** -- The review already identified 16 missing components. During implementation, more will surface. Each "quick addition" extends the timeline. | High | High | Freeze the v1 feature set at the current spec (Docs 01-17). Any new component goes to a `v2-backlog.md` file. No additions without explicit product owner approval and timeline impact assessment. Weekly scope review. |
| PR-4 | **Prompt engineering is empirical, not plannable** -- Agent prompt quality can only be assessed by running agents against real codebases. Tuning cycles are unpredictable. The planner might produce great plans on day 1, or it might take 2 weeks of iteration. | High | Medium | Start prompt engineering in Week 1 with the simplest possible end-to-end path (a trivial feature on a small repo). Capture failures as test fixtures. Accept that prompts will need ongoing tuning post-launch. Do not gate Phase 2 on "perfect" prompts. |
| PR-5 | **The 2+1 minimum team is too small** -- The spec identifies 7 roles. The minimum team of 2 engineers + 1 prompt engineer has no QA, no DevOps, no technical writer. These roles are described as "part-time" but every role has substantial deliverables. | Medium | Medium | Assign QA responsibilities to the backend engineers (test as you build). Defer formal user documentation until Phase 5. Use the prompt engineer for technical writing (they understand the system). Acknowledge that this team size means 12-15 weeks, not 9. |
| PR-6 | **First E2E test fails spectacularly** -- The first full pipeline run on a real repo will likely fail in unexpected ways. Agent output may not parse, test commands may not exist, the quality loop may oscillate, the TUI may crash. | High | Medium | Plan for it. Schedule a dedicated "first E2E" milestone at the end of Phase 2 with 3 buffer days for debugging. Use the simplest possible test case (a 1-task feature on a tiny Python repo). Have a postmortem protocol ready. |
| PR-7 | **Dependency on Claude Code plugin system stability** -- The plugin system (`.claude-plugin/`, `plugin.json`, skill definitions, hooks) is not a stable/versioned API. Anthropic may change the plugin spec at any time. | Medium | High | Monitor Anthropic changelog weekly. Keep plugin surface area minimal. Test plugin loading on every CI build. Have a fallback strategy: if the plugin system changes, the dispatcher can invoke agents directly via `claude -p` with inline system prompts (more verbose but plugin-independent). |
| PR-8 | **Team member unavailability** -- With a minimum team of 3, losing any member for even a week creates a bottleneck. The dispatcher engineer is a single point of failure. | Medium | High | Cross-train: the second engineer should be able to work on `core.py` within 2 weeks. Document all architectural decisions as they are made (not retroactively). Pair on critical path items for the first week. |
| PR-9 | **YAML output parsing fragility** -- Agents produce YAML as raw text output. LLMs are not perfect YAML generators: they may add prose before/after the YAML block, use inconsistent indentation, or produce invalid YAML. The `MalformedOutputRecovery` system (Section 7.7) handles this but may not cover all edge cases. | Medium | Medium | The spec already has a 3-stage validation pipeline (extraction, schema, semantic). Add a comprehensive corpus of malformed output examples during Phase 1 and test the recovery path. Track malformation rates in production. |
| PR-10 | **Integration testing has no end condition** -- Deliverable 38 ("integration tests on sample projects") has no definition of done. How many projects? How many features per project? What pass rate is acceptable? | High | Medium | Define concrete E2E success criteria now: 3 sample projects (per Doc 16), 2 features each, all 6 must complete without human intervention (excluding plan approval gate). Target: 80% first-run success rate. |
| PR-11 | **Cost estimation for pipeline runs is unmeasured** -- No one knows what a typical pipeline run costs. If a single feature costs $50-100 in API calls, adoption will be limited to high-value features. If it costs $5-10, it can be used for everything. This affects the product's viability. | Medium | High | Measure actual costs during Phase 2 E2E testing. Track tokens per agent per stage. Publish cost benchmarks in documentation. If costs are too high, adjust model tiers (more Sonnet, less Opus) or reduce review iterations. |
| PR-12 | **The spec is 17 documents and growing** -- At ~4,500 lines of specification across 17 documents, there is a risk that implementors cannot hold the full system in their heads. Contradictions may re-emerge during implementation. | Medium | Low | Maintain the resolution report (Doc 17) as a living document. Require implementors to read only the documents relevant to their current deliverable (use the Quick Navigation table). Hold weekly consistency checks during Phase 1-2. |

---

## 4. DEFINITION OF DONE

### 4.1 Phase-Level Definitions of Done

The roadmap says each phase has a "Goal" but no exit criteria. Here are the definitions of done:

**Phase 1 -- Foundation: DONE when:**
1. `claude -p` can be invoked programmatically and output parsed into Pydantic models
2. Pipeline state can be created, transitioned, persisted, and read back from disk
3. At least one agent (explorer) can be invoked via the skill system
4. The plugin is recognized by Claude Code (manual verification)
5. `xpatcher doctor` (Doc 15 Component 16) passes on macOS and Ubuntu
6. Unit tests exist for PipelineState, schemas, and ClaudeSession (minimum 30 tests)

**Phase 2 -- Core Pipeline: DONE when:**
1. A pipeline can go from `xpatcher start "..."` through intent capture, planning, plan review, plan approval (manual), task breakdown, and execute a single task
2. The review-fix loop works with oscillation detection
3. Plan review iteration cap (3) triggers escalation correctly
4. Pipeline state survives a Ctrl+C and resumes correctly
5. At least one successful end-to-end run on a real (small) project exists as a recorded fixture
6. Unit + integration tests: minimum 60 tests, coverage > 70% on dispatcher code

**Phase 3 -- Quality Gates: DONE when:**
1. The full quality loop (test -> review -> optional simplify) executes for a task
2. PreToolUse hooks correctly block write operations for read-only agents
3. The gap detector runs and either reports gaps or clears
4. Documentation stage runs (or gracefully degrades on failure per Doc 14 Component 15)
5. A full pipeline (all 16 stages) completes on the simplest possible test case
6. Unit + integration tests: minimum 100 tests, coverage > 80% on dispatcher code

**Phase 4 -- TUI, Logging, Polish: DONE when:**
1. The TUI renders progress correctly during a live pipeline run
2. Agent logs are captured to JSONL and queryable via `xpatcher logs`
3. Signal handling works: single Ctrl+C graceful, double Ctrl+C force-kill
4. Budget/cost tracking is NOT required (v2 per spec Section 8.4)
5. All circuit breakers (Section 8.3) are implemented and have tests

**Phase 5 -- Packaging and Distribution: DONE when:**
1. `install.sh` works on clean macOS and Ubuntu 22.04
2. `pip install xpatcher` works (or equivalent package installation)
3. 3 sample projects pass E2E testing (per Doc 16 spec)
4. Quickstart Guide and CLI Reference exist and are tested
5. `xpatcher upgrade` and `xpatcher uninstall` work
6. A new developer can go from zero to first pipeline run using only the documentation

### 4.2 When Is v1 "Done"?

v1 is done when ALL of the following are true:

1. **All 5 phases are complete** per the definitions above
2. **Sequential execution only** -- no parallel agents, no worktrees (these are v2)
3. **3 sample projects** pass full pipeline runs with <20% human intervention rate (excluding plan approval)
4. **Cost data published** -- average cost per pipeline run documented for at least 3 feature types
5. **A user who has never seen xpatcher** can install it, run a pipeline, and get a PR -- using only the documentation
6. **No known critical bugs** -- all bugs classified as "pipeline crash" or "data loss" are fixed

Items explicitly NOT required for v1:
- Parallel agent execution
- Git worktree management
- Cost budgets / budget enforcement
- External notifications (Slack, email)
- CI/CD integration
- MCP server for pipeline state
- Agent Teams migration
- Learning from outcomes / memory system

---

## 5. INTER-PHASE DEPENDENCIES

### 5.1 Can Phase 3 Start Before Phase 2 Is Complete?

**Partially yes.** The dependency graph between phases is not strictly sequential:

```
Phase 1: [ClaudeSession] [PipelineState] [Schemas] [Plugin structure]
              |                |              |            |
              v                v              v            |
Phase 2: [core.py dispatch loop]<----[prompt assembly]     |
              |                                            |
              v                                            |
         [review-fix loop]                                 |
              |                                            |
              +-----+                                      |
                    v                                      v
Phase 3:     [quality loop]              [hooks] (can start in Phase 1)
              |                            |
              v                            v
         [acceptance criteria]     [hook integration tests]
              |
              v
         [gap detection integration]
              |
              v
Phase 4: [TUI]  [log streaming]  [signal handling]
              |
              v
Phase 5: [install.sh]  [E2E tests]  [docs]  [packaging]
```

**What can overlap:**

| Work Item | Can Start During | Requires |
|-----------|-----------------|----------|
| Agent markdown files (all 8) | Phase 1, Day 1 | Nothing -- these are just markdown files |
| Skill definitions (all 9) | Phase 1, Day 1 | Nothing |
| PreToolUse hooks (#17) | Phase 1 | Plugin structure (#1) |
| Pydantic schemas (#6) | Phase 1, Day 1 | Nothing |
| Config schema (Doc 13) | Phase 1, Day 1 | Nothing |
| Error taxonomy (Doc 13) | Phase 1, Day 1 | Nothing |
| TUI skeleton (#21) | Phase 2 | State machine events defined (#5) |
| PostToolUse / lifecycle hooks (#29, 30) | Phase 2 | Plugin structure (#1) |
| install.sh (#33) | Phase 1 | Nothing -- basic installer can exist early |
| User docs draft | Phase 2 | CLI interface stabilized |

**What cannot overlap:**

| Work Item | Hard Dependency |
|-----------|----------------|
| core.py dispatch loop (#11) | ClaudeSession (#4), PipelineState (#5), prompt builder (#10) |
| Quality loop (#19) | core.py dispatch loop (#11), review-fix loop (#12) |
| Gap detection integration | Quality loop (#19) must work |
| E2E tests (#38) | Full pipeline must work end-to-end |

### 5.2 The Real Phase Structure

The spec's 5 linear phases should be reorganized into 3 parallel work streams with 4 integration milestones:

```
STREAM A (Dispatcher Core):     Session -> State -> Core loop -> Quality loop -> Gap -> Polish
STREAM B (Agents + Prompts):    Agent files -> Prompt testing -> Tuning -> Expert panel
STREAM C (Platform + UX):       Plugin -> Hooks -> TUI -> Install -> Packaging -> Docs

MILESTONES:
  M1 (Week 3):   First agent invocation works (A+B converge)
  M2 (Week 5-6): First full pipeline run (A+B+C converge)
  M3 (Week 8-9): All quality gates active, TUI works
  M4 (Week 12+): E2E passes on 3 sample projects, docs complete
```

---

## 6. RESOURCE PLAN

### 6.1 Role Consolidation

The spec identifies 7 roles for a "minimum 2+1 team." Here is how they map:

| Spec Role | Assigned To | Rationale |
|-----------|-------------|-----------|
| Python Backend Engineer #1 | **Engineer A (Lead)** | Owns Stream A: ClaudeSession, PipelineState, core.py, quality loop, gap integration. This is the critical path owner. |
| Python Backend Engineer #2 | **Engineer B** | Owns Stream D (schemas, validation) then joins Stream A for quality loop + quality gates. Also owns unit/integration test authoring. |
| Prompt Engineer / AI Specialist | **Prompt Engineer (PE)** | Owns Stream B: all agent definitions, skill definitions, prompt testing and tuning. Also writes user documentation in Phase 5 (they understand the system best). |
| DevOps / Platform Engineer | **Engineer B (secondary)** | install.sh, packaging, `xpatcher doctor` -- these are Phase 1 and Phase 5 tasks. Engineer B handles them when not on Stream A. |
| TUI / CLI Developer | **Engineer A or B** | TUI is a Phase 4 deliverable. By then, both engineers know the system well enough to build it. |
| QA Engineer | **All three (distributed)** | Test-as-you-build. No dedicated QA until/unless team grows. PE handles E2E testing (they run pipelines constantly). |
| Technical Writer | **PE** | PE writes prompts and understands the user experience. They are best positioned for quickstart guide and CLI reference. |

### 6.2 Bottleneck Analysis

| Bottleneck | Where | Impact | Mitigation |
|------------|-------|--------|------------|
| **Engineer A availability** | Stream A (critical path) | Any absence delays the entire project | Cross-train Engineer B on core.py during Week 1-2 via pairing |
| **Claude Code CLI knowledge** | Week 1 spike | Blocks all agent invocation work | Front-load the CLI exploration spike. Do it Day 1-2. |
| **Prompt tuning latency** | Stream B, Weeks 3-6 | Prompts need real pipeline runs to tune; pipeline needs working prompts to test | Use stub/minimal prompts in Phase 1-2. PE tunes in parallel as pipeline develops. |
| **API rate limits during development** | All streams | Throttled development | Use golden fixtures for unit tests. Reserve API quota for PE's prompt testing and E2E runs. |
| **Integration points** | Milestones M1-M4 | Streams must converge; integration bugs appear | Dedicate 2-3 days at each milestone for integration testing and debugging. |

### 6.3 Weekly Time Allocation (3-person team)

| Week | Engineer A | Engineer B | Prompt Engineer |
|------|-----------|-----------|----------------|
| 1 | CLI spike + ClaudeSession | Plugin structure + Pydantic schemas | Agent files (all 8) + skills |
| 2 | ClaudeSession + PipelineState | Config schema + error taxonomy + hooks | Agent files + first manual prompt tests |
| 3 | Prompt assembly + core.py (start) | Semantic validation + state tests | Prompt testing on real codebases |
| 4 | core.py dispatch loop | Review-fix loop (supporting A) | Prompt tuning + intent capture testing |
| 5 | Quality loop (Stages 12-13) | Acceptance criteria verification | Plan/execute/review prompt tuning |
| 6 | Gap detection integration | Documentation stage + hooks integration | Full pipeline prompt tuning |
| 7 | TUI implementation | Signal handling + retry logic | Expert panel tuning |
| 8 | TUI polish + log streaming | Log capture + `xpatcher logs` | E2E test runs on sample projects |
| 9 | Integration debugging + polish | install.sh + packaging | E2E testing + fixture capture |
| 10 | Resume/crash recovery hardening | `xpatcher doctor` + upgrade/uninstall | Quickstart guide draft |
| 11 | E2E test debugging | E2E test debugging | CLI reference + docs finalization |
| 12 | Final integration + release prep | Final integration + release prep | Final docs + release prep |

---

## 7. COMMUNICATION PLAN

### 7.1 Review Checkpoints

| Checkpoint | When | Who Reviews | What | Go/No-Go Criteria |
|------------|------|-------------|------|-------------------|
| **CLI Spike Review** | End of Day 2 | All 3 | ClaudeSession proof-of-concept | Can invoke `claude -p` and parse structured output? If no, re-evaluate architecture. |
| **M1: First Agent Invocation** | End of Week 3 | All 3 + Product Owner | Live demo: explorer agent answers a question about a real repo | Agent invoked programmatically, output parsed, state persisted. |
| **M2: First Pipeline Run** | End of Week 5-6 | All 3 + Product Owner | Live demo: intent -> plan -> approval -> 1 task executed | Full Stage 1-13 on a trivial feature. May fail; that is expected. |
| **M3: Quality Gates Active** | End of Week 8-9 | All 3 + Product Owner | Full pipeline with test/review/simplify/gap detection | All 16 stages execute. TUI renders. Hooks enforce policies. |
| **M4: E2E Validation** | End of Week 12 | All 3 + Product Owner + External Tester | 3 sample projects, documentation walkthrough | New user can install + run from docs. 80% first-run success rate. |

### 7.2 Progress Tracking

- **Daily standups** (15 min): blockers, progress against weekly plan, API quota status
- **Weekly demo** (30 min): show what works end-to-end, not just unit tests
- **Sprint retrospective** (bi-weekly, 30 min): what is slower than expected, what to adjust
- **Spec consistency check** (weekly, 15 min): have any implementation discoveries contradicted the spec? Update Doc 17 if so.

### 7.3 Decision Log

Maintain a `decisions.md` file in the repo root:
- Every implementation decision that deviates from spec
- Every spec ambiguity resolved during implementation
- Every v1/v2 scope boundary clarification
- Format: `[DATE] DECISION: <decision>. REASON: <reason>. SPEC REF: <section>.`

---

## 8. CONTINGENCY PLANNING

### 8.1 What If Claude Code CLI Changes Its Flags?

**Detection:** The CLI spike (Day 1-2) validates all required flags. `xpatcher doctor` includes a CLI compatibility check. CI runs a flag validation test on every build.

**Response plan:**
1. If `--output-format stream-json` changes: fall back to plain text output parsing (degraded but functional)
2. If `-p` (headless prompt) changes: this is a complete blocker. Escalate to Anthropic. No workaround.
3. If `--resume` semantics change: use fresh sessions with context bridges for everything (already the recommended approach per Section 7.8)
4. If `--agent` flag never materializes (OQ-1): use `-p` with full agent system prompt inline. More verbose, works today. Already documented as the fallback.

**Cost of contingency:** 2-3 day adapter rewrite for flag changes. Complete stop for `-p` removal.

### 8.2 What If API Rate Limits Are Too Low?

**Detection:** Track rate limit errors during development. Measure sustainable concurrent sessions.

**Response plan:**
1. **Development throttling:** Reduce parallel development API calls. Use fixtures for all tests. Rotate API keys.
2. **Product throttling:** Reduce default `max_parallel_agents` from 3 to 1. Add exponential backoff (already specified in #27 retry.py). Add queue depth to TUI display.
3. **Architecture change:** If rate limits are severe (<5 concurrent sessions), abandon parallel execution entirely. The v1 sequential architecture handles this naturally.
4. **Business response:** Request higher limits from Anthropic. Document minimum API tier requirements.

### 8.3 What If the First E2E Test Fails Spectacularly?

**It will.** Plan for it.

**Preparation:**
1. Use the smallest possible test case: a 5-file Python project, 1-task feature ("add input validation to function X")
2. Run each stage manually first (via individual skills) before attempting the full pipeline
3. Have debug logging on from the start (JSONL agent logs)
4. Record the failure as a golden fixture for the test suite

**Response protocol:**
1. **Triage** (1 hour): Which stage failed? Is it agent output parsing, state machine transition, or the agent's actual work?
2. **Isolate** (2-4 hours): Run the failing stage in isolation with the same inputs. Reproduce.
3. **Fix-forward** (1-2 days): Fix the issue, add a regression test, re-run the full pipeline.
4. **Retrospective** (30 min): What did we learn? What other stages might have similar issues?

**Budget:** 3 buffer days after M2 (first full pipeline run) specifically for E2E debugging.

### 8.4 What If the Pipeline Produces Bad Code?

This is the "works technically but the output is garbage" scenario.

**Detection:** First E2E tests will reveal code quality. Human review of PR output.

**Response plan:**
1. Tighten agent prompts (PE's primary job in Weeks 3-8)
2. Add more specific acceptance criteria templates per language
3. Increase review stringency (lower the threshold for `needs_changes` verdicts)
4. If fundamentally broken: fall back to Opus for execution (expensive but higher quality) and investigate why Sonnet is underperforming

### 8.5 What If a Core Assumption Is Wrong?

The design makes several assumptions that cannot be validated until implementation:

| Assumption | How to Validate | When | Fallback |
|------------|----------------|------|----------|
| Agents reliably produce valid YAML | Phase 1 spike: 20 invocations | Day 3-4 | Add more aggressive output extraction; accept JSON as fallback |
| Review agents catch real bugs | Phase 2: compare agent review to human review | Week 5 | Tighten prompts; add more checklist items; escalate to Opus |
| Plan approval is fast enough (<5 min) | Phase 2: time human approval at M2 | Week 6 | Add plan summary view; reduce plan verbosity; add "approve with defaults" option |
| Iteration caps of 3 are sufficient | Phase 3: track actual iteration counts | Week 7-8 | Increase caps or add strategy switching |
| Cost per pipeline is acceptable | Phase 2-3: track actual API costs | Week 5-8 | Adjust model tiers; reduce turn limits; add quality tier defaults |

---

## 9. DRY-RUN: FIRST 4 WEEKS PROJECT PLAN

### Week 1: Foundation Sprint A

**Goal:** Prove that the Python dispatcher can invoke Claude Code agents and parse their output.

| Day | Task | Owner | Deliverable | Blocked By |
|-----|------|-------|-------------|------------|
| 1 | CLI exploration spike: validate `claude -p`, `--output-format stream-json`, session flags | Eng A | Spike report: which flags work, which don't, what workarounds exist | Nothing |
| 1 | Create repo structure: `src/`, `.claude-plugin/`, `config.yaml`, `pyproject.toml` | Eng B | Skeleton repo with CI (linting, type checking) | Nothing |
| 1 | Write `explorer.md` agent + `/xpatcher:status` skill | PE | Working agent + skill files, manually tested | Nothing |
| 2 | CLI spike continued: test `--resume`, timeout behavior, error responses | Eng A | Updated spike report | Day 1 results |
| 2 | `schemas.py`: Pydantic base models (`ArtifactBase`, `PlanOutput`, `ExecutionOutput`, `ReviewOutput`) | Eng B | Models with unit tests (10+ tests) | Nothing |
| 2 | Write `planner.md`, `executor.md`, `reviewer.md` agent files | PE | Agent markdown files | Nothing |
| 3 | `session.py` (ClaudeSession): basic invocation, output parsing, error handling | Eng A | Can invoke `claude -p` and get structured output | Spike results |
| 3 | `schemas.py`: remaining models (`TestOutput`, `GapOutput`, `SimplificationOutput`, `DocsReportOutput`) | Eng B | Complete schema module with 20+ tests | Nothing |
| 3 | Write remaining agent files + begin prompt testing | PE | All 8 agent files; first manual prompt test results | Agent files |
| 4 | `session.py`: timeout handling, stream-json parsing | Eng A | ClaudeSession handles timeouts gracefully | Day 3 |
| 4 | `config.yaml` schema (`XpatcherConfig` Pydantic model per Doc 13 Component 8) | Eng B | Config loading with 4-layer resolution | Nothing |
| 4 | Manual agent invocations: run planner against a test repo, capture output | PE | Raw planner output samples; identify parsing issues | Agent files |
| 5 | Integration: ClaudeSession + schemas = invoke agent, parse output, validate | Eng A + B | Green integration test: agent -> parsed + validated output | Day 3-4 |
| 5 | Error taxonomy (`ErrorClassifier` per Doc 13 Component 9) | Eng B | Error classification with unit tests | Nothing |
| 5 | Prompt refinement based on parsing issues | PE | Updated agent prompts | Day 4 results |

**Week 1 Exit Criteria:** The team can programmatically invoke a Claude Code agent, receive YAML output, parse it into a Pydantic model, and classify any errors. Minimum 30 unit tests passing.

### Week 2: Foundation Sprint B

**Goal:** Pipeline state machine works end-to-end. Plugin is loadable.

| Day | Task | Owner | Deliverable | Blocked By |
|-----|------|-------|-------------|------------|
| 6 | `state.py`: PipelineState with validated transitions, disk persistence | Eng A | State machine with atomic writes | Nothing |
| 6 | `plugin.json`, `settings.json`, hook wrapper (`run_hook.sh`) | Eng B | Plugin loads in Claude Code | Nothing |
| 6 | Test planner output on 3 different repos: Python, TypeScript, Go | PE | Planner output quality report; prompt adjustments | Agent files |
| 7 | `state.py`: resume logic, crash recovery (read state from disk, continue) | Eng A | Pipeline survives restart | Day 6 |
| 7 | `pre_tool_use.py` hook: read-only enforcement, Bash allowlists | Eng B | Hook blocks write attempts for read-only agents | Day 6 |
| 7 | Test executor output: does it follow the plan? Does output parse? | PE | Executor quality report; prompt adjustments | Agent files |
| 8 | `PipelineStateModel` (Doc 13 Component 7): full Pydantic model for `pipeline-state.yaml` | Eng A | Validated state model with 15+ tests | Day 6 |
| 8 | `pre_tool_use.py`: tester scope, tech-writer scope, project boundary | Eng B | All 7 policies implemented with tests | Day 7 |
| 8 | Test reviewer output: does it find real issues? YAML valid? | PE | Reviewer quality report | Agent files |
| 9 | Semantic validation rules (Doc 13 Component 3): first pass (PLAN-*, EXEC-*) | Eng B | Semantic validator for plans and execution output | Day 2-3 schemas |
| 9 | Integration: state machine + session + schemas = invoke agent, update state | Eng A | Green test: state transitions through intent -> planning -> plan review | Day 6-8 |
| 9 | Skill files refined based on output testing | PE | Updated skill files | Day 6-8 testing |
| 10 | Week 2 integration test: full foundation demo | All 3 | Demo: invoke planner on a test repo, parse output, persist state, resume | Week 1-2 work |
| 10 | Retrospective + Week 3-4 planning refinement | All 3 | Updated plan based on discoveries | Week 1-2 results |

**Week 2 Exit Criteria:** Pipeline state machine works with validated transitions and disk persistence. Plugin loads in Claude Code. PreToolUse hooks enforce read-only policies. At least one agent invocation works end-to-end through the state machine. Minimum 60 unit tests passing. **Milestone M0: Foundation proved.**

### Week 3: Core Pipeline Sprint A

**Goal:** Intent capture through plan approval works.

| Day | Task | Owner | Deliverable | Blocked By |
|-----|------|-------|-------------|------------|
| 11 | `context/builder.py`: prompt assembly for planner (intent analysis prompt per Doc 14) | Eng A | Planner prompt correctly assembled with codebase context | State machine + session |
| 11 | `IntentCaptureStage` class (Doc 14 Component 4) | Eng B | Intent capture with Q&A loop (max 2 rounds) | State machine |
| 11 | Test intent capture end-to-end: "Add X to project Y" -> intent.yaml | PE | Working intent capture on 2 test repos | Agent files |
| 12 | `core.py`: main dispatch loop skeleton (Stages 1-5) | Eng A | Dispatch loop handles intent -> planning -> review -> approval | Prompt assembly |
| 12 | `IntentModel`, `TaskModel` Pydantic models (Doc 14 Component 11) | Eng B | All 4 missing Pydantic models | Schemas |
| 12 | Prompt assembly for reviewer (plan review context per Section 7.8) | PE + Eng A | Reviewer receives correct context bridge | Session management |
| 13 | `core.py`: plan review loop (Stages 3-4) with iteration cap | Eng A | Plan review loop with oscillation detection | Day 12 |
| 13 | `TaskManifestModel`, `ExecutionPlanModel` (Doc 14) | Eng B | Remaining models with tests | Day 12 |
| 13 | Test plan review: does reviewer find real issues in plans? | PE | Review quality assessment | Day 12 |
| 14 | `core.py`: human gate for plan approval (Stage 5) | Eng A | Terminal prompt with structured options | Day 13 |
| 14 | Semantic validation: REV-*, TEST-*, GAP-* rules | Eng B | Complete semantic validator | Day 9 validator |
| 14 | Full test: intent -> plan -> review -> fix -> review -> approve | PE | First 5-stage pipeline run | Day 11-14 |
| 15 | Integration + debugging: first 5-stage pipeline run | All 3 | Working Stages 1-5 on a test repo | Week 3 work |
| 15 | Sprint retrospective | All 3 | Adjusted Week 4 plan | Week 3 results |

**Week 3 Exit Criteria:** A user can run `xpatcher start "..."`, see the intent captured, review the plan, and approve it. The plan review loop iterates correctly with escalation at max iterations. **Milestone M1: First agent invocation works through the pipeline.**

### Week 4: Core Pipeline Sprint B

**Goal:** Task execution through quality loop works for a single task.

| Day | Task | Owner | Deliverable | Blocked By |
|-----|------|-------|-------------|------------|
| 16 | `core.py`: task breakdown (Stage 6) + task review loop (Stages 7-8) | Eng A | Tasks generated from approved plan | Stages 1-5 working |
| 16 | Prompt assembly for executor (task context) | Eng B | Executor receives correct task + plan context | Prompt builder |
| 16 | Test task breakdown quality on 2 repos | PE | Task quality assessment | Day 14-15 |
| 17 | `core.py`: DAG construction (Stage 9-10), sequential execution (Stage 11) | Eng A | DAG built from task dependencies, first task executes | Day 16 |
| 17 | `ArtifactVersioner` class (Section 5.6): version management for all artifacts | Eng B | Artifact versioning with tests | Nothing |
| 17 | Test executor on a real task: does it produce correct code? | PE | Execution quality report | Day 16 |
| 18 | `core.py`: per-task quality loop skeleton (Stage 12-13) | Eng A | Test -> review -> [fix iteration] loop | Day 17 |
| 18 | Acceptance criteria verification: run test commands, parse exit codes | Eng B | AC runner with timeout handling | State machine |
| 18 | Test reviewer on executor output: does review find issues? | PE | Review-after-execution quality | Day 17 |
| 19 | `core.py`: review-fix loop with oscillation detection | Eng A | Quality loop terminates correctly | Day 18 |
| 19 | Cancellation workflow (Doc 13 Component 5): `xpatcher cancel` | Eng B | Clean pipeline cancellation | State machine |
| 19 | Full pipeline test: 1-task feature, all stages | PE | Pipeline runs through Stages 1-13 | Day 18-19 |
| 20 | Integration: first single-task pipeline run through quality loop | All 3 | Working Stages 1-13 on a trivial feature | Week 4 work |
| 20 | Sprint retrospective + M2 readiness assessment | All 3 | M2 status: go/no-go for multi-task pipeline | Week 3-4 results |

**Week 4 Exit Criteria:** A single-task feature can be planned, approved, executed, tested, reviewed, and either pass or iterate through the fix loop. The pipeline handles Ctrl+C gracefully. **Nearing Milestone M2 -- first full pipeline (multi-task will follow in Week 5-6).**

### Sprint 1 Backlog (First 10 Tasks)

Prioritized by dependency chain. Each task has a clear definition of done.

| # | Task | Owner | Est. | Definition of Done | Dependencies |
|---|------|-------|------|-------------------|-------------|
| S1-01 | **CLI exploration spike** -- Validate `claude -p`, `--output-format stream-json`, `--resume`, error behavior, timeout behavior | Eng A | 2d | Written report documenting: (a) which flags work as expected, (b) output format samples, (c) error response format, (d) timeout behavior, (e) workarounds needed | None |
| S1-02 | **Repo skeleton** -- Create project structure: `src/dispatcher/`, `.claude-plugin/`, `config.yaml`, `pyproject.toml`, CI config (lint + type check) | Eng B | 0.5d | `python -m pytest` runs (empty), `ruff check` passes, `mypy` passes | None |
| S1-03 | **Agent markdown files** -- Write all 8 agent definitions per Section 4 | PE | 2d | 8 `.md` files matching spec. Manual Claude Code test: each agent loads and responds to a trivial prompt. | None |
| S1-04 | **Pydantic schema models** -- Implement `ArtifactBase`, all 7 agent output models, 4 dispatcher models per Sections 9 + Doc 14 | Eng B | 3d | All 11 models pass validation with example YAML from the spec. 25+ unit tests. `SCHEMAS` registry maps all artifact types. | S1-02 |
| S1-05 | **ClaudeSession class** -- Implement `claude -p` invocation, output parsing (stream-json and plain text), timeout handling, error classification | Eng A | 4d | Can invoke `claude -p "question"`, parse structured output into dict, handle timeout (returns error), handle malformed output (returns error with raw text). 10+ tests using mocked subprocess. | S1-01 |
| S1-06 | **XpatcherConfig model** -- Implement config schema per Doc 13 Component 8. 4-layer resolution: CLI > project > global > defaults | Eng B | 2d | Config loads from file, merges layers correctly, validates all fields. 10+ tests. | S1-02 |
| S1-07 | **PipelineState + state machine** -- Implement `PipelineStage` enum, validated transitions, atomic disk persistence, `PipelineStateModel` per Doc 13 Component 7 | Eng A | 3d | State transitions validated (invalid transitions raise error). State persists to disk and survives process restart. 15+ tests. | S1-05 (partial -- needs session concept) |
| S1-08 | **ErrorClassifier** -- Implement error taxonomy per Doc 13 Component 9. Classify errors as transient/permanent/user-actionable. | Eng B | 1d | All 16 error types classified. Retry policy per type. 8+ tests. | S1-02 |
| S1-09 | **Plugin structure** -- Create `plugin.json`, `settings.json`, skill stub files, hook wrapper script | Eng B | 1d | Plugin recognized by Claude Code. `xpatcher:status` skill responds. | S1-03 |
| S1-10 | **Foundation integration test** -- End-to-end: invoke planner agent on a test repo, parse output into `PlanOutput` model, validate semantically, persist pipeline state | Eng A + PE | 1d | Green test: planner produces valid plan YAML, parsed into model, state updated to `PLANNING`, state file exists on disk | S1-05, S1-04, S1-07 |

### Identified Blockers Before They Happen

| Blocker | When It Hits | Severity | Preemptive Action |
|---------|-------------|----------|-------------------|
| **B1: `claude -p` behaves differently than documented** | Day 1-2 (spike) | Critical | The spike IS the action. If `-p` doesn't work as expected, stop everything and adapt. This is the single-point-of-failure for the entire project. |
| **B2: Claude Code plugin spec changes** | Day 1 (plugin loading) | High | Test plugin loading on Day 1. If the spec has changed, adapt immediately. Keep plugin surface area minimal. |
| **B3: API rate limits during team testing** | Week 1-2 | Medium | Set up a shared API key usage tracker from Day 1. Establish quotas: PE gets 60% (prompt testing), Eng A gets 30% (integration tests), Eng B gets 10% (schema validation). Use mocks for all unit tests. |
| **B4: Agent YAML output is unreliable** | Day 3-4 (first agent invocations) | Medium | The spec's `MalformedOutputRecovery` (Section 7.7) handles this, but it needs to be implemented early. Budget 1 day in Week 1 for output extraction hardening. |
| **B5: State machine transitions are wrong** | Week 2 (state machine integration) | Medium | Write state transition tests FIRST (property-based: "from state X, transition Y should reach state Z"). The spec's transition tables are the oracle. |
| **B6: Prompt assembly is more complex than estimated** | Week 3 (prompt builder) | Medium | Start with the simplest possible prompt (just the system prompt + user message). Add artifact injection incrementally. Don't try to build the full prompt builder in one go. |
| **B7: Plan review loop never converges during testing** | Week 3-4 | Low | This is expected for early prompts. The iteration cap (3) handles it. Log why reviews reject plans. Tune prompts based on rejection patterns. |
| **B8: Cross-platform issues (macOS vs Linux)** | Week 2 (install.sh, hooks) | Low | Test on both platforms from Day 1. Use CI with both macOS and Ubuntu runners. The spec already fixed the `readlink -f` issue (CRIT-5). |

---

## 10. QUALITY GATES BETWEEN PHASES

### 10.1 Gate Structure

Each gate has explicit go/no-go criteria. The team stops and assesses. No gate can be passed by "we'll fix it later."

**GATE 0: Post-Spike (End of Day 2)**

| Criterion | Measurement | No-Go If... |
|-----------|-------------|-------------|
| `claude -p` invocable | Spike report | Cannot invoke Claude Code headlessly |
| Output parseable | Spike report | Output format is not machine-readable |
| Session management viable | Spike report | No way to resume or manage sessions |

**Decision:** If Gate 0 fails, the entire architecture needs rethinking. This is the kill-or-continue decision point.

**GATE 1: Foundation Complete (End of Week 2)**

| Criterion | Measurement | No-Go If... |
|-----------|-------------|------------|
| Agent invocation works | Integration test S1-10 passes | Cannot reliably invoke agents and parse output |
| State machine works | 15+ state transition tests pass | State transitions have bugs or don't persist |
| Plugin loads | Manual verification | Claude Code doesn't recognize the plugin |
| Test coverage | pytest --cov | Coverage < 60% on dispatcher code |
| Schema validation works | 25+ model tests pass | Models don't match agent output |

**Decision:** If Gate 1 fails on agent invocation, investigate and fix before proceeding. All other failures are fixable within a 1-week buffer.

**GATE 2: First Pipeline Run (End of Week 5-6)**

| Criterion | Measurement | No-Go If... |
|-----------|-------------|------------|
| Stages 1-13 work | At least 1 successful single-task pipeline run | Cannot complete a pipeline even on a trivial feature |
| Human gates work | Plan approval prompt appears and responds | Human interaction is broken |
| Ctrl+C handling works | Manual test: Ctrl+C during execution, then resume | State is corrupted after interruption |
| Quality loop terminates | Iteration cap triggers correctly | Quality loop runs forever or crashes |
| Test coverage | pytest --cov | Coverage < 70% on dispatcher code |
| Prompt quality | PE assessment + 3 sample runs | Planner produces unusable plans >50% of the time |

**Decision:** Gate 2 is the "does this approach work at all?" gate. If the pipeline cannot complete a trivial feature after reasonable debugging, the team should consider whether the scope is too ambitious for v1 and cut features (e.g., remove simplifier and gap detector from v1).

**GATE 3: Quality Gates Active (End of Week 8-9)**

| Criterion | Measurement | No-Go If... |
|-----------|-------------|------------|
| All 16 stages execute | Full pipeline run on 1 sample project | Any stage crashes or hangs |
| TUI renders correctly | Visual inspection during live run | TUI is unusable or misleading |
| Hooks enforce policies | Hook unit tests + live verification | Read-only agents can write files |
| Gap detector works | Gap detection on a project with known gaps | Gap detector misses obvious gaps |
| Test coverage | pytest --cov | Coverage < 80% on dispatcher code |
| Cost is known | API cost tracking for 3+ pipeline runs | No cost data available |

**Decision:** Gate 3 determines whether the product is shippable. If quality gates don't work, the pipeline produces unreviewed, untested code -- which is worse than useless.

**GATE 4: Release Readiness (End of Week 12+)**

| Criterion | Measurement | No-Go If... |
|-----------|-------------|------------|
| 3 sample projects pass E2E | E2E test results | <2 of 3 pass |
| New user can install + run | External tester walkthrough | Tester cannot complete quickstart guide |
| install.sh works cross-platform | CI on macOS + Ubuntu | Fails on either platform |
| No critical bugs | Bug tracker | Any "pipeline crash" or "data loss" bug open |
| Documentation complete | Quickstart + CLI reference exist | No documentation |
| Cost benchmarks published | Cost data from E2E runs | Cost unknown or unacceptable |

**Decision:** Gate 4 is the release decision. If it fails, extend for 1-2 weeks with a specific fix plan. Do not ship without Gate 4 pass.

### 10.2 Emergency Stop Criteria

The team should stop and reassess the entire approach if ANY of the following occur:

1. **Claude Code CLI removes headless mode** (`-p` flag) -- complete project blocker
2. **Agent YAML output is malformed >30% of the time** after 2 weeks of prompt tuning -- the fundamental interaction model is broken
3. **API costs exceed $100 per pipeline run** for a medium feature -- product is not viable at this cost
4. **The pipeline takes >4 hours** for a 5-task feature -- too slow for interactive use
5. **3 consecutive sprints miss >50% of planned deliverables** -- estimation or scope is fundamentally wrong

---

## SUMMARY

| Dimension | Assessment |
|-----------|-----------|
| **Spec quality** | Excellent. All 7 critical and 14 major issues resolved. 17 documents, internally consistent. |
| **Roadmap realism** | Optimistic by 30-60%. 9 weeks should be 12-15 weeks for a 3-person team. |
| **Estimability** | 22 of 40 deliverables well-scoped; 12 underestimated; 6 need spikes. |
| **Critical path** | ClaudeSession -> core.py -> quality loop. ~8-10 weeks on the critical path. |
| **Biggest risk** | Claude Code CLI stability (PR-1) and prompt engineering unpredictability (PR-4). |
| **Team size** | 2+1 is viable but tight. No slack for illness, vacation, or unexpected complexity. |
| **Definition of done** | Was absent from spec. Now defined (this review, Section 4). |
| **First sprint** | Well-defined. 10 tasks, clear dependencies, clear exit criteria. Ready to execute Monday. |

**Recommendation:** Accept the spec as the technical foundation. Adopt this review's project plan, risk register, and quality gates. Front-load the CLI spike (Day 1-2) as the kill-or-continue decision point. Plan for 12-15 weeks, not 9.

# Quality & Testing Framework Review

**Reviewer:** QA Architect / Test Engineering Lead
**Date:** 2026-03-29
**Spec Version:** 1.2 (Final Draft)
**Scope:** Sections 06-quality-testing.md, 04-agent-definitions.md, 03-pipeline-flow.md, 10-risk-mitigation.md, 12-appendices.md, 09-dispatcher-internals.md, 11-implementation-roadmap.md, and the master document.

---

## Executive Assessment

The quality and testing framework is one of the strongest parts of the xpatcher design. The multi-layered approach -- acceptance criteria with severity levels, a test quality verification pipeline, structurally isolated reviewers, convergence criteria, and gap detection -- demonstrates serious thought about the failure modes of LLM-driven development. The acceptance criteria template is well-designed and the principle that "the orchestrator evaluates the completion gate, never the executor" is exactly right.

However, there are three significant gaps:

1. **xpatcher has no test strategy for itself.** The spec exhaustively describes how xpatcher tests *target projects* but says nothing about how the xpatcher codebase (dispatcher, state machine, DAG scheduler, session management, artifact validation, hooks) will be tested. This is a critical omission for a system that must itself be reliable.

2. **The test quality verification pipeline is over-engineered for v1.** Running every new test 5 times, performing negation checks on all `must_pass` criteria, and invoking an LLM test auditor on every test creates enormous overhead that will dominate wall-clock time for simple tasks. The spec lacks any analysis of cost vs. value per gate.

3. **Integration and E2E testing of xpatcher is mentioned once** (Phase 5, Step 38: "Write integration tests on sample projects") **with zero detail.** For a pipeline that orchestrates 8 agents across 16 stages, this is woefully underspecified.

**Overall verdict:** Strong conceptual framework with practical gaps in self-testing, overhead management, and E2E coverage. Needs targeted revisions before implementation begins.

---

## Strengths

1. **Acceptance criteria template is excellent.** The five categories (functional, structural, behavioral, qualitative, regression) with YAML-native definition and the `must_pass / should_pass / nice_to_have` severity system are well-thought-out. The principle "specify WHAT is observable, not HOW it is implemented" is correct and should survive as a permanent design constraint.

2. **Orchestrator-evaluated completion gate.** The explicit statement that "the executor never self-certifies" and "the completion gate is evaluated by the orchestrator" addresses the most common failure mode of AI-driven development: premature victory declaration. This is reinforced by Section 3.4's "premature victory prevention" pattern.

3. **Reviewer structural isolation is well-designed.** The four mechanisms (separate context, checklists, read-only tools, adversarial framing) are individually sound and mutually reinforcing. The collusion prevention metrics (alert on >0.8 first-pass approval over 20 tasks) add a real feedback loop.

4. **Convergence criteria are explicit and measurable.** The six conditions in Section 6.6 (ACs pass, review approved, scope check, regression check, size check, dependent interface check) are all objectively verifiable. No "looks good to me" escape hatches.

5. **Gap detection with scope creep prevention.** The 30% cap on gap-generated tasks and the three-tier categorization (critical/expected/enhancement) is a practical mechanism for preventing runaway scope. The distinction between decomposition gaps, implicit requirement gaps, and integration gaps is analytically useful.

6. **Oscillation detection.** Hashing findings per iteration and escalating on repeat is a simple, effective mechanism that many pipeline designs miss.

7. **Language/framework auto-detection.** The manifest-file-based detection table covering 8 languages/frameworks is practical and extensible. Preflight tool validation before execution prevents late-stage failures.

---

## Critical Issues

### C1: No test strategy for xpatcher itself

**Location:** Absent from entire spec.

The spec describes in detail how xpatcher tests target project code but contains no strategy for testing the xpatcher codebase. The Python dispatcher includes a state machine, DAG scheduler, session registry, artifact validator, YAML extractor, context bridge builder, session compactor, hook enforcement engine, and TUI renderer. These are all non-trivial components with complex edge cases.

The implementation roadmap (Section 11) includes "Test" columns for each step, but these are one-line descriptions like "Schema validation works" or "State persists across restarts." This is not a test strategy; it is a checklist of demo scenarios.

**What is needed:**
- Unit tests for the state machine (valid transitions, invalid transition rejection, persistence/recovery)
- Unit tests for the DAG scheduler (cycle detection, orphan detection, topological ordering, concurrency limiting, ready-task selection after completion)
- Unit tests for the artifact validator (each of the 4 YAML extraction strategies, Pydantic validation for all 6 schema types, malformed output recovery flow)
- Unit tests for the session registry (registration, continuation lookup, stale session handling, lineage tracking, compaction thresholds)
- Integration tests for the hook enforcement (each of the 6 policies in pre_tool_use.py, including boundary conditions like symlinks and relative paths)
- Integration tests for the review-fix loop (iteration counting, oscillation detection, escalation)
- Integration tests for parallel execution (worktree creation, merge, conflict detection)

**Risk if unaddressed:** A bug in the dispatcher state machine could cause tasks to be skipped, repeated, or stuck. A bug in the DAG scheduler could cause deadlocks or wrong execution order. These bugs would be invisible to the test quality verification pipeline because that pipeline tests the *target* code, not the *orchestrator* code.

**Recommendation:** Add a new section "Testing xpatcher Itself" as Section 6.8 or as a dedicated subsection in the implementation roadmap. Define the test pyramid for xpatcher's own codebase with concrete coverage targets. This should be implemented in Phase 1-2, not Phase 5.

### C2: Test quality verification pipeline overhead is unanalyzed

**Location:** Section 6.2, test_quality_gates YAML.

The spec mandates five quality gates for every test the pipeline generates:
1. Coverage check (80% for new code)
2. Negation check (for all `must_pass` criteria)
3. LLM test auditor (read-only agent examines every test)
4. Mutation testing (optional, but recommended for critical paths)
5. Flaky detection (5 runs per new test)

For a typical task that produces 3-5 test files with 10-20 test cases, this means:
- **Negation check:** Temporarily invert each `must_pass` criterion and re-run the test suite. If there are 5 criteria, this is 5 additional full test suite runs. For a project where `npm test` takes 30 seconds, that is 2.5 additional minutes *per task*.
- **Flaky detection:** 5 full test suite runs. Another 2.5 minutes minimum.
- **LLM test auditor:** A separate Claude agent invocation. At Opus pricing, this could cost $2-5 per task and add 1-2 minutes of wall-clock time.
- **Mutation testing:** Even when "optional," if enabled for a critical path, it adds 5-30 minutes depending on codebase size.

**Total overhead per task (without mutation):** 6-10 minutes and $2-5 in additional LLM cost, on top of actual test execution. For a 12-task feature, that is 72-120 minutes and $24-60 just for test quality verification.

The spec provides no cost-benefit analysis, no recommendation for when to skip gates, and no "lite" mode for low-risk tasks.

**Recommendation:** Add a tiered quality gate configuration:
- **Lite** (low-risk tasks, refactors): coverage check + regression only
- **Standard** (most tasks): coverage + negation + flaky detection (3 runs, not 5)
- **Thorough** (critical-path, security-sensitive): all gates including mutation

Let the planner assign the tier as part of task breakdown based on `estimated_complexity` and risk classification.

### C3: E2E testing of xpatcher is a single line item

**Location:** Section 11, Phase 5, Step 38.

"Write integration tests on sample projects -- Test: Full pipeline passes on known repos."

This is the entire specification for E2E testing of a 16-stage, 8-agent pipeline. It tells us nothing about:
- What sample projects will be used (language diversity, size, complexity)
- What feature requests will be tested (new feature, bug fix, refactor, cross-cutting change)
- How pass/fail is determined (all stages complete? code actually works? diff is sensible?)
- How test stability is handled (LLM outputs are non-deterministic)
- How regression is detected when prompts or thresholds change
- Whether these tests run in CI or only manually

**Risk if unaddressed:** Without defined E2E tests, there is no way to know if a dispatcher change breaks the pipeline. Prompt changes, model updates, threshold adjustments, or session management changes could silently break the system.

**Recommendation:** Define at minimum:
- 3 sample projects (e.g., a small Express API, a React component library, a Python CLI tool)
- 5 canonical feature requests per project with known-good outcomes
- An E2E test harness that runs the full pipeline and validates: (a) pipeline completes without human intervention, (b) all acceptance criteria pass, (c) no regressions, (d) generated code compiles/passes lint
- A stability metric: run each canonical test 3 times, require 2/3 pass to account for LLM non-determinism
- A budget for E2E test runs (they will be expensive)

---

## Major Issues

### M1: Regression testing between pipeline stages is underspecified

**Location:** Section 6.6 (convergence criteria), Section 3.2 (stage specification).

Convergence criterion 4 states: "Pre-existing test suite passes on the task branch (no regressions)." This is checked at task completion. But consider this sequence:

1. Task A completes, all tests pass, merged to feature branch.
2. Task B completes, all tests pass *including Task A's tests*, merged.
3. Task C modifies a file that Task A also touched. Task C's tests pass, but Task A's specific acceptance criteria are now broken because the file was changed.

The spec says the pre-existing test suite is re-run at Step 12, but "pre-existing" is ambiguous. Does it mean:
- Tests that existed *before the feature branch was created*? (misses Task A's new tests)
- All tests on the feature branch at the time of Task C's completion? (correct, but only if Task A's acceptance criteria are encoded as persistent tests, not one-time verification commands)

**The gap:** Acceptance criteria verification commands (like `npm test -- --grep 'login.*valid'`) are run once at task completion. If a later task breaks that specific behavior, it will only be caught if that grep pattern happens to be part of the standard `npm test` run. If the acceptance criterion was verified by a specialized command, it may not be re-verified.

**Recommendation:** After each task merge to the feature branch, run ALL prior tasks' acceptance criteria commands, not just `npm test`. Store the full set of AC commands in the task manifest and have the dispatcher re-execute them as a regression gate. This is expensive but necessary for correctness.

### M2: The 0.8 first-pass approval threshold may be wrong in both directions

**Location:** Section 6.3, collusion_prevention.metrics.

The spec alerts if first-pass approval exceeds 0.8 (80%) over the last 20 tasks. This threshold has two problems:

**Too lenient for early pipeline maturity:** When the executor is Sonnet and the reviewer is Opus, a high approval rate could simply mean Opus catches things Sonnet would never produce (i.e., the problems Sonnet makes are so obvious that Opus always catches them, but Sonnet rarely makes them). An 80% first-pass approval could be perfectly healthy. The alert should consider the *severity* of rejected findings, not just the binary approve/reject rate.

**Too strict for mature pipelines:** As prompts are tuned and the system learns, first-pass approval rates should *increase*. A mature pipeline might legitimately achieve 90%+ first-pass approval. A fixed 0.8 threshold would create permanent false alarms.

**Recommendation:** Make the threshold dynamic:
- Weeks 1-4: alert above 0.7 (calibration period, expect more issues)
- Weeks 5-12: alert above 0.8 (standard operation)
- Ongoing: alert if first-pass approval *increases by more than 15 percentage points* over a 2-week window (sudden change is suspicious regardless of absolute level)

Also track the severity distribution of findings. If 100% of rejections are "nit" or "minor" and approvals have no missed issues (verified by spot check), the reviewer is working correctly even at 90% approval.

### M3: Simplification safety protocol has a gap with concurrent tasks

**Location:** Section 6.4.

The simplification safety protocol specifies: "Runs in isolated git worktree or branch" and "Must NOT modify test files." But the timing trigger is "After each task passes review -- files modified by that task."

If tasks A and B are executing in parallel in separate worktrees, and both complete review at approximately the same time, two simplification runs could execute concurrently on overlapping files. The spec does not address:
- Whether simplification runs are serialized (they should be)
- What happens if simplification on Task A's files conflicts with Task B's files
- Whether the simplification branch is per-task or per-feature

**Recommendation:** Serialize simplification runs. They should execute on the feature branch after task merges, never in parallel. Add a simplification queue in the dispatcher.

### M4: Missing test scenarios for dispatcher components

**Location:** Absent from spec.

The following dispatcher components have no test scenarios defined:

| Component | Critical Test Scenarios Missing |
|-----------|-------------------------------|
| State machine | Invalid transition rejection, crash-during-transition recovery, concurrent state updates |
| DAG scheduler | Diamond dependency patterns, fan-out/fan-in, single-task failure cascading to dependents, max-concurrency enforcement |
| Session registry | Session reuse after compaction, stale session detection across timezone boundaries, lineage depth overflow |
| Artifact validator | Adversarial YAML (injection attempts), extremely large outputs, partial YAML (truncated by timeout), unicode in field values |
| Context bridge | Missing artifacts (plan exists but no intent), circular version references, version number overflow |
| File polling | Rapid state changes during poll interval, file corruption, permission errors, disk full |
| Worktree management | Worktree creation failure, merge conflict during task merge, worktree cleanup after failure |

**Recommendation:** Create a test scenario matrix for each dispatcher component. These do not all need to be implemented in v1, but the scenarios should be *documented* so that implementation knows what to target. Prioritize state machine and DAG scheduler tests for Phase 1.

---

## Minor Issues

### m1: Negation check mechanics are underspecified

Section 6.2 says "temporarily invert the condition and confirm the test fails." But what does "invert the condition" mean concretely?

- For a test command like `npm test -- --grep 'login returns token'`, inverting means... what? Commenting out the implementation? Reverting the code change? Both are expensive operations.
- For a structural criterion like `npx tsc --noEmit`, negation would require *introducing* a type error, which is fragile and could break other things.

The concept is sound (proving the test actually exercises the code), but the implementation mechanics need to be specified per verification type.

**Recommendation:** Specify negation strategies per criterion type:
- `test` verification: revert the specific code change, re-run the test, confirm it fails
- `command` verification: skip negation (structural checks are inherently non-negatable)
- `browser` verification: same as `test` -- revert and re-run
- `review` verification: negation not applicable (qualitative)

### m2: LLM test auditor scope overlap with reviewer

The LLM test auditor (Section 6.2, gate 3) examines tests for: "meaningful assertions, could it pass if the feature were broken, is it deterministic." The reviewer agent (Section 6.3) checks "testability" as part of its checklist. These overlap.

If the auditor and reviewer disagree (auditor says test is weak, reviewer approves), which verdict wins? The spec does not define precedence.

**Recommendation:** Either (a) remove the LLM test auditor and fold its checklist into the tester agent's own validation, or (b) make the auditor's verdict a hard gate that runs *before* review, so the reviewer only sees tests that have already passed audit.

### m3: `should_pass` criteria lack a decision protocol

The severity level `should_pass` says "failure triggers a warning; reviewer decides whether to block." But the reviewer's output schema (Section 4.4) has verdicts `approve`, `request_changes`, and `reject`. There is no mechanism for the reviewer to say "I see the `should_pass` warning but I approve anyway." The warning would need to be surfaced to the reviewer as an input, and the reviewer would need a structured way to acknowledge it.

**Recommendation:** Add a `warnings_acknowledged` field to the ReviewOutput schema that lists `should_pass` criteria the reviewer has evaluated and decided not to block on, with a brief justification per item.

### m4: Coverage target of 80% for new code may be impractical

Section 6.2 sets `min_line_coverage_for_new_code: 80`. For generated code that includes error handling, logging, and configuration parsing, achieving 80% line coverage requires testing many mundane branches. This is especially problematic for:
- Auto-generated boilerplate (e.g., model definitions, serialization code)
- Defensive error handling that requires mocking complex failure conditions
- Configuration parsing with many optional fields

An LLM-generated test suite optimizing for 80% coverage will tend to produce low-value tests on easy-to-reach lines rather than high-value tests on tricky logic.

**Recommendation:** Use *branch* coverage rather than line coverage, and target 70% for new code. This better captures the intent of "no completely untested paths" without incentivizing coverage-padding tests.

### m5: Flaky detection at 5 runs is both too many and too few

Five runs catches flakiness with a failure rate above ~20%. But:
- Tests with a 5% flake rate (which is still bad) have a 77% chance of passing all 5 runs and being accepted.
- Five runs is expensive: for a task with 15 new tests, that is 75 test suite invocations.

**Recommendation:** Reduce to 3 runs for v1 (catches >30% flake rate, still practical). Add a "burn-in" mode for release candidates that runs 10 times, used only at the feature-completion stage (Stage 16), not per-task.

---

## Testing Feasibility Analysis

| Quality Gate | Wall-Clock Cost (per task) | LLM Cost (per task) | Value | v1 Recommendation |
|---|---|---|---|---|
| **Coverage check** | 10-30s (one instrumented run) | $0 | High -- catches completely untested code | Include always |
| **Negation check** | 2-5 min (one revert + re-run per must_pass AC) | $0 | Very high -- strongest proof that tests work | Include for `must_pass` only; skip for structural criteria |
| **LLM test auditor** | 1-2 min | $2-5 (Opus invocation) | Medium -- overlaps with reviewer and negation check | Defer to v2; fold checklist into tester agent prompt |
| **Mutation testing** | 5-30 min | $0 (tool-based) | High for critical code, overkill otherwise | Keep disabled by default; enable only for security/financial code |
| **Flaky detection** | 1.5-5 min (3-5 runs) | $0 | High -- catches non-deterministic tests | Reduce to 3 runs for v1 |

**Total overhead with recommended v1 profile:** 4-10 minutes per task, $0 in additional LLM cost.
**Total overhead with spec-as-written profile:** 6-30+ minutes per task, $2-5 in LLM cost.

The recommended v1 profile cuts overhead by approximately 60% while retaining the highest-value gates.

---

## Reviewer Isolation Assessment

The four isolation mechanisms are evaluated individually:

### Mechanism 1: Separate context (no executor reasoning visible)

**Effectiveness: High.** The session reuse matrix (Section 9, 7.8) explicitly specifies that executor-to-reviewer uses "Context bridge + fresh session" with "adversarial isolation (reviewer should NOT see executor's reasoning chain)." This is enforced at the dispatcher level, not by prompting alone.

**Potential gaming vector:** If the executor leaves comments in the code that explain its reasoning (e.g., `// I chose this approach because...`), the reviewer sees those via the diff. This is low-risk because such comments would be visible and auditable.

### Mechanism 2: Checklist-driven review

**Effectiveness: Medium-High.** The four perspectives (correctness, security, performance, style) with specific check items prevent the reviewer from doing a superficial "looks good" pass. However, checklists can become rote -- the reviewer might produce formulaic responses that check boxes without deep analysis.

**Potential gaming vector:** An LLM reviewer might develop a pattern of finding exactly one "minor" issue per review to appear diligent while still approving. The spot-check mechanism (1-in-5 tasks) partially addresses this, but the auditor's effectiveness depends on the auditor prompt quality.

### Mechanism 3: Tool-restricted (read-only)

**Effectiveness: High.** The PreToolUse hook (Section 8, 7.6) enforces this at the infrastructure level. The reviewer cannot quietly fix issues because it literally cannot write files. This is the strongest mechanism of the four.

### Mechanism 4: Adversarial framing

**Effectiveness: Medium.** The system prompt "your job is to find problems. Missing a real issue is worse than raising a false alarm" is good, but LLMs can still be overly agreeable. The effectiveness depends heavily on the specific model version and prompt refinement.

**Potential gaming vector:** Over successive iterations, if the reviewer sees that its findings are always addressed and the task eventually passes, it may "learn" (within the pipeline's memory system) that initial approval is acceptable. The memory scope `review-standards` (Section 4.4) could reinforce lenient patterns if not carefully curated.

### Overall isolation assessment

The four mechanisms are individually sound and the combination is robust. The strongest aspect is that isolation is enforced *structurally* (separate sessions, hook-enforced tool restrictions) rather than relying on prompting alone. The weakest aspect is the adversarial framing, which depends on model behavior.

**One missing mechanism:** There is no "canary bug" system. The risk mitigation section (10.1) mentions "canary bugs" as a detection method, but no implementation is specified. Periodically injecting a known bug and verifying the reviewer catches it would be a powerful calibration tool.

**Recommendation:** Add canary bug injection as a v2 feature. For v1, the 1-in-5 spot-check auditor is sufficient.

---

## Missing: xpatcher Self-Testing Strategy

The spec should define the following (currently absent):

### Unit Test Requirements

| Module | Test Focus | Minimum Scenarios |
|--------|-----------|-------------------|
| `state.py` | State machine transitions | All valid transitions, all invalid transitions, persistence round-trip, crash recovery mid-transition |
| `schemas.py` | Pydantic validation | Valid instance per schema, missing required fields, wrong types, boundary values, field validators |
| `session.py` | Claude CLI invocation | Mock subprocess: success, timeout, non-zero exit, malformed JSON envelope, empty stdout |
| `parallel.py` | Thread pool | Concurrency limit enforcement, task completion callback, exception propagation, graceful shutdown |
| `retry.py` | Backoff logic | Retry count, exponential delay, max delay cap, permanent failure detection |
| `tui.py` | Terminal rendering | Progress update, multi-task display, timer formatting (not visual tests -- output string tests) |

### Integration Test Requirements

| Scenario | What It Tests | Fixture Needed |
|----------|--------------|----------------|
| Plan-execute-review loop | Three agents coordinate via artifacts | Mock claude -p that returns canned YAML |
| Review-fix oscillation | Iteration cap and escalation | Mock reviewer that alternates verdicts |
| DAG with diamond dependency | Scheduler handles A -> B, A -> C, B+C -> D | 4 mock tasks with dependency declarations |
| Parallel worktree execution | File isolation, merge, conflict detection | Git repo with 2 tasks touching different files |
| Pipeline crash and resume | State persistence, session reuse | Simulated crash (kill dispatcher mid-pipeline) |
| Malformed output recovery | Same-session retry, escalation after max attempts | Mock agent that returns bad YAML then good YAML |

### E2E Test Requirements

| Sample Project | Feature Request | Success Criteria |
|----------------|----------------|------------------|
| Express.js REST API (50 files) | "Add rate limiting to all endpoints" | Pipeline completes, rate limiting works, existing tests pass |
| Python Flask app (30 files) | "Add user authentication with JWT" | Pipeline completes, auth endpoints work, token validation works |
| React component library (40 files) | "Add a DatePicker component" | Pipeline completes, component renders, tests pass |

**Key principle:** xpatcher's own test suite should use the same test quality standards it enforces on target projects. If xpatcher requires 80% coverage for target code, its own code should meet that bar.

---

## Missing: Test Scenarios for Dispatcher

Beyond the self-testing strategy above, the following dispatcher-specific test scenarios are absent from the spec:

### State Machine Edge Cases

1. **Concurrent state updates:** Two tasks complete simultaneously and both try to advance the pipeline state. What happens? (Need: mutex or compare-and-swap on `pipeline-state.yaml`)
2. **State file corruption:** `pipeline-state.yaml` is truncated or contains invalid YAML. (Need: backup file, validation on load, recovery from backup)
3. **Backward transitions:** Can the pipeline go from EXECUTING back to PLANNING if the human rejects the plan after execution has started? (The spec's transition table does not address this explicitly.)

### DAG Scheduler Edge Cases

4. **All-tasks-fail scenario:** Every task in a batch fails. Does the pipeline stall waiting for a SUCCEEDED dependency that will never come?
5. **Circular dependency introduced by gap detection:** Gap detection creates new tasks. If those new tasks have dependencies on each other that form a cycle, is this caught? (The DAG validator runs at Stage 10, but gap-generated tasks re-enter at Stage 6. Does validation re-run?)
6. **Single-task-multiple-dependents failure cascade:** Task A fails, Tasks B, C, D depend on A. Are B, C, D immediately marked BLOCKED, or do they wait indefinitely?

### Session Management Edge Cases

7. **Session ID reuse after Claude Code restart:** If Claude Code is restarted, are old session IDs still valid? The spec assumes `--resume` with old session IDs works indefinitely.
8. **Token estimate accuracy:** The session registry uses `token_estimate` for compaction decisions. If the estimate is wrong (which it often is), sessions could be abandoned prematurely or overflow.

---

## Gap Detection Analysis

### The 30% cap: Is it reasonable?

The `max_gap_tasks_ratio: 0.3` means gap-generated tasks cannot exceed 30% of the original task count. For a 10-task feature, that is 3 gap tasks maximum.

**Assessment: Reasonable for most cases, but the boundary behavior is undefined.**

- For a well-planned feature, 0-2 gaps is typical. 30% is generous enough.
- For a poorly-planned feature (which will happen), 30% may be too restrictive. A plan that missed a critical integration layer might need 5 gap tasks on a 10-task feature (50%).
- The `critical: { auto_approve: true }` category bypasses the cap for critical gaps, but the spec does not say whether critical gaps count toward the 30% cap.

**At exactly 30%:** What happens when the gap detector finds a 4th gap on a 10-task feature? The spec says `expected: { requires: "human_approval" }` and `enhancement: { default: "defer_to_backlog" }`. If the 4th gap is `expected` (not critical, not enhancement), is it:
- Silently deferred? (dangerous: user-visible gaps should not be silently dropped)
- Escalated to human? (correct behavior, but not specified)
- An error? (too aggressive)

**Recommendation:** Clarify that:
1. `critical` gaps never count toward the 30% cap (they are mandatory).
2. When the cap is reached for `expected` gaps, escalate to human with the full list of discovered gaps and let the human decide which to include.
3. `enhancement` gaps are always deferred regardless of cap.
4. Log a warning whenever the gap count exceeds 20% so the human is aware early.

### Scope creep prevention: Structural gap

Gap detection runs at Stage 14, after all tasks are complete. But gap tasks re-enter the pipeline at Stage 6 (Task Breakdown). This means gap tasks go through the full pipeline including *another* gap detection at Stage 14 after they complete.

**Question:** Can gap detection on gap tasks produce *more* gap tasks? The `max_gap_tasks_ratio` is defined relative to "original task count," so presumably gap-of-gap tasks would count toward the same 30% cap. But the spec does not explicitly state this, and without a depth limit, gap detection could theoretically recurse indefinitely.

**Recommendation:** Add an explicit `max_gap_detection_depth: 1` parameter. Gap tasks do not trigger another gap detection pass. If issues remain after gap tasks are complete, escalate to human.

---

## Convergence Criteria Completeness Check

The six convergence criteria (Section 6.6) are evaluated:

| # | Criterion | Complete? | Gap |
|---|-----------|-----------|-----|
| 1 | All AC test commands pass (exit 0) | Yes | None |
| 2 | Review verdict `approved` with no `major` findings | Mostly | What if verdict is `approved` but there are `major` findings in `nice_to_have`? The schema allows `findings` with any severity alongside an `approve` verdict. |
| 3 | Diff touches only declared `file_scope` | Mostly | "Justified scope expansion" is undefined. Who justifies it? The executor? The reviewer? How is the justification recorded? |
| 4 | Pre-existing test suite passes | Yes | But see M1 above: "pre-existing" needs clarification. |
| 5 | Output size within sanity bounds | Mostly | 5x and 0.1x of estimate -- but the estimate comes from the planner, which may be wildly wrong for complex tasks. A 100-line estimate for a task that legitimately requires 400 lines would fail this check. |
| 6 | Dependent interface smoke check | Partially | "Smoke check confirms dependent interface assumptions hold" -- how? What does the smoke check actually run? Who defines the interface assumptions? |

**Missing convergence criteria:**

7. **No new lint warnings.** The simplification safety protocol requires this (Section 6.4), but the task convergence criteria do not.
8. **No uncommitted changes.** After a task completes, the working tree should be clean. This is implied but not stated.
9. **Artifact validation.** The executor's output YAML must pass schema validation. This is handled by the dispatcher but not listed as a convergence criterion.

**Loophole: Criterion 5 (output size)** can be gamed. If the planner overestimates (says "medium, ~200 lines") and the executor produces 150 lines of bloated code, it passes the size check. Size is a weak proxy for quality. Consider removing this criterion and relying on the reviewer and simplifier to catch bloated code.

**Recommendation:** Add criteria 7 and 8. Remove or downgrade criterion 5 to a warning rather than a blocking check. Clarify criterion 3 by specifying that scope expansion requires reviewer approval and is recorded in the task's quality report.

---

## Questions for Product Owner

1. **What is the budget for test quality verification overhead?** If a 12-task feature takes 2 hours to execute, is an additional 1.5 hours for test quality verification (per the current spec) acceptable? Or should the target be under 30 minutes?

2. **Should xpatcher's own code have a defined coverage target before v1 ships?** The spec requires 80% coverage for target code but says nothing about its own.

3. **Is there a target for E2E test stability?** Given LLM non-determinism, what pass rate is acceptable? 80% of runs? 90%? 100% with retries?

4. **Who owns the canary bug library?** The spec mentions canary bugs as a detection mechanism (Section 10.1) but does not define who creates them, where they live, or how they are injected.

5. **Should the 30% gap cap be configurable per project?** A greenfield project might tolerate more gaps than a mature codebase.

6. **How should the spec handle target projects with no existing test suite?** The acceptance criteria template assumes `npm test` or equivalent exists. What if the target project has zero tests? Does xpatcher bootstrap a test framework, or does it require one as a precondition?

7. **What is the SLA for the LLM test auditor?** If it takes longer than the actual tests to audit them, is that acceptable?

8. **Should there be a fast path for trivial tasks?** A task that modifies one line in one file should not go through the full 5-gate test quality pipeline. Is there a "fast lane" for low-complexity tasks?

---

## Recommendations

### For v1 (must-have before implementation)

| # | Recommendation | Addresses |
|---|---------------|-----------|
| R1 | Add Section 6.8: "Testing xpatcher Itself" with unit/integration/E2E test strategy | C1, M4 |
| R2 | Define tiered quality gate profiles (lite/standard/thorough) keyed to task complexity | C2 |
| R3 | Specify E2E test plan: 3 sample projects, 5 feature requests each, pass criteria, stability metric | C3 |
| R4 | Define regression gate that re-runs ALL prior tasks' AC commands after each merge | M1 |
| R5 | Clarify 30% gap cap boundary behavior: critical exemption, escalation at cap, no recursive gap detection | Gap Detection Analysis |
| R6 | Add lint-clean and clean-worktree to convergence criteria | Convergence Check |
| R7 | Serialize simplification runs to avoid concurrent-task conflicts | M3 |

### For v2 (important but can defer)

| # | Recommendation | Addresses |
|---|---------------|-----------|
| R8 | Make collusion detection threshold dynamic with maturity-based adjustment | M2 |
| R9 | Implement canary bug injection for reviewer calibration | Isolation Assessment |
| R10 | Add LLM test auditor as a separate quality gate (currently overlaps with reviewer) | m2 |
| R11 | Implement mutation testing integration with configurable kill rate targets | Feasibility Analysis |
| R12 | Build a dispatcher test harness with mock `claude -p` for fast feedback loops | C1 |

### Design principles to add to the spec

1. **xpatcher tests itself to the same standard it enforces on target projects.** If the spec requires 80% coverage for generated code, xpatcher's own code must meet 80%.
2. **Test overhead must be proportional to task risk.** A one-line typo fix should not trigger 5x flaky detection and mutation testing.
3. **Every dispatcher component that manages state must have unit tests for its state transitions.** No exceptions.

---

*End of review.*

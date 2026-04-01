# QA Automation Engineering Review: Testing & Quality Framework

**Reviewer:** QA Automation Engineer (second-pass review)
**Date:** 2026-03-29
**Spec Version:** 1.2 (Final Draft) + Missing Components docs (13-17)
**Prior Review:** [03-quality-testing-review.md](03-quality-testing-review.md) (first-pass QA review)
**Scope:** Sections 06, 16 (Part 2: CRIT-7 resolution), 12, 02, 03, 04, 09, 10, 13, 14

---

## VERDICT: Quality Framework Ready -- with 3 Remaining Risks

The first-pass review (03-quality-testing-review.md) raised 3 critical issues (CRIT-7 no self-testing, unanalyzed overhead, E2E as one-liner) and 4 major issues. All 3 critical issues have been addressed in Section 16 and Section 6.2.1. The self-testing strategy (Section 16, Part 2) is thorough: 8 unit test suites with 90+ scenario definitions, integration tests with a mock `claude -p` harness, E2E tests across 3 sample projects, golden fixture strategy, CI configuration, and coverage targets. The tiered quality gate profiles (Lite/Standard/Thorough) in Section 6.2.1 resolve the overhead concern.

The framework is implementable. What remains are edge cases that will cause false negatives in production if not addressed, plus one structural issue in the regression testing strategy that could let regressions through silently.

---

## 1. Test Strategy for xpatcher Itself: 3-Layer Pyramid Assessment

### 1.1 Unit Tests (120-180 targets)

**Assessment: Realistic and well-scoped.**

The 8 unit test suites in Section 16 (13.2.1-13.2.8) enumerate 90+ concrete test scenarios across state machine, DAG scheduler, artifact validator, session registry, config resolution, schema round-trip, signal handler, and prompt builder. Reaching 120-180 individual test functions from 90+ scenarios is realistic when accounting for:
- Parameterized tests (the valid/invalid transition tests alone generate 25+ test IDs)
- Boundary value tests (e.g., session staleness at exactly max_age)
- Error path tests (corrupt YAML, missing files, invalid values)

The <30 second execution target is reasonable for pure-Python tests with no I/O or API calls.

**Gap identified:** No unit tests are specified for:
- `ArtifactVersioner` (Section 5.6) -- version number extraction from filenames, `next_version()` on an empty directory, `latest_version()` with non-sequential versions
- `PipelineStateFile` locking behavior (Section 2.3.2) -- the concurrent read/write test is in `test_state.py` but the `update()` method's read-modify-write atomicity is not tested in isolation
- Quality tier assignment logic -- the planner assigns tiers based on keywords and complexity, but there is no unit test for the tier resolution function itself (including path-level overrides from `.xpatcher.yaml`)

### 1.2 Integration Tests (30-50 targets)

**Assessment: Good coverage of pipeline flows, weak on inter-component boundaries.**

The 12 pipeline flow scenarios (Section 16, 13.3.1) cover the critical paths: happy path, plan rejection, max iterations, quality loop, gap re-entry, skip, cancel, resume, and oscillation detection. The mock `claude -p` infrastructure (MockClaudeSession) is well-designed.

**Gap identified:** No integration test for:
- **Regression gate between tasks.** The v1 regression protocol (Section 6.5.1) re-runs the standard test suite after each task merge. No integration test simulates Task C breaking Task A's tests.
- **Simplification failure + revert.** The simplification safety protocol (Section 6.4) promises per-commit revert on test failure. No integration test validates that a failed simplification is cleanly reverted and the quality loop proceeds.
- **Context bridge correctness.** The adversarial isolation guarantee depends on the context bridge NOT leaking executor reasoning to the reviewer. No integration test validates what data flows through the bridge.

### 1.3 E2E Tests (9-15 targets)

**Assessment: Adequate for v1, but the stability metric needs tightening.**

Three sample projects (Python Flask, TypeScript Express, Python minimal) with 2-3 scenarios each provide language diversity. The 2-of-3 stability requirement accounts for LLM non-determinism.

**Concern:** The 2-of-3 stability metric means a scenario that fails 33% of the time is considered "passing." For a pipeline that a paying user runs on production code, a 33% failure rate is unacceptable. This metric is appropriate for CI gating during development but should not be conflated with a production quality standard. The spec should distinguish between "CI gate stability" (2/3) and "production readiness target" (9/10 or better), tracked over time as prompts stabilize.

**Missing E2E scenario:** No E2E test covers a multi-task feature with dependencies. All 6 scenarios are simple (1-3 tasks). A scenario like "Add authentication with login, registration, and password reset endpoints" would exercise the DAG scheduler, regression testing between tasks, and gap detection with real LLM output.

---

## 2. Test Strategy for Target Projects: Acceptance Criteria Assessment

### 2.1 Template Completeness

The 5-category AC template (functional, structural, behavioral, qualitative, regression) is comprehensive. Every task type from the minimum requirements table (Section 6.2) maps to at least one category:

| Task Type | Categories Covered | Gap |
|-----------|-------------------|-----|
| New API endpoint | functional + structural + regression | No behavioral (no browser test) -- correct for API-only tasks |
| UI component | behavioral + structural + regression | No qualitative default -- acceptable, reviewer covers this |
| Business logic | functional + structural + regression | Coverage is complete |
| Bug fix | functional (regression test) + regression | Complete |
| Refactor | regression only | Complete (zero new tests is correct) |

**Missing task type:** Database migration / schema change. This is not in the minimum requirements table. A migration task needs:
- Structural: migration file exists and is syntactically valid
- Functional: migration runs forward and backward (rollback)
- Regression: existing queries still work after schema change
- Data integrity: existing data is not corrupted

Without this, the planner has no guidance on what ACs to generate for migration tasks. The executor will likely produce a migration with no rollback test.

### 2.2 Severity Level Definitions

The three levels (`must_pass`, `should_pass`, `nice_to_have`) are clear in definition but `should_pass` has a procedural gap identified in the first-pass review (m3): the reviewer has no structured mechanism to acknowledge and override a `should_pass` failure. The `ReviewOutput` schema lacks a `warnings_acknowledged` field.

**Impact:** When a `should_pass` criterion fails, the dispatcher surfaces a warning. The reviewer sees the warning but can only issue a binary `approve`/`request_changes`/`reject` verdict. If the reviewer approves despite the warning, there is no audit trail of the conscious override. This weakens the value of `should_pass` -- it becomes functionally identical to `nice_to_have` because there is no accountability mechanism.

**Recommendation:** Add `should_pass_overrides: list[ShouldPassOverride]` to `ReviewOutput` where each override records the criterion ID and justification. The first-pass review made this recommendation; it has not been addressed.

---

## 3. Quality Gate Tiering: Lite/Standard/Thorough

### 3.1 Boundary Clarity

The tier assignment rules are:

| Signal | Tier |
|--------|------|
| `estimated_complexity: low` + no security/data keywords | `lite` |
| `estimated_complexity: medium` OR cross-module changes | `standard` |
| `estimated_complexity: high` OR security/financial/migration keywords | `thorough` |

**Problem: "cross-module" is undefined.** What constitutes "cross-module"? Files in different directories? Files in different packages? The planner determines complexity and assigns tiers, but LLMs interpret "cross-module" inconsistently. A task that modifies `src/auth/login.ts` and `src/auth/session.ts` could be classified as single-module (both in auth) or cross-module (different concerns) depending on the LLM's interpretation.

**Problem: keyword-based tier escalation is fragile.** The spec says "security/financial/migration keywords" trigger `thorough`. But what keywords? If a task description says "add rate limiting" (a security concern), does "rate" or "limiting" trigger thorough? The keyword list is not enumerated, leaving this to LLM judgment.

**Recommendation:** Define the keyword list explicitly in `config.yaml`:
```yaml
quality_tiers:
  thorough_keywords:
    - auth, authentication, authorization, permission, role
    - encrypt, decrypt, hash, password, secret, token, credential
    - payment, billing, charge, refund, invoice, financial
    - migrate, migration, schema, alter table, data transform
    - delete, purge, remove all, drop table, truncate
```

The path-level overrides (`src/auth/**` -> thorough) are the correct escape valve. They should be documented as the primary mechanism, with keyword matching as a fallback.

### 3.2 Will the Planner Correctly Assign Tiers?

**Risk: No.** Tier assignment depends on `estimated_complexity`, which the planner estimates before seeing implementation details. Complexity estimation by LLMs is notoriously unreliable. A task that looks "low complexity" during planning (e.g., "add a config option") might require touching auth middleware, session handling, and tests -- making it `thorough`-worthy.

The human override at Stage 5 (plan approval) is the correct safety net. But the human sees the tier assignments only if the plan display format includes them. The plan approval display format is not specified in the spec -- it should show the quality tier per task alongside the task description.

---

## 4. Mutation Testing: 70% Kill Rate for LLM-Generated Tests

### Assessment: Unrealistic for v1, appropriate as a v2 aspiration.

The spec marks mutation testing as optional with a 60% minimum kill rate in the YAML config (Section 6.2.1, `thorough` tier) and a 70% target in Appendix B KPIs. This discrepancy (60% vs 70%) should be reconciled.

**Why 70% is unrealistic for LLM-generated tests:**

1. **LLM-generated tests tend toward happy-path assertions.** An LLM writing tests for a login endpoint will test "valid credentials return 200" and "missing password returns 400" but will miss boundary conditions (password exactly at max length, Unicode in email, concurrent login attempts). Mutation testing catches these gaps, but hitting 70% kill rate requires the kind of adversarial test thinking that LLMs currently struggle with.

2. **Mutation testing tool overhead.** For Python (`mutmut`), a codebase with 500 lines of new code generates 200-400 mutants. At 1-2 seconds per mutant (running the test suite), that is 5-13 minutes per task. For TypeScript (`stryker`), similar overhead. This fits within the `thorough` tier's 15-30 minute budget, but barely.

3. **False survivors.** Equivalent mutants (mutations that produce functionally identical code) cannot be killed by any test. In typical codebases, 10-20% of mutants are equivalent, meaning the maximum achievable kill rate is 80-90%. A 70% target requires killing ~85% of non-equivalent mutants -- achievable by human-written tests but aggressive for LLM-generated ones.

**Recommendation:** Set the v1 config value to `enabled: false` across all tiers (already the case). For v2, start with a 50% kill rate target and increase based on observed data. Reconcile the 60%/70% discrepancy -- use 60% in the config and 70% as the aspirational KPI.

---

## 5. Flaky Test Detection: 5 Runs Analysis

### Will 5 runs catch timing-dependent flakes?

**Partially.** The math:

| Flake Rate | P(all 5 pass) | P(all 3 pass) | Detection at 5 runs | Detection at 3 runs |
|-----------|---------------|---------------|---------------------|--------------------|
| 50% | 3.1% | 12.5% | 96.9% | 87.5% |
| 20% | 32.8% | 51.2% | 67.2% | 48.8% |
| 10% | 59.0% | 72.9% | 41.0% | 27.1% |
| 5% | 77.4% | 85.7% | 22.6% | 14.3% |
| 1% | 95.1% | 97.0% | 4.9% | 3.0% |

A test with a 10% flake rate -- which is a serious problem in production CI -- has a 59% chance of passing all 5 runs and being accepted. The tiered approach (3 runs for `standard`, 5 for `thorough`) is a reasonable cost/detection tradeoff, but neither setting catches low-frequency flakes.

### What about environment-dependent flakes?

**Not covered.** The spec runs all 5 repetitions in the same environment (same machine, same working directory, same time). This catches:
- Race conditions with high frequency (>20%)
- Non-deterministic ordering (set iteration, dict ordering in Python)

This does NOT catch:
- **Timezone-dependent failures** (tests that assume UTC but run in local timezone)
- **Locale-dependent failures** (string sorting, date formatting)
- **Port conflicts** (test binds to port 3000, another process already has it)
- **File system case sensitivity** (macOS case-insensitive, Linux case-sensitive)
- **PATH-dependent tool resolution** (test calls `python` which resolves differently)

These environment-dependent flakes are the hardest to catch and the most damaging in CI. The spec has no strategy for them.

**Recommendation:** For v1, accept that 3-5 run detection is sufficient for high-frequency flakes. Add to the E2E test suite a requirement that sample project tests run on both macOS and Ubuntu in CI (Section 16, 13.6 CI config runs only on `ubuntu-latest`). This catches the most common environment-dependent issues (case sensitivity, path resolution) without adding per-task overhead.

---

## 6. Negation Check: Non-Boolean Acceptance Criteria

### How does negation work in practice?

The spec (Section 6.2) says: "temporarily invert the condition and confirm the test fails." The first-pass review (m1) noted this is underspecified. The Section 6.2 YAML shows `negation_check: { enabled: true, applies_to: "must_pass" }` but does not define the negation strategy per verification type.

**Concrete analysis of each AC type:**

| Verification Type | Negation Strategy | Feasibility | Risk |
|-------------------|-------------------|-------------|------|
| `test` (e.g., `npm test -- --grep 'login returns token'`) | Revert the code change, re-run the test, confirm it fails | **Feasible** but expensive: requires a `git stash` + test run + `git stash pop`. If the test was already present before the change, this proves nothing. | Medium: stash/unstash can fail if there are other uncommitted changes |
| `command` (e.g., `npx tsc --noEmit`) | Introduce a deliberate type error | **Fragile**: where to introduce the error? The wrong location breaks unrelated code. The right location requires understanding the type graph. | High: automated error injection is unreliable |
| `browser` (e.g., Playwright spec) | Same as `test` -- revert and re-run | **Feasible** but slow: browser tests take 5-30 seconds each | Low |
| `review` (qualitative checklist) | Not applicable | N/A | N/A |

**The fundamental problem:** Negation checks verify that the test fails when the feature is absent. But "absent" is ambiguous. For a new feature added to an existing file, does "absent" mean:
1. Revert just the lines added by this task? (requires diff-level precision)
2. Revert the entire file to pre-task state? (may break other tasks' changes)
3. Comment out the new function? (fragile, language-dependent)

The spec does not resolve this ambiguity.

**Recommendation for v1:** Implement negation checks ONLY for `test` verification type using strategy: save the current state, revert the task's commits (`git stash` or `git diff > patch && git checkout -- .`), run the specific test command, confirm it fails (non-zero exit), restore the state. Skip negation for `command`, `browser`, and `review` types. Document the limitation.

---

## 7. Regression Testing Between Tasks: Edge Case Analysis

### v1: Standard Test Suite Regression

After each task completes and commits, the dispatcher re-runs the project's standard test suite (`npm test`, `pytest`, etc.). This catches regressions in code paths covered by existing tests.

**Edge cases where regressions slip through in v1:**

| Scenario | Why v1 Misses It | Severity |
|----------|-----------------|----------|
| Task A adds a new test for behavior X. Task C breaks behavior X but the test for X is behind a specific grep pattern (`npm test -- --grep 'session valid'`) not in the default `npm test` run. | v1 runs only the standard suite, not per-task AC commands | **High** -- this is the exact scenario the v2 AC regression was designed for |
| Task A adds an API endpoint. Task C changes the response format. Task A's test checks for the old format but is in a test file that only runs during `npm test` if a specific env var is set. | Conditional test execution based on env vars is not handled | **Medium** -- uncommon but possible |
| Task A modifies shared utility `utils.ts`. Task B also modifies `utils.ts`. Task B's changes conflict semantically (not textually) with Task A's usage. | v1 sequential execution prevents textual conflicts but not semantic ones. The standard test suite catches this ONLY if a test exercises both code paths through `utils.ts`. | **Medium** -- depends on test coverage of shared utilities |
| Task A adds a feature behind a feature flag (off by default). Task C removes the feature flag infrastructure. Task A's feature is now unreachable but no test fails because the flag was off. | Dead code introduced by cross-task interaction is not detectable by any test | **Low** -- unlikely but illustrative |

### v2: Full AC Regression

v2 re-runs all prior tasks' AC commands whose `files_in_scope` overlap with the current task's changed files. This narrows the gap substantially but has its own edge cases:

**Edge case: Transitive dependency not captured by file overlap.**
- Task A modifies `auth/session.ts` and its AC tests session validity.
- Task B modifies `api/router.ts` (no overlap with Task A's files).
- Task C modifies `auth/middleware.ts` which imports from both `session.ts` and `router.ts`.
- Task C's changes to `middleware.ts` break session validity, but Task A's `files_in_scope` is `[auth/session.ts]`, not `[auth/middleware.ts]`.
- v2's overlap check (`current_files & set(task.files_in_scope)`) misses this because `middleware.ts` is not in Task A's scope.

**Fix:** The overlap check should be bidirectional: also check if any of the current task's `files_in_scope` import from or are imported by any of the prior task's files. This requires import graph analysis, which is expensive but possible via `Grep` for `import` statements.

---

## 8. DRY-RUN: Test Failure Scenario Walkthrough

### Scenario

**Feature:** "Add user authentication to the REST API"
**Tasks:** 3 tasks decomposed by planner:
- task-001: Create user model and registration endpoint (files: `src/models/user.ts`, `src/routes/register.ts`)
- task-002: Create login endpoint with JWT token generation (files: `src/routes/login.ts`, `src/auth/jwt.ts`)
- task-003: Add auth middleware to protected routes (files: `src/middleware/auth.ts`, `src/routes/protected.ts`)

**The failure:** task-003's executor modifies `src/middleware/auth.ts` to validate JWT tokens. The implementation has a subtle bug: it uses `jwt.decode()` instead of `jwt.verify()`, which accepts any token without validating the signature. task-003's own tests pass because the test uses tokens generated by the same secret key. However, task-002's acceptance criterion "invalid token returns 401" now behaves differently because the middleware accepts the malformed token.

### Trace Through the State Machine

**Step 1: task-001 executes (Stage 11)**
- Executor creates `user.ts` and `register.ts`
- Commits to feature branch
- State: task-001 transitions RUNNING -> PER_TASK_QUALITY

**Step 2: task-001 quality loop (Stage 12)**
- Tester runs ACs: `npm test -- --grep 'registration'` -- PASS
- Reviewer checks code -- APPROVE
- Regression: `npm test` full suite -- PASS (no prior tests to break)
- State: task-001 transitions to SUCCEEDED

**Step 3: task-002 executes and passes quality loop**
- Login endpoint works, JWT tests pass
- ACs include: `npm test -- --grep 'invalid token returns 401'` -- PASS (no middleware yet)
- Full `npm test` -- PASS
- State: task-002 transitions to SUCCEEDED

**Step 4: task-003 executes (Stage 11)**
- Executor creates `auth.ts` middleware with `jwt.decode()` bug
- Executor writes tests for middleware: `npm test -- --grep 'auth middleware'` -- PASS (tests use valid tokens)
- Commits to feature branch

**Step 5: task-003 quality loop (Stage 12)**
- Tester runs ACs: `npm test -- --grep 'auth middleware'` -- PASS
- Reviewer examines code...

**KEY DECISION POINT: Does the reviewer catch `jwt.decode()` vs `jwt.verify()`?**

The reviewer's security checklist includes "auth enforced on endpoints." If the reviewer is Opus and the security checklist is specific enough, it MAY catch this. But `jwt.decode()` is a legitimate API that appears in many valid use cases. The reviewer would need to know that `jwt.decode()` does not verify signatures -- this is JWT domain knowledge, not a generic security pattern.

**Probability of reviewer catching this: ~50%.** Opus has strong security knowledge but `jwt.decode()` is a subtle footgun, not an obvious vulnerability like SQL injection.

**Assume reviewer MISSES it. task-003 passes review.**

**Step 6: Regression gate (Stage 12, post-approval)**

v1 behavior: Dispatcher runs `npm test` (full standard suite).

**Does this catch the bug?** It depends on whether task-002's "invalid token returns 401" test is in the standard suite or behind a grep filter.

- If task-002's test is a standard test (runs with `npm test`): The test STILL PASSES because task-002's test hits the `/login` endpoint directly, not through the middleware. The middleware is only applied to `/protected` routes. The "invalid token returns 401" test sends an invalid token to `/login`, which has its own validation, not the middleware.
- If task-002's test sends an invalid token to a `/protected` route: The middleware's `jwt.decode()` ACCEPTS the token (because decode does not verify), so the test FAILS -- regression caught!

**This is the critical ambiguity.** Whether the regression is caught depends entirely on how task-002's test is written. If the test targets the endpoint that uses the middleware, it is caught. If the test targets the login endpoint directly (which has its own auth logic), it is missed.

**Step 7 (v1, bug NOT caught): task-003 transitions to SUCCEEDED**

The bug ships. The middleware accepts any JWT token, including expired or forged ones. The gap detector (Stage 14) MIGHT catch this if its "error handling" dimension surfaces that JWT validation is incomplete, but gap detection analyzes structural completeness, not implementation correctness at the line level.

**Step 7 (v2, AC regression): Dispatcher runs overlapping AC commands**

v2 checks: task-003 changed `src/middleware/auth.ts`. task-002's `files_in_scope` is `[src/routes/login.ts, src/auth/jwt.ts]`. There is NO overlap between task-003's changed files and task-002's scope.

**v2 also MISSES this bug** because the file overlap check fails to connect `middleware/auth.ts` to `auth/jwt.ts` despite the import relationship.

### Recovery Path (if bug is caught at any point)

If the regression gate catches the failure at Step 6:

1. task-003 transitions RUNNING -> NEEDS_FIX (quality iteration 1)
2. Dispatcher provides the executor with:
   - The failing test output: `"expected 401 but got 200 for invalid token"`
   - The specific AC command that failed
   - The files changed by task-003
3. Executor receives fix iteration prompt (Stage 13)
4. Executor reads the error message, identifies `jwt.decode()` should be `jwt.verify()`
5. Executor makes the fix, commits
6. task-003 transitions NEEDS_FIX -> RUNNING (re-enters quality loop)
7. Tester re-runs all ACs -- PASS
8. Reviewer re-reviews -- APPROVE
9. Regression gate re-runs `npm test` -- PASS
10. task-003 transitions to SUCCEEDED

**Recovery probability: HIGH** once the failure is surfaced. The error message "expected 401 but got 200" is clear enough for any LLM to diagnose. The fix is a single token change (`decode` -> `verify`). This would likely resolve in 1 fix iteration.

### Lessons from the DRY-RUN

1. **The v1 regression gate works well for bugs caught by the standard test suite** but silently passes bugs that require cross-task test specificity.
2. **The v2 file-overlap heuristic misses transitive dependencies** through imports. A file that was not in any prior task's scope can still break prior task behavior if it intercepts the call chain.
3. **The reviewer is the strongest defense** against this class of bug, but it requires domain-specific security knowledge (JWT decode vs verify).
4. **The gap detector is the last safety net** but operates at a structural level, not an implementation-correctness level.

---

## 9. Coverage Gaps: Scenarios with NO Test Coverage Specified

### 9.1 Target Project Testing Gaps

| Scenario | Where It Should Be Covered | Why It Is Missing |
|----------|---------------------------|-------------------|
| **Database migration tasks** | Section 6.2 minimum requirements table | Table lists 5 task types; migrations are not one of them |
| **Performance regression** | Section 6.6 convergence criteria | No performance benchmark gate. A task could introduce an O(n^2) algorithm that passes all functional tests |
| **Accessibility (a11y)** | Section 6.2, behavioral verification | Browser tests verify render + interaction but do not check WCAG compliance, screen reader compatibility, or keyboard navigation |
| **Concurrent request handling** | Section 6.2, functional tests | The AC template has no concurrency test category. Race conditions in API endpoints are a common gap |
| **Backward compatibility of APIs** | Section 6.5 gap detection | Gap detector checks for "missing consumers" but not for breaking changes to existing API contracts |
| **Configuration validation** | Section 6.2, structural | No AC type for "new config option has a default value and validation" |

### 9.2 xpatcher Self-Testing Gaps

| Scenario | Where It Should Be Covered | Why It Is Missing |
|----------|---------------------------|-------------------|
| **Quality tier assignment logic** | Section 16 unit tests | No test for the function that maps `estimated_complexity` + keywords to a tier |
| **Negation check implementation** | Section 16 integration tests | The negation check is specified in Section 6.2 but has no test in the self-testing strategy |
| **Flaky detection loop** | Section 16 integration tests | The 3/5-run flaky detection is specified but not tested |
| **ArtifactVersioner** | Section 16 unit tests | Version numbering logic has no test suite |
| **Path-level quality tier overrides** | Section 16 unit tests | `.xpatcher.yaml` path glob matching has no test for complex patterns (e.g., `src/**/auth/**` vs `src/auth/**`) |
| **Model alias resolution** | Section 16 unit tests | The `opus` -> `claude-opus-4-6` resolution logic (Section 2.2.1) has no test |
| **Worktree cleanup after failure** | Section 16 integration tests | v2 worktree management has no cleanup test for the failure case (worktree preserved for debugging) |
| **Gap re-entry depth limit** | Section 16 integration tests | `max_gap_depth: 2` enforcement is specified (Section 3.4.1) but no integration test exercises the depth limit |

### 9.3 Cross-Cutting Gaps

| Scenario | Impact |
|----------|--------|
| **No chaos/fault-injection testing** | The spec has circuit breakers (Section 10) but no test that verifies they activate correctly. Monthly manual testing (Appendix C) is mentioned but not specified. |
| **No contract testing between agents** | Agent A produces YAML that agent B consumes. If agent A's output format drifts (e.g., model update changes formatting), agent B may fail to parse it. No test validates the agent-to-agent contract end-to-end. The golden fixtures (Section 16, 13.5) partially address this but are static -- they do not capture model version drift. |
| **No test for human gate timeout behavior** | The 2-hour soft timeout (Section 3.5) pauses the pipeline, but no test verifies that state is correctly saved and the gate is re-displayed on resume. |
| **No test for the 30% gap cap** | The `max_gap_tasks_ratio: 0.3` (Section 6.5) has defined boundary behavior but no integration test verifies the cap is enforced or that escalation occurs when exceeded. |

---

## Summary of Findings

### CRITICAL Gaps (will cause production failures if not addressed)

| # | Gap | Location | Impact |
|---|-----|----------|--------|
| C1 | **v1 and v2 regression testing both miss transitive import-chain regressions.** v1 relies on the standard test suite which may not exercise cross-task interactions. v2's file-overlap heuristic does not follow import chains. | Section 6.5.1 | A task can break another task's behavior without any test catching it, if the breakage flows through an import chain not in either task's `files_in_scope`. |
| C2 | **Negation check implementation is undefined for non-`test` verification types.** The spec says "invert the condition" but provides no strategy for `command` or `browser` types. | Section 6.2 | Either these types are silently skipped (reducing the gate's value) or an implementor invents a fragile strategy that introduces false failures. |
| C3 | **`should_pass` severity has no audit mechanism.** Reviewer can override a `should_pass` failure by approving, but the override is not recorded or justified. | Section 6.1, ReviewOutput schema | `should_pass` becomes functionally identical to `nice_to_have`, eliminating a designed quality signal. |

### MAJOR Risks (will cause false positives/negatives)

| # | Risk | Type | Impact |
|---|------|------|--------|
| M1 | **Quality tier assignment depends on LLM keyword interpretation.** No enumerated keyword list, no unit test for tier resolution. | False negative | A security-critical task assigned `lite` tier skips negation checks and flaky detection. |
| M2 | **5-run flaky detection misses tests with <20% flake rate.** Environment-dependent flakes (timezone, locale, port conflicts) are not tested at all. | False negative | Flaky tests enter the project's test suite and cause spurious failures in future tasks. |
| M3 | **E2E stability metric (2/3) is too lenient for production confidence.** | False positive | A scenario with 33% failure rate is classified as "passing." |
| M4 | **Mutation testing target discrepancy: 60% in config vs 70% in KPIs.** | Confusion | Implementors may target different thresholds depending on which document they reference. |
| M5 | **No E2E test for a multi-task feature with task dependencies.** All 6 E2E scenarios are simple 1-3 task features. | False positive | The DAG scheduler, regression gate, and gap detection are never exercised with real LLM output in a complex scenario. |

### TESTING EDGE CASES Not Covered

1. **Task modifies a file not in its declared `file_scope`.** The convergence criterion (6.6, item 3) says this requires "justified scope expansion" but the justification mechanism is undefined. Who approves? How is it recorded?

2. **Two tasks legitimately need to modify the same file.** In v1 (sequential), the second task sees the first task's changes. But the planner assigned `files_in_scope` based on pre-execution state. If task-002's `files_in_scope` includes `config.ts` and task-001 already modified `config.ts`, does the regression check use task-001's scope or the original file?

3. **Gap detection creates a task that depends on a completed task's output, but that output has been modified by a later task.** Gap tasks re-enter at Stage 6 and get the latest codebase state, but the gap report was generated against an earlier state. The gap task's assumptions may be stale.

4. **A test passes during quality loop but fails during the next task's regression gate due to test pollution** (global state, database state left over from prior test). The dispatcher re-runs the standard suite between tasks but does not reset test environment state.

5. **The simplifier removes code that task-003 depends on.** Simplification runs after task-002 passes quality. If the simplifier removes a "dead" function from task-002 that task-003 plans to use, task-003 will fail to compile. The spec says simplification is post-approval, so task-003's planner saw the function. But after simplification, it is gone.

6. **Context window exhaustion during a negation check.** The negation check reverts code, re-runs tests, and restores. If this happens inside a session that is already at 80% context utilization, the revert/restore cycle may push the session over the limit.

---

## Recommendations

### For v1 (before implementation)

| # | Action | Addresses |
|---|--------|-----------|
| R1 | Define negation check as `test`-type only for v1. Explicitly skip for `command` and `browser`. Document the limitation. | C2 |
| R2 | Add `should_pass_overrides` field to `ReviewOutput` schema in Section 9. | C3 |
| R3 | Add an enumerated `thorough_keywords` list to the quality tier config. Add a unit test for tier resolution. | M1 |
| R4 | Add macOS runner to E2E CI config (currently ubuntu-only). | M2 |
| R5 | Reconcile mutation testing target: use 60% in config, note 70% as aspirational in Appendix B. | M4 |
| R6 | Add at least one multi-task E2E scenario (4+ tasks with dependencies). | M5 |
| R7 | Add unit tests for `ArtifactVersioner`, quality tier assignment, and model alias resolution. | Section 9.2 gaps |
| R8 | Add integration test for gap re-entry depth limit enforcement. | Section 9.2 gaps |

### For v2 (important but deferrable)

| # | Action | Addresses |
|---|--------|-----------|
| R9 | Enhance v2 regression to follow import chains (import graph analysis) rather than relying solely on `files_in_scope` overlap. | C1 |
| R10 | Add environment matrix to flaky detection (run on both macOS and Linux during E2E). | M2 |
| R11 | Implement a production stability metric (9/10 passes) separate from the CI gate metric (2/3). | M3 |
| R12 | Add contract testing between agents: generate fresh output from each agent, validate it parses correctly as input to the next agent in the pipeline. | Section 9.3 gaps |

---

*End of QA Automation Engineering review.*

# 03 - Quality, Testing, and Review in Agent-Orchestrated SDLC

> Analysis document for the xpatcher agent orchestration layer.
> Covers acceptance criteria, testing strategy, review agent design, code simplification, gap detection, and regression prevention.

---

## Table of Contents

1. [Acceptance Criteria Definition](#1-acceptance-criteria-definition)
2. [Testing Strategy for Agent-Generated Code](#2-testing-strategy-for-agent-generated-code)
3. [Review Agent Design](#3-review-agent-design)
4. [Code Simplification Process](#4-code-simplification-process)
5. [Gap Detection and Completeness Verification](#5-gap-detection-and-completeness-verification)
6. [Regression Prevention](#6-regression-prevention)

---

## 1. Acceptance Criteria Definition

### 1.1 The Core Problem

Agents declare victory prematurely. Anthropic's own research confirms this: "Claude failed to recognize incomplete features until explicitly prompted to use browser automation." Vague acceptance criteria ("make it work") give the agent room to rationalize partial implementations as complete. The criteria must be concrete enough that a second, adversarial agent can mechanically verify every claim without exercising judgment.

### 1.2 Machine-Verifiable vs Human-Judgment Criteria

Every acceptance criterion falls on a spectrum from fully automatable to irreducibly subjective. The pipeline must handle both, but should maximize the automatable portion.

| Category | Verification Method | Examples |
|---|---|---|
| **Fully automatable** | Exit code, assertion, deterministic check | "Function returns 200 for valid input", "No TypeScript errors", "Bundle size < 500KB" |
| **Automatable with heuristics** | LLM-as-judge, structural analysis | "Error messages are user-friendly", "Code follows repository naming conventions" |
| **Requires human judgment** | Flagged for human review | "The UX feels intuitive", "The architecture will scale to 10x load" |

**Rule: every task MUST have at least one fully automatable criterion.** If the task is purely subjective (e.g., "improve the design"), the task definition is incomplete and must be decomposed until automatable criteria emerge.

### 1.3 Granularity: The Brittleness-Vagueness Tradeoff

Too specific:
```yaml
# BAD - brittle, tests implementation not behavior
criteria:
  - "Line 47 of auth.ts contains: const token = jwt.sign(payload, secret)"
  - "The function has exactly 3 parameters"
```

Too vague:
```yaml
# BAD - agent will rationalize anything as meeting this
criteria:
  - "Authentication works"
  - "Code is clean"
```

Correct granularity:
```yaml
# GOOD - tests observable behavior, specific enough to verify
criteria:
  - "POST /auth/login with valid credentials returns 200 and a JSON body containing a non-empty 'token' field"
  - "POST /auth/login with invalid credentials returns 401 and does not contain a 'token' field"
  - "The token, when decoded, contains 'userId' and 'exp' claims"
  - "Token expiry is between 1 hour and 24 hours from issuance"
```

**The principle: specify WHAT is observable, not HOW it is implemented.** Criteria should survive a complete rewrite of the internals.

### 1.4 Acceptance Criteria YAML Template

```yaml
task:
  id: "auth-login-endpoint"
  intent: "Users need to authenticate with email/password and receive a JWT"
  goal: "Implement POST /auth/login that validates credentials and returns a signed JWT"

  approach:
    description: "Add login route, validate against user store, sign JWT with configured secret"
    constraints:
      - "Must use existing User model, not create a new one"
      - "Must not store plaintext passwords"
      - "Must not introduce new dependencies without justification"

  acceptance_criteria:
    # --- Functional: verified by running tests ---
    functional:
      - id: "ac-01"
        description: "Valid login returns token"
        verification: "test"
        test_command: "npm test -- --grep 'login.*valid.*returns.*token'"
        severity: "must_pass"

      - id: "ac-02"
        description: "Invalid login returns 401"
        verification: "test"
        test_command: "npm test -- --grep 'login.*invalid.*401'"
        severity: "must_pass"

      - id: "ac-03"
        description: "Missing fields return 400 with field-level errors"
        verification: "test"
        test_command: "npm test -- --grep 'login.*missing.*400'"
        severity: "must_pass"

    # --- Structural: verified by static analysis / linting ---
    structural:
      - id: "ac-04"
        description: "No TypeScript compilation errors"
        verification: "command"
        command: "npx tsc --noEmit"
        expected_exit_code: 0
        severity: "must_pass"

      - id: "ac-05"
        description: "No new lint violations introduced"
        verification: "command"
        command: "npx eslint src/auth/ --max-warnings=0"
        expected_exit_code: 0
        severity: "must_pass"

    # --- Behavioral: verified by e2e or browser automation ---
    behavioral:
      - id: "ac-06"
        description: "Login flow works end-to-end through the UI"
        verification: "browser"
        script: "e2e/auth-login.spec.ts"
        severity: "must_pass"

    # --- Qualitative: verified by reviewer agent ---
    qualitative:
      - id: "ac-07"
        description: "Error messages do not leak internal details (stack traces, DB schema)"
        verification: "review"
        reviewer_checklist:
          - "No stack traces in 4xx/5xx response bodies"
          - "No database column names in error messages"
          - "No file paths in error messages"
        severity: "must_pass"

      - id: "ac-08"
        description: "Password handling follows security best practices"
        verification: "review"
        reviewer_checklist:
          - "Passwords are hashed with bcrypt/scrypt/argon2, not MD5/SHA"
          - "Plaintext passwords are not logged"
          - "Timing-safe comparison is used"
        severity: "must_pass"

    # --- Regression: existing tests still pass ---
    regression:
      - id: "ac-09"
        description: "All pre-existing tests still pass"
        verification: "command"
        command: "npm test"
        expected_exit_code: 0
        severity: "must_pass"

  completion_gate:
    all_must_pass: true
    # If any must_pass criterion fails, the task is NOT complete.
    # The orchestrator must feed failures back to the executor agent.
    max_retry_cycles: 3
    on_max_retries_exceeded: "escalate_to_human"
```

### 1.5 Severity Levels and Completion Gates

- **`must_pass`**: Task cannot be marked complete if this fails. No exceptions.
- **`should_pass`**: Failure triggers a warning. The reviewer agent decides whether to block.
- **`nice_to_have`**: Logged but never blocks completion.

The `completion_gate` is evaluated by the orchestrator, not the executor agent. The executor never self-certifies.

---

## 2. Testing Strategy for Agent-Generated Code

### 2.1 The Test Pyramid for Agent-Generated Code

Agents should generate tests at all three layers, but the distribution and purpose differ from human-written projects:

```
         /  E2E / Browser  \        <- Agent MUST generate for UI tasks
        / Integration Tests \       <- Agent MUST generate for API/data tasks
       /    Unit Tests       \      <- Agent SHOULD generate for logic-heavy code
      /  Static Analysis      \     <- Orchestrator runs automatically, always
     /_________________________\
```

**Why the emphasis differs from traditional projects:** Agents are prone to generating code that "looks right" but has subtle wiring errors. A unit test can pass on a function in isolation while the function is never actually called from the right place. Integration and e2e tests catch wiring failures that unit tests miss.

**Minimum test requirements by task type:**

| Task Type | Required Tests |
|---|---|
| New API endpoint | Integration test hitting the endpoint, at least 3 cases (happy path, validation error, auth error) |
| UI component | Browser test verifying render + interaction |
| Business logic | Unit tests covering branches, plus one integration test proving it's wired in |
| Bug fix | Regression test that fails on the old code and passes on the fix |
| Refactor | Zero new tests required, but all existing tests must pass |

### 2.2 Verifying Test Quality (Testing the Tests)

Agent-generated tests have a specific failure mode: they can be tautological. The test "passes" but verifies nothing meaningful. Examples of tautological tests:

```typescript
// BAD: tests that the mock returns what you told it to return
test("getUser returns user", () => {
  const mockDb = { findUser: jest.fn().mockReturnValue({ id: 1, name: "Alice" }) };
  const result = getUser(mockDb, 1);
  expect(result).toEqual({ id: 1, name: "Alice" }); // This tests jest.fn(), not getUser
});

// BAD: tests that the function exists, not that it works
test("login endpoint exists", () => {
  expect(typeof loginHandler).toBe("function");
});

// BAD: snapshot test on agent-generated code (the snapshot IS the bug)
test("renders correctly", () => {
  const tree = render(<LoginForm />);
  expect(tree).toMatchSnapshot(); // Snapshot was generated from possibly-wrong code
});
```

**Test quality verification pipeline:**

1. **Mutation testing** (where feasible): Run a mutation testing tool (Stryker, mutmut, cargo-mutants). If mutating the source code does not cause test failures, the tests are weak. This is computationally expensive; use it selectively on critical paths.

2. **Structural coverage check**: Run coverage analysis. Not as a vanity metric, but to identify completely untested branches. Zero coverage on an error-handling path means the agent skipped the unhappy path.

3. **LLM test auditor**: A separate reviewer subagent (read-only, no write access) examines each test and answers:
   - Does this test make at least one meaningful assertion about behavior?
   - Could this test pass even if the feature were completely broken?
   - Does the test depend on implementation details that would break on valid refactoring?
   - Is the test deterministic (no reliance on wall-clock time, random values, network)?

4. **Negation check**: For critical acceptance criteria, temporarily invert the condition (e.g., return 200 instead of 401 for invalid credentials) and confirm the corresponding test fails. This is the strongest guarantee and should be used for `must_pass` criteria.

```yaml
test_quality_gates:
  coverage:
    enabled: true
    min_line_coverage_for_new_code: 80
    command: "npm test -- --coverage --coverageReporters=json"
    parse_output: "coverage/coverage-summary.json"

  mutation:
    enabled: false  # Enable for critical-path tasks only
    command: "npx stryker run --mutate 'src/auth/**/*.ts'"
    min_mutation_score: 60

  negation_check:
    enabled: true
    # For each must_pass acceptance criterion with a test,
    # the orchestrator will:
    # 1. Apply a known-breaking patch to the relevant code
    # 2. Run the test
    # 3. Confirm the test FAILS
    # 4. Revert the patch
    applies_to: "must_pass"

  llm_audit:
    enabled: true
    reviewer: "test-quality-auditor"
    tool_restrictions: ["Read", "Grep", "Glob"]  # read-only
```

### 2.3 Handling Flaky Tests Generated by Agents

Agent-generated tests become flaky for predictable reasons:

| Flakiness Source | Detection | Mitigation |
|---|---|---|
| Timing dependencies (`setTimeout`, `Date.now()`) | Grep for time-related APIs in test files | Require agents to use fake timers; lint rule banning real timers in tests |
| Port conflicts | Test binds to hardcoded port | Require dynamic port allocation in test helpers |
| Order dependence | Run tests in shuffled order | Each test must set up and tear down its own state |
| Network calls | Test hits real external service | Mock/intercept all external HTTP in tests; lint rule banning real `fetch` in test files |
| Race conditions in async code | Non-deterministic pass/fail on repeated runs | Run each new test 5 times on creation; if any run fails, reject |

**Flaky test protocol:**

```yaml
flaky_test_detection:
  on_new_test:
    # Run each new test N times in isolation to catch non-determinism
    repetitions: 5
    must_pass_all: true
    on_failure: "reject_test_and_retry_generation"

  on_existing_test_failure:
    # If a previously-passing test fails, distinguish between:
    # 1. Legitimate regression (code broke it)
    # 2. Flaky test (test was always unreliable)
    steps:
      - rerun_count: 3
      - if_intermittent: "quarantine_test_and_log_warning"
      - if_consistent_failure: "treat_as_regression"

  quarantine:
    # Quarantined tests are tracked but do not block the pipeline
    max_quarantine_duration: "48h"
    resolution: "dedicated_fix_task_generated"
```

### 2.4 Browser/UI Verification Patterns

For tasks that produce user-facing output, text-based test assertions are insufficient. The pipeline should use browser automation (Playwright, Puppeteer) to verify:

1. **Render verification**: The page loads without console errors, the expected elements are present.
2. **Interaction verification**: Clicking buttons, filling forms, and navigating produces the correct state transitions.
3. **Visual regression** (optional): Screenshot comparison against a baseline, useful for layout-sensitive work.

```yaml
browser_verification:
  framework: "playwright"

  patterns:
    smoke_test:
      description: "Page loads and key elements are present"
      template: |
        test('page loads without errors', async ({ page }) => {
          const errors: string[] = [];
          page.on('console', msg => { if (msg.type() === 'error') errors.push(msg.text()); });
          await page.goto(URL);
          await expect(page.locator('[data-testid="main-content"]')).toBeVisible();
          expect(errors).toHaveLength(0);
        });

    form_interaction:
      description: "Form submission with validation"
      template: |
        test('login form submits and redirects', async ({ page }) => {
          await page.goto('/login');
          await page.fill('[name="email"]', 'test@example.com');
          await page.fill('[name="password"]', 'validpassword');
          await page.click('[type="submit"]');
          await expect(page).toHaveURL('/dashboard');
        });

    visual_regression:
      description: "Screenshot comparison"
      enabled: false  # Enable per-task when layout matters
      threshold: 0.01  # 1% pixel difference tolerance
```

---

## 3. Review Agent Design

### 3.1 What Makes an Effective Critic Agent

An effective reviewer agent is structurally prevented from being lenient. Leniency emerges when:

- The reviewer has no checklist (it "vibes" the code)
- The reviewer and executor share the same system prompt or context window
- The reviewer can see the executor's reasoning and sympathizes with tradeoffs
- The reviewer has no consequence for missed issues

**Design principles for the reviewer:**

1. **Isolation**: The reviewer runs in a separate context with no visibility into the executor's reasoning, only the diff and the acceptance criteria. It cannot read the executor's chain of thought.

2. **Checklist-driven**: The reviewer works from a structured checklist, not open-ended "look for problems." Each checklist item demands a concrete verdict (pass/fail/concern) with a cited line number.

3. **Tool-restricted**: The reviewer has read-only access. It can `Read`, `Grep`, `Glob`, and run read-only commands (type-checker, linter). It cannot `Edit`, `Write`, or `Bash` with write operations. This prevents the reviewer from "fixing" issues quietly instead of reporting them.

4. **Adversarial framing**: The reviewer's system prompt tells it that its job is to find problems, and that missing a real issue is worse than raising a false alarm. It is scored on issues found, not on approval rate.

### 3.2 Structured Review Checklists

Each review perspective operates from its own checklist. The reviewer must emit structured JSON, not prose.

#### 3.2.1 Correctness Review

```yaml
correctness_checklist:
  - id: "corr-01"
    check: "All acceptance criteria are met"
    method: "Cross-reference each AC with the implementation"
    verdict_options: ["pass", "fail", "partial"]
    requires_evidence: true  # Must cite specific code or test output

  - id: "corr-02"
    check: "Error handling covers all failure modes"
    method: "For each external call (DB, network, filesystem), verify error handling exists"
    verdict_options: ["pass", "fail", "concern"]

  - id: "corr-03"
    check: "Edge cases are handled"
    method: "Identify inputs at boundaries (empty, null, max-length, unicode) and verify behavior"
    verdict_options: ["pass", "fail", "concern"]

  - id: "corr-04"
    check: "Concurrency safety"
    method: "If code is concurrent/async, check for races, deadlocks, shared mutable state"
    verdict_options: ["pass", "fail", "not_applicable"]
```

#### 3.2.2 Security Review

```yaml
security_checklist:
  - id: "sec-01"
    check: "No secrets in source code"
    method: "Grep for patterns: API keys, passwords, tokens, private keys"
    automated: true
    command: "git diff --cached | grep -iE '(password|secret|api_key|token|private_key)\\s*=\\s*[\"'\''][^\"'\\'']+[\"'\'']'"

  - id: "sec-02"
    check: "Input validation on all external inputs"
    method: "Identify all request handlers; verify each validates/sanitizes input"
    verdict_options: ["pass", "fail"]

  - id: "sec-03"
    check: "No SQL injection vectors"
    method: "Check for string concatenation in queries vs parameterized queries"
    verdict_options: ["pass", "fail", "not_applicable"]

  - id: "sec-04"
    check: "Authentication/authorization enforced on new endpoints"
    method: "Verify middleware or guards present on routes that require auth"
    verdict_options: ["pass", "fail", "not_applicable"]

  - id: "sec-05"
    check: "Sensitive data not logged"
    method: "Check log statements for passwords, tokens, PII"
    verdict_options: ["pass", "fail"]
```

#### 3.2.3 Performance Review

```yaml
performance_checklist:
  - id: "perf-01"
    check: "No N+1 query patterns"
    method: "Identify loops that contain database calls"
    verdict_options: ["pass", "fail", "not_applicable"]

  - id: "perf-02"
    check: "No unbounded data fetching"
    method: "Verify queries have LIMIT clauses or pagination; no SELECT * on large tables without bounds"
    verdict_options: ["pass", "fail", "not_applicable"]

  - id: "perf-03"
    check: "No blocking operations on hot paths"
    method: "Check for synchronous file I/O, CPU-heavy computation without offloading in request handlers"
    verdict_options: ["pass", "fail", "not_applicable"]

  - id: "perf-04"
    check: "Appropriate caching or memoization"
    method: "If the same expensive computation is repeated, suggest caching"
    verdict_options: ["pass", "concern", "not_applicable"]
```

#### 3.2.4 Style/Consistency Review

```yaml
style_checklist:
  - id: "style-01"
    check: "Follows existing codebase conventions"
    method: "Compare naming, file structure, import style with surrounding code"
    verdict_options: ["pass", "fail", "concern"]

  - id: "style-02"
    check: "No dead code introduced"
    method: "Check for unused imports, unreachable branches, commented-out code"
    verdict_options: ["pass", "fail"]

  - id: "style-03"
    check: "Functions/files are reasonable size"
    method: "Flag functions over 50 lines, files over 300 lines"
    verdict_options: ["pass", "concern"]
```

### 3.3 Review Feedback Loop

The reviewer produces structured output that the orchestrator parses and routes:

```json
{
  "review_id": "rev-20260328-001",
  "task_id": "auth-login-endpoint",
  "reviewer": "correctness-reviewer",
  "overall_verdict": "changes_requested",
  "findings": [
    {
      "id": "finding-01",
      "checklist_item": "corr-02",
      "severity": "must_fix",
      "file": "src/auth/login.ts",
      "line": 34,
      "description": "Database query failure is caught but the catch block returns 200 with an empty body instead of 500",
      "suggested_fix": "Return 500 with a generic error message in the catch block",
      "evidence": "Line 34-38: catch(e) { return res.json({}) }"
    },
    {
      "id": "finding-02",
      "checklist_item": "corr-03",
      "severity": "should_fix",
      "file": "src/auth/login.ts",
      "line": 12,
      "description": "No handling for email field containing only whitespace",
      "suggested_fix": "Trim and validate email before processing"
    }
  ],
  "summary": "1 must-fix issue (error handling), 1 should-fix issue (input validation edge case)"
}
```

**Routing rules:**

| Finding Severity | Action |
|---|---|
| `must_fix` | Task returned to executor with findings. Executor must address before re-review. |
| `should_fix` | Task returned to executor. Executor must address OR provide written justification. |
| `nitpick` | Logged. Does not block completion. May feed into a future simplification task. |

**Cycle limit:** The review-fix cycle has a configurable maximum (default: 3 rounds). If the executor cannot satisfy the reviewer in 3 rounds, the task escalates to a human with the full review history.

### 3.4 Preventing Reviewer-Executor Collusion

"Collusion" in the agent context means both agents converge on a locally consistent but globally wrong state. The executor writes code with a subtle bug, the reviewer does not catch it because it applies the same flawed reasoning, and both agree the task is done.

**Structural countermeasures:**

1. **Independent system prompts**: The executor gets the task + approach. The reviewer gets only the acceptance criteria + the diff. The reviewer does not see the task's "approach" section to avoid anchoring on the executor's plan.

2. **Rotating reviewer perspectives**: Do not use a single reviewer. Use multiple specialized reviewers (correctness, security, performance) that each examine the code through a different lens. A bug in error handling might pass a performance reviewer but will likely be caught by the correctness reviewer.

3. **Deterministic checks as a floor**: Before the LLM reviewer runs, deterministic checks (linter, type-checker, test suite) run first. These are immune to "collusion" because they are not LLM-based. They form a minimum quality floor that no amount of reviewer leniency can lower.

4. **Spot-check auditor**: Periodically (not every task, but randomly), a third agent reviews both the executor's code AND the reviewer's findings, looking specifically for cases where the reviewer missed something obvious. This creates accountability pressure on the reviewer.

5. **Metrics tracking**: Track reviewer approval rates over time. A reviewer that approves everything on the first pass is not doing its job. Expected first-pass approval rate for non-trivial tasks should be 30-60%. If it exceeds 80% consistently, the reviewer's system prompt or checklist needs tightening.

```yaml
collusion_prevention:
  reviewer_isolation:
    visible_to_reviewer:
      - "acceptance_criteria"
      - "git_diff"
      - "full_source_files_touched"
      - "test_output"
    hidden_from_reviewer:
      - "executor_reasoning"
      - "task_approach_section"
      - "executor_retry_history"

  multi_perspective_review:
    required_reviewers:
      - "correctness"
      - "security"  # On tasks touching auth, data, or external APIs
    optional_reviewers:
      - "performance"  # On tasks touching hot paths or data processing
      - "style"  # Can be deferred to simplification phase

  spot_check:
    frequency: "1_in_5_tasks"
    auditor_reviews: "both_code_and_reviewer_findings"
    focus: "did_the_reviewer_miss_anything_obvious"

  metrics:
    track_approval_rate: true
    alert_if_first_pass_approval_above: 0.8
    alert_window: "last_20_tasks"
```

---

## 4. Code Simplification Process

### 4.1 When to Run Simplification

Simplification is not review. Review asks "is this correct and safe?" Simplification asks "is this as clean as it can be without changing behavior?" They are separate concerns and should run at different times.

**Timing strategy:**

| Trigger | Scope | Rationale |
|---|---|---|
| After each task passes review | Files modified by that task only | Prevents complexity from compounding incrementally |
| After a feature group completes (3-5 related tasks) | All files in the feature area | Cross-task duplication only becomes visible after multiple tasks land |
| On a scheduled cadence (weekly or every N tasks) | Entire codebase | Entropy accumulates in places no individual task touches |
| When an agent signals struggle | Module the agent struggled with | Per OpenAI's insight: "when agent struggles, treat it as signal" that the code is too complex |

**Simplification should NOT run:**
- In the middle of a task (causes churn and confuses the executor)
- On code that is currently being modified by a parallel task (merge conflict risk)
- On code with failing tests (fix correctness first, then simplify)

### 4.2 What Simplification Covers

The simplification agent works from a concrete checklist, not an open-ended "make it better" mandate:

```yaml
simplification_checklist:
  dead_code:
    - "Remove unused imports"
    - "Remove unused variables and functions"
    - "Remove commented-out code blocks (>3 lines)"
    - "Remove unreachable code after return/throw"

  duplication:
    - "Identify code blocks duplicated 3+ times and extract to shared function"
    - "Identify near-duplicates (same structure, different values) and parameterize"
    - "Consolidate duplicate type definitions"

  naming:
    - "Rename variables that are single letters (except loop indices) or ambiguous"
    - "Rename boolean variables/functions to start with is/has/can/should"
    - "Ensure consistent naming convention within a module (camelCase vs snake_case)"

  structure:
    - "Break functions over 50 lines into smaller, well-named functions"
    - "Reduce nesting depth beyond 3 levels using early returns"
    - "Replace complex conditionals with named boolean variables or guard clauses"
    - "Colocate related code that is currently spread across distant lines"

  patterns:
    - "Replace manual iteration with standard library functions (map/filter/reduce)"
    - "Replace callback-based async with async/await where possible"
    - "Replace magic numbers with named constants"
    - "Replace string literals used as identifiers with enums or constants"
```

### 4.3 Verifying Simplification Preserves Behavior

Simplification is uniquely dangerous because it modifies working code. Every simplification pass must be guarded:

```yaml
simplification_safety:
  pre_conditions:
    - "All tests pass before simplification begins"
    - "No uncommitted changes in working tree"
    - "Simplification runs in an isolated git worktree or branch"

  during_simplification:
    - "Each individual change is a separate commit"
    - "After each commit, run the full test suite"
    - "If tests fail after a change, revert that specific commit and continue"
    - "The simplification agent must NOT modify test files (separate concern)"

  post_conditions:
    - "All tests that passed before simplification still pass after"
    - "No new lint warnings introduced"
    - "Diff is reviewed by a reviewer agent (style perspective)"
    - "Net line count change is zero or negative (simplification should not grow code)"

  rollback:
    - "If any post-condition fails, the entire simplification branch is discarded"
    - "Failed simplification attempts are logged for future learning"
```

### 4.4 Integration with Claude Code's Simplify Skill

Claude Code offers a `/simplify` skill that reviews changed code for reuse, quality, and efficiency. The orchestrator should invoke this skill as a subprocess of the simplification agent, not as a replacement for the structured process:

```yaml
simplification_pipeline:
  steps:
    - name: "snapshot_test_state"
      command: "npm test"
      save_as: "baseline_test_results"

    - name: "create_worktree"
      command: "git worktree add ../xpatcher-simplify simplify-$(date +%s)"

    - name: "run_claude_simplify"
      tool: "claude_code_skill"
      skill: "simplify"
      scope: "recently_modified_files"  # or specified file list
      # The /simplify skill handles the micro-level cleanup

    - name: "run_structural_simplification"
      agent: "simplification_agent"
      checklist: "simplification_checklist"
      scope: "module_level"
      # This handles macro-level simplification (duplication across files, etc.)

    - name: "verify_tests_pass"
      command: "npm test"
      compare_with: "baseline_test_results"
      on_failure: "revert_and_log"

    - name: "review_diff"
      agent: "style_reviewer"
      input: "git diff main...simplify-branch"

    - name: "merge_or_discard"
      condition: "all_tests_pass AND review_approved"
      on_success: "merge_simplify_branch"
      on_failure: "discard_worktree_and_log"
```

---

## 5. Gap Detection and Completeness Verification

### 5.1 The Gap Detection Problem

A gap is a requirement implied by the original user intent that no task in the pipeline addresses. Gaps emerge from three sources:

1. **Decomposition gaps**: The intent was split into tasks, but the split missed something (e.g., "build a login system" was decomposed into tasks for the UI and the API, but nobody created a task for session management).

2. **Implicit requirement gaps**: The user said "build a login system" but didn't explicitly say "and handle the case where the user's account is locked." The intent implies it; no task captures it.

3. **Integration gaps**: Each task works in isolation, but they don't connect properly (the login UI posts to `/api/login` but the backend route is `/auth/login`).

### 5.2 Techniques for Detecting Gaps

#### 5.2.1 Intent Decomposition Audit

After all tasks for a feature are defined (but before execution begins), a gap-detection agent examines the original intent alongside the task list:

```yaml
gap_detection_pre_execution:
  input:
    - original_intent: "Users can sign up, log in, and reset their passwords"
    - task_list:
        - "Implement signup endpoint"
        - "Implement login endpoint"
        - "Implement password reset request endpoint"

  agent_prompt: |
    You are a requirements analyst. Given the original user intent and the
    list of tasks, identify requirements that are implied by the intent
    but not covered by any task.

    Do NOT suggest nice-to-have features. Only identify requirements that
    a reasonable user would expect as part of this intent.

    For each gap found, state:
    1. What is missing
    2. Why it is implied by the intent
    3. Suggested task to fill the gap

  expected_gaps_in_this_example:
    - "Password reset email delivery (task covers the request endpoint but not the email sending)"
    - "Password reset token verification and new password submission endpoint"
    - "Email verification on signup (implied by 'sign up' if email is the identifier)"
    - "Session/token invalidation on password reset"
```

#### 5.2.2 Post-Execution Integration Check

After tasks execute, verify that the pieces connect:

```yaml
integration_gap_detection:
  steps:
    - name: "endpoint_wiring_check"
      description: "Verify all frontend API calls have corresponding backend routes"
      method: |
        1. Extract all fetch/axios calls from frontend code (URLs and methods)
        2. Extract all route definitions from backend code (paths and methods)
        3. Report any frontend calls that don't match a backend route

    - name: "data_flow_check"
      description: "Verify data shapes match across boundaries"
      method: |
        1. Extract TypeScript interfaces used in frontend API calls
        2. Extract TypeScript interfaces used in backend response types
        3. Compare field names, types, and optionality
        4. Report mismatches

    - name: "navigation_check"
      description: "Verify all navigation targets exist"
      method: |
        1. Extract all router links and navigation calls from frontend code
        2. Extract all route definitions
        3. Report any links pointing to undefined routes

    - name: "error_path_check"
      description: "Verify error states are handled in the UI"
      method: |
        1. For each API call in the frontend, check for error handling (catch, onError, error state)
        2. Report API calls with no error handling
```

#### 5.2.3 User Journey Walk-Through

An agent simulates user journeys against the acceptance criteria to find dead ends:

```yaml
journey_gap_detection:
  journeys:
    - name: "New user signup to first use"
      steps:
        - "User visits the app for the first time"
        - "User clicks 'Sign Up'"
        - "User fills in registration form and submits"
        - "User receives confirmation"
        - "User logs in with new credentials"
        - "User sees the main dashboard"
      check: "For each step, does a task exist that implements it? Is there a test for it?"

    - name: "Password reset"
      steps:
        - "User clicks 'Forgot Password' on login page"
        - "User enters email"
        - "User receives email with reset link"
        - "User clicks link and enters new password"
        - "User logs in with new password"
      check: "Same as above"
```

### 5.3 Generating Tasks for Discovered Gaps

When gaps are found, the gap-detection agent generates new task definitions using the same YAML schema as the original tasks. These generated tasks are marked as `origin: gap_detection` to distinguish them from original tasks:

```yaml
generated_task:
  id: "password-reset-email"
  origin: "gap_detection"
  parent_intent: "Users can sign up, log in, and reset their passwords"
  gap_finding: "Password reset request endpoint exists but no email is sent"

  intent: "When a user requests a password reset, send them an email with a secure reset link"
  goal: "Implement email sending for password reset flow"

  acceptance_criteria:
    functional:
      - id: "ac-01"
        description: "Requesting password reset for a valid email triggers an email send"
        verification: "test"
        severity: "must_pass"
    # ... (full AC structure as defined in Section 1)
```

**Generated tasks go into a review queue before execution.** The orchestrator (or a human) must approve them to prevent the gap detector from silently expanding scope.

### 5.4 Preventing Scope Creep During Gap Detection

The gap detector has a strong incentive to find gaps -- that is its purpose. Left unchecked, it will invent requirements that are plausible but exceed the original intent. Countermeasures:

1. **Intent anchoring**: The gap detector's prompt explicitly states: "Only identify requirements that a reasonable user would consider essential for the stated intent. Do not suggest enhancements, optimizations, or features the user did not ask for."

2. **Scope budget**: Each feature has a maximum number of gap-generated tasks (e.g., 30% of the original task count). If the gap detector exceeds this, the excess is logged for human review but not auto-generated.

3. **Human gate on gap tasks**: Gap-generated tasks are never auto-executed. They enter a queue that either a human or a senior orchestrator agent must approve.

4. **Categorization**: Each gap finding must be categorized as:
   - **Critical gap**: The feature is broken or insecure without this (auto-approve)
   - **Expected gap**: A reasonable user would notice this missing (human-approve)
   - **Enhancement**: Nice to have but not implied by the intent (reject unless human requests)

```yaml
scope_creep_prevention:
  max_gap_tasks_ratio: 0.3  # Gap tasks cannot exceed 30% of original task count

  categories:
    critical:
      description: "Feature is broken or insecure without this"
      auto_approve: true
      examples: "Missing auth check on admin route, data loss on error"

    expected:
      description: "Reasonable user would notice this missing"
      auto_approve: false
      requires: "human_approval"
      examples: "No loading state on slow requests, no confirmation on delete"

    enhancement:
      description: "Improves the feature but was not asked for"
      auto_approve: false
      default_action: "defer_to_backlog"
      examples: "Remember me checkbox, social login, password strength meter"
```

### 5.5 When to Declare "Done" vs "Good Enough"

A feature is **done** when:
- All original tasks have passed their completion gates
- All `critical` gap tasks have been completed
- All `expected` gap tasks have been completed OR explicitly deferred by a human
- Integration gap detection finds no unresolved wiring issues
- The full regression suite passes

A feature is **good enough** when:
- All original tasks have passed their completion gates
- All `critical` gap tasks have been completed
- Some `expected` gap tasks remain, but a human has reviewed and approved deferral
- Known limitations are documented in the task completion record

The orchestrator should never auto-declare "done." It presents the status to the human with a clear summary:

```
Feature: User Authentication
Status: READY FOR REVIEW

Completed: 5/5 original tasks, 2/2 critical gap tasks
Deferred: 1 expected gap task (email verification - deferred to Phase 2)
Rejected: 2 enhancement gap tasks (social login, remember me)

All tests passing: 47/47
Integration check: PASS
Regression suite: PASS

Decision needed: Accept as done, or address deferred items?
```

---

## 6. Regression Prevention

### 6.1 Maintaining a Growing Test Suite Across Iterations

As tasks complete, the test suite grows. This growth must be managed intentionally:

```yaml
test_suite_management:
  organization:
    # Tests are organized by feature area, not by task
    # This prevents the suite from becoming a graveyard of task-specific tests
    structure:
      - "tests/unit/{feature}/{module}.test.ts"
      - "tests/integration/{feature}/{scenario}.test.ts"
      - "tests/e2e/{journey}.spec.ts"

  test_registry:
    # Every test is registered with metadata for management
    fields:
      - test_id: "unique identifier"
      - created_by_task: "task-id that generated this test"
      - acceptance_criterion: "AC this test verifies (if any)"
      - feature_area: "which feature this belongs to"
      - test_type: "unit | integration | e2e"
      - stability: "stable | flaky | quarantined"
      - last_passed: "timestamp"
      - consecutive_failures: 0

  lifecycle:
    # Tests are never silently deleted
    deletion_requires: "explicit_approval"
    modification_triggers: "review_if_test_weakened"
    # A test is "weakened" if assertions are removed or conditions relaxed
```

### 6.2 Test Stability Monitoring

```yaml
stability_monitoring:
  per_test_tracking:
    window: "last_20_runs"
    metrics:
      - pass_rate: "number of passes / total runs"
      - mean_duration: "average execution time"
      - duration_variance: "standard deviation of execution time"
      - flap_count: "number of pass->fail or fail->pass transitions"

  thresholds:
    stable: "pass_rate >= 0.95 AND flap_count <= 1"
    unstable: "pass_rate >= 0.80 AND (flap_count > 1 OR pass_rate < 0.95)"
    flaky: "pass_rate < 0.80 OR flap_count > 3"

  actions:
    stable: "no action"
    unstable: "log warning, continue running"
    flaky: "quarantine, generate fix task"

  dashboard:
    # The orchestrator maintains a stability dashboard
    # visible to both agents and humans
    refresh: "after_every_test_run"
    alerts: "on_transition_to_unstable_or_flaky"
```

### 6.3 Detecting Cross-Task Breakage

When Task B's implementation breaks a test that was introduced by Task A, the pipeline must detect, attribute, and resolve this:

```yaml
cross_task_regression_detection:
  strategy: "incremental_verification"

  on_task_completion:
    steps:
      - name: "run_full_suite"
        command: "npm test"
        capture: "per_test_results"

      - name: "compare_with_baseline"
        description: |
          Compare test results against the baseline (results from before this task started).
          Any test that was PASSING in the baseline and is now FAILING is a regression
          caused by this task.

      - name: "attribute_regression"
        description: |
          For each regression:
          1. Identify which task originally created the failing test (from test registry)
          2. Identify which files the current task modified
          3. Determine if the regression is in a file the current task touched

      - name: "route_regression"
        rules:
          - if: "regression is in a file the current task touched"
            then: "current task's executor must fix before completion"
          - if: "regression is in a file the current task did NOT touch"
            then: |
              Likely an indirect dependency. Create a dedicated fix task with:
              - The failing test
              - The current task's diff
              - The original task's acceptance criteria
              Assign to the current task's executor first (they caused it).

  baseline_management:
    # The baseline is updated after each task successfully completes
    update_trigger: "task_passes_all_gates"
    storage: "test_results/{task_id}/baseline.json"
```

### 6.4 Integration Testing Across Parallel Task Completions

When tasks execute in parallel (via git worktrees), each passes its own tests in isolation. The merge point is where regressions appear:

```yaml
parallel_merge_strategy:
  isolation:
    # Each parallel task runs in its own git worktree
    # Tests pass in isolation
    worktree_pattern: "worktrees/{task_id}"

  merge_verification:
    steps:
      - name: "merge_to_integration_branch"
        description: |
          When a task completes, merge its branch into an integration branch.
          Do NOT merge directly to main.

      - name: "resolve_conflicts"
        description: |
          If merge conflicts occur:
          - Textual conflicts: attempt auto-resolution, then agent-assisted resolution
          - Semantic conflicts (no textual conflict but behavior changes):
            detected by test failures after merge

      - name: "run_full_suite_on_integration"
        command: "npm test"
        description: |
          Run the FULL test suite on the integration branch.
          This catches semantic conflicts between parallel tasks.

      - name: "route_failures"
        rules:
          - if: "test from Task A fails after merging Task B"
            then: |
              1. Determine if it's a genuine conflict or a flaky test (rerun 3x)
              2. If genuine: create a conflict resolution task
                 - Input: both task diffs, failing test, acceptance criteria for both tasks
                 - Constraint: resolution must satisfy BOTH tasks' acceptance criteria
              3. If resolution is not possible without redesign: escalate to human

  merge_order:
    # When multiple tasks complete around the same time,
    # merge them one at a time to the integration branch.
    # This makes it clear which merge introduced a failure.
    strategy: "sequential_by_completion_time"

  promotion_to_main:
    # Integration branch promotes to main only when:
    condition: "all_tests_pass AND all_reviews_approved AND no_pending_conflict_tasks"
```

### 6.5 Regression Prevention Summary

The full regression prevention pipeline, in order of execution:

```
1. BEFORE task execution:
   - Record baseline test results
   - Snapshot test registry

2. DURING task execution:
   - Executor runs tests incrementally as it works
   - Executor's own tests must pass before submitting for review

3. AFTER task completion (pre-merge):
   - Run full test suite in task's worktree
   - Compare against baseline; flag any regressions
   - Executor must fix regressions before review

4. DURING review:
   - Reviewer verifies test quality (Section 2.2)
   - Reviewer verifies no test weakening

5. AFTER review approval (merge):
   - Merge to integration branch (not main)
   - Run full test suite on integration branch
   - If failures: create conflict resolution task

6. PERIODIC (between tasks):
   - Run stability monitoring
   - Quarantine flaky tests
   - Generate fix tasks for quarantined tests

7. FEATURE COMPLETE:
   - Full regression suite
   - Integration gap detection (Section 5.2.2)
   - Promote integration branch to main
```

---

## Appendix A: Complete Task Lifecycle with Quality Gates

```
                    TASK DEFINED
                         |
                         v
              +-----------------------+
              | Gap Detection (Pre)   |
              | - Missing requirements|
              | - Implicit needs      |
              +-----------+-----------+
                          |
                          v
              +-----------------------+
              | Executor Agent        |
              | - Implements code     |
              | - Generates tests     |
              | - Runs tests locally  |
              +-----------+-----------+
                          |
                          v
              +-----------------------+
              | Automated Checks      |  <-- Deterministic floor
              | - Type checker        |
              | - Linter              |
              | - Test suite (full)   |
              | - Test quality gates  |
              +-----------+-----------+
                          |
                     PASS | FAIL --> back to Executor (auto)
                          |
                          v
              +-----------------------+
              | Review Agents         |  <-- LLM-based verification
              | - Correctness review  |
              | - Security review     |
              | - Performance review  |
              +-----------+-----------+
                          |
              APPROVED | CHANGES_REQUESTED --> back to Executor
                          |                    (max 3 cycles)
                          v
              +-----------------------+
              | Simplification        |  <-- Optional, post-review
              | - Dead code removal   |
              | - Duplication cleanup  |
              | - Naming improvement  |
              +-----------+-----------+
                          |
                          v
              +-----------------------+
              | Merge to Integration  |
              | - Conflict resolution |
              | - Cross-task tests    |
              +-----------+-----------+
                          |
                     PASS | FAIL --> Conflict Resolution Task
                          |
                          v
              +-----------------------+
              | Gap Detection (Post)  |
              | - Integration wiring  |
              | - User journey check  |
              +-----------+-----------+
                          |
                          v
                    TASK COMPLETE
```

## Appendix B: Key Metrics to Track

| Metric | What It Reveals | Target |
|---|---|---|
| First-pass review approval rate | Executor quality / Reviewer strictness | 30-60% |
| Average review-fix cycles | How well tasks are specified | < 2.0 |
| Test-to-code ratio (lines) | Test coverage investment | 0.8 - 1.5 |
| Regression rate per task | Cross-task interference | < 5% |
| Flaky test generation rate | Agent test quality | < 10% of new tests |
| Gap tasks as % of original tasks | Decomposition quality | < 20% |
| Simplification revert rate | Simplification agent reliability | < 10% |
| Time to detect regression | Pipeline feedback speed | < 5 minutes |
| Escalation to human rate | Automation coverage | < 15% |

## Appendix C: Anti-Patterns to Watch For

1. **The Approval Rubber Stamp**: Reviewer approves everything on first pass. Fix: tighten checklist, add spot-check auditor, track approval rate.

2. **The Infinite Fix Cycle**: Executor and reviewer ping-pong indefinitely. Fix: hard cycle limit (3), escalate to human.

3. **The Tautological Test Suite**: All tests pass but test nothing meaningful. Fix: mutation testing, negation checks, LLM test auditor.

4. **The Snapshot Trap**: Agent generates snapshot tests against its own potentially-wrong output. Fix: ban snapshot tests for agent-generated code unless the snapshot is human-approved.

5. **The Scope Snowball**: Gap detection keeps finding "critical" gaps that expand the feature indefinitely. Fix: scope budget, human gate, strict categorization.

6. **The Simplification Thrash**: Simplification agent and executor have different style preferences, leading to code being rewritten back and forth. Fix: shared style configuration (linter rules, not LLM preferences), simplification agent respects existing patterns.

7. **The False Green**: All tests pass but the application doesn't work because tests mock too aggressively. Fix: require at least one integration test per feature that uses real (or realistic) dependencies.

8. **The Silent Regression**: A parallel task breaks something, but it's merged before anyone notices. Fix: sequential merge to integration branch, full suite after each merge.

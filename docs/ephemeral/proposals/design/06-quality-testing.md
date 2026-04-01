# Quality and Testing Framework

> Part of the [xpatcher Design Proposal](../xpatcher-design-proposal.md)

---

## 6.1 Acceptance Criteria Template

Every task must have at least one fully automatable acceptance criterion. Criteria follow this template:

```yaml
acceptance_criteria:
  # Functional: verified by running tests
  functional:
    - id: "ac-01"
      description: "Valid login returns token"
      verification: "test"
      test_command: "npm test -- --grep 'login.*valid.*returns.*token'"
      severity: "must_pass"

  # Structural: verified by static analysis / linting
  structural:
    - id: "ac-04"
      description: "No TypeScript compilation errors"
      verification: "command"
      command: "npx tsc --noEmit"
      expected_exit_code: 0
      severity: "must_pass"

  # Behavioral: verified by browser automation
  behavioral:
    - id: "ac-06"
      description: "Login flow works end-to-end"
      verification: "browser"
      script: "e2e/auth-login.spec.ts"
      severity: "must_pass"

  # Qualitative: verified by reviewer agent
  qualitative:
    - id: "ac-07"
      description: "Error messages do not leak internal details"
      verification: "review"
      reviewer_checklist:
        - "No stack traces in response bodies"
        - "No database column names in error messages"
      severity: "must_pass"

  # Regression: existing tests still pass
  regression:
    - id: "ac-09"
      description: "All pre-existing tests still pass"
      verification: "command"
      command: "npm test"
      expected_exit_code: 0
      severity: "must_pass"

  completion_gate:
    all_must_pass: true
    max_iterations: 3
    on_max_retries_exceeded: "escalate_to_human"
```

**Criteria granularity principle**: specify WHAT is observable, not HOW it is implemented. Criteria should survive a complete rewrite of the internals.

**Severity levels:**
- `must_pass`: task cannot complete if this fails. No exceptions.
- `should_pass`: failure triggers a warning; reviewer decides whether to block.
- `nice_to_have`: logged but never blocks completion.

The completion gate is evaluated by the **orchestrator**, not the executor. The executor never self-certifies.
Each task manifest entry must therefore contain at least one command-backed acceptance criterion that the dispatcher can execute directly.

## 6.2 Testing Strategy

```
         /  E2E / Browser  \         <-- Agent MUST generate for UI tasks
        / Integration Tests \        <-- Agent MUST generate for API/data tasks
       /    Unit Tests       \       <-- Agent SHOULD generate for logic-heavy code
      /  Static Analysis      \      <-- Orchestrator runs automatically, always
     /__________________________\
```

**Minimum test requirements by task type:**

| Task Type | Required Tests |
|-----------|---------------|
| New API endpoint | Integration test (happy path, validation error, auth error) |
| UI component | Browser test verifying render + interaction |
| Business logic | Unit tests covering branches + one integration test proving wiring |
| Bug fix | Regression test that fails on old code, passes on fix |
| Refactor | Zero new tests; all existing tests must pass |

**Test quality verification pipeline:**

1. **Command execution gate**: the dispatcher runs task acceptance commands and regression commands directly from the manifest.
2. **Coverage check**: not a vanity metric, but to identify completely untested branches.
3. **Negation check**: for `must_pass` criteria, temporarily invert the condition and confirm the test fails. This is the strongest guarantee.
4. **LLM test auditor**: a read-only agent examines each test: does it make a meaningful assertion? Could it pass if the feature were broken? Is it deterministic?
5. **Mutation testing** (optional, for critical paths): run mutation testing to verify tests catch real bugs. Minimum mutation kill rate: 70%.
6. **Flaky test detection**: run each new test 5 times on creation. If any run fails, reject and regenerate.

```yaml
test_quality_gates:
  coverage:
    enabled: true
    min_line_coverage_for_new_code: 80

  negation_check:
    enabled: true
    applies_to: "must_pass"

  mutation:
    enabled: false   # Enable for critical-path tasks only
    min_mutation_score: 60

  llm_audit:
    enabled: true
    tool_restrictions: ["Read", "Grep", "Glob"]

  flaky_detection:
    repetitions: 5
    must_pass_all: true
    on_failure: "reject_test_and_retry_generation"
```

### 6.2.1 Tiered Quality Gate Profiles

The full 5-gate test quality pipeline adds 6-30+ minutes and $2-5 per task. Not all tasks warrant this overhead. The planner assigns a **quality tier** to each task during breakdown based on `estimated_complexity` and risk classification.

| Tier | When to Use | Gates Enabled | Est. Overhead |
|------|-------------|---------------|---------------|
| **Lite** | Refactors, docs, low-risk single-file changes | Coverage check + regression suite only | 1-3 min |
| **Standard** | Most feature tasks, bug fixes | Coverage + negation check + flaky detection (3 runs) | 5-15 min |
| **Thorough** | Security-sensitive, financial logic, data migrations | All gates: coverage + negation + flaky + mutation + LLM audit | 15-30 min |

```yaml
# In config.yaml:
quality_tiers:
  lite:
    coverage: { enabled: true, min_line_coverage_for_new_code: 60 }
    negation_check: { enabled: false }
    mutation: { enabled: false }
    llm_audit: { enabled: false }
    flaky_detection: { enabled: false }

  standard:
    coverage: { enabled: true, min_line_coverage_for_new_code: 80 }
    negation_check: { enabled: true, applies_to: "must_pass" }
    mutation: { enabled: false }
    llm_audit: { enabled: false }
    flaky_detection: { enabled: true, repetitions: 3, must_pass_all: true }

  thorough:
    coverage: { enabled: true, min_line_coverage_for_new_code: 80 }
    negation_check: { enabled: true, applies_to: "must_pass" }
    mutation: { enabled: true, min_mutation_score: 60 }
    llm_audit: { enabled: true, tool_restrictions: ["Read", "Grep", "Glob"] }
    flaky_detection: { enabled: true, repetitions: 5, must_pass_all: true }
```

**Tier assignment:** The planner assigns tiers in the task manifest based on:
- `estimated_complexity: low` + no security/data keywords → `lite`
- `estimated_complexity: medium` or cross-module changes → `standard`
- `estimated_complexity: high` or security/financial/migration keywords → `thorough`

The human can override tier assignments at the plan approval gate (Stage 5). The dispatcher also accepts per-task tier overrides in `.xpatcher.yaml`:

```yaml
# .xpatcher.yaml — project-level tier overrides
quality_tier_overrides:
  default: standard          # Override default tier for this project
  paths:
    "src/auth/**": thorough  # Always thorough for auth code
    "docs/**": lite          # Always lite for docs
```

## 6.3 Review Agent Design

The reviewer is structurally prevented from being lenient through four mechanisms:

1. **Isolation**: runs in a separate context with no visibility into executor reasoning. Sees only the diff and acceptance criteria.
2. **Checklist-driven**: works from structured checklists (correctness, security, performance, style). Each item demands a concrete verdict with cited line numbers.
3. **Tool-restricted**: read-only access (Read, Grep, Glob, read-only Bash). Cannot fix issues quietly.
4. **Adversarial framing**: system prompt says "your job is to find problems. Missing a real issue is worse than raising a false alarm."

**Review checklists** cover four perspectives:

| Perspective | Key Checks |
|-------------|-----------|
| **Correctness** | All ACs met, error handling for all external calls, edge cases, concurrency safety |
| **Security** | No secrets in code, input validation, no SQL injection, auth enforced on endpoints, sensitive data not logged |
| **Performance** | No N+1 queries, no unbounded data fetching, no blocking on hot paths |
| **Style** | Follows codebase conventions, no dead code, reasonable function/file sizes |

**Collusion prevention:**

```yaml
collusion_prevention:
  reviewer_isolation:
    visible_to_reviewer: [acceptance_criteria, git_diff, source_files, test_output]
    hidden_from_reviewer: [executor_reasoning, task_approach, retry_history]

  multi_perspective_review:
    required: [correctness, security]
    optional: [performance, style]

  spot_check:
    frequency: "1_in_5_tasks"
    auditor_reviews: "both_code_and_reviewer_findings"

  metrics:
    alert_if_first_pass_approval_above: 0.8
    alert_window: "last_20_tasks"
```

## 6.4 Code Simplification Integration

Simplification is separate from review. Review asks "is this correct?" Simplification asks "is this as clean as possible?"

**Simplification is a post-approval refinement step, not part of the test/review retry loop.** It runs only after a task has passed both testing and review. Simplification failures are reverted and never increment the quality iteration counter. See Section 3.4 for the definitive quality loop flowchart.

**When to run:**

| Trigger | Scope | Condition |
|---------|-------|-----------|
| After each task passes test + review | Files modified by that task | Only if `autoSimplify: true` in config (default: false) |
| After a feature group completes (3-5 related tasks) | All files in the feature area | Only if `autoSimplify: true` |
| When an agent signals struggle | Module the agent struggled with | Manual trigger only |

**Safety protocol:**

```yaml
simplification_safety:
  pre_conditions:
    - "All tests pass before simplification begins"
    - "No uncommitted changes in working tree"
    - "Runs in isolated git worktree or branch"

  during_simplification:
    - "Each individual change is a separate commit"
    - "After each commit, run the full test suite"
    - "If tests fail after a change, revert that commit and continue"
    - "Must NOT modify test files"

  post_conditions:
    - "All tests that passed before still pass after"
    - "No new lint warnings"
    - "Net line count change is zero or negative"

  rollback:
    - "If any post-condition fails, discard entire simplification branch"
```

## 6.5 Gap Detection Process

Gaps emerge from three sources:

1. **Decomposition gaps**: the intent was split into tasks but something was missed
2. **Implicit requirement gaps**: user said "build a login system" but not "handle locked accounts"
3. **Integration gaps**: each task works alone but they do not connect properly

**Detection techniques:**

| Technique | When | What It Catches |
|-----------|------|-----------------|
| Intent decomposition audit | Pre-execution | Requirements implied but not covered by any task |
| Post-execution integration check | After all tasks | Frontend calls with no backend route, mismatched data shapes |
| User journey walk-through | After integration | Dead ends in user flows |

**Scope creep prevention:**

```yaml
scope_creep_prevention:
  max_gap_tasks_ratio: 0.3          # Gap tasks <= 30% of original task count
  categories:
    critical: { auto_approve: true }   # Feature broken without this
    expected: { requires: "human_approval" }  # User would notice
    enhancement: { default: "defer_to_backlog" }  # Nice-to-have
```

## 6.5.1 Regression Testing Between Tasks

When Task C modifies a file that Task A also touched, Task A's acceptance criteria may no longer hold. This section defines the regression testing protocol.

### v1: Standard Test Suite Regression

After each task completes its quality loop and commits to the feature branch, the dispatcher re-runs the project's **standard test suite** (detected during preflight — see Section 6.7). This catches regressions in shared code paths.

```yaml
# In execution-plan.yaml, each task records its AC commands:
tasks:
  task-001:
    acceptance_commands:
      - "npm test -- --grep 'session.*valid'"
      - "npx tsc --noEmit"
    files_in_scope: ["src/auth/session.ts", "src/auth/store.ts"]
  task-002:
    acceptance_commands:
      - "npm test -- --grep 'redis.*adapter'"
    files_in_scope: ["src/cache/redis.ts"]
```

**v1 regression gate (after each task merge to feature branch):**
1. Run the project's standard test suite (`npm test`, `pytest`, etc.)
2. If standard tests fail: mark the just-completed task as `FAILED` with reason `regression`
3. The task re-enters the fix iteration loop (Stage 13)

### v2: Full Acceptance Criteria Regression

After each task merge, in addition to the standard test suite, the dispatcher re-runs acceptance criteria commands from **all previously completed tasks** whose `files_in_scope` overlap with the current task's changed files.

```python
def get_regression_targets(current_task: Task, completed_tasks: list[Task]) -> list[Task]:
    """Find completed tasks whose AC commands should be re-run."""
    current_files = set(current_task.files_changed)
    targets = []
    for task in completed_tasks:
        if current_files & set(task.files_in_scope):
            targets.append(task)
    return targets
```

**v2 regression gate (after standard suite passes):**
1. Identify completed tasks with overlapping file scope
2. Re-run their acceptance criteria commands
3. If any fail: mark the current task as `FAILED` with `reason: regression`, listing which prior task's ACs broke
4. The fix iteration receives the specific failing AC commands as context

**Cost note:** Full AC regression is expensive (potentially re-running all prior tasks' test commands). For v1, the standard test suite is sufficient — it catches most regressions. Full AC regression is a v2 enhancement for projects with specialized per-task test commands not covered by the standard suite.

## 6.6 Convergence Criteria

A task is "done" when ALL of the following are true:

1. All acceptance criteria test commands pass (exit code 0)
2. Review agent verdict is `approved` with no `major` findings
3. Diff touches only files within declared `file_scope` (or has justified scope expansion)
4. Pre-existing test suite passes on the task branch (no regressions)
5. Output size is within sanity-check bounds (not >5x or <0.1x of estimate)
6. If task has dependents, smoke check confirms dependent interface assumptions hold

Any single failure sends the task back to fix iteration.

## 6.7 Language and Framework Detection

The planner agent auto-detects the project's tech stack and selects appropriate tooling. The dispatcher validates that required tools are installed before execution begins.

### Detection Strategy

The planner reads project manifests to determine the stack:

| Manifest File | Language/Framework | Test Command | Lint Command | Type Check |
|---|---|---|---|---|
| `package.json` | JavaScript/TypeScript | `npm test` / `yarn test` | `npx eslint .` | `npx tsc --noEmit` |
| `pyproject.toml` / `setup.py` / `requirements.txt` | Python | `pytest` | `ruff check .` / `flake8` | `mypy .` |
| `go.mod` | Go | `go test ./...` | `golangci-lint run` | (built into compiler) |
| `Cargo.toml` | Rust | `cargo test` | `cargo clippy` | (built into compiler) |
| `pom.xml` / `build.gradle` | Java | `mvn test` / `gradle test` | `checkstyle` | (built into compiler) |
| `composer.json` | PHP | `phpunit` | `phpcs` / `phpstan` | `phpstan analyse` |
| `Gemfile` | Ruby | `bundle exec rspec` | `rubocop` | `sorbet` |
| `mix.exs` | Elixir | `mix test` | `mix credo` | `mix dialyzer` |

### Preflight Tool Validation

Before pipeline execution, the dispatcher runs a preflight check:

```python
def preflight_check(project_dir: str) -> PreflightResult:
    """Detect stack and validate required tools are installed."""
    # 1. Detect project type from manifest files
    # 2. For each required tool, check if it's available (which/where)
    # 3. If missing, prompt user: "Tool X is required but not found. Install? [y/N]"
    # 4. If user approves, use claude agent to determine install command
    # 5. Report ready/blocked status
```

The preflight result is stored in `.xpatcher/<feature>/preflight.yaml` and referenced by all agents.

---

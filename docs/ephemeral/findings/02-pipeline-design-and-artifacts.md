# Pipeline Design and Artifact System

**Document:** 02-pipeline-design-and-artifacts
**Date:** 2026-03-28
**Status:** Draft / Brainstorm
**Scope:** Detailed specification of the SDLC automation pipeline stages, self-correction loops, human-in-the-loop design, artifact schema, and progress tracking.

---

## Table of Contents

1. [Pipeline Stage Design](#1-pipeline-stage-design)
2. [Self-Correction and Reflection Loops](#2-self-correction-and-reflection-loops)
3. [Human-in-the-Loop Design](#3-human-in-the-loop-design)
4. [Artifact System](#4-artifact-system)
5. [Progress Tracking and Transparency](#5-progress-tracking-and-transparency)

---

## 1. Pipeline Stage Design

### 1.1 Stage Overview

The pipeline is a directed graph with review loops, not a simple linear sequence. The critical insight from orchestration research is that "appropriate task granularity" determines success or failure more than any other factor. Stages exist to enforce granularity boundaries: no stage should accept an artifact that is too coarse for it to process reliably.

```
                          Human Gate
                              |
  [1] Intent ──> [2] Plan ──> [3] Plan Review ──> [4] Plan Fix ──┐
                                     ^                            |
                                     └────── loop ────────────────┘
                                                    |
                                              [5] Plan Approved
                                                    |
                                     ┌──────────────┘
                                     v
  [6] Task Breakdown ──> [7] Task Review ──> [8] Task Fix ──┐
                                ^                            |
                                └────── loop ────────────────┘
                                                  |
                           [9] Prioritization + Dependency Graph
                                                  |
                           [10] Execution Graph (DAG)
                                                  |
                    ┌───────────┬─────────────────┼──────────────┐
                    v           v                 v              v
              [11a] Task    [11b] Task      [11c] Task     [11d] Task
                    |           |                 |              |
              [12] Per-task loop: simplify ──> test ──> review
                    |           |                 |              |
              [13] Fix iteration (per task)
                    |           |                 |              |
                    └───────────┴─────────────────┴──────────────┘
                                                  |
                                          [14] Gap Detection
                                                  |
                                    ┌─── gaps found? ───┐
                                    v                    v
                              [6] re-enter          [15] Complete
```

### 1.2 Stage Specifications

#### Stage 1: Intent Capture

**Purpose:** Transform raw user input (natural language, ticket reference, conversation excerpt) into a normalized intent object. This is the only stage where ambiguity is tolerated; every subsequent stage demands precision.

**Entry criteria:**
- User provides any form of request (text, URL to issue tracker, voice transcript, etc.)

**Process:**
- Parse the input for explicit goals, implicit constraints, and domain context.
- Identify the target repository, affected subsystems, and any referenced prior work.
- Flag ambiguities that require clarification before planning can proceed.

**Exit criteria:**
- A structured intent artifact exists with: goal statement, scope boundaries, known constraints, and a list of open questions (which may be empty).
- If open questions exist, the pipeline pauses for human clarification before advancing.

**Artifacts produced:**
- `intent.yaml`

**Transition:** Automatic if no open questions. Human-gated if clarification is needed.

**Error handling:** If the input is unparseable (empty, pure noise, or contradictory), the stage emits an `intent.yaml` with `status: needs_clarification` and a list of specific questions. The pipeline halts until the human responds.

```yaml
# sdlc/<feature>/intent.yaml
schema_version: "1.0"
id: "intent-20260328-143022-auth-redesign"
feature: "auth-redesign"
created_at: "2026-03-28T14:30:22Z"
source:
  type: "user_input"          # user_input | issue_tracker | conversation
  raw_text: |
    We need to replace the JWT auth with session-based auth.
    The current system has token refresh bugs and the mobile
    app team wants cookie support.
  reference_urls:
    - "https://github.com/org/repo/issues/342"
    - "https://github.com/org/repo/issues/358"

parsed:
  goal: "Replace JWT-based authentication with session-based authentication"
  scope:
    include:
      - "auth middleware"
      - "session storage layer"
      - "mobile API cookie support"
    exclude:
      - "user registration flow"
      - "OAuth third-party integrations"
  constraints:
    - "Must maintain backward compatibility during rollout (feature flag)"
    - "Session store must support Redis and in-memory (for tests)"
  prior_work:
    - "Spike in branch feature/session-auth-spike (abandoned 2026-02)"

open_questions: []
  # - "Should existing JWT tokens be invalidated immediately or allowed to expire?"
  # - "Is there a target date for the mobile app release?"

status: "ready"  # ready | needs_clarification
```

---

#### Stage 2: Planning

**Purpose:** Produce a high-level implementation plan from the approved intent. The plan is a strategy document, not a task list: it names the approach, the major phases, the risks, and the decision points.

**Entry criteria:**
- Intent artifact with `status: ready`.

**Process:**
- The planning agent reads the intent, the repository structure (via AGENTS.md or a codebase summary), and any referenced issues or prior work.
- It produces a plan with: approach description, major phases, architectural decisions, risk assessment, and estimated complexity.
- The plan explicitly names what it is NOT doing (anti-scope) to prevent scope creep downstream.

**Exit criteria:**
- A plan artifact exists that a reviewer can evaluate without needing to ask "what does this mean?"
- Every phase in the plan has a clear deliverable.

**Artifacts produced:**
- `plan-v1.yaml` (versioned; revisions produce `plan-v2.yaml`, etc.)

**Transition:** Always to Plan Review (Stage 3). Never auto-approved.

**Error handling:** If the planning agent cannot produce a plan (e.g., the codebase is too unfamiliar, the intent is too large), it should produce a `plan-v1.yaml` with `status: blocked` and a `blockers` list. This escalates to the human.

```yaml
# sdlc/<feature>/plan-v1.yaml
schema_version: "1.0"
id: "plan-20260328-143522-auth-redesign-v1"
feature: "auth-redesign"
intent_ref: "intent-20260328-143022-auth-redesign"
version: 1
created_at: "2026-03-28T14:35:22Z"
status: "in_review"  # draft | in_review | approved | rejected | blocked

approach: |
  Replace JWT token issuance and validation with server-side session
  management. Use a pluggable session store interface (Redis for
  production, in-memory for tests). Introduce a feature flag
  `use_session_auth` so both paths can coexist during rollout.

phases:
  - id: "phase-1"
    name: "Session store abstraction"
    deliverable: "SessionStore interface + Redis and InMemory implementations"
    estimated_complexity: "medium"
  - id: "phase-2"
    name: "Auth middleware swap"
    deliverable: "New middleware that reads session cookie; old middleware behind feature flag"
    estimated_complexity: "high"
  - id: "phase-3"
    name: "Mobile API cookie support"
    deliverable: "Set-Cookie headers on login/refresh endpoints, SameSite configuration"
    estimated_complexity: "medium"
  - id: "phase-4"
    name: "Cleanup and migration"
    deliverable: "Remove JWT code paths, migrate active sessions, update docs"
    estimated_complexity: "low"

anti_scope:
  - "OAuth provider integrations remain unchanged"
  - "User registration flow is not modified"
  - "Admin dashboard auth is out of scope for this iteration"

risks:
  - description: "Session fixation attacks if cookie configuration is wrong"
    mitigation: "Regenerate session ID on privilege escalation; enforce Secure + HttpOnly"
  - description: "Redis downtime causes auth outage"
    mitigation: "Circuit breaker with fallback to deny-all (fail closed)"

architectural_decisions:
  - decision: "Pluggable session store via interface, not direct Redis calls"
    rationale: "Testability; future flexibility for DynamoDB or Postgres-backed sessions"
  - decision: "Feature flag, not a hard cutover"
    rationale: "Allows gradual rollout and instant rollback"

blockers: []
```

---

#### Stage 3: Plan Review

**Purpose:** An independent review agent (or human) evaluates the plan for completeness, feasibility, and alignment with intent.

**Entry criteria:**
- Plan artifact with `status: in_review`.

**Process:**
- The reviewer checks: Does the plan address the full intent? Are there gaps? Are the risks realistic? Is the anti-scope appropriate? Are the phases ordered sensibly?
- The reviewer produces a structured review with per-section verdicts, not just a pass/fail.

**Exit criteria:**
- A review artifact exists with a clear verdict: `approved`, `needs_changes`, or `rejected`.
- If `needs_changes`, every finding has an `actionable` field that the plan author can act on without guessing.

**Artifacts produced:**
- `plan-review-v1.yaml`

**Transition:** If `approved`, advance to Stage 5 (Plan Approved). If `needs_changes`, advance to Stage 4 (Plan Fix). If `rejected`, escalate to human with rationale.

**Error handling:** If the review agent produces a review that is itself vague (e.g., "needs more detail" without specifying where), a meta-review step flags this and asks the reviewer to be specific. This is a guardrail against review quality degradation.

```yaml
# sdlc/<feature>/plan-review-v1.yaml
schema_version: "1.0"
id: "plan-review-20260328-144022-auth-redesign-v1"
feature: "auth-redesign"
plan_ref: "plan-20260328-143522-auth-redesign-v1"
reviewer: "review-agent-1"
created_at: "2026-03-28T14:40:22Z"

verdict: "needs_changes"  # approved | needs_changes | rejected

findings:
  - section: "phases"
    severity: "major"        # major | minor | suggestion
    finding: "Phase 2 (middleware swap) has no mention of session expiry or renewal logic"
    actionable: "Add a sub-deliverable for session TTL configuration and renewal endpoint"

  - section: "risks"
    severity: "minor"
    finding: "No risk entry for cookie size limits on mobile browsers"
    actionable: "Add risk: session cookie payload must stay under 4KB; consider opaque session ID only"

  - section: "phases"
    severity: "suggestion"
    finding: "Phase 3 could run in parallel with Phase 2 since they touch different layers"
    actionable: "Mark phases 2 and 3 as parallelizable in the execution graph"

summary: |
  Plan is structurally sound but Phase 2 has a gap around session lifecycle
  management. Address the major finding before approval.
```

---

#### Stage 4: Plan Fix

**Purpose:** The planning agent revises the plan to address review findings.

**Entry criteria:**
- A plan review artifact with `verdict: needs_changes`.
- At least one finding with `severity: major` or multiple `minor` findings.

**Process:**
- The planning agent reads the latest plan version and the review findings.
- It produces a new plan version that addresses each finding. It must explicitly reference which finding it addresses.
- Findings marked `suggestion` may be accepted or rejected with rationale.

**Exit criteria:**
- A new plan version artifact exists.
- Every `major` finding is addressed. Every `minor` finding is addressed or has a documented reason for deferral.

**Artifacts produced:**
- `plan-v2.yaml` (incremented version)

**Transition:** Back to Stage 3 (Plan Review) for re-review.

**Error handling:** If this is the Nth iteration (configurable; default N=3), escalate to human rather than continuing the loop. The escalation includes the full review history so the human can see the oscillation pattern.

---

#### Stage 5: Plan Approval

**Purpose:** Gate that confirms the plan is approved and ready for task decomposition.

**Entry criteria:**
- Plan review with `verdict: approved`.

**Process:**
- Snapshot the approved plan version.
- Record approval metadata (who approved, when, which version).

**Exit criteria:**
- An approval record exists in the plan artifact (`status: approved`, `approved_by`, `approved_at`).

**Artifacts produced:**
- Updated plan artifact (status change).

**Transition:** Automatic to Stage 6.

---

#### Stage 6: Task Breakdown

**Purpose:** Decompose the approved plan into discrete, independently executable tasks with acceptance criteria. This is the most consequential stage in the pipeline: task granularity determines whether parallel execution succeeds or devolves into merge conflicts.

**Entry criteria:**
- Approved plan.

**Process:**
- For each phase in the plan, generate one or more tasks.
- Each task must be completable by a single agent in a single session (rough heuristic: < 500 lines of change, touching <= 3 files).
- Each task has explicit acceptance criteria (testable assertions, not vibes).
- Dependencies between tasks are declared.

**Exit criteria:**
- A task manifest exists listing all tasks.
- Every task has: description, acceptance criteria, file scope, estimated complexity, and dependency list.
- The union of all task scopes covers the full plan. No phase is left unaddressed.

**Artifacts produced:**
- `tasks/task-001-session-store-interface.yaml`
- `tasks/task-002-redis-implementation.yaml`
- ... (one file per task)
- `task-manifest.yaml` (index of all tasks with dependency graph)

**Transition:** To Stage 7 (Task Review).

**Error handling:** If a phase cannot be decomposed (too ambiguous, requires exploration first), that phase gets a single "spike" task with a time-box. The spike's deliverable is a revised task breakdown for that phase.

```yaml
# sdlc/<feature>/task-manifest.yaml
schema_version: "1.0"
id: "task-manifest-20260328-150022-auth-redesign"
feature: "auth-redesign"
plan_ref: "plan-20260328-143522-auth-redesign-v2"
created_at: "2026-03-28T15:00:22Z"
status: "in_review"

tasks:
  - id: "task-001"
    ref: "tasks/task-001-session-store-interface.yaml"
    phase: "phase-1"
    depends_on: []
    estimated_complexity: "medium"
    status: "pending"

  - id: "task-002"
    ref: "tasks/task-002-redis-implementation.yaml"
    phase: "phase-1"
    depends_on: ["task-001"]
    estimated_complexity: "medium"
    status: "pending"

  - id: "task-003"
    ref: "tasks/task-003-inmemory-implementation.yaml"
    phase: "phase-1"
    depends_on: ["task-001"]
    estimated_complexity: "low"
    status: "pending"

  - id: "task-004"
    ref: "tasks/task-004-session-middleware.yaml"
    phase: "phase-2"
    depends_on: ["task-002"]
    estimated_complexity: "high"
    status: "pending"

  - id: "task-005"
    ref: "tasks/task-005-cookie-support.yaml"
    phase: "phase-3"
    depends_on: ["task-004"]
    estimated_complexity: "medium"
    status: "pending"

  - id: "task-006"
    ref: "tasks/task-006-feature-flag.yaml"
    phase: "phase-2"
    depends_on: ["task-004"]
    estimated_complexity: "low"
    status: "pending"

dependency_graph:
  # Adjacency list: task -> [tasks that must complete first]
  task-001: []
  task-002: [task-001]
  task-003: [task-001]
  task-004: [task-002]
  task-005: [task-004]
  task-006: [task-004]

parallelism_groups:
  # Tasks in the same group can execute concurrently
  - [task-002, task-003]     # both depend only on task-001
  - [task-005, task-006]     # both depend only on task-004
```

```yaml
# sdlc/<feature>/tasks/task-001-session-store-interface.yaml
schema_version: "1.0"
id: "task-001"
feature: "auth-redesign"
manifest_ref: "task-manifest-20260328-150022-auth-redesign"
phase: "phase-1"
created_at: "2026-03-28T15:00:22Z"
status: "pending"

title: "Define SessionStore interface and data types"
description: |
  Create the SessionStore interface with Create, Get, Refresh, Destroy
  methods. Define the Session data type with ID, UserID, ExpiresAt,
  CreatedAt, Metadata fields. Place in pkg/auth/session/store.go.

file_scope:
  create:
    - "pkg/auth/session/store.go"
    - "pkg/auth/session/types.go"
  modify: []
  delete: []

acceptance_criteria:
  - id: "ac-1"
    description: "SessionStore interface exists with Create, Get, Refresh, Destroy methods"
    verification: "static_analysis"  # static_analysis | unit_test | integration_test | manual
    test_command: null
  - id: "ac-2"
    description: "Session struct has required fields with correct types"
    verification: "static_analysis"
  - id: "ac-3"
    description: "Package compiles with no errors"
    verification: "unit_test"
    test_command: "go build ./pkg/auth/session/..."
  - id: "ac-4"
    description: "Interface is documented with godoc comments"
    verification: "static_analysis"

depends_on: []
estimated_complexity: "medium"
estimated_files_changed: 2
estimated_lines_changed: 80
```

---

#### Stage 7: Task Review

**Purpose:** Validate that the task breakdown is complete, that tasks are right-sized, and that acceptance criteria are testable.

**Entry criteria:**
- Task manifest with `status: in_review`.

**Process:**
- Check coverage: does every plan phase have at least one task?
- Check granularity: is any task too large (heuristic: > 500 LOC, > 5 files, complexity "high" with no sub-tasks)?
- Check acceptance criteria: is every criterion mechanically verifiable?
- Check dependencies: are there circular dependencies? Is the critical path reasonable?

**Exit criteria:**
- A task review artifact with a verdict.

**Artifacts produced:**
- `task-review-v1.yaml`

**Transition:** Same pattern as plan review: approved goes to Stage 9, needs_changes goes to Stage 8, rejected escalates.

---

#### Stage 8: Task Fix

Mirrors Stage 4 (Plan Fix) but for task artifacts. The breakdown agent revises tasks based on review findings. Same iteration limit applies (default: 3 rounds).

---

#### Stage 9: Prioritization

**Purpose:** Assign execution order based on dependencies, risk, and value.

**Entry criteria:**
- Approved task manifest.

**Process:**
- Topological sort on the dependency graph to determine valid execution orders.
- Within a parallelism group, prioritize by: (1) tasks on the critical path, (2) higher risk tasks first (fail-fast principle), (3) tasks with more dependents.
- Assign concurrency slots based on available agent capacity and resource constraints (e.g., if two tasks modify the same file, they cannot run concurrently regardless of the dependency graph).

**Exit criteria:**
- An execution plan with ordered batches and assigned concurrency.

**Artifacts produced:**
- `execution-plan.yaml`

**Transition:** Automatic to Stage 10.

```yaml
# sdlc/<feature>/execution-plan.yaml
schema_version: "1.0"
id: "exec-plan-20260328-151022-auth-redesign"
feature: "auth-redesign"
manifest_ref: "task-manifest-20260328-150022-auth-redesign"
created_at: "2026-03-28T15:10:22Z"

max_concurrency: 3  # Maximum parallel agents

batches:
  - batch: 1
    tasks: [task-001]
    concurrency: 1
    rationale: "Foundation interface; all other tasks depend on this"

  - batch: 2
    tasks: [task-002, task-003]
    concurrency: 2
    rationale: "Both implement the interface; no file overlap; can parallelize"

  - batch: 3
    tasks: [task-004]
    concurrency: 1
    rationale: "Critical path; depends on Redis implementation being available"

  - batch: 4
    tasks: [task-005, task-006]
    concurrency: 2
    rationale: "Independent features on top of middleware; parallelizable"

critical_path: [task-001, task-002, task-004, task-005]
estimated_total_duration_minutes: 45

resource_locks:
  # Files that only one task may modify at a time
  - file: "go.mod"
    held_by: null
  - file: "go.sum"
    held_by: null
```

---

#### Stage 10: Execution Graph (DAG Construction)

**Purpose:** Transform the execution plan into a runnable DAG with concrete agent assignments, working directories (worktrees or branches), and rollback points.

**Entry criteria:**
- Execution plan exists.

**Process:**
- For each task, create a git worktree or branch.
- Assign an agent type (Claude Code for implementation, Codex for review, etc.).
- Set up the execution environment: context window contents, tool permissions, file access scope.
- Create a pre-execution git tag for rollback.

**Exit criteria:**
- Every task has an assigned agent, branch, and rollback tag.

**Artifacts produced:**
- Updated `execution-plan.yaml` with agent assignments.

**Transition:** Automatic to Stage 11.

---

#### Stage 11: Parallel Execution via Subagents

**Purpose:** Execute tasks concurrently within batch boundaries using the DPPM pattern (Decompose, Plan in Parallel, Merge).

**Entry criteria:**
- Current batch has all dependency tasks in `completed` status.

**Process (per task):**
- Spin up a subagent with: task specification, relevant codebase context, tool access.
- The subagent works in its own branch/worktree.
- On completion, the subagent reports: files changed, tests run, and a self-assessment.
- A semaphore controls total concurrency; tasks queue if at capacity.

**Exit criteria (per task):**
- Code changes committed to task branch.
- Self-reported completion from the subagent.
- Entry into Stage 12 (per-task quality loop).

**Artifacts produced:**
- `tasks/task-NNN-execution-log.yaml` (per task)
- Git commits on task branch.

**Transition:** Each completed task flows into Stage 12 independently.

**Error handling:**
- If a subagent crashes or times out, the task is retried once with a fresh agent.
- If it fails again, the task is marked `blocked` and the batch continues without it. Dependent tasks are deferred.
- All failures are logged with full context for human review.

---

#### Stage 12: Per-Task Quality Loop (Simplify, Test, Review)

**Purpose:** Each task output goes through a three-step quality check before it can be considered complete. This is where the Anthropic "premature victory" failure mode is explicitly countered.

**Entry criteria:**
- Task has committed code on its branch.

**Process:**
1. **Simplify:** A simplification agent reviews the code for unnecessary complexity, duplication, and deviation from project conventions. It may make changes.
2. **Test:** Run the acceptance criteria test commands. Collect pass/fail results with output.
3. **Review:** A review agent examines the diff against the task specification. It checks: does the code match the task description? Are the acceptance criteria actually met (not just "tests pass" but "tests test the right thing")? Are there regressions?

**Exit criteria:**
- All acceptance criteria tests pass.
- Review verdict is `approved`.
- No simplification agent changes remain uncommitted.

**Artifacts produced:**
- `tasks/task-NNN-quality-report.yaml`

**Transition:** If all checks pass, task status becomes `completed`. If not, advance to Stage 13 (Fix Iteration).

```yaml
# sdlc/<feature>/tasks/task-001-quality-report.yaml
schema_version: "1.0"
id: "quality-20260328-152022-task-001"
task_ref: "task-001"
feature: "auth-redesign"
created_at: "2026-03-28T15:20:22Z"
iteration: 1

simplification:
  status: "pass"
  changes_made: 0
  notes: "Code is clean; no simplification needed"

acceptance_tests:
  - criteria_ref: "ac-1"
    status: "pass"
    output: "Interface found with 4 methods"
  - criteria_ref: "ac-2"
    status: "pass"
    output: "Struct has 5 required fields"
  - criteria_ref: "ac-3"
    status: "pass"
    output: "go build ./pkg/auth/session/... completed with exit code 0"
  - criteria_ref: "ac-4"
    status: "fail"
    output: "Destroy method missing godoc comment"

review:
  verdict: "needs_changes"
  findings:
    - severity: "minor"
      finding: "Destroy method undocumented"
      actionable: "Add godoc comment explaining session invalidation behavior"

overall_status: "needs_fix"  # pass | needs_fix | blocked
```

---

#### Stage 13: Fix Iteration

**Purpose:** The implementing agent fixes issues found in the quality loop.

**Entry criteria:**
- Quality report with `overall_status: needs_fix`.

**Process:**
- Agent receives the quality report findings and makes targeted fixes.
- Only the failing criteria and review findings are addressed; no scope expansion.
- After fixes, flow returns to Stage 12 for re-evaluation.

**Exit criteria:**
- New commit addressing the findings.
- Re-entry into Stage 12.

**Error handling:** Iteration limit per task (default: 5). After the limit, the task is marked `stuck` and escalated. The escalation bundle includes: the original task spec, all quality reports, and all diffs.

---

#### Stage 14: Gap Detection

**Purpose:** After all tasks in the manifest are `completed`, verify that the overall feature actually works end-to-end. Individual task completion does not guarantee integration correctness.

**Entry criteria:**
- All tasks in the manifest have `status: completed`.

**Process:**
- Merge all task branches into a feature integration branch.
- Run the full test suite (not just per-task tests).
- A gap detection agent reviews the merged result against the original plan and intent.
- It specifically checks: are there plan phases with no corresponding code? Are there integration seams that no task covered? Did scope drift create inconsistencies?

**Exit criteria:**
- Gap report with a verdict: `complete` or `gaps_found`.

**Artifacts produced:**
- `gap-report.yaml`

**Transition:** If `complete`, advance to Stage 15. If `gaps_found`, generate new tasks and re-enter Stage 6 with a scoped addendum to the plan.

```yaml
# sdlc/<feature>/gap-report.yaml
schema_version: "1.0"
id: "gap-report-20260328-160022-auth-redesign"
feature: "auth-redesign"
plan_ref: "plan-20260328-143522-auth-redesign-v2"
created_at: "2026-03-28T16:00:22Z"

integration_tests:
  total: 12
  passed: 11
  failed: 1
  details:
    - test: "test_session_renewal_extends_expiry"
      status: "failed"
      output: "Expected new expiry > old expiry, got equal"

coverage_check:
  plan_phases_covered: ["phase-1", "phase-2", "phase-3"]
  plan_phases_missing: []

gaps:
  - id: "gap-1"
    description: "Session renewal in middleware does not call store.Refresh()"
    severity: "major"
    suggested_fix: "Add store.Refresh() call in the session validation middleware"
    new_task_required: true

verdict: "gaps_found"  # complete | gaps_found

new_tasks:
  - title: "Fix session renewal in middleware"
    description: "Middleware validates session but does not call Refresh to extend TTL"
    phase: "phase-2"
    estimated_complexity: "low"
```

---

#### Stage 15: Completion

**Purpose:** Final gate. Feature is merged and documented.

**Entry criteria:**
- Gap report with `verdict: complete`.

**Process:**
- Squash-merge or merge the feature integration branch into the target branch.
- Generate a completion summary.
- Archive artifacts (or mark them as historical).

**Exit criteria:**
- Feature branch merged.
- Completion artifact exists.

**Artifacts produced:**
- `completion.yaml`

**Transition:** Pipeline terminates. Human is notified.

---

### 1.3 Stage Transition Summary

| From | To | Trigger | Gate Type |
|------|----|---------|-----------|
| 1. Intent | 2. Plan | Intent is `ready` | Auto (or human if questions exist) |
| 2. Plan | 3. Plan Review | Plan produced | Auto |
| 3. Plan Review | 4. Plan Fix | Verdict `needs_changes` | Auto |
| 3. Plan Review | 5. Plan Approved | Verdict `approved` | Human confirmation |
| 4. Plan Fix | 3. Plan Review | New plan version | Auto |
| 5. Plan Approved | 6. Task Breakdown | Approval recorded | Auto |
| 6. Task Breakdown | 7. Task Review | Tasks produced | Auto |
| 7. Task Review | 8. Task Fix | Verdict `needs_changes` | Auto |
| 7. Task Review | 9. Prioritization | Verdict `approved` | Auto |
| 8. Task Fix | 7. Task Review | Revised tasks | Auto |
| 9. Prioritization | 10. Execution Graph | Priority assigned | Auto |
| 10. Execution Graph | 11. Execution | DAG ready | Auto |
| 11. Execution | 12. Quality Loop | Task code committed | Auto |
| 12. Quality Loop | 13. Fix Iteration | Quality `needs_fix` | Auto |
| 12. Quality Loop | (next batch or 14) | Quality `pass` | Auto |
| 13. Fix Iteration | 12. Quality Loop | Fix committed | Auto |
| 14. Gap Detection | 6. Task Breakdown (scoped) | Gaps found | Auto |
| 14. Gap Detection | 15. Completion | No gaps | Human confirmation |

---

## 2. Self-Correction and Reflection Loops

### 2.1 The Core Problem

LLM agents fail in predictable ways: they declare victory too early, they oscillate between two solutions without converging, and they degrade review quality under iteration pressure ("LGTM on round 4 because I'm tired of reviewing"). A self-correction system must address all three failure modes.

### 2.2 Review Agent Architecture

Review agents are structurally separated from implementing agents. They never share conversation context. This is not optional: if the reviewer has seen the implementer's reasoning, it anchors on that reasoning and produces less independent reviews.

**Review agent input:**
- The specification (task or plan).
- The artifact to review (code diff, plan document).
- The acceptance criteria.
- Previous review findings (for iteration rounds > 1).

**Review agent output must be structured:**

```yaml
review_output:
  verdict: "needs_changes"
  confidence: 0.85           # How confident the reviewer is in its verdict
  findings:
    - id: "f-1"
      severity: "major"
      location: "pkg/auth/session/store.go:42"
      finding: "Refresh method does not validate that the session exists before extending TTL"
      actionable: "Add existence check: if session not found, return ErrSessionNotFound"
      evidence: "Line 42 calls store.Set() without prior Get(); a non-existent session ID would create a phantom session"
  meta:
    review_duration_seconds: 12
    files_examined: 2
    criteria_checked: 4
```

The key field is `actionable`. A finding without an actionable remedy is useless. The harness should reject review outputs where `actionable` is empty or is a restatement of the `finding`.

**Validation of review quality:**
- After generating the review, a lightweight meta-check verifies:
  - Every finding has a non-empty, non-tautological `actionable` field.
  - `evidence` references actual code locations, not vague gestures.
  - The verdict is consistent with the findings (e.g., `approved` with `major` findings is contradictory).
- If the meta-check fails, the review is regenerated with a prompt that includes the specific meta-check failures.

### 2.3 Iteration Limits

Every loop in the pipeline has a hard iteration ceiling. These are configurable but must have defaults.

| Loop | Default Max Iterations | Escalation |
|------|----------------------|------------|
| Plan review/fix | 3 | Human with full review history |
| Task review/fix | 3 | Human with full review history |
| Per-task quality (Stage 12-13) | 5 | Mark task `stuck`, continue others |
| Gap detection re-entry | 2 | Human with gap reports |

**Why these numbers:** Plan and task review loops are expensive (full document re-generation). 3 rounds is enough for converging fixes; if you haven't converged in 3 rounds, there is a fundamental disagreement that an agent cannot resolve. Per-task quality gets 5 rounds because fixes are usually small and converge quickly; the higher limit prevents unnecessary escalation on minor issues. Gap detection gets only 2 rounds because each round is a full re-execution; more than 2 suggests the plan itself is flawed.

### 2.4 Premature Victory Detection

This is the most insidious failure mode (identified explicitly by Anthropic's research). The agent says "done" when it is not done. Detection strategies:

**Strategy 1: Acceptance criteria as boolean gates, not self-assessment.**
The agent does not decide if it passed. The harness runs the test commands and checks the output. If `ac-3` says "package compiles" and the test command is `go build ./...`, the harness runs the build and checks the exit code. The agent's opinion is irrelevant.

**Strategy 2: Diff-vs-spec verification.**
After an agent declares completion, a separate verification agent reads the diff and the task specification side by side and answers specific questions:
- "Does the diff create or modify every file listed in `file_scope`?"
- "For each acceptance criterion, identify the specific code that satisfies it."
- If the verification agent cannot point to specific code for a criterion, the task is not done.

**Strategy 3: Regression canary.**
Before marking a task complete, run the pre-existing test suite on the task branch. If any test that passed on main now fails, the task introduced a regression, regardless of what the agent claims.

**Strategy 4: Output size sanity check.**
If a task estimates 80 lines of change and the agent produces 5 lines, or produces 800 lines, flag for review. Significant deviation from estimates (configurable threshold: > 5x or < 0.1x) suggests either misunderstanding or scope creep.

### 2.5 Convergence Criteria

"How do you know when something is done?" This is a concrete checklist, not a feeling:

1. All acceptance criteria test commands pass (exit code 0, expected output matches).
2. The review agent verdict is `approved` with no `major` findings.
3. The diff touches only files within the declared `file_scope` (or has an explicit justification for scope expansion).
4. The pre-existing test suite passes on the task branch (no regressions).
5. The output size is within the sanity-check bounds.
6. If the task has downstream dependents, a smoke check confirms the dependent task's interface assumptions still hold.

All six must be true. Any single failure sends the task back to fix iteration.

### 2.6 Oscillation Detection

Sometimes an agent "fixes" finding A by breaking condition B, then "fixes" B by re-introducing A. This oscillation can consume all iteration rounds without progress.

**Detection:** After each fix iteration, hash the set of active findings (severity + location + finding text). If a hash seen in a previous iteration reappears, the agent is oscillating. Escalate immediately rather than burning remaining iterations.

**Prevention:** When sending fix instructions to the agent, include the full history of previous findings and fixes, with an explicit instruction: "The following findings were present in previous iterations and were fixed. Ensure your changes do not re-introduce them."

---

## 3. Human-in-the-Loop Design

### 3.1 Gate Classification

The pipeline has three types of gates:

**Hard gates (always require human):**
- Plan approval (Stage 5): The human must confirm the plan before significant compute is spent on execution.
- Final completion (Stage 15): The human must confirm the feature is ready to merge.
- Escalations from iteration limits.

**Soft gates (human notified, auto-proceeds after timeout):**
- Task review approval: If the task breakdown review passes with no `major` findings, the human has a configurable window (default: 30 minutes) to intervene. If they do not, the pipeline proceeds.
- Per-task completion: Human can subscribe to notifications but does not block.

**Auto gates (no human involvement):**
- All fix iterations (Stages 4, 8, 13).
- Prioritization and DAG construction (Stages 9, 10).
- Batch transitions during execution (Stage 11).
- Gap detection (Stage 14) on the first pass. Second pass escalates.

### 3.2 Decision Presentation

Humans should never face open-ended "what do you think?" prompts. Every human touchpoint presents:

1. **A specific question** with enumerated options.
2. **Context** summarized to the minimum needed for the decision.
3. **A recommended action** from the pipeline.
4. **The consequence** of each option.

```yaml
# Example human decision prompt
decision:
  id: "decision-20260328-144522-plan-approval"
  feature: "auth-redesign"
  stage: "plan_approval"
  urgency: "normal"         # critical | normal | low
  timeout_minutes: null     # null = no auto-proceed

  question: "Approve the implementation plan for auth-redesign?"

  context:
    plan_version: 2
    review_rounds: 2
    plan_summary: "Replace JWT with session-based auth using pluggable store + feature flag"
    open_risks:
      - "Redis downtime → auth outage (mitigated by circuit breaker)"
    review_history:
      - round: 1
        verdict: "needs_changes"
        major_findings: 1
      - round: 2
        verdict: "approved"
        major_findings: 0

  options:
    - id: "approve"
      label: "Approve plan and begin task breakdown"
      consequence: "Pipeline will decompose into ~6 tasks and begin execution"
      recommended: true
    - id: "reject"
      label: "Reject plan and provide new direction"
      consequence: "Pipeline returns to Stage 2 with your feedback"
      requires_input: true
      input_prompt: "What should change?"
    - id: "defer"
      label: "Defer decision"
      consequence: "Pipeline pauses. You will be reminded in 24 hours."

  artifacts:
    - "sdlc/auth-redesign/plan-v2.yaml"
    - "sdlc/auth-redesign/plan-review-v2.yaml"
```

### 3.3 State Preservation During Human Review

When the pipeline pauses for human input, the following must be preserved:

- **Pipeline state:** Current stage, pending decisions, iteration counts. Stored in `sdlc/<feature>/pipeline-state.yaml`.
- **Agent context:** Not preserved. Agents are stateless between invocations. All context is reconstructed from artifacts. This is intentional: it avoids stale context and forces the artifact system to be the single source of truth.
- **Git state:** All branches, worktrees, and tags remain. No cleanup during human review.
- **Timers:** Any timeout clocks are paused or reset when the human begins interacting.

```yaml
# sdlc/<feature>/pipeline-state.yaml
schema_version: "1.0"
feature: "auth-redesign"
updated_at: "2026-03-28T14:45:22Z"

current_stage: "plan_approval"
status: "waiting_for_human"

pending_decision: "decision-20260328-144522-plan-approval"

iteration_counts:
  plan_review: 2
  task_review: 0
  gap_detection: 0

completed_stages:
  - stage: "intent_capture"
    completed_at: "2026-03-28T14:30:22Z"
  - stage: "planning"
    completed_at: "2026-03-28T14:35:22Z"
  - stage: "plan_review"
    completed_at: "2026-03-28T14:45:22Z"

task_statuses: {}  # Populated after task breakdown
```

### 3.4 Resumption After Human Input

When the human responds:

1. Load `pipeline-state.yaml` to determine where we paused.
2. Load the pending decision and match the human's choice to an option.
3. If the option `requires_input`, validate the input is non-empty and coherent.
4. Record the decision in the pipeline state.
5. Advance to the appropriate next stage.
6. Agents are spun up fresh. They read only artifacts, not prior agent conversations.

This design means the pipeline can survive arbitrarily long human delays (days, weeks) without corruption. There is no in-memory state to lose.

### 3.5 Urgency Levels and Timeout Handling

| Urgency | Notification | Auto-proceed | Reminder |
|---------|-------------|-------------|----------|
| `critical` | Push notification / alert | Never | Every 1 hour |
| `normal` | Standard notification | After 30 min (soft gates only) | Every 24 hours |
| `low` | Batch digest | After 4 hours (soft gates only) | Every 72 hours |

Hard gates never auto-proceed regardless of urgency. The urgency level affects only the notification channel and reminder frequency.

---

## 4. Artifact System

### 4.1 Folder Structure

All pipeline artifacts live under a single `sdlc/` directory at the project root. Each feature gets its own subdirectory. Artifacts are YAML files with consistent naming.

```
sdlc/
  auth-redesign/                              # Feature directory
    intent.yaml                                # Stage 1 output
    plan-v1.yaml                               # Stage 2 output (version 1)
    plan-v2.yaml                               # Stage 4 output (revised)
    plan-review-v1.yaml                        # Stage 3 output (review of plan-v1)
    plan-review-v2.yaml                        # Stage 3 output (review of plan-v2)
    task-manifest.yaml                         # Stage 6 output
    task-review-v1.yaml                        # Stage 7 output
    execution-plan.yaml                        # Stage 9 output
    gap-report.yaml                            # Stage 14 output
    completion.yaml                            # Stage 15 output
    pipeline-state.yaml                        # Current pipeline state (mutable)
    decisions/                                 # Human decisions
      decision-20260328-144522-plan-approval.yaml
    tasks/                                     # Per-task artifacts
      task-001-session-store-interface.yaml     # Task specification
      task-001-execution-log.yaml              # Agent execution log
      task-001-quality-report.yaml             # Stage 12 output
      task-001-quality-report-v2.yaml          # Stage 12 (after fix)
      task-002-redis-implementation.yaml
      task-002-execution-log.yaml
      task-002-quality-report.yaml
      ...
    logs/                                      # Pipeline orchestration logs
      20260328-143022-stage-intent.yaml
      20260328-143522-stage-plan.yaml
      20260328-144022-stage-plan-review.yaml
      ...
```

### 4.2 File Naming Conventions

**Feature directories:** kebab-case, matching the feature identifier.

**Versioned artifacts:** `<type>-v<N>.yaml` where N is a monotonically increasing integer. Examples: `plan-v1.yaml`, `plan-review-v2.yaml`, `task-review-v1.yaml`.

**Task artifacts:** `task-<NNN>-<slug>.yaml` where NNN is a zero-padded task number and slug is a kebab-case short name. Examples: `task-001-session-store-interface.yaml`, `task-001-quality-report.yaml`.

**Timestamped artifacts (logs, decisions):** `<YYYYMMDD>-<HHMMSS>-<description>.yaml`. Examples: `20260328-143022-stage-intent.yaml`, `decision-20260328-144522-plan-approval.yaml`.

**The mutable singleton:** `pipeline-state.yaml` is the only artifact that is updated in place rather than versioned. It represents current state, not history.

### 4.3 Core YAML Schemas

Every artifact shares a common header:

```yaml
# Common header (present in every artifact)
schema_version: "1.0"        # Schema version for forward compatibility
id: "<unique-identifier>"     # Globally unique; format varies by type
feature: "<feature-slug>"     # Which feature this belongs to
created_at: "<ISO-8601>"      # When this artifact was created
```

#### Intent Schema

```yaml
schema_version: "1.0"
id: "intent-<datetime>-<feature>"
feature: "<feature>"
created_at: "<ISO-8601>"
source:
  type: "user_input | issue_tracker | conversation"
  raw_text: "<string>"
  reference_urls: ["<url>"]
parsed:
  goal: "<string>"
  scope:
    include: ["<string>"]
    exclude: ["<string>"]
  constraints: ["<string>"]
  prior_work: ["<string>"]
open_questions: ["<string>"]
status: "ready | needs_clarification"
```

#### Plan Schema

```yaml
schema_version: "1.0"
id: "plan-<datetime>-<feature>-v<N>"
feature: "<feature>"
intent_ref: "<intent-id>"
version: <integer>
created_at: "<ISO-8601>"
status: "draft | in_review | approved | rejected | blocked"
approach: "<string>"
phases:
  - id: "<phase-id>"
    name: "<string>"
    deliverable: "<string>"
    estimated_complexity: "low | medium | high"
anti_scope: ["<string>"]
risks:
  - description: "<string>"
    mitigation: "<string>"
architectural_decisions:
  - decision: "<string>"
    rationale: "<string>"
blockers: ["<string>"]
approved_by: "<string | null>"     # Set when approved
approved_at: "<ISO-8601 | null>"   # Set when approved
```

#### Review Schema (used for plan reviews, task reviews, and quality reviews)

```yaml
schema_version: "1.0"
id: "<review-type>-<datetime>-<feature>-v<N>"
feature: "<feature>"
target_ref: "<id-of-artifact-being-reviewed>"
reviewer: "<agent-id | human-id>"
created_at: "<ISO-8601>"
verdict: "approved | needs_changes | rejected"
confidence: <float 0.0-1.0>
findings:
  - id: "<finding-id>"
    severity: "major | minor | suggestion"
    location: "<file:line | section-name>"
    finding: "<string>"
    actionable: "<string>"
    evidence: "<string>"
summary: "<string>"
meta:
  review_duration_seconds: <integer>
  files_examined: <integer>
  criteria_checked: <integer>
```

#### Task Schema

```yaml
schema_version: "1.0"
id: "task-<NNN>"
feature: "<feature>"
manifest_ref: "<manifest-id>"
phase: "<phase-id>"
created_at: "<ISO-8601>"
status: "pending | in_progress | completed | stuck | blocked"
title: "<string>"
description: "<string>"
file_scope:
  create: ["<path>"]
  modify: ["<path>"]
  delete: ["<path>"]
acceptance_criteria:
  - id: "<ac-id>"
    description: "<string>"
    verification: "static_analysis | unit_test | integration_test | manual"
    test_command: "<string | null>"
depends_on: ["<task-id>"]
estimated_complexity: "low | medium | high"
estimated_files_changed: <integer>
estimated_lines_changed: <integer>
assigned_agent: "<agent-id | null>"
branch: "<branch-name | null>"
started_at: "<ISO-8601 | null>"
completed_at: "<ISO-8601 | null>"
iteration_count: <integer>
```

#### Execution Log Schema

```yaml
schema_version: "1.0"
id: "exec-log-<datetime>-<task-id>"
task_ref: "<task-id>"
feature: "<feature>"
agent_id: "<agent-id>"
branch: "<branch-name>"
started_at: "<ISO-8601>"
completed_at: "<ISO-8601 | null>"
status: "running | completed | failed | timed_out"
steps:
  - timestamp: "<ISO-8601>"
    action: "<string>"
    detail: "<string>"
    duration_seconds: <number>
files_changed:
  - path: "<file-path>"
    action: "created | modified | deleted"
    lines_added: <integer>
    lines_removed: <integer>
commits:
  - sha: "<commit-sha>"
    message: "<string>"
    timestamp: "<ISO-8601>"
errors: ["<string>"]
agent_self_assessment:
  confidence: <float 0.0-1.0>
  notes: "<string>"
```

#### Pipeline Stage Log Schema

```yaml
schema_version: "1.0"
id: "stage-log-<datetime>-<stage-name>"
feature: "<feature>"
stage: "<stage-name>"
started_at: "<ISO-8601>"
completed_at: "<ISO-8601 | null>"
status: "running | completed | failed | escalated"
input_artifacts: ["<artifact-id>"]
output_artifacts: ["<artifact-id>"]
duration_seconds: <number>
agent_id: "<agent-id | null>"
notes: "<string>"
```

### 4.4 Cross-Referencing

Artifacts reference each other via their `id` fields using `*_ref` fields. The reference graph is always a DAG (no circular references):

```
intent
  └── plan-v1 (intent_ref → intent)
        └── plan-review-v1 (target_ref → plan-v1)
              └── plan-v2 (references plan-review-v1 findings)
                    └── plan-review-v2 (target_ref → plan-v2)
                          └── task-manifest (plan_ref → plan-v2)
                                ├── task-001 (manifest_ref → task-manifest)
                                │     ├── task-001-execution-log (task_ref → task-001)
                                │     └── task-001-quality-report (task_ref → task-001)
                                ├── task-002
                                │     └── ...
                                └── execution-plan (manifest_ref → task-manifest)
```

**Querying cross-references:** A simple script can parse all YAML files in a feature directory and build the reference graph. This enables queries like:
- "Show me all findings for task-003 across all review rounds."
- "What is the lineage of the current plan?" (intent -> plan-v1 -> review -> plan-v2 -> review -> approved)
- "Which tasks are blocked and why?"

### 4.5 Versioning Strategy

Artifacts are **immutable once created**, with one exception (`pipeline-state.yaml`). Revisions produce new files with incremented version numbers rather than overwriting existing files. This provides a complete audit trail.

**Why not git-only versioning?** Git tracks file history, but querying across versions requires checking out old commits. Having all versions as separate files means any tool can compare plan-v1 and plan-v2 by reading two files in the working tree. The trade-off is disk space, which is negligible for YAML files.

**Tombstoning:** Artifacts are never deleted. If a task is cancelled, its status is set to `cancelled` with a `cancelled_reason` field. It remains on disk for audit.

### 4.6 Querying and Dashboards

The artifact system is designed to be queryable by simple tools: `yq`, `jq` (after YAML-to-JSON conversion), or a lightweight Python/Node script.

**Example queries:**

```bash
# All tasks with status "stuck" for a feature
yq '.status' sdlc/auth-redesign/tasks/task-*.yaml | grep stuck

# Total iteration count across all tasks
yq '.iteration_count' sdlc/auth-redesign/tasks/task-*.yaml | paste -sd+ | bc

# All major findings across all reviews
yq '.findings[] | select(.severity == "major")' sdlc/auth-redesign/*-review-*.yaml

# Current pipeline stage
yq '.current_stage' sdlc/auth-redesign/pipeline-state.yaml
```

**Dashboard data model:**

```yaml
# Generated summary (not a stored artifact; computed on demand)
dashboard:
  feature: "auth-redesign"
  pipeline_stage: "execution"
  plan_version: 2
  plan_review_rounds: 2
  total_tasks: 6
  tasks_completed: 3
  tasks_in_progress: 2
  tasks_pending: 0
  tasks_stuck: 1
  tasks_blocked: 0
  total_iterations: 8
  total_findings: 12
  open_major_findings: 1
  elapsed_time_minutes: 45
  estimated_remaining_minutes: 20
  blocking_items:
    - "task-004 is stuck after 5 fix iterations"
```

---

## 5. Progress Tracking and Transparency

### 5.1 Real-Time Progress Visibility

The pipeline must be observable at three levels of detail:

**Level 1 -- Feature overview (for stakeholders):**
- Current stage name.
- Percentage of tasks completed.
- Estimated time remaining.
- Blocking issues count.
- One-line status: "Executing batch 3 of 4. 3/6 tasks complete. 1 task stuck."

**Level 2 -- Stage detail (for technical leads):**
- Which tasks are running, on which branches.
- Current iteration count for each active task.
- Review findings summary.
- Dependency graph with completion status.

**Level 3 -- Agent trace (for debugging):**
- Per-agent execution logs with timestamps.
- Tool calls made by each agent.
- Token usage per agent invocation.
- Full quality reports with test output.

### 5.2 Log Structure

Logs are stored at two levels:

**Pipeline orchestration logs** (in `sdlc/<feature>/logs/`): Record stage transitions, human decisions, and system events. These are the "what happened and when" record.

```yaml
# sdlc/<feature>/logs/20260328-151022-batch-2-start.yaml
schema_version: "1.0"
id: "log-20260328-151022-batch-2-start"
feature: "auth-redesign"
timestamp: "2026-03-28T15:10:22Z"
event_type: "batch_start"     # stage_start | stage_end | batch_start | batch_end
                               # task_start | task_end | escalation | human_decision
                               # error | retry | timeout
level: "info"                  # debug | info | warn | error

data:
  batch: 2
  tasks: [task-002, task-003]
  concurrency: 2
  agents_assigned:
    task-002: "agent-claude-7a3f"
    task-003: "agent-claude-9b2e"

message: "Starting batch 2 with 2 parallel tasks"
```

**Agent execution logs** (in `sdlc/<feature>/tasks/`): Record what each agent did during task execution. These are the "how" record. (Schema defined in Section 4.3 under Execution Log Schema.)

### 5.3 Status Report Generation

The pipeline should be able to generate a human-readable status report at any time. The report is computed from artifacts, not from memory.

```yaml
# Status report template (generated, not stored)
report:
  generated_at: "2026-03-28T15:30:00Z"
  feature: "auth-redesign"

  overview:
    status: "In Progress"
    current_stage: "Parallel Execution (Batch 3)"
    started_at: "2026-03-28T14:30:22Z"
    elapsed_minutes: 60
    estimated_remaining_minutes: 15

  plan:
    version: 2
    review_rounds: 2
    approved_at: "2026-03-28T14:50:00Z"

  tasks:
    total: 6
    by_status:
      completed: 3
      in_progress: 1
      stuck: 1
      pending: 1
    details:
      - id: task-001
        title: "Define SessionStore interface"
        status: completed
        iterations: 1
        duration_minutes: 8
      - id: task-002
        title: "Redis implementation"
        status: completed
        iterations: 2
        duration_minutes: 12
      - id: task-003
        title: "InMemory implementation"
        status: completed
        iterations: 1
        duration_minutes: 5
      - id: task-004
        title: "Session middleware"
        status: stuck
        iterations: 5
        issue: "Oscillating between two approaches for session renewal"
      - id: task-005
        title: "Cookie support"
        status: pending
        blocked_by: [task-004]
      - id: task-006
        title: "Feature flag"
        status: in_progress
        iterations: 1

  quality:
    total_review_findings: 8
    findings_resolved: 6
    findings_open: 2
    total_acceptance_tests: 18
    tests_passing: 15
    tests_failing: 3

  issues:
    - severity: "high"
      description: "task-004 is stuck after 5 iterations; escalation pending"
    - severity: "low"
      description: "task-002 took 2 iterations due to missing error handling"

  human_actions_needed:
    - "Review and unblock task-004 (session middleware)"
```

### 5.4 Metrics

The following metrics should be tracked per feature and aggregated across features over time. They serve both operational purposes (is this pipeline run healthy?) and improvement purposes (is the system getting better?).

#### Per-Feature Metrics

| Metric | Source | Purpose |
|--------|--------|---------|
| Total wall-clock time | `pipeline-state.yaml` | End-to-end speed |
| Time per stage | Stage logs | Identify bottlenecks |
| Plan review rounds | Count of `plan-review-v*.yaml` | Plan quality trend |
| Task review rounds | Count of `task-review-v*.yaml` | Task breakdown quality trend |
| Tasks per feature | `task-manifest.yaml` | Decomposition granularity |
| Fix iterations per task | `task-*.yaml` `iteration_count` | Agent coding quality |
| Stuck task rate | Tasks with `status: stuck` / total | Agent capability limit |
| Findings per review | Review artifacts | Review thoroughness |
| Acceptance test pass rate (first attempt) | Quality reports | Code quality on first try |
| Gap detection rate | Gap reports | Decomposition completeness |
| Human intervention count | Decision artifacts | Automation maturity |
| Lines of code per task | Execution logs | Granularity calibration |

#### Aggregate / Trend Metrics

| Metric | Computation | Signal |
|--------|-------------|--------|
| Average fix iterations | Mean across all tasks, all features | System improving? |
| Plan approval on first review | % of features where plan-v1 is approved | Planning agent quality |
| Time in human-wait | Sum of all pause durations | Human bottleneck |
| Escalation rate | Escalations / total loops | Self-correction effectiveness |
| Agent token usage per task | Sum of tokens across all invocations for a task | Cost tracking |
| Reuse of prior patterns | Features that reference prior_work | Institutional learning |

#### Metric Storage

Metrics are computed from artifacts, not stored separately. The artifact system IS the metrics store. A metrics dashboard reads the `sdlc/` directory tree and aggregates. This avoids a separate metrics database that can drift from reality.

If query performance becomes an issue (many features, many tasks), a periodic job can materialize a summary file:

```yaml
# sdlc/.metrics-cache.yaml (generated, gitignored)
generated_at: "2026-03-28T16:00:00Z"
features:
  auth-redesign:
    status: "in_progress"
    started: "2026-03-28T14:30:22Z"
    tasks_total: 6
    tasks_completed: 3
    avg_iterations: 2.1
    escalations: 1
  payment-refactor:
    status: "completed"
    started: "2026-03-25T09:00:00Z"
    completed: "2026-03-25T14:30:00Z"
    tasks_total: 8
    tasks_completed: 8
    avg_iterations: 1.4
    escalations: 0
```

---

## Design Trade-offs and Open Questions

### Trade-offs Accepted

1. **Artifact verbosity vs. debuggability.** The artifact system produces many files. This is intentional: when something goes wrong at 2 AM, you want a complete paper trail, not a minimal one. The cost is disk space and visual clutter, both of which are cheap compared to debugging a black box.

2. **Stateless agents vs. context efficiency.** Agents are restarted fresh for each invocation and reconstruct context from artifacts. This wastes tokens (re-reading the plan, task spec, prior findings) but eliminates an entire class of bugs: stale context, hallucinated prior conversation, and context window overflow. The artifact system is sized so that any single agent invocation needs at most a few artifacts in its context.

3. **Iteration limits vs. completion rate.** Hard iteration limits mean some tasks will be escalated rather than solved. This is preferable to infinite loops, but it means the pipeline will sometimes produce "stuck" tasks that require human intervention. The mitigation is better task granularity over time (tracked by the metrics system).

4. **Human gates vs. speed.** Plan approval and final completion require human confirmation. This slows the pipeline but prevents catastrophic errors (merging a fundamentally wrong feature). The soft gate pattern (auto-proceed on timeout) provides a middle ground for lower-risk decisions.

5. **File-per-version vs. git history.** Storing plan-v1, plan-v2, etc. as separate files duplicates what git already tracks. The benefit is direct queryability without git archaeology. The cost is redundant storage (trivial for YAML files).

### Open Questions

1. **Agent identity and specialization.** Should review agents be specialized (a "security reviewer" vs. a "style reviewer") or general-purpose? Specialized agents produce better reviews but require a routing decision. Start general, specialize when metrics show which review categories have the highest miss rate.

2. **Cross-feature coordination.** When two features are in progress simultaneously and modify overlapping files, how does the pipeline handle conflicts? Options: global file lock table (simple but constraining), optimistic concurrency with merge-conflict resolution (complex but flexible). This needs a separate design document.

3. **Learning across features.** Can the pipeline learn from past features? For example, if task-004 type tasks (middleware changes) consistently take more iterations, can the planning agent adjust its estimates? This requires a feedback loop from metrics into the planning prompt. Worth prototyping.

4. **Cost management.** Each agent invocation costs tokens. A feature with 6 tasks, 5 fix iterations each, plus reviews and gap detection could require 50+ agent invocations. Need a cost budget per feature with alerts.

5. **Testing depth.** The current design assumes acceptance criteria can be expressed as test commands. Some criteria ("code is readable") cannot. How deep should the pipeline go into subjective quality? The current answer is "not at all; use linters for style, humans for taste" but this deserves more thought.

---

## Summary of Key Principles

1. **Artifacts are the system of record.** Agents are ephemeral. Artifacts are permanent. If it is not in an artifact, it did not happen.

2. **No agent self-assessment is trusted.** Acceptance is determined by external checks: test commands, independent review agents, and deterministic validation.

3. **Loops have hard ceilings.** Every review/fix cycle has a maximum iteration count. Exceeding it triggers escalation, not continuation.

4. **Human decisions are structured.** The pipeline never asks open-ended questions. It presents options with consequences.

5. **Premature victory is the primary adversary.** Multiple independent checks (acceptance tests, diff-vs-spec, regression canary, output size sanity) combine to make false completion claims difficult.

6. **Granularity determines success.** A task that is too large will fail. A task that is too small creates coordination overhead. The sweet spot is < 500 LOC, <= 3 files, completable in a single agent session.

7. **The pipeline is resumable.** Human pauses, agent crashes, and system restarts do not lose progress. All state is reconstructable from the `sdlc/` directory.

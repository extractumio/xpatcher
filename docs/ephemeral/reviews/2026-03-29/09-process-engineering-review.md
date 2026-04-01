# xpatcher Design Specification -- Process Engineering Review

**Date:** 2026-03-29
**Reviewer Role:** Automation Process Engineer
**Documents Reviewed:** Design docs 01-17, consolidated review (00)
**Focus:** Workflow correctness, operational reliability, implementability

---

## VERDICT: Process Design Ready -- with 3 operational risks requiring mitigation before v1 ships

The pipeline design is well-structured and internally consistent. The prior review round resolved all critical architecture issues. The 16-stage pipeline, the iteration caps, the adversarial review isolation, and the file-based coordination are sound design choices. However, this process engineering review identifies operational risks that the prior review (focused on architecture and schema correctness) did not cover: scenarios where the pipeline can stall without clear recovery, edge cases in the gap re-entry protocol, and specification ambiguities that will surface during dispatcher implementation.

**Confidence:** High that the design can be built. Medium that it will run reliably on first deployment without the mitigations below.

---

## 1. CRITICAL PROCESS GAPS

### PGAP-1: No Specification of What Happens When the Quality Loop and Gap Detection Interact on Regression

**Location:** Sections 3.4, 3.4.1, 6.5.1

**The problem:** Consider this sequence:
1. Tasks 1-5 complete, all pass quality loops.
2. Gap detection (Stage 14) finds gaps, creates gap tasks G001-G003.
3. Gap task G002 modifies a file that task-003 also modified.
4. Gap task G002 passes its own quality loop.
5. The regression test suite runs. A regression is detected in task-003's acceptance criteria.

Who owns this failure? The spec (Section 6.5.1 v1) says: "mark the just-completed task as FAILED with reason regression." But task-003 already has state SUCCEEDED and its code is already committed to the feature branch. The gap task G002 is the one that just completed. If G002 is marked FAILED, it re-enters the fix loop -- but the executor for G002 may not understand task-003's acceptance criteria well enough to fix the regression.

**Impact:** Pipeline stalls in gap re-entry with no clear owner of the regression.

**Recommended fix:** Define a "regression attribution" rule: when a regression is detected after a gap task completes, the dispatcher must (a) identify which prior task's ACs broke, (b) provide the gap task executor with both the gap task spec AND the failing prior-task ACs, and (c) count this against the gap task's iteration limit. If the gap task cannot fix the regression after max iterations, escalate to human with full context of both tasks.

### PGAP-2: Task File Movement Between Directories Is Not Atomic

**Location:** Section 5.1, Section 2.3.2

**The problem:** Tasks move between `tasks/todo/`, `tasks/in-progress/`, and `tasks/done/` directories as status changes. The `PipelineStateFile` class provides atomic writes for `pipeline-state.yaml`, but there is no equivalent atomicity for task file moves. If the dispatcher crashes between moving a task file from `in-progress/` to `done/` and updating `pipeline-state.yaml`, the two sources of truth diverge. On resume, the dispatcher reads `pipeline-state.yaml` (which says RUNNING) but cannot find the task file in `in-progress/` (it was already moved to `done/`).

**Impact:** Resume fails with a file-not-found error, or worse, the dispatcher silently re-executes a completed task.

**Recommended fix:** Either (a) make `pipeline-state.yaml` the sole source of truth for task state and treat the directory structure as a convenience view (rebuild directories from state on resume), or (b) write a two-phase commit: update state first, then move file, and on resume scan all three directories to find each task's actual location, reconciling with the state file.

### PGAP-3: The Oscillation Detection Hash Does Not Account for Finding Severity Changes

**Location:** Section 3.4

**The problem:** Oscillation detection hashes "the set of active findings." If iteration 1 has findings {A, B}, iteration 2 fixes A but introduces C, and iteration 3 fixes C but reintroduces A, the hash of {A, B} reappears and oscillation is correctly detected. But consider: iteration 1 has finding A (severity: critical), iteration 2 resolves the critical aspect but the reviewer downgrades it to A (severity: minor). The finding ID is the same, the hash matches, but the situation is actually improving. False oscillation detection will prematurely escalate tasks that are converging.

**Impact:** Tasks escalated to human unnecessarily, increasing human gate load.

**Recommended fix:** The oscillation hash should include both finding IDs and their severities. Additionally, add a "severity regression" check: if the same finding reappears at the same or higher severity, that is oscillation; if it reappears at a lower severity, that is convergence and should not trigger oscillation detection.

---

## 2. OPERATIONAL RISKS

### ORISK-1: 4-Hour Session Expiry Creates a Cliff for Complex Tasks

**Location:** Sections 2.7, 7.8

**Risk level:** Medium

The session management spec says sessions older than 4 hours get fresh starts with context bridges on resume. But there is no mechanism to detect or warn when a task is approaching the 4-hour mark during active execution. A complex task running for 3.5 hours might be interrupted by a human gate (e.g., plan approval took 30 minutes), resume after the gate, and silently lose all accumulated context.

**Scenario:**
1. Pipeline starts at 10:00. Planning takes 30 min. Human gate opens at 10:30.
2. User approves at 14:00 (3.5 hours later). Task breakdown, review, and prioritization take 30 min.
3. Executor starts task-001 at 14:30 -- but the planner session from 10:00 is now 4.5 hours old.
4. The executor gets a fresh session with a context bridge. The bridge captures the plan summary and task spec, but the planner's deep understanding of the codebase architecture is lost.

This is by design (adversarial isolation means executor does not inherit planner sessions anyway), but the risk applies to executor sessions during fix iterations: if the user takes 3 hours at a human gate between iteration 1 and iteration 2, the executor loses its codebase context.

**Mitigation:** Add a "session aging" warning to the TUI when a session is within 30 minutes of the 4-hour threshold. For fix iterations specifically, allow the 4-hour timeout to be extended to 8 hours since the executor-to-fix-executor transition is same-agent, same-model.

### ORISK-2: The 30-Minute Soft Gate for Task Review Is Too Short for Real Teams

**Location:** Section 3.5

**Risk level:** Medium

The task review soft gate auto-proceeds after 30 minutes. In practice, a developer receiving the bell notification may be in a meeting, at lunch, or working on something else. Thirty minutes is barely enough time to read and understand the task manifest for a medium-complexity feature (4-8 tasks). If the soft gate auto-proceeds and the task breakdown has a structural problem (e.g., wrong dependency ordering, missing task), the pipeline will execute all tasks before the developer notices.

**The cost of auto-proceeding incorrectly:** All tasks execute (potentially 30-60 minutes of API cost and compute), the gap detector may catch the problem but may not, and the developer has to either roll back or manually fix the structural issue.

**Mitigation:** Change the default soft gate timeout from 30 minutes to 2 hours (matching the hard gate timeout). Alternatively, make the task review a hard gate for the first pipeline run in a project (where the user is still learning xpatcher's task decomposition style) and a soft gate for subsequent runs.

### ORISK-3: Context Bridge Quality Is Unverifiable

**Location:** Section 7.9

**Risk level:** Medium

The `ContextBridge` class constructs summaries of prior stage results for cross-stage transitions. The quality of this summary determines whether the downstream agent (reviewer, executor, gap detector) has sufficient context. But there is no validation that the context bridge contains enough information. A bridge that summarizes a 200-line plan as "Add OAuth2 support with 12 tasks" is technically valid but operationally useless.

**Mitigation:** Add a minimum content threshold for context bridges: bridges must include at minimum (a) the original intent goal, (b) the task spec including all acceptance criteria, and (c) any prior review findings. The dispatcher should assert on these fields being non-empty before passing the bridge to an agent.

### ORISK-4: Batch 2 Tasks Can Reference Batch 1 Artifacts That Don't Exist Yet on First Run

**Location:** Sections 2.5, 3.2 (Stages 9-11)

**Risk level:** Low-Medium

The spec says batch boundaries ensure all dependencies are satisfied. But "satisfied" means the task state is SUCCEEDED -- it does not guarantee that the task's output artifacts (e.g., a new API endpoint, a new module) are actually importable/callable from the code perspective. In v1 (sequential execution), this is mostly fine because tasks commit to the same branch. But consider: Task-001 creates `src/auth/session.ts`, task-002 modifies `src/auth/middleware.ts` to import from `src/auth/session.ts`. If task-001 is in batch 1 and task-002 is in batch 2, the dependency DAG correctly orders them. But if task-001 creates the file with a slightly different interface than what the planner specified (and passes its own quality loop), task-002's executor may fail because the interface does not match what the plan described.

**Mitigation:** This is partially addressed by the existing quality loop (the reviewer checks interface consistency). Strengthen the check: when the executor for task-002 starts, the prompt builder should include the actual interface signatures from task-001's changed files, not just the plan's description. This ensures the executor works against the real code, not the planned code.

---

## 3. BOTTLENECK ANALYSIS

### Time Bottleneck: Plan Review Loop

The plan review loop (Stages 2-4) can take up to 3 iterations. Each iteration involves:
- Planner invocation (Opus[1m], 30 maxTurns, ~600s timeout): ~5 min
- Reviewer invocation (Opus, 25 maxTurns, ~300s timeout): ~3 min

Worst case: 3 x (5 + 3) = 24 minutes before the human gate at Stage 5. Add 30 minutes for human review = 54 minutes before any task executes.

**Recommendation:** This is acceptable. The planning phase is the cheapest place to catch problems. No change needed.

### Cost Bottleneck: Per-Task Quality Loop with Thorough Tier

A single task with "thorough" quality tier runs:
- Tester (Sonnet, 40 maxTurns, ~600s timeout)
- Reviewer (Opus, 25 maxTurns, ~300s timeout)
- Simplifier (optional, Sonnet, 30 maxTurns, ~300s timeout)
- Coverage check, negation check, flaky detection (5 runs), mutation testing, LLM audit

Per the spec (Section 6.2.1), thorough tier adds 15-30 minutes per task. With 3 quality iterations, a single thorough task can consume 45-90 minutes.

**Recommendation:** The tiered approach is the right design. Ensure the planner defaults to "standard" and only assigns "thorough" to tasks with genuine security/financial risk. Add a pipeline summary showing per-task quality tier assignments at the plan approval gate so the human can override before execution.

### Throughput Bottleneck: Sequential Execution in v1

In v1, all tasks execute sequentially. A 12-task feature with average 5 minutes per task (execution + quality) = 60 minutes of serial execution. With 3 batches, no parallelism savings.

**Recommendation:** This is an acceptable v1 tradeoff. The spec correctly identifies this as the primary motivation for v2 parallel execution. No design change needed.

### Human Bottleneck: Two Hard Gates

The pipeline blocks at Stage 5 (plan approval) and Stage 16 (completion). With the 2-hour soft timeout on hard gates, worst case is 4 hours of human waiting per pipeline run. The `xpatcher pending` command mitigates this.

**Recommendation:** Consider adding a `--auto-approve` flag for experienced users who want to skip the plan approval gate for low-risk features (e.g., documentation-only changes). This should be opt-in, not default.

---

## 4. DRY-RUN: Full Pipeline Simulation for "Add OAuth2 to an Express.js API"

### Preconditions
- Project: Express.js API with existing JWT auth at `/src/auth/`
- Tests: Jest, existing test suite at `/tests/`
- No `.xpatcher.yaml` in project

### Stage 1: Intent Capture (~1 min)

```bash
xpatcher start "Add OAuth2 support to the Express.js API"
```

Dispatcher:
1. Creates pipeline `xp-20260329-f7a3`
2. Creates feature branch `xpatcher/add-oauth2-support`
3. Creates `.xpatcher/add-oauth2-support/`
4. Runs preflight: detects Node.js + Jest + TypeScript
5. Invokes planner (Opus[1m]) with intent analysis prompt

Planner reads `package.json`, `src/auth/`, existing JWT middleware. Determines intent is clear (no Q&A needed).

**Artifact produced:** `intent.yaml`
```yaml
goal: "Add OAuth2 support to the Express.js API"
scope: ["src/auth/", "src/middleware/", "src/routes/", "config/"]
constraints: ["Must coexist with existing JWT auth", "Express.js patterns"]
status: ready
```

**Potential ambiguity the spec does not address:** "Add OAuth2 support" is ambiguous -- does the user want OAuth2 as an authentication provider (Google/GitHub login), as an authorization server (issue tokens), or as a resource server (validate external tokens)? The planner should detect this ambiguity and enter the Q&A loop, but the spec does not provide guidance on what level of ambiguity triggers Q&A. The `ambiguity_level` field has values `clear | minor | major` but no threshold definition for what constitutes "minor" vs "major." This is a judgment call left to the planner agent, which is acceptable but may lead to inconsistent behavior.

### Stage 2: Planning (~5 min)

Planner (Opus[1m]) explores codebase, invokes expert panel:
- Complexity assessment: medium (cross-module, auth-related)
- Experts spawned: backend-expert, security-architect, qa-automation (3 subagents, parallel)

**Expert panel cost:** 3 Sonnet invocations + 1 Opus synthesis = ~4 min

Planner produces plan with 3 phases, 8 tasks:
```
Phase 1: OAuth2 Provider Integration (tasks 001-003)
Phase 2: API Endpoint Updates (tasks 004-006)
Phase 3: Testing and Documentation (tasks 007-008)
```

**Artifact produced:** `plan-v1.yaml`

### Stage 3: Plan Review (~3 min)

Reviewer (Opus, fresh session, context bridge) reviews plan.
- Checks: task granularity, AC completeness, file scope, dependency correctness
- Finding: task-002 (token validation middleware) has no acceptance criterion for token refresh. Verdict: `request_changes`

**Artifact produced:** `plan-review-v1.yaml`

### Stage 4: Plan Fix (~4 min)

Planner (Opus, resumed session + review findings) revises plan.
- Adds AC to task-002: "Token refresh returns new access token with valid refresh token"
- Adds edge case AC: "Expired refresh token returns 401"

**Artifact produced:** `plan-v2.yaml`

### Stage 3 (repeat): Plan Review (~2 min)

Reviewer (Opus, fresh session) reviews plan-v2. Verdict: `approved`

**Artifact produced:** `plan-review-v2.yaml`

**Running time so far: ~15 min**

### Stage 5: Plan Approval (HUMAN GATE -- variable time)

TUI displays:
```
PLAN APPROVAL REQUIRED
Plan version: v2 (after 1 review iteration)
Phases: 3 | Tasks: 8 | Est. complexity: medium
[1] Approve and begin execution
[2] Request changes
[3] Reject and restart
[4] View full plan details
```

User presses `1` to approve.

**Artifact produced:** decision recorded in `decisions/`

### Stage 6: Task Breakdown (~2 min)

Planner (Opus, resumed session) decomposes plan-v2 into 8 task YAMLs with:
- Acceptance criteria with test commands
- File scope per task
- Dependency declarations
- Quality tier assignments (standard for most, thorough for task-002)

**Artifacts produced:** `task-manifest.yaml`, `tasks/todo/task-001-*.yaml` through `task-008-*.yaml`

### Stage 7: Task Review (~2 min)

Reviewer (Opus, fresh session) reviews task manifest.
- Checks: granularity, AC quality, dependency graph correctness, file scope overlap
- Verdict: `approved` (soft gate auto-proceeds after 30 min if no human intervention)

**OPERATIONAL NOTE:** This is where ORISK-2 applies. If the reviewer approves but the user wanted to override quality tiers, they have 30 minutes (default) to intervene. The recommendation is to extend this to 2 hours.

**Artifact produced:** `task-review-v1.yaml`

### Stage 8: (skipped -- verdict was approved)

### Stage 9: Prioritization (~10 sec)

Dispatcher builds DAG from dependency fields:
```
Batch 1: task-001, task-003 (no dependencies, independent)
Batch 2: task-002 (depends on task-001)
Batch 3: task-004, task-005, task-006 (depend on task-002)
Batch 4: task-007, task-008 (depend on batch 3)
```

**Artifact produced:** `execution-plan.yaml`

**PROCESS NOTE:** In v1, batches are sequential. Batch 1 runs task-001 first, then task-003 (or vice versa -- the spec does not define ordering within a batch in v1). Since both tasks are independent, ordering does not matter functionally, but the dispatcher needs a deterministic tiebreaker (e.g., task ID order) for reproducibility.

### Stage 10: Execution Graph (~5 sec)

Dispatcher creates rollback tags:
```bash
git tag xpatcher/pre-batch-1-xp-20260329-f7a3
```

**Artifact produced:** updated `execution-plan.yaml` with rollback tags

### Stage 11-13: Task Execution + Quality Loop

**Batch 1 -- task-001: "Set up OAuth2 provider client" (~8 min)**

1. Executor (Sonnet) reads task spec, explores existing auth code
2. Creates `src/auth/oauth2-client.ts`, modifies `config/auth.ts`
3. Commits: `xpatcher(task-001): Set up OAuth2 provider client`
4. Quality loop:
   - Tester (Sonnet) generates integration tests, runs them: 4 passed
   - Reviewer (Opus, fresh session) reviews code: verdict `approved`
   - Regression suite (`npm test`): all existing tests pass
5. Task state: SUCCEEDED
6. Task file moved: `todo/ -> done/`

**Batch 1 -- task-003: "Create OAuth2 callback route" (~7 min)**
Same pattern. SUCCEEDED.

```bash
git tag xpatcher/pre-batch-2-xp-20260329-f7a3
```

**Batch 2 -- task-002: "Token validation middleware" (~12 min)**

Quality tier: thorough (security-sensitive).
1. Executor (Sonnet) implements middleware
2. Quality loop iteration 1:
   - Tester: runs tests + negation check + flaky detection (5 runs)
   - Reviewer: finds missing CSRF protection on token endpoint. Verdict: `request_changes`
   - Iteration counter: 1
3. Fix iteration:
   - Executor (resumed session + findings): adds CSRF protection
   - Commits fix
4. Quality loop iteration 2:
   - Tester: re-runs + negation + flaky
   - Reviewer (fresh session): verdict `approved`
   - Mutation testing: 72% kill rate (above 70% threshold)
   - Iteration counter: 2
5. Regression suite: pass
6. Task state: SUCCEEDED

**Batch 3 -- tasks 004, 005, 006** (~15 min total, sequential)
Standard quality tier. All pass within 1-2 quality iterations.

```bash
git tag xpatcher/pre-batch-4-xp-20260329-f7a3
```

**Batch 4 -- tasks 007, 008** (~10 min total)
task-007: test suite enhancement (lite tier). task-008: README update (lite tier). Both SUCCEEDED.

**Running time so far (excluding human gate): ~65 min**

### Stage 14: Gap Detection (~3 min)

Gap detector (Opus, fresh session) receives:
- Original intent
- All 8 completed task summaries
- Full git diff vs main

Findings:
- Gap G1 (critical): No rate limiting on OAuth2 token endpoint
- Gap G2 (minor): No logging of OAuth2 events for audit trail

Per gap categorization rules:
- G1 (critical): auto-approved for execution
- G2 (minor): deferred to `deferred-gaps.yaml`

**Artifact produced:** `gap-report-v1.yaml`

### Gap Re-entry (Stages 6-14, scoped)

`current_gap_depth: 1` (max: 2)

1. Planner creates gap task: `task-G001: Add rate limiting to OAuth2 token endpoint`
2. Task review: approved
3. Execution: Executor adds rate limiting middleware
4. Quality loop: 1 iteration, approved
5. Regression: existing tests pass
6. Gap detection (round 2): no new gaps. Verdict: `complete`

**Artifact produced:** `task-manifest-v2.yaml`, `gap-report-v2.yaml`

**PROCESS NOTE (PGAP-1 scenario):** If gap task G001 had caused a regression in task-002's token validation, the current spec does not clearly attribute the regression. See PGAP-1 above.

### Stage 15: Documentation (~2 min)

Tech writer (Sonnet) reads the diff, existing README, and API docs.
- Updates `README.md` with OAuth2 setup instructions
- Creates `docs/auth/oauth2.md` with endpoint reference
- Updates `CHANGELOG.md` if present

**Artifact produced:** `docs-report.yaml`

### Stage 16: Completion (HUMAN GATE)

Dispatcher:
1. Pushes feature branch to remote
2. Creates PR via `gh pr create` (if `gh` is available)

TUI displays completion summary:
```
PIPELINE COMPLETE -- xp-20260329-f7a3 -- Total: 72m 18s
Feature: add-oauth2-support
Branch: xpatcher/add-oauth2-support
PR: https://github.com/org/repo/pull/43

Tasks: 9 completed (8 original + 1 gap), 0 failed
Iterations: 1.4 avg per task
Gap tasks: 1 executed, 1 deferred
```

User confirms completion.

### Summary of Pipeline Simulation

| Phase | Time | Agent Invocations | Human Wait |
|-------|------|-------------------|------------|
| Planning (Stages 1-4) | ~15 min | 5 (planner x2, reviewer x2, 3 experts) | 0 |
| Plan Approval (Stage 5) | variable | 0 | user-dependent |
| Task Decomposition (Stages 6-8) | ~4 min | 2 (planner, reviewer) | 0 |
| Prioritization + DAG (Stages 9-10) | ~15 sec | 0 | 0 |
| Execution + Quality (Stages 11-13) | ~52 min | ~24 (8 executor + 8 tester + 8 reviewer) | 0 |
| Gap Detection + Re-entry | ~8 min | ~6 (gap detector, planner, executor, tester, reviewer, gap detector) | 0 |
| Documentation (Stage 15) | ~2 min | 1 (tech writer) | 0 |
| Completion (Stage 16) | ~1 min | 0 | confirmation only |
| **Total** | **~82 min** | **~38 invocations** | **2 gates** |

**Estimated API cost** (rough): Opus invocations (~12) at $15/1M input, Sonnet invocations (~26) at $3/1M input. With ~50k tokens avg per invocation: Opus ~$9, Sonnet ~$4, total ~$13 for the pipeline.

**Bottleneck identified:** Execution + Quality is 63% of total time. This is expected and is the primary target for v2 parallelism.

---

## 5. DRY-RUN: Main Dispatch Loop Design Walkthrough

### Core Data Structures Needed

```python
# 1. Pipeline state (the mutable singleton)
class PipelineState:
    pipeline_id: str
    feature: str
    current_stage: PipelineStage
    status: Literal["running", "waiting_for_human", "paused", "completed", "failed"]
    task_states: dict[str, TaskState]  # task_id -> state
    iterations: dict[str, IterationTracker]  # "plan_review", "task-001:quality" -> tracker
    gap_reentry: GapReentryState | None
    skipped_tasks: list[SkippedTaskRecord]
    timing: dict[str, float]  # stage -> elapsed seconds

# 2. Execution plan (read-only after Stage 10)
class ExecutionPlan:
    batches: list[Batch]  # ordered list of task groups
    dag: TaskDAG
    rollback_tags: dict[int, str]  # batch_number -> git tag

# 3. Session registry (mutable, persisted)
class SessionRegistry:
    sessions: dict[str, SessionRecord]

# 4. Configuration (read-only after init)
class XpatcherConfig:
    models: ModelConfig
    iterations: IterationConfig
    timeouts: TimeoutConfig
    quality_tiers: QualityTiersConfig
    gates: GateConfig
```

### Main Dispatch Loop

```python
def run_pipeline(self, raw_request: str, project_dir: str):
    """Main dispatch loop -- the heart of core.py."""

    # Phase 1: Initialize
    pipeline_id = self.generate_pipeline_id()
    feature_slug = self.slugify(raw_request)
    feature_dir = f"{project_dir}/.xpatcher/{feature_slug}"
    self.create_feature_dir(feature_dir)
    self.create_feature_branch(feature_slug)
    self.state = PipelineState(pipeline_id, feature_slug)

    # Phase 2: Planning loop
    intent = self.run_intent_capture(raw_request, feature_slug)  # Stage 1
    plan = self.run_planning_loop(intent)                         # Stages 2-4
    self.run_human_gate("plan_approval", plan)                    # Stage 5

    # Phase 3: Task decomposition loop
    manifest = self.run_task_breakdown(plan)                      # Stage 6
    manifest = self.run_task_review_loop(manifest)                # Stages 7-8

    # Phase 4: Execution
    exec_plan = self.run_prioritization(manifest)                 # Stage 9
    self.run_execution_graph_setup(exec_plan)                     # Stage 10
    self.run_execution_with_quality(exec_plan)                    # Stages 11-13

    # Phase 5: Gap detection (with re-entry)
    self.run_gap_detection_loop(exec_plan)                        # Stage 14

    # Phase 6: Finalization
    self.run_documentation()                                       # Stage 15
    self.run_completion()                                          # Stage 16
```

### Spec Ambiguities Discovered During Design Walkthrough

**AMB-1: `run_planning_loop()` -- Who drives the loop?**

The transition from Stage 3 (Plan Review) to Stage 4 (Plan Fix) is triggered by `verdict: needs_changes`. But the spec says the reviewer produces a `plan-review-v1.yaml` and the planner reads it. Who orchestrates this? The dispatcher must:
1. Invoke reviewer -> get review artifact
2. Parse verdict
3. If needs_changes: invoke planner with review findings
4. Loop back to step 1

This is clear in the transition table but not explicitly stated as dispatcher logic. The dispatcher must own the loop control, not the agents. This is consistent with the "thin dispatcher" philosophy but should be explicitly stated.

**AMB-2: How does the dispatcher know which task to execute next in v1?**

The spec says tasks within a batch run "one at a time" in v1 but does not specify the ordering within a batch. The DAG provides cross-batch ordering via dependencies, but within a batch, all tasks are independent. Options:
- Task ID order (deterministic, simple)
- Critical path first (more optimal, requires path analysis)

The spec mentions "critical path optimization" (Section 2.5) but only in the context of parallel scheduling. For v1 sequential execution, a deterministic tiebreaker is needed.

**Recommendation:** Use task ID order within batches for v1. Document this in the execution plan.

**AMB-3: What is the `expected_type` for a plan review vs. a task review?**

Both use the reviewer agent and produce `ReviewOutput` with type `"review"`. The dispatcher needs to distinguish between plan-level reviews (which affect pipeline-level iteration tracking) and task-level reviews (which affect per-task iteration tracking). The `task_id` field on `ReviewOutput` handles this for task reviews, but plan reviews also have a `task_id` field -- what value does the reviewer put there?

**Recommendation:** For plan reviews, `task_id` should be a sentinel value like `"plan"` or the plan version identifier. The canonical schema (`TASK_ID_PATTERN = r"^task-[A-Z]?\d{3}$"`) does not accommodate this. Either relax the pattern for plan reviews or add a `review_scope: Literal["plan", "task"]` field.

**AMB-4: When does the tester agent run vs. when does the dispatcher run test commands?**

The spec describes two different testing mechanisms:
1. The tester agent (Section 4.5) generates and runs tests
2. The dispatcher runs acceptance criteria commands independently (Section 3.4: "The dispatcher runs acceptance criteria test commands independently")

The quality loop flowchart shows "(1) TEST -- run acceptance criteria commands + regression suite." But the tester agent's purpose is "Generate and run tests." Are these the same step? The spec seems to conflate them.

**Recommended interpretation:** The dispatcher runs the acceptance criteria commands (as specified in the task YAML) and the regression suite. If tests do not exist yet, the tester agent is invoked to generate them FIRST, then the dispatcher runs the AC commands. This two-step process is implied but not explicitly stated.

**AMB-5: How does the simplifier know which files were "recently modified"?**

The simplifier receives "a list of files recently modified." The spec does not define how this list is generated. Options:
- `git diff --name-only` against the pre-task commit
- The `files_changed` list from the executor's output
- The `files_in_scope` from the task spec

**Recommendation:** Use the executor's `files_changed` list (already validated by the semantic validator). This is the most accurate source.

**AMB-6: The `ReviewOutput.verdict` has three values (`approve`, `request_changes`, `reject`) but the pipeline only handles two outcomes.**

The transition table (Section 3.3) shows:
- `verdict: needs_changes` -> Stage 4 (Plan Fix)
- `verdict: approved` -> Stage 5 (Plan Approval)

But `ReviewOutput.verdict` uses `approve | request_changes | reject`. What happens on `reject`? The validator requires at least one critical/major finding for `reject`, but the transition table has no path for it. The likely intent is that `reject` = "plan is fundamentally wrong, do not iterate, escalate to human." But this is not specified.

**Recommendation:** Add a transition: `verdict: reject` -> escalate to human gate with reviewer's findings. This is different from iteration-cap escalation because it short-circuits the loop.

---

## 6. ADDITIONAL FINDINGS

### Self-Correction Loop Escape Scenario

**Can a scenario escape the iteration caps?**

The caps are:
- Plan review/fix: 3 iterations
- Task review/fix: 3 iterations
- Per-task quality: 3 iterations
- Gap detection re-entry: 2 rounds

Consider this scenario:
1. Plan approved on iteration 1.
2. Task manifest approved on iteration 1.
3. All 8 tasks complete within 3 quality iterations each.
4. Gap detection round 1: finds 3 critical gaps.
5. Gap tasks: 3 tasks created (30% of 10 would be 3, so under the cap).
6. Gap task G001 completes. Gap task G002 uses 3 quality iterations (STUCK). Gap task G003 completes.
7. Gap detection round 2: finds 1 more critical gap (from the incomplete G002).
8. But `current_gap_depth = 2 = max_gap_depth`. Escalate to human.

This correctly terminates. The caps hold.

**Edge case: gap tasks generate more gap tasks that generate more quality iterations:**
- Round 1: 10 original tasks. Gap detection finds 3 gaps (3 tasks). 30% = 3. OK.
- Round 2: 13 total tasks. Gap detection finds 2 gaps (2 tasks). But wait -- does the 30% cap apply to the original 10 or the running total of 13? The spec says `max_gap_tasks_ratio: 0.3` but does not specify the denominator.

**Recommendation:** Clarify that the 30% cap applies to the original task count (the task manifest from Stage 6, before any gap re-entry). This prevents compound growth: round 1 can add 3 tasks (30% of 10), round 2 can add 3 more tasks (30% of 10 again), for a maximum of 6 gap tasks total across all rounds.

### Human Gate Usability

The gate designs are well-structured with numbered options, consequences, and recommended actions. The plan approval gate (Stage 5) is particularly good -- it shows phase count, task count, and estimated complexity.

**One gap:** The gate does not show estimated cost or time. Users deciding whether to approve a plan should know "this will take approximately 45 minutes and cost approximately $10." Even a rough estimate would help users make informed decisions. The cost estimation system is deferred to v2, but a simple time estimate based on task count and quality tiers could be added in v1.

### Crash Recovery Completeness

The resume mechanism (Section 2.7) is well-designed:
- Reads `pipeline-state.yaml` for current stage
- Any task in RUNNING is reset to READY
- Sessions older than 4 hours get fresh starts

**One concern:** If the dispatcher crashes during a git commit (e.g., after `git add` but before `git commit`), the working tree has staged changes. On resume, the executor will start a fresh session and attempt to re-implement the task. It will find partially-staged changes from the previous attempt. The spec does not specify whether the dispatcher should `git reset HEAD` on resume to clean the working tree.

**Recommendation:** On resume, before starting any agent, the dispatcher should run:
```bash
git stash --include-untracked  # Save any orphaned changes
git checkout -- .               # Clean working tree
```
And store the stash ref in pipeline-state.yaml for the user to inspect if needed.

---

## 7. SUMMARY OF RECOMMENDATIONS

### Must-fix before v1 (process correctness):

| ID | Issue | Recommendation |
|----|-------|----------------|
| PGAP-1 | Regression attribution during gap re-entry | Define regression attribution rule: gap task executor receives both gap spec and failing prior-task ACs |
| PGAP-2 | Non-atomic task file movement | Make pipeline-state.yaml the sole source of truth; rebuild directory structure from state on resume |
| PGAP-3 | Oscillation detection false positives | Include finding severity in oscillation hash; add "severity regression" distinction |
| AMB-3 | Plan review `task_id` violates schema | Add `review_scope` field or relax `TASK_ID_PATTERN` for plan reviews |
| AMB-6 | `reject` verdict has no transition | Add `reject` -> escalate to human |

### Should-fix before v1 (operational reliability):

| ID | Issue | Recommendation |
|----|-------|----------------|
| ORISK-2 | 30-min soft gate too short | Extend default to 2 hours |
| AMB-2 | No within-batch ordering for v1 | Use task ID order; document it |
| AMB-4 | Tester vs dispatcher test execution | Clarify two-step process: tester generates, dispatcher verifies |
| Crash | Orphaned git state on resume | Add `git stash` + `git checkout` cleanup on resume |
| Gap cap | 30% denominator ambiguous | Clarify: 30% of original (pre-gap) task count |

### Nice-to-have for v1 (user experience):

| ID | Issue | Recommendation |
|----|-------|----------------|
| ORISK-1 | Session aging cliff | Add TUI warning when session approaches 4-hour threshold |
| ORISK-3 | Context bridge quality | Add minimum content assertions |
| Gate UX | No time/cost estimate at approval gate | Add rough time estimate based on task count and tiers |
| Quality tiers | Not visible at approval gate | Show per-task tier assignments in plan approval display |

---

*End of process engineering review.*

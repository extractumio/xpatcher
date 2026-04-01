# Pipeline Flow & Workflow Review

**Reviewer:** Workflow/Process Engineer
**Date:** 2026-03-29
**Scope:** 16-stage pipeline, state transitions, self-correction loops, human gates, end-to-end workflow correctness
**Documents reviewed:** 03-pipeline-flow.md, 02-system-architecture.md, 05-artifact-system.md, 06-quality-testing.md, 09-dispatcher-internals.md, 10-risk-mitigation.md, 11-implementation-roadmap.md, 12-appendices.md, 04-agent-definitions.md, and xpatcher-design-proposal.md (master)

---

## Executive Assessment

The 16-stage pipeline is well-structured with clear separation of concerns, auditable artifact production at every stage, and sensible human gate placement. The self-correction loops have hard caps with oscillation detection, and the file-based coordination design makes the pipeline inherently inspectable and crash-recoverable. These are significant strengths.

However, the design has **3 critical issues**, **6 major issues**, and **8 minor issues** that must be resolved before implementation. The most consequential problem is the underspecification of parallel task branch merging (the spec never describes how parallel worktree branches are reconciled onto the feature branch, which is the central mechanic of the execution phase). The second critical issue is that the two-level state machine in Section 2.4 does not align with the 16-stage pipeline in Section 3, creating ambiguity about what the dispatcher actually tracks. The third is that gap detection re-entry semantics are incomplete -- the spec says "re-enter Stage 6" but does not specify whether Stages 7-13 run for the gap tasks, or how gap tasks interact with already-completed tasks.

**Overall verdict:** The pipeline design is sound in concept but needs a focused tightening pass on state machine consistency, merge mechanics, and loop re-entry semantics before it can be implemented without ambiguity.

---

## Strengths

1. **Artifact-first design.** Every stage produces a named, versioned YAML file. This makes the pipeline fully auditable and debuggable without specialized tooling (`yq` and `cat` suffice). The cross-referencing strategy via `*_ref` fields creates a clean DAG of artifacts.

2. **Human gate design is well-tiered.** Hard gates at plan approval (Stage 5) and completion (Stage 16), soft gates for task review, and auto gates for mechanical transitions. The structured prompt format with numbered options and consequences is excellent for human factors.

3. **Self-correction loops have escape hatches.** Every loop has a hard cap (3 for plan review, 3 for task review, 5 for per-task quality, 2 for gap detection) with clear escalation. The oscillation detection via findings-hash comparison is a clever mechanism that prevents the most common infinite loop pathology.

4. **Pipeline resumption is first-class.** State persistence to `pipeline-state.yaml` on every transition, session registry for context preservation, and explicit rebase-if-base-changed logic. This is unusually well-thought-out for a design at this stage.

5. **Failure output is actionable.** The blocked pipeline display (Section 3.6) tells the user exactly what to do next, with numbered options. Stuck tasks are moved back to `todo/` for human inspection.

6. **Premature victory prevention.** The executor never self-certifies. The dispatcher runs acceptance criteria independently. A separate reviewer cross-references the diff. Pre-existing tests are re-run. This is the right set of defenses.

7. **Transparent timing.** Per-stage elapsed time tracking, task-level timers, and the summary breakdown in the completion output. This makes performance analysis possible from day one.

---

## Critical Issues

### C1. Parallel worktree branch merging is unspecified

**Location:** 02-system-architecture.md Section 2.5, 03-pipeline-flow.md Stages 11-12

The spec describes creating worktrees per task:
```
git worktree add .xpatcher/worktrees/TASK-003 -b xpatcher/feature-auth/TASK-003
```

But it never describes how these task branches are merged back onto the feature branch. This is the single most operationally complex part of the entire pipeline and it is not specified. Questions that must be answered:

- When a task passes quality (Stage 12), is its branch immediately merged to the feature branch? Or does it wait until the entire batch completes?
- If merged immediately, subsequent tasks in the same batch may see merge conflicts from sibling tasks. How are conflicts resolved?
- If merged at batch boundaries, what triggers the merge? What if 4/5 tasks pass but 1 is stuck?
- What merge strategy is used? (fast-forward, merge commit, rebase?)
- After merge, is the worktree cleaned up? The spec says task files move to `done/` but says nothing about worktrees.
- The gap detector (Stage 14) needs to analyze the integrated codebase. But if worktree branches have not been merged, the gap detector sees only individual task branches.

**Recommendation:** Add a Section 2.5.1 "Worktree Merge Protocol" specifying: (a) merge timing (per-task on quality pass, or per-batch), (b) merge strategy, (c) conflict resolution policy, (d) worktree cleanup, (e) what happens to the task branch if the task is stuck.

### C2. Two-level state machine does not match the 16-stage pipeline

**Location:** 02-system-architecture.md Section 2.4 vs 03-pipeline-flow.md Section 3.2

The pipeline-level state machine in Section 2.4 has these states:
```
UNINITIALIZED -> PLANNING -> PLAN_REVIEW -> APPROVED -> EXECUTING ->
REVIEWING -> REVIEW_COMPLETE / CHANGES_REQUESTED -> TESTING ->
SIMPLIFYING -> GAP_DETECTION -> FINALIZING -> DONE
```

But the 16-stage pipeline in Section 3.2 has these stages:
```
1 Intent, 2 Plan, 3 Plan Review, 4 Plan Fix, 5 Plan Approval,
6 Task Breakdown, 7 Task Review, 8 Task Fix, 9 Prioritization,
10 Exec Graph, 11 Execution, 12 Per-Task Quality, 13 Fix Iteration,
14 Gap Detection, 15 Documentation, 16 Completion
```

These do not align. The state machine has no states for Intent Capture, Task Breakdown, Task Review, Task Fix, Prioritization, Execution Graph, Fix Iteration, or Documentation. It has states (SIMPLIFYING, TESTING) that are not standalone stages in the pipeline (they are part of the Per-Task Quality loop in Stage 12). The state machine shows REVIEWING -> CHANGES_REQUESTED as a loop, but this does not map to any specific stage boundary.

The `pipeline-state.yaml` schema uses `current_stage: "<stage-name>"` but it is ambiguous whether this tracks the 16 pipeline stages or the state machine states.

**Recommendation:** Reconcile the two representations. Either (a) make the state machine exactly match the 16 stages, with `current_stage` being one of {`intent`, `planning`, `plan_review`, `plan_fix`, `plan_approval`, `task_breakdown`, `task_review`, `task_fix`, `prioritization`, `exec_graph`, `execution`, `per_task_quality`, `fix_iteration`, `gap_detection`, `documentation`, `completion`}, or (b) explicitly document the mapping between the abstract state machine and the concrete pipeline stages. Option (a) is strongly preferred for implementation clarity.

### C3. Gap detection re-entry semantics are incomplete

**Location:** 03-pipeline-flow.md Sections 3.2 and 3.3, 06-quality-testing.md Section 6.5

The transition table says:
```
14 Gap Detection -> 6 Breakdown (scoped) | Gaps found | Auto
```

The word "(scoped)" is the only hint that this is not a full pipeline restart. Critical questions:

- "Scoped" to what? Only the new gap-identified tasks? Or all tasks, including previously completed ones?
- After new tasks are created in Stage 6, do they go through Stage 7 (Task Review), Stage 8 (Task Fix), Stage 9 (Prioritization), Stage 10 (Exec Graph) before execution? The spec does not say.
- The scope creep prevention rule (gap tasks <= 30% of original count) implies gap tasks are appended. But the task manifest was already "approved" in the original flow. Does the manifest get a new version? Does it need re-approval?
- When gap tasks execute, they may touch files already modified by completed tasks. How are conflicts handled?
- After gap tasks complete Stages 11-13, does gap detection (Stage 14) run again? The max gap detection re-entry is 2, implying yes. But the transition table only shows a single 14->6 transition, not a 14->14 re-entry.
- The gap report schema has `new_tasks` but the task breakdown (Stage 6) normally reads the plan, not the gap report. How does the planner know to generate tasks only for the gaps?

**Recommendation:** Add a subsection "Gap Re-entry Protocol" to Section 3.4 specifying: (a) exact stages that re-run (probably 6-7-8-9-10-11-12-13-14), (b) scoping of re-entry (only new gap tasks, not re-running completed tasks), (c) task manifest update mechanics, (d) how the planner receives gap context, (e) the loop structure (14 -> 6 -> ... -> 14 again, max 2 times).

---

## Major Issues

### M1. Task review soft gate: 30-minute auto-proceed with major findings

**Location:** 03-pipeline-flow.md Section 3.5

The spec says task review approval has a "30-minute window for human intervention if no major findings." This implies that if there ARE major findings, the gate does not auto-proceed. But the wording is ambiguous:

- What constitutes a "major finding" at the task review level? The task review (Stage 7) reviews task granularity, acceptance criteria, and dependencies -- not code. The finding severity model (`major | minor | suggestion`) is defined for code reviews but not task reviews.
- If the task review verdict is `needs_changes`, the pipeline auto-proceeds to Stage 8 (Task Fix) regardless. The soft gate only applies when the verdict is `approved`. So the 30-minute window is only relevant for approved task reviews where a human might want to override.
- But if the review is approved with no major findings, what would the human override? This gate appears to serve no practical purpose in its current form.

**Recommendation:** Clarify the soft gate semantics. Either: (a) the 30-minute window applies when the auto-reviewer approves, giving the human a chance to catch false approvals (make this explicit), or (b) the soft gate applies when there are `minor` or `suggestion` findings (human can decide to block). State the exact condition under which the 30-minute timer starts and what "auto-proceed" means in terms of the transition.

### M2. Stuck task handling: worktree and branch cleanup missing

**Location:** 03-pipeline-flow.md Section 3.6

The failure output says: "Stuck tasks are moved back to `tasks/todo/` so the user can find and address them." But:

- The task's worktree still exists at `.xpatcher/worktrees/TASK-NNN`. Is it cleaned up? Left for inspection?
- The task's branch `xpatcher/feature-auth/TASK-NNN` still exists. Is it deleted? Left for manual merge?
- If the task had partial commits on its branch, those commits represent incomplete work. Are they preserved, squashed, or discarded?
- The task YAML's `status` field in the `todo/` folder: is it reset to `pending`, or set to `stuck`? The task schema allows both values but the transition is not specified.
- The `pipeline-state.yaml` `task_statuses` field: what value does the stuck task get?
- The `execution-plan.yaml` batches: is it updated to remove the stuck task, or does it retain it with a `stuck` status?

**Recommendation:** Add a "Stuck Task Cleanup Protocol" specifying the state of every artifact (worktree, branch, task YAML, pipeline state, execution plan) when a task is marked stuck.

### M3. Batch transition mechanics during parallel execution are undefined

**Location:** 03-pipeline-flow.md Section 3.3, 02-system-architecture.md Section 2.5

The transition table says:
```
12 Quality -> Next batch or 14 | Quality `pass` | Auto
```

The word "Next batch" is doing a lot of work. The execution plan schema shows batches:
```yaml
batches:
  - batch: 1
    tasks: [task-001]
  - batch: 2
    tasks: [task-002, task-003]
```

Questions:
- Does "next batch" mean ALL tasks in the current batch must pass quality before the next batch starts? Or can completed tasks in batch N+1 start as soon as their specific dependencies are satisfied (DAG-based)?
- The DAG (Section 2.5) uses dependency-based scheduling, but the execution plan uses batch-based scheduling. These are different models. Which takes precedence?
- If task-002 passes quality but task-003 is in its 3rd quality iteration, does the pipeline wait for task-003 before starting batch 3?
- What if a batch 2 task finishes and its dependent in batch 3 is now ready, but another batch 2 task is still running? Does the batch 3 task start immediately (DAG-driven) or wait (batch-driven)?

**Recommendation:** Clarify whether scheduling is strictly batch-sequential or DAG-driven-with-batches-as-hints. The DAG model is more efficient but more complex. The batch model is simpler but creates artificial bottlenecks. Pick one and document the transition trigger precisely.

### M4. Documentation stage failure path is missing

**Location:** 03-pipeline-flow.md Section 3.2, Section 3.3

The transition table shows:
```
15 Documentation -> 16 Completion | Docs updated | Human confirmation
```

But there is no transition for documentation failure. What if:
- The tech-writer agent produces malformed output that fails validation after all retries?
- The tech-writer makes incorrect documentation changes (factual errors about the code)?
- The documentation step finds that it cannot update docs because the doc structure is incomprehensible?
- The tech-writer needs to create files but is blocked by a hook (file pattern does not match doc patterns)?

The documentation stage has no review loop, no quality check, and no failure transition. It is a fire-and-forget step that goes directly to completion. For a pipeline that emphasizes verification at every other stage, this is inconsistent.

**Recommendation:** Either (a) add a documentation review step (a lightweight human or auto review of doc changes), or (b) explicitly state that documentation failures are non-blocking (a warning is logged, but the pipeline proceeds to completion), or (c) treat documentation failure like any other agent failure (escalate to human with debug output).

### M5. Completion stage "human confirmation" is vague

**Location:** 03-pipeline-flow.md Section 3.2

Stage 16 has gate type "Human" and produces `completion.yaml`. But:
- What is the human confirming? That they want to push the branch? That they approve the PR? That they have reviewed the summary?
- Is this a review gate (human reads a summary and approves) or an action gate (human performs an action like merging)?
- The completion output (Section 3.6) mentions "pushed to remote" and a PR URL. But the human gate blocks. So is the branch pushed BEFORE or AFTER human confirmation?
- What are the human's options? Approve (push + PR)? Approve with modifications? Reject (discard feature branch)?
- The decision YAML template (Section 3.5) shows structured options, but no completion-specific decision template is provided.

**Recommendation:** Provide a concrete completion gate specification: what the human sees (summary of all stages, warnings, the diff), what options they have (approve/push, approve/no-push, request-changes, cancel), and what actions each option triggers.

### M6. Per-task quality loop (Stages 12-13) internal sequencing is unclear

**Location:** 03-pipeline-flow.md Section 3.2, 06-quality-testing.md Sections 6.1-6.4

Stage 12 is described as "Simplify + test + review pass" with agents Simplifier, Tester, and Reviewer. But the spec is ambiguous about the internal ordering:

- The stage overview diagram says: `simplify --> test --> review`
- But Section 6.4 says simplification runs "After each task passes review" -- which is AFTER the quality loop, not inside it.
- The acceptance criteria template (Section 6.1) shows `completion_gate.max_retry_cycles: 3` but the self-correction table says 5 iterations for per-task quality. Which is it?
- Is simplification optional within the quality loop? Section 7.4's settings.json shows `autoSimplify: false` by default.
- If simplification is run and modifies code, are tests re-run? Is the reviewer invoked again? This could multiply iterations significantly.
- The quality report schema has fields for simplification, acceptance_tests, and review, suggesting all three are part of one pass. But the iteration count: does one iteration = one full simplify+test+review cycle?

**Recommendation:** Provide a detailed flowchart of the per-task quality loop showing the exact sequence: (1) simplify (optional), (2) run tests, (3) review. Define what constitutes one "iteration." Clarify the max iteration count (3 from AC template vs 5 from self-correction table). Reconcile the simplification timing with Section 6.4.

---

## Minor Issues

### m1. Stage numbering: 16 stages listed but only 14 are uniquely numbered

Stages 11a-11d in the diagram are all "Stage 11" (Parallel Execution). Stages 12 and 13 are shown as shared across parallel tasks in the diagram but numbered individually in the spec table. The distinction between "Stage 11" (spawn tasks) and "Stage 12-13" (quality loop per task) is clear in intent but the numbering is confusing because 11-12-13 overlap temporally. Consider whether the per-task quality loop is better modeled as a sub-stage of execution (11.1, 11.2) rather than a separate stage.

### m2. Plan fix produces `plan-v2.yaml` but the transition goes back to Stage 3

The transition is 4 -> 3, which re-invokes the reviewer. But the reviewer needs to know it is reviewing a revision, not a fresh plan. The reviewer context bridge (Section 7.8) shows review history is passed to the reviewer, which is good. But the pipeline-state iteration tracking (Section 5.6) shows `plan_review.current: 3` -- this counter is incremented when: the review happens, or the fix happens? If the counter increments on review and the limit is 3, then the flow allows 3 reviews (plan-v1, plan-v2, plan-v3). If it increments on fix, it allows 3 fixes (plan-v2, plan-v3, plan-v4). The semantics should be explicit.

### m3. Gap detection has two passes but only the second is a human gate

Section 3.5 says gap detection first pass is auto, second pass is human. But the self-correction table says max gap detection re-entry is 2. So the flow could be: (1) auto gap detection finds gaps, (2) re-enter Stage 6, execute gap tasks, (3) auto gap detection finds more gaps, (4) re-enter Stage 6 again, execute, (5) gap detection -- is this the "2nd pass" that becomes a human gate? Or does "2nd pass" refer to the second time gap detection runs (regardless of re-entry count)? Clarify.

### m4. The `status` field enum is inconsistent across schemas

- Plan schema: `draft | in_review | approved | rejected | blocked`
- Task schema: `pending | in_progress | completed | stuck | blocked`
- Per-task state machine (Section 2.4): `PENDING | BLOCKED | READY | RUNNING | SUCCEEDED | FAILED | NEEDS_REVIEW | RETRYING | REVISED`
- Task manifest: `in_review | approved`
- Pipeline state: `running | waiting_for_human | paused | completed | failed`

The task schema uses `completed` but the state machine uses `SUCCEEDED`. The task schema has `stuck` but the state machine does not. The state machine has `READY`, `RETRYING`, `NEEDS_REVIEW`, and `REVISED` which are not in the task schema. These must be reconciled; the task YAML on disk should reflect the actual state the dispatcher tracks.

### m5. The `execution-plan.yaml` is created at Stage 9 but the diagram shows Stage 10 as "Execution Graph"

Stage 9 creates `execution-plan.yaml` with batches and critical path. Stage 10 is "Execution Graph" which creates "worktrees/branches, rollback tags" and produces "Updated execution plan." The distinction between Stage 9 and Stage 10 is thin -- one computes the schedule, the other materializes it in git. Consider whether these should be a single stage with two substeps, since there is no gate or decision between them.

### m6. Session reuse matrix has a gap for gap detector re-entry

The session reuse matrix (Section 7.8) defines inheritance for `gap_detection:gap_detector -> planning:planner`. But when gap detection triggers re-entry to Stage 6 (Task Breakdown), the planner runs again. The matrix does not define what session the planner uses for gap-triggered re-entry. Does it resume the original planning session? Start fresh? Use a context bridge from the gap report? This is important because the planner needs the gap report context to scope its new task breakdown.

### m7. `rejected` verdict in review schema but no pipeline transition for it

The `ReviewOutput` schema allows `verdict: "reject"` (distinct from `request_changes`). The review finding severities include `critical`. But the transition table only handles `needs_changes` and `approved`. There is no transition for a `rejected` verdict. What happens if the reviewer rejects the plan entirely (as opposed to requesting changes)? The validator requires findings for a reject verdict, but the pipeline has no path for it.

### m8. Preflight check is mentioned but has no stage in the pipeline

Section 6.7 describes a preflight check (tool validation, stack detection) that runs before execution. Section 9 mentions it during project initialization. But it is not a pipeline stage and has no transition entry. When exactly does it run? Before Stage 1? Between Stage 5 and Stage 6? If the preflight fails (missing tool), does the pipeline block? What is the recovery path? This should be a Stage 0 or an explicit pre-pipeline step with a defined failure transition.

---

## Happy Path Dry-Run

**Feature:** "Add OAuth2 login to a Flask app"
**Assumptions:** Clean Flask project with existing SQLAlchemy models, pytest test suite, and basic session auth. The user runs `xpatcher start "Add OAuth2 login with Google and GitHub providers"` from the project root.

### Pre-pipeline (~10 seconds)

The dispatcher:
- Generates pipeline ID: `xp-20260329-f7c3`
- Creates `.xpatcher/oauth2-login/` directory structure
- Creates feature branch: `git checkout -b xpatcher/oauth2-login`
- Runs preflight check (detects Python/Flask/pytest stack)
- Writes `pipeline-state.yaml` with `current_stage: intent`
- Displays: "Pipeline started: xp-20260329-f7c3"

### Stage 1: Intent Capture (~15 seconds)

**Agent:** Planner (Opus[1m])
**Input:** User's natural language request
**Process:** The planner parses the request, identifies scope (OAuth2 with Google and GitHub), detects Flask/SQLAlchemy patterns in the codebase, and identifies open questions (e.g., "Should OAuth2 replace existing session auth or supplement it?")
**Output:** `intent.yaml` with `status: needs_clarification` (likely, given the scope question)
**Gate:** Auto, but switches to human if `open_questions` is non-empty
**Transition trigger:** Intent `status: ready` (after human answers questions)

**Timing estimate:** 15 seconds for analysis, plus potential human wait time for clarification
**Ambiguity flag:** The spec says gate is "Auto (human if questions)" but does not define how human Q&A is conducted. Does the TUI prompt inline? Is it a separate decision artifact? How is the answer fed back to produce a new intent.yaml with `status: ready`? **This Q&A loop is not in the transition table.**

### Stage 2: Planning (~3-5 minutes)

**Agent:** Planner (Opus[1m]) -- may include Expert Panel
**Input:** `intent.yaml` with `status: ready`
**Process:** For a non-trivial feature like OAuth2, the expert panel is activated. Backend expert, security architect, and QA automation experts provide independent analyses (Round 1, parallel, ~90 seconds each). If no conflicts, synthesis proceeds directly (Round 3, ~60 seconds). The synthesized plan covers: OAuth2 library selection, provider config, user model changes, login/callback routes, session integration, CSRF protection, tests.
**Output:** `plan-v1.yaml` with phases and ~8-10 tasks
**Gate:** Auto
**Transition trigger:** Plan produced

**Timing estimate:** 3-5 minutes (2 minutes for expert panel if 2 rounds, 1 minute for synthesis, 1 minute for plan formatting)
**Artifact:** `plan-v1.yaml` with ~4 phases, 8-10 tasks, risks (OAuth provider downtime, token refresh complexity)

### Stage 3: Plan Review (~1-2 minutes)

**Agent:** Reviewer (Opus)
**Input:** `plan-v1.yaml`, codebase context
**Process:** Reviews plan for completeness, risk coverage, task granularity, dependency correctness. Checks that referenced files exist. Validates acceptance criteria are automatable.
**Output:** `plan-review-v1.yaml` with `verdict: approved` or `verdict: needs_changes`
**Gate:** Auto

**Timing estimate:** 1-2 minutes
**Likely outcome for OAuth2:** `needs_changes` on first pass (common for complex features -- reviewer might flag missing CSRF protection task, or that the token refresh mechanism is not explicitly planned).

### Stage 4: Plan Fix (~1-2 minutes)

**Agent:** Planner (Opus[1m]) -- resumes reviewer session
**Input:** `plan-review-v1.yaml` findings
**Process:** Addresses each finding. Adds missing CSRF task, clarifies token refresh in acceptance criteria.
**Output:** `plan-v2.yaml`
**Gate:** Auto
**Transition trigger:** New plan version produced, loops back to Stage 3

### Stage 3 (second pass): Plan Review (~1-2 minutes)

**Agent:** Reviewer (Opus)
**Input:** `plan-v2.yaml`, prior review history
**Output:** `plan-review-v2.yaml` with `verdict: approved`
**Gate:** Auto (triggers human gate at Stage 5)

### Stage 5: Plan Approval (~human dependent, 0-4 hours)

**Agent:** None
**Input:** Approved plan, TUI prompt
**Process:** Human reviews `plan-v2.yaml`, sees 4 phases with 10 tasks, estimated medium complexity. TUI shows structured prompt with options: [1] Approve, [2] Request changes, [3] Reject, [4] View details.
**Gate:** **Hard human gate** -- pipeline blocks until human responds
**Transition trigger:** Human selects "Approve"

**Timing estimate:** If user is at terminal: 30 seconds to 5 minutes. If user stepped away: up to 4 hours (stale session threshold).

### Stage 6: Task Breakdown (~1-2 minutes)

**Agent:** Planner (Opus[1m])
**Input:** Approved `plan-v2.yaml`
**Process:** Decomposes plan phases into individual task YAMLs with acceptance criteria, file scopes, dependencies, and complexity estimates. Creates ~10 task files in `tasks/todo/`.
**Output:** `task-manifest.yaml`, `tasks/todo/task-001-oauth-config.yaml` through `task-010-csrf-protection.yaml`
**Gate:** Auto
**Transition trigger:** Tasks produced

**Timing estimate:** 1-2 minutes

### Stage 7: Task Review (~1 minute)

**Agent:** Reviewer (Opus)
**Input:** `task-manifest.yaml`, all task YAMLs
**Process:** Reviews task granularity (each < 5 files), acceptance criteria testability, dependency correctness, complexity estimates.
**Output:** `task-review-v1.yaml`
**Gate:** Soft (30-minute auto-proceed if no major findings)
**Likely outcome:** Approved, possibly with minor suggestions about task splitting.

**Timing estimate:** 1 minute for review, then either auto-proceeds or waits up to 30 minutes

### Stage 8: Task Fix (skipped on happy path)

### Stage 9: Prioritization (~10 seconds)

**Agent:** Dispatcher (automated)
**Input:** Approved task manifest with dependency graph
**Process:** Topological sort of tasks, critical path identification, batch assignment. Task-001 (OAuth config) has no deps and goes in batch 1. Tasks 002-004 depend on 001 and form batch 2. And so on.
**Output:** `execution-plan.yaml` with 4 batches, critical path marked
**Gate:** Auto

### Stage 10: Execution Graph (~15 seconds)

**Agent:** Dispatcher (automated)
**Input:** `execution-plan.yaml`
**Process:** Creates git worktrees for batch 1 tasks, creates task branches, sets up rollback tags.
**Output:** Updated `execution-plan.yaml` with branch names and worktree paths
**Gate:** Auto

### Stage 11: Parallel Execution (~15-25 minutes total)

**Batch 1 (~3-5 minutes):**
- task-001 (OAuth config): Executor (Sonnet) adds `authlib` dependency, creates `config/oauth.py` with Google and GitHub provider configs, creates environment variable template.
- Output: code committed on `xpatcher/oauth2-login/TASK-001`

**Batch 2 (~5-8 minutes, 3 tasks in parallel):**
- task-002 (User model): Extends SQLAlchemy User model with OAuth fields
- task-003 (OAuth routes): Creates `/auth/google/login`, `/auth/google/callback`, etc.
- task-004 (Token service): Token management service

**Batch 3 (~5-8 minutes):**
- task-005-007: Session integration, middleware, error handling

**Batch 4 (~3-5 minutes):**
- task-008-010: Integration tests, UI templates, CSRF protection

**Gate:** Auto per task completion
**Transition trigger:** Task code committed -> Stage 12

**Critical timing question:** Each task goes through Stage 11 (execution) and immediately enters Stage 12 (quality). So Stages 11-13 run concurrently across tasks. The batch structure controls when new tasks START, but quality loops run independently per task.

### Stage 12: Per-Task Quality (~2-5 minutes per task)

For each completed task:
1. **Simplify** (if enabled): Simplifier reviews the diff, may reduce complexity. ~30 seconds.
2. **Test**: Tester generates tests for acceptance criteria, runs them. ~1-2 minutes.
3. **Review**: Reviewer examines the diff against task spec. ~1-2 minutes.

**Output per task:** `task-NNN-quality-report-v1.yaml`
**Gate:** Auto
**Transition trigger:** Quality `pass` -> task is done, check if batch complete / all tasks complete.

**For our OAuth2 feature:** Most tasks pass quality in 1-2 iterations. A few may need fixes (see Failure Path for those). On the happy path, assume average 1.5 iterations.

### Stage 13: Fix Iteration (runs as needed within Stage 12 loop)

For tasks that get `needs_fix`, the executor is re-invoked with review findings. It fixes the code, commits, and re-enters Stage 12. On the happy path, most tasks need at most 1 fix.

### Stage 14: Gap Detection (~2-3 minutes)

**Agent:** Gap Detector (Opus)
**Input:** All completed tasks, full diff on feature branch, plan, test results
**Process:** Checks plan coverage, integration points (do the routes actually call the token service? does the user model migration exist?), error handling completeness, edge cases.
**Output:** `gap-report-v1.yaml` with `verdict: complete`
**Gate:** Auto (first pass)

**Timing estimate:** 2-3 minutes
**On happy path:** No critical gaps found. Possibly 1 minor gap (deferred).

### Stage 15: Documentation (~1-2 minutes)

**Agent:** Technical Writer (Sonnet)
**Input:** Plan summary, full diff, completed task list, existing docs inventory
**Process:** Updates README.md with OAuth2 configuration section, creates `docs/auth/oauth2.md` with setup guide, updates CHANGELOG.
**Output:** `docs-report.yaml`, committed doc changes on feature branch
**Gate:** Auto (transitions to human at Stage 16)

### Stage 16: Completion (~human dependent)

**Agent:** None (dispatcher)
**Process:**
1. Pushes feature branch to remote
2. Creates PR (if `gh` CLI available)
3. Generates completion summary
4. Presents to human for confirmation

**Output:** `completion.yaml`, PR URL
**Gate:** **Hard human gate**

**Total happy path timing estimate:** ~35-55 minutes of compute time, plus human gate latency at Stages 5 and 16.

**Time breakdown:**
| Phase | Estimated Time |
|-------|---------------|
| Intent + Planning (Stages 1-5) | 8-12 minutes + human wait |
| Task Breakdown + Review (Stages 6-8) | 2-4 minutes |
| Scheduling (Stages 9-10) | ~30 seconds |
| Execution + Quality (Stages 11-13) | 20-35 minutes |
| Gap Detection (Stage 14) | 2-3 minutes |
| Documentation (Stage 15) | 1-2 minutes |
| Completion (Stage 16) | ~10 seconds + human wait |

---

## Failure Path Dry-Run

Same feature: "Add OAuth2 login to a Flask app." We inject the following failures:

### Failure 1: Plan review rejects the plan twice

**Iteration 1:**
- Stage 2: Planner produces `plan-v1.yaml`
- Stage 3: Reviewer produces `plan-review-v1.yaml` with `verdict: needs_changes`
  - Finding: "No task for handling OAuth token refresh when access token expires"
  - Finding: "Session migration task missing -- existing users need migration path"
- Stage 4: Planner fixes, produces `plan-v2.yaml`
- `pipeline-state.yaml`: `iteration_counts.plan_review: 1`

**Iteration 2:**
- Stage 3: Reviewer reviews `plan-v2.yaml`, produces `plan-review-v2.yaml` with `verdict: needs_changes`
  - Finding: "Token refresh task added but acceptance criteria are not automatable (says 'verify refresh works' instead of a test command)"
- Stage 4: Planner fixes acceptance criteria, produces `plan-v3.yaml`
- `pipeline-state.yaml`: `iteration_counts.plan_review: 2`

**Iteration 3:**
- Stage 3: Reviewer reviews `plan-v3.yaml`, produces `plan-review-v3.yaml` with `verdict: approved`
- Pipeline proceeds to Stage 5 (human approval)
- `pipeline-state.yaml`: `iteration_counts.plan_review: 3` -- at the cap, but approved on this iteration so no escalation

**If the review had NOT approved on iteration 3:**
- `plan_review: 3` equals the max (3)
- Pipeline escalates to human with full review history
- Pipeline state: `status: waiting_for_human`, `pending_decision: "plan-review-escalation"`
- Human sees: plan-v3.yaml, all 3 review versions, and must decide: (a) approve as-is, (b) manually edit plan, (c) abort pipeline
- **Question:** The escalation path for plan review limit (3) in Section 3.4 says "Escalate to human with full review history." But this human intervention is not a Stage 5 approval -- it is an emergency gate at Stage 3. The pipeline-state needs a distinct state for "escalated." Is this `waiting_for_human`? Does the human's decision return to Stage 4 (with human feedback as the "review"), or does it jump to Stage 5?

### Failure 2: One task fails review 3 times (hits cap)

Task-003 (OAuth callback routes) enters the quality loop:

**Quality iteration 1:**
- Stage 12: Tester finds tests pass, but Reviewer flags: "Callback endpoint does not validate `state` parameter -- CSRF vulnerability (critical)"
- Stage 13: Executor fixes, adds state validation
- `pipeline-state.yaml`: `quality_loop.task-003.current: 1`

**Quality iteration 2:**
- Stage 12: Reviewer flags: "State validation added but uses timing-vulnerable string comparison. Use `hmac.compare_digest`"
- Stage 13: Executor fixes
- `pipeline-state.yaml`: `quality_loop.task-003.current: 2`

**Quality iteration 3:**
- Stage 12: Reviewer flags: "HMAC comparison fixed, but the `state` token generation uses `random` instead of `secrets`. Cryptographically insecure."
- Stage 13: Executor fixes
- `pipeline-state.yaml`: `quality_loop.task-003.current: 3`

**Quality iteration 4:**
- Stage 12: Reviewer flags: "Now using `secrets.token_urlsafe` -- good. But the state is stored in a plain cookie without `HttpOnly` flag."
- Stage 13: Executor fixes
- `pipeline-state.yaml`: `quality_loop.task-003.current: 4`

**Quality iteration 5 (max):**
- Stage 12: Reviewer still flags: "Cookie now has HttpOnly but missing `SameSite=Lax` attribute."
- `quality_loop.task-003.current: 5` equals max
- Pipeline marks task-003 as `stuck`
- Task file moved from `tasks/in-progress/task-003-oauth-routes.yaml` to `tasks/todo/task-003-oauth-routes.yaml`
- **Status:** The task is `stuck`, but other tasks in the same batch continue
- **Dependent tasks:** task-006 (session integration) depends on task-003. It remains `blocked`.

**Remaining pipeline behavior:**
- All non-dependent tasks continue through quality loops
- When all executable tasks complete, the pipeline enters a partially-completed state
- Pipeline displays the BLOCKED output (Section 3.6) showing task-003 as stuck, task-006 as blocked
- Human options: fix manually and `xpatcher resume`, skip task (`xpatcher skip task-003`), or cancel

**Ambiguity identified:** The spec says the quality loop max is 5 (Section 3.4) but the acceptance criteria template says `max_retry_cycles: 3` (Section 6.1). If both apply, which takes precedence? Per-task config or global config?

### Failure 3: One task gets stuck (oscillation detected)

Task-005 (middleware setup) enters the quality loop:

**Quality iteration 1:**
- Reviewer: "Middleware is not properly chained -- request context is lost after OAuth check"
- Executor fixes by wrapping in Flask `before_request`

**Quality iteration 2:**
- Reviewer: "Using `before_request` breaks existing API endpoints that don't need auth. Should use a decorator pattern instead."
- Executor reverts `before_request`, switches to decorator

**Quality iteration 3:**
- Oscillation detection triggers: The findings hash from iteration 3 matches iteration 1 (both point to "middleware not properly chained"). The executor oscillated between `before_request` and decorator approaches.
- **Immediate escalation** -- does not wait for remaining iterations
- Task-005 marked `stuck` with reason: `oscillation_detected`
- Pipeline state updated, TUI shows warning

**Critical question:** The findings hash is computed over "the set of active findings." But findings in iteration 1 said "context is lost" and iteration 3 says "middleware not properly chained." These are semantically similar but textually different. Is the hash over the finding IDs? The finding text? The file locations? If the hash is too coarse (e.g., just finding categories), false positives occur. If too fine (exact text), oscillation is missed. **The spec does not define what is hashed.**

### Failure 4: Gap detection finds 2 critical gaps

After all executable tasks complete (some stuck, some done), gap detection runs:

**Gap detection pass 1:**
- Gap Detector analyzes the integrated codebase
- Finds 2 critical gaps:
  1. "No CSRF token rotation on OAuth callback -- replay attack possible" (severity: critical)
  2. "OAuth provider error responses (rate limit, invalid grant) have no user-facing error page" (severity: critical)
- `gap-report-v1.yaml` with `verdict: gaps_found`, 2 new tasks proposed
- Scope check: 2 new tasks vs ~10 original tasks = 20% < 30% cap. Passes.

**Re-entry to Stage 6:**
- Pipeline returns to Stage 6 with gap context
- Planner creates `task-011-csrf-rotation.yaml` and `task-012-oauth-error-pages.yaml`
- Tasks placed in `tasks/todo/`

**Gap tasks go through Stages 6-13:**
- **Stage 7:** Task review for gap tasks (auto-reviewer checks they are well-scoped)
- **Stage 8:** Fix if needed
- **Stage 9-10:** Updated execution plan (new batch 5 with just these 2 tasks)
- **Stage 11:** Execution of gap tasks
- **Stage 12-13:** Quality loop for gap tasks

**Question:** Does the task manifest get a new version? `task-manifest-v2.yaml`? The current manifest schema has `status: approved` and a fixed task list. Appending tasks to an approved manifest is architecturally questionable. The spec does not address this.

**Gap detection pass 2:**
- After gap tasks complete, Stage 14 runs again
- `gap-report-v2.yaml` with `verdict: complete`
- `pipeline-state.yaml`: `iteration_counts.gap_detection: 2` (at max)
- Pipeline proceeds to Stage 15 (Documentation)

**If gaps were found again on pass 2:**
- `gap_detection: 2` equals max
- Escalate to human with both gap reports
- Human decides: (a) create more tasks manually, (b) accept remaining gaps, (c) abort

### Combined failure scenario: final pipeline state

With all four failures active:
- task-003: stuck (hit quality cap at 5 iterations)
- task-005: stuck (oscillation detected at iteration 3)
- task-006: blocked (depends on task-003)
- task-011, task-012: completed (gap tasks)
- All other tasks: completed

Pipeline output shows BLOCKED status. Human must decide what to do about tasks 003, 005, and 006. The pipeline cannot reach Stage 15 (Documentation) or Stage 16 (Completion) because some tasks are stuck.

**Critical question:** Can the pipeline proceed to Documentation and Completion with stuck tasks, or does it hard-block? The spec's failure output (Section 3.6) implies the pipeline blocks, but does not explicitly state the blocking condition. Is it "any stuck task" or "any stuck task with dependents that are also stuck"?

---

## Missing Transitions

| From | To | Scenario | Status |
|------|----|----------|--------|
| 1 Intent | 1 Intent (Q&A loop) | Intent has `open_questions`, human answers, intent is re-parsed | **Not in transition table** |
| 3 Plan Review | Human escalation | Plan review hits max iterations (3) without approval | Described in Section 3.4 but **not in transition table** |
| 7 Task Review | Human escalation | Task review hits max iterations (3) without approval | Described in Section 3.4 but **not in transition table** |
| 12 Quality | Human escalation | Per-task quality hits max (5) and task is marked stuck | Described in Section 3.4 as "mark stuck, continue" but **stuck task escalation not in transition table** |
| 14 Gap Detection | Human escalation | Gap detection hits max re-entry (2) with gaps still found | Described in Section 3.4 but **not in transition table** |
| 15 Documentation | Failure | Tech-writer produces invalid output after retries | **No failure transition defined** |
| 3 Plan Review | 5 Approval | Reviewer gives `verdict: rejected` (not `needs_changes`) | **No transition for reject verdict** |
| 7 Task Review | ? | Reviewer gives `verdict: rejected` | **No transition for reject verdict** |
| Any stage | Paused | User pauses pipeline (`xpatcher pause` or Ctrl+C) | **Not in transition table** (described in Section 2.7 but not formalized) |
| Paused | Resume | User resumes pipeline | **Not in transition table** |

---

## Ambiguous Stage Boundaries

1. **Stage 11 -> Stage 12 boundary per task:** The transition table says `11 Execution -> 12 Quality Loop | Task code committed | Auto`. But during parallel execution, individual tasks cross this boundary at different times. Is the "transition" tracked per-task or per-pipeline? The pipeline-level state machine cannot be in both Stage 11 and Stage 12 simultaneously, but that is what happens.

2. **Stage 12 -> "Next batch or 14" boundary:** The phrasing "Next batch or 14" is a conditional transition, but the condition is not specified. The condition is presumably "all tasks in the pipeline have passed quality or are stuck, and there are no more batches to start." This should be stated explicitly.

3. **Stage 14 -> Stage 6 re-entry boundary:** When does the pipeline transition from "gap detection found gaps" to "re-entering task breakdown"? Is it immediate, or does the human need to approve the gap-identified tasks? Section 3.5 says gap detection first pass is auto but second pass involves human. But for the first pass, auto-approving gap tasks that generate new code seems aggressive.

4. **Stage 5 -> Stage 6 boundary:** Is there a delay between plan approval and task breakdown starting? Can the human request that task breakdown wait (e.g., "I approve the plan but want to start execution tomorrow")? The "defer" option in the decision template suggests yes, but the pipeline has no "deferred" state.

---

## Pipeline Resumption Gaps

1. **Stage 11 (Parallel Execution) resumption:** If the pipeline crashes during parallel execution, the dispatcher resumes. But which tasks were mid-execution? The `tasks/in-progress/` folder tells us which tasks were active, but the Claude session for each task may be lost (if the session ID is not saved before the crash). The session registry is saved to disk, but the execution log may have a gap between the last logged step and the crash.

2. **Stage 12-13 (Quality Loop) resumption:** If the pipeline crashes after a quality report is written but before the fix iteration starts, the dispatcher must detect that the quality report exists and has `verdict: needs_fix`, and re-enter the fix iteration. This is feasible from the artifact state but is not explicitly described as a resume scenario.

3. **Human gate resumption:** If the pipeline is waiting for human input (Stage 5 or 16) and the dispatcher crashes, on resume it reads `pipeline-state.yaml` with `status: waiting_for_human`. It must re-display the human prompt. Is the original prompt reconstructed from artifacts, or is it stored? The `pending_decision` field in pipeline-state.yaml suggests a stored reference, but the decision template is not persisted.

4. **Worktree state on resume:** If the dispatcher crashes during worktree creation (Stage 10) or during execution, worktrees may be in an inconsistent state. The resume logic should validate worktrees exist, are on the correct branches, and have no uncommitted changes. This is not described.

5. **Base branch drift during long human gates:** Section 2.7 says "If base changed: rebases feature branch, re-runs affected tests." But this only applies to `xpatcher resume`. If the pipeline is paused at Stage 5 for 3 hours and main has advanced significantly, the rebase happens on resume. But what if the rebase has conflicts? The spec does not define a rebase conflict resolution path.

---

## Questions for Product Owner

1. **Should stuck tasks block the entire pipeline, or can the pipeline proceed to Documentation/Completion with a partial feature?** The spec implies full blocking, but users may prefer partial delivery with explicit documentation of what is missing.

2. **Is the expert panel required for every feature, or only features above a complexity threshold?** Running 5-7 Sonnet agents for a "fix typo in README" feature is wasteful. What is the activation criteria?

3. **Should the 30-minute soft gate on task review be configurable per-project?** Some teams want fast iteration; others want every task review to be human-approved.

4. **For gap detection re-entry: should the human approve gap-identified tasks before they are executed?** Currently the first pass is auto, which means gap tasks are automatically generated and executed without human oversight. For a security-sensitive feature like OAuth2, this seems risky.

5. **What is the desired behavior when `xpatcher resume` is called and the base branch has conflicting changes?** Options: (a) abort and ask human, (b) attempt rebase and escalate on conflict, (c) create a new branch from updated main and re-run from the current stage. The spec says "rebases" but does not handle conflicts.

6. **Should the pipeline support "skip and continue" at any stage, not just for stuck tasks?** E.g., "skip documentation" or "skip simplification." The settings.json `autoSimplify: false` suggests this is already partially supported, but a general skip mechanism is not defined.

7. **The iteration cap for plan review is 3 but the state tracking example (Section 5.6) shows `max: 5`.** Which is the intended default? The inconsistency needs resolution.

---

## Recommendations

### R1. Define the worktree merge protocol (Critical, blocks implementation)

Add a new subsection specifying:
- Tasks are merged to the feature branch immediately upon passing quality (not at batch boundaries)
- Use `git merge --no-ff` to create a merge commit with task metadata
- If the merge has conflicts (from parallel sibling tasks), attempt auto-resolution; if that fails, mark the task as `blocked` with reason `merge_conflict` and escalate
- Worktrees are cleaned up after merge
- For stuck tasks, worktrees are preserved (not cleaned up) so users can inspect them

### R2. Reconcile the state machine with the 16-stage pipeline (Critical)

Replace the Section 2.4 state machine with one that exactly matches the 16 stages. Use the stage names as enum values in `pipeline-state.yaml`. The per-task state machine should be reconciled with the task YAML schema -- use one set of status values everywhere.

### R3. Specify gap re-entry as a formal sub-pipeline (Critical)

Define gap re-entry as running Stages 6-14 (scoped to new gap tasks only), with all the same gates and transitions. The task manifest gets a new version (v2). The execution plan is regenerated for only the new tasks. Completed tasks are not re-run. The gap detector on the second pass sees both original and gap-task results.

### R4. Add failure transitions to the transition table (Major)

Every escalation path described in Section 3.4 should have a corresponding entry in the transition table. Add transitions for: iteration limit escalation at each loop, `rejected` verdict handling, documentation failure, pause/resume, and the intent Q&A loop.

### R5. Clarify per-task quality loop internals (Major)

Create a sub-flowchart showing: simplify (if enabled) -> test -> review, and define what counts as one "iteration." Resolve the 3 vs 5 max iteration discrepancy. Make simplification timing consistent with Section 6.4.

### R6. Specify the completion gate concretely (Major)

Define what the human sees, what options they have, and what each option does. The branch should NOT be pushed before human confirmation (pushing first is not reversible without force-push).

### R7. Add documentation failure handling (Major)

At minimum: documentation failures produce a warning and the pipeline proceeds to completion with a note that docs were not updated. The human at Stage 16 sees this warning and can decide whether to block.

### R8. Define batch vs DAG scheduling precedence (Major)

Recommend: use DAG-driven scheduling with batches as documentation/hints only. A task starts executing as soon as all its dependencies have passed quality, regardless of batch boundaries. This is more efficient and aligns with the DAG design in Section 2.5. Batches are computed for the TUI display and time estimation, not for scheduling enforcement.

### R9. Add pipeline state diagram for pause/resume (Minor)

Add `paused` as an explicit pipeline state with transitions from any active state. Define resume transitions that return to the exact stage where pause occurred.

### R10. Define oscillation hash function (Minor)

Specify that oscillation detection hashes the set of finding IDs (not finding text, not file locations). Finding IDs are stable across iterations if the same issue recurs, even if the wording changes. The agent must produce deterministic finding IDs for this to work -- add this requirement to the reviewer agent definition.

---

*End of review.*

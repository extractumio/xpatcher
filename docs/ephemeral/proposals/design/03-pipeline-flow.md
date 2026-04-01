# Pipeline Flow

> Part of the [xpatcher Design Proposal](../xpatcher-design-proposal.md)

---

## 3.1 Stage Overview Diagram

```
  [1] Intent --> [2] Spec Draft --> [3] Spec Review --> [4] Spec Fix ---+
                                        ^                               |
                                        +-------- loop -----------------+
                                                    |
                                           [5] Spec Freeze
                                                    |
                                     +--------------+
                                     v
  [6] Slice Breakdown --> [7] Slice Review --> [8] Slice Fix ----+
                                ^                                |
                                +-------- loop ------------------+
                                                  |
                           [9] Prioritization + Dependency Graph
                                                  |
                           [10] Execution Graph (DAG)
                                                  |
                    +-------------+-----------------+--------------+
                    v             v                 v              v
              [11a] Task    [11b] Task      [11c] Task     [11d] Task
                    |             |                 |              |
              [12] Per-task loop: test --> review --> [simplify]
                    |             |                 |              |
              [13] Fix iteration (per task)
                    |             |                 |              |
                    +-------------+-----------------+--------------+
                                                  |
                                   [14] Spec-to-Code Gap Detection
                                                  |
                                    +--- gaps found? ---+
                                    v                    v
                              [6] re-enter      [15] Documentation
                                                       |
                                                [16] Completion Summary
```

## 3.2 Stage Specification Table

| # | Stage | Entry Criteria | Exit Criteria | Gate Type | Agent | Artifacts Produced |
|---|-------|---------------|---------------|-----------|-------|--------------------|
| 1 | Intent Capture | User provides request | Structured intent with goal, scope, constraints, questions | Auto (human if questions) | Planner | `intent.yaml` |
| 2 | Specification Draft | Intent `status: ready` | Executable specification with phases, risks, anti-scope | Auto | Planner | `plan-v1.yaml` |
| 3 | Specification Review | Spec `status: in_review` | Spec review with `approved \| needs_changes \| rejected` + findings | Auto | Plan Reviewer | `plan-review-v1.yaml` |
| 4 | Specification Fix | Review `verdict: needs_changes` | New spec version addressing findings | Auto | Planner | `plan-v2.yaml` |
| 5 | Specification Freeze | Review `verdict: approved` | Auto-approval recorded unless ambiguity or config requires human confirmation | Auto by default | Dispatcher | Plan approval decision |
| 6 | Execution Slice Breakdown | Approved spec | Validated task manifest plus materialized `tasks/todo/task-NNN-<slug>.yaml` files | Auto | Planner | `tasks/task-NNN.yaml`, `task-manifest.yaml` |
| 7 | Execution Slice Review | Manifest `status: in_review` | Task-manifest review with `approved \| needs_changes \| rejected` + findings | Auto | Plan Reviewer | `task-review-v1.yaml` |
| 8 | Execution Slice Fix | Review `verdict: needs_changes` | Revised tasks | Auto | Planner | Updated task YAMLs |
| 9 | Prioritization | Approved task manifest | Execution order, batches, concurrency | Auto | Dispatcher | `execution-plan.yaml` |
| 10 | Execution Graph | Execution plan exists | v1: rollback tags created. v2: worktrees/branches created, rollback tags | Auto | Dispatcher | Updated execution plan |
| 11 | Task Execution | Batch deps satisfied | v1: code committed on feature branch (sequential). v2: code committed on task branches (parallel), merged to feature branch per Section 2.6.1 | Auto | Executor | `tasks/task-NNN-execution-log.yaml` |
| 12 | Per-Task Quality | Code committed | Dispatcher-run command checks + review pass (+ optional simplify) | Auto | Dispatcher, Reviewer, Simplifier (if enabled) | `tasks/task-NNN-quality-report.yaml` |
| 13 | Fix Iteration | Quality `needs_fix` | Fix committed, re-enter Stage 12 | Auto | Executor | Updated commits |
| 14 | Spec-to-Code Gap Detection | All tasks completed | Gap report (complete or gaps found) | Auto (2nd pass: human on iteration limit) | Gap Detector | `gap-report.yaml` |
| 15 | Documentation | Gap report `verdict: complete` | Relevant docs created/updated | Auto | Technical Writer | `docs-report.yaml`, updated doc files |
| 16 | Completion Summary | Docs updated, all stages green | Summary generated and pipeline marked done; human gate optional by config | Auto by default | Dispatcher | `completion.yaml` |

## 3.2.1 Pipeline State Enum (`current_stage`)

This is the single authoritative definition of pipeline-level states. The `current_stage` field in `pipeline-state.yaml` must be one of these values. Every transition is validated — invalid transitions are rejected with an error. State is serialized to disk on every transition.

```python
class PipelineStage(str, Enum):
    UNINITIALIZED    = "uninitialized"      # Pipeline created, not yet started
    INTENT_CAPTURE   = "intent_capture"     # Stage 1:  Capturing user request
    PLANNING         = "planning"           # Stage 2:  Generating executable specification
    PLAN_REVIEW      = "plan_review"        # Stage 3:  Reviewing specification
    PLAN_FIX         = "plan_fix"           # Stage 4:  Revising specification based on review
    PLAN_APPROVAL    = "plan_approval"      # Stage 5:  Auto freeze or optional human confirmation
    TASK_BREAKDOWN   = "task_breakdown"     # Stage 6:  Decomposing specification into execution slices
    TASK_REVIEW      = "task_review"        # Stage 7:  Reviewing task manifest
    TASK_FIX         = "task_fix"           # Stage 8:  Revising tasks based on review
    PRIORITIZATION   = "prioritization"     # Stage 9:  Ordering tasks, building batches
    EXECUTION_GRAPH  = "execution_graph"    # Stage 10: Creating DAG, rollback tags (v2: worktrees)
    TASK_EXECUTION   = "task_execution"     # Stage 11: Running task code
    PER_TASK_QUALITY = "per_task_quality"   # Stage 12: Test + review loop (+ optional simplify on pass)
    FIX_ITERATION    = "fix_iteration"      # Stage 13: Executor fixing quality findings
    GAP_DETECTION    = "gap_detection"      # Stage 14: Cross-task gap analysis
    DOCUMENTATION    = "documentation"      # Stage 15: Creating/updating docs
    COMPLETION       = "completion"         # Stage 16: [Human Gate] Final review

    # Terminal / meta states
    DONE             = "done"               # Pipeline completed successfully
    PAUSED           = "paused"             # Pipeline suspended (Ctrl+C, `xpatcher pause`)
    BLOCKED          = "blocked"            # Waiting on human escalation (iteration limit)
    FAILED           = "failed"             # Unrecoverable error
    CANCELLED        = "cancelled"          # Pipeline cancelled via `xpatcher cancel`
    ROLLED_BACK      = "rolled_back"        # Pipeline rolled back via `xpatcher rollback`
```

**Note on Stages 11-13:** During task execution, the pipeline-level `current_stage` reflects the *active phase* of the currently-running task (or the batch). Individual task progress is tracked via per-task states (see Section 2.5). When multiple tasks are in flight (v2), the pipeline stage is `TASK_EXECUTION` until all tasks in the current batch have completed their quality loops.

Per-task states (`TaskState`) are defined in Section 2.5.

## 3.3 Stage Transition Table

| From | To | Trigger | Gate |
|------|----|---------|------|
| 1 Intent | 2 Plan | Valid `IntentOutput` written | Auto (human if open questions) |
| 2 Plan | 3 Plan Review | Plan produced | Auto |
| 3 Plan Review | 4 Plan Fix | Verdict `needs_changes` | Auto |
| 3 Plan Review | 5 Approved | Verdict `approved` | **Human confirmation** |
| 4 Plan Fix | 3 Plan Review | New plan version | Auto |
| 5 Approved | 6 Task Breakdown | Approval recorded | Auto |
| 6 Breakdown | 7 Task Review | Valid non-empty `TaskManifestOutput` produced and task files materialized | Auto |
| 7 Task Review | 8 Task Fix | Verdict `needs_changes` | Auto |
| 7 Task Review | 9 Prioritization | Verdict `approved` | Auto |
| 8 Task Fix | 7 Task Review | Revised tasks | Auto |
| 9 Priority | 10 Exec Graph | Priority assigned | Auto |
| 10 Exec Graph | 11 Execution | DAG ready | Auto |
| 11 Execution | 12 Quality Loop | Task code committed | Auto |
| 12 Quality | 13 Fix Iteration | Quality `needs_fix` | Auto |
| 12 Quality | Next task/batch or 14 | Quality `pass` (v2: + merge to feature branch per 2.6.1) | Auto |
| 13 Fix | 12 Quality Loop | Fix committed | Auto |
| 14 Gap Detection | 6 Breakdown (scoped) | Gaps found | Auto |
| 14 Gap Detection | 15 Documentation | No gaps | Auto |
| 15 Documentation | 16 Completion | Docs updated | **Human confirmation** |

## 3.4 Self-Correction Loop Design

Every loop in the pipeline has a hard iteration ceiling with escalation:

| Loop | Max Iterations | On Limit Exceeded |
|------|---------------|-------------------|
| Plan review/fix (Stages 3-4) | 3 | Escalate to human with full review history |
| Task review/fix (Stages 7-8) | 3 | Escalate to human with full review history |
| Per-task quality (Stages 12-13) | 3 | Mark task `stuck`, continue other tasks |
| Gap detection re-entry (Stage 14) | 2 | Escalate to human with gap reports |

**Per-task quality loop flowchart:**

```
Task code committed
    │
    v
(1) TEST ──── run acceptance criteria commands + regression suite
    │
    ├─ fail? ──> increment iteration counter
    │            ├─ iterations < max (3)? ──> FIX ITERATION (Stage 13) ──> re-enter (1)
    │            └─ iterations >= max?    ──> mark task STUCK, escalate
    v
(2) REVIEW ── adversarial code review (fresh session, context bridge)
    │
    ├─ needs_fix? ──> increment iteration counter
    │                 ├─ iterations < max (3)? ──> FIX ITERATION (Stage 13) ──> re-enter (1)
    │                 └─ iterations >= max?    ──> mark task STUCK, escalate
    v
(3) SIMPLIFY (optional, only if autoSimplify enabled in config)
    │   ├─ Run simplification on changed files
    │   ├─ Each simplification is a separate commit
    │   └─ RE-TEST after simplification (regression check)
    │        ├─ tests fail? ──> revert simplification commits, proceed without
    │        └─ tests pass? ──> keep simplification
    v
TASK COMPLETE ── advance to next task or Stage 14
```

One "iteration" = one test + review cycle. Simplification is a **post-approval refinement step**, not part of the retry loop. Simplification failures are reverted silently and never increment the iteration counter.

**Oscillation detection**: after each fix iteration, hash the set of active findings. If a previously-seen hash reappears, the agent is oscillating between two states. Escalate immediately rather than burning remaining iterations.

**Premature victory prevention**: the executor never self-certifies completion. The dispatcher runs command-backed acceptance criteria independently from agent self-reporting. A separate verification agent cross-references the diff against the task spec. Pre-existing checks are re-run to catch regressions.

### 3.4.1 Gap Re-entry Protocol

When the gap detector (Stage 14) reports `verdict: gaps_found`, the pipeline re-enters a scoped sub-pipeline to address the gaps. This section defines the exact re-entry semantics.

**Stages that re-run on gap re-entry:** Stages 6 through 14 (Task Breakdown → Gap Detection), scoped to gap tasks only. Completed tasks from the original run are untouched.

```
Gap Detection (Stage 14) → verdict: gaps_found
    │
    ├─ Depth check: current_gap_depth < max_gap_depth (default: 2)?
    │    ├─ Yes → proceed with scoped re-entry
    │    └─ No  → escalate to human with all gap reports
    │
    ├─ [6] Task Breakdown (scoped): planner receives gap report + original intent
    │      + completed task summaries. Produces only NEW tasks for gaps.
    │      Gap tasks are prefixed: task-G001, task-G002, etc.
    │
    ├─ [7] Task Review: reviewer validates gap tasks against gap report
    ├─ [8] Task Fix: if review requires changes
    │
    ├─ [9] Prioritization: gap tasks are appended to execution plan
    │      Original completed tasks are NOT re-prioritized.
    │
    ├─ [10] Execution Graph: DAG updated with gap tasks only
    ├─ [11] Task Execution: gap tasks execute on the feature branch
    ├─ [12-13] Per-Task Quality: standard quality loop for gap tasks
    │
    └─ [14] Gap Detection (round 2): re-runs gap analysis on ALL tasks
           (original + gap tasks). If still gaps found AND depth < max,
           re-enters again. Otherwise escalates to human.
```

**Manifest versioning:** Each gap re-entry produces a new task manifest version: `task-manifest-v2.yaml`, `task-manifest-v3.yaml`. The latest manifest is the authoritative task list. Previous manifests are retained for audit.

**Gap context flow to planner:** The planner receives:
1. The original `intent.yaml`
2. The gap report (`gap-report-v{N}.yaml`) with specific findings
3. A summary of all completed tasks (task IDs, descriptions, files changed)
4. The current state of the codebase (via tool access)

The planner does NOT receive the full original specification during gap re-entry — it reads the gap report and creates targeted tasks to address specific gaps.

**Depth limit enforcement:** `max_gap_depth: 2` (configurable in `config.yaml`). After 2 rounds of gap detection + re-entry, any remaining gaps are escalated to the human with the full history of gap reports. This prevents unbounded recursion.

**Human approval gate for gap tasks:** Gap tasks categorized as `critical` in the gap report are auto-approved for execution. Gap tasks categorized as `expected` require human approval before execution. Gap tasks categorized as `enhancement` are deferred to a backlog file (`.xpatcher/<feature>/deferred-gaps.yaml`) and not executed. See Section 6.5 for gap categorization rules.

**Pipeline state tracking:**
```yaml
# In pipeline-state.yaml during gap re-entry:
gap_reentry:
  current_depth: 1
  max_depth: 2
  rounds:
    - round: 1
      gap_report: "gap-report-v1.yaml"
      gap_tasks_created: ["task-G001", "task-G002"]
      gap_tasks_completed: ["task-G001", "task-G002"]
      manifest_version: "task-manifest-v2.yaml"
```

## 3.5 Human Gate Design

**Hard gates** (always block):
- Plan approval (Stage 5)
- Final completion (Stage 16)
- Any escalation from iteration limits

**Hard gate notification:** When a hard gate is reached, the dispatcher:
1. Prints a terminal bell character (`\a`) — terminal emulators (iTerm2, tmux, etc.) can surface this as a system notification
2. Starts a configurable **soft timeout** (default: 2 hours). After the timeout expires, the pipeline pauses, writes state, and prints a clear resume message:
   ```
   ⏳ Plan approval pending for 2h with no response.
   Pipeline paused. Resume with: xpatcher resume xp-20260328-a1b2
   Or check all pending gates: xpatcher pending
   ```
3. The gate remains available for response — pausing does not discard it. On resume, the same gate prompt is re-displayed.

**Soft gates** (auto-proceed after timeout):
- Task review approval: 30-minute window for human intervention if no major findings
- Per-task completion: human can subscribe but does not block

**Auto gates** (no human):
- All fix iterations
- Prioritization, DAG construction
- Batch transitions during execution
- Gap detection (first pass)

Human prompts are structured with specific questions, enumerated options, a recommended action, and the consequence of each option:

```yaml
decision:
  question: "Approve the executable specification for auth-redesign?"
  options:
    - id: "approve"
      label: "Approve plan and begin task breakdown"
      consequence: "Pipeline decomposes into ~6 tasks and begins execution"
      recommended: true
    - id: "reject"
      label: "Reject plan and provide new direction"
      consequence: "Pipeline returns to Stage 2 with your feedback"
      requires_input: true
    - id: "defer"
      label: "Defer decision"
      consequence: "Pipeline pauses. Reminder in 24 hours."
```

## 3.6 Pipeline Completion Output

### Happy path:
```
┌─ PIPELINE COMPLETE ─ xp-20260328-a1b2 ─ Total: 47m 12s ─────────────────┐
│                                                                           │
│  Feature: auth-redesign                                                   │
│  Branch: xpatcher/auth-redesign (pushed to remote)                        │
│  PR: https://github.com/org/repo/pull/42                                  │
│                                                                           │
│  Stage Breakdown:                                                         │
│    Planning ............  4:32    Execution ...........  28:15             │
│    Review ..............  6:44    Testing .............   3:22             │
│    Gap Detection .......  1:48    Documentation .......   2:31             │
│                                                                           │
│  Tasks: 12 completed, 0 failed                                            │
│  Iterations: 2.1 avg per task                                             │
│  Docs updated: README.md, docs/api/sessions.md (new)                      │
│  Warnings: 3 (see below)                                                  │
│                                                                           │
│  Artifacts:                                                               │
│    Plan: .xpatcher/auth-redesign/plan-v2.yaml                             │
│    Tasks: .xpatcher/auth-redesign/tasks/done/                             │
│    Reviews: .xpatcher/auth-redesign/tasks/done/*-review*                  │
│    Docs report: .xpatcher/auth-redesign/docs-report.yaml                  │
│    Gap report: .xpatcher/auth-redesign/gap-report-v1.yaml                 │
│    Logs: .xpatcher/auth-redesign/logs/                                    │
│                                                                           │
│  Warnings:                                                                │
│    task-003 required 4 review iterations (12m 18s)                        │
│    Simplifier found 3 opportunities not applied (dry)                     │
│    Gap detector noted 1 minor gap (deferred)                              │
│                                                                           │
└───────────────────────────────────────────────────────────────────────────┘
```

### Failure path:
```
┌─ PIPELINE BLOCKED ─ xp-20260328-a1b2 ─ Elapsed: 32m 05s ────────────────┐
│                                                                           │
│  Feature: auth-redesign                                                   │
│  Stage: Per-Task Quality (Stage 12) ─ stuck for 8m 44s                    │
│                                                                           │
│  Stuck tasks (3):                                                         │
│    task-005: Max iterations reached (review oscillation)  [12m 18s]       │
│    task-008: Tests failing - TypeError in session.py:42   [ 6m 22s]       │
│    task-009: Blocked by task-005 (dependency)              [waiting]       │
│                                                                           │
│  Completed tasks (9): all merged to feature branch                        │
│                                                                           │
│  What to do next:                                                         │
│    1. Review stuck tasks in .xpatcher/auth-redesign/                      │
│       tasks/todo/ (moved back from in-progress)                           │
│    2. Inspect agent logs: .xpatcher/auth-redesign/logs/                   │
│    3. Fix manually and run: xpatcher resume xp-20260328                   │
│    4. Or skip stuck tasks: xpatcher skip task-005,008                     │
│    5. Or cancel: xpatcher cancel xp-20260328-a1b2                         │
│                                                                           │
└───────────────────────────────────────────────────────────────────────────┘
```

Stuck tasks are moved back to `tasks/todo/` so the user can find and address them.

---

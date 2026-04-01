# Pipeline Stages

xpatcher uses a 16-stage pipeline with review loops, self-correction, and human gates.

## Stage Diagram

```
  [1] Intent --> [2] Spec Draft --> [3] Spec Review --> [4] Spec Fix ---+
                                        ^                               |
                                        +-------- loop (max 3) --------+
                                                    |
                                           [5] Spec Freeze (human gate)
                                                    |
  [6] Slice Breakdown --> [7] Slice Review --> [8] Slice Fix ----+
                                ^                                |
                                +-------- loop (max 3) ---------+
                                                  |
                           [9] Prioritization + Dependency Graph
                                                  |
                           [10] Execution Graph (DAG)
                                                  |
                    +-------------+-----------------+--------------+
                    v             v                 v              v
              [11] Task Execution (sequential)
                    |
              [12] Per-task quality loop: test --> review --> [simplify]
                    |
              [13] Fix iteration (max 3 per task)
                    |
              [14] Spec-to-Code Gap Detection
                    |
              gaps? --yes--> re-enter [6] (max 2 re-entries)
                    |
              [15] Documentation
                    |
              [16] Completion Summary (human gate)
```

## Stage Table

| # | Stage | Agent | Gate | Key Artifacts |
|---|-------|-------|------|---------------|
| 1 | Intent Capture | Planner | Auto | `intent.yaml` |
| 2 | Specification Draft | Planner | Auto | `plan-v1.yaml` |
| 3 | Specification Review | Plan Reviewer | Auto | `plan-review-v{N}.yaml` |
| 4 | Specification Fix | Planner | Auto | `plan-v{N}.yaml` |
| 5 | Specification Freeze | Dispatcher | **Human** | Decision record |
| 6 | Execution Slice Breakdown | Planner | Auto | `task-manifest.yaml`, `tasks/todo/*.yaml` |
| 7 | Execution Slice Review | Plan Reviewer | Auto | `task-review-v{N}.yaml` |
| 8 | Execution Slice Fix | Planner | Auto | Updated task YAMLs |
| 9 | Prioritization | Dispatcher | Auto | `execution-plan.yaml` |
| 10 | Execution Graph | Dispatcher | Auto | DAG + rollback tags |
| 11 | Task Execution | Executor | Auto | Commits on feature branch |
| 12 | Per-Task Quality | Dispatcher + Reviewer + Simplifier | Auto | Quality reports |
| 13 | Fix Iteration | Executor | Auto | Fix commits |
| 14 | Gap Detection | Gap Detector | Auto | `gap-report-v{N}.yaml` |
| 15 | Documentation | Technical Writer | Auto | Updated docs |
| 16 | Completion Summary | Dispatcher | **Human** | `completion.yaml` |

## Self-Correction Limits

| Loop | Max Iterations | On Limit |
|------|---------------|----------|
| Plan review/fix (3-4) | 3 | Escalate to human |
| Task review/fix (7-8) | 3 | Escalate to human |
| Per-task quality (12-13) | 3 | Mark task `stuck`, continue others |
| Gap re-entry (14) | 2 | Escalate to human |

Oscillation detection: if the same set of findings recurs, escalate immediately rather than burning iterations.

## Pipeline State Enum

`current_stage` values: `uninitialized`, `intent_capture`, `planning`, `plan_review`, `plan_fix`, `plan_approval`, `task_breakdown`, `task_review`, `task_fix`, `prioritization`, `execution_graph`, `task_execution`, `per_task_quality`, `fix_iteration`, `gap_detection`, `documentation`, `completion`, `done`, `paused`, `blocked`, `failed`, `cancelled`, `rolled_back`

## Per-Task State Enum

`TaskState` values: `pending`, `blocked`, `ready`, `running`, `succeeded`, `failed`, `needs_fix`, `stuck`, `skipped`, `cancelled`

## Full Specification

Historical design spec: [ephemeral/proposals/design/03-pipeline-flow.md](ephemeral/proposals/design/03-pipeline-flow.md) (may not match current implementation). See [architecture-snapshot.md](architecture-snapshot.md) for the implemented pipeline flow.

# Pipeline Artifacts

Each xpatcher pipeline run produces a set of YAML artifacts and logs under
`$XPATCHER_HOME/.xpatcher/projects/<project-hash>/<feature-slug>/`.

Agents write artifacts directly to this directory via the Write tool.
The dispatcher validates each artifact against its Pydantic schema, enriches
it with metadata (`created_at`, `schema_version`), and saves the final version.
Agent conversation logs are kept alongside as JSONL files.

## Directory layout

```
<feature-slug>/
  intent.yaml
  plan-v1.yaml  [plan-v2.yaml, ...]
  plan-review-v1.yaml  [plan-review-v2.yaml, ...]
  task-manifest.yaml
  task-manifest-v1.yaml  [task-manifest-v2.yaml, ...]
  task-review-v1.yaml  [task-review-v2.yaml, ...]
  execution-plan.yaml
  gap-report-v1.yaml  [gap-report-v2.yaml, ...]
  docs-report.yaml
  completion.yaml
  pipeline-state.yaml
  sessions.yaml
  decisions/
    decision-<timestamp>-plan-approval.yaml
  tasks/
    todo/       (empty after pipeline completes)
    in-progress/ (empty after pipeline completes)
    done/
      task-001-<slug>.yaml
      task-001-execution-log.yaml
      task-001-quality-report-v1.yaml
      task-001-review-v1.yaml
  logs/
    agent-planner-<timestamp>.jsonl
    agent-plan-reviewer-<timestamp>.jsonl
    agent-executor-task-001-<timestamp>.jsonl
    agent-reviewer-task-001-<timestamp>.jsonl
    agent-gap-detector-<timestamp>.jsonl
    agent-tech-writer-<timestamp>.jsonl
```

## Pipeline artifacts

| File | Stage | Schema | Description |
|------|-------|--------|-------------|
| `intent.yaml` | 1 - Intent Capture | `IntentOutput` | Goal, scope items, constraints, and clarifying questions distilled from the user's feature request. |
| `plan-v{N}.yaml` | 2 - Planning | `PlanOutput` | Phases, tasks, acceptance criteria, risks, and perspective analysis. Versioned when the plan-review loop requests changes. |
| `plan-review-v{N}.yaml` | 3 - Plan Review | `PlanReviewOutput` | Verdict (`approved` / `needs_changes` / `rejected`), confidence, and findings. One per review iteration. |
| `task-manifest.yaml` | 6 - Task Breakdown | `TaskManifestOutput` | Execution slices with `task-NNN` IDs, acceptance criteria (each with a runnable `command`), dependencies, and complexity. Also saved as `task-manifest-v{N}.yaml` for versioning. |
| `task-review-v{N}.yaml` | 7 - Task Review | `TaskManifestReviewOutput` | Verdict on the task manifest. Checks single-responsibility, green-state, verifiability, and anti-fragmentation. |
| `execution-plan.yaml` | 9 - Prioritization | (internal) | Topological execution order and DAG derived from task dependencies. |
| `tasks/done/{task_id}-{slug}.yaml` | 11 - Execution | (task definition) | The task definition file, moved through `todo/` -> `in-progress/` -> `done/` as the task progresses. |
| `tasks/done/{task_id}-execution-log.yaml` | 11 - Execution | `ExecutionOutput` | Status (`completed` / `blocked` / `deviated`), files changed, commit hashes, branch name, and push status. |
| `tasks/done/{task_id}-quality-report-v{N}.yaml` | 12 - Quality | `TestOutput` | Per-acceptance-criterion pass/fail results from automated command execution. |
| `tasks/done/{task_id}-review-v{N}.yaml` | 12 - Review | `ReviewOutput` | Code review verdict (`approve` / `request_changes` / `reject`), confidence, and findings. |
| `gap-report-v{N}.yaml` | 14 - Gap Detection | `GapOutput` | Verdict (`complete` / `gaps_found`) and gap list. Triggers re-entry if gaps are found. |
| `docs-report.yaml` | 15 - Documentation | `DocsReportOutput` | Documentation files updated, created, or skipped. |
| `completion.yaml` | 16 - Completion | (internal) | Final status, total cost, and completion timestamp. |

## Supporting files

| File | Purpose |
|------|---------|
| `pipeline-state.yaml` | State machine record: current stage, all transitions with timestamps, task states, iteration counters, loop history, branch metadata, and cost. |
| `sessions.yaml` | Claude CLI session registry. Tracks session IDs, turn counts, token estimates, and costs for session reuse across agent invocations. |
| `decisions/decision-{timestamp}-{type}.yaml` | Human gate decisions (plan approval, completion confirmation). Records whether auto-approved or human-approved, with the plan version and timestamp. |

## Logs

Each agent invocation produces a JSONL log file under `logs/`. These are
full Claude Code conversation logs containing every tool call, tool result,
thinking block, and assistant message from that invocation. File naming:

```
agent-{role}[-{task_id}]-{YYYYMMDD-HHMMSS}.jsonl
```

Multiple log files for the same role indicate session resumptions (the agent
was invoked multiple times across pipeline stages using the same session for
context continuity).

## Versioning

Artifacts that participate in review loops are versioned:

- **Plan**: `plan-v1.yaml` -> reviewer rejects -> `plan-v2.yaml` (up to `plan_review_max` iterations)
- **Task manifest**: `task-manifest-v1.yaml` -> reviewer rejects -> `task-manifest-v2.yaml`
- **Quality reports**: `task-001-quality-report-v1.yaml` -> fix iteration -> `task-001-quality-report-v2.yaml`
- **Reviews**: `task-001-review-v1.yaml` -> fix iteration -> `task-001-review-v2.yaml`
- **Gap reports**: `gap-report-v1.yaml` -> re-entry -> `gap-report-v2.yaml`

The unversioned `task-manifest.yaml` always reflects the latest approved version
and is the file read by the execution stages.

## Task lifecycle

Task definition files move through subdirectories as work progresses:

```
tasks/todo/task-001-add-feature.yaml      (created at task breakdown)
  -> tasks/in-progress/task-001-add-feature.yaml  (moved when executor starts)
    -> tasks/done/task-001-add-feature.yaml        (moved when quality loop ends)
```

The execution log and quality/review artifacts are written directly to
`tasks/done/` (or `tasks/in-progress/` during execution, then moved).

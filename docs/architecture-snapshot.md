# Architecture Snapshot

> Derived from the codebase as of 2026-04-01. This document reflects what is actually implemented, not what is planned.

## System Overview

xpatcher is a Python CLI that orchestrates Claude Code subagents to implement features from natural-language descriptions. The Python dispatcher owns the execution loop, state machine, and process lifecycle. Claude Code agents own reasoning, code generation, and review.

```
                 xpatcher CLI  (src/dispatcher/core.py main())
                        |
          +-------------+-------------+
          |                           |
   Dispatcher class             CLI commands
   (pipeline orchestration)     (status, list, cancel,
          |                      skip, pending, logs)
          |
   +------+------+------+------+------+
   |      |      |      |      |      |
 state  session schemas  tui  store  prompts
```

## Component Map

### Dispatcher (`src/dispatcher/core.py`)

The `Dispatcher` class is the main orchestrator. It:

- Runs the 16-stage pipeline sequentially via `_run_pipeline()`
- Invokes Claude Code agents via `ClaudeSession.invoke()` with `subprocess.run(["claude", "-p", ...])`
- Validates every agent output against Pydantic schemas before advancing
- Retries malformed output up to 2 times with targeted fix prompts (`MalformedOutputRecovery`)
- Tracks cost, writes JSONL agent logs, manages session reuse
- Handles human gates (plan approval, completion) with terminal prompts
- Supports cancellation via polling `pipeline-state.yaml` between stages

CLI entry point: `main()` -> `argparse` subcommands -> `Dispatcher.start()` / `.resume()` or standalone functions for status/list/cancel/skip/pending/logs.

### State Machine (`src/dispatcher/state.py`)

Two-level state tracking:

**Pipeline-level** (`PipelineStage` enum, 22 values):
- 16 stage values: `uninitialized` through `completion`
- 6 terminal/meta values: `done`, `paused`, `blocked`, `failed`, `cancelled`, `rolled_back`
- Transitions validated against `VALID_TRANSITIONS` dict; any non-terminal stage can also go to `cancelled`/`paused`/`failed`/`blocked`

**Per-task** (`TaskState` enum, 10 values):
- `pending`, `blocked`, `ready`, `running`, `succeeded`, `failed`, `needs_fix`, `stuck`, `skipped`, `cancelled`

**Persistence** (`PipelineStateFile`):
- Thread-safe atomic writes (write to temp file, `os.rename`)
- `threading.Lock` on all mutations
- Read-modify-write via `.update(**fields)`

**Task DAG** (`TaskDAG`):
- Built from task manifest `depends_on` fields via `TaskDAG.from_tasks()`
- Cycle detection, dependency validation, topological sort
- `mark_complete()` / `mark_skipped()` propagate state to dependents
- `get_ready_tasks()` returns tasks with all deps satisfied

### Session Management (`src/dispatcher/session.py`)

**`ClaudeSession`**:
- Wraps `claude -p` invocations with `--output-format json`, `--plugin-dir`, `--permission-mode bypassPermissions`
- Preflight check verifies CLI version, plugin loading, and required agents
- Discovers plugin name dynamically from the init event (not hardcoded)
- Supports cancellation: when `cancel_check` callback is provided, runs via `Popen` with polling loop (250ms interval) instead of `subprocess.run`
- Process termination: SIGTERM -> wait 5s -> SIGKILL, via process group

**`MalformedOutputRecovery`**:
- On schema validation failure, sends a fix prompt to the same session with error details
- Up to 2 fix attempts before giving up

**`SessionRegistry`**:
- Tracks sessions in `sessions.yaml` for reuse across stages
- `get_session_for_continuation()`: returns existing session ID if token estimate < 90% of context limit
- Context limit: 1M for `[1m]` agents, 200K otherwise

### Schemas (`src/dispatcher/schemas.py`)

Pydantic models for all artifact types. This is the single authoritative schema reference.

| Schema | Artifact Type | Used By |
|--------|--------------|---------|
| `IntentOutput` | `intent` | Stage 1 |
| `PlanOutput` | `plan` | Stage 2, 4 |
| `PlanReviewOutput` | `plan_review` | Stage 3 |
| `TaskManifestOutput` | `task_manifest` | Stage 6, 8 |
| `TaskManifestReviewOutput` | `task_manifest_review` | Stage 7 |
| `ExecutionOutput` | `execution_result` | Stage 11 |
| `ReviewOutput` | `review` | Stage 12 |
| `TestOutput` | `test_result` | Quality loop |
| `SimplificationOutput` | `simplification` | Quality loop (not wired) |
| `GapOutput` | `gap_report` | Stage 14 |
| `DocsReportOutput` | `docs_report` | Stage 15 |

**`ArtifactValidator`**:
- Three-stage validation: YAML extraction -> schema validation -> semantic checks
- Normalizes agent output quirks: `trivial`->`low`, numeric confidence->string, dict scope->list, list acceptance->normalized
- Semantic checks for `task_manifest`: every task must have at least one `must_pass` command-based acceptance criterion with a concrete command (rejects placeholders like `todo`, `tbd`, `n/a`)

### YAML Extraction (`src/dispatcher/yaml_utils.py`)

`extract_yaml()` tries 4 strategies in order:
1. Raw parse entire text
2. Parse content after `---` separator
3. Parse ` ```yaml ` code blocks
4. Strip leading prose, parse from first YAML key line

### Prompt Builder (`src/context/builder.py`)

`PromptBuilder` loads templates from `src/context/prompts.yaml` and renders them with `string.Template`. Per-stage methods: `build_intent_capture()`, `build_planner()`, `build_plan_reviewer()`, `build_plan_fix()`, `build_task_breakdown()`, `build_task_reviewer()`, `build_task_fix()`, `build_executor()`, `build_executor_fix()`, `build_tester()`, `build_reviewer()`, `build_gap_detector()`, `build_tech_writer()`.

Task file lookup searches `tasks/{todo,in-progress,done}/` and excludes execution-log/quality/review artifacts.

### Artifact Store (`src/artifacts/store.py`)

`ArtifactStore` manages reading/writing YAML artifacts under the feature directory. Supports:
- Versioned artifacts: `latest_version("plan")` finds highest `plan-v{N}.yaml`
- Decision records: timestamped files under `decisions/`
- Auto-adds `created_at` and `schema_version` fields
- Enum-safe serialization

### TUI (`src/dispatcher/tui.py`)

Simple ANSI-colored terminal output. No Rich live panels yet (just `print()` calls). Methods: `header()`, `stage()`, `status()`, `success()`, `error()`, `warning()`, `human_gate()` (with terminal bell), `cost_update()`, `cost_summary()`, `prompt_approval()`.

### Supporting Modules

- **`src/dispatcher/retry.py`**: Generic `retry_with_backoff()` with exponential delay. Not currently wired into the pipeline (agents use `MalformedOutputRecovery` instead).
- **`src/dispatcher/parallel.py`**: `AgentPool` with `execute_sequential()` and `execute_parallel()` (ThreadPoolExecutor). Not wired into core.py; tasks execute sequentially in `_run_prioritization_and_execution()`.
- **`src/context/diff.py`**: Git diff helpers: `get_staged_diff()`, `get_feature_diff()`, `get_recent_commits()`.
- **`src/context/memory.py`**: `SessionMemory` placeholder — simple key-value YAML store. Not wired into the pipeline.
- **`src/artifacts/collector.py`**: `ArtifactCollector` thin wrapper around `ArtifactValidator.validate()`. Not directly used; core.py calls validator directly.

## Pipeline Flow (as implemented)

```
start()
  |
  +--> preflight (verify claude CLI, plugin, agents)
  +--> git checkout -b xpatcher/<feature-slug>
  +--> initialize feature dir (tasks/{todo,in-progress,done}, logs, decisions)
  |
  _run_pipeline()
    |
    [1] intent_capture     -- planner agent -> IntentOutput -> intent.yaml
    [2] planning           -- planner agent -> PlanOutput -> plan-v1.yaml
    [3-4] plan review loop -- plan-reviewer agent -> PlanReviewOutput
    |     approved? continue. needs_changes? planner fixes, loop (max 3)
    |     limit reached? -> BLOCKED
    [5] plan_approval      -- human gate (if config or clarifying questions)
    |     auto-approve by default unless spec_confirmation=true
    [6] task_breakdown     -- planner agent -> TaskManifestOutput -> task-manifest.yaml
    |     materializes tasks/todo/task-NNN-<slug>.yaml files
    [7-8] task review loop -- plan-reviewer -> TaskManifestReviewOutput
    |     same loop structure as plan review (max 3)
    [9] prioritization     -- build DAG, topological sort -> execution-plan.yaml
    [10] execution_graph   -- (pass-through, no worktrees yet)
    [11-13] task execution -- for each task in topo order:
    |   [11] executor agent -> ExecutionOutput
    |   [12] acceptance checks (dispatcher runs commands via subprocess)
    |        if only missing commands: fall back to reviewer-only quality check
    |        if tests pass: reviewer agent -> ReviewOutput
    |        approve? -> SUCCEEDED
    |        request_changes? -> [13] executor fix, loop (max 3, oscillation detection)
    |   move task artifact: todo -> in-progress -> done
    [14] gap_detection     -- gap-detector agent -> GapOutput
    |     complete? continue
    |     gaps_found? -> re-enter [6-13] for gap tasks (max 2 re-entries)
    |     merge gap tasks into manifest, review, execute
    [15] documentation     -- tech-writer agent -> DocsReportOutput
    [16] completion        -- human gate (if completion_confirmation=true)
    |     auto-approve by default -> DONE
```

## Agent Invocation

All agents are invoked identically via `_invoke_agent()`:

1. Resolve model from config (`executor_default` for executor, snake_case agent name otherwise)
2. Resolve timeout from config
3. Check `SessionRegistry` for reusable session
4. Call `ClaudeSession.invoke()` with `AgentInvocation`
5. Accumulate cost, write JSONL log, register session
6. Return `AgentResult`

Validated invocations (`_invoke_validated_agent()`) add:
1. Call `ArtifactValidator.validate()` on raw text
2. On failure: send fix prompt to same session, retry up to 2 times

## Runtime Artifact Layout

```
$XPATCHER_HOME/.xpatcher/
  pipelines/<project-slug>.yaml          -- per-project pipeline index
  projects/<project-hash>/<feature>/
    pipeline-state.yaml                  -- mutable state singleton (atomic writes)
    sessions.yaml                        -- session registry
    intent.yaml                          -- Stage 1
    plan-v{N}.yaml                       -- Stage 2/4 (versioned)
    plan-review-v{N}.yaml                -- Stage 3
    task-manifest.yaml                   -- Stage 6 (latest version)
    task-manifest-v{N}.yaml              -- Stage 6/8 (versioned copies for N>1)
    task-review-v{N}.yaml                -- Stage 7
    execution-plan.yaml                  -- Stage 9 (DAG + topo order)
    gap-report-v{N}.yaml                 -- Stage 14
    docs-report.yaml                     -- Stage 15
    completion.yaml                      -- Stage 16
    decisions/
      decision-YYYYMMDD-HHMMSS-<type>.yaml
    tasks/
      todo/task-NNN-<slug>.yaml
      in-progress/task-NNN-<slug>.yaml
      in-progress/task-NNN-execution-log.yaml
      done/task-NNN-<slug>.yaml
      done/task-NNN-execution-log.yaml
      done/task-NNN-quality-report-v{N}.yaml
      done/task-NNN-review-v{N}.yaml
    logs/
      agent-<name>[-task-NNN]-YYYYMMDD-HHMMSS.jsonl
```

## What Is NOT Implemented

- **Parallel task execution**: `AgentPool.execute_parallel()` exists but is not wired. Tasks run sequentially.
- **Git worktrees**: No per-task branches or worktree isolation.
- **Simplifier agent**: Schema exists (`SimplificationOutput`), agent definition exists, but not invoked in the quality loop.
- **Tester agent**: Schema exists (`TestOutput`), but quality loop runs acceptance commands directly via `subprocess`; the tester agent is not invoked.
- **Rich TUI**: `TUIRenderer` uses plain `print()` with ANSI escapes. No Rich live panels or progress bars.
- **Cost budgets**: Cost is tracked and displayed but not enforced (no circuit breakers).
- **Context compaction**: Session registry tracks token estimates but does not trigger compaction.
- **Retry with backoff**: `retry_with_backoff()` exists but is not wired into agent invocations.
- **Session memory**: `SessionMemory` exists but is not used.
- **Artifact collector**: `ArtifactCollector` exists but core.py validates directly.
- **Full resume**: Resume only supports paused human gates (plan approval, completion). Mid-pipeline recovery requires manual intervention.

## Configuration

`config.yaml` at xpatcher home root. Loaded by `Dispatcher._load_config()`.

Key sections:
- `models`: per-agent model aliases (e.g., `planner: "opus[1m]"`, `executor_default: "sonnet"`)
- `iterations`: `plan_review_max`, `task_review_max`, `quality_loop_max`, `gap_reentry_max`
- `timeouts`: per-agent timeout in seconds
- `human_gates`: `spec_confirmation` (default false), `completion_confirmation` (default false)
- `quality_tiers`: `lite`, `standard`, `thorough` (defined but tier selection is in task specs, not enforced by dispatcher)
- `concurrency`: `max_parallel_agents` (defined but not yet enforced)

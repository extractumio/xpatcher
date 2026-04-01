# CLI, Installation, and Plugin Configuration

> Part of the [xpatcher Design Proposal](../xpatcher-design-proposal.md)

---

## 7.1 Dispatcher CLI Interface

The primary user interaction is through the `xpatcher` CLI command, not individual Claude Code skills. The dispatcher provides a colorful, interactive terminal interface.

### Commands

```bash
# Start a new pipeline (auto-detects project from cwd)
xpatcher start "Replace JWT auth with session-based auth"

# Start with explicit project path
xpatcher start "Add OAuth2 support" --project /path/to/myapp

# Start with verbose agent log streaming
xpatcher start "Add caching layer" --verbose
xpatcher start "Add caching layer" --stream-logs --log-lines 20

# Resume an interrupted pipeline
xpatcher resume <pipeline-id>

# Check status of current/specific pipeline
xpatcher status [pipeline-id]

# List all pipelines (active, paused, completed)
xpatcher list

# Cancel a pipeline
xpatcher cancel <pipeline-id>

# Skip stuck or failed tasks and continue the pipeline
# Dependents remain BLOCKED by default (safe); use --force-unblock to override
xpatcher skip <pipeline-id> <task-id>[,<task-id>...] [--force-unblock]

# Show all pipelines waiting for human input
xpatcher pending

# View agent logs for a pipeline
xpatcher logs <pipeline-id> [--agent executor] [--task task-003] [--tail 50]
```

The dispatcher keeps per-project pipeline index files under `$XPATCHER_HOME/.xpatcher/pipelines/`. Each file is keyed by the target project path slug and contains only that project's pipeline records, so `status`, `list`, `pending`, `logs`, `cancel`, and `skip` can resolve pipelines outside the current working directory without a single global registry file becoming a write hotspot.

By default, xpatcher runs in an automation-first SDD mode: if the executable specification is fully reviewed and does not contain unresolved clarifying questions, the dispatcher auto-freezes it and continues. Completion is also auto-finalized unless `human_gates.completion_confirmation` is enabled in `config.yaml`.

### `xpatcher skip` — Skip Tasks and Continue

Skips one or more stuck or failed tasks and resumes the pipeline. **By default, dependents of skipped tasks remain `BLOCKED`** (safe mode). Use `--force-unblock` to optimistically unblock dependents.

```bash
# Default: dependents stay BLOCKED
xpatcher skip xp-20260328-a1b2 task-005,task-008

# Explicit: unblock dependents (use when skipped task's output is not required)
xpatcher skip xp-20260328-a1b2 task-005,task-008 --force-unblock
```

**DAG semantics:**

1. Each skipped task's state transitions to `SKIPPED`.
2. For each dependent task of a skipped task:
   - **Default (blocking):** Dependents transition to `BLOCKED`. The user is informed which tasks are now blocked and can either retry the skipped task, manually resolve it, or use `--force-unblock`.
   - **With `--force-unblock`:** If the dependent has **no other unmet dependencies** (all deps are `SUCCEEDED` or `SKIPPED`), it transitions to `READY`. If other deps are still unmet, it remains `BLOCKED`.
3. The skip is recorded in `pipeline-state.yaml` with the task IDs, timestamp, reason, and whether `--force-unblock` was used.
4. After skip processing, the pipeline resumes from Stage 11 (Task Execution) for any newly ready tasks. If no tasks are ready and all remaining tasks are blocked/skipped, the pipeline advances to Stage 14 (Gap Detection).

**State transitions:**

| From | To | Trigger |
|------|----|---------|
| `STUCK` | `SKIPPED` | `xpatcher skip` |
| `FAILED` | `SKIPPED` | `xpatcher skip` |
| `PENDING`/`BLOCKED` (dep on skipped) | `BLOCKED` | Default: skipped deps block dependents |
| `BLOCKED` (by skipped task) | `READY` | `--force-unblock` and all remaining deps satisfied |

**Constraints:**
- Only tasks in `STUCK`, `FAILED`, or `BLOCKED` state can be skipped.
- Tasks in `RUNNING` or `SUCCEEDED` state cannot be skipped (cancel the pipeline instead).
- The gap detector (Stage 14) is informed of skipped tasks and includes them in its coverage analysis.
- Without `--force-unblock`, the CLI prints which dependent tasks are now blocked and suggests next actions.

**Pipeline state recording:**
```yaml
# Added to pipeline-state.yaml
skipped_tasks:
  - task_id: task-005
    skipped_at: "2026-03-28T16:22:00Z"
    previous_state: stuck
    reason: "User skip via CLI"
    force_unblock: false
    dependents_blocked: [task-009]
  - task_id: task-008
    skipped_at: "2026-03-28T16:22:00Z"
    previous_state: failed
    reason: "User skip via CLI"
    force_unblock: false
    dependents_blocked: []
```

### `xpatcher pending` — Show Pipelines Awaiting Human Input

Lists all pipelines that are currently blocked on a human gate (plan approval, escalation, completion review, or soft-timeout pause).

```bash
xpatcher pending
```

**Output:**
```
Pipelines awaiting human input:

  xp-20260328-a1b2  auth-redesign
    Gate: Plan Approval (Stage 5)
    Waiting since: 2026-03-28 14:35:00 (47m ago)
    Action: xpatcher resume xp-20260328-a1b2

  xp-20260329-c3d4  caching-layer
    Gate: Task Escalation (task-005 stuck, Stage 12)
    Waiting since: 2026-03-29 09:12:00 (2h 15m ago)
    Action: xpatcher resume xp-20260329-c3d4
           or: xpatcher skip xp-20260329-c3d4 task-005
```

The command scans all `.xpatcher/*/pipeline-state.yaml` files across known projects for `status: waiting_for_human` or `status: paused` with a gate reason. If no pipelines are pending, it prints "No pipelines awaiting human input."

### Global Flags

| Flag | Description |
|------|-------------|
| `--project <path>` | Target project directory (default: cwd) |
| `--verbose` | Show agent log streaming (last 8 lines) |
| `--stream-logs` | Show expanded agent log streaming (last 20 lines) |
| `--log-lines N` | Number of agent log lines to show (with --verbose) |
| `--quiet` | Minimal output (one-line status updates only) |
| `--config <path>` | Override config file (default: `~/xpatcher/config.yaml`) |

### Pipeline ID

Each pipeline gets a human-friendly ID: `xp-<YYYYMMDD>-<short-hash>` (e.g., `xp-20260328-a1b2`). This ID is:
- Displayed at pipeline start
- Used for resume/status commands
- Stored in `$XPATCHER_HOME/.xpatcher/projects/<project-slug-hash>/<feature>/pipeline-state.yaml`
- Indexed from `$XPATCHER_HOME/.xpatcher/pipelines/<project-slug-hash>.yaml`

### Interactive TUI and Transparent Output

The dispatcher uses the `rich` library to provide a real-time, transparent terminal interface. The output is designed so the user always knows: what happened, what is happening now, and how long each step has taken.

#### Color Coding

- **Blue**: Informational (stage transitions, progress)
- **Green**: Success (task completed, tests passed)
- **Yellow**: Warning (iteration cap approaching, review findings)
- **Red**: Error (task failed, pipeline blocked)
- **Magenta**: Human input required (approval prompts, questions)
- **Dim/Gray**: Agent log output (when streaming is enabled)

#### Live Progress Display

During pipeline execution, the TUI shows a persistent status panel at the top of the terminal:

```
┌─ xpatcher: xp-20260328-a1b2 ─ auth-redesign ─ 14m 22s elapsed ──────────┐
│                                                                           │
│  [✓] Intent Capture ............................  0:12  done              │
│  [✓] Planning ..................................  2:45  done              │
│  [✓] Plan Review ...............................  1:30  done (v2)         │
│  [✓] Plan Approval .............................  0:05  approved          │
│  [✓] Task Breakdown ............................  1:15  12 tasks          │
│  [✓] Task Review ...............................  0:48  approved          │
│  [✓] Prioritization ............................  0:03  3 batches         │
│  [▶] Parallel Execution ........................  7:44  running           │
│      ├── task-001 session-store [✓] ..........  3:12  done              │
│      ├── task-002 redis-adapter [✓] ..........  2:55  done              │
│      ├── task-003 api-endpoints [▶] ..........  1:37  executor running  │
│      ├── task-004 middleware    [·] ...........  ----  waiting (→003)    │
│      └── task-005 config       [·] ...........  ----  waiting (→001)    │
│  [·] Per-Task Quality ..........................  ----  pending           │
│  [·] Gap Detection .............................  ----  pending           │
│  [·] Documentation .............................  ----  pending           │
│  [·] Completion ................................  ----  pending           │
│                                                                           │
│  Agents active: 1/3  │  Tokens: ~125k  │  Tasks: 2/12 done              │
└───────────────────────────────────────────────────────────────────────────┘
```

Key elements:
- **Pipeline elapsed time** in the header, updated every second
- **Per-stage elapsed time** showing how long each stage took (or is taking)
- **Task-level detail** during parallel execution with individual task timers
- **Footer summary** with active agent count, token estimate, task completion ratio

#### Agent Log Streaming

When `--verbose` or `--stream-logs` is passed, the TUI includes a scrolling log pane below the progress panel showing the last N lines of agent output in real-time:

```
┌─ Agent Logs (task-003 executor) ──────────── press 'q' to hide ──────────┐
│  [14:07:32] Reading src/auth/endpoints.py ...                             │
│  [14:07:33] Found existing route pattern at line 45                       │
│  [14:07:35] Creating POST /api/v1/sessions endpoint                      │
│  [14:07:38] Writing src/auth/endpoints.py (lines 45-78 modified)          │
│  [14:07:40] Running: python -m pytest tests/auth/ -x                      │
│  [14:07:44] 3 passed, 0 failed                                            │
│  [14:07:45] Committing: xpatcher(task-003): Add session API endpoints     │
└───────────────────────────────────────────────────────────────────────────┘
```

Log streaming implementation:
- Claude Code's `--output-format stream-json` emits events as newline-delimited JSON
- The dispatcher reads agent stdout asynchronously via `asyncio.subprocess`
- Each event is parsed and displayed: tool calls, tool results, and text output
- Logs are always written to disk at `.xpatcher/<feature>/logs/agent-<name>-<task>-<timestamp>.jsonl` regardless of whether streaming is enabled in the TUI
- Default: last 8 lines visible; configurable via `--log-lines N`
- When multiple agents run in parallel, the TUI rotates focus between them (keyboard shortcut `Tab` to switch, or shows the most recently active agent)

#### Log File Format

All agent invocations produce structured log files:

```jsonl
{"ts":"2026-03-28T14:07:32Z","event":"tool_call","tool":"Read","input":{"file_path":"src/auth/endpoints.py"},"duration_ms":120}
{"ts":"2026-03-28T14:07:33Z","event":"text","content":"Found existing route pattern at line 45"}
{"ts":"2026-03-28T14:07:35Z","event":"tool_call","tool":"Edit","input":{"file_path":"src/auth/endpoints.py"},"duration_ms":450}
{"ts":"2026-03-28T14:07:40Z","event":"tool_call","tool":"Bash","input":{"command":"python -m pytest tests/auth/ -x"},"duration_ms":4200}
{"ts":"2026-03-28T14:07:44Z","event":"tool_result","tool":"Bash","exit_code":0,"summary":"3 passed, 0 failed"}
```

Log files serve three purposes:
1. **Post-hoc debugging**: when a task fails, inspect the full agent trace
2. **Audit trail**: what did each agent do, when, and for how long
3. **Performance tuning**: identify slow tool calls or excessive token usage

#### Verbosity Levels

| Flag | Progress Panel | Agent Logs | Log Files |
|------|---------------|------------|-----------|
| (default) | Yes, compact | Hidden | Written to disk |
| `--verbose` | Yes, expanded | Last 8 lines, auto-focused | Written to disk |
| `--stream-logs` | Yes, expanded | Last 20 lines, all agents | Written to disk |
| `--quiet` | One-line status | Hidden | Written to disk |
| `--log-lines N` | (with verbose) | Last N lines | Written to disk |

Log files are **always** written to disk regardless of verbosity. The TUI only controls what is shown in the terminal.

#### Accessing Logs After the Fact

```bash
# View full log for a specific agent invocation
cat .xpatcher/auth-redesign/logs/agent-executor-task-003-20260328-140700.jsonl

# Search across all logs for errors
grep '"event":"error"' .xpatcher/auth-redesign/logs/*.jsonl

# Show all tool calls made by the planner
grep '"event":"tool_call"' .xpatcher/auth-redesign/logs/agent-planner-*.jsonl | jq .

# Tail a running agent's log in real-time (from a second terminal)
tail -f .xpatcher/auth-redesign/logs/agent-executor-task-003-*.jsonl
```

#### Human Gate Prompts

When human input is needed, the TUI pauses the progress display and shows a structured prompt:

```
┌─ PLAN APPROVAL REQUIRED ─────────────────────────────────────────────────┐
│                                                                           │
│  Feature: auth-redesign                                                   │
│  Plan version: v2 (after 1 review iteration)                              │
│  Phases: 4  │  Tasks: 12  │  Est. complexity: medium                      │
│  Time in planning: 4m 32s                                                 │
│                                                                           │
│  Plan file: .xpatcher/auth-redesign/plan-v2.yaml                          │
│                                                                           │
│  [1] Approve and begin execution                                          │
│  [2] Request changes (opens editor for feedback)                          │
│  [3] Reject and restart planning                                          │
│  [4] View full plan details                                               │
│                                                                           │
└───────────────────────────────────────────────────────────────────────────┘
> Enter choice [1-4]:
```

All human prompts follow this structured format with numbered options.

## 7.2 Core Installation Directory Layout

This is the **core xpatcher installation** (e.g. `~/xpatcher/`). It is installed once and used across all projects. See Section 2.3.1 for the installation model.

```
~/xpatcher/                              # CORE INSTALLATION
+-- bin/
|   +-- xpatcher                         # CLI entry point (symlink or wrapper)
+-- .claude-plugin/
|   +-- plugin.json                      # Plugin manifest
|   +-- settings.json                    # Default settings
|   +-- .mcp.json                        # MCP server declarations
|   +-- agents/
|   |   +-- planner.md                   # Strategic planning agent
|   |   +-- executor.md                  # Code execution agent
|   |   +-- reviewer.md                  # Code review agent
|   |   +-- tester.md                    # Test generation/execution agent
|   |   +-- simplifier.md               # Code simplification agent
|   |   +-- gap-detector.md              # Gap/coverage analysis agent
|   |   +-- tech-writer.md              # Documentation update agent
|   |   +-- explorer.md                 # Read-only exploration agent
|   +-- skills/
|   |   +-- plan/
|   |   |   +-- SKILL.md                # /xpatcher:plan
|   |   +-- execute/
|   |   |   +-- SKILL.md                # /xpatcher:execute
|   |   +-- review/
|   |   |   +-- SKILL.md                # /xpatcher:review
|   |   +-- test/
|   |   |   +-- SKILL.md                # /xpatcher:test
|   |   +-- simplify/
|   |   |   +-- SKILL.md                # /xpatcher:simplify
|   |   +-- detect-gaps/
|   |   |   +-- SKILL.md                # /xpatcher:detect-gaps
|   |   +-- update-docs/
|   |   |   +-- SKILL.md                # /xpatcher:update-docs
|   |   +-- status/
|   |   |   +-- SKILL.md                # /xpatcher:status
|   |   +-- pipeline/
|   |       +-- SKILL.md                # /xpatcher:pipeline (full run)
|   +-- hooks/
|       +-- pre_tool_use.py              # Tool-call validation
|       +-- post_tool_use.py             # Logging and artifact capture
|       +-- lifecycle.py                 # Subagent start/stop tracking
+-- src/
|   +-- dispatcher/
|   |   +-- __init__.py
|   |   +-- core.py                      # Main dispatch loop
|   |   +-- session.py                   # Claude session management
|   |   +-- schemas.py                   # Pydantic models for structured output
|   |   +-- parallel.py                 # Subprocess pool for parallel agents
|   |   +-- state.py                     # Pipeline state machine
|   |   +-- retry.py                     # Error handling and retry logic
|   |   +-- tui.py                       # Terminal UI (progress, log streaming)
|   +-- context/
|   |   +-- __init__.py
|   |   +-- builder.py                   # Prompt/context assembly
|   |   +-- diff.py                      # Git diff context extraction
|   |   +-- memory.py                    # Cross-session memory interface
|   +-- artifacts/
|   |   +-- __init__.py
|   |   +-- collector.py                 # Gather outputs from agents
|   |   +-- store.py                     # Persist to project .xpatcher/
|   +-- mcp_servers/
|       +-- __init__.py
|       +-- xpatcher_server.py           # Custom MCP server (optional)
+-- config.yaml                          # Global defaults
+-- tests/
|   +-- test_dispatcher.py
|   +-- test_schemas.py
|   +-- test_pipeline.py
+-- pyproject.toml
```

### Project-Level Artifacts (created during pipeline runs)

```
<any-project>/
+-- .xpatcher/                           # PROJECT-SPECIFIC (auto-created)
|   +-- <feature>/                       # One per feature pipeline
|   |   +-- intent.yaml
|   |   +-- plan-v{N}.yaml
|   |   +-- pipeline-state.yaml
|   |   +-- sessions.yaml
|   |   +-- tasks/
|   |   |   +-- todo/
|   |   |   +-- in-progress/
|   |   |   +-- done/
|   |   +-- logs/
|   |   |   +-- agent-planner-20260328-143022.jsonl
|   |   |   +-- agent-executor-task-001-20260328-150100.jsonl
|   |   |   +-- ...
|   |   +-- decisions/
|   |   +-- debug/
+-- .xpatcher.yaml                       # Optional project-level config overrides
```

The `.xpatcher/` directory should be added to `.gitignore` (it contains transient pipeline state). The final code changes, documentation updates, and commits are on the feature branch itself. Commit message bodies reference `.xpatcher/` artifact paths as local informational pointers (for developers debugging locally), not as git-tracked content — see Section 2.6 for details.

## 7.3 plugin.json Manifest

```json
{
  "name": "xpatcher",
  "description": "SDD automation pipeline: specify, execute, review, verify, simplify",
  "version": "0.1.0",
  "author": "Extractum"
}
```

## 7.4 settings.json Defaults

```json
{
  "defaultAgent": "xpatcher:explorer",
  "preferences": {
    "xpatcher.pipeline.autoReview": true,
    "xpatcher.pipeline.autoTest": true,
    "xpatcher.pipeline.autoSimplify": false,
    "xpatcher.pipeline.maxRetries": 2,
    "xpatcher.pipeline.parallelAgents": 3
  },
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "*",
        "command": ".claude-plugin/hooks/run_hook.sh pre_tool_use.py"
      }
    ],
    "PostToolUse": [
      {
        "matcher": "*",
        "command": ".claude-plugin/hooks/run_hook.sh post_tool_use.py"
      }
    ]
  }
}
```

Setting the default agent to `explorer` (Haiku, read-only) means casual interactions are cheap and safe. Pipeline skills escalate to more powerful agents explicitly.

**Hook invocation:** Hook scripts use a thin wrapper (`run_hook.sh`) that activates the xpatcher venv, avoiding the bare `python` problem (may not exist on macOS, may be Python 2 on Linux). The wrapper is generated by the installer:

```bash
#!/usr/bin/env bash
# .claude-plugin/hooks/run_hook.sh — generated by install.sh
HOOK_DIR="$(cd "$(dirname "$0")" && pwd -P)"
XPATCHER_HOME="$(cd "$HOOK_DIR/../.." && pwd -P)"
exec "$XPATCHER_HOME/.venv/bin/python" "$HOOK_DIR/$1"
```

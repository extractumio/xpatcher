# System Architecture Overview

> Part of the [xpatcher Design Proposal](../xpatcher-design-proposal.md)

---

## 2.1 Component Diagram

```
+======================================================================+
|  CORE INSTALLATION  (~/xpatcher/ or server-wide)                     |
|                                                                      |
|  +------------------------------------------------------------------+|
|  | Claude Code Plugin  (.claude-plugin/)                            ||
|  |                                                                  ||
|  |  +----------------------------+ +------------------------------+ ||
|  |  | Skills (slash commands)    | | Agents (subagent defs)       | ||
|  |  |  /xpatcher:plan            | |  planner.md    (Opus[1m])    | ||
|  |  |  /xpatcher:execute         | |  executor.md   (Sonnet)      | ||
|  |  |  /xpatcher:review          | |  reviewer.md   (Opus)        | ||
|  |  |  /xpatcher:test            | |  tester.md     (Sonnet)      | ||
|  |  |  /xpatcher:simplify        | |  simplifier.md (Sonnet)      | ||
|  |  |  /xpatcher:detect-gaps     | |  gap-detector.md (Opus)      | ||
|  |  |  /xpatcher:update-docs     | |  tech-writer.md (Sonnet)     | ||
|  |  |  /xpatcher:status          | |  explorer.md   (Haiku)       | ||
|  |  |  /xpatcher:pipeline        | +------------------------------+ ||
|  |  +----------------------------+                                  ||
|  |                                                                  ||
|  |  +--------------------------------------------------------------+||
|  |  | Hooks                                                        |||
|  |  |  pre_tool_use.py   (enforce read-only, scope, safety)        |||
|  |  |  post_tool_use.py  (audit logging, artifact capture)         |||
|  |  |  lifecycle.py      (agent start/stop tracking)               |||
|  |  +--------------------------------------------------------------+||
|  +------------------------------------------------------------------+|
|                                                                      |
|  +------------------------------------------------------------------+|
|  | Python Dispatcher (src/dispatcher/)                              ||
|  |                                                                  ||
|  |  core.py         -- Main dispatch loop + TUI renderer            ||
|  |  state.py        -- Pipeline state machine with persistence      ||
|  |  session.py      -- Claude CLI invocation (claude -p)            ||
|  |  schemas.py      -- Pydantic models for structured output        ||
|  |  parallel.py     -- Thread pool for concurrent agents            ||
|  |  retry.py        -- Exponential backoff retry logic              ||
|  |  tui.py          -- Live terminal output (progress, logs)        ||
|  |                                                                  ||
|  |  context/                                                        ||
|  |    builder.py    -- Prompt assembly per agent stage               ||
|  |    diff.py       -- Git diff context extraction                  ||
|  |    memory.py     -- Cross-session memory interface               ||
|  |                                                                  ||
|  |  artifacts/                                                      ||
|  |    collector.py  -- Gather outputs from agents                   ||
|  |    store.py      -- Persist artifacts to $XPATCHER_HOME/.xpatcher/ ||
|  +------------------------------------------------------------------+|
+======================================================================+
         |
         | Dispatcher invoked with --project-dir <path>
         v
+======================================================================+
|  PROJECT RUNTIME ARTIFACTS  ($XPATCHER_HOME/.xpatcher/projects/...)  |
|  (Created outside the target repo during pipeline runs)              |
|                                                                      |
|  intent.yaml              plan-v1.yaml            state.yaml         |
|  task-manifest.yaml       execution-plan.yaml     gap-report.yaml    |
|  tasks/TASK-001.yaml      reviews/TASK-001-review.yaml               |
|  test-results/            logs/agent-*.jsonl       decisions/         |
+======================================================================+
```

## Constraint: Single Feature at a Time

xpatcher processes one feature at a time. This eliminates:
- Cross-feature merge conflicts
- Shared file contention between features
- Complexity in tracking multiple pipeline states

Independent tasks within a feature CAN run in parallel (via git worktrees), but only one feature pipeline is active. To start a new feature, the current one must complete, be paused, or be cancelled.

## 2.2 Python Thin Dispatcher: Design Rationale

The Python layer is a **thin orchestration shell**, not a fat orchestrator. Python owns:

- **Process management**: spawning `claude -p` subprocesses, monitoring PIDs, handling timeouts
- **State machine**: validated transitions, persistence to disk, crash recovery
- **DAG scheduling**: topological sort, critical path priority, semaphore-based concurrency
- **File I/O**: reading/writing YAML artifacts, git operations

Python does NOT own:

- Code generation, review, testing, or any reasoning -- that belongs to agent prompts
- Architectural decisions about the target codebase
- Heuristics about code quality

This avoids encoding domain logic in Python that duplicates or conflicts with what the LLM already knows.

**Why Python at all, not pure Claude Code?** Claude Code's headless mode is a subprocess invocation. You need something to manage subprocess lifecycle, handle timeouts, retry on failure, manage concurrency limits, and persist state between invocations. A pure skill-based approach works for simple linear workflows but breaks down for parallel execution, DAG scheduling, or crash recovery.

## 2.2.1 Model ID Reference

Agent definitions use **aliases** that auto-resolve to the latest model version. For production deployments, pin to full model IDs via `config.yaml`. Model alias resolution via `--model` has been **validated** against Claude Code CLI v2.1.87 (see Section 7.7.1).

| Alias | Full Model ID | Current Version | Validated |
|-------|--------------|-----------------|-----------|
| `opus` | `claude-opus-4-6` | Opus 4.6 | ✅ |
| `sonnet` | `claude-sonnet-4-6` | Sonnet 4.6 | ✅ |
| `haiku` | `claude-haiku-4-5-20251001` | Haiku 4.5 | ✅ (`haiku` -> `claude-haiku-4-5-20251001`) |
| `opus[1m]` | `claude-opus-4-6[1m]` | Opus 4.6 + 1M context | For large codebase analysis |
| `sonnet[1m]` | `claude-sonnet-4-6[1m]` | Sonnet 4.6 + 1M context | For long execution sessions |
| `opusplan` | (composite) | Opus in plan mode, Sonnet in execution | Hybrid alias |

## 2.3 File-Based Coordination

The alternatives considered were:

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| File polling | Simple, debuggable, crash-recoverable | Seconds of latency, disk I/O | **Selected** |
| WebSocket/IPC | Low latency, real-time | Complex, state lost on crash | Rejected |
| Message queue | Scalable, decoupled | External dependency, overkill | Rejected |
| Agent Teams | Native, integrated | Experimental, limited control | Deferred to v2 |

File polling wins because: (1) state files ARE the recovery mechanism -- restart reads `pipeline-state.yaml` and continues; (2) human inspection -- the runtime artifacts under `$XPATCHER_HOME/.xpatcher/` show exactly what is happening; (3) agents naturally produce file outputs.

Polling interval: 2 seconds for active tasks, 10 seconds for idle monitoring.

### 2.3.2 Pipeline State File Locking

`pipeline-state.yaml` is the single mutable singleton in the system. Even though v1 uses sequential execution, file locking and atomic writes are implemented from day one to prevent corruption from concurrent reads during status queries, signal handler writes during Ctrl+C, and to prepare for v2 parallel execution.

```python
import os
import tempfile
import threading
import yaml

class PipelineStateFile:
    """Thread-safe, atomic read/write for pipeline-state.yaml."""

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()

    def read(self) -> dict:
        """Read current state. Lock-free (reads are atomic on YAML-sized files)."""
        with open(self.path) as f:
            return yaml.safe_load(f) or {}

    def write(self, state: dict) -> None:
        """Atomic write: write to temp file, then os.rename (atomic on POSIX)."""
        with self._lock:
            dir_name = os.path.dirname(self.path)
            fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
            try:
                with os.fdopen(fd, "w") as f:
                    yaml.dump(state, f, default_flow_style=False)
                os.rename(tmp_path, self.path)  # Atomic on POSIX
            except Exception:
                os.unlink(tmp_path)
                raise

    def update(self, **fields) -> dict:
        """Read-modify-write with lock held."""
        with self._lock:
            state = self.read()
            state.update(fields)
            dir_name = os.path.dirname(self.path)
            fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
            try:
                with os.fdopen(fd, "w") as f:
                    yaml.dump(state, f, default_flow_style=False)
                os.rename(tmp_path, self.path)
            except Exception:
                os.unlink(tmp_path)
                raise
            return state
```

**Why implement in v1:** The atomic write pattern is ~15 lines and costs nothing. It prevents corruption from signal handlers (Ctrl+C during a write), status queries during updates, and any future parallelism. The threading.Lock is a no-op in sequential execution but becomes critical in v2.

## 2.3.1 Installation and Deployment Model

xpatcher separates **core installation** (code, agents, dispatcher) from **project artifacts** (plans, tasks, state). This allows a single installation to serve multiple projects.

### Installation Locations

| Installation Target | Path | Use Case |
|---|---|---|
| Per-user (recommended) | `~/xpatcher/` | Developer workstation, personal use |
| Server-wide | `/opt/xpatcher/` or `/usr/local/xpatcher/` | Shared CI/CD server, team use |
| Per-project (legacy) | `<project>/.claude-plugin/` | Self-contained, no global install |

### What Lives Where

**Core installation** (`~/xpatcher/` or configured path):
```
~/xpatcher/
  .claude-plugin/              # Agents, skills, hooks
    plugin.json
    agents/*.md
    skills/*/SKILL.md
    hooks/*.py
  src/                         # Python dispatcher code
    dispatcher/
    context/
    artifacts/
  config.yaml                  # Global defaults (models, concurrency, etc.)
  pyproject.toml
```

**Project runtime artifacts** (created during pipeline runs):
``` 
$XPATCHER_HOME/.xpatcher/projects/<project-slug-hash>/<feature>/
  intent.yaml
  plan-v{N}.yaml
  pipeline-state.yaml
  sessions.yaml
      tasks/
      logs/
      ...
  .xpatcher.yaml               # Optional: project-level overrides
```

The `.xpatcher.yaml` file in the project root allows per-project configuration overrides:

```yaml
# <project>/.xpatcher.yaml -- project-level overrides
models:
  executor_default: opus       # Override: use opus for all execution in this project
concurrency:
  max_parallel_agents: 2       # This project has limited API quota
gates:
  auto_approve_task_review: true  # Trust task reviews for this project
```

### Resolution Order

Configuration is resolved in priority order:
1. CLI flags (`--model opus`, `--concurrency 5`)
2. Project overrides (`<project>/.xpatcher.yaml`)
3. Global config (`~/xpatcher/config.yaml`)
4. Built-in defaults

### Running Against a Project

```bash
# From any directory -- specify the project
xpatcher start "Add OAuth2 support" --project /path/to/myapp

# From within the project directory (auto-detected)
cd /path/to/myapp
xpatcher start "Add OAuth2 support"

# Explicit core installation path (if not on PATH)
~/xpatcher/bin/xpatcher start "Add OAuth2 support"
```

The dispatcher resolves the core installation path via:
1. `XPATCHER_HOME` environment variable (if set)
2. `~/xpatcher/` (default user install)
3. The directory containing the `xpatcher` binary (self-relative)

## 2.4 Two-Level State Machine

xpatcher uses a two-level state machine:

1. **Pipeline-level stages** — the 16 stages through which a feature progresses. Defined in **Section 3** (Pipeline Flow), which is the single authoritative source for the stage diagram, stage specification table, transition table, and `current_stage` enum.

2. **Per-task states** — the lifecycle of an individual task within the DAG. Defined in **Section 2.5** below, alongside the DAG scheduler that drives transitions.

## 2.5 Task Dependency Graph (DAG)

Tasks are organized in a DAG. The dispatcher constructs the graph from `dependencies` fields on each task YAML, then validates:

1. **Cycle detection** via topological sort
2. **Orphan detection**: every task belongs to a feature; every feature has tasks
3. **Completeness check**: every referenced dependency exists

```python
class TaskDAG:
    def get_ready_tasks(self) -> list[TaskNode]:
        """Return tasks whose dependencies are all SUCCEEDED."""
        return [
            node for node in self.nodes.values()
            if node.state == TaskState.READY
        ]

    def mark_complete(self, task_id: str, success: bool):
        node = self.nodes[task_id]
        node.state = TaskState.SUCCEEDED if success else TaskState.FAILED
        if success:
            for dep_id in node.dependents:
                dep = self.nodes[dep_id]
                if all(self.nodes[d].state == TaskState.SUCCEEDED
                       for d in dep.dependencies):
                    dep.state = TaskState.READY

    def mark_skipped(self, task_id: str, force_unblock: bool = False):
        """Skip a task. Dependents remain BLOCKED unless force_unblock=True."""
        node = self.nodes[task_id]
        node.state = TaskState.SKIPPED
        for dep_id in node.dependents:
            dep = self.nodes[dep_id]
            if force_unblock:
                # Optimistic: treat SKIPPED as satisfied
                if all(self.nodes[d].state in (TaskState.SUCCEEDED, TaskState.SKIPPED)
                       for d in dep.dependencies):
                    dep.state = TaskState.READY
            else:
                # Default: SKIPPED deps block dependents
                dep.state = TaskState.BLOCKED
```

### Per-Task State Machine

Each task in the DAG has an independent lifecycle. Every transition is validated — invalid transitions (e.g., `PENDING` directly to `SUCCEEDED`) are rejected with an error. State is serialized to disk on every transition.

```
PENDING ──> BLOCKED ──> READY ──> RUNNING ──> SUCCEEDED
                │                    │            │
                v                    v            v
             SKIPPED              FAILED      NEEDS_FIX
                                     │            │
                                     v            v
                                  STUCK       RUNNING (re-enter quality loop)
                                     │
                                     v
                                  SKIPPED
```

```python
class TaskState(str, Enum):
    PENDING    = "pending"     # Waiting for dependencies
    BLOCKED    = "blocked"     # Dependency failed or human escalation
    READY      = "ready"       # All dependencies succeeded, eligible for scheduling
    RUNNING    = "running"     # Executor agent active
    SUCCEEDED  = "succeeded"   # Quality loop passed (v2: merged to feature branch)
    FAILED     = "failed"      # Execution or quality failed, retries exhausted
    NEEDS_FIX  = "needs_fix"   # Quality loop returned findings
    STUCK      = "stuck"       # Max quality iterations reached, escalated to human
    SKIPPED    = "skipped"     # User skipped via `xpatcher skip` (see Section 7.1)
    CANCELLED  = "cancelled"   # Pipeline cancelled via `xpatcher cancel`
```

| From | To | Trigger |
|------|----|---------|
| `PENDING` | `BLOCKED` | A dependency entered `FAILED`, `STUCK`, or `SKIPPED` |
| `PENDING` | `READY` | All dependencies `SUCCEEDED` (note: `SKIPPED` deps **block** by default — use `--force-unblock` to override) |
| `BLOCKED` | `READY` | Blocking dependency resolved (human intervention, skip, or retry) |
| `READY` | `RUNNING` | Scheduler picks task, executor agent starts |
| `RUNNING` | `SUCCEEDED` | Quality loop passes (v2: merge to feature branch succeeds) |
| `RUNNING` | `NEEDS_FIX` | Quality loop returns findings |
| `RUNNING` | `FAILED` | Execution error or v2 integration test failure after merge |
| `NEEDS_FIX` | `RUNNING` | Executor commits fix, re-enters quality loop |
| `NEEDS_FIX` | `STUCK` | Max quality iterations reached (default: 3) |
| `STUCK` | `READY` | Human resolves and requests retry |
| `STUCK` | `SKIPPED` | `xpatcher skip` (see Section 7.1) |
| `FAILED` | `SKIPPED` | `xpatcher skip` (see Section 7.1) |
| `FAILED` | `READY` | Human resolves and requests retry |
| `FAILED` | `READY` | Human resolves and requests retry |

Concurrency is semaphore-based. Default: 3 parallel agents (bound by API rate limits, not CPU).

**v1:** Tasks execute sequentially on the feature branch in DAG order — no worktrees, no per-task branches. The DAG determines execution order within and across batches. See Section 2.6.1.

**v2:** Each parallel task runs in its own **git worktree** for file isolation, with a defined merge protocol for rejoining the feature branch. See Section 2.6.1.

```bash
# v2 only: worktree creation per task
git worktree add .xpatcher/worktrees/TASK-003 -b xpatcher/feature-auth/TASK-003
```

**Critical path optimization**: tasks on the longest dependency chain are prioritized for scheduling, use Opus instead of Sonnet, and get fast-tracked reviews.

## 2.6 Git Branching Strategy

xpatcher uses a single-branch-per-feature model:

1. **Feature branch**: Created from `main` (or `master`, auto-detected) at pipeline start:
   ```
   git checkout -b xpatcher/<feature-slug>
   ```

2. **Atomic task commits**: Each task produces commits on the feature branch with structured messages:
   ```
   xpatcher(task-001): Add session store interface

   Plan: .xpatcher/auth-redesign/plan-v2.yaml (phase-1)
   Task: .xpatcher/auth-redesign/tasks/done/task-001-session-store.yaml
   Acceptance: All criteria passed (3/3 must_pass, 1/1 should_pass)
   ```

   > **Note:** Commit message bodies reference `.xpatcher/` artifact paths as **local informational pointers** for developers debugging the pipeline on their machine. These paths are not tracked in git (`.xpatcher/` is gitignored per Section 7.2) and will not resolve when viewing commits on a remote (e.g., in a GitHub PR). This is intentional — the artifacts are transient pipeline state, not source code. The commit message subject line (`xpatcher(task-NNN): ...`) stands alone as a meaningful commit description.

3. **Review and testing**: All review, testing, and simplification happen on the feature branch.

4. **Completion**: On pipeline success, the dispatcher:
   - Pushes the feature branch to remote
   - Optionally creates a PR (if `gh` CLI is available)
   - Reports the branch name, PR URL, and artifact summary

5. **No direct merges to main**: xpatcher never merges to main/master. That is always a human action via PR review.

### 2.6.1 Task Execution and Merge Strategy

#### v1: Sequential Execution (No Worktrees)

In v1, tasks execute **sequentially on the feature branch**. There are no worktrees, no per-task branches, and no merge step. This eliminates merge conflicts, concurrent state file access, and worktree lifecycle management entirely.

```
feature branch: ──A──B──C──D──  (tasks commit directly, in dependency order)
```

The DAG and batch concepts from Section 2.5 still apply — they determine **execution order**, not parallelism. Tasks within a batch run one at a time; the batch boundary ensures all dependencies are satisfied before the next group begins.

**Why sequential for v1:** The merge protocol, conflict resolution, integration testing, worktree cleanup, and concurrent `pipeline-state.yaml` access (MAJ-5) represent significant implementation complexity. Sequential execution eliminates all of these while preserving the pipeline's core value: automated plan → task → quality loop → gap detection. Parallel execution is additive — the architecture supports it without a rewrite.

#### v2: Parallel Execution with Worktree Merge Protocol

In v2, tasks within a batch run concurrently in isolated git worktrees on per-task branches (as described in Section 2.5). The merge protocol below governs how task branches rejoin the feature branch.

**Merge method: `--no-ff` merge commits.** Merge commits preserve branch topology (clear "task-003 was merged here" markers), provide atomic revert per task via `git revert -m1 <merge>`, and maintain the strongest audit trail linking task branches to the feature branch. Rebase produces linear history but loses branch provenance and complicates atomic rollback. Cherry-pick creates orphaned commits and the highest tracking complexity.

**Merge timing: Eager (per-task, on quality pass).** Each task merges to the feature branch immediately after passing its per-task quality loop (Stages 12-13). This catches pairwise integration breakage at the smallest possible scope. Later tasks in the same batch do NOT see earlier merges (their worktrees are snapshots), but the next batch starts from the fully-merged feature branch.

**Conflict resolution: Tiered escalation.**

| Tier | Handles | Latency | Action |
|------|---------|---------|--------|
| 1. Git auto-resolve | Textual non-overlapping changes | Zero | Proceed |
| 2. Agent-assisted | Semantic conflicts (e.g., duplicate imports) | 1-2 min | One agent attempt with full context of both tasks |
| 3. Human escalation | Unresolvable or agent-failed | Blocks pipeline | Mark task `BLOCKED`, notify human |

Most merges will be textually clean because `file_scope` assignment (Section 2.5) prevents task overlap. The tiered approach handles the rare exceptions without blocking the pipeline unnecessarily.

**Full merge protocol:**

```
task completes quality loop (Stages 12-13)
  │
  ├─ acquire feature-branch merge lock (threading.Lock)
  ├─ git checkout xpatcher/<feature-slug>
  ├─ git merge --no-ff xpatcher/<feature-slug>/TASK-NNN
  │    ├─ clean merge? → continue
  │    └─ conflict?
  │         ├─ git auto-resolvable? → resolve, continue
  │         ├─ agent attempt (1 shot) → resolved? → continue
  │         └─ mark task BLOCKED, release lock, notify human
  ├─ run integration tests on feature branch
  │    ├─ pass? → delete task branch + worktree, release lock
  │    └─ fail? → git revert -m1 HEAD, mark task FAILED, release lock
  └─ next task in queue may now acquire lock
```

**Key properties:**
- The **merge lock** serializes merges even though execution is parallel. This eliminates race conditions on `pipeline-state.yaml` at the merge point.
- **Integration tests** run on the feature branch after each merge, catching breakage between tasks that passed their individual quality loops.
- **Failed integration** triggers an atomic revert (`git revert -m1`) so the feature branch remains clean for subsequent merges.
- **Worktree cleanup**: on successful merge, both the task branch and worktree are deleted. On failure or block, the worktree is preserved for debugging until the pipeline completes or the human intervenes.

## 2.7 Pipeline Resumption

Pipelines can be interrupted (Ctrl+C, terminal close, crash) and resumed:

```bash
xpatcher resume xp-20260328-a1b2
```

On resume, the dispatcher:
1. Reads `.xpatcher/<feature>/pipeline-state.yaml` to determine current stage
2. Reads `.xpatcher/<feature>/sessions.yaml` to find reusable Claude sessions
3. Checks if the base branch has changed since pause (git log comparison)
4. If base changed: rebases feature branch, re-runs affected tests
5. If base unchanged: continues from the exact point of interruption
6. Reports what was completed and what remains

**Session strategy on resume:** Non-review stages (planner, executor, tester) resume their prior session if context is still valid (< 4 hours, < 80% context window). Review stages (plan reviewer, task reviewer, gap detector) always start fresh sessions with context bridges to maintain adversarial isolation. See Section 7.8 for full session management rules.

The pipeline ID is displayed prominently at start:
```
Pipeline started: xp-20260328-a1b2
   Feature: auth-redesign
   Branch: xpatcher/auth-redesign
   To resume later: xpatcher resume xp-20260328-a1b2
```

---

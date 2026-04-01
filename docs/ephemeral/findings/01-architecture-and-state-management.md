# Architecture & State Management Analysis

**Date**: 2026-03-28
**Status**: Draft for review
**Scope**: System architecture, state management, task graphs, agent specialization, and plugin packaging for an SDLC automation pipeline built on Claude Code.

---

## 1. Overall System Architecture

### 1.1 Proposed Approach: Python Dispatcher as Thin Orchestration Shell

The central design decision is the relationship between the Python dispatcher and Claude Code agents. We recommend a **thin orchestration shell** pattern: Python owns the execution loop, state machine transitions, and process lifecycle management, while Claude Code agents own all reasoning, code generation, and decision-making.

```
+------------------------------------------------------------------+
|  Claude Code Plugin  (.claude-plugin/)                           |
|  +--------------------------+  +-----------------------------+   |
|  | Skills (slash commands)  |  | Agents (subagent defs)      |   |
|  |  /sdlc-plan              |  |  planner.md                 |   |
|  |  /sdlc-run               |  |  executor.md                |   |
|  |  /sdlc-status            |  |  reviewer.md                |   |
|  |  /sdlc-resume            |  |  tester.md                  |   |
|  +--------------------------+  |  simplifier.md              |   |
|                                +-----------------------------+   |
|  +-----------------------------------------------------------+   |
|  | Python Dispatcher (sdlc_dispatch.py)                       |   |
|  |  - State machine engine                                    |   |
|  |  - DAG scheduler                                           |   |
|  |  - Process manager (claude -p invocations)                 |   |
|  |  - State persistence (YAML read/write)                     |   |
|  |  - Git operations (commit, branch, worktree management)    |   |
|  +-----------------------------------------------------------+   |
|                                                                   |
|  +-----------------------------------------------------------+   |
|  | State Layer  (./sdlc/)                                     |   |
|  |  plan.yaml, tasks/*.yaml, reviews/*.yaml, state.yaml      |   |
|  +-----------------------------------------------------------+   |
+------------------------------------------------------------------+
```

**Why thin shell, not fat orchestrator**: The Python layer should not contain domain logic about how to write code, review code, or make architectural decisions. That belongs in agent prompts. Python handles what it's good at: process management, file I/O, scheduling, and deterministic state transitions. This avoids the trap of encoding increasingly complex heuristics in Python that duplicate or conflict with what the LLM already knows.

**Why Python at all, not pure Claude Code**: Claude Code's headless mode (`claude -p`) is a subprocess invocation. You need something to manage the lifecycle of those subprocesses, handle timeouts, retry on failure, manage concurrency limits, and persist state between invocations. A pure skill-based approach would work for simple linear workflows but breaks down when you need parallel execution, DAG scheduling, or crash recovery. Python is the right tool for that plumbing.

### 1.2 Trade-off: Event-Driven vs Polling-Based Coordination

**Recommendation: File-based event loop with polling.**

The alternatives are:

| Approach | Pros | Cons |
|----------|------|------|
| **File polling** | Simple, debuggable, survives crashes, no infrastructure | Latency (seconds), disk I/O |
| **WebSocket/IPC** | Low latency, real-time | Complex, state lost on crash, needs server process |
| **Message queue (Redis/NATS)** | Scalable, decoupled | External dependency, overkill for single-machine |
| **Claude Code Agent Teams** | Native, integrated | Experimental, limited control over scheduling |

File polling wins because:
1. State files are the recovery mechanism. If the dispatcher crashes and restarts, it reads `./sdlc/state.yaml` and picks up where it left off. No reconstruction needed.
2. Human inspection. A developer can `cat ./sdlc/state.yaml` to understand exactly what the system is doing. This is critical for trust and debugging.
3. Claude Code agents naturally produce file outputs. Asking them to write a task result to `./sdlc/tasks/TASK-003-result.yaml` is trivial.

The polling interval should be 2 seconds for active tasks, 10 seconds for idle monitoring. This is fast enough for an SDLC pipeline where individual tasks take minutes.

### 1.3 State Machine Design

The pipeline state machine has two levels: a **pipeline-level** state machine and per-**task** state machines.

**Pipeline states:**

```
UNINITIALIZED
    |
    v
PLANNING ---------> PLAN_REVIEW (human approves plan)
    |                    |
    v                    v
EXECUTING <-------- APPROVED
    |
    v
REVIEWING --------> REVIEW_COMPLETE
    |                    |
    v                    v
TESTING  <--------- CHANGES_REQUESTED (loop back)
    |
    v
SIMPLIFYING -------> COMPLETE
    |
    v
FINALIZING --------> DONE
```

**Task states:**

```
PENDING --> BLOCKED --> READY --> RUNNING --> SUCCEEDED
                                    |            |
                                    v            v
                                 FAILED    NEEDS_REVIEW
                                    |            |
                                    v            v
                                 RETRYING   REVISED --> RUNNING
```

The state machine is explicit and serialized. Every transition is logged. The dispatcher's main loop is:

```python
while pipeline.state != PipelineState.DONE:
    match pipeline.state:
        case PipelineState.EXECUTING:
            ready_tasks = dag.get_ready_tasks()
            for task in ready_tasks[:concurrency_limit]:
                launch_agent(task)
            completed = poll_running_agents(timeout=2)
            for result in completed:
                dag.mark_complete(result.task_id, result.status)
                persist_state()
        case PipelineState.PLAN_REVIEW:
            if human_approved():
                pipeline.transition(PipelineState.EXECUTING)
            # Otherwise, keep polling (human is thinking)
        # ... other states
```

### 1.4 Monolithic vs Microservice-like Agent Decomposition

**Recommendation: Specialized agents with narrow scopes.**

A single "do everything" agent works for small tasks but fails for pipeline-scale work because:
- Context windows fill up. A planning agent's context should not be polluted with test output details.
- Permissions differ. A planner should not have shell access. A tester needs it.
- Model economics. Planning needs Opus. Linting can use Haiku.
- Failure isolation. A crashed executor should not take down the review cycle.

The decomposition follows the SDLC phases, not arbitrary microservice boundaries. Each agent maps to a real development activity that a human developer would recognize. Details in Section 4.

---

## 2. State Management & Persistence

### 2.1 Proposed Approach: `./sdlc/` Folder with YAML Files

All pipeline state lives in a `./sdlc/` directory at the project root. YAML over JSON because it supports comments (useful for human annotation during review pauses) and is more readable for the multi-line text fields that dominate SDLC artifacts.

**Directory structure:**

```
./sdlc/
  state.yaml                  # Pipeline-level state machine
  plan.yaml                   # Decomposed feature plan
  config.yaml                 # Pipeline configuration overrides
  tasks/
    TASK-001.yaml             # Individual task definitions + status
    TASK-002.yaml
    TASK-003.yaml
  reviews/
    TASK-001-review.yaml      # Code review results per task
    TASK-002-review.yaml
  test-results/
    TASK-001-tests.yaml       # Test execution results
  logs/
    TASK-001-executor.log     # Raw agent output per task
    TASK-001-reviewer.log
  progress.txt                # Free-form progress notes (Anthropic pattern)
  .gitignore                  # Ignore logs/, keep everything else
```

**Why not a database**: SQLite or similar would work, but files are superior here because:
1. Git-trackable. The plan and task states can be committed, allowing rollback to previous pipeline states via `git checkout`.
2. Agent-readable. Claude Code agents can be told "read `./sdlc/tasks/TASK-003.yaml`" and understand the full context.
3. Merge-friendly. If two agents update different task files simultaneously, there is no conflict.
4. Human-editable. A developer can pause the pipeline, edit a task YAML to change requirements, and resume.

### 2.2 Schema Definitions

**`state.yaml` -- Pipeline State:**

```yaml
# state.yaml
version: 1
pipeline_id: "sdlc-20260328-143022"
created_at: "2026-03-28T14:30:22Z"
updated_at: "2026-03-28T15:42:11Z"

state: EXECUTING          # Current pipeline state
previous_state: APPROVED  # For transition logging

source:
  repo: "."
  branch: "sdlc/feature-auth-module"
  base_branch: "main"
  initial_commit: "a1b2c3d"

concurrency:
  max_parallel_tasks: 3
  active_agents: 2

counters:
  tasks_total: 12
  tasks_completed: 5
  tasks_failed: 0
  tasks_running: 2
  tasks_pending: 5

human_review:
  required_at: [PLAN_REVIEW, REVIEW_COMPLETE]
  last_prompt: "2026-03-28T15:30:00Z"
  last_response: "2026-03-28T15:35:00Z"
```

**`plan.yaml` -- Feature Decomposition Plan:**

```yaml
# plan.yaml
version: 1
objective: "Implement user authentication module with OAuth2 support"
generated_by: planner
generated_at: "2026-03-28T14:31:05Z"
approved_by: human
approved_at: "2026-03-28T14:45:00Z"

# JSON-compatible feature list (Anthropic's recommendation to prevent
# agents from treating it as prose and overwriting it)
features:
  - id: "FEAT-001"
    name: "Database schema for users and sessions"
    description: "Create migration files for user, session, and oauth_token tables"
    estimated_complexity: low
    tasks: ["TASK-001", "TASK-002"]

  - id: "FEAT-002"
    name: "Authentication service layer"
    description: "Implement login, logout, token refresh, and OAuth2 flows"
    estimated_complexity: high
    tasks: ["TASK-003", "TASK-004", "TASK-005", "TASK-006"]

  - id: "FEAT-003"
    name: "API endpoints and middleware"
    description: "REST endpoints for auth operations and JWT middleware"
    estimated_complexity: medium
    tasks: ["TASK-007", "TASK-008", "TASK-009"]

architecture_decisions:
  - decision: "Use bcrypt for password hashing, not argon2"
    rationale: "Wider library support, sufficient for this use case"
  - decision: "JWT with short-lived access tokens + refresh token rotation"
    rationale: "Standard OAuth2 pattern, well-understood security properties"

constraints:
  - "All new code must have >80% test coverage"
  - "No new dependencies without explicit approval in task definition"
  - "Database migrations must be reversible"
```

**`tasks/TASK-001.yaml` -- Individual Task:**

```yaml
# tasks/TASK-001.yaml
version: 1
id: "TASK-001"
feature: "FEAT-001"
title: "Create user table migration"
description: |
  Create a database migration that adds the `users` table with columns:
  id (UUID, PK), email (unique, indexed), password_hash, display_name,
  created_at, updated_at, deleted_at (soft delete).

state: SUCCEEDED
created_at: "2026-03-28T14:31:05Z"
started_at: "2026-03-28T14:46:12Z"
completed_at: "2026-03-28T14:52:38Z"

dependencies: []     # No dependencies -- can run immediately
dependents: ["TASK-003", "TASK-007"]  # These are blocked until this completes

execution:
  agent: executor
  model: claude-sonnet-4-20250514
  session_id: "sess_abc123"
  attempts: 1
  max_attempts: 3

result:
  status: succeeded
  files_changed:
    - "migrations/001_create_users_table.sql"
    - "migrations/001_create_users_table_down.sql"
  commit: "d4e5f6a"
  summary: "Created reversible migration for users table with all specified columns and indexes."

review:
  status: approved
  reviewer_agent: reviewer
  comments: []
  reviewed_at: "2026-03-28T14:55:02Z"
```

**`reviews/TASK-001-review.yaml` -- Code Review:**

```yaml
# reviews/TASK-001-review.yaml
version: 1
task_id: "TASK-001"
reviewer: reviewer
model: claude-opus-4-20250514
reviewed_at: "2026-03-28T14:55:02Z"

verdict: approved  # approved | changes_requested | rejected

summary: "Migration is correct and reversible. Index on email column is present."

findings: []
# Example of a finding when changes are requested:
# findings:
#   - severity: error       # error | warning | suggestion
#     file: "migrations/001_create_users_table.sql"
#     line: 12
#     message: "Missing NOT NULL constraint on email column"
#     suggested_fix: "ALTER COLUMN email SET NOT NULL"

checklist:
  compiles: true
  tests_pass: true
  follows_plan: true
  no_unnecessary_changes: true
  security_reviewed: true
```

### 2.3 State Recovery and Resumption

The system must handle three interruption scenarios:

**Scenario 1: Human-in-the-loop pause.** The pipeline enters `PLAN_REVIEW` or `REVIEW_COMPLETE` and waits. The developer closes their terminal and comes back hours later. Recovery: the dispatcher reads `state.yaml`, sees the pipeline state, and resumes polling for human input. No data loss because nothing was in-flight.

**Scenario 2: Dispatcher crash during task execution.** An agent subprocess is running when the dispatcher process dies. Recovery strategy:

1. On restart, read `state.yaml` to get pipeline state.
2. Scan `tasks/*.yaml` for tasks with `state: RUNNING`.
3. For each running task, check if the `claude` subprocess is still alive (PID stored in task YAML).
4. If alive, re-attach by polling its output.
5. If dead, check if the task's expected output files exist (commit was made). If yes, mark succeeded. If no, mark failed and schedule retry.

This requires storing the PID in the task file:

```yaml
execution:
  pid: 48291
  started_at: "2026-03-28T14:46:12Z"
  heartbeat_file: "./sdlc/logs/TASK-001.heartbeat"
```

The heartbeat file is touched every 30 seconds by a wrapper around the `claude -p` invocation. If the file's mtime is older than 90 seconds, the agent is assumed dead.

**Scenario 3: Agent timeout.** A task exceeds its time budget. The dispatcher sends SIGTERM to the agent process, waits 10 seconds, then SIGKILL. The task is marked `FAILED` with reason `timeout`. The retry logic increments the attempt counter and re-launches if under `max_attempts`.

### 2.4 Partial Completions and Rollbacks

Each task operates on its own git branch (or worktree, if parallelism requires it). The branch naming convention is `sdlc/{pipeline_id}/{task_id}`:

```
main
  \-- sdlc/feature-auth/TASK-001  (merged after review)
  \-- sdlc/feature-auth/TASK-002  (merged after review)
  \-- sdlc/feature-auth/TASK-003  (in progress)
```

**Rollback of a single task**: `git branch -D sdlc/feature-auth/TASK-003` and reset the task state to PENDING. No other tasks are affected because they are on separate branches.

**Rollback of the entire pipeline**: The `state.yaml` records `initial_commit`. Reset to it: `git reset --hard {initial_commit}`. All SDLC branch artifacts are deleted. The `./sdlc/` folder can be preserved (it records what happened) or deleted (clean slate).

**Partial completion**: If an agent produced 3 of 5 expected files before crashing, the retry launches a fresh agent with the task definition. The fresh agent sees the partial work (it is on the branch) and can decide to continue or start over. The task definition should include a note:

```yaml
execution:
  attempts: 2
  previous_attempt_notes: |
    Attempt 1 timed out after producing migrations/001 and migrations/002.
    Files are on branch. Continue from where attempt 1 left off if possible.
```

---

## 3. Task Dependency Graph & Execution

### 3.1 DAG Construction

The planner agent produces `plan.yaml` with a flat list of features and tasks. The dispatcher constructs a DAG from the `dependencies` field on each task. The DAG is validated at construction time:

1. **Cycle detection**: Topological sort. If it fails, reject the plan and ask the planner to fix circular dependencies.
2. **Orphan detection**: Every task must belong to a feature. Every feature must have at least one task.
3. **Completeness check**: Every task referenced in a `dependencies` array must exist in the plan.

The DAG is stored implicitly in the task YAML files (each task lists its own dependencies). The dispatcher reconstructs it on startup by scanning `tasks/*.yaml`. This is intentional -- the task files are the source of truth, not a separate graph file that could drift.

**Python implementation sketch:**

```python
from dataclasses import dataclass, field
from enum import Enum

class TaskState(Enum):
    PENDING = "pending"
    BLOCKED = "blocked"
    READY = "ready"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"

@dataclass
class TaskNode:
    id: str
    dependencies: list[str] = field(default_factory=list)
    dependents: list[str] = field(default_factory=list)
    state: TaskState = TaskState.PENDING

class TaskDAG:
    def __init__(self, tasks: list[TaskNode]):
        self.nodes = {t.id: t for t in tasks}
        self._validate()
        self._compute_initial_states()

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
                if all(
                    self.nodes[d].state == TaskState.SUCCEEDED
                    for d in dep.dependencies
                ):
                    dep.state = TaskState.READY

    def _compute_initial_states(self):
        for node in self.nodes.values():
            if not node.dependencies:
                node.state = TaskState.READY
            else:
                node.state = TaskState.BLOCKED

    def _validate(self):
        # Topological sort for cycle detection
        visited = set()
        in_stack = set()
        for node_id in self.nodes:
            if node_id not in visited:
                self._dfs_cycle_check(node_id, visited, in_stack)

    def _dfs_cycle_check(self, node_id, visited, in_stack):
        visited.add(node_id)
        in_stack.add(node_id)
        for dep_id in self.nodes[node_id].dependents:
            if dep_id in in_stack:
                raise ValueError(f"Cycle detected involving {dep_id}")
            if dep_id not in visited:
                self._dfs_cycle_check(dep_id, visited, in_stack)
        in_stack.remove(node_id)
```

### 3.2 Parallel Execution Scheduling

**Concurrency model**: Semaphore-based. The dispatcher maintains a counter of running agents. When a task completes, the counter decrements and the next ready task is launched.

```python
import asyncio

class AgentPool:
    def __init__(self, max_concurrent: int = 3):
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.running: dict[str, asyncio.subprocess.Process] = {}

    async def launch(self, task: TaskNode, agent_def: str):
        async with self.semaphore:
            proc = await asyncio.create_subprocess_exec(
                "claude", "-p", self._build_prompt(task),
                "--output-format", "stream-json",
                "--allowedTools", self._tools_for(agent_def),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            self.running[task.id] = proc
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=task.timeout_seconds or 600,
            )
            del self.running[task.id]
            return self._parse_result(task.id, stdout, proc.returncode)
```

**Why limit concurrency to 3**: API rate limits are the binding constraint, not CPU. Three concurrent Claude Code sessions hit a practical ceiling for most API tiers. The default should be configurable via `config.yaml`.

**Git worktrees for parallel isolation**: When two tasks modify overlapping file paths, they need separate working directories. Git worktrees provide this without duplicating the repository.

```bash
# Dispatcher creates worktrees for parallel tasks
git worktree add ./sdlc/worktrees/TASK-003 -b sdlc/feature-auth/TASK-003
git worktree add ./sdlc/worktrees/TASK-004 -b sdlc/feature-auth/TASK-004
```

Each agent is launched with its worktree as the cwd. After task completion, the dispatcher merges the branch back and removes the worktree. Claude Code has native worktree support (`EnterWorktree`/`ExitWorktree`), but managing them from Python gives better lifecycle control.

### 3.3 Critical Path Optimization

The critical path is the longest chain of dependent tasks through the DAG. Optimizing it means:

1. **Priority scheduling**: Tasks on the critical path are launched before non-critical tasks when the semaphore has limited slots.
2. **Fast-track reviews**: Critical path tasks get reviewed immediately. Non-critical tasks can be batched.
3. **Resource allocation**: Critical path tasks use Opus. Non-critical tasks use Sonnet.

Computing the critical path is a standard longest-path-in-DAG algorithm (negate weights, run shortest path on the topological order). Task "weight" is estimated complexity from the plan (low=1, medium=3, high=5).

### 3.4 Dynamic Task Graph Modifications

During execution, new tasks can be discovered. For example, the executor working on TASK-005 might realize it needs a utility function that does not exist yet. Two approaches:

**Option A: Agent requests a new task.** The executor writes a request file:

```yaml
# sdlc/task-requests/REQ-001.yaml
requested_by: "TASK-005"
title: "Create string sanitization utility"
rationale: "TASK-005 needs to sanitize OAuth callback URLs. No existing utility."
suggested_dependencies: ["TASK-001"]
suggested_dependents: ["TASK-005"]
blocking: true  # TASK-005 cannot continue without this
```

The dispatcher detects the request file, pauses TASK-005, creates TASK-013 from the request, inserts it into the DAG, and launches it. When TASK-013 completes, TASK-005 is resumed (or re-launched with context about what TASK-013 produced).

**Option B: Agent handles it inline.** The executor simply creates the utility as part of TASK-005. This is simpler but risks scope creep and makes review harder.

**Recommendation: Option A for tasks marked `blocking: true` with complexity > low. Option B for trivial additions.** The executor agent's prompt should include instructions for when to request a new task vs handle inline, with a bias toward inline for small changes (under ~50 lines).

---

## 4. Agent Architecture & Specialization

### 4.1 Agent Roster

Five agent types, each defined as a Claude Code subagent markdown file:

| Agent | Purpose | Model | Tools Needed | Permission Mode |
|-------|---------|-------|-------------|-----------------|
| **Planner** | Decompose requirements into features/tasks, define dependencies | Opus | Read, Glob, Grep, WebSearch | plan-mode (no writes) |
| **Executor** | Implement a single task -- write code, create tests | Sonnet (default), Opus (critical path) | Read, Write, Edit, Bash, Glob, Grep | full (with allowlist) |
| **Reviewer** | Review code changes against plan and quality standards | Opus | Read, Glob, Grep, Bash (read-only) | plan-mode |
| **Tester** | Run tests, check coverage, verify behavior | Sonnet | Read, Bash, Glob, Grep | bypassPermissions for test commands |
| **Simplifier** | Refactor for clarity, remove duplication, check consistency | Sonnet | Read, Write, Edit, Glob, Grep | full (scoped to changed files) |

An optional sixth agent, **Garbage Collector** (from OpenAI's pattern), runs periodically to clean up dead code, unused imports, and stale comments. This is low priority for v1 but should be planned for.

### 4.2 Subagent Definitions

Each agent is a markdown file in `.claude-plugin/agents/`:

**`.claude-plugin/agents/planner.md`:**

```markdown
---
name: sdlc-planner
model: claude-opus-4-20250514
allowedTools:
  - Read
  - Glob
  - Grep
  - WebSearch
  - Write  # Only for writing plan.yaml and task files
---

# SDLC Planner Agent

You are a software architect planning the implementation of a feature.

## Your Task
Read the objective from the input, analyze the existing codebase, and produce:
1. A feature decomposition in `./sdlc/plan.yaml`
2. Individual task files in `./sdlc/tasks/TASK-NNN.yaml`

## Rules
- Decompose into tasks that are each completable in a single agent session (15-30 min of work)
- Each task must have clear acceptance criteria
- Define dependencies explicitly -- a task that needs the database schema must list the schema task as a dependency
- Estimate complexity as low/medium/high
- Prefer many small tasks over few large ones
- Never create a task that requires modifying more than 5 files
- Include test-writing as part of implementation tasks, not as separate tasks (unless integration tests spanning multiple features)

## Output Format
Write plan.yaml and task files following the schemas defined in ./sdlc/schemas/.
Write a summary of the plan to stdout.

## Codebase Analysis Checklist
Before planning, always:
1. Read the project's README, package.json/pyproject.toml/Cargo.toml
2. Understand the existing directory structure
3. Identify existing patterns (naming conventions, test locations, config approach)
4. Check for existing CI/CD configuration
5. Read AGENTS.md or CLAUDE.md if present
```

**`.claude-plugin/agents/executor.md`:**

```markdown
---
name: sdlc-executor
model: claude-sonnet-4-20250514
allowedTools:
  - Read
  - Write
  - Edit
  - Bash
  - Glob
  - Grep
---

# SDLC Executor Agent

You implement a single task from the SDLC pipeline.

## Input
You receive a task definition (YAML) as your prompt. It contains:
- Task description and acceptance criteria
- Dependencies (already completed -- their output files are available)
- Constraints from the plan

## Rules
- Implement ONLY what the task specifies. Do not refactor unrelated code.
- Write tests for all new functionality.
- Follow existing code patterns in the repository.
- Make a git commit when the task is complete with message: "sdlc: {TASK-ID} - {title}"
- If you discover the task requires work outside its scope, write a task request to ./sdlc/task-requests/REQ-NNN.yaml and note it in your output. Do NOT do the out-of-scope work.
- If you cannot complete the task, write a clear explanation of what went wrong to stdout.

## Completion Checklist
Before declaring done:
1. All acceptance criteria from the task definition are met
2. New code compiles / passes syntax checks
3. Tests pass locally
4. No unrelated files were modified
5. Changes are committed to git

## Anti-Patterns to Avoid
- Do NOT declare victory prematurely. Verify your work compiles and tests pass.
- Do NOT modify the task YAML files. The dispatcher manages task state.
- Do NOT install new dependencies without them being listed in the task constraints.
```

**`.claude-plugin/agents/reviewer.md`:**

```markdown
---
name: sdlc-reviewer
model: claude-opus-4-20250514
allowedTools:
  - Read
  - Glob
  - Grep
  - Bash
---

# SDLC Reviewer Agent

You review code changes produced by the executor agent for a single task.

## Input
You receive:
- The task definition (YAML)
- The git diff of changes made by the executor
- The plan context (which feature this task belongs to)

## Review Criteria
1. **Correctness**: Does the code do what the task specifies?
2. **Completeness**: Are all acceptance criteria met?
3. **Quality**: Is the code clean, well-named, properly structured?
4. **Tests**: Are there adequate tests? Do they test meaningful behavior, not just implementation?
5. **Security**: Any obvious vulnerabilities (SQL injection, XSS, hardcoded secrets)?
6. **Consistency**: Does the code follow existing project patterns?
7. **Scope**: Did the executor stay within the task boundary? Flag any out-of-scope changes.

## Output
Write your review to ./sdlc/reviews/TASK-NNN-review.yaml following the review schema.

Your verdict must be one of:
- `approved` -- Ship it.
- `changes_requested` -- Specific, actionable findings that the executor must fix.
- `rejected` -- Fundamentally wrong approach, needs re-planning.

## Rules
- Be specific. "Code quality could be better" is not actionable. "Function `authenticate()` at line 42 should validate email format before database lookup" is.
- Run the tests yourself (via Bash) to verify they pass. Do not trust the executor's claim.
- Check the git diff carefully for debugging artifacts (console.log, TODO comments, commented-out code).
```

### 4.3 Agent Communication and Handoff

Agents do not communicate directly. All communication flows through the state files, mediated by the dispatcher. This is a deliberate architectural choice:

```
Planner --writes--> plan.yaml, tasks/*.yaml
                        |
                        v
Dispatcher --reads--> picks next ready task
                        |
                        v
Executor --reads--> task YAML --writes--> code + commit + stdout result
                        |
                        v
Dispatcher --reads--> result --writes--> task state update
                        |
                        v
Reviewer --reads--> task YAML + git diff --writes--> review YAML
                        |
                        v
Dispatcher --reads--> review verdict
    |
    +--> approved: merge branch, advance pipeline
    +--> changes_requested: update task YAML with findings, re-launch executor
    +--> rejected: flag for human review
```

**Why no direct agent-to-agent communication**:
1. Debuggability. Every piece of information exchanged is a file you can read.
2. Replaceability. You can swap out the reviewer agent without changing the executor.
3. Determinism. The dispatcher controls execution order, not emergent agent behavior.
4. Crash resilience. If the reviewer crashes, its partial output is in the review file. The dispatcher can re-launch it.

The one exception where agent-to-agent awareness matters is when the executor needs to know about review findings from a previous attempt. This is handled by the dispatcher appending the review findings to the task YAML before re-launching:

```yaml
# Added by dispatcher before retry
previous_reviews:
  - attempt: 1
    verdict: changes_requested
    findings:
      - severity: error
        file: "src/auth/service.py"
        line: 42
        message: "Missing email validation before DB lookup"
```

### 4.4 Model Selection Strategy

The model selection is not just about capability but about cost and latency:

| Operation | Model | Rationale |
|-----------|-------|-----------|
| Planning | Opus | Needs to understand full codebase architecture, make decomposition decisions |
| Execution (critical path) | Opus | Critical path tasks block everything; higher quality reduces review cycles |
| Execution (non-critical) | Sonnet | Good enough for well-defined tasks; 5-10x cheaper than Opus |
| Review | Opus | Must catch subtle bugs; false negatives are expensive (merged bad code) |
| Testing | Sonnet | Running tests and checking output is mostly procedural |
| Simplification | Sonnet | Refactoring well-tested code is lower risk |
| Garbage collection | Haiku | Detecting unused imports, dead code is mechanical |

The `config.yaml` allows overriding these defaults:

```yaml
# config.yaml
models:
  planner: claude-opus-4-20250514
  executor_critical: claude-opus-4-20250514
  executor_default: claude-sonnet-4-20250514
  reviewer: claude-opus-4-20250514
  tester: claude-sonnet-4-20250514
  simplifier: claude-sonnet-4-20250514
```

---

## 5. Plugin Architecture

### 5.1 Plugin Directory Structure

```
.claude-plugin/
  plugin.json              # Plugin manifest
  settings.json            # Default settings for the plugin
  .mcp.json                # MCP server definitions (if any)

  skills/
    sdlc-plan/
      SKILL.md             # /sdlc-plan slash command
    sdlc-run/
      SKILL.md             # /sdlc-run slash command
    sdlc-status/
      SKILL.md             # /sdlc-status slash command
    sdlc-resume/
      SKILL.md             # /sdlc-resume slash command
    sdlc-review/
      SKILL.md             # /sdlc-review (trigger human review)

  agents/
    planner.md
    executor.md
    reviewer.md
    tester.md
    simplifier.md

  hooks/
    pre-commit-check.sh    # Validates SDLC state before git commits

  lib/
    sdlc_dispatch.py       # Main dispatcher
    dag.py                 # DAG scheduler
    state.py               # State management
    agents.py              # Agent process management
    config.py              # Configuration loading
    schemas.py             # YAML schema validation
    requirements.txt       # Python dependencies (pyyaml, etc.)
```

### 5.2 Plugin Manifest

**`plugin.json`:**

```json
{
  "name": "xpatcher-sdlc",
  "version": "0.1.0",
  "description": "SDLC automation pipeline -- orchestrates planning, execution, review, testing, and simplification of software features.",
  "skills": [
    "skills/sdlc-plan",
    "skills/sdlc-run",
    "skills/sdlc-status",
    "skills/sdlc-resume",
    "skills/sdlc-review"
  ],
  "agents": [
    "agents/planner.md",
    "agents/executor.md",
    "agents/reviewer.md",
    "agents/tester.md",
    "agents/simplifier.md"
  ]
}
```

### 5.3 Skill Definitions

Skills are the user-facing entry points. They bridge between the Claude Code interactive session and the Python dispatcher.

**`skills/sdlc-plan/SKILL.md`:**

```markdown
---
name: sdlc-plan
description: "Plan an SDLC pipeline for a feature. Decomposes requirements into tasks with dependencies."
arguments: objective
---

# SDLC Plan

Create an SDLC execution plan for the following objective:

$ARGUMENTS

## Steps

1. Initialize the ./sdlc/ directory structure if it does not exist:
!`mkdir -p ./sdlc/tasks ./sdlc/reviews ./sdlc/test-results ./sdlc/logs ./sdlc/task-requests`

2. Launch the planner agent to analyze the codebase and produce a plan:
!`python3 .claude-plugin/lib/sdlc_dispatch.py plan "$ARGUMENTS"`

3. Read the generated plan and present it to the user for review.
4. Wait for the user to approve or request changes.
```

**`skills/sdlc-run/SKILL.md`:**

```markdown
---
name: sdlc-run
description: "Execute the SDLC pipeline. Runs tasks from an approved plan."
arguments: options
---

# SDLC Run

Execute the SDLC pipeline.

## Steps

1. Verify that ./sdlc/plan.yaml exists and has been approved:
!`python3 .claude-plugin/lib/sdlc_dispatch.py check-plan`

2. Launch the dispatcher in execution mode:
!`python3 .claude-plugin/lib/sdlc_dispatch.py run $ARGUMENTS`

The dispatcher will:
- Build the task dependency graph
- Launch executor agents for ready tasks (up to concurrency limit)
- Monitor task completion
- Launch reviewer agents for completed tasks
- Handle retry logic for failed/rejected tasks
- Report progress to stdout

3. When all tasks complete, present the summary to the user.
```

**`skills/sdlc-status/SKILL.md`:**

```markdown
---
name: sdlc-status
description: "Show the current status of the SDLC pipeline."
---

# SDLC Status

!`python3 .claude-plugin/lib/sdlc_dispatch.py status`

Present the output to the user in a readable format.
```

**`skills/sdlc-resume/SKILL.md`:**

```markdown
---
name: sdlc-resume
description: "Resume a paused or crashed SDLC pipeline."
---

# SDLC Resume

!`python3 .claude-plugin/lib/sdlc_dispatch.py resume`

The dispatcher will:
1. Read ./sdlc/state.yaml to determine pipeline state
2. Check for interrupted tasks (RUNNING state with dead processes)
3. Recover or retry interrupted tasks
4. Continue execution from where it left off
```

### 5.4 Interaction Between Dispatcher and Plugin Components

The key architectural question is: **does the dispatcher run as a long-lived process or as repeated short invocations?**

**Recommendation: Long-lived process for `run` and `resume`, short invocations for everything else.**

- `/sdlc-plan` invokes `sdlc_dispatch.py plan "..."` which runs the planner agent once, writes files, exits.
- `/sdlc-run` invokes `sdlc_dispatch.py run` which starts the event loop and runs until the pipeline completes or hits a human review gate. It streams status updates to stdout, which the calling skill presents to the user.
- `/sdlc-status` invokes `sdlc_dispatch.py status` which reads state files and exits immediately.
- `/sdlc-resume` invokes `sdlc_dispatch.py resume` which is equivalent to `run` but with crash-recovery logic first.

The dispatcher process talks to Claude Code agents via `claude -p` subprocess invocations. Each agent invocation is a separate process with its own context window. The dispatcher passes task context via the prompt string and receives results via stdout (with `--output-format json`).

```python
import subprocess
import json
import yaml

def run_agent(agent_name: str, prompt: str, model: str,
              allowed_tools: list[str], cwd: str,
              timeout: int = 600) -> dict:
    """Launch a Claude Code agent and return its result."""
    cmd = [
        "claude", "-p", prompt,
        "--output-format", "json",
        "--model", model,
    ]
    for tool in allowed_tools:
        cmd.extend(["--allowedTools", tool])

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=cwd,
        timeout=timeout,
    )

    if result.returncode != 0:
        return {
            "status": "failed",
            "error": result.stderr,
            "exit_code": result.returncode,
        }

    return json.loads(result.stdout)
```

### 5.5 Configuration and Customization Points

Users configure the pipeline through `./sdlc/config.yaml` (per-project) and the plugin's `settings.json` (defaults).

**`./sdlc/config.yaml`:**

```yaml
# Project-specific SDLC configuration
version: 1

# Concurrency
concurrency:
  max_parallel_tasks: 3
  max_retries: 3
  task_timeout_seconds: 600   # 10 minutes per task

# Model overrides (see Section 4.4 for defaults)
models:
  executor_default: claude-sonnet-4-20250514

# Human review gates
review_gates:
  plan_approval: true          # Pause after planning for human review
  code_review: false           # Auto-approve if reviewer agent approves
  final_review: true           # Pause before marking pipeline complete

# Git behavior
git:
  branch_prefix: "sdlc"
  auto_commit: true
  use_worktrees: true          # Required for parallel execution
  merge_strategy: "squash"     # squash | merge | rebase

# Quality thresholds
quality:
  min_test_coverage: 80
  max_files_per_task: 5
  require_reversible_migrations: true

# Agent prompt overrides (appended to default prompts)
prompt_additions:
  executor: |
    Additional project-specific rules:
    - Use pytest, not unittest
    - All API endpoints must have OpenAPI docstrings
  reviewer: |
    Additional review criteria:
    - Check for proper error handling in all async functions
```

**`.claude-plugin/settings.json`:**

```json
{
  "permissions": {
    "allow": [
      "Bash(python3 .claude-plugin/lib/*)",
      "Bash(mkdir -p ./sdlc/*)",
      "Read(./sdlc/*)",
      "Write(./sdlc/*)"
    ]
  }
}
```

---

## Summary of Key Architectural Decisions

| Decision | Choice | Key Rationale |
|----------|--------|---------------|
| Orchestrator role | Thin Python shell, agents own reasoning | Avoid duplicating LLM capabilities in Python |
| Coordination | File-based polling | Crash-resilient, human-inspectable, agent-readable |
| State storage | YAML files in `./sdlc/` | Git-trackable, human-editable, agent-readable |
| Task execution | DAG with semaphore-limited parallelism | Respects dependencies, maximizes throughput |
| Parallel isolation | Git worktrees per task | Clean separation, standard git tooling for merge |
| Agent communication | Indirect via state files, dispatcher-mediated | Debuggable, deterministic, crash-resilient |
| Agent specialization | 5 types matching SDLC phases | Narrow scope = better results, appropriate model selection |
| Plugin structure | Skills as entry points, Python lib as engine | Skills provide UX, Python provides orchestration |
| Human-in-the-loop | Configurable review gates | Trust builds over time; start gated, relax later |
| Dynamic tasks | Agent requests via files, dispatcher inserts into DAG | Controlled scope expansion without agent coordination |

## Open Questions for Next Phase

1. **Browser verification**: Should we integrate Puppeteer/Playwright testing as a standard pipeline stage, or leave it to the tester agent's discretion?
2. **Progress file format**: Anthropic recommends `claude-progress.txt` as free-form text. Should we formalize it or keep it loose for agent flexibility?
3. **Multi-repo support**: The current design assumes a single repository. How should cross-repo features be handled?
4. **Cost tracking**: Should the dispatcher track API costs per task and enforce budgets?
5. **Deterministic linting**: OpenAI's pattern uses linters as constraints. Should we run linters as a post-execution gate before review, or let the reviewer catch lint issues?
6. **AGENTS.md generation**: Should the planner auto-generate or update an AGENTS.md file as part of planning, following OpenAI's pattern of a concise map to deeper documentation?

# DevOps, Git Strategy & Deployment Review

**Reviewer:** DevOps/Release Engineer
**Date:** 2026-03-29
**Spec version:** 1.2 (Final Draft, 2026-03-28)
**Scope:** Git branching, worktree management, crash recovery, pipeline resumption, operational concerns
**Documents reviewed:** All 12 design subdocuments + master proposal + project memory

---

## Executive Assessment

The xpatcher design is architecturally sound for a local-first, single-developer automation pipeline. The file-based state machine, YAML artifacts, and crash-recovery-through-resumption model are well-suited to the problem domain. The single-feature-at-a-time constraint dramatically simplifies the git model and eliminates an entire class of coordination problems.

However, there is one **design-breaking gap**: the specification describes parallel task execution in git worktrees but never specifies how changes from those worktrees converge back onto the feature branch. This is not a minor omission -- it is the central data flow problem of the execution stage and touches conflict resolution, commit ordering, test validity, and crash recovery. Until this is resolved, the parallel execution design is incomplete.

Beyond that gap, there are several significant operational concerns around worktree lifecycle management, cleanup after failure, branch naming sanitization, authentication for remote push, and the absence of a rollback strategy. These are all solvable but need explicit design decisions.

**Verdict:** Do not proceed to implementation of parallel execution (Phase 4, Step 26) until the worktree merge strategy is fully specified. Phases 1-3 can proceed safely since they are sequential.

---

## Strengths

1. **File-based state is a superb choice for crash recovery.** The design's core insight -- that `pipeline-state.yaml` IS the recovery mechanism -- is elegant. On resume, the dispatcher reads state from disk and continues. No database, no journal, no write-ahead log. This is operationally simple and debuggable.

2. **Single feature at a time eliminates cross-feature conflicts.** This is a pragmatic constraint that avoids the combinatorial explosion of multi-feature merge conflicts. It also means the git model is simple: one branch, linear(ish) history.

3. **Immutable artifacts with versioning.** Plan revisions create `plan-v{N}.yaml` rather than overwriting. This makes audit trails trivial and means you can always reconstruct what happened, even after a crash mid-revision.

4. **Two-level state machine is well-designed.** Pipeline-level and per-task states are clean, validated transitions are enforced, and every transition is persisted. This is solid state machine engineering.

5. **Circuit breakers and kill switches are specified.** Token budget, iteration count, cost budget, wall-clock time, and emergency kill are all defined. The monthly testing requirement is a good operational discipline.

6. **Session registry for context continuity.** The `sessions.yaml` approach and session reuse decision matrix (Section 7.8) are thoughtful. Reusing planner sessions for reviewers, starting fresh for adversarial isolation -- these are good context management decisions.

7. **Structured commit messages with artifact references.** The format `xpatcher(task-001): Add session store interface` plus plan/task YAML references in the commit body creates excellent traceability.

---

## Critical Issues

### CRIT-01: Worktree Merge Strategy is Completely Unspecified

**Severity: Design-breaking**

This is the single largest gap in the entire specification. The design says:

- Section 2.5: "Each parallel task runs in its own git worktree for file isolation."
- Section 2.5: `git worktree add .xpatcher/worktrees/TASK-003 -b xpatcher/feature-auth/TASK-003`
- Section 2.6: "Atomic task commits: Each task produces commits on the feature branch"
- Stage 10: "Worktrees/branches created, rollback tags"
- Stage 11: "Code committed on task branches"

The spec creates per-task branches in worktrees but never describes:

1. **How task branches merge back to the feature branch.** Is it `git merge`? `git rebase`? `git cherry-pick`? Each has different implications for history, conflict resolution, and atomicity.

2. **When merges happen.** After each task completes? After each batch completes? After all tasks complete? The answer affects whether later tasks in a batch see earlier tasks' changes.

3. **Merge conflict resolution.** If task-002 and task-003 run in parallel and both modify `src/config.py` (despite `file_scope` attempting to prevent this), what happens? The spec's file_scope field on tasks is advisory -- the planner declares expected file scope, but the executor could touch files outside that scope (the spec even contemplates "justified scope expansion" in Section 6.6, convergence criteria item 3).

4. **Commit ordering on the feature branch.** After parallel tasks merge, what order do commits appear? Topological (by dependency)? Chronological (by completion time)? This affects `git bisect` usability and history readability.

5. **Integration testing after merge.** The per-task quality loop (Stages 12-13) runs tests in the worktree. But after merging multiple task branches to the feature branch, the combined result has never been tested. The Appendix C checklist item "Integration tests run on merged result of parallel branches" acknowledges this need but no stage in the pipeline performs it.

6. **Worktree branch cleanup.** After a successful merge to the feature branch, are task branches deleted? Are worktrees removed? The spec is silent.

**Recommendation:** Define a complete merge protocol. My suggested approach:

```
For each batch of parallel tasks:
  1. Tasks execute in isolated worktrees on per-task branches
  2. As each task completes its quality loop (Stages 12-13):
     a. Checkout the feature branch
     b. Merge (or rebase) the task branch onto the feature branch
     c. If merge conflict: attempt auto-resolution; if that fails,
        mark task as BLOCKED and escalate to human
     d. Run integration tests on the feature branch post-merge
     e. If integration tests fail: revert the merge, mark task FAILED
     f. Delete the task branch and worktree on success
  3. Remaining parallel tasks in the batch continue in their worktrees
     (they do NOT see each other's merges during execution -- this is
     intentional for isolation)
  4. After all tasks in a batch have merged or failed, advance to the
     next batch
```

This is a first-order design decision that should appear in Section 2.6 with the same level of detail as the rest of the git strategy.

### CRIT-02: Pipeline-State.yaml Concurrent Access

**Severity: High**

`pipeline-state.yaml` is the mutable singleton (Section 5.2). The dispatcher writes to it on every state transition. But during parallel execution (Stage 11), multiple things are happening:

- The dispatcher's main loop updates pipeline-level state
- Per-task state transitions happen as tasks complete in worktrees
- The `task_statuses` map in `pipeline-state.yaml` is updated by the dispatcher for each task

If the dispatcher is single-threaded and all state writes go through it, this is safe. But Section 7.1 describes `parallel.py` as a "thread pool for concurrent agents." If multiple threads update `pipeline-state.yaml` concurrently, YAML writes can corrupt the file (partial write visible to reader, interleaved writes).

The spec says file locking is "deferred" (Section 10, Open Questions item 5). For the dispatcher's own internal state file, this cannot be deferred -- it is a v1 requirement.

**Recommendation:** Either (a) use a threading lock inside the dispatcher for all `pipeline-state.yaml` writes (simplest -- the dispatcher is the only writer), or (b) use atomic write (write to temp file, then `os.rename()`) to prevent partial-read corruption. Option (a) is sufficient for v1 since the dispatcher is the single writer.

---

## Major Issues

### MAJ-01: Branch Name Sanitization

**Severity: Medium-High**

Section 2.6: `git checkout -b xpatcher/<feature-slug>`

The feature slug comes from user input (the natural-language task description, processed by the planner). The spec does not describe how the feature slug is generated or sanitized. Git branch names have specific rules:

- Cannot contain: space, `~`, `^`, `:`, `?`, `*`, `[`, `\`
- Cannot start with `-` or end with `.lock`
- Cannot contain `..` or `@{`
- Cannot be a single `@`
- Maximum length varies by platform (typically ~255 bytes for the full ref path)

A user request like "Add OAuth2 support (Google & GitHub)" would produce a slug with characters that are invalid in branch names. A request like "Fix the bug where users can't login when they have special characters like 'quotes' in their password" would be even worse.

**Recommendation:** Define an explicit slug generation algorithm in Section 2.6:

```python
import re

def slugify_for_git(text: str, max_length: int = 50) -> str:
    slug = text.lower()
    slug = re.sub(r'[^a-z0-9]+', '-', slug)
    slug = slug.strip('-')
    slug = slug[:max_length]
    slug = slug.rstrip('-')
    return slug or 'unnamed-feature'
```

### MAJ-02: Rebase on Resume Rewrites History

**Severity: Medium-High**

Section 2.7: "If base changed: rebases feature branch, re-runs affected tests"

If the feature branch has already been pushed to remote (which happens on completion per Section 2.6 step 4, but could also happen if the user manually pushes during a pause), rebasing creates divergent history. A subsequent `git push` will be rejected unless forced.

Scenarios where this breaks:
1. Pipeline completes partially, user pushes to remote for backup, pipeline resumes and rebases
2. Pipeline is interrupted, another developer pulls the feature branch, pipeline resumes and rebases
3. Pipeline creates a draft PR (if configured), pipeline resumes and rebases -- PR now shows force-push

**Recommendation:** On resume, check if the feature branch has been pushed to remote:

```python
def can_safely_rebase(branch: str) -> bool:
    # Check if branch exists on remote
    result = subprocess.run(
        ["git", "rev-parse", "--verify", f"origin/{branch}"],
        capture_output=True
    )
    if result.returncode != 0:
        return True  # Not pushed, safe to rebase

    # Check if local and remote are the same
    local = subprocess.run(["git", "rev-parse", branch], capture_output=True, text=True)
    remote = subprocess.run(["git", "rev-parse", f"origin/{branch}"], capture_output=True, text=True)

    if local.stdout.strip() == remote.stdout.strip():
        return True  # Same commit, rebase would be a no-op or safe

    return False  # Branch was pushed and diverged, rebase is dangerous
```

If rebase is unsafe, offer the user a choice: merge instead of rebase, or force-push (with explicit confirmation).

### MAJ-03: Worktree Cleanup After Crash

**Severity: Medium-High**

Section 2.5 creates worktrees at `.xpatcher/worktrees/TASK-NNN`. If the pipeline crashes during parallel execution:

1. Worktrees remain on disk, consuming space (each worktree is a full working copy)
2. Git still tracks these worktrees (`git worktree list` shows them)
3. The associated task branches exist in the local repo
4. Any uncommitted work in worktrees is lost

On resume, the dispatcher reads `pipeline-state.yaml` but the spec does not describe:
- How to detect orphaned worktrees
- Whether to reuse existing worktrees or recreate them
- How to clean up worktrees from tasks that completed successfully before the crash
- How to handle worktrees with uncommitted changes (half-completed executor work)

**Recommendation:** Add a worktree reconciliation step to the resume protocol (Section 2.7):

```
On resume during parallel execution:
1. List all worktrees: git worktree list
2. For each worktree:
   a. If corresponding task is SUCCEEDED: merge task branch, remove worktree
   b. If corresponding task is RUNNING: check for uncommitted changes
      - If uncommitted changes exist: stash or discard (configurable)
      - Reset task to PENDING, recreate worktree fresh
   c. If corresponding task is FAILED: remove worktree, keep task in failed state
3. Prune stale worktree references: git worktree prune
4. Continue normal execution
```

### MAJ-04: Git Hooks Interference

**Severity: Medium**

The target project may have its own git hooks (pre-commit linters, commit-msg formatters, pre-push checks). xpatcher's executors commit code (Section 4.3 executor completion checklist item 5). These commits will trigger the project's hooks.

Potential problems:
1. **pre-commit hooks** that run linters may reject executor commits (the executor may not produce code that passes all linters on first try)
2. **commit-msg hooks** that enforce Conventional Commits format will reject xpatcher's `xpatcher(task-001): ...` format
3. **pre-push hooks** that run full test suites will delay the completion push
4. Hooks that require interactive input (e.g., GPG signing prompts) will hang the headless `claude -p` subprocess

**Recommendation:** Document the hook interaction explicitly. Options:
- (a) Skip hooks during xpatcher execution (`git commit --no-verify`) and run project linters as an explicit pipeline step instead
- (b) Run with hooks enabled but handle failures gracefully (retry after fixing lint issues)
- (c) Allow configuration per project in `.xpatcher.yaml`

Option (c) is recommended with (b) as default behavior:

```yaml
# .xpatcher.yaml
git:
  skip_hooks: false  # Set to true if project hooks are incompatible
  commit_message_prefix: "xpatcher"  # Customize if project enforces different format
```

### MAJ-05: Commit Message Format Not Enforced

**Severity: Medium**

Section 2.6 specifies the commit message format: `xpatcher(task-001): Add session store interface` with a body referencing plan and task YAML paths. But this is only specified in the executor agent's instructions (Section 4.3). Agent instructions are soft guidance -- the LLM may deviate.

If the executor produces a different commit message format, the dispatcher has no mechanism to detect or correct it. Since commit messages are used for traceability (linking commits to tasks and plans), inconsistent formatting breaks audit trails.

**Recommendation:** Add commit message validation as a post-execution step in the dispatcher:

```python
def validate_commit_messages(task_id: str, expected_prefix: str) -> list[str]:
    """Validate that all commits for a task follow the expected format."""
    result = subprocess.run(
        ["git", "log", "--format=%s", f"xpatcher/{feature}/{task_id}"],
        capture_output=True, text=True
    )
    issues = []
    for line in result.stdout.strip().split('\n'):
        if not line.startswith(f"xpatcher({task_id}):"):
            issues.append(f"Non-conforming commit message: {line}")
    return issues
```

If validation fails, the dispatcher can either (a) amend the commit message (since it has not been pushed yet), or (b) flag it as a warning in the quality report.

---

## Minor Issues

### MIN-01: PR Creation Is Under-Specified

Section 2.6: "Optionally creates a PR if `gh` CLI is available"

This casual treatment of PR creation leaves several questions:
- What is the PR title? (Feature slug? User's original intent text?)
- What is the PR body? (Summary of tasks? Link to artifacts?)
- What labels/reviewers are assigned?
- What if `gh auth status` shows the user is not authenticated?
- What if the repo has PR templates?

**Recommendation:** Define a PR creation specification:

```yaml
pr_creation:
  enabled: auto  # auto (if gh available) | always | never
  title_source: intent_goal  # Use parsed.goal from intent.yaml
  body_template: |
    ## Summary
    {plan_summary}

    ## Tasks Completed
    {task_list}

    ## Artifacts
    Plan: `.xpatcher/{feature}/plan-v{N}.yaml`

    ---
    *Generated by xpatcher pipeline {pipeline_id}*
  auth_check: true  # Verify gh auth before attempting
  draft: true  # Create as draft PR by default
```

### MIN-02: Remote Push Authentication

Section 2.6 step 4: "Pushes the feature branch to remote"

The spec assumes remote push will succeed but does not address:
- SSH key availability (agent running in a context without SSH agent)
- Token-based auth (GitHub PAT, credential helpers)
- Two-factor authentication prompts
- Permission errors (user has read but not write access)

**Recommendation:** Add a preflight check for remote push capability:

```python
def verify_push_capability(remote: str = "origin") -> bool:
    """Verify we can push to the remote before starting the pipeline."""
    # Check remote exists
    result = subprocess.run(["git", "remote", "get-url", remote], capture_output=True)
    if result.returncode != 0:
        return False
    # Attempt a dry-run push (git push --dry-run)
    result = subprocess.run(
        ["git", "push", "--dry-run", remote, "HEAD:refs/heads/__xpatcher_push_test"],
        capture_output=True
    )
    return result.returncode == 0
```

Run this during the preflight phase (project initialization) so the user is warned early, not 47 minutes into a pipeline.

### MIN-03: Disk Space Management

Each git worktree is essentially a full working copy (minus `.git`). For a 500MB project with 5 parallel tasks, that is 2.5GB of additional disk usage during execution. Plus:
- YAML artifacts accumulate over multiple pipeline runs
- JSONL agent logs can be large (every tool call is logged)
- Debug files for malformed output are stored indefinitely

The spec has no cleanup or garbage collection strategy.

**Recommendation:** Add an `xpatcher gc` command and automatic cleanup:

```yaml
cleanup:
  worktrees:
    delete_on_task_complete: true
    delete_on_pipeline_complete: true
  logs:
    retention_days: 30
    max_total_size_mb: 500
  artifacts:
    retain_last_n_pipelines: 5
    compress_old_artifacts: true
  auto_gc_on_start: true  # Clean up before starting new pipeline
```

### MIN-04: Concurrent Pipeline Prevention Is Unclear

Section 2 states "xpatcher processes one feature at a time" but does not describe the enforcement mechanism. What happens if a user runs `xpatcher start "Feature B"` while Feature A is still running?

Options:
- Check `pipeline-state.yaml` for `status: running` and refuse to start
- Use a PID-based lock file
- Use a file lock on a sentinel file

**Recommendation:** Use a lock file at `.xpatcher/.lock` containing the PID and pipeline ID of the active pipeline. On start, check if the lock exists and if the PID is still alive (to handle stale locks from crashes):

```python
def acquire_pipeline_lock(project_dir: str, pipeline_id: str) -> bool:
    lock_path = os.path.join(project_dir, ".xpatcher", ".lock")
    if os.path.exists(lock_path):
        with open(lock_path) as f:
            data = yaml.safe_load(f)
        pid = data.get("pid")
        if pid and is_process_alive(pid):
            print(f"Pipeline {data['pipeline_id']} is already running (PID {pid})")
            return False
        # Stale lock, clean up
        os.remove(lock_path)
    # Acquire lock
    with open(lock_path, 'w') as f:
        yaml.dump({"pid": os.getpid(), "pipeline_id": pipeline_id}, f)
    return True
```

### MIN-05: Worktree Path Collision

Section 2.5: `.xpatcher/worktrees/TASK-003`

If a pipeline crashes and is restarted, or if the user runs `xpatcher cancel` and immediately starts a new pipeline reusing the same task IDs (task-001, task-002, etc.), the worktree paths from the old run may still exist.

**Recommendation:** Include the pipeline ID in the worktree path: `.xpatcher/worktrees/{pipeline-id}/TASK-003`. This ensures no collision across pipeline runs.

---

## Git Workflow Walkthrough: Parallel Tasks in Worktrees to Feature Branch

This section reconstructs what the spec intends and highlights where the gaps are. The walkthrough covers a feature with 5 tasks where tasks 2 and 3 can run in parallel.

### Phase 1: Setup (Stage 10 - Execution Graph)

```
main: A---B---C
                \
                 D  (xpatcher/auth-redesign feature branch created)
```

The dispatcher creates the feature branch from main at commit C. Commit D is the initial state (possibly just the `.xpatcher/` setup).

### Phase 2: Sequential task-001 (Stage 11, Batch 1)

Task-001 has no dependencies, runs alone.

```
main: A---B---C
                \
                 D---E---F  (task-001 commits directly on feature branch)
                           (no worktree needed for single sequential tasks)
```

**Gap:** Does a single-task batch use a worktree, or commit directly to the feature branch? The spec is ambiguous. Using a worktree for single tasks adds overhead for no isolation benefit.

### Phase 3: Parallel task-002 and task-003 (Stage 11, Batch 2)

Both tasks depend on task-001 (completed). They run in parallel worktrees.

```
Worktree creation:
  git worktree add .xpatcher/worktrees/TASK-002 -b xpatcher/auth-redesign/TASK-002
  git worktree add .xpatcher/worktrees/TASK-003 -b xpatcher/auth-redesign/TASK-003

Both worktrees branch from commit F (end of task-001):

feature branch: D---E---F
                         \
task-002 branch:          G---H  (worktree 1)
                         \
task-003 branch:          I---J  (worktree 2)
```

### Phase 4: Per-Task Quality (Stage 12-13)

Task-002 and task-003 go through simplify/test/review in their respective worktrees. Tests run in isolation -- each worktree has task-001's changes but not the other parallel task's changes.

### Phase 5: Merge Back (THE GAP)

This is where the spec stops. Here is what MUST happen (one way or another):

**Option A: Sequential merge as tasks complete**

If task-002 finishes first:
```
feature branch: D---E---F-----------M1  (merge task-002)
                         \         /
task-002 branch:          G---H---+
                         \
task-003 branch:          I---J  (still running, does not see M1)
```

Then task-003 finishes and merges:
```
feature branch: D---E---F-----------M1---M2  (merge task-003)
                         \         /    /
task-002 branch:          G---H---+    /
                         \            /
task-003 branch:          I---J------+
```

**Problem:** M2 is a merge commit that has never been tested. Task-003 was tested against F, not against M1. If task-002 and task-003 both modified related files (even if not the same file), the merged result may fail tests.

**Mitigation needed:** Run integration tests on the feature branch after M2. If tests fail, revert M2 and flag task-003 for re-execution against the current feature branch (which now includes task-002).

**Option B: Batch merge after all parallel tasks complete**

Wait for all tasks in the batch to complete, then merge all at once. This allows:
- Running integration tests once after all merges
- Failing the entire batch if integration breaks
- Cleaner history (all merges happen at the same point)

**Trade-off:** If task-002 takes 3 minutes and task-003 takes 30 minutes, task-004 (which depends on task-002 but not task-003) must wait for the entire batch to merge before starting.

**Option C: Rebase instead of merge (my recommendation for v1)**

After each task completes its quality loop:
1. Rebase the task branch onto the current feature branch HEAD
2. Re-run tests after rebase (catches integration issues)
3. Fast-forward merge the task branch into the feature branch
4. This produces linear history on the feature branch

```
feature branch: D---E---F---G'---H'---I'---J'
                                (task-002)  (task-003)
```

**Trade-off:** Rebase rewrites task branch history, but since these branches are ephemeral and never pushed, this is safe. The resulting feature branch history is clean and `git bisect`-friendly.

### Phase 6: Post-Batch Integration Verification

Regardless of merge strategy, after all tasks in a batch have merged to the feature branch, the dispatcher MUST:
1. Run the full test suite on the feature branch
2. If tests fail, identify which task's merge broke things
3. Revert the offending merge and re-queue that task

This step is currently absent from the pipeline stages.

---

## Crash Recovery Assessment (Per-Stage)

### Stages 1-5 (Intent through Plan Approval)

**Risk: Low.** These stages are sequential and produce immutable artifacts. On crash:
- Resume reads `pipeline-state.yaml`, finds `current_stage`
- Completed stages have artifacts on disk -- skip them
- In-progress stage (e.g., planning): restart the stage from scratch (or resume Claude session if session ID is in `sessions.yaml`)
- Human gate (plan approval): re-prompt the user

**Gap: None significant.** This is the simplest recovery path.

### Stages 6-9 (Task Breakdown through Execution Graph)

**Risk: Low.** Sequential stages producing immutable artifacts. Same recovery as above.

**Minor gap:** If crash occurs during task manifest creation (multiple task YAML files being written), some files may exist and others may not. The dispatcher should validate the task manifest against the actual files in `tasks/todo/` on resume.

### Stage 10 (Execution Graph / Worktree Creation)

**Risk: Medium.** Worktree creation is partially completed. Some worktrees exist, others do not.

**Recovery needed:** On resume, compare the execution plan's task list against `git worktree list`. Create missing worktrees, validate existing ones.

### Stage 11 (Parallel Execution)

**Risk: High.** This is the most complex recovery scenario.

On crash during parallel execution:
- Some tasks may have completed (commits on task branches, worktrees still exist)
- Some tasks may be mid-execution (uncommitted changes in worktrees)
- The dispatcher's thread pool is gone (all subprocess PIDs are dead)
- `pipeline-state.yaml` reflects the last-persisted task statuses

**Recovery steps needed (not in spec):**

1. Kill any surviving `claude -p` subprocesses (check PIDs from `active.yaml` lifecycle hook)
2. For each task in RUNNING state:
   a. Check worktree for uncommitted changes (`git status` in worktree)
   b. Check worktree for unpushed commits (`git log task-branch..feature-branch`)
   c. If the task has commits, run acceptance tests to determine if it actually completed
   d. If incomplete: reset to PENDING, discard worktree, recreate fresh
3. For each task in SUCCEEDED state:
   a. Verify the task branch exists and has the expected commits
   b. If merged to feature branch: confirm; if not: merge now
4. Rebuild the DAG state from task statuses and resume scheduling

### Stages 12-13 (Quality Loop / Fix Iteration)

**Risk: Medium.** Per-task quality loops are independent. On crash:
- The current iteration count is tracked in `pipeline-state.yaml` (Section 5.6)
- If the quality check was mid-flight: re-run the quality check from scratch
- If the fix was mid-flight: discard uncommitted changes, re-run the fix

**Gap:** The spec does not explicitly say whether quality loop state is per-worktree or on the feature branch. If quality checks happen in the worktree (likely, since the task is not yet merged), then worktree cleanup/recovery applies here too.

### Stages 14-16 (Gap Detection through Completion)

**Risk: Low.** These stages are sequential and happen after all parallel work is merged. Recovery is straightforward: re-run the current stage.

---

## File Locking Risk Assessment

The spec defers file locking to a separate discussion. Here is the concrete risk analysis.

### What needs locking?

| File | Writers | Risk Without Lock |
|------|---------|-------------------|
| `pipeline-state.yaml` | Dispatcher (from multiple threads) | Corrupted state on concurrent write; pipeline cannot resume |
| `sessions.yaml` | Dispatcher (from multiple threads when parallel agents complete) | Lost session IDs; context continuity breaks |
| `active.yaml` (lifecycle hook) | Multiple agent processes via lifecycle hook | Incorrect active agent count; hang detection breaks |
| Task YAML files | Dispatcher moves between `todo/`, `in-progress/`, `done/` | File-not-found errors if move races with read |
| JSONL log files | Each agent writes its own log file | **No risk** -- one writer per file |

### Risk severity by file

- **pipeline-state.yaml**: HIGH. The dispatcher is multi-threaded (thread pool for parallel agents). If two tasks complete simultaneously and both trigger state updates, the state file can be corrupted. This must be locked in v1 via a threading.Lock or atomic file writes.

- **sessions.yaml**: MEDIUM. Same concurrent access pattern as pipeline-state.yaml but less critical (session loss means a fresh session, not pipeline failure).

- **active.yaml**: LOW. The lifecycle hook is invoked by Claude Code processes, not the dispatcher threads. If two agents start simultaneously, one write could overwrite the other. Impact: incorrect active count in the TUI, not pipeline failure.

- **Task YAML files**: LOW. The dispatcher moves files between folders as a single-threaded operation (even during parallel execution, the dispatcher's state machine transitions are serialized). However, if agents read task files from the filesystem while the dispatcher is moving them, they could get file-not-found errors. This is unlikely since agents receive task data via their prompt, not by reading the filesystem.

### Recommendation

Implement a minimal locking strategy for v1:

```python
import threading

class StateManager:
    def __init__(self):
        self._lock = threading.Lock()

    def update_state(self, updater: callable):
        with self._lock:
            state = self._read_state()
            updater(state)
            self._write_state_atomic(state)

    def _write_state_atomic(self, state):
        """Atomic write: write to temp, then rename."""
        tmp = self.state_path + ".tmp"
        with open(tmp, 'w') as f:
            yaml.dump(state, f)
        os.rename(tmp, self.state_path)  # Atomic on POSIX
```

This is 15 lines of code and eliminates the highest-risk concurrency issue.

---

## Operational Gaps

### OPS-01: No Log Rotation or Retention Policy

Agent logs (`agent-*.jsonl`) accumulate indefinitely. Over many pipeline runs, the `.xpatcher/<feature>/logs/` directory can grow without bound. The spec provides excellent log querying examples (Section 7.1) but no lifecycle management.

**Recommendation:** Add retention configuration:
```yaml
log_management:
  max_log_age_days: 30
  max_log_size_per_pipeline_mb: 100
  compress_after_days: 7
  gc_command: "xpatcher gc --logs"
```

### OPS-02: No Health Check or Diagnostic Command

When something goes wrong, the user's first question is "what happened?" The `xpatcher status` command shows pipeline state but does not diagnose problems. There is no equivalent of `docker inspect` or `kubectl describe`.

**Recommendation:** Add `xpatcher diagnose <pipeline-id>` that checks:
- Is the dispatcher process alive?
- Are all expected worktrees present?
- Are there orphaned processes?
- Is the state file consistent?
- Are there disk space issues?
- What was the last error in the logs?

### OPS-03: No Metrics or Telemetry

The KPIs in Appendix B are excellent targets but there is no mechanism to collect or report them. The spec describes logging tool calls to JSONL but not aggregating pipeline-level metrics.

**Recommendation (v2):** Aggregate metrics per pipeline and store them in `.xpatcher/<feature>/metrics.yaml`:
```yaml
metrics:
  total_duration_seconds: 2832
  tasks_completed: 12
  tasks_failed: 0
  total_iterations: 26
  avg_iterations_per_task: 2.17
  total_tokens_estimated: 450000
  first_pass_approval_rate: 0.67
  # etc.
```

### OPS-04: No Notification Mechanism for Long-Running Pipelines

The spec notes "No external notifications in v1" (project memory). This is fine for the initial version, but a pipeline that pauses at a human gate while the user is away means the pipeline sits idle. The TUI shows the prompt, but only if the terminal is visible.

**Recommendation:** At minimum, document in the CLI help that human gates require terminal attention. Consider adding a simple notification hook (e.g., `osascript -e 'display notification "xpatcher needs input"'` on macOS) as a low-effort improvement.

---

## Missing: Rollback Strategy

The spec describes what happens when a pipeline succeeds and when it gets stuck, but not what happens when it completes successfully and the result is wrong. The user approves at Stage 16, the branch is pushed, and then they realize the implementation is incorrect.

### What does rollback look like?

**Option A: Git reset the feature branch**
```bash
git checkout xpatcher/auth-redesign
git reset --hard <commit-before-pipeline>
git push --force
```
This is destructive and loses all pipeline work.

**Option B: Revert commits**
```bash
git revert --no-commit HEAD~12..HEAD  # Revert all task commits
git commit -m "Revert: auth-redesign pipeline (xp-20260328-a1b2)"
```
This preserves history but creates a messy revert.

**Option C: xpatcher provides rollback**
```bash
xpatcher rollback xp-20260328-a1b2
```
This command would:
1. Read `pipeline-state.yaml` to find the base commit (before pipeline started)
2. Create a new branch `xpatcher/auth-redesign-rollback`
3. Reset to the base commit
4. Optionally push and create a PR that reverts

**Recommendation:** At minimum, record the base commit SHA in `pipeline-state.yaml` at pipeline start:
```yaml
base_commit: "abc123def"  # SHA of commit where feature branch was created
base_branch: "main"
```

This enables rollback without needing to reconstruct the history. The `xpatcher rollback` command can be added in a later phase, but the data to support it should be captured from v1.

---

## Missing: Cleanup / Garbage Collection

After multiple pipeline runs, a project accumulates:
- `.xpatcher/<feature>/` directories for each feature ever run
- Worktrees that may not have been cleaned up after crashes
- Task branches that may not have been deleted
- Log files that grow without bound
- Pipeline state files for completed pipelines

There is no `xpatcher gc` or `xpatcher clean` command specified. Over time, this will consume significant disk space and clutter `git branch` output.

**Recommendation:** Add cleanup commands:

```bash
# Remove all artifacts for completed pipelines older than 30 days
xpatcher gc --older-than 30d

# Remove a specific pipeline's artifacts
xpatcher clean xp-20260328-a1b2

# Remove all worktrees and task branches for completed pipelines
xpatcher gc --git

# Dry run (show what would be cleaned)
xpatcher gc --dry-run
```

Also add automatic cleanup on pipeline completion:
- Delete all worktrees for the current feature
- Delete all task branches (they have been merged to the feature branch)
- Optionally compress log files

---

## Questions for Product Owner

1. **Worktree merge order:** When parallel tasks complete at different times, should they merge to the feature branch immediately (first-come-first-merged) or wait for the batch to complete? The answer affects whether tasks in later batches can start sooner vs. whether the feature branch is always integration-tested.

2. **Integration testing after parallel merge:** Should there be an explicit pipeline stage between parallel execution and gap detection that runs the full test suite on the merged feature branch? This is currently missing and is the only way to catch cross-task integration issues.

3. **Worktree vs. direct commit for single tasks:** Should a task that runs alone (no parallelism in its batch) use a worktree, or commit directly to the feature branch? Worktrees add overhead; direct commits are simpler but inconsistent with the parallel path.

4. **Hook compatibility mode:** Should xpatcher provide an option to disable the target project's git hooks during execution? Some projects have aggressive pre-commit hooks that may conflict with xpatcher's commit patterns.

5. **Maximum worktree count:** The spec defaults to 3 parallel agents. Should there be a hard limit on worktree count considering disk space? For a 1GB project, 5 worktrees = 5GB of disk. Users should be warned.

6. **Failed pipeline artifact retention:** When a pipeline fails and the user cancels, should `.xpatcher/<feature>/` be preserved for debugging or cleaned up? Currently, artifacts are never deleted, which is good for debugging but bad for disk space.

7. **Rollback support:** Is `xpatcher rollback <pipeline-id>` a v1 requirement or can it be deferred? At minimum, recording the base commit SHA should be v1.

8. **Remote push timing:** Should the feature branch be pushed to remote only at completion (current design), or should it be pushed incrementally after each batch merges? Incremental pushes provide backup but make rebase-on-resume dangerous.

---

## Recommendations

### Priority 1 (Must resolve before Phase 4 implementation)

| # | Recommendation | Section |
|---|---------------|---------|
| R1 | **Specify the worktree-to-feature-branch merge protocol** -- merge strategy, conflict handling, post-merge integration testing, worktree cleanup | New section in 02-system-architecture.md |
| R2 | **Add threading lock for pipeline-state.yaml writes** -- the dispatcher's thread pool creates a real concurrency hazard on the mutable state file | 09-dispatcher-internals.md |
| R3 | **Add branch name sanitization** -- generate git-safe slugs from user input | 02-system-architecture.md Section 2.6 |

### Priority 2 (Should resolve before Phase 5 / packaging)

| # | Recommendation | Section |
|---|---------------|---------|
| R4 | Add rebase safety check (detect if branch was pushed before rebasing) | 02-system-architecture.md Section 2.7 |
| R5 | Add worktree reconciliation to crash recovery protocol | 02-system-architecture.md Section 2.7 |
| R6 | Add preflight check for remote push capability | 07-cli-and-installation.md |
| R7 | Add commit message validation in dispatcher | 09-dispatcher-internals.md |
| R8 | Define concurrent pipeline prevention mechanism (lock file) | 02-system-architecture.md |

### Priority 3 (Should be in backlog for post-v1)

| # | Recommendation | Section |
|---|---------------|---------|
| R9 | Add `xpatcher gc` command for cleanup | 07-cli-and-installation.md |
| R10 | Record base commit SHA for rollback support | 05-artifact-system.md |
| R11 | Add `xpatcher diagnose` command | 07-cli-and-installation.md |
| R12 | Add log retention and rotation policy | 07-cli-and-installation.md |
| R13 | Add metrics aggregation per pipeline | 12-appendices.md |
| R14 | Document git hook interaction and add compatibility configuration | 02-system-architecture.md |
| R15 | Define PR creation specification (title, body, labels, auth check) | 02-system-architecture.md Section 2.6 |

---

*Review complete. The worktree merge strategy (R1) is the blocking item. Everything else is important but can be addressed incrementally.*

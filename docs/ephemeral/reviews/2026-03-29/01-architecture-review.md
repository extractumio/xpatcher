# Architecture & Core Engine Review

**Reviewer:** Senior Software Architect (automated review)
**Date:** 2026-03-29
**Spec Version:** 1.2 (Final Draft, 2026-03-28)
**Documents Reviewed:** Master proposal + 12 subdocuments (01 through 12)

---

## Executive Assessment

The xpatcher design is ambitious, well-structured, and demonstrates strong architectural thinking -- particularly the file-based coordination model, the adversarial review isolation, and the deliberate thin-dispatcher boundary. It is **not yet ready for implementation** as specified. The primary blockers are: (1) the two-level state machine has incomplete and contradictory transition definitions between the high-level diagrams and the stage specification tables; (2) the git worktree merge-back strategy for parallel tasks is entirely unspecified, leaving the hardest concurrency problem as an exercise for the implementor; (3) critical schema field names are inconsistent between agent prompt output formats, YAML artifact schemas, and Pydantic validation models, meaning the first end-to-end run will fail on validation; and (4) the session management system, while elegantly designed, conflates `--resume` (which continues a conversation) with context bridging in ways that will not work with the Claude CLI's actual session semantics. Confidence: **medium-high** that the overall architecture is sound, **low** that an implementor could build from this spec without significant clarification rounds. Recommend one focused revision pass addressing the issues below before Phase 1 begins.

---

## Strengths

1. **File-based coordination is the right call.** YAML state files as the single source of truth eliminates an entire class of distributed-state bugs. The crash recovery story writes itself: read the file, resume. This is the spec's strongest architectural decision.

2. **Thin dispatcher boundary is clearly drawn.** The division of responsibilities -- Python owns process lifecycle, state machine, and DAG scheduling; agents own all reasoning -- is stated explicitly and consistently enforced in the design. This will prevent the most common failure mode of AI orchestration systems: encoding domain logic in the orchestrator.

3. **Adversarial review isolation is structurally enforced.** Four independent mechanisms (separate context, checklists, read-only tools, adversarial framing) plus collusion prevention metrics. This is not just a prompt instruction; it is an architecture-level guarantee.

4. **Iteration caps with escalation are specified everywhere.** Every loop has a hard ceiling. Oscillation detection via finding-hash comparison is a practical and novel approach. The system cannot run away.

5. **Artifact immutability with version progression** creates a complete audit trail. The decision to never delete artifacts and to auto-increment versions is operationally excellent. The `ArtifactVersioner` class is well-designed.

6. **The expert panel protocol** for planning (parallel domain experts, cross-review, opus synthesis) is a thoughtful way to get multi-perspective planning without serial bottlenecks. The consensus-based round skipping is a good optimization.

7. **YAML extraction with fallback strategies** (raw parse, separator, code block, strip prose) is a practical defense against agent output variability. The same-session fix protocol for malformed output is the right approach -- the agent has context.

8. **The TUI design** with per-stage elapsed timers, task-level detail, and agent log streaming provides transparency without requiring users to dig through files. The verbosity levels are well-graduated.

9. **Pre-tool-use hooks for policy enforcement** are a hard guarantee, not a prompt suggestion. Read-only agents physically cannot write. This is defense in depth done correctly.

10. **The session reuse decision matrix** (Section 9, end) is one of the most valuable tables in the spec. It makes explicit the reasoning behind session continuity decisions that would otherwise be implicit tribal knowledge.

---

## Critical Issues (must fix before implementation)

### C1. Pipeline state machine transitions are incomplete and self-contradictory

The pipeline-level state machine in Section 2.4 defines states: `UNINITIALIZED, PLANNING, PLAN_REVIEW, APPROVED, EXECUTING, REVIEWING, REVIEW_COMPLETE, TESTING, CHANGES_REQUESTED, SIMPLIFYING, GAP_DETECTION, FINALIZING, DONE`.

But the stage specification table in Section 3.2 defines 16 stages that do not map to these states. For example:
- Stages 6-8 (Task Breakdown, Task Review, Task Fix) have no corresponding pipeline-level states.
- `REVIEW_COMPLETE` and `CHANGES_REQUESTED` appear in the state diagram but have no stage entry/exit criteria.
- The state diagram shows `EXECUTING -> REVIEWING -> REVIEW_COMPLETE -> TESTING` but the stage table shows execution feeds into per-task quality (Stage 12), not a pipeline-level `REVIEWING` state.
- `SIMPLIFYING` is a pipeline-level state in the diagram but simplification is actually per-task within Stage 12.

The implementor will have to invent the actual state machine. This must be reconciled into a single authoritative definition.

**Recommendation:** Replace the Section 2.4 diagram with one that maps 1:1 to the 16 stages. Define which stages are pipeline-level states vs. sub-states of a parent state. Specify the exact `pipeline-state.yaml` `current_stage` enum values.

### C2. Git worktree merge-back strategy is unspecified

Section 2.5 says each parallel task runs in its own git worktree with its own branch. Section 2.6 says there is a single feature branch per pipeline and tasks produce "atomic task commits" on the feature branch. These two statements are contradictory.

If task-003 runs in worktree `.xpatcher/worktrees/TASK-003` on branch `xpatcher/feature-auth/TASK-003`, how does its work get onto the single feature branch `xpatcher/auth-redesign`? The merge strategy is completely absent. Key unanswered questions:
- Is it merge, rebase, or cherry-pick?
- What happens when parallel tasks modify the same file (e.g., adding imports to the same module)?
- Who resolves conflicts -- the dispatcher (Python), a dedicated agent, or the user?
- When do worktree branches merge -- immediately after task completion, after quality loop passes, or after all parallel tasks in a batch finish?
- Section 6.6 says convergence requires "diff touches only files within declared `file_scope`" -- but what if task scope overlaps?

This is the single most implementation-blocking gap in the spec. Parallel execution is a headline feature and the hardest part is merge, not scheduling.

**Recommendation:** Add a dedicated subsection on worktree merge strategy. Define merge timing, conflict resolution policy, and the rollback procedure when a merge fails post-quality-loop.

### C3. Schema field names are inconsistent across documents

The agent prompt output formats (Section 4), YAML artifact schemas (Section 5), and Pydantic models (Section 9 / Appendix A) define overlapping but inconsistent field names for the same concepts:

| Concept | Agent Prompt (Sec 4) | YAML Schema (Sec 5) | Pydantic Model (Sec 9) |
|---------|---------------------|---------------------|----------------------|
| Review severity levels | `critical, major, minor, nit` | `major, minor, suggestion` | `critical, warning, suggestion, note` |
| Review confidence | `low, medium, high` (string) | `float 0.0-1.0` | `low, medium, high` (Literal) |
| Executor output has | `files_modified` + `files_created` (separate lists) | (not shown as separate) | `files_modified` only (single list) |
| Review category | `correctness, completeness, style, security, testability, simplicity` | (not defined) | `security, performance, correctness, style, architecture` |
| Executor status values | `completed, blocked, deviated` | `pending, in_progress, completed, stuck, blocked` | `completed, blocked, deviated` |
| Plan task ID format | `task-1.1` (dotted) | `task-001` (zero-padded) | `^task-\d+\.\d+$` (dotted regex) |

The task ID format mismatch is particularly damaging: the planner prompt uses `task-1.1` (dotted notation per phase), the artifact system uses `task-001` (sequential numbering), and the Pydantic model validates `task-\d+\.\d+`. These cannot all be correct. The first plan output will fail Pydantic validation.

**Recommendation:** Create a single canonical field reference table. Choose one format for task IDs (recommend `task-NNN` since the file system uses it). Update all three sources to match exactly.

### C4. Session `--resume` semantics conflated with context bridging

Section 9 describes session lineages where the planner session is `--resume`d by the reviewer (to inherit codebase context). But `--resume` in Claude Code continues the **same conversation** -- the reviewer would see the planner's full reasoning chain, which directly contradicts the adversarial isolation requirement (Section 6.3: "reviewer cannot see executor reasoning").

The session reuse matrix says "Planner -> Plan Reviewer: `--resume` planner session" and separately "Task Executor -> Task Reviewer: Context bridge + fresh session (adversarial isolation)". But the same isolation concern applies to plan review: if the plan reviewer sees the planner's internal reasoning (including abandoned approaches, uncertainty, etc.), the review is compromised.

Additionally, `--resume` with a `--agent` flag for a different agent type is undefined behavior in Claude CLI. The spec does not address whether you can resume a planner session as a reviewer.

**Recommendation:** Clarify Claude CLI capabilities: can you `--resume` a session while switching `--agent`? If not, context bridging is the only viable cross-agent mechanism. Revisit the entire session reuse matrix with this constraint. For adversarial isolation, plan review should also use context bridge + fresh session.

---

## Major Issues (should fix, high risk if ignored)

### M1. No semantic validation stage is actually defined

The validation pipeline (Section 9) describes three stages: YAML extraction, schema validation, and semantic validation. Stages 1 and 2 have concrete implementations. Stage 3 ("cross-reference checks: file paths exist, task IDs valid") has zero implementation. There is no `SemanticValidator` class, no list of checks, and no specification of which checks apply to which artifact types.

For a system that relies entirely on structured YAML flowing between agents, semantic validation is where the real bugs hide. An agent can produce schema-valid YAML that references non-existent task IDs, files that were deleted by another parallel task, or phases that were removed during plan revision.

**Recommendation:** Define the semantic validation rules per artifact type. At minimum: (1) plan references valid files in the repo, (2) task `depends_on` references existing task IDs, (3) review `target_ref` matches an existing artifact, (4) execution log `files_changed` paths are within project boundary.

### M2. Per-task quality loop ordering is ambiguous

Section 3.1 diagram shows Stage 12 as "simplify -> test -> review" but this ordering is never specified in the stage specification table or transition table. The diagram in the master document says "Per-task Quality" but does not break down the sub-stages.

Questions the implementor must answer:
- Does the simplifier run before or after tests?
- If simplification changes code, are tests re-run?
- If the reviewer finds issues after simplification, does the executor fix and then re-simplify?
- Section 6.4 says simplification runs "after each task passes review" but the diagram shows it running before review.

These contradictions will cause the implementor to make arbitrary choices that may conflict with the quality guarantees.

**Recommendation:** Define the per-task quality loop as an explicit sub-state machine with its own transition table.

### M3. DAG scheduling lacks handling for dynamic dependency changes

The DAG is constructed once from task YAML `dependencies` fields (Section 2.5). But gap detection (Stage 14) can inject new tasks that re-enter the pipeline at Stage 6. These new tasks may depend on already-completed tasks or may need to be inserted into the middle of the dependency chain.

The `TaskDAG` class has no method for dynamic modification. `mark_complete` updates state but there is no `add_task`, `add_dependency`, or `rebuild_from_manifest`. The execution plan schema defines static batches -- there is no mechanism to re-batch after gap tasks are injected.

**Recommendation:** Add DAG mutation methods and specify the re-planning workflow when gap tasks are added. Define whether gap tasks form their own execution plan or are merged into the existing one.

### M4. Simplifier has Bash(read-only) but needs to run tests

Section 4.6 gives the simplifier `Bash(git diff:git log:ls:wc)` -- read-only Bash. But Section 6.4 says "After each commit, run the full test suite." The simplifier cannot run tests with its current tool permissions.

Either the simplifier needs full Bash access (to run `pytest`, `npm test`, etc.) or the dispatcher must handle test execution between simplification commits. The spec does not clarify this.

**Recommendation:** If the simplifier runs tests itself, grant it Bash access for test commands specifically. If the dispatcher runs tests, specify that integration explicitly (the dispatcher is supposed to be "thin" and not run project tooling).

### M5. Pipeline state file has two different field names for iterations

Section 5.4 (Pipeline State Schema) uses `iteration_counts` with fields `plan_review`, `task_review`, `gap_detection`. But Section 5.6 (Iteration Tracking in state.yaml) uses `iterations` with a much richer structure including `current`, `max`, and `history` per loop. These are two different schemas for the same file.

The `pipeline-state.yaml` also uses `current_stage` as an enum value but the allowed values are never enumerated.

**Recommendation:** Consolidate into one schema. The richer version from Section 5.6 is better. Add a Pydantic model for `pipeline-state.yaml` (currently missing from the SCHEMAS registry).

### M6. Expert panel cost and latency are not bounded

The expert panel (Section 4.2.1) runs up to 7 parallel Sonnet agents in Round 1, then 7 again in Round 2, then 1 Opus agent for synthesis. That is potentially 15 agent invocations just for planning, before a single line of code is written. For a simple feature, this is massive overhead.

There is no skip heuristic. The spec says "for non-trivial features" but does not define what triggers the panel vs. a solo planner run. No cost estimate is provided. At ~$0.05-0.15 per Sonnet invocation and ~$0.50-1.00 per Opus invocation, the panel could cost $5-10 per planning cycle, and plans can iterate 3 times.

**Recommendation:** Define a concrete trigger heuristic (e.g., feature touches >3 modules, >10 files, or user requests it). Add a `--no-panel` flag. Add a cost estimate to the spec.

### M7. No specification for how the dispatcher actually assembles prompts

`context/builder.py` is listed in the component diagram but never specified. The agent definitions show what agents receive ("You receive: a task description, relevant file paths...") but the dispatcher must assemble these prompts from YAML artifacts, git diffs, and configuration. This prompt assembly logic is the glue between the file-based artifact system and the agents, and it is entirely unspecified.

Questions:
- How much of the codebase does the planner receive? The full tree? A curated subset?
- How does the executor get "a structured plan (YAML)"? Is it the full plan or the relevant phase?
- The context bridge (Section 9) is a partial answer but only covers cross-stage continuity, not initial prompt construction.

**Recommendation:** Specify prompt templates or assembly rules for each agent invocation at each stage. The `ContextBridge` class is a start; extend it to cover all invocation points.

---

## Minor Issues (nice to fix)

### m1. Polling interval inconsistency
Section 2.3 says "2 seconds for active tasks, 10 seconds for idle monitoring." But the dispatcher uses `subprocess.run` (blocking call, Section 9), which means it waits for the agent to complete -- there is no polling during agent execution. Polling only applies to monitoring external state changes. Clarify when polling is actually used vs. when the dispatcher blocks on subprocess completion.

### m2. The `opusplan` alias is undefined
Section 2.2.1 lists `opusplan` as a "composite" alias that "uses opus during plan mode and sonnet during execution" but this is never referenced again and no implementation is suggested. Either define it or remove it.

### m3. Acceptance criteria schema mismatch between Section 5.4 and Section 6.1
Section 5.4 (Task Schema) defines `acceptance_criteria` as a flat list with `id, description, verification, test_command`. Section 6.1 defines a categorized structure with `functional`, `structural`, `behavioral`, `qualitative`, `regression` groups and a `completion_gate`. These are two different schemas for the same data.

### m4. The `--bare` flag usage is unclear
`ClaudeSession.invoke()` checks `invocation.bare_mode_off` to conditionally add `--bare`. The intent is unclear -- `--bare` suppresses system prompt formatting. When should it be on vs. off? This should be documented per agent type.

### m5. `ArtifactVersioner.latest_version` has a sorting bug
The method sorts by `glob.glob` filename string order, not by version number. `plan-v10.yaml` sorts before `plan-v2.yaml` alphabetically. Use the numeric extraction already implemented in `all_versions` instead.

### m6. Missing Pydantic models for several artifact types
The SCHEMAS registry covers 6 types (`plan`, `execution_result`, `review`, `test_result`, `gap_report`, `docs_report`) but the spec defines 10 YAML schemas (Section 5.4): intent, plan, review, task, task manifest, execution plan, execution log, quality report, gap report, pipeline state. Four types have YAML schemas but no Pydantic validation.

### m7. Hook exit codes
The pre_tool_use.py hook uses `sys.exit(2)` for block decisions and `sys.exit(0)` for allow. But the standard Claude Code hook protocol may not use exit codes this way -- it may only inspect stdout JSON. Verify against actual Claude Code hook contract.

### m8. The `/xpatcher:pipeline` skill shells out to the CLI
The `pipeline` skill runs `xpatcher start "$ARGUMENTS"` via Bash. This means invoking Claude Code, which invokes a skill, which invokes the CLI, which invokes Claude Code again. This recursive invocation will likely fail or create confusion. This skill should probably just print instructions rather than actually invoking the dispatcher.

---

## Inconsistencies Found

| # | Location A | Location B | Inconsistency |
|---|-----------|-----------|---------------|
| 1 | Sec 3.4: plan review max iterations = 3 | Sec 5.6: `plan_review.max: 5` | Iteration cap is 3 or 5? |
| 2 | Sec 4 reviewer prompt: severity `critical, major, minor, nit` | Sec 5.4 review schema: severity `major, minor, suggestion` | Sec 9 Pydantic: `critical, warning, suggestion, note` -- three different enum sets |
| 3 | Sec 4 reviewer prompt: confidence `low, medium, high` | Sec 5.4 review schema: confidence `float 0.0-1.0` | String enum vs float |
| 4 | Sec 4 planner prompt: task IDs `task-1.1` | Sec 5: task files `task-001-<slug>.yaml` | Dotted vs zero-padded numbering |
| 5 | Sec 3.1 diagram: quality loop = `simplify -> test -> review` | Sec 6.4: simplification runs "after each task passes review" | Before or after review? |
| 6 | Sec 5.4 pipeline state: field `iteration_counts` | Sec 5.6 iteration tracking: field `iterations` | Different field names for same concept |
| 7 | Sec 7.4 settings.json: `maxRetries: 2` | Sec 9: `MAX_FIX_ATTEMPTS = 2` | Sec 3.4: max per-task quality iterations = 5. Are these the same concept? |
| 8 | Sec 4 executor: outputs `files_modified` + `files_created` | Sec 9 Pydantic: `ExecutionOutput` has only `files_modified` | Missing `files_created` field in Pydantic |
| 9 | Sec 2.4 state machine: states include `CHANGES_REQUESTED` | Sec 3.3 transition table: no transition produces `CHANGES_REQUESTED` | State exists in diagram but has no entry path in transition table |
| 10 | Sec 10 risk table: plan review iteration cap = 3 with "strategy switching" | Sec 3.4: escalate to human on limit | Strategy switching vs human escalation |
| 11 | Sec 4.4 reviewer: category includes `completeness, testability, simplicity` | Sec 9 Pydantic: category is `security, performance, correctness, style, architecture` | Categories do not overlap |
| 12 | Sec 7.4: `.xpatcher/` should be added to `.gitignore` | Sec 2.6: commit messages reference `.xpatcher/` artifact paths | If `.xpatcher/` is gitignored, commit body references to artifacts will point to files not in git |

---

## Happy Path Dry-Run

Walking through a complete pipeline for a feature "Add session-based authentication" on a Node.js project.

**Stage 1 -- Intent Capture**

The user runs `xpatcher start "Replace JWT auth with session-based auth"`. The dispatcher:
1. Generates pipeline ID `xp-20260329-f4a1`
2. Creates `.xpatcher/session-auth/`
3. Invokes the planner agent with the raw text

**Issue:** The intent capture stage is described as producing `intent.yaml` with parsed goal, scope, and constraints. But the spec does not show the planner being prompted to produce an intent artifact vs. a plan artifact. There is no separate "intent agent" -- the planner does both. The dispatcher must issue two sequential prompts to the planner (one for intent parsing, one for planning), or the planner must produce both artifacts in one invocation. Neither is specified.

**Stage 2 -- Planning**

Assuming intent is captured, the planner reads the codebase (via Read, Glob, Grep, Bash read-only) and produces `plan-v1.yaml`.

**Issue:** The expert panel activation heuristic is undefined. Does it run? Assume it does. 7 Sonnet agents fire in parallel (Round 1). They produce domain analyses. Round 2 fires 7 more. Opus synthesizes into a plan. Total: ~15 API calls just for planning.

The planner outputs a YAML plan. The dispatcher runs YAML extraction. Let's say the planner wraps it in a code block (agents often do this despite instructions). Strategy 3 (code block extraction) catches it. Pydantic validation runs. The plan task IDs use `task-1.1` format (as shown in the prompt). The Pydantic model validates `^task-\d+\.\d+$`. **This passes** -- but downstream, the task breakdown (Stage 6) will produce task files named `task-001-*.yaml`. The ID format mismatch becomes a runtime problem when the execution plan tries to cross-reference plan task IDs with task file IDs.

**Stage 3-5 -- Plan Review, Fix, Approval**

The reviewer agent is invoked. Per the session reuse matrix, it `--resume`s the planner session. **Problem (C4):** The reviewer now sees the planner's entire reasoning chain. If the planner considered and rejected approaches, the reviewer is biased by that reasoning. More practically, can `--resume` switch from `--agent planner` to `--agent reviewer`? This is unverified.

Assume the reviewer produces `plan-review-v1.yaml` with verdict `approved`. The dispatcher prompts the human for approval. The human approves.

**Stage 6-8 -- Task Breakdown and Review**

The planner decomposes the plan into tasks. Tasks are written to `tasks/todo/task-001-session-store.yaml`, etc. The task manifest is produced.

**Issue:** The planner agent produces task YAML, but the task schema (Section 5.4) has fields like `manifest_ref`, `assigned_agent`, `branch`, `started_at`, `completed_at`, `iteration_count` that are dispatcher-managed, not planner-produced. The dispatcher must enrich planner output with these fields. This enrichment logic is not specified.

Task review runs (reviewer agent), approved. No issues.

**Stage 9-10 -- Prioritization and Execution Graph**

The dispatcher (Python, not an agent) constructs the DAG from `depends_on` fields, runs topological sort, identifies critical path, creates batches. `execution-plan.yaml` is produced.

**This is clean.** The `TaskDAG` class is well-specified for this step.

**Stage 11 -- Parallel Execution**

Batch 1 runs: `task-001` (no dependencies). The dispatcher creates a git worktree:
```
git worktree add .xpatcher/worktrees/TASK-001 -b xpatcher/session-auth/TASK-001
```

The executor agent is invoked with `cwd` set to the worktree. It reads the task spec, implements code, commits.

Batch 2 runs: `task-002` and `task-003` in parallel. Two worktrees, two executor agents.

**Problem (C2):** Both `task-002` and `task-003` complete. Their code is on branches `xpatcher/session-auth/TASK-002` and `xpatcher/session-auth/TASK-003`. How do they get merged to the feature branch `xpatcher/session-auth`? The spec says nothing. If both tasks modify `src/auth/index.ts` (likely for an auth feature), there will be merge conflicts. No resolution strategy exists.

**Stage 12-13 -- Per-Task Quality**

For each completed task, the quality loop runs: simplify, test, review. The simplifier is invoked on the worktree.

**Problem (M4):** The simplifier needs to run tests after each commit but has read-only Bash. Either the dispatcher runs tests (adding thickness to the thin dispatcher) or the simplifier cannot verify its own changes.

The tester writes tests. The reviewer reviews. Assume all pass on first iteration.

**Stage 14 -- Gap Detection**

The gap detector is invoked with the full plan, all execution logs, reviews, and test reports. It produces `gap-report-v1.yaml` with verdict `complete`.

**Issue:** The gap detector receives "the current git diff" per its agent definition (Section 4.7). But which diff? The feature branch vs. main? Each task branch vs. feature? If worktree merges have not happened yet (C2), the feature branch may not have all the code. The gap detector would analyze incomplete state.

**Stage 15-16 -- Documentation and Completion**

The tech-writer updates docs. The human approves completion. The dispatcher pushes the feature branch and optionally creates a PR.

**Overall assessment of happy path:** The architecture broadly works for a linear, non-parallel case. Parallel execution introduces merge complexity that is unaddressed. The schema inconsistencies (C3) will cause validation failures on the first real run. The session reuse strategy (C4) needs empirical validation against the Claude CLI.

---

## Failure Path Dry-Run

Walking through a pipeline where things go wrong.

**Scenario:** Feature "Add rate limiting middleware." Task-005 (implement rate limiter logic) fails repeatedly.

**Stage 12, Iteration 1:**

The executor completes task-005. The reviewer finds two critical issues: (1) no TTL on rate limit counters, (2) race condition on concurrent requests. Verdict: `request_changes`. The dispatcher enters fix iteration.

**Fix iteration 1:**

The dispatcher resumes the executor session (per session reuse matrix: "Task Reviewer -> Task Fixer: `--resume` executor session + inject review findings"). The executor fixes both issues and commits.

**Stage 12, Iteration 2:**

The reviewer runs again. It finds a new issue: the TTL fix introduced a memory leak in the timer cleanup. Verdict: `request_changes`.

**Oscillation detection check:** The dispatcher hashes the finding set. Hash is different from iteration 1 (new finding). No oscillation detected. Continue.

**Fix iteration 2:**

The executor fixes the memory leak. But in doing so, it reintroduces the race condition from iteration 1 (classic oscillation pattern, but the finding set hash is different because the description includes different line numbers).

**Stage 12, Iteration 3:**

The reviewer catches the reintroduced race condition. The hash is different from iteration 1 (different line numbers, slightly different description). Oscillation detection does not trigger. Verdict: `request_changes`.

**Problem:** The oscillation detection hashes "the set of active findings." But findings are natural-language descriptions with line numbers that shift between iterations. Two semantically identical findings will have different hashes if line numbers change. The oscillation detector is effectively disabled for any non-trivial code change.

**Fix iteration 3 and beyond:**

With per-task quality cap at 5 (Section 3.4), this can continue for 2 more iterations. But each iteration costs an executor invocation (~$0.10-0.50) and a reviewer invocation (~$0.50-1.00), plus the fix attempt. Five iterations at $1-2 each = $5-10 for a single task failure loop.

**Iteration 5 (cap reached):**

The task is marked `stuck`. It is moved back to `tasks/todo/`. The pipeline continues with other tasks.

**Problem (M3):** Task-006 depends on task-005. The spec says stuck tasks are moved to `todo/` and the pipeline reports them. But the `TaskDAG` class only transitions dependents to READY when dependencies are SUCCEEDED. Task-006 will remain BLOCKED forever. The pipeline cannot complete.

The failure output (Section 3.6) shows "task-009: Blocked by task-005 (dependency) [waiting]" which is correct. But the spec does not define what happens to the pipeline as a whole. Does it:
- Wait indefinitely for the human to fix task-005?
- Complete with partial results (contradicts the project decision "no partial feature delivery")?
- Automatically restructure the DAG to skip task-005's dependents?

**Problem:** The `xpatcher skip task-005,008` command shown in the failure output is not defined in the CLI commands (Section 7.1). It appears only in the failure output mockup. There is no specification for what "skip" means for the DAG -- does it mark dependents as cancelled? Does it remove the dependency edge?

**Persistent validation failure:**

Separately, suppose the reviewer agent produces malformed YAML on every attempt (context pollution). The `MalformedOutputRecovery` retries twice in the same session, then `handle_persistent_validation_failure` fires. For task-level agents, it marks the task as `failed` and expects "next loop iteration will retry with a fresh agent session." But this is not connected to the iteration counter. Does a malformed output count as a quality iteration? If not, a task could theoretically retry indefinitely: 5 quality iterations x 2 malformed retries each x fresh session retry = 15+ agent invocations for one task. The circuit breakers (token budget, cost budget) are deferred to v2. There is no hard stop.

**Human escalation:**

The pipeline enters `waiting_for_human`. The human reviews the stuck tasks, fixes task-005 manually, and runs `xpatcher resume xp-20260329-f4a1`. The resume logic (Section 2.7) reads `pipeline-state.yaml`, checks if the base branch changed, and continues.

**Problem:** The resume logic checks for base branch changes but does not account for the human's manual fix on the feature branch. If the human committed directly to the feature branch (fixing task-005's code), the dispatcher does not know that task-005 is now fixed. It still shows as `stuck` in `pipeline-state.yaml`. The human must manually update the task status (not specified how) or the dispatcher must detect new commits and re-evaluate.

**Overall assessment of failure path:** The failure detection (iteration caps, oscillation) is well-designed in principle but the oscillation detection has a semantic weakness. The recovery path has significant gaps: no `skip` command specification, no handling of blocked dependents when a task is stuck, and no mechanism for the human to signal that a manual fix resolves a stuck task.

---

## Missing Components

1. **Worktree merge strategy** -- the central missing piece. How parallel task branches merge to the feature branch. Conflict detection, resolution, and rollback.

2. **Prompt assembly specification** -- `context/builder.py` is listed but never defined. This is the critical bridge between artifacts and agents.

3. **Semantic validation rules** -- Stage 3 of the validation pipeline is described but not implemented or specified.

4. **Intent capture workflow** -- Stage 1 produces `intent.yaml` but no agent or prompt is specifically designated for intent parsing vs. planning.

5. **Cancellation and cleanup** -- `xpatcher cancel` is listed as a CLI command but the cancellation workflow (kill running agents, clean up worktrees, update state, preserve partial work) is not specified.

6. **Task skip/unblock mechanism** -- referenced in failure output but not defined.

7. **Pipeline state `current_stage` enum** -- the allowed values are never defined, leaving implementors to invent their own.

8. **Configuration schema** -- `config.yaml` is referenced extensively but no schema is provided. Fields are scattered across multiple sections.

9. **Error taxonomy** -- there is no classification of errors (transient vs. permanent, retriable vs. fatal). The retry logic (`retry.py`) is listed but has no specification beyond "exponential backoff."

10. **Timeout specification per agent** -- `SessionAwareDispatcher._timeout_for(agent_type)` is referenced but never defined. What is the timeout for a planner? An executor? A reviewer?

11. **Pipeline-state.yaml Pydantic model** -- the mutable singleton has no Pydantic validation, unlike all other artifacts. This is the most frequently written file and the most critical for crash recovery.

---

## Over-Engineering Concerns

1. **The expert panel is likely overkill for v1.** Running up to 15 agent invocations for planning adds cost, latency, and implementation complexity without proven benefit. A simpler approach: the planner uses a checklist-driven multi-perspective analysis within a single Opus invocation. The panel can be added in v2 once the basic pipeline is validated.

2. **Session lineage tracking with `max_lineage_depth: 5`.** Session chaining is elegant but operationally complex. For v1, a simpler model -- fresh session per agent with context bridge -- would be more predictable. The "context bridge" pattern already handles the information transfer. Session resumption should be limited to the malformed-output fix case where it is clearly necessary.

3. **Four YAML extraction strategies with fallback.** In practice, if agents are properly prompted with "start with --- on its own line, do NOT wrap in code blocks," two strategies (raw parse, separator) should cover >95% of cases. The code-block and strip-prose strategies are defensive but add maintenance surface. Keep them but do not over-invest in testing them.

4. **Collusion prevention metrics** (alert if first-pass approval rate >80% over last 20 tasks). This requires a metrics storage and alerting system that is not otherwise needed in v1. A simpler approach: log the approval rate in pipeline completion output and let the human notice patterns.

5. **Mutation testing as a pipeline gate.** Already marked optional, which is good. Even optional, the integration surface (installing mutation testing tools per language, parsing results, defining kill rate thresholds) is substantial. Defer entirely to v2.

6. **Transcript storage** (`store_transcripts: true` in session config). Full session transcripts will be enormous. The JSONL agent logs already capture tool calls and text output. Transcripts are redundant with logs for debugging purposes.

---

## Questions for Product Owner

1. **Worktree merge strategy priority:** Is parallel execution a hard requirement for v1, or can the first version run tasks sequentially on the feature branch (eliminating C2) and add parallel execution in v2?

2. **Expert panel scope:** Can the expert panel be deferred to v2? A single-planner-agent approach with structured multi-perspective prompting would dramatically reduce implementation surface.

3. **Cost tracking:** Section 10.4 defers cost budgets to v2, but Section 10.3 lists a "Cost circuit breaker" that "pauses all agents." Which is it? If cost tracking is v2, the cost circuit breaker cannot exist in v1.

4. **Schema authority:** When agent prompts, YAML schemas, and Pydantic models disagree, which is authoritative? Recommendation: Pydantic models are the source of truth (they are code), and agent prompts must be generated/validated against them.

5. **`.xpatcher/` in git:** The spec says add `.xpatcher/` to `.gitignore`, but commit messages reference artifact paths. Should `.xpatcher/` artifacts be committed to the feature branch for auditability, or are they truly transient? This affects whether team members reviewing the PR can see the planning artifacts.

6. **Task ID format:** `task-1.1` (plan-scoped, dotted) vs `task-001` (global, sequential). Which should be canonical? The dotted format preserves phase-task hierarchy; the sequential format is simpler and avoids collisions if phases change.

7. **Human gate timeout behavior:** The task review soft gate "auto-proceeds after 30 minutes." Does this mean unapproved task reviews are treated as approved after 30 minutes? If so, this undermines the review quality guarantee. Define explicitly what auto-proceed means.

---

## Recommendations

1. **Fix C1-C4 before Phase 1 begins.** The state machine, merge strategy, schema consistency, and session semantics are foundational. Building on inconsistent foundations will create cascading rework.

2. **Simplify for v1: sequential task execution.** Run tasks in dependency order, one at a time, on the feature branch. This eliminates worktree management, merge conflicts, and the concurrency model entirely. Add parallelism in v2 when the core pipeline is proven. This is the single highest-ROI simplification.

3. **Create a canonical schema reference.** One document (or one Python module) that defines every field name, every enum value, and every ID format. Generate agent prompt output-format sections from this reference. This eliminates C3 permanently.

4. **Implement the prompt builder specification.** Define what each agent receives at each stage as a concrete prompt template with placeholders. This is the most impactful unwritten section for implementation velocity.

5. **Add a `pipeline-state.yaml` Pydantic model** and include it in the SCHEMAS registry. This is the most critical file for crash recovery and it has no validation.

6. **Validate Claude CLI session semantics empirically.** Before committing to the session reuse matrix, verify: (a) can `--resume` switch agents, (b) what happens to system prompts on resume, (c) does `--resume` with `--agent` actually work. Adjust the session strategy based on findings.

7. **Defer the expert panel to v2.** Use a single planner invocation with structured multi-perspective analysis (the "Critical Thinking Protocol" already instructs agents to consider alternatives). Add the full panel when the basic pipeline is working.

8. **Define the `xpatcher skip` command** and the DAG restructuring it triggers. Without this, any stuck task with dependents makes the pipeline permanently incomplete.

9. **Add a walkthrough section to the spec** showing the exact CLI commands, file contents, and dispatcher behavior for a simple 3-task feature. This "worked example" would have caught most of the inconsistencies identified in this review.

10. **Schedule a focused 1-day spec revision** addressing the critical and major issues before implementation begins. The spec is 90% there; the remaining 10% is load-bearing.

# xpatcher Design Specification -- System Architect Review

**Date:** 2026-03-29
**Reviewer:** System Architect (independent post-review audit)
**Spec Version:** 1.2 (Final Draft, 2026-03-28) + Supplements 13-17 (2026-03-29)
**Documents Reviewed:** All 17 design documents (01-17) + consolidated review (00)
**Review Focus:** v1 implementation readiness, architectural soundness, dry-run simulation

---

## VERDICT: CONDITIONALLY READY

The specification is architecturally sound and unusually thorough for a design document. The thin-dispatcher + Claude Code agent architecture is viable and well-motivated. The previous 7-expert review has been addressed comprehensively. However, this independent audit identifies 2 critical gaps that block implementation, 5 major concerns that carry high risk if ignored, and several minor observations. The critical gaps are narrow and fixable in 1-2 days of spec work.

---

## CRITICAL GAPS (Block Implementation)

### CRIT-A: Appendix A (Section 12) Schemas Contradict Section 9 Canonical Schemas

**Location:** `12-appendices.md` vs `09-dispatcher-internals.md` (lines 446-731)

The appendix note on line 9 says "Where any discrepancy exists between this appendix and Section 9, Section 9 wins." This is necessary but insufficient. The schemas in Section 12 still contain concrete contradictions that will confuse implementors:

| Field | Section 12 (Appendix A) | Section 9 (Canonical) |
|-------|------------------------|-----------------------|
| `PlanPhaseTask.id` pattern | `^task-\d+\.\d+$` (dot-separated) | `^task-[A-Z]?\d{3}$` (zero-padded) |
| `ReviewFinding.severity` | `critical \| warning \| suggestion \| note` | `critical \| major \| minor \| nit` |
| `ReviewFinding.category` | 5 values (missing `completeness`, `testability`) | 7 values |
| `ExecutionOutput.files_modified` | Field named `files_modified` | Field named `files_changed` |
| `GapOutput.gaps` | `list[dict]` (untyped) | `list[GapFinding]` (typed model) |

**Risk:** An implementor who starts from the appendix (which appears first in their reading order and has complete, copy-pasteable code) will produce code that rejects valid agent output. The validator will fail on the first real pipeline run.

**Fix required:** Either (a) delete the Appendix A code entirely and replace it with a pointer to Section 9, or (b) regenerate the appendix from the canonical models. Option (a) is safer and simpler. The appendix header disclaimer is not enough -- implementors copy code, not disclaimers.

### CRIT-B: `claude -p` Invocation Assumptions Are Untested and Potentially Incorrect

**Location:** `09-dispatcher-internals.md` (lines 17-85), `11-implementation-roadmap.md` (lines 197-201)

The entire dispatcher depends on invoking `claude -p <prompt> --output-format json --agent <name> --plugin-dir <path>`. The open questions section (Section 10) explicitly acknowledges that `--agent` and `--plugin-dir` flags may not exist. The resolution report (Doc 17, OQ-1 and OQ-2) marks these as "non-blocking" with workarounds, but the workarounds are vague:

- OQ-1 workaround: "use `-p` + agent-specific system prompts." This fundamentally changes the architecture -- without `--agent`, every agent definition in Section 4 (the `.md` frontmatter with model, tools, maxTurns, disallowedTools, memory) is inert. The dispatcher would need to replicate all of this in the `-p` prompt and CLI flags.
- OQ-2 workaround: "symlink `.claude-plugin/` per project." This breaks the core installation model (Section 2.3.1) where one install serves multiple projects.

Additionally, the `ClaudeSession.invoke()` code assumes:
1. `--output-format json` returns a JSON envelope with `session_id` and `result` fields. The actual envelope schema is not cited from Claude Code documentation.
2. `--resume <session_id>` continues a prior session. Resuming with a different `--agent` flag (which happens during plan-fix iterations) may or may not work.
3. `--bare` suppresses conversational preamble. Its interaction with `--agent` is unspecified.
4. `--max-turns` is available as a CLI flag.
5. `--allowedTools` accepts a comma-separated list.

**Risk:** If any of these assumptions are wrong, the dispatcher cannot invoke agents. This is the single most load-bearing integration point in the system.

**Fix required:** Before implementation begins, run a validation script that tests each CLI flag combination against the actual `claude` binary. Document the exact JSON envelope schema from real output. If `--agent` does not exist, the spec must define the fallback mechanism explicitly (system prompt injection, tool restriction via hooks only, etc.) -- not hand-wave it as "works with `-p`."

---

## MAJOR CONCERNS (High Risk If Ignored)

### MAJ-A: Semantically Correct But Functionally Wrong Agent Output Has No Catch

**Observation:** The validation pipeline (Section 7.7 + Section 13 Component 3) handles three failure modes: (1) malformed YAML, (2) schema violations, (3) semantic cross-reference errors (file paths, task IDs, commit hashes). This is good.

But there is a fourth failure mode with no mitigation: the agent produces valid, schema-compliant, semantically consistent YAML that is **functionally wrong**. Examples:

- The executor claims `status: completed` and lists real files in `files_changed`, but the implementation is subtly incorrect (wrong algorithm, missing edge case, security flaw).
- The reviewer produces `verdict: approve` with `confidence: high` and zero findings, but the code has a critical bug the reviewer missed.
- The gap detector says `verdict: complete` but missed a requirement because it hallucinated that a completed task covered it.

The spec relies on the review/test/gap pipeline to catch these, but the pipeline itself is composed of agents that can all independently fail silently. The oscillation detection (hash-based) catches flip-flopping but not consistent agreement on a wrong answer.

**Recommendation:** Add a "canary assertion" mechanism: for each task, the planner generates one assertion that should be false if the feature is implemented correctly (e.g., "a request to /api/sessions without credentials should return 200"). The tester runs these negative assertions. If the canary passes (the assertion is true when it should be false), the task is flagged. This is mentioned briefly in Risk Mitigation (Section 10, "canary bugs") but never specified in the testing pipeline or tester agent prompt. Formalize it.

### MAJ-B: File-Based Coordination Assumes Agents Respect Output Format Instructions

**Observation:** The entire system depends on agents producing YAML as their text output. Agents are instructed: "Start with --- on its own line. Do NOT wrap in \`\`\`yaml\`\`\` code blocks. Do NOT include prose before or after." The `_extract_yaml` method has 4 fallback strategies (raw, separator, code block, strip prose) to handle non-compliance.

In practice, Claude models frequently violate output format instructions, especially:
- Prefixing YAML with explanatory prose ("Here is the plan:")
- Wrapping in code fences despite being told not to
- Appending prose after the YAML ("Let me know if you'd like changes")
- Producing partial YAML when hitting token limits

The 4 fallback strategies handle the first three. The fourth (partial YAML from token exhaustion) will silently produce a truncated dict that passes YAML parsing but fails Pydantic validation (missing required fields). The malformed output recovery then retries in the same session -- but if the session is already near context limits, the retry will also truncate.

**Recommendation:** Add a `maxTurns` check before retry. If the agent used all available turns, retry with a fresh session and a shorter prompt (context bridge only), not `--resume`. Also consider using `--output-format stream-json` to detect token exhaustion in real-time rather than discovering it after the fact in the parsed output.

### MAJ-C: The `PENDING -> READY` Transition Allows Skipped Dependencies

**Location:** `02-system-architecture.md` (Section 2.5, transition table, line 337)

The transition table says:
```
PENDING -> READY: All dependencies SUCCEEDED or SKIPPED
```

This means if Task B depends on Task A, and Task A is skipped, Task B becomes READY and executes. But Task B was designed with the assumption that Task A's output exists. For example, if Task A creates a database schema and Task B writes queries against it, skipping A means B executes against a non-existent schema.

The spec says the gap detector (Stage 14) "is informed of skipped tasks and includes them in its coverage analysis" (Section 7.1). But the gap detector runs after all tasks complete. The damage is already done -- Task B has either failed (if the missing dependency causes a build error) or produced wrong code (if it hallucinates the schema).

**Recommendation:** When a task's dependency is `SKIPPED` (not `SUCCEEDED`), the task should transition to `READY` only if its `acceptance_criteria` do not reference output from the skipped task. Otherwise, it should transition to `BLOCKED` with a message explaining that it depends on a skipped task. This requires the planner to annotate inter-task data dependencies, not just execution-order dependencies. Alternatively, simpler: make `SKIPPED` dependencies block by default, with an explicit `--force-unblock` flag.

### MAJ-D: No Specification for How the Dispatcher Detects Agent Completion

**Location:** `09-dispatcher-internals.md` (lines 17-40)

The `ClaudeSession.invoke()` uses `subprocess.run()` with `capture_output=True`. This means the dispatcher blocks until the agent process terminates. For a 15-minute executor session, the dispatcher thread is completely blocked.

In v1 (sequential execution), this is tolerable -- one agent at a time. But the TUI (Section 7.1) promises real-time progress updates, elapsed timers, and agent log streaming during execution. A blocking `subprocess.run()` cannot support any of this.

The TUI section mentions "`asyncio.subprocess`" for log streaming (Section 7.1, line 205) but the `ClaudeSession` code uses synchronous `subprocess.run()`. These are incompatible.

**Recommendation:** Specify whether the dispatcher uses synchronous or asynchronous subprocess management. If async (required for the TUI), rewrite `ClaudeSession` to use `asyncio.create_subprocess_exec` and define how stdout/stderr streaming integrates with the TUI renderer. If sync, acknowledge that real-time log streaming and elapsed timers are not possible during agent execution in v1.

### MAJ-E: Plan Review Uses Same Agent Definition As Task Review, Creating Confusion

**Location:** `04-agent-definitions.md` (Section 4.4)

The reviewer agent is used for plan review (Stage 3), task manifest review (Stage 7), and per-task code review (Stage 12). These are fundamentally different activities:

- Plan review evaluates architectural feasibility, scope completeness, and risk assessment of a plan document.
- Task review evaluates code correctness, security, and style of a git diff.

The reviewer agent prompt (Section 4.4) is entirely oriented toward code review ("Check the git diff for debugging artifacts", "Run the tests yourself via Bash"). When this agent is invoked for plan review, it will attempt to run tests and look at git diffs -- neither of which exists at that stage.

The `PromptBuilder` (Section 7.9) handles this by providing different context per stage, but the agent's system prompt and checklist remain code-review-oriented regardless of stage.

**Recommendation:** Either (a) create a separate `plan-reviewer.md` agent definition with a plan-specific checklist (architectural soundness, scope coverage, risk assessment, task granularity), or (b) add a `review_mode` parameter to the reviewer prompt that switches the checklist. The current design will produce low-quality plan reviews because the agent is not primed for that task.

---

## MINOR OBSERVATIONS

### MIN-1: `ArtifactVersioner.latest_version()` Relies on Lexicographic Sort

**Location:** `05-artifact-system.md` (line 260)

`sorted(glob.glob(pattern))` sorts filenames lexicographically. This means `plan-v10.yaml` sorts before `plan-v2.yaml` (because "1" < "2" in string comparison). For plans with 10+ versions, `latest_version()` will return the wrong file.

**Fix:** Sort by extracted version number, not filename.

### MIN-2: The Simplifier Agent's Use of `/simplify` Skill Creates a Recursive Dependency

**Location:** `04-agent-definitions.md` (Section 4.6)

The simplifier agent is told to "Run `/simplify` on recently changed code." But `/simplify` is itself an xpatcher skill (defined in `08-skills-and-hooks.md`). The simplifier agent IS the agent behind the `/xpatcher:simplify` skill. If the simplifier invokes `/simplify`, it is either (a) recursively invoking itself, or (b) invoking Claude Code's built-in `/simplify` skill (which is a different thing). The spec says "native `/simplify` slash command" but this ambiguity needs explicit resolution.

### MIN-3: `BLOCKED` Appears as Both a Pipeline Stage and a Task State

**Location:** `03-pipeline-flow.md` (Section 3.2.1) and `02-system-architecture.md` (Section 2.5)

`PipelineStage.BLOCKED` and `TaskState.blocked` exist independently. A pipeline can be in `BLOCKED` stage while individual tasks are in `blocked` state, but these are unrelated concepts (pipeline is waiting on human escalation; task is waiting on a failed dependency). The naming collision will cause confusion in logs, state files, and debugging.

### MIN-4: The Hook Wrapper Path Assumes a Fixed Installation Layout

**Location:** `07-cli-and-installation.md` (Section 7.4, lines 421-428)

The `run_hook.sh` wrapper navigates from `$HOOK_DIR/../..` to find `$XPATCHER_HOME`. This assumes the hook is always at `.claude-plugin/hooks/run_hook.sh` relative to the xpatcher root. If the plugin is loaded from a different path (per-project installation, symlink), this resolution will fail.

### MIN-5: `xpatcher pending` Scans "All Known Projects" But Has No Project Registry

**Location:** `07-cli-and-installation.md` (Section 7.1, line 116)

The `xpatcher pending` command "scans all `.xpatcher/*/pipeline-state.yaml` files across known projects." But there is no project registry -- xpatcher does not track which projects it has been used with. In practice, this command can only scan the current directory (or `--project` flag), not "all known projects."

### MIN-6: Session Lineage in `sessions.yaml` Records Inherited Sessions

**Location:** `09-dispatcher-internals.md` (lines 1268-1281)

The `plan_review:reviewer` session shows `lineage: ["sess_abc123"]` (inherited from planner). But Section 7.8 explicitly states that plan review uses a fresh session with NO inheritance (adversarial isolation). The lineage should be empty. This example contradicts the session reuse decision matrix.

### MIN-7: `ROLLED_BACK` State Is Missing from `PipelineStateModel.validate_current_stage`

**Location:** `13-missing-components-architecture.md` (lines 1581-1590) vs `03-pipeline-flow.md` (line 98)

Section 3.2.1 defines `ROLLED_BACK = "rolled_back"` in the `PipelineStage` enum. But the `PipelineStateModel.validate_current_stage` validator in Section 13 (lines 1581-1590) does not include `"rolled_back"` in its valid_stages set. A rolled-back pipeline will fail validation.

### MIN-8: No Specification for Feature Slug Generation

The pipeline creates `.xpatcher/<feature-slug>/` directories but never specifies how the feature slug is generated from the user's request string. "Replace JWT auth with session-based auth" becomes "auth-redesign" in examples, but the transformation rules (lowercase, kebab-case, length limit, collision handling) are unspecified.

---

## QUESTIONS FOR THE PRODUCT OWNER

1. **Appendix cleanup priority:** Should Appendix A (Section 12) be deleted entirely, or regenerated from canonical Section 9 models? Deletion is simpler and eliminates all future drift risk. Regeneration keeps a "quick reference" but requires maintenance discipline.

2. **Claude CLI validation timeline:** Can we allocate 1 day before Phase 1 begins to run the CLI flag validation test? This will unblock or reshape the entire `ClaudeSession` implementation. If `--agent` does not exist, the fallback design needs to be specified before any agent code is written.

3. **Skipped dependency policy:** Should tasks with skipped dependencies be auto-blocked (safe, conservative) or auto-unblocked (current spec, optimistic)? The answer depends on how often you expect to use `xpatcher skip` -- if it is a rare escape hatch, auto-block is better. If it is a frequent workflow, auto-unblock with gap detection is acceptable.

4. **Plan review agent:** Is the plan review quality acceptable with the current code-review-oriented reviewer, or should a separate plan-review agent be created? A plan-review agent adds implementation complexity but significantly improves plan quality.

5. **Synchronous vs async dispatcher:** Is real-time TUI streaming a v1 requirement, or can v1 use a simpler synchronous dispatcher with batch progress updates between agent invocations? Async adds significant complexity to the dispatcher core.

6. **Cost guardrails for v1:** Section 8.4 defers cost tracking entirely to v2. A single pipeline can easily consume $50-200 in API calls (planner Opus, reviewer Opus, expert panel). Should v1 have even a basic "warn at $X spent" mechanism, or is manual monitoring acceptable?

---

## DRY-RUN FINDINGS

### Scenario: "Add rate limiting to the /api/users endpoint"

Tracing through the pipeline from UNINITIALIZED to DONE, targeting a small Python Flask application.

**Stage 1 - Intent Capture (UNINITIALIZED -> INTENT_CAPTURE)**
- User runs: `xpatcher start "Add rate limiting to the /api/users endpoint"`
- Dispatcher creates `.xpatcher/rate-limiting/` directory
- **Question:** How is `rate-limiting` derived from the user's string? No slug generation spec exists (MIN-8).
- Dispatcher invokes planner with `_build_intent_analysis` prompt
- Planner analyzes codebase, finds Flask app with `/api/users` endpoint in `src/routes/users.py`
- **Clear path:** Planner produces `intent.yaml` with `status: ready`. No Q&A needed.
- Transition: INTENT_CAPTURE -> PLANNING

**Stage 2 - Planning (PLANNING)**
- Dispatcher invokes planner with `_build_planner` prompt
- Planner explores codebase, identifies Flask-Limiter is already a dependency but unused
- Planner determines this is "Simple" complexity (2-3 tasks, single module) -> no expert panel
- Planner produces `plan-v1.yaml` with 3 tasks:
  - task-001: Add rate limiter configuration
  - task-002: Apply rate limits to /api/users endpoints
  - task-003: Add rate limit exceeded error handling
- **Concern:** Planner must output task IDs matching `^task-[A-Z]?\d{3}$`. The planner prompt says "Task IDs use format `task-NNN` (zero-padded, e.g. `task-001`)." This is clear.
- Transition: PLANNING -> PLAN_REVIEW

**Stage 3 - Plan Review (PLAN_REVIEW)**
- Dispatcher invokes reviewer with `_build_reviewer` prompt
- **Problem (MAJ-E):** The reviewer agent prompt is code-review-oriented. It will try to "Run the tests yourself (via Bash)" and "Check the git diff for debugging artifacts." Neither of these exist yet -- this is a plan, not code. The reviewer may produce a confused review or a vacuous "approve" because its checklist does not apply to plans.
- Assume reviewer produces `verdict: approved`
- Transition: PLAN_REVIEW -> PLAN_APPROVAL

**Stage 5 - Plan Approval (PLAN_APPROVAL)**
- Human gate: terminal bell, structured prompt with approve/reject/defer
- **Smooth path.** This is well-specified.
- Human approves.
- Transition: PLAN_APPROVAL -> TASK_BREAKDOWN

**Stage 6 - Task Breakdown (TASK_BREAKDOWN)**
- Dispatcher invokes planner to decompose plan into task YAMLs
- **Ambiguity:** The plan (Stage 2) already contains tasks with IDs and acceptance criteria. Is Stage 6 producing identical tasks, or refining them? The spec says Stage 6 produces "tasks with ACs, deps, file scope." But the plan already has tasks with ACs and deps. The boundary between "plan tasks" and "execution tasks" is unclear.
- Assume planner produces 3 task YAML files in `tasks/todo/`
- Transition: TASK_BREAKDOWN -> TASK_REVIEW

**Stage 7 - Task Review (TASK_REVIEW)**
- Reviewer reviews task manifest for granularity, ACs, deps
- **Same problem as MAJ-E:** reviewer checklist is code-oriented, not task-review-oriented.
- Assume approved.
- Transition: TASK_REVIEW -> PRIORITIZATION

**Stage 9 - Prioritization (PRIORITIZATION)**
- Dispatcher (not an agent) orders tasks and creates `execution-plan.yaml`
- task-001 has no deps -> batch 1
- task-002 depends on task-001 -> batch 2
- task-003 depends on task-002 -> batch 3
- **Smooth path.** DAG construction is well-specified.
- Transition: PRIORITIZATION -> EXECUTION_GRAPH

**Stage 10 - Execution Graph (EXECUTION_GRAPH)**
- Dispatcher creates rollback tags
- v1: no worktrees, no per-task branches
- Transition: EXECUTION_GRAPH -> TASK_EXECUTION

**Stages 11-13 - Task Execution + Quality Loop (TASK_EXECUTION -> PER_TASK_QUALITY)**
- Executor runs task-001 (add rate limiter config)
- Executor commits: `xpatcher(task-001): Add rate limiter configuration`
- Tester writes and runs tests for rate limiter config
- Reviewer reviews the code diff
- **Smooth path for simple tasks.** Well-specified.
- **Timing concern:** After task-001 completes quality loop, dispatcher runs regression tests (Section 6.5.1). For v1 this is `pytest` (or equivalent). If the project has a slow test suite (60+ seconds), each task adds a regression test overhead. With 3 tasks, that is 3 extra test suite runs.

**Stage 14 - Gap Detection (GAP_DETECTION)**
- All 3 tasks completed
- Gap detector analyzes plan vs implementation
- **Smooth path if no gaps.** Well-specified.
- `verdict: complete` -> transition to DOCUMENTATION

**Stage 15 - Documentation (DOCUMENTATION)**
- Tech writer updates README with rate limiting documentation
- **Smooth path.** Non-blocking on failure (Doc 14, Component 15).

**Stage 16 - Completion (COMPLETION)**
- Human gate for final review
- Pipeline pushes feature branch, optionally creates PR
- Transition: COMPLETION -> DONE

### Dry-Run Summary

The pipeline works end-to-end for this scenario, with these friction points:

1. **Feature slug generation** -- unspecified (MIN-8)
2. **Plan review quality** -- reviewer is not primed for plan review (MAJ-E)
3. **Plan-to-task boundary** -- the distinction between Stage 2 plan tasks and Stage 6 execution tasks is unclear
4. **Regression test overhead** -- sequential regression runs after each task add wall-clock time proportional to task count multiplied by test suite duration
5. **All paths smooth once execution begins** -- the executor/tester/reviewer loop for code-level work is well-designed

### Interruption Scenario: Ctrl+C During Task-002 Execution

- Single SIGINT -> graceful shutdown
- Wait 30s for current agent turn to complete
- Save pipeline state: `current_stage: task_execution`, task-001: `succeeded`, task-002: `running`
- On resume: task-002 state is reset to `READY` (if crash) or resumed via session (if graceful)
- **Smooth path.** Well-specified in Section 7.7 (SignalHandler).

### Failure Scenario: Task-002 Stuck After 3 Quality Iterations

- task-002 fails review 3 times (oscillation or persistent issue)
- task-002 transitions: RUNNING -> NEEDS_FIX -> RUNNING -> NEEDS_FIX -> RUNNING -> NEEDS_FIX -> STUCK
- task-003 transitions: PENDING -> BLOCKED (depends on stuck task-002)
- Pipeline blocks, emits terminal bell, shows structured prompt
- User runs: `xpatcher skip xp-20260329-a1b2 task-002`
- task-002: STUCK -> SKIPPED
- task-003: BLOCKED -> READY (if we use current spec's optimistic unblocking) or stays BLOCKED (per MAJ-C recommendation)
- **If task-003 runs:** It depends on task-002's rate limiting middleware being present. If that middleware does not exist because task-002 was skipped, task-003 will likely fail during execution or testing. The gap detector (Stage 14) would catch this, but only after wasting time on task-003 execution.
- **Recommendation from MAJ-C:** Default to blocking tasks with skipped dependencies. The user can force-unblock explicitly.

---

## CROSS-DOCUMENT CONSISTENCY CHECK

### Sections 9, 13, 14 Schema Alignment

| Model | Section 9 | Section 13 | Section 14 | Status |
|-------|-----------|------------|------------|--------|
| `PlanOutput` | Defined | Referenced | Referenced | Consistent |
| `ExecutionOutput` | Defined (`files_changed`) | Referenced | Referenced | Consistent |
| `ReviewOutput` | Defined (`ReviewSeverity`, `ReviewCategory`) | Semantic checks use correct enum values | Referenced | Consistent |
| `TestOutput` | Defined | Semantic checks match | Referenced | Consistent |
| `GapOutput` | Defined (`GapFinding` model) | Semantic checks match | Referenced | Consistent |
| `PipelineStateModel` | Not defined | **Defined here** (authoritative) | References `task_states` correctly | Consistent |
| `XpatcherConfig` | Not defined | **Defined here** (authoritative) | Not referenced | N/A |
| `IntentModel` | Not defined | Not defined | **Defined here** (authoritative) | N/A |
| `TaskModel` | Not defined | `TaskStateRecord` is distinct (state tracking, not task definition) | **Defined here** (authoritative) | Consistent |
| `TaskManifestModel` | Not defined | Not defined | **Defined here** (authoritative) | N/A |
| `ExecutionPlanModel` | Not defined | Not defined | **Defined here** (authoritative) | N/A |

**Cross-reference issues found:**

1. Section 12 (Appendix A) contradicts Section 9 on 5+ fields (see CRIT-A above).
2. `ROLLED_BACK` missing from `PipelineStateModel.validate_current_stage` (MIN-7).
3. Session lineage example contradicts adversarial isolation policy (MIN-6).
4. All other cross-references checked: consistent.

### v1/v2 Boundary Clarity

| Feature | v1 | v2 | Boundary Clear? |
|---------|----|----|-----------------|
| Task execution | Sequential on feature branch | Parallel with worktrees | Yes |
| Merge protocol | N/A (no merge) | `--no-ff` merge commits | Yes |
| Regression testing | Standard test suite | Full AC regression | Yes |
| Expert panel | Solo planner for simple, panel for complex | Same | Yes |
| Cost tracking | None | Budget enforcement | Yes |
| Session lineage | Fresh + context bridge | Same + optimization | **Blurry** -- `max_lineage_depth: 5` is configured for v1 but only matters for v2 |
| Mutation testing | Disabled | Opt-in | Yes |
| Worktree cleanup during cancel | Not applicable | Clean up worktrees | Yes |

**One blurry boundary:** The session management configuration (Section 7.8, `max_lineage_depth: 5`) is over-specified for v1 where lineage tracking adds complexity without benefit. For v1, session management should be: fresh session per agent invocation, `--resume` only for same-session fix retries. Context bridges are the only cross-stage continuity mechanism needed.

---

## SCALABILITY ASSESSMENT

### File-Based Coordination

For v1 (single feature, sequential execution, <20 tasks), file-based coordination is fine. The `PipelineStateFile` atomic write pattern handles the signal handler race condition. YAML read/write latency (sub-millisecond for typical state files) is negligible compared to agent invocation time (minutes).

**Scaling concerns for v2:**
- Parallel agents writing to `pipeline-state.yaml` simultaneously: handled by `threading.Lock` + atomic write. Correct but serializes updates.
- Many tasks (50+): `pipeline-state.yaml` grows linearly with task count. Each `TaskStateRecord` is ~10 lines of YAML. At 50 tasks, the file is ~500 lines -- still fast to read/write.
- Log files: one JSONL file per agent invocation. With 50 tasks and 3 iterations each, that is 150+ log files. File listing becomes slow but individual file access remains fast.

**Verdict:** File-based coordination scales to v1's constraints without issues. For v2 with parallel execution, the threading.Lock serialization may become a bottleneck during merge storms (multiple tasks completing quality loops simultaneously). Consider a lightweight SQLite backend for v2 pipeline state.

### Single-Feature Constraint

This is the right decision for v1. Multi-feature pipelines introduce cross-feature merge conflicts, shared file contention, and state management complexity that would triple the implementation effort. The constraint is clearly documented and easy to lift later because the state is fully self-contained in `.xpatcher/<feature>/`.

### Polling Intervals

2 seconds for active tasks, 10 seconds for idle. These are sensible. The polling is only for file-based state observation (e.g., `xpatcher status` reading `pipeline-state.yaml`). Active agent monitoring uses subprocess lifecycle, not polling.

---

## FINAL ASSESSMENT

The specification is the most complete SDLC automation design I have reviewed. The two-level state machine, adversarial review isolation, oscillation detection, and file-based crash recovery are well-thought-out. The 7-expert review and subsequent supplement documents (13-17) have addressed the vast majority of gaps.

The two critical issues (CRIT-A and CRIT-B) are narrow: one is a documentation cleanup, the other is a validation exercise. Neither requires architectural changes. The major concerns (MAJ-A through MAJ-E) are design refinements, not structural problems. If the Claude CLI validation (CRIT-B) confirms that `--agent` and `--plugin-dir` work as assumed, the architecture is sound and implementation can begin.

**Recommended action before Phase 1:**
1. Delete Appendix A or regenerate from Section 9 models (CRIT-A, 2 hours)
2. Run Claude CLI flag validation test (CRIT-B, 4 hours)
3. Decide on skipped-dependency policy (MAJ-C, 30 minutes)
4. Decide on sync vs async dispatcher for v1 TUI (MAJ-D, 30 minutes)

---

*End of System Architect review.*

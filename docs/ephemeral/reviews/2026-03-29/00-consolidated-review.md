# xpatcher Design Specification - Consolidated Review

**Date:** 2026-03-29
**Spec Version:** 1.2 (Final Draft, 2026-03-28)
**Review Panel:** 7 expert agents (Architecture, Platform, QA, Pipeline, Agent Design, DevOps, UX)
**Documents Reviewed:** Master proposal + 12 subdocuments (01-12)

---

## Overall Verdict

**The design is architecturally strong and ready for implementation with minor remaining items.**

The specification demonstrates excellent thinking on file-based coordination, adversarial review isolation, iteration caps with oscillation detection, and the thin-dispatcher boundary. All 6 critical issues and all 14 major issues have been resolved in a spec revision pass (2026-03-29). One critical item (CRIT-7: self-testing strategy) and several minor items remain for Phase 1-2.

**Confidence:** High that the overall architecture is sound. High that an implementor can build from this spec with minimal clarification rounds.

---

## Readiness Summary

| Area | Ready? | Blocking Issues |
|------|--------|-----------------|
| Core Architecture | ✅ Yes | ~~State machine inconsistency~~ Resolved. ~~Schema field mismatches~~ Resolved. ~~Session semantics~~ Resolved. ~~Prompt assembly~~ Resolved. ~~File locking~~ Resolved. |
| Pipeline Flow | ✅ Yes | ~~Worktree merge gap~~ Resolved. ~~Gap re-entry semantics~~ Resolved. ~~Quality loop ordering~~ Resolved. |
| Agent Definitions | ✅ Yes | ~~Prompt/schema divergence~~ Resolved. ~~Hook security gap~~ Resolved. ~~Simplifier Bash contradiction~~ Resolved. ~~Expert panel threshold~~ Resolved. |
| Quality/Testing | ✅ Yes | ~~No self-testing strategy (CRIT-7)~~ Resolved. ~~Overhead unanalyzed~~ Resolved (tiered profiles). ~~Regression testing~~ Resolved. |
| Installation | ✅ Yes | ~~install.sh breaks on macOS~~ Resolved. ~~Bare python~~ Resolved. ~~venv module~~ Resolved. ~~Uninstall/upgrade mechanism~~ Resolved. |
| CLI/TUI | ✅ Yes | ~~Missing `skip` command~~ Resolved. ~~No Ctrl+C handling~~ Resolved. ~~Plan approval blocking~~ Resolved. |
| Documentation | ✅ Yes | ~~No user docs plan, no onboarding~~ Resolved. |
| Git/DevOps | ✅ Yes | ~~Worktree merge strategy completely absent~~ Resolved. ~~Rollback strategy~~ Resolved. |

---

## Critical Issues (Must Fix Before Implementation)

These are blocking issues independently identified and corroborated by multiple expert reviewers.

### ~~CRIT-1: Git Worktree Merge Strategy is Completely Unspecified~~ ✅ RESOLVED
**Flagged by:** Architecture, Pipeline, DevOps (unanimous)
**Severity:** Design-breaking
**Resolution (2026-03-29):** Added Section 2.6.1 to system architecture spec. v1 uses sequential execution on the feature branch (no worktrees, no merge conflicts). v2 defines a full merge protocol: `--no-ff` merge commits, eager per-task merge timing, tiered conflict resolution (auto → agent → human), post-merge integration testing, atomic revert on failure, and worktree cleanup semantics. Section 2.5 updated to clarify v1 vs v2 behavior. Pipeline stages 10-11 updated to reflect both modes.

### ~~CRIT-2: Pipeline State Machine Does Not Match the 16-Stage Pipeline~~ ✅ RESOLVED
**Flagged by:** Architecture, Pipeline
**Severity:** High
**Resolution (2026-03-29):** Section 2.4 replaced with a pointer — Section 3 is now the single authoritative source for pipeline-level states. Added `PipelineStage` enum (Section 3.2.1) mapping 1:1 to all 16 stages plus terminal/meta states (`DONE`, `PAUSED`, `BLOCKED`, `FAILED`). Per-task states (`TaskState` enum + transition table) consolidated into Section 2.5 alongside the DAG scheduler. No state definition is duplicated across documents.

### ~~CRIT-3: Schema Field Names Inconsistent Across 3 Layers~~ ✅ RESOLVED
**Flagged by:** Architecture, Agent Design
**Severity:** High (every agent output will fail validation)
**Resolution (2026-03-29):** Section 9 Pydantic models are now the single canonical schema reference. All conflicts resolved:

| Concept | Canonical Value (Section 9) |
|---------|-----------------------------|
| Review severity | `ReviewSeverity`: `critical \| major \| minor \| nit` |
| Review confidence | `Confidence`: `low \| medium \| high` (Literal) |
| Review categories | `ReviewCategory`: `correctness \| completeness \| security \| performance \| style \| architecture \| testability` (7 values) |
| Task ID format | `TASK_ID_PATTERN`: `^task-\d{3}$` (zero-padded, e.g. `task-001`) |
| Executor files | `files_changed: list[FileChange]` — single list with `action: created \| modified \| deleted` |
| Gap severity | `GapSeverity`: `critical \| major \| minor` |

Section 5.4 YAML schemas replaced with a reference table to Section 9 models. Section 4 agent output format examples replaced with model references + build-time injection comments. Section 4 header notes Section 9 as authoritative.

### ~~CRIT-4: PreToolUse Hook Cannot Block Bash-Mediated File Writes~~ ✅ RESOLVED
**Flagged by:** Agent Design
**Severity:** High (undermines read-only enforcement)
**Resolution (2026-03-29):** PreToolUse hook (Section 7.6) rewritten with three-layer Bash enforcement: (1) per-agent command allowlists (`BASH_ALLOWLISTS` dict), (2) regex-based write pattern detection (redirects, `tee`, `sed -i`, `mv`, `cp`, etc.), (3) command chaining blocker (`;`, `&&`, `||`, `$()`, backticks) with safe pipe targets allowlist. Policy table updated from 5 to 7 policies.

### ~~CRIT-5: `readlink -f` in install.sh Breaks on macOS~~ ✅ RESOLVED
**Flagged by:** Platform
**Severity:** Blocker on macOS
**Resolution (2026-03-29):** Replaced `readlink -f` with `$(cd "$(dirname "$0")/.." && pwd -P)` in `bin/xpatcher` wrapper (Section 11). Also added Python >= 3.10 version validation to install.sh (was MAJ-10: printed version but never checked it).

### ~~CRIT-6: `xpatcher skip` Referenced in Output But Never Defined~~ ✅ RESOLVED
**Flagged by:** UX, Pipeline, Architecture
**Severity:** High (user hits failure, follows suggested fix, gets second failure)
**Resolution (2026-03-29):** Added `xpatcher skip` command to CLI specification (Section 7.1) with full DAG semantics: state transitions (`STUCK`/`FAILED` → `SKIPPED`), dependent task unblocking rules, pipeline resumption behavior, constraint enforcement, and `pipeline-state.yaml` recording format. Added `SKIPPED` state to `TaskState` enum (Section 2.5) with transition table entries.

### ~~CRIT-7: No Test Strategy for xpatcher Itself~~ ✅ RESOLVED
**Flagged by:** QA
**Severity:** High
**Resolution (2026-03-29):** Complete self-testing strategy defined: 8 unit test suites (90+ test scenarios), 2 integration test suites, E2E test suite with 3 sample projects, golden fixture strategy, CI configuration, and coverage targets (80-90% for dispatcher code).

---

## Major Issues (Should Fix, High Risk If Ignored)

### ~~MAJ-1: Session `--resume` Semantics Conflated with Context Bridging~~ ✅ RESOLVED
**Source:** Architecture
**Resolution (2026-03-29):** Session Reuse Decision Matrix (Section 7.8) updated: all review stages (plan review, task review, gap detection) now use context bridge + fresh session, never `--resume` from the stage being reviewed. Session inheritance map updated to enforce adversarial isolation. Section 2.7 updated to clarify session strategy on resume.

### ~~MAJ-2: Gap Detection Re-Entry Semantics Incomplete~~ ✅ RESOLVED
**Source:** Pipeline, Architecture
**Resolution (2026-03-29):** Added Section 3.4.1 "Gap Re-entry Protocol" specifying: (a) exact stages that re-run (6-14), (b) scoping rules (only new gap tasks, completed tasks untouched), (c) manifest versioning (`task-manifest-v2.yaml`), (d) gap context flow to planner, (e) depth limit enforcement (`max_gap_depth: 2`), (f) human approval gate for non-critical gap tasks, (g) pipeline state tracking for gap re-entry rounds.

### ~~MAJ-3: Per-Task Quality Loop Ordering Ambiguous~~ ✅ RESOLVED
**Source:** Pipeline, Architecture
**Resolution (2026-03-29):** Added concrete flowchart to Section 3.4: (1) test → (2) review → (3) if pass + autoSimplify, simplify → (4) re-test after simplification. One "iteration" = one test+review cycle. Simplification is a post-approval refinement step, not part of the retry loop. Max iterations standardized to 3 across all documents (Sections 2.5, 3.4, 5.4, 6.1). Diagram updated from `simplify → test → review` to `test → review → [simplify]`.

### ~~MAJ-4: Simplifier Has Read-Only Bash But Needs to Run Tests~~ ✅ RESOLVED
**Source:** Architecture, Agent Design
**Resolution (2026-03-29):** Simplifier agent (Section 4.6) updated to use Claude Code's native `/simplify` slash command. Agent now has full Bash access (not read-only) to run tests after simplification. Removed from BASH_ALLOWLISTS in hooks. Each simplification is a separate commit; test failures revert the commit.

### ~~MAJ-5: `pipeline-state.yaml` Concurrent Access During Parallel Execution~~ ✅ RESOLVED
**Source:** DevOps
**Resolution (2026-03-29):** Added Section 2.3.2 "Pipeline State File Locking" with `PipelineStateFile` class: `threading.Lock` around all writes + atomic write pattern (write to temp file, `os.rename`). Implemented in Phase 1 even though parallel execution is v2.

### ~~MAJ-6: No Ctrl+C / Signal Handling Specification~~ ✅ RESOLVED
**Source:** UX
**Resolution (2026-03-29):** Added `SignalHandler` specification to Section 7.7 (Dispatcher Internals): single SIGINT = graceful (wait 30s for current turn, save state, exit); double SIGINT within 2s = force-kill all child processes + crash recovery state; SIGTERM = same as single SIGINT. Resume behavior documented for each interruption type. Context-specific behavior table for human gate vs execution vs log viewing.

### ~~MAJ-7: Plan Approval Blocks Forever With No Notification~~ ✅ RESOLVED
**Source:** UX
**Resolution (2026-03-29):** Section 3.5 updated: hard gates now emit terminal bell (`\a`) on arrival + configurable soft timeout (default: 2 hours) after which pipeline pauses with resume message. Added `xpatcher pending` command to Section 7.1 showing all pipelines awaiting human input across projects.

### ~~MAJ-8: Expert Panel Has No Activation Threshold~~ ✅ RESOLVED
**Source:** Architecture, Agent Design, Pipeline
**Resolution (2026-03-29):** Section 4.2.1 rewritten to use Claude Code's native team mode. Planner spawns expert subagents via Agent tool (parallel). Added 3-tier activation threshold: simple (solo planner), medium (2-3 experts), complex (full panel). Reduced from 14-21 sequential calls to 2-7 parallel subagent calls + planner synthesis.

### ~~MAJ-9: No Prompt Assembly Specification~~ ✅ RESOLVED
**Source:** Architecture
**Resolution (2026-03-29):** Added Section 7.9 "Prompt Assembly Specification" to dispatcher internals. Defines: prompt structure per agent (what's in the prompt vs. discovered via tools), `PromptBuilder` implementation, `MissingArtifactError` handling. Design principles: agents discover artifacts autonomously via `@` references; missing inputs = immediate stop + user notification; no token budget for v1.

### ~~MAJ-10: Python Version Not Validated in install.sh~~ ✅ RESOLVED
**Source:** Platform
**Resolution (2026-03-29):** Already resolved — `install.sh` (Section 11) validates `sys.version_info >= (3, 10)` and exits with error if check fails.

### ~~MAJ-11: Hook Scripts Use Bare `python` (May Not Exist)~~ ✅ RESOLVED
**Source:** Platform
**Resolution (2026-03-29):** `settings.json` (Section 7.4) updated: hooks now invoke `.claude-plugin/hooks/run_hook.sh` which activates the xpatcher venv. `install.sh` generates the wrapper script at install time, resolving the bare `python` problem on macOS and Python 2 on Linux.

### ~~MAJ-12: `python3 -m venv` Fails on Stock Ubuntu/Debian~~ ✅ RESOLVED
**Source:** Platform
**Resolution (2026-03-29):** `install.sh` (Section 11) now checks `python3 -c "import venv"` before venv creation. On failure, prints platform-specific install instructions (`apt install python3-venv` / `dnf install python3-venv`) and exits.

### ~~MAJ-13: Regression Testing Between Stages Underspecified~~ ✅ RESOLVED
**Source:** QA
**Resolution (2026-03-29):** Added Section 6.5.1 "Regression Testing Between Tasks". v1: re-run standard test suite after each task merge to feature branch. v2: store per-task AC commands in execution plan; after each merge, re-run ACs from completed tasks with overlapping `files_in_scope`. Regression failures re-enter fix iteration loop.

### ~~MAJ-14: Test Quality Pipeline Overhead Unanalyzed~~ ✅ RESOLVED
**Source:** QA
**Resolution (2026-03-29):** Added Section 6.2.1 "Tiered Quality Gate Profiles": Lite (refactors: coverage + regression only, 1-3 min), Standard (most tasks: coverage + negation + flaky, 5-15 min), Thorough (security/financial: all gates, 15-30 min). Planner assigns tier during task breakdown. Project-level and path-level overrides via `.xpatcher.yaml`.

---

## Key Inconsistencies Found (Cross-Document)

| # | Conflict | Locations |
|---|----------|-----------|
| ~~1~~ | ~~Plan review max iterations: 3 vs 5~~ ✅ Resolved — standardized to 3 across all sections | ~~Sec 3.4 vs Sec 5.6~~ |
| ~~2~~ | ~~Review severity enums: three different sets~~ ✅ Resolved — canonical `ReviewSeverity` in Sec 9 | ~~Sec 4 vs Sec 5.4 vs Sec 9~~ |
| ~~3~~ | ~~Task ID format: `task-1.1` vs `task-001` vs `^task-\d+\.\d+$`~~ ✅ Resolved — canonical `task-NNN` in Sec 9 | ~~Sec 4 vs Sec 5 vs Sec 9~~ |
| ~~4~~ | ~~Quality loop iterations: 3 vs 5~~ ✅ Resolved — standardized to 3 (Sec 2.5, 3.4, 5.4, 6.1) | ~~Sec 6.1 vs Sec 3.4~~ |
| ~~5~~ | ~~Simplification timing: before vs after review~~ ✅ Resolved — runs after test+review pass (Sec 3.4 flowchart, Sec 6.4) | ~~Sec 3.1 diagram vs Sec 6.4~~ |
| ~~6~~ | ~~Pipeline state field: `iteration_counts` vs `iterations`~~ ✅ Resolved — canonical `iterations` in Sec 5.4 | ~~Sec 5.4 vs Sec 5.6~~ |
| ~~7~~ | ~~`.xpatcher/` in gitignore vs commit messages referencing artifacts~~ ✅ Resolved — commit messages reference `.xpatcher/` paths as local informational pointers (not git-tracked); clarifying notes added to Sec 2.6 and Sec 7.2 | ~~Sec 7.2 vs Sec 2.6~~ |
| ~~8~~ | ~~Review `confidence` type: string enum vs float~~ ✅ Resolved — canonical `Confidence` Literal in Sec 9 | ~~Sec 4 vs Sec 5.4~~ |
| ~~9~~ | ~~`CHANGES_REQUESTED` state exists in diagram but has no entry path~~ ✅ Resolved — old diagram removed | ~~Sec 2.4 vs Sec 3.3~~ |
| ~~10~~ | ~~Executor `files_created` field: present in prompt, absent in Pydantic~~ ✅ Resolved — canonical `files_changed` list in Sec 9 | ~~Sec 4.3 vs Sec 9~~ |
| ~~11~~ | ~~`settings.json` maxRetries:2 vs MAX_FIX_ATTEMPTS=2 vs quality iterations=5~~ ✅ Resolved — quality iterations standardized to 3, maxRetries=2 is for malformed output (different concern) | ~~Sec 7.4 vs Sec 9 vs Sec 3.4~~ |

---

## Missing Components

| # | Component | Impact | Noted By |
|---|-----------|--------|----------|
| ~~1~~ | ~~Worktree merge protocol~~ | ~~Parallel execution is unimplementable~~ ✅ Resolved in Section 2.6.1 | Arch, Pipeline, DevOps |
| ~~2~~ | ~~Prompt assembly specification (`context/builder.py`)~~ | ~~Agent invocations cannot be constructed~~ ✅ Resolved in Section 7.9 | Architecture |
| ~~3~~ | ~~Semantic validation rules (Stage 3 of validation pipeline)~~ | ~~Cross-reference bugs will slip through~~ ✅ Resolved | Architecture |
| ~~4~~ | ~~Intent capture workflow (Stage 1 Q&A loop)~~ | ~~Not in transition table~~ ✅ Resolved | Pipeline |
| ~~5~~ | ~~Cancellation and cleanup workflow~~ | ~~`xpatcher cancel` behavior undefined~~ ✅ Resolved | Architecture |
| ~~6~~ | ~~`xpatcher skip` command and DAG restructuring~~ ✅ Resolved in Section 7.1 | ~~Stuck task recovery broken~~ | UX, Pipeline |
| ~~7~~ | ~~`pipeline-state.yaml` Pydantic model~~ | ~~Most critical file has no validation~~ ✅ Resolved | Architecture |
| ~~8~~ | ~~`config.yaml` schema~~ | ~~Fields scattered across documents~~ ✅ Resolved | Architecture |
| ~~9~~ | ~~Error taxonomy (transient vs permanent)~~ | ~~Retry logic has no classification~~ ✅ Resolved | Architecture |
| ~~10~~ | ~~Timeout specification per agent~~ | ~~`_timeout_for()` referenced, never defined~~ ✅ Resolved | Architecture |
| ~~11~~ | ~~Missing Pydantic models (4 of 10 artifact types)~~ | ~~intent, task, task-manifest, exec-plan have no validation~~ ✅ Resolved | Architecture |
| ~~12~~ | ~~User documentation plan~~ | ~~No onboarding, no CLI reference, no troubleshooting guide~~ ✅ Resolved | UX |
| ~~13~~ | ~~Uninstall/upgrade mechanism~~ | ~~No way to cleanly remove or update~~ ✅ Resolved | Platform |
| ~~14~~ | ~~Rollback strategy~~ | ~~No way to undo a completed pipeline~~ ✅ Resolved | DevOps |
| ~~15~~ | ~~Documentation stage failure path~~ | ~~No transition for tech-writer failure~~ ✅ Resolved | Pipeline |
| ~~16~~ | ~~System requirements documentation~~ | ~~Users don't know what they need installed~~ ✅ Resolved | Platform |

---

## Over-Engineering Concerns (Simplify for v1)

The following features add substantial implementation complexity with questionable v1 value:

1. ~~**Expert panel** (14-21 agent invocations for planning)~~ ✅ Deferred to v2. v1 uses single planner with multi-perspective checklist prompt (Section 4.2.1).
2. **Session lineage tracking** (max_lineage_depth: 5) -- Use fresh sessions with context bridges for v1. Simpler and more predictable.
3. **Collusion prevention metrics** (alert thresholds, spot-checks) -- Log approval rates in completion output. Add alerting in v2.
4. **Mutation testing gate** -- Already marked optional. Defer entirely to v2.
5. **LLM test auditor** -- Overlaps with reviewer. Fold checklist into tester prompt for v1.
6. **Transcript storage** -- JSONL agent logs already capture the same information.

---

## Team Roles Required for Implementation

| Role | Phase(s) | Responsibilities |
|------|----------|-----------------|
| **Python Backend Engineer** | 1-4 | Dispatcher core, state machine, DAG, session management, parallel execution |
| **Python Backend Engineer #2** | 2-4 | Artifact validation, YAML extraction, Pydantic schemas, context builder |
| **Prompt Engineer / AI Specialist** | 2-3 | Agent definitions, prompt tuning, output format reliability, expert panel |
| **DevOps / Platform Engineer** | 1, 4-5 | install.sh fixes, CLI wrapper, git worktree integration, packaging |
| **TUI / CLI Developer** | 4 | Rich-based TUI, log streaming, human gate prompts, signal handling |
| **QA Engineer** | 3-5 | xpatcher self-tests, E2E test harness, sample projects, quality gate tuning |
| **Technical Writer** | 5 | User docs, CLI reference, quickstart guide, troubleshooting |

**Minimum team:** 2 engineers (backend + devops/TUI) + 1 prompt engineer. QA and docs can be part-time.

---

## Recommended Action Plan

### Before Phase 1 (1-2 day spec revision)

1. ~~**Fix CRIT-1:** Define worktree merge protocol OR decide on sequential execution for v1~~ ✅ Done — Section 2.6.1 added
2. ~~**Fix CRIT-2:** Reconcile state machine with 16-stage pipeline~~ ✅ Done — Section 2.4 simplified, PipelineStage enum added to 3.2.1, TaskState enum added to 2.5
3. ~~**Fix CRIT-3:** Create canonical schema reference, update all 3 layers~~ ✅ Done — Section 9 Pydantic models are canonical, Sections 4 and 5 reference them
4. ~~**Fix CRIT-4:** Add Bash allowlist enforcement to PreToolUse hook~~ ✅ Done — 3-layer Bash enforcement added to Section 7.6
5. ~~**Fix CRIT-5:** Fix `readlink -f` in install.sh~~ ✅ Done — replaced with `pwd -P` in Section 11, also added Python version check
6. ~~**Fix CRIT-6:** Define `xpatcher skip` command~~ ✅ Done — full DAG semantics added to Section 7.1, `SKIPPED` state added to Section 2.5
7. ~~**Fix CRIT-7:** Add xpatcher self-testing section~~ ✅ Done
8. ~~Resolve the 11 cross-document inconsistencies~~ ✅ Done — all 11 resolved
9. Add missing transition table entries (9 missing transitions found by Pipeline expert)

### During Phase 1-2

10. Validate Claude CLI session semantics empirically (`--resume` with `--agent` switch)
11. Implement `pipeline-state.yaml` Pydantic model
12. Define prompt assembly rules for each agent invocation

### During Phase 3-4

13. Define tiered quality gate profiles (lite/standard/thorough)
14. Add signal handling specification
15. Specify E2E test plan with sample projects

### Phase 5

16. User documentation (quickstart, CLI reference, troubleshooting)
17. Uninstall/upgrade mechanism
18. System requirements documentation

---

## Individual Expert Reports

Detailed findings from each expert are available in separate files:

1. [Architecture & Core Engine Review](01-architecture-review.md) -- 4 critical, 7 major, 8 minor issues, 12 inconsistencies
2. [Platform & Installation Review](02-platform-installation-review.md) -- 2 critical, 4 major, 7 minor issues
3. [Quality & Testing Review](03-quality-testing-review.md) -- 3 critical, 4 major, 5 minor issues
4. [Pipeline & Workflow Review](04-pipeline-workflow-review.md) -- 3 critical, 6 major, 8 minor issues, 9 missing transitions
5. [Agent Design & Extensibility Review](05-agent-design-review.md) -- 3 critical, 6 major, 8 minor issues
6. [DevOps & Git Strategy Review](06-devops-git-review.md) -- 2 critical, 5 major, 5 minor issues
7. [UX, TUI & Documentation Review](07-ux-documentation-review.md) -- 3 critical, 5 major, 7 minor issues

---

*End of consolidated review.*

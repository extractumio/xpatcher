# Pipeline Iteration Log

## 2026-04-03

### Objective
Improve the real end-to-end behavior of `xpatcher` by running live pipelines against `/Users/greg/INTEGRUM`, stopping early on obviously bad trajectories, and fixing root-cause architectural issues instead of tuning around symptoms.

### Round 1: Review and fix loops were too expensive

Observed behavior:
- Review and fix stages were spending minutes on full artifact regeneration.
- Small schema issues caused full reruns instead of local repair.
- Repeated retries made no progress and still consumed time.

Root causes:
- Validation repair prompts were broad and encouraged document rewrites.
- Deterministic, cheap schema fixes were being delegated back to the model.
- Retry policy lacked a real no-progress stop condition.

Changes made:
- Added deterministic local artifact repair in `src/dispatcher/schemas.py`.
  - Normalize common type aliases like `plan-review -> plan_review`.
  - Repair malformed task IDs and update `depends_on` references.
  - Normalize mixed/dict-shaped intent list items into plain string lists.
- Updated validation retry flow in `src/dispatcher/core.py`.
  - Snapshot invalid artifacts before retry.
  - Build targeted repair prompts against the last invalid artifact.
  - Stop retrying when the same invalid output repeats without progress.
- Updated stage prompts in `src/context/prompts.yaml`.
  - Emphasize targeted edits.
  - Preserve unchanged content, ordering, and IDs.

Result:
- Recoverable validation failures now stay cheap.
- Unrecoverable or stagnant retries stop earlier.

### Round 2: Planner and reviewer explored too broadly

Observed behavior:
- Quick stages like intent/planning were exploring project files too aggressively.
- Reviewer/planner behavior looked like fresh research rather than bounded artifact updates.

Root causes:
- Prompts did not sufficiently bias the agent toward bootstrap artifacts.
- Planner agent guidance still left too much room for exploratory behavior.

Changes made:
- Strengthened bootstrap-driven prompts in `src/context/prompts.yaml`.
  - Intent capture now uses bootstrap context first.
  - Planner/reviewer/fix stages are told to read stable context artifacts before touching project files.
  - Agent delegation is explicitly discouraged for quick spec stages.
- Updated planner guidance in `.claude-plugin/agents/planner.md`.
  - Prefer `Read`/`Grep`/`Glob` before changes.
  - Use existing artifacts intelligently and edit in place where possible.
  - Avoid spawning subagents for routine planning/review/fix work.
- Expanded bootstrap inventory in `src/context/packets.py`.
  - Detect nested manifests/config files.
  - Include richer repo inventory, scripts, and package-manager signals.

Result:
- Stages get better context up front and have less reason to rediscover the repo manually.

### Round 3: Agents needed safe artifact editing capabilities

Observed behavior:
- Planner could reason about artifacts but lacked a clean, explicit in-place artifact editing model.

Root causes:
- Hook policy and agent docs were oriented around read-only planning or full artifact writes.

Changes made:
- Updated planner tool access and rules in:
  - `.claude-plugin/agents/planner.md`
  - `.claude-plugin/hooks/pre_tool_use.py`
  - `src/context/prompts.yaml`
- Allowed planner `Edit`/`Write` on xpatcher-managed artifacts only.
- Added tests in `tests/test_hooks.py`.

Result:
- Planner can patch manifests/plans/reviews directly while remaining blocked from project source edits.

### Round 4: TUI duplicated wrapped lines

Observed behavior:
- The live dashboard duplicated long wrapped header lines on refresh.

Root cause:
- TUI tracked logical lines rendered, not actual terminal rows consumed.

Changes made:
- Fixed row counting and clearing in `src/dispatcher/tui.py`.
- Added regression coverage in `tests/test_tui.py`.

Result:
- Wrapped dashboard rows clear correctly.

### Round 5: Acceptance commands could crash the dispatcher

Observed behavior:
- Acceptance checks containing shell negation like `! grep ...` crashed with `FileNotFoundError`.

Root causes:
- `!` was treated like an executable instead of shell syntax.
- Acceptance command execution exceptions escaped the quality loop.

Changes made:
- Updated shell parsing in `src/dispatcher/command_validation.py`.
- Hardened acceptance execution in `src/dispatcher/core.py`.
  - Shell-only commands route through the shell.
  - Missing binaries and OS errors become failed checks, not dispatcher crashes.
- Added regression coverage in `tests/test_core.py` and `tests/test_schemas.py`.

Result:
- Acceptance command problems now degrade into quality failures instead of tearing down the pipeline.

### Round 6: Stage handoff needed prerequisite validation

Observed behavior:
- Invalid prerequisite artifacts could leak across stages and fail later in less obvious ways.

Root causes:
- Stage entry did not consistently validate the artifact produced by the previous stage.

Changes made:
- Added fail-fast prerequisite validation in `src/dispatcher/core.py`.
  - Plan review requires a valid plan.
  - Plan fix requires a valid prior review.
  - Task breakdown requires a valid plan.
  - Task review/fix require valid manifest/review artifacts.
  - Prioritization, gap detection, execution, and quality validate their prerequisites before proceeding.
- Unhandled dispatcher exceptions now mark the pipeline `failed`.

Result:
- Bad artifacts fail at handoff time instead of contaminating downstream flow.

### Round 7: Stdin contamination and stale live state

Observed behavior:
- Claude prompts could behave inconsistently when xpatcher was started through wrappers/heredocs.
- Pipeline state and UI could lag while a stage was already in progress.

Root causes:
- Claude subprocesses inherited stdin.
- Invocation metadata was persisted too late.

Changes made:
- Added `stdin=subprocess.DEVNULL` to Claude invocations in `src/dispatcher/session.py`.
- Persist active invocation state before stage execution in `src/dispatcher/core.py`.
  - `active_stage`
  - `active_task_id`
  - `active_lane`
  - `active_agent`
  - `active_session_id`
  - `stage_started_at`

Result:
- Prompt input is cleaner.
- Live state more accurately reflects in-flight work.

### Round 8: Deletion/cancel did not fully stop v2 sessions

Observed behavior:
- Deleting or cancelling a pipeline could still leave v2 lane-scoped Claude work alive.

Root causes:
- Cleanup only looked at limited session sources.
- Lane session IDs were not fully aggregated.

Changes made:
- Added comprehensive session discovery in `src/dispatcher/core.py`.
  - `sessions.yaml`
  - `pipeline-state.yaml`
  - `lane_sessions.*.session_id`
  - `lanes/lane-*.yaml`
- Updated cleanup to kill all collected Claude sessions.
- Added tests in `tests/test_core.py`.

Result:
- Delete/cancel can terminate lane-scoped Claude processes reliably.

### Round 9: Fresh reruns were not actually fresh

Observed behavior:
- A new pipeline for the same feature description could reuse:
  - the same feature directory
  - old logs
  - old lane files
  - old artifacts
  - the same branch name
- This caused contradictory state such as:
  - intent just wrote successfully
  - planning artifacts already existed
  - pipeline-state had already been flipped to `failed`

Root causes:
- Feature directories were slug-scoped, not pipeline-scoped.
- Branch names were slug-scoped, not pipeline-scoped.
- Cancel/delete only killed Claude sessions, not the dispatcher PID itself.
- Old runs could still interfere with new runs for the same feature slug.

Changes made:
- Switched to pipeline-scoped identities in `src/dispatcher/core.py`.
  - Feature dir: `<feature-slug>--<pipeline-id>`
  - Branch name: `xpatcher/<feature-slug>-<pipeline-id>`
- Recorded `dispatcher_pid` in pipeline state.
- Added dispatcher PID termination on cancel/delete.
- Made branch creation fail fast if checkout fails instead of silently continuing on the wrong branch.
- Added tests in:
  - `tests/test_core.py`
  - `tests/test_e2e_pipeline.py`

Result:
- A rerun for the same description is now isolated from previous runs.
- Old lane state and artifacts no longer bleed into a new pipeline by path reuse.

### Verification completed during these iterations

Representative checks run:
- `pytest -q tests/test_core.py tests/test_e2e_pipeline.py -q`
- `pytest -q tests/test_schemas.py tests/test_v2_packets.py tests/test_context.py -q`
- `python -m compileall src/dispatcher/core.py`
- `python -m compileall src/context/packets.py`
- `python -m compileall src/dispatcher/schemas.py`
- `bash -n install.sh`
- `./install.sh`

Latest status at the time of this log entry:
- Focused tests for the touched areas passed.
- Installer completed successfully.
- The next live iteration should be run against a fresh pipeline ID with the new isolation behavior in place.

### Additional live rounds on 2026-04-04

#### Round 1: Fresh-run isolation validated, but planning still over-explored

Observed behavior:
- Fresh rerun used a unique feature dir and unique branch as intended.
- `intent_capture` completed in one pass.
- `planning` then reused a single planner conversation for the stage and wandered through the repo.
- The planner read the project `.env`, exposing a secret path that planning should never touch.

Root causes:
- Intent and planning were still separate prompts but not separate conversation domains before this round.
- Tool policy blocked some dangerous writes but did not block sensitive reads.

Changes made:
- Split `intent_capture` onto its own lane in `src/dispatcher/lanes.py`.
- Added sensitive-read blocking in `.claude-plugin/hooks/pre_tool_use.py`.
  - `.env*`
  - key/cert files
  - `data/`
  - `data_backup_*`
  - common secret/credential directories
- Strengthened prompts in `src/context/prompts.yaml` to explicitly forbid secret/data reads.
- Added tests in `tests/test_hooks.py` and `tests/test_v2_lanes.py`.

Result:
- New planning sessions no longer inherit the intent conversation.
- Secret reads are blocked at the hook boundary.

#### Round 2: Secret leak removed, but planning still enumerated too broadly

Observed behavior:
- Planning no longer read `.env`.
- It still spent too much time enumerating backend files and reading around the repo before writing the plan.

Root causes:
- Planner still had to rediscover too much from the repository.
- Bootstrap context was too shallow for implementation planning.
- Broad recursive globbing was still allowed.

Changes made:
- Added `implementation-scout.yaml` in `src/context/packets.py`.
  - Bounded previews of likely-relevant implementation files from repo inventory.
- Updated planning/review prompts in `src/context/prompts.yaml`.
  - Read implementation scout first.
  - Avoid recursive codebase globs.
  - Cap additional file inspection.
- Added hook policy to block broad recursive globs like `**/*.py` for planner/review agents in `.claude-plugin/hooks/pre_tool_use.py`.
- Added tests in:
  - `tests/test_v2_packets.py`
  - `tests/test_context.py`
  - `tests/test_hooks.py`

Result:
- Planning context is now more directed and broad recursive enumeration is blocked.

#### Round 3: Bootstrap inventory itself was polluting planning context

Observed behavior:
- Even after adding implementation scout, the bootstrap inventory still listed irrelevant/sensitive project areas such as:
  - `data/.../CLAUDE.md`
  - `data_backup_.../...`
- That widened the planner search space unnecessarily and risked dragging sensitive paths back into consideration.

Root cause:
- Repo inventory generation included deep `data/` and backup-tree entries as ordinary key configs.

Changes made:
- Filtered sensitive/irrelevant paths out of inventory generation in `src/context/packets.py`.
  - exclude `data/`
  - exclude `data_backup_*`
  - exclude `.env*`
  - exclude secret/credential directories
- Added regression coverage in `tests/test_v2_packets.py`.

Result:
- Bootstrap artifacts are cleaner and better aligned with planning needs.

Status after these extra rounds:
- Four live validation rounds have been used to harden:
  - run isolation
  - stage isolation
  - secret-read boundaries
  - broad-glob boundaries
  - bootstrap context quality
- Focused tests for these changes passed.
- A full successful end-to-end live completion still remains to be proven after the last bootstrap filtering change.

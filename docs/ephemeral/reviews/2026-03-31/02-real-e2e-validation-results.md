# Real End-to-End Validation Results

Date: 2026-03-31
Scope: live xpatcher validation using real Claude Code CLI runs in disposable repos under `/tmp/mytmpproject/`

## Verdict

Partially working end-to-end, but still not production-ready.

The current system can complete a real full pipeline and produce the expected runtime artifacts outside the target repo. Claude Code was invoked successfully, code was changed, tests passed in the successful repo, and the project-scoped pipeline index layout worked.

At the same time, the live runs exposed important correctness and operational gaps:

- the cancel command does not stop an in-flight dispatcher cleanly
- gap re-entry can still crash on an invalid state transition
- the planner/task-manifest path is inconsistent about producing runnable acceptance commands
- the quality loop can mark tasks stuck even when the produced code itself is correct

## Environment

- Claude Code CLI version: `2.1.87 (Claude Code)`
- xpatcher runtime entrypoint: `python -m src.dispatcher.core`
- XPATCHER homes:
  - `/tmp/mytmpproject/xpatcher-home-auto`
  - `/tmp/mytmpproject/xpatcher-home-human`

## Scenarios Executed

### Scenario A: Full successful cycle

- Repo: `/tmp/mytmpproject/repos/happy`
- Pipeline ID: `xp-20260331-156d`
- XPATCHER_HOME: `/tmp/mytmpproject/xpatcher-home-auto`
- Request: `Add a farewell helper with tests and update the README`

Observed outcome:

- Reached `current_stage: done`
- Wrote `completion.yaml`, `docs-report.yaml`, `gap-report-v1.yaml`, `sessions.yaml`, `execution-plan.yaml`
- Wrote per-task specs, execution logs, review artifacts, and quality reports
- Left no `.xpatcher/` directory in the target repo
- Updated source files and tests successfully

Relevant artifact root:

- `/tmp/mytmpproject/xpatcher-home-auto/.xpatcher/projects/happy-90c566af/add-a-farewell-helper-with-tests-and-update-the-re/`

Target repo quality check:

- `python -m pytest -q` in `/tmp/mytmpproject/repos/happy` passed with `2 passed`

Resulting code looked correct and proportionate:

- `app.py` gained a small `farewell(name)` helper
- `tests/test_app.py` gained a matching test
- `README.md` gained a short helper note

### Scenario B: Forced human gate and resume

- Repo: `/tmp/mytmpproject/repos/resume`
- Pipeline ID: `xp-20260331-ce05`
- XPATCHER_HOME: `/tmp/mytmpproject/xpatcher-home-human`
- Request: `Add an exclamation helper with tests and README note`
- Config override: `human_gates.spec_confirmation: true`

Observed outcome:

- Live start run paused at Stage 5 spec confirmation
- `pending`, `status`, and `list` correctly showed the paused pipeline
- `resume xp-20260331-ce05` returned to the confirmation gate
- approving the gate continued through task breakdown, task review, prioritization, and real task execution

This proves the explicit pause/resume path for a human gate works in the current runtime.

### Scenario C: Cancel path

- Repo: `/tmp/mytmpproject/repos/cancel`
- Pipeline ID: `xp-20260331-37df`
- XPATCHER_HOME: `/tmp/mytmpproject/xpatcher-home-auto`
- Request: `Add a subtract helper with tests`

Observed outcome:

- `xpatcher cancel xp-20260331-37df` updated persisted pipeline state to:
  - `current_stage: cancelled`
  - `status: cancelled`
- The per-project pipeline index was correct
- Runtime state stayed outside the target repo

Important defect exposed:

- the original `start` process did not stop immediately after cancellation
- it continued until the next transition attempt, then crashed with:
  - `InvalidTransitionError: cancelled -> planning`

This means cancellation updates persisted state correctly but does not yet interrupt a live dispatcher process cleanly.

### Scenario D: Failure path via live planner/reviewer behavior

Two separate failure-class behaviors were observed.

#### D1. Task-manifest review loop instability

- Pipeline: `xp-20260331-156d` during its early iterations

Observed behavior:

- task review v1 and v2 both rejected the manifest because all `must_pass` acceptance criteria still had empty command fields
- the planner required multiple revisions before task review finally approved on v3

This is a real agent/runtime quality problem, not a mocked one. The pipeline eventually converged in this repo, but only after burning time and cost in the task-review loop.

#### D2. Gap re-entry state-machine crash after real code execution

- Pipeline: `xp-20260331-ce05`

Observed behavior:

- the resumed pipeline generated real code and commits
- tasks `task-001` and `task-003` executed
- gap detection found that `task-002` had not been completed
- the dispatcher re-entered `task_breakdown`
- it then crashed with:
  - `InvalidTransitionError: task_breakdown -> blocked`

This is still a real end-to-end bug in the gap-reentry path.

## Artifact Validation

### Project-scoped index files

Observed:

- `/tmp/mytmpproject/xpatcher-home-auto/.xpatcher/pipelines/happy-90c566af.yaml`
- `/tmp/mytmpproject/xpatcher-home-auto/.xpatcher/pipelines/cancel-d306992b.yaml`

Each contained:

- `project_dir`
- `pipelines.<pipeline_id>.feature_dir`
- `registered_at`

### Successful-run artifact set

For `xp-20260331-156d`, the artifact root contained at minimum:

- `intent.yaml`
- `plan-v1.yaml`
- `plan-review-v1.yaml`
- `task-manifest.yaml`
- `task-review-v1.yaml`
- `task-review-v2.yaml`
- `task-review-v3.yaml`
- `execution-plan.yaml`
- `gap-report-v1.yaml`
- `docs-report.yaml`
- `completion.yaml`
- `pipeline-state.yaml`
- `sessions.yaml`
- `decisions/decision-20260331-132845-plan-approval.yaml`
- JSONL logs under `logs/`
- materialized task specs and per-task execution/review/quality artifacts under `tasks/done/`

### No target-repo runtime pollution

Checked repo roots:

- `/tmp/mytmpproject/repos/happy`
- `/tmp/mytmpproject/repos/resume`
- `/tmp/mytmpproject/repos/cancel`
- `/tmp/mytmpproject/repos/fail`

Observed:

- no project-local `.xpatcher/`
- no project-local `pipelines.yaml`

## Claude Invocation Evidence

Claude Code was definitely invoked in this validation round.

Evidence:

- real preflight succeeded and reported `Claude Code CLI v2.1.87`
- JSONL agent logs were created for planner, plan-reviewer, executor, reviewer, gap-detector, and tech-writer
- `xpatcher logs xp-20260331-ce05 --tail 12` showed real Claude message and tool-use events
- real cost accumulated in pipeline state

## Code Quality Assessment

### Successful repo: `happy`

Outcome:

- code is simple and correct for the requested scope
- tests pass
- docs update is proportionate

Quality notes:

- implementation is acceptable for a tiny repo
- generated code matched repo style
- no obvious over-engineering

### Resumed repo: `resume`

Outcome:

- code that landed is mostly reasonable
- `service.py` and `README.md` changes are coherent
- tests pass when run from the repo root: `4 passed`

Important caveat:

- the pipeline did not complete cleanly
- task `task-002` never executed, so the gap detector correctly reported missing planned coverage for the `already-exclaimed` case
- the state file marked `task-001` and `task-003` as `stuck` because the quality loop only saw missing acceptance commands, even though the resulting code itself was fine

This means the produced code can be locally good while the orchestrator still misclassifies pipeline health.

## Edge Cases Checked

- `pending` with a paused human-gate pipeline: worked
- `status` for specific pipelines: worked
- `list` across a home with multiple projects: worked
- `logs` for a real pipeline: worked
- project-scoped pipeline indices: worked
- runtime artifacts outside target repos: worked

## Main Findings

1. Real full-cycle success is possible in the current system.
2. Resume from a paused human confirmation gate works.
3. Cancel persists the cancelled state but does not safely stop the active dispatcher.
4. Gap re-entry still has a live invalid-transition bug.
5. Planner/task-manifest quality is still inconsistent; acceptance commands may be omitted or malformed in real runs.
6. The orchestrator can report task-level failure/stuck states even when the committed code is locally correct, because acceptance-command quality dominates the control loop.

## Recommendation

Do not treat the project as production-ready yet.

Use the current state for continued controlled dogfooding only. The next priorities should be:

1. Fix live cancellation so the active dispatcher exits cleanly once a pipeline is cancelled.
2. Fix the gap-reentry state transition defect.
3. Harden the planner/task-manifest prompts and validation so executable command checks are consistently produced in the correct schema fields.
4. Re-run this same validation plan after those fixes on at least two different repo shapes.

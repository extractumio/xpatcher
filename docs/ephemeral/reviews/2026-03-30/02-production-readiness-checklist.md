# Production Readiness Checklist

Date: 2026-03-30
Scope: post-fix validation of the current dispatcher against real Claude Code CLI usage and end-to-end pipeline behavior

## Verdict

Not production-ready yet.

The project is materially stronger than the state reviewed in `01-spec-vs-implementation-review.md`, and the latest validation now includes one fully successful real end-to-end run. Even so, it still shows enough operational gaps and limited recovery coverage that I would not deploy it to production yet.

## Checklist

### Runtime and CLI

- [x] Claude Code CLI installed and reachable
- [x] Real CLI version verified against deployment target
- [x] Plugin-qualified agent naming validated against real CLI output
- [x] `status`, `list`, `pending`, and `logs` exercised against real pipeline artifacts
- [x] `skip` and `cancel` exercised against real pipeline artifacts
- [~] `resume` for paused human gates works interactively, but broader mid-pipeline recovery is still limited

### Dispatcher Control Plane

- [x] Intent, plan, plan-review, task-manifest, and task-manifest-review artifacts validated before advancing
- [x] Task manifests materialize per-task YAML files
- [x] Manifest revisions now refresh existing `tasks/todo/*.yaml` task specs
- [x] Cost totals, session registry, and JSONL agent logs persist to disk during execution
- [x] Gap detection now re-enters scoped task creation and task review instead of always flowing straight to docs
- [x] State machine allows terminal failure from any non-terminal stage
- [~] Full resume-from-arbitrary-stage support still not implemented

### Quality and Test Guarantees

- [x] Weak schema/context tests replaced with contract tests
- [x] End-to-end integration tests added for complete run and gap re-entry filesystem/state transitions
- [x] Dispatcher-owned acceptance-command execution validated
- [~] Real quality-loop behavior still depends heavily on agent-produced command-backed acceptance criteria

### Real Pipeline Evidence

- [x] 6 disposable real pipelines executed during validation: `xp-20260330-1b2d`, `xp-20260330-2c37`, `xp-20260330-7521`, `xp-20260330-952a`, `xp-20260330-1826`, `xp-20260330-8b74`
- [x] Artifacts, state files, decisions, task files, and JSONL logs inspected on disk
- [x] Human-gate resume path exercised interactively
- [x] Skip and cancel CLI paths exercised against real saved pipelines
- [x] A fully successful real pipeline run to `completion.yaml` and `current_stage: done` (`xp-20260330-8b74`)

## What Was Validated

### Real CLI compatibility

Command checked:

```bash
claude --version
```

Observed version:
- `2.1.87 (Claude Code)`

Real preflight inspection showed that Claude registered this inline plugin as `.claude-plugin`, not `xpatcher`. The dispatcher was updated to discover the runtime plugin name from the real init event instead of hard-coding the qualifier.

### Real pipeline runs

Disposable repositories under `/tmp/xpatcher-prodcheck/` were used to run live pipelines against actual Claude agents.

Observed progression across the five completed validation runs:

1. `xp-20260330-1b2d`
   - Reached intent capture and planning.
   - Failed on live planner output shape mismatch (`acceptance` emitted as list).

2. `xp-20260330-2c37`
   - Reached planning again.
   - Failed on live planner complexity vocabulary mismatch (`trivial` instead of `low`).

3. `xp-20260330-7521`
   - Reached plan approval.
   - Exposed human-gate stdin/resume fragility.

4. `xp-20260330-952a`
   - Used to validate interactive resume and cancel flows.
   - Exposed and then verified fixes around paused human-gate resume handling.

5. `xp-20260330-1826`
   - Reached task breakdown, task review/fix, execution, and per-task quality.
   - Exposed stale per-task YAML after manifest revision and the `task_execution -> gap_detection` state-machine defect.

6. `xp-20260330-8b74`
   - Completed end-to-end on 2026-03-30.
   - Reached `current_stage: done`, wrote `completion.yaml`, `docs-report.yaml`, `gap-report-v1.yaml`, session registry, and per-task execution/review/quality artifacts.

These runs directly drove fixes that are now covered by tests.

## Real Issues Found During This Validation

### Fixed in this round

- Runtime plugin name mismatch with real Claude CLI registration
- Planner schema drift for live `acceptance` and complexity values
- Unsafe YAML serialization of enums into Python-tagged YAML
- Resume double-transition bug at paused plan approval
- Resume flow stopping after approval instead of continuing the pipeline
- `task_execution -> gap_detection` transition bug
- Failure transitions from non-terminal stages throwing a second state-machine error
- Gap re-entry skipping scoped task review
- Task manifest revisions not updating existing task spec files in `tasks/todo/`

### Still limiting production use

- Resume support is still targeted, not general crash recovery for arbitrary stages
- Human approval works reliably in interactive TTY mode, but non-interactive feeding of approvals is not reliable and should not be treated as supported behavior
- Real runtime performance is slow even on a trivial single-function feature; the repo5 live run took multiple minutes before first execution
- The quality loop still trusts the task-manifest contract heavily; if agent-generated acceptance commands are weak, the dispatcher can only enforce the commands it was given

## Tests Added

New or upgraded tests now cover:

- Realistic schema routing and manifest contracts
- Refresh of per-task YAML when task manifests are revised
- Resume after paused plan approval continuing the pipeline
- Full mocked end-to-end pipeline completion
- Mocked gap re-entry with manifest versioning and gap task execution
- Runtime plugin-name discovery during preflight
- State-machine transitions for `failed` and `gap_detection`

Current suite result:

```bash
pytest -q
```

Observed result on 2026-03-30:
- `119 passed`

## Production Decision

Do not use in production yet.

Use it only for controlled internal dogfooding until the following are complete:

1. Validate resume from more than paused human gates.
2. Run several more live pipelines on small but different repo shapes, not just the single-file Python toy repo.
3. Run at least one multi-task or multi-gap real feature, not just a trivial single-function change.
4. Decide which remaining roadmap/spec items are actually product commitments vs. aspirational design.

## Remaining Design-Doc Promises: Implement or Downgrade

### Keep as implementation commitments

- Structured control-artifact validation
- Materialized task files and task-manifest authority
- Session persistence and JSONL invocation logs
- Scoped gap re-entry
- Human-gate pause/resume for explicit approvals
- CLI visibility commands: `status`, `list`, `pending`, `logs`

### Downgrade in docs unless more work is funded

- Broad crash-safe resume from arbitrary stages
- Rich TUI behavior beyond current basic terminal output
- Strong orchestrator-owned test-quality guarantees beyond command execution
- Full operational recovery workflow for every stuck/running mid-execution state
- Any implication that the platform is already production-hardened

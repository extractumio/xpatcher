# Real End-to-End Validation Plan

Date: 2026-03-31
Scope: live validation of xpatcher as an SDD pipeline using the real Claude Code CLI in disposable repositories under `/tmp/mytmpproject/`.

## Goals

1. Prove whether the current runtime works end-to-end in a real environment, not just mocked tests.
2. Verify that runtime artifacts are created correctly and outside the target repo.
3. Verify that Claude Code is actually invoked and that live agent logs/sessions are persisted.
4. Assess the quality of the generated code and whether the resulting code stands on its own.
5. Exercise unhappy paths: pause/resume, cancel, and failure handling.
6. Exercise edge cases around empty or invalid state, CLI visibility, and artifact indexing.

## Scenarios

### Scenario A: Happy-path full cycle

- Create a small Python repo with a minimal existing feature and tests.
- Run `xpatcher start` with a concrete feature request that should produce:
  - intent/spec artifacts
  - reviewed task manifest
  - execution
  - quality/review artifacts
  - gap report
  - docs report
  - completion artifact
- Validate target repo changes for correctness and quality.
- Validate runtime artifact tree under `$XPATCHER_HOME/.xpatcher/projects/...`.

### Scenario B: Forced human gate and resume

- Enable `human_gates.spec_confirmation: true` in xpatcher config for this run.
- Start a pipeline and confirm it pauses at specification confirmation.
- Validate `pending`, `status`, and `list` visibility.
- Resume interactively and verify the pipeline continues.

### Scenario C: Cancel path

- Start a new pipeline in another disposable repo.
- Cancel it after the pipeline has created state and at least one artifact/log.
- Validate `cancel` transition, persisted state, and artifact integrity.

### Scenario D: Failure path

- Trigger a real failure mode without mocking, ideally by using a repo/task shape that should fail validation or execution.
- Validate resulting state, logs, and whether the failure is diagnosable.

### Scenario E: Edge cases and operational checks

- Check `status`, `list`, `pending`, and `logs` across multiple projects.
- Validate per-project pipeline indices.
- Validate target repos remain free of runtime artifact directories.
- Inspect behavior when no pipelines are pending.

## Evidence To Capture

- Absolute repo paths used under `/tmp/mytmpproject/`
- Pipeline IDs and current stages
- Claude Code CLI version
- Presence and contents of:
  - `pipeline-state.yaml`
  - `intent.yaml`
  - `plan-v*.yaml`
  - `plan-review-v*.yaml`
  - `task-manifest*.yaml`
  - `tasks/todo|in-progress|done/*.yaml`
  - `gap-report-v*.yaml`
  - `docs-report.yaml`
  - `completion.yaml`
  - `sessions.yaml`
  - `logs/agent-*.jsonl`
  - `.xpatcher/pipelines/*.yaml`
- Final source diffs and tests in the target repo
- Any blockers, failures, or quality concerns

## Acceptance Criteria For This Validation Round

- At least one live pipeline reaches `done`.
- At least one live pause/resume path is exercised.
- At least one live cancel path is exercised.
- At least one live failure path is exercised.
- Evidence confirms Claude Code was actually invoked.
- Evidence confirms runtime artifacts are outside the target repo.
- Evidence confirms project-scoped pipeline indexing is used.

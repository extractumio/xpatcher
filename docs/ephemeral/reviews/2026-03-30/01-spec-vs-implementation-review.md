# Spec vs Implementation Review

Date: 2026-03-30
Scope: `docs/proposals/design/` compared against current implementation in `src/`

## Summary

The current codebase is materially behind the published platform specification. The main risk is not just missing features; several core control-flow and artifact contracts are internally inconsistent, so the dispatcher can accept malformed outputs, skip required work, and still advance to completion.

`pytest -q` currently passes, but the test suite mostly validates the present simplified implementation rather than conformance to the published design.

## Findings

### 1. Plan review and task review are modeled with the wrong schema

Severity: High

The implementation routes Stage 3 plan review and Stage 7 task-manifest review through the generic `ReviewOutput` schema, which requires a `task_id` and uses executor-style verdicts (`approve | request_changes | reject`). The design docs define these reviews as plan/manifest reviews and describe verdicts like `approved` and `needs_changes`.

Impact:
- An agent following the design docs can produce output that fails validation.
- The dispatcher review loops can mis-handle valid spec-compliant outcomes.
- The "single authoritative schema" claim is false for review artifacts.

Evidence:
- `docs/proposals/design/03-pipeline-flow.md`: Stage 3 and 7 definitions use plan/task-manifest review semantics.
- `src/dispatcher/schemas.py`: `ReviewOutput` requires `task_id` and `approve | request_changes | reject`.
- `src/dispatcher/core.py`: both plan review and task review validate against `"review"`.

References:
- [03-pipeline-flow.md](/Users/greg/EXTRACTUM/xpatcher/docs/proposals/design/03-pipeline-flow.md#L53)
- [schemas.py](/Users/greg/EXTRACTUM/xpatcher/src/dispatcher/schemas.py#L165)
- [core.py](/Users/greg/EXTRACTUM/xpatcher/src/dispatcher/core.py#L127)

### 2. Task breakdown and execution do not share a real artifact contract

Severity: High

The dispatcher saves only a top-level `task-manifest.yaml`, but the executor prompt expects per-task YAML files under `tasks/todo/`. Those files are never created, versioned, or moved by the dispatcher. Stage 11 therefore depends on artifacts that Stage 6 never materializes.

Impact:
- The execution stage cannot operate against the artifact layout described in the spec.
- Task-level auditability is missing.
- The documented `tasks/todo -> in-progress -> done` workflow is not implemented.

Evidence:
- `src/context/builder.py`: task breakdown asks for a manifest; executor expects `tasks/todo/{task_id}-*.yaml`.
- `src/dispatcher/core.py`: Stage 6 saves only `task-manifest.yaml`.
- `docs/proposals/design/05-artifact-system.md`: spec requires both manifest and per-task files.

References:
- [builder.py](/Users/greg/EXTRACTUM/xpatcher/src/context/builder.py#L61)
- [builder.py](/Users/greg/EXTRACTUM/xpatcher/src/context/builder.py#L92)
- [core.py](/Users/greg/EXTRACTUM/xpatcher/src/dispatcher/core.py#L169)
- [core.py](/Users/greg/EXTRACTUM/xpatcher/src/dispatcher/core.py#L210)
- [05-artifact-system.md](/Users/greg/EXTRACTUM/xpatcher/docs/proposals/design/05-artifact-system.md#L19)

### 3. Intent capture and task breakdown are not validated, and empty outputs can still lead to "success"

Severity: High

Stage 1 saves `result.parsed or {"description": description}` without schema validation. Stage 6 saves `result.parsed or {}` without schema validation. Stage 9 treats a missing task list as an empty valid DAG and continues into gap detection, documentation, and final approval.

Impact:
- The pipeline can advance with malformed or empty control artifacts.
- A run can reach completion without any actual executable tasks.
- This breaks the spec claim that the dispatcher validates transitions and structured outputs.

Evidence:
- `src/dispatcher/core.py`: unvalidated Stage 1 and Stage 6 writes.
- `src/dispatcher/core.py`: empty `tasks` becomes an empty DAG and is accepted.

References:
- [core.py](/Users/greg/EXTRACTUM/xpatcher/src/dispatcher/core.py#L101)
- [core.py](/Users/greg/EXTRACTUM/xpatcher/src/dispatcher/core.py#L165)
- [core.py](/Users/greg/EXTRACTUM/xpatcher/src/dispatcher/core.py#L210)
- [03-pipeline-flow.md](/Users/greg/EXTRACTUM/xpatcher/docs/proposals/design/03-pipeline-flow.md#L49)

### 4. Gap detection has no effect on control flow

Severity: High

The design defines Stage 14 as a gate: `gaps_found` should trigger scoped re-entry into Stages 6-14, with depth tracking and escalation limits. The current implementation saves the gap report if valid and then always proceeds directly to documentation.

Impact:
- The system can explicitly detect missing work and still continue to "complete".
- One of the core risk-mitigation mechanisms in the design is absent.

Evidence:
- `src/dispatcher/core.py`: after Stage 14, there is no branch on `validation.data["verdict"]`.
- `docs/proposals/design/03-pipeline-flow.md`: gap detection must either re-enter Stage 6 or move to documentation.

References:
- [core.py](/Users/greg/EXTRACTUM/xpatcher/src/dispatcher/core.py#L255)
- [03-pipeline-flow.md](/Users/greg/EXTRACTUM/xpatcher/docs/proposals/design/03-pipeline-flow.md#L125)
- [03-pipeline-flow.md](/Users/greg/EXTRACTUM/xpatcher/docs/proposals/design/03-pipeline-flow.md#L176)

### 5. The quality loop does not implement the orchestrator-owned guarantees in the spec

Severity: High

The design says the dispatcher independently runs acceptance-criteria commands, regression checks, optional simplification, oscillation detection, and escalation to `stuck`. The implementation only asks the tester for YAML, asks the reviewer for YAML, and re-invokes the executor on failure. There is no independent harness, no simplify step, no oscillation detection, and no `stuck` handling when retries are exhausted.

Impact:
- "Completion gate evaluated by orchestrator" is not true in practice.
- The implementation is vulnerable to test theater and premature success declarations.
- The `TaskState.STUCK` state exists but is not used in the quality loop.

Evidence:
- `src/dispatcher/core.py`: `_run_quality_loop()` only coordinates agent calls.
- `docs/proposals/design/03-pipeline-flow.md`: detailed per-task quality flow.
- `docs/proposals/xpatcher-design-proposal.md`: orchestrator-owned completion gate is a key design decision.

References:
- [core.py](/Users/greg/EXTRACTUM/xpatcher/src/dispatcher/core.py#L304)
- [03-pipeline-flow.md](/Users/greg/EXTRACTUM/xpatcher/docs/proposals/design/03-pipeline-flow.md#L146)
- [xpatcher-design-proposal.md](/Users/greg/EXTRACTUM/xpatcher/docs/proposals/xpatcher-design-proposal.md#L110)

### 6. The public CLI surface is mostly declared but not implemented

Severity: Medium

`resume`, `cancel`, `skip`, `pending`, and `logs` are defined in the argument parser but all fall through to a generic "not yet implemented" path. `status` ignores the provided `pipeline_id` and only scans the current working directory. `list` is only a thin wrapper around that same cwd-local status logic.

Impact:
- The documented operational workflow is unavailable.
- Human-gate recovery and stuck-task handling are not functional.
- The implementation does not satisfy the published CLI contract.

Evidence:
- `src/dispatcher/core.py`: parser includes these commands, but dispatch only handles `start`, `status`, and `list`.
- `docs/proposals/design/07-cli-and-installation.md`: the CLI contract treats these commands as supported platform features.

References:
- [core.py](/Users/greg/EXTRACTUM/xpatcher/src/dispatcher/core.py#L378)
- [core.py](/Users/greg/EXTRACTUM/xpatcher/src/dispatcher/core.py#L417)
- [core.py](/Users/greg/EXTRACTUM/xpatcher/src/dispatcher/core.py#L429)
- [07-cli-and-installation.md](/Users/greg/EXTRACTUM/xpatcher/docs/proposals/design/07-cli-and-installation.md#L13)

### 7. Cost tracking, session reuse, and log capture are specified but not operational

Severity: Medium

The dispatcher instantiates a session registry but never registers or reuses sessions. Running cost is accumulated only in memory and shown in the terminal, but not persisted back into `pipeline-state.yaml` during execution. The spec also promises structured JSONL agent logs, but no logging path currently writes invocation events.

Impact:
- Resumption, compaction, and session lineage features are effectively absent.
- Crash recovery loses cost visibility.
- Observability and auditability are much weaker than the spec claims.

Evidence:
- `src/dispatcher/core.py`: `SessionRegistry` is created but unused.
- `src/dispatcher/core.py`: `_invoke_agent()` updates `self.total_cost_usd` only.
- The codebase contains no write path for `logs/agent-*.jsonl`.

References:
- [core.py](/Users/greg/EXTRACTUM/xpatcher/src/dispatcher/core.py#L81)
- [core.py](/Users/greg/EXTRACTUM/xpatcher/src/dispatcher/core.py#L293)
- [07-cli-and-installation.md](/Users/greg/EXTRACTUM/xpatcher/docs/proposals/design/07-cli-and-installation.md#L210)
- [xpatcher-design-proposal.md](/Users/greg/EXTRACTUM/xpatcher/docs/proposals/xpatcher-design-proposal.md#L156)

## Design-Level Issues

### A. The design set contradicts itself on iteration ceilings

The master proposal says per-task quality has a hard iteration cap of 5, while the detailed pipeline document says 3. This weakens the repeated claim that the docs are authoritative.

References:
- [xpatcher-design-proposal.md](/Users/greg/EXTRACTUM/xpatcher/docs/proposals/xpatcher-design-proposal.md#L57)
- [03-pipeline-flow.md](/Users/greg/EXTRACTUM/xpatcher/docs/proposals/design/03-pipeline-flow.md#L133)

### B. Canonical schemas are incomplete for core control-plane artifacts

The docs present `intent.yaml`, `task-manifest.yaml`, execution plans, and pipeline state as core first-class artifacts, but there is no schema authority for them in the implementation. That leaves key pipeline contracts informal and easy to drift.

References:
- [05-artifact-system.md](/Users/greg/EXTRACTUM/xpatcher/docs/proposals/design/05-artifact-system.md#L93)
- [schemas.py](/Users/greg/EXTRACTUM/xpatcher/src/dispatcher/schemas.py#L83)

### C. The proposal reads like a finished platform spec, but much of it is still roadmap-level

The docs describe hooks, structured logging, session reuse, rich TUI behavior, gap re-entry, and CLI recovery flows as concrete platform behavior. The codebase is much closer to a prototype skeleton. That mismatch is a product/documentation risk in its own right.

References:
- [xpatcher-design-proposal.md](/Users/greg/EXTRACTUM/xpatcher/docs/proposals/xpatcher-design-proposal.md#L12)
- [xpatcher-design-proposal.md](/Users/greg/EXTRACTUM/xpatcher/docs/proposals/xpatcher-design-proposal.md#L154)

## Verification

Command run:

```bash
pytest -q
```

Result:
- 152 tests passed
- 1 pytest collection warning in `src/dispatcher/schemas.py`

Interpretation:
- The current tests validate the implemented code paths.
- They do not meaningfully verify conformance with the published platform spec.

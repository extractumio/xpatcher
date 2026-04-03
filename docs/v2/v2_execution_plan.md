# xpatcher v2 Execution Plan

## Status

Draft implementation specification derived from [`v2_proposal.txt`](/Users/greg/EXTRACTUM/xpatcher/docs/v2/v2_proposal.txt).

This document turns the proposal into an execution plan for engineering work. It defines the target architecture, the concrete module changes, the rollout path from v1, and the acceptance criteria for each milestone.

## 1. Scope

### In scope

- Replace pipeline-wide session reuse with lane-scoped session reuse
- Introduce reusable context artifacts so stages stop rediscovering the repo
- Move deterministic stages to direct `claude --agent ...`
- Add validation repair loops and bounded retry policy
- Reduce prompt/schema drift with generated contract blocks
- Add severity-gated review handling
- Replace gap re-entry full re-breakdown with delta-based gap closure
- Add cost budgets and budget-aware retry behavior
- Preserve v1 compatibility during rollout

### Explicit non-goals for this effort

- Parallel task execution and worktree merge orchestration
- Web UI or Rich TUI redesign
- Team/multi-user coordination features
- Mutation testing and advanced import-graph regression logic
- Redesign of the overall pipeline state machine

These remain separate concerns. v2 in this document is about determinism, resilience, context reuse, and cost control.

## 2. Problem Statement

The current implementation has four coupled failure modes:

1. One long-lived session accumulates unrelated context across planning, review, task breakdown, execution, gap analysis, and docs.
2. Reusable project knowledge is trapped in session history instead of explicit artifacts.
3. Schema guidance is duplicated across `schemas.py`, prompts, and agent markdown, which drifts.
4. Validation failures and review loops are too binary, causing expensive dead runs.

The result is:

- schema bleed across stages
- repeated repo rediscovery
- avoidable pre-execution spend
- opaque normalization behavior
- weak gap re-entry determinism

## 3. Target Outcomes

V2 is successful if it produces all of the following on real runs:

- task breakdown and plan authoring use isolated lane sessions
- later stages reuse explicit context artifacts rather than rediscovering the repo
- malformed structured outputs are repaired automatically in bounded retries
- planner/schema drift is reduced to a single canonical contract source
- minor-only review findings do not trigger full review/fix loops
- gap re-entry produces only delta tasks
- total pre-execution cost drops materially relative to v1
- v1 mode remains runnable until v2 reaches parity

## 3.1 Why This Design

This section makes the design intent explicit so implementation does not drift back toward v1 behavior.

### Why lanes instead of one fresh session per stage

Fresh sessions for every invocation would reduce contamination, but they would also force repeated rediscovery and repeated fix-loop context rebuilding. That is too expensive and too brittle in execution stages.

Lanes are the middle ground:

- they isolate unrelated artifact families
- they preserve continuity where continuity is genuinely useful
- they make session reuse a conscious policy instead of a global side effect

This is why `task_exec:<task_id>` is a lane, but `planning` and `task_breakdown` are not in the same lane.

### Why context artifacts instead of transcript reuse

Transcript reuse is cheap to implement but expensive to operate. It causes:

- hidden state
- hard-to-reproduce failures
- token-heavy rediscovery
- no inspectable explanation for why a later stage acted a certain way

Context artifacts solve all four:

- shared knowledge becomes explicit
- later lanes can reuse it cheaply
- failures can be traced to concrete artifact inputs
- humans can inspect, diff, and improve the context system

### Why direct `--agent` instead of prompt-based delegation

The current prompt shape relies on a top-level agent to route work to a subagent. That adds cost and weakens contract delivery. Direct `--agent` is preferable for deterministic structured-output stages because:

- the intended agent receives the prompt directly
- the contract block is not filtered through a router prompt
- per-stage costs are reduced
- stage behavior is easier to test and reason about

### Why generated contracts instead of handwritten schema hints

The current system already showed that three copies of the same contract drift:

- Pydantic schema
- prompt text
- agent markdown

Generated contracts keep the schema canonical while still giving agents compact, readable instructions.

### Why gap deltas instead of full manifest regeneration

A second full manifest synthesis during gap re-entry reintroduces planning nondeterminism late in the pipeline. It can duplicate tasks, subtly mutate existing task intent, or change task boundaries after code already exists. Delta-only gap closure prevents all three.

## 4. Architecture

### 4.1 Control Plane

The dispatcher remains the control plane. Its responsibilities stay the same:

- stage transitions
- artifact persistence
- resume and recovery
- cancellation
- budget accounting
- human gates

The change is internal: the dispatcher stops owning a single pipeline session and instead coordinates lane sessions.

### 4.2 New Core Components

#### `LaneManager`

Owns session continuity by lane.

Responsibilities:

- resolve lane name for a stage
- create and persist lane session state
- resume the correct session for a lane
- rotate sessions when retry policy or contamination policy requires it
- expose lane telemetry to the dispatcher

Implementation detail:

`LaneManager` should be a pure stateful coordinator, not an agent-invocation wrapper. The dispatcher remains responsible for stage semantics. `LaneManager` should answer:

- what lane is this stage in
- what session should this invocation use
- should this invocation resume or rotate
- how should this lane be recorded in state

#### `ContextManager`

Owns generation and refresh of reusable context artifacts.

Responsibilities:

- create stable project context
- create dynamic plan/manifest/task/gap packets
- expose context refs to prompt builders
- track context artifact versions

Implementation detail:

`ContextManager` should distinguish:

- stable context generation
- packet generation
- invalidation logic

Stable context should not be regenerated on every stage. Packet artifacts may be regenerated when their source artifact version changes.

#### `ContractManager`

Owns prompt-side contract generation from canonical schemas.

Responsibilities:

- generate compact field contract blocks
- generate semantic invariant blocks
- provide contract fingerprints for telemetry

Implementation detail:

The output of `ContractManager` must be deterministic. If contract generation is unstable, golden tests become noisy and prompts become hard to diff. The generated representation should therefore be compact, ordered, and normalized.

#### `BudgetManager`

Owns lane, stage-family, and pipeline budget enforcement.

Responsibilities:

- compute effective budget for an invocation
- warn at threshold crossings
- tighten retry policy when near budget limit
- block or require human intervention at hard cap

Implementation detail:

`BudgetManager` should not only reject work after the fact. It should influence retry policy before the next invocation. Near budget exhaustion, the system should prefer:

- one precise repair attempt
- no speculative extra loop
- escalation over repeated low-signal retries

## 5. Session Lane Model

### 5.1 Lane Names

The initial v2 lane set is:

- `bootstrap`
- `spec_author`
- `spec_review`
- `manifest_author`
- `manifest_review`
- `task_exec:<task_id>`
- `gap_analysis`
- `docs`

### 5.2 Stage-to-Lane Mapping

| Stage | Lane | Reuse Policy |
|---|---|---|
| `intent_capture` | `spec_author` | resume |
| `planning` | `spec_author` | resume |
| `plan_fix` | `spec_author` | resume |
| `plan_review` | `spec_review` | isolated, optional resume inside review loop |
| `task_breakdown` | `manifest_author` | resume |
| `task_fix` | `manifest_author` | resume |
| `task_review` | `manifest_review` | isolated, optional resume inside review loop |
| `task_execution` | `task_exec:<task_id>` | resume |
| `fix_iteration` | `task_exec:<task_id>` | resume |
| `gap_detection` | `gap_analysis` | resume |
| `gap_manifest_delta` | `gap_analysis` | resume |
| `documentation` | `docs` | isolated/fresh |

### 5.3 Lane Policy

Each lane record stores:

- `lane_name`
- `agent`
- `session_id`
- `resume_enabled`
- `invocation_count`
- `max_invocations_before_rotate`
- `budget_usd_cap`
- `last_stage`
- `rotated_at`
- `context_refs`

Recommended additional fields:

- `contract_family`
- `created_at`
- `last_used_at`
- `total_cost_usd`
- `retry_count`
- `status`

### 5.4 Rotation Rules

Default rotation rules:

- no rotation on normal author/fix continuity
- rotate immediately when a retry strategy asks for a fresh lane session
- rotate if a lane exceeds `max_invocations_before_rotate`
- rotate when contract family changes inside a lane, which normally should not happen

Recommended initial defaults:

- author lanes: rotate after `5` invocations
- review lanes: rotate after `2` invocations
- task execution lanes: rotate only on validation contamination or explicit budget policy
- docs lane: prefer fresh session by default

These defaults are intentionally conservative. They bias toward determinism before optimizing session reuse further.

## 6. Context Knowledge System

### 6.1 Design Rule

Project knowledge must be stored in explicit artifacts, not in session lineage.

Sessions are for bounded reasoning continuity. Context artifacts are for reusable knowledge.

### 6.2 Stable Context Artifacts

Created once per pipeline unless invalidated:

- `context/repo-inventory.yaml`
- `context/architecture-map.yaml`
- `context/conventions.yaml`
- `context/verification-catalog.yaml`
- `context/runtime-constraints.yaml`
- `context/feature-brief.yaml`

These artifacts must be concise, structured, and versioned.

Implementation note:

Each stable artifact should target one question:

- repo inventory: "what is here?"
- architecture map: "how is it organized?"
- conventions: "how does this repo usually work?"
- verification catalog: "how do we verify things?"
- runtime constraints: "what external constraints matter?"
- feature brief: "what is requested in this pipeline?"

If an artifact answers too many questions, it will bloat and become another transcript substitute.

### 6.3 Dynamic Context Artifacts

Created as the pipeline evolves:

- `context/plan-packet-vN.yaml`
- `context/manifest-packet-vN.yaml`
- `context/task-packets/task-XXX.yaml`
- `context/gap-packet-vN.yaml`

### 6.4 Artifact Contracts

Each context artifact needs a schema in `src/dispatcher/schemas.py`. These should be strict enough for deterministic reuse and simple enough for agent generation.

Required initial schema set:

- `RepoInventoryOutput`
- `ArchitectureMapOutput`
- `ConventionsOutput`
- `VerificationCatalogOutput`
- `RuntimeConstraintsOutput`
- `FeatureBriefOutput`
- `PlanPacketOutput`
- `ManifestPacketOutput`
- `TaskPacketOutput`
- `GapPacketOutput`
- `TaskManifestDeltaOutput`

### 6.5 Packet Usage

Prompt builders must stop embedding large repeated context prose.

Instead:

- spec author reads stable context + feature brief
- manifest author reads stable context + plan packet + plan
- executor reads stable context + task packet
- gap analysis reads stable context + final artifacts + gap packet

### 6.6 Context Invalidation Rules

Stable context is reused until one of the following occurs:

- repository root changes
- bootstrap context version changes
- key config files change materially during the pipeline
- the user explicitly forces bootstrap refresh

Dynamic packets are invalidated when their source artifact changes:

- `plan-packet-vN` regenerates when plan version changes
- `manifest-packet-vN` regenerates when approved plan or stable context changes
- `task-packets/task-XXX.yaml` regenerate when task manifest version changes
- `gap-packet-vN` regenerates when gap report version changes

### 6.7 Context Generation Policy

Bootstrap context generation should be fact-first, not essay-first.

Preferred techniques:

- read a bounded set of repo-defining files
- inspect directory structure
- detect commands from real config files
- summarize into structured fields

Avoid:

- broad prose summaries
- speculative architecture narratives without file evidence
- embedding large code excerpts in context artifacts

## 7. Invocation Model

### 7.1 Direct Agent Invocation

Structured-output deterministic stages must use direct `--agent` invocation.

Required CLI flags:

- `--agent`
- `--max-budget-usd`
- `--plugin-dir`
- `--output-format json`

### 7.2 Agent Role Split

Agent markdown should define role and method, not duplicate stage-specific contracts.

Prompt templates should define:

- input artifact refs
- output artifact path
- generated contract block
- semantic rules block
- time limit
- budget limit
- lane-local instructions

Rationale:

This split is important because agent markdown should be stable over time while stage prompts change per invocation. If stage contracts continue to live in agent markdown, schema drift will return.

### 7.3 Agent Invocation Shape

`AgentInvocation` must be extended with:

- `agent: str | None`
- `max_budget_usd: float | None`
- `lane_name: str | None`

`ClaudeSession._build_cmd()` must append:

- `--agent <name>` if present
- `--max-budget-usd <amount>` if present

Recommended debug output additions:

- lane name
- lane session ID
- contract fingerprint
- effective budget for this invocation
- whether the invocation is a normal call, same-session repair, or rotated-session repair

## 8. Contract and Validation Model

### 8.1 Canonical Source

`src/dispatcher/schemas.py` remains the canonical contract source.

Prompts and agent guidance must derive from it rather than restating it manually.

### 8.2 Generated Contract Blocks

`ContractManager` must export:

- `build_field_contract(schema_name)`
- `build_semantic_rules(schema_name)`
- `fingerprint(schema_name, version)`

Contract blocks should be compact, readable, and stable enough for golden tests.

### 8.3 Validation Retry Strategy

Every validated invocation follows this sequence:

1. invoke
2. read artifact
3. validate schema and semantic rules
4. if invalid:
   - save invalid output snapshot
   - save structured validation errors
   - issue repair prompt
   - retry in same lane session
5. if still invalid:
   - rotate lane session
   - retry with same contract block
6. if still invalid:
   - fail or block according to stage criticality and budget state

Rationale:

The first retry assumes the model simply made a local formatting or contract error and can self-repair. The second retry assumes contamination inside the lane session and forces a fresh context window while preserving the same explicit contract.

### 8.4 Retry Limits

Default retry limits:

- validation retry in same session: `1`
- validation retry in rotated session: `1`
- total attempts per validated invocation: `3`

These limits must be centrally configurable.

Recommended implementation split:

- syntactic/schema repair limits
- review/fix loop limits
- task quality loop limits

Do not count validation repair retries as full review iterations. They are invocation repair, not substantive design cycles.

## 9. Normalization Policy

V2 keeps normalization, but reduces hidden coercion.

### 9.1 Allowed by Default

- null-to-empty-string for optional string fields
- safe key renames such as `risk -> description`
- compatible enum aliases such as `production -> thorough`

### 9.2 Disallowed for Deterministic Stages

- inventing missing summaries
- inventing missing strategic content
- coercing malformed top-level structures into valid-looking artifacts

### 9.3 Traceability

Validation results must include normalization metadata:

- `applied_safe_normalizations`
- `applied_aliases`
- `masked_violation_attempts`

This metadata is persisted in validation failure snapshots and optional telemetry.

Implementation note:

Normalization metadata should be attached to the `ValidationResult` object rather than only logged. The dispatcher should be able to make policy decisions based on whether a result was accepted cleanly or accepted only through compatibility aliases.

## 10. Review Policy

Review loops become severity-gated.

### 10.1 Decision Rules

- `approved` -> accept
- `needs_changes` with any `critical|major` -> fix loop
- `needs_changes` with only `minor|nit` -> accept with warnings
- `rejected` -> block unless retry is explicitly allowed for that stage

### 10.2 Applies To

- plan review
- task manifest review
- per-task code review where the review schema semantics support it

Rationale:

Minor-only findings are useful feedback, but in a cost-sensitive pipeline they should not trigger another full planner or manifest synthesis pass. They should be preserved as warnings and surfaced in output artifacts.

## 11. Gap Closure Model

Gap re-entry must stop regenerating full manifests.

### 11.1 New Artifacts

- `gap-report-vN.yaml`
- `context/gap-packet-vN.yaml`
- `task-manifest-delta-vN.yaml`

### 11.2 New Flow

1. run gap analysis
2. if complete, continue
3. if gaps found:
   - build gap packet
   - run dedicated delta manifest authoring
   - validate that every delta task maps to one or more unresolved gaps
   - review only the delta
   - merge delta into master manifest
   - execute only delta tasks

### 11.3 Merge Rules

Delta merge must:

- preserve completed tasks unchanged
- reject duplicate task IDs
- preserve authoritative latest manifest version
- produce a new merged manifest version
- persist task lineage for audit

Recommended extra rule:

- every delta task must include a `source_gap_ids` field or equivalent lineage metadata in the delta artifact, even if the merged authoritative manifest omits it later

## 12. Budget Model

### 12.1 Budget Scopes

- per invocation
- per lane
- per stage family
- pre-execution total
- per task execution lane
- total pipeline

### 12.2 Threshold Behavior

- `70%`: warn in TUI and state
- `85%`: reduce retry aggressiveness and prefer fresh-session direct repair only when critical
- `100%`: block or require human intervention

### 12.3 Initial Budget Targets

These must be config-driven, but the implementation should support:

- bootstrap cap
- spec authoring cap
- manifest authoring cap
- review loop cap
- per-task cap
- total pipeline cap

Recommended initial behavior before strong enforcement:

- Phase 0-3: record and warn
- Phase 5 onward: enforce on v2 mode

This avoids blocking development during the architecture transition while still collecting calibration data.

## 13. State and File Layout

### 13.1 State Additions

`pipeline-state.yaml` gains:

- `lane_sessions`
- `context_versions`
- `contract_versions`
- `normalization_events`
- `budget_checkpoints`
- `validation_failures`

### 13.2 Feature Directory Additions

Add:

- `context/`
- `validation-failures/`
- `lanes/`

Target shape:

```text
feature-dir/
  context/
    repo-inventory.yaml
    architecture-map.yaml
    conventions.yaml
    verification-catalog.yaml
    runtime-constraints.yaml
    feature-brief.yaml
    plan-packet-v1.yaml
    manifest-packet-v1.yaml
    gap-packet-v1.yaml
    task-packets/
      task-001.yaml
  validation-failures/
    task_breakdown-attempt-1.yaml
    task_breakdown-attempt-1-errors.yaml
  lanes/
    lane-spec_author.yaml
    lane-manifest_author.yaml
    lane-task_exec-task-001.yaml
```

### 13.3 Lane State File Shape

Each `lanes/lane-*.yaml` file should include:

```yaml
---
lane_name: spec_author
agent: planner
session_id: 11111111-2222-3333-4444-555555555555
contract_family: plan
resume_enabled: true
invocation_count: 2
retry_count: 1
total_cost_usd: 0.82
status: active
created_at: "2026-04-03T12:00:00Z"
last_used_at: "2026-04-03T12:05:00Z"
context_refs:
  - context/repo-inventory.yaml
  - context/feature-brief.yaml
  - context/plan-packet-v1.yaml
```

The lane files exist for inspectability and debugging, not as the sole source of truth. `pipeline-state.yaml` remains authoritative.

## 14. Module-Level Implementation Plan

### 14.1 `src/dispatcher/core.py`

Required changes:

- remove pipeline-wide session ownership
- add lane lookup and lane-aware invocation
- add lane-aware validation repair flow
- add severity-gated review handling
- add budget-aware decision points
- change gap re-entry to delta flow

Required new helpers:

- `_lane_for_stage(stage, task_id="")`
- `_agent_for_lane(lane_name)`
- `_budget_for_lane(lane_name, stage, task_id="")`
- `_invoke_validated_with_repair(...)`
- `_build_repair_prompt(...)`
- `_record_validation_failure(...)`
- `_record_normalization_events(...)`
- `_merge_manifest_delta(...)`

Additional required changes:

- split validated structured-output invocation from unvalidated plain invocation more explicitly
- ensure task fix execution results are validated, not ignored
- make stage retry behavior visible in TUI and logs
- record lane/session IDs in agent logs

### 14.2 `src/dispatcher/session.py`

Required changes:

- extend `AgentInvocation`
- append `--agent` and `--max-budget-usd`
- keep `--session-id` and `--resume` behavior, now lane-scoped
- include lane name in debug and telemetry output

Implementation caution:

Do not break v1 resume semantics while adding lane support. The command builder should be backward-compatible when `agent` and `max_budget_usd` are unset.

### 14.3 `src/context/builder.py`

Refactor into packet-aware prompt building.

Required new methods:

- `build_bootstrap_context(...)`
- `build_plan_packet(...)`
- `build_manifest_packet(...)`
- `build_task_packet(...)`
- `build_gap_packet(...)`
- updated stage prompt builders that reference artifact refs instead of duplicating large prose

Implementation detail:

`PromptBuilder` should not become responsible for generating facts. Facts come from `ContextManager`. `PromptBuilder` should assemble prompts from:

- artifact refs
- contract blocks
- small stage-local instructions
- budget/time values

### 14.4 `src/dispatcher/schemas.py`

Required changes:

- add schemas for context artifacts
- add schema for delta manifest
- add validation metadata model if needed
- tighten normalization policy
- keep compatibility aliases where justified

### 14.5 New Modules

Add:

- `src/dispatcher/lanes.py`
- `src/context/contracts.py`
- `src/context/packets.py`
- `src/dispatcher/budget.py`

Recommended ownership:

- `lanes.py`: lane state and session lifecycle
- `contracts.py`: prompt-side contract rendering
- `packets.py`: packet assembly and artifact writing
- `budget.py`: threshold logic and caps

## 15. Migration Plan

The migration must be incremental and feature-flagged.

### Phase 0: Hardening Now

Objective:
stop current expensive failure modes immediately.

Deliverables:

- `quality_tier` aliases
- planner/task breakdown contract fix
- validation retry in live path
- validated executor fix outputs

Acceptance:

- malformed task manifest output gets retried automatically
- same failure no longer kills the run on first invalid enum
- executor fix result artifacts are validated before the loop proceeds

### Phase 1: Lane Manager

Objective:
introduce lane sessions without changing stage semantics.

Deliverables:

- `LaneManager`
- stage-to-lane mapping
- lane state persistence
- replacement of `_pipeline_session_id`

Acceptance:

- planning and task breakdown use different sessions
- task execution and task fix iterations for one task reuse the same lane
- state and debug logs show which lane each invocation used

### Phase 2: Context Artifacts

Objective:
remove repeated rediscovery.

Deliverables:

- bootstrap context generation
- stable context artifact schemas and writers
- task packet generation
- prompt builder reads artifact refs

Acceptance:

- at least three later stages consume generated context artifacts
- task executor starts from task packet instead of repo rediscovery prompt
- bootstrap context is not regenerated on every later stage

### Phase 3: Direct `--agent`

Objective:
remove router prompt pattern.

Deliverables:

- `AgentInvocation.agent`
- `AgentInvocation.max_budget_usd`
- direct agent invocation in deterministic stages
- prompt cleanup removing `@agent-...` delegation text

Acceptance:

- deterministic stages run with `--agent`
- contract block reaches the intended agent directly
- routing-style delegation text is removed from stage prompts used in v2

### Phase 4: Contract Generation

Objective:
eliminate prompt/schema drift.

Deliverables:

- `ContractManager`
- generated field contract blocks
- generated semantic rules blocks
- prompt builder integration

Acceptance:

- prompt schema hints are no longer hand-maintained duplicates for covered stages
- golden tests cover contract generation output
- planner and manifest author prompts consume generated contract blocks in v2 mode

### Phase 5: Review Severity + Budgets

Objective:
cut waste and add stop conditions.

Deliverables:

- severity-gated review policy
- budget manager
- per-lane budget checkpoints

Acceptance:

- minor-only review results no longer trigger fix loops
- budget thresholds are recorded and enforced
- retry policy changes when the lane is near budget cap

### Phase 6: Gap Delta Redesign

Objective:
fix re-entry determinism.

Deliverables:

- gap packet generation
- delta manifest schema and authoring step
- delta merge logic
- removal of full re-breakdown during gap handling

Acceptance:

- gap closure produces only new tasks
- completed tasks remain untouched during re-entry
- merged manifest lineage can show which delta tasks came from which gap report

### Phase 7: Optional Simplification

Objective:
reduce stage count only after parity.

Candidates:

- merge intent into feature brief
- merge low-value author/review preflight steps

This phase is explicitly deferred until telemetry proves a simplification is safe.

## 16. Config and Compatibility Flags

V2 ships behind flags first.

Required flags:

- `pipeline.mode: v1 | v2`
- `sessions.use_lanes: true|false`
- `context.use_bootstrap_artifacts: true|false`
- `contracts.generated: true|false`
- `reviews.severity_gate: true|false`
- `gaps.delta_mode: true|false`

Optional early debug flags:

- `sessions.rotate_on_validation_retry`
- `context.persist_packets`
- `budgets.enforced`
- `validation.persist_raw_attempts`

## 17. Test Strategy

### Unit

- stage-to-lane mapping
- lane rotation policy
- budget threshold behavior
- repair prompt generation
- contract block generation
- quality tier alias normalization
- review severity gating
- delta manifest merge rules

### Integration

- planning lane and manifest lane do not share session IDs
- task lane resumes across fix iterations
- context artifacts are created and referenced by later stages
- invalid manifest output retries and succeeds
- gap re-entry creates only delta tasks
- bootstrap context reuse reduces repeated repo discovery in prompt construction

### Regression

- v1 mode still runs
- existing state transitions remain valid
- artifact store semantics remain intact

### Golden Tests

Snapshot:

- context artifacts
- task packets
- contract blocks
- lane state evolution

## 18.1 Real-System Validation Loop

Unit and integration tests are not sufficient for v2. The system must be validated in installed form, against a substantial real repository, with repeated patch-install-run-debug cycles.

### Why this is required

The core risks in v2 are integration risks:

- Claude CLI invocation shape
- plugin loading
- lane/session resume behavior
- prompt and contract delivery
- artifact generation under real repository structure
- cost behavior on real codebases

These risks cannot be fully validated by mocked tests alone.

### Validation Mode

Treat xpatcher as a product, not just a Python package under test:

1. install it to a clean `XPATCHER_HOME`
2. run it from the installed CLI
3. execute against a real external repository
4. collect failures and artifacts
5. patch source
6. reinstall
7. repeat until the scenario passes cleanly

### Benchmark Repository Criteria

The benchmark repository should be:

- real and externally maintained
- materially larger than a toy repo
- actively tested
- multi-directory with configs, docs, and nontrivial code structure
- practical to run locally

Recommended primary benchmark candidates:

- `mkdocs/mkdocs`
- `httpie/cli`
- `Textualize/rich`
- `pallets/click`

Recommended first primary benchmark:

- `mkdocs/mkdocs`

Why:

- sizeable but not extreme
- Python-based, matching xpatcher's current strongest ecosystem fit
- real tests and docs
- enough complexity to stress planning, manifesting, and verification

Add a second benchmark after primary stabilization to avoid overfitting to one repo.

### Benchmark Task Selection Rules

Use tasks that are real enough to exercise the pipeline but bounded enough to debug repeatedly.

Good tasks:

- add a CLI option with docs and tests
- improve validation on an existing code path
- add a small but real config feature
- fix a bug that touches multiple files and tests

Avoid early tasks that require:

- deep framework internals
- large architectural migration
- external service credentials
- major dependency upgrades

### Validation Environment Setup

For each validation round:

1. choose a clean install dir
2. install xpatcher via `./install.sh`
3. clone or refresh the benchmark repo
4. create a clean branch in the benchmark repo
5. run xpatcher from the installed CLI, not from the source tree

Example:

```bash
export XPATCHER_HOME="$HOME/xpatcher-v2-dev"
rm -rf "$XPATCHER_HOME"
./install.sh

git clone https://github.com/mkdocs/mkdocs.git "$HOME/bench/mkdocs"
cd "$HOME/bench/mkdocs"
git checkout -B xpatcher-v2-bench

"$XPATCHER_HOME/bin/xpatcher" start "Add a bounded feature request here"
```

### Iterative Validation Cycle

The expected cycle is:

1. install current source
2. run benchmark scenario
3. inspect pipeline state, artifacts, logs, and produced branch diff
4. identify failure source
5. patch xpatcher source
6. reinstall to the same or a fresh `XPATCHER_HOME`
7. rerun the benchmark from a clean benchmark repo state
8. repeat until the scenario passes

This loop must be expected and documented as normal engineering workflow for v2.

### Mandatory Inspection After Each Real Run

After every failed or suspicious run, inspect:

- installed CLI smoke behavior
- `pipeline-state.yaml`
- lane files under `lanes/`
- context artifacts under `context/`
- raw validation failure snapshots
- agent logs under `logs/`
- final git diff in the benchmark repo
- total cost and per-stage cost

Questions to answer each round:

- did the stage use the expected lane
- did it use the expected agent
- did it read the expected context packet
- did validation fail cleanly and retry properly
- did review severity handling behave correctly
- did gap re-entry create deltas only
- was the produced code/spec sensible

### Reinstall Rule

Always reinstall after changing any of the following:

- Python source under `src/`
- plugin agents under `.claude-plugin/agents/`
- prompts or builder logic
- config defaults
- install-time runtime assumptions

Do not trust editable local behavior as a substitute for installed-product behavior.

### Real-World Validation Matrix

Minimum matrix before calling v2 ready for wider use:

- one planning/manifest scenario on benchmark repo passes
- one execution/fix-loop scenario passes
- one validation-repair scenario passes
- one gap-reentry scenario passes
- one resume-from-paused-gate scenario passes

At least two real repositories should eventually be used:

- primary: Python benchmark repo
- secondary: another medium-size repo with different structure

### Recommended Reiteration Commands

Run product tests:

```bash
pytest -q
```

Reinstall:

```bash
export XPATCHER_HOME="$HOME/xpatcher-v2-dev"
rm -rf "$XPATCHER_HOME"
./install.sh
```

Reset benchmark repo between iterations:

```bash
cd "$HOME/bench/mkdocs"
git reset --hard HEAD
git clean -fd
git checkout -B xpatcher-v2-bench
```

Run pipeline:

```bash
"$XPATCHER_HOME/bin/xpatcher" start "Add a small config validation improvement with tests and docs"
```

Inspect progress:

```bash
"$XPATCHER_HOME/bin/xpatcher" list
"$XPATCHER_HOME/bin/xpatcher" status <pipeline-id>
"$XPATCHER_HOME/bin/xpatcher" logs <pipeline-id> --tail 100
```

### Failure Triage Categories

Tag each failure found in reiteration with one primary category:

- lane/session policy bug
- context artifact bug
- contract generation bug
- validation retry bug
- budget policy bug
- review severity policy bug
- gap delta bug
- install/runtime packaging bug
- prompt quality issue
- benchmark-task-specific issue

This classification matters because v2 changes multiple control-plane surfaces at once.

### Exit Criteria For Real-System Validation

A milestone is not complete until:

- unit/integration tests pass
- installed CLI smoke test passes
- benchmark repo scenario passes from installed CLI
- resulting artifacts and lane files match intended design
- the same scenario can be rerun cleanly after reinstall
- no manual intervention was required outside documented gates

## 18. Definition of Done

V2 is implementation-complete when all of the following are true:

- lane-scoped sessions replace pipeline-wide session reuse for v2 mode
- reusable project knowledge is persisted as context artifacts and consumed by later stages
- direct `--agent` is used for deterministic stages
- generated contract blocks replace duplicated manual schema hints for covered stages
- validation repair loop is live and bounded
- severity-gated review policy is live
- gap closure uses delta manifests
- budget enforcement is live
- v1 compatibility mode remains operational
- tests cover unit, integration, and compatibility behavior

## 19. Recommended First Delivery Slice

The first production-worthy v2 slice should include only:

- Phase 0
- Phase 1
- Phase 2
- Phase 3

This slice solves the main real-world failures:

- schema bleed
- repeated rediscovery
- missing validation retry
- delegation overhead

Contract generation, budget enforcement, and delta gap closure should land after the base lane/context architecture is stable.

## 19.1 Recommended First External Scenario

After Phase 3 lands, the first required external validation scenario should be:

- benchmark repo: `mkdocs/mkdocs`
- request: a small but real documentation/config or validation improvement
- success criteria:
  - bootstrap artifacts created
  - planning and manifest authoring use different lane sessions
  - task execution lane is separate and resumable
  - pipeline reaches first code change without schema-failure death
  - installed CLI behavior matches source-tree expectations

## 20. Engineering Notes

- Do not delete v1 code paths early. Add v2 paths beside them and switch by config.
- Prefer new modules for v2 control-plane logic rather than enlarging `core.py` further.
- Keep context artifacts concise. If an artifact becomes essay-like, it will recreate the original token problem in a different form.
- Preserve artifact inspectability. A human should be able to open any context packet and understand why a later lane behaved as it did.
- Use golden fixtures aggressively. V2 is about determinism; this should be tested explicitly.

# Artifact System

> Part of the [xpatcher Design Proposal](../xpatcher-design-proposal.md)

---

## 5.1 Folder Structure

```
.xpatcher/
  <feature>/                                    # Feature directory (kebab-case)
    intent.yaml                                 # Stage 1 output
    plan-v1.yaml                                # Stage 2 output (versioned)
    plan-v2.yaml                                # Stage 4 output (revision after review)
    plan-v{N}.yaml                              # ...auto-incremented per revision
    plan-review-v1.yaml                         # Stage 3 output (reviews plan-v1)
    plan-review-v2.yaml                         # Stage 3 output (reviews plan-v2)
    plan-review-v{N}.yaml                       # ...one review per plan version
    task-manifest.yaml                          # Stage 6 output
    task-review-v1.yaml                         # Stage 7 output (auto-incremented)
    execution-plan.yaml                         # Stage 9 output
    gap-report-v{N}.yaml                        # Stage 14 output (per gap detection round)
    docs-report.yaml                            # Stage 15 output (documentation changes)
    completion.yaml                             # Stage 16 output
    pipeline-state.yaml                         # Current state (mutable singleton)
    sessions.yaml                               # Session registry (see Section 7.8)
    decisions/                                  # Human decisions
      decision-YYYYMMDD-HHMMSS-<type>.yaml
    tasks/
      todo/                                     # Tasks not yet started
        task-001-<slug>.yaml
        task-002-<slug>.yaml
      in-progress/                              # Tasks currently being executed
        task-003-<slug>.yaml
        task-003-execution-log.yaml
      done/                                     # Completed tasks with all artifacts
        task-004-<slug>.yaml
        task-004-execution-log.yaml
        task-004-quality-report-v1.yaml
        task-004-review-v1.yaml
        task-004-review-v2.yaml
    logs/                                       # Agent invocation logs (JSONL)
      agent-planner-YYYYMMDD-HHMMSS.jsonl
      agent-executor-task-001-YYYYMMDD-HHMMSS.jsonl
      agent-reviewer-task-001-YYYYMMDD-HHMMSS.jsonl
      agent-tech-writer-YYYYMMDD-HHMMSS.jsonl
      YYYYMMDD-HHMMSS-stage-<name>.yaml         # Stage transition events
      YYYYMMDD-HHMMSS-batch-<N>-start.yaml      # Batch execution events
    debug/                                      # Malformed output dumps (if any)
```

The dispatcher moves task files between `todo/`, `in-progress/`, and `done/` folders as their status changes. This makes it easy to see pipeline progress by listing directories.

## 5.2 File Naming Conventions

| Type | Pattern | Examples |
|------|---------|---------|
| Feature directories | `kebab-case` | `auth-redesign`, `session-management` |
| Versioned artifacts | `<type>-v<N>.yaml` | `plan-v1.yaml`, `plan-review-v2.yaml` |
| Task artifacts | `task-<NNN>-<slug>.yaml` | `task-001-session-store-interface.yaml` |
| Timestamped artifacts | `YYYYMMDD-HHMMSS-<desc>.yaml` | `20260328-143022-stage-intent.yaml` |
| Mutable singleton | `pipeline-state.yaml` | Updated in place (only exception) |

## 5.3 Common Header

Every artifact shares a common header for consistency and machine parsing:

```yaml
schema_version: "1.0"         # Schema version for forward compatibility
id: "<unique-identifier>"     # Globally unique; format varies by type
feature: "<feature-slug>"     # Which feature this belongs to
created_at: "<ISO-8601>"      # When this artifact was created
```

## 5.4 Artifact Schemas

All artifact field definitions, enum values, and validation rules are defined by the **Pydantic models in Section 9** (Canonical Schema Reference). Section 9 is the single source of truth. This section maps each artifact file to its corresponding model.

### Agent Output Artifacts

These are produced by agent invocations and validated by the dispatcher on receipt.

| Artifact File | Pydantic Model (Section 9) | Produced By | Stage |
|---|---|---|---|
| `intent.yaml` | `IntentOutput` | Planner | 1 |
| `plan-v{N}.yaml` | `PlanOutput` | Planner | 2, 4 |
| `task-manifest.yaml` | `TaskManifestOutput` | Planner | 6, 8 |
| `plan-review-v{N}.yaml` | `PlanReviewOutput` | Plan Reviewer | 3 |
| `task-review-v{N}.yaml` | `TaskManifestReviewOutput` | Plan Reviewer | 7 |
| `tasks/task-NNN-execution-log.yaml` | `ExecutionOutput` | Executor | 11 |
| `tasks/task-NNN-review-v{N}.yaml` | `ReviewOutput` | Reviewer | 12 |
| `tasks/task-NNN-quality-report.yaml` (test section) | `TestOutput` | Tester | 12 |
| `tasks/task-NNN-quality-report.yaml` (simplification section) | `SimplificationOutput` | Simplifier | 12 |
| `gap-report-v{N}.yaml` | `GapOutput` | Gap Detector | 14 |
| `docs-report.yaml` | `DocsReportOutput` | Tech Writer | 15 |

### Dispatcher-Managed Artifacts

These are written directly by the Python dispatcher, not by agents. They use the common header (Section 5.3) and the `PipelineStage` enum (Section 3.2.1) but do not have Pydantic agent-output models since they are never produced by LLM output parsing. Their schemas are defined by the dispatcher code itself.

| Artifact File | Managed By | Description |
|---|---|---|
| `execution-plan.yaml` | Dispatcher | Batches, concurrency, critical path |
| `pipeline-state.yaml` | Dispatcher | Mutable singleton: current stage, task statuses, iteration counts |
| `sessions.yaml` | Dispatcher | Session registry (see Section 7.8) |
| `decisions/*.yaml` | Dispatcher | Human gate decisions |
| `completion.yaml` | Dispatcher | Final pipeline summary |

The dispatcher validates all plan-control artifacts before writing them. Empty or schema-invalid `intent.yaml`, `task-manifest.yaml`, and stage-review outputs are rejected and cannot advance the pipeline.

### Pipeline State: `current_stage` Values

The `current_stage` field in `pipeline-state.yaml` uses the `PipelineStage` enum defined in Section 3.2.1. The `status` field uses: `running | waiting_for_human | paused | completed | failed`.

### Iteration Tracking

The `pipeline-state.yaml` tracks iteration counts under an `iterations` key (not `iteration_counts`):

```yaml
# In pipeline-state.yaml:
iterations:
  plan_review:
    current: 3
    max: 3          # From config.yaml, default 3
    history:
      - version: 1
        verdict: needs_changes
        timestamp: "2026-03-28T14:35:00Z"
  task_review:
    task-001:
      current: 2
      max: 3
  quality_loop:
    task-001:
      current: 1
      max: 3
```

## 5.5 Cross-Referencing Strategy

Artifacts reference each other via `id` fields using `*_ref` fields. The reference graph is a DAG:

```
intent
  +-- plan-v1 (intent_ref --> intent)
        +-- plan-review-v1 (target_ref --> plan-v1)
              +-- plan-v2 (references plan-review-v1 findings)
                    +-- plan-review-v2 (target_ref --> plan-v2)
                          +-- task-manifest (plan_ref --> plan-v2)
                                +-- task-001 (manifest_ref --> task-manifest)
                                |     +-- task-001-execution-log (task_ref --> task-001)
                                |     +-- task-001-quality-report (task_ref --> task-001)
                                +-- task-002
                                |     +-- ...
                                +-- execution-plan (manifest_ref --> task-manifest)
```

**Querying**: all YAML files can be queried with `yq`:

```bash
# All stuck tasks
yq '.status' .xpatcher/auth-redesign/tasks/done/task-*.yaml | grep stuck

# All major findings across reviews
yq '.findings[] | select(.severity == "major")' .xpatcher/auth-redesign/*-review-*.yaml

# Current pipeline stage
yq '.current_stage' .xpatcher/auth-redesign/pipeline-state.yaml
```

## 5.6 Versioning Strategy

Artifacts are **immutable once created** (except `pipeline-state.yaml`). Revisions produce new files with incremented version numbers. This provides a complete audit trail without requiring git history queries.

Artifacts are never deleted. Cancelled tasks get `status: cancelled` with a `cancelled_reason` field.

### Versioning Rules

Versions are auto-assigned by the dispatcher. The folder contains whatever versions the process actually produced.

- Plans: `plan-v{N}.yaml` where N starts at 1 and increments on each revision
- Plan reviews: `plan-review-v{N}.yaml` where N corresponds to the plan version being reviewed
- Task reviews: `task-{NNN}-review-v{N}.yaml` where N increments per review iteration
- Quality reports: `task-{NNN}-quality-v{N}.yaml` per quality loop iteration

**Example: simple feature (1 review, approved immediately):**
```
.xpatcher/add-logout-button/
  intent.yaml
  plan-v1.yaml
  plan-review-v1.yaml          # verdict: approved
  task-manifest.yaml
  tasks/
    task-001-logout-button.yaml
```

**Example: complex feature (4 plan review iterations):**
```
.xpatcher/auth-redesign/
  intent.yaml
  plan-v1.yaml
  plan-review-v1.yaml          # verdict: needs_changes
  plan-v2.yaml                 # addresses v1 review findings
  plan-review-v2.yaml          # verdict: needs_changes (new issues found)
  plan-v3.yaml                 # addresses v2 review findings
  plan-review-v3.yaml          # verdict: needs_changes (minor)
  plan-v4.yaml                 # final revision
  plan-review-v4.yaml          # verdict: approved
  task-manifest.yaml
  tasks/
    task-001-session-store.yaml
    task-001-review-v1.yaml    # verdict: request_changes
    task-001-review-v2.yaml    # verdict: approve
    task-001-quality-v1.yaml   # overall: needs_fix
    task-001-quality-v2.yaml   # overall: pass
    ...
```

### Dispatcher Version Management

```python
import glob
import os

class ArtifactVersioner:
    """Manages auto-incrementing artifact versions."""

    def __init__(self, feature_dir: str):
        self.feature_dir = feature_dir

    def next_version(self, artifact_type: str, prefix: str = "") -> tuple[int, str]:
        """
        Returns (version_number, file_path) for the next version of an artifact.

        Usage:
            v, path = versioner.next_version("plan")
            # Returns (1, ".xpatcher/auth-redesign/plan-v1.yaml") if no plans exist
            # Returns (3, ".xpatcher/auth-redesign/plan-v3.yaml") if v1 and v2 exist
        """
        pattern = os.path.join(self.feature_dir, f"{prefix}{artifact_type}-v*.yaml")
        existing = glob.glob(pattern)

        if not existing:
            version = 1
        else:
            versions = []
            for f in existing:
                basename = os.path.basename(f)
                # Extract version number from filename like "plan-v3.yaml"
                try:
                    v = int(basename.split("-v")[-1].replace(".yaml", ""))
                    versions.append(v)
                except ValueError:
                    continue
            version = max(versions) + 1 if versions else 1

        filename = f"{prefix}{artifact_type}-v{version}.yaml"
        filepath = os.path.join(self.feature_dir, filename)
        return version, filepath

    def latest_version(self, artifact_type: str, prefix: str = "") -> tuple[int, str] | None:
        """Returns the latest version of an artifact, or None if none exist."""
        pattern = os.path.join(self.feature_dir, f"{prefix}{artifact_type}-v*.yaml")
        existing = sorted(glob.glob(pattern))
        if not existing:
            return None
        latest = existing[-1]
        basename = os.path.basename(latest)
        v = int(basename.split("-v")[-1].replace(".yaml", ""))
        return v, latest

    def all_versions(self, artifact_type: str, prefix: str = "") -> list[tuple[int, str]]:
        """Returns all versions of an artifact as [(version, path), ...]."""
        pattern = os.path.join(self.feature_dir, f"{prefix}{artifact_type}-v*.yaml")
        existing = sorted(glob.glob(pattern))
        results = []
        for f in existing:
            basename = os.path.basename(f)
            try:
                v = int(basename.split("-v")[-1].replace(".yaml", ""))
                results.append((v, f))
            except ValueError:
                continue
        return results
```

### Iteration Tracking

See Section 5.4 for the `iterations` schema in `pipeline-state.yaml`.

---

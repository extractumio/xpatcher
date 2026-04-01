# Conventions

## Naming

- **Pipeline IDs**: `xp-YYYYMMDD-<4-hex>` (e.g., `xp-20260330-8b74`)
- **Task IDs**: `task-NNN` zero-padded (e.g., `task-001`, `task-012`)
- **Gap tasks**: `task-G001`, `task-G002` (prefixed with G)
- **Feature branches**: `xpatcher/<feature-slug>`
- **Commit messages**: `xpatcher(task-NNN): <description>`
- **Artifact filenames**: kebab-case, versioned with `-v{N}` suffix

## Data Format

- All artifacts are **YAML** (never JSON, except Claude Code plugin spec files like `plugin.json`)
- Config files: YAML
- Agent logs: JSONL (one JSON object per line)
- State enums: snake_case strings

## Schemas

Pydantic models in `src/dispatcher/schemas.py` are the **single authoritative schema reference**. If any discrepancy exists between agent prompts, design docs, and schemas.py, **schemas.py wins**.

Key schema types:
- `IntentOutput` -- Stage 1 output
- `PlanOutput` -- Stage 2 output
- `PlanReviewOutput` -- Stage 3 output (verdict: `approved | needs_changes | rejected`)
- `TaskManifestOutput` -- Stage 6 output
- `ExecutorOutput` -- Stage 11 output
- `ReviewOutput` -- Stage 12 output (verdict: `approve | request_changes | reject`)
- `GapReport` -- Stage 14 output
- `DocsReport` -- Stage 15 output

## Git Strategy

- Single feature branch per pipeline from main/master
- xpatcher never merges to main (always human via PR)
- Atomic task commits with structured messages
- `.xpatcher/` is gitignored in target repos
- Feature branch pushed to remote on completion; PR created if `gh` available

## Code Style

- Python 3.10+ (type hints, `match` statements OK)
- Dependencies: pydantic, pyyaml, rich (minimal footprint)
- Dev dependencies: pytest, pytest-cov
- `src/` layout with namespace packages

## Configuration Precedence

1. CLI flags
2. Project overrides (`.xpatcher.yaml`)
3. Global config (`config.yaml`)
4. Built-in defaults

## Quality Tiers

Three tiers: `lite`, `standard`, `thorough` (configured in `config.yaml`).

## Agent Model Assignment

| Role | Model | Why |
|------|-------|-----|
| Planning, review, gap detection | Opus | Deep reasoning required |
| Execution, testing, simplification, docs | Sonnet | Speed/cost balance |
| Exploration | Haiku | Cheap, fast, read-only |

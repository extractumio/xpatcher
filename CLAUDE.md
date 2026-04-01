# xpatcher

See [README.md](README.md) for project description, installation, and usage.

**Version:** 0.1.0

## Two Separate Contexts: Building xpatcher vs. Running xpatcher

This repository contains two distinct contexts that must not be conflated:

1. **Building xpatcher** (the current development context). This is a normal Python project with source code under `src/`, tests under `tests/`, and standard development workflows (pytest, pip install, git). When working on xpatcher -- fixing bugs, adding features, writing tests, discussing gotchas -- this is the context that applies. The development rules, conventions, and issues are about the Python dispatcher, its state machine, schema validation, CLI, and how they are built and tested.

2. **Running xpatcher** (the product it delivers). xpatcher's purpose is to orchestrate a 16-stage pipeline for end users. The `.claude-plugin/` directory (agents, hooks, skills) and `config.yaml` define the pipeline that xpatcher will execute when deployed. These are **deliverables** -- artifacts that ship as part of the product. They are not rules or workflows governing how this repository is developed.

**Why this matters:** The agent definitions in `.claude-plugin/agents/`, the hook scripts in `.claude-plugin/hooks/`, and the skill files in `.claude-plugin/skills/` describe what xpatcher does for its users, not what we do when developing xpatcher. When asked about xpatcher's development process, testing strategy, gotchas, or conventions, reason about the Python codebase and its development cycle. Do not treat the plugin directory contents as the project's own workflow -- they are the product being built.

## Source of Truth

The codebase is the single source of truth. Design proposals, research findings, and review documents under `docs/ephemeral/` are historical artifacts from the design phase -- they may describe features that were never implemented, use outdated naming, or contradict the actual code. When in doubt, read the code.

**Ephemeral documents rule:** When creating plans, reviews, analysis reports, validation results, or any other document that reflects a point-in-time snapshot rather than enduring reference material, place it under `docs/ephemeral/`. These documents are useful in the moment but become outdated as the code evolves. Only `docs/*.md` files referenced from this CLAUDE.md are maintained as living documentation; everything else goes to `docs/ephemeral/`.

## Reference

| Topic | Doc |
|-------|-----|
| Current architecture (from code) | [docs/architecture-snapshot.md](docs/architecture-snapshot.md) |
| Codebase structure and file locations | [docs/project-map.md](docs/project-map.md) |
| What this project is | [docs/project-overview.md](docs/project-overview.md) |
| 16-stage pipeline flow | [docs/pipeline.md](docs/pipeline.md) |
| Agent roster and model assignments | [docs/agents.md](docs/agents.md) |
| Testing strategy and quality framework | [docs/testing.md](docs/testing.md) |
| Installation, config, and CLI usage | [docs/deployment.md](docs/deployment.md) |
| Known bugs and debugging | [docs/troubleshooting.md](docs/troubleshooting.md) |
| Naming, schemas, and code style | [docs/conventions.md](docs/conventions.md) |
| Deferred features and known gaps | [docs/TODO.md](docs/TODO.md) |

## Development

```bash
# Setup
python -m venv .venv && . .venv/bin/activate && pip install -e '.[dev]'

# Run tests
pytest -q

# Run a pipeline
xpatcher start "Add a farewell helper with tests"
```

## Key Rules

- **The codebase is the source of truth** -- not the design docs, not the proposals, not the reviews
- `src/dispatcher/schemas.py` is the **single authoritative schema reference** -- if agent prompts disagree, schemas.py wins
- All artifacts are YAML (never JSON, except `plugin.json`)
- Runtime state goes under `$XPATCHER_HOME/.xpatcher/`, never inside the target repo
- xpatcher never merges to main/master
- Sequential task execution (parallel with git worktrees is not yet implemented)

# Deployment and Installation

## Installation Methods

### Development (editable install)
```bash
git clone <repo> && cd xpatcher
python -m venv .venv
. .venv/bin/activate
pip install -e .
```

### Per-user install (recommended for use)
```bash
./install.sh
```

Installs to `$XPATCHER_HOME` (default: `~/xpatcher/`). Creates venv, copies plugin + source, sets up CLI entry point at `~/xpatcher/bin/xpatcher`.

### What the installer does

1. Checks Python 3.10+
2. Checks Claude Code CLI is installed
3. Creates installation directory
4. Copies core files (plugin, src, config)
5. Creates venv, installs deps (pydantic, pyyaml, rich)
6. Creates CLI entry point wrapper
7. Creates hook wrapper script
8. Smoke tests: verifies Claude Code CLI + plugin loading
9. Prints PATH setup instructions

## Configuration

### Global config (`config.yaml`)

Controls models, concurrency, iteration limits, quality tiers, gates, and timeouts. Located in the xpatcher installation root.

### Per-project overrides (`.xpatcher.yaml` in project root)

```yaml
models:
  executor_default: opus       # Override model per project
concurrency:
  max_parallel_agents: 2       # Lower parallelism for this project
gates:
  auto_approve_task_review: true
```

### Resolution order
1. CLI flags (`--model opus`, `--concurrency 5`)
2. Project overrides (`<project>/.xpatcher.yaml`)
3. Global config (`~/xpatcher/config.yaml`)
4. Built-in defaults

## Running Against a Project

```bash
# From within a project directory (auto-detected)
xpatcher start "Add OAuth2 support"

# Explicit project path
xpatcher start "Add OAuth2 support" --project /path/to/myapp

# Explicit core installation path
~/xpatcher/bin/xpatcher start "Add OAuth2 support"
```

### Installation path resolution
1. `XPATCHER_HOME` environment variable
2. `~/xpatcher/` (default)
3. Directory containing the `xpatcher` binary (self-relative)

## CLI Commands

```bash
xpatcher start "<request>"          # Start a new pipeline
xpatcher resume <pipeline-id>       # Resume interrupted pipeline
xpatcher status [pipeline-id]       # Check pipeline status
xpatcher list                       # List all pipelines
xpatcher cancel <pipeline-id>       # Cancel a pipeline
xpatcher skip <pipeline-id> <task-id>[,<task-id>...]  # Skip stuck tasks
xpatcher pending                    # Show pipelines waiting for human input
xpatcher logs <pipeline-id> [--agent <name>] [--task <id>] [--tail N]
```

## Runtime Artifact Layout

Artifacts are stored under `$XPATCHER_HOME/.xpatcher/`, never inside the target repository.

Pipeline lookup indices: `$XPATCHER_HOME/.xpatcher/pipelines/<project-slug>.yaml`

Per-pipeline artifacts: `$XPATCHER_HOME/.xpatcher/projects/<project-hash>/<feature>/`

## Full Specification

Historical design spec: [ephemeral/proposals/design/07-cli-and-installation.md](ephemeral/proposals/design/07-cli-and-installation.md) (may not match current implementation).

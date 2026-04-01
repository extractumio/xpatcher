# Project Overview

xpatcher is an SDLC automation pipeline that transforms natural-language feature requests into production-ready code. It uses a **Python thin dispatcher** to orchestrate **Claude Code subagents** via the headless CLI (`claude -p`).

## Core Architecture

- **Python dispatcher** (`src/dispatcher/`): owns execution loop, state machine, DAG scheduling, process lifecycle, file I/O
- **Claude Code agents** (`.claude-plugin/agents/`): own all reasoning, code generation, review, and decision-making
- **File-based coordination**: all runtime state is YAML under `$XPATCHER_HOME/.xpatcher/`, human-inspectable and crash-recoverable

## Operating Model

1. Create a detailed, reviewable specification before code changes start
2. Keep that specification ephemeral and subordinate to the code
3. Use executable acceptance checks and adversarial review to drive implementation
4. Treat the shipped source tree as the only long-term source of truth

## Key Design Constraints

- Single feature at a time (no concurrent pipelines)
- Single branch per feature from main/master
- xpatcher never merges to main -- that is always a human action via PR
- Runtime artifacts stored outside target repo under `$XPATCHER_HOME/.xpatcher/`
- Sequential task execution (parallel with git worktrees not yet implemented)
- Sync dispatcher (`subprocess.run()`); async with streaming not yet implemented

## Requirements

- Python 3.10+
- Claude Code CLI installed and authenticated
- A git repository to run pipelines against

## Detailed Design

The current architecture is documented in [architecture-snapshot.md](architecture-snapshot.md), derived from the actual code. Historical design proposals are archived under [ephemeral/proposals/](ephemeral/proposals/) and may not match current implementation.

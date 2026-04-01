# Project Map

## Source Code

```
src/
  dispatcher/
    core.py          -- Main dispatch loop, CLI entry point, TUI renderer
    state.py         -- Pipeline state machine with persistence (PipelineState, TaskState enums)
    session.py       -- Claude CLI invocation wrapper (claude -p), session management
    schemas.py       -- Pydantic models for all structured agent output
    parallel.py      -- Thread pool for concurrent agents (not yet wired)
    retry.py         -- Exponential backoff retry logic
    tui.py           -- Live terminal output (progress, logs) via Rich
    yaml_utils.py    -- YAML serialization helpers
  context/
    builder.py       -- Prompt assembly per agent stage
    diff.py          -- Git diff context extraction
    memory.py        -- Cross-session memory interface
    prompts.yaml     -- Prompt templates per stage
  artifacts/
    collector.py     -- Gather outputs from agents
    store.py         -- Persist artifacts to $XPATCHER_HOME/.xpatcher/
```

## Claude Code Plugin

```
.claude-plugin/
  plugin.json        -- Plugin manifest (name, version, description)
  settings.json      -- Default settings (default agent, etc.)
  agents/
    planner.md       -- Strategic planning (Opus[1m])
    plan-reviewer.md -- Plan and task-manifest review (Opus)
    executor.md      -- Code execution (Sonnet, Opus for critical path)
    reviewer.md      -- Code review (Opus)
    tester.md        -- Test generation/execution (Sonnet)
    simplifier.md    -- Code simplification (Sonnet)
    gap-detector.md  -- Spec-to-code gap analysis (Opus)
    tech-writer.md   -- Documentation updates (Sonnet)
    explorer.md      -- Read-only codebase exploration (Haiku)
  hooks/
    pre_tool_use.py  -- Enforce read-only, scope, safety per agent
    post_tool_use.py -- Audit logging, artifact capture
    run_hook.sh      -- Shell wrapper for hook invocation
  skills/
    pipeline/SKILL.md -- /xpatcher:pipeline (full pipeline run)
    status/SKILL.md   -- /xpatcher:status (pipeline status)
```

## Tests

```
tests/
  test_core.py         -- Dispatcher core logic
  test_state.py        -- State machine transitions
  test_session.py      -- Claude CLI session wrapper
  test_schemas.py      -- Pydantic schema validation
  test_retry.py        -- Retry/backoff logic
  test_tui.py          -- TUI rendering
  test_artifacts.py    -- Artifact persistence
  test_context.py      -- Prompt/context building
  test_hooks.py        -- Hook enforcement
  test_e2e_pipeline.py -- End-to-end pipeline integration
```

## Configuration

```
config.yaml       -- Global defaults: models, concurrency, iterations, quality tiers, gates, timeouts
pyproject.toml     -- Package metadata, pytest config, coverage settings
VERSION            -- Semver (currently 0.1.0)
install.sh         -- Per-user installer (~/xpatcher/)
bin/xpatcher       -- CLI entry point wrapper
```

## Documentation

```
docs/
  architecture-snapshot.md           -- Current architecture derived from the codebase
  ephemeral/                         -- Historical design docs (may not match code)
    proposals/                       -- Original design specification (11 subdocuments)
    findings/                        -- Research brainstorming sessions (5 documents)
    reviews/                         -- Dated expert reviews and validation results
    pipelines.yaml                   -- Test pipeline registry data from validation runs
  TODO.md                            -- Deferred features and known gaps
```

## Runtime Artifacts (created during pipeline runs)

```
$XPATCHER_HOME/.xpatcher/
  pipelines/<project-slug>.yaml    -- Per-project pipeline index
  projects/<project-hash>/<feature>/
    intent.yaml                    -- Stage 1
    plan-v{N}.yaml                 -- Stage 2/4
    plan-review-v{N}.yaml          -- Stage 3
    task-manifest.yaml             -- Stage 6
    task-review-v{N}.yaml          -- Stage 7
    execution-plan.yaml            -- Stage 9
    gap-report-v{N}.yaml           -- Stage 14
    docs-report.yaml               -- Stage 15
    completion.yaml                -- Stage 16
    pipeline-state.yaml            -- Mutable state singleton
    sessions.yaml                  -- Session registry
    decisions/                     -- Human gate decisions
    tasks/{todo,in-progress,done}/ -- Per-task specs + execution artifacts
    logs/                          -- JSONL agent logs
```

# Testing

## Testing xpatcher Itself

Run all tests:
```bash
pytest -q
```

Run with coverage:
```bash
pytest --cov=src --cov-report=term-missing
```

Coverage threshold: 60% (configured in `pyproject.toml`).

### Test Categories

| File | Scope |
|------|-------|
| `test_core.py` | Dispatcher core logic, stage progression |
| `test_state.py` | State machine transitions, persistence, crash recovery |
| `test_session.py` | Claude CLI invocation wrapper |
| `test_schemas.py` | Pydantic schema validation (contract tests) |
| `test_retry.py` | Retry/backoff logic |
| `test_tui.py` | TUI rendering |
| `test_artifacts.py` | Artifact persistence to disk |
| `test_context.py` | Prompt/context building |
| `test_hooks.py` | Hook enforcement (read-only, scope) |
| `test_e2e_pipeline.py` | End-to-end pipeline integration (filesystem + state) |

### Testing Principles

- Contract tests validate Pydantic schemas match what agents actually produce
- E2E tests use real filesystem operations, not mocks
- State machine tests verify every valid and invalid transition
- No database -- all state is YAML files, so tests can use tmp directories

## Quality Framework for Generated Code

xpatcher validates agent-generated code through a multi-layer quality loop (Stages 12-13).

### Quality Tiers (configured in `config.yaml`)

| Tier | Coverage | Negation Check | Mutation | LLM Audit | Flaky Detection |
|------|----------|---------------|----------|-----------|-----------------|
| **lite** | 60% new code | No | No | No | No |
| **standard** | 80% new code | Yes (must_pass) | No | No | Yes (3 reps) |
| **thorough** | 80% new code | Yes (must_pass) | Yes (60% kill rate) | Yes | Yes (5 reps) |

### Per-Task Quality Loop

```
Task code committed
  -> TEST: run acceptance criteria commands + regression suite
  -> REVIEW: adversarial code review (fresh session)
  -> SIMPLIFY (optional): simplification pass, reverted if tests break
  -> PASS or FIX ITERATION (max 3)
```

### Acceptance Criteria Rules

- Every task MUST have at least one fully automatable criterion
- Criteria tested by the dispatcher (harness), not self-reported by the agent
- `must_pass` criteria block task completion
- `should_pass` criteria generate warnings but do not block

### Regression Prevention

- Pre-existing tests re-run after every task execution
- Simplification commits are auto-reverted if they break tests

## Full Specification

Historical design spec: [ephemeral/proposals/design/06-quality-testing.md](ephemeral/proposals/design/06-quality-testing.md) (may not match current implementation).

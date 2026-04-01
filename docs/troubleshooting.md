# Troubleshooting

## Known Issues

### Cancel does not stop a running dispatcher
`xpatcher cancel` updates persisted state to `cancelled` but does **not** interrupt the active dispatcher process. The running process continues until the next state transition, then crashes with `InvalidTransitionError: cancelled -> <next_stage>`.

**Workaround:** After cancelling, Ctrl+C the running process manually.

### Gap re-entry state transition (fixed)
Previously, when gap re-entry task execution failed, the pipeline was left in an intermediate state. Now the dispatcher transitions to `blocked` with gate reason `gap_execution_failed`, so the pipeline can be resumed after human intervention.

### Planner produces malformed acceptance commands (mitigated)
The planner/task-manifest sometimes omits or produces empty `command` fields for acceptance criteria. Missing commands are now tracked in the verification summary but no longer counted as failures. Tasks proceed to code review instead of being marked `stuck`. If real command-based checks pass, the task advances normally regardless of missing command specs.

## Debugging Workflow

### Inspect pipeline state
```bash
xpatcher status <pipeline-id>
xpatcher logs <pipeline-id> --tail 50
```

### Check raw state files
All state is YAML, human-readable:
```bash
# Find the pipeline artifacts
cat $XPATCHER_HOME/.xpatcher/pipelines/<project-slug>.yaml

# Read pipeline state
cat $XPATCHER_HOME/.xpatcher/projects/<hash>/<feature>/pipeline-state.yaml

# Read sessions
cat $XPATCHER_HOME/.xpatcher/projects/<hash>/<feature>/sessions.yaml

# Check agent logs (JSONL)
tail -20 $XPATCHER_HOME/.xpatcher/projects/<hash>/<feature>/logs/<agent>-*.jsonl
```

### Stuck tasks
Tasks marked `stuck` are moved back to `tasks/todo/`. To resolve:
1. Inspect the task spec and quality report
2. Fix manually if needed
3. Run `xpatcher resume <pipeline-id>`
4. Or skip: `xpatcher skip <pipeline-id> <task-id>`

### Session issues
- Stale sessions (>4 hours) are automatically abandoned on resume
- Review agents always use fresh sessions for adversarial isolation
- Session context usage is tracked; sessions are compacted at 70% and abandoned at 90%

## Common Failure Modes

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| `InvalidTransitionError` | State machine doesn't allow the attempted transition | Check current stage in `pipeline-state.yaml`; may need manual state edit |
| Schema validation error on agent output | Agent produced output not matching Pydantic model | Check `schemas.py` for the expected format; may need prompt refinement |
| Agent output is empty or malformed YAML | Context window exhaustion or model timeout | Increase timeout in `config.yaml`; check logs for truncation |
| Pipeline stuck at human gate | Waiting for approval | Run `xpatcher pending` to see what needs attention |
| Tests pass but task marked `stuck` | Reviewer keeps rejecting code | Inspect review findings; may need manual fix or `xpatcher skip` |


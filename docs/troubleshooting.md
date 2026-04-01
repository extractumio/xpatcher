# Troubleshooting

## Known Issues

### Cancel does not stop a running dispatcher
`xpatcher cancel` updates persisted state to `cancelled` but does **not** interrupt the active dispatcher process. The running process continues until the next state transition, then crashes with `InvalidTransitionError: cancelled -> <next_stage>`.

**Workaround:** After cancelling, Ctrl+C the running process manually.

### Gap re-entry state transition crash
When gap detection finds issues and triggers re-entry into task breakdown, the dispatcher can crash with `InvalidTransitionError: task_breakdown -> blocked`. The gap re-entry path does not handle all valid downstream transitions.

### Planner produces malformed acceptance commands
The planner/task-manifest sometimes omits or produces empty `command` fields for `must_pass` acceptance criteria. This causes the quality loop to mark tasks as `stuck` even when the produced code is correct, because the dispatcher cannot run the acceptance check.

### Quality loop false negatives
The orchestrator can report task-level `stuck` states even when committed code is locally correct, because acceptance-command quality dominates the control loop. If the planner doesn't produce runnable commands, the quality loop fails.

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
| Tests pass but task marked `stuck` | Empty acceptance command fields | Inspect task spec; planner didn't produce runnable commands |


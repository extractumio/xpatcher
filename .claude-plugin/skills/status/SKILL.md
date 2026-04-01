---
name: xpatcher:status
description: Read pipeline-state.yaml and display a human-readable status summary showing current stage, task progress, elapsed time, and cost.
disable-model-invocation: true
---

# xpatcher:status

Display a human-readable pipeline status summary using the dispatcher CLI.

## Usage

```
/xpatcher:status
```

## Behavior

1. Run the status command via the dispatcher CLI:
   ```bash
   xpatcher status
   ```
2. The dispatcher will:
   - Read the current pipeline state
   - Parse pipeline metadata and task statuses
   - Compute summary statistics
   - Format a human-readable report

## Output

A human-readable status summary showing:
- **Current stage** -- which pipeline phase is active (plan, execute, review, test, etc.)
- **Task progress** -- counts of tasks by status (todo / in-progress / done)
- **Elapsed time** -- wall-clock time since the pipeline started
- **Cost so far** -- accumulated API/compute cost for the pipeline run

## Notes

- This skill is for manual/debug invocation. The normal workflow uses the dispatcher CLI directly.
- If no pipeline is currently active, the status command will report that no active pipeline was found.
- This is a read-only operation; it does not modify pipeline state.

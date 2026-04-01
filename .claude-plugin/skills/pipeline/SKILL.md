---
name: xpatcher:pipeline
description: Invoke the full Software Driven Development (SDD) dispatcher pipeline. This is the main entry point for running the complete automation. Takes a feature description as input.
disable-model-invocation: true
---

# xpatcher:pipeline

Invoke the full dispatcher pipeline to run the complete Software Driven Development (SDD) automation end-to-end.

## Usage

```
/xpatcher:pipeline <feature-description>
```

## Behavior

1. Parse the feature description from `$ARGUMENTS`.
2. Launch the full pipeline via shell:
   ```bash
   xpatcher start "$ARGUMENTS"
   ```
3. The dispatcher will orchestrate all stages in sequence:
   - **Plan** -- analyze the codebase and produce a structured implementation plan
   - **Execute** -- implement each task from the plan
   - **Review** -- adversarial code review of each task
   - **Verify** -- run dispatcher-controlled acceptance checks and task review
   - **Detect gaps** -- find missing requirements and integration issues
   - **Update docs** -- update documentation to reflect changes
4. Pipeline state is tracked in `pipeline-state.yaml` throughout execution.

## Output

The full pipeline output including results from each stage. Use `/xpatcher:status` to monitor progress during execution.

## Notes

- This is the main entry point for running the complete SDD automation.
- This skill is for manual/debug invocation. The normal workflow invokes the dispatcher CLI directly.
- The pipeline can be monitored with `/xpatcher:status` while running.

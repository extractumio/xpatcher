# Skill Definitions and Hooks

> Part of the [xpatcher Design Proposal](../xpatcher-design-proposal.md)

---

## 7.5 Skill Definitions

> **Note:** The primary user interface is the `xpatcher` CLI dispatcher command (see Section 7.1). Individual skills below are available for debugging, manual intervention, or advanced usage but are NOT the normal workflow. They have `disable-model-invocation: true` to prevent Claude from auto-triggering them.

### /xpatcher:plan

```markdown
---
name: plan
description: >
  Analyze requirements and produce a structured implementation plan.
  Usage: /xpatcher:plan <description of what to build or change>
disable-model-invocation: true
allowed-tools:
  - Read
  - Glob
  - Grep
  - Bash
  - Agent
agent: planner
---

# xpatcher Plan

Create an implementation plan for the following task:

$ARGUMENTS

## Context

!`git log --oneline -10 2>/dev/null || echo "No git history"`

!`ls -la 2>/dev/null`

Analyze the codebase thoroughly, then produce a structured YAML plan
as specified in your agent instructions.
```

### /xpatcher:execute

```markdown
---
name: execute
description: >
  Execute an implementation plan or a specific task from a plan.
  Usage: /xpatcher:execute [task description or plan reference]
disable-model-invocation: true
allowed-tools:
  - Read
  - Edit
  - Write
  - Glob
  - Grep
  - Bash
  - LSP
  - NotebookEdit
  - Agent
agent: executor
---

# xpatcher Execute

Implement the following:

$ARGUMENTS

## Current State

!`cat .xpatcher/current-feature/plan-v*.yaml 2>/dev/null || echo "No active plan."`

!`git diff --stat HEAD 2>/dev/null || echo "No git changes"`

Follow the plan if one exists. Otherwise, implement directly from the description.
```

### /xpatcher:review

```markdown
---
name: review
description: >
  Review recent code changes for quality, correctness, and adherence to plan.
  Usage: /xpatcher:review [optional focus area]
disable-model-invocation: true
allowed-tools:
  - Read
  - Glob
  - Grep
  - Bash
  - LSP
  - Agent
agent: reviewer
---

# xpatcher Review

Review the recent code changes.

$ARGUMENTS

## Changes to Review

!`git diff HEAD 2>/dev/null || echo "No uncommitted changes"`

!`git log --oneline -5 2>/dev/null`

## Plan Context

!`cat .xpatcher/current-feature/plan-v*.yaml 2>/dev/null || echo "No active plan"`

Produce a structured review as specified in your agent instructions.
```

### /xpatcher:test

```markdown
---
name: test
description: >
  Generate and run tests for recent code changes.
  Usage: /xpatcher:test [optional focus area or file pattern]
disable-model-invocation: true
allowed-tools:
  - Read
  - Edit
  - Write
  - Glob
  - Grep
  - Bash
  - LSP
  - Agent
agent: tester
---

# xpatcher Test

Generate and run tests for recent changes.

$ARGUMENTS

## What Changed

!`git diff --name-only HEAD 2>/dev/null || echo "No changes detected"`

## Existing Test Structure

!`find . -name "test_*" -o -name "*_test.*" -o -name "*.spec.*" -o -name "*.test.*" | head -30 2>/dev/null || echo "No test files found"`

Write and run tests as specified in your agent instructions.
```

### /xpatcher:simplify

```markdown
---
name: simplify
description: >
  Analyze recent changes for unnecessary complexity and apply simplifications.
  Usage: /xpatcher:simplify [--dry-run] [optional file pattern]
disable-model-invocation: true
allowed-tools:
  - Read
  - Edit
  - Glob
  - Grep
  - Bash
  - LSP
  - Agent
agent: simplifier
---

# xpatcher Simplify

Analyze and simplify recent code changes.

$ARGUMENTS

## Recently Modified Files

!`git diff --name-only HEAD 2>/dev/null || echo "No changes detected"`

If "--dry-run" appears in the arguments above, use dryRun mode. Otherwise use apply mode.
```

### /xpatcher:detect-gaps

```markdown
---
name: detect-gaps
description: >
  Detect gaps between plan and implementation: missing error handling,
  untested paths, incomplete work.
  Usage: /xpatcher:detect-gaps
disable-model-invocation: true
allowed-tools:
  - Read
  - Glob
  - Grep
  - Bash
  - LSP
  - Agent
agent: gap-detector
---

# xpatcher Gap Detection

Analyze the implementation for gaps.

$ARGUMENTS

## Plan

!`cat .xpatcher/current-feature/plan-v*.yaml 2>/dev/null || echo "No active plan"`

## Changes

!`git diff HEAD 2>/dev/null || echo "No uncommitted changes"`

Analyze for gaps as specified in your agent instructions.
```

### /xpatcher:update-docs

```markdown
---
name: update-docs
description: >
  Update project documentation to reflect implemented code changes.
  Runs automatically after gap detection passes. Can also be invoked manually.
  Usage: /xpatcher:update-docs [optional focus area or file pattern]
disable-model-invocation: true
allowed-tools:
  - Read
  - Edit
  - Write
  - Glob
  - Grep
  - Bash
  - Agent
agent: tech-writer
---

# xpatcher Documentation Update

Update documentation for recent code changes.

$ARGUMENTS

## Plan Context

!`cat .xpatcher/current-feature/plan-v*.yaml 2>/dev/null || echo "No active plan"`

## Code Changes

!`git diff main...HEAD --stat 2>/dev/null || git diff HEAD --stat 2>/dev/null || echo "No changes detected"`

## Existing Documentation

!`find . -maxdepth 3 -name "*.md" -o -name "*.rst" -o -name "CHANGELOG*" | head -20 2>/dev/null || echo "No docs found"`

## Completed Tasks

!`ls .xpatcher/current-feature/tasks/done/ 2>/dev/null || echo "No completed tasks"`

Update or create documentation as specified in your agent instructions.
```

### /xpatcher:status

```markdown
---
name: status
description: >
  Show the current pipeline status: active stage, completed stages, recent results.
  Usage: /xpatcher:status
disable-model-invocation: true
allowed-tools:
  - Read
  - Bash
model: haiku
---

# xpatcher Pipeline Status

Show the current state of the xpatcher pipeline.

## Pipeline State

!`cat .xpatcher/current-feature/pipeline-state.yaml 2>/dev/null || echo "No active pipeline"`

## Recent Results

!`ls -lt .xpatcher/current-feature/tasks/done/ 2>/dev/null | head -10 || echo "No results yet"`

## Current Plan

!`cat .xpatcher/current-feature/plan-v*.yaml 2>/dev/null | python3 -c "import sys,yaml; d=yaml.safe_load(sys.stdin); print(d.get('summary','No summary'))" 2>/dev/null || echo "No active plan"`

Summarize concisely. Show completed stages, pending work, blockers.
```

### /xpatcher:pipeline

```markdown
---
name: pipeline
description: >
  Run the full SDLC pipeline: plan -> execute -> review -> test -> simplify.
  Usage: /xpatcher:pipeline <task description>
disable-model-invocation: true
allowed-tools:
  - Bash
  - Read
model: sonnet
---

# xpatcher Full Pipeline

Run the complete SDLC automation pipeline.

## Task
$ARGUMENTS

## Execution

This skill invokes the Python dispatcher to orchestrate the full pipeline.

```bash
xpatcher start "$ARGUMENTS" --project "$(pwd)"
```

Monitor the pipeline state:

```bash
cat .xpatcher/current-feature/pipeline-state.yaml
```

Report the final results when the pipeline completes.
```

## 7.6 Hook Specifications

### PreToolUse Hook: Policy Enforcement

The PreToolUse hook enforces seven policies. Policies are evaluated in order; the first match blocks.

| Policy | Rule | Blocked Action |
|--------|------|----------------|
| Read-only agents | Planner, reviewer, gap-detector, explorer cannot use Edit/Write | Write attempts by read-only agents |
| Bash write enforcement | Read-only agents cannot use Bash to write files | `echo > file`, `cat > file`, `tee`, `sed -i`, `mv`, `cp` to project paths |
| Tester scope | Tester can only write to files matching test patterns | Production code edits by tester |
| Tech-writer scope | Tech-writer can only write to doc files (`.md`, `.rst`, `.txt`, `CHANGELOG`) | Non-documentation edits by tech-writer |
| Project boundary | No agent can write outside project directory | Path traversal |
| Dangerous commands | Block `rm -rf /`, `chmod 777`, `dd if=`, etc. | Destructive bash commands |
| Executor isolation | Executor cannot use WebSearch/WebFetch | Web access during execution |

```python
#!/usr/bin/env python3
# hooks/pre_tool_use.py

import json, sys, os, re
from pathlib import Path

hook_input = json.loads(sys.stdin.read())
tool_name = hook_input.get("tool_name", "")
tool_input = hook_input.get("tool_input", {})
agent_name = os.environ.get("CLAUDE_AGENT_NAME", "")

READ_ONLY_AGENTS = {"planner", "reviewer", "gap-detector", "explorer"}
WRITE_TOOLS = {"Edit", "Write", "NotebookEdit"}

# ── Per-agent Bash command allowlists ────────────────────────────
# Read-only agents can only run these Bash commands.
# Any command not matching the allowlist is blocked.
BASH_ALLOWLISTS = {
    "planner":       {"git", "ls", "wc"},
    "reviewer":      {"git", "ls", "wc", "python"},
    "gap-detector":  {"git", "ls", "wc", "python"},
    "explorer":      {"git", "ls", "wc", "file", "du"},
    # Note: simplifier is NOT in BASH_ALLOWLISTS — it has full Bash access
    # because it uses native /simplify which needs to run tests after changes.
    "tech-writer":   {"git", "ls", "wc"},
}

# Patterns that indicate Bash-mediated file writes.
# These bypass Edit/Write tool checks, so must be caught here.
BASH_WRITE_PATTERNS = [
    r"[>|]",              # Redirect or pipe to file
    r"\btee\b",           # tee writes to files
    r"\bsed\b.*-i",       # sed in-place edit
    r"\bawk\b.*-i",       # awk in-place (gawk)
    r"\bdd\b.*of=",       # dd output file
    r"\bmv\b",            # move (can overwrite)
    r"\bcp\b",            # copy (can create/overwrite)
    r"\bchmod\b",         # permission changes
    r"\bchown\b",         # ownership changes
    r"\bmkdir\b",         # directory creation
    r"\btouch\b",         # file creation
    r"\brm\b",            # file deletion
    r"\bpatch\b",         # patch application
    r"\binstall\b",       # file installation
]

# Policy: Read-only agents cannot use write tools
if agent_name in READ_ONLY_AGENTS and tool_name in WRITE_TOOLS:
    print(json.dumps({"decision": "block",
        "reason": f"Agent '{agent_name}' is read-only"}))
    sys.exit(2)

# Policy: Bash command enforcement for agents with allowlists
if tool_name == "Bash" and agent_name in BASH_ALLOWLISTS:
    command = tool_input.get("command", "")
    allowlist = BASH_ALLOWLISTS[agent_name]

    # Extract the base command (first word, ignoring env vars)
    # Handles: "VAR=x cmd args", "cmd args", "/usr/bin/cmd"
    stripped = re.sub(r"^\s*(\w+=\S+\s+)*", "", command)
    base_cmd = os.path.basename(stripped.split()[0]) if stripped.split() else ""

    # Check 1: Base command must be in allowlist
    if base_cmd not in allowlist:
        print(json.dumps({"decision": "block",
            "reason": f"Agent '{agent_name}' can only run: {', '.join(sorted(allowlist))}. "
                      f"Blocked: '{base_cmd}'"}))
        sys.exit(2)

    # Check 2: Even allowed commands cannot use write patterns
    # (e.g., "git" is allowed but "git checkout -- file > out" is not)
    for pattern in BASH_WRITE_PATTERNS:
        if re.search(pattern, command):
            print(json.dumps({"decision": "block",
                "reason": f"Agent '{agent_name}' is read-only. "
                          f"Blocked write pattern in Bash: '{command[:80]}'"}))
            sys.exit(2)

    # Check 3: Block command chaining that could smuggle writes
    # Semicolons, &&, ||, $(), backticks can chain arbitrary commands
    if agent_name in READ_ONLY_AGENTS:
        if re.search(r"[;]|\$\(|`|&&|\|\|", command):
            # Allow simple pipes (single |) to grep/head/tail/wc/sort/uniq
            # Block everything else
            SAFE_PIPE_TARGETS = {"grep", "head", "tail", "wc", "sort", "uniq",
                                 "cut", "awk", "jq", "yq", "less", "more", "cat"}
            pipe_parts = command.split("|")
            if len(pipe_parts) > 1:
                for part in pipe_parts[1:]:
                    part_cmd = part.strip().split()[0] if part.strip().split() else ""
                    if os.path.basename(part_cmd) not in SAFE_PIPE_TARGETS:
                        print(json.dumps({"decision": "block",
                            "reason": f"Agent '{agent_name}' cannot chain commands. "
                                      f"Blocked: '{command[:80]}'"}))
                        sys.exit(2)
            else:
                print(json.dumps({"decision": "block",
                    "reason": f"Agent '{agent_name}' cannot use command chaining. "
                              f"Blocked: '{command[:80]}'"}))
                sys.exit(2)

# Policy: Tester can only write to test files
TEST_PATTERNS = ["test_", "_test.", ".test.", ".spec.", "tests/", "__tests__/"]
if agent_name == "tester" and tool_name in WRITE_TOOLS:
    file_path = tool_input.get("file_path", "")
    if not any(p in file_path for p in TEST_PATTERNS):
        print(json.dumps({"decision": "block",
            "reason": f"Tester can only write test files: '{file_path}'"}))
        sys.exit(2)

# Policy: Tech-writer can only write to documentation files
DOC_PATTERNS = [".md", ".rst", ".txt", "CHANGELOG", "README", "docs/", "doc/"]
if agent_name == "tech-writer" and tool_name in WRITE_TOOLS:
    file_path = tool_input.get("file_path", "")
    if not any(p in file_path for p in DOC_PATTERNS):
        print(json.dumps({"decision": "block",
            "reason": f"Tech-writer can only write doc files: '{file_path}'"}))
        sys.exit(2)

# Policy: No writing outside project directory
if tool_name in WRITE_TOOLS:
    file_path = tool_input.get("file_path", "")
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd())
    try:
        if not str(Path(file_path).resolve()).startswith(
                str(Path(project_dir).resolve())):
            print(json.dumps({"decision": "block",
                "reason": f"Cannot write outside project: {file_path}"}))
            sys.exit(2)
    except Exception:
        pass

# Policy: Block dangerous bash commands
DANGEROUS = ["rm -rf /", "rm -rf ~", "chmod 777", ":(){ :|:& };:",
             "dd if=", "mkfs.", "> /dev/sd"]
if tool_name == "Bash":
    command = tool_input.get("command", "")
    for pattern in DANGEROUS:
        if pattern in command:
            print(json.dumps({"decision": "block",
                "reason": f"Blocked dangerous pattern: {pattern}"}))
            sys.exit(2)

# Policy: Executor cannot access web
if agent_name == "executor" and tool_name in {"WebSearch", "WebFetch"}:
    print(json.dumps({"decision": "block",
        "reason": "Executor cannot access the web"}))
    sys.exit(2)

print(json.dumps({"decision": "allow"}))
sys.exit(0)
```

### PostToolUse Hook: Audit Logging

Logs every tool call to a JSONL file for audit trail. Tracks which files were actually modified (not just what the agent claims).

### Lifecycle Hook: Agent Tracking

Tracks agent start/stop events. Maintains an `active.yaml` file listing currently running agents with PIDs and start times. Enables the dispatcher to detect hangs and clean up after crashes.

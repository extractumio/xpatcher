#!/usr/bin/env python3
"""PostToolUse hook: audit logging for xpatcher agents.

Receives JSON on stdin with: tool_name, tool_input, tool_result, duration_ms.
Logs every tool call to a JSONL file at .xpatcher/<feature>/logs/.

Log format (one JSON object per line):
  {"ts":"<ISO8601>","event":"tool_call","agent":"<name>","tool":"<name>",
   "input":{...},"duration_ms":<N>}
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Read hook input
# ---------------------------------------------------------------------------
try:
    hook_input = json.loads(sys.stdin.read())
except (json.JSONDecodeError, EOFError):
    sys.exit(0)

tool_name = hook_input.get("tool_name", "")
tool_input = hook_input.get("tool_input", {})
tool_result = hook_input.get("tool_result", "")
duration_ms = hook_input.get("duration_ms", 0)
agent_name = os.environ.get("CLAUDE_AGENT_NAME", "")

# If no agent name, we're not in an xpatcher session -- skip logging
if not agent_name:
    sys.exit(0)

# ---------------------------------------------------------------------------
# Determine log directory
# ---------------------------------------------------------------------------
# Look for the .xpatcher directory in the project.
# CLAUDE_PROJECT_DIR is set by the dispatcher; fall back to cwd.
project_dir = os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd())
xpatcher_dir = Path(project_dir) / ".xpatcher"

# Find the current feature directory.
# Convention: .xpatcher/current-feature is a symlink or directory name.
current_feature = xpatcher_dir / "current-feature"
if current_feature.is_symlink() or current_feature.is_dir():
    log_dir = current_feature / "logs"
else:
    # Fall back to a shared logs directory
    log_dir = xpatcher_dir / "logs"

# Create log directory if it doesn't exist
try:
    log_dir.mkdir(parents=True, exist_ok=True)
except OSError:
    # If we can't create the log directory, skip logging silently
    sys.exit(0)

# ---------------------------------------------------------------------------
# Build log entry
# ---------------------------------------------------------------------------
timestamp = datetime.now(timezone.utc).isoformat()

# Truncate large tool_input and tool_result to keep log files manageable.
# Full content is in the Claude session itself; this is for audit trail.
MAX_INPUT_LEN = 2000
MAX_RESULT_LEN = 1000

input_summary = tool_input
if isinstance(tool_input, dict):
    input_summary = {}
    for k, v in tool_input.items():
        if isinstance(v, str) and len(v) > MAX_INPUT_LEN:
            input_summary[k] = v[:MAX_INPUT_LEN] + f"... ({len(v)} chars)"
        else:
            input_summary[k] = v
elif isinstance(tool_input, str) and len(tool_input) > MAX_INPUT_LEN:
    input_summary = tool_input[:MAX_INPUT_LEN] + f"... ({len(tool_input)} chars)"

result_summary = tool_result
if isinstance(tool_result, str) and len(tool_result) > MAX_RESULT_LEN:
    result_summary = tool_result[:MAX_RESULT_LEN] + f"... ({len(tool_result)} chars)"

log_entry = {
    "ts": timestamp,
    "event": "tool_call",
    "agent": agent_name,
    "tool": tool_name,
    "input": input_summary,
    "duration_ms": duration_ms,
}

# Only include result summary if it's non-empty and not too large
if result_summary:
    log_entry["result_preview"] = (
        result_summary[:200] if isinstance(result_summary, str) else str(result_summary)[:200]
    )

# ---------------------------------------------------------------------------
# Write to JSONL log file
# ---------------------------------------------------------------------------
# One log file per agent per day to keep files manageable.
date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
log_file = log_dir / f"agent-{agent_name}-{date_str}.jsonl"

try:
    with open(log_file, "a") as f:
        f.write(json.dumps(log_entry, separators=(",", ":")) + "\n")
except OSError:
    # Logging failure should never block the agent
    pass

sys.exit(0)

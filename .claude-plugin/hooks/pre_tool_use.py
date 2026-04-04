#!/usr/bin/env python3
"""PreToolUse hook: policy enforcement for xpatcher agents.

Receives JSON on stdin with: tool_name, tool_input, and optionally
agent context. Agent name comes from CLAUDE_AGENT_NAME env var.

Outputs JSON: {"decision": "allow"} or {"decision": "block", "reason": "..."}
Exit 0 = allow, Exit 2 = block.
"""

import json
import os
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Read hook input
# ---------------------------------------------------------------------------
try:
    hook_input = json.loads(sys.stdin.read())
except (json.JSONDecodeError, EOFError):
    # If we can't parse input, allow by default (fail-open for robustness)
    print(json.dumps({"decision": "allow"}))
    sys.exit(0)

tool_name = hook_input.get("tool_name", "")
tool_input = hook_input.get("tool_input", {})
if isinstance(tool_input, str):
    try:
        tool_input = json.loads(tool_input)
    except (json.JSONDecodeError, ValueError):
        tool_input = {"command": tool_input}

agent_name = os.environ.get("CLAUDE_AGENT_NAME", "")

# If no agent name is set, we're not in an xpatcher-managed session -- allow
if not agent_name:
    print(json.dumps({"decision": "allow"}))
    sys.exit(0)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
READ_ONLY_AGENTS = {"plan-reviewer", "reviewer", "gap-detector", "explorer"}
BASH_RESTRICTED_AGENTS = READ_ONLY_AGENTS | {"planner"}
WRITE_TOOLS = {"Edit", "Write", "NotebookEdit"}
READ_TOOLS = {"Read", "Grep", "Glob"}

# Per-agent Bash command allowlists.
# Agents in this dict can ONLY run commands whose base name is in the set.
BASH_ALLOWLISTS = {
    "planner":       {"git", "ls", "wc"},
    "plan-reviewer": {"git", "ls", "wc", "tree"},
    "reviewer":      {"git", "ls", "wc"},
    "gap-detector":  {"git", "ls", "wc"},
    "explorer":      {"git", "ls", "wc", "file", "du"},
    "tech-writer":   {"git", "ls", "wc"},
}

# Patterns that indicate Bash-mediated file writes.
BASH_WRITE_PATTERNS = [
    r"[>|]",              # Redirect or pipe to file
    r"\btee\b",           # tee writes to files
    r"\bsed\s+-i",        # sed in-place edit
    r"\bdd\b",            # dd can write to devices/files
    r"\bmv\b",            # move (can overwrite)
    r"\bcp\b",            # copy (can create/overwrite)
    r"\bchmod\b",         # permission changes
    r"\bmkdir\b",         # directory creation
    r"\btouch\b",         # file creation
    r"\brm\b",            # file deletion
    r"\bpatch\b",         # patch application
    r"\binstall\b",       # file installation
]

# Network-accessing commands -- blocked for all agents except planner
# (planner uses WebSearch/WebFetch, NOT curl).
BASH_NETWORK_PATTERNS = [
    r"\bcurl\b",
    r"\bwget\b",
    r"\bnc\b",
    r"\bncat\b",
    r"\bsocat\b",
    r"\bssh\b",
    r"\bscp\b",
    r"\brsync\b",
]

# Command chaining operators that could smuggle writes.
COMMAND_CHAIN_PATTERNS = [r";", r"&&", r"\|\|", r"\$\(", r"`"]

# Safe targets that can appear after a single pipe (|) for read-only agents.
SAFE_PIPE_TARGETS = {
    "grep", "head", "tail", "wc", "sort", "uniq",
    "cut", "awk", "jq", "yq", "less", "more", "cat",
}

# File patterns for scope enforcement.
TEST_FILE_PATTERNS = [r"test_", r"_test\.", r"\.spec\.", r"\.test\.", r"tests/", r"__tests__/"]
DOC_FILE_PATTERNS = [r"\.md$", r"\.rst$", r"\.txt$", r"CHANGELOG", r"README", r"docs/", r"doc/"]
ARTIFACT_FILE_PATTERNS = [
    r"/\.xpatcher/",
    r"/xpatcher/\.xpatcher/",
    r"pipeline-state\.ya?ml$",
    r"intent\.ya?ml$",
    r"plan(-review)?-v\d+\.ya?ml$",
    r"task-review-v\d+\.ya?ml$",
    r"task-manifest\.ya?ml$",
    r"docs-report\.ya?ml$",
    r"gap-report\.ya?ml$",
    r"validation-failures/",
]
SENSITIVE_FILE_PATTERNS = [
    r"(^|/)\.env(\..*)?$",
    r"(^|/)\.npmrc$",
    r"(^|/)\.pypirc$",
    r"(^|/)id_[^/]+$",
    r"\.(pem|key|p12|pfx|crt|cer)$",
    r"(^|/)\.ssh/",
    r"(^|/)secrets?/",
    r"(^|/)credentials?/",
    r"(^|/)data(_backup_[^/]+)?($|/)",
]
BROAD_GLOB_PATTERNS = [
    r"^\*\*/\*\.[^/]+$",
    r"^\*\*/\*$",
    r"/\*\*/\*\.[^/]+$",
    r"/\*\*/\*$",
]

# Dangerous commands that should NEVER run.
DANGEROUS_PATTERNS = [
    "rm -rf /",
    "rm -rf ~",
    "chmod 777",
    ":(){ :|:& };:",
    "dd if=",
    "mkfs.",
    "> /dev/sd",
]


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------
def block(reason: str) -> None:
    """Print block decision and exit."""
    print(json.dumps({"decision": "block", "reason": reason}))
    sys.exit(2)


def _check_pipe_targets(command: str, agent_name: str) -> None:
    """Block pipes to unsafe targets for read-only agents."""
    pipe_parts = command.split("|")
    if len(pipe_parts) > 1:
        for part in pipe_parts[1:]:
            part_tokens = part.strip().split()
            part_cmd = os.path.basename(part_tokens[0]) if part_tokens else ""
            if part_cmd not in SAFE_PIPE_TARGETS:
                block(
                    f"Agent '{agent_name}' is read-only. "
                    f"Blocked unsafe pipe target '{part_cmd}' in: '{command[:80]}'"
                )


def _tool_path_candidates(tool_name: str, tool_input: dict) -> list[str]:
    if tool_name in {"Read", "Edit", "Write", "NotebookEdit"}:
        return [str(tool_input.get("file_path", ""))]
    if tool_name == "Grep":
        return [str(tool_input.get("path", ""))]
    if tool_name == "Glob":
        return [str(tool_input.get("path", ""))]
    return []


# ---------------------------------------------------------------------------
# Policy 1: Read-only agents cannot use Edit/Write/NotebookEdit
# ---------------------------------------------------------------------------
if agent_name in READ_ONLY_AGENTS and tool_name in WRITE_TOOLS:
    block(f"Agent '{agent_name}' is read-only and cannot use {tool_name}")

# Planner may update xpatcher-managed artifacts in place, but not project code.
if agent_name == "planner" and tool_name in WRITE_TOOLS:
    file_path = tool_input.get("file_path", "")
    if not any(re.search(p, file_path) for p in ARTIFACT_FILE_PATTERNS):
        block(
            "Planner may only write or edit xpatcher artifact files "
            f"(plans, manifests, reviews, pipeline state). Blocked: '{file_path}'"
        )

# Sensitive files must never be read during planning/review-style work.
if agent_name in (READ_ONLY_AGENTS | {"planner", "tech-writer", "tester"}) and tool_name in READ_TOOLS:
    for path in _tool_path_candidates(tool_name, tool_input):
        if any(re.search(pattern, path) for pattern in SENSITIVE_FILE_PATTERNS):
            block(
                f"Agent '{agent_name}' cannot access sensitive files or directories during this stage. "
                f"Blocked: '{path}'"
            )

if agent_name in (READ_ONLY_AGENTS | {"planner"}) and tool_name == "Glob":
    pattern = str(tool_input.get("pattern", ""))
    if any(re.search(expr, pattern) for expr in BROAD_GLOB_PATTERNS):
        block(
            f"Agent '{agent_name}' cannot use broad recursive glob patterns during planning/review. "
            f"Blocked: '{pattern}'"
        )

# ---------------------------------------------------------------------------
# Policy 2 & 3 & 4: Bash enforcement for agents with allowlists
# ---------------------------------------------------------------------------
if tool_name == "Bash" and agent_name in BASH_ALLOWLISTS:
    command = tool_input.get("command", "")
    allowlist = BASH_ALLOWLISTS[agent_name]

    # Extract the base command (first word, ignoring leading env vars like VAR=x)
    stripped = re.sub(r"^\s*(\w+=\S+\s+)*", "", command)
    tokens = stripped.split()
    base_cmd = os.path.basename(tokens[0]) if tokens else ""

    # Policy 2: Base command must be in allowlist
    if base_cmd not in allowlist:
        block(
            f"Agent '{agent_name}' can only run: {', '.join(sorted(allowlist))}. "
            f"Blocked: '{base_cmd}'"
        )

    # Policy 3: Even allowed commands cannot use write patterns
    if agent_name in BASH_RESTRICTED_AGENTS:
        for pattern in BASH_WRITE_PATTERNS:
            if re.search(pattern, command):
                # Special case: allow single pipe (|) to safe targets
                if pattern == r"[>|]":
                    if ">" in command:
                        block(
                            f"Agent '{agent_name}' is read-only. "
                            f"Blocked write pattern in Bash: '{command[:80]}'"
                        )
                    _check_pipe_targets(command, agent_name)
                    continue
                block(
                    f"Agent '{agent_name}' is read-only. "
                    f"Blocked write pattern in Bash: '{command[:80]}'"
                )

    # Policy 4: Block command chaining for read-only agents
    if agent_name in BASH_RESTRICTED_AGENTS:
        if re.search(r";|\$\(|`|&&|\|\|", command):
            pipe_parts = command.split("|")
            if len(pipe_parts) > 1:
                _check_pipe_targets(command, agent_name)
            else:
                block(
                    f"Agent '{agent_name}' cannot use command chaining. "
                    f"Blocked: '{command[:80]}'"
                )

# ---------------------------------------------------------------------------
# Policy 5: Tester can only write to test files
# ---------------------------------------------------------------------------
if agent_name == "tester" and tool_name in WRITE_TOOLS:
    file_path = tool_input.get("file_path", "")
    if not any(re.search(p, file_path) for p in TEST_FILE_PATTERNS):
        block(f"Tester can only write test files. Blocked: '{file_path}'")

# ---------------------------------------------------------------------------
# Policy 6: Tech-writer can only write to doc files
# ---------------------------------------------------------------------------
if agent_name == "tech-writer" and tool_name in WRITE_TOOLS:
    file_path = tool_input.get("file_path", "")
    if not any(re.search(p, file_path) for p in DOC_FILE_PATTERNS):
        block(f"Tech-writer can only write doc files. Blocked: '{file_path}'")

# ---------------------------------------------------------------------------
# Policy 7: Block dangerous commands for ALL agents
# ---------------------------------------------------------------------------
if tool_name == "Bash":
    command = tool_input.get("command", "")
    for pattern in DANGEROUS_PATTERNS:
        if pattern in command:
            block(f"Blocked dangerous command pattern: '{pattern}'")

# ---------------------------------------------------------------------------
# Policy 8: Block network commands for all agents except planner
# (Planner uses WebSearch/WebFetch, not curl/wget)
# ---------------------------------------------------------------------------
if tool_name == "Bash":
    command = tool_input.get("command", "")
    if agent_name != "planner":
        for pattern in BASH_NETWORK_PATTERNS:
            if re.search(pattern, command):
                block(
                    f"Agent '{agent_name}' cannot use network commands. "
                    f"Blocked: '{command[:80]}'"
                )
    else:
        # Even planner cannot use curl/wget -- it has WebSearch/WebFetch
        for pattern in BASH_NETWORK_PATTERNS:
            if re.search(pattern, command):
                block(
                    f"Planner must use WebSearch/WebFetch, not Bash network commands. "
                    f"Blocked: '{command[:80]}'"
                )

# ---------------------------------------------------------------------------
# Policy 9: Executor cannot use WebSearch/WebFetch
# (Also enforced via disallowedTools in agent definition, but double-check)
# ---------------------------------------------------------------------------
if agent_name == "executor" and tool_name in {"WebSearch", "WebFetch"}:
    block("Executor cannot access the web")

# ---------------------------------------------------------------------------
# All policies passed
# ---------------------------------------------------------------------------
print(json.dumps({"decision": "allow"}))
sys.exit(0)

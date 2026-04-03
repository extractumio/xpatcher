#!/usr/bin/env bash
# Test: what @agent syntax works for plugin-loaded agents?
# The plugin dir name is ".claude-plugin" so the qualified name is
# ".claude-plugin:file-inspector". But what does @agent-mention need?
#
# Variants:
#   A) @agent-file-inspector (bare name)
#   B) @agent-.claude-plugin:file-inspector (fully qualified)
#   C) Plugin dir renamed to "xpatcher" → @agent-xpatcher:file-inspector

set -euo pipefail

WORKDIR=$(mktemp -d)
PROJECT_DIR="$WORKDIR/project"
mkdir -p "$PROJECT_DIR"
cat > "$PROJECT_DIR/sample.txt" <<'EOF'
Alpha Beta Gamma
EOF

ANTHROPIC_API_KEY=$(python3 -c "
import sys; sys.path.insert(0, '$(pwd)/src')
from pathlib import Path
from dispatcher.auth import resolve_auth_env
env = resolve_auth_env(Path.home() / 'xpatcher')
print(env.get('ANTHROPIC_API_KEY', ''))
")
export ANTHROPIC_API_KEY

make_plugin() {
    local DIR_NAME="$1"
    local PDIR="$WORKDIR/$DIR_NAME"
    rm -rf "$PDIR"
    mkdir -p "$PDIR/agents"
    cat > "$PDIR/plugin.json" <<EOF
{ "name": "xpatcher", "version": "0.1.0" }
EOF
    cat > "$PDIR/agents/file-inspector.md" <<'EOF'
---
name: file-inspector
description: Inspects files. Use for any file inspection task.
model: haiku
maxTurns: 3
tools:
  - Read
---

Read the requested file. Report: LINES=<count>, FIRST=<first line>.
EOF
    echo "$PDIR"
}

run_test() {
    local LABEL="$1"
    local PLUGIN_DIR="$2"
    local PROMPT="$3"

    echo ""
    echo "=== Test $LABEL ==="
    echo "    plugin_dir=$(basename $PLUGIN_DIR)"
    echo "    prompt: $PROMPT"

    OUTPUT=$(cd "$PROJECT_DIR" && claude -p "$PROMPT" \
        --output-format json \
        --plugin-dir "$PLUGIN_DIR" \
        --max-turns 10 \
        --permission-mode bypassPermissions \
        2>/dev/null) || true

    SESSION_ID=$(echo "$OUTPUT" | python3 -c "
import json, sys
events = json.loads(sys.stdin.read())
for e in events:
    if e.get('type') == 'result':
        print(e.get('session_id', '')); break
" 2>/dev/null)

    RESULT=$(echo "$OUTPUT" | python3 -c "
import json, sys
events = json.loads(sys.stdin.read())
for e in events:
    if e.get('type') == 'result':
        print(e.get('result', '')[:200]); break
" 2>/dev/null)

    # Check what happened
    MAIN_JSONL=$(find ~/.claude/projects/ -name "${SESSION_ID}.jsonl" 2>/dev/null | head -1)
    DELEGATED="no"
    AGENT_TYPE=""
    if [ -n "$MAIN_JSONL" ]; then
        AGENT_TYPE=$(python3 -c "
import json
with open('$MAIN_JSONL') as f:
    for line in f:
        e = json.loads(line)
        if e.get('type') == 'assistant':
            for b in e.get('message',{}).get('content',[]):
                if isinstance(b, dict) and b.get('type') == 'tool_use' and b.get('name') == 'Agent':
                    print(b.get('input',{}).get('subagent_type','?'))
                    break
" 2>/dev/null)
    fi

    SESSION_DIR=$(find ~/.claude/projects/ -type d -name "$SESSION_ID" 2>/dev/null | head -1)
    if [ -n "$SESSION_DIR" ] && [ -d "$SESSION_DIR/subagents" ]; then
        META=$(cat "$SESSION_DIR/subagents/"*.meta.json 2>/dev/null | head -1)
        DELEGATED="YES"
        echo "    Agent tool subagent_type: $AGENT_TYPE"
        echo "    Subagent meta: $META"
    else
        echo "    Agent tool subagent_type: ${AGENT_TYPE:-(none)}"
        echo "    No subagent transcripts"
        DELEGATED="no"
    fi
    echo "    Delegated: $DELEGATED"
    echo "    Response: ${RESULT:0:120}"
}

# --- Test A: dir=.claude-plugin, @agent-file-inspector ---
PDIR=$(make_plugin ".claude-plugin")
run_test "A: .claude-plugin dir + @agent-file-inspector" "$PDIR" \
    '@agent-file-inspector inspect sample.txt'

# --- Test B: dir=.claude-plugin, @agent-.claude-plugin:file-inspector ---
PDIR=$(make_plugin ".claude-plugin")
run_test "B: .claude-plugin dir + @agent-.claude-plugin:file-inspector" "$PDIR" \
    '@agent-.claude-plugin:file-inspector inspect sample.txt'

# --- Test C: dir=xpatcher, @agent-xpatcher:file-inspector ---
PDIR=$(make_plugin "xpatcher")
run_test "C: xpatcher dir + @agent-xpatcher:file-inspector" "$PDIR" \
    '@agent-xpatcher:file-inspector inspect sample.txt'

# --- Test D: dir=xpatcher, @agent-file-inspector ---
PDIR=$(make_plugin "xpatcher")
run_test "D: xpatcher dir + @agent-file-inspector" "$PDIR" \
    '@agent-file-inspector inspect sample.txt'

rm -rf "$WORKDIR"
echo ""
echo "Done."

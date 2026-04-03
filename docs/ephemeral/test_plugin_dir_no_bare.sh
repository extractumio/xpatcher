#!/usr/bin/env bash
# Test: --plugin-dir without --bare loads agents for native Agent tool delegation
#
# Usage: bash docs/ephemeral/test_plugin_dir_no_bare.sh

set -euo pipefail

WORKDIR=$(mktemp -d)
PLUGIN_DIR="$WORKDIR/.claude-plugin"
PROJECT_DIR="$WORKDIR/project"
mkdir -p "$PLUGIN_DIR/agents" "$PROJECT_DIR"

cat > "$PROJECT_DIR/sample.txt" <<'EOF'
Line one
Line two
EOF

cat > "$PLUGIN_DIR/plugin.json" <<'EOF'
{ "name": "testplugin", "version": "0.1.0" }
EOF

# Agent defined as .md file in plugin dir (not JSON)
cat > "$PLUGIN_DIR/agents/file-inspector.md" <<'EOF'
---
name: file-inspector
description: Inspects files and reports line count, first line, last line. Use for file analysis tasks.
model: haiku
maxTurns: 3
tools:
  - Read
  - Glob
---

You are the File Inspector. Read the requested file and report exactly:
LINES: <count>
FIRST: <first line>
LAST: <last line>
Nothing else.
EOF

# Resolve auth
ANTHROPIC_API_KEY=$(python3 -c "
import sys; sys.path.insert(0, '$(pwd)/src')
from pathlib import Path
from dispatcher.auth import resolve_auth_env
env = resolve_auth_env(Path.home() / 'xpatcher')
print(env.get('ANTHROPIC_API_KEY', ''))
")
export ANTHROPIC_API_KEY

echo "Auth: OK"
echo "Plugin dir: $PLUGIN_DIR"
echo "Agents: $(ls $PLUGIN_DIR/agents/)"
echo ""

# Run WITHOUT --bare, WITH --plugin-dir
echo ">>> Running: claude -p (no --bare) + --plugin-dir"
OUTPUT=$(cd "$PROJECT_DIR" && claude -p \
    '@agent-file-inspector inspect sample.txt. Report line count, first line, last line.' \
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
        print(e.get('result', '')[:300]); break
" 2>/dev/null)

COST=$(echo "$OUTPUT" | python3 -c "
import json, sys
events = json.loads(sys.stdin.read())
for e in events:
    if e.get('type') == 'result':
        print(f\"{e.get('total_cost_usd', 0):.4f}\"); break
" 2>/dev/null)

# Check init event for loaded agents and plugins
MAIN_JSONL=$(find ~/.claude/projects/ -name "${SESSION_ID}.jsonl" 2>/dev/null | head -1)
echo "Session: $SESSION_ID"
echo "Cost: \$$COST"
echo "Response: $RESULT"
echo ""

if [ -n "$MAIN_JSONL" ]; then
    echo "--- Init event (plugins + agents) ---"
    python3 -c "
import json
with open('$MAIN_JSONL') as f:
    for line in f:
        e = json.loads(line)
        if e.get('type') == 'system' and e.get('subtype') == 'init':
            plugins = e.get('plugins', [])
            agents = e.get('agents', [])
            print(f'  Plugins: {[p.get(\"name\",\"?\") for p in plugins]}')
            print(f'  Agents:  {agents}')
            break
    else:
        print('  (no init event in JSONL)')
" 2>/dev/null

    echo ""
    echo "--- Agent tool usage ---"
    python3 -c "
import json
with open('$MAIN_JSONL') as f:
    for i, line in enumerate(f, 1):
        e = json.loads(line)
        if e.get('type') == 'assistant':
            for b in e.get('message',{}).get('content',[]):
                if isinstance(b, dict) and b.get('type') == 'tool_use' and b.get('name') == 'Agent':
                    inp = b.get('input', {})
                    print(f'  L{i}: Agent tool → type={inp.get(\"subagent_type\",\"(default)\")} desc={inp.get(\"description\",\"?\")[:60]}')
" 2>/dev/null
fi

# Check subagent transcripts
SESSION_DIR=$(find ~/.claude/projects/ -type d -name "$SESSION_ID" 2>/dev/null | head -1)
echo ""
if [ -n "$SESSION_DIR" ] && [ -d "$SESSION_DIR/subagents" ]; then
    echo "--- Subagent transcripts ---"
    for meta in "$SESSION_DIR/subagents/"*.meta.json; do
        [ -f "$meta" ] && echo "  $(basename $meta): $(cat $meta)"
    done
    echo ""
    echo "RESULT: DELEGATED via plugin-dir agents ✓"
else
    echo "RESULT: NOT DELEGATED ✗"
fi

rm -rf "$WORKDIR"
echo ""
echo "Done."

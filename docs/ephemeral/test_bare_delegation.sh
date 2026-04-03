#!/usr/bin/env bash
# Test: does --bare still allow the Agent tool for subagent delegation?
# And: does @-mention syntax work in -p prompts to force delegation?
#
# Three variants:
#   A) --bare + natural language delegation request
#   B) --bare + @agent mention syntax
#   C) no --bare + @agent mention syntax (control)
#
# Usage: bash docs/ephemeral/test_bare_delegation.sh
# Requires: claude CLI authenticated and on PATH

set -euo pipefail

WORKDIR=$(mktemp -d)
PROJECT_DIR="$WORKDIR/project"
mkdir -p "$PROJECT_DIR"

cat > "$PROJECT_DIR/sample.txt" <<'EOF'
Hello from the test project.
This file has 3 lines.
The answer is 42.
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

if [ -z "$ANTHROPIC_API_KEY" ]; then
    echo "FAIL: No auth available."
    rm -rf "$WORKDIR"
    exit 1
fi
echo "Auth: OK"

# Custom subagent definition
AGENTS_JSON=$(python3 -c "
import json
agents = {
    'file-inspector': {
        'description': 'Inspects files and reports line count, first line, last line. Use this agent to analyze file contents.',
        'prompt': 'You are the File Inspector. Read the requested file and report exactly:\nLINES: <count>\nFIRST: <first line>\nLAST: <last line>\nNothing else.',
        'tools': ['Read', 'Glob'],
        'model': 'haiku'
    }
}
print(json.dumps(agents))
")

run_test() {
    local LABEL="$1"
    local BARE_FLAG="$2"
    local PROMPT="$3"

    echo ""
    echo "============================================================"
    echo ">>> Test $LABEL"
    echo "    bare=$BARE_FLAG"
    echo "    prompt=$PROMPT"
    echo "============================================================"

    CMD=(claude)
    [ "$BARE_FLAG" = "yes" ] && CMD+=(--bare)
    CMD+=(-p "$PROMPT"
        --output-format json
        --agents "$AGENTS_JSON"
        --max-turns 10
        --permission-mode bypassPermissions)

    OUTPUT=$(cd "$PROJECT_DIR" && "${CMD[@]}" 2>/dev/null) || true

    SESSION_ID=$(echo "$OUTPUT" | python3 -c "
import json, sys
events = json.loads(sys.stdin.read())
for e in events:
    if e.get('type') == 'result':
        print(e.get('session_id', '')); break
" 2>/dev/null) || true

    RESULT=$(echo "$OUTPUT" | python3 -c "
import json, sys
events = json.loads(sys.stdin.read())
for e in events:
    if e.get('type') == 'result':
        print(e.get('result', '')[:300]); break
" 2>/dev/null) || true

    COST=$(echo "$OUTPUT" | python3 -c "
import json, sys
events = json.loads(sys.stdin.read())
for e in events:
    if e.get('type') == 'result':
        print(f\"{e.get('total_cost_usd', 0):.4f}\"); break
" 2>/dev/null) || true

    echo "    Session: $SESSION_ID"
    echo "    Cost: \$$COST"
    echo "    Response: $RESULT"

    # Check for Agent tool usage in main JSONL
    MAIN_JSONL=$(find ~/.claude/projects/ -name "${SESSION_ID}.jsonl" 2>/dev/null | head -1)
    AGENT_TOOL_USED="no"
    SUBAGENT_FILES=0

    if [ -n "$MAIN_JSONL" ]; then
        AGENT_TOOL_USED=$(python3 -c "
import json
found = False
with open('$MAIN_JSONL') as f:
    for line in f:
        e = json.loads(line)
        if e.get('type') == 'assistant':
            for b in e.get('message',{}).get('content',[]):
                if isinstance(b, dict) and b.get('type') == 'tool_use' and b.get('name') == 'Agent':
                    inp = b.get('input', {})
                    print(f'yes → subagent_type={inp.get(\"subagent_type\",\"(default)\")} desc={inp.get(\"description\",\"?\")[:50]}')
                    found = True
if not found:
    print('no')
" 2>/dev/null)

        # Check for subagent transcripts
        SESSION_DIR=$(find ~/.claude/projects/ -type d -name "$SESSION_ID" 2>/dev/null | head -1)
        if [ -n "$SESSION_DIR" ] && [ -d "$SESSION_DIR/subagents" ]; then
            SUBAGENT_FILES=$(ls -1 "$SESSION_DIR/subagents/"*.jsonl 2>/dev/null | wc -l | tr -d ' ')
            echo "    Subagent transcripts: $SUBAGENT_FILES"
            for meta in "$SESSION_DIR/subagents/"*.meta.json; do
                [ -f "$meta" ] && echo "      $(cat $meta)"
            done
        fi
    fi

    echo "    Agent tool used: $AGENT_TOOL_USED"

    if [[ "$AGENT_TOOL_USED" == yes* ]]; then
        echo "    RESULT: DELEGATED ✓"
    else
        echo "    RESULT: NOT DELEGATED ✗"
    fi
}

# ── Test A: --bare + natural language ────────────────────────────────────────
run_test "A: --bare + natural language" "yes" \
    "Delegate to the file-inspector agent to inspect sample.txt. Report what it found."

# ── Test B: --bare + @agent mention ──────────────────────────────────────────
run_test "B: --bare + @agent mention" "yes" \
    '@agent-file-inspector inspect sample.txt in the current directory. Report: line count, first line, last line.'

# ── Test C: no --bare + @agent mention (control) ────────────────────────────
run_test "C: no --bare + @agent mention" "no" \
    '@agent-file-inspector inspect sample.txt in the current directory. Report: line count, first line, last line.'

# Cleanup
rm -rf "$WORKDIR"
echo ""
echo "============================================================"
echo "Done."

#!/usr/bin/env bash
# Test: main agent delegates to a custom subagent via the native Agent tool
#
# This validates the core mechanism xpatcher should use:
#   1. A main agent session starts (no --agent, just default claude)
#   2. Custom subagents are provided via --agents JSON
#   3. The prompt instructs the main agent to delegate to a specific subagent
#   4. The subagent runs in its own context, produces output, returns to main
#   5. Separate JSONL files appear under {sessionId}/subagents/
#
# Usage: bash docs/ephemeral/test_subagent_delegation.sh
# Requires: claude CLI authenticated and on PATH

set -euo pipefail

WORKDIR=$(mktemp -d)
PLUGIN_DIR="$WORKDIR/.claude-plugin"
PROJECT_DIR="$WORKDIR/project"
mkdir -p "$PLUGIN_DIR/agents" "$PROJECT_DIR"

# Create a small file in the project for the subagent to read
cat > "$PROJECT_DIR/sample.txt" <<'EOF'
Hello from the test project.
This file has 3 lines.
The answer is 42.
EOF

# Minimal plugin.json
cat > "$PLUGIN_DIR/plugin.json" <<'EOF'
{ "name": "delegation-test", "version": "0.1.0" }
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
echo "Auth: key prefix ${ANTHROPIC_API_KEY:0:15}..."

# Define a custom subagent as JSON (the reviewer)
# The main agent will delegate to it via the Agent tool
AGENTS_JSON=$(python3 -c "
import json
agents = {
    'file-inspector': {
        'description': 'Inspects files and reports findings. Use this agent whenever you need to read and analyze a file.',
        'prompt': 'You are the File Inspector agent. When given a file to inspect, read it using the Read tool and report: (1) the number of lines, (2) the first line, (3) the last line. Return your answer in exactly this format:\nLINES: <count>\nFIRST: <first line text>\nLAST: <last line text>',
        'tools': ['Read', 'Glob'],
        'model': 'haiku'
    }
}
print(json.dumps(agents))
")

echo "=== Workdir: $WORKDIR ==="
echo "=== Project: $PROJECT_DIR ==="
echo ""

# ── Run: main agent delegates to file-inspector subagent ─────────────────────
echo ">>> Starting main agent session, asking it to delegate to file-inspector subagent"
echo ""

# NOTE: --bare suppresses the full system prompt (which includes Agent tool docs).
# We need the Agent tool to be available. Drop --bare and use normal mode,
# or provide sufficient instruction. Let's try without --bare first.
OUTPUT=$(cd "$PROJECT_DIR" && claude -p "You have a custom subagent called 'file-inspector' available. Use the Agent tool to delegate to it. Ask it to inspect sample.txt and report the number of lines, first line, and last line. Do NOT read the file yourself — you MUST delegate to the file-inspector agent using the Agent tool with subagent_type='file-inspector'." \
    --output-format json \
    --agents "$AGENTS_JSON" \
    --max-turns 10 \
    --permission-mode bypassPermissions \
    2>/dev/null) || true

# Extract session ID and result
SESSION_ID=$(echo "$OUTPUT" | python3 -c "
import json, sys
events = json.loads(sys.stdin.read())
for e in events:
    if e.get('type') == 'result':
        print(e.get('session_id', ''))
        break
")

RESULT_TEXT=$(echo "$OUTPUT" | python3 -c "
import json, sys
events = json.loads(sys.stdin.read())
for e in events:
    if e.get('type') == 'result':
        print(e.get('result', '')[:500])
        break
")

NUM_TURNS=$(echo "$OUTPUT" | python3 -c "
import json, sys
events = json.loads(sys.stdin.read())
for e in events:
    if e.get('type') == 'result':
        print(e.get('num_turns', 0))
        break
")

COST=$(echo "$OUTPUT" | python3 -c "
import json, sys
events = json.loads(sys.stdin.read())
for e in events:
    if e.get('type') == 'result':
        print(f\"{e.get('total_cost_usd', 0):.4f}\")
        break
")

echo "    Session ID: $SESSION_ID"
echo "    Turns: $NUM_TURNS"
echo "    Cost: \$$COST"
echo ""
echo "    --- Main agent response ---"
echo "    $RESULT_TEXT"
echo ""

# ── Check for subagent JSONL files ───────────────────────────────────────────
echo ">>> Checking for subagent transcripts"

# Find the session directory
PROJECT_SLUG=$(echo "$PROJECT_DIR" | sed 's|/|-|g')
SESSION_DIR="$HOME/.claude/projects/$PROJECT_SLUG/$SESSION_ID"

# Also try CWD-based slug (claude uses CWD, not necessarily project_dir)
CWD_SLUG=$(pwd | sed 's|/|-|g')
SESSION_DIR_ALT="$HOME/.claude/projects/$CWD_SLUG/$SESSION_ID"

for DIR in "$SESSION_DIR" "$SESSION_DIR_ALT"; do
    if [ -d "$DIR/subagents" ]; then
        SESSION_DIR="$DIR"
        break
    fi
done

if [ ! -d "$SESSION_DIR/subagents" ]; then
    echo "    Looking for session dir..."
    SESSION_DIR=$(find ~/.claude/projects/ -type d -name "$SESSION_ID" 2>/dev/null | head -1)
fi

if [ -z "$SESSION_DIR" ] || [ ! -d "$SESSION_DIR" ]; then
    echo "    WARN: No session directory found for $SESSION_ID"
    echo "    Checking if Agent tool was used in the main JSONL..."

    MAIN_JSONL=$(find ~/.claude/projects/ -name "${SESSION_ID}.jsonl" 2>/dev/null | head -1)
    if [ -n "$MAIN_JSONL" ]; then
        echo "    Main JSONL: $MAIN_JSONL"
        echo ""
        echo "    --- Agent tool calls in main JSONL ---"
        python3 -c "
import json
with open('$MAIN_JSONL') as f:
    for i, line in enumerate(f, 1):
        e = json.loads(line)
        if e.get('type') == 'assistant':
            for b in e.get('message',{}).get('content',[]):
                if isinstance(b, dict) and b.get('type') == 'tool_use' and b.get('name') == 'Agent':
                    inp = b.get('input', {})
                    print(f'    L{i}: Agent tool → type={inp.get(\"subagent_type\",\"?\")} desc={inp.get(\"description\",\"?\")[:60]}')
        if e.get('type') == 'agent-setting':
            print(f'    L{i}: agent-setting → {e.get(\"agentSetting\")}')
"
    fi
else
    echo "    Session dir: $SESSION_DIR"
    echo ""
    echo "    --- Subagent transcripts ---"
    ls -la "$SESSION_DIR/subagents/" 2>/dev/null
    echo ""

    echo "    --- Subagent metadata ---"
    for meta in "$SESSION_DIR/subagents/"*.meta.json; do
        [ -f "$meta" ] && echo "    $(basename $meta): $(cat $meta)"
    done
    echo ""

    # Show what the subagent did
    echo "    --- Subagent activity ---"
    for jsonl in "$SESSION_DIR/subagents/"*.jsonl; do
        [ -f "$jsonl" ] || continue
        echo "    File: $(basename $jsonl)"
        python3 -c "
import json
with open('$jsonl') as f:
    for i, line in enumerate(f, 1):
        e = json.loads(line)
        t = e.get('type')
        if t == 'assistant':
            for b in e.get('message',{}).get('content',[]):
                if isinstance(b, dict):
                    if b.get('type') == 'tool_use':
                        name = b.get('name','?')
                        inp = b.get('input',{})
                        detail = str(inp.get('file_path', inp.get('command', inp.get('pattern', ''))))[:60]
                        print(f'        L{i}: {name}: {detail}')
                    elif b.get('type') == 'text':
                        txt = b.get('text','').strip()[:120]
                        if txt:
                            print(f'        L{i}: text: {txt}')
"
    done
fi

echo ""

# ── Verdict ──────────────────────────────────────────────────────────────────
echo "=== VERDICT ==="
if [ -d "$SESSION_DIR/subagents" ] && [ "$(ls -1 "$SESSION_DIR/subagents/"*.jsonl 2>/dev/null | wc -l)" -gt 0 ]; then
    SUBAGENT_COUNT=$(ls -1 "$SESSION_DIR/subagents/"*.jsonl 2>/dev/null | wc -l | tr -d ' ')
    echo "PASS: Main agent delegated to subagent(s). Found $SUBAGENT_COUNT subagent transcript(s)."
    echo "      This confirms the native Agent tool delegation pattern works with custom --agents."
else
    echo "INCONCLUSIVE: No subagent transcripts found."
    echo "      The main agent may have handled it inline instead of delegating."
fi

# Cleanup
rm -rf "$WORKDIR"
echo ""
echo "Done."

#!/usr/bin/env bash
# Test: does --resume with a different --agent actually switch the agent?
#
# Plan:
#   1. Create a temp plugin dir with two agents that have distinct identities
#   2. Start a session with agent-alpha (asks "who are you?")
#   3. Resume the same session with agent-beta (asks "who are you?" again)
#   4. Parse the JSONL to check:
#      a) Which agent-setting events were recorded
#      b) Whether the assistant's self-identification changed
#
# Usage: bash tests/test_agent_resume_switch.sh
# Requires: claude CLI authenticated and on PATH

set -euo pipefail

WORKDIR=$(mktemp -d)
PLUGIN_DIR="$WORKDIR/.claude-plugin"
PROJECT_DIR="$WORKDIR/project"
mkdir -p "$PLUGIN_DIR/agents" "$PROJECT_DIR"

# Minimal plugin.json
cat > "$PLUGIN_DIR/plugin.json" <<'EOF'
{ "name": "switchtest", "version": "0.1.0" }
EOF

# Agent Alpha — identifies as "ALPHA"
cat > "$PLUGIN_DIR/agents/alpha.md" <<'EOF'
---
name: alpha
description: Test agent Alpha
model: haiku
maxTurns: 2
tools:
  - Read
---

You are agent ALPHA. When asked who you are, respond with exactly: I am ALPHA.
Do not add any other text. Just "I am ALPHA."
EOF

# Agent Beta — identifies as "BETA"
cat > "$PLUGIN_DIR/agents/beta.md" <<'EOF'
---
name: beta
description: Test agent Beta
model: haiku
maxTurns: 2
tools:
  - Read
---

You are agent BETA. When asked who you are, respond with exactly: I am BETA.
Do not add any other text. Just "I am BETA."
EOF

# Bake agents JSON (same as xpatcher's bake_agents_json)
python3 -c "
import json, yaml
from pathlib import Path
agents = {}
for md in sorted(Path('$PLUGIN_DIR/agents').glob('*.md')):
    text = md.read_text()
    parts = text.split('---', 2)
    if len(parts) < 3: continue
    meta = yaml.safe_load(parts[1])
    body = parts[2].strip()
    name = meta.get('name', md.stem)
    agents[f'switchtest:{name}'] = {
        'description': meta.get('description',''),
        'prompt': body,
        'model': meta.get('model','haiku'),
        'maxTurns': meta.get('maxTurns', 5),
        'tools': meta.get('tools', []),
    }
print(json.dumps(agents))
" > "$PLUGIN_DIR/agents.json"

# Resolve auth — reuse xpatcher's auth resolver
ANTHROPIC_API_KEY=$(python3 -c "
import sys; sys.path.insert(0, '$(pwd)/src')
from pathlib import Path
from dispatcher.auth import resolve_auth_env
env = resolve_auth_env(Path.home() / 'xpatcher')
print(env.get('ANTHROPIC_API_KEY', ''))
")
export ANTHROPIC_API_KEY

if [ -z "$ANTHROPIC_API_KEY" ]; then
    echo "FAIL: No auth available. Set ANTHROPIC_API_KEY or run 'claude auth login'."
    rm -rf "$WORKDIR"
    exit 1
fi
echo "Auth: key prefix ${ANTHROPIC_API_KEY:0:15}..."

AGENTS_JSON=$(cat "$PLUGIN_DIR/agents.json")

echo "=== Plugin dir: $PLUGIN_DIR ==="
echo "=== Agents JSON: $(echo "$AGENTS_JSON" | python3 -m json.tool | head -5) ... ==="
echo ""

# ── Step 1: Fresh session with agent ALPHA ──────────────────────────────────
echo ">>> Step 1: Starting fresh session with switchtest:alpha"
STEP1_OUTPUT=$(claude --bare -p "Who are you? Reply in one short sentence." \
    --output-format json \
    --plugin-dir "$PLUGIN_DIR" \
    --agents "$AGENTS_JSON" \
    --agent "switchtest:alpha" \
    --max-turns 2 \
    --permission-mode bypassPermissions \
    2>/dev/null) || true

# Extract session ID from output
SESSION_ID=$(echo "$STEP1_OUTPUT" | python3 -c "
import json, sys
events = json.loads(sys.stdin.read())
for e in events:
    if e.get('type') == 'result':
        print(e.get('session_id', ''))
        break
")

if [ -z "$SESSION_ID" ]; then
    echo "FAIL: Could not extract session_id from step 1"
    echo "Raw output: $STEP1_OUTPUT" | head -20
    rm -rf "$WORKDIR"
    exit 1
fi

echo "    Session ID: $SESSION_ID"

# Show alpha's response
ALPHA_RESPONSE=$(echo "$STEP1_OUTPUT" | python3 -c "
import json, sys
events = json.loads(sys.stdin.read())
for e in events:
    if e.get('type') == 'result':
        print(e.get('result', '')[:200])
        break
")
echo "    Alpha said: $ALPHA_RESPONSE"
echo ""

# ── Step 2: Resume same session with agent BETA ─────────────────────────────
echo ">>> Step 2: Resuming session $SESSION_ID with switchtest:beta"
STEP2_OUTPUT=$(claude --bare -p "Who are you? Reply in one short sentence." \
    --output-format json \
    --plugin-dir "$PLUGIN_DIR" \
    --agents "$AGENTS_JSON" \
    --agent "switchtest:beta" \
    --resume "$SESSION_ID" \
    --max-turns 2 \
    --permission-mode bypassPermissions \
    2>/dev/null) || true

BETA_RESPONSE=$(echo "$STEP2_OUTPUT" | python3 -c "
import json, sys
events = json.loads(sys.stdin.read())
for e in events:
    if e.get('type') == 'result':
        print(e.get('result', '')[:200])
        break
")
echo "    Beta said: $BETA_RESPONSE"
echo ""

# ── Step 3: Analyze the JSONL ────────────────────────────────────────────────
echo ">>> Step 3: Analyzing session JSONL"

# Find the JSONL file (Claude stores it under ~/.claude/projects/<slug>/)
PROJECT_SLUG=$(echo "$PROJECT_DIR" | sed 's|/|-|g')
JSONL_FILE="$HOME/.claude/projects/$PROJECT_SLUG/$SESSION_ID.jsonl"

if [ ! -f "$JSONL_FILE" ]; then
    echo "    JSONL not at expected path: $JSONL_FILE"
    echo "    Searching..."
    JSONL_FILE=$(find ~/.claude/projects/ -name "${SESSION_ID}.jsonl" 2>/dev/null | head -1)
fi

if [ -z "$JSONL_FILE" ] || [ ! -f "$JSONL_FILE" ]; then
    echo "    WARN: Could not find JSONL file — analyzing CLI output only"
else
    echo "    JSONL: $JSONL_FILE"
    echo ""
    echo "    --- Agent-setting events and session boundaries ---"
    python3 -c "
import json
with open('$JSONL_FILE') as f:
    for i, line in enumerate(f, 1):
        e = json.loads(line)
        t = e.get('type')
        if t == 'agent-setting':
            print(f'    L{i:3d}: agent-setting → {e.get(\"agentSetting\")}')
        elif t == 'last-prompt':
            print(f'    L{i:3d}: ──── resume boundary ────')
        elif t == 'assistant':
            for b in e.get('message',{}).get('content',[]):
                if isinstance(b, dict) and b.get('type') == 'text':
                    txt = b['text'].strip()[:100]
                    if txt:
                        print(f'    L{i:3d}: assistant: {txt}')
                    break
"
fi

echo ""

# ── Verdict ──────────────────────────────────────────────────────────────────
echo "=== VERDICT ==="
ALPHA_MATCH=false
BETA_MATCH=false
[[ "$ALPHA_RESPONSE" == *"ALPHA"* ]] && ALPHA_MATCH=true
[[ "$BETA_RESPONSE" == *"BETA"* ]] && BETA_MATCH=true

if $ALPHA_MATCH && $BETA_MATCH; then
    echo "PASS: Agent switch works. Alpha identified as ALPHA, Beta identified as BETA."
elif $ALPHA_MATCH && ! $BETA_MATCH; then
    echo "FAIL: Agent did NOT switch. Beta still identified as: $BETA_RESPONSE"
    echo "      --resume preserves the original agent; --agent is ignored on resume."
else
    echo "INCONCLUSIVE: Alpha='$ALPHA_RESPONSE', Beta='$BETA_RESPONSE'"
    echo "      Agents may not have followed identity instructions strictly."
fi

# Cleanup
rm -rf "$WORKDIR"
echo ""
echo "Done."

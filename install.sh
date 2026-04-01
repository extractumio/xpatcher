#!/usr/bin/env bash
# install.sh -- Install xpatcher to ~/xpatcher/ (or custom XPATCHER_HOME path)
#
# This installer sets up xpatcher as a per-user installation. It is run
# once and serves all projects.
#
# Steps:
#   1. Check Python 3.10+
#   2. Check Claude Code CLI
#   3. Create installation directory
#   4. Copy core files (plugin, src, config)
#   5. Create venv, install deps (pydantic, pyyaml, rich)
#   6. Create CLI entry point
#   7. Create hook wrapper
#   8. Smoke test: verify Claude Code CLI + plugin loading
#   9. Print PATH setup instructions

set -euo pipefail

INSTALL_DIR="${XPATCHER_HOME:-$HOME/xpatcher}"
SOURCE_DIR="$(cd "$(dirname "$0")" && pwd -P)"

echo "xpatcher installer"
echo "====================="
echo "Installing to: $INSTALL_DIR"
echo ""

# ---------------------------------------------------------------------------
# 1. Check Python 3.10+
# ---------------------------------------------------------------------------
if ! command -v python3 &>/dev/null; then
    echo "ERROR: Python 3 not found. Please install Python 3.10+"
    exit 1
fi
PY_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PY_OK=$(python3 -c "import sys; print(int(sys.version_info >= (3, 10)))")
if [ "$PY_OK" != "1" ]; then
    echo "ERROR: Python 3.10+ required (found $PY_VERSION)"
    exit 1
fi
echo "[ok] Python $PY_VERSION"

# ---------------------------------------------------------------------------
# 2. Check Claude Code CLI
# ---------------------------------------------------------------------------
if ! command -v claude &>/dev/null; then
    echo "ERROR: Claude Code CLI not found."
    echo "  Install from: https://claude.ai/code"
    exit 1
fi
CLAUDE_VERSION=$(claude --version 2>/dev/null || echo "unknown")
echo "[ok] Claude Code CLI found ($CLAUDE_VERSION)"

# ---------------------------------------------------------------------------
# 3. Create installation directory
# ---------------------------------------------------------------------------
mkdir -p "$INSTALL_DIR"
echo "[ok] Installation directory: $INSTALL_DIR"

# ---------------------------------------------------------------------------
# 4. Copy core files
# ---------------------------------------------------------------------------
# Copy plugin directory (agents, hooks, skills, plugin.json, settings.json)
cp -r "$SOURCE_DIR/.claude-plugin/" "$INSTALL_DIR/.claude-plugin/"

# Copy Python source
cp -r "$SOURCE_DIR/src/" "$INSTALL_DIR/src/"

# Copy project metadata
cp "$SOURCE_DIR/pyproject.toml" "$INSTALL_DIR/"

# Copy config (prefer config.yaml, fall back to config.yaml.example)
if [ -f "$SOURCE_DIR/config.yaml" ]; then
    cp "$SOURCE_DIR/config.yaml" "$INSTALL_DIR/"
elif [ -f "$SOURCE_DIR/config.yaml.example" ]; then
    cp "$SOURCE_DIR/config.yaml.example" "$INSTALL_DIR/config.yaml"
fi

# Copy VERSION file
if [ -f "$SOURCE_DIR/VERSION" ]; then
    cp "$SOURCE_DIR/VERSION" "$INSTALL_DIR/"
fi

echo "[ok] Core files installed"

# ---------------------------------------------------------------------------
# 5. Create venv and install dependencies
# ---------------------------------------------------------------------------
if ! python3 -c "import venv" 2>/dev/null; then
    echo "ERROR: Python venv module not found."
    echo "  On Ubuntu/Debian: sudo apt install python3-venv"
    echo "  On Fedora/RHEL:   sudo dnf install python3-venv"
    echo "  On macOS:         venv is included with Python from python.org or brew"
    exit 1
fi

if [ ! -d "$INSTALL_DIR/.venv" ]; then
    echo "  Creating virtual environment..."
    python3 -m venv "$INSTALL_DIR/.venv"
fi

echo "  Installing dependencies..."
"$INSTALL_DIR/.venv/bin/pip" install -q --upgrade pip 2>/dev/null
"$INSTALL_DIR/.venv/bin/pip" install -q pydantic pyyaml rich
echo "[ok] Dependencies installed"

# ---------------------------------------------------------------------------
# 6. Create CLI entry point
# ---------------------------------------------------------------------------
mkdir -p "$INSTALL_DIR/bin"
cat > "$INSTALL_DIR/bin/xpatcher" << 'ENTRY'
#!/usr/bin/env bash
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd -P)"
export XPATCHER_HOME="$SCRIPT_DIR"
exec "$SCRIPT_DIR/.venv/bin/python" -m src.dispatcher.core "$@"
ENTRY
chmod +x "$INSTALL_DIR/bin/xpatcher"
echo "[ok] CLI entry point: $INSTALL_DIR/bin/xpatcher"

# ---------------------------------------------------------------------------
# 7. Create hook wrapper
# ---------------------------------------------------------------------------
cat > "$INSTALL_DIR/.claude-plugin/hooks/run_hook.sh" << 'HOOKWRAP'
#!/usr/bin/env bash
HOOK_DIR="$(cd "$(dirname "$0")" && pwd -P)"
XPATCHER_HOME="$(cd "$HOOK_DIR/../.." && pwd -P)"
exec "$XPATCHER_HOME/.venv/bin/python" "$HOOK_DIR/$1"
HOOKWRAP
chmod +x "$INSTALL_DIR/.claude-plugin/hooks/run_hook.sh"
echo "[ok] Hook wrapper: $INSTALL_DIR/.claude-plugin/hooks/run_hook.sh"

# ---------------------------------------------------------------------------
# 8. Smoke test: verify Claude Code CLI + plugin loading
# ---------------------------------------------------------------------------
echo ""
echo "Running smoke test..."
SMOKE_OUTPUT=""
SMOKE_EXIT=0
SMOKE_OUTPUT=$(claude -p "respond with ok" --output-format json \
    --plugin-dir "$INSTALL_DIR/.claude-plugin/" \
    --max-turns 1 --permission-mode bypassPermissions 2>&1) || SMOKE_EXIT=$?

if [ "$SMOKE_EXIT" != "0" ]; then
    echo "WARNING: Claude Code CLI smoke test failed (exit code $SMOKE_EXIT)"
    echo "  Check that you are authenticated: run 'claude' interactively"
    echo "  Installation succeeded but xpatcher may not work until this is resolved."
else
    # Verify plugin loaded and agents registered by parsing the init event
if echo "$SMOKE_OUTPUT" | "$INSTALL_DIR/.venv/bin/python" -c "
import sys, json

try:
    events = json.load(sys.stdin)
except (json.JSONDecodeError, ValueError):
    print('WARNING: Could not parse smoke test output as JSON')
    sys.exit(1)

# Handle both array-of-events and single-event formats
if isinstance(events, dict):
    events = [events]

init = next(
    (e for e in events
     if e.get('type') == 'system' and e.get('subtype') == 'init'),
    {}
)

if not init:
    print('WARNING: No init event found in smoke test output')
    sys.exit(1)

plugins = [p.get('name', '') for p in init.get('plugins', [])]
plugin = next((p for p in init.get('plugins', []) if p.get('path') == '$INSTALL_DIR/.claude-plugin'), None)
agents = init.get('agents', [])
version = init.get('claude_code_version', 'unknown')

if not plugin:
    print(f'WARNING: Plugin for path $INSTALL_DIR/.claude-plugin not found in loaded plugins: {plugins}')
    sys.exit(1)

plugin_name = plugin.get('name', 'xpatcher')
xp_agents = [a for a in agents if a.startswith(f'{plugin_name}:')]
if len(xp_agents) < 9:
    print(f'WARNING: Expected 9 xpatcher agents, found {len(xp_agents)}: {xp_agents}')
    sys.exit(1)

print(f'[ok] Claude Code CLI v{version} -- plugin loaded as {plugin_name}, {len(xp_agents)} agents registered')
" 2>/dev/null; then
        :  # Success message already printed
    else
        echo "WARNING: Plugin verification could not be completed."
        echo "  Installation succeeded but plugin loading could not be confirmed."
    fi
fi

# ---------------------------------------------------------------------------
# 9. PATH setup instructions
# ---------------------------------------------------------------------------
if [[ ":$PATH:" != *":$INSTALL_DIR/bin:"* ]]; then
    echo ""
    echo "------------------------------------------------------------"
    echo "Add xpatcher to your PATH:"
    echo ""
    echo "  # For bash:"
    echo "  echo 'export PATH=\"$INSTALL_DIR/bin:\$PATH\"' >> ~/.bashrc"
    echo ""
    echo "  # For zsh:"
    echo "  echo 'export PATH=\"$INSTALL_DIR/bin:\$PATH\"' >> ~/.zshrc"
    echo ""
    echo "Then restart your shell or run:"
    echo "  export PATH=\"$INSTALL_DIR/bin:\$PATH\""
    echo "------------------------------------------------------------"
fi

echo ""
echo "Installation complete."
echo ""
echo "Run from any project directory:"
echo "  cd /path/to/your/project"
echo "  xpatcher start \"your task description\""

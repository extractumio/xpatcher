#!/usr/bin/env bash
# install.sh -- Install xpatcher to ~/xpatcher/ (or custom XPATCHER_HOME path)
#
# This installer sets up xpatcher as a per-user installation. It is run
# once and serves all projects.

set -euo pipefail

INSTALL_DIR="${XPATCHER_HOME:-$HOME/xpatcher}"
SOURCE_DIR="$(cd "$(dirname "$0")" && pwd -P)"

# ---------------------------------------------------------------------------
# Colors
# ---------------------------------------------------------------------------
RESET="\033[0m"
BOLD="\033[1m"
DIM="\033[2m"
RED="\033[31m"
GREEN="\033[32m"
YELLOW="\033[33m"
BLUE="\033[34m"
MAGENTA="\033[35m"
CYAN="\033[36m"

ok()   { printf "  ${GREEN}✓${RESET} %b\n" "$*"; }
err()  { printf "  ${RED}✗${RESET} %b\n" "$*" >&2; }
warn() { printf "  ${YELLOW}⚠${RESET} %b\n" "$*"; }
info() { printf "  ${DIM}%b${RESET}\n" "$*"; }

printf "\n${BOLD}${CYAN}  ╔══════════════════════════════════════╗${RESET}\n"
printf "${BOLD}${CYAN}  ║         xpatcher installer            ║${RESET}\n"
printf "${BOLD}${CYAN}  ╚══════════════════════════════════════╝${RESET}\n\n"
info "Target: $INSTALL_DIR"
echo ""

# ---------------------------------------------------------------------------
# 1. Check Python 3.10+
# ---------------------------------------------------------------------------
if ! command -v python3 &>/dev/null; then
    err "Python 3 not found. Please install Python 3.10+"
    exit 1
fi
PY_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PY_OK=$(python3 -c "import sys; print(int(sys.version_info >= (3, 10)))")
if [ "$PY_OK" != "1" ]; then
    err "Python 3.10+ required (found $PY_VERSION)"
    exit 1
fi
ok "Python $PY_VERSION"

# ---------------------------------------------------------------------------
# 2. Check Claude Code CLI
# ---------------------------------------------------------------------------
if ! command -v claude &>/dev/null; then
    err "Claude Code CLI not found."
    info "Install from: https://claude.ai/code"
    exit 1
fi
CLAUDE_VERSION=$(claude --version 2>/dev/null || echo "unknown")
ok "Claude Code CLI ($CLAUDE_VERSION)"

# ---------------------------------------------------------------------------
# 3. Create installation directory
# ---------------------------------------------------------------------------
mkdir -p "$INSTALL_DIR"
ok "Installation directory ready"

# ---------------------------------------------------------------------------
# 4. Copy core files
# ---------------------------------------------------------------------------
cp -r "$SOURCE_DIR/.claude-plugin/" "$INSTALL_DIR/.claude-plugin/"
cp -r "$SOURCE_DIR/src/" "$INSTALL_DIR/src/"
cp "$SOURCE_DIR/pyproject.toml" "$INSTALL_DIR/"

if [ -f "$SOURCE_DIR/config.yaml" ]; then
    cp "$SOURCE_DIR/config.yaml" "$INSTALL_DIR/"
elif [ -f "$SOURCE_DIR/config.yaml.example" ]; then
    cp "$SOURCE_DIR/config.yaml.example" "$INSTALL_DIR/config.yaml"
fi

if [ -f "$SOURCE_DIR/VERSION" ]; then
    cp "$SOURCE_DIR/VERSION" "$INSTALL_DIR/"
fi

ok "Core files installed"

# ---------------------------------------------------------------------------
# 5. Create venv and install dependencies
# ---------------------------------------------------------------------------
if ! python3 -c "import venv" 2>/dev/null; then
    err "Python venv module not found."
    info "On Ubuntu/Debian: sudo apt install python3-venv"
    info "On Fedora/RHEL:   sudo dnf install python3-venv"
    info "On macOS:         venv is included with Python from python.org or brew"
    exit 1
fi

if [ ! -d "$INSTALL_DIR/.venv" ]; then
    info "Creating virtual environment..."
    python3 -m venv "$INSTALL_DIR/.venv"
fi

info "Installing dependencies..."
"$INSTALL_DIR/.venv/bin/pip" install -q --upgrade pip 2>/dev/null
"$INSTALL_DIR/.venv/bin/pip" install -q pydantic pyyaml rich
ok "Dependencies installed"

# ---------------------------------------------------------------------------
# 6. Create CLI entry point
# ---------------------------------------------------------------------------
mkdir -p "$INSTALL_DIR/bin"
cat > "$INSTALL_DIR/bin/xpatcher" << 'ENTRY'
#!/usr/bin/env bash
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd -P)"
export XPATCHER_HOME="$SCRIPT_DIR"
export PYTHONPATH="$SCRIPT_DIR${PYTHONPATH:+:$PYTHONPATH}"
exec "$SCRIPT_DIR/.venv/bin/python" -m src.dispatcher.core "$@"
ENTRY
chmod +x "$INSTALL_DIR/bin/xpatcher"
ok "CLI entry point: ${DIM}$INSTALL_DIR/bin/xpatcher${RESET}"

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
ok "Hook wrapper installed"

# ---------------------------------------------------------------------------
# 8. Verify plugin agents exist
# ---------------------------------------------------------------------------
AGENT_COUNT=$(ls -1 "$INSTALL_DIR/.claude-plugin/agents/"*.md 2>/dev/null | wc -l | tr -d ' ')
if [ "$AGENT_COUNT" -eq 0 ]; then
    err "No agent definitions found in .claude-plugin/agents/"
    exit 1
fi
ok "$AGENT_COUNT agent definitions found"

# ---------------------------------------------------------------------------
# 9. Resolve authentication
# ---------------------------------------------------------------------------
echo ""
printf "${BOLD}${BLUE}  Authentication${RESET}\n"

# Delegate to the Python auth module (single source of truth)
AUTH_RESULT=$("$INSTALL_DIR/.venv/bin/python" -c "
import os, sys
sys.path.insert(0, '$INSTALL_DIR')
from src.dispatcher.auth import resolve_auth_env, describe_auth_source
auth_env = resolve_auth_env(sys.path[0] and __import__('pathlib').Path('$INSTALL_DIR'))
env_has_key = bool(os.environ.get('ANTHROPIC_API_KEY'))
source = describe_auth_source(auth_env, env_has_key=env_has_key)
key = auth_env.get('ANTHROPIC_API_KEY', '')
print(f'{source}\n{key}')
" 2>/dev/null || echo "none")

AUTH_SOURCE=$(echo "$AUTH_RESULT" | head -1)
_auth_key=$(echo "$AUTH_RESULT" | tail -1)

AUTH_ENV=()
if [ -n "$_auth_key" ] && [ "$_auth_key" != "$AUTH_SOURCE" ]; then
    AUTH_ENV=(env ANTHROPIC_API_KEY="$_auth_key")
fi

if [ "$AUTH_SOURCE" = "none" ]; then
    echo ""
    err "${BOLD}No authentication found. Cannot proceed.${RESET}"
    info "Either:"
    info "  1. Add ${BOLD}ANTHROPIC_API_KEY=sk-ant-...${RESET} to ${BOLD}$INSTALL_DIR/.env${RESET}"
    info "  2. Log in interactively: run ${BOLD}claude${RESET} and complete login"
    echo ""
    exit 1
else
    ok "Auth: ${BOLD}${MAGENTA}$AUTH_SOURCE${RESET}"
fi

# ---------------------------------------------------------------------------
# 10. Smoke test: verify Claude Code CLI + plugin loading
# ---------------------------------------------------------------------------
echo ""
printf "${BOLD}${BLUE}  Smoke Test${RESET}\n"

SMOKE_OUTPUT=""
SMOKE_EXIT=0
SMOKE_CMD=(claude -p "respond with ok" --output-format json
    --plugin-dir "$INSTALL_DIR/.claude-plugin/"
    --max-turns 1 --permission-mode bypassPermissions)
SMOKE_OUTPUT=$(${AUTH_ENV[@]+"${AUTH_ENV[@]}"} "${SMOKE_CMD[@]}" 2>&1) || SMOKE_EXIT=$?

if [ "$SMOKE_EXIT" != "0" ]; then
    warn "Claude Code CLI smoke test failed (exit code $SMOKE_EXIT)"
    if [ "$AUTH_SOURCE" = "none" ]; then
        info "No credentials found. Either:"
        info "  1. Add ANTHROPIC_API_KEY=sk-ant-... to $INSTALL_DIR/.env"
        info "  2. Log in interactively: run 'claude' and complete login"
    else
        info "Auth ($AUTH_SOURCE) was detected but CLI still failed."
        info "Check that your credentials are valid."
    fi
    warn "Installation succeeded but xpatcher may not work until this is resolved."
else
    # Verify plugin loaded and agents registered by parsing the init event
if echo "$SMOKE_OUTPUT" | "$INSTALL_DIR/.venv/bin/python" -c "
import sys, json

try:
    events = json.load(sys.stdin)
except (json.JSONDecodeError, ValueError):
    print('WARNING: Could not parse smoke test output as JSON')
    sys.exit(1)

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

# Agents are prefixed with the plugin directory name (e.g. '.claude-plugin:planner').
# Match by suffix to be prefix-agnostic.
required = {'planner', 'plan-reviewer', 'executor', 'reviewer', 'gap-detector', 'tech-writer', 'explorer'}
found_bare = {a.split(':')[-1] for a in agents}
missing = required - found_bare
if missing:
    print(f'WARNING: Missing agents: {missing}')
    sys.exit(1)

xp_agents = [a for a in agents if a.split(':')[-1] in required]
print(f'[ok] Claude Code CLI v{version} -- plugin loaded, {len(xp_agents)} agents registered')
" 2>/dev/null; then
        :  # Success message already printed
    else
        ok "Claude Code CLI responded successfully"
        warn "Plugin agent verification could not be completed."
        info "Installation succeeded but plugin loading could not be confirmed."
    fi
fi

# ---------------------------------------------------------------------------
# 11. Add to PATH in shell rc files (idempotent, supports bash + zsh)
# ---------------------------------------------------------------------------
echo ""
printf "${BOLD}${BLUE}  PATH Setup${RESET}\n"
PATH_LINE="export PATH=\"$INSTALL_DIR/bin:\$PATH\""

# On macOS, terminal shells are login shells that source profile files
# (bash_profile/zprofile), not rc files (bashrc/zshrc). We add to both
# to cover interactive login shells and non-login interactive shells.
for rcfile in "$HOME/.bashrc" "$HOME/.bash_profile" "$HOME/.zshrc" "$HOME/.zprofile"; do
    [ -f "$rcfile" ] || continue
    if ! grep -qF "$INSTALL_DIR/bin" "$rcfile"; then
        printf '\n# xpatcher\n%s\n' "$PATH_LINE" >> "$rcfile"
        ok "Added to PATH in $(basename "$rcfile")"
    else
        ok "PATH already configured in $(basename "$rcfile")"
    fi
done

# Make it available in the current session too
export PATH="$INSTALL_DIR/bin:$PATH"

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
echo ""
printf "${BOLD}${GREEN}  ╔══════════════════════════════════════╗${RESET}\n"
printf "${BOLD}${GREEN}  ║       Installation complete!         ║${RESET}\n"
printf "${BOLD}${GREEN}  ╚══════════════════════════════════════╝${RESET}\n\n"
info "Run from any project directory:"
printf "  ${BOLD}cd /path/to/your/project${RESET}\n"
printf "  ${BOLD}xpatcher start \"your task description\"${RESET}\n\n"

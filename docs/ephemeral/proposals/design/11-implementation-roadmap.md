# Implementation Roadmap and Open Questions

> Part of the [xpatcher Design Proposal](../xpatcher-design-proposal.md)

---

# 9. Implementation Roadmap

## Phase 1: Foundation (Weeks 1-2)

**Goal:** Prove the plugin loading mechanism and basic agent invocation.

| Step | Deliverable | Test |
|------|------------|------|
| 1 | Create `.claude-plugin/` directory with `plugin.json` | Plugin recognized by Claude Code |
| 2 | Write `explorer.md` agent | Interactive Q&A works with Haiku |
| 3 | Write `/xpatcher:status` skill | Slash command loads and responds |
| 4 | Write `src/dispatcher/session.py` (ClaudeSession) | Can invoke `claude -p` and parse output |
| 5 | Write `src/dispatcher/state.py` (PipelineState) | State persists across restarts |
| 6 | Write `src/dispatcher/schemas.py` (Pydantic models) | Schema validation works |

## Phase 2: Core Pipeline (Weeks 3-4)

**Goal:** Planning through execution with review loop.

| Step | Deliverable | Test |
|------|------------|------|
| 7 | Write `planner.md` agent and `/xpatcher:plan` skill | Produces structured YAML plan |
| 8 | Write `executor.md` agent and `/xpatcher:execute` skill | Implements a single task from plan |
| 9 | Write `reviewer.md` agent and `/xpatcher:review` skill | Produces structured review |
| 10 | Write `src/context/builder.py` (prompt assembly) | Correct context for each stage |
| 11 | Implement plan-execute-review pipeline in `core.py` | Three-stage pipeline works end-to-end |
| 12 | Implement review-fix loop with iteration cap | Loop terminates and escalates correctly |

## Phase 3: Quality Gates (Weeks 5-6)

**Goal:** Testing, simplification, gap detection, and documentation.

| Step | Deliverable | Test |
|------|------------|------|
| 13 | Write `tester.md` agent and `/xpatcher:test` skill | Generates and runs tests |
| 14 | Write `simplifier.md` agent and `/xpatcher:simplify` skill | Identifies/applies simplifications |
| 15 | Write `gap-detector.md` agent and `/xpatcher:detect-gaps` skill | Finds integration gaps |
| 16 | Write `tech-writer.md` agent and `/xpatcher:update-docs` skill | Updates documentation for changes |
| 17 | Write PreToolUse hooks (read-only enforcement, scope, tech-writer scope) | Read-only agents blocked from writing; tech-writer limited to doc files |
| 18 | Implement acceptance criteria verification in dispatcher | Harness runs test commands, not agent |
| 19 | Implement per-task quality loop (Stages 12-13) | Simplify-test-review cycle works |
| 20 | Implement documentation stage (Stage 15) in pipeline | Tech-writer runs after gap detection passes |

## Phase 4: TUI, Logging, and Polish (Weeks 7-8)

**Goal:** Transparent output, agent log streaming, parallel execution, and resilience.

| Step | Deliverable | Test |
|------|------------|------|
| 21 | Write `src/dispatcher/tui.py` (rich-based TUI renderer) | Live progress panel renders correctly |
| 22 | Implement per-stage elapsed time tracking | Timers display in progress panel |
| 23 | Implement agent log capture to JSONL files | All agent invocations produce structured logs |
| 24 | Implement agent log streaming in TUI (`--verbose`, `--stream-logs`) | Live agent output visible during execution |
| 25 | Write `src/dispatcher/parallel.py` (thread pool) | Multiple agents run concurrently |
| 26 | Implement git worktree management for parallel tasks | File isolation between agents |
| 27 | Write `src/dispatcher/retry.py` (backoff logic) | Transient failures retry correctly |
| 28 | Implement state persistence and `resume_pipeline()` | Pipeline survives dispatcher crash |
| 29 | Write PostToolUse hooks (audit logging) | Full tool call audit trail |
| 30 | Write lifecycle hooks (agent tracking) | Active agent monitoring works |
| 31 | Implement cost tracking and budget enforcement | Budget breaches pause pipeline |
| 32 | Add DAG-based task scheduling with critical path priority | Critical path tasks run first |

## Phase 5: Packaging and Distribution (Week 9+)

**Goal:** Per-user installable CLI, project auto-init, documentation.

| Step | Deliverable | Test |
|------|------------|------|
| 33 | Write `install.sh` (per-user/server installation script) | `~/xpatcher/bin/xpatcher` works |
| 34 | Implement project auto-detection and `.xpatcher/` initialization | First run on any git project creates artifacts dir |
| 35 | Implement `.xpatcher.yaml` project-level config overrides | Per-project settings override global defaults |
| 36 | Write `/xpatcher:pipeline` skill (full pipeline) | Single command runs everything |
| 37 | Implement `xpatcher logs` command for post-hoc log inspection | Logs queryable by agent, task, time range |
| 38 | Write integration tests on sample projects | Full pipeline passes on known repos |
| 39 | Package as installable CLI with `pip install` support | `pip install xpatcher` works |
| 40 | Write user-facing documentation | New user can install and run pipeline from docs |

## Installation Script

The installer sets up xpatcher as a per-user or server-wide installation. It is run **once** and serves all projects.

```bash
#!/usr/bin/env bash
# install.sh -- Install xpatcher to ~/xpatcher/ (or custom path)

set -euo pipefail

INSTALL_DIR="${XPATCHER_HOME:-$HOME/xpatcher}"

echo "xpatcher installer"
echo "====================="
echo "Installing to: $INSTALL_DIR"

# 1. Check Python 3.10+
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
echo "[✓] Python $PY_VERSION"

# 2. Check Claude Code CLI
if ! command -v claude &>/dev/null; then
    echo "ERROR: Claude Code CLI not found. Install from: https://claude.ai/code"
    exit 1
fi
echo "[✓] Claude Code CLI found"

# 3. Create installation directory
mkdir -p "$INSTALL_DIR"
echo "[✓] Installation directory created"

# 4. Copy core files (plugin, src, config)
cp -r .claude-plugin/ "$INSTALL_DIR/.claude-plugin/"
cp -r src/ "$INSTALL_DIR/src/"
cp pyproject.toml "$INSTALL_DIR/"
cp config.yaml "$INSTALL_DIR/" 2>/dev/null || cp config.yaml.example "$INSTALL_DIR/config.yaml"
echo "[✓] Core files installed"

# 5. Create venv and install dependencies
if ! python3 -c "import venv" 2>/dev/null; then
    echo "ERROR: Python venv module not found."
    echo "  On Ubuntu/Debian: sudo apt install python3-venv"
    echo "  On Fedora/RHEL:   sudo dnf install python3-venv"
    exit 1
fi
if [ ! -d "$INSTALL_DIR/.venv" ]; then
    python3 -m venv "$INSTALL_DIR/.venv"
fi
"$INSTALL_DIR/.venv/bin/pip" install -q pydantic pyyaml rich
echo "[✓] Dependencies installed"

# 6. Create CLI entry point
mkdir -p "$INSTALL_DIR/bin"
cat > "$INSTALL_DIR/bin/xpatcher" << 'ENTRY'
#!/usr/bin/env bash
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd -P)"
export XPATCHER_HOME="$SCRIPT_DIR"
exec "$SCRIPT_DIR/.venv/bin/python" -m src.dispatcher.core "$@"
ENTRY
chmod +x "$INSTALL_DIR/bin/xpatcher"
echo "[✓] CLI entry point created"

# 7. Create hook wrapper script (avoids bare `python` invocation)
cat > "$INSTALL_DIR/.claude-plugin/hooks/run_hook.sh" << HOOKWRAP
#!/usr/bin/env bash
HOOK_DIR="\$(cd "\$(dirname "\$0")" && pwd -P)"
XPATCHER_HOME="\$(cd "\$HOOK_DIR/../.." && pwd -P)"
exec "\$XPATCHER_HOME/.venv/bin/python" "\$HOOK_DIR/\$1"
HOOKWRAP
chmod +x "$INSTALL_DIR/.claude-plugin/hooks/run_hook.sh"
echo "[✓] Hook wrapper created"

# 8. Smoke test: verify Claude Code CLI + plugin loading
echo ""
echo "Running smoke test..."
SMOKE_OUTPUT=$(claude -p "respond with ok" --output-format json \
    --plugin-dir "$INSTALL_DIR/.claude-plugin/" \
    --max-turns 1 --permission-mode bypassPermissions 2>&1)
SMOKE_EXIT=$?

if [ "$SMOKE_EXIT" != "0" ]; then
    echo "⚠ Claude Code CLI smoke test failed (exit code $SMOKE_EXIT)"
    echo "  Check that you are authenticated: run 'claude' interactively"
    echo "  Installation succeeded but xpatcher may not work until this is resolved."
else
    # Check plugin loaded by looking for "xpatcher" in the init event's plugins
    if echo "$SMOKE_OUTPUT" | python3 -c "
import sys, json
events = json.load(sys.stdin)
init = next((e for e in events if e.get('type') == 'system' and e.get('subtype') == 'init'), {})
plugins = [p.get('name') for p in init.get('plugins', [])]
agents = init.get('agents', [])
version = init.get('claude_code_version', 'unknown')
if 'xpatcher' not in plugins:
    print(f'⚠ Plugin \"xpatcher\" not found in loaded plugins: {plugins}')
    sys.exit(1)
xp_agents = [a for a in agents if 'xpatcher:' in a]
if len(xp_agents) < 9:
    print(f'⚠ Expected 9 xpatcher agents, found {len(xp_agents)}: {xp_agents}')
    sys.exit(1)
print(f'[✓] Claude Code CLI v{version} — plugin loaded, {len(xp_agents)} agents registered')
" 2>/dev/null; then
        :  # Success message already printed
    else
        echo "⚠ Plugin verification failed. Check .claude-plugin/plugin.json"
        echo "  Installation succeeded but plugin may not load correctly."
    fi
fi

# 9. Add to PATH guidance
if [[ ":$PATH:" != *":$INSTALL_DIR/bin:"* ]]; then
    echo ""
    echo "Add xpatcher to your PATH:"
    echo "  echo 'export PATH=\"$INSTALL_DIR/bin:\$PATH\"' >> ~/.bashrc"
    echo "  echo 'export PATH=\"$INSTALL_DIR/bin:\$PATH\"' >> ~/.zshrc"
fi

echo ""
echo "Installation complete. Run from any project directory:"
echo "  cd /path/to/your/project"
echo "  xpatcher start \"your task description\""
```

### Preflight Check

Every `xpatcher start` invocation begins with a preflight check. This runs **before** any pipeline work and fails fast with actionable errors.

**Phase 1 — Claude Code CLI validation** (runs once per dispatcher session, cached):

1. Runs `claude -p "respond with ok" --output-format json --plugin-dir <path> --max-turns 1`
2. Parses the `init` event from the JSON array output
3. Verifies CLI responded without error (`result.is_error` is false)
4. Verifies plugin `"xpatcher"` appears in `init.plugins[]` (confirms `plugin.json` loaded)
5. Verifies all 9 required agents appear in `init.agents[]` (e.g., `xpatcher:planner`, `xpatcher:executor`, etc.)
6. Records `init.claude_code_version` for compatibility tracking

If any check fails, the dispatcher prints a specific error and exits — no pipeline state is created. See `ClaudeSession.preflight()` in Section 7.7 for the full implementation.

**Phase 2 — Project validation** (runs per project):

1. Verifies the project is a git repository
2. Detects the project stack (language, test framework, linter)
3. Creates `.xpatcher/` directory in the project
4. Optionally creates `.xpatcher.yaml` with detected defaults
5. Adds `.xpatcher/` to `.gitignore` if not already present

No manual setup is needed per-project.

---

# 10. Open Questions and Decisions Needed

## Architecture Decisions

1. ~~**Agent selection in headless mode**~~ ✅ **RESOLVED (2026-03-29):** The `--agent` CLI flag exists and works with plugin agents. Plugin agents use qualified names: `<plugin-name>:<agent-name>` (e.g., `xpatcher:planner`). The `--plugin-dir` flag loads the plugin directory and registers all agent `.md` files. Validated against Claude Code CLI v2.1.87. See Section 7.7.1 for the full validation matrix.

2. **Hook input protocol**: the exact JSON schema for hook stdin/stdout varies across Claude Code versions. The hook scripts assume `tool_name`, `tool_input`, and `decision`/`block` format. Needs validation.

3. **Plugin settings.json scope**: whether `settings.json` in a plugin can register hooks that apply session-wide is unclear. If not, hooks must be installed at the project level via setup script.

4. **Structured output failure handling**: ~~resolved~~ Agents output YAML which is validated by the `ArtifactValidator` (Section 7.7). Malformed output triggers same-session retry via `MalformedOutputRecovery` with up to 2 fix attempts before escalation.

5. **Concurrent session isolation**: when multiple agents run in parallel as separate `claude -p` subprocesses, can they conflict on file writes? Git worktree isolation helps but adds merge complexity. *(Note: `--resume` with the same `session_id` has been validated to correctly continue sessions. Different agent invocations receive independent `session_id` values — no conflict.)*

## Process Decisions

6. **Default human gate configuration**: should plan approval (Stage 5) be blocking by default, or should there be a "trust mode" for experienced users? Recommendation: blocking by default, with opt-out.

7. **State persistence format**: YAML is used for all artifacts and state files (human-readable, git-friendly). `pipeline-state.yaml` is the mutable singleton for pipeline state.

8. **Agent Teams migration**: Claude Code's Agent Teams feature could replace parts of the dispatcher for parallel execution. Should we plan a migration path now, or treat Agent Teams as fully out of scope until it stabilizes?

9. **MCP server for pipeline state**: instead of file-based state, an MCP server could expose state to agents via tool calls for richer querying. Worth building in Phase 4 or deferring?

10. **Cost estimation accuracy**: the pre-pipeline cost estimate depends on predicting tokens per task. How should we calibrate this? Recommendation: run 10 sample pipelines, measure actual costs, compute multipliers per task complexity level.

## Technical Unknowns

11. **Context window management**: at what point should we implement context checkpointing? If tasks stay under 50 turns, it may not be needed for v1. But if review-fix loops push past that, it becomes critical.

12. **Git worktree limits**: is there a practical limit to the number of worktrees git can manage? For typical features (5-10 tasks), this should be fine, but edge cases with 50+ tasks could hit filesystem or git limitations.

13. **Rate limiting**: Claude API rate limits may constrain parallelism more than the 3-agent default assumes. Need empirical data on sustainable concurrent sessions for the target API tier.

14. **Learning from outcomes**: the memory system could store patterns from successful and failed pipelines so the planner improves over time. Deferred to post-v1 but should be designed for.

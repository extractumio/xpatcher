# Plan: Refactor Pipeline to Native Subagent Delegation

**Date:** 2026-04-02
**Status:** Draft

## Summary

Replace the current architecture (dispatcher selects agent via `--agent` per stage, all work in one session context) with native Claude Code subagent delegation (main agent orchestrates, delegates to subagents via Agent tool, each subagent gets its own context).

## Motivation

Current design runs all stages in a single session JSONL with `--agent` switches. This:
- Accumulates full working context (tool calls, reads, writes) across all stages into one session
- Doesn't leverage Claude Code's native Agent tool for context isolation
- Required `--bare` mode which disables the Agent tool entirely

New design: the dispatcher resumes a single main agent session per stage. The main agent receives a stage prompt that instructs it to `@agent-<name>` delegate to the appropriate subagent. The subagent does the heavy lifting in its own context, returns results. The main session stays lean — only orchestration and summaries.

## Evidence (from tests in this session)

| Finding | Test |
|---------|------|
| `--bare` disables Agent tool — delegation impossible | `test_bare_delegation.sh` tests A+B vs C |
| `--resume` with `--agent` switch: JSONL logs stale agent name but behavior does change | `test_agent_resume_switch.sh` |
| `--plugin-dir` without `--bare` loads agents natively; Agent tool delegates to them | `test_plugin_dir_no_bare.sh` |
| `@agent-<name>` (bare name, no prefix) works regardless of plugin dir name | `test_agent_prefix.sh` tests A-D |
| Subagent transcripts go to `{sessionId}/subagents/agent-*.jsonl` + `.meta.json` | Verified on existing sessions |
| Auth via `ANTHROPIC_API_KEY` env var works without `--bare` | All non-bare tests |

## What changes

### 1. Drop `--bare` from all CLI invocations

**Files:** `session.py`, `config.yaml`

- `preflight()`: remove `--bare` from preflight command
- `_build_cmd_legacy()`: remove `--bare` from base command
- `config.yaml`: remove `--bare` from all agent command templates

Without `--bare`, Claude Code auto-discovers:
- CLAUDE.md from project dir
- Plugins from `--plugin-dir`
- Agents from plugin `agents/` dir
- Skills from plugin `skills/` dir
- Hooks from plugin `settings.json`

### 2. Drop `--append-system-prompt-file` CLAUDE.md injection

**Files:** `session.py:388-391`

Delete the block:
```python
# Inject project CLAUDE.md as system prompt context (--bare skips auto-discovery)
claude_md = self.project_dir / "CLAUDE.md"
if claude_md.is_file() and "--append-system-prompt-file" not in cmd:
    cmd.extend(["--append-system-prompt-file", str(claude_md)])
```

Claude Code loads it automatically from the project dir.

### 3. Drop `--agents` JSON and `bake_agents_json()`

**Files:** `session.py:144-175` (delete function), `session.py:247-251` (delete agents.json loading), `session.py:266-267,423-424` (delete `--agents` flag injection), `core.py` (delete any call to `bake_agents_json`)

Agents are loaded natively from `--plugin-dir`. The `agents.json` file and all JSON baking machinery is dead code.

### 4. Drop `--agent` flag — no more direct agent selection

**Files:** `session.py:425-429`, `AgentInvocation.agent` field

The dispatcher no longer tells Claude Code which agent to run. Instead, the prompt tells the *main agent* to delegate via `@agent-<name>`.

Remove from `AgentInvocation`:
- `agent: Optional[str]`
- `model: Optional[str]` (subagent models live in their `.md` frontmatter)
- `allowed_tools` / `disallowed_tools` (in frontmatter)

### 5. Drop per-agent command templates from `config.yaml`

**Current:** 9 near-identical command templates differing only in `--agent` and `--model`.

**New:** single main-agent config:

```yaml
main_agent:
  timeout: 900          # subprocess timeout per invocation

iterations:
  plan_review_max: 3
  task_review_max: 3
  quality_loop_max: 3
  gap_reentry_max: 2

human_gates:
  spec_confirmation: false
  completion_confirmation: false
```

The command is built in code, not config. Config only holds timeout and iteration/gate settings.

### 6. Simplify `_build_cmd_legacy()` → `_build_cmd()`

New command construction:

```python
def _build_cmd(self, invocation: AgentInvocation) -> list[str]:
    cmd = [
        "claude", "-p", invocation.prompt,
        "--output-format", "json",
        "--plugin-dir", str(self.plugin_dir),
        "--permission-mode", invocation.permission_mode,
    ]
    if invocation.max_turns:
        cmd.extend(["--max-turns", str(invocation.max_turns)])
    if invocation.resume and invocation.session_id:
        cmd.extend(["--resume", invocation.session_id])
    return cmd
```

No `--bare`, no `--agents`, no `--agent`, no `--model`, no `--append-system-prompt-file`.

### 7. Simplify `_invoke_agent()` → `_invoke_stage()`

Current signature: `_invoke_agent(self, agent, prompt, config, stage, task_id)`

The `agent` parameter goes away. The dispatcher doesn't pick an agent — the prompt does, via `@agent-<name>`.

New signature: `_invoke_stage(self, prompt, config, stage, task_id="")`

Drop:
- Per-agent model lookup (`config.models`)
- Per-agent timeout lookup from config (use single `main_agent.timeout`)
- `agent_config.get("command")` template path — no more command templates

### 8. Rewrite prompts to delegate via `@agent-<name>`

**File:** `src/context/prompts.yaml`

Each stage prompt changes from direct instructions (telling the subagent what to do) to orchestration instructions (telling the main agent to delegate). Example for plan review:

**Before (direct to plan-reviewer agent):**
```
Review the executable specification at: $plan_path
Original intent at: $intent_path
...
Write the YAML review to: $output_path
```

**After (instruction to main agent to delegate):**
```
@agent-plan-reviewer Review the executable specification at: $plan_path
against the original intent at: $intent_path in project $project_dir.

Write the YAML review to: $output_path
The file must start with --- and contain only valid YAML conforming to PlanReviewOutput schema.

Time constraint: current time is $current_time. Hard limit of $timeout_minutes minutes.
```

The subagent's `.md` system prompt already contains the detailed review instructions, criteria, and formatting rules. The stage prompt provides only the per-invocation context (file paths, constraints).

### 9. Slim down agent `.md` files

Currently each agent `.md` has both:
- Frontmatter (model, tools, maxTurns) — stays
- Body (detailed instructions) — this is the subagent's system prompt and stays, but the per-stage instructions that are currently duplicated between `prompts.yaml` and the `.md` body should live in only one place

The `.md` body should define the agent's **role and approach**. The `prompts.yaml` templates provide the **per-invocation inputs** (file paths, time constraints). No duplication between them.

### 10. Drop `_build_cmd_from_template()` and `resume_args_template`

**Files:** `session.py:395-413`, `AgentInvocation` fields

With no per-agent command templates, this entire code path is dead. One `_build_cmd()` method handles everything.

### 11. Simplify preflight

Current preflight:
- Starts a `--bare` session
- Checks `--plugin-dir` loaded
- Checks all required agents present by qualified name

New preflight:
- Starts a normal session with `--plugin-dir`
- Checks plugin loaded
- Checks agents present (names may use `.claude-plugin:` prefix or bare names — test showed both work)
- Optionally: verify Agent tool is available (not stripped by `--bare`)

### 12. Update `auth.py` docstrings

Remove references to `--bare` mode. Auth mechanism is unchanged — `ANTHROPIC_API_KEY` in subprocess env — but the docstrings reference `--bare` as the reason for manual auth resolution. Update to explain that we pass auth explicitly for subprocess isolation, not because of `--bare`.

### 13. Update `SessionTailer` for subagent transcripts

**File:** `session.py:29-131`

Current tailer watches `{sessionId}.jsonl` only. With subagent delegation, activity happens in `{sessionId}/subagents/agent-*.jsonl`. Update `poll()` to also watch the subagents directory for new transcripts appearing during execution.

### 14. Drop `SessionRegistry` or simplify

**File:** `session.py:567-669`

The `SessionRegistry` tracked per-agent sessions for reuse. With a single main session and subagents in their own ephemeral contexts, the registry's purpose is gone. The main session ID is managed by the dispatcher directly. Subagent sessions are managed by Claude Code internally.

Either delete entirely or reduce to a simple log of session IDs for debugging.

## What stays unchanged

- **Pipeline state machine** (`state.py`): stages, transitions, task states — all unchanged
- **Schema validation** (`schemas.py`): artifact validation after each stage — unchanged
- **Artifact store** (`artifacts/store.py`): YAML artifact read/write — unchanged
- **TUI** (`tui.py`): rendering — unchanged (maybe add subagent activity display)
- **Auth resolution** (`auth.py`): mechanism unchanged, just docstring updates
- **Agent `.md` frontmatter**: model, tools, maxTurns — these define subagent capabilities natively
- **Plugin structure**: `plugin.json`, `agents/`, `hooks/`, `skills/` — loaded natively by Claude Code
- **`--resume` / `--session-id`**: single session across pipeline, same as today
- **`--output-format json`**: dispatcher still parses JSON events
- **`--permission-mode bypassPermissions`**: unattended execution

## Migration order

1. **Update `session.py`**: drop `--bare`, `--agents`, `--append-system-prompt-file`, `bake_agents_json()`, simplify `_build_cmd`, simplify `AgentInvocation`
2. **Update `config.yaml`**: replace 9 agent templates with single main-agent config
3. **Update `core.py` `_invoke_agent()`**: drop agent parameter, simplify to `_invoke_stage()`
4. **Rewrite `prompts.yaml`**: stage prompts use `@agent-<name>` delegation
5. **Update preflight**: adapt to non-bare plugin loading
6. **Update `SessionTailer`**: watch subagent transcript dir
7. **Clean up**: drop `SessionRegistry`, dead `AgentInvocation` fields, docstrings
8. **Update tests**: adapt `test_session.py`, `test_agent_commands.py`, `test_core.py`
9. **Update docs**: `CLAUDE.md`, `docs/architecture-snapshot.md`, `docs/pipeline.md`

## Risks

| Risk | Mitigation |
|------|-----------|
| Main agent doesn't delegate (does work itself) | `@agent-<name>` syntax proven reliable; explicit "delegate to" instruction |
| Main agent adds commentary instead of passing results through | Prompt engineering: "Report the agent's output verbatim" |
| Extra cost from main agent orchestration turns | Acceptable tradeoff for context isolation; main agent uses minimal turns |
| Plugin hooks fire unexpectedly without `--bare` | Review `.claude-plugin/settings.json` hooks — they may need adjustment for the new flow |
| Subagent can't write output YAML (tool restrictions) | Ensure executor/planner agents have Write tool in frontmatter (already do) |

"""Claude Code CLI session management."""

import os
import json
import signal
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

import yaml

import sys

from .auth import build_subprocess_env, resolve_auth_env
from .yaml_utils import extract_yaml, load_yaml_file


# ── Live session tailer ─────────────────────────────────────────────────────

_DIM = "\033[2m"
_CYAN = "\033[36m"
_RESET = "\033[0m"


class SessionTailer:
    """Tails Claude Code JSONL conversation logs in real time.

    Watches the project's Claude dir for JSONL files (main session + subagents)
    and prints compact summaries of tool calls and text output to stderr.
    """

    def __init__(self, project_dir: Path, session_id: str):
        slug = str(project_dir).replace("/", "-")
        self._watch_dir = Path.home() / ".claude" / "projects" / slug
        self._session_id = session_id
        self._files: dict[Path, int] = {}  # path -> bytes read offset
        self._known_files: set[Path] = set()
        # Snapshot existing file sizes so we only show NEW content
        self._baseline: dict[Path, int] = {}
        if self._watch_dir.exists():
            for path in self._watch_dir.glob("*.jsonl"):
                try:
                    self._baseline[path] = path.stat().st_size
                except OSError:
                    pass

    def poll(self) -> None:
        """Check for new content in all session JSONL files."""
        if not self._watch_dir.exists():
            return
        # Discover new JSONL files (subagents spawned after tailer started)
        for path in self._watch_dir.glob("*.jsonl"):
            if path not in self._known_files:
                self._known_files.add(path)
                # Start from current size for pre-existing files, 0 for new ones
                self._files[path] = self._baseline.get(path, 0)
        # Read new lines from each file
        for path, offset in list(self._files.items()):
            try:
                size = path.stat().st_size
                if size <= offset:
                    continue
                with open(path) as f:
                    f.seek(offset)
                    new_data = f.read()
                    self._files[path] = f.tell()
                for line in new_data.splitlines():
                    self._print_event(line, path)
            except (OSError, ValueError):
                pass

    def _print_event(self, line: str, path: Path) -> None:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return
        etype = event.get("type")
        # Show the session file stem only if it's a subagent (not main session)
        prefix = ""
        if path.stem != self._session_id:
            prefix = f"[{path.stem[:8]}] "

        if etype == "assistant":
            for block in event.get("message", {}).get("content", []):
                if not isinstance(block, dict):
                    continue
                bt = block.get("type")
                if bt == "tool_use":
                    name = block.get("name", "?")
                    inp = block.get("input", {})
                    detail = self._tool_summary(name, inp)
                    self._emit(f"{prefix}{name}: {detail}")
                elif bt == "text":
                    text = block.get("text", "").strip()
                    if text:
                        # Show first line only, truncated
                        first = text.split("\n")[0][:120]
                        self._emit(f"{prefix}{first}")
        elif etype == "agent-setting":
            agent = event.get("agentSetting", "")
            if agent:
                self._emit(f"{prefix}agent: {agent}")

    @staticmethod
    def _tool_summary(name: str, inp: dict) -> str:
        if name in ("Read", "read"):
            return inp.get("file_path", "?")
        if name in ("Write", "write"):
            return inp.get("file_path", "?")
        if name in ("Edit", "edit"):
            return inp.get("file_path", "?")
        if name in ("Bash", "bash"):
            cmd = inp.get("command", "?")
            return cmd[:100] + ("..." if len(cmd) > 100 else "")
        if name in ("Glob", "glob"):
            return inp.get("pattern", "?")
        if name in ("Grep", "grep"):
            return f'/{inp.get("pattern", "?")}/  {inp.get("path", "")}'
        if name == "Agent":
            return inp.get("description", inp.get("prompt", "?"))[:80]
        return str(inp)[:80]

    @staticmethod
    def _emit(text: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        print(f"  {_DIM}{ts} {_CYAN}│{_RESET} {_DIM}{text}{_RESET}", file=sys.stderr, flush=True)


# ── Agent JSON baking ────────────────────────────────────────────────────────

# Fields from agent .md frontmatter that --agents JSON supports.
# 'memory' is intentionally excluded — it silently breaks --agents parsing
# in Claude Code CLI ≤2.1.89.
_SUPPORTED_AGENT_FIELDS = frozenset({
    "description", "model", "maxTurns", "tools",
    "disallowedTools", "effort",
})


def bake_agents_json(
    agents_dir: Path,
    output_path: Path,
    plugin_name: str = "xpatcher",
) -> dict[str, dict]:
    """Parse agent .md files and write a --agents-compatible JSON file.

    Returns the agents dict (keyed by ``plugin_name:agent_name``).
    """
    agents: dict[str, dict] = {}
    for md_file in sorted(agents_dir.glob("*.md")):
        text = md_file.read_text()
        parts = text.split("---", 2)
        if len(parts) < 3:
            continue
        meta = yaml.safe_load(parts[1])
        if not isinstance(meta, dict):
            continue
        body = parts[2].strip()
        name = meta.get("name", md_file.stem)
        agent_def: dict = {
            "description": str(meta.get("description", "")).strip(),
            "prompt": body,
        }
        for key in _SUPPORTED_AGENT_FIELDS - {"description"}:
            if key in meta:
                agent_def[key] = meta[key]
        agents[f"{plugin_name}:{name}"] = agent_def

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(agents))
    return agents


@dataclass
class AgentInvocation:
    """Parameters for a single Claude agent invocation."""
    prompt: str
    agent: Optional[str] = None
    session_id: Optional[str] = None
    max_turns: Optional[int] = None
    timeout: int = 600
    allowed_tools: Optional[list[str]] = None
    disallowed_tools: Optional[list[str]] = None
    model: Optional[str] = None
    permission_mode: str = "bypassPermissions"
    resume: bool = False
    cancel_check: Optional[Callable[[], bool]] = None
    command_template: Optional[list[str]] = None
    resume_args_template: Optional[list[str]] = None
    debug_tailer: Optional[SessionTailer] = None


@dataclass
class AgentResult:
    """Result from a Claude agent invocation."""
    session_id: str = ""
    raw_text: str = ""
    parsed: Optional[dict] = None
    exit_code: int = 0
    cost_usd: float = 0.0
    duration_ms: int = 0
    num_turns: int = 0
    stop_reason: str = ""
    usage: Optional[dict] = None
    events: list[dict] = field(default_factory=list)


@dataclass
class PreflightResult:
    """Result of the Claude Code CLI preflight check."""
    ok: bool
    error: str = ""
    cli_version: str = ""
    plugin_loaded: bool = False
    agents_found: list[str] = field(default_factory=list)
    cost_usd: float = 0.0


class ClaudeSession:
    """Manages Claude Code CLI invocations."""

    PLUGIN_NAME = "xpatcher"
    PREFLIGHT_TIMEOUT_SEC = 90

    REQUIRED_AGENTS = [
        "xpatcher:planner",
        "xpatcher:plan-reviewer",
        "xpatcher:executor",
        "xpatcher:reviewer",
        "xpatcher:gap-detector",
        "xpatcher:tech-writer",
        "xpatcher:explorer",
    ]

    def __init__(self, plugin_dir: Path, project_dir: Path, auth_env: dict[str, str] | None = None):
        self.plugin_dir = plugin_dir
        self.project_dir = project_dir
        self.plugin_name = self.PLUGIN_NAME
        self._auth_env = auth_env or {}
        self._xpatcher_home = plugin_dir.parent  # for re-resolving OAuth
        self._subprocess_env = build_subprocess_env(self._auth_env)
        self._agents_json: str | None = None
        agents_path = plugin_dir / "agents.json"
        if agents_path.is_file():
            raw = agents_path.read_text()
            json.loads(raw)  # fail fast on corrupt file
            self._agents_json = raw

    def _required_agents(self, plugin_name: str | None = None) -> list[str]:
        active_name = plugin_name or self.plugin_name or self.PLUGIN_NAME
        return [agent.replace(f"{self.PLUGIN_NAME}:", f"{active_name}:") for agent in self.REQUIRED_AGENTS]

    def preflight(self) -> PreflightResult:
        """Verify Claude Code CLI is authenticated, responsive, and plugin loaded."""
        cmd = [
            "claude", "--bare", "-p", "respond with ok",
            "--output-format", "json",
            "--plugin-dir", str(self.plugin_dir),
            "--max-turns", "1",
            "--permission-mode", "bypassPermissions",
        ]
        if self._agents_json:
            cmd.extend(["--agents", self._agents_json])

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.PREFLIGHT_TIMEOUT_SEC,
                env=self._subprocess_env,
            )
        except FileNotFoundError:
            return PreflightResult(
                ok=False,
                error="Claude Code CLI not found. Install from: https://claude.ai/code",
            )
        except subprocess.TimeoutExpired:
            return PreflightResult(
                ok=False,
                error=f"Claude Code CLI timed out after {self.PREFLIGHT_TIMEOUT_SEC}s. Check authentication.",
            )

        if result.returncode != 0:
            return PreflightResult(
                ok=False,
                error=f"CLI exited with code {result.returncode}: {result.stderr[:500]}",
            )

        try:
            events = json.loads(result.stdout)
        except json.JSONDecodeError:
            return PreflightResult(ok=False, error="Invalid JSON from CLI.")

        init_event = next(
            (e for e in events if e.get("type") == "system" and e.get("subtype") == "init"),
            None,
        )
        result_event = next(
            (e for e in events if e.get("type") == "result"),
            None,
        )

        if not init_event:
            return PreflightResult(ok=False, error="No init event in CLI output.")

        if result_event and result_event.get("is_error"):
            return PreflightResult(
                ok=False,
                error=f"CLI error: {result_event.get('result', 'unknown')}",
            )

        plugin_record = next(
            (p for p in init_event.get("plugins", []) if p.get("path") == str(self.plugin_dir)),
            None,
        )
        if plugin_record is None:
            plugins = [p.get("name") for p in init_event.get("plugins", [])]
            return PreflightResult(
                ok=False,
                error=f"Plugin '{self.PLUGIN_NAME}' not loaded. Found: {plugins}",
                cli_version=init_event.get("claude_code_version", ""),
            )
        # Note: plugin_record["name"] is the directory name (e.g. ".claude-plugin"),
        # NOT necessarily the agent prefix. Agents are baked with PLUGIN_NAME prefix,
        # so we keep self.plugin_name = PLUGIN_NAME for agent name construction.

        agents = init_event.get("agents", [])
        missing = [a for a in self._required_agents() if a not in agents]
        if missing:
            return PreflightResult(
                ok=False,
                error=f"Missing agents: {missing}",
                cli_version=init_event.get("claude_code_version", ""),
                plugin_loaded=True,
            )

        return PreflightResult(
            ok=True,
            cli_version=init_event.get("claude_code_version", ""),
            plugin_loaded=True,
            agents_found=agents,
            cost_usd=result_event.get("total_cost_usd", 0.0) if result_event else 0.0,
        )

    def preview_cmd(self, invocation: AgentInvocation) -> str:
        """Return a human-readable command string for debug output.

        Strips prompt text and agents JSON to keep the output scannable.
        """
        if invocation.command_template:
            cmd = self._build_cmd_from_template(invocation)
        else:
            cmd = self._build_cmd_legacy(invocation)
        sanitized = []
        skip_next = False
        for i, arg in enumerate(cmd):
            if skip_next:
                skip_next = False
                continue
            if arg in ("-p", "--prompt"):
                sanitized.append(arg)
                sanitized.append("'...'")
                skip_next = True
            elif arg == "--agents":
                sanitized.append(arg)
                sanitized.append("'{...}'")
                skip_next = True
            else:
                sanitized.append(arg)
        return " ".join(sanitized)

    def invoke(self, invocation: AgentInvocation) -> AgentResult:
        """Invoke a Claude agent via CLI."""
        if invocation.command_template:
            cmd = self._build_cmd_from_template(invocation)
        else:
            cmd = self._build_cmd_legacy(invocation)

        # Inject pre-assigned session ID if set but not already a --resume
        if invocation.session_id and "--resume" not in cmd and "--session-id" not in cmd:
            cmd.extend(["--session-id", invocation.session_id])

        # Inject project CLAUDE.md as system prompt context (--bare skips auto-discovery)
        claude_md = self.project_dir / "CLAUDE.md"
        if claude_md.is_file() and "--append-system-prompt-file" not in cmd:
            cmd.extend(["--append-system-prompt-file", str(claude_md)])

        return self._run_cmd(cmd, invocation)

    def _build_cmd_from_template(self, invocation: AgentInvocation) -> list[str]:
        """Build command from config-driven template with placeholder substitution."""
        subs = {
            "{prompt}": invocation.prompt,
            "{plugin_dir}": str(self.plugin_dir),
            "{agents_json}": self._agents_json or "",
        }
        cmd = [subs.get(arg, arg) for arg in invocation.command_template]
        # Drop --agents with empty value (happens when no agents.json exists)
        try:
            idx = cmd.index("--agents")
            if idx + 1 < len(cmd) and cmd[idx + 1] == "":
                del cmd[idx:idx + 2]
        except ValueError:
            pass
        if invocation.resume and invocation.session_id and invocation.resume_args_template:
            resume_subs = {"{session_id}": invocation.session_id}
            cmd.extend(resume_subs.get(arg, arg) for arg in invocation.resume_args_template)
        return cmd

    def _build_cmd_legacy(self, invocation: AgentInvocation) -> list[str]:
        """Build command from individual AgentInvocation fields (backward compat)."""
        cmd = [
            "claude", "--bare", "-p", invocation.prompt,
            "--output-format", "json",
            "--plugin-dir", str(self.plugin_dir),
            "--permission-mode", invocation.permission_mode,
        ]
        if self._agents_json:
            cmd.extend(["--agents", self._agents_json])
        if invocation.agent:
            qualified = invocation.agent
            if ":" not in qualified:
                qualified = f"{self.plugin_name}:{qualified}"
            cmd.extend(["--agent", qualified])
        if invocation.resume and invocation.session_id:
            cmd.extend(["--resume", invocation.session_id])
        if invocation.max_turns:
            cmd.extend(["--max-turns", str(invocation.max_turns)])
        if invocation.model:
            cmd.extend(["--model", invocation.model])
        if invocation.allowed_tools:
            cmd.extend(["--allowed-tools", ",".join(invocation.allowed_tools)])
        if invocation.disallowed_tools:
            cmd.extend(["--disallowed-tools", ",".join(invocation.disallowed_tools)])
        return cmd

    def _run_cmd(self, cmd: list[str], invocation: AgentInvocation) -> AgentResult:
        # Refresh OAuth token before each invocation to avoid stale credentials
        fresh_env = resolve_auth_env(self._xpatcher_home)
        if fresh_env:
            self._subprocess_env = build_subprocess_env(fresh_env)

        if invocation.cancel_check is None:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=invocation.timeout,
                cwd=str(self.project_dir),
                env=self._subprocess_env,
            )
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
            cancelled = False
        else:
            # Use temp files instead of PIPE to avoid pipe buffer exhaustion.
            # Large sessions (>64KB of --output-format json events) would fill
            # the pipe, causing the CLI to block or silently discard output.
            import tempfile
            stdout_file = tempfile.NamedTemporaryFile(mode="w+", suffix=".json", delete=False)
            stderr_file = tempfile.NamedTemporaryFile(mode="w+", suffix=".err", delete=False)
            try:
                proc = subprocess.Popen(
                    cmd,
                    stdout=stdout_file,
                    stderr=stderr_file,
                    cwd=str(self.project_dir),
                    env=self._subprocess_env,
                    start_new_session=True,
                )
                start = time.monotonic()
                stdout = ""
                stderr = ""
                cancelled = False

                try:
                    while True:
                        if invocation.cancel_check():
                            cancelled = True
                            self._terminate_process(proc)
                            proc.wait()
                            break
                        if proc.poll() is not None:
                            break
                        if time.monotonic() - start > invocation.timeout:
                            self._terminate_process(proc)
                            raise subprocess.TimeoutExpired(cmd, invocation.timeout)
                        if invocation.debug_tailer:
                            invocation.debug_tailer.poll()
                        time.sleep(0.25)
                except KeyboardInterrupt:
                    self._terminate_process(proc)
                    raise

                stdout_file.close()
                stderr_file.close()
                stdout = Path(stdout_file.name).read_text()
                stderr = Path(stderr_file.name).read_text()
            finally:
                for f in (stdout_file, stderr_file):
                    try:
                        Path(f.name).unlink(missing_ok=True)
                    except OSError:
                        pass

        try:
            events = json.loads(stdout) if stdout else []
        except json.JSONDecodeError:
            events = []

        session_id = ""
        raw_text = ""
        cost_usd = 0.0
        duration_ms = 0
        num_turns = 0
        stop_reason = ""
        usage = None

        for event in events:
            etype = event.get("type")
            if etype == "system" and event.get("subtype") == "init":
                session_id = event.get("session_id", session_id)
            elif etype == "result":
                raw_text = event.get("result", "")
                session_id = event.get("session_id", session_id)
                cost_usd = event.get("total_cost_usd", 0.0)
                duration_ms = event.get("duration_ms", 0)
                num_turns = event.get("num_turns", 0)
                stop_reason = event.get("stop_reason", "")
                usage = event.get("usage")

        yaml_content = extract_yaml(raw_text)

        return AgentResult(
            session_id=session_id,
            raw_text=raw_text,
            parsed=yaml_content,
            exit_code=130 if cancelled else proc.returncode,
            cost_usd=cost_usd,
            duration_ms=duration_ms,
            num_turns=num_turns,
            stop_reason="cancelled" if cancelled else stop_reason,
            usage=usage,
            events=events,
        )

    def _terminate_process(self, proc: subprocess.Popen) -> None:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except Exception:
            proc.terminate()
        try:
            proc.wait(timeout=5)
            return
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except Exception:
            proc.kill()

class SessionRecord:
    """A single session record in the registry."""

    def __init__(
        self,
        session_id: str,
        agent_type: str,
        stage: str,
        task_id: str = "",
        created_at: str = "",
    ):
        self.session_id = session_id
        self.agent_type = agent_type
        self.stage = stage
        self.task_id = task_id
        self.created_at = created_at or datetime.now(timezone.utc).isoformat()
        self.last_used_at = self.created_at
        self.turn_count = 0
        self.token_estimate = 0
        self.duration_ms = 0
        self.cost_usd = 0.0
        self.claude_debug_log = ""
        self.compacted = False


class SessionRegistry:
    """Tracks sessions across agent invocations for reuse and context bridging."""

    def __init__(self, registry_path: Path, abandon_threshold_pct: int = 90):
        self.registry_path = registry_path
        self._abandon_threshold = abandon_threshold_pct / 100.0
        self._sessions: dict[str, SessionRecord] = {}
        self._load()

    def _load(self) -> None:
        data = load_yaml_file(self.registry_path)
        if data:
            for sid, rec in data.get("sessions", {}).items():
                self._sessions[sid] = SessionRecord(
                    session_id=sid,
                    agent_type=rec.get("agent_type", ""),
                    stage=rec.get("stage", ""),
                    task_id=rec.get("task_id", ""),
                    created_at=rec.get("created_at", ""),
                )

    def register(
        self, result: AgentResult, agent_type: str, stage: str, task_id: str = ""
    ) -> str:
        rec = SessionRecord(result.session_id, agent_type, stage, task_id)
        rec.turn_count = result.num_turns
        rec.duration_ms = result.duration_ms
        rec.cost_usd = result.cost_usd
        if result.usage:
            rec.token_estimate = (
                result.usage.get("input_tokens", 0) + result.usage.get("output_tokens", 0)
            )
        debug_log = Path.home() / ".claude" / "debug" / f"{result.session_id}.txt"
        if debug_log.exists():
            rec.claude_debug_log = str(debug_log)
        self._sessions[result.session_id] = rec
        self._save()
        return result.session_id

    def get_session_for_continuation(
        self, stage: str, agent_type: str, task_id: str = ""
    ) -> Optional[str]:
        """Find a reusable session. Returns None if context likely exhausted.

        Sessions are matched by (agent_type, stage, task_id) — all three must
        match. This prevents cross-stage reuse (e.g. intent_capture session
        being resumed for planning even though both use the planner agent).
        """
        for sid, rec in self._sessions.items():
            if rec.agent_type == agent_type and rec.stage == stage and rec.task_id == task_id:
                context_limit = 1_000_000 if "[1m]" in agent_type else 200_000
                if rec.token_estimate > context_limit * self._abandon_threshold:
                    continue
                return sid
        return None

    def _save(self) -> None:
        data: dict = {"sessions": {}}
        for sid, rec in self._sessions.items():
            entry: dict = {
                "agent_type": rec.agent_type,
                "stage": rec.stage,
                "task_id": rec.task_id,
                "created_at": rec.created_at,
                "last_used_at": rec.last_used_at,
                "turn_count": rec.turn_count,
                "token_estimate": rec.token_estimate,
                "duration_ms": rec.duration_ms,
                "cost_usd": rec.cost_usd,
            }
            if rec.claude_debug_log:
                entry["claude_debug_log"] = rec.claude_debug_log
            data["sessions"][sid] = entry
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        self.registry_path.write_text(
            yaml.dump(data, default_flow_style=False, sort_keys=False)
        )

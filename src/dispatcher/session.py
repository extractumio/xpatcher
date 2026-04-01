"""Claude Code CLI session management."""

import os
import json
import signal
import subprocess
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

import yaml

from .schemas import ArtifactValidator, ValidationResult
from .yaml_utils import extract_yaml, load_yaml_file


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
    cancel_check: Optional[Callable[[], bool]] = None
    command_template: Optional[list[str]] = None
    resume_args_template: Optional[list[str]] = None


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

    def __init__(self, plugin_dir: Path, project_dir: Path):
        self.plugin_dir = plugin_dir
        self.project_dir = project_dir
        self.plugin_name = self.PLUGIN_NAME

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

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.PREFLIGHT_TIMEOUT_SEC,
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
        self.plugin_name = plugin_record.get("name", self.PLUGIN_NAME)

        agents = init_event.get("agents", [])
        missing = [a for a in self._required_agents(self.plugin_name) if a not in agents]
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

    def invoke(self, invocation: AgentInvocation) -> AgentResult:
        """Invoke a Claude agent via CLI."""
        if invocation.command_template:
            cmd = self._build_cmd_from_template(invocation)
        else:
            cmd = self._build_cmd_legacy(invocation)

        return self._run_cmd(cmd, invocation)

    def _build_cmd_from_template(self, invocation: AgentInvocation) -> list[str]:
        """Build command from config-driven template with placeholder substitution."""
        subs = {
            "{prompt}": invocation.prompt,
            "{plugin_dir}": str(self.plugin_dir),
        }
        cmd = [subs.get(arg, arg) for arg in invocation.command_template]
        if invocation.session_id and invocation.resume_args_template:
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
        if invocation.agent:
            qualified = invocation.agent
            if ":" not in qualified:
                qualified = f"{self.plugin_name}:{qualified}"
            cmd.extend(["--agent", qualified])
        if invocation.session_id:
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

        if invocation.cancel_check is None:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=invocation.timeout,
                cwd=str(self.project_dir),
            )
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
            cancelled = False
        else:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=str(self.project_dir),
                start_new_session=True,
            )
            start = time.monotonic()
            stdout = ""
            stderr = ""
            cancelled = False

            while True:
                if invocation.cancel_check():
                    cancelled = True
                    self._terminate_process(proc)
                    stdout, stderr = proc.communicate()
                    break
                if proc.poll() is not None:
                    stdout, stderr = proc.communicate()
                    break
                if time.monotonic() - start > invocation.timeout:
                    self._terminate_process(proc)
                    raise subprocess.TimeoutExpired(cmd, invocation.timeout)
                time.sleep(0.25)

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

class MalformedOutputRecovery:
    """Retries agent invocations with targeted fix prompts on malformed output."""

    MAX_FIX_ATTEMPTS = 2

    def __init__(self, session: ClaudeSession, validator: ArtifactValidator):
        self.session = session
        self.validator = validator

    def invoke_with_validation(
        self, invocation: AgentInvocation, expected_type: str
    ) -> tuple[AgentResult, ValidationResult]:
        """Invoke agent with up to MAX_FIX_ATTEMPTS retries for malformed output."""
        result = self.session.invoke(invocation)
        validation = self.validator.validate(result.raw_text, expected_type)

        for _attempt in range(self.MAX_FIX_ATTEMPTS):
            if validation.valid:
                break

            fix_prompt = self._build_fix_prompt(validation.errors, expected_type)
            fix_invocation = replace(
                invocation,
                prompt=fix_prompt,
                session_id=result.session_id,
            )
            result = self.session.invoke(fix_invocation)
            validation = self.validator.validate(result.raw_text, expected_type)

        return result, validation

    def _build_fix_prompt(self, errors: list[str], expected_type: str) -> str:
        error_list = "\n".join(f"- {e}" for e in errors)
        return (
            f"Your previous output had validation errors. Please fix and resubmit.\n\n"
            f"Expected artifact type: {expected_type}\n\n"
            f"Errors:\n{error_list}\n\n"
            f"Respond with ONLY the corrected YAML document. Start with --- on its own line."
        )


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
        if result.usage:
            rec.token_estimate = (
                result.usage.get("input_tokens", 0) + result.usage.get("output_tokens", 0)
            )
        self._sessions[result.session_id] = rec
        self._save()
        return result.session_id

    def get_session_for_continuation(
        self, stage: str, agent_type: str, task_id: str = ""
    ) -> Optional[str]:
        """Find a reusable session. Returns None if context likely exhausted."""
        for sid, rec in self._sessions.items():
            if rec.agent_type == agent_type and rec.task_id == task_id:
                context_limit = 1_000_000 if "[1m]" in agent_type else 200_000
                if rec.token_estimate > context_limit * self._abandon_threshold:
                    continue
                return sid
        return None

    def _save(self) -> None:
        data: dict = {"sessions": {}}
        for sid, rec in self._sessions.items():
            data["sessions"][sid] = {
                "agent_type": rec.agent_type,
                "stage": rec.stage,
                "task_id": rec.task_id,
                "created_at": rec.created_at,
                "last_used_at": rec.last_used_at,
                "turn_count": rec.turn_count,
                "token_estimate": rec.token_estimate,
            }
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        self.registry_path.write_text(
            yaml.dump(data, default_flow_style=False, sort_keys=False)
        )

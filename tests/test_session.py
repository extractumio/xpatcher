"""Tests for dispatcher.session — ClaudeSession, MalformedOutputRecovery, SessionRegistry."""

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from src.dispatcher.schemas import ArtifactValidator
from src.dispatcher.session import (
    AgentInvocation,
    AgentResult,
    ClaudeSession,
    MalformedOutputRecovery,
    PreflightResult,
    SessionRegistry,
)

# ===========================================================================
# ClaudeSession._extract_yaml
# ===========================================================================

class TestClaudeSessionExtractYaml:
    def test_raw_yaml(self):
        from src.dispatcher.yaml_utils import extract_yaml
        result = extract_yaml("type: plan\nfoo: bar")
        assert result == {"type": "plan", "foo": "bar"}

    def test_separator(self):
        from src.dispatcher.yaml_utils import extract_yaml
        result = extract_yaml("preamble\n---\ntype: plan\nfoo: bar")
        assert result["type"] == "plan"

    def test_yaml_code_block(self):
        from src.dispatcher.yaml_utils import extract_yaml
        text = "Output:\n```yaml\ntype: review\nverdict: approve\n```"
        result = extract_yaml(text)
        assert result["type"] == "review"

    def test_strip_prose(self):
        from src.dispatcher.yaml_utils import extract_yaml
        text = "Here is my analysis:\n\ntype: plan\nfoo: bar"
        result = extract_yaml(text)
        assert result["type"] == "plan"

    def test_none_on_garbage(self):
        from src.dispatcher.yaml_utils import extract_yaml
        result = extract_yaml("{{{{NOT YAML}}}}")
        assert result is None

    def test_none_on_empty(self):
        from src.dispatcher.yaml_utils import extract_yaml
        result = extract_yaml("")
        assert result is None


# ===========================================================================
# ClaudeSession.invoke — command construction
# ===========================================================================

class TestClaudeSessionInvoke:
    def setup_method(self):
        self.session = ClaudeSession(Path("/tmp/plugin"), Path("/tmp/project"))

    @patch("src.dispatcher.session.subprocess.run")
    def test_legacy_invoke_builds_full_command_from_invocation(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout=json.dumps([]),
            returncode=0,
        )
        inv = AgentInvocation(
            prompt="do stuff",
            agent="planner",
            session_id="sess-123",
            model="opus",
            max_turns=5,
            allowed_tools=["Read", "Bash"],
            disallowed_tools=["WebSearch"],
        )
        self.session.invoke(inv)

        cmd = mock_run.call_args[0][0]
        assert cmd == [
            "claude",
            "--bare",
            "-p",
            "do stuff",
            "--output-format",
            "json",
            "--plugin-dir",
            "/tmp/plugin",
            "--permission-mode",
            "bypassPermissions",
            "--agent",
            "xpatcher:planner",
            "--resume",
            "sess-123",
            "--max-turns",
            "5",
            "--model",
            "opus",
            "--allowed-tools",
            "Read,Bash",
            "--disallowed-tools",
            "WebSearch",
        ]

    @patch("src.dispatcher.session.subprocess.run")
    def test_preserves_already_qualified_agent_name(self, mock_run):
        mock_run.return_value = MagicMock(stdout=json.dumps([]), returncode=0)

        self.session.invoke(AgentInvocation(prompt="test", agent="xpatcher:planner"))

        cmd = mock_run.call_args[0][0]
        agent_idx = cmd.index("--agent")
        assert cmd[agent_idx + 1] == "xpatcher:planner"


class TestClaudeSessionCommandTemplate:
    def setup_method(self):
        self.session = ClaudeSession(Path("/tmp/plugin"), Path("/tmp/project"))

    @patch("src.dispatcher.session.subprocess.run")
    def test_template_substitutes_runtime_values_and_resume_args(self, mock_run):
        mock_run.return_value = MagicMock(stdout=json.dumps([]), returncode=0)
        inv = AgentInvocation(
            prompt="do stuff",
            command_template=["claude", "-p", "{prompt}", "--plugin-dir", "{plugin_dir}", "--model", "opus"],
            resume_args_template=["--resume", "{session_id}"],
            session_id="sess-abc",
        )
        self.session.invoke(inv)
        cmd = mock_run.call_args[0][0]
        assert cmd == [
            "claude",
            "-p",
            "do stuff",
            "--plugin-dir",
            "/tmp/plugin",
            "--model",
            "opus",
            "--resume",
            "sess-abc",
        ]

    @patch("src.dispatcher.session.subprocess.run")
    def test_template_skips_resume_args_when_no_session(self, mock_run):
        mock_run.return_value = MagicMock(stdout=json.dumps([]), returncode=0)
        inv = AgentInvocation(
            prompt="do stuff",
            command_template=["claude", "-p", "{prompt}"],
            resume_args_template=["--resume", "{session_id}"],
        )
        self.session.invoke(inv)
        cmd = mock_run.call_args[0][0]
        assert "--resume" not in cmd

    @patch("src.dispatcher.session.subprocess.run")
    def test_template_with_custom_binary(self, mock_run):
        mock_run.return_value = MagicMock(stdout=json.dumps([]), returncode=0)
        inv = AgentInvocation(
            prompt="do stuff",
            command_template=["codex", "--prompt", "{prompt}", "--model", "o3"],
        )
        self.session.invoke(inv)
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "codex"
        assert cmd == ["codex", "--prompt", "do stuff", "--model", "o3"]


# ===========================================================================
# ClaudeSession.preflight
# ===========================================================================

class TestClaudeSessionPreflight:
    def setup_method(self):
        self.session = ClaudeSession(Path("/tmp/plugin"), Path("/tmp/project"))

    def _make_preflight_output(self, plugin_loaded=True, agents=None, is_error=False):
        """Build a minimal JSON output that mimics Claude CLI preflight."""
        if agents is None:
            agents = list(ClaudeSession.REQUIRED_AGENTS)
        plugins = [{"name": "xpatcher", "path": str(self.session.plugin_dir)}] if plugin_loaded else []
        events = [
            {
                "type": "system",
                "subtype": "init",
                "session_id": "sess-abc",
                "claude_code_version": "1.2.3",
                "plugins": plugins,
                "agents": agents,
            },
            {
                "type": "result",
                "result": "ok",
                "session_id": "sess-abc",
                "total_cost_usd": 0.001,
                "is_error": is_error,
            },
        ]
        return json.dumps(events)

    @patch("src.dispatcher.session.subprocess.run")
    def test_preflight_success(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=self._make_preflight_output(),
            stderr="",
        )
        result = self.session.preflight()
        assert result.ok is True
        assert result.cli_version == "1.2.3"
        assert result.plugin_loaded is True
        assert len(result.agents_found) > 0
        assert self.session.plugin_name == "xpatcher"

    @patch("src.dispatcher.session.subprocess.run")
    def test_preflight_cli_not_found(self, mock_run):
        mock_run.side_effect = FileNotFoundError()
        result = self.session.preflight()
        assert result.ok is False
        assert "not found" in result.error

    @patch("src.dispatcher.session.subprocess.run")
    def test_preflight_timeout(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="claude", timeout=30)
        result = self.session.preflight()
        assert result.ok is False
        assert "timed out" in result.error

    @patch("src.dispatcher.session.subprocess.run")
    def test_preflight_plugin_not_loaded(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=self._make_preflight_output(plugin_loaded=False),
            stderr="",
        )
        result = self.session.preflight()
        assert result.ok is False
        assert "not loaded" in result.error

    @patch("src.dispatcher.session.subprocess.run")
    def test_preflight_missing_agents(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=self._make_preflight_output(agents=["xpatcher:planner"]),
            stderr="",
        )
        result = self.session.preflight()
        assert result.ok is False
        assert "Missing agents" in result.error

    @patch("src.dispatcher.session.subprocess.run")
    def test_preflight_uses_runtime_plugin_dir_name(self, mock_run):
        runtime_name = ".claude-plugin"
        agents = [agent.replace("xpatcher:", f"{runtime_name}:") for agent in ClaudeSession.REQUIRED_AGENTS]
        events = [
            {
                "type": "system",
                "subtype": "init",
                "session_id": "sess-abc",
                "claude_code_version": "1.2.3",
                "plugins": [{"name": runtime_name, "path": str(self.session.plugin_dir)}],
                "agents": agents,
            },
            {
                "type": "result",
                "result": "ok",
                "session_id": "sess-abc",
                "total_cost_usd": 0.001,
                "is_error": False,
            },
        ]
        mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps(events), stderr="")
        result = self.session.preflight()
        assert result.ok is True
        assert self.session.plugin_name == runtime_name


# ===========================================================================
# MalformedOutputRecovery
# ===========================================================================

class TestMalformedOutputRecovery:
    def setup_method(self):
        self.mock_session = MagicMock(spec=ClaudeSession)
        self.validator = ArtifactValidator()
        self.recovery = MalformedOutputRecovery(self.mock_session, self.validator)

    def test_valid_on_first_try(self):
        valid_yaml = yaml.dump({
            "type": "simplification",
            "mode": "dry_run",
        })
        first_result = AgentResult(
            session_id="sess-1",
            raw_text=valid_yaml,
        )
        self.mock_session.invoke.return_value = first_result
        inv = AgentInvocation(prompt="test")
        _, validation = self.recovery.invoke_with_validation(inv, "simplification")
        assert validation.valid is True
        # Should have been called only once
        assert self.mock_session.invoke.call_count == 1

    def test_retries_on_invalid_succeeds_on_retry(self):
        invalid_result = AgentResult(session_id="sess-1", raw_text="garbage")
        valid_yaml = yaml.dump({
            "type": "simplification",
            "mode": "dry_run",
        })
        valid_result = AgentResult(session_id="sess-1", raw_text=valid_yaml)

        self.mock_session.invoke.side_effect = [invalid_result, valid_result]
        inv = AgentInvocation(prompt="test")
        _, validation = self.recovery.invoke_with_validation(inv, "simplification")
        assert validation.valid is True
        assert self.mock_session.invoke.call_count == 2

    def test_exhausts_retries_returns_invalid(self):
        bad = AgentResult(session_id="sess-1", raw_text="garbage")
        self.mock_session.invoke.return_value = bad
        inv = AgentInvocation(prompt="test")
        _, validation = self.recovery.invoke_with_validation(inv, "simplification")
        assert validation.valid is False
        # 1 initial + MAX_FIX_ATTEMPTS retries
        assert self.mock_session.invoke.call_count == 1 + MalformedOutputRecovery.MAX_FIX_ATTEMPTS


# ===========================================================================
# SessionRegistry
# ===========================================================================

class TestSessionRegistry:
    def test_register_and_save(self, tmp_path):
        reg_path = tmp_path / "sessions.yaml"
        registry = SessionRegistry(reg_path)

        result = AgentResult(
            session_id="sess-abc",
            num_turns=3,
            usage={"input_tokens": 1000, "output_tokens": 500},
        )
        sid = registry.register(result, agent_type="planner", stage="planning")
        assert sid == "sess-abc"

        # Verify file was saved
        assert reg_path.exists()
        data = yaml.safe_load(reg_path.read_text())
        assert "sess-abc" in data["sessions"]

    def test_get_session_for_continuation(self, tmp_path):
        reg_path = tmp_path / "sessions.yaml"
        registry = SessionRegistry(reg_path)

        result = AgentResult(
            session_id="sess-abc",
            num_turns=3,
            usage={"input_tokens": 1000, "output_tokens": 500},
        )
        registry.register(result, agent_type="planner", stage="planning")

        sid = registry.get_session_for_continuation(
            stage="planning", agent_type="planner"
        )
        assert sid == "sess-abc"

    def test_get_session_none_when_no_match(self, tmp_path):
        reg_path = tmp_path / "sessions.yaml"
        registry = SessionRegistry(reg_path)
        sid = registry.get_session_for_continuation(
            stage="planning", agent_type="planner"
        )
        assert sid is None

    def test_reload_from_disk(self, tmp_path):
        reg_path = tmp_path / "sessions.yaml"
        reg1 = SessionRegistry(reg_path)
        result = AgentResult(
            session_id="sess-xyz",
            num_turns=1,
            usage={"input_tokens": 100, "output_tokens": 50},
        )
        reg1.register(result, agent_type="executor", stage="task_execution", task_id="task-001")

        # Create a new instance that loads from disk
        reg2 = SessionRegistry(reg_path)
        sid = reg2.get_session_for_continuation(
            stage="task_execution", agent_type="executor", task_id="task-001"
        )
        assert sid == "sess-xyz"

"""Tests for dispatcher.session — ClaudeSession."""

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
    PreflightResult,

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
    def test_invoke_builds_correct_command(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout=json.dumps([]),
            returncode=0,
        )
        inv = AgentInvocation(
            prompt="do stuff",
            session_id="sess-123",
            resume=True,
            max_turns=5,
        )
        self.session.invoke(inv)

        cmd = mock_run.call_args[0][0]
        assert cmd == [
            "claude",
            "-p",
            "do stuff",
            "--output-format",
            "json",
            "--plugin-dir",
            "/tmp/plugin",
            "--permission-mode",
            "bypassPermissions",
            "--resume",
            "sess-123",
            "--max-turns",
            "5",
        ]

    @patch("src.dispatcher.session.subprocess.run")
    def test_no_bare_flag_in_command(self, mock_run):
        """Verify --bare is NOT used (needed for Agent tool support)."""
        mock_run.return_value = MagicMock(stdout=json.dumps([]), returncode=0)
        self.session.invoke(AgentInvocation(prompt="test"))
        cmd = mock_run.call_args[0][0]
        assert "--bare" not in cmd

    @patch("src.dispatcher.session.subprocess.run")
    def test_no_agents_flag_in_command(self, mock_run):
        """Verify --agents is NOT used (agents loaded via --plugin-dir)."""
        mock_run.return_value = MagicMock(stdout=json.dumps([]), returncode=0)
        self.session.invoke(AgentInvocation(prompt="test"))
        cmd = mock_run.call_args[0][0]
        assert "--agents" not in cmd

    @patch("src.dispatcher.session.subprocess.run")
    def test_no_append_system_prompt_file(self, mock_run, tmp_path):
        """Verify --append-system-prompt-file is NOT used (CLAUDE.md auto-discovered)."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        (project_dir / "CLAUDE.md").write_text("# Guide\n")

        session = ClaudeSession(Path("/tmp/plugin"), project_dir)
        mock_run.return_value = MagicMock(stdout=json.dumps([]), returncode=0)
        session.invoke(AgentInvocation(prompt="test"))
        cmd = mock_run.call_args[0][0]
        assert "--append-system-prompt-file" not in cmd


class TestClaudeSessionCommandShape:
    """Verify CLI command includes session-id and correct structure."""

    @patch("src.dispatcher.session.subprocess.run")
    def test_command_includes_session_id(self, mock_run, tmp_path):
        session = ClaudeSession(Path("/tmp/plugin"), tmp_path / "project")
        mock_run.return_value = MagicMock(stdout=json.dumps([]), returncode=0)

        session.invoke(AgentInvocation(prompt="do work", session_id="sess-42"))

        cmd = mock_run.call_args[0][0]

        # Session ID injected
        assert "--session-id" in cmd
        idx = cmd.index("--session-id")
        assert cmd[idx + 1] == "sess-42"

        # Core flags present
        assert "--output-format" in cmd
        assert "--plugin-dir" in cmd

    @patch("src.dispatcher.session.subprocess.run")
    def test_resume_uses_resume_flag(self, mock_run):
        session = ClaudeSession(Path("/tmp/plugin"), Path("/tmp/project"))
        mock_run.return_value = MagicMock(stdout=json.dumps([]), returncode=0)
        session.invoke(AgentInvocation(prompt="test", session_id="s1", resume=True))
        cmd = mock_run.call_args[0][0]
        assert "--resume" in cmd
        assert "--session-id" not in cmd  # --resume already implies session


# ===========================================================================
# ClaudeSession.preflight
# ===========================================================================

class TestClaudeSessionPreflight:
    def setup_method(self):
        self.session = ClaudeSession(Path("/tmp/plugin"), Path("/tmp/project"))

    def _make_preflight_output(self, plugin_loaded=True, agents=None, is_error=False):
        """Build a minimal JSON output that mimics Claude CLI preflight."""
        if agents is None:
            # Use qualified names as Claude Code returns them
            agents = [f".claude-plugin:{a}" for a in ClaudeSession.REQUIRED_AGENTS]
        plugins = [{"name": ".claude-plugin", "path": str(self.session.plugin_dir)}] if plugin_loaded else []
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

    @patch("src.dispatcher.session.subprocess.run")
    def test_preflight_no_bare_flag(self, mock_run):
        """Preflight must not use --bare."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=self._make_preflight_output(),
            stderr="",
        )
        self.session.preflight()
        cmd = mock_run.call_args[0][0]
        assert "--bare" not in cmd

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
            stdout=self._make_preflight_output(agents=[".claude-plugin:planner"]),
            stderr="",
        )
        result = self.session.preflight()
        assert result.ok is False
        assert "Missing agents" in result.error

    @patch("src.dispatcher.session.subprocess.run")
    def test_preflight_matches_agents_by_bare_name(self, mock_run):
        """Agent prefix (e.g. '.claude-plugin:') should be ignored when matching."""
        agents = [f"custom-prefix:{a}" for a in ClaudeSession.REQUIRED_AGENTS]
        events = [
            {
                "type": "system",
                "subtype": "init",
                "session_id": "sess-abc",
                "claude_code_version": "1.2.3",
                "plugins": [{"name": "custom-prefix", "path": str(self.session.plugin_dir)}],
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

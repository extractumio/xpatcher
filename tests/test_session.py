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
    def test_legacy_invoke_builds_full_command_from_invocation(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout=json.dumps([]),
            returncode=0,
        )
        inv = AgentInvocation(
            prompt="do stuff",
            agent="planner",
            session_id="sess-123",
            resume=True,
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


class TestClaudeSessionClaudeMdAndCommandShape:
    """Verify CLI command includes CLAUDE.md, session-id, and correct structure."""

    @patch("src.dispatcher.session.subprocess.run")
    def test_command_includes_claude_md_session_id_and_agent(self, mock_run, tmp_path):
        """Single test checking multiple CLI command properties."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        claude_md = project_dir / "CLAUDE.md"
        claude_md.write_text("# Project Guide\nUse pytest for tests.\n")

        session = ClaudeSession(Path("/tmp/plugin"), project_dir)
        mock_run.return_value = MagicMock(stdout=json.dumps([]), returncode=0)

        session.invoke(AgentInvocation(prompt="do work", agent="planner", session_id="sess-42"))

        cmd = mock_run.call_args[0][0]

        # CLAUDE.md injected
        assert "--append-system-prompt-file" in cmd
        idx = cmd.index("--append-system-prompt-file")
        assert cmd[idx + 1] == str(claude_md)

        # Session ID injected
        assert "--session-id" in cmd
        idx = cmd.index("--session-id")
        assert cmd[idx + 1] == "sess-42"

        # Agent qualified with plugin name
        assert "--agent" in cmd
        idx = cmd.index("--agent")
        assert cmd[idx + 1] == "xpatcher:planner"

        # Core flags present
        assert "--bare" in cmd
        assert "--output-format" in cmd

    @patch("src.dispatcher.session.subprocess.run")
    def test_command_skips_claude_md_when_absent(self, mock_run, tmp_path):
        project_dir = tmp_path / "project"
        project_dir.mkdir()

        session = ClaudeSession(Path("/tmp/plugin"), project_dir)
        mock_run.return_value = MagicMock(stdout=json.dumps([]), returncode=0)

        session.invoke(AgentInvocation(prompt="test", agent="planner"))

        cmd = mock_run.call_args[0][0]
        assert "--append-system-prompt-file" not in cmd

    @patch("src.dispatcher.session.subprocess.run")
    def test_command_template_also_gets_claude_md_and_session_id(self, mock_run, tmp_path):
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        (project_dir / "CLAUDE.md").write_text("# Guide\n")

        session = ClaudeSession(Path("/tmp/plugin"), project_dir)
        mock_run.return_value = MagicMock(stdout=json.dumps([]), returncode=0)

        inv = AgentInvocation(
            prompt="test",
            command_template=["claude", "--bare", "-p", "{prompt}"],
            session_id="sess-99",
        )
        session.invoke(inv)

        cmd = mock_run.call_args[0][0]

        # Template substitution worked
        assert cmd[:3] == ["claude", "--bare", "-p"]
        assert cmd[3] == "test"

        # CLAUDE.md injected after template expansion
        assert "--append-system-prompt-file" in cmd
        assert cmd[cmd.index("--append-system-prompt-file") + 1] == str(project_dir / "CLAUDE.md")

        # Session ID injected
        assert "--session-id" in cmd
        assert cmd[cmd.index("--session-id") + 1] == "sess-99"


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
            resume=True,
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
    def test_preflight_ignores_plugin_dir_name_for_agent_matching(self, mock_run):
        """Plugin dir name (e.g. '.claude-plugin') differs from agent prefix ('xpatcher:').
        Preflight must match agents by the baked prefix, not the dir name."""
        runtime_name = ".claude-plugin"
        # Agents are baked with xpatcher: prefix, NOT .claude-plugin:
        agents = list(ClaudeSession.REQUIRED_AGENTS)
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
        # plugin_name stays as PLUGIN_NAME for correct agent invocation
        assert self.session.plugin_name == ClaudeSession.PLUGIN_NAME



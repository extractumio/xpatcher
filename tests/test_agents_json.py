"""Tests for agent .md structure, bake_agents_json(), and --agents injection."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from src.dispatcher.session import (
    AgentInvocation,
    ClaudeSession,
    bake_agents_json,
)


# ── Paths ─────────────────────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).resolve().parents[1]
AGENTS_DIR = REPO_ROOT / ".claude-plugin" / "agents"

EXPECTED_AGENTS = {
    "executor",
    "explorer",
    "gap-detector",
    "plan-reviewer",
    "planner",
    "reviewer",
    "simplifier",
    "tech-writer",
    "tester",
}

REQUIRED_FRONTMATTER = {"name", "description", "model"}


# ── Agent .md file structure ──────────────────────────────────────────────────

class TestAgentMdStructure:
    """Validate that every agent .md has correct frontmatter and body."""

    @pytest.fixture(autouse=True)
    def _load_agents(self):
        self.agent_files = sorted(AGENTS_DIR.glob("*.md"))
        self.parsed = {}
        for f in self.agent_files:
            text = f.read_text()
            parts = text.split("---", 2)
            assert len(parts) >= 3, f"{f.name} missing YAML frontmatter delimiters"
            meta = yaml.safe_load(parts[1])
            assert isinstance(meta, dict), f"{f.name} frontmatter is not a YAML mapping"
            body = parts[2].strip()
            self.parsed[f.stem] = {"meta": meta, "body": body, "path": f}

    def test_all_expected_agents_exist(self):
        assert set(self.parsed) == EXPECTED_AGENTS

    def test_each_agent_has_required_frontmatter(self):
        for name, data in self.parsed.items():
            for field in REQUIRED_FRONTMATTER:
                assert field in data["meta"], f"{name}.md missing '{field}'"

    def test_each_agent_has_nonempty_body(self):
        for name, data in self.parsed.items():
            assert len(data["body"]) > 50, f"{name}.md body too short"

    def test_agent_names_match_filenames(self):
        for name, data in self.parsed.items():
            assert data["meta"]["name"] == name, (
                f"{name}.md: frontmatter name '{data['meta']['name']}' != filename '{name}'"
            )

    def test_model_is_valid(self):
        valid_models = {"haiku", "sonnet", "opus", "opus[1m]"}
        for name, data in self.parsed.items():
            assert data["meta"]["model"] in valid_models, (
                f"{name}.md has unexpected model '{data['meta']['model']}'"
            )

    def test_tools_is_list_when_present(self):
        for name, data in self.parsed.items():
            if "tools" in data["meta"]:
                assert isinstance(data["meta"]["tools"], list), (
                    f"{name}.md 'tools' must be a list"
                )


# ── bake_agents_json() ────────────────────────────────────────────────────────

class TestBakeAgentsJson:
    @pytest.fixture(autouse=True)
    def _bake(self, tmp_path):
        self.output = tmp_path / "agents.json"
        self.agents = bake_agents_json(AGENTS_DIR, self.output)

    def test_bakes_all_agents(self):
        assert len(self.agents) == len(EXPECTED_AGENTS)
        for name in EXPECTED_AGENTS:
            assert f"xpatcher:{name}" in self.agents

    def test_output_is_valid_json(self):
        data = json.loads(self.output.read_text())
        assert isinstance(data, dict)
        assert len(data) == len(EXPECTED_AGENTS)

    def test_unsupported_fields_are_stripped(self):
        unsupported = {"memory", "author", "name"}
        for name, defn in self.agents.items():
            leaked = unsupported & set(defn)
            assert not leaked, f"{name} still has unsupported fields: {leaked}"

    def test_each_agent_has_description_and_prompt(self):
        for name, defn in self.agents.items():
            assert "description" in defn and defn["description"], f"{name} missing description"
            assert "prompt" in defn and defn["prompt"], f"{name} missing prompt"

    def test_supported_fields_are_preserved(self):
        for name, defn in self.agents.items():
            assert "model" in defn, f"{name} missing model"
            assert "effort" in defn, f"{name} missing effort"

    def test_custom_plugin_name(self, tmp_path):
        output = tmp_path / "custom" / "agents.json"
        agents = bake_agents_json(AGENTS_DIR, output, plugin_name="my-plugin")
        for key in agents:
            assert key.startswith("my-plugin:"), f"unexpected key prefix: {key}"

    def test_skips_non_yaml_frontmatter(self, tmp_path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        (agents_dir / "broken.md").write_text("no frontmatter here")
        (agents_dir / "good.md").write_text(
            "---\nname: good\ndescription: ok\n---\nPrompt body."
        )
        output = tmp_path / "out" / "agents.json"
        agents = bake_agents_json(agents_dir, output)
        assert len(agents) == 1
        assert "xpatcher:good" in agents


# ── --agents injection in commands ────────────────────────────────────────────

class TestAgentsJsonInjection:
    """Verify --agents is injected into CLI commands when agents.json exists."""

    def _session_with_agents_json(self, tmp_path):
        plugin_dir = tmp_path / "plugin"
        plugin_dir.mkdir()
        agents_json = {"xpatcher:test": {"description": "test", "prompt": "test"}}
        (plugin_dir / "agents.json").write_text(json.dumps(agents_json))
        return ClaudeSession(plugin_dir, tmp_path / "project")

    @patch("src.dispatcher.session.subprocess.run")
    def test_legacy_command_includes_agents_flag(self, mock_run, tmp_path):
        session = self._session_with_agents_json(tmp_path)
        mock_run.return_value = MagicMock(stdout=json.dumps([]), returncode=0)
        session.invoke(AgentInvocation(prompt="test", agent="planner"))
        cmd = mock_run.call_args[0][0]
        assert "--agents" in cmd
        agents_idx = cmd.index("--agents")
        agents_val = json.loads(cmd[agents_idx + 1])
        assert "xpatcher:test" in agents_val

    @patch("src.dispatcher.session.subprocess.run")
    def test_template_command_substitutes_agents_json(self, mock_run, tmp_path):
        session = self._session_with_agents_json(tmp_path)
        mock_run.return_value = MagicMock(stdout=json.dumps([]), returncode=0)
        inv = AgentInvocation(
            prompt="test",
            command_template=[
                "claude", "--bare", "-p", "{prompt}",
                "--plugin-dir", "{plugin_dir}",
                "--agents", "{agents_json}",
                "--agent", "xpatcher:planner",
            ],
        )
        session.invoke(inv)
        cmd = mock_run.call_args[0][0]
        agents_idx = cmd.index("--agents")
        agents_val = json.loads(cmd[agents_idx + 1])
        assert "xpatcher:test" in agents_val

    @patch("src.dispatcher.session.subprocess.run")
    def test_no_agents_flag_when_no_agents_json(self, mock_run):
        session = ClaudeSession(Path("/tmp/no-such-plugin"), Path("/tmp/project"))
        mock_run.return_value = MagicMock(stdout=json.dumps([]), returncode=0)
        session.invoke(AgentInvocation(prompt="test", agent="planner"))
        cmd = mock_run.call_args[0][0]
        assert "--agents" not in cmd

    @patch("src.dispatcher.session.subprocess.run")
    def test_template_strips_empty_agents_placeholder(self, mock_run):
        """When no agents.json exists, --agents "" must be stripped from templates."""
        session = ClaudeSession(Path("/tmp/no-such-plugin"), Path("/tmp/project"))
        mock_run.return_value = MagicMock(stdout=json.dumps([]), returncode=0)
        inv = AgentInvocation(
            prompt="test",
            command_template=[
                "claude", "--bare", "-p", "{prompt}",
                "--agents", "{agents_json}",
                "--agent", "xpatcher:planner",
            ],
        )
        session.invoke(inv)
        cmd = mock_run.call_args[0][0]
        assert "--agents" not in cmd
        assert "" not in cmd

    @patch("src.dispatcher.session.subprocess.run")
    def test_preflight_includes_agents_flag(self, mock_run, tmp_path):
        session = self._session_with_agents_json(tmp_path)
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps([
                {
                    "type": "system", "subtype": "init",
                    "session_id": "s", "claude_code_version": "1.0",
                    "plugins": [{"name": "xpatcher", "path": str(session.plugin_dir)}],
                    "agents": list(ClaudeSession.REQUIRED_AGENTS) + ["xpatcher:test"],
                },
                {"type": "result", "result": "ok", "is_error": False,
                 "total_cost_usd": 0.001},
            ]),
            stderr="",
        )
        result = session.preflight()
        cmd = mock_run.call_args[0][0]
        assert "--agents" in cmd
        assert result.ok is True

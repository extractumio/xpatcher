"""Tests for agent .md structure and plugin agent discovery."""

from pathlib import Path

import pytest
import yaml

from src.dispatcher.session import (
    list_plugin_agents,
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


# ── list_plugin_agents() ─────────────────────────────────────────────────────

class TestListPluginAgents:
    def test_discovers_all_agents(self):
        names = list_plugin_agents(AGENTS_DIR)
        assert set(names) == EXPECTED_AGENTS

    def test_skips_non_yaml_frontmatter(self, tmp_path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        (agents_dir / "broken.md").write_text("no frontmatter here")
        (agents_dir / "good.md").write_text(
            "---\nname: good\ndescription: ok\n---\nPrompt body."
        )
        names = list_plugin_agents(agents_dir)
        assert names == ["good"]

    def test_empty_dir(self, tmp_path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        assert list_plugin_agents(agents_dir) == []

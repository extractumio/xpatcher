"""Behavioral tests for config-driven agent command wiring."""

from pathlib import Path

import yaml

from src.dispatcher.core import Dispatcher
from src.dispatcher.session import AgentInvocation, AgentResult, ClaudeSession, SessionRegistry
from src.dispatcher.state import PipelineStateFile


CONFIG = yaml.safe_load((Path(__file__).resolve().parents[1] / "config.yaml").read_text())
EXPECTED_AGENT_KEYS = {
    "planner",
    "plan_reviewer",
    "executor",
    "reviewer",
    "tester",
    "simplifier",
    "gap_detector",
    "tech_writer",
    "explorer",
}


def _flag_value(cmd: list[str], flag: str) -> str:
    idx = cmd.index(flag)
    return cmd[idx + 1]


def _make_dispatcher(tmp_path) -> Dispatcher:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    dispatcher = Dispatcher(project_dir, home)
    feature_dir = home / ".xpatcher" / "projects" / "test" / "feature"
    (feature_dir / "tasks" / "todo").mkdir(parents=True)
    (feature_dir / "tasks" / "in-progress").mkdir(parents=True)
    (feature_dir / "tasks" / "done").mkdir(parents=True)
    (feature_dir / "logs").mkdir(parents=True)
    dispatcher.feature_dir = feature_dir
    dispatcher.state_file = PipelineStateFile(str(feature_dir / "pipeline-state.yaml"))
    dispatcher.state_file.write({"task_states": {}, "total_cost_usd": 0.0, "iterations": {}, "transitions": []})
    dispatcher.registry = SessionRegistry(feature_dir / "sessions.yaml")
    return dispatcher


class TestAgentConfig:
    def test_config_defines_expected_agents_with_command_templates(self):
        agents = CONFIG.get("agents", {})
        assert set(agents) == EXPECTED_AGENT_KEYS
        assert all(isinstance(agents[key]["command"], list) for key in EXPECTED_AGENT_KEYS)
        assert all("{prompt}" in agents[key]["command"] for key in EXPECTED_AGENT_KEYS)
        assert all("{plugin_dir}" in agents[key]["command"] for key in EXPECTED_AGENT_KEYS)

    def test_templates_build_fully_substituted_commands_for_every_agent(self):
        session = ClaudeSession(Path("/opt/xpatcher/.claude-plugin"), Path("/workspace/project"))

        for agent_key, agent_cfg in CONFIG["agents"].items():
            invocation = AgentInvocation(
                prompt="ship it",
                session_id="sess-123",
                command_template=agent_cfg["command"],
                resume_args_template=agent_cfg.get("resume_args"),
            )

            cmd = session._build_cmd_from_template(invocation)

            assert cmd[0] == "claude"
            assert _flag_value(cmd, "-p") == "ship it"
            assert _flag_value(cmd, "--plugin-dir") == str(session.plugin_dir)
            assert "{prompt}" not in " ".join(cmd)
            assert "{plugin_dir}" not in " ".join(cmd)
            if agent_cfg.get("resume_args"):
                assert _flag_value(cmd, "--resume") == "sess-123"


class TestDispatcherInvokeUsesConfig:
    def test_invoke_agent_uses_configured_template_for_each_agent(self, tmp_path):
        dispatcher = _make_dispatcher(tmp_path)
        captured = []

        def fake_invoke(invocation: AgentInvocation) -> AgentResult:
            cmd = dispatcher.session._build_cmd_from_template(invocation)
            captured.append(cmd)
            return AgentResult(session_id="sess-ok", exit_code=0, events=[{"type": "result"}])

        dispatcher.session.invoke = fake_invoke

        for code_name, agent_cfg in (
            ("planner", CONFIG["agents"]["planner"]),
            ("plan-reviewer", CONFIG["agents"]["plan_reviewer"]),
            ("executor", CONFIG["agents"]["executor"]),
            ("reviewer", CONFIG["agents"]["reviewer"]),
            ("tester", CONFIG["agents"]["tester"]),
            ("simplifier", CONFIG["agents"]["simplifier"]),
            ("gap-detector", CONFIG["agents"]["gap_detector"]),
            ("tech-writer", CONFIG["agents"]["tech_writer"]),
            ("explorer", CONFIG["agents"]["explorer"]),
        ):
            result = dispatcher._invoke_agent(
                agent=code_name,
                prompt=f"run {code_name}",
                config=CONFIG,
                stage="testing",
                resume_session_id="sess-prev",
            )

            cmd = captured.pop(0)
            assert result.exit_code == 0
            assert _flag_value(cmd, "-p") == f"run {code_name}"
            assert _flag_value(cmd, "--agent") == _flag_value(agent_cfg["command"], "--agent")
            assert _flag_value(cmd, "--model") == _flag_value(agent_cfg["command"], "--model")
            assert _flag_value(cmd, "--resume") == "sess-prev"

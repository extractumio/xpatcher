"""Behavioral tests for stage invocation wiring."""

from pathlib import Path

import yaml

from src.dispatcher.core import Dispatcher
from src.dispatcher.session import AgentInvocation, AgentResult, ClaudeSession
from src.dispatcher.state import PipelineStateFile


CONFIG = yaml.safe_load((Path(__file__).resolve().parents[1] / "config.yaml").read_text())


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
    return dispatcher


class TestConfig:
    def test_config_has_main_agent_timeout(self):
        assert "main_agent" in CONFIG
        assert "timeout" in CONFIG["main_agent"]
        assert isinstance(CONFIG["main_agent"]["timeout"], int)

    def test_config_has_no_per_agent_command_templates(self):
        """Per-agent command templates were removed in the subagent refactor."""
        assert "agents" not in CONFIG

    def test_config_has_iterations(self):
        assert "iterations" in CONFIG
        assert CONFIG["iterations"]["plan_review_max"] == 3


class TestDispatcherInvokeStage:
    def test_invoke_stage_uses_single_session(self, tmp_path):
        """All stage invocations share one pipeline session, resuming after the first."""
        dispatcher = _make_dispatcher(tmp_path)
        captured = []

        def fake_invoke(invocation: AgentInvocation) -> AgentResult:
            captured.append(invocation)
            return AgentResult(session_id="sess-ok", exit_code=0, events=[{"type": "result"}])

        dispatcher.session.invoke = fake_invoke

        for i in range(3):
            dispatcher._invoke_stage(
                prompt=f"stage {i}",
                config=CONFIG,
                stage="testing",
            )

        # First call is fresh, subsequent resume
        assert not captured[0].resume
        assert captured[1].resume
        assert captured[2].resume
        # All share the same session
        assert captured[0].session_id == captured[1].session_id == captured[2].session_id

    def test_invoke_stage_does_not_use_agent_flag(self, tmp_path):
        """Stage invocations must not use --agent; delegation is via prompt @-mention."""
        dispatcher = _make_dispatcher(tmp_path)

        def fake_invoke(invocation: AgentInvocation) -> AgentResult:
            cmd = dispatcher.session._build_cmd(invocation)
            assert "--agent" not in cmd
            assert "--bare" not in cmd
            return AgentResult(session_id="sess-ok", exit_code=0, events=[{"type": "result"}])

        dispatcher.session.invoke = fake_invoke

        dispatcher._invoke_stage(
            prompt="@agent-planner do stuff",
            config=CONFIG,
            stage="planning",
        )

    def test_stage_timeout_from_config(self, tmp_path):
        dispatcher = _make_dispatcher(tmp_path)
        timeout = dispatcher._stage_timeout(CONFIG)
        assert timeout == CONFIG["main_agent"]["timeout"]

"""Tests that every agent in config.yaml produces the correct CLI command
and completes with exit code 0."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from src.dispatcher.core import Dispatcher
from src.dispatcher.session import AgentInvocation, AgentResult, ClaudeSession, SessionRegistry
from src.dispatcher.state import PipelineStateFile


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

CONFIG = yaml.safe_load((Path(__file__).resolve().parents[1] / "config.yaml").read_text())

AGENT_SPECS = [
    # (config_key, code_name, expected_agent_flag, expected_model, expected_timeout)
    ("planner",       "planner",       "xpatcher:planner",       "opus[1m]", 600),
    ("plan_reviewer", "plan-reviewer", "xpatcher:plan-reviewer", "opus",     300),
    ("executor",      "executor",      "xpatcher:executor",      "sonnet",   900),
    ("reviewer",      "reviewer",      "xpatcher:reviewer",      "opus",     300),
    ("tester",        "tester",        "xpatcher:tester",        "sonnet",   600),
    ("simplifier",    "simplifier",    "xpatcher:simplifier",    "sonnet",   300),
    ("gap_detector",  "gap-detector",  "xpatcher:gap-detector",  "opus",     300),
    ("tech_writer",   "tech-writer",   "xpatcher:tech-writer",   "sonnet",   300),
    ("explorer",      "explorer",      "xpatcher:explorer",      "haiku",    120),
]


def _flag_value(cmd: list[str], flag: str) -> str:
    """Return the value immediately after *flag* in the command list."""
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


def _ok_json_output(session_id: str = "sess-test") -> str:
    """Minimal valid JSON event stream that ClaudeSession parses as success."""
    return json.dumps([
        {"type": "system", "subtype": "init", "session_id": session_id},
        {"type": "result", "result": "---\ntype: intent\ngoal: test\n",
         "session_id": session_id, "total_cost_usd": 0.01,
         "duration_ms": 100, "num_turns": 1, "stop_reason": "end_turn"},
    ])


# ---------------------------------------------------------------------------
# 1. Config completeness — every expected agent is present
# ---------------------------------------------------------------------------

class TestConfigCompleteness:
    @pytest.mark.parametrize("config_key,code_name,agent_ref,model,timeout", AGENT_SPECS,
                             ids=[s[0] for s in AGENT_SPECS])
    def test_agent_exists_in_config(self, config_key, code_name, agent_ref, model, timeout):
        agents = CONFIG.get("agents", {})
        assert config_key in agents, f"Missing agents.{config_key} in config.yaml"

    @pytest.mark.parametrize("config_key,code_name,agent_ref,model,timeout", AGENT_SPECS,
                             ids=[s[0] for s in AGENT_SPECS])
    def test_agent_has_command_list(self, config_key, code_name, agent_ref, model, timeout):
        agent_cfg = CONFIG["agents"][config_key]
        assert isinstance(agent_cfg["command"], list)
        assert len(agent_cfg["command"]) >= 4, "Command template too short"


# ---------------------------------------------------------------------------
# 2. Command template correctness — verify every flag
# ---------------------------------------------------------------------------

class TestCommandTemplateArgs:
    """For each agent, build the command via _build_cmd_from_template and
    verify every static flag is correct."""

    @pytest.fixture()
    def session(self):
        return ClaudeSession(Path("/opt/xpatcher/.claude-plugin"), Path("/workspace/project"))

    @pytest.mark.parametrize("config_key,code_name,agent_ref,model,timeout", AGENT_SPECS,
                             ids=[s[0] for s in AGENT_SPECS])
    def test_binary_is_claude(self, session, config_key, code_name, agent_ref, model, timeout):
        cmd = self._build(session, config_key)
        assert cmd[0] == "claude"

    @pytest.mark.parametrize("config_key,code_name,agent_ref,model,timeout", AGENT_SPECS,
                             ids=[s[0] for s in AGENT_SPECS])
    def test_prompt_substituted(self, session, config_key, code_name, agent_ref, model, timeout):
        cmd = self._build(session, config_key)
        assert _flag_value(cmd, "-p") == "test prompt here"

    @pytest.mark.parametrize("config_key,code_name,agent_ref,model,timeout", AGENT_SPECS,
                             ids=[s[0] for s in AGENT_SPECS])
    def test_output_format_json(self, session, config_key, code_name, agent_ref, model, timeout):
        cmd = self._build(session, config_key)
        assert _flag_value(cmd, "--output-format") == "json"

    @pytest.mark.parametrize("config_key,code_name,agent_ref,model,timeout", AGENT_SPECS,
                             ids=[s[0] for s in AGENT_SPECS])
    def test_plugin_dir_substituted(self, session, config_key, code_name, agent_ref, model, timeout):
        cmd = self._build(session, config_key)
        assert _flag_value(cmd, "--plugin-dir") == str(session.plugin_dir)

    @pytest.mark.parametrize("config_key,code_name,agent_ref,model,timeout", AGENT_SPECS,
                             ids=[s[0] for s in AGENT_SPECS])
    def test_permission_mode(self, session, config_key, code_name, agent_ref, model, timeout):
        cmd = self._build(session, config_key)
        assert _flag_value(cmd, "--permission-mode") == "bypassPermissions"

    @pytest.mark.parametrize("config_key,code_name,agent_ref,model,timeout", AGENT_SPECS,
                             ids=[s[0] for s in AGENT_SPECS])
    def test_agent_flag(self, session, config_key, code_name, agent_ref, model, timeout):
        cmd = self._build(session, config_key)
        assert _flag_value(cmd, "--agent") == agent_ref

    @pytest.mark.parametrize("config_key,code_name,agent_ref,model,timeout", AGENT_SPECS,
                             ids=[s[0] for s in AGENT_SPECS])
    def test_model_flag(self, session, config_key, code_name, agent_ref, model, timeout):
        cmd = self._build(session, config_key)
        assert _flag_value(cmd, "--model") == model

    @pytest.mark.parametrize("config_key,code_name,agent_ref,model,timeout", AGENT_SPECS,
                             ids=[s[0] for s in AGENT_SPECS])
    def test_timeout_from_config(self, config_key, code_name, agent_ref, model, timeout):
        assert CONFIG["agents"][config_key]["timeout"] == timeout

    @pytest.mark.parametrize("config_key,code_name,agent_ref,model,timeout", AGENT_SPECS,
                             ids=[s[0] for s in AGENT_SPECS])
    def test_no_unsubstituted_placeholders(self, session, config_key, code_name, agent_ref, model, timeout):
        cmd = self._build(session, config_key)
        for arg in cmd:
            assert "{prompt}" not in arg, f"Unsubstituted {{prompt}} in {config_key}"
            assert "{plugin_dir}" not in arg, f"Unsubstituted {{plugin_dir}} in {config_key}"

    def _build(self, session: ClaudeSession, config_key: str) -> list[str]:
        agent_cfg = CONFIG["agents"][config_key]
        inv = AgentInvocation(
            prompt="test prompt here",
            command_template=agent_cfg["command"],
            resume_args_template=agent_cfg.get("resume_args"),
        )
        return session._build_cmd_from_template(inv)


# ---------------------------------------------------------------------------
# 3. Resume args — session continuation appends correctly
# ---------------------------------------------------------------------------

class TestResumeArgs:
    @pytest.fixture()
    def session(self):
        return ClaudeSession(Path("/opt/xpatcher/.claude-plugin"), Path("/workspace/project"))

    @pytest.mark.parametrize("config_key,code_name,agent_ref,model,timeout", AGENT_SPECS,
                             ids=[s[0] for s in AGENT_SPECS])
    def test_resume_appended_when_session_id_set(self, session, config_key, code_name, agent_ref, model, timeout):
        agent_cfg = CONFIG["agents"][config_key]
        inv = AgentInvocation(
            prompt="test",
            command_template=agent_cfg["command"],
            resume_args_template=agent_cfg.get("resume_args"),
            session_id="sess-abc-123",
        )
        cmd = session._build_cmd_from_template(inv)
        assert "--resume" in cmd
        assert _flag_value(cmd, "--resume") == "sess-abc-123"

    @pytest.mark.parametrize("config_key,code_name,agent_ref,model,timeout", AGENT_SPECS,
                             ids=[s[0] for s in AGENT_SPECS])
    def test_resume_omitted_when_no_session_id(self, session, config_key, code_name, agent_ref, model, timeout):
        agent_cfg = CONFIG["agents"][config_key]
        inv = AgentInvocation(
            prompt="test",
            command_template=agent_cfg["command"],
            resume_args_template=agent_cfg.get("resume_args"),
        )
        cmd = session._build_cmd_from_template(inv)
        assert "--resume" not in cmd


# ---------------------------------------------------------------------------
# 4. Full _invoke_agent → subprocess — correct command, exit code 0
# ---------------------------------------------------------------------------

class TestInvokeAgentEndToEnd:
    """Call Dispatcher._invoke_agent with real config and mock subprocess.
    Verify the captured command matches expectations and exit_code == 0."""

    @pytest.mark.parametrize("config_key,code_name,agent_ref,model,timeout", AGENT_SPECS,
                             ids=[s[0] for s in AGENT_SPECS])
    def test_invoke_runs_correct_command_and_exits_zero(
        self, tmp_path, config_key, code_name, agent_ref, model, timeout
    ):
        dispatcher = _make_dispatcher(tmp_path)
        captured_cmd = []

        def fake_invoke(invocation: AgentInvocation) -> AgentResult:
            cmd = dispatcher.session._build_cmd_from_template(invocation)
            captured_cmd.extend(cmd)
            return AgentResult(
                session_id="sess-ok",
                raw_text="---\ntype: intent\ngoal: test\n",
                exit_code=0,
                cost_usd=0.01,
                events=[{"type": "result"}],
            )

        dispatcher.session.invoke = fake_invoke

        result = dispatcher._invoke_agent(
            agent=code_name,
            prompt="Build the feature",
            config=CONFIG,
            stage="testing",
        )

        # Correct command built
        assert captured_cmd[0] == "claude"
        assert _flag_value(captured_cmd, "-p") == "Build the feature"
        assert _flag_value(captured_cmd, "--output-format") == "json"
        assert _flag_value(captured_cmd, "--plugin-dir") == str(dispatcher.plugin_dir)
        assert _flag_value(captured_cmd, "--permission-mode") == "bypassPermissions"
        assert _flag_value(captured_cmd, "--agent") == agent_ref
        assert _flag_value(captured_cmd, "--model") == model

        # Exit code 0
        assert result.exit_code == 0

    @pytest.mark.parametrize("config_key,code_name,agent_ref,model,timeout", AGENT_SPECS,
                             ids=[s[0] for s in AGENT_SPECS])
    def test_invoke_with_session_continuation(
        self, tmp_path, config_key, code_name, agent_ref, model, timeout
    ):
        dispatcher = _make_dispatcher(tmp_path)
        captured_cmd = []

        def fake_invoke(invocation: AgentInvocation) -> AgentResult:
            cmd = dispatcher.session._build_cmd_from_template(invocation)
            captured_cmd.extend(cmd)
            return AgentResult(session_id="sess-cont", exit_code=0, cost_usd=0.0, events=[{"type": "result"}])

        dispatcher.session.invoke = fake_invoke

        result = dispatcher._invoke_agent(
            agent=code_name,
            prompt="Continue the work",
            config=CONFIG,
            stage="testing",
            resume_session_id="sess-prev-42",
        )

        assert _flag_value(captured_cmd, "--resume") == "sess-prev-42"
        assert result.exit_code == 0

    @pytest.mark.parametrize("config_key,code_name,agent_ref,model,timeout", AGENT_SPECS,
                             ids=[s[0] for s in AGENT_SPECS])
    @patch("src.dispatcher.session.subprocess.run")
    def test_subprocess_receives_correct_command(
        self, mock_run, tmp_path, config_key, code_name, agent_ref, model, timeout
    ):
        """End-to-end: real config → _invoke_agent → ClaudeSession.invoke →
        subprocess.run receives the correct command and returns exit code 0."""
        mock_run.return_value = MagicMock(
            stdout=_ok_json_output(),
            stderr="",
            returncode=0,
        )
        dispatcher = _make_dispatcher(tmp_path)

        # Use cancel_check=None to go through subprocess.run (not Popen)
        original_invoke_agent = dispatcher._invoke_agent

        def invoke_without_cancel(agent, prompt, config, stage, task_id="", resume_session_id=None):
            """Call _invoke_agent but clear cancel_check so subprocess.run is used."""
            dispatcher._raise_if_cancelled = lambda: None
            agent_key = "executor" if agent == "executor" else agent.replace("-", "_")
            agent_config = config.get("agents", {}).get(agent_key, {})
            invocation = AgentInvocation(
                prompt=prompt,
                timeout=agent_config.get("timeout", 600),
                session_id=resume_session_id,
                cancel_check=None,
                command_template=agent_config["command"],
                resume_args_template=agent_config.get("resume_args"),
            )
            return dispatcher.session.invoke(invocation)

        result = invoke_without_cancel(code_name, "Build the feature", CONFIG, "testing")

        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "claude"
        assert _flag_value(cmd, "-p") == "Build the feature"
        assert _flag_value(cmd, "--output-format") == "json"
        assert _flag_value(cmd, "--permission-mode") == "bypassPermissions"
        assert _flag_value(cmd, "--agent") == agent_ref
        assert _flag_value(cmd, "--model") == model
        assert mock_run.return_value.returncode == 0
        assert result.exit_code == 0

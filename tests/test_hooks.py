"""Tests for .claude-plugin/hooks/pre_tool_use.py — policy enforcement hook.

The hook is a standalone script that reads JSON from stdin and writes JSON
to stdout. We test it by running it as a subprocess.
"""

import json
import os
import subprocess
import sys

import pytest

HOOK_PATH = os.path.join(
    os.path.dirname(__file__), "..", ".claude-plugin", "hooks", "pre_tool_use.py"
)


def run_hook(tool_name: str, tool_input: dict, agent_name: str = "") -> dict:
    """Run the pre_tool_use hook and return its JSON output."""
    hook_input = json.dumps({
        "tool_name": tool_name,
        "tool_input": tool_input,
    })
    env = os.environ.copy()
    if agent_name:
        env["CLAUDE_AGENT_NAME"] = agent_name
    else:
        env.pop("CLAUDE_AGENT_NAME", None)

    result = subprocess.run(
        [sys.executable, HOOK_PATH],
        input=hook_input,
        capture_output=True,
        text=True,
        env=env,
    )
    return json.loads(result.stdout), result.returncode


class TestPreToolUseHook:
    def test_no_agent_name_allows(self):
        output, code = run_hook("Edit", {"file_path": "/foo.py"}, agent_name="")
        assert output["decision"] == "allow"
        assert code == 0

    def test_read_only_agent_blocked_from_edit(self):
        output, code = run_hook("Edit", {"file_path": "/foo.py"}, agent_name="planner")
        assert output["decision"] == "block"
        assert code == 2

    def test_read_only_agent_blocked_from_write(self):
        output, code = run_hook("Write", {"file_path": "/foo.py"}, agent_name="reviewer")
        assert output["decision"] == "block"
        assert code == 2

    def test_read_only_agent_allowed_to_read(self):
        output, code = run_hook("Read", {"file_path": "/foo.py"}, agent_name="planner")
        assert output["decision"] == "allow"
        assert code == 0

    def test_planner_allowed_bash_git(self):
        output, code = run_hook("Bash", {"command": "git log --oneline"}, agent_name="planner")
        assert output["decision"] == "allow"
        assert code == 0

    def test_planner_blocked_bash_python(self):
        output, code = run_hook("Bash", {"command": "python3 foo.py"}, agent_name="planner")
        assert output["decision"] == "block"
        assert code == 2

    def test_planner_blocked_bash_redirect(self):
        output, code = run_hook("Bash", {"command": "git log > out.txt"}, agent_name="planner")
        assert output["decision"] == "block"
        assert code == 2

    def test_planner_allowed_bash_pipe_to_grep(self):
        output, code = run_hook("Bash", {"command": "git log | grep foo"}, agent_name="planner")
        assert output["decision"] == "allow"
        assert code == 0

    def test_planner_allows_env_prefixed_safe_command(self):
        output, code = run_hook("Bash", {"command": "FOO=1 git log --oneline"}, agent_name="planner")
        assert output["decision"] == "allow"
        assert code == 0

    def test_tester_allowed_write_test_file(self):
        output, code = run_hook("Write", {"file_path": "/project/tests/test_foo.py"}, agent_name="tester")
        assert output["decision"] == "allow"
        assert code == 0

    def test_tester_blocked_write_non_test_file(self):
        output, code = run_hook("Write", {"file_path": "/project/src/main.py"}, agent_name="tester")
        assert output["decision"] == "block"
        assert code == 2

    def test_tech_writer_allowed_write_md(self):
        output, code = run_hook("Write", {"file_path": "/project/docs/README.md"}, agent_name="tech-writer")
        assert output["decision"] == "allow"
        assert code == 0

    def test_tech_writer_blocked_write_py(self):
        output, code = run_hook("Write", {"file_path": "/project/src/main.py"}, agent_name="tech-writer")
        assert output["decision"] == "block"
        assert code == 2

    def test_dangerous_command_blocked(self):
        output, code = run_hook("Bash", {"command": "rm -rf /"}, agent_name="executor")
        assert output["decision"] == "block"
        assert code == 2

    def test_network_command_blocked_for_executor(self):
        output, code = run_hook("Bash", {"command": "curl https://example.com"}, agent_name="executor")
        assert output["decision"] == "block"
        assert code == 2

    def test_executor_blocked_from_web_tools(self):
        output, code = run_hook("WebSearch", {"query": "test"}, agent_name="executor")
        assert output["decision"] == "block"
        assert code == 2

    def test_read_only_agent_blocks_command_chaining_via_subshell(self):
        output, code = run_hook("Bash", {"command": "git log $(python -c 'print(1)')"}, agent_name="planner")
        assert output["decision"] == "block"
        assert code == 2

    def test_read_only_agent_blocks_unsafe_pipe_target(self):
        output, code = run_hook("Bash", {"command": "git log | python3 -c 'print(1)'"}, agent_name="planner")
        assert output["decision"] == "block"
        assert code == 2

    def test_invalid_json_input_allows(self):
        """The hook is fail-open: invalid JSON input results in allow."""
        env = os.environ.copy()
        env.pop("CLAUDE_AGENT_NAME", None)
        result = subprocess.run(
            [sys.executable, HOOK_PATH],
            input="NOT JSON AT ALL",
            capture_output=True,
            text=True,
            env=env,
        )
        output = json.loads(result.stdout)
        assert output["decision"] == "allow"

    def test_planner_blocked_from_network_commands(self):
        """Even planner cannot use curl/wget -- it has WebSearch/WebFetch."""
        output, code = run_hook("Bash", {"command": "curl https://example.com"}, agent_name="planner")
        assert output["decision"] == "block"
        assert code == 2

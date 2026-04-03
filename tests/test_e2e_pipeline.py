"""Integration-style pipeline tests with mocked Claude CLI responses."""

from pathlib import Path
import re

import yaml

from src.dispatcher.core import Dispatcher
from src.dispatcher.session import AgentResult, PreflightResult


def _make_repo(tmp_path: Path) -> Path:
    project_dir = tmp_path / "repo"
    project_dir.mkdir()
    (project_dir / ".git").mkdir()
    (project_dir / "app.py").write_text("def greet(name):\n    return f\"Hello, {name}\"\n")
    return project_dir


def _make_home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    (home / ".claude-plugin").mkdir()
    (home / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "models": {},
                "timeouts": {},
                "iterations": {
                    "plan_review_max": 3,
                    "task_review_max": 3,
                    "quality_loop_max": 3,
                    "gap_reentry_max": 2,
                },
                "session_management": {
                    "abandon_threshold_pct": 90,
                },
                "human_gates": {
                    "spec_confirmation": False,
                    "completion_confirmation": False,
                },
            }
        )
    )
    return home


def _feature_dir(dispatcher: Dispatcher, feature_slug: str) -> Path:
    return dispatcher._feature_dir_for(feature_slug)


def _result(text: str, session_id: str) -> AgentResult:
    return AgentResult(session_id=session_id, raw_text=text, cost_usd=0.1, events=[{"type": "result"}], usage={"input_tokens": 1, "output_tokens": 1})


def _invoke_and_write_artifact(responses):
    def _invoke(invocation):
        result = next(responses)
        match = re.search(r"Write .* to: (.+)", invocation.prompt)
        if match:
            path = Path(match.group(1).strip())
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(result.raw_text)
        return result

    return _invoke


def test_full_pipeline_completes_and_moves_task_artifacts(tmp_path, monkeypatch):
    project_dir = _make_repo(tmp_path)
    home = _make_home(tmp_path)
    dispatcher = Dispatcher(project_dir, home)

    monkeypatch.setattr(dispatcher.session, "preflight", lambda: PreflightResult(ok=True, cli_version="2.1.87", plugin_loaded=True))
    monkeypatch.setattr(dispatcher.tui, "prompt_approval", lambda prompt: True)

    responses = iter(
        [
            _result(
                "---\ntype: intent\ngoal: Add farewell helper to the app\nscope:\n  - add farewell helper\nconstraints: []\nclarifying_questions: []\n",
                "s1",
            ),
            _result(
                "---\ntype: plan\nsummary: Add farewell helper and tests with a simple plan\nphases:\n  - id: phase-1\n    name: Phase One\n    description: Implement the change\n    tasks:\n      - id: task-001\n        description: Update app and tests to add farewell behavior\n        files: [app.py, tests/test_app.py]\n        acceptance: ['pytest exits with code 0']\n        depends_on: []\n        estimated_complexity: low\n",
                "s1",
            ),
            _result(
                "---\ntype: plan_review\nplan_version: 1\nverdict: approved\nconfidence: high\nsummary: The plan is ready\n",
                "s2",
            ),
            _result(
                "---\ntype: task_manifest\nplan_version: 1\nsummary: Single executable task\ntasks:\n  - id: task-001\n    title: Add farewell helper\n    description: Implement farewell helper and verify it\n    files_in_scope: [app.py, tests/test_app.py]\n    acceptance_criteria:\n      - id: ac-01\n        description: The command succeeds\n        verification: command\n        command: python -c \"print('ok')\"\n        severity: must_pass\n    depends_on: []\n    estimated_complexity: low\n    quality_tier: lite\n",
                "s1",
            ),
            _result(
                "---\ntype: task_manifest_review\nmanifest_version: 1\nverdict: approved\nconfidence: high\nsummary: The task manifest is ready\n",
                "s2",
            ),
            _result(
                "---\ntype: execution_result\ntask_id: task-001\nstatus: completed\nsummary: Implemented the task successfully\n",
                "s3",
            ),
            _result(
                "---\ntype: review\ntask_id: task-001\nverdict: approve\nconfidence: high\nsummary: The code review passed\nfindings: []\n",
                "s4",
            ),
            _result(
                "---\ntype: gap_report\nverdict: complete\ngaps: []\n",
                "s5",
            ),
            _result(
                "---\ntype: docs_report\nsummary: Updated docs report artifact\n",
                "s6",
            ),
        ]
    )
    monkeypatch.setattr(dispatcher.session, "invoke", _invoke_and_write_artifact(responses))

    dispatcher.start("Add a farewell helper with tests")

    feature_dir = _feature_dir(dispatcher, "add-a-farewell-helper-with-tests")
    state = yaml.safe_load((feature_dir / "pipeline-state.yaml").read_text())
    assert state["current_stage"] == "done"
    assert state["task_states"]["task-001"] == "succeeded"
    assert (feature_dir / "tasks" / "done" / "task-001-add-farewell-helper.yaml").exists()
    assert (feature_dir / "tasks" / "done" / "task-001-execution-log.yaml").exists()
    assert (feature_dir / "docs-report.yaml").exists()
    assert not (project_dir / ".xpatcher").exists()


def test_gap_reentry_creates_new_manifest_and_gap_task(tmp_path, monkeypatch):
    project_dir = _make_repo(tmp_path)
    home = _make_home(tmp_path)
    dispatcher = Dispatcher(project_dir, home)

    monkeypatch.setattr(dispatcher.session, "preflight", lambda: PreflightResult(ok=True, cli_version="2.1.87", plugin_loaded=True))
    monkeypatch.setattr(dispatcher.tui, "prompt_approval", lambda prompt: True)

    responses = iter(
        [
            _result("---\ntype: intent\ngoal: Add feature\nscope:\n  - add feature\nconstraints: []\nclarifying_questions: []\n", "a1"),
            _result("---\ntype: plan\nsummary: Plan summary with sufficient detail\nphases:\n  - id: phase-1\n    name: Phase One\n    description: Implement\n    tasks:\n      - id: task-001\n        description: Do initial task completely\n        files: [app.py]\n        acceptance: ['python command succeeds']\n        depends_on: []\n        estimated_complexity: low\n", "a1"),
            _result("---\ntype: plan_review\nplan_version: 1\nverdict: approved\nconfidence: high\nsummary: The plan review approved the plan\n", "a2"),
            _result("---\ntype: task_manifest\nplan_version: 1\nsummary: Initial manifest\ntasks:\n  - id: task-001\n    title: Initial task\n    description: Run initial task\n    files_in_scope: [app.py]\n    acceptance_criteria:\n      - id: ac-01\n        description: Initial task command succeeds\n        verification: command\n        command: python -c \"print('ok')\"\n        severity: must_pass\n    depends_on: []\n    estimated_complexity: low\n    quality_tier: lite\n", "a1"),
            _result("---\ntype: task_manifest_review\nmanifest_version: 1\nverdict: approved\nconfidence: high\nsummary: The manifest review approved the tasks\n", "a2"),
            _result("---\ntype: execution_result\ntask_id: task-001\nstatus: completed\nsummary: Completed the initial task successfully\n", "a3"),
            _result("---\ntype: review\ntask_id: task-001\nverdict: approve\nconfidence: high\nsummary: The initial task review passed cleanly\nfindings: []\n", "a4"),
            _result("---\ntype: gap_report\nverdict: gaps_found\ngaps:\n  - id: G-1\n    severity: major\n    category: integration\n    description: A follow-up gap remains\n", "a5"),
            _result("---\ntype: task_manifest\nplan_version: 1\nsummary: Gap manifest\ntasks:\n  - id: task-001\n    title: Initial task\n    description: Run initial task\n    files_in_scope: [app.py]\n    acceptance_criteria:\n      - id: ac-01\n        description: Initial task command succeeds\n        verification: command\n        command: python -c \"print('ok')\"\n        severity: must_pass\n    depends_on: []\n    estimated_complexity: low\n    quality_tier: lite\n  - id: task-G001\n    title: Gap task\n    description: Close the reported gap\n    files_in_scope: [app.py]\n    acceptance_criteria:\n      - id: ac-02\n        description: Gap task command succeeds\n        verification: command\n        command: python -c \"print('ok')\"\n        severity: must_pass\n    depends_on: []\n    estimated_complexity: low\n    quality_tier: lite\n", "a1"),
            _result("---\ntype: task_manifest_review\nmanifest_version: 2\nverdict: approved\nconfidence: high\nsummary: The gap task review approved the tasks\n", "a2"),
            _result("---\ntype: execution_result\ntask_id: task-G001\nstatus: completed\nsummary: Completed the gap task successfully\n", "a6"),
            _result("---\ntype: review\ntask_id: task-G001\nverdict: approve\nconfidence: high\nsummary: The gap task review passed cleanly\nfindings: []\n", "a7"),
            _result("---\ntype: gap_report\nverdict: complete\ngaps: []\n", "a8"),
            _result("---\ntype: docs_report\nsummary: Updated docs report artifact\n", "a9"),
        ]
    )
    monkeypatch.setattr(dispatcher.session, "invoke", _invoke_and_write_artifact(responses))

    dispatcher.start("Add a farewell helper with tests")

    feature_dir = _feature_dir(dispatcher, "add-a-farewell-helper-with-tests")
    state = yaml.safe_load((feature_dir / "pipeline-state.yaml").read_text())
    assert state["current_stage"] == "done"
    assert (feature_dir / "task-manifest-v2.yaml").exists()
    assert (feature_dir / "gap-report-v1.yaml").exists()
    assert (feature_dir / "gap-report-v2.yaml").exists()
    assert (feature_dir / "tasks" / "done" / "task-G001-gap-task.yaml").exists()
    assert not (project_dir / ".xpatcher").exists()

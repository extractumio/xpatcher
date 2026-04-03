"""High-signal tests for dispatcher.core."""

from argparse import Namespace
from pathlib import Path

import yaml

from src.artifacts.store import ArtifactStore
from src.context.builder import PromptBuilder
from src.dispatcher.core import CancelledPipelineError, Dispatcher, _project_pipeline_index_path, _register_pipeline_index, _skip_tasks
from src.dispatcher.session import AgentResult
from src.dispatcher.state import PipelineStateFile, PipelineStateMachine, TaskState


def _make_dispatcher(tmp_path) -> Dispatcher:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    xpatcher_home = tmp_path / "home"
    xpatcher_home.mkdir()
    dispatcher = Dispatcher(project_dir, xpatcher_home)
    feature_dir = xpatcher_home / ".xpatcher" / "projects" / "project-test" / "feature"
    (feature_dir / "tasks" / "todo").mkdir(parents=True)
    (feature_dir / "tasks" / "in-progress").mkdir(parents=True)
    (feature_dir / "tasks" / "done").mkdir(parents=True)
    (feature_dir / "logs").mkdir(parents=True)
    dispatcher.feature_dir = feature_dir
    dispatcher.state_file = PipelineStateFile(str(feature_dir / "pipeline-state.yaml"))
    dispatcher.state_file.write({"task_states": {}, "total_cost_usd": 0.0, "iterations": {}, "transitions": []})
    return dispatcher


def _manifest() -> dict:
    return {
        "type": "task_manifest",
        "plan_version": 1,
        "summary": "Manifest with executable command checks",
        "tasks": [
            {
                "id": "task-001",
                "title": "Add hello command",
                "description": "Implement and verify a hello command path",
                "files_in_scope": ["src/hello.py"],
                "acceptance_criteria": [
                    {
                        "id": "ac-01",
                        "description": "The command exits successfully",
                        "verification": "command",
                        "command": "python -c \"print('ok')\"",
                        "severity": "must_pass",
                    }
                ],
                "depends_on": [],
                "estimated_complexity": "low",
                "quality_tier": "lite",
            }
        ],
    }


class TestDispatcherArtifacts:
    def test_save_task_manifest_materializes_per_task_files(self, tmp_path):
        dispatcher = _make_dispatcher(tmp_path)
        store = ArtifactStore(dispatcher.feature_dir)
        dispatcher._save_task_manifest(store, _manifest(), manifest_version=1)

        manifest_path = dispatcher.feature_dir / "task-manifest.yaml"
        task_path = dispatcher.feature_dir / "tasks" / "todo" / "task-001-add-hello-command.yaml"

        assert manifest_path.exists()
        assert task_path.exists()
        assert yaml.safe_load(task_path.read_text())["acceptance_criteria"][0]["command"].startswith("python -c")

    def test_acceptance_checks_fail_on_broken_command(self, tmp_path):
        dispatcher = _make_dispatcher(tmp_path)
        task = _manifest()["tasks"][0]
        task["acceptance_criteria"][0]["command"] = "python -c \"import sys; sys.exit(1)\""

        report = dispatcher._run_acceptance_checks(task)

        assert report["overall"] == "fail"
        assert report["regression_failures"]

    def test_save_task_manifest_refreshes_existing_todo_task_files(self, tmp_path):
        dispatcher = _make_dispatcher(tmp_path)
        store = ArtifactStore(dispatcher.feature_dir)
        manifest = _manifest()
        dispatcher._save_task_manifest(store, manifest, manifest_version=1)

        manifest["tasks"][0]["acceptance_criteria"][0]["command"] = "python -c \"print('updated')\""
        dispatcher._save_task_manifest(store, manifest, manifest_version=1)

        task_path = dispatcher.feature_dir / "tasks" / "todo" / "task-001-add-hello-command.yaml"
        task_data = yaml.safe_load(task_path.read_text())
        assert task_data["acceptance_criteria"][0]["command"] == "python -c \"print('updated')\""

    def test_quality_loop_falls_back_to_review_when_commands_are_missing(self, tmp_path, monkeypatch):
        dispatcher = _make_dispatcher(tmp_path)
        store = ArtifactStore(dispatcher.feature_dir)
        manifest = _manifest()
        manifest["tasks"][0]["acceptance_criteria"][0]["command"] = ""
        dispatcher._save_task_manifest(store, manifest, manifest_version=1)

        monkeypatch.setattr(
            dispatcher,
            "_invoke_validated_stage",
            lambda *args, **kwargs: (
                AgentResult(),
                type("V", (), {"valid": True, "data": {"type": "review", "task_id": "task-001", "verdict": "approve", "confidence": "high", "summary": "Code matches the task", "findings": []}})(),
            ),
        )

        passed = dispatcher._run_quality_loop(
            "task-001",
            {"iterations": {"quality_loop_max": 2}},
            PromptBuilder(dispatcher.feature_dir, dispatcher.project_dir),
            store,
            PipelineStateMachine(dispatcher.state_file),
        )

        assert passed is True


class TestDispatcherCancellation:
    def test_invoke_stage_raises_when_pipeline_is_cancelled(self, tmp_path):
        dispatcher = _make_dispatcher(tmp_path)
        dispatcher.state_file.write(
            {
                "current_stage": "cancelled",
                "status": "cancelled",
                "task_states": {},
                "iterations": {},
                "transitions": [],
                "total_cost_usd": 0.0,
            }
        )

        try:
            dispatcher._invoke_stage("plan", {"main_agent": {"timeout": 900}}, "planning")
        except CancelledPipelineError:
            pass
        else:
            raise AssertionError("expected cancellation to short-circuit dispatcher")


class TestDispatcherPersistence:
    def test_invoke_stage_persists_cost_and_logs(self, tmp_path):
        dispatcher = _make_dispatcher(tmp_path)
        dispatcher.session.invoke = lambda invocation: AgentResult(
            session_id="sess-1",
            raw_text="---\ntype: docs_report\nsummary: Updated the docs cleanly\n",
            cost_usd=1.25,
            events=[{"type": "system"}, {"type": "result"}],
            num_turns=2,
            usage={"input_tokens": 10, "output_tokens": 5},
        )

        dispatcher._invoke_stage(
            prompt="write docs",
            config={"main_agent": {"timeout": 900}},
            stage="documentation",
        )

        state = dispatcher.state_file.read()
        log_files = list((dispatcher.feature_dir / "logs").glob("*documentation*.jsonl"))

        assert state["total_cost_usd"] == 1.25
        assert log_files


class TestResumeHumanGate:
    def test_resume_plan_approval_continues_pipeline(self, tmp_path, monkeypatch):
        dispatcher = _make_dispatcher(tmp_path)
        feature_dir = dispatcher.feature_dir
        dispatcher.state_file.write(
            {
                "pipeline_id": "xp-20260330-resume",
                "current_stage": "paused",
                "previous_stage": "plan_approval",
                "status": "paused",
                "gate_reason": "plan_approval",
                "task_states": {},
                "iterations": {},
                "transitions": [],
                "total_cost_usd": 0.0,
            }
        )
        _register_pipeline_index(dispatcher.xpatcher_home, "xp-20260330-resume", dispatcher.project_dir, feature_dir)
        (feature_dir / "plan-v1.yaml").write_text(yaml.dump({"type": "plan"}))

        monkeypatch.setattr(Dispatcher, "_load_config", lambda self: {})
        monkeypatch.setattr(Dispatcher, "_handle_plan_approval", lambda *args, **kwargs: True)
        monkeypatch.setattr(Dispatcher, "_run_task_breakdown_and_review", lambda *args, **kwargs: 1)
        monkeypatch.setattr(Dispatcher, "_run_prioritization_and_execution", lambda *args, **kwargs: True)
        monkeypatch.setattr(Dispatcher, "_run_gap_detection_with_reentry", lambda *args, **kwargs: True)
        monkeypatch.setattr(
            Dispatcher,
            "_invoke_validated_stage",
            lambda *args, **kwargs: (AgentResult(), type("V", (), {"valid": True, "data": {"type": "docs_report", "summary": "Updated docs"}})()),
        )
        monkeypatch.setattr(Dispatcher, "_handle_completion_gate", lambda *args, **kwargs: None)

        dispatcher.resume("xp-20260330-resume")

        assert (feature_dir / "docs-report.yaml").exists()


class TestSpecificationAutomation:
    def test_plan_approval_auto_advances_without_clarifying_questions(self, tmp_path, monkeypatch):
        dispatcher = _make_dispatcher(tmp_path)
        store = ArtifactStore(dispatcher.feature_dir)
        dispatcher.state_file.write(
            {
                "pipeline_id": "xp-20260330-auto",
                "current_stage": "plan_approval",
                "status": "running",
                "task_states": {},
                "iterations": {},
                "transitions": [],
                "total_cost_usd": 0.0,
            }
        )
        (dispatcher.feature_dir / "intent.yaml").write_text(
            yaml.safe_dump(
                {
                    "type": "intent",
                    "goal": "Add login flow",
                    "scope": ["add login flow"],
                    "constraints": [],
                    "clarifying_questions": [],
                }
            )
        )

        monkeypatch.setattr(dispatcher.tui, "prompt_approval", lambda prompt: (_ for _ in ()).throw(AssertionError("unexpected human prompt")))

        approved = dispatcher._handle_plan_approval(
            PipelineStateMachine(dispatcher.state_file),
            store,
            PromptBuilder(dispatcher.feature_dir, dispatcher.project_dir),
            {"human_gates": {"spec_confirmation": False}},
            plan_version=1,
            transition_stage=False,
        )

        decision_files = list((dispatcher.feature_dir / "decisions").glob("decision-*-plan-approval.yaml"))
        assert approved is True
        assert dispatcher.state_file.read()["status"] == "running"
        assert decision_files
        assert yaml.safe_load(decision_files[0].read_text())["auto_approved"] is True

    def test_completion_auto_finishes_by_default(self, tmp_path, monkeypatch):
        dispatcher = _make_dispatcher(tmp_path)
        store = ArtifactStore(dispatcher.feature_dir)
        dispatcher.total_cost_usd = 2.5
        dispatcher.state_file.write(
            {
                "pipeline_id": "xp-20260330-complete",
                "current_stage": "documentation",
                "status": "running",
                "task_states": {},
                "iterations": {},
                "transitions": [],
                "total_cost_usd": 2.5,
            }
        )
        monkeypatch.setattr(dispatcher.tui, "prompt_approval", lambda prompt: (_ for _ in ()).throw(AssertionError("unexpected human prompt")))

        dispatcher._handle_completion_gate(PipelineStateMachine(dispatcher.state_file), store)

        state = dispatcher.state_file.read()
        completion = yaml.safe_load((dispatcher.feature_dir / "completion.yaml").read_text())

        assert state["current_stage"] == "done"
        assert state["status"] == "completed"
        assert completion["auto_completed"] is True

    def test_prioritization_returns_false_when_task_finishes_failed(self, tmp_path, monkeypatch):
        dispatcher = _make_dispatcher(tmp_path)
        store = ArtifactStore(dispatcher.feature_dir)
        dispatcher.state_file.write(
            {
                "current_stage": "task_review",
                "status": "running",
                "task_states": {},
                "iterations": {},
                "transitions": [],
                "total_cost_usd": 0.0,
            }
        )
        dispatcher._save_task_manifest(store, _manifest(), manifest_version=1)

        monkeypatch.setattr(dispatcher, "_execute_task", lambda *args, **kwargs: False)

        ok = dispatcher._run_prioritization_and_execution(
            PipelineStateMachine(dispatcher.state_file),
            store,
            PromptBuilder(dispatcher.feature_dir, dispatcher.project_dir),
            {},
        )

        assert ok is False


class TestCliSkip:
    def test_skip_records_cli_action_and_blocks_dependents_by_default(self, tmp_path):
        dispatcher = _make_dispatcher(tmp_path)
        feature_dir = dispatcher.feature_dir
        dispatcher.state_file.write(
            {
                "pipeline_id": "xp-20260330-abcd",
                "task_states": {"task-001": "stuck", "task-002": "pending"},
                "total_cost_usd": 0.0,
                "iterations": {},
                "transitions": [],
            }
        )
        (feature_dir / "task-manifest.yaml").write_text(
            yaml.dump(
                {
                    "tasks": [
                        {"id": "task-001", "depends_on": []},
                        {"id": "task-002", "depends_on": ["task-001"]},
                    ]
                }
            )
        )
        _register_pipeline_index(dispatcher.xpatcher_home, "xp-20260330-abcd", dispatcher.project_dir, feature_dir)

        rc = _skip_tasks(
            Namespace(pipeline_id="xp-20260330-abcd", task_ids="task-001", force_unblock=False),
            dispatcher.xpatcher_home,
        )

        state = dispatcher.state_file.read()
        assert rc == 0
        assert state["task_states"]["task-001"] == TaskState.SKIPPED.value
        assert state["task_states"]["task-002"] == TaskState.BLOCKED.value
        assert state["skipped_tasks"][0]["task_id"] == "task-001"


class TestAcceptanceChecks:
    def test_missing_commands_do_not_cause_failure(self, tmp_path):
        dispatcher = _make_dispatcher(tmp_path)
        task = _manifest()["tasks"][0]
        task["acceptance_criteria"][0]["command"] = ""

        report = dispatcher._run_acceptance_checks(task)

        assert report["overall"] == "pass"
        assert not report["regression_failures"]
        assert report["verification_summary"]["missing_commands"] == 1
        assert report["verification_summary"]["commands_executed"] == 0

    def test_missing_commands_mixed_with_real_failures(self, tmp_path):
        dispatcher = _make_dispatcher(tmp_path)
        task = _manifest()["tasks"][0]
        task["acceptance_criteria"] = [
            {"id": "ac-01", "description": "Has empty command", "verification": "command", "command": "", "severity": "must_pass"},
            {"id": "ac-02", "description": "Fails on execution", "verification": "command", "command": "python -c \"import sys; sys.exit(1)\"", "severity": "must_pass"},
        ]

        report = dispatcher._run_acceptance_checks(task)

        assert report["overall"] == "fail"
        assert len(report["regression_failures"]) == 1
        assert "command failed" in report["regression_failures"][0]
        assert report["verification_summary"]["missing_commands"] == 1


class TestGapReentryBlocked:
    def test_gap_reentry_transitions_to_blocked_when_execution_fails(self, tmp_path, monkeypatch):
        dispatcher = _make_dispatcher(tmp_path)
        store = ArtifactStore(dispatcher.feature_dir)
        manifest = _manifest()
        dispatcher._save_task_manifest(store, manifest, manifest_version=1)
        dispatcher.state_file.write({
            "current_stage": "task_execution",
            "status": "running",
            "task_states": {},
            "iterations": {},
            "transitions": [],
            "total_cost_usd": 0.0,
        })

        gap_report_data = {"type": "gap_report", "verdict": "gaps_found", "gaps": [{"id": "G-1", "severity": "major", "category": "integration", "description": "Gap"}]}
        task_manifest_data = {
            "type": "task_manifest", "plan_version": 1, "summary": "Gap manifest",
            "tasks": manifest["tasks"] + [{
                "id": "task-G001", "title": "Gap task", "description": "Close the gap",
                "files_in_scope": [], "acceptance_criteria": [
                    {"id": "ac-gap", "description": "Gap check passes", "verification": "command", "command": "true", "severity": "must_pass"},
                ],
                "depends_on": [], "estimated_complexity": "low", "quality_tier": "lite",
            }],
        }
        review_data = {"type": "task_manifest_review", "manifest_version": 2, "verdict": "approved", "confidence": "high", "summary": "OK"}

        responses = iter([
            (AgentResult(), type("V", (), {"valid": True, "data": gap_report_data})()),
            (AgentResult(), type("V", (), {"valid": True, "data": task_manifest_data})()),
            (AgentResult(), type("V", (), {"valid": True, "data": review_data})()),
        ])
        monkeypatch.setattr(dispatcher, "_invoke_validated_stage", lambda *args, **kwargs: next(responses))
        monkeypatch.setattr(dispatcher, "_run_prioritization_and_execution", lambda *args, **kwargs: False)

        sm = PipelineStateMachine(dispatcher.state_file)
        result = dispatcher._run_gap_detection_with_reentry(
            sm, store, PromptBuilder(dispatcher.feature_dir, dispatcher.project_dir),
            {"iterations": {"gap_reentry_max": 2}}, plan_version=1,
        )

        state = dispatcher.state_file.read()
        assert result is False
        assert state["current_stage"] == "blocked"
        assert state.get("gate_reason") == "gap_execution_failed"


class TestPipelineIndex:
    def test_register_pipeline_index_creates_per_project_index_file(self, tmp_path):
        project_dir = tmp_path / "project-a"
        project_dir.mkdir()
        xpatcher_home = tmp_path / "home"
        xpatcher_home.mkdir()
        feature_dir = xpatcher_home / ".xpatcher" / "projects" / "project-a-12345678" / "feature"
        feature_dir.mkdir(parents=True)

        _register_pipeline_index(xpatcher_home, "xp-20260330-abcd", project_dir, feature_dir)

        index_path = _project_pipeline_index_path(xpatcher_home, project_dir)
        data = yaml.safe_load(index_path.read_text())
        assert index_path.parent.name == "pipelines"
        assert data["project_dir"] == str(project_dir)
        assert data["pipelines"]["xp-20260330-abcd"]["feature_dir"] == str(feature_dir)
        assert not (xpatcher_home / "pipelines.yaml").exists()

    def test_register_pipeline_index_keeps_projects_isolated(self, tmp_path):
        xpatcher_home = tmp_path / "home"
        xpatcher_home.mkdir()
        project_a = tmp_path / "project-a"
        project_b = tmp_path / "project-b"
        project_a.mkdir()
        project_b.mkdir()
        feature_a = xpatcher_home / ".xpatcher" / "projects" / "project-a-12345678" / "feature-a"
        feature_b = xpatcher_home / ".xpatcher" / "projects" / "project-b-87654321" / "feature-b"
        feature_a.mkdir(parents=True)
        feature_b.mkdir(parents=True)

        _register_pipeline_index(xpatcher_home, "xp-20260330-aaaa", project_a, feature_a)
        _register_pipeline_index(xpatcher_home, "xp-20260330-bbbb", project_b, feature_b)

        index_a = yaml.safe_load(_project_pipeline_index_path(xpatcher_home, project_a).read_text())
        index_b = yaml.safe_load(_project_pipeline_index_path(xpatcher_home, project_b).read_text())

        assert set(index_a["pipelines"]) == {"xp-20260330-aaaa"}
        assert set(index_b["pipelines"]) == {"xp-20260330-bbbb"}
        assert not (xpatcher_home / "pipelines.yaml").exists()

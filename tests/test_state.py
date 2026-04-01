"""Tests for dispatcher.state — PipelineStage, TaskState, PipelineStateFile,
PipelineStateMachine, TaskDAG."""

import os

import pytest

from src.dispatcher.state import (
    TERMINAL_STAGES,
    VALID_TRANSITIONS,
    InvalidTransitionError,
    PipelineStage,
    PipelineStateFile,
    PipelineStateMachine,
    TaskDAG,
    TaskState,
)


# ===========================================================================
# PipelineStage enum
# ===========================================================================

class TestPipelineStage:
    def test_has_all_values(self):
        # The spec says 22 values -- but let's just verify against the enum.
        # Count actual members, not the alias count.
        # The enum is: UNINITIALIZED through ROLLED_BACK = 23 values
        expected = {
            "uninitialized", "intent_capture", "planning", "plan_review",
            "plan_fix", "plan_approval", "task_breakdown", "task_review",
            "task_fix", "prioritization", "execution_graph", "task_execution",
            "per_task_quality", "fix_iteration", "gap_detection", "documentation",
            "completion", "done", "paused", "blocked", "failed", "cancelled",
            "rolled_back",
        }
        assert set(s.value for s in PipelineStage) == expected


# ===========================================================================
# TaskState enum
# ===========================================================================

class TestTaskState:
    def test_has_all_values(self):
        expected = {
            "pending", "blocked", "ready", "running", "succeeded",
            "failed", "needs_fix", "stuck", "skipped", "cancelled",
        }
        assert set(s.value for s in TaskState) == expected
        assert len(TaskState) == 10


# ===========================================================================
# PipelineStateFile
# ===========================================================================

class TestPipelineStateFile:
    def test_write_and_read(self, tmp_path):
        path = str(tmp_path / "state.yaml")
        sf = PipelineStateFile(path)
        sf.write({"current_stage": "planning", "foo": "bar"})
        data = sf.read()
        assert data["current_stage"] == "planning"
        assert data["foo"] == "bar"

    def test_read_nonexistent(self, tmp_path):
        path = str(tmp_path / "no-such-file.yaml")
        sf = PipelineStateFile(path)
        assert sf.read() == {}

    def test_update(self, tmp_path):
        path = str(tmp_path / "state.yaml")
        sf = PipelineStateFile(path)
        sf.write({"current_stage": "planning"})
        result = sf.update(current_stage="plan_review", extra="value")
        assert result["current_stage"] == "plan_review"
        assert result["extra"] == "value"
        # Verify persisted
        reread = sf.read()
        assert reread["current_stage"] == "plan_review"

    def test_update_atomic_creates_dirs(self, tmp_path):
        path = str(tmp_path / "nested" / "dir" / "state.yaml")
        sf = PipelineStateFile(path)
        sf.update(current_stage="uninitialized")
        assert os.path.exists(path)


# ===========================================================================
# PipelineStateMachine
# ===========================================================================

class TestPipelineStateMachine:
    def _make_sm(self, tmp_path):
        path = str(tmp_path / "state.yaml")
        sf = PipelineStateFile(path)
        return PipelineStateMachine(sf)

    def test_valid_transition(self, tmp_path):
        sm = self._make_sm(tmp_path)
        assert sm.current_stage == PipelineStage.UNINITIALIZED
        state = sm.transition(PipelineStage.INTENT_CAPTURE)
        assert state["current_stage"] == "intent_capture"
        assert sm.current_stage == PipelineStage.INTENT_CAPTURE

    def test_invalid_transition_raises(self, tmp_path):
        sm = self._make_sm(tmp_path)
        with pytest.raises(InvalidTransitionError):
            sm.transition(PipelineStage.DONE)

    def test_any_nonterminal_to_cancelled(self, tmp_path):
        sm = self._make_sm(tmp_path)
        sm.transition(PipelineStage.INTENT_CAPTURE)
        sm.transition(PipelineStage.CANCELLED)
        assert sm.current_stage == PipelineStage.CANCELLED

    def test_any_nonterminal_to_paused(self, tmp_path):
        sm = self._make_sm(tmp_path)
        sm.transition(PipelineStage.INTENT_CAPTURE)
        sm.transition(PipelineStage.PAUSED)
        assert sm.current_stage == PipelineStage.PAUSED

    def test_any_nonterminal_to_failed(self, tmp_path):
        sm = self._make_sm(tmp_path)
        sm.transition(PipelineStage.INTENT_CAPTURE)
        sm.transition(PipelineStage.FAILED)
        assert sm.current_stage == PipelineStage.FAILED

    def test_any_nonterminal_to_blocked(self, tmp_path):
        sm = self._make_sm(tmp_path)
        sm.transition(PipelineStage.INTENT_CAPTURE)
        sm.transition(PipelineStage.BLOCKED)
        assert sm.current_stage == PipelineStage.BLOCKED

    def test_terminal_done_cannot_transition(self, tmp_path):
        sm = self._make_sm(tmp_path)
        # Walk to DONE
        sm.transition(PipelineStage.INTENT_CAPTURE)
        sm.transition(PipelineStage.PLANNING)
        sm.transition(PipelineStage.PLAN_REVIEW)
        sm.transition(PipelineStage.PLAN_APPROVAL)
        sm.transition(PipelineStage.TASK_BREAKDOWN)
        sm.transition(PipelineStage.TASK_REVIEW)
        sm.transition(PipelineStage.PRIORITIZATION)
        sm.transition(PipelineStage.EXECUTION_GRAPH)
        sm.transition(PipelineStage.TASK_EXECUTION)
        sm.transition(PipelineStage.PER_TASK_QUALITY)
        sm.transition(PipelineStage.GAP_DETECTION)
        sm.transition(PipelineStage.DOCUMENTATION)
        sm.transition(PipelineStage.COMPLETION)
        sm.transition(PipelineStage.DONE)
        with pytest.raises(InvalidTransitionError):
            sm.transition(PipelineStage.PLANNING)

    def test_task_execution_can_advance_to_gap_detection(self, tmp_path):
        sm = self._make_sm(tmp_path)
        sm.transition(PipelineStage.INTENT_CAPTURE)
        sm.transition(PipelineStage.PLANNING)
        sm.transition(PipelineStage.PLAN_REVIEW)
        sm.transition(PipelineStage.PLAN_APPROVAL)
        sm.transition(PipelineStage.TASK_BREAKDOWN)
        sm.transition(PipelineStage.TASK_REVIEW)
        sm.transition(PipelineStage.PRIORITIZATION)
        sm.transition(PipelineStage.EXECUTION_GRAPH)
        sm.transition(PipelineStage.TASK_EXECUTION)
        state = sm.transition(PipelineStage.GAP_DETECTION)
        assert state["current_stage"] == "gap_detection"

    def test_terminal_cancelled_cannot_transition(self, tmp_path):
        sm = self._make_sm(tmp_path)
        sm.transition(PipelineStage.INTENT_CAPTURE)
        sm.transition(PipelineStage.CANCELLED)
        with pytest.raises(InvalidTransitionError):
            sm.transition(PipelineStage.PLANNING)

    def test_terminal_rolled_back_cannot_transition(self, tmp_path):
        sm = self._make_sm(tmp_path)
        sm.transition(PipelineStage.INTENT_CAPTURE)
        sm.transition(PipelineStage.CANCELLED)
        # CANCELLED is terminal, so force state for testing ROLLED_BACK
        sf = PipelineStateFile(str(tmp_path / "state.yaml"))
        sf.write({"current_stage": "rolled_back"})
        sm2 = PipelineStateMachine(sf)
        with pytest.raises(InvalidTransitionError):
            sm2.transition(PipelineStage.PLANNING)

    def test_transition_history_recorded(self, tmp_path):
        sm = self._make_sm(tmp_path)
        sm.transition(PipelineStage.INTENT_CAPTURE)
        sm.transition(PipelineStage.PLANNING)
        sf = PipelineStateFile(str(tmp_path / "state.yaml"))
        state = sf.read()
        assert "transitions" in state
        assert len(state["transitions"]) == 2
        assert state["transitions"][0]["from"] == "uninitialized"
        assert state["transitions"][0]["to"] == "intent_capture"
        assert state["transitions"][1]["from"] == "intent_capture"
        assert state["transitions"][1]["to"] == "planning"


# ===========================================================================
# TaskDAG
# ===========================================================================

class TestTaskDAG:
    def test_add_tasks_and_build_dependents(self):
        dag = TaskDAG()
        dag.add_task("task-001")
        dag.add_task("task-002", ["task-001"])
        dag._register_dependents()
        assert "task-002" in dag.nodes["task-001"].dependents

    def test_validate_missing_deps(self):
        dag = TaskDAG()
        dag.add_task("task-001", ["task-999"])
        errors = dag.validate()
        assert any("non-existent" in e for e in errors)

    def test_validate_detects_cycles(self):
        dag = TaskDAG()
        dag.add_task("task-001", ["task-002"])
        dag.add_task("task-002", ["task-001"])
        # Build dependents manually since add_task only adds if dep exists at add time
        dag.nodes["task-001"].dependents.append("task-002")
        dag.nodes["task-002"].dependents.append("task-001")
        errors = dag.validate()
        assert any("Cycle" in e for e in errors)

    def test_initialize_states_no_deps_ready(self):
        dag = TaskDAG()
        dag.add_task("task-001")
        dag.add_task("task-002", ["task-001"])
        dag.initialize_states()
        assert dag.nodes["task-001"].state == TaskState.READY
        assert dag.nodes["task-002"].state == TaskState.PENDING

    def test_get_ready_tasks(self):
        dag = TaskDAG()
        dag.add_task("task-001")
        dag.add_task("task-002", ["task-001"])
        dag.initialize_states()
        ready = dag.get_ready_tasks()
        assert len(ready) == 1
        assert ready[0].task_id == "task-001"

    def test_get_topological_order(self):
        dag = TaskDAG()
        dag.add_task("task-001")
        dag.add_task("task-002", ["task-001"])
        dag.add_task("task-003", ["task-002"])
        order = dag.get_topological_order()
        assert order.index("task-001") < order.index("task-002")
        assert order.index("task-002") < order.index("task-003")

    def test_mark_complete_success_unblocks_dependents(self):
        dag = TaskDAG()
        dag.add_task("task-001")
        dag.add_task("task-002", ["task-001"])
        dag._register_dependents()
        dag.initialize_states()
        dag.mark_complete("task-001", success=True)
        assert dag.nodes["task-001"].state == TaskState.SUCCEEDED
        assert dag.nodes["task-002"].state == TaskState.READY

    def test_mark_complete_failure_blocks_dependents(self):
        dag = TaskDAG()
        dag.add_task("task-001")
        dag.add_task("task-002", ["task-001"])
        dag._register_dependents()
        dag.initialize_states()
        dag.mark_complete("task-001", success=False)
        assert dag.nodes["task-001"].state == TaskState.FAILED
        assert dag.nodes["task-002"].state == TaskState.BLOCKED

    def test_mark_skipped_default_blocks_dependents(self):
        dag = TaskDAG()
        dag.add_task("task-001")
        dag.add_task("task-002", ["task-001"])
        dag._register_dependents()
        dag.initialize_states()
        dag.mark_skipped("task-001")
        assert dag.nodes["task-001"].state == TaskState.SKIPPED
        assert dag.nodes["task-002"].state == TaskState.BLOCKED

    def test_mark_skipped_force_unblock(self):
        dag = TaskDAG()
        dag.add_task("task-001")
        dag.add_task("task-002", ["task-001"])
        dag._register_dependents()
        dag.initialize_states()
        dag.mark_skipped("task-001", force_unblock=True)
        assert dag.nodes["task-001"].state == TaskState.SKIPPED
        assert dag.nodes["task-002"].state == TaskState.READY

    def test_from_tasks_classmethod(self):
        tasks = [
            {"id": "task-001", "depends_on": []},
            {"id": "task-002", "depends_on": ["task-001"]},
            {"id": "task-003", "depends_on": ["task-001"]},
        ]
        dag = TaskDAG.from_tasks(tasks)
        assert len(dag.nodes) == 3
        assert dag.nodes["task-001"].state == TaskState.READY
        assert dag.nodes["task-002"].state == TaskState.PENDING
        # task-002 and task-003 depend on task-001
        assert "task-002" in dag.nodes["task-001"].dependents
        assert "task-003" in dag.nodes["task-001"].dependents

    def test_to_dict(self):
        dag = TaskDAG()
        dag.add_task("task-001")
        dag.initialize_states()
        d = dag.to_dict()
        assert "task-001" in d
        assert d["task-001"]["state"] == "ready"

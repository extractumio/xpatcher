"""Tests for v2 lane-scoped session management.

Focus: session isolation invariants that matter for pipeline correctness.
"""

from src.dispatcher.lanes import STAGE_LANE_MAP, LaneManager


class TestLaneIsolationInvariants:
    """The core v2 design invariant: stages that must not share sessions."""

    def test_every_pipeline_stage_maps_to_a_lane(self):
        required = {
            "intent_capture", "planning", "plan_fix", "plan_review",
            "plan_approval", "task_breakdown", "task_fix", "task_review",
            "prioritization", "execution_graph", "task_execution",
            "per_task_quality", "fix_iteration", "gap_detection",
            "documentation", "completion",
        }
        assert required <= set(STAGE_LANE_MAP), f"Unmapped: {required - set(STAGE_LANE_MAP)}"

    def test_plan_author_and_review_are_isolated(self, tmp_path):
        """Review independence: reviewer must not see author's conversation."""
        mgr = LaneManager(tmp_path / "f")
        sid_author, _ = mgr.resolve_session("planning")
        sid_review, _ = mgr.resolve_session("plan_review")
        assert sid_author != sid_review

    def test_planning_and_task_breakdown_are_isolated(self, tmp_path):
        """Manifest authoring must not inherit planning session bias."""
        mgr = LaneManager(tmp_path / "f")
        sid_plan, _ = mgr.resolve_session("planning")
        sid_manifest, _ = mgr.resolve_session("task_breakdown")
        assert sid_plan != sid_manifest

    def test_different_tasks_get_different_sessions(self, tmp_path):
        """No cross-task contamination during execution."""
        mgr = LaneManager(tmp_path / "f")
        sid1, _ = mgr.resolve_session("task_execution", "task-001")
        sid2, _ = mgr.resolve_session("task_execution", "task-002")
        assert sid1 != sid2


class TestLaneContinuity:
    """Continuity within a lane: stages that should share context."""

    def test_plan_draft_and_fix_share_session(self, tmp_path):
        """Planner remembers design tradeoffs across fix iterations."""
        mgr = LaneManager(tmp_path / "f")
        sid1, _ = mgr.resolve_session("planning")
        sid2, resume = mgr.resolve_session("plan_fix")
        assert sid1 == sid2
        assert resume

    def test_task_execution_and_fix_share_session(self, tmp_path):
        """Executor keeps context across fix iterations for one task."""
        mgr = LaneManager(tmp_path / "f")
        sid1, _ = mgr.resolve_session("task_execution", "task-001")
        sid2, resume = mgr.resolve_session("fix_iteration", "task-001")
        assert sid1 == sid2
        assert resume

    def test_intent_capture_is_isolated_from_planning(self, tmp_path):
        """Intent capture should not contaminate planning with a reused conversation."""
        mgr = LaneManager(tmp_path / "f")
        sid1, _ = mgr.resolve_session("intent_capture")
        sid2, resume = mgr.resolve_session("planning")
        assert sid1 != sid2
        assert not resume


class TestLaneRotation:
    def test_rotation_replaces_session_after_limit(self, tmp_path):
        config = {"lanes": {"spec_author": {"max_invocations": 2}}}
        mgr = LaneManager(tmp_path / "f", config)
        sid1, _ = mgr.resolve_session("planning")
        mgr.resolve_session("plan_fix")
        sid3, resume = mgr.resolve_session("planning")
        assert sid3 != sid1
        assert not resume  # fresh session after rotation

    def test_forced_rotation_on_validation_contamination(self, tmp_path):
        mgr = LaneManager(tmp_path / "f")
        sid1, _ = mgr.resolve_session("planning")
        sid2 = mgr.rotate_lane("planning")
        assert sid2 != sid1


class TestLanePersistence:
    def test_lanes_survive_manager_restart(self, tmp_path):
        """Session ID and invocation count persist; cost is in-memory only (persisted via pipeline state)."""
        feature_dir = tmp_path / "f"
        mgr = LaneManager(feature_dir)
        sid, _ = mgr.resolve_session("planning")

        mgr2 = LaneManager(feature_dir)
        state = mgr2.get_lane_state("spec_author")
        assert state.session_id == sid
        assert state.invocation_count == 1

    def test_all_lane_states_exported_for_pipeline_state(self, tmp_path):
        mgr = LaneManager(tmp_path / "f")
        mgr.resolve_session("planning")
        mgr.resolve_session("task_breakdown")
        mgr.resolve_session("task_execution", "task-001")
        states = mgr.get_all_lane_states()
        assert {"spec_author", "manifest_author", "task_exec:task-001"} <= set(states)

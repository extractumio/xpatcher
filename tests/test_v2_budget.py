"""Tests for v2 budget management.

Focus: policy decisions that affect pipeline behavior, not arithmetic.
"""

from src.dispatcher.budget import BudgetManager


class TestBudgetPolicy:
    """Budget thresholds drive retry and blocking decisions."""

    def test_no_cap_never_blocks(self):
        mgr = BudgetManager()
        mgr.record_cost("spec_author", 999.0)
        assert not mgr.should_block("spec_author")
        assert mgr.max_retries("spec_author") == 2

    def test_budget_progression_warn_tighten_block(self):
        """As cost approaches cap: full retries → reduced retries → blocked."""
        mgr = BudgetManager({"budgets": {"spec_author": 10.0}})
        # Well under: full retries
        assert mgr.max_retries("spec_author") == 2
        # At 85%: tightened
        mgr.record_cost("spec_author", 8.5)
        assert mgr.max_retries("spec_author") == 1
        assert not mgr.should_block("spec_author")
        # At 100%: blocked
        mgr.record_cost("spec_author", 1.5)
        assert mgr.max_retries("spec_author") == 0
        assert mgr.should_block("spec_author")

    def test_pipeline_total_tracks_all_scopes(self):
        mgr = BudgetManager()
        mgr.record_cost("spec_author", 0.50)
        mgr.record_cost("manifest_author", 0.30)
        mgr.record_cost("task_exec:task-001", 0.20)
        assert mgr.get_cost("pipeline") == 1.00

    def test_task_exec_inherits_base_cap(self):
        """task_exec:task-001 should use the task_exec cap."""
        mgr = BudgetManager({"budgets": {"task_exec": 5.0}})
        mgr.record_cost("task_exec:task-001", 5.0)
        assert mgr.should_block("task_exec:task-001")

    def test_threshold_warnings_recorded_as_checkpoints(self):
        mgr = BudgetManager({"budgets": {"spec_author": 10.0}})
        mgr.record_cost("spec_author", 7.0)
        cp = mgr.check("spec_author")
        assert cp.warning  # at 70%, should warn
        assert mgr.get_checkpoints()  # checkpoint recorded

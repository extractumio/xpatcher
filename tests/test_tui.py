"""Tests for dispatcher.tui — TUIRenderer."""

from datetime import datetime, timedelta, timezone

from src.dispatcher.tui import TUIRenderer


class TestTUIRenderer:
    @staticmethod
    def _plain(text: str) -> str:
        return TUIRenderer._ANSI_RE.sub("", text)

    def test_header_and_stage_render_user_visible_text(self, capsys):
        tui = TUIRenderer()
        tui.header("Pipeline Start")
        tui.stage("Planning")
        captured = capsys.readouterr()
        assert "Pipeline Start" in captured.out
        assert "Planning" in captured.out

    def test_elapsed_formats_seconds_and_minutes(self):
        tui = TUIRenderer()
        tui._start_time = datetime.now(timezone.utc) - timedelta(seconds=5)
        short_elapsed = tui._elapsed()

        tui._start_time = datetime.now(timezone.utc) - timedelta(minutes=2, seconds=30)
        long_elapsed = tui._elapsed()

        assert "s" in short_elapsed
        assert "m" not in short_elapsed
        assert "2m" in long_elapsed
        assert "30s" in long_elapsed

    def test_status_messages_route_to_expected_streams(self, capsys):
        tui = TUIRenderer()
        tui.success("Task completed")
        tui.warning("Slow response")
        tui.cost_update(1.2345)
        tui.error("Something failed")
        tui.debug("Agent session abc-123")
        captured = capsys.readouterr()

        assert "Task completed" in captured.out
        assert "Slow response" in captured.out
        assert "1.2345" in captured.out
        assert "Something failed" in captured.err
        assert "Agent session abc-123" in captured.err
        assert "[debug]" in captured.err

    def test_dashboard_lines_show_pipeline_flow_and_current_stage(self):
        tui = TUIRenderer()
        tui.set_pipeline("xp-20260403-test", "Add echo count")
        tui.set_stage("task_execution", task_id="task-002", lane="task_exec:task-002", owner_agent="executor")
        tui.set_invocation_context(claude_session_id="cb9abcdf")
        tui.update_activity("plan-reviewer", "Read", "reading src/echo.py")
        lines = tui._dashboard_lines()
        current_plain = self._plain(lines[2])
        activity_plain = self._plain(lines[3])

        assert "xp-20260403-test" in lines[0]
        assert "[◐ Exec]" in lines[1]
        assert "[✓ Intent]" in lines[1]
        assert "task-002" in current_plain
        assert "claude:cb9abcdf" in current_plain
        assert "plan-reviewer · Read" in activity_plain
        assert "reading src/echo.py" in activity_plain

    def test_dashboard_recent_events_roll_up_latest_activity(self):
        tui = TUIRenderer()
        tui.set_pipeline("xp-20260403-test", "Feature")
        tui.set_stage("plan_review", owner_agent="plan-reviewer")
        tui.update_activity("plan-reviewer", "Grep", "searching /__init__/ src/")
        tui.update_activity("planner", "Edit", "editing src/app.py")
        lines = tui._dashboard_lines()
        recent_plain = self._plain(lines[4])

        assert "planner · Edit editing src/app.py" in recent_plain
        assert "plan-reviewer · Grep searching /__init__/ src/" in recent_plain

    def test_dashboard_shows_loop_progress_and_clears_on_non_loop_stage(self):
        tui = TUIRenderer()
        tui.set_pipeline("xp-20260403-test", "Feature")
        tui.set_stage("plan_review", owner_agent="plan-reviewer")
        tui.set_loop_progress("plan review", 2, 3)
        assert "plan review 2/3" in tui._dashboard_lines()[2]

        tui.set_stage("task_execution", owner_agent="executor")
        assert "plan review 2/3" not in tui._dashboard_lines()[2]

    def test_dashboard_heartbeat_uses_last_tool_name(self):
        tui = TUIRenderer()
        tui.set_pipeline("xp-20260403-test", "Feature")
        tui.set_stage("task_execution", owner_agent="executor")
        tui.update_activity("executor", "Bash", "running pytest -q")
        tui._activity = ""
        assert "waiting on Bash" in tui._dashboard_lines()[3]

    def test_dashboard_main_agent_defaults_to_waiting_for_model(self):
        tui = TUIRenderer()
        tui.set_pipeline("xp-20260403-test", "Feature")
        tui.set_stage("task_execution", owner_agent="executor")
        tui.set_invocation_context(owner_agent="executor")
        lines = tui._dashboard_lines()
        assert "executor" in lines[3]
        assert "waiting for model" in lines[3]

    def test_dashboard_waiting_for_subagent_after_agent_tool(self):
        tui = TUIRenderer()
        tui.set_pipeline("xp-20260403-test", "Feature")
        tui.set_stage("task_execution", owner_agent="executor")
        tui.update_activity("executor", "Agent", "delegating to reviewer: verify diff")
        tui._activity = ""
        assert "waiting for subagent" in tui._dashboard_lines()[3]

    def test_dashboard_lines_are_trimmed_to_terminal_width(self, monkeypatch):
        tui = TUIRenderer()
        monkeypatch.setattr(tui, "_term_width", lambda: 70)
        tui.set_pipeline("xp-20260403-test", "A very long feature description that would otherwise wrap across the terminal")
        tui.set_stage("task_execution", task_id="task-002", lane="task_exec:task-002", owner_agent="executor")
        lines = tui._dashboard_lines()

        for line in lines:
            assert tui._visible_len(line) <= 69

    def test_rendered_row_count_accounts_for_wrapped_lines(self, monkeypatch):
        tui = TUIRenderer()
        monkeypatch.setattr(tui, "_term_width", lambda: 20)
        rows = tui._rendered_row_count([
            "1234567890123456789",
            "12345678901234567890123456789012345678",
        ])
        assert rows == 3

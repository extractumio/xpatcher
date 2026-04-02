"""Tests for dispatcher.tui — TUIRenderer."""

from datetime import datetime, timedelta, timezone

from src.dispatcher.tui import TUIRenderer


class TestTUIRenderer:
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

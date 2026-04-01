"""Tests for dispatcher.tui — TUIRenderer."""

import io
import sys
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import pytest

from src.dispatcher.tui import TUIRenderer


class TestTUIRenderer:
    def test_header_outputs_text(self, capsys):
        tui = TUIRenderer()
        tui.header("Pipeline Start")
        captured = capsys.readouterr()
        assert "Pipeline Start" in captured.out

    def test_stage_outputs_text(self, capsys):
        tui = TUIRenderer()
        tui.stage("Planning")
        captured = capsys.readouterr()
        assert "Planning" in captured.out

    def test_elapsed_returns_formatted_time(self):
        tui = TUIRenderer()
        # Override start time to a known value
        tui._start_time = datetime.now(timezone.utc) - timedelta(seconds=5)
        elapsed = tui._elapsed()
        # Should be "5s" or close to it
        assert "s" in elapsed
        # Should not have "m" for just 5 seconds
        assert "m" not in elapsed

    def test_elapsed_with_minutes(self):
        tui = TUIRenderer()
        tui._start_time = datetime.now(timezone.utc) - timedelta(minutes=2, seconds=30)
        elapsed = tui._elapsed()
        assert "2m" in elapsed
        assert "30s" in elapsed

    def test_success_outputs_text(self, capsys):
        tui = TUIRenderer()
        tui.success("Task completed")
        captured = capsys.readouterr()
        assert "Task completed" in captured.out

    def test_error_outputs_to_stderr(self, capsys):
        tui = TUIRenderer()
        tui.error("Something failed")
        captured = capsys.readouterr()
        assert "Something failed" in captured.err

    def test_warning_outputs_text(self, capsys):
        tui = TUIRenderer()
        tui.warning("Slow response")
        captured = capsys.readouterr()
        assert "Slow response" in captured.out

    def test_cost_update(self, capsys):
        tui = TUIRenderer()
        tui.cost_update(1.2345)
        captured = capsys.readouterr()
        assert "1.2345" in captured.out

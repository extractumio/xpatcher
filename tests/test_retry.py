"""Tests for dispatcher.retry — retry_with_backoff."""

from unittest.mock import MagicMock, patch

import pytest

from src.dispatcher.retry import RetryExhausted, retry_with_backoff


class TestRetryWithBackoff:
    @patch("src.dispatcher.retry.time.sleep")
    def test_succeeds_first_try(self, mock_sleep):
        fn = MagicMock(return_value="ok")
        result = retry_with_backoff(fn, max_retries=3)
        assert result == "ok"
        fn.assert_called_once()
        mock_sleep.assert_not_called()

    @patch("src.dispatcher.retry.time.sleep")
    def test_retries_then_succeeds(self, mock_sleep):
        fn = MagicMock(side_effect=[ValueError("fail"), "ok"])
        result = retry_with_backoff(fn, max_retries=3, base_delay=0.1)
        assert result == "ok"
        assert fn.call_count == 2
        mock_sleep.assert_called_once()

    @patch("src.dispatcher.retry.time.sleep")
    def test_exhausts_retries(self, mock_sleep):
        fn = MagicMock(side_effect=ValueError("always fails"))
        with pytest.raises(RetryExhausted, match="Failed after"):
            retry_with_backoff(fn, max_retries=2, base_delay=0.1)
        assert fn.call_count == 3  # 1 initial + 2 retries

    @patch("src.dispatcher.retry.time.sleep")
    def test_non_retryable_propagates(self, mock_sleep):
        fn = MagicMock(side_effect=KeyboardInterrupt("stop"))
        with pytest.raises(KeyboardInterrupt):
            retry_with_backoff(
                fn, max_retries=3, base_delay=0.1,
                retryable_exceptions=(ValueError,),
            )
        fn.assert_called_once()
        mock_sleep.assert_not_called()

    @patch("src.dispatcher.retry.time.sleep")
    def test_backoff_delay_increases(self, mock_sleep):
        fn = MagicMock(side_effect=[ValueError("1"), ValueError("2"), "ok"])
        retry_with_backoff(fn, max_retries=3, base_delay=1.0, max_delay=30.0)
        assert mock_sleep.call_count == 2
        delays = [call.args[0] for call in mock_sleep.call_args_list]
        assert delays[0] == 1.0   # 1.0 * 2^0
        assert delays[1] == 2.0   # 1.0 * 2^1

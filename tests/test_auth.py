"""Tests for src.dispatcher.auth — authentication resolution."""

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from src.dispatcher.auth import (
    resolve_auth_env,
    build_subprocess_env,
    describe_auth_source,
    has_oauth_credentials,
)


# ---------------------------------------------------------------------------
# resolve_auth_env: .env file
# ---------------------------------------------------------------------------

class TestDotenvResolution:
    def test_loads_api_key_from_dotenv(self, tmp_path):
        (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=sk-ant-api03-test123\n")
        result = resolve_auth_env(tmp_path)
        assert result == {"ANTHROPIC_API_KEY": "sk-ant-api03-test123"}

    def test_dotenv_strips_double_quotes(self, tmp_path):
        (tmp_path / ".env").write_text('ANTHROPIC_API_KEY="sk-ant-api03-quoted"\n')
        result = resolve_auth_env(tmp_path)
        assert result["ANTHROPIC_API_KEY"] == "sk-ant-api03-quoted"

    def test_dotenv_strips_single_quotes(self, tmp_path):
        (tmp_path / ".env").write_text("ANTHROPIC_API_KEY='sk-ant-api03-single'\n")
        result = resolve_auth_env(tmp_path)
        assert result["ANTHROPIC_API_KEY"] == "sk-ant-api03-single"

    def test_dotenv_skips_comments_and_blanks(self, tmp_path):
        (tmp_path / ".env").write_text(
            "# This is a comment\n\n  \nANTHROPIC_API_KEY=sk-ant-api03-real\n"
        )
        result = resolve_auth_env(tmp_path)
        assert result["ANTHROPIC_API_KEY"] == "sk-ant-api03-real"

    def test_dotenv_ignores_other_vars(self, tmp_path):
        (tmp_path / ".env").write_text("OTHER_VAR=foo\n")
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ANTHROPIC_API_KEY", None)
            with patch("src.dispatcher.auth._extract_oauth_access_token", return_value=None):
                result = resolve_auth_env(tmp_path)
        assert result == {}

    def test_dotenv_empty_value_returns_none(self, tmp_path):
        (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=\n")
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ANTHROPIC_API_KEY", None)
            with patch("src.dispatcher.auth._extract_oauth_access_token", return_value=None):
                result = resolve_auth_env(tmp_path)
        assert result == {}

    def test_no_dotenv_file(self, tmp_path):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ANTHROPIC_API_KEY", None)
            with patch("src.dispatcher.auth._extract_oauth_access_token", return_value=None):
                result = resolve_auth_env(tmp_path)
        assert result == {}

    def test_dotenv_takes_priority_over_env(self, tmp_path):
        (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=sk-ant-api03-from-dotenv\n")
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-api03-from-env"}):
            result = resolve_auth_env(tmp_path)
        assert result["ANTHROPIC_API_KEY"] == "sk-ant-api03-from-dotenv"


# ---------------------------------------------------------------------------
# resolve_auth_env: inherited environment
# ---------------------------------------------------------------------------

class TestEnvironmentResolution:
    def test_inherits_existing_api_key(self, tmp_path):
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-api03-env"}):
            result = resolve_auth_env(tmp_path)
        assert result == {}  # Nothing to add, already inherited


# ---------------------------------------------------------------------------
# resolve_auth_env: OAuth fallback
# ---------------------------------------------------------------------------

class TestOAuthResolution:
    def test_uses_native_oauth_without_injecting_api_key(self, tmp_path):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ANTHROPIC_API_KEY", None)
            with patch("src.dispatcher.auth.has_oauth_credentials", return_value=True):
                result = resolve_auth_env(tmp_path)
        assert result == {}

    def test_no_oauth_returns_empty(self, tmp_path):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ANTHROPIC_API_KEY", None)
            with patch("src.dispatcher.auth.has_oauth_credentials", return_value=False):
                result = resolve_auth_env(tmp_path)
        assert result == {}


# ---------------------------------------------------------------------------
# build_subprocess_env
# ---------------------------------------------------------------------------

class TestBuildSubprocessEnv:
    def test_empty_returns_none(self):
        assert build_subprocess_env({}) is None

    def test_merges_with_os_environ(self):
        env = build_subprocess_env({"ANTHROPIC_API_KEY": "test"})
        assert env is not None
        assert env["ANTHROPIC_API_KEY"] == "test"
        assert "PATH" in env  # Inherited from os.environ


# ---------------------------------------------------------------------------
# describe_auth_source
# ---------------------------------------------------------------------------

class TestDescribeAuthSource:
    def test_api_key_from_dotenv(self):
        assert describe_auth_source({"ANTHROPIC_API_KEY": "sk-ant-api03-x"}) == "API key (.env)"

    def test_oauth_token(self):
        assert describe_auth_source({"ANTHROPIC_API_KEY": "sk-ant-oat01-x"}) == "Claude subscription (OAuth)"

    def test_native_oauth_without_env_override(self):
        with patch("src.dispatcher.auth.has_oauth_credentials", return_value=True):
            assert describe_auth_source({}) == "Claude subscription (OAuth)"

    def test_from_environment(self):
        assert describe_auth_source({}, env_has_key=True) == "API key (environment)"

    def test_none(self):
        with patch("src.dispatcher.auth.has_oauth_credentials", return_value=False):
            assert describe_auth_source({}) == "none"


# ---------------------------------------------------------------------------
# Credential JSON parsing (via OAuth extraction)
# ---------------------------------------------------------------------------

class TestCredentialParsing:
    def test_has_oauth_credentials_from_credentials_file(self, tmp_path):
        cred = {
            "claudeAiOauth": {
                "accessToken": "sk-ant-oat01-abc123",
                "refreshToken": "sk-ant-ort01-def456",
            }
        }
        with patch("src.dispatcher.auth.platform") as mock_platform:
            mock_platform.system.return_value = "Linux"
            with patch("src.dispatcher.auth.Path.home", return_value=tmp_path):
                (tmp_path / ".claude").mkdir()
                (tmp_path / ".claude" / ".credentials.json").write_text(json.dumps(cred))
                assert has_oauth_credentials() is True

    def test_parses_keychain_json(self, tmp_path):
        cred = {
            "claudeAiOauth": {
                "accessToken": "sk-ant-oat01-abc123",
                "refreshToken": "sk-ant-ort01-def456",
            }
        }
        cred_file = Path.home() / ".claude" / ".credentials.json"
        with patch("src.dispatcher.auth.platform") as mock_platform:
            mock_platform.system.return_value = "Linux"
            with patch("src.dispatcher.auth.Path.home", return_value=tmp_path):
                (tmp_path / ".claude").mkdir()
                (tmp_path / ".claude" / ".credentials.json").write_text(json.dumps(cred))
                from src.dispatcher.auth import _extract_oauth_access_token
                token = _extract_oauth_access_token()
        assert token == "sk-ant-oat01-abc123"

    def test_missing_credentials_file(self):
        with patch("src.dispatcher.auth.platform") as mock_platform:
            mock_platform.system.return_value = "Linux"
            with patch("src.dispatcher.auth.Path.home", return_value=Path("/nonexistent")):
                from src.dispatcher.auth import _extract_oauth_access_token
                token = _extract_oauth_access_token()
        assert token is None

    def test_malformed_json(self, tmp_path):
        with patch("src.dispatcher.auth.platform") as mock_platform:
            mock_platform.system.return_value = "Linux"
            with patch("src.dispatcher.auth.Path.home", return_value=tmp_path):
                (tmp_path / ".claude").mkdir()
                (tmp_path / ".claude" / ".credentials.json").write_text("not json")
                from src.dispatcher.auth import _extract_oauth_access_token
                token = _extract_oauth_access_token()
        assert token is None

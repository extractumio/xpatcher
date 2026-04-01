"""Authentication resolution for Claude Code CLI --bare invocations.

Claude Code ``--bare`` mode accepts only two auth mechanisms:
``ANTHROPIC_API_KEY`` env var, or ``apiKeyHelper`` via ``--settings``.
Keychain / OAuth reads are explicitly skipped.

This module bridges the gap by resolving credentials from the user's
environment and returning environment variables that ``subprocess``
calls merge into the child process environment.

Resolution order
----------------
1. ``ANTHROPIC_API_KEY`` from ``$XPATCHER_HOME/.env``
2. ``ANTHROPIC_API_KEY`` already present in the inherited environment
3. OAuth access token extracted from the local Claude Code credential
   store (macOS Keychain or ``~/.claude/.credentials.json``) — passed
   as ``ANTHROPIC_API_KEY`` (the Anthropic API routes by token prefix)
"""

import json
import os
import platform
import subprocess
from pathlib import Path

_KEYCHAIN_SERVICE = "Claude Code-credentials"
_OAUTH_KEY = "claudeAiOauth"
_SOURCE_DOTENV = "API key (.env)"
_SOURCE_ENV = "API key (environment)"
_SOURCE_OAUTH = "Claude subscription (OAuth)"


def resolve_auth_env(xpatcher_home: Path) -> dict[str, str]:
    """Return env-var overrides that authenticate ``claude --bare``.

    The caller should merge the returned dict into the subprocess
    environment.  An empty dict means no extra variables are needed
    (credentials are already in the inherited environment or absent
    entirely).
    """
    api_key = _load_api_key_from_dotenv(xpatcher_home)
    if api_key:
        return {"ANTHROPIC_API_KEY": api_key}

    if os.environ.get("ANTHROPIC_API_KEY"):
        return {}

    token = _extract_oauth_access_token()
    if token:
        return {"ANTHROPIC_API_KEY": token}

    return {}


def build_subprocess_env(auth_env: dict[str, str]) -> dict[str, str] | None:
    """Merge *auth_env* into the current process environment.

    Returns ``None`` when *auth_env* is empty so that ``subprocess``
    falls back to natural inheritance.
    """
    if not auth_env:
        return None
    env = os.environ.copy()
    env.update(auth_env)
    return env


def describe_auth_source(auth_env: dict[str, str], env_has_key: bool = False) -> str:
    """Human-readable label for the resolved auth method.

    *env_has_key* should be ``True`` when ``ANTHROPIC_API_KEY`` is
    already present in the inherited environment (avoids reading
    ``os.environ`` as hidden global state).
    """
    key = auth_env.get("ANTHROPIC_API_KEY", "")
    if key:
        if key.startswith("sk-ant-oat"):
            return _SOURCE_OAUTH
        return _SOURCE_DOTENV
    if env_has_key:
        return _SOURCE_ENV
    return "none"


def _load_api_key_from_dotenv(xpatcher_home: Path) -> str | None:
    env_file = xpatcher_home / ".env"
    if not env_file.is_file():
        return None

    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition("=")
        if key.strip() != "ANTHROPIC_API_KEY":
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        return value or None

    return None


def _extract_oauth_access_token() -> str | None:
    """Extract the OAuth access token from the local Claude Code credential store."""
    if platform.system() == "Darwin":
        return _oauth_from_keychain()
    return _oauth_from_credentials_file()


def _oauth_from_keychain() -> str | None:
    try:
        proc = subprocess.run(
            ["security", "find-generic-password", "-s", _KEYCHAIN_SERVICE, "-w"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if proc.returncode != 0:
            return None
        return _parse_access_token(proc.stdout.strip())
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None


def _oauth_from_credentials_file() -> str | None:
    cred_path = Path.home() / ".claude" / ".credentials.json"
    if not cred_path.is_file():
        return None
    try:
        return _parse_access_token(cred_path.read_text())
    except OSError:
        return None


def _parse_access_token(raw: str) -> str | None:
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None
    return data.get(_OAUTH_KEY, {}).get("accessToken") or None

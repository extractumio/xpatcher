"""Validation helpers for manifest acceptance commands."""

from __future__ import annotations

import re
import shlex
import shutil


_SHELL_OPERATOR_TOKENS = {";", "&&", "||", "|", ">", ">>", "<", "<<", "&"}
_BASH_ONLY_PATTERNS = (
    re.compile(r"<\("),
    re.compile(r">\("),
    re.compile(r"\[\["),
    re.compile(r"\]\]"),
)


def lint_acceptance_command(command: str) -> str | None:
    """Return a human-readable error when *command* is not safely executable."""
    _, error = prepare_acceptance_command(command)
    return error


def prepare_acceptance_command(command: str) -> tuple[list[str] | None, str | None]:
    """Normalize an acceptance command into argv for subprocess execution."""
    command = command.strip()
    if not command:
        return None, "empty command"
    if "\n" in command:
        return None, "multi-line commands are not supported"
    if "`" in command:
        return None, "backtick shell syntax is not supported"

    try:
        parsed = shlex.split(command)
    except ValueError as exc:
        return None, f"invalid command syntax: {exc}"
    if not parsed:
        return None, "empty command"

    python_error = _lint_python_c_command(parsed)
    if python_error:
        return None, python_error

    if _requires_shell(command, parsed):
        bash_path = shutil.which("bash")
        sh_path = shutil.which("sh")
        if _uses_bash_only_syntax(command):
            if not bash_path:
                return None, "bash is required for this command but is not available"
            return [bash_path, "-lc", command], None
        if bash_path:
            return [bash_path, "-lc", command], None
        if sh_path:
            return [sh_path, "-c", command], None
        return None, "no shell is available to execute this command"

    return parsed, None


def _requires_shell(command: str, parsed: list[str]) -> bool:
    if any(token in _SHELL_OPERATOR_TOKENS for token in parsed):
        return True
    return _uses_bash_only_syntax(command)


def _uses_bash_only_syntax(command: str) -> bool:
    return any(pattern.search(command) for pattern in _BASH_ONLY_PATTERNS)


def _lint_python_c_command(parsed: list[str]) -> str | None:
    executable = parsed[0]
    if executable not in {"python", "python3"}:
        return None
    if "-c" not in parsed:
        return None
    index = parsed.index("-c")
    if index + 1 >= len(parsed):
        return "python -c requires an inline program"
    snippet = parsed[index + 1]
    try:
        compile(snippet, "<acceptance-command>", "exec")
    except SyntaxError as exc:
        message = exc.msg or "invalid Python syntax"
        return f"invalid python -c program: {message}"
    return None

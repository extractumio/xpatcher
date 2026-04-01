"""Git diff context extraction."""

import subprocess
from pathlib import Path


def get_staged_diff(project_dir: Path) -> str:
    result = subprocess.run(
        ["git", "diff", "--cached"],
        capture_output=True, text=True, cwd=str(project_dir),
    )
    return result.stdout


def get_feature_diff(project_dir: Path, base_branch: str = "main") -> str:
    result = subprocess.run(
        ["git", "diff", f"{base_branch}...HEAD"],
        capture_output=True, text=True, cwd=str(project_dir),
    )
    return result.stdout


def get_recent_commits(project_dir: Path, count: int = 10) -> str:
    result = subprocess.run(
        ["git", "log", f"-{count}", "--oneline"],
        capture_output=True, text=True, cwd=str(project_dir),
    )
    return result.stdout

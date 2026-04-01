"""Shared YAML utilities for agent output parsing and file I/O."""

import re
from pathlib import Path
import yaml


def load_yaml_file(path: Path) -> dict:
    """Load a YAML file, returning empty dict if missing or empty."""
    try:
        return yaml.safe_load(path.read_text()) or {}
    except (FileNotFoundError, OSError):
        return {}


def extract_yaml(text: str) -> dict | None:
    """Extract a YAML dict from agent output text using 4 strategies.

    Strategies (tried in order):
    1. Raw parse the entire text
    2. Parse content after --- separator
    3. Parse ```yaml code blocks
    4. Strip leading prose and parse from first YAML key line
    """
    if not text:
        return None

    # Strategy 1: Raw parse
    try:
        result = yaml.safe_load(text)
        if isinstance(result, dict):
            return result
    except yaml.YAMLError:
        pass

    # Strategy 2: After --- separator
    if "---" in text:
        parts = text.split("---")
        for part in parts[1:]:
            part = part.strip()
            if not part:
                continue
            try:
                result = yaml.safe_load(part)
                if isinstance(result, dict):
                    return result
            except yaml.YAMLError:
                continue

    # Strategy 3: ```yaml code blocks
    yaml_blocks = re.findall(r"```ya?ml\s*\n(.*?)```", text, re.DOTALL)
    for block in yaml_blocks:
        try:
            result = yaml.safe_load(block)
            if isinstance(result, dict):
                return result
        except yaml.YAMLError:
            continue

    # Strategy 4: Strip leading prose (find first line starting with a YAML key)
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if re.match(r"^[a-z_]+:", line):
            yaml_text = "\n".join(lines[i:])
            try:
                result = yaml.safe_load(yaml_text)
                if isinstance(result, dict):
                    return result
            except yaml.YAMLError:
                break

    return None

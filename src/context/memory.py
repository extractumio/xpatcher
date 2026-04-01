"""Cross-session memory interface (placeholder for v1)."""

from pathlib import Path
import yaml

from ..dispatcher.yaml_utils import load_yaml_file


class SessionMemory:
    """Stores key decisions and context across agent sessions."""

    def __init__(self, feature_dir: Path):
        self.feature_dir = feature_dir
        self._memory_file = feature_dir / "session-memory.yaml"

    def store(self, key: str, value: str):
        data = load_yaml_file(self._memory_file)
        data[key] = value
        self._memory_file.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False))

    def recall(self, key: str) -> str:
        data = load_yaml_file(self._memory_file)
        return data.get(key, "")

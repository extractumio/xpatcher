"""Persist artifacts to .xpatcher/ directory."""

from enum import Enum
import re
import yaml
from datetime import datetime, timezone
from pathlib import Path

from ..dispatcher.yaml_utils import load_yaml_file


class ArtifactStore:
    """Manages reading and writing YAML artifacts."""

    def __init__(self, feature_dir: Path):
        self.feature_dir = feature_dir

    def save(self, filename: str, data: dict) -> Path:
        path = self.feature_dir / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        data = self._plain_data(data)
        # Add common header if not present
        if "created_at" not in data:
            data["created_at"] = datetime.now(timezone.utc).isoformat()
        if "schema_version" not in data:
            data["schema_version"] = "1.0"
        path.write_text(yaml.safe_dump(data, default_flow_style=False, sort_keys=False))
        return path

    def load(self, filename: str) -> dict:
        return load_yaml_file(self.feature_dir / filename)

    def save_decision(self, decision_type: str, data: dict) -> Path:
        now = datetime.now(timezone.utc)
        filename = f"decisions/decision-{now.strftime('%Y%m%d-%H%M%S')}-{decision_type}.yaml"
        data["decision_type"] = decision_type
        data["decided_at"] = now.isoformat()
        return self.save(filename, data)

    def latest_version(self, prefix: str) -> int:
        """Find the latest version number for a versioned artifact (e.g., plan-v3.yaml -> 3)."""
        pattern = re.compile(rf'^{re.escape(prefix)}-v(\d+)\.yaml$')
        max_version = 0
        for path in self.feature_dir.iterdir():
            match = pattern.match(path.name)
            if match:
                version = int(match.group(1))
                max_version = max(max_version, version)
        return max_version

    def _plain_data(self, value):
        if isinstance(value, Enum):
            return value.value
        if isinstance(value, dict):
            return {key: self._plain_data(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._plain_data(item) for item in value]
        return value

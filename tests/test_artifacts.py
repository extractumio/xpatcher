"""Tests for artifacts.store — ArtifactStore."""

from pathlib import Path
from enum import Enum

import pytest
import yaml

from src.artifacts.store import ArtifactStore


class _FakeEnum(str, Enum):
    LOW = "low"


class TestArtifactStore:
    def test_save_creates_file(self, tmp_path):
        store = ArtifactStore(tmp_path)
        data = {"type": "plan", "summary": "test"}
        path = store.save("plan.yaml", data)
        assert path.exists()
        content = yaml.safe_load(path.read_text())
        assert content["type"] == "plan"

    def test_save_auto_injects_created_at(self, tmp_path):
        store = ArtifactStore(tmp_path)
        data = {"type": "plan", "summary": "test"}
        path = store.save("plan.yaml", data)
        content = yaml.safe_load(path.read_text())
        assert "created_at" in content

    def test_save_auto_injects_schema_version(self, tmp_path):
        store = ArtifactStore(tmp_path)
        data = {"type": "plan", "summary": "test"}
        path = store.save("plan.yaml", data)
        content = yaml.safe_load(path.read_text())
        assert content["schema_version"] == "1.0"

    def test_save_preserves_existing_created_at(self, tmp_path):
        store = ArtifactStore(tmp_path)
        data = {"type": "plan", "created_at": "2025-01-01T00:00:00"}
        path = store.save("plan.yaml", data)
        content = yaml.safe_load(path.read_text())
        assert content["created_at"] == "2025-01-01T00:00:00"

    def test_load_returns_dict(self, tmp_path):
        store = ArtifactStore(tmp_path)
        store.save("test.yaml", {"key": "value"})
        result = store.load("test.yaml")
        assert isinstance(result, dict)
        assert result["key"] == "value"

    def test_load_nonexistent_returns_empty(self, tmp_path):
        store = ArtifactStore(tmp_path)
        result = store.load("nonexistent.yaml")
        assert result == {}

    def test_save_decision_creates_timestamped_file(self, tmp_path):
        store = ArtifactStore(tmp_path)
        data = {"reason": "approved by human"}
        path = store.save_decision("approval", data)
        assert path.exists()
        assert "decisions" in str(path)
        assert "decision-" in path.name
        assert "approval" in path.name
        content = yaml.safe_load(path.read_text())
        assert content["decision_type"] == "approval"
        assert "decided_at" in content

    def test_latest_version_finds_highest(self, tmp_path):
        store = ArtifactStore(tmp_path)
        # Create versioned files
        (tmp_path / "plan-v1.yaml").write_text("v: 1")
        (tmp_path / "plan-v2.yaml").write_text("v: 2")
        (tmp_path / "plan-v5.yaml").write_text("v: 5")
        assert store.latest_version("plan") == 5

    def test_latest_version_returns_zero_when_none(self, tmp_path):
        store = ArtifactStore(tmp_path)
        assert store.latest_version("plan") == 0

    def test_save_creates_subdirectories(self, tmp_path):
        store = ArtifactStore(tmp_path)
        path = store.save("sub/dir/artifact.yaml", {"key": "value"})
        assert path.exists()

    def test_save_serializes_enums_as_plain_yaml_scalars(self, tmp_path):
        store = ArtifactStore(tmp_path)
        path = store.save("plan.yaml", {"severity": _FakeEnum.LOW})
        text = path.read_text()
        loaded = yaml.safe_load(text)
        assert "!!python/object" not in text
        assert loaded["severity"] == "low"

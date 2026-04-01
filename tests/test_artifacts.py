"""Tests for artifacts.store — ArtifactStore."""

from enum import Enum
import yaml

from src.artifacts.store import ArtifactStore


class _FakeEnum(str, Enum):
    LOW = "low"


class TestArtifactStore:
    def test_save_round_trip_adds_standard_metadata(self, tmp_path):
        store = ArtifactStore(tmp_path)
        data = {"type": "plan", "summary": "test"}
        path = store.save("plan.yaml", data)
        content = store.load("plan.yaml")

        assert path.exists()
        assert content["type"] == "plan"
        assert content["schema_version"] == "1.0"
        assert "created_at" in content

    def test_save_preserves_existing_created_at_and_supports_nested_paths(self, tmp_path):
        store = ArtifactStore(tmp_path)
        path = store.save(
            "sub/dir/plan.yaml",
            {"type": "plan", "created_at": "2025-01-01T00:00:00"},
        )
        content = yaml.safe_load(path.read_text())

        assert path.exists()
        assert content["created_at"] == "2025-01-01T00:00:00"

    def test_save_decision_writes_timestamped_decision_artifact(self, tmp_path):
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

    def test_load_missing_returns_empty_and_latest_version_tracks_highest(self, tmp_path):
        store = ArtifactStore(tmp_path)
        (tmp_path / "plan-v1.yaml").write_text("v: 1")
        (tmp_path / "plan-v2.yaml").write_text("v: 2")
        (tmp_path / "plan-v5.yaml").write_text("v: 5")
        empty_store = ArtifactStore(tmp_path / "empty")
        empty_store.feature_dir.mkdir()

        assert store.load("nonexistent.yaml") == {}
        assert store.latest_version("plan") == 5
        assert empty_store.latest_version("plan") == 0

    def test_save_serializes_enums_as_plain_yaml_scalars(self, tmp_path):
        store = ArtifactStore(tmp_path)
        path = store.save("plan.yaml", {"severity": _FakeEnum.LOW})
        text = path.read_text()
        loaded = yaml.safe_load(text)
        assert "!!python/object" not in text
        assert loaded["severity"] == "low"

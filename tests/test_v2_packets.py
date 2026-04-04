"""Tests for v2 context packet generation.

Focus: packets correctly bridge data between pipeline phases,
not just that files land on disk.
"""

from pathlib import Path

import yaml

from src.context.packets import ContextManager


def _make_context_mgr(tmp_path):
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / ".git").mkdir()
    (project_dir / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")
    (project_dir / "src").mkdir()
    (project_dir / "src" / "main.py").write_text("print('hello')\n")
    (project_dir / "tests").mkdir()
    (project_dir / "tests" / "test_main.py").write_text("def test_it(): pass\n")
    feature_dir = tmp_path / "feature"
    feature_dir.mkdir()
    return ContextManager(feature_dir, project_dir)


class TestRepoInventoryDetection:
    """Inventory must detect real project characteristics."""

    def test_detects_python_project_structure(self, tmp_path):
        mgr = _make_context_mgr(tmp_path)
        inv = mgr.build_repo_inventory()
        assert "python" in inv["primary_languages"]
        assert "pytest" in inv["test_frameworks"]
        assert "src/" in inv["source_roots"]
        assert "tests/" in inv["test_roots"]

    def test_detects_npm_project(self, tmp_path):
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        (project_dir / "package.json").write_text("{}")
        (project_dir / "src").mkdir()
        (project_dir / "src" / "index.js").write_text("module.exports = {}\n")
        feature_dir = tmp_path / "feature"
        feature_dir.mkdir()
        mgr = ContextManager(feature_dir, project_dir)
        inv = mgr.build_repo_inventory()
        assert "javascript" in inv["primary_languages"]
        assert "npm" in inv["package_managers"]

    def test_detects_nested_workspace_manifests_and_scripts(self, tmp_path):
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        (project_dir / "health-dashboard" / "frontend").mkdir(parents=True)
        (project_dir / "health-dashboard" / "backend").mkdir(parents=True)
        (project_dir / "health-dashboard" / "frontend" / "package.json").write_text("{}")
        (project_dir / "health-dashboard" / "frontend" / "vite.config.ts").write_text("export default {};\n")
        (project_dir / "health-dashboard" / "backend" / "requirements.txt").write_text("fastapi\n")
        (project_dir / "health-dashboard" / "start.sh").write_text("#!/bin/bash\n")
        feature_dir = tmp_path / "feature"
        feature_dir.mkdir()

        inv = ContextManager(feature_dir, project_dir).build_repo_inventory()

        assert "npm" in inv["package_managers"]
        manifest_paths = {item["path"] for item in inv["workspace_manifests"]}
        assert "health-dashboard/frontend/package.json" in manifest_paths
        assert "health-dashboard/backend/requirements.txt" in inv["key_configs"]
        assert "health-dashboard/frontend/vite.config.ts" in inv["key_configs"]
        assert "health-dashboard/start.sh" in inv["notable_scripts"]

    def test_inventory_excludes_data_and_backup_paths(self, tmp_path):
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        (project_dir / "data" / "greg").mkdir(parents=True)
        (project_dir / "data" / "greg" / "CLAUDE.md").write_text("private\n")
        (project_dir / "data_backup_20260329" / "greg").mkdir(parents=True)
        (project_dir / "data_backup_20260329" / "greg" / "CLAUDE.md").write_text("backup\n")
        (project_dir / "health-dashboard").mkdir()
        (project_dir / "health-dashboard" / "start.sh").write_text("#!/bin/bash\n")
        feature_dir = tmp_path / "feature"
        feature_dir.mkdir()

        inv = ContextManager(feature_dir, project_dir).build_repo_inventory()

        assert all("data/" not in path for path in inv.get("key_configs", []))
        assert all("data_backup" not in path for path in inv.get("key_configs", []))


class TestBootstrapContext:
    def test_bootstrap_creates_all_artifacts_and_they_are_loadable(self, tmp_path):
        mgr = _make_context_mgr(tmp_path)
        artifacts = mgr.build_bootstrap_context("Add caching layer")
        # All returned paths exist and contain valid YAML
        for name, path in artifacts.items():
            assert path.exists(), f"{name} not created"
            data = yaml.safe_load(path.read_text())
            assert "type" in data

    def test_has_bootstrap_context_reflects_reality(self, tmp_path):
        mgr = _make_context_mgr(tmp_path)
        assert not mgr.has_bootstrap_context()
        mgr.build_bootstrap_context("Test")
        assert mgr.has_bootstrap_context()

    def test_bootstrap_includes_implementation_scout(self, tmp_path):
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        (project_dir / "health-dashboard").mkdir()
        (project_dir / "health-dashboard" / "start.sh").write_text("#!/usr/bin/env bash\necho start\n")
        (project_dir / "health-dashboard" / "frontend").mkdir(parents=True)
        (project_dir / "health-dashboard" / "frontend" / "package.json").write_text("{\"name\":\"frontend\"}\n")
        feature_dir = tmp_path / "feature"
        feature_dir.mkdir()
        mgr = ContextManager(feature_dir, project_dir)

        artifacts = mgr.build_bootstrap_context("Dockerize app")
        scout = yaml.safe_load(artifacts["implementation_scout"].read_text())

        assert scout["type"] == "implementation_scout"
        assert scout["entries"]


class TestFeatureBriefBridging:
    """Feature brief must bind intent data to project knowledge."""

    def test_intent_data_flows_into_brief(self, tmp_path):
        mgr = _make_context_mgr(tmp_path)
        intent = {
            "goal": "Implement caching layer",
            "scope": ["src/cache.py"],
            "constraints": ["Out of scope: Redis"],
            "clarifying_questions": ["What cache TTL?"],
        }
        brief = mgr.build_feature_brief("Implement caching", intent)
        assert brief["goal"] == "Implement caching layer"  # prefers intent over raw description
        assert brief["scope"] == ["src/cache.py"]
        assert "What cache TTL?" in brief["ambiguity_flags"]

    def test_falls_back_to_description_without_intent(self, tmp_path):
        mgr = _make_context_mgr(tmp_path)
        brief = mgr.build_feature_brief("Raw description only")
        assert brief["goal"] == "Raw description only"


class TestPacketDataBridging:
    """Packets must carry forward data from their source artifacts."""

    def test_plan_packet_carries_open_questions(self, tmp_path):
        mgr = _make_context_mgr(tmp_path)
        (mgr.feature_dir / "plan-v1.yaml").write_text(
            yaml.dump({"type": "plan", "summary": "Plan", "open_questions": ["What about auth?"]})
        )
        packet = mgr.build_plan_packet(1)
        assert "What about auth?" in packet["open_questions"]

    def test_manifest_packet_carries_plan_summary(self, tmp_path):
        mgr = _make_context_mgr(tmp_path)
        (mgr.feature_dir / "plan-v1.yaml").write_text(
            yaml.dump({"type": "plan", "summary": "Add a caching layer with Redis backend"})
        )
        packet = mgr.build_manifest_packet(plan_version=1)
        assert "caching" in packet["plan_summary"].lower()

    def test_task_packet_carries_acceptance_criteria(self, tmp_path):
        mgr = _make_context_mgr(tmp_path)
        task = {
            "id": "task-001",
            "title": "Add cache module",
            "description": "Implement the caching layer",
            "acceptance_criteria": [
                {"id": "ac-01", "command": "pytest tests/test_cache.py", "severity": "must_pass"},
            ],
        }
        packet = mgr.build_task_packet(task)
        assert packet["acceptance_criteria"][0]["command"] == "pytest tests/test_cache.py"

    def test_gap_packet_carries_unresolved_gaps(self, tmp_path):
        mgr = _make_context_mgr(tmp_path)
        gap_report = {
            "verdict": "gaps_found",
            "gaps": [
                {"id": "G-001", "severity": "major", "description": "Missing error handling"},
                {"id": "G-002", "severity": "minor", "description": "No docs"},
            ],
        }
        packet = mgr.build_gap_packet(gap_report, 1)
        assert len(packet["unresolved_gaps"]) == 2
        assert packet["unresolved_gaps"][0]["severity"] == "major"

    def test_bulk_task_packets_cover_all_manifest_tasks(self, tmp_path):
        mgr = _make_context_mgr(tmp_path)
        manifest = {
            "tasks": [
                {"id": "task-001", "title": "T1", "description": "D1"},
                {"id": "task-002", "title": "T2", "description": "D2"},
                {"id": "task-003", "title": "T3", "description": "D3"},
            ]
        }
        packets = mgr.build_all_task_packets(manifest)
        ids = {p["task_id"] for p in packets}
        assert ids == {"task-001", "task-002", "task-003"}

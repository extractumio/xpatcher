"""Context packet generation for v2 pipeline.

Creates reusable context artifacts so that later stages stop
rediscovering the repo. Artifacts are concise, structured, versioned.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ..dispatcher.yaml_utils import load_yaml_file, save_yaml_file, now_iso

_SKIP_DIRS = frozenset({
    ".git", "node_modules", ".venv", "__pycache__", ".tox",
    "dist", "build", ".mypy_cache", "vendor", ".eggs",
})
_MAX_FILES_WALK = 10_000
_MAX_DISCOVERY_DEPTH = 4
_MAX_MANIFEST_MATCHES = 24
_MAX_SCRIPT_MATCHES = 12
_MAX_SCOUT_FILES = 8
_MAX_SCOUT_LINES = 80
_INVENTORY_EXCLUDE_PARTS = {"data", ".env", ".ssh", "secrets", "credentials"}


def _is_inventory_safe(rel_path: Path) -> bool:
    rel_text = str(rel_path)
    parts = set(rel_path.parts)
    if any(part.startswith("data_backup") for part in rel_path.parts):
        return False
    if parts & _INVENTORY_EXCLUDE_PARTS:
        return False
    if rel_path.name.startswith(".env"):
        return False
    return True


class ContextManager:
    """Creates and manages reusable context artifacts.

    Distinguishes stable project knowledge (created once per pipeline)
    from dynamic feature knowledge (evolves with approved plan and work).
    """

    def __init__(self, feature_dir: Path, project_dir: Path):
        self.feature_dir = feature_dir
        self.project_dir = project_dir
        self.context_dir = feature_dir / "context"
        self.context_dir.mkdir(parents=True, exist_ok=True)

    # -------------------------------------------------------------------
    # Stable context artifacts
    # -------------------------------------------------------------------

    def build_repo_inventory(self) -> dict[str, Any]:
        """Create repo-inventory.yaml: fast factual project orientation."""
        inventory: dict[str, Any] = {
            "type": "repo_inventory",
            "schema_version": "1.0",
            "repo_name": self.project_dir.name,
            "created_at": now_iso(),
        }

        # Detect languages — bounded walk with broader exclusions
        exts: dict[str, int] = {}
        count = 0
        for root, dirs, files in os.walk(self.project_dir):
            dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
            for filename in files:
                if count >= _MAX_FILES_WALK:
                    break
                path = Path(root) / filename
                exts[path.suffix.lower()] = exts.get(path.suffix.lower(), 0) + 1
                count += 1
            if count >= _MAX_FILES_WALK:
                break
        lang_map = {".py": "python", ".js": "javascript", ".ts": "typescript",
                     ".go": "go", ".rs": "rust", ".java": "java", ".rb": "ruby",
                     ".php": "php", ".cs": "csharp", ".cpp": "cpp", ".c": "c"}
        inventory["primary_languages"] = [lang for ext, lang in lang_map.items() if exts.get(ext, 0) > 0][:5]

        # Package managers and nested workspace manifests
        pm_markers = {
            "pyproject.toml": "uv/pip", "setup.py": "pip", "Pipfile": "pipenv",
            "package.json": "npm", "yarn.lock": "yarn", "pnpm-lock.yaml": "pnpm",
            "go.mod": "go", "Cargo.toml": "cargo", "Gemfile": "bundler",
            "pom.xml": "maven", "build.gradle": "gradle",
        }
        workspace_manifests: list[dict[str, str]] = []
        package_managers: set[str] = set()
        manifest_matches = 0
        key_configs_seen: set[str] = set()
        notable_scripts: list[str] = []
        script_names = {"start.sh", "setup.sh", "deploy.sh", "entrypoint.sh"}

        for root, dirs, files in os.walk(self.project_dir):
            rel_root = Path(root).relative_to(self.project_dir)
            if len(rel_root.parts) > _MAX_DISCOVERY_DEPTH:
                dirs[:] = []
                continue
            dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
            for filename in files:
                rel_path = rel_root / filename if rel_root != Path(".") else Path(filename)
                rel_text = str(rel_path)
                if not _is_inventory_safe(rel_path):
                    continue
                if filename in pm_markers and manifest_matches < _MAX_MANIFEST_MATCHES:
                    workspace_manifests.append({"path": rel_text, "type": pm_markers[filename]})
                    package_managers.add(pm_markers[filename])
                    manifest_matches += 1
                if filename in {
                    "pyproject.toml", "package.json", "vite.config.ts", "vite.config.js",
                    "Dockerfile", "docker-compose.yml", "docker-compose.yaml",
                    ".env.example", "requirements.txt", "Makefile", "CLAUDE.md", "AGENTS.md",
                } and rel_text not in key_configs_seen:
                    key_configs_seen.add(rel_text)
                if filename in script_names and len(notable_scripts) < _MAX_SCRIPT_MATCHES:
                    notable_scripts.append(rel_text)

        inventory["package_managers"] = sorted(package_managers)
        inventory["workspace_manifests"] = workspace_manifests

        # Test frameworks — read pyproject.toml once
        test_fws: set[str] = set()
        tf_markers = {
            "pytest.ini": "pytest", "conftest.py": "pytest",
            "jest.config.js": "jest", "jest.config.ts": "jest",
            "vitest.config.ts": "vitest",
        }
        for marker, fw in tf_markers.items():
            if (self.project_dir / marker).exists():
                test_fws.add(fw)
        pyproject = self.project_dir / "pyproject.toml"
        pyproject_content = pyproject.read_text() if pyproject.exists() else ""
        if "pytest" in pyproject_content:
            test_fws.add("pytest")
        inventory["test_frameworks"] = sorted(test_fws)

        # Source and test roots
        source_roots = [f"{d}/" for d in ("src", "lib", "app", "pkg") if (self.project_dir / d).is_dir()]
        test_roots = [f"{d}/" for d in ("tests", "test", "spec", "__tests__") if (self.project_dir / d).is_dir()]
        inventory["source_roots"] = source_roots or ["."]
        inventory["test_roots"] = test_roots

        # Key configs and scripts
        inventory["key_configs"] = sorted(key_configs_seen)
        if notable_scripts:
            inventory["notable_scripts"] = notable_scripts

        # Common commands — reuse pyproject_content
        commands: dict[str, list[str]] = {}
        if "pytest" in test_fws:
            commands["test"] = ["pytest -q"]
        elif (self.project_dir / "package.json").exists():
            commands["test"] = ["npm test"]
        if "ruff" in pyproject_content:
            commands["lint"] = ["ruff check ."]
        inventory["common_commands"] = commands

        save_yaml_file(self.context_dir / "repo-inventory.yaml", inventory)
        return inventory

    def build_feature_brief(self, description: str, intent_data: dict | None = None) -> dict[str, Any]:
        """Create feature-brief.yaml: normalized request bound to project."""
        brief: dict[str, Any] = {
            "type": "feature_brief",
            "schema_version": "1.0",
            "created_at": now_iso(),
        }
        if intent_data:
            brief["goal"] = intent_data.get("goal", description)
            brief["scope"] = intent_data.get("scope", [])
            brief["constraints"] = intent_data.get("constraints", [])
            brief["non_goals"] = [c for c in intent_data.get("constraints", []) if "out of scope" in c.lower()]
        else:
            brief["goal"] = description
            brief["scope"] = []
            brief["constraints"] = []
            brief["non_goals"] = []
        brief["ambiguity_flags"] = intent_data.get("clarifying_questions", []) if intent_data else []

        save_yaml_file(self.context_dir / "feature-brief.yaml", brief)
        return brief

    def build_implementation_scout(self) -> dict[str, Any]:
        """Create implementation-scout.yaml with a bounded set of likely-relevant files."""
        inventory = load_yaml_file(self.context_dir / "repo-inventory.yaml")
        candidates: list[str] = []
        for rel_path in inventory.get("notable_scripts", []):
            if rel_path not in candidates:
                candidates.append(rel_path)
        for rel_path in inventory.get("key_configs", []):
            if rel_path not in candidates:
                candidates.append(rel_path)

        entries: list[dict[str, Any]] = []
        for rel_path in candidates[:_MAX_SCOUT_FILES]:
            path = self.project_dir / rel_path
            if not path.is_file():
                continue
            try:
                lines = path.read_text().splitlines()
            except (OSError, UnicodeDecodeError):
                continue
            entries.append(
                {
                    "path": rel_path,
                    "line_count": len(lines),
                    "preview": lines[:_MAX_SCOUT_LINES],
                }
            )

        scout = {
            "type": "implementation_scout",
            "schema_version": "1.0",
            "created_at": now_iso(),
            "entries": entries,
        }
        save_yaml_file(self.context_dir / "implementation-scout.yaml", scout)
        return scout

    def build_bootstrap_context(self, description: str, intent_data: dict | None = None) -> dict[str, Path]:
        """Run the full bootstrap phase: create all stable context artifacts."""
        artifacts = {}
        self.build_repo_inventory()
        artifacts["repo_inventory"] = self.context_dir / "repo-inventory.yaml"
        self.build_feature_brief(description, intent_data)
        artifacts["feature_brief"] = self.context_dir / "feature-brief.yaml"
        self.build_implementation_scout()
        artifacts["implementation_scout"] = self.context_dir / "implementation-scout.yaml"
        return artifacts

    # -------------------------------------------------------------------
    # Dynamic context packets
    # -------------------------------------------------------------------

    def build_plan_packet(self, plan_version: int) -> dict[str, Any]:
        """Create plan-packet-vN.yaml: bridges stable context into planning."""
        plan_data = load_yaml_file(self.feature_dir / f"plan-v{plan_version}.yaml")
        packet: dict[str, Any] = {
            "type": "plan_packet",
            "schema_version": "1.0",
            "plan_version": plan_version,
            "created_at": now_iso(),
            "feature_brief_ref": "context/feature-brief.yaml",
            "stable_context_refs": ["context/repo-inventory.yaml"],
            "subsystem_targets": [],
            "planning_assumptions": [],
            "open_questions": plan_data.get("open_questions", []),
        }
        save_yaml_file(self.context_dir / f"plan-packet-v{plan_version}.yaml", packet)
        return packet

    def build_manifest_packet(self, plan_version: int, manifest_version: int = 1) -> dict[str, Any]:
        """Create manifest-packet-vN.yaml: bridges approved plan into task breakdown."""
        plan_data = load_yaml_file(self.feature_dir / f"plan-v{plan_version}.yaml")
        packet: dict[str, Any] = {
            "type": "manifest_packet",
            "schema_version": "1.0",
            "manifest_version": manifest_version,
            "plan_ref": f"plan-v{plan_version}.yaml",
            "plan_summary": plan_data.get("summary", ""),
            "created_at": now_iso(),
            "stable_context_refs": ["context/repo-inventory.yaml"],
            "affected_subsystems": [],
            "execution_constraints": [],
        }
        save_yaml_file(self.context_dir / f"manifest-packet-v{manifest_version}.yaml", packet)
        return packet

    def build_task_packet(self, task_data: dict) -> dict[str, Any]:
        """Create task-packets/task-XXX.yaml: the unit of execution."""
        task_id = task_data.get("id", "task-000")
        packet: dict[str, Any] = {
            "type": "task_packet",
            "schema_version": "1.0",
            "task_id": task_id,
            "title": task_data.get("title", ""),
            "objective": task_data.get("description", ""),
            "files_in_scope": task_data.get("files_in_scope", []),
            "acceptance_criteria": task_data.get("acceptance_criteria", []),
            "depends_on": task_data.get("depends_on", []),
            "estimated_complexity": task_data.get("estimated_complexity", "medium"),
            "quality_tier": task_data.get("quality_tier", "standard"),
            "created_at": now_iso(),
            "stable_context_refs": ["context/repo-inventory.yaml"],
        }
        packets_dir = self.context_dir / "task-packets"
        packets_dir.mkdir(parents=True, exist_ok=True)
        save_yaml_file(packets_dir / f"{task_id}.yaml", packet)
        return packet

    def build_gap_packet(self, gap_report: dict, gap_version: int = 1) -> dict[str, Any]:
        """Create gap-packet-vN.yaml: translates gap report into delta work."""
        packet: dict[str, Any] = {
            "type": "gap_packet",
            "schema_version": "1.0",
            "gap_version": gap_version,
            "created_at": now_iso(),
            "gap_report_ref": f"gap-report-v{gap_version}.yaml",
            "unresolved_gaps": gap_report.get("gaps", []),
            "affected_tasks": [],
            "candidate_delta_tasks": [],
        }
        save_yaml_file(self.context_dir / f"gap-packet-v{gap_version}.yaml", packet)
        return packet

    def build_all_task_packets(self, manifest_data: dict) -> list[dict]:
        """Create task packets for all tasks in a manifest."""
        return [self.build_task_packet(task) for task in manifest_data.get("tasks", [])]

    def get_stable_context_refs(self) -> list[str]:
        """Return paths to all available stable context artifacts."""
        return [f"context/{name}" for name in ("repo-inventory.yaml", "feature-brief.yaml")
                if (self.context_dir / name).exists()]

    def has_bootstrap_context(self) -> bool:
        """Check if bootstrap context artifacts exist."""
        return (self.context_dir / "repo-inventory.yaml").exists()

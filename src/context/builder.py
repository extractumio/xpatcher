"""Prompt assembly per agent and pipeline stage."""

from datetime import datetime
from pathlib import Path
from string import Template

import yaml


class MissingArtifactError(Exception):
    pass


class PromptBuilder:
    """Builds prompts for each agent invocation."""

    _templates: dict[str, str] | None = None

    def __init__(self, feature_dir: Path, project_dir: Path):
        self.feature_dir = feature_dir
        self.project_dir = project_dir

    @classmethod
    def _load_templates(cls) -> dict[str, str]:
        if cls._templates is None:
            prompts_path = Path(__file__).with_name("prompts.yaml")
            data = yaml.safe_load(prompts_path.read_text()) or {}
            cls._templates = data.get("templates", {})
        return cls._templates

    def _render(self, template_name: str, timeout_seconds: int = 600, **values) -> str:
        template = self._load_templates()[template_name]
        now = datetime.now()
        timeout_min = max(1, timeout_seconds // 60)
        payload = {
            "feature_dir": self._escape(self.feature_dir),
            "project_dir": self._escape(self.project_dir),
            "current_time": now.strftime("%Y-%m-%d %H:%M:%S"),
            "timeout_minutes": str(timeout_min),
        }
        for key, value in values.items():
            payload[key] = self._escape(value)
        return Template(template).substitute(payload)

    @staticmethod
    def _escape(value) -> str:
        return str(value).replace("$", "$$")

    def build_intent_capture(self, description: str, output_path: Path, timeout: int = 900) -> str:
        return self._render("intent_capture", timeout, description=description, output_path=output_path)

    def build_planner(self, output_path: Path, timeout: int = 900) -> str:
        intent_path = self.feature_dir / "intent.yaml"
        self._require_file(intent_path, "intent.yaml")
        return self._render("planner", timeout, intent_path=intent_path, output_path=output_path)

    def build_plan_reviewer(self, plan_version: int, output_path: Path, timeout: int = 600) -> str:
        plan_path = self.feature_dir / f"plan-v{plan_version}.yaml"
        intent_path = self.feature_dir / "intent.yaml"
        self._require_file(plan_path, f"plan-v{plan_version}.yaml")
        return self._render("plan_reviewer", timeout, plan_path=plan_path, intent_path=intent_path, output_path=output_path)

    def build_plan_fix(self, previous_version: int, output_path: Path, timeout: int = 900) -> str:
        review_path = self.feature_dir / f"plan-review-v{previous_version}.yaml"
        plan_path = self.feature_dir / f"plan-v{previous_version}.yaml"
        return self._render("plan_fix", timeout, review_path=review_path, plan_path=plan_path, output_path=output_path)

    def build_task_breakdown(self, plan_version: int, output_path: Path, timeout: int = 900) -> str:
        plan_path = self.feature_dir / f"plan-v{plan_version}.yaml"
        return self._render("task_breakdown", timeout, plan_path=plan_path, output_path=output_path)

    def build_task_reviewer(self, output_path: Path, timeout: int = 600) -> str:
        manifest_path = self.feature_dir / "task-manifest.yaml"
        self._require_file(manifest_path, "task-manifest.yaml")
        return self._render("task_reviewer", timeout, manifest_path=manifest_path, output_path=output_path)

    def build_task_fix(self, review_version: int, output_path: Path, timeout: int = 900) -> str:
        review_path = self.feature_dir / f"task-review-v{review_version}.yaml"
        manifest_path = self.feature_dir / "task-manifest.yaml"
        return self._render("task_fix", timeout, review_path=review_path, manifest_path=manifest_path, output_path=output_path)

    def build_executor(self, task_id: str, output_path: Path, timeout: int = 900) -> str:
        task_file = self._find_task_file(task_id)
        return self._render("executor", timeout, task_id=task_id, task_file=task_file, output_path=output_path)

    def build_executor_fix(self, task_id: str, findings: list, output_path: Path, timeout: int = 900) -> str:
        findings_text = yaml.safe_dump(findings, default_flow_style=False) if findings else "No specific findings"
        return self._render("executor_fix", timeout, task_id=task_id, findings_text=findings_text, output_path=output_path)

    def build_tester(self, task_id: str, output_path: Path, timeout: int = 600) -> str:
        return self._render("tester", timeout, task_id=task_id, output_path=output_path)

    def build_reviewer(self, task_id: str, output_path: Path, timeout: int = 600) -> str:
        return self._render("reviewer", timeout, task_id=task_id, output_path=output_path)

    def build_gap_detector(self, output_path: Path, timeout: int = 600) -> str:
        return self._render("gap_detector", timeout, output_path=output_path)

    def build_tech_writer(self, output_path: Path, timeout: int = 300) -> str:
        return self._render("tech_writer", timeout, output_path=output_path)

    def _require_file(self, path: Path, name: str):
        if not path.exists():
            raise MissingArtifactError(f"Required artifact missing: {name} at {path}")

    def _find_task_file(self, task_id: str) -> Path:
        for folder in ("todo", "in-progress", "done"):
            matches = [
                path for path in sorted((self.feature_dir / "tasks" / folder).glob(f"{task_id}-*.yaml"))
                if "-execution-log" not in path.name and "-quality-" not in path.name and "-review-" not in path.name
            ]
            if matches:
                return matches[0]
        raise MissingArtifactError(f"Required task artifact missing for {task_id}")

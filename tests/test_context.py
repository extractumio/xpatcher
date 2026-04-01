"""Behavioral tests for PromptBuilder."""

from pathlib import Path

import pytest
import yaml

from src.context.builder import MissingArtifactError, PromptBuilder


def _make_builder(tmp_path) -> PromptBuilder:
    feature_dir = tmp_path / "feature"
    (feature_dir / "tasks" / "todo").mkdir(parents=True)
    (feature_dir / "tasks" / "in-progress").mkdir(parents=True)
    (feature_dir / "tasks" / "done").mkdir(parents=True)
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    return PromptBuilder(feature_dir, project_dir)


class TestPromptBuilder:
    def test_executor_uses_materialized_task_file(self, tmp_path):
        builder = _make_builder(tmp_path)
        task_path = builder.feature_dir / "tasks" / "todo" / "task-001-add-login.yaml"
        task_path.write_text(yaml.dump({"id": "task-001"}))
        prompt = builder.build_executor("task-001")
        assert str(task_path) in prompt

    def test_executor_ignores_quality_artifacts_when_resolving_task_file(self, tmp_path):
        builder = _make_builder(tmp_path)
        quality_path = builder.feature_dir / "tasks" / "done" / "task-001-quality-report-v1.yaml"
        quality_path.write_text(yaml.dump({"type": "test_result"}))
        with pytest.raises(MissingArtifactError):
            builder.build_executor("task-001")

    def test_plan_and_manifest_review_prompts_use_stage_specific_schemas(self, tmp_path):
        builder = _make_builder(tmp_path)
        (builder.feature_dir / "intent.yaml").write_text(yaml.dump({"goal": "Add login flow"}))
        (builder.feature_dir / "plan-v1.yaml").write_text(yaml.dump({"type": "plan"}))
        (builder.feature_dir / "task-manifest.yaml").write_text(yaml.dump({"type": "task_manifest"}))

        plan_prompt = builder.build_plan_reviewer(1)
        task_prompt = builder.build_task_reviewer()

        assert "PlanReviewOutput" in plan_prompt
        assert "TaskManifestReviewOutput" in task_prompt

    def test_prompts_frame_artifacts_as_ephemeral_specifications(self, tmp_path):
        builder = _make_builder(tmp_path)
        (builder.feature_dir / "intent.yaml").write_text(yaml.dump({"goal": "Add login flow"}))
        (builder.feature_dir / "plan-v1.yaml").write_text(yaml.dump({"type": "plan"}))
        (builder.feature_dir / "task-manifest.yaml").write_text(yaml.dump({"type": "task_manifest"}))
        task_path = builder.feature_dir / "tasks" / "todo" / "task-001-add-login.yaml"
        task_path.write_text(yaml.dump({"id": "task-001"}))

        planner_prompt = builder.build_planner()
        executor_prompt = builder.build_executor("task-001")
        writer_prompt = builder.build_tech_writer()

        assert "temporary specification artifact" in planner_prompt
        assert "source code is the only long-term source of truth" in executor_prompt
        assert "not preserve or restate ephemeral planning artifacts" in writer_prompt

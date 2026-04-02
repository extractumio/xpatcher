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
        out = builder.feature_dir / "exec-out.yaml"
        prompt = builder.build_executor("task-001", out)
        assert str(task_path) in prompt

    def test_executor_ignores_quality_artifacts_when_resolving_task_file(self, tmp_path):
        builder = _make_builder(tmp_path)
        quality_path = builder.feature_dir / "tasks" / "done" / "task-001-quality-report-v1.yaml"
        quality_path.write_text(yaml.dump({"type": "test_result"}))
        out = builder.feature_dir / "exec-out.yaml"
        with pytest.raises(MissingArtifactError):
            builder.build_executor("task-001", out)

    def test_plan_and_manifest_review_prompts_use_stage_specific_schemas(self, tmp_path):
        builder = _make_builder(tmp_path)
        (builder.feature_dir / "intent.yaml").write_text(yaml.dump({"goal": "Add login flow"}))
        (builder.feature_dir / "plan-v1.yaml").write_text(yaml.dump({"type": "plan"}))
        (builder.feature_dir / "task-manifest.yaml").write_text(yaml.dump({"type": "task_manifest"}))

        plan_out = builder.feature_dir / "plan-review.yaml"
        task_out = builder.feature_dir / "task-review.yaml"
        plan_prompt = builder.build_plan_reviewer(1, plan_out)
        task_prompt = builder.build_task_reviewer(task_out)

        assert "PlanReviewOutput" in plan_prompt
        assert "TaskManifestReviewOutput" in task_prompt

    def test_builder_renders_required_runtime_paths_without_placeholders(self, tmp_path):
        builder = _make_builder(tmp_path)
        (builder.feature_dir / "intent.yaml").write_text(yaml.dump({"goal": "Add login flow"}))
        (builder.feature_dir / "plan-v1.yaml").write_text(yaml.dump({"type": "plan"}))
        (builder.feature_dir / "task-manifest.yaml").write_text(yaml.dump({"type": "task_manifest"}))
        task_path = builder.feature_dir / "tasks" / "todo" / "task-001-add-login.yaml"
        task_path.write_text(yaml.dump({"id": "task-001"}))

        planner_out = builder.feature_dir / "plan.yaml"
        exec_out = builder.feature_dir / "exec.yaml"
        writer_out = builder.feature_dir / "docs.yaml"
        planner_prompt = builder.build_planner(planner_out)
        executor_prompt = builder.build_executor("task-001", exec_out)
        writer_prompt = builder.build_tech_writer(writer_out)

        assert str(builder.feature_dir / "intent.yaml") in planner_prompt
        assert str(task_path) in executor_prompt
        assert str(builder.project_dir) in executor_prompt
        assert str(builder.feature_dir) in writer_prompt
        assert "${" not in planner_prompt + executor_prompt + writer_prompt

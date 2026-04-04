"""Behavioral tests for PromptBuilder."""

import re
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

    def test_v2_prompts_bias_existing_context_and_no_subagents(self, tmp_path):
        builder = _make_builder(tmp_path)
        builder.v2_mode = True
        (builder.feature_dir / "intent.yaml").write_text(yaml.dump({"goal": "Add login flow"}))
        (builder.feature_dir / "plan-v1.yaml").write_text(yaml.dump({"type": "plan"}))
        (builder.feature_dir / "plan-review-v1.yaml").write_text(yaml.dump({"type": "plan_review"}))
        (builder.feature_dir / "task-manifest.yaml").write_text(yaml.dump({"type": "task_manifest"}))
        (builder.feature_dir / "task-review-v1.yaml").write_text(yaml.dump({"type": "task_manifest_review"}))
        out = builder.feature_dir / "out.yaml"

        intent_prompt = builder.build_intent_capture("desc", out, timeout=900)
        planner_prompt = builder.build_planner(out, timeout=900)
        task_prompt = builder.build_task_breakdown(1, out, timeout=900)

        assert str(builder.feature_dir / "context" / "feature-brief.yaml") in intent_prompt
        assert "Do not use the Agent tool for this stage." in intent_prompt
        assert str(builder.feature_dir / "context" / "repo-inventory.yaml") in planner_prompt
        assert str(builder.feature_dir / "context" / "implementation-scout.yaml") in planner_prompt
        assert "Avoid recursive codebase globs" in planner_prompt
        assert "Do not use the Agent tool for this stage." in planner_prompt
        assert str(builder.feature_dir / "context" / "manifest-packet-v1.yaml") in task_prompt

    def test_every_prompt_contains_time_constraint_with_correct_timeout(self, tmp_path):
        """Every build_* method injects current_time and timeout_minutes into the prompt."""
        builder = _make_builder(tmp_path)
        (builder.feature_dir / "intent.yaml").write_text(yaml.dump({"goal": "g"}))
        (builder.feature_dir / "plan-v1.yaml").write_text(yaml.dump({"type": "plan"}))
        (builder.feature_dir / "task-manifest.yaml").write_text(yaml.dump({"type": "tm"}))
        (builder.feature_dir / "plan-review-v1.yaml").write_text(yaml.dump({"type": "pr"}))
        (builder.feature_dir / "task-review-v1.yaml").write_text(yaml.dump({"type": "tr"}))
        task_path = builder.feature_dir / "tasks" / "todo" / "task-001-do-stuff.yaml"
        task_path.write_text(yaml.dump({"id": "task-001"}))
        out = builder.feature_dir / "out.yaml"

        cases = [
            ("intent_capture",  builder.build_intent_capture("desc", out, timeout=900), 15),
            ("planner",         builder.build_planner(out, timeout=900), 15),
            ("plan_reviewer",   builder.build_plan_reviewer(1, out, timeout=600), 10),
            ("plan_fix",        builder.build_plan_fix(1, out, timeout=900), 15),
            ("task_breakdown",  builder.build_task_breakdown(1, out, timeout=900), 15),
            ("task_reviewer",   builder.build_task_reviewer(out, timeout=600), 10),
            ("task_fix",        builder.build_task_fix(1, out, timeout=900), 15),
            ("executor",        builder.build_executor("task-001", out, timeout=900), 15),
            ("executor_fix",    builder.build_executor_fix("task-001", [], out, timeout=900), 15),
            ("tester",          builder.build_tester("task-001", out, timeout=600), 10),
            ("reviewer",        builder.build_reviewer("task-001", out, timeout=600), 10),
            ("gap_detector",    builder.build_gap_detector(out, timeout=600), 10),
            ("tech_writer",     builder.build_tech_writer(out, timeout=300), 5),
        ]

        for name, prompt, expected_minutes in cases:
            # Has a timestamp (YYYY-MM-DD HH:MM:SS)
            assert re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", prompt), \
                f"{name}: missing timestamp"
            # Has the correct timeout in minutes
            assert f"hard limit of {expected_minutes} minutes" in prompt, \
                f"{name}: expected {expected_minutes} minutes, got: " + \
                (re.search(r"hard limit of (\d+) minutes", prompt).group(0) if re.search(r"hard limit of (\d+) minutes", prompt) else "nothing")
            # Has the output path
            assert str(out) in prompt, f"{name}: missing output_path"
            # No unresolved template variables
            assert "${" not in prompt, f"{name}: unresolved template variable"

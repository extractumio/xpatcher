"""Tests for v2 dispatcher core behavior.

Focus: pipeline-level behavior changes — validation retry, severity gating,
direct agent invocation, and mode switching. Not Python procedure testing.
"""

from pathlib import Path

import pytest
import yaml

from src.dispatcher.core import BudgetExceededError, Dispatcher
from src.dispatcher.session import AgentInvocation, AgentResult
from src.dispatcher.state import PipelineStateFile, PipelineStateMachine
from src.dispatcher.schemas import ArtifactValidator, ValidationResult
from src.dispatcher.lanes import LaneManager
from src.dispatcher.budget import BudgetManager
from src.context.packets import ContextManager
from src.context.builder import PromptBuilder


def _make_v2_dispatcher(tmp_path) -> Dispatcher:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / ".git").mkdir()
    (project_dir / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")
    (project_dir / "src").mkdir()
    (project_dir / "tests").mkdir()

    xpatcher_home = tmp_path / "home"
    xpatcher_home.mkdir()
    config = {
        "main_agent": {"timeout": 900},
        "iterations": {"plan_review_max": 3, "task_review_max": 3, "quality_loop_max": 3, "gap_reentry_max": 2},
        "human_gates": {"spec_confirmation": False, "completion_confirmation": False},
        "pipeline": {"mode": "v2"},
        "sessions": {"use_lanes": True},
        "context": {"use_bootstrap_artifacts": True},
        "contracts": {"generated": True},
        "reviews": {"severity_gate": True},
        "gaps": {"delta_mode": True},
        "validation": {"max_retries": 2, "rotate_on_retry": True},
    }
    (xpatcher_home / "config.yaml").write_text(yaml.dump(config))

    d = Dispatcher(project_dir, xpatcher_home)
    feature_dir = xpatcher_home / ".xpatcher" / "projects" / "test" / "feature"
    d._initialize_feature_dir(feature_dir)
    d.feature_dir = feature_dir
    d.state_file = PipelineStateFile(str(feature_dir / "pipeline-state.yaml"))
    d.state_file.write({"task_states": {}, "total_cost_usd": 0.0, "iterations": {}, "transitions": []})
    d.lanes = LaneManager(feature_dir, config)
    d.budget = BudgetManager(config)
    d.context_mgr = ContextManager(feature_dir, project_dir)
    return d


class TestV2ModeSwitching:
    def test_v2_enabled_by_config(self, tmp_path):
        d = _make_v2_dispatcher(tmp_path)
        assert d._is_v2(d._load_config())

    def test_v1_is_default_without_config(self, tmp_path):
        project_dir = tmp_path / "p"
        project_dir.mkdir()
        home = tmp_path / "h"
        home.mkdir()
        (home / "config.yaml").write_text(yaml.dump({"main_agent": {"timeout": 900}}))
        d = Dispatcher(project_dir, home)
        assert not d._is_v2(d._load_config())

    def test_v1_dispatcher_has_no_lanes(self, tmp_path):
        project_dir = tmp_path / "p"
        project_dir.mkdir()
        home = tmp_path / "h"
        home.mkdir()
        (home / "config.yaml").write_text(yaml.dump({"main_agent": {"timeout": 900}}))
        d = Dispatcher(project_dir, home)
        assert d.lanes is None


class TestSeverityGating:
    """Review verdict → pipeline decision mapping."""

    def test_approved_continues(self, tmp_path):
        d = _make_v2_dispatcher(tmp_path)
        assert d._review_severity_allows_continue({"verdict": "approved"})

    def test_minor_and_nit_only_auto_approve(self, tmp_path):
        d = _make_v2_dispatcher(tmp_path)
        data = {"verdict": "needs_changes", "findings": [
            {"severity": "minor", "description": "Style"},
            {"severity": "nit", "description": "Naming"},
        ]}
        assert d._review_severity_allows_continue(data)

    def test_any_major_blocks(self, tmp_path):
        d = _make_v2_dispatcher(tmp_path)
        data = {"verdict": "needs_changes", "findings": [
            {"severity": "major", "description": "Missing impl"},
            {"severity": "nit", "description": "Naming"},
        ]}
        assert not d._review_severity_allows_continue(data)

    def test_empty_findings_on_needs_changes_blocks(self, tmp_path):
        """Agent says needs_changes but gave no findings — treat as blocking."""
        d = _make_v2_dispatcher(tmp_path)
        assert not d._review_severity_allows_continue({"verdict": "needs_changes", "findings": []})


class TestValidationRepairPrompt:
    """The repair prompt must give the agent enough info to self-correct."""

    def test_includes_specific_errors(self, tmp_path):
        d = _make_v2_dispatcher(tmp_path)
        validation = ValidationResult(valid=False, errors=[
            "Missing field: summary",
            "quality_tier must be one of: lite | standard | thorough",
        ])
        prompt = d._build_repair_prompt("original", "task_manifest", validation, Path("/out.yaml"))
        assert "Missing field: summary" in prompt
        assert "quality_tier" in prompt
        assert "Original task instructions:" in prompt
        assert "original" in prompt

    def test_includes_schema_contract(self, tmp_path):
        d = _make_v2_dispatcher(tmp_path)
        validation = ValidationResult(valid=False, errors=["bad"])
        prompt = d._build_repair_prompt("original", "task_manifest", validation, None)
        assert "Output contract:" in prompt
        assert "must_pass" in prompt  # semantic rule included

    def test_targets_smallest_possible_correction(self, tmp_path):
        d = _make_v2_dispatcher(tmp_path)
        invalid_path = d.feature_dir / "validation-failures" / "plan_fix-attempt-1-input.yaml"
        invalid_path.parent.mkdir(parents=True, exist_ok=True)
        invalid_path.write_text("type: plan\n")
        validation = ValidationResult(valid=False, errors=["bad"])
        prompt = d._build_repair_prompt("original", "plan", validation, Path("/out.yaml"), repair_source=invalid_path)
        assert "smallest possible correction" in prompt
        assert str(invalid_path) in prompt

    def test_contract_block_can_be_disabled(self, tmp_path):
        d = _make_v2_dispatcher(tmp_path)
        validation = ValidationResult(valid=False, errors=["bad"])
        prompt = d._build_repair_prompt(
            "original",
            "task_manifest",
            validation,
            None,
            {"pipeline": {"mode": "v2"}, "contracts": {"generated": False}},
        )
        assert "Output contract:" not in prompt

    def test_failure_snapshot_persisted(self, tmp_path):
        d = _make_v2_dispatcher(tmp_path)
        result = AgentResult(session_id="s-123", raw_text="bad yaml\ntype: wrong")
        validation = ValidationResult(valid=False, errors=["Type mismatch"])
        d._record_validation_failure("planning", result, "plan", validation, 0)
        snapshot = yaml.safe_load((d.feature_dir / "validation-failures" / "planning-attempt-1.yaml").read_text())
        assert snapshot["errors"] == ["Type mismatch"]
        assert snapshot["session_id"] == "s-123"
        raw = (d.feature_dir / "validation-failures" / "planning-attempt-1-raw.txt").read_text()
        assert "bad yaml" in raw


class TestDirectAgentInvocation:
    """v2 uses --agent to bypass the delegation router."""

    def test_v2_cmd_includes_agent_and_budget(self, tmp_path):
        d = _make_v2_dispatcher(tmp_path)
        inv = AgentInvocation(prompt="do work", agent="planner", max_budget_usd=5.0)
        cmd = d.session._build_cmd(inv)
        assert cmd[cmd.index("--agent") + 1] == "planner"
        assert cmd[cmd.index("--max-budget-usd") + 1] == "5.0"

    def test_v1_cmd_omits_agent_and_budget(self, tmp_path):
        d = _make_v2_dispatcher(tmp_path)
        inv = AgentInvocation(prompt="do work")
        cmd = d.session._build_cmd(inv)
        assert "--agent" not in cmd
        assert "--max-budget-usd" not in cmd


class TestBudgetEnforcement:
    def test_exhausted_lane_budget_blocks_invocation(self, tmp_path):
        d = _make_v2_dispatcher(tmp_path)
        d.budget = BudgetManager({"budgets": {"spec_author": 1.0}})
        d.budget.record_cost("spec_author", 1.0)
        with pytest.raises(BudgetExceededError):
            d._invoke_stage("do work", d._load_config(), "planning")

    def test_pipeline_budget_also_limits_invocation(self, tmp_path):
        d = _make_v2_dispatcher(tmp_path)
        d.budget = BudgetManager({"budgets": {"pipeline": 2.0, "spec_author": 10.0}})
        d.budget.record_cost("pipeline", 1.75)
        assert d._remaining_budget_usd("planning") == 0.25

    def test_restore_v2_state_hydrates_budget_costs(self, tmp_path):
        d = _make_v2_dispatcher(tmp_path)
        d.budget = BudgetManager({"budgets": {"pipeline": 10.0}})
        d._restore_v2_state({"budget_costs": {"pipeline": 3.5, "spec_author": 1.25}})
        assert d.budget.get_cost("pipeline") == 3.5
        assert d.budget.get_cost("spec_author") == 1.25

    def test_restore_v2_state_supports_legacy_budget_field(self, tmp_path):
        d = _make_v2_dispatcher(tmp_path)
        d.budget = BudgetManager({"budgets": {"pipeline": 10.0}})
        d._restore_v2_state({"budget_checkpoints": {"pipeline": 2.0}})
        assert d.budget.get_cost("pipeline") == 2.0


class TestValidationRetryLoop:
    def test_invalid_prerequisite_artifact_fails_fast(self, tmp_path):
        d = _make_v2_dispatcher(tmp_path)
        sm = PipelineStateMachine(d.state_file)
        bad_plan = d.feature_dir / "plan-v1.yaml"
        bad_plan.write_text("---\ntype: plan\nsummary: short\n")

        data = d._ensure_valid_yaml_artifact(sm, bad_plan, "plan", "plan-v1.yaml")

        assert data is None
        state = d.state_file.read()
        assert state["current_stage"] == "failed"
        assert state["status"] == "failed"

    def test_missing_output_file_is_a_validation_failure(self, tmp_path, monkeypatch):
        d = _make_v2_dispatcher(tmp_path)
        output_path = d.feature_dir / "missing.yaml"

        monkeypatch.setattr(d, "_invoke_stage", lambda *args, **kwargs: AgentResult(session_id="s-1", raw_text="---\ntype: plan\n"))

        _, validation = d._invoke_validated_stage(
            prompt="original prompt",
            config={"pipeline": {"mode": "v2"}, "validation": {"max_retries": 0}},
            expected_type="plan",
            stage="planning",
            output_path=output_path,
        )

        assert not validation.valid
        assert "did not write it" in validation.errors[0]

    def test_nonrecoverable_auth_failure_stops_without_retry(self, tmp_path, monkeypatch):
        d = _make_v2_dispatcher(tmp_path)
        attempts = []

        def fake_invoke(prompt, config, stage, task_id=""):
            attempts.append(prompt)
            return AgentResult(session_id="s-1", raw_text="invalid api key", exit_code=1)

        validate_calls = []
        monkeypatch.setattr(d, "_invoke_stage", fake_invoke)
        monkeypatch.setattr(d.validator, "validate", lambda *args, **kwargs: validate_calls.append(True) or ValidationResult(valid=False, errors=["should not happen"]))

        _, validation = d._invoke_validated_stage(
            prompt="original prompt",
            config=d._load_config(),
            expected_type="plan",
            stage="planning",
        )

        assert not validation.valid
        assert "Non-recoverable agent invocation failure" in validation.errors[0]
        assert len(attempts) == 1
        assert validate_calls == []

    def test_retry_clears_stale_output_and_succeeds(self, tmp_path, monkeypatch):
        d = _make_v2_dispatcher(tmp_path)
        output_path = d.feature_dir / "retry.yaml"
        output_path.write_text("stale")
        seen_exists = []
        payloads = iter(["bad", "---\ntype: plan\nsummary: valid summary\nphases: []\nrisks: []\n"])

        def fake_invoke(prompt, config, stage, task_id=""):
            seen_exists.append(output_path.exists())
            output_path.write_text(next(payloads))
            return AgentResult(session_id="s-1", raw_text="")

        def fake_validate(raw_text, expected_type):
            if "type: plan" in raw_text:
                return ValidationResult(valid=True, data={"type": "plan", "summary": "valid summary", "phases": [], "risks": []})
            return ValidationResult(valid=False, errors=["bad yaml"])

        monkeypatch.setattr(d, "_invoke_stage", fake_invoke)
        monkeypatch.setattr(d.validator, "validate", fake_validate)

        _, validation = d._invoke_validated_stage(
            prompt="original prompt",
            config=d._load_config(),
            expected_type="plan",
            stage="planning",
            output_path=output_path,
        )

        assert validation.valid
        assert seen_exists == [False, False]

    def test_second_retry_rotates_lane(self, tmp_path, monkeypatch):
        d = _make_v2_dispatcher(tmp_path)
        output_path = d.feature_dir / "retry-rotate.yaml"
        writes = iter(["bad", "still bad", "---\ntype: plan\nsummary: valid summary\nphases: []\nrisks: []\n"])
        rotate_calls = []

        def fake_invoke(prompt, config, stage, task_id=""):
            output_path.write_text(next(writes))
            return AgentResult(session_id="s-1", raw_text="")

        def fake_validate(raw_text, expected_type):
            if "type: plan" in raw_text:
                return ValidationResult(valid=True, data={"type": "plan", "summary": "valid summary", "phases": [], "risks": []})
            return ValidationResult(valid=False, errors=["bad yaml"])

        monkeypatch.setattr(d, "_invoke_stage", fake_invoke)
        monkeypatch.setattr(d.validator, "validate", fake_validate)
        monkeypatch.setattr(d.lanes, "rotate_lane", lambda stage, task_id="": rotate_calls.append((stage, task_id)) or "rotated")

        _, validation = d._invoke_validated_stage(
            prompt="original prompt",
            config=d._load_config(),
            expected_type="plan",
            stage="planning",
            output_path=output_path,
        )

        assert validation.valid
        assert rotate_calls == [("planning", "")]

    def test_stops_early_when_retry_repeats_same_invalid_state(self, tmp_path, monkeypatch):
        d = _make_v2_dispatcher(tmp_path)
        attempts = []

        def fake_invoke(prompt, config, stage, task_id=""):
            attempts.append(prompt)
            return AgentResult(session_id="s-1", raw_text="bad")

        monkeypatch.setattr(d, "_invoke_stage", fake_invoke)
        monkeypatch.setattr(
            d.validator,
            "validate",
            lambda raw_text, expected_type: ValidationResult(valid=False, data={"type": "plan"}, errors=["same failure"]),
        )

        _, validation = d._invoke_validated_stage(
            prompt="original prompt",
            config={"pipeline": {"mode": "v2"}, "validation": {"max_retries": 3, "rotate_on_retry": False}},
            expected_type="plan",
            stage="planning",
        )

        assert not validation.valid
        assert len(attempts) == 3

    def test_budget_tightening_reduces_retry_count(self, tmp_path, monkeypatch):
        d = _make_v2_dispatcher(tmp_path)
        d.budget = BudgetManager({"budgets": {"spec_author": 10.0}})
        d.budget.record_cost("spec_author", 8.5)
        attempts = []

        def fake_invoke(prompt, config, stage, task_id=""):
            attempts.append(prompt)
            return AgentResult(session_id="s-1", raw_text="bad")

        monkeypatch.setattr(d, "_invoke_stage", fake_invoke)
        monkeypatch.setattr(d.validator, "validate", lambda raw_text, expected_type: ValidationResult(valid=False, errors=["bad yaml"]))

        _, validation = d._invoke_validated_stage(
            prompt="original prompt",
            config=d._load_config(),
            expected_type="plan",
            stage="planning",
        )

        assert not validation.valid
        assert len(attempts) == 2

    def test_validation_failure_persistence_can_be_disabled(self, tmp_path, monkeypatch):
        d = _make_v2_dispatcher(tmp_path)
        calls = []
        monkeypatch.setattr(d, "_invoke_stage", lambda *args, **kwargs: AgentResult(session_id="s-1", raw_text="bad"))
        monkeypatch.setattr(d.validator, "validate", lambda raw_text, expected_type: ValidationResult(valid=False, errors=["bad yaml"]))
        monkeypatch.setattr(d, "_record_validation_failure", lambda *args, **kwargs: calls.append("persisted"))

        _, validation = d._invoke_validated_stage(
            prompt="original prompt",
            config={"pipeline": {"mode": "v2"}, "validation": {"max_retries": 0, "persist_failures": False}},
            expected_type="plan",
            stage="planning",
        )

        assert not validation.valid
        assert calls == []


class TestQualityTierNormalization:
    """Agents write 'production', 'basic', etc. — validator must accept them."""

    def _validate_manifest_with_tier(self, tier: str) -> ValidationResult:
        return ArtifactValidator().validate_data({
            "type": "task_manifest",
            "plan_version": 1,
            "summary": "Manifest with non-standard quality tier value",
            "tasks": [{
                "id": "task-001",
                "title": "Test task title for validation",
                "description": "Description of what this task does",
                "quality_tier": tier,
                "acceptance_criteria": [{
                    "id": "ac-01", "description": "Test passes correctly",
                    "verification": "command", "command": "pytest -q", "severity": "must_pass",
                }],
                "estimated_complexity": "low",
            }],
        }, "task_manifest")

    def test_production_to_thorough(self):
        r = self._validate_manifest_with_tier("production")
        assert r.valid
        assert r.data["tasks"][0]["quality_tier"] == "thorough"

    def test_basic_to_lite(self):
        r = self._validate_manifest_with_tier("basic")
        assert r.valid
        assert r.data["tasks"][0]["quality_tier"] == "lite"

    def test_canonical_values_unchanged(self):
        for tier in ("lite", "standard", "thorough"):
            r = self._validate_manifest_with_tier(tier)
            assert r.valid
            assert r.data["tasks"][0]["quality_tier"] == tier


class TestDeterministicArtifactRepair:
    def test_plan_review_type_alias_is_normalized_without_retry(self):
        result = ArtifactValidator().validate_data({
            "type": "plan-review",
            "plan_version": 1,
            "verdict": "approve",
            "confidence": "high",
            "summary": "This review is valid after normalizing the type alias.",
            "findings": [],
        }, "plan_review")
        assert result.valid
        assert result.data["type"] == "plan_review"
        assert result.data["verdict"] == "approved"

    def test_plan_task_suffix_ids_are_repaired_and_dependencies_follow(self):
        result = ArtifactValidator().validate_data({
            "type": "plan",
            "summary": "This is a valid plan summary with enough detail for validation.",
            "phases": [{
                "id": "phase-1",
                "name": "Phase one",
                "description": "Phase description",
                "tasks": [
                    {
                        "id": "task-001",
                        "description": "First task in the plan",
                        "files": ["a.txt"],
                        "acceptance": ["A valid acceptance criterion"],
                        "depends_on": [],
                        "estimated_complexity": "low",
                    },
                    {
                        "id": "task-001a",
                        "description": "Second task in the plan",
                        "files": ["b.txt"],
                        "acceptance": ["Another valid acceptance criterion"],
                        "depends_on": ["task-001"],
                        "estimated_complexity": "low",
                    },
                    {
                        "id": "task-002",
                        "description": "Third task in the plan",
                        "files": ["c.txt"],
                        "acceptance": ["Third valid acceptance criterion"],
                        "depends_on": ["task-001a"],
                        "estimated_complexity": "low",
                    },
                ],
            }],
            "risks": [],
        }, "plan")
        assert result.valid
        ids = [task["id"] for task in result.data["phases"][0]["tasks"]]
        assert ids == ["task-001", "task-002", "task-003"]
        assert result.data["phases"][0]["tasks"][2]["depends_on"] == ["task-002"]


class TestPromptDelegationStripping:
    """v2 prompts strip @agent delegation text since --agent handles routing."""

    def test_v2_strips_delegation(self, tmp_path):
        feature_dir = tmp_path / "f"
        feature_dir.mkdir()
        (feature_dir / "intent.yaml").write_text(yaml.dump({"goal": "test"}))
        pb = PromptBuilder(feature_dir, tmp_path / "p", v2_mode=True)
        prompt = pb.build_planner(feature_dir / "plan-v1.yaml", 600)
        assert "@agent-planner" not in prompt
        assert "Task for the planner:" not in prompt

    def test_v1_keeps_delegation(self, tmp_path):
        feature_dir = tmp_path / "f"
        feature_dir.mkdir()
        (feature_dir / "intent.yaml").write_text(yaml.dump({"goal": "test"}))
        pb = PromptBuilder(feature_dir, tmp_path / "p", v2_mode=False)
        prompt = pb.build_planner(feature_dir / "plan-v1.yaml", 600)
        assert "@agent-planner" in prompt


class TestGapDeltaPrompt:
    """Delta gap prompt must tell the agent what already exists."""

    def test_prompt_lists_existing_task_ids(self, tmp_path):
        d = _make_v2_dispatcher(tmp_path)
        pb = PromptBuilder(d.feature_dir, d.project_dir, v2_mode=True)
        config = d._load_config()
        gap_report = {"gaps": [{"id": "G-001", "description": "Missing error handling"}]}
        prompt = d._build_gap_delta_prompt(pb, 1, d.feature_dir / "out.yaml",
                                            gap_report, {"task-001", "task-002"}, config)
        assert "task-001" in prompt
        assert "task-002" in prompt
        assert "ONLY new tasks" in prompt or "delta" in prompt.lower()
        assert "task-GNNN" in prompt


class TestFeatureFlags:
    def test_helpers_reflect_disabled_flags(self, tmp_path):
        d = _make_v2_dispatcher(tmp_path)
        config = {
            "pipeline": {"mode": "v2"},
            "sessions": {"use_lanes": False},
            "context": {"use_bootstrap_artifacts": False},
            "contracts": {"generated": False},
            "validation": {"persist_failures": False},
        }
        assert not d._use_lanes(config)
        assert not d._use_bootstrap_artifacts(config)
        assert not d._use_generated_contracts(config)
        assert not d._persist_validation_failures(config)

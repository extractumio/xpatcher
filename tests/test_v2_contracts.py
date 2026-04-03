"""Tests for v2 contract generation from schemas.

Focus: contracts are usable for validation repair and stable across runs.
"""

from src.context.contracts import (
    build_contract_block,
    build_field_contract,
    build_semantic_rules,
    contract_fingerprint,
)
from src.dispatcher.schemas import SCHEMAS, TaskManifestOutput, PlanOutput


class TestContractBlockUsability:
    """Generated contracts must be useful in repair prompts."""

    def test_task_manifest_contract_covers_agent_failure_modes(self):
        """The contract must mention the fields agents most often get wrong."""
        block = build_contract_block(TaskManifestOutput, "task_manifest")
        # Fields agents commonly omit or malform
        assert "plan_version" in block
        assert "quality_tier" in block
        assert "acceptance_criteria" in block
        assert "must_pass" in block  # semantic rule that prevents the #1 failure mode

    def test_plan_contract_covers_structural_requirements(self):
        block = build_contract_block(PlanOutput, "plan")
        assert "phases" in block
        assert "risks" in block
        assert "task IDs must be globally unique" in block

    def test_every_registered_schema_generates_valid_contract(self):
        """Contract generation must not crash on any schema — it runs in repair prompts."""
        for artifact_type, schema_class in SCHEMAS.items():
            block = build_contract_block(schema_class, artifact_type)
            assert block  # non-empty
            assert "Output contract:" in block

    def test_semantic_rules_absent_for_unknown_types(self):
        assert build_semantic_rules("nonexistent") == ""


class TestContractStability:
    """Contracts must be deterministic for golden tests and fingerprinting."""

    def test_same_schema_produces_identical_fingerprint(self):
        fp1 = contract_fingerprint(TaskManifestOutput, "task_manifest")
        fp2 = contract_fingerprint(TaskManifestOutput, "task_manifest")
        assert fp1 == fp2

    def test_different_schemas_produce_different_fingerprints(self):
        fp1 = contract_fingerprint(TaskManifestOutput, "task_manifest")
        fp2 = contract_fingerprint(PlanOutput, "plan")
        assert fp1 != fp2

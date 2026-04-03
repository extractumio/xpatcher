"""Contract tests for dispatcher.schemas."""

import pytest

from src.dispatcher.schemas import ArtifactValidator, TaskManifestOutput


def _task_manifest_data() -> dict:
    return {
        "type": "task_manifest",
        "plan_version": 1,
        "summary": "Break the plan into executable tasks",
        "tasks": [
            {
                "id": "task-001",
                "title": "Add API endpoint",
                "description": "Implement the new API endpoint end to end",
                "files_in_scope": ["src/api.py"],
                "acceptance_criteria": [
                    {
                        "id": "ac-01",
                        "description": "Endpoint returns a successful response",
                        "verification": "command",
                        "command": "pytest tests/test_api.py -q",
                        "severity": "must_pass",
                    }
                ],
                "depends_on": [],
                "estimated_complexity": "medium",
                "quality_tier": "standard",
            }
        ],
    }


class TestSchemaContracts:
    def test_plan_acceptance_allows_observed_live_list_shape(self):
        from src.dispatcher.schemas import PlanOutput

        plan = PlanOutput(
            type="plan",
            summary="A valid summary for the planner output contract",
            phases=[
                {
                    "id": "phase-1",
                    "name": "Phase One",
                    "description": "Do the work in a single phase",
                    "tasks": [
                        {
                            "id": "task-001",
                            "description": "Implement the feature end to end",
                            "files": ["app.py"],
                            "acceptance": ["pytest exits with code 0", "the function exists"],
                            "depends_on": [],
                            "estimated_complexity": "low",
                        }
                    ],
                }
            ],
        )
        assert isinstance(plan.phases[0].tasks[0].acceptance, list)

    def test_plan_complexity_normalizes_live_synonyms(self):
        from src.dispatcher.schemas import PlanOutput

        plan = PlanOutput(
            type="plan",
            summary="A valid summary for the planner output contract",
            phases=[
                {
                    "id": "phase-1",
                    "name": "Phase One",
                    "description": "Do the work in a single phase",
                    "tasks": [
                        {
                            "id": "task-001",
                            "description": "Implement the feature end to end",
                            "files": ["app.py"],
                            "acceptance": "pytest exits with code 0",
                            "depends_on": [],
                            "estimated_complexity": "trivial",
                        }
                    ],
                }
            ],
        )
        assert plan.phases[0].tasks[0].estimated_complexity.value == "low"

    def test_task_manifest_requires_executable_acceptance_checks(self):
        data = _task_manifest_data()
        data["tasks"][0]["acceptance_criteria"][0]["command"] = ""
        with pytest.raises(Exception, match="require a command"):
            TaskManifestOutput(**data)

    def test_task_manifest_requires_must_pass_command_per_task(self):
        data = _task_manifest_data()
        data["tasks"][0]["acceptance_criteria"][0]["severity"] = "should_pass"
        validator = ArtifactValidator()
        result = validator.validate_data(data, expected_type="task_manifest")
        assert result.valid is False
        assert "must include at least one must_pass command-based acceptance criterion" in result.errors[0]

    def test_task_manifest_rejects_empty_task_list(self):
        data = _task_manifest_data()
        data["tasks"] = []
        with pytest.raises(Exception):
            TaskManifestOutput(**data)


class TestValidatorRouting:
    def test_validator_rejects_wrong_schema_routing_for_plan_review(self):
        validator = ArtifactValidator()
        result = validator.validate_data(
            {
                "type": "plan_review",
                "plan_version": 1,
                "verdict": "approved",
                "confidence": "high",
                "summary": "Plan is valid and complete",
            },
            expected_type="review",
        )
        assert result.valid is False
        assert "Expected artifact type" in result.errors[0]

    def test_validator_accepts_valid_task_manifest(self):
        validator = ArtifactValidator()
        result = validator.validate_data(_task_manifest_data(), expected_type="task_manifest")
        assert result.valid is True
        assert result.data["tasks"][0]["acceptance_criteria"][0]["command"].startswith("pytest")

    def test_validator_returns_json_safe_enum_values(self):
        validator = ArtifactValidator()
        result = validator.validate_data(_task_manifest_data(), expected_type="task_manifest")
        assert result.valid is True
        assert result.data["tasks"][0]["estimated_complexity"] == "medium"

    def test_validator_normalizes_live_intent_scope_shape(self):
        validator = ArtifactValidator()
        result = validator.validate_data(
            {
                "kind": "intent",
                "goal": "Add a farewell helper and document it clearly",
                "scope": {
                    "in_scope": ["Add farewell helper", "Add tests"],
                    "out_of_scope": ["No CLI changes"],
                },
                "constraints": ["Keep the current module layout"],
                "clarifying_questions": [],
            },
            expected_type="intent",
        )
        assert result.valid is True
        assert result.data["type"] == "intent"
        assert result.data["scope"] == ["Add farewell helper", "Add tests"]
        assert "Out of scope: No CLI changes" in result.data["constraints"]

    def test_validator_normalizes_numeric_review_confidence(self):
        validator = ArtifactValidator()
        result = validator.validate_data(
            {
                "type": "review",
                "task_id": "task-001",
                "verdict": "approve",
                "confidence": 0.98,
                "summary": "The implementation matches the requested behavior cleanly",
                "findings": [],
            },
            expected_type="review",
        )
        assert result.valid is True
        assert result.data["confidence"] == "high"

    def test_validator_normalizes_mixed_plan_acceptance_items(self):
        validator = ArtifactValidator()
        result = validator.validate_data(
            {
                "type": "plan",
                "summary": "A valid plan summary for the normalization test",
                "phases": [
                    {
                        "id": "phase-1",
                        "name": "Phase One",
                        "description": "Implement the requested change",
                        "tasks": [
                            {
                                "id": "task-001",
                                "description": "Implement the feature end to end",
                                "files": ["service.py"],
                                "acceptance": [
                                    "pytest passes",
                                    {"exclaim signature": "str) -> str"},
                                ],
                                "depends_on": [],
                                "estimated_complexity": "low",
                            }
                        ],
                    }
                ],
            },
            expected_type="plan",
        )
        assert result.valid is True
        assert result.data["phases"][0]["tasks"][0]["acceptance"][1] == "exclaim signature: str) -> str"

    def test_validator_normalizes_plan_risk_key_to_description(self):
        validator = ArtifactValidator()
        result = validator.validate_data(
            {
                "type": "plan",
                "summary": "Plan with risk using wrong field name",
                "phases": [
                    {
                        "id": "phase-1",
                        "name": "Phase One",
                        "description": "Implement the feature",
                        "tasks": [
                            {
                                "id": "task-001",
                                "description": "Implement the feature end to end",
                                "acceptance": "Tests pass",
                                "estimated_complexity": "low",
                            }
                        ],
                    }
                ],
                "risks": [
                    {
                        "risk": "SQLite WAL journal mode may cause issues under Docker volume mounts",
                        "mitigation": "Document WAL mode behavior and recommend host-native DB path",
                        "severity": "medium",
                    }
                ],
            },
            expected_type="plan",
        )
        assert result.valid is True
        assert "SQLite WAL" in result.data["risks"][0]["description"]

    def test_validator_normalizes_plan_notes_list_to_string(self):
        validator = ArtifactValidator()
        result = validator.validate_data(
            {
                "type": "plan",
                "summary": "Plan with task notes as a list instead of string",
                "phases": [
                    {
                        "id": "phase-1",
                        "name": "Phase One",
                        "description": "Implement the feature",
                        "tasks": [
                            {
                                "id": "task-001",
                                "description": "Implement the feature end to end",
                                "acceptance": "Tests pass",
                                "estimated_complexity": "low",
                                "notes": [
                                    "The catch-all SPA route must exclude /api/*",
                                    "CORS origins must be configurable",
                                ],
                            }
                        ],
                    }
                ],
            },
            expected_type="plan",
        )
        assert result.valid is True
        assert isinstance(result.data["phases"][0]["tasks"][0]["notes"], str)
        assert "catch-all SPA" in result.data["phases"][0]["tasks"][0]["notes"]

    def test_validator_normalizes_perspective_analysis_lists_to_strings(self):
        validator = ArtifactValidator()
        result = validator.validate_data(
            {
                "type": "plan",
                "summary": "Plan with perspective analysis as lists",
                "phases": [
                    {
                        "id": "phase-1",
                        "name": "Phase One",
                        "description": "Implement the feature",
                        "tasks": [
                            {
                                "id": "task-001",
                                "description": "Implement the feature end to end",
                                "acceptance": "Tests pass",
                                "estimated_complexity": "low",
                            }
                        ],
                    }
                ],
                "perspective_analysis": {
                    "security": ["Container runs as non-root", "No secrets in image"],
                    "backend": ["CORS origins configurable", "Directory structure preserved"],
                },
            },
            expected_type="plan",
        )
        assert result.valid is True
        assert isinstance(result.data["perspective_analysis"]["security"], str)
        assert "non-root" in result.data["perspective_analysis"]["security"]

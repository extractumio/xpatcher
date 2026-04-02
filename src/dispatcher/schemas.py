"""Canonical Pydantic models for xpatcher artifact schemas (Design Spec Section 9).

All agent output artifacts are validated against these models. The SCHEMAS
registry maps artifact type strings to their Pydantic model classes.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class ReviewSeverity(str, Enum):
    CRITICAL = "critical"
    MAJOR = "major"
    MINOR = "minor"
    NIT = "nit"


class ReviewCategory(str, Enum):
    CORRECTNESS = "correctness"
    COMPLETENESS = "completeness"
    SECURITY = "security"
    PERFORMANCE = "performance"
    STYLE = "style"
    ARCHITECTURE = "architecture"
    TESTABILITY = "testability"
    REUSE = "reuse"
    EFFICIENCY = "efficiency"


class Confidence(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Complexity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class GapSeverity(str, Enum):
    CRITICAL = "critical"
    MAJOR = "major"
    MINOR = "minor"


class GapCategory(str, Enum):
    PLAN_COVERAGE = "plan-coverage"
    ERROR_HANDLING = "error-handling"
    EDGE_CASE = "edge-case"
    MIGRATION = "migration"
    DOCUMENTATION = "documentation"
    INTEGRATION = "integration"


class SimplificationType(str, Enum):
    DEDUP = "dedup"
    FLATTEN = "flatten"
    EXTRACT = "extract"
    REMOVE_DEAD = "remove_dead"
    REUSE_EXISTING = "reuse_existing"
    CONSTANT = "constant"


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TASK_ID_PATTERN = r"^task-[A-Z]?\d{3}$"
COMPLEXITY_ALIASES = {
    "trivial": Complexity.LOW,
    "simple": Complexity.LOW,
    "moderate": Complexity.MEDIUM,
    "complex": Complexity.HIGH,
}


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------

class ArtifactBase(BaseModel):
    schema_version: str = Field(default="1.0", pattern=r"^\d+\.\d+$")
    type: str


# ---------------------------------------------------------------------------
# Intent
# ---------------------------------------------------------------------------

class IntentOutput(ArtifactBase):
    type: Literal["intent"] = "intent"
    goal: str = Field(..., min_length=10)
    scope: list[str] = Field(..., min_length=1)
    constraints: list[str] = Field(default_factory=list)
    clarifying_questions: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------

class PlanPhaseTask(BaseModel):
    id: str = Field(..., pattern=TASK_ID_PATTERN)
    description: str = Field(..., min_length=10)
    files: list[str] = Field(default_factory=list)
    acceptance: str | list[str]
    depends_on: list[str] = Field(default_factory=list)
    estimated_complexity: Complexity
    notes: str = ""

    @field_validator("acceptance")
    @classmethod
    def normalize_acceptance(cls, value: str | list[str]) -> str | list[str]:
        if isinstance(value, list):
            cleaned = [item.strip() for item in value if item.strip()]
            if not cleaned:
                raise ValueError("Acceptance criteria list cannot be empty")
            return cleaned
        if len(value.strip()) < 10:
            raise ValueError("Acceptance criteria text must be at least 10 characters")
        return value

    @field_validator("estimated_complexity", mode="before")
    @classmethod
    def normalize_complexity(cls, value):
        if isinstance(value, str):
            return COMPLEXITY_ALIASES.get(value.lower(), value.lower())
        return value


class PlanPhase(BaseModel):
    id: str = Field(..., pattern=r"^phase-\d+$")
    name: str = Field(..., min_length=3)
    description: str
    tasks: list[PlanPhaseTask] = Field(..., min_length=1)


class PlanRisk(BaseModel):
    description: str = Field(..., min_length=10)
    mitigation: str = Field(..., min_length=10)
    severity: Complexity  # low/medium/high


class PlanOutput(ArtifactBase):
    type: Literal["plan"] = "plan"
    summary: str = Field(..., min_length=20)
    phases: list[PlanPhase] = Field(..., min_length=1)
    risks: list[PlanRisk] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    perspective_analysis: dict[str, str] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------

class FileChange(BaseModel):
    path: str
    action: Literal["created", "modified", "deleted"]
    description: str


class Commit(BaseModel):
    hash: str
    message: str


class ExecutionOutput(ArtifactBase):
    type: Literal["execution_result"] = "execution_result"
    task_id: str = Field(..., pattern=TASK_ID_PATTERN)
    status: Literal["completed", "blocked", "deviated"]
    summary: str = Field(..., min_length=10)
    files_changed: list[FileChange] = Field(default_factory=list)
    commits: list[Commit] = Field(default_factory=list)
    deviations: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    branch_name: str = ""
    branch_head_commit: str = ""
    task_commit_hash: str = ""
    upstream_branch: str = ""
    upstream_head_commit: str = ""
    branch_pushed: bool = False


# ---------------------------------------------------------------------------
# Task Manifest
# ---------------------------------------------------------------------------

class AcceptanceCriterion(BaseModel):
    id: str = Field(..., min_length=3)
    description: str = Field(..., min_length=10)
    verification: Literal["command", "review"] = "command"
    command: str = ""
    severity: Literal["must_pass", "should_pass", "nice_to_have"] = "must_pass"

    @field_validator("command")
    @classmethod
    def command_required_for_command_checks(cls, v: str, info) -> str:
        command = v.strip()
        if info.data.get("verification") == "command" and not command:
            raise ValueError("Command-based acceptance criteria require a command")
        lowered = command.lower()
        placeholder_tokens = ("todo", "tbd", "n/a", "<command>", "fill me in")
        if info.data.get("verification") == "command" and any(token in lowered for token in placeholder_tokens):
            raise ValueError("Command-based acceptance criteria require a concrete executable command")
        return command


class TaskDefinition(BaseModel):
    id: str = Field(..., pattern=TASK_ID_PATTERN)
    title: str = Field(..., min_length=5)
    description: str = Field(..., min_length=10)
    rationale: str = Field("", description="Why this is a single task and not split further or merged with another")
    files_in_scope: list[str] = Field(default_factory=list)
    acceptance_criteria: list[AcceptanceCriterion] = Field(..., min_length=1)
    depends_on: list[str] = Field(default_factory=list)
    estimated_complexity: Complexity
    quality_tier: Literal["lite", "standard", "thorough"]
    status: Literal["pending", "in_progress", "completed", "stuck", "blocked"] = "pending"
    notes: str = ""

    @field_validator("estimated_complexity", mode="before")
    @classmethod
    def normalize_complexity(cls, value):
        if isinstance(value, str):
            return COMPLEXITY_ALIASES.get(value.lower(), value.lower())
        return value


class TaskManifestOutput(ArtifactBase):
    type: Literal["task_manifest"] = "task_manifest"
    plan_version: int = Field(..., ge=1)
    summary: str = Field(..., min_length=10)
    tasks: list[TaskDefinition] = Field(..., min_length=1)


# ---------------------------------------------------------------------------
# Review
# ---------------------------------------------------------------------------

class ReviewFinding(BaseModel):
    id: str
    severity: ReviewSeverity
    category: ReviewCategory
    file: str
    line_range: str = ""
    description: str = Field(..., min_length=10)
    suggestion: str = ""
    evidence: str = ""


class ReviewOutput(ArtifactBase):
    type: Literal["review"] = "review"
    task_id: str
    verdict: Literal["approve", "request_changes", "reject"]
    confidence: Confidence
    summary: str = Field(..., min_length=10)
    findings: list[ReviewFinding] = Field(default_factory=list)

    @field_validator("findings")
    @classmethod
    def reject_must_have_findings(cls, v: list[ReviewFinding], info) -> list[ReviewFinding]:
        if info.data.get("verdict") == "reject" and not v:
            raise ValueError("Reject verdict must include at least one finding")
        return v


class PlanReviewOutput(ArtifactBase):
    type: Literal["plan_review"] = "plan_review"
    plan_version: int = Field(..., ge=1)
    verdict: Literal["approved", "needs_changes", "rejected"]
    confidence: Confidence
    summary: str = Field(..., min_length=10)
    findings: list[ReviewFinding] = Field(default_factory=list)

    @field_validator("findings")
    @classmethod
    def rejected_or_changed_must_have_findings(cls, v: list[ReviewFinding], info) -> list[ReviewFinding]:
        if info.data.get("verdict") in {"needs_changes", "rejected"} and not v:
            raise ValueError("Non-approved plan reviews must include at least one finding")
        return v


class TaskManifestReviewOutput(ArtifactBase):
    type: Literal["task_manifest_review"] = "task_manifest_review"
    manifest_version: int = Field(..., ge=1)
    verdict: Literal["approved", "needs_changes", "rejected"]
    confidence: Confidence
    summary: str = Field(..., min_length=10)
    findings: list[ReviewFinding] = Field(default_factory=list)

    @field_validator("findings")
    @classmethod
    def rejected_or_changed_must_have_findings(cls, v: list[ReviewFinding], info) -> list[ReviewFinding]:
        if info.data.get("verdict") in {"needs_changes", "rejected"} and not v:
            raise ValueError("Non-approved task-manifest reviews must include at least one finding")
        return v


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

class TestResult(BaseModel):
    name: str
    status: Literal["passed", "failed", "skipped", "error"]
    duration_ms: int = 0
    error_message: str = ""


class TestOutput(ArtifactBase):
    type: Literal["test_result"] = "test_result"
    task_id: str = Field(..., pattern=TASK_ID_PATTERN)
    overall: Literal["pass", "fail", "error"]
    test_results: list[TestResult] = Field(default_factory=list)
    coverage_pct: float = Field(default=0.0, ge=0.0, le=100.0)
    new_tests_added: int = Field(default=0, ge=0)
    regression_failures: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Simplification
# ---------------------------------------------------------------------------

class SimplificationItem(BaseModel):
    file: str
    line: int = 0
    type: SimplificationType
    description: str
    applied: bool = False


class SimplificationOutput(ArtifactBase):
    type: Literal["simplification"] = "simplification"
    mode: Literal["dry_run", "apply"]
    simplifications: list[SimplificationItem] = Field(default_factory=list)
    lines_removed: int = 0
    lines_added: int = 0
    net_reduction: int = 0


# ---------------------------------------------------------------------------
# Gap Detection
# ---------------------------------------------------------------------------

class GapFinding(BaseModel):
    id: str
    severity: GapSeverity
    category: GapCategory
    description: str = Field(..., min_length=10)
    location: str = ""
    recommendation: str = ""


class GapOutput(ArtifactBase):
    type: Literal["gap_report"] = "gap_report"
    verdict: Literal["complete", "gaps_found"]
    gaps: list[GapFinding] = Field(default_factory=list)
    plan_completeness: str = ""
    overall_risk: Complexity = Complexity.LOW

    @field_validator("gaps")
    @classmethod
    def gaps_found_must_have_gaps(cls, v: list[GapFinding], info) -> list[GapFinding]:
        if info.data.get("verdict") == "gaps_found" and not v:
            raise ValueError("gaps_found verdict must include at least one gap")
        return v


# ---------------------------------------------------------------------------
# Documentation
# ---------------------------------------------------------------------------

class DocChange(BaseModel):
    path: str
    action: Literal["updated", "created", "deleted"]
    section: str = ""
    description: str


class DocsReportOutput(ArtifactBase):
    type: Literal["docs_report"] = "docs_report"
    feature: str = ""
    docs_updated: list[DocChange] = Field(default_factory=list)
    docs_created: list[DocChange] = Field(default_factory=list)
    docs_skipped: list[str] = Field(default_factory=list)
    summary: str = Field(..., min_length=10)


# ---------------------------------------------------------------------------
# Schema Registry
# ---------------------------------------------------------------------------

SCHEMAS: dict[str, type[ArtifactBase]] = {
    "intent": IntentOutput,
    "plan": PlanOutput,
    "task_manifest": TaskManifestOutput,
    "plan_review": PlanReviewOutput,
    "task_manifest_review": TaskManifestReviewOutput,
    "execution_result": ExecutionOutput,
    "review": ReviewOutput,
    "test_result": TestOutput,
    "simplification": SimplificationOutput,
    "gap_report": GapOutput,
    "docs_report": DocsReportOutput,
}


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

class ValidationResult(BaseModel):
    valid: bool
    data: Optional[dict] = None
    errors: list[str] = Field(default_factory=list)
    model_used: str = ""


class ArtifactValidator:
    """Validates agent YAML output against Pydantic schemas."""

    def validate_data(self, data: dict[str, Any], expected_type: str) -> ValidationResult:
        data = self._normalize_data(dict(data), expected_type)
        artifact_type = data.get("type", expected_type)
        schema_class = SCHEMAS.get(artifact_type)
        if schema_class is None:
            return ValidationResult(
                valid=False,
                data=data,
                errors=[f"Unknown artifact type: {artifact_type}"],
            )

        if artifact_type != expected_type:
            return ValidationResult(
                valid=False,
                data=data,
                errors=[f"Expected artifact type '{expected_type}', got '{artifact_type}'"],
                model_used=schema_class.__name__,
            )

        try:
            validated = schema_class.model_validate(data)
            semantic_errors = self._semantic_errors(validated.model_dump(mode="json"), expected_type)
            if semantic_errors:
                return ValidationResult(
                    valid=False,
                    data=validated.model_dump(mode="json"),
                    errors=semantic_errors,
                    model_used=schema_class.__name__,
                )
            return ValidationResult(
                valid=True,
                data=validated.model_dump(mode="json"),
                model_used=schema_class.__name__,
            )
        except Exception as e:
            return ValidationResult(
                valid=False,
                data=data,
                errors=[str(e)],
                model_used=schema_class.__name__,
            )

    def validate(self, raw_text: str, expected_type: str) -> ValidationResult:
        """Three-stage validation: YAML extraction -> schema validation -> semantic checks."""
        from .yaml_utils import extract_yaml

        # Stage 1: Extract YAML
        data = extract_yaml(raw_text)
        if data is None:
            return ValidationResult(
                valid=False,
                errors=["Failed to extract YAML from agent output"],
            )

        # Stage 2: Schema validation
        return self.validate_data(data, expected_type)

    def _semantic_errors(self, data: dict[str, Any], expected_type: str) -> list[str]:
        if expected_type != "task_manifest":
            return []

        errors: list[str] = []
        for task in data.get("tasks", []):
            criteria = task.get("acceptance_criteria", [])
            must_pass_commands = [
                criterion for criterion in criteria
                if criterion.get("severity") == "must_pass" and criterion.get("verification") == "command"
            ]
            if not must_pass_commands:
                errors.append(
                    f"Task {task.get('id', '<unknown>')} must include at least one must_pass command-based acceptance criterion"
                )
                continue

            for criterion in must_pass_commands:
                command = criterion.get("command", "").strip()
                if not command:
                    errors.append(
                        f"Task {task.get('id', '<unknown>')} criterion {criterion.get('id', '<unknown>')} is missing its executable command"
                    )

        return errors

    def _normalize_data(self, data: dict[str, Any], expected_type: str) -> dict[str, Any]:
        if "type" not in data and isinstance(data.get("kind"), str):
            data["type"] = data["kind"]

        artifact_type = data.get("type", expected_type)

        if expected_type == "intent":
            scope = data.get("scope")
            if isinstance(scope, dict):
                in_scope = scope.get("in_scope", [])
                out_of_scope = scope.get("out_of_scope", [])
                if isinstance(in_scope, list):
                    data["scope"] = [str(item).strip() for item in in_scope if str(item).strip()]
                constraints = list(data.get("constraints", []))
                if isinstance(out_of_scope, list):
                    constraints.extend(
                        f"Out of scope: {str(item).strip()}"
                        for item in out_of_scope
                        if str(item).strip()
                    )
                data["constraints"] = constraints

        if artifact_type in {"review", "plan_review", "task_manifest_review"}:
            # Normalize numeric confidence → string
            confidence = data.get("confidence")
            if isinstance(confidence, (int, float)):
                if confidence >= 0.85:
                    data["confidence"] = "high"
                elif confidence >= 0.5:
                    data["confidence"] = "medium"
                else:
                    data["confidence"] = "low"
            # Default confidence if missing
            if "confidence" not in data:
                data["confidence"] = "high"
            # Normalize verdict aliases
            verdict = data.get("verdict", "")
            if artifact_type in {"plan_review", "task_manifest_review"}:
                if verdict == "approve":
                    data["verdict"] = "approved"
            # Normalize plan_version from plan_ref
            if artifact_type == "plan_review" and "plan_version" not in data:
                plan_ref = data.get("plan_ref", "")
                import re as _re
                m = _re.search(r"v?(\d+)", str(plan_ref))
                data["plan_version"] = int(m.group(1)) if m else 1
            # Normalize manifest_version
            if artifact_type == "task_manifest_review" and "manifest_version" not in data:
                data["manifest_version"] = 1
            # Default file="" in findings if missing
            for finding in data.get("findings", []):
                if isinstance(finding, dict) and "file" not in finding:
                    finding["file"] = ""

        if artifact_type == "gap_report":
            # Infer verdict from gaps list if missing
            if "verdict" not in data:
                data["verdict"] = "gaps_found" if data.get("gaps") else "complete"

        if artifact_type == "docs_report":
            # Default summary from feature/description if missing
            if "summary" not in data:
                data["summary"] = data.get("feature", "Documentation update completed")
                if len(data["summary"]) < 10:
                    data["summary"] = "Documentation update completed"

        if artifact_type == "execution_result":
            # Normalize status aliases
            status = data.get("status", "")
            if status == "success":
                data["status"] = "completed"
            # Default description="" in files_changed
            for fc in data.get("files_changed", []):
                if isinstance(fc, dict) and "description" not in fc:
                    fc["description"] = ""
            # Coerce null strings to empty
            for key in ("upstream_branch", "upstream_head_commit", "branch_name", "branch_head_commit", "task_commit_hash"):
                if data.get(key) is None:
                    data[key] = ""

        if artifact_type == "task_manifest":
            # Default plan_version if missing
            if "plan_version" not in data:
                data["plan_version"] = 1
            # Default summary from first task title
            if "summary" not in data and data.get("tasks"):
                first_title = data["tasks"][0].get("title", "Task execution")
                data["summary"] = f"Execute: {first_title}"
            # Auto-generate acceptance_criteria IDs if missing
            for task in data.get("tasks", []):
                for i, ac in enumerate(task.get("acceptance_criteria", [])):
                    if isinstance(ac, dict) and "id" not in ac:
                        ac["id"] = f"ac-{i + 1:02d}"
                # Default quality_tier
                if "quality_tier" not in task:
                    task["quality_tier"] = "lite"

        if artifact_type == "plan":
            # Normalize goal → summary
            if "summary" not in data and "goal" in data:
                data["summary"] = data.pop("goal")
            # Normalize flat tasks list → single phase wrapper
            if "phases" not in data and "tasks" in data:
                data["phases"] = [{
                    "id": "phase-1",
                    "name": "Implementation",
                    "description": data.get("summary", "Execute all tasks"),
                    "tasks": data.pop("tasks"),
                }]
            for phase in data.get("phases", []):
                for task in phase.get("tasks", []):
                    # Normalize common field name variants
                    if "acceptance" not in task and "acceptance_criteria" in task:
                        task["acceptance"] = task.pop("acceptance_criteria")
                    if "estimated_complexity" not in task and "complexity" in task:
                        task["estimated_complexity"] = task.pop("complexity")
                    if "files" not in task and "files_to_modify" in task:
                        files_to_modify = task.pop("files_to_modify")
                        task["files"] = [f.get("path", f) if isinstance(f, dict) else f for f in files_to_modify]
                    if "files" not in task and "files_in_scope" in task:
                        task["files"] = task.pop("files_in_scope")
                    acceptance = task.get("acceptance")
                    if isinstance(acceptance, list):
                        normalized_items: list[str] = []
                        for item in acceptance:
                            if isinstance(item, str):
                                text = item.strip()
                            elif isinstance(item, dict):
                                text = "; ".join(
                                    f"{str(key).strip()}: {str(value).strip()}".strip(": ")
                                    for key, value in item.items()
                                    if str(key).strip() or str(value).strip()
                                )
                            else:
                                text = str(item).strip()
                            if text:
                                normalized_items.append(text)
                        task["acceptance"] = normalized_items

        return data

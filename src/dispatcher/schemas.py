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
        import re as _re
        command = v.strip()
        if info.data.get("verification") == "command" and not command:
            raise ValueError("Command-based acceptance criteria require a command")
        lowered = command.lower()
        # Word-boundary match to avoid false positives like "bin/activate" matching "n/a"
        placeholder_patterns = (r"\btodo\b", r"\btbd\b", r"\bn/a\b", r"<command>", r"fill me in")
        if info.data.get("verification") == "command" and any(
            _re.search(pat, lowered) for pat in placeholder_patterns
        ):
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
    file: str = ""
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
        """Normalize agent-produced YAML to match schema field names and types.

        Agents frequently use natural field names (goal, title, description)
        instead of schema names (summary), produce dicts where strings are
        expected, use verdict aliases (approve vs approved), omit fields that
        have sensible defaults, and write null instead of empty string. This
        method bridges every known gap so the schemas can stay strict.
        """
        import re as _re

        # ── Global: type/kind alias ──────────────────────────────────────
        if "type" not in data and isinstance(data.get("kind"), str):
            data["type"] = data["kind"]
        # Common type name mismatches
        _TYPE_ALIASES = {
            "task_review": "task_manifest_review",
            "manifest_review": "task_manifest_review",
            "execution_output": "execution_result",
            "execution": "execution_result",
            "test": "test_result",
            "test_output": "test_result",
            "gap": "gap_report",
            "gaps": "gap_report",
            "docs": "docs_report",
            "documentation": "docs_report",
            "simplify": "simplification",
            "simplification_output": "simplification",
        }
        if data.get("type") in _TYPE_ALIASES:
            data["type"] = _TYPE_ALIASES[data["type"]]
        artifact_type = data.get("type", expected_type)

        # ── Coerce null → "" for all top-level string fields ─────────────
        _null_to_empty_str(data, ("summary", "feature", "plan_completeness",
                                  "branch_name", "branch_head_commit",
                                  "task_commit_hash", "upstream_branch",
                                  "upstream_head_commit", "task_id", "notes"))

        # ── Intent ───────────────────────────────────────────────────────
        if artifact_type == "intent":
            # scope as {in_scope, out_of_scope} dict → flat list + constraints
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
                        for item in out_of_scope if str(item).strip()
                    )
                    data["constraints"] = constraints
            # summary/description/title → goal
            if "goal" not in data:
                for alt in ("summary", "description", "title"):
                    if alt in data:
                        data["goal"] = data[alt]
                        break
            # Ensure lists default
            data.setdefault("constraints", [])
            data.setdefault("clarifying_questions", [])
            # clarifying_questions may be dicts
            _list_of_dicts_to_strings(data, "clarifying_questions", "question")

        # ── All review types ─────────────────────────────────────────────
        if artifact_type in {"review", "plan_review", "task_manifest_review"}:
            # confidence: numeric → string, missing → "high"
            confidence = data.get("confidence")
            if isinstance(confidence, (int, float)):
                data["confidence"] = "high" if confidence >= 0.85 else ("medium" if confidence >= 0.5 else "low")
            if "confidence" not in data or data["confidence"] is None:
                data["confidence"] = "high"

            # summary aliases: description → summary
            if "summary" not in data:
                for alt in ("description", "overview", "comment"):
                    if alt in data and isinstance(data[alt], str) and len(data[alt]) >= 10:
                        data["summary"] = data[alt]
                        break

            # findings normalization
            for finding in data.get("findings", []):
                if not isinstance(finding, dict):
                    continue
                _null_to_empty_str(finding, ("file", "line_range", "suggestion", "evidence"))
                # Default missing fields
                finding.setdefault("file", "")
                finding.setdefault("line_range", "")
                finding.setdefault("suggestion", "")
                finding.setdefault("evidence", "")
                # Auto-generate finding IDs
                if "id" not in finding:
                    finding["id"] = f"f-{id(finding) % 10000:04d}"
                # location → file fallback
                if not finding.get("file") and finding.get("location"):
                    finding["file"] = finding["location"]
                # severity aliases
                _FINDING_SEV_ALIASES = {"blocking": "critical", "high": "critical",
                                        "severe": "critical", "error": "critical",
                                        "medium": "major", "moderate": "major",
                                        "warning": "minor", "low": "minor",
                                        "info": "nit", "suggestion": "nit",
                                        "trivial": "nit", "nitpick": "nit"}
                sev = finding.get("severity", "")
                if isinstance(sev, str):
                    finding["severity"] = _FINDING_SEV_ALIASES.get(sev.lower(), sev.lower())
                # category aliases
                _FINDING_CAT_ALIASES = {"bug": "correctness", "logic": "correctness",
                                        "correct": "correctness",
                                        "missing": "completeness", "incomplete": "completeness",
                                        "scope": "completeness",
                                        "sec": "security", "vulnerability": "security",
                                        "auth": "security",
                                        "perf": "performance", "speed": "performance",
                                        "formatting": "style", "naming": "style",
                                        "convention": "style", "lint": "style",
                                        "design": "architecture", "structure": "architecture",
                                        "test": "testability", "testing": "testability",
                                        "duplicate": "reuse", "duplication": "reuse",
                                        "redundant": "efficiency", "waste": "efficiency"}
                cat = finding.get("category", "")
                if isinstance(cat, str):
                    finding["category"] = _FINDING_CAT_ALIASES.get(cat.lower(), cat.lower())

            # Verdict aliases
            verdict = data.get("verdict", "")
            if artifact_type in {"plan_review", "task_manifest_review"}:
                _PLAN_VERDICT_ALIASES = {"approve": "approved", "accepted": "approved",
                                         "reject": "rejected", "changes_needed": "needs_changes",
                                         "needs_revision": "needs_changes", "revise": "needs_changes"}
                data["verdict"] = _PLAN_VERDICT_ALIASES.get(verdict, verdict)
            if artifact_type == "review":
                _REVIEW_VERDICT_ALIASES = {"approved": "approve", "pass": "approve",
                                           "accepted": "approve", "reject": "request_changes",
                                           "rejected": "reject", "changes_needed": "request_changes",
                                           "needs_changes": "request_changes"}
                data["verdict"] = _REVIEW_VERDICT_ALIASES.get(verdict, verdict)

            # plan_version / manifest_version inference
            if artifact_type == "plan_review" and "plan_version" not in data:
                plan_ref = str(data.get("plan_ref", data.get("plan", "")))
                m = _re.search(r"v?(\d+)", plan_ref)
                data["plan_version"] = int(m.group(1)) if m else 1
            if artifact_type == "task_manifest_review" and "manifest_version" not in data:
                mref = str(data.get("manifest_ref", data.get("manifest", "")))
                m = _re.search(r"v?(\d+)", mref)
                data["manifest_version"] = int(m.group(1)) if m else 1

            # task_id default for code reviews
            if artifact_type == "review" and "task_id" not in data:
                data["task_id"] = data.get("task", "")

        # ── Plan ─────────────────────────────────────────────────────────
        if artifact_type == "plan":
            # goal/title/description → summary
            if "summary" not in data:
                for alt in ("goal", "description", "title", "overview", "plan_summary",
                             "objective", "purpose", "name"):
                    if alt in data and isinstance(data[alt], str) and len(data[alt]) >= 20:
                        data["summary"] = data.pop(alt)
                        break
            # Last-resort summary from first phase description
            if "summary" not in data:
                phases = data.get("phases", data.get("tasks", []))
                if phases and isinstance(phases, list) and isinstance(phases[0], dict):
                    desc = phases[0].get("description", phases[0].get("name", ""))
                    if isinstance(desc, str) and len(desc) >= 20:
                        data["summary"] = desc

            # open_questions: dicts → strings, null → []
            if data.get("open_questions") is None:
                data["open_questions"] = []
            _list_of_dicts_to_strings(data, "open_questions", "question")

            # risks: dicts with wrong shape → normalize, null → []
            if data.get("risks") is None:
                data["risks"] = []
            for risk in data.get("risks", []):
                if isinstance(risk, dict):
                    _rename_key(risk, "risk", "description")
                    _rename_key(risk, "name", "description")
                    _rename_key(risk, "title", "description")
                    _null_to_empty_str(risk, ("description", "mitigation"))
                    if "severity" not in risk:
                        risk["severity"] = "low"

            # perspective_analysis: coerce non-string values → strings
            pa = data.get("perspective_analysis")
            if isinstance(pa, dict):
                for key, val in pa.items():
                    if isinstance(val, list):
                        pa[key] = "; ".join(str(v) for v in val)
                    elif isinstance(val, dict):
                        # Extract 'analysis' key if present, else stringify
                        pa[key] = val.get("analysis", val.get("description",
                                   "; ".join(f"{k}: {v}" for k, v in val.items()
                                             if k != "applicable")))

            # Flat tasks → single phase wrapper
            if "phases" not in data and "tasks" in data:
                data["phases"] = [{
                    "id": "phase-1",
                    "name": "Implementation",
                    "description": data.get("summary", "Execute all tasks"),
                    "tasks": data.pop("tasks"),
                }]
            # Normalize tasks within phases
            for phase in data.get("phases", []):
                if not isinstance(phase, dict):
                    continue
                for task in phase.get("tasks", []):
                    if not isinstance(task, dict):
                        continue
                    # Field renames
                    _rename_key(task, "acceptance_criteria", "acceptance")
                    _rename_key(task, "complexity", "estimated_complexity")
                    if "files" not in task:
                        for alt in ("files_to_modify", "files_in_scope", "files_changed"):
                            if alt in task:
                                task["files"] = task.pop(alt)
                                break
                    # files items: coerce dicts → path strings
                    if "files" in task and isinstance(task["files"], list):
                        task["files"] = [
                            f.get("path", str(f)) if isinstance(f, dict) else str(f)
                            for f in task["files"]
                        ]
                    if "description" not in task and "title" in task:
                        task["description"] = task["title"]
                    # notes: coerce list → joined string
                    notes = task.get("notes")
                    if isinstance(notes, list):
                        task["notes"] = "; ".join(str(n) for n in notes)
                    # acceptance list normalization
                    acceptance = task.get("acceptance")
                    if isinstance(acceptance, list):
                        task["acceptance"] = _flatten_acceptance_list(acceptance)

        # ── Task Manifest ────────────────────────────────────────────────
        if artifact_type == "task_manifest":
            data.setdefault("plan_version", 1)
            if "summary" not in data:
                for alt in ("description", "title", "overview"):
                    if alt in data and isinstance(data[alt], str) and len(data[alt]) >= 10:
                        data["summary"] = data[alt]
                        break
            if "summary" not in data and data.get("tasks"):
                data["summary"] = f"Execute: {data['tasks'][0].get('title', 'Task execution')}"

            for task in data.get("tasks", []):
                if not isinstance(task, dict):
                    continue
                # Auto-generate acceptance_criteria IDs
                for i, ac in enumerate(task.get("acceptance_criteria", [])):
                    if isinstance(ac, dict):
                        ac.setdefault("id", f"ac-{i + 1:02d}")
                        _null_to_empty_str(ac, ("command", "description"))
                        # String acceptance criteria → structured
                    elif isinstance(ac, str):
                        task["acceptance_criteria"][i] = {
                            "id": f"ac-{i + 1:02d}",
                            "description": ac,
                            "verification": "command" if _looks_like_command(ac) else "review",
                            "command": ac if _looks_like_command(ac) else "",
                            "severity": "must_pass",
                        }
                # files_in_scope items: coerce dicts → path strings
                fis = task.get("files_in_scope")
                if isinstance(fis, list):
                    task["files_in_scope"] = [
                        f.get("path", str(f)) if isinstance(f, dict) else str(f)
                        for f in fis
                    ]
                # Defaults
                task.setdefault("quality_tier", "lite")
                task.setdefault("rationale", "")
                task.setdefault("files_in_scope", [])
                task.setdefault("depends_on", [])
                # notes: coerce list → string, then default
                notes = task.get("notes")
                if isinstance(notes, list):
                    task["notes"] = "; ".join(str(n) for n in notes)
                task.setdefault("notes", "")
                # Complexity aliases
                _rename_key(task, "complexity", "estimated_complexity")

        # ── Execution Result ─────────────────────────────────────────────
        if artifact_type == "execution_result":
            # Status aliases
            _STATUS_ALIASES = {"success": "completed", "done": "completed",
                               "failed": "blocked", "error": "blocked"}
            data["status"] = _STATUS_ALIASES.get(data.get("status", ""), data.get("status", ""))
            # summary fallback
            if "summary" not in data:
                for alt in ("description", "message", "result"):
                    if alt in data and isinstance(data[alt], str) and len(data[alt]) >= 10:
                        data["summary"] = data[alt]
                        break
            if "summary" not in data:
                data["summary"] = f"Task {data.get('task_id', '?')} {data.get('status', 'unknown')}"
            # files_changed normalization
            for fc in data.get("files_changed", []):
                if isinstance(fc, dict):
                    _null_to_empty_str(fc, ("description", "path"))
                    # action aliases
                    action = fc.get("action", "")
                    _ACTION_ALIASES = {"added": "created", "new": "created",
                                       "changed": "modified", "edited": "modified",
                                       "updated": "modified", "removed": "deleted"}
                    fc["action"] = _ACTION_ALIASES.get(action, action)
            # commits normalization
            for commit in data.get("commits", []):
                if isinstance(commit, dict):
                    _null_to_empty_str(commit, ("hash", "message"))
                    _rename_key(commit, "sha", "hash")
                    _rename_key(commit, "commit_hash", "hash")
            # Null string fields → ""
            for key in ("upstream_branch", "upstream_head_commit", "branch_name",
                        "branch_head_commit", "task_commit_hash"):
                if data.get(key) is None:
                    data[key] = ""

        # ── Test Result ──────────────────────────────────────────────────
        if artifact_type == "test_result":
            # overall aliases
            _OVERALL_ALIASES = {"passed": "pass", "success": "pass",
                                "failed": "fail", "failure": "fail"}
            data["overall"] = _OVERALL_ALIASES.get(data.get("overall", ""), data.get("overall", ""))
            # test_results status aliases
            for tr in data.get("test_results", []):
                if isinstance(tr, dict):
                    _TR_STATUS_ALIASES = {"pass": "passed", "success": "passed",
                                          "fail": "failed", "failure": "failed",
                                          "skip": "skipped", "ok": "passed"}
                    tr["status"] = _TR_STATUS_ALIASES.get(tr.get("status", ""), tr.get("status", ""))
                    # duration_ms: coerce from string/float
                    dur = tr.get("duration_ms")
                    if isinstance(dur, str):
                        tr["duration_ms"] = int("".join(c for c in dur if c.isdigit()) or "0")
                    elif isinstance(dur, float):
                        tr["duration_ms"] = int(dur)
                    _null_to_empty_str(tr, ("error_message",))
            # coverage_pct: coerce from string
            cov = data.get("coverage_pct")
            if isinstance(cov, str):
                data["coverage_pct"] = float("".join(c for c in cov if c.isdigit() or c == ".") or "0")
            # null defaults
            data.setdefault("test_results", [])
            data.setdefault("regression_failures", [])
            if data.get("regression_failures") is None:
                data["regression_failures"] = []

        # ── Simplification ──────────────────────────────────────────────
        if artifact_type == "simplification":
            # mode aliases
            _MODE_ALIASES = {"dry-run": "dry_run", "dryrun": "dry_run",
                             "dryRun": "dry_run", "dry": "dry_run"}
            data["mode"] = _MODE_ALIASES.get(data.get("mode", ""), data.get("mode", ""))
            # simplifications items
            for item in data.get("simplifications", []):
                if isinstance(item, dict):
                    # type case-folding and aliases
                    stype = item.get("type", "")
                    if isinstance(stype, str):
                        _STYPE_ALIASES = {"remove-dead": "remove_dead", "dead_code": "remove_dead",
                                          "reuse-existing": "reuse_existing", "reuse": "reuse_existing",
                                          "deduplicate": "dedup", "duplicate": "dedup",
                                          "inline": "flatten", "simplify": "flatten"}
                        item["type"] = _STYPE_ALIASES.get(stype.lower(), stype.lower())
            data.setdefault("simplifications", [])

        # ── Gap Report ───────────────────────────────────────────────────
        if artifact_type == "gap_report":
            # Infer verdict from gaps list
            if "verdict" not in data:
                data["verdict"] = "gaps_found" if data.get("gaps") else "complete"
            # Verdict aliases
            _GAP_VERDICT_ALIASES = {"no_gaps": "complete", "none": "complete",
                                    "pass": "complete", "clean": "complete",
                                    "found": "gaps_found", "incomplete": "gaps_found"}
            data["verdict"] = _GAP_VERDICT_ALIASES.get(data.get("verdict", ""), data.get("verdict", ""))
            # gaps null → []
            if data.get("gaps") is None:
                data["gaps"] = []
            for gap in data.get("gaps", []):
                if isinstance(gap, dict):
                    _null_to_empty_str(gap, ("location", "recommendation", "description"))
                    gap.setdefault("id", f"G-{id(gap) % 10000:04d}")
                    # severity aliases
                    _GAP_SEV_ALIASES = {"high": "critical", "blocking": "critical",
                                        "medium": "major", "moderate": "major",
                                        "low": "minor", "trivial": "minor", "nit": "minor"}
                    sev = gap.get("severity", "")
                    if isinstance(sev, str):
                        gap["severity"] = _GAP_SEV_ALIASES.get(sev.lower(), sev.lower())
                    # category aliases
                    _GAP_CAT_ALIASES = {"coverage": "plan-coverage", "spec": "plan-coverage",
                                        "spec-coverage": "plan-coverage", "plan_coverage": "plan-coverage",
                                        "error": "error-handling", "errors": "error-handling",
                                        "error_handling": "error-handling",
                                        "edge": "edge-case", "edge_case": "edge-case",
                                        "docs": "documentation", "doc": "documentation",
                                        "migrate": "migration", "data": "migration",
                                        "integrate": "integration", "api": "integration"}
                    cat = gap.get("category", "")
                    if isinstance(cat, str):
                        gap["category"] = _GAP_CAT_ALIASES.get(cat.lower(), cat.lower())

        # ── Docs Report ──────────────────────────────────────────────────
        if artifact_type == "docs_report":
            if "summary" not in data:
                for alt in ("description", "overview", "message"):
                    if alt in data and isinstance(data[alt], str) and len(data[alt]) >= 10:
                        data["summary"] = data[alt]
                        break
            if "summary" not in data:
                data["summary"] = data.get("feature") or "Documentation update completed"
                if len(data["summary"]) < 10:
                    data["summary"] = "Documentation update completed"
            # null lists → []
            for key in ("docs_updated", "docs_created", "docs_skipped"):
                if data.get(key) is None:
                    data[key] = []
            # Normalize doc changes
            _DOC_ACTION_ALIASES = {"modified": "updated", "changed": "updated",
                                   "edited": "updated", "added": "created",
                                   "new": "created", "removed": "deleted"}
            for key in ("docs_updated", "docs_created"):
                for dc in data.get(key, []):
                    if isinstance(dc, dict):
                        _null_to_empty_str(dc, ("section", "description", "path"))
                        if "action" not in dc:
                            dc["action"] = "updated" if key == "docs_updated" else "created"
                        else:
                            dc["action"] = _DOC_ACTION_ALIASES.get(dc["action"], dc["action"])
            # docs_skipped items may be dicts
            _list_of_dicts_to_strings(data, "docs_skipped", "path")

        return data


# ── Normalization helpers ────────────────────────────────────────────────────

def _null_to_empty_str(d: dict, keys: tuple[str, ...]) -> None:
    """Coerce None values to empty string for specified keys."""
    for k in keys:
        if k in d and d[k] is None:
            d[k] = ""


def _rename_key(d: dict, old: str, new: str) -> None:
    """Move d[old] → d[new] if old exists and new doesn't."""
    if new not in d and old in d:
        d[new] = d.pop(old)


def _list_of_dicts_to_strings(data: dict, field: str, text_key: str) -> None:
    """Convert a list of dicts to a list of strings by extracting text_key."""
    items = data.get(field, [])
    if items and isinstance(items[0], dict):
        data[field] = [
            item.get(text_key, str(item)) if isinstance(item, dict) else str(item)
            for item in items
        ]


def _flatten_acceptance_list(items: list) -> list[str]:
    """Flatten mixed acceptance criteria (strings and dicts) to a string list."""
    result: list[str] = []
    for item in items:
        if isinstance(item, str):
            text = item.strip()
        elif isinstance(item, dict):
            text = "; ".join(
                f"{str(k).strip()}: {str(v).strip()}".strip(": ")
                for k, v in item.items() if str(k).strip() or str(v).strip()
            )
        else:
            text = str(item).strip()
        if text:
            result.append(text)
    return result


def _looks_like_command(text: str) -> bool:
    """Heuristic: does this acceptance criterion text look like a shell command?"""
    cmd_prefixes = ("python", "npm", "node", "go ", "cargo", "make", "bash",
                    "sh ", "curl", "docker", "git ", "pytest", "jest", "mvn",
                    "gradle", "ruby", "php", "java ", "dotnet", "test ", "ls ")
    lowered = text.lower().strip()
    return any(lowered.startswith(p) for p in cmd_prefixes)

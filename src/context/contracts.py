"""Contract generation from canonical Pydantic schemas.

Generates compact, readable contract blocks for agent prompts,
replacing hand-maintained schema hints that drift over time.
"""

from __future__ import annotations

import enum
import hashlib
import typing
from typing import Any, Literal, Union, get_args, get_origin

from pydantic import BaseModel
from pydantic.fields import FieldInfo


def _field_type_str(annotation: Any) -> str:
    """Convert a type annotation to a compact contract string."""
    origin = get_origin(annotation)
    args = get_args(annotation)

    if annotation is str:
        return "string"
    if annotation is int:
        return "integer"
    if annotation is float:
        return "number"
    if annotation is bool:
        return "boolean"
    if origin is type(None):
        return "null"
    if origin is Literal:
        return " | ".join(str(a) for a in args)
    if origin is list:
        return f"list[{_field_type_str(args[0])}]" if args else "list"
    if origin is dict:
        if args and len(args) == 2:
            return f"dict[{_field_type_str(args[0])}, {_field_type_str(args[1])}]"
        return "dict"
    if origin is Union:
        non_none = [a for a in args if a is not type(None)]
        if len(non_none) == 1:
            return _field_type_str(non_none[0])
        return " | ".join(_field_type_str(a) for a in non_none)
    if isinstance(annotation, type) and issubclass(annotation, enum.Enum):
        return " | ".join(e.value for e in annotation)
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation.__name__

    return str(annotation)


def _field_constraints(info: FieldInfo) -> list[str]:
    """Extract constraint strings from a Pydantic FieldInfo."""
    constraints = []
    metadata = info.metadata or []
    for m in metadata:
        if hasattr(m, "min_length") and m.min_length is not None:
            constraints.append(f"min_length={m.min_length}")
        if hasattr(m, "ge") and m.ge is not None:
            constraints.append(f">= {m.ge}")
        if hasattr(m, "pattern") and m.pattern:
            constraints.append(f"pattern {m.pattern}")

    # Check FieldInfo directly for common constraints
    if hasattr(info, "pattern") and info.pattern:
        constraints.append(f"pattern {info.pattern}")
    json_schema = info.json_schema_extra or {}
    if isinstance(json_schema, dict):
        if "minLength" in json_schema:
            constraints.append(f"min_length={json_schema['minLength']}")
        if "pattern" in json_schema:
            constraints.append(f"pattern {json_schema['pattern']}")

    return constraints


def build_field_contract(schema_class: type[BaseModel], indent: int = 0) -> str:
    """Generate a compact field contract block from a Pydantic model.

    Example output:
        type: task_manifest
        plan_version: integer >= 1
        summary: string min_length=10
        tasks:
          - id: string pattern ^task-[A-Z]?\\d{3}$
    """
    lines = []
    prefix = "  " * indent
    for name, info in schema_class.model_fields.items():
        annotation = info.annotation
        type_str = _field_type_str(annotation)
        constraints = _field_constraints(info)

        # Check for nested models
        inner_model = None
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            inner_model = annotation
        elif get_origin(annotation) is list:
            inner_args = get_args(annotation)
            if inner_args and isinstance(inner_args[0], type) and issubclass(inner_args[0], BaseModel):
                inner_model = inner_args[0]

        constraint_str = " " + " ".join(constraints) if constraints else ""

        if inner_model and get_origin(annotation) is list:
            lines.append(f"{prefix}{name}:{constraint_str}")
            lines.append(f"{prefix}  - (each {inner_model.__name__}):")
            lines.append(build_field_contract(inner_model, indent + 2))
        elif inner_model:
            lines.append(f"{prefix}{name}:{constraint_str}")
            lines.append(build_field_contract(inner_model, indent + 1))
        else:
            lines.append(f"{prefix}{name}: {type_str}{constraint_str}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Semantic rules
# ---------------------------------------------------------------------------

# Registry of semantic rules per artifact type
_SEMANTIC_RULES: dict[str, list[str]] = {
    "task_manifest": [
        "every task must include at least one must_pass criterion using verification: command",
        "every command criterion must contain a concrete executable command (no placeholders)",
        "task IDs must be unique and match pattern ^task-[A-Z]?\\d{3}$",
        "quality_tier must be one of: lite | standard | thorough",
        "do not output prose outside YAML",
    ],
    "plan": [
        "phases must be sequentially numbered (phase-1, phase-2, ...)",
        "task IDs must be globally unique across all phases",
        "each task must reference at least one acceptance criterion",
        "risks must include both description and mitigation",
        "do not output prose outside YAML",
    ],
    "plan_review": [
        "verdict must be: approved | needs_changes | rejected",
        "non-approved verdicts must include at least one finding",
        "findings must have severity (critical|major|minor|nit) and category",
        "do not output prose outside YAML",
    ],
    "task_manifest_review": [
        "verdict must be: approved | needs_changes | rejected",
        "non-approved verdicts must include at least one finding",
        "reject if any task lacks a must_pass command-based acceptance criterion",
        "do not output prose outside YAML",
    ],
    "review": [
        "verdict must be: approve | request_changes | reject",
        "reject verdict must include at least one finding",
        "findings must have severity and category",
        "do not output prose outside YAML",
    ],
    "execution_result": [
        "status must be: completed | blocked | deviated",
        "report all commit hashes created",
        "list all files changed with action (created|modified|deleted)",
        "do not output prose outside YAML",
    ],
    "gap_report": [
        "verdict must be: complete | gaps_found",
        "gaps_found verdict must include at least one gap",
        "each gap must have severity (critical|major|minor) and category",
        "do not output prose outside YAML",
    ],
    "docs_report": [
        "list all docs updated and created",
        "do not output prose outside YAML",
    ],
    "intent": [
        "scope must be a flat list of strings",
        "goal must be at least 10 characters",
        "do not output prose outside YAML",
    ],
}


def build_semantic_rules(artifact_type: str) -> str:
    """Generate a semantic rules block for an artifact type."""
    rules = _SEMANTIC_RULES.get(artifact_type, [])
    if not rules:
        return ""
    lines = ["Semantic rules:"]
    for rule in rules:
        lines.append(f"  - {rule}")
    return "\n".join(lines)


def build_contract_block(schema_class: type[BaseModel], artifact_type: str) -> str:
    """Generate the complete contract block: field contract + semantic rules."""
    parts = ["Output contract:"]
    parts.append(build_field_contract(schema_class, indent=1))
    semantic = build_semantic_rules(artifact_type)
    if semantic:
        parts.append("")
        parts.append(semantic)
    return "\n".join(parts)


def contract_fingerprint(schema_class: type[BaseModel], artifact_type: str) -> str:
    """Generate a stable fingerprint for a contract block."""
    block = build_contract_block(schema_class, artifact_type)
    return hashlib.sha256(block.encode()).hexdigest()[:12]

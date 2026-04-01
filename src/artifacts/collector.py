"""Gather and organize agent outputs."""

from pathlib import Path
from typing import Optional
from ..dispatcher.schemas import ArtifactValidator, ValidationResult


class ArtifactCollector:
    """Collects and validates agent outputs."""

    def __init__(self, validator: ArtifactValidator):
        self.validator = validator

    def collect_and_validate(
        self, raw_text: str, expected_type: str
    ) -> ValidationResult:
        return self.validator.validate(raw_text, expected_type)

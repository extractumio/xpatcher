"""Lane-scoped session management for v2 pipeline.

Each lane is a named conversation domain with its own session policy.
Lanes isolate unrelated artifact families while preserving continuity
where it is genuinely useful (e.g., plan author/fix iterations).
"""

from __future__ import annotations

import dataclasses
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .yaml_utils import load_yaml_file, save_yaml_file, now_iso


# ---------------------------------------------------------------------------
# Stage-to-lane mapping
# ---------------------------------------------------------------------------

STAGE_LANE_MAP: dict[str, str] = {
    "intent_capture": "intent_author",
    "planning": "spec_author",
    "plan_fix": "spec_author",
    "plan_review": "spec_review",
    "plan_approval": "spec_author",
    "task_breakdown": "manifest_author",
    "task_fix": "manifest_author",
    "task_review": "manifest_review",
    "prioritization": "manifest_author",
    "execution_graph": "manifest_author",
    "task_execution": "task_exec",  # :<task_id> suffix added at runtime
    "per_task_quality": "task_exec",
    "fix_iteration": "task_exec",
    "gap_detection": "gap_analysis",
    "documentation": "docs",
    "completion": "docs",
}

LANE_AGENT_MAP: dict[str, str] = {
    "intent_author": "planner",
    "spec_author": "planner",
    "spec_review": "plan-reviewer",
    "manifest_author": "planner",
    "manifest_review": "plan-reviewer",
    "task_exec": "executor",
    "gap_analysis": "gap-detector",
    "docs": "tech-writer",
}

DEFAULT_ROTATION_LIMITS: dict[str, int] = {
    "intent_author": 2,
    "spec_author": 8,
    "spec_review": 3,
    "manifest_author": 6,
    "manifest_review": 3,
    "task_exec": 20,
    "gap_analysis": 5,
    "docs": 2,
}


@dataclass
class LaneState:
    """State of a single lane."""
    lane_name: str
    agent: str = ""
    session_id: str = ""
    resume_enabled: bool = True
    invocation_count: int = 0
    max_invocations_before_rotate: int = 8
    total_cost_usd: float = 0.0
    last_stage: str = ""
    created_at: str = ""
    last_used_at: str = ""
    rotated_at: Optional[str] = None
    context_refs: list[str] = field(default_factory=list)
    status: str = "active"

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> LaneState:
        known = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})


class LaneManager:
    """Manages session continuity by lane.

    Resolves lane names for stages, creates/persists lane sessions,
    handles rotation policy, and exposes lane telemetry.
    """

    def __init__(self, feature_dir: Path, config: dict | None = None):
        self.feature_dir = feature_dir
        self._lanes: dict[str, LaneState] = {}
        self._config = config or {}
        self._lanes_dir = feature_dir / "lanes"
        self._lanes_dir.mkdir(parents=True, exist_ok=True)
        self._load()

    def lane_for_stage(self, stage: str, task_id: str = "") -> str:
        """Determine lane name for a stage, with optional task_id suffix."""
        base = STAGE_LANE_MAP.get(stage, "spec_author")
        if base == "task_exec" and task_id:
            return f"task_exec:{task_id}"
        return base

    def agent_for_lane(self, lane_name: str) -> str:
        """Return the agent name appropriate for a lane."""
        return LANE_AGENT_MAP.get(lane_name.split(":")[0], "planner")

    def resolve_session(self, stage: str, task_id: str = "") -> tuple[str, bool]:
        """Resolve session ID and resume flag for a stage."""
        lane_name = self.lane_for_stage(stage, task_id)
        lane = self._get_or_create_lane(lane_name)

        if self._should_rotate(lane):
            self._rotate(lane)

        is_resume = lane.invocation_count > 0 and lane.resume_enabled
        lane.invocation_count += 1
        lane.last_stage = stage
        lane.last_used_at = now_iso()
        self._save_lane(lane)
        return lane.session_id, is_resume

    def record_cost(self, stage: str, task_id: str, cost_usd: float) -> None:
        """Record cost against the lane for a stage."""
        lane_name = self.lane_for_stage(stage, task_id)
        if lane_name in self._lanes:
            self._lanes[lane_name].total_cost_usd += cost_usd
            self._save_lane(self._lanes[lane_name])

    def rotate_lane(self, stage: str, task_id: str = "") -> str:
        """Force-rotate a lane session and return the new session ID."""
        lane = self._get_or_create_lane(self.lane_for_stage(stage, task_id))
        self._rotate(lane)
        return lane.session_id

    def get_lane_state(self, lane_name: str) -> LaneState | None:
        return self._lanes.get(lane_name)

    def get_all_lane_states(self) -> dict[str, dict]:
        """Return all lane states as a dict for persistence in pipeline-state.yaml."""
        return {name: lane.to_dict() for name, lane in self._lanes.items()}

    def _get_or_create_lane(self, lane_name: str) -> LaneState:
        if lane_name in self._lanes:
            return self._lanes[lane_name]

        base = lane_name.split(":")[0]
        lane_config = self._config.get("lanes", {}).get(base, {})
        now = now_iso()
        lane = LaneState(
            lane_name=lane_name,
            agent=LANE_AGENT_MAP.get(base, "planner"),
            session_id=str(uuid.uuid4()),
            resume_enabled=base != "docs",
            max_invocations_before_rotate=lane_config.get(
                "max_invocations", DEFAULT_ROTATION_LIMITS.get(base, 8)),
            created_at=now,
            last_used_at=now,
        )
        self._lanes[lane_name] = lane
        return lane

    def _should_rotate(self, lane: LaneState) -> bool:
        return lane.invocation_count >= lane.max_invocations_before_rotate

    def _rotate(self, lane: LaneState) -> None:
        lane.session_id = str(uuid.uuid4())
        lane.invocation_count = 0
        lane.rotated_at = now_iso()
        self._save_lane(lane)

    def _save_lane(self, lane: LaneState) -> None:
        safe_name = lane.lane_name.replace(":", "-")
        save_yaml_file(self._lanes_dir / f"lane-{safe_name}.yaml", lane.to_dict())

    def _load(self) -> None:
        for path in self._lanes_dir.glob("lane-*.yaml"):
            data = load_yaml_file(path)
            if data.get("lane_name"):
                self._lanes[data["lane_name"]] = LaneState.from_dict(data)

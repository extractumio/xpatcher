"""Pipeline state machine with persistence and task DAG scheduling."""

import os
import tempfile
import threading
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

import yaml


class PipelineStage(str, Enum):
    """Single authoritative definition of pipeline-level states."""
    UNINITIALIZED    = "uninitialized"
    INTENT_CAPTURE   = "intent_capture"
    PLANNING         = "planning"
    PLAN_REVIEW      = "plan_review"
    PLAN_FIX         = "plan_fix"
    PLAN_APPROVAL    = "plan_approval"
    TASK_BREAKDOWN   = "task_breakdown"
    TASK_REVIEW      = "task_review"
    TASK_FIX         = "task_fix"
    PRIORITIZATION   = "prioritization"
    EXECUTION_GRAPH  = "execution_graph"
    TASK_EXECUTION   = "task_execution"
    PER_TASK_QUALITY = "per_task_quality"
    FIX_ITERATION    = "fix_iteration"
    GAP_DETECTION    = "gap_detection"
    DOCUMENTATION    = "documentation"
    COMPLETION       = "completion"
    DONE             = "done"
    PAUSED           = "paused"
    BLOCKED          = "blocked"
    FAILED           = "failed"
    CANCELLED        = "cancelled"
    ROLLED_BACK      = "rolled_back"


# Valid transitions: dict of from_stage -> set of to_stages
VALID_TRANSITIONS: dict[PipelineStage, set[PipelineStage]] = {
    PipelineStage.UNINITIALIZED: {PipelineStage.INTENT_CAPTURE},
    PipelineStage.INTENT_CAPTURE: {PipelineStage.PLANNING, PipelineStage.BLOCKED},
    PipelineStage.PLANNING: {PipelineStage.PLAN_REVIEW, PipelineStage.FAILED},
    PipelineStage.PLAN_REVIEW: {PipelineStage.PLAN_FIX, PipelineStage.PLAN_APPROVAL, PipelineStage.FAILED},
    PipelineStage.PLAN_FIX: {PipelineStage.PLAN_REVIEW, PipelineStage.BLOCKED, PipelineStage.FAILED},
    PipelineStage.PLAN_APPROVAL: {PipelineStage.TASK_BREAKDOWN, PipelineStage.PLAN_FIX, PipelineStage.PAUSED},
    PipelineStage.TASK_BREAKDOWN: {PipelineStage.TASK_REVIEW, PipelineStage.FAILED},
    PipelineStage.TASK_REVIEW: {PipelineStage.TASK_FIX, PipelineStage.PRIORITIZATION, PipelineStage.FAILED},
    PipelineStage.TASK_FIX: {PipelineStage.TASK_REVIEW, PipelineStage.BLOCKED, PipelineStage.FAILED},
    PipelineStage.PRIORITIZATION: {PipelineStage.EXECUTION_GRAPH},
    PipelineStage.EXECUTION_GRAPH: {PipelineStage.TASK_EXECUTION},
    PipelineStage.TASK_EXECUTION: {
        PipelineStage.PER_TASK_QUALITY, PipelineStage.GAP_DETECTION,
        PipelineStage.FAILED, PipelineStage.BLOCKED,
    },
    PipelineStage.PER_TASK_QUALITY: {
        PipelineStage.FIX_ITERATION, PipelineStage.TASK_EXECUTION,
        PipelineStage.GAP_DETECTION, PipelineStage.BLOCKED,
    },
    PipelineStage.FIX_ITERATION: {PipelineStage.PER_TASK_QUALITY, PipelineStage.BLOCKED, PipelineStage.FAILED},
    PipelineStage.GAP_DETECTION: {PipelineStage.DOCUMENTATION, PipelineStage.TASK_BREAKDOWN, PipelineStage.BLOCKED},
    PipelineStage.DOCUMENTATION: {PipelineStage.COMPLETION, PipelineStage.FAILED},
    PipelineStage.COMPLETION: {PipelineStage.DONE, PipelineStage.PAUSED},
    # Terminal states have no outbound transitions
    PipelineStage.DONE: set(),
    PipelineStage.FAILED: {PipelineStage.PAUSED},  # Can pause a failed pipeline for investigation
    PipelineStage.PAUSED: {
        PipelineStage.INTENT_CAPTURE, PipelineStage.PLANNING, PipelineStage.PLAN_REVIEW,
        PipelineStage.PLAN_APPROVAL, PipelineStage.TASK_BREAKDOWN, PipelineStage.TASK_REVIEW,
        PipelineStage.TASK_FIX, PipelineStage.PRIORITIZATION, PipelineStage.EXECUTION_GRAPH,
        PipelineStage.TASK_EXECUTION, PipelineStage.GAP_DETECTION, PipelineStage.DOCUMENTATION,
        PipelineStage.COMPLETION,
    },  # Resume to any stage
    PipelineStage.BLOCKED: {PipelineStage.PAUSED},
    PipelineStage.CANCELLED: set(),
    PipelineStage.ROLLED_BACK: set(),
}

# Any stage can transition to CANCELLED, PAUSED, FAILED, or BLOCKED
TERMINAL_STAGES = {PipelineStage.DONE, PipelineStage.CANCELLED, PipelineStage.ROLLED_BACK}


class TaskState(str, Enum):
    PENDING    = "pending"
    BLOCKED    = "blocked"
    READY      = "ready"
    RUNNING    = "running"
    SUCCEEDED  = "succeeded"
    FAILED     = "failed"
    NEEDS_FIX  = "needs_fix"
    STUCK      = "stuck"
    SKIPPED    = "skipped"
    CANCELLED  = "cancelled"


class InvalidTransitionError(Exception):
    pass


class PipelineStateFile:
    """Thread-safe, atomic read/write for pipeline-state.yaml."""

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()

    def read(self) -> dict:
        if not os.path.exists(self.path):
            return {}
        with open(self.path) as f:
            return yaml.safe_load(f) or {}

    def write(self, state: dict) -> None:
        with self._lock:
            self._atomic_write(state)

    def update(self, **fields) -> dict:
        """Read-modify-write with lock held. Atomic."""
        with self._lock:
            state = self.read()
            state.update(fields)
            self._atomic_write(state)
            return state

    def _atomic_write(self, state: dict) -> None:
        dir_name = os.path.dirname(self.path)
        os.makedirs(dir_name, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                yaml.dump(state, f, default_flow_style=False, sort_keys=False)
            os.rename(tmp_path, self.path)
        except Exception:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise


class PipelineStateMachine:
    """Manages pipeline stage transitions with validation."""

    def __init__(self, state_file: PipelineStateFile):
        self._state_file = state_file

    @property
    def current_stage(self) -> PipelineStage:
        state = self._state_file.read()
        return PipelineStage(state.get("current_stage", "uninitialized"))

    def transition(self, to_stage: PipelineStage) -> dict:
        state = self._state_file.read()
        current = PipelineStage(state.get("current_stage", "uninitialized"))

        # Any non-terminal stage can go to CANCELLED, PAUSED, FAILED, or BLOCKED
        if to_stage in (PipelineStage.CANCELLED, PipelineStage.PAUSED, PipelineStage.FAILED, PipelineStage.BLOCKED) and current not in TERMINAL_STAGES:
            return self._do_transition(state, current, to_stage)

        # Check explicit valid transitions
        valid = VALID_TRANSITIONS.get(current, set())
        if to_stage not in valid:
            raise InvalidTransitionError(
                f"Invalid transition: {current.value} -> {to_stage.value}. "
                f"Valid targets: {[s.value for s in valid]}"
            )

        return self._do_transition(state, current, to_stage)

    def _do_transition(self, state: dict, from_stage: PipelineStage, to_stage: PipelineStage) -> dict:
        now = datetime.now(timezone.utc).isoformat()
        state["current_stage"] = to_stage.value
        state["previous_stage"] = from_stage.value
        state["last_transition_at"] = now
        # Append to transition history (capped at 500 entries)
        history = state.get("transitions", [])
        history.append({
            "from": from_stage.value,
            "to": to_stage.value,
            "at": now,
        })
        if len(history) > 500:
            history = history[-500:]
        state["transitions"] = history
        self._state_file.write(state)
        return state


class TaskNode:
    def __init__(self, task_id: str, dependencies: Optional[list[str]] = None):
        self.task_id = task_id
        self.dependencies = dependencies or []
        self.dependents: list[str] = []
        self.state = TaskState.PENDING


class TaskDAG:
    """Manages task dependency graph and state transitions."""

    def __init__(self):
        self.nodes: dict[str, TaskNode] = {}

    def add_task(self, task_id: str, dependencies: Optional[list[str]] = None) -> TaskNode:
        node = TaskNode(task_id, dependencies or [])
        self.nodes[task_id] = node
        return node

    def validate(self) -> list[str]:
        """Validate DAG: cycle detection, orphan detection, completeness check.
        Returns list of error strings (empty if valid)."""
        errors = []

        # Check all dependencies exist
        for node in self.nodes.values():
            for dep_id in node.dependencies:
                if dep_id not in self.nodes:
                    errors.append(f"Task {node.task_id} depends on non-existent task {dep_id}")

        # Cycle detection via topological sort
        if not errors:
            visited: set[str] = set()
            in_stack: set[str] = set()

            def has_cycle(task_id: str) -> bool:
                visited.add(task_id)
                in_stack.add(task_id)
                for dep_id in self.nodes[task_id].dependencies:
                    if dep_id in in_stack:
                        return True
                    if dep_id not in visited and has_cycle(dep_id):
                        return True
                in_stack.discard(task_id)
                return False

            for task_id in self.nodes:
                if task_id not in visited:
                    if has_cycle(task_id):
                        errors.append(f"Cycle detected involving task {task_id}")
                        break

        return errors

    def initialize_states(self) -> None:
        """Set initial states: tasks with no deps -> READY, others -> PENDING."""
        for node in self.nodes.values():
            if not node.dependencies:
                node.state = TaskState.READY
            else:
                node.state = TaskState.PENDING

    def get_ready_tasks(self) -> list[TaskNode]:
        return [n for n in self.nodes.values() if n.state == TaskState.READY]

    def get_topological_order(self) -> list[str]:
        """Return task IDs in topological order (dependencies first)."""
        visited: set[str] = set()
        order: list[str] = []

        def visit(task_id: str) -> None:
            if task_id in visited:
                return
            visited.add(task_id)
            for dep_id in self.nodes[task_id].dependencies:
                visit(dep_id)
            order.append(task_id)

        for task_id in self.nodes:
            visit(task_id)
        return order

    def mark_complete(self, task_id: str, success: bool) -> None:
        node = self.nodes[task_id]
        node.state = TaskState.SUCCEEDED if success else TaskState.FAILED
        if success:
            self._check_unblock_dependents(task_id)
        else:
            self._block_dependents(task_id)

    def mark_skipped(self, task_id: str, force_unblock: bool = False) -> None:
        """Skip a task. Dependents remain BLOCKED unless force_unblock=True."""
        node = self.nodes[task_id]
        node.state = TaskState.SKIPPED
        if force_unblock:
            self._check_unblock_dependents(task_id, allow_skipped=True)
        else:
            self._block_dependents(task_id)

    def _check_unblock_dependents(self, task_id: str, allow_skipped: bool = False) -> None:
        valid_states = (TaskState.SUCCEEDED, TaskState.SKIPPED) if allow_skipped else (TaskState.SUCCEEDED,)
        node = self.nodes[task_id]
        for dep_id in node.dependents:
            dep = self.nodes[dep_id]
            if all(self.nodes[d].state in valid_states for d in dep.dependencies):
                dep.state = TaskState.READY

    def _block_dependents(self, task_id: str) -> None:
        node = self.nodes[task_id]
        for dep_id in node.dependents:
            dep = self.nodes[dep_id]
            if dep.state == TaskState.PENDING:
                dep.state = TaskState.BLOCKED

    def to_dict(self) -> dict:
        return {
            task_id: {
                "state": node.state.value,
                "dependencies": node.dependencies,
                "dependents": node.dependents,
            }
            for task_id, node in self.nodes.items()
        }

    def _register_dependents(self) -> None:
        """Register all dependents after all tasks are added."""
        for node in self.nodes.values():
            for dep_id in node.dependencies:
                if dep_id in self.nodes and node.task_id not in self.nodes[dep_id].dependents:
                    self.nodes[dep_id].dependents.append(node.task_id)

    @classmethod
    def from_tasks(cls, tasks: list[dict]) -> "TaskDAG":
        """Build DAG from list of task dicts with 'id' and 'depends_on' fields."""
        dag = cls()
        for task in tasks:
            dag.add_task(task["id"], task.get("depends_on", []))
        dag._register_dependents()
        dag.initialize_states()
        return dag

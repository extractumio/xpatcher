"""xpatcher dispatcher — main dispatch loop."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .auth import resolve_auth_env, describe_auth_source
from .state import PipelineStage, PipelineStateFile, PipelineStateMachine, TaskDAG, TaskState
from .session import AgentInvocation, AgentResult, ClaudeSession, SessionTailer
from .schemas import ArtifactValidator, ValidationResult
from .tui import TUIRenderer
from .yaml_utils import load_yaml_file
from ..artifacts.store import ArtifactStore
from ..context.builder import PromptBuilder


class CancelledPipelineError(RuntimeError):
    """Raised when a pipeline is cancelled while a dispatcher is running."""


def generate_pipeline_id() -> str:
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y%m%d")
    hash_str = hashlib.sha256(now.isoformat().encode()).hexdigest()[:4]
    return f"xp-{date_str}-{hash_str}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slugify(text: str) -> str:
    slug = text.lower().strip()
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"[\s]+", "-", slug)
    return slug[:50]


def _project_storage_slug(project_dir: Path) -> str:
    project_slug = _slugify(project_dir.name or "project")
    digest = hashlib.sha256(str(project_dir.resolve()).encode()).hexdigest()[:8]
    return f"{project_slug}-{digest}"


def _branch_name_for(feature_slug: str) -> str:
    return f"xpatcher/{feature_slug}"


def _home_relative(path: Path) -> str:
    """Return path with $HOME replaced by ~."""
    try:
        return "~/" + str(path.relative_to(Path.home()))
    except ValueError:
        return str(path)


def _pipeline_index_dir(xpatcher_home: Path) -> Path:
    return xpatcher_home / ".xpatcher" / "pipelines"


def _project_pipeline_index_path(xpatcher_home: Path, project_dir: Path) -> Path:
    return _pipeline_index_dir(xpatcher_home) / f"{_project_storage_slug(project_dir)}.yaml"


def _load_pipeline_index_file(path: Path) -> dict:
    return load_yaml_file(path)


def _save_pipeline_index_file(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    import yaml

    path.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False))


def _register_pipeline_index(xpatcher_home: Path, pipeline_id: str, project_dir: Path, feature_dir: Path) -> None:
    path = _project_pipeline_index_path(xpatcher_home, project_dir)
    data = _load_pipeline_index_file(path)
    data["project_dir"] = str(project_dir)
    pipelines = data.setdefault("pipelines", {})
    pipelines[pipeline_id] = {
        "project_dir": str(project_dir),
        "feature_dir": str(feature_dir),
        "registered_at": _now_iso(),
    }
    _save_pipeline_index_file(path, data)


def _iter_pipeline_indices(xpatcher_home: Path) -> list[Path]:
    index_dir = _pipeline_index_dir(xpatcher_home)
    return sorted(index_dir.glob("*.yaml")) if index_dir.exists() else []


def _find_pipeline_record(xpatcher_home: Path, pipeline_id: str) -> dict | None:
    for path in _iter_pipeline_indices(xpatcher_home):
        data = _load_pipeline_index_file(path)
        record = data.get("pipelines", {}).get(pipeline_id)
        if record is not None:
            return record
    return None


def _find_all_pipeline_records(xpatcher_home: Path) -> dict[str, dict]:
    records: dict[str, dict] = {}
    for path in _iter_pipeline_indices(xpatcher_home):
        records.update(_load_pipeline_index_file(path).get("pipelines", {}))
    return records


class Dispatcher:
    def __init__(self, project_dir: Path, xpatcher_home: Path, debug: bool = False):
        self.project_dir = project_dir
        self.xpatcher_home = xpatcher_home
        self.plugin_dir = xpatcher_home / ".claude-plugin"
        self.debug = debug
        self._auth_env = resolve_auth_env(xpatcher_home)
        self.session = ClaudeSession(self.plugin_dir, project_dir, self._auth_env)
        self.validator = ArtifactValidator()
        self.tui = TUIRenderer()
        self.total_cost_usd = 0.0
        self.state_file: PipelineStateFile | None = None
        self._pipeline_session_id: str = ""
        self._pipeline_session_used: bool = False
        self.feature_dir: Path | None = None

    def _require_auth(self):
        """Show auth source and abort if no credentials were resolved."""
        env_has_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
        label = describe_auth_source(self._auth_env, env_has_key=env_has_key)
        if label == "none":
            self.tui.error("No authentication found. Cannot proceed.")
            self.tui.info("  Either add ANTHROPIC_API_KEY to $XPATCHER_HOME/.env")
            self.tui.info("  or log in interactively: run 'claude' and complete login")
            sys.exit(1)
        self.tui.success(f"Auth: {label}")

    def _feature_dir_for(self, feature_slug: str) -> Path:
        return self.xpatcher_home / ".xpatcher" / "projects" / _project_storage_slug(self.project_dir) / feature_slug

    def start(self, description: str, verbose: bool = False, debug: bool = False):
        """Start a new pipeline."""
        self.tui.status("Running preflight checks...")
        preflight = self.session.preflight()
        if not preflight.ok:
            self.tui.error(f"Preflight failed: {preflight.error}")
            sys.exit(1)
        self.tui.success(f"Claude Code CLI v{preflight.cli_version} — plugin loaded")
        self._require_auth()

        if not (self.project_dir / ".git").is_dir():
            self.tui.error("Not a git repository")
            sys.exit(1)

        dirty = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(self.project_dir),
            capture_output=True, text=True,
        )
        if dirty.stdout.strip():
            self.tui.error("Working tree has uncommitted changes. Commit or stash before starting a pipeline.")
            self.tui.info(dirty.stdout.rstrip())
            sys.exit(1)

        pipeline_id = generate_pipeline_id()
        feature_slug = _slugify(description)
        branch_name = _branch_name_for(feature_slug)
        feature_dir = self._feature_dir_for(feature_slug)
        self._initialize_feature_dir(feature_dir)

        state_file = PipelineStateFile(str(feature_dir / "pipeline-state.yaml"))
        state_file.write({
            "pipeline_id": pipeline_id,
            "feature": feature_slug,
            "description": description,
            "current_stage": PipelineStage.UNINITIALIZED.value,
            "created_at": _now_iso(),
            "status": "running",
            "total_cost_usd": 0.0,
            "branch_name": branch_name,
            "branch_created_at": _now_iso(),
            "task_states": {},
            "iterations": {
                "plan_review": {"current": 0, "max": 0, "history": []},
                "task_review": {"current": 0, "max": 0, "history": []},
                "quality_loop": {},
                "gap_reentry": {"current_depth": 0, "rounds": []},
            },
            "transitions": [],
        })

        _register_pipeline_index(self.xpatcher_home, pipeline_id, self.project_dir, feature_dir)

        sm = PipelineStateMachine(state_file)
        store = ArtifactStore(feature_dir)
        prompt_builder = PromptBuilder(feature_dir, self.project_dir)
        config = self._load_config()

        self.state_file = state_file
        self.feature_dir = feature_dir
        self.total_cost_usd = 0.0
        # Single session for the entire pipeline — resume after first use
        self._pipeline_session_id = str(uuid.uuid4())
        self._pipeline_session_used = False

        subprocess.run(
            ["git", "checkout", "-b", branch_name],
            cwd=str(self.project_dir),
            capture_output=True,
        )

        self.tui.header(f"Pipeline {pipeline_id}: {description}")
        try:
            self._run_pipeline(sm, store, prompt_builder, config, description, verbose, debug)
        except CancelledPipelineError:
            self.tui.warning(f"Pipeline {pipeline_id} was cancelled. Dispatcher exited cleanly.")
        except KeyboardInterrupt:
            self._handle_interrupt(sm, pipeline_id)

    def resume(self, pipeline_id: str):
        record = _find_pipeline_record(self.xpatcher_home, pipeline_id)
        if record is None:
            self.tui.error(f"Unknown pipeline: {pipeline_id}")
            sys.exit(1)

        feature_dir = Path(record["feature_dir"])
        self.project_dir = Path(record["project_dir"])
        self.session = ClaudeSession(self.plugin_dir, self.project_dir, self._auth_env)
        self.feature_dir = feature_dir
        self.state_file = PipelineStateFile(str(feature_dir / "pipeline-state.yaml"))
        config = self._load_config()
        self.total_cost_usd = self.state_file.read().get("total_cost_usd", 0.0)
        self._pipeline_session_id = str(uuid.uuid4())
        self._pipeline_session_used = False

        state = self.state_file.read()
        current = PipelineStage(state.get("current_stage", PipelineStage.UNINITIALIZED.value))
        gate_reason = state.get("gate_reason", "")
        self.tui.header(f"Resuming {pipeline_id}")
        self._require_auth()

        try:
            if gate_reason == "plan_approval" and current in {PipelineStage.PAUSED, PipelineStage.PLAN_APPROVAL}:
                sm = PipelineStateMachine(self.state_file)
                store = ArtifactStore(feature_dir)
                prompt_builder = PromptBuilder(feature_dir, self.project_dir)
                approved = self._handle_plan_approval(sm, store, prompt_builder, config, transition_stage=False)
                if not approved:
                    return
                plan_version = store.latest_version("plan")
                manifest_version = self._run_task_breakdown_and_review(
                    sm,
                    store,
                    prompt_builder,
                    config,
                    plan_version=plan_version,
                    manifest_version=max(1, store.latest_version("task-manifest")),
                )
                if manifest_version is None:
                    return
                if not self._run_prioritization_and_execution(sm, store, prompt_builder, config):
                    return
                if not self._run_gap_detection_with_reentry(sm, store, prompt_builder, config, plan_version):
                    return
                sm.transition(PipelineStage.DOCUMENTATION)
                self.tui.stage("Stage 15: Documentation")
                docs_out = self.feature_dir / "docs-report.yaml"
                _, validation = self._invoke_validated_agent(
                    agent="tech-writer",
                    prompt=prompt_builder.build_tech_writer(docs_out, self._agent_timeout(config, "tech-writer")),
                    config=config,
                    expected_type="docs_report",
                    stage=PipelineStage.DOCUMENTATION.value,
                    output_path=docs_out,
                )
                if not validation.valid:
                    self._fail_pipeline(sm, f"Docs validation failed: {validation.errors}")
                    return
                store.save("docs-report.yaml", validation.data)
                sm.transition(PipelineStage.COMPLETION)
                self._handle_completion_gate(sm, store, transition_stage=False)
                return

            if gate_reason == "completion" and current in {PipelineStage.PAUSED, PipelineStage.COMPLETION}:
                self._handle_completion_gate(PipelineStateMachine(self.state_file), ArtifactStore(feature_dir), transition_stage=False)
                return

            self.tui.info(
                "Resume supports paused human gates. For execution-stage recovery, use "
                "`status`, `skip`, `cancel`, or rerun once the pipeline is unblocked."
            )
        except CancelledPipelineError:
            self.tui.warning(f"Pipeline {pipeline_id} was cancelled. Dispatcher exited cleanly.")
        except KeyboardInterrupt:
            sm = PipelineStateMachine(self.state_file)
            self._handle_interrupt(sm, pipeline_id)

    def _run_pipeline(
        self,
        sm: PipelineStateMachine,
        store: ArtifactStore,
        prompt_builder: PromptBuilder,
        config: dict,
        description: str,
        verbose: bool,
        debug: bool = False,
    ) -> None:
        del verbose
        self.debug = debug
        self._raise_if_cancelled()

        sm.transition(PipelineStage.INTENT_CAPTURE)
        self._update_status(status="running")
        self.tui.stage("Stage 1: Intent Capture")
        intent_path = self.feature_dir / "intent.yaml"
        _, validation = self._invoke_validated_agent(
            agent="planner",
            prompt=prompt_builder.build_intent_capture(description, intent_path, self._agent_timeout(config, "planner")),
            config=config,
            expected_type="intent",
            stage=PipelineStage.INTENT_CAPTURE.value,
            output_path=intent_path,
        )
        if not validation.valid:
            self._fail_pipeline(sm, f"Intent validation failed: {validation.errors}")
            return
        store.save("intent.yaml", validation.data)

        sm.transition(PipelineStage.PLANNING)
        self.tui.stage("Stage 2: Planning")
        plan_v1_path = self.feature_dir / "plan-v1.yaml"
        _, validation = self._invoke_validated_agent(
            agent="planner",
            prompt=prompt_builder.build_planner(plan_v1_path, self._agent_timeout(config, "planner")),
            config=config,
            expected_type="plan",
            stage=PipelineStage.PLANNING.value,
            output_path=plan_v1_path,
        )
        if not validation.valid:
            self._fail_pipeline(sm, f"Plan validation failed: {validation.errors}")
            return
        store.save("plan-v1.yaml", validation.data)

        plan_version = self._run_plan_review_loop(sm, store, prompt_builder, config, starting_version=1)
        if plan_version is None:
            return

        if not self._handle_plan_approval(sm, store, prompt_builder, config, plan_version=plan_version):
            return

        manifest_version = self._run_task_breakdown_and_review(
            sm,
            store,
            prompt_builder,
            config,
            plan_version=plan_version,
            manifest_version=1,
        )
        if manifest_version is None:
            return

        if not self._run_prioritization_and_execution(sm, store, prompt_builder, config):
            return

        if not self._run_gap_detection_with_reentry(sm, store, prompt_builder, config, plan_version):
            return

        sm.transition(PipelineStage.DOCUMENTATION)
        self.tui.stage("Stage 15: Documentation")
        docs_out = self.feature_dir / "docs-report.yaml"
        _, validation = self._invoke_validated_agent(
            agent="tech-writer",
            prompt=prompt_builder.build_tech_writer(docs_out, self._agent_timeout(config, "tech-writer")),
            config=config,
            expected_type="docs_report",
            stage=PipelineStage.DOCUMENTATION.value,
            output_path=docs_out,
        )
        if not validation.valid:
            self._fail_pipeline(sm, f"Docs validation failed: {validation.errors}")
            return
        store.save("docs-report.yaml", validation.data)

        sm.transition(PipelineStage.COMPLETION)
        self._handle_completion_gate(sm, store, transition_stage=False)

    def _run_plan_review_loop(
        self,
        sm: PipelineStateMachine,
        store: ArtifactStore,
        prompt_builder: PromptBuilder,
        config: dict,
        starting_version: int,
    ) -> int | None:
        max_iterations = config.get("iterations", {}).get("plan_review_max", 3)
        plan_version = starting_version

        for iteration in range(1, max_iterations + 1):
            self._raise_if_cancelled()
            sm.transition(PipelineStage.PLAN_REVIEW)
            self.tui.stage(f"Stage 3: Plan Review (iteration {iteration})")
            review_out = self.feature_dir / f"plan-review-v{plan_version}.yaml"
            _, validation = self._invoke_validated_agent(
                agent="plan-reviewer",
                prompt=prompt_builder.build_plan_reviewer(plan_version, review_out, self._agent_timeout(config, "plan-reviewer")),
                config=config,
                expected_type="plan_review",
                stage=PipelineStage.PLAN_REVIEW.value,
                output_path=review_out,
            )
            if not validation.valid:
                self._fail_pipeline(sm, f"Plan review validation failed: {validation.errors}")
                return None

            store.save(f"plan-review-v{plan_version}.yaml", validation.data)
            self._set_loop_history("plan_review", iteration, max_iterations, validation.data["verdict"])

            if validation.data["verdict"] == "approved":
                return plan_version

            if iteration >= max_iterations:
                sm.transition(PipelineStage.BLOCKED)
                self._update_status(status="waiting_for_human", gate_reason="plan_review_limit")
                self.tui.warning("Plan review iteration limit reached; pipeline blocked for human intervention.")
                return None

            sm.transition(PipelineStage.PLAN_FIX)
            self.tui.stage("Stage 4: Plan Fix")
            plan_version += 1
            plan_fix_out = self.feature_dir / f"plan-v{plan_version}.yaml"
            _, plan_validation = self._invoke_validated_agent(
                agent="planner",
                prompt=prompt_builder.build_plan_fix(plan_version - 1, plan_fix_out, self._agent_timeout(config, "planner")),
                config=config,
                expected_type="plan",
                stage=PipelineStage.PLAN_FIX.value,
                output_path=plan_fix_out,
            )
            if not plan_validation.valid:
                self._fail_pipeline(sm, f"Plan fix validation failed: {plan_validation.errors}")
                return None
            store.save(f"plan-v{plan_version}.yaml", plan_validation.data)

        return None

    def _handle_plan_approval(
        self,
        sm: PipelineStateMachine,
        store: ArtifactStore,
        prompt_builder: PromptBuilder,
        config: dict,
        plan_version: int | None = None,
        transition_stage: bool = True,
    ) -> bool:
        del prompt_builder
        self._raise_if_cancelled()
        if plan_version is None:
            plan_version = store.latest_version("plan")

        if transition_stage:
            sm.transition(PipelineStage.PLAN_APPROVAL)
        requires_human = self._requires_plan_confirmation(config)
        if not requires_human:
            self.tui.stage("Stage 5: Specification Freeze")
            self.tui.info("Specification auto-approved; continuing without a human gate.")
            self._update_status(status="running", gate_reason="")
            store.save_decision("plan-approval", {"approved": True, "auto_approved": True, "plan_version": plan_version})
            return True

        self._update_status(status="waiting_for_human", gate_reason="plan_approval", waiting_since=_now_iso())
        self.tui.human_gate("Stage 5: Specification Confirmation Required")
        self.tui.info(f"Review the plan at: {self.feature_dir}/plan-v{plan_version}.yaml")
        approved = self.tui.prompt_approval("Approve this specification? [y/n]: ")
        if not approved:
            sm.transition(PipelineStage.PAUSED)
            self._update_status(status="paused", gate_reason="plan_approval")
            self.tui.info("Pipeline paused. Resume with: xpatcher resume <pipeline-id>")
            return False

        self._update_status(status="running", gate_reason="")
        store.save_decision("plan-approval", {"approved": True, "auto_approved": False, "plan_version": plan_version})
        return True

    def _run_task_breakdown_and_review(
        self,
        sm: PipelineStateMachine,
        store: ArtifactStore,
        prompt_builder: PromptBuilder,
        config: dict,
        plan_version: int,
        manifest_version: int,
        gap_round: int = 0,
    ) -> int | None:
        del gap_round
        self._raise_if_cancelled()
        sm.transition(PipelineStage.TASK_BREAKDOWN)
        self.tui.stage("Stage 6: Execution Slice Breakdown")
        manifest_out = self.feature_dir / "task-manifest.yaml"
        _, validation = self._invoke_validated_agent(
            agent="planner",
            prompt=prompt_builder.build_task_breakdown(plan_version, manifest_out, self._agent_timeout(config, "planner")),
            config=config,
            expected_type="task_manifest",
            stage=PipelineStage.TASK_BREAKDOWN.value,
            output_path=manifest_out,
        )
        if not validation.valid:
            self._fail_pipeline(sm, f"Task manifest validation failed: {validation.errors}")
            return None

        manifest = validation.data
        if not manifest.get("tasks"):
            self._fail_pipeline(sm, "Task manifest was empty")
            return None

        self._save_task_manifest(store, manifest, manifest_version)

        max_iterations = config.get("iterations", {}).get("task_review_max", 3)
        current_manifest_version = manifest_version
        for iteration in range(1, max_iterations + 1):
            self._raise_if_cancelled()
            sm.transition(PipelineStage.TASK_REVIEW)
            self.tui.stage(f"Stage 7: Execution Slice Review (iteration {iteration})")
            task_review_out = self.feature_dir / f"task-review-v{iteration}.yaml"
            _, review_validation = self._invoke_validated_agent(
                agent="plan-reviewer",
                prompt=prompt_builder.build_task_reviewer(task_review_out, self._agent_timeout(config, "plan-reviewer")),
                config=config,
                expected_type="task_manifest_review",
                stage=PipelineStage.TASK_REVIEW.value,
                output_path=task_review_out,
            )
            if not review_validation.valid:
                self._fail_pipeline(sm, f"Task review validation failed: {review_validation.errors}")
                return None

            store.save(f"task-review-v{iteration}.yaml", review_validation.data)
            self._set_loop_history("task_review", iteration, max_iterations, review_validation.data["verdict"])

            if review_validation.data["verdict"] == "approved":
                return current_manifest_version

            if iteration >= max_iterations:
                sm.transition(PipelineStage.BLOCKED)
                self._update_status(status="waiting_for_human", gate_reason="task_review_limit")
                self.tui.warning("Task review iteration limit reached; pipeline blocked for human intervention.")
                return None

            sm.transition(PipelineStage.TASK_FIX)
            self.tui.stage(f"Stage 8: Execution Slice Fix (iteration {iteration})")
            current_manifest_version += 1
            task_fix_out = self.feature_dir / "task-manifest.yaml"
            _, manifest_validation = self._invoke_validated_agent(
                agent="planner",
                prompt=prompt_builder.build_task_fix(iteration, task_fix_out, self._agent_timeout(config, "planner")),
                config=config,
                expected_type="task_manifest",
                stage=PipelineStage.TASK_FIX.value,
                output_path=task_fix_out,
            )
            if not manifest_validation.valid:
                self._fail_pipeline(sm, f"Task fix validation failed: {manifest_validation.errors}")
                return None
            self._save_task_manifest(store, manifest_validation.data, current_manifest_version)

        return None

    def _run_prioritization_and_execution(
        self,
        sm: PipelineStateMachine,
        store: ArtifactStore,
        prompt_builder: PromptBuilder,
        config: dict,
    ) -> bool:
        overall_success = True
        self._raise_if_cancelled()
        sm.transition(PipelineStage.PRIORITIZATION)
        self.tui.stage("Stage 9: Spec-Derived Prioritization")

        manifest = store.load("task-manifest.yaml")
        tasks = manifest.get("tasks", [])
        if not tasks:
            self._fail_pipeline(sm, "Task manifest contained no tasks to prioritize")
            return False

        dag = self._build_dag(tasks)
        errors = dag.validate()
        if errors:
            self._fail_pipeline(sm, f"DAG validation errors: {errors}")
            return False

        execution_order = dag.get_topological_order()
        store.save("execution-plan.yaml", {"order": execution_order, "dag": dag.to_dict()})

        sm.transition(PipelineStage.EXECUTION_GRAPH)
        self.tui.stage("Stage 10: Execution Graph")

        sm.transition(PipelineStage.TASK_EXECUTION)
        for task_id in execution_order:
            self._raise_if_cancelled()
            node = dag.nodes[task_id]
            if node.state in {TaskState.SUCCEEDED, TaskState.SKIPPED, TaskState.FAILED, TaskState.STUCK}:
                continue
            if node.state != TaskState.READY:
                continue
            if not self._execute_task(sm, store, prompt_builder, config, task_id):
                overall_success = False
                continue

            dag = self._build_dag(tasks)

        terminal_failures = {
            TaskState.FAILED.value,
            TaskState.STUCK.value,
            TaskState.BLOCKED.value,
            TaskState.RUNNING.value,
            TaskState.NEEDS_FIX.value,
        }
        task_states = self.state_file.read().get("task_states", {}) if self.state_file else {}
        if any(state in terminal_failures for state in task_states.values()):
            overall_success = False

        return overall_success

    def _execute_task(
        self,
        sm: PipelineStateMachine,
        store: ArtifactStore,
        prompt_builder: PromptBuilder,
        config: dict,
        task_id: str,
    ) -> bool:
        self._raise_if_cancelled()
        self.tui.stage(f"Stage 11: Executing {task_id}")
        self._update_task_state(task_id, TaskState.RUNNING)
        task_path = self._move_task_artifact(task_id, "todo", "in-progress")

        exec_out = self.feature_dir / "tasks" / "in-progress" / f"{task_id}-execution-log.yaml"
        exec_out.parent.mkdir(parents=True, exist_ok=True)
        _, validation = self._invoke_validated_agent(
            agent="executor",
            prompt=prompt_builder.build_executor(task_id, exec_out, self._agent_timeout(config, "executor")),
            config=config,
            expected_type="execution_result",
            stage=PipelineStage.TASK_EXECUTION.value,
            task_id=task_id,
            output_path=exec_out,
        )
        if not validation.valid:
            self._update_task_state(task_id, TaskState.FAILED)
            self._move_task_artifact(task_id, "in-progress", "done")
            return False

        execution_data = self._enrich_execution_result(validation.data)
        store.save(f"tasks/in-progress/{task_id}-execution-log.yaml", execution_data)
        if execution_data.get("status") != "completed":
            self._update_task_state(task_id, TaskState.FAILED)
            self._move_task_artifact(task_id, "in-progress", "done")
            return False

        sm.transition(PipelineStage.PER_TASK_QUALITY)
        quality_passed = self._run_quality_loop(task_id, config, prompt_builder, store, sm)
        if quality_passed:
            self._update_task_state(task_id, TaskState.SUCCEEDED)
        else:
            state_name = self.state_file.read().get("task_states", {}).get(task_id, TaskState.FAILED.value)
            self._update_task_state(task_id, TaskState(state_name))

        self._move_task_artifact(task_id, "in-progress", "done")
        if (self.feature_dir / "tasks" / "in-progress" / f"{task_id}-execution-log.yaml").exists():
            shutil.move(
                str(self.feature_dir / "tasks" / "in-progress" / f"{task_id}-execution-log.yaml"),
                str(self.feature_dir / "tasks" / "done" / f"{task_id}-execution-log.yaml"),
            )
        sm.transition(PipelineStage.TASK_EXECUTION)
        return quality_passed

    def _run_quality_loop(self, task_id, config, prompt_builder, store, sm):
        max_iterations = config.get("iterations", {}).get("quality_loop_max", 3)
        seen_finding_hashes: set[str] = set()
        task_spec = self._load_task_spec(task_id)

        for iteration in range(1, max_iterations + 1):
            self._raise_if_cancelled()
            self._set_quality_iteration(task_id, iteration, max_iterations)
            test_report = self._run_acceptance_checks(task_spec)
            store.save(f"tasks/done/{task_id}-quality-report-v{iteration}.yaml", test_report)

            if test_report["overall"] != "pass":
                if self._only_verification_spec_failures(test_report):
                    return self._review_task_without_command_checks(task_id, prompt_builder, store, config)
                finding_hash = self._findings_hash(test_report.get("regression_failures", []))
                if finding_hash in seen_finding_hashes:
                    self._update_task_state(task_id, TaskState.STUCK)
                    return False
                seen_finding_hashes.add(finding_hash)

                if iteration >= max_iterations:
                    self._update_task_state(task_id, TaskState.STUCK)
                    return False

                sm.transition(PipelineStage.FIX_ITERATION)
                self._update_task_state(task_id, TaskState.NEEDS_FIX)
                fix_out = self.feature_dir / "tasks" / "in-progress" / f"{task_id}-fix-result.yaml"
                self._invoke_agent(
                    agent="executor",
                    prompt=prompt_builder.build_executor_fix(task_id, [{"description": "; ".join(test_report["regression_failures"])}], fix_out, self._agent_timeout(config, "executor")),
                    config=config,
                    stage=PipelineStage.FIX_ITERATION.value,
                    task_id=task_id,
                )
                sm.transition(PipelineStage.PER_TASK_QUALITY)
                continue

            self.tui.stage(f"Stage 12: Reviewing {task_id} Against the Specification (iteration {iteration})")
            review_out = self.feature_dir / "tasks" / "done" / f"{task_id}-review-v{iteration}.yaml"
            review_out.parent.mkdir(parents=True, exist_ok=True)
            _, review_validation = self._invoke_validated_agent(
                agent="reviewer",
                prompt=prompt_builder.build_reviewer(task_id, review_out, self._agent_timeout(config, "reviewer")),
                config=config,
                expected_type="review",
                stage=PipelineStage.PER_TASK_QUALITY.value,
                task_id=task_id,
                output_path=review_out,
            )
            if not review_validation.valid:
                self._update_task_state(task_id, TaskState.FAILED)
                return False

            store.save(f"tasks/done/{task_id}-review-v{iteration}.yaml", review_validation.data)
            if review_validation.data["verdict"] == "approve":
                return True

            findings = [f["description"] for f in review_validation.data.get("findings", [])]
            finding_hash = self._findings_hash(findings)
            if finding_hash in seen_finding_hashes:
                self._update_task_state(task_id, TaskState.STUCK)
                return False
            seen_finding_hashes.add(finding_hash)

            if iteration >= max_iterations:
                self._update_task_state(task_id, TaskState.STUCK)
                return False

            sm.transition(PipelineStage.FIX_ITERATION)
            self._update_task_state(task_id, TaskState.NEEDS_FIX)
            fix_out2 = self.feature_dir / "tasks" / "in-progress" / f"{task_id}-fix-result.yaml"
            self._invoke_agent(
                agent="executor",
                prompt=prompt_builder.build_executor_fix(task_id, review_validation.data.get("findings", []), fix_out2, self._agent_timeout(config, "executor")),
                config=config,
                stage=PipelineStage.FIX_ITERATION.value,
                task_id=task_id,
            )
            sm.transition(PipelineStage.PER_TASK_QUALITY)

        self._update_task_state(task_id, TaskState.STUCK)
        return False

    def _run_gap_detection_with_reentry(
        self,
        sm: PipelineStateMachine,
        store: ArtifactStore,
        prompt_builder: PromptBuilder,
        config: dict,
        plan_version: int,
    ) -> bool:
        max_depth = config.get("iterations", {}).get("gap_reentry_max", 2)
        depth = 0

        while True:
            self._raise_if_cancelled()
            sm.transition(PipelineStage.GAP_DETECTION)
            self.tui.stage("Stage 14: Specification-to-Code Gap Detection")
            gap_out = self.feature_dir / f"gap-report-v{depth + 1}.yaml"
            _, validation = self._invoke_validated_agent(
                agent="gap-detector",
                prompt=prompt_builder.build_gap_detector(gap_out, self._agent_timeout(config, "gap-detector")),
                config=config,
                expected_type="gap_report",
                stage=PipelineStage.GAP_DETECTION.value,
                output_path=gap_out,
            )
            if not validation.valid:
                self._fail_pipeline(sm, f"Gap report validation failed: {validation.errors}")
                return False

            store.save(f"gap-report-v{depth + 1}.yaml", validation.data)
            if validation.data["verdict"] == "complete":
                return True

            depth += 1
            self._record_gap_round(depth, max_depth, validation.data)
            if depth > max_depth:
                sm.transition(PipelineStage.BLOCKED)
                self._update_status(status="waiting_for_human", gate_reason="gap_reentry_limit")
                self.tui.warning("Gap re-entry limit reached; pipeline blocked for human intervention.")
                return False

            manifest = store.load("task-manifest.yaml")
            existing_ids = {task["id"] for task in manifest.get("tasks", [])}

            new_manifest_version = max(2, depth + 1)
            sm.transition(PipelineStage.TASK_BREAKDOWN)
            gap_manifest_out = self.feature_dir / "task-manifest.yaml"
            result, task_validation = self._invoke_validated_agent(
                agent="planner",
                prompt=prompt_builder.build_task_breakdown(plan_version, gap_manifest_out, self._agent_timeout(config, "planner")),
                config=config,
                expected_type="task_manifest",
                stage=PipelineStage.TASK_BREAKDOWN.value,
                output_path=gap_manifest_out,
            )
            del result
            if not task_validation.valid:
                self._fail_pipeline(sm, f"Gap task manifest validation failed: {task_validation.errors}")
                return False

            gap_tasks = [task for task in task_validation.data["tasks"] if task["id"] not in existing_ids]
            if not gap_tasks:
                sm.transition(PipelineStage.BLOCKED)
                self._update_status(status="waiting_for_human", gate_reason="gap_tasks_empty")
                self.tui.warning("Gap detector reported missing work, but no new tasks were materialized.")
                return False

            merged_manifest = dict(manifest)
            merged_manifest["tasks"] = manifest.get("tasks", []) + gap_tasks
            self._save_task_manifest(store, merged_manifest, new_manifest_version)

            sm.transition(PipelineStage.TASK_REVIEW)
            gap_review_out = self.feature_dir / f"task-review-v{new_manifest_version}.yaml"
            _, review_validation = self._invoke_validated_agent(
                agent="plan-reviewer",
                prompt=prompt_builder.build_task_reviewer(gap_review_out, self._agent_timeout(config, "plan-reviewer")),
                config=config,
                expected_type="task_manifest_review",
                stage=PipelineStage.TASK_REVIEW.value,
                output_path=gap_review_out,
            )
            if not review_validation.valid:
                self._fail_pipeline(sm, f"Gap task review validation failed: {review_validation.errors}")
                return False
            store.save(f"task-review-v{new_manifest_version}.yaml", review_validation.data)
            if review_validation.data["verdict"] != "approved":
                sm.transition(PipelineStage.BLOCKED)
                self._update_status(status="waiting_for_human", gate_reason="gap_task_review")
                return False

            if not self._run_prioritization_and_execution(sm, store, prompt_builder, config):
                sm.transition(PipelineStage.BLOCKED)
                self._update_status(status="waiting_for_human", gate_reason="gap_execution_failed")
                self.tui.warning("Gap re-entry tasks failed; pipeline blocked for human intervention.")
                return False

    def _agent_timeout(self, config: dict, agent: str) -> int:
        """Look up the configured timeout (seconds) for an agent."""
        agent_key = "executor" if agent == "executor" else agent.replace("-", "_")
        agent_config = config.get("agents", {}).get(agent_key, {})
        return agent_config.get("timeout", 600)

    def _invoke_validated_agent(
        self,
        agent: str,
        prompt: str,
        config: dict,
        expected_type: str,
        stage: str,
        task_id: str = "",
        output_path: Path | None = None,
    ) -> tuple[AgentResult, ValidationResult]:
        # Clear stale output so we only read what this invocation writes
        if output_path and output_path.exists():
            output_path.unlink()

        result = self._invoke_agent(agent, prompt, config, stage, task_id)

        # Primary: read artifact from the file the agent wrote
        raw_text = ""
        if output_path and output_path.exists():
            raw_text = output_path.read_text()
        # Fallback: extract from CLI stdout (backward compat)
        if not raw_text:
            raw_text = result.raw_text

        validation = self.validator.validate(raw_text, expected_type)
        if not validation.valid:
            self._report_validation_failure(agent, result, expected_type, validation)
        return result, validation

    def _invoke_agent(
        self,
        agent: str,
        prompt: str,
        config: dict,
        stage: str,
        task_id: str = "",
    ) -> AgentResult:
        self._raise_if_cancelled()
        agent_key = "executor" if agent == "executor" else agent.replace("-", "_")
        agent_config = config.get("agents", {}).get(agent_key, {})

        # Pipeline-wide session: first call is fresh, all subsequent resume
        if not self._pipeline_session_id:
            self._pipeline_session_id = str(uuid.uuid4())
        session_id = self._pipeline_session_id
        is_resume = self._pipeline_session_used
        if not self._pipeline_session_used:
            self._pipeline_session_used = True

        if agent_config.get("command"):
            invocation = AgentInvocation(
                prompt=prompt,
                timeout=agent_config.get("timeout", 600),
                session_id=session_id,
                resume=is_resume,
                cancel_check=self._is_cancelled,
                command_template=agent_config["command"],
                resume_args_template=agent_config.get("resume_args"),
            )
        else:
            model_key = "executor_default" if agent == "executor" else agent.replace("-", "_")
            model = config.get("models", {}).get(model_key, config.get("models", {}).get(agent, "sonnet"))
            timeout = config.get("timeouts", {}).get(agent.replace("-", "_"), config.get("timeouts", {}).get(agent, 600))
            invocation = AgentInvocation(
                prompt=prompt,
                agent=agent,
                model=model,
                timeout=timeout,
                session_id=session_id,
                resume=is_resume,
                cancel_check=self._is_cancelled,
            )

        if self.debug:
            cmd_preview = self.session.preview_cmd(invocation)
            project_slug = str(self.project_dir).replace("/", "-")
            cc_log = f"~/.claude/projects/{project_slug}/{session_id}.jsonl"
            self.tui.debug(f"agent={agent} stage={stage} task={task_id or '-'} timeout={invocation.timeout}s session={session_id}")
            self.tui.debug(f"$ {cmd_preview}")
            self.tui.debug(f"tail -f {cc_log}")
            invocation.debug_tailer = SessionTailer(self.project_dir, session_id)

        try:
            result = self.session.invoke(invocation)
        except subprocess.TimeoutExpired:
            self.tui.warning(f"Agent '{agent}' timed out after {invocation.timeout}s")
            result = AgentResult(
                session_id=session_id,
                exit_code=124,
                stop_reason="timeout",
            )

        # Detect OAuth token expiry — fail fast with actionable guidance
        if result.exit_code != 0 and "invalid api key" in result.raw_text.lower():
            self.tui.error("Authentication failed: OAuth access token expired during pipeline execution.")
            self.tui.error("Fix: add a permanent API key to ~/xpatcher/.env:")
            self.tui.error('  echo "ANTHROPIC_API_KEY=sk-ant-..." >> ~/xpatcher/.env')
            self.tui.error("Or refresh your OAuth token by running `claude` interactively, then retry.")

        self._raise_if_cancelled()
        self.total_cost_usd += result.cost_usd
        self.tui.cost_update(self.total_cost_usd)
        self._update_status(total_cost_usd=self.total_cost_usd)
        log_path = self._write_agent_log(agent, result, task_id)

        if self.debug:
            xp_log = _home_relative(log_path) if log_path else "-"
            self.tui.debug(
                f"done agent={agent} "
                f"{result.duration_ms}ms turns={result.num_turns} ${result.cost_usd:.4f} "
                f"exit={result.exit_code} stop={result.stop_reason} output={len(result.raw_text)}ch"
            )
            self.tui.debug(f"xpatcher log: {xp_log}")

        return result

    def _write_agent_log(self, agent: str, result: AgentResult, task_id: str = "") -> Path | None:
        if self.feature_dir is None:
            return None
        ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        task_suffix = f"-{task_id}" if task_id else ""
        path = self.feature_dir / "logs" / f"agent-{agent}{task_suffix}-{ts}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w") as handle:
            for event in result.events:
                handle.write(json.dumps(event) + "\n")
        return path

    def _run_acceptance_checks(self, task_spec: dict) -> dict:
        failures: list[str] = []
        checks = []
        missing_commands = 0
        for criterion in task_spec.get("acceptance_criteria", []):
            if criterion.get("verification") != "command":
                continue
            command = criterion.get("command", "").strip()
            if not command:
                missing_commands += 1
                continue
            proc = subprocess.run(
                command,
                cwd=str(self.project_dir),
                shell=True,
                capture_output=True,
                text=True,
            )
            checks.append({
                "name": criterion.get("id", command),
                "status": "passed" if proc.returncode == 0 else "failed",
                "duration_ms": 0,
                "error_message": proc.stderr.strip() or proc.stdout.strip(),
            })
            if proc.returncode != 0:
                failures.append(f"{criterion.get('id', 'unknown')}: command failed")

        return {
            "type": "test_result",
            "task_id": task_spec["id"],
            "overall": "pass" if not failures else "fail",
            "test_results": checks,
            "regression_failures": failures,
            "verification_summary": {
                "command_checks": len(checks) + missing_commands,
                "commands_executed": len(checks),
                "missing_commands": missing_commands,
            },
        }

    def _save_task_manifest(self, store: ArtifactStore, manifest: dict, manifest_version: int) -> None:
        store.save("task-manifest.yaml", manifest)
        if manifest_version > 1:
            store.save(f"task-manifest-v{manifest_version}.yaml", manifest)
        self._materialize_task_files(manifest.get("tasks", []))

    def _materialize_task_files(self, tasks: list[dict]) -> None:
        if self.feature_dir is None:
            return
        todo_dir = self.feature_dir / "tasks" / "todo"
        for task in tasks:
            slug = _slugify(task.get("title", task["id"]))
            plain_task = __import__("yaml").safe_dump(task, default_flow_style=False, sort_keys=False)
            existing_todo = self._task_artifact_path(task["id"], "todo")
            if existing_todo is not None:
                target = existing_todo
                if existing_todo.name != f"{task['id']}-{slug}.yaml":
                    target = todo_dir / f"{task['id']}-{slug}.yaml"
                    existing_todo.rename(target)
                target.write_text(plain_task)
                continue
            if self._task_artifact_path(task["id"], "done") or self._task_artifact_path(task["id"], "in-progress"):
                continue
            (todo_dir / f"{task['id']}-{slug}.yaml").write_text(plain_task)

    def _load_task_spec(self, task_id: str) -> dict:
        for location in ("todo", "in-progress", "done"):
            path = self._task_artifact_path(task_id, location)
            if path:
                return load_yaml_file(path)
        raise FileNotFoundError(f"No task spec found for {task_id}")

    def _move_task_artifact(self, task_id: str, source_dir: str, dest_dir: str) -> Path | None:
        path = self._task_artifact_path(task_id, source_dir)
        if path is None:
            return None
        target = self.feature_dir / "tasks" / dest_dir / path.name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(target))
        return target

    def _task_artifact_path(self, task_id: str, subdir: str) -> Path | None:
        if self.feature_dir is None:
            return None
        matches = [
            path
            for path in sorted((self.feature_dir / "tasks" / subdir).glob(f"{task_id}-*.yaml"))
            if "-execution-log" not in path.name and "-quality-" not in path.name and "-review-" not in path.name
        ]
        return matches[0] if matches else None

    def _build_dag(self, tasks: list[dict]) -> TaskDAG:
        dag = TaskDAG.from_tasks(tasks)
        task_states = self.state_file.read().get("task_states", {}) if self.state_file else {}
        for task_id, state in task_states.items():
            if task_id in dag.nodes:
                dag.nodes[task_id].state = TaskState(state)
        for node in dag.nodes.values():
            if node.state in {TaskState.PENDING, TaskState.BLOCKED}:
                if all(dag.nodes[dep].state in {TaskState.SUCCEEDED, TaskState.SKIPPED} for dep in node.dependencies):
                    node.state = TaskState.READY
        return dag

    def _update_task_state(self, task_id: str, state: TaskState) -> None:
        current = self.state_file.read()
        task_states = current.get("task_states", {})
        task_states[task_id] = state.value
        self._update_status(task_states=task_states)

    def _set_loop_history(self, loop_name: str, current: int, maximum: int, verdict: str) -> None:
        state = self.state_file.read()
        iterations = state.get("iterations", {})
        loop_state = iterations.get(loop_name, {"history": []})
        loop_state["current"] = current
        loop_state["max"] = maximum
        loop_state.setdefault("history", []).append({
            "version": current,
            "verdict": verdict,
            "timestamp": _now_iso(),
        })
        iterations[loop_name] = loop_state
        self._update_status(iterations=iterations)

    def _set_quality_iteration(self, task_id: str, current: int, maximum: int) -> None:
        state = self.state_file.read()
        iterations = state.get("iterations", {})
        quality = iterations.get("quality_loop", {})
        quality[task_id] = {"current": current, "max": maximum}
        iterations["quality_loop"] = quality
        self._update_status(iterations=iterations)

    def _record_gap_round(self, depth: int, maximum: int, gap_report: dict) -> None:
        state = self.state_file.read()
        iterations = state.get("iterations", {})
        gap_state = iterations.get("gap_reentry", {"current_depth": 0, "rounds": []})
        gap_state["current_depth"] = depth
        gap_state["max_depth"] = maximum
        gap_state.setdefault("rounds", []).append({
            "round": depth,
            "gap_report": f"gap-report-v{depth}.yaml",
            "verdict": gap_report.get("verdict", ""),
        })
        iterations["gap_reentry"] = gap_state
        self._update_status(iterations=iterations)

    def _findings_hash(self, findings: list[str]) -> str:
        canonical = "\n".join(sorted(findings))
        return hashlib.sha256(canonical.encode()).hexdigest()

    def _enrich_execution_result(self, data: dict) -> dict:
        enriched = dict(data)
        enriched.update(self._git_branch_trace())
        commits = enriched.get("commits", [])
        if commits:
            enriched["task_commit_hash"] = commits[-1].get("hash", "")
        return enriched

    def _git_branch_trace(self) -> dict[str, str | bool]:
        branch_name = self._git_output(["git", "branch", "--show-current"])
        branch_head_commit = self._git_output(["git", "rev-parse", "HEAD"])
        upstream_branch = self._git_output(["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"])
        upstream_head_commit = self._git_output(["git", "rev-parse", "@{upstream}"]) if upstream_branch else ""
        branch_pushed = bool(branch_head_commit and upstream_head_commit and branch_head_commit == upstream_head_commit)
        return {
            "branch_name": branch_name,
            "branch_head_commit": branch_head_commit,
            "upstream_branch": upstream_branch,
            "upstream_head_commit": upstream_head_commit,
            "branch_pushed": branch_pushed,
        }

    def _git_output(self, command: list[str]) -> str:
        proc = subprocess.run(command, cwd=str(self.project_dir), capture_output=True, text=True)
        if proc.returncode != 0:
            return ""
        return proc.stdout.strip()

    def _only_verification_spec_failures(self, test_report: dict) -> bool:
        failures = test_report.get("regression_failures", [])
        return bool(failures) and all("missing command" in failure for failure in failures)

    def _review_task_without_command_checks(self, task_id: str, prompt_builder: PromptBuilder, store: ArtifactStore, config: dict) -> bool:
        self.tui.info(f"{task_id}: acceptance commands are missing; falling back to spec review instead of looping code fixes.")
        existing_reviews = sorted((self.feature_dir / "tasks" / "done").glob(f"{task_id}-review-v*.yaml"))
        version = len(existing_reviews) + 1
        review_out = self.feature_dir / "tasks" / "done" / f"{task_id}-review-v{version}.yaml"
        review_out.parent.mkdir(parents=True, exist_ok=True)
        _, review_validation = self._invoke_validated_agent(
            agent="reviewer",
            prompt=prompt_builder.build_reviewer(task_id, review_out, self._agent_timeout(config, "reviewer")),
            config=config,
            expected_type="review",
            stage=PipelineStage.PER_TASK_QUALITY.value,
            task_id=task_id,
            output_path=review_out,
        )
        if not review_validation.valid:
            self._update_task_state(task_id, TaskState.FAILED)
            return False
        store.save(f"tasks/done/{task_id}-review-v{version}.yaml", review_validation.data)
        if review_validation.data["verdict"] == "approve":
            return True
        self._update_task_state(task_id, TaskState.BLOCKED)
        return False

    def _is_cancelled(self) -> bool:
        if self.state_file is None:
            return False
        state = self.state_file.read()
        return state.get("current_stage") == PipelineStage.CANCELLED.value or state.get("status") == "cancelled"

    def _raise_if_cancelled(self) -> None:
        if self._is_cancelled():
            raise CancelledPipelineError("Pipeline cancelled")

    def _handle_interrupt(self, sm: PipelineStateMachine, pipeline_id: str) -> None:
        """Handle Ctrl+C: cancel pipeline, kill agent processes, switch branch, update state."""
        self.tui.warning(f"\nInterrupted. Cleaning up pipeline {pipeline_id}...")
        sm.transition(PipelineStage.CANCELLED)
        self._update_status(status="cancelled", cancelled_at=_now_iso())
        if self.feature_dir:
            _kill_session_processes(self.feature_dir)
        # Switch back to default branch
        for default in ("main", "master"):
            check = subprocess.run(
                ["git", "rev-parse", "--verify", default],
                cwd=str(self.project_dir), capture_output=True, text=True,
            )
            if check.returncode == 0:
                subprocess.run(
                    ["git", "checkout", default],
                    cwd=str(self.project_dir), capture_output=True, text=True,
                )
                self.tui.status(f"Switched back to {default}")
                break
        self.tui.warning(f"Pipeline {pipeline_id} cancelled.")

    def _report_validation_failure(
        self,
        agent: str,
        result: AgentResult,
        expected_type: str,
        validation: ValidationResult,
    ) -> None:
        """Log detailed diagnostics when an agent produces invalid output."""
        session_id = result.session_id or "(unknown)"
        project_slug = str(self.project_dir).replace("/", "-")
        cc_log = f"~/.claude/projects/{project_slug}/{session_id}.jsonl"

        self.tui.error(f"Agent '{agent}' returned invalid output")
        self.tui.error(f"  Expected artifact type: {expected_type}")
        for err in validation.errors:
            self.tui.error(f"  - {err}")
        self.tui.error(f"  Session: {session_id}")
        self.tui.error(f"  Duration: {result.duration_ms}ms | Turns: {result.num_turns} | Cost: ${result.cost_usd:.4f}")
        self.tui.error(f"  Claude Code log: {cc_log}")

        # Show what the agent actually produced
        raw = result.raw_text or "(empty response)"
        if len(raw) > 2000:
            raw = raw[:2000] + f"\n... ({len(raw)} chars total, truncated)"
        self.tui.error(f"  Raw output:\n{raw}")

    def _fail_pipeline(self, sm: PipelineStateMachine, message: str) -> None:
        self.tui.error(message)
        sm.transition(PipelineStage.FAILED)
        self._update_status(status="failed")

    def _handle_completion_gate(self, sm: PipelineStateMachine, store: ArtifactStore, transition_stage: bool = True) -> None:
        if transition_stage:
            sm.transition(PipelineStage.COMPLETION)
        self.tui.cost_summary(self.total_cost_usd)
        if not self._requires_completion_confirmation():
            self.tui.stage("Stage 16: Completion Summary")
            sm.transition(PipelineStage.DONE)
            self._update_status(status="completed", gate_reason="")
            store.save(
                "completion.yaml",
                {
                    "status": "completed",
                    "auto_completed": True,
                    "total_cost_usd": self.total_cost_usd,
                    "completed_at": _now_iso(),
                },
            )
            self.tui.success("Pipeline completed successfully!")
            return

        self._update_status(status="waiting_for_human", gate_reason="completion", waiting_since=_now_iso())
        self.tui.human_gate("Stage 16: Completion Review Required")
        approved = self.tui.prompt_approval("Approve completion? [y/n]: ")
        if approved:
            sm.transition(PipelineStage.DONE)
            self._update_status(status="completed", gate_reason="")
            store.save(
                "completion.yaml",
                {
                    "status": "completed",
                    "auto_completed": False,
                    "total_cost_usd": self.total_cost_usd,
                    "completed_at": _now_iso(),
                },
            )
            self.tui.success("Pipeline completed successfully!")
        else:
            sm.transition(PipelineStage.PAUSED)
            self._update_status(status="paused", gate_reason="completion")

    def _initialize_feature_dir(self, feature_dir: Path) -> None:
        feature_dir.mkdir(parents=True, exist_ok=True)
        (feature_dir / "tasks" / "todo").mkdir(parents=True, exist_ok=True)
        (feature_dir / "tasks" / "in-progress").mkdir(parents=True, exist_ok=True)
        (feature_dir / "tasks" / "done").mkdir(parents=True, exist_ok=True)
        (feature_dir / "logs").mkdir(parents=True, exist_ok=True)
        (feature_dir / "decisions").mkdir(parents=True, exist_ok=True)

    def _update_status(self, **fields) -> None:
        if self.state_file is not None:
            self.state_file.update(**fields)

    def _load_config(self) -> dict:
        config = load_yaml_file(self.xpatcher_home / "config.yaml")
        config.setdefault("human_gates", {})
        config["human_gates"].setdefault("spec_confirmation", False)
        config["human_gates"].setdefault("completion_confirmation", False)
        return config

    def _requires_plan_confirmation(self, config: dict) -> bool:
        if config.get("human_gates", {}).get("spec_confirmation", False):
            return True
        if self.feature_dir is None:
            return False
        intent = load_yaml_file(self.feature_dir / "intent.yaml")
        return bool(intent.get("clarifying_questions"))

    def _requires_completion_confirmation(self) -> bool:
        config = self._load_config()
        return bool(config.get("human_gates", {}).get("completion_confirmation", False))


def _show_status(args, xpatcher_home):
    pipeline_id = getattr(args, "pipeline_id", None)
    if pipeline_id:
        record = _find_pipeline_record(xpatcher_home, pipeline_id)
        if record is None:
            print(f"Unknown pipeline: {pipeline_id}")
            return
        state = load_yaml_file(Path(record["feature_dir"]) / "pipeline-state.yaml")
        print(f"{state.get('pipeline_id', '?')}  {state.get('feature', '?')}")
        print(f"  Stage:  {state.get('current_stage', '?')}")
        print(f"  Status: {state.get('status', '?')}")
        print(f"  Cost:   ${state.get('total_cost_usd', 0):.4f}")
        return

    for pid, record in sorted(_find_all_pipeline_records(xpatcher_home).items()):
        state = load_yaml_file(Path(record["feature_dir"]) / "pipeline-state.yaml")
        print(f"{pid}  {state.get('feature', '?')}  {state.get('current_stage', '?')}  {state.get('status', '?')}")


def _list_pipelines(xpatcher_home):
    for pid, record in sorted(_find_all_pipeline_records(xpatcher_home).items()):
        state = load_yaml_file(Path(record["feature_dir"]) / "pipeline-state.yaml")
        print(f"{pid}  {state.get('feature', '?')}  {state.get('status', '?')}  ${state.get('total_cost_usd', 0):.4f}")


def _cancel_pipeline(args, xpatcher_home):
    record = _find_pipeline_record(xpatcher_home, args.pipeline_id)
    if record is None:
        print(f"Unknown pipeline: {args.pipeline_id}")
        return 1
    feature_dir = Path(record["feature_dir"])
    state_file = PipelineStateFile(str(feature_dir / "pipeline-state.yaml"))
    sm = PipelineStateMachine(state_file)
    sm.transition(PipelineStage.CANCELLED)
    state_file.update(status="cancelled", gate_reason="", cancelled_at=_now_iso())

    # Kill orphaned Claude CLI processes
    _kill_session_processes(feature_dir)

    print(f"Cancelled pipeline {args.pipeline_id}")
    return 0


def _kill_session_processes(feature_dir: Path) -> None:
    """Kill any Claude CLI processes associated with sessions in this pipeline."""
    sessions_path = feature_dir / "sessions.yaml"
    if not sessions_path.exists():
        return
    sessions_data = load_yaml_file(sessions_path)
    for sid in sessions_data.get("sessions", {}):
        try:
            ps = subprocess.run(
                ["pgrep", "-f", sid],
                capture_output=True, text=True,
            )
            for pid_str in ps.stdout.strip().splitlines():
                pid = int(pid_str)
                os.kill(pid, signal.SIGTERM)
                print(f"  Killed process {pid} (session {sid[:12]}...)")
        except (ValueError, ProcessLookupError, OSError):
            pass


def _delete_pipeline(args, xpatcher_home):
    pipeline_id = args.pipeline_id
    record = _find_pipeline_record(xpatcher_home, pipeline_id)
    if record is None:
        print(f"Unknown pipeline: {pipeline_id}")
        return 1

    feature_dir = Path(record["feature_dir"])
    project_dir = Path(record["project_dir"])

    # 1. Cancel if running, and kill orphaned Claude CLI processes
    state = load_yaml_file(feature_dir / "pipeline-state.yaml")
    if state.get("status") == "running":
        state_file = PipelineStateFile(str(feature_dir / "pipeline-state.yaml"))
        state_file.update(status="cancelled")
        print(f"  Cancelled running pipeline")

    _kill_session_processes(feature_dir)

    # 2. Read branch name before deleting state
    branch_name = state.get("branch_name", "")

    # 2. Remove feature directory (artifacts, logs, state)
    if feature_dir.exists():
        shutil.rmtree(feature_dir)
        print(f"  Removed {feature_dir}")

    # Clean up parent if empty
    parent = feature_dir.parent
    if parent.exists() and not any(parent.iterdir()):
        parent.rmdir()

    # 3. Remove pipeline entry from index
    for path in _iter_pipeline_indices(xpatcher_home):
        data = _load_pipeline_index_file(path)
        if pipeline_id in data.get("pipelines", {}):
            del data["pipelines"][pipeline_id]
            if data["pipelines"]:
                _save_pipeline_index_file(path, data)
            else:
                path.unlink()
                print(f"  Removed {path}")
            break

    # 4. Delete git branch from target repo
    if branch_name and project_dir.is_dir():
        # Switch off the branch if it's currently checked out
        head = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(project_dir), capture_output=True, text=True,
        )
        if head.returncode == 0 and head.stdout.strip() == branch_name:
            # Find default branch (main or master)
            for default in ("main", "master"):
                check = subprocess.run(
                    ["git", "rev-parse", "--verify", default],
                    cwd=str(project_dir), capture_output=True, text=True,
                )
                if check.returncode == 0:
                    subprocess.run(
                        ["git", "checkout", default],
                        cwd=str(project_dir), capture_output=True, text=True,
                    )
                    print(f"  Switched to {default}")
                    break

        result = subprocess.run(
            ["git", "branch", "-D", branch_name],
            cwd=str(project_dir),
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            print(f"  Deleted branch {branch_name}")
        elif "not found" not in result.stderr:
            print(f"  Branch {branch_name}: {result.stderr.strip()}")

    print(f"Deleted pipeline {pipeline_id}")
    return 0


def _skip_tasks(args, xpatcher_home):
    record = _find_pipeline_record(xpatcher_home, args.pipeline_id)
    if record is None:
        print(f"Unknown pipeline: {args.pipeline_id}")
        return 1

    feature_dir = Path(record["feature_dir"])
    state_file = PipelineStateFile(str(feature_dir / "pipeline-state.yaml"))
    state = state_file.read()
    manifest = load_yaml_file(feature_dir / "task-manifest.yaml")
    dag = TaskDAG.from_tasks(manifest.get("tasks", []))
    task_states = state.get("task_states", {})
    for task_id, value in task_states.items():
        if task_id in dag.nodes:
            dag.nodes[task_id].state = TaskState(value)

    skipped_records = state.get("skipped_tasks", [])
    for task_id in [item.strip() for item in args.task_ids.split(",") if item.strip()]:
        if task_id not in dag.nodes:
            print(f"Unknown task: {task_id}")
            continue
        if dag.nodes[task_id].state not in {TaskState.STUCK, TaskState.FAILED, TaskState.BLOCKED}:
            print(f"Cannot skip task {task_id} from state {dag.nodes[task_id].state.value}")
            continue
        dag.mark_skipped(task_id, force_unblock=args.force_unblock)
        task_states[task_id] = TaskState.SKIPPED.value
        blocked = [dep for dep in dag.nodes[task_id].dependents if dag.nodes[dep].state == TaskState.BLOCKED]
        for dep in dag.nodes[task_id].dependents:
            task_states[dep] = dag.nodes[dep].state.value
        skipped_records.append({
            "task_id": task_id,
            "skipped_at": _now_iso(),
            "previous_state": state.get("task_states", {}).get(task_id, ""),
            "reason": "User skip via CLI",
            "force_unblock": args.force_unblock,
            "dependents_blocked": blocked,
        })

    state_file.update(task_states=task_states, skipped_tasks=skipped_records)
    print(f"Updated skip state for pipeline {args.pipeline_id}")
    return 0


def _show_pending(xpatcher_home):
    any_pending = False
    for pid, record in sorted(_find_all_pipeline_records(xpatcher_home).items()):
        state = load_yaml_file(Path(record["feature_dir"]) / "pipeline-state.yaml")
        if state.get("status") not in {"waiting_for_human", "paused"}:
            continue
        any_pending = True
        print(f"{pid}  {state.get('feature', '?')}")
        print(f"  Gate: {state.get('gate_reason', 'human_input')}")
        print(f"  Stage: {state.get('current_stage', '?')}")
        print(f"  Action: xpatcher resume {pid}")
    if not any_pending:
        print("No pipelines awaiting human input.")


def _show_logs(args, xpatcher_home):
    record = _find_pipeline_record(xpatcher_home, args.pipeline_id)
    if record is None:
        print(f"Unknown pipeline: {args.pipeline_id}")
        return 1
    log_dir = Path(record["feature_dir"]) / "logs"
    files = sorted(log_dir.glob("agent-*.jsonl"))
    if args.agent:
        files = [path for path in files if f"agent-{args.agent}" in path.name]
    if args.task:
        files = [path for path in files if f"-{args.task}-" in path.name]
    if not files:
        print("No matching logs found.")
        return 0
    lines = []
    for path in files:
        lines.extend(path.read_text().splitlines())
    for line in lines[-args.tail:]:
        print(line)
    return 0


def _resolve_description(args) -> str:
    """Resolve feature description from positional arg, --file, or stdin."""
    if args.file is not None:
        if str(args.file) == "-":
            if sys.stdin.isatty():
                return ""
            return sys.stdin.read().strip()
        path = Path(args.file)
        if not path.is_file():
            print(f"Error: file not found: {path}", file=sys.stderr)
            sys.exit(1)
        return path.read_text().strip()
    if args.description:
        return args.description
    if not sys.stdin.isatty():
        return sys.stdin.read().strip()
    return ""


def main():
    parser = argparse.ArgumentParser(prog="xpatcher", description="Specification-driven development automation pipeline")
    subparsers = parser.add_subparsers(dest="command")

    start_parser = subparsers.add_parser("start", help="Start a new pipeline")
    start_parser.add_argument("description", nargs="?", default=None, help="Feature description (omit when using --file or stdin)")
    start_parser.add_argument("--file", "-f", type=Path, default=None, help="Read feature description from a file (use - for stdin)")
    start_parser.add_argument("--project", type=Path, default=Path.cwd())
    start_parser.add_argument("--verbose", action="store_true")
    start_parser.add_argument("--debug", action="store_true", help="Show detailed agent execution info (commands, session IDs, timings, output)")

    resume_parser = subparsers.add_parser("resume", help="Resume an interrupted pipeline")
    resume_parser.add_argument("pipeline_id")
    resume_parser.add_argument("--debug", action="store_true", help="Show detailed agent execution info")

    status_parser = subparsers.add_parser("status", help="Show pipeline status")
    status_parser.add_argument("pipeline_id", nargs="?")

    subparsers.add_parser("list", help="List all pipelines")

    cancel_parser = subparsers.add_parser("cancel", help="Cancel a pipeline")
    cancel_parser.add_argument("pipeline_id")

    skip_parser = subparsers.add_parser("skip", help="Skip stuck tasks")
    skip_parser.add_argument("pipeline_id")
    skip_parser.add_argument("task_ids", help="Comma-separated task IDs")
    skip_parser.add_argument("--force-unblock", action="store_true")

    delete_parser = subparsers.add_parser("delete", help="Delete a pipeline and all its artifacts")
    delete_parser.add_argument("pipeline_id")

    subparsers.add_parser("pending", help="Show pipelines awaiting human input")

    logs_parser = subparsers.add_parser("logs", help="View agent logs")
    logs_parser.add_argument("pipeline_id")
    logs_parser.add_argument("--agent", default=None)
    logs_parser.add_argument("--task", default=None)
    logs_parser.add_argument("--tail", type=int, default=50)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    xpatcher_home = Path(os.environ.get("XPATCHER_HOME", Path.home() / "xpatcher"))

    if args.command == "start":
        description = _resolve_description(args)
        if not description:
            print("Error: provide a description, --file, or pipe to stdin", file=sys.stderr)
            sys.exit(1)
        Dispatcher(args.project, xpatcher_home, debug=args.debug).start(description, verbose=args.verbose, debug=args.debug)
        return
    if args.command == "resume":
        record = _find_pipeline_record(xpatcher_home, args.pipeline_id)
        project_dir = Path(record["project_dir"]) if record else Path.cwd()
        Dispatcher(project_dir, xpatcher_home, debug=args.debug).resume(args.pipeline_id)
        return
    if args.command == "status":
        _show_status(args, xpatcher_home)
        return
    if args.command == "list":
        _list_pipelines(xpatcher_home)
        return
    if args.command == "cancel":
        sys.exit(_cancel_pipeline(args, xpatcher_home))
    if args.command == "skip":
        sys.exit(_skip_tasks(args, xpatcher_home))
    if args.command == "delete":
        sys.exit(_delete_pipeline(args, xpatcher_home))
    if args.command == "pending":
        _show_pending(xpatcher_home)
        return
    if args.command == "logs":
        sys.exit(_show_logs(args, xpatcher_home))


if __name__ == "__main__":
    main()

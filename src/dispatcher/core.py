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
from .command_validation import prepare_acceptance_command
from .state import PipelineStage, PipelineStateFile, PipelineStateMachine, TaskDAG, TaskState
from .session import AgentInvocation, AgentResult, ClaudeSession, SessionTailer
from .schemas import ArtifactValidator, SCHEMAS, TaskDefinition, ValidationResult
from .lanes import LaneManager
from .budget import BudgetManager
from .tui import TUIRenderer
from .yaml_utils import load_yaml_file
from ..artifacts.store import ArtifactStore
from ..context.builder import PromptBuilder
from ..context.packets import ContextManager
from ..context.contracts import build_contract_block


class CancelledPipelineError(RuntimeError):
    """Raised when a pipeline is cancelled while a dispatcher is running."""


class BudgetExceededError(RuntimeError):
    """Raised when a configured budget cap prevents further execution."""


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


def _branch_name_for(feature_slug: str, pipeline_id: str) -> str:
    return f"xpatcher/{feature_slug}-{pipeline_id}"


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
        # v2 components (initialized per-pipeline)
        self.lanes: LaneManager | None = None
        self.budget: BudgetManager | None = None
        self.context_mgr: ContextManager | None = None

    def _require_auth(self):
        """Show auth source and abort if no credentials were resolved."""
        env_has_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
        label = describe_auth_source(self._auth_env, env_has_key=env_has_key)
        if label == "none":
            env_path = self.xpatcher_home / ".env"
            self.tui.error("No authentication found. Cannot proceed.")
            self.tui.info(f"  Either add ANTHROPIC_API_KEY to {env_path}")
            self.tui.info("  or log in interactively: run 'claude' and complete login")
            sys.exit(1)
        self.tui.success(f"Auth: {label}")

    def _feature_dir_for(self, feature_slug: str, pipeline_id: str | None = None) -> Path:
        base = self.xpatcher_home / ".xpatcher" / "projects" / _project_storage_slug(self.project_dir)
        if pipeline_id:
            return base / f"{feature_slug}--{pipeline_id}"
        return base / feature_slug

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
        branch_name = _branch_name_for(feature_slug, pipeline_id)
        feature_dir = self._feature_dir_for(feature_slug, pipeline_id)
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
            "dispatcher_pid": os.getpid(),
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
        config = self._load_config()
        prompt_builder = PromptBuilder(feature_dir, self.project_dir, v2_mode=self._is_v2(config))

        self.state_file = state_file
        self.feature_dir = feature_dir
        self.total_cost_usd = 0.0
        # v1 fallback: single session for the entire pipeline
        self._pipeline_session_id = str(uuid.uuid4())
        self._pipeline_session_used = False
        self.lanes = None
        self.budget = None
        self.context_mgr = None
        # v2: lane-scoped sessions, budget, context
        if self._is_v2(config):
            if self._use_lanes(config):
                self.lanes = LaneManager(feature_dir, config)
            self.budget = BudgetManager(config)
            if self._use_bootstrap_artifacts(config):
                self.context_mgr = ContextManager(feature_dir, self.project_dir)

        branch_result = subprocess.run(
            ["git", "checkout", "-b", branch_name],
            cwd=str(self.project_dir),
            capture_output=True,
            text=True,
        )
        if branch_result.returncode != 0:
            self.tui.error(f"Failed to create branch {branch_name}: {branch_result.stderr.strip()}")
            sys.exit(1)

        self.tui.configure_live_dashboard(self._use_live_dashboard(config))
        self.tui.set_pipeline(pipeline_id, description)
        self.tui.header(f"Pipeline {pipeline_id}: {description}")
        try:
            self._run_pipeline(sm, store, prompt_builder, config, description, verbose, debug)
        except BudgetExceededError as exc:
            self._handle_budget_exhausted(sm, str(exc))
        except CancelledPipelineError:
            self.tui.warning(f"Pipeline {pipeline_id} was cancelled. Dispatcher exited cleanly.")
        except KeyboardInterrupt:
            self._handle_interrupt(sm, pipeline_id)
        except Exception as exc:
            self._fail_pipeline(sm, f"Unhandled dispatcher error: {exc}")
            raise

    # Stages that --from-stage accepts, mapped to the pipeline method entry points
    _RESUMABLE_STAGES = {
        "intent_capture": 1,
        "planning": 2,
        "plan_review": 3,
        "plan_approval": 5,
        "task_breakdown": 6,
        "task_execution": 9,
        "gap_detection": 14,
        "documentation": 15,
        "completion": 16,
    }

    def resume(self, pipeline_id: str, from_stage: str | None = None, debug: bool = False):
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
        state = self.state_file.read()
        self.total_cost_usd = state.get("total_cost_usd", 0.0)
        self._pipeline_session_id = str(uuid.uuid4())
        self._pipeline_session_used = False
        self.debug = debug
        self.lanes = None
        self.budget = None
        self.context_mgr = None
        # v2 components for resume
        if self._is_v2(config):
            if self._use_lanes(config):
                self.lanes = LaneManager(feature_dir, config)
            self.budget = BudgetManager(config)
            self._restore_v2_state(state)
            if self._use_bootstrap_artifacts(config):
                self.context_mgr = ContextManager(feature_dir, self.project_dir)

        current = PipelineStage(state.get("current_stage", PipelineStage.UNINITIALIZED.value))
        gate_reason = state.get("gate_reason", "")
        self.tui.configure_live_dashboard(self._use_live_dashboard(config))
        self.tui.set_pipeline(pipeline_id, state.get("description", ""))
        self.tui.set_stage(current.value)
        self.tui.header(f"Resuming {pipeline_id}")
        self._require_auth()

        try:
            # --from-stage: jump to a specific stage using existing artifacts
            if from_stage:
                self._resume_from_stage(from_stage, feature_dir, config, state)
                return

            if gate_reason == "plan_approval" and current in {PipelineStage.PAUSED, PipelineStage.PLAN_APPROVAL}:
                sm = PipelineStateMachine(self.state_file)
                store = ArtifactStore(feature_dir)
                prompt_builder = PromptBuilder(feature_dir, self.project_dir, v2_mode=self._is_v2(config))
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
                self.tui.stage("Stage 15: Documentation", stage_key=PipelineStage.DOCUMENTATION.value)
                docs_out = self.feature_dir / "docs-report.yaml"
                _, validation = self._invoke_validated_stage(
                    prompt=prompt_builder.build_tech_writer(docs_out, self._stage_timeout(config)),
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
                    "Resume supports paused human gates and --from-stage for stage-level recovery.\n"
                    f"  Available stages: {', '.join(self._RESUMABLE_STAGES)}\n"
                    "  Example: xpatcher resume <id> --from-stage plan_review"
                )
        except BudgetExceededError as exc:
            self._handle_budget_exhausted(PipelineStateMachine(self.state_file), str(exc))
        except CancelledPipelineError:
            self.tui.warning(f"Pipeline {pipeline_id} was cancelled. Dispatcher exited cleanly.")
        except KeyboardInterrupt:
            sm = PipelineStateMachine(self.state_file)
            self._handle_interrupt(sm, pipeline_id)
        except Exception as exc:
            sm = PipelineStateMachine(self.state_file)
            self._fail_pipeline(sm, f"Unhandled dispatcher error: {exc}")
            raise

    def _resume_from_stage(self, from_stage: str, feature_dir: Path, config: dict, state: dict):
        """Resume pipeline execution from a specific stage, reusing existing artifacts."""
        if from_stage not in self._RESUMABLE_STAGES:
            self.tui.error(f"Unknown stage: {from_stage}")
            self.tui.info(f"Available stages: {', '.join(self._RESUMABLE_STAGES)}")
            return

        target = PipelineStage(from_stage)
        sm = PipelineStateMachine(self.state_file)
        store = ArtifactStore(feature_dir)
        prompt_builder = PromptBuilder(feature_dir, self.project_dir, v2_mode=self._is_v2(config))
        description = state.get("description", "")

        # Validate required artifacts exist for the target stage
        missing = self._check_stage_prerequisites(target, store)
        if missing:
            self.tui.error(f"Cannot resume from {from_stage} — missing artifacts:")
            for m in missing:
                self.tui.error(f"  - {m}")
            return

        # Force state to PAUSED so we can transition to any stage
        current = PipelineStage(state.get("current_stage", PipelineStage.UNINITIALIZED.value))
        if current != PipelineStage.PAUSED:
            sm.transition(PipelineStage.PAUSED)

        self._update_status(status="running", gate_reason="")
        stage_num = self._RESUMABLE_STAGES[from_stage]
        self.tui.info(f"Resuming from Stage {stage_num}: {from_stage}")

        plan_version = store.latest_version("plan") or 1
        manifest_version = max(1, store.latest_version("task-manifest"))

        if target == PipelineStage.INTENT_CAPTURE:
            sm.transition(PipelineStage.INTENT_CAPTURE)
            self._run_pipeline(sm, store, prompt_builder, config, description, verbose=False, debug=self.debug)

        elif target == PipelineStage.PLANNING:
            sm.transition(PipelineStage.PLANNING)
            self.tui.stage("Stage 2: Planning", stage_key=PipelineStage.PLANNING.value)
            next_version = plan_version + 1
            plan_path = self.feature_dir / f"plan-v{next_version}.yaml"
            _, validation = self._invoke_validated_stage(
                prompt=prompt_builder.build_planner(plan_path, self._stage_timeout(config)),
                config=config,
                expected_type="plan",
                stage=PipelineStage.PLANNING.value,
                output_path=plan_path,
            )
            if not validation.valid:
                self._fail_pipeline(sm, f"Plan validation failed: {validation.errors}")
                return
            store.save(f"plan-v{next_version}.yaml", validation.data)
            plan_version = self._run_plan_review_loop(sm, store, prompt_builder, config, starting_version=next_version)
            if plan_version is None:
                return
            self._continue_from_plan_approved(sm, store, prompt_builder, config, plan_version)

        elif target == PipelineStage.PLAN_REVIEW:
            plan_version = self._run_plan_review_loop(sm, store, prompt_builder, config, starting_version=plan_version)
            if plan_version is None:
                return
            self._continue_from_plan_approved(sm, store, prompt_builder, config, plan_version)

        elif target == PipelineStage.PLAN_APPROVAL:
            self._continue_from_plan_approved(sm, store, prompt_builder, config, plan_version)

        elif target == PipelineStage.TASK_BREAKDOWN:
            manifest_version = self._run_task_breakdown_and_review(
                sm, store, prompt_builder, config,
                plan_version=plan_version,
                manifest_version=manifest_version,
            )
            if manifest_version is None:
                return
            if not self._run_prioritization_and_execution(sm, store, prompt_builder, config):
                return
            self._continue_from_gap_detection(sm, store, prompt_builder, config, plan_version)

        elif target == PipelineStage.TASK_EXECUTION:
            if not self._run_prioritization_and_execution(sm, store, prompt_builder, config):
                return
            self._continue_from_gap_detection(sm, store, prompt_builder, config, plan_version)

        elif target == PipelineStage.GAP_DETECTION:
            self._continue_from_gap_detection(sm, store, prompt_builder, config, plan_version)

        elif target == PipelineStage.DOCUMENTATION:
            sm.transition(PipelineStage.DOCUMENTATION)
            self.tui.stage("Stage 15: Documentation", stage_key=PipelineStage.DOCUMENTATION.value)
            docs_out = self.feature_dir / "docs-report.yaml"
            _, validation = self._invoke_validated_stage(
                prompt=prompt_builder.build_tech_writer(docs_out, self._stage_timeout(config)),
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

        elif target == PipelineStage.COMPLETION:
            sm.transition(PipelineStage.COMPLETION)
            self._handle_completion_gate(sm, store, transition_stage=False)

    def _continue_from_plan_approved(self, sm, store, prompt_builder, config, plan_version):
        """Continue pipeline from after plan approval through to completion."""
        if not self._handle_plan_approval(sm, store, prompt_builder, config, plan_version=plan_version):
            return
        manifest_version = self._run_task_breakdown_and_review(
            sm, store, prompt_builder, config,
            plan_version=plan_version,
            manifest_version=max(1, store.latest_version("task-manifest")),
        )
        if manifest_version is None:
            return
        if not self._run_prioritization_and_execution(sm, store, prompt_builder, config):
            return
        self._continue_from_gap_detection(sm, store, prompt_builder, config, plan_version)

    def _continue_from_gap_detection(self, sm, store, prompt_builder, config, plan_version):
        """Continue pipeline from gap detection through to completion."""
        if not self._run_gap_detection_with_reentry(sm, store, prompt_builder, config, plan_version):
            return
        sm.transition(PipelineStage.DOCUMENTATION)
        self.tui.stage("Stage 15: Documentation", stage_key=PipelineStage.DOCUMENTATION.value)
        docs_out = self.feature_dir / "docs-report.yaml"
        _, validation = self._invoke_validated_stage(
            prompt=prompt_builder.build_tech_writer(docs_out, self._stage_timeout(config)),
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

    def _check_stage_prerequisites(self, target: PipelineStage, store: ArtifactStore) -> list[str]:
        """Check that required artifacts exist for resuming from a given stage."""
        missing = []
        intent_required = {
            PipelineStage.PLANNING, PipelineStage.PLAN_REVIEW, PipelineStage.PLAN_APPROVAL,
            PipelineStage.TASK_BREAKDOWN, PipelineStage.TASK_EXECUTION,
            PipelineStage.GAP_DETECTION, PipelineStage.DOCUMENTATION,
        }
        plan_required = {
            PipelineStage.PLAN_REVIEW, PipelineStage.PLAN_APPROVAL,
            PipelineStage.TASK_BREAKDOWN, PipelineStage.TASK_EXECUTION,
            PipelineStage.GAP_DETECTION, PipelineStage.DOCUMENTATION,
        }
        manifest_required = {
            PipelineStage.TASK_EXECUTION, PipelineStage.GAP_DETECTION,
            PipelineStage.DOCUMENTATION,
        }

        if target in intent_required and not (self.feature_dir / "intent.yaml").exists():
            missing.append("intent.yaml")
        if target in plan_required and store.latest_version("plan") == 0:
            missing.append("plan-v*.yaml")
        if target in manifest_required and not (self.feature_dir / "task-manifest.yaml").exists():
            missing.append("task-manifest.yaml")
        return missing

    def _is_v2(self, config: dict | None = None) -> bool:
        """Check if v2 mode is enabled."""
        if config is None:
            config = self._load_config()
        return config.get("pipeline", {}).get("mode", "v1") == "v2"

    def _has_severity_gate(self, config: dict) -> bool:
        """Check if severity-gated review is enabled."""
        return config.get("reviews", {}).get("severity_gate", False) and self._is_v2(config)

    def _has_delta_gaps(self, config: dict) -> bool:
        """Check if delta-based gap handling is enabled."""
        return config.get("gaps", {}).get("delta_mode", False) and self._is_v2(config)

    def _use_lanes(self, config: dict) -> bool:
        return self._is_v2(config) and config.get("sessions", {}).get("use_lanes", True)

    def _use_bootstrap_artifacts(self, config: dict) -> bool:
        return self._is_v2(config) and config.get("context", {}).get("use_bootstrap_artifacts", True)

    def _use_generated_contracts(self, config: dict) -> bool:
        return self._is_v2(config) and config.get("contracts", {}).get("generated", True)

    def _persist_validation_failures(self, config: dict) -> bool:
        return config.get("validation", {}).get("persist_failures", True)

    def _use_live_dashboard(self, config: dict) -> bool:
        return config.get("ui", {}).get("live_dashboard", True) and not self.debug

    def _review_severity_allows_continue(self, review_data: dict) -> bool:
        """Check if review findings are minor-only (auto-approve)."""
        if review_data.get("verdict") == "approved":
            return True
        findings = review_data.get("findings", [])
        if not findings:
            return False
        severities = {f.get("severity", "critical") for f in findings}
        # If only minor and nit findings, auto-approve
        return severities <= {"minor", "nit"}

    def _restore_v2_state(self, state: dict) -> None:
        if self.budget:
            persisted_costs = state.get("budget_costs")
            if not isinstance(persisted_costs, dict):
                legacy_costs = state.get("budget_checkpoints")
                persisted_costs = legacy_costs if isinstance(legacy_costs, dict) else {}
            self.budget.load_costs(persisted_costs)

    def _budget_scopes_for(self, stage: str, task_id: str = "") -> list[str]:
        scopes: list[str] = []
        if self.lanes:
            scopes.append(self.lanes.lane_for_stage(stage, task_id))
        if self.budget:
            scopes.append("pipeline")
        return scopes

    def _remaining_budget_usd(self, stage: str, task_id: str = "") -> float | None:
        if not self.budget:
            return None
        remaining_values = []
        for scope in self._budget_scopes_for(stage, task_id):
            remaining = self.budget.remaining(scope)
            if remaining is not None:
                remaining_values.append((scope, remaining))
        if not remaining_values:
            return None
        min_scope, min_remaining = min(remaining_values, key=lambda item: item[1])
        if min_remaining <= 0:
            raise BudgetExceededError(f"Budget exhausted for {min_scope}")
        return min_remaining

    def _max_retries_for(self, stage: str, task_id: str, default: int) -> int:
        if not self.budget:
            return default
        allowed = default
        for scope in self._budget_scopes_for(stage, task_id):
            allowed = min(allowed, self.budget.max_retries(scope, default=default))
        return allowed

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

        # v2: bootstrap context before any agent invocation
        if self.context_mgr and self._is_v2(config):
            self.tui.stage("Bootstrap: Building project context artifacts")
            self.context_mgr.build_bootstrap_context(description)
            self.tui.success("Context artifacts created")

        sm.transition(PipelineStage.INTENT_CAPTURE)
        self._update_status(status="running")
        self.tui.stage("Stage 1: Intent Capture", stage_key=PipelineStage.INTENT_CAPTURE.value)
        intent_path = self.feature_dir / "intent.yaml"
        _, validation = self._invoke_validated_stage(
            prompt=prompt_builder.build_intent_capture(description, intent_path, self._stage_timeout(config)),
            config=config,
            expected_type="intent",
            stage=PipelineStage.INTENT_CAPTURE.value,
            output_path=intent_path,
        )
        if not validation.valid:
            self._fail_pipeline(sm, f"Intent validation failed: {validation.errors}")
            return
        store.save("intent.yaml", validation.data)

        # v2: update feature brief with structured intent data
        if self.context_mgr and self._is_v2(config):
            self.context_mgr.build_feature_brief(description, validation.data)

        sm.transition(PipelineStage.PLANNING)
        self.tui.stage("Stage 2: Planning", stage_key=PipelineStage.PLANNING.value)
        plan_v1_path = self.feature_dir / "plan-v1.yaml"
        _, validation = self._invoke_validated_stage(
            prompt=prompt_builder.build_planner(plan_v1_path, self._stage_timeout(config)),
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

        # v2: build plan and manifest context packets
        if self.context_mgr and self._is_v2(config):
            self.context_mgr.build_plan_packet(plan_version)
            self.context_mgr.build_manifest_packet(plan_version)

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
        self.tui.stage("Stage 15: Documentation", stage_key=PipelineStage.DOCUMENTATION.value)
        docs_out = self.feature_dir / "docs-report.yaml"
        _, validation = self._invoke_validated_stage(
            prompt=prompt_builder.build_tech_writer(docs_out, self._stage_timeout(config)),
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
            if self._ensure_valid_yaml_artifact(
                sm,
                self.feature_dir / f"plan-v{plan_version}.yaml",
                "plan",
                f"plan-v{plan_version}.yaml",
            ) is None:
                return None
            sm.transition(PipelineStage.PLAN_REVIEW)
            self.tui.stage(f"Stage 3: Plan Review (iteration {iteration})", stage_key=PipelineStage.PLAN_REVIEW.value)
            review_out = self.feature_dir / f"plan-review-v{plan_version}.yaml"
            _, validation = self._invoke_validated_stage(
                prompt=prompt_builder.build_plan_reviewer(plan_version, review_out, self._stage_timeout(config)),
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

            # v2: severity-gated auto-approve for minor-only findings
            if self._has_severity_gate(config) and self._review_severity_allows_continue(validation.data):
                self.tui.info("Plan review: minor-only findings — auto-approved with warnings recorded.")
                return plan_version

            if iteration >= max_iterations:
                sm.transition(PipelineStage.BLOCKED)
                self._update_status(status="waiting_for_human", gate_reason="plan_review_limit")
                self.tui.warning("Plan review iteration limit reached; pipeline blocked for human intervention.")
                return None

            sm.transition(PipelineStage.PLAN_FIX)
            self.tui.stage("Stage 4: Plan Fix", stage_key=PipelineStage.PLAN_FIX.value)
            plan_version += 1
            if self._ensure_valid_yaml_artifact(
                sm,
                self.feature_dir / f"plan-review-v{plan_version - 1}.yaml",
                "plan_review",
                f"plan-review-v{plan_version - 1}.yaml",
            ) is None:
                return None
            plan_fix_out = self.feature_dir / f"plan-v{plan_version}.yaml"
            _, plan_validation = self._invoke_validated_stage(
                prompt=prompt_builder.build_plan_fix(plan_version - 1, plan_fix_out, self._stage_timeout(config)),
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
        if self._ensure_valid_yaml_artifact(
            sm,
            self.feature_dir / f"plan-v{plan_version}.yaml",
            "plan",
            f"plan-v{plan_version}.yaml",
        ) is None:
            return False

        if transition_stage:
            sm.transition(PipelineStage.PLAN_APPROVAL)
        requires_human = self._requires_plan_confirmation(config)
        if not requires_human:
            self.tui.stage("Stage 5: Specification Freeze", stage_key=PipelineStage.PLAN_APPROVAL.value)
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
        if self._ensure_valid_yaml_artifact(
            sm,
            self.feature_dir / f"plan-v{plan_version}.yaml",
            "plan",
            f"plan-v{plan_version}.yaml",
        ) is None:
            return None
        sm.transition(PipelineStage.TASK_BREAKDOWN)
        self.tui.stage("Stage 6: Execution Slice Breakdown", stage_key=PipelineStage.TASK_BREAKDOWN.value)
        manifest_out = self.feature_dir / "task-manifest.yaml"
        _, validation = self._invoke_validated_stage(
            prompt=prompt_builder.build_task_breakdown(plan_version, manifest_out, self._stage_timeout(config)),
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
            if self._ensure_valid_yaml_artifact(
                sm,
                self.feature_dir / "task-manifest.yaml",
                "task_manifest",
                "task-manifest.yaml",
            ) is None:
                return None
            sm.transition(PipelineStage.TASK_REVIEW)
            self.tui.stage(f"Stage 7: Execution Slice Review (iteration {iteration})", stage_key=PipelineStage.TASK_REVIEW.value)
            task_review_out = self.feature_dir / f"task-review-v{iteration}.yaml"
            _, review_validation = self._invoke_validated_stage(
                prompt=prompt_builder.build_task_reviewer(task_review_out, self._stage_timeout(config)),
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

            # v2: severity-gated auto-approve for minor-only findings
            if self._has_severity_gate(config) and self._review_severity_allows_continue(review_validation.data):
                self.tui.info("Task review: minor-only findings — auto-approved with warnings recorded.")
                return current_manifest_version

            if iteration >= max_iterations:
                sm.transition(PipelineStage.BLOCKED)
                self._update_status(status="waiting_for_human", gate_reason="task_review_limit")
                self.tui.warning("Task review iteration limit reached; pipeline blocked for human intervention.")
                return None

            sm.transition(PipelineStage.TASK_FIX)
            self.tui.stage(f"Stage 8: Execution Slice Fix (iteration {iteration})", stage_key=PipelineStage.TASK_FIX.value)
            current_manifest_version += 1
            if self._ensure_valid_yaml_artifact(
                sm,
                self.feature_dir / f"task-review-v{iteration}.yaml",
                "task_manifest_review",
                f"task-review-v{iteration}.yaml",
            ) is None:
                return None
            task_fix_out = self.feature_dir / "task-manifest.yaml"
            _, manifest_validation = self._invoke_validated_stage(
                prompt=prompt_builder.build_task_fix(iteration, task_fix_out, self._stage_timeout(config)),
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
        self.tui.stage("Stage 9: Spec-Derived Prioritization", stage_key=PipelineStage.PRIORITIZATION.value)
        if self._ensure_valid_yaml_artifact(
            sm,
            self.feature_dir / "task-manifest.yaml",
            "task_manifest",
            "task-manifest.yaml",
        ) is None:
            return False

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
        self.tui.stage("Stage 10: Execution Graph", stage_key=PipelineStage.EXECUTION_GRAPH.value)

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
        self.tui.stage(f"Stage 11: Executing {task_id}", stage_key=PipelineStage.TASK_EXECUTION.value, task_id=task_id)
        self._update_task_state(task_id, TaskState.RUNNING)
        task_path = self._move_task_artifact(task_id, "todo", "in-progress")
        if task_path is None:
            self._fail_pipeline(sm, f"Task artifact missing for {task_id}")
            self._update_task_state(task_id, TaskState.FAILED)
            return False
        if self._ensure_valid_task_artifact(sm, task_path, task_id) is None:
            self._update_task_state(task_id, TaskState.FAILED)
            return False

        exec_out = self.feature_dir / "tasks" / "in-progress" / f"{task_id}-execution-log.yaml"
        exec_out.parent.mkdir(parents=True, exist_ok=True)
        _, validation = self._invoke_validated_stage(
            prompt=prompt_builder.build_executor(task_id, exec_out, self._stage_timeout(config)),
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
        task_spec = self._ensure_valid_task_spec(sm, task_id)
        if task_spec is None:
            self._update_task_state(task_id, TaskState.FAILED)
            return False

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
                _, fix_validation = self._invoke_validated_stage(
                    prompt=prompt_builder.build_executor_fix(task_id, [{"description": "; ".join(test_report["regression_failures"])}], fix_out, self._stage_timeout(config)),
                    config=config,
                    expected_type="execution_result",
                    stage=PipelineStage.FIX_ITERATION.value,
                    task_id=task_id,
                    output_path=fix_out,
                )
                if not fix_validation.valid:
                    self._update_task_state(task_id, TaskState.FAILED)
                    return False
                sm.transition(PipelineStage.PER_TASK_QUALITY)
                task_spec = self._ensure_valid_task_spec(sm, task_id)
                if task_spec is None:
                    self._update_task_state(task_id, TaskState.FAILED)
                    return False
                continue

            self.tui.stage(f"Stage 12: Reviewing {task_id} Against the Specification (iteration {iteration})", stage_key=PipelineStage.PER_TASK_QUALITY.value, task_id=task_id)
            review_out = self.feature_dir / "tasks" / "done" / f"{task_id}-review-v{iteration}.yaml"
            review_out.parent.mkdir(parents=True, exist_ok=True)
            _, review_validation = self._invoke_validated_stage(
                prompt=prompt_builder.build_reviewer(task_id, review_out, self._stage_timeout(config)),
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
            _, fix_validation = self._invoke_validated_stage(
                prompt=prompt_builder.build_executor_fix(task_id, review_validation.data.get("findings", []), fix_out2, self._stage_timeout(config)),
                config=config,
                expected_type="execution_result",
                stage=PipelineStage.FIX_ITERATION.value,
                task_id=task_id,
                output_path=fix_out2,
            )
            if not fix_validation.valid:
                self._update_task_state(task_id, TaskState.FAILED)
                return False
            sm.transition(PipelineStage.PER_TASK_QUALITY)
            task_spec = self._ensure_valid_task_spec(sm, task_id)
            if task_spec is None:
                self._update_task_state(task_id, TaskState.FAILED)
                return False

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
            self.tui.stage("Stage 14: Specification-to-Code Gap Detection", stage_key=PipelineStage.GAP_DETECTION.value)
            if self._ensure_valid_yaml_artifact(
                sm,
                self.feature_dir / "task-manifest.yaml",
                "task_manifest",
                "task-manifest.yaml",
            ) is None:
                return False
            gap_out = self.feature_dir / f"gap-report-v{depth + 1}.yaml"
            _, validation = self._invoke_validated_stage(
                prompt=prompt_builder.build_gap_detector(gap_out, self._stage_timeout(config)),
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

            # v2: build gap packet for delta-based gap closure
            if self.context_mgr and self._has_delta_gaps(config):
                self.context_mgr.build_gap_packet(validation.data, depth)

            manifest = store.load("task-manifest.yaml")
            existing_ids = {task["id"] for task in manifest.get("tasks", [])}

            new_manifest_version = max(2, store.latest_version("task-manifest") + 1)
            sm.transition(PipelineStage.TASK_BREAKDOWN)
            gap_manifest_out = self.feature_dir / "task-manifest.yaml"

            # v2 delta mode: prompt specifically asks for delta tasks only
            if self._has_delta_gaps(config):
                gap_prompt = self._build_gap_delta_prompt(
                    prompt_builder, plan_version, gap_manifest_out,
                    validation.data, existing_ids, config,
                )
            else:
                gap_prompt = prompt_builder.build_task_breakdown(
                    plan_version, gap_manifest_out, self._stage_timeout(config),
                )

            result, task_validation = self._invoke_validated_stage(
                prompt=gap_prompt,
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

            # v2: save delta manifest separately for lineage
            if self._has_delta_gaps(config):
                delta_manifest = {"type": "task_manifest_delta", "gap_version": depth,
                                  "tasks": gap_tasks, "source_gap_report": f"gap-report-v{depth}.yaml"}
                store.save(f"task-manifest-delta-v{depth}.yaml", delta_manifest)

            merged_manifest = dict(manifest)
            merged_manifest["tasks"] = manifest.get("tasks", []) + gap_tasks
            self._save_task_manifest(store, merged_manifest, new_manifest_version)

            sm.transition(PipelineStage.TASK_REVIEW)
            if self._ensure_valid_yaml_artifact(
                sm,
                self.feature_dir / "task-manifest.yaml",
                "task_manifest",
                "task-manifest.yaml",
            ) is None:
                return False
            gap_review_out = self.feature_dir / f"task-review-v{new_manifest_version}.yaml"
            _, review_validation = self._invoke_validated_stage(
                prompt=prompt_builder.build_task_reviewer(gap_review_out, self._stage_timeout(config)),
                config=config,
                expected_type="task_manifest_review",
                stage=PipelineStage.TASK_REVIEW.value,
                output_path=gap_review_out,
            )
            if not review_validation.valid:
                self._fail_pipeline(sm, f"Gap task review validation failed: {review_validation.errors}")
                return False
            store.save(f"task-review-v{new_manifest_version}.yaml", review_validation.data)

            # v2: severity-gated gap review
            if review_validation.data["verdict"] != "approved":
                if self._has_severity_gate(config) and self._review_severity_allows_continue(review_validation.data):
                    self.tui.info("Gap task review: minor-only findings — auto-approved.")
                else:
                    sm.transition(PipelineStage.BLOCKED)
                    self._update_status(status="waiting_for_human", gate_reason="gap_task_review")
                    return False

            if not self._run_prioritization_and_execution(sm, store, prompt_builder, config):
                sm.transition(PipelineStage.BLOCKED)
                self._update_status(status="waiting_for_human", gate_reason="gap_execution_failed")
                self.tui.warning("Gap re-entry tasks failed; pipeline blocked for human intervention.")
                return False

    def _build_gap_delta_prompt(
        self,
        prompt_builder: PromptBuilder,
        plan_version: int,
        output_path: Path,
        gap_report: dict,
        existing_ids: set[str],
        config: dict,
    ) -> str:
        """Build a prompt for delta-only gap closure task breakdown."""
        import yaml
        gap_summary = yaml.dump(gap_report.get("gaps", []), default_flow_style=False)
        existing_list = ", ".join(sorted(existing_ids))
        timeout = self._stage_timeout(config)
        timeout_min = max(1, timeout // 60)

        return (
            f"Create ONLY new tasks to close the gaps found in the gap report. "
            f"Do NOT regenerate existing tasks.\n\n"
            f"Gap findings:\n{gap_summary}\n"
            f"Existing task IDs (do not duplicate): {existing_list}\n\n"
            f"Plan: {self.feature_dir}/plan-v{plan_version}.yaml\n"
            f"Project: {self.project_dir}\n\n"
            f"Each new task must have: id (task-NNN or task-GNNN format, unique), title, description, "
            f"files_in_scope, acceptance_criteria with must_pass command checks, "
            f"depends_on, estimated_complexity, quality_tier.\n\n"
            f"Time constraint: hard limit of {timeout_min} minutes.\n"
            f"Write the delta task manifest as YAML to: {output_path}\n"
            f"The file must start with --- and contain only valid YAML conforming to TaskManifestOutput schema."
        )

    def _stage_timeout(self, config: dict) -> int:
        """Return the configured timeout (seconds) for stage invocations."""
        return config.get("main_agent", {}).get("timeout", 900)

    def _ensure_valid_yaml_artifact(
        self,
        sm: PipelineStateMachine,
        path: Path,
        expected_type: str,
        label: str,
    ) -> dict | None:
        """Fail fast when a prerequisite artifact is missing or invalid."""
        if expected_type not in SCHEMAS:
            self._fail_pipeline(sm, f"Unknown schema for prerequisite {label}: {expected_type}")
            return None
        if not path.exists():
            self._fail_pipeline(sm, f"Required artifact missing before continuing: {label} at {path}")
            return None
        raw_text = path.read_text()
        if not raw_text.strip():
            self._fail_pipeline(sm, f"Required artifact is empty before continuing: {label} at {path}")
            return None
        validation = self.validator.validate(raw_text, expected_type)
        if not validation.valid:
            self._fail_pipeline(sm, f"Invalid prerequisite artifact {label}: {validation.errors}")
            return None
        return validation.data

    def _ensure_valid_task_artifact(
        self,
        sm: PipelineStateMachine,
        path: Path,
        task_id: str,
    ) -> dict | None:
        if not path.exists():
            self._fail_pipeline(sm, f"Required task artifact missing for {task_id}: {path}")
            return None
        try:
            task_data = load_yaml_file(path)
        except Exception as exc:
            self._fail_pipeline(sm, f"Failed to parse task artifact for {task_id}: {exc}")
            return None
        wrapper = {
            "type": "task_manifest",
            "plan_version": 1,
            "summary": f"Validation wrapper for {task_id}",
            "tasks": [task_data],
        }
        validation = self.validator.validate_data(wrapper, "task_manifest")
        if not validation.valid:
            self._fail_pipeline(sm, f"Invalid task artifact for {task_id}: {validation.errors}")
            return None
        try:
            TaskDefinition.model_validate(validation.data["tasks"][0])
        except Exception as exc:
            self._fail_pipeline(sm, f"Invalid task definition for {task_id}: {exc}")
            return None
        return validation.data["tasks"][0]

    def _ensure_valid_task_spec(self, sm: PipelineStateMachine, task_id: str) -> dict | None:
        task_path = self._task_artifact_path(task_id, "in-progress") or self._task_artifact_path(task_id, "todo") or self._task_artifact_path(task_id, "done")
        if task_path is None:
            self._fail_pipeline(sm, f"No task spec found for {task_id}")
            return None
        return self._ensure_valid_task_artifact(sm, task_path, task_id)

    def _invoke_validated_stage(
        self,
        prompt: str,
        config: dict,
        expected_type: str,
        stage: str,
        task_id: str = "",
        output_path: Path | None = None,
    ) -> tuple[AgentResult, ValidationResult]:
        """Invoke a stage with validation and bounded retry (v2 repair loop).

        Retry strategy:
        1. First attempt: normal invocation
        2. If invalid: retry in same lane session with repair prompt
        3. If still invalid: rotate lane session, retry with same contract
        4. Fail after bounded retries
        """
        self._remaining_budget_usd(stage, task_id)
        max_retries = config.get("validation", {}).get("max_retries", 2)
        rotate_on_retry = config.get("validation", {}).get("rotate_on_retry", True)

        # Budget-aware retry tightening
        if self.budget:
            max_retries = self._max_retries_for(stage, task_id, max_retries)

        last_result = None
        last_validation = None

        for attempt in range(1 + max_retries):
            # First attempt uses original prompt; retries use repair prompt
            repair_source: Path | None = None
            if output_path and output_path.exists():
                repair_source = self._snapshot_invalid_artifact(stage, output_path, attempt)

            if attempt == 0:
                invoke_prompt = prompt
            else:
                invoke_prompt = self._build_repair_prompt(
                    prompt, expected_type, last_validation, output_path, config, repair_source
                )

            # Clear stale output so we only read what this invocation writes
            if output_path and output_path.exists():
                output_path.unlink()

            # On second retry, rotate lane session if configured
            if attempt >= 2 and rotate_on_retry and self.lanes:
                self.lanes.rotate_lane(stage, task_id)
                self.tui.info(f"Rotated lane session for {stage} (retry {attempt})")

            result = self._invoke_stage(invoke_prompt, config, stage, task_id)
            invocation_error = self._nonrecoverable_invocation_error(result)

            if invocation_error:
                validation = ValidationResult(valid=False, errors=[invocation_error])
            elif output_path is not None:
                if not output_path.exists():
                    validation = ValidationResult(
                        valid=False,
                        errors=[f"Expected YAML artifact file at {output_path}, but the agent did not write it"],
                    )
                else:
                    raw_text = output_path.read_text()
                    if not raw_text.strip():
                        validation = ValidationResult(
                            valid=False,
                            errors=[f"Expected YAML artifact file at {output_path}, but it was empty"],
                        )
                    else:
                        validation = self.validator.validate(raw_text, expected_type)
            else:
                validation = self.validator.validate(result.raw_text, expected_type)
            last_result = result

            if validation.valid:
                if attempt > 0:
                    self.tui.success(f"Validation repair succeeded on attempt {attempt + 1}")
                return result, validation

            stop_retry_reason = self._stop_retry_reason(last_validation, validation, attempt)
            last_validation = validation

            # Persist validation failure snapshot
            if self._persist_validation_failures(config):
                self._record_validation_failure(stage, result, expected_type, validation, attempt)
            self._report_validation_failure(stage, result, expected_type, validation)

            if invocation_error:
                self.tui.error(f"Stopping {stage} without retry because the failure is non-recoverable.")
                break

            unrecoverable_validation = self._nonrecoverable_validation_error(validation, expected_type)
            if unrecoverable_validation:
                self.tui.error(f"Stopping {stage} without retry because the validation failure is non-recoverable: {unrecoverable_validation}")
                break

            if stop_retry_reason:
                self.tui.warning(f"Stopping retries for {stage}: {stop_retry_reason}")
                break

            if attempt < max_retries:
                self.tui.warning(f"Validation failed for {stage} (attempt {attempt + 1}/{1 + max_retries}), retrying...")

        return last_result, last_validation

    def _nonrecoverable_invocation_error(self, result: AgentResult) -> str | None:
        """Return an error string when retrying this invocation would be wasteful."""
        if result.exit_code == 0:
            return None
        raw = (result.raw_text or "").lower()
        if "invalid api key" in raw or "authentication failed" in raw:
            return "Non-recoverable agent invocation failure: authentication was rejected"
        return None

    def _nonrecoverable_validation_error(self, validation: ValidationResult, expected_type: str) -> str | None:
        """Return a message when a validation failure should fail fast instead of retrying."""
        if validation.valid:
            return None
        if expected_type not in SCHEMAS:
            return f"unknown expected schema type '{expected_type}'"
        joined = " ".join(validation.errors).lower()
        if "unknown artifact type:" in joined and "got" not in joined:
            return validation.errors[0]
        if "required artifact missing before continuing" in joined:
            return validation.errors[0]
        return None

    def _build_repair_prompt(
        self,
        original_prompt: str,
        expected_type: str,
        validation: ValidationResult | None,
        output_path: Path | None,
        config: dict | None = None,
        repair_source: Path | None = None,
    ) -> str:
        """Build a targeted repair prompt from validation errors."""
        errors = validation.errors if validation else ["Unknown validation error"]
        error_text = "\n".join(f"  - {e}" for e in errors[:5])

        # Try to get contract block for the expected type
        contract_hint = ""
        if config is None or self._use_generated_contracts(config):
            from .schemas import SCHEMAS
            schema_class = SCHEMAS.get(expected_type)
            if schema_class:
                try:
                    contract_hint = f"\n\nExpected output contract:\n{build_contract_block(schema_class, expected_type)}"
                except Exception:
                    pass

        output_instruction = f"\nWrite the corrected YAML to: {output_path}" if output_path else ""
        repair_instruction = (
            f"\nBase your fix on the last invalid artifact at: {repair_source}\n"
            "Make the smallest possible correction. Preserve unchanged content, ordering, and IDs unless a validation error explicitly requires changing them."
            if repair_source else
            "\nMake the smallest possible correction. Preserve unchanged content and structure wherever possible."
        )
        original_block = f"\n\nOriginal task instructions:\n{original_prompt}" if original_prompt else ""

        return (
            f"The previous output failed validation for type '{expected_type}'. "
            f"Fix the following errors and rewrite the complete YAML artifact:\n\n"
            f"Validation errors:\n{error_text}\n"
            f"{contract_hint}\n"
            f"{repair_instruction}\n"
            f"{original_block}\n"
            f"IMPORTANT: Output ONLY valid YAML starting with ---. No prose.{output_instruction}"
        )

    def _snapshot_invalid_artifact(self, stage: str, output_path: Path, attempt: int) -> Path | None:
        """Persist the last invalid artifact so retries can patch it instead of regenerating it."""
        if not output_path.exists() or not self.feature_dir:
            return None
        failures_dir = self.feature_dir / "validation-failures"
        failures_dir.mkdir(parents=True, exist_ok=True)
        snapshot_path = failures_dir / f"{stage}-attempt-{attempt + 1}-input.yaml"
        try:
            shutil.copyfile(output_path, snapshot_path)
            return snapshot_path
        except OSError:
            return None

    def _stop_retry_reason(
        self,
        previous_validation: ValidationResult | None,
        current_validation: ValidationResult,
        attempt: int,
    ) -> str | None:
        """Fail fast when another retry is unlikely to change the outcome."""
        if previous_validation is None:
            return None
        if previous_validation.valid or current_validation.valid:
            return None
        # Keep one more path available for recovery, including lane rotation.
        if attempt < 2:
            return None
        if previous_validation.errors == current_validation.errors and previous_validation.data == current_validation.data:
            return "the previous retry produced the same invalid artifact with no validation progress"
        return None

    def _record_validation_failure(
        self,
        stage: str,
        result: AgentResult,
        expected_type: str,
        validation: ValidationResult,
        attempt: int,
    ) -> None:
        """Persist validation failure snapshot for debugging."""
        if not self.feature_dir:
            return
        from .yaml_utils import save_yaml_file
        failures_dir = self.feature_dir / "validation-failures"
        snapshot = {
            "stage": stage,
            "expected_type": expected_type,
            "attempt": attempt + 1,
            "errors": validation.errors,
            "timestamp": _now_iso(),
            "session_id": result.session_id,
            "duration_ms": result.duration_ms,
            "cost_usd": result.cost_usd,
        }
        save_yaml_file(failures_dir / f"{stage}-attempt-{attempt + 1}.yaml", snapshot)
        raw_path = failures_dir / f"{stage}-attempt-{attempt + 1}-raw.txt"
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_text(result.raw_text or "(empty)")

    def _check_oauth_before_invoke(self):
        """Check OAuth token expiry before invoking an agent."""
        from .auth import check_oauth_expiry
        status = check_oauth_expiry(self.xpatcher_home)
        if status is None:
            return  # Not using OAuth
        if status["expired"]:
            self.tui.warning("Stored OAuth access token is expired; allowing Claude CLI to refresh native auth if available.")
            return
        if status["needs_refresh"]:
            self.tui.warning(f"OAuth token expires in {status['minutes_remaining']}m — consider refreshing soon.")

    def _invoke_stage(
        self,
        prompt: str,
        config: dict,
        stage: str,
        task_id: str = "",
    ) -> AgentResult:
        self._raise_if_cancelled()
        self._check_oauth_before_invoke()

        # v2: lane-scoped sessions (resolve_session internally calls lane_for_stage)
        if self.lanes:
            lane_name = self.lanes.lane_for_stage(stage, task_id)
            agent_name = self.lanes.agent_for_lane(lane_name)
        else:
            # v1 fallback: pipeline-wide session
            if not self._pipeline_session_id:
                self._pipeline_session_id = str(uuid.uuid4())
            session_id = self._pipeline_session_id
            is_resume = self._pipeline_session_used
            if not self._pipeline_session_used:
                self._pipeline_session_used = True
            lane_name = ""
            agent_name = None

        # v2: direct agent invocation for deterministic stages
        use_direct_agent = self._is_v2(config) and agent_name
        # v2: per-invocation budget
        budget_usd = self._remaining_budget_usd(stage, task_id)
        if self.lanes:
            session_id, is_resume = self.lanes.resolve_session(stage, task_id)

        timeout = self._stage_timeout(config)
        self._update_status(
            active_stage=stage,
            active_task_id=task_id or "",
            active_lane=lane_name or "",
            active_agent=agent_name or "",
            active_session_id=session_id,
            stage_started_at=_now_iso(),
        )
        self._persist_v2_state()
        invocation = AgentInvocation(
            prompt=prompt,
            timeout=timeout,
            session_id=session_id,
            resume=is_resume,
            cancel_check=self._is_cancelled,
            agent=agent_name if use_direct_agent else None,
            max_budget_usd=budget_usd,
            lane_name=lane_name,
        )
        self.tui.set_invocation_context(
            lane=lane_name,
            owner_agent=agent_name or "",
            task_id=task_id,
            claude_session_id=session_id,
        )
        if agent_name:
            self.tui.update_activity(agent_name, "", "preparing stage")

        def _status_callback(activities):
            if activities:
                latest = activities[-1]
                self.tui.update_activity(latest.actor, latest.tool_name, latest.summary)
            else:
                self.tui.update_activity()

        invocation.status_callback = _status_callback

        if self.debug:
            cmd_preview = self.session.preview_cmd(invocation)
            project_slug = str(self.project_dir).replace("/", "-")
            cc_log = f"~/.claude/projects/{project_slug}/{session_id}.jsonl"
            lane_info = f" lane={lane_name}" if lane_name else ""
            agent_info = f" agent={agent_name}" if use_direct_agent else ""
            self.tui.debug(f"stage={stage} task={task_id or '-'}{lane_info}{agent_info} timeout={invocation.timeout}s session={session_id}")
            self.tui.debug(f"$ {cmd_preview}")
            self.tui.debug(f"tail -f {cc_log}")
            invocation.debug_tailer = SessionTailer(self.project_dir, session_id, emit_debug=True)
        elif self._use_live_dashboard(config):
            invocation.debug_tailer = SessionTailer(self.project_dir, session_id, emit_debug=False)

        try:
            result = self.session.invoke(invocation)
        except subprocess.TimeoutExpired:
            self.tui.warning(f"Stage '{stage}' timed out after {invocation.timeout}s")
            result = AgentResult(
                session_id=session_id,
                exit_code=124,
                stop_reason="timeout",
            )
        finally:
            self.tui.clear_activity()

        # Detect OAuth token expiry — fail fast with actionable guidance
        if result.exit_code != 0 and "invalid api key" in result.raw_text.lower():
            env_path = self.xpatcher_home / ".env"
            self.tui.error("Authentication failed: Claude rejected the current credentials.")
            self.tui.error(f"Fix: add a permanent API key to {env_path}:")
            self.tui.error(f'  echo "ANTHROPIC_API_KEY=sk-ant-..." >> {env_path}')
            self.tui.error("Or refresh native Claude auth by running `claude auth login`, then retry.")

        self._raise_if_cancelled()
        self.total_cost_usd += result.cost_usd
        self.tui.cost_update(self.total_cost_usd)
        self._update_status(
            total_cost_usd=self.total_cost_usd,
            active_stage=stage,
            active_task_id=task_id or "",
            active_lane=lane_name or "",
            active_agent=agent_name or "",
            active_session_id=session_id,
            last_stage_result_at=_now_iso(),
        )
        # v2: record cost to lane and budget, persist at checkpoint
        if self.lanes and lane_name:
            self.lanes.record_cost(stage, task_id, result.cost_usd)
        if self.budget and lane_name:
            self.budget.record_cost(lane_name, result.cost_usd)
            for scope in dict.fromkeys([lane_name, "pipeline"]):
                cp = self.budget.check(scope)
                if cp.warning:
                    self.tui.warning(cp.warning)
        self._persist_v2_state()
        log_path = self._write_agent_log(stage, result, task_id)

        # Show agent's final message with colored stripe
        is_error = result.exit_code != 0
        summary = result.raw_text.strip()
        if summary:
            # Truncate very long output to ~500 chars
            if len(summary) > 500:
                summary = summary[:500] + f"\n... ({len(result.raw_text)} chars total, truncated)"
            self.tui.agent_result(summary, is_error=is_error)

        if self.debug:
            xp_log = _home_relative(log_path) if log_path else "-"
            self.tui.debug(
                f"done stage={stage} "
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
            parsed_command, parse_error = self._parse_acceptance_command(command)
            if parse_error:
                checks.append({
                    "name": criterion.get("id", command),
                    "status": "failed",
                    "duration_ms": 0,
                    "error_message": parse_error,
                })
                failures.append(f"{criterion.get('id', 'unknown')}: {parse_error}")
                continue
            try:
                proc = subprocess.run(
                    parsed_command,
                    cwd=str(self.project_dir),
                    capture_output=True,
                    text=True,
                )
            except (FileNotFoundError, OSError) as exc:
                message = f"command execution error: {exc}"
                checks.append({
                    "name": criterion.get("id", command),
                    "status": "failed",
                    "duration_ms": 0,
                    "error_message": message,
                })
                failures.append(f"{criterion.get('id', 'unknown')}: {message}")
                continue
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

    def _parse_acceptance_command(self, command: str) -> tuple[list[str] | None, str | None]:
        return prepare_acceptance_command(command)

    def _save_task_manifest(self, store: ArtifactStore, manifest: dict, manifest_version: int) -> None:
        store.save("task-manifest.yaml", manifest)
        if manifest_version > 1:
            store.save(f"task-manifest-v{manifest_version}.yaml", manifest)
        self._materialize_task_files(manifest.get("tasks", []))
        # v2: build task packets for all tasks
        if self.context_mgr:
            self.context_mgr.build_all_task_packets(manifest)

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
        label = "plan review" if loop_name == "plan_review" else loop_name.replace("_", " ")
        if loop_name == "task_review":
            label = "task review"
        self.tui.set_loop_progress(label, current, maximum)

    def _set_quality_iteration(self, task_id: str, current: int, maximum: int) -> None:
        state = self.state_file.read()
        iterations = state.get("iterations", {})
        quality = iterations.get("quality_loop", {})
        quality[task_id] = {"current": current, "max": maximum}
        iterations["quality_loop"] = quality
        self._update_status(iterations=iterations)
        self.tui.set_loop_progress("quality", current, maximum)

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
        self.tui.set_loop_progress("gap re-entry", depth, maximum)

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
        markers = ("missing command", "unsupported shell syntax", "invalid command syntax", "empty command", "invalid python -c program")
        return bool(failures) and all(any(marker in failure for marker in markers) for failure in failures)

    def _review_task_without_command_checks(self, task_id: str, prompt_builder: PromptBuilder, store: ArtifactStore, config: dict) -> bool:
        self.tui.info(f"{task_id}: acceptance commands are missing; falling back to spec review instead of looping code fixes.")
        existing_reviews = sorted((self.feature_dir / "tasks" / "done").glob(f"{task_id}-review-v*.yaml"))
        version = len(existing_reviews) + 1
        review_out = self.feature_dir / "tasks" / "done" / f"{task_id}-review-v{version}.yaml"
        review_out.parent.mkdir(parents=True, exist_ok=True)
        _, review_validation = self._invoke_validated_stage(
            prompt=prompt_builder.build_reviewer(task_id, review_out, self._stage_timeout(config)),
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
        stage: str,
        result: AgentResult,
        expected_type: str,
        validation: ValidationResult,
    ) -> None:
        """Log detailed diagnostics when a stage produces invalid output."""
        session_id = result.session_id or "(unknown)"
        project_slug = str(self.project_dir).replace("/", "-")
        cc_log = f"~/.claude/projects/{project_slug}/{session_id}.jsonl"

        self.tui.error(f"Stage '{stage}' returned invalid output")
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

    def _handle_budget_exhausted(self, sm: PipelineStateMachine, message: str) -> None:
        self.tui.set_stage(PipelineStage.BLOCKED.value)
        self.tui.error(message)
        sm.transition(PipelineStage.BLOCKED)
        self._update_status(status="waiting_for_human", gate_reason="budget_exhausted")

    def _fail_pipeline(self, sm: PipelineStateMachine, message: str) -> None:
        self.tui.set_stage(PipelineStage.FAILED.value)
        self.tui.error(message)
        sm.transition(PipelineStage.FAILED)
        self._update_status(status="failed")

    def _handle_completion_gate(self, sm: PipelineStateMachine, store: ArtifactStore, transition_stage: bool = True) -> None:
        if transition_stage:
            sm.transition(PipelineStage.COMPLETION)
        self.tui.cost_summary(self.total_cost_usd)
        if not self._requires_completion_confirmation():
            self.tui.stage("Stage 16: Completion Summary", stage_key=PipelineStage.COMPLETION.value)
            sm.transition(PipelineStage.DONE)
            self.tui.set_stage(PipelineStage.DONE.value)
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
        # v2 directories
        (feature_dir / "context").mkdir(parents=True, exist_ok=True)
        (feature_dir / "context" / "task-packets").mkdir(parents=True, exist_ok=True)
        (feature_dir / "lanes").mkdir(parents=True, exist_ok=True)
        (feature_dir / "validation-failures").mkdir(parents=True, exist_ok=True)

    def _update_status(self, **fields) -> None:
        if self.state_file is not None:
            self.state_file.update(**fields)

    def _persist_v2_state(self) -> None:
        """Persist lane and budget state. Called at pipeline checkpoints, not every status update."""
        if self.state_file is None:
            return
        fields: dict = {}
        if self.lanes:
            fields["lane_sessions"] = self.lanes.get_all_lane_states()
        if self.budget:
            fields["budget_costs"] = self.budget.get_all_costs()
            fields["budget_checkpoints"] = self.budget.get_checkpoints()
        if fields:
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


def _show_progress(args, xpatcher_home):
    record = _find_pipeline_record(xpatcher_home, args.pipeline_id)
    if record is None:
        print(f"Unknown pipeline: {args.pipeline_id}")
        return 1

    feature_dir = Path(record["feature_dir"])
    state = load_yaml_file(feature_dir / "pipeline-state.yaml")
    pipeline_id = state.get("pipeline_id", args.pipeline_id)
    status = state.get("status", "?")
    current_stage = state.get("current_stage", "?")
    cost = state.get("total_cost_usd", 0.0)
    created = state.get("created_at", "")
    transitions = state.get("transitions", [])
    task_states = state.get("task_states", {})

    # Pipeline ordered stages with display labels
    STAGE_DISPLAY = [
        ("intent_capture",  " 1", "Intent"),
        ("planning",        " 2", "Plan"),
        ("plan_review",     " 3", "Review"),
        ("plan_fix",        " 4", "Fix"),
        ("plan_approval",   " 5", "Approve"),
        ("task_breakdown",  " 6", "Tasks"),
        ("task_review",     " 7", "TaskRev"),
        ("task_fix",        " 8", "TaskFix"),
        ("prioritization",  " 9", "Priority"),
        ("execution_graph", "10", "DAG"),
        ("task_execution",  "11", "Execute"),
        ("per_task_quality","12", "Quality"),
        ("fix_iteration",   "13", "FixIter"),
        ("gap_detection",   "14", "Gaps"),
        ("documentation",   "15", "Docs"),
        ("completion",      "16", "Done"),
    ]

    # Build per-stage timing from transitions
    stage_visits: dict[str, list[dict]] = {}  # stage -> [{enter, exit, duration_s}]
    for i, t in enumerate(transitions):
        to_stage = t["to"]
        enter_ts = t["at"]
        # Find exit: next transition's timestamp
        exit_ts = transitions[i + 1]["at"] if i + 1 < len(transitions) else None
        if to_stage not in stage_visits:
            stage_visits[to_stage] = []
        duration_s = 0.0
        if exit_ts:
            try:
                from datetime import datetime as _dt, timezone as _tz
                fmt = "%Y-%m-%dT%H:%M:%S.%f%z" if "." in enter_ts else "%Y-%m-%dT%H:%M:%S%z"
                fmt2 = "%Y-%m-%dT%H:%M:%S.%f%z" if "." in exit_ts else "%Y-%m-%dT%H:%M:%S%z"
                t_enter = _dt.fromisoformat(enter_ts)
                t_exit = _dt.fromisoformat(exit_ts)
                duration_s = (t_exit - t_enter).total_seconds()
            except (ValueError, TypeError):
                pass
        stage_visits[to_stage].append({"enter": enter_ts, "exit": exit_ts, "duration_s": duration_s})

    # Aggregate per-stage: total time, visit count
    stage_summary: dict[str, dict] = {}
    for stage, visits in stage_visits.items():
        total_s = sum(v["duration_s"] for v in visits)
        stage_summary[stage] = {"total_s": total_s, "visits": len(visits)}

    # Total elapsed time
    total_elapsed_s = 0.0
    if created and transitions:
        try:
            t_start = datetime.fromisoformat(created)
            t_last = datetime.fromisoformat(transitions[-1]["at"])
            total_elapsed_s = (t_last - t_start).total_seconds()
        except (ValueError, TypeError):
            pass

    # Per-agent cost from log files
    agent_costs: dict[str, dict] = {}  # log_name -> {duration_ms, turns, cost, stage_label}
    log_dir = feature_dir / "logs"
    if log_dir.exists():
        for log_file in sorted(log_dir.glob("agent-*.jsonl")):
            try:
                with open(log_file) as f:
                    lines = [json.loads(line) for line in f]
                for entry in lines:
                    if entry.get("type") == "result":
                        name = log_file.stem  # e.g. agent-planner-20260402-180619
                        agent_costs[name] = {
                            "duration_ms": entry.get("duration_ms", 0),
                            "turns": entry.get("num_turns", 0),
                            "cost": entry.get("cost_usd", 0.0),
                        }
            except (json.JSONDecodeError, OSError):
                pass

    # Status icon
    status_icon = {"running": "▶", "completed": "✓", "done": "✓", "failed": "✗",
                   "paused": "⏸", "waiting_for_human": "⏸", "cancelled": "⊘"}.get(status, "?")

    # Header
    desc = state.get("description", "").strip()[:80]
    print(f"{pipeline_id}  {status_icon} {status}  ${cost:.2f}  {_fmt_duration(total_elapsed_s)}")
    if desc:
        print(f"  {desc}")
    print()

    # Stage progress table
    reached_stages = set(stage_visits.keys())
    # All visited stages except current are "done"
    done_stages = reached_stages - {current_stage}
    # Find the last reached position in the display order
    last_reached_idx = -1
    for idx, (stage_key, _, _) in enumerate(STAGE_DISPLAY):
        if stage_key in reached_stages:
            last_reached_idx = idx

    for idx, (stage_key, num, label) in enumerate(STAGE_DISPLAY):
        info = stage_summary.get(stage_key)
        if stage_key == current_stage:
            if status in ("failed", "cancelled"):
                marker = "✗"
            elif status in ("paused", "waiting_for_human"):
                marker = "⏸"
            else:
                marker = "▶"
            time_str = _fmt_duration(info["total_s"]) if info else ""
            visits = info["visits"] if info else 0
            extra = f"  x{visits}" if visits > 1 else ""
            print(f"  {marker} {num} {label:<8s} {time_str:>6s}{extra}  ← {status}")
        elif stage_key in done_stages and info:
            time_str = _fmt_duration(info["total_s"])
            visits = info["visits"]
            extra = f"  x{visits}" if visits > 1 else ""
            print(f"  ✓ {num} {label:<8s} {time_str:>6s}{extra}")
        elif idx > last_reached_idx:
            print(f"  · {num} {label}")

    # Task states (if any)
    if task_states:
        print()
        for tid, ts in sorted(task_states.items()):
            t_icon = {"succeeded": "✓", "failed": "✗", "running": "▶",
                      "stuck": "⚠", "skipped": "⊘", "pending": "·",
                      "blocked": "⏸", "needs_fix": "⟳"}.get(ts, "?")
            print(f"  {t_icon} {tid}: {ts}")

    # Agent log summary — aggregate by agent type
    if agent_costs:
        print()
        # Group by agent name (e.g. "planner", "executor-task-001")
        agent_agg: dict[str, dict] = {}
        for name, info in agent_costs.items():
            # agent-planner-20260402-180619 -> planner
            # agent-executor-task-001-20260401-233648 -> executor
            parts = name.replace("agent-", "").split("-")
            # Find where the timestamp starts (8-digit date)
            agent_name_parts = []
            for p in parts:
                if len(p) == 8 and p.isdigit():
                    break
                agent_name_parts.append(p)
            agent_key = "-".join(agent_name_parts)
            if agent_key not in agent_agg:
                agent_agg[agent_key] = {"runs": 0, "total_ms": 0, "total_turns": 0, "total_cost": 0.0}
            agent_agg[agent_key]["runs"] += 1
            agent_agg[agent_key]["total_ms"] += info["duration_ms"]
            agent_agg[agent_key]["total_turns"] += info["turns"]
            agent_agg[agent_key]["total_cost"] += info.get("cost", 0.0)

        print("  Agents:")
        for agent_key, agg in sorted(agent_agg.items()):
            dur_s = agg["total_ms"] / 1000
            runs = agg["runs"]
            turns = agg["total_turns"]
            c = agg["total_cost"]
            cost_str = f"  ${c:.2f}" if c else ""
            runs_str = f"  x{runs}" if runs > 1 else ""
            print(f"    {agent_key:<20s} {_fmt_duration(dur_s):>6s}  {turns:>3d}t{runs_str}{cost_str}")

    print()
    return 0


def _fmt_duration(seconds: float) -> str:
    """Format seconds into compact human-readable: 2m30s, 15s, 1h5m."""
    if seconds <= 0:
        return ""
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    secs = seconds % 60
    if minutes < 60:
        return f"{minutes}m{secs:02d}s" if secs else f"{minutes}m"
    hours = minutes // 60
    mins = minutes % 60
    return f"{hours}h{mins:02d}m"


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

    _kill_dispatcher_process(feature_dir)
    # Kill orphaned Claude CLI processes
    _kill_session_processes(feature_dir)

    print(f"Cancelled pipeline {args.pipeline_id}")
    return 0


def _collect_pipeline_session_ids(feature_dir: Path) -> set[str]:
    """Collect all known Claude session IDs for a pipeline across v1 and v2 state."""
    session_ids: set[str] = set()

    sessions_path = feature_dir / "sessions.yaml"
    if sessions_path.exists():
        sessions_data = load_yaml_file(sessions_path)
        for sid in sessions_data.get("sessions", {}):
            if sid:
                session_ids.add(str(sid))

    state_path = feature_dir / "pipeline-state.yaml"
    if state_path.exists():
        state = load_yaml_file(state_path)
        active_session_id = state.get("active_session_id")
        if active_session_id:
            session_ids.add(str(active_session_id))
        lane_sessions = state.get("lane_sessions", {})
        if isinstance(lane_sessions, dict):
            for lane_state in lane_sessions.values():
                if isinstance(lane_state, dict) and lane_state.get("session_id"):
                    session_ids.add(str(lane_state["session_id"]))

    lanes_dir = feature_dir / "lanes"
    if lanes_dir.exists():
        for lane_path in lanes_dir.glob("lane-*.yaml"):
            lane_state = load_yaml_file(lane_path)
            if lane_state.get("session_id"):
                session_ids.add(str(lane_state["session_id"]))

    return {sid for sid in session_ids if sid}


def _kill_dispatcher_process(feature_dir: Path) -> None:
    """Kill the dispatcher process recorded for a pipeline, if it still exists."""
    state_path = feature_dir / "pipeline-state.yaml"
    if not state_path.exists():
        return
    state = load_yaml_file(state_path)
    try:
        pid = int(state.get("dispatcher_pid", 0))
    except (TypeError, ValueError):
        return
    if pid <= 0:
        return
    try:
        os.kill(pid, signal.SIGTERM)
        print(f"  Killed dispatcher {pid}")
    except ProcessLookupError:
        pass
    except OSError:
        pass


def _kill_session_processes(feature_dir: Path) -> None:
    """Kill any Claude CLI processes associated with sessions in this pipeline."""
    for sid in sorted(_collect_pipeline_session_ids(feature_dir)):
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

    _kill_dispatcher_process(feature_dir)
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

    _stage_list = "intent_capture|planning|plan_review|plan_approval|task_breakdown|task_execution|gap_detection|documentation|completion"
    resume_parser = subparsers.add_parser("resume", help="Resume an interrupted pipeline")
    resume_parser.add_argument("pipeline_id")
    resume_parser.add_argument("--from-stage", default=None, dest="from_stage",
                               help=f"Resume from a specific stage: {_stage_list}")
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

    progress_parser = subparsers.add_parser("progress", help="Show detailed pipeline progress with per-stage timings and costs")
    progress_parser.add_argument("pipeline_id")

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
        Dispatcher(project_dir, xpatcher_home, debug=args.debug).resume(
            args.pipeline_id, from_stage=args.from_stage, debug=args.debug,
        )
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
    if args.command == "progress":
        sys.exit(_show_progress(args, xpatcher_home))
    if args.command == "pending":
        _show_pending(xpatcher_home)
        return
    if args.command == "logs":
        sys.exit(_show_logs(args, xpatcher_home))


if __name__ == "__main__":
    main()

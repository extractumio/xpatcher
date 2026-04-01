# Dispatcher Internals

> Part of the [xpatcher Design Proposal](../xpatcher-design-proposal.md)

---

## 7.7 Python Dispatcher Integration Patterns

> **CLI Validation (2026-03-29):** All critical CLI flags have been empirically validated against Claude Code CLI v2.1.87. See Section 7.7.1 below for the complete validation matrix.

The dispatcher uses `--output-format json` for the Claude CLI wrapper. The output is a **JSON array of event objects** (not a single JSON object). Event types: `system` (init metadata), `assistant` (model messages), `user` (tool results), `rate_limit_event`, and `result` (final output with cost/usage). The dispatcher extracts the `result` event to get the agent's text output, which contains YAML.

```python
import yaml
import json
import subprocess
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class AgentInvocation:
    """Parameters for a single Claude agent invocation."""
    prompt: str
    agent: Optional[str] = None          # Plugin-qualified name, e.g. "xpatcher:planner"
    session_id: Optional[str] = None     # For --resume
    max_turns: Optional[int] = None
    timeout: int = 600                   # seconds
    allowed_tools: Optional[list[str]] = None
    disallowed_tools: Optional[list[str]] = None
    model: Optional[str] = None          # Model alias override
    permission_mode: str = "bypassPermissions"


@dataclass
class AgentResult:
    """Result from a Claude agent invocation."""
    session_id: str
    raw_text: str                        # Agent's final text output
    parsed: Optional[dict] = None        # Extracted YAML (if successful)
    exit_code: int = 0
    cost_usd: float = 0.0
    duration_ms: int = 0
    num_turns: int = 0
    stop_reason: str = ""                # "end_turn", "tool_use", "max_turns"
    usage: Optional[dict] = None
    events: list[dict] = field(default_factory=list)  # All raw events


class ClaudeSession:
    """Manages Claude Code CLI invocations for the dispatcher.

    Agent naming: When using --plugin-dir, agents are registered with a
    plugin-qualified name: "<plugin-dirname>:<agent-name>". The dispatcher
    must use this qualified name when specifying --agent. For example,
    if the plugin dir is ~/xpatcher/.claude-plugin/ and the agent file is
    agents/planner.md, the qualified name used with --agent is
    "xpatcher:planner" (derived from the plugin name in plugin.json, not
    the directory basename).
    """

    PLUGIN_NAME = "xpatcher"  # Must match plugin.json "name" field

    # Agents that must appear in the init event for the plugin to be considered loaded
    REQUIRED_AGENTS = [
        "xpatcher:planner",
        "xpatcher:plan-reviewer",
        "xpatcher:executor",
        "xpatcher:reviewer",
        "xpatcher:tester",
        "xpatcher:simplifier",
        "xpatcher:gap-detector",
        "xpatcher:tech-writer",
        "xpatcher:explorer",
    ]

    def preflight(self) -> "PreflightResult":
        """Verify Claude Code CLI is authenticated, responsive, and the xpatcher
        plugin is loaded with all expected agents.

        Runs a minimal invocation: `claude -p "hello" --output-format json
        --plugin-dir <path> --max-turns 1 --permission-mode bypassPermissions`

        Parses the init event from the JSON array output and checks:
        1. CLI responds without error (result event exists, is_error is false)
        2. Plugin "xpatcher" appears in init.plugins[]
        3. All REQUIRED_AGENTS appear in init.agents[]
        4. claude_code_version is recorded for compatibility tracking

        Returns PreflightResult with pass/fail status and diagnostics.
        Called by the dispatcher before starting any pipeline.
        """
        cmd = [
            "claude", "-p", "respond with ok",
            "--output-format", "json",
            "--plugin-dir", str(self.plugin_dir),
            "--max-turns", "1",
            "--permission-mode", "bypassPermissions",
        ]

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=30
            )
        except FileNotFoundError:
            return PreflightResult(
                ok=False,
                error="Claude Code CLI not found. Install from: https://claude.ai/code",
            )
        except subprocess.TimeoutExpired:
            return PreflightResult(
                ok=False,
                error="Claude Code CLI timed out after 30s. Check authentication: run `claude` interactively.",
            )

        if result.returncode != 0:
            return PreflightResult(
                ok=False,
                error=f"Claude Code CLI exited with code {result.returncode}: {result.stderr[:500]}",
            )

        try:
            events = json.loads(result.stdout)
        except json.JSONDecodeError:
            return PreflightResult(
                ok=False,
                error="Claude Code CLI returned invalid JSON. Unexpected output format.",
            )

        # Extract init and result events
        init_event = next((e for e in events if e.get("type") == "system" and e.get("subtype") == "init"), None)
        result_event = next((e for e in events if e.get("type") == "result"), None)

        if init_event is None:
            return PreflightResult(ok=False, error="No init event in CLI output.")

        # Check CLI responsiveness
        if result_event and result_event.get("is_error"):
            return PreflightResult(
                ok=False,
                error=f"Claude Code returned an error: {result_event.get('result', 'unknown')}",
            )

        # Check plugin loaded
        plugins = init_event.get("plugins", [])
        plugin_names = [p.get("name") for p in plugins]
        if self.PLUGIN_NAME not in plugin_names:
            return PreflightResult(
                ok=False,
                error=(
                    f"Plugin '{self.PLUGIN_NAME}' not found in loaded plugins: {plugin_names}. "
                    f"Check that {self.plugin_dir}/plugin.json exists and has '\"name\": \"{self.PLUGIN_NAME}\"'."
                ),
                cli_version=init_event.get("claude_code_version"),
            )

        # Check all required agents are registered
        agents = init_event.get("agents", [])
        missing = [a for a in self.REQUIRED_AGENTS if a not in agents]
        if missing:
            return PreflightResult(
                ok=False,
                error=f"Missing xpatcher agents: {missing}. Found: {[a for a in agents if 'xpatcher' in a]}",
                cli_version=init_event.get("claude_code_version"),
                plugin_loaded=True,
            )

        return PreflightResult(
            ok=True,
            cli_version=init_event.get("claude_code_version"),
            plugin_loaded=True,
            agents_found=agents,
            cost_usd=result_event.get("total_cost_usd", 0.0) if result_event else 0.0,
        )


@dataclass
class PreflightResult:
    """Result of the Claude Code CLI preflight check."""
    ok: bool
    error: str = ""
    cli_version: str = ""
    plugin_loaded: bool = False
    agents_found: list[str] = field(default_factory=list)
    cost_usd: float = 0.0


    def invoke(self, invocation: AgentInvocation) -> AgentResult:
        cmd = ["claude", "-p", invocation.prompt,
               "--output-format", "json",
               "--plugin-dir", str(self.plugin_dir),
               "--permission-mode", invocation.permission_mode]

        if invocation.agent:
            # Plugin agents use qualified name: "xpatcher:planner"
            qualified = invocation.agent
            if ":" not in qualified:
                qualified = f"{self.PLUGIN_NAME}:{qualified}"
            cmd.extend(["--agent", qualified])
        if invocation.session_id:
            cmd.extend(["--resume", invocation.session_id])
        if invocation.max_turns:
            cmd.extend(["--max-turns", str(invocation.max_turns)])
        if invocation.model:
            cmd.extend(["--model", invocation.model])
        if invocation.allowed_tools:
            cmd.extend(["--allowed-tools", ",".join(invocation.allowed_tools)])
        if invocation.disallowed_tools:
            cmd.extend(["--disallowed-tools", ",".join(invocation.disallowed_tools)])

        result = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=invocation.timeout, cwd=str(self.project_dir)
        )

        # Parse the Claude CLI JSON output: a JSON array of event objects
        events = json.loads(result.stdout)

        # Extract fields from typed events
        session_id = ""
        raw_text = ""
        cost_usd = 0.0
        duration_ms = 0
        num_turns = 0
        stop_reason = ""
        usage = None

        for event in events:
            etype = event.get("type")
            if etype == "system" and event.get("subtype") == "init":
                session_id = event.get("session_id", "")
            elif etype == "result":
                raw_text = event.get("result", "")
                session_id = event.get("session_id", session_id)
                cost_usd = event.get("total_cost_usd", 0.0)
                duration_ms = event.get("duration_ms", 0)
                num_turns = event.get("num_turns", 0)
                stop_reason = event.get("stop_reason", "")
                usage = event.get("usage")

        # Extract YAML from agent's text output
        yaml_content = self._extract_yaml(raw_text)

        return AgentResult(
            session_id=session_id,
            raw_text=raw_text,
            parsed=yaml_content,
            exit_code=result.returncode,
            cost_usd=cost_usd,
            duration_ms=duration_ms,
            num_turns=num_turns,
            stop_reason=stop_reason,
            usage=usage,
            events=events,
        )

    def _extract_yaml(self, text: str) -> dict | None:
        """Extract and parse YAML from agent output text."""
        # Try parsing the whole text as YAML
        try:
            return yaml.safe_load(text)
        except yaml.YAMLError:
            pass

        # Try extracting YAML between --- markers
        if "---" in text:
            parts = text.split("---")
            for part in parts[1:]:  # Skip text before first ---
                part = part.strip()
                if not part:
                    continue
                try:
                    return yaml.safe_load(part)
                except yaml.YAMLError:
                    continue

        # Try extracting from ```yaml code blocks
        import re
        yaml_blocks = re.findall(r'```ya?ml\s*\n(.*?)```', text, re.DOTALL)
        for block in yaml_blocks:
            try:
                return yaml.safe_load(block)
            except yaml.YAMLError:
                continue

        return None  # Triggers malformed output recovery
```

### 7.7.1 CLI Flag Validation Matrix

> **Validated:** 2026-03-29 against Claude Code CLI v2.1.87

All critical CLI flags have been empirically tested. The dispatcher's invocation pattern is confirmed to work.

| Flag | Status | Actual Behavior |
|------|--------|-----------------|
| `-p <prompt>` | **Validated** | Non-interactive mode, prints response and exits |
| `--output-format json` | **Validated** | Returns **JSON array** of typed event objects (not a single JSON object) |
| `--agent <name>` | **Validated** | Selects agent. Plugin agents use qualified name: `<plugin-name>:<agent-name>` (e.g., `xpatcher:planner`) |
| `--plugin-dir <path>` | **Validated** | Loads plugin; agents appear in init event's `agents` list with qualified names |
| `--resume <session_id>` | **Validated** | Resumes prior session. Same `session_id` returned. Context preserved across invocations |
| `--model <alias>` | **Validated** | Aliases work: `haiku` -> `claude-haiku-4-5-20251001`, `sonnet` -> `claude-sonnet-4-6`, `opus` -> `claude-opus-4-6` |
| `--max-turns N` | **Validated** | Limits agent turns. Result event has `subtype: "error_max_turns"` when limit reached |
| `--allowed-tools <list>` | **Validated** | Comma or space-separated tool names. Empty string `""` disables all tools |
| `--disallowed-tools <list>` | **Validated** | Successfully removes tools (e.g., `WebSearch`, `WebFetch`, `Agent`) from available set |
| `--permission-mode <mode>` | **Validated** | Supports `bypassPermissions`, `default`, `plan`, `auto`, etc. |
| `--bare` | **Validated** | Skips hooks, LSP, plugin sync, CLAUDE.md discovery. **Requires `ANTHROPIC_API_KEY` env var** (skips OAuth/keychain) |

**JSON Output Event Schema (validated):**

```
Event types in the JSON array:
├── type: "system", subtype: "init"
│     Fields: session_id, tools[], model, agents[], plugins[{name, path, source}],
│             cwd, permissionMode, claude_code_version, skills[]
├── type: "assistant"
│     Fields: message.content[] (thinking, text, tool_use), session_id
├── type: "user"
│     Fields: tool_use_id, tool_result (for tool responses)
├── type: "rate_limit_event"
│     Fields: rate_limit_info (status, resetsAt, rateLimitType)
└── type: "result"
      Fields: result (text), session_id, total_cost_usd, duration_ms,
              num_turns, stop_reason, usage{}, is_error, subtype
```

**Key discovery — agent naming:** When a plugin is loaded via `--plugin-dir`, agents are registered with the plugin's `name` field from `plugin.json` as a prefix: `<plugin-name>:<agent-name>`. For xpatcher (where `plugin.json` has `"name": "xpatcher"`), agents are referenced as `xpatcher:planner`, `xpatcher:executor`, etc. The `ClaudeSession` class auto-prefixes agent names (see above).

**Key discovery — cost tracking:** The `result` event includes `total_cost_usd` per invocation. This means basic cost accumulation is available in v1 without any additional infrastructure — the dispatcher can sum `cost_usd` across all invocations and display the running total in the completion summary.

**Key discovery — preflight via init event:** The `init` event (type: `"system"`, subtype: `"init"`) contains `plugins[]` (loaded plugins with name/path/source), `agents[]` (all available agent names including plugin-qualified), and `claude_code_version`. This makes it possible to verify at startup that: (a) Claude Code CLI is authenticated and responsive, (b) the xpatcher plugin loaded successfully, and (c) all 9 expected agents are registered. See `ClaudeSession.preflight()` above.

### YAML Validation Pipeline

Every agent output goes through a three-stage validation pipeline before acceptance:

```
Agent Output
    |
    v
[Stage 1: YAML Extraction]
    | Extract YAML from raw text (handle ```blocks, --- markers, prose)
    | If no YAML found -> MALFORMED_NO_YAML
    v
[Stage 2: Schema Validation]
    | Validate against Pydantic schema for the artifact type
    | Check required fields, types, enum values
    | If invalid -> MALFORMED_SCHEMA_ERROR
    v
[Stage 3: Semantic Validation]
    | Cross-reference checks (file paths exist, task IDs valid, etc.)
    | If invalid -> MALFORMED_SEMANTIC_ERROR
    v
[ACCEPTED] -> Write to artifact file
```

### ArtifactValidator

```python
import yaml
from pydantic import ValidationError
from typing import Any


class ValidationResult:
    def __init__(self, valid: bool, data: dict | None = None,
                 errors: list[str] | None = None, raw_text: str = ""):
        self.valid = valid
        self.data = data
        self.errors = errors or []
        self.raw_text = raw_text


class ArtifactValidator:
    """Validates agent YAML output against schemas with recovery."""

    MAX_FIX_ATTEMPTS = 2  # Max retries for malformed output

    def validate(self, raw_text: str, expected_type: str) -> ValidationResult:
        """Full validation pipeline: extract -> schema -> semantic."""

        # Stage 1: Extract YAML
        parsed = self._extract_yaml(raw_text)
        if parsed is None:
            return ValidationResult(
                valid=False, raw_text=raw_text,
                errors=["MALFORMED_NO_YAML: Could not extract valid YAML from agent output"]
            )

        # Stage 2: Schema validation
        schema_class = SCHEMAS.get(expected_type)
        if not schema_class:
            return ValidationResult(
                valid=False, data=parsed, raw_text=raw_text,
                errors=[f"Unknown artifact type: {expected_type}"]
            )

        try:
            validated = schema_class(**parsed)
            return ValidationResult(valid=True, data=validated.model_dump(), raw_text=raw_text)
        except ValidationError as e:
            errors = []
            for err in e.errors():
                field_path = " -> ".join(str(loc) for loc in err["loc"])
                errors.append(f"SCHEMA_ERROR [{field_path}]: {err['msg']}")
            return ValidationResult(valid=False, data=parsed, errors=errors, raw_text=raw_text)

    def _extract_yaml(self, text: str) -> dict | None:
        """Try multiple extraction strategies."""
        strategies = [
            self._try_raw_yaml,
            self._try_yaml_after_separator,
            self._try_yaml_code_block,
            self._try_strip_prose,
        ]
        for strategy in strategies:
            result = strategy(text)
            if result is not None:
                return result
        return None

    def _try_raw_yaml(self, text: str) -> dict | None:
        try:
            data = yaml.safe_load(text)
            return data if isinstance(data, dict) else None
        except yaml.YAMLError:
            return None

    def _try_yaml_after_separator(self, text: str) -> dict | None:
        if "---" not in text:
            return None
        parts = text.split("---")
        for part in parts[1:]:
            part = part.strip()
            if not part:
                continue
            try:
                data = yaml.safe_load(part)
                if isinstance(data, dict):
                    return data
            except yaml.YAMLError:
                continue
        return None

    def _try_yaml_code_block(self, text: str) -> dict | None:
        import re
        blocks = re.findall(r'```ya?ml\s*\n(.*?)```', text, re.DOTALL)
        for block in blocks:
            try:
                data = yaml.safe_load(block)
                if isinstance(data, dict):
                    return data
            except yaml.YAMLError:
                continue
        return None

    def _try_strip_prose(self, text: str) -> dict | None:
        """Strip leading prose, try to find YAML starting with a known key."""
        known_starts = ["schema_version:", "type:", "task_id:", "verdict:", "status:"]
        for start_key in known_starts:
            idx = text.find(start_key)
            if idx != -1:
                try:
                    data = yaml.safe_load(text[idx:])
                    if isinstance(data, dict):
                        return data
                except yaml.YAMLError:
                    continue
        return None
```

### Malformed Output Recovery (Same-Session Fix Protocol)

When an agent produces malformed YAML, the dispatcher retries using the **same session** (`--resume`) with a targeted fix prompt. This is critical: the agent has the full context of what it was trying to produce, so it can fix rather than regenerate.

```python
class MalformedOutputRecovery:
    """Handles retrying malformed agent output in the same session."""

    def __init__(self, session: ClaudeSession, validator: ArtifactValidator):
        self.session = session
        self.validator = validator

    def invoke_with_validation(
        self,
        invocation: AgentInvocation,
        expected_type: str,
    ) -> tuple[AgentResult, ValidationResult]:
        """
        Invoke agent, validate output, retry in same session if malformed.
        Returns the final (result, validation) after up to MAX_FIX_ATTEMPTS retries.
        """
        result = self.session.invoke(invocation)
        validation = self.validator.validate(result.raw_text, expected_type)

        attempt = 0
        while not validation.valid and attempt < ArtifactValidator.MAX_FIX_ATTEMPTS:
            attempt += 1

            # Build fix prompt with specific errors
            fix_prompt = self._build_fix_prompt(
                result.raw_text, validation.errors, expected_type, attempt
            )

            # Resume the SAME session so agent has full context
            fix_invocation = AgentInvocation(
                agent=invocation.agent,
                prompt=fix_prompt,
                session_id=result.session_id,  # CRITICAL: same session
                timeout=invocation.timeout,
                max_turns=10,  # Shorter turn limit for fixes
                allowed_tools=invocation.allowed_tools,
            )

            result = self.session.invoke(fix_invocation)
            validation = self.validator.validate(result.raw_text, expected_type)

        return result, validation

    def _build_fix_prompt(
        self, raw_text: str, errors: list[str], expected_type: str, attempt: int
    ) -> str:
        error_list = "\n".join(f"  - {e}" for e in errors)

        return f"""Your previous output had YAML formatting/schema errors. Please fix and re-output.

## Errors Found (attempt {attempt} of {ArtifactValidator.MAX_FIX_ATTEMPTS}):
{error_list}

## What You Produced:
```
{raw_text[:2000]}
```

## What To Do:
1. Fix ALL listed errors
2. Re-output the complete YAML document
3. Start with --- on its own line
4. Do NOT wrap in ```yaml``` code blocks
5. Do NOT include any explanation text before or after the YAML
6. Ensure all required fields are present for artifact type: {expected_type}

Output ONLY the corrected YAML:"""
```

### Failure Escalation

If validation fails after all retry attempts:

```python
def handle_persistent_validation_failure(
    self,
    task_id: str,
    agent_type: str,
    validation: ValidationResult,
    result: AgentResult,
) -> None:
    """Called when an agent's output remains invalid after all retries."""

    # 1. Save the raw output for debugging
    debug_path = os.path.join(
        self.feature_dir, "debug",
        f"{task_id}-{agent_type}-malformed.txt"
    )
    os.makedirs(os.path.dirname(debug_path), exist_ok=True)
    with open(debug_path, "w") as f:
        f.write(f"# Malformed output after {ArtifactValidator.MAX_FIX_ATTEMPTS} fix attempts\n")
        f.write(f"# Errors:\n")
        for err in validation.errors:
            f.write(f"#   {err}\n")
        f.write(f"\n{result.raw_text}")

    # 2. Log to pipeline state
    self.state.log_event(
        event="validation_failure",
        task_id=task_id,
        agent=agent_type,
        errors=validation.errors,
        debug_file=debug_path,
    )

    # 3. Decide action based on agent type
    if agent_type in ("planner", "gap_detector"):
        # Critical agents: escalate to human
        self.state.set_pipeline_state("waiting_for_human", reason={
            "type": "malformed_output",
            "agent": agent_type,
            "errors": validation.errors,
            "debug_file": debug_path,
            "question": f"Agent '{agent_type}' produced invalid output after retries. "
                        f"Review debug file and decide: (1) retry with fresh session, "
                        f"(2) manually create the artifact, (3) abort pipeline.",
        })
        self.notify("CRITICAL", f"Agent '{agent_type}' stuck: malformed output", debug_path)

    elif agent_type in ("executor", "reviewer", "tester"):
        # Task-level agents: mark task for retry with fresh session
        self.state.update_task(task_id, status="failed", reason="malformed_output")
        # Next loop iteration will retry with a fresh agent session
```

### Signal Handling

The dispatcher handles OS signals to prevent orphaned API-consuming subprocesses, corrupted state files, and lost session IDs.

```python
import signal
import time

class SignalHandler:
    """Graceful shutdown with two-tier SIGINT handling."""

    def __init__(self, state_file: "PipelineStateFile", process_manager: "ProcessManager"):
        self.state_file = state_file
        self.process_manager = process_manager
        self._shutdown_requested = False
        self._last_sigint_time = 0.0
        self._DOUBLE_SIGINT_WINDOW = 2.0  # seconds

        signal.signal(signal.SIGINT, self._handle_sigint)
        signal.signal(signal.SIGTERM, self._handle_sigterm)

    def _handle_sigint(self, signum, frame):
        now = time.time()

        if self._shutdown_requested and (now - self._last_sigint_time) < self._DOUBLE_SIGINT_WINDOW:
            # Double SIGINT within 2s: force-kill immediately
            self._force_shutdown()
        else:
            # Single SIGINT: graceful shutdown
            self._shutdown_requested = True
            self._last_sigint_time = now
            self._graceful_shutdown()

    def _handle_sigterm(self, signum, frame):
        # SIGTERM: same as single SIGINT (graceful)
        self._shutdown_requested = True
        self._graceful_shutdown()

    def _graceful_shutdown(self):
        """Single SIGINT / SIGTERM: graceful shutdown."""
        print("\n⚠ Shutting down gracefully (Ctrl+C again within 2s to force-kill)...")
        # 1. Set shutdown flag — dispatch loop checks this between stages
        # 2. Wait for current agent turn to complete (up to 30s timeout)
        self.process_manager.wait_for_current_turn(timeout=30)
        # 3. Save pipeline state with status=paused
        self.state_file.update(status="paused", paused_reason="user_interrupt")
        # 4. Record active session IDs for resume
        self.process_manager.save_active_sessions()
        # 5. Clean exit
        print(f"Pipeline paused. Resume with: xpatcher resume <pipeline-id>")
        raise SystemExit(0)

    def _force_shutdown(self):
        """Double SIGINT: force-kill all child processes."""
        print("\n⛔ Force-killing all agent processes...")
        # 1. Kill all child processes immediately
        self.process_manager.kill_all()
        # 2. Write crash recovery state (best-effort)
        try:
            self.state_file.update(
                status="paused",
                paused_reason="force_interrupt",
                crash_recovery=True
            )
        except Exception:
            pass  # State file may be corrupted; resume will detect this
        raise SystemExit(1)

    @property
    def should_stop(self) -> bool:
        """Check in dispatch loop between stages."""
        return self._shutdown_requested
```

**Signal behavior by context:**

| Signal | During agent execution | During human gate | During log viewing |
|--------|----------------------|-------------------|-------------------|
| Single SIGINT | Wait up to 30s for current turn, save state, exit | Exit immediately (no agent running) | Exit immediately |
| Double SIGINT (< 2s) | Force-kill agent processes, save crash state | N/A | N/A |
| SIGTERM | Same as single SIGINT | Same as single SIGINT | Same as single SIGINT |

**Resume behavior after interruption:**

| Interruption type | State saved | Resume behavior |
|-------------------|------------|-----------------|
| Graceful (single SIGINT/SIGTERM) | Clean: stage, task states, session IDs | Resumes from exact point; reuses sessions |
| Force (double SIGINT) | Best-effort: may be mid-write | Resumes from last clean state; re-runs current task from scratch |
| Crash (OOM, power loss) | Whatever was on disk | Reads pipeline-state.yaml; any task in `RUNNING` is reset to `READY` |

### Canonical Schema Reference (Pydantic Models)

These Pydantic models are the **single source of truth** for all artifact schemas. Agent prompt output format instructions (Section 4) and artifact file formats (Section 5) are derived from these models. At build time, agent prompts have their output format sections generated from these definitions.

When any discrepancy exists between these models, agent prompts, or YAML examples elsewhere in the spec, **these models win**.

```python
from pydantic import BaseModel, Field, field_validator
from typing import Optional, Literal
from enum import Enum


# ─── Shared Enums ───────────────────────────────────────────────

class ConcernSeverity(str, Enum):
    """Severity for executor-reported concerns (non-review context)."""
    info = "info"
    warning = "warning"
    critical = "critical"


class ReviewSeverity(str, Enum):
    """Severity for review and quality findings."""
    critical = "critical"
    major = "major"
    minor = "minor"
    nit = "nit"


class GapSeverity(str, Enum):
    """Severity for gap detector findings."""
    critical = "critical"
    major = "major"
    minor = "minor"


class ReviewCategory(str, Enum):
    """Categories for review findings."""
    correctness = "correctness"
    completeness = "completeness"
    security = "security"
    performance = "performance"
    style = "style"
    architecture = "architecture"
    testability = "testability"


class GapCategory(str, Enum):
    """Categories for gap findings."""
    plan_coverage = "plan-coverage"
    error_handling = "error-handling"
    edge_case = "edge-case"
    migration = "migration"
    documentation = "documentation"
    integration = "integration"


class Complexity(str, Enum):
    """Reused for estimated_complexity, risk severity, overall_risk."""
    low = "low"
    medium = "medium"
    high = "high"


class Confidence(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"


class SimplificationType(str, Enum):
    dedup = "dedup"
    flatten = "flatten"
    extract = "extract"
    remove_dead = "remove_dead"
    reuse_existing = "reuse_existing"
    constant = "constant"


# Task ID format: zero-padded sequential, e.g. task-001, task-012
# Gap re-entry tasks use an uppercase letter prefix: task-G001, task-G002 (see Section 3.4.1)
TASK_ID_PATTERN = r"^task-[A-Z]?\d{3}$"


# ─── Base ────────────────────────────────────────────────────────

class ArtifactBase(BaseModel):
    """Base for all YAML artifacts. Every artifact must have these."""
    schema_version: str = Field(default="1.0", pattern=r"^\d+\.\d+$")
    type: str


# ─── Plan Models ─────────────────────────────────────────────────

class PlanPhaseTask(BaseModel):
    id: str = Field(..., pattern=TASK_ID_PATTERN)
    description: str = Field(..., min_length=10)
    files: list[str] = Field(default_factory=list)
    acceptance: str = Field(..., min_length=10)
    depends_on: list[str] = Field(default_factory=list)
    estimated_complexity: Complexity
    notes: str = ""


class PlanPhase(BaseModel):
    id: str = Field(..., pattern=r"^phase-\d+$")
    name: str = Field(..., min_length=3)
    description: str
    tasks: list[PlanPhaseTask] = Field(..., min_length=1)


class PlanRisk(BaseModel):
    description: str = Field(..., min_length=10)
    mitigation: str = Field(..., min_length=10)
    severity: Complexity  # low | medium | high


class PlanOutput(ArtifactBase):
    """Agent: planner. Artifact type: 'plan'."""
    type: Literal["plan"] = "plan"
    summary: str = Field(..., min_length=20)
    phases: list[PlanPhase] = Field(..., min_length=1)
    risks: list[PlanRisk] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)


# ─── Execution Models ────────────────────────────────────────────

class FileChange(BaseModel):
    """Single file changed by executor. Replaces prior files_modified + files_created split."""
    path: str
    action: Literal["created", "modified", "deleted"]
    description: str


class Commit(BaseModel):
    hash: str
    message: str


class Concern(BaseModel):
    severity: ConcernSeverity
    description: str


class ExecutionOutput(ArtifactBase):
    """Agent: executor. Artifact type: 'execution_result'."""
    type: Literal["execution_result"] = "execution_result"
    task_id: str = Field(..., pattern=TASK_ID_PATTERN)
    status: Literal["completed", "blocked", "deviated"]
    summary: str = Field(..., min_length=10)
    files_changed: list[FileChange] = Field(default_factory=list)
    commits: list[Commit] = Field(default_factory=list)
    deviations: list[dict] = Field(default_factory=list)
    blockers: list[dict] = Field(default_factory=list)
    concerns: list[Concern] = Field(default_factory=list)


# ─── Review Models ───────────────────────────────────────────────

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
    """Agent: reviewer. Artifact type: 'review'."""
    type: Literal["review"] = "review"
    task_id: str = Field(..., pattern=TASK_ID_PATTERN)
    verdict: Literal["approve", "request_changes", "reject"]
    confidence: Confidence
    summary: str = Field(..., min_length=10)
    findings: list[ReviewFinding] = Field(default_factory=list)

    @field_validator("findings")
    @classmethod
    def reject_must_have_findings(cls, v, info):
        if info.data.get("verdict") == "reject" and not v:
            raise ValueError("Reject verdict must include at least one finding")
        return v


# ─── Test Models ─────────────────────────────────────────────────

class TestResult(BaseModel):
    name: str
    status: Literal["passed", "failed", "skipped", "error"]
    duration_ms: int = 0
    error_message: str = ""


class TestOutput(ArtifactBase):
    """Agent: tester. Artifact type: 'test_result'."""
    type: Literal["test_result"] = "test_result"
    task_id: str = Field(..., pattern=TASK_ID_PATTERN)
    overall: Literal["pass", "fail", "error"]
    test_results: list[TestResult] = Field(default_factory=list)
    coverage_pct: float = Field(default=0.0, ge=0.0, le=100.0)
    new_tests_added: int = Field(default=0, ge=0)
    regression_failures: list[str] = Field(default_factory=list)


# ─── Simplification Models ───────────────────────────────────────

class SimplificationItem(BaseModel):
    file: str
    line: int
    type: SimplificationType
    description: str
    applied: bool


class SimplificationOutput(ArtifactBase):
    """Agent: simplifier. Artifact type: 'simplification'."""
    type: Literal["simplification"] = "simplification"
    mode: Literal["dry_run", "apply"]
    simplifications: list[SimplificationItem] = Field(default_factory=list)
    lines_removed: int = Field(default=0, ge=0)
    lines_added: int = Field(default=0, ge=0)
    net_reduction: int = 0


# ─── Gap Detection Models ────────────────────────────────────────

class GapFinding(BaseModel):
    id: str
    severity: GapSeverity
    category: GapCategory
    description: str = Field(..., min_length=10)
    location: str = ""
    recommendation: str = ""


class GapPlanCompleteness(BaseModel):
    total_tasks: int
    completed: int
    partial: int = 0
    skipped: int = 0
    details: list[str] = Field(default_factory=list)


class GapOutput(ArtifactBase):
    """Agent: gap-detector. Artifact type: 'gap_report'."""
    type: Literal["gap_report"] = "gap_report"
    verdict: Literal["complete", "gaps_found"]
    gaps: list[GapFinding] = Field(default_factory=list)
    plan_completeness: GapPlanCompleteness | None = None
    overall_risk: Complexity | None = None


# ─── Documentation Models ────────────────────────────────────────

class DocChange(BaseModel):
    path: str
    action: Literal["updated", "created", "deleted"]
    section: str = ""
    description: str


class DocsReportOutput(ArtifactBase):
    """Agent: tech-writer. Artifact type: 'docs_report'."""
    type: Literal["docs_report"] = "docs_report"
    feature: str = ""
    docs_updated: list[DocChange] = Field(default_factory=list)
    docs_created: list[DocChange] = Field(default_factory=list)
    docs_skipped: list[dict] = Field(default_factory=list)
    summary: str = Field(..., min_length=10)


# ─── Schema Registry ─────────────────────────────────────────────

SCHEMAS = {
    "plan": PlanOutput,
    "execution_result": ExecutionOutput,
    "review": ReviewOutput,
    "test_result": TestOutput,
    "simplification": SimplificationOutput,
    "gap_report": GapOutput,
    "docs_report": DocsReportOutput,
}
```

#### Canonical Enum Quick Reference

| Concept | Enum | Values |
|---------|------|--------|
| Review severity | `ReviewSeverity` | `critical`, `major`, `minor`, `nit` |
| Review category | `ReviewCategory` | `correctness`, `completeness`, `security`, `performance`, `style`, `architecture`, `testability` |
| Review confidence | `Confidence` | `low`, `medium`, `high` |
| Review verdict | (Literal) | `approve`, `request_changes`, `reject` |
| Gap severity | `GapSeverity` | `critical`, `major`, `minor` |
| Gap category | `GapCategory` | `plan-coverage`, `error-handling`, `edge-case`, `migration`, `documentation`, `integration` |
| Executor concern severity | `ConcernSeverity` | `info`, `warning`, `critical` |
| Complexity / risk | `Complexity` | `low`, `medium`, `high` |
| Task ID format | `TASK_ID_PATTERN` | `task-NNN` (zero-padded, e.g. `task-001`) |

**Agent communication flow** (all mediated by the dispatcher):

```
Planner --writes--> plan.yaml, tasks/*.yaml
                        |
Dispatcher --reads--> picks next ready task
                        |
Executor --reads--> task YAML --writes--> code + commit
                        |
Dispatcher --reads--> result --writes--> task state update
                        |
Reviewer --reads--> task YAML + git diff --writes--> review YAML
                        |
Dispatcher --reads--> review verdict
    |
    +--> approved: merge branch, advance pipeline
    +--> changes_requested: update task with findings, re-launch executor
    +--> rejected: flag for human review
```

Agents never communicate directly. This ensures debuggability (every exchange is a file), replaceability (swap agents without changing others), and crash resilience (partial output is always on disk).

## 7.8 Smart Session Management

### Problem

Treating each agent invocation as a standalone `claude -p` call loses valuable context:
- The planner's understanding of the codebase is lost when the executor starts
- A reviewer loses context about what was already reviewed in prior iterations
- Each fresh invocation re-discovers the same codebase structure, wasting tokens
- Fix iterations lose the context of what was tried before

### Solution: Session Registry with Strategic Reuse

The dispatcher maintains a **session registry** that tracks Claude Code session IDs and enables context-preserving continuation.

#### Session Lineages and Adversarial Isolation

A **session lineage** is a chain of related sessions that share context through `--continue`/`--resume`. The dispatcher manages lineages per pipeline stage and per task.

**Critical invariant:** Review stages always start a **fresh session** with a **context bridge**. Reviewers never resume or inherit sessions from the stage being reviewed. This enforces adversarial isolation — the reviewer sees only artifacts (plan YAML, git diff, acceptance criteria), never the planner's or executor's reasoning chain.

```
Pipeline Session Lineage:
  planning-session  ──X──  plan-review-session     plan-fix-session  ->  ...
       |                        |                        |
   (context:              (FRESH session,           (RESUMES planner
    codebase,              context bridge:           session + injects
    intent,                plan artifact +           review findings)
    structure)             codebase refs only)

Task Session Lineage (per task):
  executor-session  ──X──  reviewer-session       fix-session  ──X──  re-review
       |                        |                      |                  |
   (context:              (FRESH session,          (RESUMES           (FRESH session,
    task spec +            context bridge:          executor            context bridge:
    codebase)              task spec + diff +       session +           updated diff +
                           ACs only)               review findings)    ACs only)

   ──X── = isolation boundary (no session inheritance, context bridge only)
```

#### SessionRegistry

```python
import json
import os
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime


@dataclass
class SessionRecord:
    session_id: str
    agent_type: str
    stage: str
    task_id: Optional[str]  # None for pipeline-level sessions
    created_at: str
    last_used_at: str
    turn_count: int = 0
    token_estimate: int = 0
    compacted: bool = False   # Whether /compact was triggered
    lineage: list[str] = field(default_factory=list)  # Parent session IDs


class SessionRegistry:
    """Manages Claude Code sessions across the pipeline."""

    def __init__(self, state_dir: str):
        self.state_dir = state_dir
        self.registry_path = os.path.join(state_dir, "sessions.yaml")
        self.sessions: dict[str, SessionRecord] = {}
        self._load()

    def _load(self):
        if os.path.exists(self.registry_path):
            import yaml
            with open(self.registry_path) as f:
                data = yaml.safe_load(f) or {}
            for key, rec in data.get("sessions", {}).items():
                self.sessions[key] = SessionRecord(**rec)

    def _save(self):
        import yaml
        data = {"sessions": {}}
        for key, rec in self.sessions.items():
            data["sessions"][key] = {
                "session_id": rec.session_id,
                "agent_type": rec.agent_type,
                "stage": rec.stage,
                "task_id": rec.task_id,
                "created_at": rec.created_at,
                "last_used_at": rec.last_used_at,
                "turn_count": rec.turn_count,
                "token_estimate": rec.token_estimate,
                "compacted": rec.compacted,
                "lineage": rec.lineage,
            }
        with open(self.registry_path, "w") as f:
            yaml.dump(data, f, default_flow_style=False)

    def register(self, result: "AgentResult", agent_type: str,
                 stage: str, task_id: str = None,
                 parent_session_key: str = None) -> str:
        """Register a new session from an agent result."""
        key = self._make_key(stage, agent_type, task_id)
        lineage = []
        if parent_session_key and parent_session_key in self.sessions:
            lineage = self.sessions[parent_session_key].lineage + [
                self.sessions[parent_session_key].session_id
            ]

        self.sessions[key] = SessionRecord(
            session_id=result.session_id,
            agent_type=agent_type,
            stage=stage,
            task_id=task_id,
            created_at=datetime.utcnow().isoformat() + "Z",
            last_used_at=datetime.utcnow().isoformat() + "Z",
            turn_count=result.usage.get("turns", 0) if result.usage else 0,
            token_estimate=result.usage.get("total_tokens", 0) if result.usage else 0,
            compacted=False,
            lineage=lineage,
        )
        self._save()
        return key

    def get_session_for_continuation(
        self, stage: str, agent_type: str, task_id: str = None
    ) -> Optional[str]:
        """
        Get session_id to continue, or None to start fresh.

        Returns session_id if:
        - A session exists for this stage/agent/task
        - It hasn't been compacted too many times
        - Its token estimate is below 80% of context window
        """
        key = self._make_key(stage, agent_type, task_id)
        rec = self.sessions.get(key)
        if rec is None:
            return None

        # Don't reuse if context is likely exhausted
        # Two-threshold model:
        #   compact_threshold (0.7) = trigger compaction before next use
        #   abandon_threshold (0.9) = start fresh session instead
        # Here we check the abandon threshold to decide whether to reuse.
        # Opus 4.6 has 200k default, 1M with [1m]
        context_limit = 1_000_000 if "[1m]" in self._get_model(agent_type) else 200_000
        if rec.token_estimate > context_limit * 0.9:
            return None  # Above abandon threshold — start fresh

        return rec.session_id

    def get_related_session(
        self, stage: str, agent_type: str, task_id: str = None
    ) -> Optional[str]:
        """
        Get a related session from a previous stage for context continuity.

        E.g., when starting the reviewer, get the executor's session
        so the reviewer inherits codebase context.
        """
        # Define session inheritance chains
        # IMPORTANT: Review stages NEVER inherit from the stage being reviewed.
        # This enforces adversarial isolation — reviewers must not see
        # executor/planner reasoning chains. Use context bridges instead.
        inheritance = {
            # plan_review: NO inheritance — fresh session + context bridge (adversarial isolation)
            ("plan_fix", "planner"): ("planning", "planner"),       # Resume planner's own session
            ("task_execution", "executor"): ("planning", "planner"),
            # task_review: NO inheritance — fresh session + context bridge (adversarial isolation)
            ("task_fix", "executor"): ("task_execution", "executor"),  # Resume executor's own session
            ("testing", "tester"): ("task_execution", "executor"),
            # gap_detection: NO inheritance — fresh session + context bridge
        }

        parent_spec = inheritance.get((stage, agent_type))
        if parent_spec is None:
            return None

        parent_stage, parent_agent = parent_spec
        parent_key = self._make_key(parent_stage, parent_agent, task_id)
        parent_rec = self.sessions.get(parent_key)
        if parent_rec is None:
            return None

        return parent_rec.session_id

    def _make_key(self, stage: str, agent_type: str, task_id: str = None) -> str:
        if task_id:
            return f"{stage}:{agent_type}:{task_id}"
        return f"{stage}:{agent_type}"

    def _get_model(self, agent_type: str) -> str:
        model_map = {
            "planner": "opus[1m]",
            "executor": "sonnet",
            "reviewer": "opus",
            "tester": "sonnet",
            "simplifier": "sonnet",
            "gap_detector": "opus",
            "tech_writer": "sonnet",
            "explorer": "haiku",
        }
        return model_map.get(agent_type, "sonnet")
```

#### SessionAwareDispatcher

```python
class SessionAwareDispatcher:
    """Dispatcher that reuses sessions for context continuity."""

    def __init__(self, session: ClaudeSession, registry: SessionRegistry, ...):
        self.session = session
        self.registry = registry

    def invoke_agent(
        self,
        agent_type: str,
        prompt: str,
        stage: str,
        task_id: str = None,
        continuation_mode: str = "smart",  # "smart" | "fresh" | "continue"
    ) -> tuple[AgentResult, ValidationResult]:
        """
        Invoke an agent with smart session management.

        continuation_mode:
          "smart" - dispatcher decides based on context size and stage
          "fresh" - always start a new session (e.g., after max retries)
          "continue" - always continue existing session (e.g., fix iterations)
        """

        session_id = None

        if continuation_mode == "continue":
            # Must continue same session (e.g., YAML fix retry, review iteration)
            session_id = self.registry.get_session_for_continuation(
                stage, agent_type, task_id
            )

        elif continuation_mode == "smart":
            # First, try to continue own session (same stage, same agent)
            session_id = self.registry.get_session_for_continuation(
                stage, agent_type, task_id
            )

            # If no own session, try to inherit from related stage
            if session_id is None:
                session_id = self.registry.get_related_session(
                    stage, agent_type, task_id
                )

        # Build invocation
        invocation = AgentInvocation(
            agent=f"xpatcher:{agent_type}",
            prompt=prompt,
            session_id=session_id,
            timeout=self._timeout_for(agent_type),
            allowed_tools=self._tools_for(agent_type),
        )

        # Invoke with validation and malformed-output recovery
        result, validation = self.recovery.invoke_with_validation(
            invocation, expected_type=self._expected_type(agent_type)
        )

        # Register session for future reuse
        parent_key = f"{stage}:{agent_type}:{task_id}" if task_id else f"{stage}:{agent_type}"
        self.registry.register(
            result, agent_type, stage, task_id,
            parent_session_key=parent_key
        )

        return result, validation
```

#### SessionCompactor

Long-running sessions accumulate context. The dispatcher proactively manages this:

```python
class SessionCompactor:
    """Manages context window health across pipeline sessions."""

    # Thresholds (fraction of context window)
    COMPACT_THRESHOLD = 0.7    # Trigger compaction at 70%
    ABANDON_THRESHOLD = 0.9    # Start fresh at 90%

    def should_compact(self, record: SessionRecord, model: str) -> bool:
        """Check if session needs compaction before next use."""
        context_limit = 1_000_000 if "[1m]" in model else 200_000
        return record.token_estimate > context_limit * self.COMPACT_THRESHOLD

    def should_start_fresh(self, record: SessionRecord, model: str) -> bool:
        """Check if session should be abandoned for a fresh start."""
        context_limit = 1_000_000 if "[1m]" in model else 200_000
        return record.token_estimate > context_limit * self.ABANDON_THRESHOLD

    def compact_session(self, session_id: str) -> str:
        """
        Compact a session by continuing it with a compaction prompt.
        Returns the session_id (same session, compacted).
        """
        # Claude Code handles /compact internally via auto-compaction.
        # We trigger it by continuing the session with awareness of token usage.
        # Setting CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=70 in agent env handles this.
        #
        # The dispatcher's role is:
        # 1. Track token estimates per session
        # 2. Set CLAUDE_AUTOCOMPACT_PCT_OVERRIDE appropriately
        # 3. When approaching limits, start fresh with a context summary
        return session_id
```

### 7.9 Prompt Assembly Specification (`context/builder.py`)

Every agent invocation requires a prompt assembled from the initial request, stage-specific context, and references to artifacts on disk. This section specifies how prompts are constructed.

#### Design Principles

1. **Agents discover artifacts autonomously.** The prompt provides the artifact directory path and references to key files via `@` notation. The agent uses its tools (Read, Glob, Grep) to explore and load what it needs. The dispatcher does NOT paste entire artifacts into the prompt.

2. **No token budget for v1.** Agents are not constrained by token budgets per invocation. They use their full context window and the dispatcher relies on context compaction (Section 7.8) for long sessions.

3. **Missing input = immediate stop.** If an agent's required input artifacts are missing (e.g., reviewer starts but no plan exists), the agent must immediately report the error. The dispatcher detects this via the output schema (`status: blocked`, `blockers: [...]`) and terminates execution with a notification to the user.

#### Prompt Structure Per Agent

| Agent | Initial Prompt Contains | Agent Discovers via Tools |
|-------|------------------------|--------------------------|
| **Planner** | User's feature request (string), project path, `@.xpatcher/<feature>/intent.yaml` | Codebase structure, existing code, conventions, README, CLAUDE.md |
| **Executor** | Task ID, `@.xpatcher/<feature>/tasks/todo/<task>.yaml`, review findings (if fix iteration) | Plan for context, codebase files, test patterns |
| **Reviewer** | Task ID, `@.xpatcher/<feature>/tasks/in-progress/<task>.yaml`, git diff ref | Acceptance criteria, original intent, prior review history |
| **Tester** | Task ID, `@.xpatcher/<feature>/tasks/in-progress/<task>.yaml`, list of modified files | Test framework patterns, existing test structure, acceptance criteria |
| **Simplifier** | List of recently modified files, plan summary, dryRun/apply flag | Broader codebase for reuse opportunities, existing utilities |
| **Gap Detector** | `@.xpatcher/<feature>/intent.yaml`, completed task summaries, gap report history | Plan, execution logs, git diff, test results |
| **Tech Writer** | `@.xpatcher/<feature>/plan-v{N}.yaml`, completed task list, git diff stat | Existing docs inventory, CHANGELOG, README |

#### PromptBuilder Implementation

```python
class PromptBuilder:
    """Assembles prompts for each agent invocation."""

    def __init__(self, feature_dir: str, project_dir: str):
        self.feature_dir = feature_dir
        self.project_dir = project_dir

    def build(self, agent_type: str, task_id: str = None, **kwargs) -> str:
        """Build the prompt for an agent invocation."""
        builder = getattr(self, f"_build_{agent_type}", None)
        if builder is None:
            raise ValueError(f"No prompt builder for agent: {agent_type}")
        return builder(task_id=task_id, **kwargs)

    def _build_planner(self, task_id: str = None, **kwargs) -> str:
        intent_path = os.path.join(self.feature_dir, "intent.yaml")
        self._require_file(intent_path, "intent.yaml")
        return f"""Implement the following feature for the project at {self.project_dir}.

Read the intent file for full details: @{intent_path}

Explore the codebase to understand the current architecture, then produce
a structured YAML plan as specified in your agent instructions."""

    def _build_executor(self, task_id: str = None, **kwargs) -> str:
        task_path = self._find_task_file(task_id, "todo")
        self._require_file(task_path, f"task {task_id}")
        parts = [f"Implement task {task_id}. Read the task specification: @{task_path}"]
        # If this is a fix iteration, include review findings
        review_findings = kwargs.get("review_findings")
        if review_findings:
            parts.append(f"\nThe reviewer found issues to fix:\n{review_findings}")
        return "\n".join(parts)

    def _build_reviewer(self, task_id: str = None, **kwargs) -> str:
        task_path = self._find_task_file(task_id, "in-progress")
        self._require_file(task_path, f"task {task_id}")
        # Use context bridge for isolation (no session inheritance)
        bridge = kwargs.get("context_bridge", "")
        return f"""Review the code changes for task {task_id}.

Task specification: @{task_path}
Git diff: Run `git diff` to see the changes.

{bridge}

Produce a structured review as specified in your agent instructions."""

    def _require_file(self, path: str, name: str):
        """Verify required input artifact exists."""
        if not os.path.exists(path):
            raise MissingArtifactError(
                f"Required artifact missing: {name} (expected at {path}). "
                f"Upstream stage may not have completed."
            )
```

**Error handling for missing inputs:** `MissingArtifactError` is caught by the dispatcher, which logs the error, sets the pipeline to `FAILED` with a descriptive message, and notifies the user. This replaces silent failures with explicit, actionable error messages.

#### ContextBridge

When a new agent must start fresh (different agent type, different model), the dispatcher creates a **context bridge** -- a summary of prior stage results:

```python
class ContextBridge:
    """Builds context summaries for cross-stage continuity."""

    def build_executor_context(self, task_id: str, feature_dir: str) -> str:
        """Build context for an executor starting a new task."""
        parts = []

        # 1. Original intent (always include)
        intent = self._read_artifact(feature_dir, "intent.yaml")
        if intent:
            parts.append(f"## Original Intent\n{intent.get('parsed', {}).get('goal', '')}")

        # 2. Approved plan (relevant phase only)
        plan = self._read_latest_artifact(feature_dir, "plan")
        if plan:
            task_phase = self._find_task_in_plan(plan, task_id)
            if task_phase:
                parts.append(f"## Plan Phase\n{yaml.dump(task_phase)}")

        # 3. Task spec
        task = self._read_artifact(feature_dir, f"tasks/{task_id}.yaml")
        if task:
            parts.append(f"## Task Specification\n{yaml.dump(task)}")

        # 4. Prior review findings (if this is a fix iteration)
        latest_review = self._read_latest_artifact(
            feature_dir, f"tasks/{task_id}-review"
        )
        if latest_review and latest_review.get("verdict") != "approve":
            parts.append(f"## Review Findings to Address\n{yaml.dump(latest_review.get('findings', []))}")

        # 5. Architectural decisions from plan
        if plan and plan.get("architectural_decisions"):
            parts.append(f"## Architectural Constraints\n{yaml.dump(plan['architectural_decisions'])}")

        return "\n\n".join(parts)

    def build_reviewer_context(self, task_id: str, feature_dir: str) -> str:
        """Build context for a reviewer about to review a task."""
        parts = []

        # 1. Task spec + acceptance criteria
        task = self._read_artifact(feature_dir, f"tasks/{task_id}.yaml")
        if task:
            parts.append(f"## Task to Review\n{yaml.dump(task)}")

        # 2. Original intent (to check alignment)
        intent = self._read_artifact(feature_dir, "intent.yaml")
        if intent:
            parts.append(f"## Original Intent\n{intent.get('parsed', {}).get('goal', '')}")

        # 3. Prior review history (so reviewer doesn't repeat findings)
        all_reviews = self._read_all_versions(feature_dir, f"tasks/{task_id}-review")
        if all_reviews:
            parts.append(f"## Prior Review History ({len(all_reviews)} reviews)")
            for v, review in all_reviews:
                parts.append(f"### Review v{v}: {review.get('verdict')}")
                findings_summary = [f.get('description', '')[:100] for f in review.get('findings', [])]
                parts.append("\n".join(f"  - {f}" for f in findings_summary))

        return "\n\n".join(parts)
```

#### sessions.yaml Schema and Example

```yaml
# .xpatcher/<feature>/sessions.yaml
sessions:
  "planning:planner":
    session_id: "sess_abc123"
    agent_type: planner
    stage: planning
    task_id: null
    created_at: "2026-03-28T14:30:22Z"
    last_used_at: "2026-03-28T14:45:00Z"
    turn_count: 12
    token_estimate: 45000
    compacted: false
    lineage: []

  "plan_review:reviewer":
    session_id: "sess_def456"
    agent_type: reviewer
    stage: plan_review
    task_id: null
    created_at: "2026-03-28T14:46:00Z"
    last_used_at: "2026-03-28T14:52:00Z"
    turn_count: 8
    token_estimate: 32000
    compacted: false
    lineage: ["sess_abc123"]  # Inherited from planner session

  "task_execution:executor:task-001":
    session_id: "sess_ghi789"
    agent_type: executor
    stage: task_execution
    task_id: task-001
    created_at: "2026-03-28T15:00:00Z"
    last_used_at: "2026-03-28T15:15:00Z"
    turn_count: 25
    token_estimate: 85000
    compacted: false
    lineage: ["sess_abc123"]  # Inherited from planner for codebase context

  "task_review:reviewer:task-001":
    session_id: "sess_jkl012"
    agent_type: reviewer
    stage: task_review
    task_id: task-001
    created_at: "2026-03-28T15:16:00Z"
    last_used_at: "2026-03-28T15:22:00Z"
    turn_count: 6
    token_estimate: 28000
    compacted: false
    lineage: ["sess_abc123", "sess_ghi789"]  # Planner -> executor lineage
```

#### Session Reuse Decision Matrix

| Scenario | Session Strategy | Rationale |
|----------|-----------------|-----------|
| Planner -> Plan Reviewer | Context bridge + fresh session | **Adversarial isolation**: reviewer must NOT see planner reasoning. Bridge transfers plan artifact + codebase context only |
| Plan Reviewer -> Plan Fixer | `--resume` planner session + inject review findings | Fixer needs planner's full codebase context to make targeted revisions |
| Plan approved -> Task Executor | Context bridge + fresh session | Different model (sonnet), planner context too large for task scope |
| Task Executor -> Task Reviewer | Context bridge + fresh session | **Adversarial isolation**: reviewer must NOT see executor reasoning chain. Bridge transfers task spec + git diff + acceptance criteria only |
| Task Reviewer -> Task Fixer | `--resume` executor session + inject review findings | Fixer needs executor's full context to make targeted fixes |
| Gap Detector | Context bridge + fresh session | Gap detector needs plan + completion reports but NOT individual agent reasoning |
| YAML fix retry | `--resume` same session | Agent has full context, just needs to fix formatting |
| After max retries exhausted | Fresh session | Old session's context is polluted with failed attempts |
| After human-in-the-loop pause (< 4 hours) | `--resume` paused session | Context is still valid |
| After human-in-the-loop pause (> 4 hours) | Context bridge + fresh session | Codebase may have changed, stale context risk |

#### Environment Configuration for Session Management

```yaml
# .xpatcher/config.yaml
session_management:
  # Auto-compaction threshold (% of context window)
  compact_threshold_pct: 70

  # Abandon session and start fresh at this threshold
  abandon_threshold_pct: 90

  # Human pause timeout (seconds) before fresh session
  stale_session_timeout: 14400  # 4 hours

  # Store session transcripts for debugging
  store_transcripts: true
  transcript_dir: ".xpatcher/<feature>/transcripts/"

  # Enable context bridging between stages
  context_bridging: true

  # Max lineage depth (how many parent sessions to track)
  max_lineage_depth: 5
```

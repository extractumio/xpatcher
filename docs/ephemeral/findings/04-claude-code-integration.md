# 04 -- Claude Code Integration: Plugin Architecture for xpatcher

## 0. Executive Summary

This document specifies how to package the xpatcher SDLC automation pipeline as a Claude Code plugin. The architecture uses a **Python dispatcher as the orchestration brain** and **Claude Code subagents as specialized workers**, connected via the Claude Code CLI/SDK in headless mode. The plugin exposes skills for interactive use and agents for programmatic dispatch, while hooks enforce process constraints at every tool call boundary.

The core design tension is between Claude Code's "flat" agent model (no nesting) and our need for multi-stage orchestration. We resolve this by keeping the Python dispatcher as the sole orchestrator -- it spawns subagents directly, collects their structured output, manages state transitions, and decides what runs next. Claude Code never needs to spawn a subagent from within a subagent.

---

## 1. Plugin Structure Design

### 1.1 Directory Layout

```
xpatcher/
├── .claude-plugin/
│   ├── plugin.json                    # Plugin manifest
│   ├── settings.json                  # Default settings (default agent, etc.)
│   ├── .mcp.json                      # MCP server declarations
│   ├── agents/
│   │   ├── planner.md                 # Strategic planning agent
│   │   ├── executor.md                # Code execution agent
│   │   ├── reviewer.md                # Code review agent
│   │   ├── tester.md                  # Test generation/execution agent
│   │   ├── simplifier.md             # Code simplification agent
│   │   ├── gap-detector.md            # Gap/coverage analysis agent
│   │   └── explorer.md               # Read-only exploration agent
│   ├── skills/
│   │   ├── plan/
│   │   │   └── SKILL.md              # /xpatcher:plan
│   │   ├── execute/
│   │   │   └── SKILL.md              # /xpatcher:execute
│   │   ├── review/
│   │   │   └── SKILL.md              # /xpatcher:review
│   │   ├── test/
│   │   │   └── SKILL.md              # /xpatcher:test
│   │   ├── simplify/
│   │   │   └── SKILL.md              # /xpatcher:simplify
│   │   ├── detect-gaps/
│   │   │   └── SKILL.md              # /xpatcher:detect-gaps
│   │   ├── status/
│   │   │   └── SKILL.md              # /xpatcher:status
│   │   └── pipeline/
│   │       └── SKILL.md              # /xpatcher:pipeline (full run)
│   └── hooks/
│       ├── pre_tool_use.py            # Tool-call validation
│       ├── post_tool_use.py           # Logging and artifact capture
│       └── lifecycle.py               # Subagent start/stop tracking
├── src/
│   ├── dispatcher/
│   │   ├── __init__.py
│   │   ├── core.py                    # Main dispatch loop
│   │   ├── session.py                 # Claude session management
│   │   ├── schemas.py                 # Pydantic models for structured output
│   │   ├── parallel.py               # Subprocess pool for parallel agents
│   │   ├── state.py                   # Pipeline state machine
│   │   └── retry.py                   # Error handling and retry logic
│   ├── context/
│   │   ├── __init__.py
│   │   ├── builder.py                 # Prompt/context assembly
│   │   ├── diff.py                    # Git diff context extraction
│   │   └── memory.py                  # Cross-session memory interface
│   ├── artifacts/
│   │   ├── __init__.py
│   │   ├── collector.py               # Gather outputs from agents
│   │   └── store.py                   # Persist artifacts to disk/DB
│   └── mcp_servers/
│       ├── __init__.py
│       └── xpatcher_server.py         # Custom MCP server (optional)
├── docs/
│   └── findings/
│       └── 04-claude-code-integration.md   # This document
├── tests/
│   ├── test_dispatcher.py
│   ├── test_schemas.py
│   └── test_pipeline.py
├── pyproject.toml
└── README.md
```

### 1.2 plugin.json Manifest

```json
{
  "name": "xpatcher",
  "description": "SDLC automation pipeline: plan, execute, review, test, simplify",
  "version": "0.1.0",
  "author": "Extractum"
}
```

This is intentionally minimal. The plugin manifest identifies the package; all behavior comes from the agents, skills, hooks, and MCP config declared in sibling files.

### 1.3 settings.json Defaults

```json
{
  "defaultAgent": "xpatcher:explorer",
  "preferences": {
    "xpatcher.pipeline.autoReview": true,
    "xpatcher.pipeline.autoTest": true,
    "xpatcher.pipeline.autoSimplify": false,
    "xpatcher.pipeline.maxRetries": 2,
    "xpatcher.pipeline.parallelAgents": 3
  }
}
```

Setting the default agent to `explorer` (a read-only haiku agent) means that when xpatcher is the active plugin, casual interactions are cheap and safe. The pipeline skills escalate to more powerful agents explicitly.

### 1.4 MCP Server Configuration (.mcp.json)

```json
{
  "mcpServers": {
    "xpatcher-state": {
      "command": "python",
      "args": ["-m", "src.mcp_servers.xpatcher_server"],
      "cwd": "${PLUGIN_DIR}/..",
      "env": {
        "XPATCHER_STATE_DIR": "${PROJECT_DIR}/.xpatcher"
      }
    }
  }
}
```

**Important caveat**: Plugin subagents cannot use `mcpServers` in their own frontmatter. This MCP server is declared at the plugin level and becomes available to the main Claude Code session. Subagents access state through file I/O and tool calls instead. See Section 6 for full workaround details.

---

## 2. Subagent Definitions

### 2.1 Design Principles

- **Model selection**: Opus for planning and review (requires deep reasoning). Sonnet for execution (good balance of capability and speed). Haiku for exploration and quick checks (fast, cheap).
- **Permission mode**: All plugin subagents run under the plugin's permission constraints (no `permissionMode` override allowed). The dispatcher handles approval flows externally.
- **Tool restrictions**: Each agent gets the minimum tool set needed for its role. This prevents an executor from reviewing its own work, or a reviewer from modifying code.
- **No nesting**: Subagents cannot spawn other subagents. The Python dispatcher is the sole orchestrator. Each agent completes a unit of work and returns structured output.

### 2.2 Planner Agent

```markdown
---
name: planner
description: >
  Analyzes requirements, existing code, and constraints to produce a structured
  implementation plan. Reads broadly, writes nothing. Output is a JSON plan
  document with phases, tasks, dependencies, and risk assessments.
model: claude-opus-4-20250514
maxTurns: 30
tools:
  - Read
  - Glob
  - Grep
  - Bash(git log:git diff:git show:git blame:ls:wc)
  - WebSearch
  - WebFetch
memory:
  - scope: project
    key: planning-patterns
effort: high
---

You are the **xpatcher Planner**. Your job is to produce an implementation plan.

## Inputs
You receive:
- A task description (what needs to be built or changed)
- Relevant file paths or patterns to investigate
- Any constraints or architectural decisions from prior plans

## Process
1. **Explore** the codebase to understand current structure, patterns, and conventions.
2. **Identify** all files that need to change and why.
3. **Decompose** the work into ordered, atomic tasks with clear acceptance criteria.
4. **Assess** risks, unknowns, and areas where the executor will need to make judgment calls.
5. **Output** your plan as a structured JSON document.

## Output Format
Respond with a single JSON object (no markdown fencing) matching this schema:

```json
{
  "summary": "string -- one-paragraph summary of the plan",
  "phases": [
    {
      "id": "phase-1",
      "name": "string",
      "description": "string",
      "tasks": [
        {
          "id": "task-1.1",
          "description": "string -- what to do",
          "files": ["path/to/file.py"],
          "acceptance": "string -- how to verify this is done correctly",
          "dependsOn": [],
          "estimatedComplexity": "low|medium|high",
          "notes": "string -- gotchas, judgment calls, alternatives"
        }
      ]
    }
  ],
  "risks": [
    {
      "description": "string",
      "mitigation": "string",
      "severity": "low|medium|high"
    }
  ],
  "openQuestions": ["string"]
}
```

## Constraints
- You MUST NOT write or modify any files. You are read-only.
- You MUST NOT produce code. Only produce the plan document.
- If the task is ambiguous, include the ambiguity in `openQuestions` rather than guessing.
- Reference specific file paths and line ranges wherever possible.
```

### 2.3 Executor Agent

```markdown
---
name: executor
description: >
  Implements code changes according to a plan. Has full write access.
  Follows the plan precisely, reports deviations.
model: claude-sonnet-4-20250514
maxTurns: 50
tools:
  - Read
  - Edit
  - Write
  - Glob
  - Grep
  - Bash
  - LSP
  - NotebookEdit
disallowedTools:
  - Agent
  - WebSearch
  - WebFetch
  - SendMessage
memory:
  - scope: project
    key: coding-patterns
effort: high
---

You are the **xpatcher Executor**. You implement code changes according to a plan.

## Inputs
You receive:
- A structured plan (JSON) specifying exactly what to build
- The current task ID you are working on
- Any feedback from a prior review cycle

## Rules
1. **Follow the plan**. Do not add features, refactor unrelated code, or "improve" things outside scope.
2. **One task at a time**. Complete the current task fully before reporting done.
3. **Preserve conventions**. Match the existing code style, naming patterns, import organization, and test structure already present in the codebase.
4. **Test as you go**. If the task includes acceptance criteria, verify them before reporting completion.
5. **Report deviations**. If you must deviate from the plan, explain why in your output.

## Output Format
Respond with a JSON object:

```json
{
  "taskId": "task-1.1",
  "status": "completed|blocked|deviated",
  "filesModified": ["path/to/file.py"],
  "filesCreated": ["path/to/new_file.py"],
  "summary": "string -- what you did",
  "deviations": ["string -- any deviations from the plan and why"],
  "blockers": ["string -- anything preventing completion"],
  "testResults": "string -- any test output or verification performed"
}
```

## Constraints
- Do NOT search the web or fetch external resources. Work with what is in the repo.
- Do NOT spawn subagents or delegate work.
- Do NOT modify files outside the scope of the current task.
```

### 2.4 Reviewer Agent

```markdown
---
name: reviewer
description: >
  Reviews code changes for correctness, style, security, and adherence to plan.
  Read-only. Produces structured review feedback.
model: claude-opus-4-20250514
maxTurns: 25
tools:
  - Read
  - Glob
  - Grep
  - Bash(git diff:git log:git show:git blame:ls:wc:python -m pytest --collect-only)
  - LSP
memory:
  - scope: project
    key: review-standards
effort: high
---

You are the **xpatcher Reviewer**. You review code changes for quality.

## Inputs
You receive:
- The original plan (JSON)
- The executor's completion report (JSON)
- A git diff of all changes made

## Review Checklist
1. **Correctness**: Does the code do what the plan specified? Are edge cases handled?
2. **Completeness**: Were all tasks in scope addressed? Anything missing?
3. **Style**: Does the code match existing conventions? Naming, formatting, imports?
4. **Security**: Any obvious vulnerabilities? Unsanitized inputs, exposed secrets, unsafe operations?
5. **Testability**: Are changes testable? Were tests added/updated where needed?
6. **Simplicity**: Is there unnecessary complexity? Could anything be simpler without losing functionality?

## Output Format
Respond with a JSON object:

```json
{
  "verdict": "approve|request-changes|reject",
  "score": 0-100,
  "findings": [
    {
      "severity": "critical|major|minor|nit",
      "category": "correctness|completeness|style|security|testability|simplicity",
      "file": "path/to/file.py",
      "line": 42,
      "description": "string -- what is wrong",
      "suggestion": "string -- how to fix it"
    }
  ],
  "summary": "string -- overall assessment",
  "mustFix": ["string -- items that must be addressed before approval"],
  "niceToHave": ["string -- items that would improve the code but are not blocking"]
}
```

## Constraints
- You MUST NOT modify any files. You are read-only.
- Be specific: reference exact file paths and line numbers.
- Distinguish clearly between blocking issues and suggestions.
- If the code is good, say so. Do not manufacture findings.
```

### 2.5 Tester Agent

```markdown
---
name: tester
description: >
  Generates and runs tests for code changes. Has write access limited
  to test files. Validates acceptance criteria from the plan.
model: claude-sonnet-4-20250514
maxTurns: 40
tools:
  - Read
  - Edit
  - Write
  - Glob
  - Grep
  - Bash
  - LSP
memory:
  - scope: project
    key: test-patterns
effort: high
---

You are the **xpatcher Tester**. You write and run tests for code changes.

## Inputs
You receive:
- The plan with acceptance criteria per task
- The executor's completion report listing modified/created files
- The current test suite structure

## Process
1. **Understand** what changed by reading the modified files.
2. **Identify** existing test patterns (framework, structure, naming, fixtures).
3. **Write** tests that validate the acceptance criteria from the plan.
4. **Run** the test suite and report results.
5. **Fix** any test infrastructure issues (imports, fixtures, mocks) but do NOT fix the code under test -- report failures as findings.

## Output Format
```json
{
  "testsCreated": ["path/to/test_file.py"],
  "testsModified": ["path/to/existing_test.py"],
  "testResults": {
    "passed": 12,
    "failed": 1,
    "errors": 0,
    "skipped": 0
  },
  "failures": [
    {
      "test": "test_name",
      "file": "path/to/test.py",
      "error": "string -- error message",
      "analysis": "string -- likely cause in the code under test"
    }
  ],
  "coverageNotes": "string -- areas not covered and why",
  "summary": "string"
}
```

## Constraints
- Only write to test files (files matching `test_*`, `*_test.*`, `tests/`, `__tests__/`, `*.spec.*`, `*.test.*`).
- Do NOT modify production code. If tests fail, report the failure.
- Match the existing test framework and patterns exactly.
```

### 2.6 Simplifier Agent

```markdown
---
name: simplifier
description: >
  Reviews recently changed code for unnecessary complexity, duplication,
  and opportunities to reuse existing utilities. Suggests and optionally
  applies simplifications.
model: claude-sonnet-4-20250514
maxTurns: 30
tools:
  - Read
  - Edit
  - Glob
  - Grep
  - Bash(git diff:git log:ls:wc)
  - LSP
memory:
  - scope: project
    key: simplification-patterns
effort: high
---

You are the **xpatcher Simplifier**. You reduce unnecessary complexity.

## Inputs
You receive:
- A list of files recently modified
- The original plan summary (for context on intent)
- A flag: `dryRun` (analyze only) or `apply` (make changes)

## Process
1. **Read** all recently modified files.
2. **Search** the broader codebase for existing utilities, patterns, or abstractions that the new code could reuse.
3. **Identify** simplification opportunities:
   - Duplicated logic that could use an existing helper
   - Over-abstraction (unnecessary classes, layers, or indirection)
   - Dead code or unused imports introduced by the changes
   - Overly complex conditionals that could be flattened
   - Magic numbers or strings that should be constants
4. If `apply` mode: make the simplifications. If `dryRun`: report only.

## Output Format
```json
{
  "mode": "dryRun|apply",
  "simplifications": [
    {
      "file": "path/to/file.py",
      "line": 42,
      "type": "dedup|flatten|extract|remove-dead|reuse-existing|constant",
      "description": "string",
      "applied": true
    }
  ],
  "linesRemoved": 15,
  "linesAdded": 5,
  "netReduction": 10,
  "summary": "string"
}
```

## Constraints
- Simplifications must be behavior-preserving. Do NOT change functionality.
- When reusing existing utilities, verify they actually do what the new code needs.
- In `dryRun` mode, do NOT modify any files.
```

### 2.7 Gap Detector Agent

```markdown
---
name: gap-detector
description: >
  Analyzes plan vs. implementation to find gaps: missing error handling,
  untested paths, unaddressed requirements, incomplete migrations.
model: claude-opus-4-20250514
maxTurns: 25
tools:
  - Read
  - Glob
  - Grep
  - Bash(git diff:git log:git show:ls:wc:python -m pytest --collect-only)
  - LSP
memory:
  - scope: project
    key: gap-patterns
effort: high
---

You are the **xpatcher Gap Detector**. You find what was missed.

## Inputs
You receive:
- The original plan
- The executor's completion report
- The reviewer's findings (if any)
- The tester's report (if any)
- The current git diff

## Analysis Dimensions
1. **Plan coverage**: Which plan tasks were completed, skipped, or only partially done?
2. **Error handling**: Are all error paths covered? What happens on invalid input, network failure, disk full, permission denied?
3. **Edge cases**: Empty collections, null/None values, Unicode, very large inputs, concurrent access?
4. **Migration gaps**: If this changes data formats, APIs, or schemas -- are all consumers updated? Is there a migration path?
5. **Documentation**: Were public APIs documented? Are new config options explained?
6. **Integration points**: Do all callers of changed functions pass the right arguments? Were type signatures updated everywhere?

## Output Format
```json
{
  "gaps": [
    {
      "severity": "critical|major|minor",
      "category": "plan-coverage|error-handling|edge-case|migration|documentation|integration",
      "description": "string",
      "location": "path/to/file.py:42 or general",
      "recommendation": "string"
    }
  ],
  "planCompleteness": {
    "totalTasks": 8,
    "completed": 7,
    "partial": 1,
    "skipped": 0,
    "details": ["task-2.3 partially done: missing error handler for timeout"]
  },
  "overallRisk": "low|medium|high",
  "summary": "string"
}
```

## Constraints
- You MUST NOT modify any files. You are read-only.
- Be thorough but practical. Focus on gaps that would cause production issues.
- Do not re-do the reviewer's job. Focus on structural and systemic gaps.
```

### 2.8 Explorer Agent

```markdown
---
name: explorer
description: >
  Lightweight read-only exploration agent for quick codebase questions.
  Used as the default agent for interactive sessions.
model: claude-haiku-4-20250514
maxTurns: 15
tools:
  - Read
  - Glob
  - Grep
  - Bash(git log:git diff:git show:git blame:ls:wc:file:du)
effort: low
---

You are the **xpatcher Explorer**. Answer questions about the codebase quickly and accurately.

Keep responses concise. Reference specific file paths and line numbers.
Do not modify any files. If asked to make changes, suggest using the
appropriate xpatcher skill instead (/xpatcher:plan, /xpatcher:execute, etc.).
```

### 2.9 Model Selection Rationale

| Agent | Model | Why |
|-------|-------|-----|
| planner | opus | Requires deep understanding of requirements, architecture, and tradeoffs. Must produce a coherent multi-step plan. |
| executor | sonnet | Good balance of capability, speed, and cost. Follows plans well. High maxTurns to handle large implementations. |
| reviewer | opus | Must catch subtle bugs, security issues, and architectural drift. Deep reasoning is critical. |
| tester | sonnet | Test writing is well-structured work. Sonnet is capable and faster than opus for this. |
| simplifier | sonnet | Pattern matching and refactoring. Does not need opus-level reasoning for most simplifications. |
| gap-detector | opus | Must synthesize across plan, implementation, tests, and review findings. Broad analytical reasoning. |
| explorer | haiku | Quick, cheap, read-only. Used for casual interaction, not pipeline stages. |

---

## 3. Python Dispatcher Integration

### 3.1 Architecture Overview

```
                    ┌─────────────────────────────────┐
                    │        Python Dispatcher         │
                    │                                  │
                    │  ┌──────────┐  ┌──────────────┐  │
                    │  │  State   │  │   Session     │  │
                    │  │  Machine │  │   Manager     │  │
                    │  └────┬─────┘  └──────┬───────┘  │
                    │       │               │          │
                    │  ┌────┴───────────────┴───────┐  │
                    │  │      Core Dispatch Loop     │  │
                    │  └────┬──────┬──────┬────┬────┘  │
                    │       │      │      │    │       │
                    └───────┼──────┼──────┼────┼───────┘
                            │      │      │    │
                      ┌─────┘  ┌───┘  ┌───┘  ┌─┘
                      ▼        ▼      ▼      ▼
                  planner  executor reviewer tester
                  (opus)   (sonnet) (opus)   (sonnet)
                      │        │      │      │
                      └────────┴──────┴──────┘
                              │
                     Claude Code CLI (headless)
```

The dispatcher never runs inside Claude Code. It is a standalone Python process that invokes `claude` CLI subprocesses.

### 3.2 CLI Invocation Pattern

The dispatcher calls Claude Code in headless mode using `claude -p` with structured output:

```python
# src/dispatcher/session.py

import subprocess
import json
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class AgentInvocation:
    """Parameters for a single agent invocation."""
    agent: str                          # e.g., "xpatcher:planner"
    prompt: str                         # The full prompt including context
    json_schema: Optional[dict] = None  # For structured output validation
    session_id: Optional[str] = None    # For --continue/--resume
    timeout: int = 300                  # seconds
    max_turns: Optional[int] = None     # Override agent default
    allowed_tools: Optional[list[str]] = None  # Additional tool restrictions
    system_prompt_append: Optional[str] = None


@dataclass
class AgentResult:
    """Result from a single agent invocation."""
    success: bool
    output: dict                        # Parsed JSON output
    raw_output: str                     # Raw stdout
    session_id: Optional[str] = None    # For session continuation
    exit_code: int = 0
    stderr: str = ""
    duration_seconds: float = 0.0


class ClaudeSession:
    """Manages Claude Code CLI invocations."""

    def __init__(self, project_dir: str, plugin_dir: str):
        self.project_dir = Path(project_dir)
        self.plugin_dir = Path(plugin_dir)
        self._active_sessions: dict[str, str] = {}  # agent -> session_id

    def invoke(self, invocation: AgentInvocation) -> AgentResult:
        """
        Invoke a Claude Code agent and return structured output.
        """
        import time
        start = time.monotonic()

        cmd = self._build_command(invocation)

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=invocation.timeout,
                cwd=str(self.project_dir),
            )
        except subprocess.TimeoutExpired as e:
            return AgentResult(
                success=False,
                output={"error": "timeout", "timeout_seconds": invocation.timeout},
                raw_output=e.stdout or "",
                exit_code=-1,
                stderr=e.stderr or "",
                duration_seconds=time.monotonic() - start,
            )

        duration = time.monotonic() - start

        # Parse structured output
        parsed = self._parse_output(result.stdout)

        # Extract session ID from output for continuation
        session_id = self._extract_session_id(result.stdout)
        if session_id:
            self._active_sessions[invocation.agent] = session_id

        return AgentResult(
            success=result.returncode == 0,
            output=parsed,
            raw_output=result.stdout,
            session_id=session_id,
            exit_code=result.returncode,
            stderr=result.stderr,
            duration_seconds=duration,
        )

    def _build_command(self, inv: AgentInvocation) -> list[str]:
        cmd = [
            "claude",
            "-p", inv.prompt,
            "--output-format", "json",
            "--plugin-dir", str(self.plugin_dir),
            "--bare",                          # Skip tips, MCP startup noise
        ]

        # Agent selection: use --agents for inline or rely on plugin agents
        # For plugin agents, we prepend the agent name to the prompt
        # since -p mode doesn't have /agent commands.
        # Instead, use --agents with a reference.
        if inv.agent:
            cmd.extend(["--agent", inv.agent])

        if inv.json_schema:
            schema_str = json.dumps(inv.json_schema)
            cmd.extend(["--json-schema", schema_str])

        if inv.session_id:
            cmd.extend(["--resume", inv.session_id])
        elif inv.agent in self._active_sessions:
            cmd.extend(["--continue", self._active_sessions[inv.agent]])

        if inv.max_turns:
            cmd.extend(["--max-turns", str(inv.max_turns)])

        if inv.allowed_tools:
            for tool in inv.allowed_tools:
                cmd.extend(["--allowedTools", tool])

        if inv.system_prompt_append:
            cmd.extend(["--append-system-prompt", inv.system_prompt_append])

        return cmd

    def _parse_output(self, stdout: str) -> dict:
        """Parse JSON output from Claude CLI."""
        try:
            # --output-format json wraps output in a JSON envelope
            envelope = json.loads(stdout)
            # The actual content is in the 'result' field
            if isinstance(envelope, dict) and "result" in envelope:
                content = envelope["result"]
                # Try to parse inner JSON (agent output)
                try:
                    return json.loads(content)
                except (json.JSONDecodeError, TypeError):
                    return {"raw": content}
            return envelope
        except json.JSONDecodeError:
            return {"raw": stdout, "parse_error": True}

    def _extract_session_id(self, stdout: str) -> Optional[str]:
        """Extract session ID from Claude CLI output for continuation."""
        try:
            envelope = json.loads(stdout)
            return envelope.get("session_id")
        except (json.JSONDecodeError, KeyError):
            return None
```

### 3.3 Core Dispatch Loop

```python
# src/dispatcher/core.py

from __future__ import annotations
import logging
from typing import Optional
from .session import ClaudeSession, AgentInvocation, AgentResult
from .state import PipelineState, Stage
from .schemas import (
    PlanOutput, ExecutionOutput, ReviewOutput,
    TestOutput, SimplifyOutput, GapOutput,
)
from .parallel import AgentPool
from .retry import RetryPolicy, with_retry
from ..context.builder import ContextBuilder

logger = logging.getLogger(__name__)


class Dispatcher:
    """
    Orchestrates the xpatcher SDLC pipeline.

    The dispatcher is the sole orchestrator. It:
    1. Manages pipeline state transitions
    2. Builds context/prompts for each agent
    3. Invokes agents via Claude Code CLI
    4. Parses and validates structured output
    5. Decides what runs next based on results
    6. Handles retries on failure
    """

    def __init__(
        self,
        project_dir: str,
        plugin_dir: str,
        config: Optional[dict] = None,
    ):
        self.session = ClaudeSession(project_dir, plugin_dir)
        self.state = PipelineState()
        self.context = ContextBuilder(project_dir)
        self.pool = AgentPool(max_workers=config.get("parallelAgents", 3) if config else 3)
        self.retry_policy = RetryPolicy(
            max_retries=config.get("maxRetries", 2) if config else 2
        )
        self.config = config or {}

    def run_pipeline(self, task_description: str) -> dict:
        """
        Run the full SDLC pipeline for a given task.

        Returns a summary dict with results from all stages.
        """
        results = {}

        # Stage 1: Plan
        self.state.transition(Stage.PLANNING)
        plan_result = self._run_planning(task_description)
        results["plan"] = plan_result

        if not plan_result.success:
            return self._abort("Planning failed", results)

        plan = PlanOutput.model_validate(plan_result.output)

        # Check for open questions that need human input
        if plan.openQuestions:
            self.state.transition(Stage.BLOCKED)
            results["blockedReason"] = "Plan has open questions requiring human input"
            results["openQuestions"] = plan.openQuestions
            return results

        # Stage 2: Execute (task by task, respecting dependencies)
        self.state.transition(Stage.EXECUTING)
        exec_results = self._run_execution(plan)
        results["execution"] = exec_results

        if any(r.output.get("status") == "blocked" for r in exec_results):
            return self._abort("Execution blocked", results)

        # Stage 3: Review
        self.state.transition(Stage.REVIEWING)
        review_result = self._run_review(plan, exec_results)
        results["review"] = review_result

        review = ReviewOutput.model_validate(review_result.output)

        # Stage 3b: Handle review feedback loop
        retry_count = 0
        while review.verdict == "request-changes" and retry_count < self.retry_policy.max_retries:
            retry_count += 1
            logger.info(f"Review requested changes (attempt {retry_count})")

            self.state.transition(Stage.EXECUTING)
            fix_results = self._run_fixes(plan, review)
            results[f"fix_round_{retry_count}"] = fix_results

            self.state.transition(Stage.REVIEWING)
            review_result = self._run_review(plan, fix_results)
            review = ReviewOutput.model_validate(review_result.output)
            results[f"review_round_{retry_count}"] = review_result

        if review.verdict == "reject":
            return self._abort("Review rejected changes", results)

        # Stage 4: Test (can run in parallel with gap detection)
        parallel_results = self._run_parallel_analysis(plan, exec_results)
        results.update(parallel_results)

        # Stage 5: Simplify (optional)
        if self.config.get("autoSimplify", False):
            self.state.transition(Stage.SIMPLIFYING)
            simplify_result = self._run_simplification(plan, exec_results)
            results["simplify"] = simplify_result

        self.state.transition(Stage.COMPLETE)
        results["status"] = "complete"
        return results

    def _run_planning(self, task_description: str) -> AgentResult:
        """Invoke the planner agent."""
        context = self.context.build_planning_context(task_description)

        return with_retry(
            self.retry_policy,
            lambda: self.session.invoke(AgentInvocation(
                agent="xpatcher:planner",
                prompt=context,
                json_schema=PlanOutput.model_json_schema(),
            )),
        )

    def _run_execution(self, plan: PlanOutput) -> list[AgentResult]:
        """
        Execute plan tasks in dependency order.

        Tasks without dependencies can run in parallel.
        Tasks with dependencies wait until dependencies complete.
        """
        results = []
        completed_tasks: set[str] = set()

        for phase in plan.phases:
            # Group tasks by dependency readiness
            pending = list(phase.tasks)

            while pending:
                # Find tasks whose dependencies are all completed
                ready = [
                    t for t in pending
                    if all(dep in completed_tasks for dep in t.dependsOn)
                ]

                if not ready:
                    # Circular dependency or missing dependency
                    logger.error(f"No ready tasks but {len(pending)} pending. Possible circular dependency.")
                    break

                # Execute ready tasks (in parallel if multiple)
                if len(ready) == 1:
                    result = self._execute_single_task(plan, ready[0])
                    results.append(result)
                    if result.output.get("status") == "completed":
                        completed_tasks.add(ready[0].id)
                else:
                    batch_results = self.pool.run_parallel([
                        lambda t=task: self._execute_single_task(plan, t)
                        for task in ready
                    ])
                    for task, result in zip(ready, batch_results):
                        results.append(result)
                        if result.output.get("status") == "completed":
                            completed_tasks.add(task.id)

                # Remove completed tasks from pending
                pending = [t for t in pending if t.id not in completed_tasks]

        return results

    def _execute_single_task(self, plan: PlanOutput, task) -> AgentResult:
        """Execute a single task from the plan."""
        context = self.context.build_execution_context(plan, task)

        return with_retry(
            self.retry_policy,
            lambda: self.session.invoke(AgentInvocation(
                agent="xpatcher:executor",
                prompt=context,
            )),
        )

    def _run_review(self, plan: PlanOutput, exec_results: list[AgentResult]) -> AgentResult:
        """Invoke the reviewer agent."""
        context = self.context.build_review_context(plan, exec_results)

        return self.session.invoke(AgentInvocation(
            agent="xpatcher:reviewer",
            prompt=context,
        ))

    def _run_fixes(self, plan: PlanOutput, review: ReviewOutput) -> list[AgentResult]:
        """Re-execute tasks that the reviewer flagged."""
        results = []
        for finding in review.mustFix:
            context = self.context.build_fix_context(plan, finding, review)
            result = self.session.invoke(AgentInvocation(
                agent="xpatcher:executor",
                prompt=context,
            ))
            results.append(result)
        return results

    def _run_parallel_analysis(self, plan: PlanOutput, exec_results: list[AgentResult]) -> dict:
        """Run test and gap-detection in parallel."""
        def run_tests():
            context = self.context.build_test_context(plan, exec_results)
            return self.session.invoke(AgentInvocation(
                agent="xpatcher:tester",
                prompt=context,
            ))

        def run_gap_detection():
            context = self.context.build_gap_context(plan, exec_results)
            return self.session.invoke(AgentInvocation(
                agent="xpatcher:gap-detector",
                prompt=context,
            ))

        test_result, gap_result = self.pool.run_parallel([run_tests, run_gap_detection])

        return {
            "tests": test_result,
            "gaps": gap_result,
        }

    def _run_simplification(self, plan: PlanOutput, exec_results: list[AgentResult]) -> AgentResult:
        """Invoke the simplifier agent."""
        context = self.context.build_simplify_context(plan, exec_results)
        return self.session.invoke(AgentInvocation(
            agent="xpatcher:simplifier",
            prompt=context,
        ))

    def _abort(self, reason: str, results: dict) -> dict:
        self.state.transition(Stage.FAILED)
        results["status"] = "failed"
        results["failureReason"] = reason
        logger.error(f"Pipeline aborted: {reason}")
        return results
```

### 3.4 Structured Output Schemas

```python
# src/dispatcher/schemas.py

from __future__ import annotations
from pydantic import BaseModel, Field
from enum import Enum
from typing import Optional


# --- Plan Output ---

class TaskComplexity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

class PlanTask(BaseModel):
    id: str
    description: str
    files: list[str] = []
    acceptance: str = ""
    dependsOn: list[str] = Field(default_factory=list)
    estimatedComplexity: TaskComplexity = TaskComplexity.MEDIUM
    notes: str = ""

class PlanPhase(BaseModel):
    id: str
    name: str
    description: str
    tasks: list[PlanTask]

class Risk(BaseModel):
    description: str
    mitigation: str
    severity: TaskComplexity  # Reuses low/medium/high

class PlanOutput(BaseModel):
    summary: str
    phases: list[PlanPhase]
    risks: list[Risk] = Field(default_factory=list)
    openQuestions: list[str] = Field(default_factory=list)


# --- Execution Output ---

class ExecutionStatus(str, Enum):
    COMPLETED = "completed"
    BLOCKED = "blocked"
    DEVIATED = "deviated"

class ExecutionOutput(BaseModel):
    taskId: str
    status: ExecutionStatus
    filesModified: list[str] = Field(default_factory=list)
    filesCreated: list[str] = Field(default_factory=list)
    summary: str
    deviations: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    testResults: str = ""


# --- Review Output ---

class ReviewVerdict(str, Enum):
    APPROVE = "approve"
    REQUEST_CHANGES = "request-changes"
    REJECT = "reject"

class FindingSeverity(str, Enum):
    CRITICAL = "critical"
    MAJOR = "major"
    MINOR = "minor"
    NIT = "nit"

class FindingCategory(str, Enum):
    CORRECTNESS = "correctness"
    COMPLETENESS = "completeness"
    STYLE = "style"
    SECURITY = "security"
    TESTABILITY = "testability"
    SIMPLICITY = "simplicity"

class ReviewFinding(BaseModel):
    severity: FindingSeverity
    category: FindingCategory
    file: str
    line: Optional[int] = None
    description: str
    suggestion: str = ""

class ReviewOutput(BaseModel):
    verdict: ReviewVerdict
    score: int = Field(ge=0, le=100)
    findings: list[ReviewFinding] = Field(default_factory=list)
    summary: str
    mustFix: list[str] = Field(default_factory=list)
    niceToHave: list[str] = Field(default_factory=list)


# --- Test Output ---

class TestResults(BaseModel):
    passed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0

class TestFailure(BaseModel):
    test: str
    file: str
    error: str
    analysis: str = ""

class TestOutput(BaseModel):
    testsCreated: list[str] = Field(default_factory=list)
    testsModified: list[str] = Field(default_factory=list)
    testResults: TestResults = Field(default_factory=TestResults)
    failures: list[TestFailure] = Field(default_factory=list)
    coverageNotes: str = ""
    summary: str


# --- Simplify Output ---

class SimplificationType(str, Enum):
    DEDUP = "dedup"
    FLATTEN = "flatten"
    EXTRACT = "extract"
    REMOVE_DEAD = "remove-dead"
    REUSE_EXISTING = "reuse-existing"
    CONSTANT = "constant"

class Simplification(BaseModel):
    file: str
    line: Optional[int] = None
    type: SimplificationType
    description: str
    applied: bool = False

class SimplifyOutput(BaseModel):
    mode: str  # "dryRun" or "apply"
    simplifications: list[Simplification] = Field(default_factory=list)
    linesRemoved: int = 0
    linesAdded: int = 0
    netReduction: int = 0
    summary: str


# --- Gap Detection Output ---

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

class Gap(BaseModel):
    severity: GapSeverity
    category: GapCategory
    description: str
    location: str = "general"
    recommendation: str = ""

class PlanCompleteness(BaseModel):
    totalTasks: int
    completed: int
    partial: int = 0
    skipped: int = 0
    details: list[str] = Field(default_factory=list)

class GapOutput(BaseModel):
    gaps: list[Gap] = Field(default_factory=list)
    planCompleteness: PlanCompleteness
    overallRisk: str = "low"
    summary: str
```

### 3.5 Parallel Agent Execution

```python
# src/dispatcher/parallel.py

from __future__ import annotations
import concurrent.futures
import logging
from typing import Callable, TypeVar

from .session import AgentResult

logger = logging.getLogger(__name__)

T = TypeVar("T")


class AgentPool:
    """
    Manages parallel agent execution via a thread pool.

    Each agent invocation is a subprocess (claude CLI), so threads
    are appropriate -- we are I/O-bound, not CPU-bound.
    """

    def __init__(self, max_workers: int = 3):
        self.max_workers = max_workers

    def run_parallel(self, tasks: list[Callable[[], T]]) -> list[T]:
        """
        Run multiple agent invocations in parallel.

        Returns results in the same order as the input tasks.
        """
        results: list[T | None] = [None] * len(tasks)

        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            future_to_index = {
                pool.submit(task): i
                for i, task in enumerate(tasks)
            }

            for future in concurrent.futures.as_completed(future_to_index):
                index = future_to_index[future]
                try:
                    results[index] = future.result()
                except Exception as exc:
                    logger.error(f"Agent task {index} failed: {exc}")
                    results[index] = AgentResult(
                        success=False,
                        output={"error": str(exc)},
                        raw_output="",
                        exit_code=-1,
                        stderr=str(exc),
                    )

        return results  # type: ignore[return-value]
```

### 3.6 Retry Logic

```python
# src/dispatcher/retry.py

from __future__ import annotations
import logging
import time
from dataclasses import dataclass
from typing import Callable, TypeVar

from .session import AgentResult

logger = logging.getLogger(__name__)

T = TypeVar("T")


@dataclass
class RetryPolicy:
    max_retries: int = 2
    backoff_base: float = 2.0      # seconds
    backoff_max: float = 30.0      # seconds
    retryable_exit_codes: set[int] = None

    def __post_init__(self):
        if self.retryable_exit_codes is None:
            # Exit code 1 = general error (may be transient)
            # Exit code -1 = our timeout sentinel
            self.retryable_exit_codes = {1, -1}


def with_retry(policy: RetryPolicy, fn: Callable[[], AgentResult]) -> AgentResult:
    """
    Retry an agent invocation according to the given policy.

    Only retries on retryable exit codes. Parse errors or non-retryable
    failures are returned immediately.
    """
    last_result = None

    for attempt in range(1 + policy.max_retries):
        if attempt > 0:
            delay = min(
                policy.backoff_base ** attempt,
                policy.backoff_max,
            )
            logger.info(f"Retry attempt {attempt} after {delay:.1f}s backoff")
            time.sleep(delay)

        result = fn()
        last_result = result

        if result.success:
            return result

        if result.exit_code not in policy.retryable_exit_codes:
            logger.warning(
                f"Non-retryable exit code {result.exit_code}, "
                f"not retrying (stderr: {result.stderr[:200]})"
            )
            return result

        logger.warning(
            f"Attempt {attempt + 1}/{1 + policy.max_retries} failed "
            f"(exit={result.exit_code}): {result.stderr[:200]}"
        )

    logger.error(f"All {1 + policy.max_retries} attempts exhausted")
    return last_result  # type: ignore[return-value]
```

### 3.7 Context Building

```python
# src/context/builder.py

from __future__ import annotations
import subprocess
import json
from pathlib import Path
from typing import Optional


class ContextBuilder:
    """
    Assembles prompts with appropriate context for each agent stage.

    Context is injected into the prompt as structured text, not as
    system prompts (which we reserve for behavioral instructions
    already in the agent markdown).
    """

    def __init__(self, project_dir: str):
        self.project_dir = Path(project_dir)

    def build_planning_context(self, task_description: str) -> str:
        """Build the prompt for the planner agent."""
        repo_summary = self._get_repo_summary()
        recent_history = self._get_recent_git_history()

        return f"""## Task
{task_description}

## Repository Context
{repo_summary}

## Recent Git History
{recent_history}

Analyze the codebase and produce an implementation plan as specified in your instructions."""

    def build_execution_context(self, plan, task) -> str:
        """Build the prompt for the executor agent on a specific task."""
        plan_json = plan.model_dump_json(indent=2) if hasattr(plan, 'model_dump_json') else json.dumps(plan, indent=2)

        return f"""## Current Task
Task ID: {task.id}
Description: {task.description}
Files: {', '.join(task.files) if task.files else 'To be determined'}
Acceptance Criteria: {task.acceptance}
Notes: {task.notes}

## Full Plan (for context only -- focus on the current task)
{plan_json}

Implement this task according to the plan. Follow your instructions for output format."""

    def build_review_context(self, plan, exec_results: list) -> str:
        """Build the prompt for the reviewer agent."""
        plan_json = plan.model_dump_json(indent=2) if hasattr(plan, 'model_dump_json') else json.dumps(plan, indent=2)
        git_diff = self._get_staged_diff()
        exec_summaries = self._summarize_exec_results(exec_results)

        return f"""## Plan
{plan_json}

## Execution Results
{exec_summaries}

## Git Diff of All Changes
```diff
{git_diff}
```

Review these changes according to your checklist. Produce structured review output."""

    def build_fix_context(self, plan, finding: str, review) -> str:
        """Build the prompt for fixing a specific review finding."""
        review_json = review.model_dump_json(indent=2) if hasattr(review, 'model_dump_json') else json.dumps(review, indent=2)

        return f"""## Fix Required
{finding}

## Full Review Context
{review_json}

## Plan (for reference)
{plan.model_dump_json(indent=2) if hasattr(plan, 'model_dump_json') else json.dumps(plan, indent=2)}

Address this specific review finding. Do not change anything else."""

    def build_test_context(self, plan, exec_results: list) -> str:
        """Build the prompt for the tester agent."""
        plan_json = plan.model_dump_json(indent=2) if hasattr(plan, 'model_dump_json') else json.dumps(plan, indent=2)
        exec_summaries = self._summarize_exec_results(exec_results)

        return f"""## Plan (with acceptance criteria)
{plan_json}

## Execution Results
{exec_summaries}

Write and run tests according to your instructions."""

    def build_gap_context(self, plan, exec_results: list) -> str:
        """Build the prompt for the gap detector agent."""
        plan_json = plan.model_dump_json(indent=2) if hasattr(plan, 'model_dump_json') else json.dumps(plan, indent=2)
        git_diff = self._get_staged_diff()
        exec_summaries = self._summarize_exec_results(exec_results)

        return f"""## Plan
{plan_json}

## Execution Results
{exec_summaries}

## Git Diff
```diff
{git_diff}
```

Analyze for gaps according to your instructions."""

    def build_simplify_context(self, plan, exec_results: list) -> str:
        """Build the prompt for the simplifier agent."""
        modified_files = []
        for r in exec_results:
            if hasattr(r, 'output') and isinstance(r.output, dict):
                modified_files.extend(r.output.get("filesModified", []))
                modified_files.extend(r.output.get("filesCreated", []))

        return f"""## Recently Modified Files
{json.dumps(list(set(modified_files)), indent=2)}

## Plan Summary (for context on intent)
{plan.summary if hasattr(plan, 'summary') else ''}

## Mode
apply

Analyze and simplify according to your instructions."""

    # --- Helpers ---

    def _get_repo_summary(self) -> str:
        """Quick repo structure summary."""
        try:
            result = subprocess.run(
                ["find", ".", "-type", "f", "-not", "-path", "./.git/*",
                 "-not", "-path", "./node_modules/*", "-not", "-path", "./.venv/*",
                 "-not", "-path", "./__pycache__/*"],
                capture_output=True, text=True, cwd=self.project_dir,
                timeout=10,
            )
            files = result.stdout.strip().split("\n")
            if len(files) > 200:
                return f"Repository has {len(files)} files. Top-level structure:\n" + \
                    subprocess.run(
                        ["ls", "-la"], capture_output=True, text=True,
                        cwd=self.project_dir, timeout=5
                    ).stdout
            return "Files:\n" + "\n".join(files[:200])
        except Exception:
            return "(Could not read repository structure)"

    def _get_recent_git_history(self, count: int = 20) -> str:
        try:
            result = subprocess.run(
                ["git", "log", "--oneline", f"-{count}"],
                capture_output=True, text=True, cwd=self.project_dir,
                timeout=10,
            )
            return result.stdout.strip() or "(No git history)"
        except Exception:
            return "(Not a git repository)"

    def _get_staged_diff(self) -> str:
        try:
            result = subprocess.run(
                ["git", "diff", "HEAD"],
                capture_output=True, text=True, cwd=self.project_dir,
                timeout=30,
            )
            diff = result.stdout.strip()
            if len(diff) > 50_000:
                return diff[:50_000] + "\n... (diff truncated at 50k chars)"
            return diff or "(No changes)"
        except Exception:
            return "(Could not get diff)"

    def _summarize_exec_results(self, exec_results: list) -> str:
        summaries = []
        for i, r in enumerate(exec_results):
            if hasattr(r, 'output') and isinstance(r.output, dict):
                summaries.append(json.dumps(r.output, indent=2))
            else:
                summaries.append(f"Result {i}: {r}")
        return "\n---\n".join(summaries)
```

### 3.8 Pipeline State Machine

```python
# src/dispatcher/state.py

from __future__ import annotations
import json
import logging
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class Stage(str, Enum):
    IDLE = "idle"
    PLANNING = "planning"
    EXECUTING = "executing"
    REVIEWING = "reviewing"
    TESTING = "testing"
    GAP_DETECTING = "gap-detecting"
    SIMPLIFYING = "simplifying"
    BLOCKED = "blocked"
    FAILED = "failed"
    COMPLETE = "complete"


# Valid transitions
TRANSITIONS: dict[Stage, set[Stage]] = {
    Stage.IDLE:           {Stage.PLANNING},
    Stage.PLANNING:       {Stage.EXECUTING, Stage.BLOCKED, Stage.FAILED},
    Stage.EXECUTING:      {Stage.REVIEWING, Stage.BLOCKED, Stage.FAILED},
    Stage.REVIEWING:      {Stage.EXECUTING, Stage.TESTING, Stage.GAP_DETECTING, Stage.FAILED},
    Stage.TESTING:        {Stage.GAP_DETECTING, Stage.SIMPLIFYING, Stage.COMPLETE, Stage.FAILED},
    Stage.GAP_DETECTING:  {Stage.EXECUTING, Stage.SIMPLIFYING, Stage.COMPLETE, Stage.FAILED},
    Stage.SIMPLIFYING:    {Stage.REVIEWING, Stage.COMPLETE, Stage.FAILED},
    Stage.BLOCKED:        {Stage.PLANNING, Stage.EXECUTING, Stage.FAILED},
    Stage.FAILED:         {Stage.IDLE},
    Stage.COMPLETE:       {Stage.IDLE},
}


class PipelineState:
    """
    Tracks the current stage of the pipeline with persistence.

    State is persisted to .xpatcher/pipeline-state.json so the
    dispatcher can resume after interruption.
    """

    def __init__(self, state_dir: Optional[str] = None):
        self.state_dir = Path(state_dir) if state_dir else None
        self.current = Stage.IDLE
        self.history: list[dict] = []
        self._load()

    def transition(self, target: Stage) -> None:
        """Transition to a new stage, validating the transition is legal."""
        allowed = TRANSITIONS.get(self.current, set())
        if target not in allowed:
            raise ValueError(
                f"Invalid transition: {self.current} -> {target}. "
                f"Allowed: {allowed}"
            )

        entry = {
            "from": self.current.value,
            "to": target.value,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self.history.append(entry)
        logger.info(f"Pipeline: {self.current} -> {target}")
        self.current = target
        self._save()

    def _save(self) -> None:
        if not self.state_dir:
            return
        self.state_dir.mkdir(parents=True, exist_ok=True)
        path = self.state_dir / "pipeline-state.json"
        path.write_text(json.dumps({
            "current": self.current.value,
            "history": self.history,
        }, indent=2))

    def _load(self) -> None:
        if not self.state_dir:
            return
        path = self.state_dir / "pipeline-state.json"
        if path.exists():
            data = json.loads(path.read_text())
            self.current = Stage(data.get("current", "idle"))
            self.history = data.get("history", [])
```

---

## 4. Skill Definitions

Skills are the user-facing entry points. They translate interactive commands into dispatcher invocations or direct agent calls.

### 4.1 /xpatcher:plan

```markdown
---
name: plan
description: >
  Analyze requirements and produce a structured implementation plan.
  Usage: /xpatcher:plan <description of what to build or change>
allowed-tools:
  - Read
  - Glob
  - Grep
  - Bash
  - Agent
agent: planner
---

# xpatcher Plan

Create an implementation plan for the following task:

$ARGUMENTS

## Context

!`git log --oneline -10 2>/dev/null || echo "No git history"`

!`ls -la 2>/dev/null`

Analyze the codebase thoroughly, then produce a structured JSON plan
as specified in your agent instructions.
```

### 4.2 /xpatcher:execute

```markdown
---
name: execute
description: >
  Execute an implementation plan or a specific task from a plan.
  Usage: /xpatcher:execute [task description or plan reference]
allowed-tools:
  - Read
  - Edit
  - Write
  - Glob
  - Grep
  - Bash
  - LSP
  - NotebookEdit
  - Agent
agent: executor
---

# xpatcher Execute

Implement the following:

$ARGUMENTS

## Current State

!`cat .xpatcher/current-plan.json 2>/dev/null || echo "No active plan. Working from description only."`

!`git diff --stat HEAD 2>/dev/null || echo "No git changes"`

Follow the plan if one exists. Otherwise, implement directly from the description.
Produce structured output as specified in your agent instructions.
```

### 4.3 /xpatcher:review

```markdown
---
name: review
description: >
  Review recent code changes for quality, correctness, and adherence to plan.
  Usage: /xpatcher:review [optional focus area]
allowed-tools:
  - Read
  - Glob
  - Grep
  - Bash
  - LSP
  - Agent
agent: reviewer
---

# xpatcher Review

Review the recent code changes.

$ARGUMENTS

## Changes to Review

!`git diff HEAD 2>/dev/null || echo "No uncommitted changes"`

!`git log --oneline -5 2>/dev/null`

## Plan Context

!`cat .xpatcher/current-plan.json 2>/dev/null || echo "No active plan"`

Produce a structured review as specified in your agent instructions.
```

### 4.4 /xpatcher:test

```markdown
---
name: test
description: >
  Generate and run tests for recent code changes.
  Usage: /xpatcher:test [optional focus area or file pattern]
allowed-tools:
  - Read
  - Edit
  - Write
  - Glob
  - Grep
  - Bash
  - LSP
  - Agent
agent: tester
---

# xpatcher Test

Generate and run tests for recent changes.

$ARGUMENTS

## What Changed

!`git diff --name-only HEAD 2>/dev/null || echo "No changes detected"`

## Existing Test Structure

!`find . -name "test_*" -o -name "*_test.*" -o -name "*.spec.*" -o -name "*.test.*" | head -30 2>/dev/null || echo "No test files found"`

## Plan Context

!`cat .xpatcher/current-plan.json 2>/dev/null || echo "No active plan"`

Write and run tests as specified in your agent instructions.
```

### 4.5 /xpatcher:simplify

```markdown
---
name: simplify
description: >
  Analyze recent changes for unnecessary complexity and apply simplifications.
  Usage: /xpatcher:simplify [--dry-run] [optional file pattern]
allowed-tools:
  - Read
  - Edit
  - Glob
  - Grep
  - Bash
  - LSP
  - Agent
agent: simplifier
---

# xpatcher Simplify

Analyze and simplify recent code changes.

$ARGUMENTS

## Recently Modified Files

!`git diff --name-only HEAD 2>/dev/null || echo "No changes detected"`

## Mode
If "--dry-run" appears in the arguments above, use dryRun mode. Otherwise use apply mode.

Analyze and simplify as specified in your agent instructions.
```

### 4.6 /xpatcher:detect-gaps

```markdown
---
name: detect-gaps
description: >
  Detect gaps between plan and implementation: missing error handling,
  untested paths, incomplete work.
  Usage: /xpatcher:detect-gaps
allowed-tools:
  - Read
  - Glob
  - Grep
  - Bash
  - LSP
  - Agent
agent: gap-detector
---

# xpatcher Gap Detection

Analyze the implementation for gaps.

$ARGUMENTS

## Plan

!`cat .xpatcher/current-plan.json 2>/dev/null || echo "No active plan"`

## Changes

!`git diff HEAD 2>/dev/null || echo "No uncommitted changes"`

!`git diff --stat HEAD 2>/dev/null`

Analyze for gaps as specified in your agent instructions.
```

### 4.7 /xpatcher:status

```markdown
---
name: status
description: >
  Show the current pipeline status: active stage, completed stages,
  recent results.
  Usage: /xpatcher:status
allowed-tools:
  - Read
  - Bash
model: claude-haiku-4-20250514
---

# xpatcher Pipeline Status

Show the current state of the xpatcher pipeline.

## Pipeline State

!`cat .xpatcher/pipeline-state.json 2>/dev/null || echo "No active pipeline"`

## Recent Results

!`ls -lt .xpatcher/results/ 2>/dev/null | head -10 || echo "No results yet"`

## Current Plan

!`cat .xpatcher/current-plan.json 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('summary','No summary'))" 2>/dev/null || echo "No active plan"`

Summarize the pipeline status concisely. Show which stages have completed,
what is pending, and any blockers or failures.
```

### 4.8 /xpatcher:pipeline

```markdown
---
name: pipeline
description: >
  Run the full SDLC pipeline: plan -> execute -> review -> test -> simplify.
  Usage: /xpatcher:pipeline <task description>
allowed-tools:
  - Bash
  - Read
model: claude-sonnet-4-20250514
---

# xpatcher Full Pipeline

Run the complete SDLC automation pipeline.

## Task
$ARGUMENTS

## Execution

This skill invokes the Python dispatcher to orchestrate the full pipeline.
The dispatcher manages agent sequencing, structured output parsing, and
retry logic.

Run the dispatcher:

```bash
python -m src.dispatcher.core --task "$ARGUMENTS" --project-dir "$(pwd)" --plugin-dir "$(pwd)/.claude-plugin"
```

Monitor the pipeline state:

```bash
cat .xpatcher/pipeline-state.json
```

Report the final results when the pipeline completes.
```

### 4.9 Skill Design Notes

**User-invocable vs model-invocable**: All skills above are invocable by both users (via `/xpatcher:plan` etc.) and by the model (the model can call them when it determines they are appropriate). The `pipeline` skill is the primary user entry point; the individual stage skills are useful for re-running specific stages or for debugging.

**Dynamic context injection**: The `!`command`` syntax in skills runs shell commands at skill-load time and injects their output. This gives each agent fresh context about the repo state, git history, and pipeline progress without the dispatcher needing to pre-assemble everything. This is particularly valuable for interactive (non-dispatcher) use where the user invokes skills directly.

**Agent delegation**: Skills use the `agent` frontmatter field (where applicable) to run in the context of a specific subagent, inheriting that agent's model, tools, and behavior. The `context:fork` mechanism could also be used but `agent` is cleaner for our case since each skill maps 1:1 to an agent.

---

## 5. Hook Design

### 5.1 Security Constraint

**Plugin subagents cannot define hooks in their own frontmatter.** Hooks must be declared at the project level (in `.claude/settings.json` or the user's settings) or provided by the plugin's `hooks/` directory and registered via `settings.json`.

The plugin's `settings.json` can configure hooks that apply when the plugin is loaded:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "*",
        "command": "python .claude-plugin/hooks/pre_tool_use.py \"$TOOL_NAME\" \"$TOOL_INPUT\""
      }
    ],
    "PostToolUse": [
      {
        "matcher": "*",
        "command": "python .claude-plugin/hooks/post_tool_use.py \"$TOOL_NAME\" \"$TOOL_INPUT\" \"$TOOL_OUTPUT\""
      }
    ],
    "SubagentStart": [
      {
        "matcher": "*",
        "command": "python .claude-plugin/hooks/lifecycle.py start \"$AGENT_NAME\""
      }
    ],
    "SubagentStop": [
      {
        "matcher": "*",
        "command": "python .claude-plugin/hooks/lifecycle.py stop \"$AGENT_NAME\" \"$EXIT_CODE\""
      }
    ]
  }
}
```

**Note**: The exact hook API for plugins is subject to Claude Code's evolving plugin security model. If plugin-level hook registration is not supported, these hooks must be installed at the project level by the user (e.g., via a setup script that writes to `.claude/settings.json`). The hook scripts themselves can live in the plugin directory.

### 5.2 PreToolUse Hook: Validation

```python
#!/usr/bin/env python3
"""
hooks/pre_tool_use.py

PreToolUse hook that validates tool calls against xpatcher policies.

Receives tool name and input via stdin (JSON) per Claude Code hook protocol.
Exits 0 to allow, exits 2 to block with a message on stdout.
"""

import json
import sys
import os
from pathlib import Path

# Read hook input from stdin
hook_input = json.loads(sys.stdin.read())
tool_name = hook_input.get("tool_name", "")
tool_input = hook_input.get("tool_input", {})
agent_name = os.environ.get("CLAUDE_AGENT_NAME", "")

# --- Policy: Read-only agents cannot write ---

READ_ONLY_AGENTS = {"planner", "reviewer", "gap-detector", "explorer"}

WRITE_TOOLS = {"Edit", "Write", "NotebookEdit"}

if agent_name in READ_ONLY_AGENTS and tool_name in WRITE_TOOLS:
    print(json.dumps({
        "decision": "block",
        "reason": f"Agent '{agent_name}' is read-only and cannot use {tool_name}",
    }))
    sys.exit(2)


# --- Policy: Tester can only write to test files ---

TEST_FILE_PATTERNS = [
    "test_", "_test.", ".test.", ".spec.", "tests/", "__tests__/",
    "conftest.py", "fixtures/",
]

if agent_name == "tester" and tool_name in WRITE_TOOLS:
    file_path = tool_input.get("file_path", "")
    is_test_file = any(pattern in file_path for pattern in TEST_FILE_PATTERNS)
    if not is_test_file:
        print(json.dumps({
            "decision": "block",
            "reason": f"Tester agent can only write to test files. "
                      f"'{file_path}' does not match test file patterns.",
        }))
        sys.exit(2)


# --- Policy: No writing outside project directory ---

if tool_name in WRITE_TOOLS:
    file_path = tool_input.get("file_path", "")
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd())
    try:
        resolved = str(Path(file_path).resolve())
        if not resolved.startswith(str(Path(project_dir).resolve())):
            print(json.dumps({
                "decision": "block",
                "reason": f"Cannot write outside project directory: {file_path}",
            }))
            sys.exit(2)
    except Exception:
        pass


# --- Policy: Prevent dangerous bash commands ---

DANGEROUS_PATTERNS = [
    "rm -rf /", "rm -rf ~", "chmod 777", ":(){ :|:& };:",
    "dd if=", "mkfs.", "> /dev/sd",
]

if tool_name == "Bash":
    command = tool_input.get("command", "")
    for pattern in DANGEROUS_PATTERNS:
        if pattern in command:
            print(json.dumps({
                "decision": "block",
                "reason": f"Blocked dangerous command pattern: {pattern}",
            }))
            sys.exit(2)


# --- Policy: Executor cannot use web tools ---

if agent_name == "executor" and tool_name in {"WebSearch", "WebFetch"}:
    print(json.dumps({
        "decision": "block",
        "reason": "Executor agent cannot access the web. Work with local code only.",
    }))
    sys.exit(2)


# Allow by default
print(json.dumps({"decision": "allow"}))
sys.exit(0)
```

### 5.3 PostToolUse Hook: Logging and Artifacts

```python
#!/usr/bin/env python3
"""
hooks/post_tool_use.py

PostToolUse hook that logs tool usage and captures artifacts.

Used for:
- Audit trail of all tool calls per pipeline run
- Capturing file contents after writes for diff reconstruction
- Timing data for performance analysis
"""

import json
import sys
import os
from datetime import datetime, timezone
from pathlib import Path


hook_input = json.loads(sys.stdin.read())
tool_name = hook_input.get("tool_name", "")
tool_input = hook_input.get("tool_input", {})
tool_output = hook_input.get("tool_output", "")
agent_name = os.environ.get("CLAUDE_AGENT_NAME", "")

# Determine state directory
state_dir = Path(os.environ.get("XPATCHER_STATE_DIR", ".xpatcher"))
log_dir = state_dir / "tool-log"
log_dir.mkdir(parents=True, exist_ok=True)

# Write log entry
entry = {
    "timestamp": datetime.now(timezone.utc).isoformat(),
    "agent": agent_name,
    "tool": tool_name,
    "input_summary": _summarize_input(tool_name, tool_input),
    "output_length": len(str(tool_output)),
    "success": True,  # Hook only fires on success
}

# Append to JSONL log
log_file = log_dir / f"{datetime.now(timezone.utc).strftime('%Y%m%d')}.jsonl"
with open(log_file, "a") as f:
    f.write(json.dumps(entry) + "\n")


# Track files modified by write tools
if tool_name in {"Edit", "Write", "NotebookEdit"}:
    file_path = tool_input.get("file_path", "")
    if file_path:
        modified_file = state_dir / "modified-files.txt"
        with open(modified_file, "a") as f:
            f.write(f"{datetime.now(timezone.utc).isoformat()}\t{agent_name}\t{file_path}\n")


def _summarize_input(tool_name: str, tool_input: dict) -> str:
    """Create a short summary of tool input for logging."""
    if tool_name == "Bash":
        cmd = tool_input.get("command", "")
        return cmd[:200] if len(cmd) > 200 else cmd
    elif tool_name in {"Edit", "Write"}:
        return tool_input.get("file_path", "unknown")
    elif tool_name in {"Read", "Glob", "Grep"}:
        return str(tool_input)[:200]
    return str(tool_input)[:100]


# Always allow (post-use hooks are informational)
print(json.dumps({"decision": "allow"}))
sys.exit(0)
```

### 5.4 Lifecycle Hook: Agent Tracking

```python
#!/usr/bin/env python3
"""
hooks/lifecycle.py

SubagentStart/SubagentStop hook for tracking agent lifecycle.

Usage:
  lifecycle.py start <agent_name>
  lifecycle.py stop <agent_name> <exit_code>
"""

import json
import sys
import os
from datetime import datetime, timezone
from pathlib import Path

hook_input = json.loads(sys.stdin.read())
event = sys.argv[1] if len(sys.argv) > 1 else "unknown"
agent_name = hook_input.get("agent_name", "unknown")

state_dir = Path(os.environ.get("XPATCHER_STATE_DIR", ".xpatcher"))
lifecycle_dir = state_dir / "lifecycle"
lifecycle_dir.mkdir(parents=True, exist_ok=True)

entry = {
    "timestamp": datetime.now(timezone.utc).isoformat(),
    "event": event,
    "agent": agent_name,
}

if event == "stop":
    entry["exit_code"] = hook_input.get("exit_code", -1)

# Write lifecycle event
log_file = lifecycle_dir / "events.jsonl"
with open(log_file, "a") as f:
    f.write(json.dumps(entry) + "\n")

# Update active agents tracking
active_file = lifecycle_dir / "active.json"
try:
    active = json.loads(active_file.read_text()) if active_file.exists() else {}
except Exception:
    active = {}

if event == "start":
    active[agent_name] = {
        "started": entry["timestamp"],
        "pid": os.getpid(),
    }
elif event == "stop":
    active.pop(agent_name, None)

active_file.write_text(json.dumps(active, indent=2))

print(json.dumps({"decision": "allow"}))
sys.exit(0)
```

### 5.5 How Hooks Enforce the SDLC Process

The hook system provides defense-in-depth for the pipeline:

| Hook | Enforcement |
|------|-------------|
| PreToolUse (write check) | Prevents read-only agents (planner, reviewer, gap-detector) from accidentally modifying files, even if the model hallucinates a write tool call. |
| PreToolUse (tester scope) | Ensures the tester only writes test files, not production code. This is critical -- if the tester "fixes" failing tests by changing production code, the whole review cycle is compromised. |
| PreToolUse (project boundary) | Prevents any agent from writing outside the project directory. Defense against path traversal or confused agent context. |
| PreToolUse (dangerous commands) | Blocks obviously destructive bash commands. Not a complete sandbox (that requires OS-level isolation) but catches common accidents. |
| PostToolUse (logging) | Creates a full audit trail of every tool call. Essential for debugging pipeline failures and understanding what each agent actually did vs. what it reported. |
| PostToolUse (modified files) | Tracks which files were actually modified (not just what the agent claims). Enables the reviewer and gap-detector to verify executor reports against reality. |
| Lifecycle (agent tracking) | Enables the dispatcher to monitor which agents are active, detect hangs, and clean up after crashes. |

---

## 6. Workarounds for Limitations

### 6.1 Plugin Subagents Cannot Define hooks, mcpServers, or permissionMode

**The Problem**: Agent markdown files inside a plugin's `agents/` directory are security-restricted. They cannot declare `hooks`, `mcpServers`, or `permissionMode` in their YAML frontmatter. This means:
- Agents cannot self-enforce tool policies via their own hooks.
- Agents cannot declare MCP servers they need.
- Agents cannot set their own permission mode (e.g., auto-approve).

**Workaround Strategy**:

1. **Hooks**: Define hooks at the plugin level in `settings.json` (if supported) or at the project level in `.claude/settings.json`. The hooks themselves live in the plugin's `hooks/` directory but are registered externally. The hook scripts check `CLAUDE_AGENT_NAME` to apply agent-specific policies. This is actually better architecture anyway -- centralized policy enforcement rather than self-declared permissions.

2. **MCP Servers**: Declare MCP servers in the plugin's `.mcp.json` file (plugin-level, not agent-level). These servers become available to the Claude Code session, and agents can use their tools. For agent-specific MCP needs, the tool allowlist in the agent frontmatter controls which MCP tools each agent can access.

3. **Permission Mode**: This is the hardest limitation. Without `permissionMode: auto` on agents, every tool call may require user approval in interactive mode. Workarounds:
   - **Headless mode** (`claude -p`): When invoked by the dispatcher, there is no interactive approval -- the CLI runs to completion. This is the primary execution path.
   - **Interactive mode**: Users can set `permissionMode` in their project-level `.claude/settings.json` or accept the approval prompts. A setup script can configure this.
   - **Tool allowlists**: Explicitly listing allowed tools in the agent frontmatter reduces the surface area of approval prompts.

**Setup Script** (to install project-level configuration):

```python
#!/usr/bin/env python3
"""
Install xpatcher configuration at the project level.
Run once per project: python -m xpatcher.setup
"""

import json
from pathlib import Path

CLAUDE_DIR = Path(".claude")
CLAUDE_DIR.mkdir(exist_ok=True)

settings_path = CLAUDE_DIR / "settings.json"

# Load existing settings or start fresh
if settings_path.exists():
    settings = json.loads(settings_path.read_text())
else:
    settings = {}

# Merge xpatcher hooks
hooks = settings.setdefault("hooks", {})

hooks.setdefault("PreToolUse", []).append({
    "matcher": "*",
    "command": "python .claude-plugin/hooks/pre_tool_use.py"
})

hooks.setdefault("PostToolUse", []).append({
    "matcher": "*",
    "command": "python .claude-plugin/hooks/post_tool_use.py"
})

settings_path.write_text(json.dumps(settings, indent=2))
print(f"Wrote {settings_path}")
```

### 6.2 Subagents Cannot Spawn Other Subagents

**The Problem**: Claude Code has a flat agent model. A subagent cannot use the `Agent` tool to spawn another subagent. This means:
- The planner cannot delegate a sub-analysis to the explorer.
- The executor cannot ask the tester to validate a specific change inline.
- No agent can orchestrate other agents.

**Workaround Strategy**: This limitation is actually well-aligned with our architecture. The Python dispatcher is the orchestrator, not any Claude Code agent.

```
    Dispatcher (Python)
        │
        ├── invoke(planner) ──> returns plan JSON
        │
        ├── invoke(executor, task-1) ──> returns execution report
        ├── invoke(executor, task-2) ──> returns execution report (parallel)
        │
        ├── invoke(reviewer) ──> returns review JSON
        │   │
        │   └── if request-changes:
        │       ├── invoke(executor, fix-1) ──> returns fix report
        │       └── invoke(reviewer) ──> re-review
        │
        ├── invoke(tester) ──> returns test report     ─┐
        ├── invoke(gap-detector) ──> returns gap report ─┘ (parallel)
        │
        └── invoke(simplifier) ──> returns simplify report (optional)
```

Each arrow is a separate `claude -p` invocation. The dispatcher:
- Sequences stages based on the state machine.
- Passes output from one stage as input context to the next.
- Handles the review-fix loop by re-invoking executor and reviewer.
- Runs independent stages in parallel via the thread pool.

**What if an agent needs information only another agent could provide?** The dispatcher pre-computes it. For example, if the reviewer needs to know what the planner intended, the dispatcher includes the plan JSON in the reviewer's prompt. The agents never need to talk to each other directly.

### 6.3 Agent Teams: Use or Avoid?

**The Problem**: Claude Code's Agent Teams feature (experimental) provides built-in multi-agent coordination with shared task lists, inter-agent messaging, and file-level locking. Should we use it instead of the Python dispatcher?

**Recommendation: Do not rely on Agent Teams for the core pipeline. Use the Python dispatcher.**

Reasons:

1. **Experimental status**: Agent Teams is explicitly marked experimental. Building a production pipeline on it means accepting breaking changes and instability.

2. **Control granularity**: The dispatcher gives us fine-grained control over:
   - Exactly when each agent runs and with what context.
   - Structured output parsing and validation between stages.
   - Retry logic with exponential backoff.
   - Pipeline state persistence and resumability.
   - Parallel execution with a tunable thread pool.
   Agent Teams' task-based model is more autonomous but less controllable.

3. **Structured output**: The dispatcher can use `--json-schema` to enforce output schemas. Agent Teams' inter-agent communication is less structured.

4. **Debuggability**: With the dispatcher, every agent invocation is a discrete subprocess with captured stdin/stdout/stderr. Agent Teams' in-process coordination is harder to debug.

5. **Testing**: The dispatcher is standard Python -- unit-testable, mockable, CI-friendly. Agent Teams requires a running Claude Code environment.

**However**, Agent Teams could be valuable for a future "autonomous mode" where the pipeline runs with less human oversight. The task-based model with dependency tracking is a natural fit for SDLC stages. Consider Agent Teams as a v2 alternative once it stabilizes.

**Hybrid approach for future consideration**: Use the dispatcher for the outer pipeline loop but Agent Teams for parallelizable work within a single stage (e.g., executing multiple independent tasks from the plan).

### 6.4 Session State Across Interruptions

**The Problem**: The pipeline may be interrupted by:
- User cancellation (Ctrl+C)
- Network timeout
- System crash
- Claude API rate limits or outages

**Workaround Strategy**: Multi-layer state persistence.

1. **Pipeline state machine** (Section 3.8): Persisted to `.xpatcher/pipeline-state.json` on every transition. After restart, the dispatcher reads this file and resumes from the last completed stage.

2. **Plan artifact**: The plan JSON is written to `.xpatcher/current-plan.json` immediately after planning completes. Even if execution fails, the plan is preserved.

3. **Per-stage results**: Each stage's output is written to `.xpatcher/results/<stage>-<timestamp>.json`. The dispatcher can detect partial completion and resume.

4. **Git as checkpoint**: After each successful execution task, the dispatcher can create a WIP commit. This provides an undo mechanism and prevents loss of work.

5. **Session continuation**: Claude Code supports `--continue` and `--resume` for session persistence. The dispatcher stores session IDs and can resume an interrupted agent invocation rather than starting fresh.

```python
# Resume logic in the dispatcher

def resume_pipeline(self) -> dict:
    """Resume a pipeline from its last persisted state."""
    state = self.state  # Loaded from .xpatcher/pipeline-state.json

    if state.current == Stage.IDLE or state.current == Stage.COMPLETE:
        raise ValueError("No pipeline to resume")

    if state.current == Stage.FAILED:
        # Re-enter from the stage that failed
        last_good = self._find_last_good_stage()
        state.current = last_good

    # Load existing results
    results = self._load_persisted_results()

    # Re-enter the pipeline at the current stage
    if state.current == Stage.PLANNING:
        return self.run_pipeline(self._load_task_description())
    elif state.current == Stage.EXECUTING:
        plan = self._load_plan()
        # Find which tasks are already completed
        completed = self._load_completed_tasks()
        return self._resume_execution(plan, completed, results)
    elif state.current == Stage.REVIEWING:
        return self._resume_from_review(results)
    # ... etc
```

6. **Idempotency**: Tasks should be idempotent where possible. Re-running an executor task that was partially completed should produce the same result as running it fresh. The executor agent's tool restrictions (no web access, scoped file writes) help ensure this.

---

## 7. Implementation Sequence

The recommended build order, with each step being independently testable:

### Phase 1: Foundation
1. Create the plugin directory structure (`.claude-plugin/` with `plugin.json`).
2. Write the explorer agent (simplest, proves the agent loading mechanism).
3. Write the `/xpatcher:status` skill (simplest skill, proves skill loading).
4. Test with `claude --plugin-dir .claude-plugin` interactively.

### Phase 2: Single-Agent Pipeline
5. Write the planner agent and `/xpatcher:plan` skill.
6. Write `src/dispatcher/session.py` (ClaudeSession) and test with planner.
7. Write `src/dispatcher/schemas.py` (PlanOutput only).
8. Verify end-to-end: dispatcher invokes planner, parses structured output.

### Phase 3: Execution Loop
9. Write the executor agent and `/xpatcher:execute` skill.
10. Write `src/context/builder.py` for prompt assembly.
11. Implement the plan-then-execute two-stage pipeline in the dispatcher.
12. Write `src/dispatcher/state.py` (pipeline state machine).

### Phase 4: Review Cycle
13. Write the reviewer agent and `/xpatcher:review` skill.
14. Implement the review-fix loop in the dispatcher.
15. Write PreToolUse hooks (read-only enforcement, project boundary).
16. Test the three-stage pipeline with review feedback.

### Phase 5: Full Pipeline
17. Write the tester, simplifier, and gap-detector agents.
18. Write `src/dispatcher/parallel.py` for parallel execution.
19. Write `src/dispatcher/retry.py` for error handling.
20. Implement the full pipeline including parallel test + gap detection.
21. Write PostToolUse hooks (logging, artifact capture).
22. Write lifecycle hooks (agent tracking).

### Phase 6: Resilience
23. Implement state persistence and `resume_pipeline()`.
24. Add git checkpoint logic (WIP commits after execution tasks).
25. Handle edge cases: rate limits, timeouts, malformed output.
26. Write integration tests that run the full pipeline on a sample project.

---

## 8. Open Questions and Future Directions

### Open Questions

1. **Agent selection in headless mode**: The `--agent` CLI flag for selecting a plugin agent may not exist yet or may have different syntax. Need to verify against the current Claude Code CLI. Fallback: use `--agents` with inline JSON agent definitions (more verbose but guaranteed to work).

2. **Hook input protocol**: The exact JSON schema for hook stdin/stdout varies across Claude Code versions. The hook scripts above assume a specific protocol (`tool_name`, `tool_input`, decision/block). This needs validation against the actual Claude Code hook API.

3. **Plugin settings.json scope**: Whether `settings.json` in a plugin can register hooks that apply session-wide is unclear. If not, hooks must be installed at the project level.

4. **Structured output reliability**: When using `--json-schema`, Claude Code validates the output against the schema. But what happens on validation failure? Does it retry? Return an error? The retry logic in the dispatcher needs to handle this.

5. **Concurrent session isolation**: When the dispatcher runs multiple agents in parallel, each is a separate `claude -p` subprocess. Do they share session state? Can they conflict on file writes? Git worktree isolation (using `isolation: worktree` in agent frontmatter) could help but adds merge complexity.

### Future Directions

- **MCP server for pipeline state**: Instead of file-based state, expose pipeline state through an MCP server. Agents could query it via MCP tools for richer context.
- **Learning from outcomes**: Use the memory system to store patterns from successful and failed pipelines. The planner could learn which decomposition strategies work for the specific codebase.
- **Agent Teams migration**: Once Agent Teams stabilizes, evaluate migrating the executor parallelism layer to Agent Teams while keeping the dispatcher for high-level orchestration.
- **Custom model routing**: Use different models not just per agent but per task complexity. A simple rename task gets haiku; a complex architectural change gets opus.
- **Human-in-the-loop checkpoints**: Add configurable gates where the pipeline pauses for human review before proceeding (e.g., plan approval before execution).

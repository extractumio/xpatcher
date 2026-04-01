# xpatcher Design Proposal: SDLC Automation Pipeline

**Version:** 1.3
**Date:** 2026-03-29
**Status:** Final Draft (merged with Addendum 01 + Addendum 02 + Product Decisions 2026-03-29)
**Authors:** Extractum Engineering (synthesized from 5 expert brainstorming sessions)

---

## What is xpatcher?

xpatcher is an SDLC automation pipeline that uses Claude Code as its execution engine. It transforms a natural-language feature request into production-ready code through a structured, multi-agent pipeline: planning, task decomposition, parallel execution, code review, testing, simplification, gap detection, and documentation. Every step produces auditable YAML artifacts stored in a `.xpatcher/` folder within the target project.

**Core design**: A Python thin dispatcher orchestrates Claude Code subagents via `claude -p`. Python owns process lifecycle, state machine, and DAG scheduling. Claude agents own all reasoning and code generation. xpatcher is installed once (per user or server) and run against any project.

---

## Document Map

This proposal is organized into 11 subdocuments for progressive disclosure. Each section below provides a summary and key decisions, with a link to the full document.

---

### [1. Executive Summary](design/01-executive-summary.md)

High-level overview of xpatcher: what it is, the core architecture decision, key innovation points, and what this document set contains.

**Key points:**
- Two-level state machine (pipeline + per-task) with crash recovery
- File-based coordination -- all state is YAML, human-inspectable, git-trackable
- Adversarial review architecture with structurally isolated reviewer agents
- Self-correction loops with hard iteration caps and oscillation detection
- Per-agent model selection (Opus/Sonnet/Haiku) and transparent TUI output

---

### [2. System Architecture](design/02-system-architecture.md)

The technical foundation: component diagram, dispatcher design rationale, file-based coordination, installation model, state machine, task DAG, git branching, and pipeline resumption.

**Key decisions:**
- **Installation model**: Core installed once at `~/xpatcher/`, project artifacts at `<project>/.xpatcher/`. Config resolution: CLI flags > project `.xpatcher.yaml` > global `config.yaml` > defaults
- **File polling** selected over WebSocket/IPC/message queues for simplicity and crash recovery
- **Two-level state machine**: pipeline-level (PLANNING → EXECUTING → REVIEWING → ...) and per-task (PENDING → RUNNING → SUCCEEDED/FAILED)
- **DAG scheduling** with cycle detection, critical path priority, semaphore-based concurrency (default: 3 parallel agents in git worktrees)
- **Single feature branch** per pipeline; xpatcher never merges to main

**Sections:** Component Diagram | Dispatcher Design | Model ID Reference | File-Based Coordination | Installation Model | State Machine | Task DAG | Git Strategy | Pipeline Resumption

---

### [3. Pipeline Flow](design/03-pipeline-flow.md)

The 16-stage pipeline from intent capture through documentation and completion, with stage diagrams, specification tables, transition rules, self-correction loops, human gates, and completion output formatting.

**Key decisions:**
- **16 stages**: Intent → Plan → Plan Review → Plan Fix → Plan Approval (human) → Task Breakdown → Task Review → Task Fix → Prioritization → Execution Graph → Parallel Execution → Per-Task Quality → Fix Iteration → Gap Detection → Documentation → Completion (human)
- **Hard iteration caps**: plan review 3, task review 3, per-task quality 5, gap detection re-entry 2
- **Oscillation detection**: hash findings each iteration, escalate on repeat
- **Human gates**: plan approval and final completion always block; task review has 30-minute soft gate
- **Transparent output**: elapsed time per stage, task-level timers, live progress panel with optional agent log streaming

**Sections:** Stage Diagram | Stage Specification Table | Transition Table | Self-Correction Loops | Human Gates | Completion Output (happy + failure paths)

---

### [4. Agent Definitions](design/04-agent-definitions.md)

Complete definitions for all 8 agents with markdown frontmatter, system prompts, tool permissions, input/output formats, and constraints. Plus the expert panel discussion protocol and model selection rationale.

**Agents:**

| Agent | Model | Role |
|-------|-------|------|
| **Planner** | `opus[1m]` | Codebase analysis, structured YAML plan, read-only |
| **Executor** | `sonnet` / `opus` (critical) | Code implementation, plan-following, no web access |
| **Reviewer** | `opus` | Adversarial code review, read-only, checklist-driven |
| **Tester** | `sonnet` | Test generation and execution, test-files-only writes |
| **Simplifier** | `sonnet` | Behavior-preserving complexity reduction |
| **Gap Detector** | `opus` | Cross-cutting gap analysis, read-only |
| **Technical Writer** | `sonnet` | Documentation updates, doc-files-only writes |
| **Explorer** | `haiku` | Quick read-only codebase Q&A (default agent) |

**Also covers:** Multi-perspective planning checklist (v1, expert panel deferred to v2), plan-reviewer agent, critical thinking protocol, model selection rationale with config.yaml

**Sections:** Agent Roster | Planner | Multi-Perspective Planning | Plan Reviewer | Executor | Reviewer | Tester | Simplifier | Gap Detector | Technical Writer | Explorer | Model Selection

---

### [5. Artifact System](design/05-artifact-system.md)

The `.xpatcher/` folder structure, file naming conventions, YAML schemas for all artifact types, cross-referencing strategy, and dynamic versioning with the ArtifactVersioner.

**Key decisions:**
- **Folder layout**: `tasks/todo/`, `tasks/in-progress/`, `tasks/done/` -- dispatcher moves files between folders
- **Immutable artifacts**: all artifacts except `pipeline-state.yaml` are write-once; revisions create new versioned files
- **Dynamic versioning**: `plan-v{N}.yaml`, `plan-review-v{N}.yaml` -- auto-incremented by dispatcher, not hardcoded
- **Structured agent logs**: JSONL files per agent invocation at `logs/agent-<name>-<task>-<timestamp>.jsonl`

**Schemas defined:** Intent | Plan | Review | Task | Task Manifest | Execution Plan | Execution Log | Quality Report | Gap Report | Pipeline State

**Sections:** Folder Structure | Naming Conventions | Common Header | YAML Schemas (10 types) | Cross-Referencing | Versioning Strategy | Iteration Tracking

---

### [6. Quality and Testing Framework](design/06-quality-testing.md)

Acceptance criteria templates, testing strategy (pyramid), review agent structural isolation, simplification integration, gap detection process, convergence criteria, and language/framework auto-detection.

**Key decisions:**
- **Acceptance criteria severity**: `must_pass` (blocks), `should_pass` (warning), `nice_to_have` (logged)
- **Completion gate evaluated by orchestrator**, never by executor self-assessment
- **Test quality pipeline**: coverage check → negation check → LLM audit → mutation testing (optional) → flaky detection (5 runs)
- **Reviewer isolation**: 4 mechanisms (separate context, checklists, read-only tools, adversarial framing) plus collusion prevention metrics
- **Simplification safety**: isolated branch, per-change commits, automatic revert on test failure
- **Gap scope creep prevention**: gap tasks capped at 30% of original task count

**Sections:** Acceptance Criteria | Testing Strategy | Review Design | Simplification | Gap Detection | Convergence Criteria | Language Detection

---

### [7. CLI, Installation, and Plugin Configuration](design/07-cli-and-installation.md)

The `xpatcher` CLI command interface, installation directory layout (core vs project), the live TUI with elapsed time tracking and agent log streaming, plugin.json manifest, and settings.json defaults.

**Key features:**
- **CLI commands**: `xpatcher start`, `resume`, `status`, `list`, `cancel`, `logs`
- **Global flags**: `--project`, `--verbose`, `--stream-logs`, `--log-lines N`, `--quiet`, `--config`
- **Live TUI**: persistent progress panel with per-stage elapsed time, task-level timers, active agent count, token estimates
- **Agent log streaming**: `--verbose` (8 lines), `--stream-logs` (20 lines), Tab to switch between parallel agents
- **Log files always written** to `logs/agent-*.jsonl` regardless of verbosity
- **Core installation at `~/xpatcher/`**, project artifacts at `<project>/.xpatcher/`

**Sections:** CLI Commands | Global Flags | Pipeline ID | Interactive TUI | Log Streaming | Log File Format | Verbosity Levels | Post-hoc Log Access | Human Gate Prompts | Directory Layout | plugin.json | settings.json

---

### [8. Skill Definitions and Hooks](design/08-skills-and-hooks.md)

Complete skill definitions for all 9 slash commands (`/xpatcher:plan`, `:execute`, `:review`, `:test`, `:simplify`, `:detect-gaps`, `:update-docs`, `:status`, `:pipeline`) and hook specifications for policy enforcement, audit logging, and agent lifecycle tracking.

**Key decisions:**
- All skills have `disable-model-invocation: true` -- they are for manual/debug use; normal workflow uses the dispatcher CLI
- **PreToolUse hook** enforces 6 policies: read-only agents, tester scope, tech-writer scope, project boundary, dangerous commands, executor web isolation
- **PostToolUse hook** logs every tool call to JSONL for audit trail
- **Lifecycle hook** tracks agent start/stop with PIDs for hang detection

**Sections:** 9 Skill Definitions (with full frontmatter and prompts) | PreToolUse Hook (with Python code) | PostToolUse Hook | Lifecycle Hook

---

### [9. Dispatcher Internals](design/09-dispatcher-internals.md)

The Python dispatcher implementation: Claude session management with YAML parsing, artifact validation pipeline (3-stage), malformed output recovery (same-session fix), failure escalation, Pydantic schema definitions, and smart session management with context bridging.

**Key decisions:**
- **YAML-native**: agents output YAML (not JSON); dispatcher extracts via 4 strategies (raw parse → `---` separator → code block → strip prose)
- **3-stage validation**: YAML extraction → Pydantic schema validation → semantic cross-checks
- **Same-session fix**: malformed output retries use `--resume` so agent keeps full context (up to 2 retries)
- **Session lineages**: sessions chain across stages; registry tracks token estimates and triggers compaction at 70% / fresh start at 90% of context window
- **Context bridges**: when agents can't share sessions (different model, adversarial isolation), dispatcher builds targeted context summaries
- **Session reuse matrix**: planner→reviewer uses `--resume`; executor→reviewer gets fresh session (adversarial isolation); YAML fix uses same session; stale sessions (>4h) get fresh start

**Sections:** ClaudeSession | YAML Extraction | Validation Pipeline | ArtifactValidator | Malformed Output Recovery | Failure Escalation | Pydantic Schemas | Session Registry | Session-Aware Dispatcher | Compaction | Context Bridging | Session Reuse Matrix

---

### [10. Risk Mitigation](design/10-risk-mitigation.md)

Top 5 critical risks with severity ratings, 8 anti-patterns to avoid, circuit breakers and kill switches, cost management strategy, and security threat model.

**Top risks:**
1. Infinite correction loops (severity 9) -- mitigated by hard caps + strategy switching
2. Test theater (severity 9) -- mitigated by mutation testing + negation checks
3. Context window exhaustion (severity 6) -- mitigated by checkpointing + session compaction
4. Planning hallucination (severity 6) -- mitigated by grounding + two-pass validation
5. Reviewer/executor echo chamber (severity 6) -- mitigated by adversarial framing + isolation

**Circuit breakers:** Token budget | Iteration count | Cost budget | Wall-clock time | Emergency kill switch (all tested monthly)

**Security threat model (v1):** Single developer on trusted private repos. Bash pattern blocking for network sandbox. Environment scrubbing before agent spawning. Basic anti-injection prompts. Full defense deferred to v2.

**Cost visibility (v1):** Running cost in TUI footer, total pipeline cost in completion summary, per-agent/per-stage breakdown in completion.yaml. No budget enforcement in v1.

---

### [11. Implementation Roadmap and Open Questions](design/11-implementation-roadmap.md)

5-phase implementation plan (9+ weeks), installation script, project initialization, and 14 open questions organized by category.

**Phases:**
1. **Foundation** (Weeks 1-2): Plugin loading, explorer agent, session management, state machine
2. **Core Pipeline** (Weeks 3-4): Planner, executor, reviewer; plan-execute-review loop
3. **Quality Gates** (Weeks 5-6): Tester, simplifier, gap detector, tech-writer; hooks; quality loop
4. **TUI, Logging, Polish** (Weeks 7-8): Rich TUI, log streaming, parallel execution, worktrees, retry, circuit breakers
5. **Packaging** (Week 9+): Per-user installer, project auto-init, `xpatcher logs` command, integration tests

**Open questions include:** headless agent selection, hook protocol, plugin settings scope, session isolation, Agent Teams migration path, MCP server for state, context window management strategy

---

## Quick Navigation

| I want to... | Start here |
|---|---|
| Understand what xpatcher is | [Executive Summary](design/01-executive-summary.md) |
| See the full pipeline flow | [Pipeline Flow](design/03-pipeline-flow.md) |
| Install and run xpatcher | [CLI and Installation](design/07-cli-and-installation.md) |
| Understand agent roles and prompts | [Agent Definitions](design/04-agent-definitions.md) |
| See what artifacts are produced | [Artifact System](design/05-artifact-system.md) |
| Understand the testing approach | [Quality and Testing](design/06-quality-testing.md) |
| Modify skills or hooks | [Skills and Hooks](design/08-skills-and-hooks.md) |
| Work on the Python dispatcher | [Dispatcher Internals](design/09-dispatcher-internals.md) |
| Assess risks and mitigations | [Risk Mitigation](design/10-risk-mitigation.md) |
| Plan implementation work | [Roadmap and Open Questions](design/11-implementation-roadmap.md) |
| Look up canonical schemas | [Dispatcher Internals, Canonical Schema Reference](design/09-dispatcher-internals.md) |

# Executive Summary

> Part of the [xpatcher Design Proposal](../xpatcher-design-proposal.md)

---

## What is xpatcher?

xpatcher is an SDD automation pipeline that uses Claude Code as its execution engine. It transforms a natural-language feature request into production-ready code through a structured, multi-agent pipeline: intent capture, executable specification drafting, execution-slice decomposition, execution, code review, dispatcher-owned verification, specification-to-code gap detection, and documentation. The specification artifacts are intentionally ephemeral: they exist to drive implementation and validation, while the shipped source code remains the only long-term source of truth.

## Core Architecture Decision

A **Python thin dispatcher** orchestrates **Claude Code subagents** invoked via the headless CLI (`claude -p`). Python owns the execution loop, state machine transitions, DAG scheduling, and process lifecycle. Claude Code agents own all reasoning, code generation, and decision-making. xpatcher is **installed once** (per user account or server, e.g. `~/xpatcher/`) and run against **any project**. The core installation contains the plugin (agents, skills, hooks) and dispatcher code. Project-specific runtime artifacts are created under `$XPATCHER_HOME/.xpatcher/projects/...` during pipeline runs.

## Key Innovation Points

- **Two-level state machine**: pipeline-level states (planning, executing, reviewing) and per-task states (pending, running, succeeded, failed) with validated transitions and crash recovery.
- **Executable-spec workflow**: xpatcher creates a detailed temporary specification first, then uses it to drive execution, adversarial review, and gap detection with minimal human intervention.
- **File-based coordination**: all runtime state lives under `$XPATCHER_HOME/.xpatcher/` as YAML. Human-inspectable and crash-recoverable without polluting the target repository.
- **Adversarial review architecture**: reviewer agents are structurally isolated from executor agents -- different context, different prompts, checklist-driven, read-only tools.
- **Self-correction with hard limits**: review-fix loops have iteration caps (default 3), oscillation detection, and escalation to humans.
- **Per-agent model selection**: Opus for planning and review (deep reasoning), Sonnet for execution and testing (speed/cost balance), Haiku for exploration (cheap and fast).
- **Transparent pipeline output**: real-time TUI showing elapsed time per stage, current activity, and optional streaming of agent logs.

## What This Document Contains

This is the master reference for building xpatcher. It specifies the system architecture, all 16 pipeline stages with entry/exit criteria, 8 agent definitions with complete markdown frontmatter, YAML schemas for all artifacts, the quality/testing framework, the full Claude Code plugin specification, the installation and deployment model, risk mitigations, and a phased implementation roadmap.

---

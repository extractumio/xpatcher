# Agent Definitions

xpatcher uses 9 specialized Claude Code subagents, each with a specific role, model, tool access, and constraints.

## Agent Roster

| Agent | Model | Purpose | Key Constraint |
|-------|-------|---------|----------------|
| **Planner** | Opus[1m] | Decompose requirements into tasks/DAG | No file writes (except plan/task YAMLs) |
| **Plan Reviewer** | Opus | Review plans and task manifests (Stages 3, 7) | Read-only, plan-specific checklist |
| **Executor** | Sonnet (Opus for critical path) | Implement a single task | No web access, no subagent spawning |
| **Reviewer** | Opus | Adversarial code review | Read-only, isolated from executor context |
| **Tester** | Sonnet | Generate and run tests | Can only write test files |
| **Simplifier** | Sonnet | Reduce complexity, remove duplication | Behavior-preserving; reverts on test failure |
| **Gap Detector** | Opus | Find missing requirements/integrations | Read-only, anchored to original intent |
| **Technical Writer** | Sonnet | Update/create docs for implemented changes | Only writes doc/readme files |
| **Explorer** | Haiku | Quick codebase questions (default agent) | Read-only, low effort |

## Model Selection Rationale

- **Opus (+ 1M context)**: planning, review, gap detection -- tasks requiring deep reasoning
- **Sonnet**: execution, testing, simplification, docs -- speed/cost balance for mechanical work
- **Haiku**: exploration -- cheap and fast for read-only lookups

Model aliases auto-resolve to latest versions. Pin to full IDs in `config.yaml` for production.

| Alias | Full Model ID |
|-------|--------------|
| `opus` | `claude-opus-4-6` |
| `sonnet` | `claude-sonnet-4-6` |
| `haiku` | `claude-haiku-4-5-20251001` |
| `opus[1m]` | `claude-opus-4-6[1m]` |

## Adversarial Isolation

Reviewer agents are structurally isolated from executor agents:
- Different system prompts and context
- Reviewer cannot see executor reasoning
- Reviewer uses a checklist-driven approach
- Review sessions always start fresh (no session reuse)

## Agent Definitions Location

Agent markdown files with full prompts, frontmatter, and tool specifications are in `.claude-plugin/agents/`.

## Full Specification

Historical design spec: [ephemeral/proposals/design/04-agent-definitions.md](ephemeral/proposals/design/04-agent-definitions.md) (may not match current implementation).

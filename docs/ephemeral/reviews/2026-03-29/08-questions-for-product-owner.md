# Questions for Product Owner / Product Manager

**Date:** 2026-03-29
**Context:** Design review of xpatcher v1.2 specification by 7-expert panel
**Action required:** Decisions on these questions before implementation begins

---

## Priority 1: Blocking Decisions (Must Decide Before Phase 1)

### Q1. Sequential vs Parallel Execution for v1
**Source:** Architecture, Pipeline, DevOps (unanimous recommendation)

The worktree merge strategy for parallel execution is the biggest gap in the spec. The experts unanimously recommend deferring parallel execution to v2 and running tasks sequentially on the feature branch in v1.

**Trade-off:**
- **Sequential v1:** Eliminates CRIT-1 entirely. Simpler to implement, debug, and test. Tasks run in dependency order, one at a time, directly on the feature branch. No worktrees, no merge conflicts, no concurrent state issues. Estimated: saves 2-3 weeks of implementation.
- **Parallel v1:** Higher throughput (~2x-3x faster execution for features with independent tasks). But requires: merge protocol, conflict resolution, integration testing post-merge, worktree cleanup, concurrent state protection. Estimated: adds 2-3 weeks and is the highest-risk component.

**Question:** Is parallel execution a hard requirement for v1, or can it be deferred to v2?

---

### Q2. Expert Panel Scope for v1
**Source:** Architecture, Agent Design, Pipeline

The expert panel (7 domain experts x 2-3 rounds) adds 14-21 agent invocations before a single line of code is written. Cost: $5-10+ per planning cycle. For simple features, this is massive overhead.

**Trade-off:**
- **Defer panel to v2:** Use single planner with multi-perspective checklist prompt (already defined as "Critical Thinking Protocol"). Dramatically simpler.
- **Keep panel but add activation threshold:** Only activate for features touching >3 modules or >10 files, or when user explicitly requests `--panel`.

**Question:** Can the expert panel be deferred to v2, or is it needed for v1? If kept, what is the activation threshold?

---

### Q3. Task ID Format
**Source:** Architecture, Pipeline

Three conflicting formats in the spec:
- `task-1.1` (phase.task, dotted -- preserves hierarchy)
- `task-001` (sequential, zero-padded -- simpler, used in filenames)
- `^task-\d+\.\d+$` (Pydantic regex -- matches dotted format)

**Question:** Which format should be canonical? Recommendation: `task-NNN` (zero-padded sequential) since file paths already use it and it avoids dot-parsing complexity.

---

### Q4. Schema Authority
**Source:** Architecture, Agent Design

When agent prompts, YAML schemas, and Pydantic models disagree (which they do in 11+ places), which is authoritative?

**Recommendation:** Pydantic models are the source of truth (they are executable code). Agent prompts must be validated against them. YAML schema docs are generated from Pydantic models.

**Question:** Do you agree with this approach?

---

### Q5. `.xpatcher/` in Git
**Source:** Architecture

The spec says add `.xpatcher/` to `.gitignore`, but commit messages reference artifact paths inside `.xpatcher/`. If gitignored, team members reviewing the PR cannot see planning artifacts.

**Options:**
- **(A) Gitignore (current):** Artifacts are transient. Only the code diff matters. Artifacts are on the developer's machine for debugging.
- **(B) Commit artifacts:** Full audit trail in git. PR reviewers see the plan, reviews, test reports. But: large YAML files, JSONL logs in git history.
- **(C) Hybrid:** Commit key artifacts (plan, task manifest, gap report) but gitignore logs and session data.

**Question:** Which approach? Recommendation: Option A for v1 (simplest), consider C for v2.

---

## Priority 2: Important Decisions (Should Decide Before Phase 2)

### Q6. Plan Review Iteration Cap
**Source:** Architecture, Pipeline

The spec says 3 in Section 3.4 but 5 in Section 5.6. These are different numbers in different documents for the same parameter.

**Question:** What is the intended default? Recommendation: 3 (matches the design principle of "escalate early, don't burn iterations").

---

### Q7. Per-Task Quality Loop Max Iterations
**Source:** Pipeline, QA

Section 3.4 says 5. Section 6.1 says `max_retry_cycles: 3`. Which applies?

**Question:** Is the per-task quality loop cap 3 or 5? Recommendation: 3 for the full simplify/test/review cycle, with a hard stop at 5 total agent invocations (counting fix iterations separately).

---

### Q8. Stuck Tasks: Block Pipeline or Allow Partial Completion?
**Source:** Pipeline, Architecture

Current spec implies the pipeline hard-blocks when tasks are stuck. But the "no partial feature delivery" design decision suggests this is intentional.

**Options:**
- **(A) Hard block:** Pipeline cannot reach Documentation/Completion with stuck tasks. User must fix or skip.
- **(B) Soft block:** Pipeline proceeds to Documentation/Completion but warns prominently. Stuck tasks documented as incomplete.

**Question:** Hard or soft block? The `xpatcher skip` command (once defined) provides the escape valve for hard block.

---

### Q9. Human Gate Timeout Behavior
**Source:** UX

Plan approval blocks forever. No timeout, no notification, no reminder.

**Options:**
- **(A) Block forever (current):** Pipeline waits. User must actively check.
- **(B) Soft timeout (4h):** Pipeline pauses, writes state. Resumable anytime.
- **(C) Terminal bell:** Print `\a` when gate is reached. Lightweight notification.

**Question:** Which approach for v1? Recommendation: B+C (4h soft timeout with terminal bell).

---

### Q10. Gap Detection: Auto-Execute or Human-Approve Gap Tasks?
**Source:** Pipeline

Currently, gap detection pass 1 auto-generates and auto-executes new tasks. For security-sensitive features (like OAuth), auto-executing gap-generated code seems risky.

**Options:**
- **(A) Auto-execute critical gaps, human-approve expected gaps (current, but unclear)**
- **(B) Always human-approve gap tasks before execution**
- **(C) Auto-execute all gap tasks (fastest)**

**Question:** Which approach? Recommendation: B for v1 (conservative), move to A when trust is established.

---

### Q11. Test Quality Gate Profile
**Source:** QA

The 5-gate test quality pipeline adds 6-30+ minutes per task. QA expert recommends tiered profiles:

| Profile | Gates | When |
|---------|-------|------|
| **Lite** | Coverage + regression only | Low-risk tasks, refactors |
| **Standard** | Coverage + negation + flaky (3 runs) | Most tasks |
| **Thorough** | All gates including mutation | Critical/security tasks |

**Question:** Should the planner assign quality profiles to tasks, or should there be a single profile for all tasks in v1?

---

## Priority 3: Decisions That Can Wait (Phase 3-5)

### Q12. Install Path Convention
**Source:** Platform

Default `~/xpatcher/` is visible in home directory. Alternative: `~/.xpatcher/` (hidden, XDG convention).

**Question:** Keep `~/xpatcher/` (discoverable) or change to `~/.xpatcher/` (tidy)?

---

### Q13. Ubuntu 20.04 Support
**Source:** Platform

Ubuntu 20.04 has Python 3.8 (not supported). Still in extended LTS.

**Question:** Explicitly out of scope? Should the installer warn clearly?

---

### Q14. Docker/CI Usage
**Source:** Platform

Is running xpatcher in Docker or CI an intended use case for v1? If so, installation needs a Dockerfile and non-interactive mode.

**Question:** Is this v1 scope or deferred?

---

### Q15. Target Projects With No Test Suite
**Source:** QA

Acceptance criteria templates assume test frameworks exist. What if the target project has zero tests?

**Question:** Does xpatcher bootstrap a test framework, or require one as a precondition?

---

### Q16. Minimum Claude Code CLI Version
**Source:** Platform

xpatcher depends on specific CLI flags (`--agent`, `--resume`, `--output-format json`). Open Question #1 in the spec notes `--agent` may not exist yet.

**Question:** What is the minimum Claude Code CLI version? This must be pinned before Phase 5.

---

### Q17. Rebase-on-Resume Safety
**Source:** DevOps

If the feature branch was pushed to remote before pause, rebase-on-resume creates divergent history.

**Options:**
- **(A) Only rebase if branch is unpushed**
- **(B) Always rebase, force-push if needed (risky)**
- **(C) Merge instead of rebase when branch is pushed**

**Question:** Which approach? Recommendation: A (safest).

---

*Total: 17 questions requiring product owner decision.*
*Priority 1 (5 questions): Must decide before Phase 1.*
*Priority 2 (6 questions): Should decide before Phase 2.*
*Priority 3 (6 questions): Can decide during Phases 3-5.*

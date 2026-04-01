# Risk Analysis: SDLC Automation Pipeline

## Risks, Pitfalls, and Mitigations for Agent-Orchestrated Development

**Document ID:** 05-risks-pitfalls-mitigations
**Status:** Draft
**Last Updated:** 2026-03-28

---

## Table of Contents

1. [Risk Rating Framework](#1-risk-rating-framework)
2. [Common Pitfalls in Agent Orchestration](#2-common-pitfalls-in-agent-orchestration)
3. [State Management Risks](#3-state-management-risks)
4. [Quality Risks](#4-quality-risks)
5. [Operational Risks](#5-operational-risks)
6. [Mitigation Strategies (Cross-Cutting)](#6-mitigation-strategies-cross-cutting)
7. [Success Metrics and Monitoring](#7-success-metrics-and-monitoring)
8. [Anti-Patterns to Avoid](#8-anti-patterns-to-avoid)
9. [Summary Risk Register](#9-summary-risk-register)

---

## 1. Risk Rating Framework

Every risk in this document is scored on two axes:

| Dimension    | Low (1)                        | Medium (2)                          | High (3)                                  |
|-------------|--------------------------------|-------------------------------------|-------------------------------------------|
| Likelihood  | Unlikely under normal use      | Happens periodically in production  | Expected regularly without mitigation     |
| Impact      | Minor inconvenience, easy fix  | Significant rework or cost overrun  | Pipeline halt, data loss, or shipped bugs |

**Severity** = Likelihood x Impact. Scores: 1-2 Low, 3-4 Medium, 6-9 High.

Each risk entry follows a consistent template:

- **Description:** What the risk is and why it matters.
- **Likelihood / Impact / Severity:** Numeric rating.
- **Detection Method:** How operators or the system can detect the risk materializing.
- **Mitigation Strategy:** Concrete, actionable steps to reduce likelihood or impact.

---

## 2. Common Pitfalls in Agent Orchestration

### 2.1 Infinite Correction Loops

- **Description:** An executor agent produces code, a reviewer finds issues, the executor "fixes" them but introduces new issues or reverts the previous fix, the reviewer flags those, and the cycle repeats indefinitely. This is the single most predictable failure mode in agent-driven development loops. It burns tokens, exhausts context windows, and produces progressively worse code as the agent loses track of what it already tried.
- **Likelihood:** 3 (High) -- Without explicit bounds, this happens on roughly 15-25% of non-trivial tasks.
- **Impact:** 3 (High) -- Consumes budget, wastes wall-clock time, and the final output is often worse than the first attempt.
- **Severity:** 9 (High)
- **Detection Method:**
  - Count review-fix round-trips per task. Alert when count exceeds threshold (recommended: 3).
  - Track semantic diff size across iterations. If iteration N+1 changes the same lines as iteration N-1, the loop is oscillating.
  - Monitor token consumption per task against a per-task budget ceiling.
- **Mitigation Strategy:**
  - Hard cap on correction iterations (default: 3). After the cap, escalate to a human with the full iteration history.
  - On the second correction pass, inject a "diff of diffs" into the agent's context showing what changed across all iterations, so it can see its own oscillation.
  - If the first two corrections fail on the same issue, switch strategy: instead of asking the same agent to fix it, spawn a fresh agent with only the original spec and the test failures (no accumulated bad context).
  - Maintain a per-task "attempted fixes" log that is persisted outside the agent's context window so no fix is silently re-attempted.

### 2.2 Context Window Exhaustion

- **Description:** Long-running tasks accumulate conversation history, code snippets, review comments, and tool outputs until the agent's effective context window is consumed. Once exhausted, the agent either truncates important early context (losing the original specification) or fails outright.
- **Likelihood:** 3 (High) -- Virtually guaranteed for any task requiring more than ~4 iterations or touching more than ~5 files.
- **Impact:** 2 (Medium) -- Usually recoverable by restarting the agent with a fresh context, but progress is lost.
- **Severity:** 6 (High)
- **Detection Method:**
  - Track token usage per agent session. Alert at 60% and 80% of the model's context limit.
  - Monitor for "amnesia" signals: agent re-asks questions already answered, contradicts its own earlier output, or loses track of file paths.
- **Mitigation Strategy:**
  - Implement context checkpointing: at defined milestones (after planning, after each file edit, after each review pass), compress the conversation into a structured summary and start a new session with that summary as the seed.
  - Separate "reference context" (spec, architecture decisions, file tree) from "working context" (current iteration's code and review). Keep reference context in a persistent document the agent can re-read, rather than relying on conversation history.
  - For multi-file tasks, process files sequentially rather than loading all files into context simultaneously.
  - Use the filesystem as external memory: write intermediate plans, decisions, and notes to scratch files that the agent reads on demand rather than holding in context.

### 2.3 Token Cost Explosion with Parallel Agents

- **Description:** Parallel execution of N agents means N times the token consumption. Worse, each agent may redundantly read the same files, regenerate the same context, and duplicate reasoning about shared architectural decisions. A 5-agent parallel run can easily consume 10-20x the tokens of a single sequential run (not just 5x) due to redundant context loading.
- **Likelihood:** 3 (High) -- Inherent to the parallel model.
- **Impact:** 2 (Medium) -- Purely financial until budgets are exhausted, then it blocks work.
- **Severity:** 6 (High)
- **Detection Method:**
  - Real-time token consumption dashboard with per-agent and per-task breakdowns.
  - Compare actual cost against pre-estimated budget at task assignment time. Alert at 150% of estimate.
  - Track "context overlap ratio": how much of each agent's context is shared with other agents' contexts.
- **Mitigation Strategy:**
  - Pre-compute shared context once and inject it into all parallel agents, rather than having each agent independently discover it.
  - Set per-task and per-pipeline token budgets. When a task hits its budget, pause and escalate rather than continuing to burn tokens.
  - Limit parallelism based on budget: if the pipeline budget is $X, and each agent costs ~$Y per task, cap parallel agents at X / (Y * estimated_tasks).
  - Use cheaper models for mechanical subtasks (formatting, simple refactors) and reserve expensive models for design decisions and complex code generation.
  - Cache common tool outputs (file reads, directory listings, dependency trees) at the harness level so agents don't each pay to regenerate them.

### 2.4 Agent Hallucination in Planning and Task Decomposition

- **Description:** When a planner agent decomposes a large task into subtasks, it may hallucinate dependencies that do not exist, invent APIs or libraries that are not available, misunderstand the codebase structure, or produce subtasks that are internally inconsistent. Since the plan drives all downstream work, a hallucinated plan wastes the entire pipeline.
- **Likelihood:** 2 (Medium) -- Modern models are reasonably good at planning when given sufficient context, but they still hallucinate under ambiguity.
- **Impact:** 3 (High) -- A bad plan wastes every downstream agent's work.
- **Severity:** 6 (High)
- **Detection Method:**
  - Validate plans against the actual codebase: check that referenced files, functions, and APIs actually exist before accepting the plan.
  - Run a separate "plan critic" agent that tries to find flaws in the plan (adversarial review).
  - Check that the plan's dependency graph is a valid DAG (no cycles, no orphan nodes).
  - Flag plans that reference external libraries not in the project's dependency manifest.
- **Mitigation Strategy:**
  - Ground the planner with concrete codebase context: feed it the actual file tree, existing module interfaces, and dependency manifests -- not just the task description.
  - Require the planner to cite specific files and line ranges for every subtask. If it cannot cite them, the subtask is likely hallucinated.
  - Use a two-pass planning approach: first pass generates the plan, second pass validates every claim in the plan against the real codebase (using tool calls to check file existence, function signatures, etc.).
  - Keep a registry of available internal APIs, libraries, and architectural patterns that the planner must select from, rather than inventing new ones.

### 2.5 Over-Decomposition vs. Under-Decomposition

- **Description:** Over-decomposition splits work into so many tiny tasks that coordination overhead dominates: agents spend more time reading shared context and resolving merge conflicts than writing code. Under-decomposition produces tasks so large that individual agents exhaust their context windows or produce monolithic changes that are unreviewable. Finding the right granularity is a balancing act that agents are not naturally good at.
- **Likelihood:** 2 (Medium) -- Planners tend toward over-decomposition because they are rewarded for appearing thorough.
- **Impact:** 2 (Medium) -- Either direction degrades throughput and quality, but is recoverable by re-planning.
- **Severity:** 4 (Medium)
- **Detection Method:**
  - Track average task completion time. If tasks complete in under 30 seconds, they are likely too small. If they exceed 20 minutes, they are likely too large.
  - Monitor the ratio of "coordination tokens" (reading context, resolving conflicts) to "productive tokens" (actual code generation) per task.
  - Count the number of inter-task dependencies. If more than 40% of tasks block on other tasks, decomposition is likely too fine-grained.
- **Mitigation Strategy:**
  - Define a target task size: each task should produce a reviewable diff of roughly 50-200 lines, touching 1-3 files.
  - Provide the planner with explicit decomposition guidelines and examples of well-sized tasks from past successful runs.
  - If a plan exceeds 10 subtasks for a feature, require the planner to justify why it cannot be done in fewer.
  - Build a feedback loop: after pipeline completion, score the decomposition quality and use that data to improve future planning prompts.

### 2.6 "Works on My Machine" -- Context-Dependent Code

- **Description:** An agent produces code that works within its own execution context (specific file state, specific branch, specific tool versions) but fails when integrated into the broader codebase. This happens because agents operate on a snapshot of the codebase and may not account for concurrent changes, environment differences, or implicit dependencies.
- **Likelihood:** 2 (Medium) -- Common when agents do not run integration tests within their workflow.
- **Impact:** 2 (Medium) -- Caught at integration time, but causes rework.
- **Severity:** 4 (Medium)
- **Detection Method:**
  - Run each agent's output through a fresh integration test suite (not just the agent's own tests) before accepting it.
  - Compare the agent's assumed file state (as seen in its context) against the actual current state of the branch.
  - Flag agent outputs that import modules or call functions not visible in the agent's provided context.
- **Mitigation Strategy:**
  - Give agents access to the real filesystem and real test runner, not simulated environments.
  - Require agents to run the full test suite (or at minimum, affected test suites) as a final step before declaring completion.
  - For parallel agents, run integration tests on the merged result of all agents' changes, not just individual outputs.
  - Pin the codebase snapshot at task assignment time and detect drift: if the base branch has changed since assignment, re-validate before merging.

---

## 3. State Management Risks

### 3.1 Pipeline State Corruption

- **Description:** The harness maintains state about each task (status, assigned agent, iteration count, artifacts). If this state becomes inconsistent -- for example, a task is marked "complete" but its artifacts were not written, or a task is marked "in progress" but its agent has crashed -- the pipeline makes decisions based on false information.
- **Likelihood:** 2 (Medium) -- Especially likely during error recovery or manual intervention.
- **Impact:** 3 (High) -- Corrupted state can cause silent failures where the pipeline "completes" but the output is incomplete.
- **Severity:** 6 (High)
- **Detection Method:**
  - Periodic state consistency checks: verify that every "complete" task has its expected artifacts, every "in progress" task has a live agent, every "blocked" task has an unresolved dependency.
  - Checksums on state transitions: before moving a task to a new state, verify preconditions (e.g., "complete" requires test pass + review pass).
  - Log every state transition with timestamps and the actor that triggered it, enabling post-hoc audit.
- **Mitigation Strategy:**
  - Use a state machine with explicit, validated transitions. Reject invalid transitions (e.g., "pending" directly to "complete" without passing through "in progress" and "review").
  - Store pipeline state in a transactional store (even a simple SQLite database) rather than in-memory data structures or flat files that can be partially written.
  - Implement an idempotent recovery procedure: on any restart, the harness re-derives state from artifacts on disk (git history, test results, review logs) rather than trusting the cached state.
  - Run a "state reconciliation" pass at pipeline startup and at configurable intervals.

### 3.2 Race Conditions in Parallel Task Execution

- **Description:** When multiple agents execute in parallel, they may read the same file simultaneously and then both attempt to write changes. The second write silently overwrites the first, or both writes produce a conflict that neither agent is aware of. Beyond file-level races, there are logical races: two agents may both decide to refactor the same function differently based on their independent understanding of the codebase.
- **Likelihood:** 3 (High) -- Virtually certain whenever parallel agents touch overlapping code areas.
- **Impact:** 2 (Medium) -- Usually caught by merge conflicts or test failures, but the rework cost is high.
- **Severity:** 6 (High)
- **Detection Method:**
  - Pre-execution file overlap analysis: before launching parallel agents, check whether their planned file edits overlap. Flag overlaps for sequential execution or explicit coordination.
  - Git-level conflict detection: attempt a dry-run merge of all parallel branches before actual integration.
  - Semantic conflict detection: even if files do not textually conflict, check for logical conflicts (e.g., two agents both renaming the same function to different names).
- **Mitigation Strategy:**
  - Enforce file-level locking: if agent A is modifying `foo.py`, no other agent may modify `foo.py` concurrently. Assign file locks at task assignment time based on the planner's file-change predictions.
  - Use feature branches per agent and integrate via automated merge with conflict detection, not direct commits to a shared branch.
  - When overlap is unavoidable, designate one agent as the "owner" of the shared file and have other agents submit their changes as requests to the owner.
  - Design task decomposition to minimize file overlap: split by module/package boundary, not by feature slice across modules.

### 3.3 Stale State After Human-in-the-Loop Delays

- **Description:** When the pipeline pauses for human approval (review gates, architectural decisions, budget approval), the codebase may change in the interim. Other developers push commits, dependencies update, or the requirements themselves evolve. When the pipeline resumes, it operates on assumptions that are no longer valid.
- **Likelihood:** 2 (Medium) -- Depends on how long human gates take, but even a few hours can introduce drift.
- **Impact:** 2 (Medium) -- Wasted work if assumptions have changed, but detectable.
- **Severity:** 4 (Medium)
- **Detection Method:**
  - On pipeline resume, compare the current HEAD of the target branch against the HEAD at pause time. If they differ, flag for re-evaluation.
  - Check timestamps: if a human gate has been open for more than a configurable threshold (e.g., 4 hours), trigger a freshness check before resuming.
  - Monitor for requirement changes (issue updates, spec document modifications) during the pause.
- **Mitigation Strategy:**
  - On resume, always rebase the working branch onto the current target and re-run affected tests before proceeding.
  - Implement a "freshness window": if the pause exceeds a threshold, invalidate the cached plan and re-plan from the current state.
  - Provide human reviewers with a clear, time-bounded notification that includes the cost of delay: "This pipeline is paused waiting for your review. Estimated cost of stale-state rework increases by $X per hour of delay."
  - Allow "provisional continuation": the pipeline proceeds with the assumption that approval will be granted, but tags all subsequent work as provisional. If the human rejects, the provisional work is discarded. This reduces the cost of delay for high-confidence approvals.

### 3.4 Recovery from Partial Failures

- **Description:** A pipeline of 10 tasks completes 7 successfully when task 8 fails catastrophically (agent crash, API outage, unrecoverable error). The pipeline must decide: retry task 8, roll back to before task 8, roll back everything, or pause for human intervention. If tasks have side effects (git commits, file modifications, external API calls), rollback is non-trivial.
- **Likelihood:** 2 (Medium) -- Partial failures are a normal part of distributed system operation.
- **Impact:** 3 (High) -- Without a clear recovery strategy, operators face a "half-built" state that is difficult to reason about.
- **Severity:** 6 (High)
- **Detection Method:**
  - Health checks after each task: verify expected artifacts exist and tests pass before proceeding.
  - Agent heartbeat monitoring: detect crashed agents within seconds, not minutes.
  - Distinguish between transient failures (API timeout -- retry is safe) and permanent failures (fundamental design flaw -- retry will not help).
- **Mitigation Strategy:**
  - Design tasks to be idempotent: re-running a task from scratch should produce the same result without duplicating side effects.
  - Maintain rollback points: before each task, tag the git state so the pipeline can revert to any previous good state.
  - Classify failures and define automated responses:
    - Transient (network, timeout): retry up to 3 times with exponential backoff.
    - Agent error (bad output, hallucination): restart with fresh agent and accumulated context from prior good tasks.
    - Fundamental (impossible task, missing dependency): pause pipeline, notify human, preserve all state for diagnosis.
  - Never auto-rollback successful tasks without human confirmation. The cost of re-doing 7 good tasks is almost always higher than manually resolving 1 failed task.

### 3.5 Git Merge Conflicts from Parallel Agents

- **Description:** Multiple agents working on parallel branches produce changes that conflict at merge time. Textual conflicts (same line changed differently) are detectable by git, but semantic conflicts (incompatible design decisions that do not overlap textually) are invisible until tests fail or humans review.
- **Likelihood:** 3 (High) -- Near-certain for any non-trivial parallel workload.
- **Impact:** 2 (Medium) -- Resolution is time-consuming and may require re-doing work.
- **Severity:** 6 (High)
- **Detection Method:**
  - Dry-run merges after each agent completes, before accepting the result.
  - Run the full test suite on the merged result, not just individual branches.
  - Static analysis on the merged code: check for duplicate function definitions, incompatible type signatures, import conflicts.
- **Mitigation Strategy:**
  - Minimize merge surface area through clean task decomposition along module boundaries.
  - Establish a merge order: merge the most foundational changes (interfaces, types, shared utilities) first, then rebase dependent branches onto the merged result before proceeding.
  - Use an "integration agent" whose sole job is to merge parallel branches, resolve conflicts, and verify the merged result passes all tests.
  - For high-conflict areas, fall back to sequential execution: serialize tasks that touch the same modules.

---

## 4. Quality Risks

### 4.1 Reviewer/Executor Echo Chamber

- **Description:** When the same model (or even the same prompt style) is used for both code generation and code review, the reviewer has the same blind spots as the executor. It tends to approve the same patterns it would have generated, miss the same edge cases, and fail to challenge the same assumptions. The review becomes a formality that adds cost without adding assurance.
- **Likelihood:** 3 (High) -- Structurally guaranteed when using a single model for both roles.
- **Impact:** 2 (Medium) -- Bugs that should be caught in review reach testing or production.
- **Severity:** 6 (High)
- **Detection Method:**
  - Track "review pass-through rate": if the reviewer approves more than 80% of submissions on the first pass, it is likely not catching enough issues.
  - Compare agent-reviewed code against human-reviewed code on a sample basis. Measure the miss rate.
  - Inject known-bad code samples periodically ("canary bugs") and check whether the reviewer catches them.
- **Mitigation Strategy:**
  - Use different system prompts for reviewer vs. executor. The reviewer prompt should be adversarial: "Your job is to find problems. You are evaluated on bugs found, not on approval rate."
  - Use a checklist-based review process: the reviewer must explicitly address each item (error handling, edge cases, security, performance, naming, test coverage) rather than giving a holistic "looks good."
  - Rotate review personas: use different temperature settings, different emphasis areas, or different models for review vs. execution.
  - Mandate that the reviewer cannot see the executor's reasoning/chain-of-thought, only the code diff and the original specification. This prevents the reviewer from being anchored by the executor's justifications.
  - Reserve human review for high-risk changes (security-sensitive code, public API changes, database migrations) where agent review is insufficient.

### 4.2 Test Theater

- **Description:** Agents write tests that technically pass but do not actually verify meaningful behavior. Common manifestations: tests that assert `True == True`, tests that mock so aggressively that no real code is exercised, tests that duplicate the implementation logic as the assertion (tautological tests), and tests that pass regardless of whether the feature works. Agents are incentivized to make tests pass, not to make tests meaningful.
- **Likelihood:** 3 (High) -- Agents optimize for the metric they are given. If the metric is "tests pass," they will make tests pass by any means.
- **Impact:** 3 (High) -- False confidence in code quality. Bugs ship to production with a green test suite.
- **Severity:** 9 (High)
- **Detection Method:**
  - Mutation testing: systematically introduce bugs into the code and check whether the test suite catches them. If mutation survival rate exceeds 30%, the tests are theatrical.
  - Measure code coverage, but also measure "assertion density": a test that executes code but does not assert on its output is worthless.
  - Flag tests that contain no assertions, tests that only assert on mock return values, and tests where the expected value is computed by the same logic being tested.
  - Review test-to-code ratio: if an agent produces 500 lines of code and 5 lines of tests, the tests are almost certainly insufficient.
- **Mitigation Strategy:**
  - Provide explicit test quality criteria in the agent's prompt: "Each test must assert on observable behavior. Mocking is permitted only for external services. Each test must fail if the feature is removed."
  - Run mutation testing as a pipeline gate. Define a minimum mutation kill rate (recommended: 70% for new code).
  - Require negative test cases: for every feature, the agent must also write tests that verify the feature rejects invalid input, handles errors, and respects boundary conditions.
  - Separate the "test writer" role from the "code writer" role. The test writer receives only the specification, not the implementation, and writes tests that the implementation must satisfy. This is effectively contract-based testing.
  - Periodically have a human review a random sample of agent-generated tests for meaningfulness.

### 4.3 Regression Introduction During Parallel Execution

- **Description:** Agent A modifies module X and all of X's tests pass. Agent B modifies module Y and all of Y's tests pass. But the combined changes break module Z, which depends on both X and Y. Neither agent ran Z's tests because Z was not in their scope. This is the classic integration regression, amplified by parallelism.
- **Likelihood:** 2 (Medium) -- Depends on the coupling of the codebase.
- **Impact:** 3 (High) -- Regressions caught late are expensive to debug and fix because the causal chain spans multiple agents' changes.
- **Severity:** 6 (High)
- **Detection Method:**
  - Full test suite execution on the integrated result after merging all parallel branches.
  - Dependency analysis: before accepting parallel changes, check whether the changed modules have downstream dependents that were not tested.
  - Track test execution scope: flag when an agent's test run covers less than the transitive closure of affected modules.
- **Mitigation Strategy:**
  - After each merge of parallel branches, run the complete test suite, not just the tests for changed modules.
  - Build a dependency graph of the codebase and use it to automatically expand each agent's required test scope to include downstream dependents.
  - For tightly coupled codebases, prefer sequential execution over parallel execution. The speed gain of parallelism is not worth the integration cost.
  - Implement "integration checkpoints": after every N parallel tasks are merged, pause and run a full integration test before proceeding with more parallel work.

### 4.4 Code Quality Degradation Over Many Iterations

- **Description:** Each agent interaction adds code that is locally correct but globally incoherent. Over many iterations, the codebase accumulates inconsistent naming conventions, duplicated logic across modules (because agents do not know about each other's utilities), unnecessary abstractions introduced to solve single problems, and style drift. The codebase becomes progressively harder for both humans and agents to work with.
- **Likelihood:** 3 (High) -- Entropy increases without deliberate effort to reduce it.
- **Impact:** 2 (Medium) -- Gradual degradation. No single change is catastrophic, but cumulative effect is significant.
- **Severity:** 6 (High)
- **Detection Method:**
  - Track static analysis metrics over time (cyclomatic complexity, duplication ratio, dependency depth). Alert on sustained upward trends.
  - Monitor agent performance over time: if tasks that used to complete in 2 iterations now take 5, the codebase may be becoming harder to work with.
  - Periodic human code review focused on architectural coherence, not individual correctness.
- **Mitigation Strategy:**
  - Include architecture documentation and coding standards in every agent's context. Make adherence to these standards an explicit review criterion.
  - Schedule periodic "consolidation" tasks: deduplicate utilities, enforce naming conventions, remove dead code. These tasks can themselves be agent-driven.
  - Maintain a "project knowledge base" that records architectural decisions, utility locations, and naming conventions. Update it after each significant change so future agents have accurate context.
  - Run linters and formatters as automated pipeline gates, not as agent responsibilities. The agent should never have to think about formatting.

### 4.5 Security Vulnerabilities in Agent-Generated Code

- **Description:** Agents generate code that contains common security vulnerabilities: SQL injection, path traversal, insecure deserialization, hardcoded credentials, overly permissive CORS, missing input validation. Agents are trained on vast amounts of code, including insecure code, and may reproduce insecure patterns.
- **Likelihood:** 2 (Medium) -- Modern models are somewhat aware of security best practices, but still produce vulnerable code when context is insufficient.
- **Impact:** 3 (High) -- Security vulnerabilities in production can lead to data breaches, legal liability, and reputational damage.
- **Severity:** 6 (High)
- **Detection Method:**
  - Run SAST (Static Application Security Testing) tools as a pipeline gate on every code change.
  - Include security-focused review criteria in the review agent's prompt.
  - Scan for common vulnerability patterns: hardcoded secrets, `eval()` calls, unsanitized user input, disabled TLS verification.
  - Dependency vulnerability scanning on any newly added dependencies.
- **Mitigation Strategy:**
  - Include security requirements in every task specification, not as an afterthought.
  - Maintain a "banned patterns" list (e.g., `eval()`, `pickle.loads()` on untrusted input, `subprocess.shell=True` with user input) and check agent output against it automatically.
  - Use SAST tools as a hard gate: code with high-severity findings cannot proceed through the pipeline.
  - For security-critical code (authentication, authorization, cryptography, input parsing), mandate human review regardless of agent confidence.
  - Provide the agent with project-specific security guidelines and approved patterns for common operations (database queries, file I/O, HTTP requests).

---

## 5. Operational Risks

### 5.1 Cost Management

- **Description:** API token costs can escalate rapidly, especially with parallel agents, long-running tasks, and correction loops. A single runaway pipeline can consume hundreds of dollars in tokens before anyone notices. There is no natural ceiling on costs without explicit budget enforcement.
- **Likelihood:** 3 (High) -- Without budgets, cost overruns are the default.
- **Impact:** 2 (Medium) -- Financial cost, plus potential for hard stops when budget limits are hit at an inopportune time.
- **Severity:** 6 (High)
- **Detection Method:**
  - Real-time cost tracking at the pipeline, task, and agent level.
  - Compare actual spend against pre-allocated budget at regular intervals (e.g., after each task completion).
  - Alert at 50%, 75%, and 90% of budget consumption.
  - Track cost-per-line-of-code-produced and cost-per-test-passed as efficiency metrics.
- **Mitigation Strategy:**
  - Set hard budget limits at three levels: per-task, per-pipeline, and per-day. When any limit is hit, pause (do not crash) and escalate.
  - Pre-estimate costs before pipeline execution: (number of tasks) x (estimated tokens per task) x (price per token) x (1.5 safety margin for retries). Get human approval if estimate exceeds threshold.
  - Use tiered model selection: cheap models for planning, task decomposition, and simple edits; expensive models only for complex code generation and nuanced review.
  - Implement "cost-aware scheduling": if the pipeline is over budget, reduce parallelism to slow the burn rate while preserving the ability to complete.
  - Maintain a cost anomaly detector: flag any single agent session that consumes more than 3x the average for its task type.

### 5.2 Latency and Timeout Handling

- **Description:** LLM API calls have variable latency. A call that usually takes 5 seconds can take 60 seconds or time out entirely due to provider-side load. Pipeline steps that assume consistent latency will either fail on slow calls or waste time with unnecessarily long timeouts.
- **Likelihood:** 2 (Medium) -- API latency variability is a known characteristic of LLM services.
- **Impact:** 2 (Medium) -- Individual timeouts are recoverable, but cascading timeouts can stall the pipeline.
- **Severity:** 4 (Medium)
- **Detection Method:**
  - Track p50, p95, and p99 latency for API calls over time.
  - Alert when the p95 exceeds 2x its rolling average.
  - Monitor for timeout rate increases.
- **Mitigation Strategy:**
  - Use adaptive timeouts: set the timeout to 3x the rolling p95 latency, not a fixed value.
  - Implement retry with exponential backoff for transient failures (HTTP 429, 503, timeout).
  - Design pipeline steps to be resumable: if a call times out, the next attempt should not redo completed work within the same step.
  - Have a fallback model provider: if the primary API is degraded, route requests to a secondary provider (accepting potential quality differences).
  - Set a maximum wall-clock time for the entire pipeline. If exceeded, pause and report status rather than continuing indefinitely.

### 5.3 Dependency on External Services

- **Description:** The pipeline depends on LLM APIs, git hosting, CI/CD systems, package registries, and potentially other services. An outage in any of these blocks the pipeline. The more external dependencies, the lower the pipeline's effective availability.
- **Likelihood:** 2 (Medium) -- Each service has its own availability SLA. Combined availability is the product of individual availabilities.
- **Impact:** 2 (Medium) -- Pipeline stalls until the service recovers.
- **Severity:** 4 (Medium)
- **Detection Method:**
  - Health checks for each external dependency before and during pipeline execution.
  - Track external service error rates. Alert if any service exceeds a 1% error rate.
- **Mitigation Strategy:**
  - Design for graceful degradation: if a non-critical service is down (e.g., code coverage reporting), continue the pipeline and backfill later.
  - Cache aggressively: cache file reads, dependency resolutions, and test results so that transient outages do not block already-computed work.
  - Maintain a "can proceed without" classification for each dependency. LLM API is critical (cannot proceed). Git hosting is critical (cannot proceed). Code formatting service is non-critical (can proceed and format later).
  - Implement circuit breakers per service: after N consecutive failures, stop retrying for a cooldown period and report the outage.

### 5.4 Human Attention Bottleneck at Approval Gates

- **Description:** Humans are the scarcest resource in the system. Every approval gate blocks the pipeline until a human acts. If the pipeline produces review requests faster than humans can process them, a queue builds. The pipeline either stalls (wasting agent capacity) or humans rubber-stamp approvals to clear the queue (negating the purpose of the gate).
- **Likelihood:** 3 (High) -- This is the most common operational bottleneck in human-in-the-loop systems.
- **Impact:** 2 (Medium) -- Throughput bottleneck. The pipeline's effective speed becomes the human's review speed.
- **Severity:** 6 (High)
- **Detection Method:**
  - Track time-in-queue for human review requests. Alert when the average exceeds 2 hours or any single request exceeds 8 hours.
  - Monitor approval rate: if it is above 95%, humans may be rubber-stamping.
  - Track the human's rejection quality: are rejected items being caught for real issues, or are rejections pro-forma?
- **Mitigation Strategy:**
  - Tier approval gates by risk level:
    - **Low risk** (formatting changes, documentation, test-only changes): no human gate, automated validation only.
    - **Medium risk** (new feature code that passes all automated checks): async human review, pipeline continues provisionally.
    - **High risk** (security-sensitive, API changes, database migrations): blocking human review required.
  - Provide concise, structured review summaries. Do not dump the full diff on the human. Show: what changed, why, what tests passed, what the AI reviewer found, and what specifically requires human judgment.
  - Batch related approvals: instead of 10 individual review requests for a single feature, present one consolidated review.
  - Set up SLA expectations: "reviews should be completed within 4 hours during business hours." Automate reminders.

### 5.5 Information Overload for Human Reviewers

- **Description:** The pipeline generates large volumes of artifacts: plans, code diffs, test results, review comments, iteration histories. A human reviewing a pipeline run faces hundreds of lines of output, most of which is routine. Important information (novel architectural decisions, security concerns, failed-and-recovered issues) is buried in noise.
- **Likelihood:** 3 (High) -- Agents are verbose by nature. Without curation, output volume overwhelms humans.
- **Impact:** 2 (Medium) -- Humans miss important issues because they are buried in routine output.
- **Severity:** 6 (High)
- **Detection Method:**
  - Track how long humans spend on reviews vs. the volume of material presented. If review time decreases as volume increases, humans are skimming.
  - Survey reviewers periodically: "Did you feel you had enough time and focus to review thoroughly?"
- **Mitigation Strategy:**
  - Implement a "reviewer digest": a structured summary of the pipeline run that highlights only items requiring human attention, with expandable detail for those who want it.
  - Classify every pipeline output by human-relevance:
    - **Must see:** Architectural decisions, security findings, unresolved issues, budget anomalies.
    - **Should see:** Significant code changes, test coverage metrics, performance benchmarks.
    - **Can skip:** Routine code formatting, passing test details, iteration logs.
  - Use progressive disclosure in the review interface: summary first, drill down on demand.
  - Limit the number of items surfaced per review session to prevent decision fatigue. If there are 20 items needing attention, present the top 5 highest-priority ones first.

---

## 6. Mitigation Strategies (Cross-Cutting)

These strategies apply across multiple risk categories and form the backbone of a resilient pipeline.

### 6.1 Circuit Breakers and Kill Switches

- **Purpose:** Prevent runaway processes from consuming unbounded resources or producing unbounded damage.
- **Implementation:**
  - **Token circuit breaker:** If a single agent session exceeds its token budget, terminate the session immediately and preserve its output so far.
  - **Iteration circuit breaker:** If a task exceeds its maximum iteration count, halt and escalate. Do not retry automatically.
  - **Cost circuit breaker:** If the daily or pipeline cost budget is exceeded, pause all agents and require human approval to continue.
  - **Time circuit breaker:** If the pipeline exceeds its maximum wall-clock time, checkpoint state and halt.
  - **Emergency kill switch:** A single command (CLI, API, or dashboard button) that immediately halts all running agents, preserves state, and sends a notification. This must work even if the harness itself is misbehaving.
- **Testing:** Circuit breakers must be tested regularly. An untested circuit breaker is worse than no circuit breaker because it creates false confidence. Schedule monthly "chaos exercises" that deliberately trigger each breaker.

### 6.2 Cost Budgets and Usage Monitoring

- **Purpose:** Make costs visible, predictable, and controllable.
- **Implementation:**
  - Pre-pipeline cost estimate based on task count, estimated complexity, and model pricing.
  - Real-time cost accumulator updated after every API call.
  - Three-tier alerting: informational (50% consumed), warning (75%), critical (90%).
  - Historical cost database for trend analysis and budget forecasting.
  - Per-task cost attribution so that expensive tasks can be identified and optimized.
- **Governance:** Weekly cost review comparing actual vs. budgeted. Any task type that consistently exceeds its budget by more than 50% should be investigated and the estimation model updated.

### 6.3 Canary Deployments for Agent Changes

- **Purpose:** Reduce the blast radius of changes to prompts, model versions, or pipeline configuration.
- **Implementation:**
  - When changing a system prompt, model version, or pipeline parameter, first run the new configuration on a small sample of representative tasks (3-5 tasks across different difficulty levels).
  - Compare the canary run's metrics (cost, quality, iteration count, success rate) against the baseline.
  - Only roll out the change broadly if canary metrics are within acceptable bounds.
  - Maintain a rollback capability: the previous configuration should be restorable within minutes.
- **Specific canary criteria:**
  - Cost per task within 20% of baseline.
  - First-pass review approval rate within 10% of baseline.
  - Test pass rate equal to or better than baseline.
  - No new failure modes observed.

### 6.4 Fallback Strategies When Agents Fail

- **Purpose:** Ensure the pipeline does not hard-stop on any single failure.
- **Fallback hierarchy (in order of preference):**
  1. **Retry with same agent and model.** Appropriate for transient errors (API timeouts, rate limits).
  2. **Retry with fresh agent.** Appropriate when the agent's context is corrupted or it has entered a loop.
  3. **Retry with different model.** Appropriate when the primary model is producing systematically bad output for a particular task type.
  4. **Simplify the task.** Break the failing task into smaller subtasks and retry each independently.
  5. **Escalate to human.** Provide the human with: the original task spec, all attempted solutions, all error messages, and a summary of what was tried. The human resolves the task and the pipeline continues.
  6. **Skip the task.** If the task is non-blocking and non-critical, mark it as deferred, continue the pipeline, and create a follow-up task for manual resolution.
- **Each fallback level should have a clear trigger condition and a maximum number of attempts before escalating to the next level.**

---

## 7. Success Metrics and Monitoring

### 7.1 Key Performance Indicators

| Metric | What It Measures | Target | Red Flag |
|--------|-----------------|--------|----------|
| **Task success rate** | % of tasks completed without human intervention | >85% | <70% |
| **First-pass review approval rate** | % of code that passes review on first submission | >60% | <40% |
| **Average iterations per task** | Number of review-fix cycles before completion | <2.5 | >4 |
| **Cost per task** | Average token cost per completed task | Project-specific | >2x baseline |
| **Pipeline throughput** | Tasks completed per hour | Project-specific | <50% of target |
| **Integration success rate** | % of parallel merges that pass integration tests | >90% | <75% |
| **Human gate latency** | Average time tasks spend waiting for human review | <4 hours | >8 hours |
| **Regression rate** | % of completed tasks that introduce a regression caught later | <5% | >15% |
| **Mutation test kill rate** | % of artificial mutations caught by the test suite | >70% | <50% |
| **Agent context utilization** | Average % of context window used per session | <70% | >85% |

### 7.2 Leading Indicators of Problems

These metrics predict problems before they fully materialize:

- **Rising average iteration count.** If the trailing 10-task average of iterations is increasing, something is degrading: the codebase is getting harder to work with, the prompts are becoming less effective, or the tasks are becoming more complex than the agents can handle.
- **Increasing cost variance.** If the standard deviation of cost-per-task is growing, the system's behavior is becoming less predictable. Investigate outliers.
- **Decreasing first-pass approval rate.** The reviewer is finding more issues on first submission, meaning the executor's output quality is declining.
- **Growing human gate queue.** Humans are falling behind on reviews. Either reduce the number of gates, increase reviewer capacity, or reduce pipeline throughput to match.
- **Increasing context utilization.** Agents are using more of their context window, approaching exhaustion. Tasks may need to be decomposed differently, or context management needs improvement.

### 7.3 Dashboard Design for Operators

The operator dashboard should provide three views:

**1. Pipeline Overview (the "war room" view)**
- Current pipeline status: running / paused / completed / failed.
- Task board showing all tasks and their states (pending, in progress, review, testing, complete, failed).
- Active agent count and their current activities.
- Budget consumption: spent / remaining / estimated total.
- Wall-clock elapsed time vs. estimated total time.

**2. Health Metrics (the "vital signs" view)**
- Real-time charts of the KPIs from section 7.1.
- Trend lines showing the leading indicators from section 7.2.
- Current alert status and any active incidents.
- External service health (API availability, CI/CD status).

**3. Task Detail (the "microscope" view)**
- Per-task drill-down: iteration history, agent conversations, code diffs, test results, review comments.
- Ability to view the agent's reasoning at each step.
- Ability to manually intervene: approve, reject, reassign, or cancel any task.
- Cost breakdown for the individual task.

### 7.4 Alerting and Escalation Policies

| Alert Level | Trigger | Response | Notification Channel |
|------------|---------|----------|---------------------|
| **Info** | Budget 50% consumed; task iteration count reaches 2 | Log only | Dashboard |
| **Warning** | Budget 75% consumed; task iteration count reaches 3; agent context >80%; human gate queue >3 items | Notify operator | Dashboard + Slack/email |
| **Critical** | Budget 90% consumed; task fails after all retries; pipeline stalled >30 min; security finding in generated code | Page operator; pause pipeline | Dashboard + Slack + PagerDuty |
| **Emergency** | Budget exceeded; data loss detected; agent producing destructive operations (force push, DROP TABLE) | Kill all agents; freeze state | All channels + phone |

**Escalation timeline:**
- Warning unacknowledged after 30 minutes: escalate to Critical.
- Critical unacknowledged after 15 minutes: escalate to Emergency.
- Emergency unacknowledged after 5 minutes: auto-kill all agents and send incident report to all stakeholders.

### 7.5 Post-Mortem Process for Pipeline Failures

Every pipeline failure (severity Critical or Emergency) should trigger a post-mortem:

1. **Within 1 hour:** Capture the full pipeline state, agent logs, and metrics at time of failure. This data decays quickly as context is lost.
2. **Within 24 hours:** Write a preliminary incident report covering: what happened, what the impact was, how it was detected, how it was resolved.
3. **Within 1 week:** Complete the post-mortem document with root cause analysis, contributing factors, timeline, and corrective actions.
4. **Post-mortem template:**
   - **Summary:** One-paragraph description.
   - **Timeline:** Minute-by-minute reconstruction.
   - **Root cause:** The fundamental reason, not just the proximate trigger.
   - **Contributing factors:** What made this worse than it should have been.
   - **What went well:** Detection, response, or recovery actions that worked.
   - **What went poorly:** Gaps in detection, response, or recovery.
   - **Action items:** Specific, assigned, time-bounded changes to prevent recurrence.
5. **Action items from post-mortems feed back into:** updated prompts, new circuit breaker thresholds, new monitoring rules, updated documentation, and improved task decomposition heuristics.

---

## 8. Anti-Patterns to Avoid

### 8.1 The "Autonomous Autopilot" Anti-Pattern

- **What it is:** Running the pipeline fully autonomously with no human gates, trusting that agents will self-correct.
- **Why it is tempting:** Maximum throughput. Humans are slow. The system "should" work end-to-end.
- **Why it fails:** Agents have systematic blind spots. Without human oversight, these blind spots compound across tasks. A single hallucinated architectural decision can propagate through the entire pipeline, producing a large volume of internally consistent but fundamentally wrong code.
- **What to do instead:** Start with human gates at every stage. Remove gates only when you have measured data showing that a particular gate catches issues less than 5% of the time. Even then, keep the gate but make it async (non-blocking).

### 8.2 The "Infinite Retry" Anti-Pattern

- **What it is:** Retrying failed tasks indefinitely, hoping that the agent will eventually produce correct output.
- **Why it is tempting:** "It almost worked last time." Each iteration feels like it is getting closer.
- **Why it fails:** If the first 3 attempts failed, the 4th attempt is rarely better. The agent is usually stuck in a local optimum or oscillating between two bad states. Further retries consume tokens without improving outcomes.
- **What to do instead:** Hard cap at 3 iterations. After the cap, change strategy: different prompt, different model, smaller task scope, or human intervention. Never retry the same approach more than 3 times.

### 8.3 The "Kitchen Sink Context" Anti-Pattern

- **What it is:** Loading the agent's context with every piece of information that might be relevant: the entire codebase, all documentation, the full git history, all test results, and the complete conversation history from all previous tasks.
- **Why it is tempting:** "More information is better." The agent "might need" any of it.
- **Why it fails:** Context windows are finite. Irrelevant information displaces relevant information. The agent's attention is degraded by noise, producing worse output than a focused prompt with less information. This is a well-documented phenomenon in retrieval-augmented generation.
- **What to do instead:** Curate context aggressively. Include only: the task specification, the specific files being modified, the interfaces of adjacent modules, and the relevant test files. Let the agent request additional context as needed via tool calls.

### 8.4 The "One Model to Rule Them All" Anti-Pattern

- **What it is:** Using the most powerful (and most expensive) model for every task, regardless of complexity.
- **Why it is tempting:** "Better model = better results." Using the best model is the "safe" choice.
- **Why it fails:** Most tasks in a pipeline are routine: formatting, simple refactors, running tests, generating boilerplate. Using a frontier model for these tasks is wasteful. Moreover, cheaper models often perform equally well on simple tasks and faster too.
- **What to do instead:** Classify tasks by complexity and assign models accordingly. Use frontier models for planning, complex code generation, and nuanced review. Use cheaper/faster models for mechanical tasks. Measure quality per model per task type to calibrate this allocation.

### 8.5 The "Ship It and Forget It" Anti-Pattern (Static Harnesses)

- **What it is:** Building the harness once, deploying it, and not iterating on it as models improve, costs change, and the team learns what works.
- **Why it is tempting:** The harness "works." Changing it risks breaking things. The team is busy with other priorities.
- **Why it fails:** LLM capabilities change rapidly. A prompt that was necessary for GPT-4-level models may be unnecessary (or even counterproductive) for newer models. Cost structures change. The codebase evolves. A static harness accumulates technical debt like any other software.
- **What to do instead:** Build "rippable" systems: every component of the harness should be replaceable without rewriting the whole system. Schedule quarterly reviews of every prompt, every model assignment, and every threshold. Track which guardrails are still catching issues and which have become dead weight.

### 8.6 The "Premature Victory Declaration" Anti-Pattern

- **What it is:** Accepting an agent's claim that a task is "complete" without independent verification.
- **Why it is tempting:** The agent says it is done. The tests pass (or so it claims). Verifying takes time.
- **Why it fails:** Agents are trained to be helpful and confident. They will declare completion even when the work is partial, the tests are superficial, or edge cases are unhandled. This is one of the most consistently reported failure modes in agent systems.
- **What to do instead:** Define "done" objectively: tests pass (verified by the harness, not self-reported by the agent), review passes (by a separate agent or human), the diff matches the specification, and integration tests pass on the merged result. Never trust the agent's self-assessment as the sole signal of completion.

### 8.7 The "Everything in Parallel" Anti-Pattern

- **What it is:** Maximizing parallelism on the assumption that more agents = more throughput.
- **Why it is tempting:** Parallel execution is faster in theory. Agents are cheap compared to human developers. Why not run 20 agents at once?
- **Why it fails:** Coordination overhead grows non-linearly with parallelism. With 20 parallel agents, merge conflicts are near-certain, context duplication is massive, and integration testing becomes a bottleneck. Beyond a point, adding more agents slows the pipeline down because every agent's merge requires conflict resolution and re-testing.
- **What to do instead:** Limit parallelism to the number of truly independent tasks. For a typical feature, this is 2-5, not 20. Measure throughput as a function of parallelism and find the empirical sweet spot for your codebase. Start low and increase gradually.

### 8.8 The "Agent Knows Best" Anti-Pattern

- **What it is:** Treating agent output as authoritative without maintaining human-legible documentation of why decisions were made.
- **Why it is tempting:** The agent produced working code. Why document the reasoning? The code speaks for itself.
- **Why it fails:** When something goes wrong later, no one (human or agent) knows why the code was written that way. Was it a deliberate design choice or a hallucination that happened to work? Without reasoning artifacts, debugging is archaeology.
- **What to do instead:** Require the agent to produce a brief decision log for every non-trivial choice: what alternatives were considered, why this approach was selected, what tradeoffs were accepted. Store these logs alongside the code (as PR descriptions, commit messages, or ADR-style documents) so they are available to future agents and humans.

---

## 9. Summary Risk Register

A consolidated view of all risks for executive reporting and prioritization.

| ID | Risk | Likelihood | Impact | Severity | Primary Mitigation |
|----|------|-----------|--------|----------|--------------------|
| 2.1 | Infinite correction loops | 3 | 3 | **9** | Hard iteration cap + strategy switching |
| 2.2 | Context window exhaustion | 3 | 2 | **6** | Context checkpointing + external memory |
| 2.3 | Token cost explosion | 3 | 2 | **6** | Per-task budgets + tiered model selection |
| 2.4 | Planning hallucination | 2 | 3 | **6** | Grounded planning + validation pass |
| 2.5 | Wrong task granularity | 2 | 2 | **4** | Target task size guidelines + feedback loop |
| 2.6 | Context-dependent code | 2 | 2 | **4** | Integration tests on real environment |
| 3.1 | Pipeline state corruption | 2 | 3 | **6** | Transactional state + reconciliation |
| 3.2 | Race conditions | 3 | 2 | **6** | File-level locking + feature branches |
| 3.3 | Stale state after delays | 2 | 2 | **4** | Freshness checks on resume |
| 3.4 | Partial failure recovery | 2 | 3 | **6** | Idempotent tasks + rollback points |
| 3.5 | Git merge conflicts | 3 | 2 | **6** | Module-boundary decomposition + integration agent |
| 4.1 | Echo chamber reviews | 3 | 2 | **6** | Adversarial review prompts + canary bugs |
| 4.2 | Test theater | 3 | 3 | **9** | Mutation testing as pipeline gate |
| 4.3 | Integration regressions | 2 | 3 | **6** | Full test suite on merged result |
| 4.4 | Code quality degradation | 3 | 2 | **6** | Periodic consolidation + architecture docs |
| 4.5 | Security vulnerabilities | 2 | 3 | **6** | SAST gates + banned pattern checking |
| 5.1 | Cost overruns | 3 | 2 | **6** | Three-tier budgets + real-time tracking |
| 5.2 | Latency and timeouts | 2 | 2 | **4** | Adaptive timeouts + retry with backoff |
| 5.3 | External service dependency | 2 | 2 | **4** | Graceful degradation + caching |
| 5.4 | Human attention bottleneck | 3 | 2 | **6** | Risk-tiered approval gates |
| 5.5 | Information overload | 3 | 2 | **6** | Curated reviewer digests |

**Critical risks (Severity 9):** Infinite correction loops (2.1), Test theater (4.2). These two risks should be mitigated before the system enters production use.

**High risks (Severity 6):** 15 risks at this level. Prioritize by implementation effort, addressing low-effort mitigations first (budget limits, iteration caps, linting gates) before tackling structural changes (context management, task decomposition optimization).

**Medium risks (Severity 4):** 4 risks at this level. Address after high-severity risks are mitigated.

---

## Appendix A: Quick-Reference Mitigation Checklist

Use this checklist when setting up a new pipeline or auditing an existing one.

- [ ] Iteration caps configured for all review-fix loops (recommended: 3)
- [ ] Token budgets set at per-task, per-pipeline, and per-day levels
- [ ] Circuit breakers tested and verified functional
- [ ] Emergency kill switch accessible and tested
- [ ] Context checkpointing implemented for long-running tasks
- [ ] File-level locking or module-boundary decomposition in place for parallel execution
- [ ] Integration tests run on merged result of parallel branches
- [ ] SAST scanning enabled as a pipeline gate
- [ ] Mutation testing configured with minimum kill rate threshold
- [ ] Reviewer prompts use adversarial framing, not collaborative framing
- [ ] Human approval gates tiered by risk level
- [ ] Reviewer digest format defined and implemented
- [ ] Real-time cost dashboard operational
- [ ] Alerting and escalation policies configured and tested
- [ ] Post-mortem process documented and team trained on it
- [ ] Canary deployment process defined for prompt/model changes
- [ ] Recovery procedures documented for each failure class
- [ ] Pipeline state stored transactionally with reconciliation on restart
- [ ] Architecture documentation and coding standards accessible to all agents
- [ ] Decision logs required for non-trivial agent choices

## Appendix B: Recommended Iteration Thresholds

These thresholds are starting points. Tune them based on observed data from your specific pipeline.

| Parameter | Recommended Default | Tune Based On |
|-----------|-------------------|---------------|
| Max review-fix iterations per task | 3 | Success rate at each iteration count |
| Max parallel agents | 3-5 | Merge conflict rate and throughput curve |
| Context checkpoint interval | Every 2 iterations or 50% context usage | Context exhaustion frequency |
| Token budget safety margin | 1.5x estimated | Historical estimate accuracy |
| Stale state threshold | 4 hours | Codebase change velocity |
| Human gate SLA | 4 hours during business hours | Queue depth trends |
| Canary sample size | 3-5 representative tasks | Variance in task outcomes |
| Mutation test kill rate minimum | 70% | Historical regression rate |
| Cost anomaly threshold | 3x average for task type | Cost distribution shape |
| Emergency kill switch test frequency | Monthly | Incident frequency |

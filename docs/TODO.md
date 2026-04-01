# xpatcher -- Deferred Features and Known Gaps

## Deferred Features

### Intent Source Integrations
- GitHub Issues integration (fetch issue by URL/number)
- Jira integration (fetch ticket by key)
- Linear integration (fetch issue by ID)
- Slack thread ingestion (extract requirements from conversation)
- **Why deferred:** Currently accepts text input only. Users paste content manually.

### Notification Channels
- Slack webhook notifications for pipeline events
- Telegram bot for approvals and status
- Generic webhook adapter for custom integrations
- Interactive approvals via chat (approve/reject buttons)
- **Why deferred:** Uses interactive TUI. Notifications require external service setup.

### Cost Management
- Per-task, per-pipeline, and per-day token budgets
- Real-time cost dashboard
- Cost-per-line-of-code and cost-per-test metrics
- Alerting at 50%/75%/90% thresholds
- **Why deferred:** Adds complexity without blocking core pipeline value.

### Agent Teams Migration
- Replace dispatcher-managed parallelism with Claude Code Agent Teams
- Shared task list, inter-agent messaging
- **Why deferred:** Agent Teams is experimental. Dispatcher provides superior control.

### MCP Server for Pipeline State
- Expose pipeline state via MCP tools for richer agent querying
- Replace file-based polling with tool calls
- **Why deferred:** File-based approach is simpler and debuggable.

### Learning from Outcomes
- Store patterns from successful/failed pipelines in agent memory
- Planner improves over time based on historical data
- **Why deferred:** Requires significant data collection before patterns emerge.

### File-Level Resource Locking
- Advisory or enforced locks when parallel tasks touch shared files
- Conflict detection and resolution mechanisms
- **Why deferred:** Needs separate design discussion. Single-branch strategy reduces need.

### Multiple Concurrent Features
- Run multiple feature pipelines simultaneously
- Cross-feature conflict detection and resolution
- **Why deferred:** Adds merge complexity. Sequential features are simpler.

## Known Gaps

### Not Yet Implemented

#### Pipeline execution
- [ ] Parallel task execution with git worktrees (`AgentPool.execute_parallel()` exists but is not wired; tasks run sequentially)
- [ ] Async dispatcher with streaming (currently synchronous `subprocess.run()`)
- [ ] Full mid-pipeline resume (only supports paused human gates; execution-stage recovery requires manual intervention)
- [ ] Cancel does not interrupt a running dispatcher (updates persisted state but the process continues until the next transition attempt)
- [x] Gap re-entry state transition crash — fixed: gap re-entry now transitions to `blocked` when execution fails instead of leaving the pipeline in an intermediate state
- [x] Planner empty `command` fields causing false `stuck` states — fixed: missing commands are tracked but no longer counted as acceptance failures

#### Agents not wired
- [ ] Simplifier agent: schema (`SimplificationOutput`) and agent definition exist, but not invoked in the quality loop
- [ ] Tester agent: schema (`TestOutput`) exists, but dispatcher runs acceptance commands directly via `subprocess`; the tester agent is not invoked

#### Quality and testing
- [ ] Browser/Playwright e2e testing integration (acceptance criteria only)
- [ ] Mutation testing (defined in `config.yaml` quality tiers but not enforced by dispatcher)
- [ ] Quality tier enforcement (tiers defined in config but tier selection in task specs is not validated by dispatcher)

#### Session and context management
- [ ] Context compaction (session registry tracks token estimates but does not trigger compaction)
- [ ] Session memory (`SessionMemory` in `src/context/memory.py` exists but is not used by the pipeline)
- [ ] Retry with backoff (`retry_with_backoff()` in `src/dispatcher/retry.py` exists but is not wired into agent invocations)

#### Cost and budget
- [ ] Cost budget enforcement (cost is tracked and displayed but not enforced; no circuit breakers)
- [ ] Concurrency limits (`max_parallel_agents` defined in config but not enforced)

#### UI and hooks
- [ ] Rich TUI (`TUIRenderer` uses plain `print()` with ANSI escapes; no Rich live panels or progress bars despite `rich` being a dependency)
- [ ] PostToolUse hook: only sketched, no full artifact capture implementation
- [ ] Lifecycle hooks: agent start/stop tracking not implemented

#### Unused code
- [ ] `ArtifactCollector` in `src/artifacts/collector.py` is a thin wrapper; `core.py` validates directly via `ArtifactValidator`

### Technical Debt
- [ ] Pydantic schemas use camelCase in some places (JSON legacy) -- convert to snake_case for YAML consistency
- [ ] Some skill definitions still reference `.xpatcher/current-plan.json` patterns
- [ ] `install.sh` needs testing across macOS, Linux, WSL
- [x] ~~Gap re-entry state transition crash~~ (fixed: transitions to `blocked` with `gap_execution_failed` gate reason)
- [x] ~~Planner empty `command` fields causing false `stuck` states~~ (fixed: missing commands no longer counted as failures)

# xpatcher Design Specification -- Implementability Review

**Date:** 2026-03-29
**Reviewer Role:** Senior Python Developer (implementation readiness)
**Documents Reviewed:** All 17 design documents (01-17) + consolidated review (00)
**Spec Version:** 1.2 (Final Draft, 2026-03-28) + Missing Components (2026-03-29)

---

## VERDICT: Ready to Implement (with caveats)

The specification is remarkably thorough for a design document. The Pydantic models, class definitions, and code samples are substantially above the quality bar I see in most specs. A competent Python developer can start Phase 1 from this spec alone with approximately 2-3 days of clarification-free implementation before hitting the first real ambiguity.

**However**, there are implementation risks and code issues that will cause friction. None are blocking, but several will add 30-50% to estimated timelines if not addressed upfront.

---

## 1. CODE ISSUES (Bugs and Anti-Patterns in Spec Code)

### 1.1 CRITICAL: Appendix A schemas contradict Section 9 canonical schemas

The Appendix (Document 12) contains a completely separate set of Pydantic models that conflict with the Section 9 canonical models in Document 09. Despite the note saying "Section 9 wins," an implementor who reads linearly will encounter Appendix A first or reach it via cross-references and may use the wrong definitions.

**Specific conflicts:**

| Field | Appendix A (Doc 12) | Section 9 (Doc 09) | Impact |
|-------|--------------------|--------------------|--------|
| `PlanPhaseTask.id` pattern | `^task-\d+\.\d+$` (e.g. `task-1.1`) | `^task-[A-Z]?\d{3}$` (e.g. `task-001`) | Every plan YAML will fail validation if the wrong pattern is used |
| `ReviewFinding.severity` | `Literal["critical", "warning", "suggestion", "note"]` | `ReviewSeverity` enum: `critical, major, minor, nit` | Review finding severities are completely different |
| `ReviewFinding.category` | `Literal["security", "performance", "correctness", "style", "architecture"]` (5 values) | `ReviewCategory` enum: 7 values (adds `completeness`, `testability`) | Category filtering logic will miss 2 categories |
| `ExecutionOutput.files_modified` | `list[FileModification]` with field name `files_modified` | `list[FileChange]` with field name `files_changed` | Field name mismatch breaks deserialization |
| `Severity` enum | `INFO, WARNING, CRITICAL` (uppercase) | `ConcernSeverity`: `info, warning, critical` (lowercase) | Case-sensitive string comparison failures |
| `ReviewOutput.verdict` | `Literal["approve", "request_changes", "reject"]` | Same | Consistent (one of few) |

**Recommendation:** Remove or clearly mark Appendix A as DEPRECATED. Or better: delete it from the spec entirely since it causes nothing but confusion.

### 1.2 HIGH: `PipelineStateFile.read()` is not thread-safe despite the claim

```python
def read(self) -> dict:
    """Read current state. Lock-free (reads are atomic on YAML-sized files)."""
    with open(self.path) as f:
        return yaml.safe_load(f) or {}
```

The comment "reads are atomic on YAML-sized files" is incorrect. POSIX does not guarantee atomic reads of any size. If a write is in progress (temp file created but `os.rename` not yet called), `read()` returns stale data -- which is acceptable. But if the process crashes between `os.fdopen(fd, "w")` and `os.rename`, the read will see the old file (good), but the temp file is leaked (minor). The real issue: `read()` is called from `update()` without holding the lock yet (it acquires the lock, then calls `self.read()` inside the lock). But `read()` is also callable externally without a lock. If an external status query calls `read()` concurrently with an `update()`, the reader may see the pre-update state -- which is fine for eventual consistency, but the code comment overpromises.

**Fix:** The `ValidatedPipelineStateFile` in Document 13 properly wraps both read and write with a lock. Use that as the canonical implementation and demote the original `PipelineStateFile` to an internal detail.

### 1.3 HIGH: `PipelineStateFile.update()` duplicates write logic

The `update()` method in Section 2.3.2 contains a full copy of the atomic write logic instead of calling `self.write()`:

```python
def update(self, **fields) -> dict:
    with self._lock:
        state = self.read()
        state.update(fields)
        # ... duplicated tempfile + rename logic ...
```

This violates DRY and means any future change to the write mechanism (e.g., adding fsync, adding validation) must be applied in two places.

**Fix:** `update()` should call `self.write(state)` internally. The lock acquisition needs restructuring to avoid double-locking (since `write()` also acquires the lock). Use a private `_write_unlocked()` method.

### 1.4 MEDIUM: `ArtifactVersioner.latest_version()` uses lexicographic sort, not numeric

```python
existing = sorted(glob.glob(pattern))  # Lexicographic sort
latest = existing[-1]
```

`sorted(glob.glob(...))` sorts lexicographically. This means `plan-v9.yaml` sorts AFTER `plan-v10.yaml` (because "9" > "1" character-by-character). For version numbers >= 10, this returns the wrong "latest" version.

**Fix:** Sort by extracted version number:

```python
existing = sorted(glob.glob(pattern), key=lambda f: int(os.path.basename(f).split("-v")[-1].replace(".yaml", "")))
```

### 1.5 MEDIUM: `_extract_yaml` in `ClaudeSession` differs from `ArtifactValidator._extract_yaml`

Two separate YAML extraction implementations exist: one in `ClaudeSession.invoke()` (Section 7.7) and one in `ArtifactValidator` (also Section 7.7, further down). They use different strategies and handle edge cases differently. The `ClaudeSession` version lacks `_try_strip_prose`, while the `ArtifactValidator` version has four strategies.

**Fix:** Use only the `ArtifactValidator._extract_yaml()`. Remove the extraction from `ClaudeSession.invoke()` and have it return raw text. The validator pipeline should own all extraction.

### 1.6 MEDIUM: `subprocess.run` with `capture_output=True` and large agent outputs

```python
result = subprocess.run(
    cmd, capture_output=True, text=True,
    timeout=invocation.timeout, cwd=str(self.project_dir)
)
```

`capture_output=True` buffers the entire stdout and stderr in memory. Claude Code agents can produce substantial output (100K+ tokens of tool call traces when using `--output-format json`). For a 1M-context Opus session with full JSON output, this could be 10-50MB of text buffered in a Python string.

More critically, `subprocess.run` with `capture_output=True` uses `communicate()` internally, which reads both stdout and stderr to completion. If the agent hangs and produces no output, the timeout will fire correctly. But if the agent produces a continuous stream of output, the timeout still fires correctly (it measures wall-clock time). The real concern is memory pressure with many concurrent agents in v2.

**Mitigation for v1:** Not a problem with sequential execution and typical session sizes. For v2, consider streaming to a temp file and reading on completion.

### 1.7 MEDIUM: Signal handler uses `print()` which is not async-signal-safe

```python
def _graceful_shutdown(self):
    print("\n Warning: Shutting down gracefully...")
```

`print()` acquires a lock on `sys.stdout`. If the signal arrives while the main thread is already holding that lock (e.g., mid-print in the TUI), this deadlocks. Python's `signal.signal()` handlers run in the main thread between bytecodes, so this is unlikely but possible when Rich is rendering complex TUI layouts.

**Fix:** Set a flag in the signal handler and check it in the main loop. Do I/O only from the main loop, never from the handler. The `should_stop` property already exists for this purpose -- use it consistently.

### 1.8 LOW: `BASH_WRITE_PATTERNS` has a false positive on `|` (pipe)

```python
BASH_WRITE_PATTERNS = [
    r"[>|]",  # Redirect or pipe to file
    ...
]
```

The pattern `[>|]` matches ANY pipe character, including read-only pipes like `git log | head`. This means `git log --oneline | head -20` is blocked for read-only agents because `|` matches the write pattern. The check happens before the safe pipe target check (Check 3), but Check 2 fires first and blocks.

Actually, examining more carefully: Check 2 (write patterns) fires for all agents in `BASH_ALLOWLISTS`, not just `READ_ONLY_AGENTS`. The Check 3 (command chaining) only fires for `READ_ONLY_AGENTS`. So a reviewer running `git diff | head` would be blocked at Check 2, never reaching Check 3's safe pipe target logic.

**Fix:** Move the pipe character `|` out of `BASH_WRITE_PATTERNS` and handle it exclusively in Check 3's pipe parsing logic. Keep `>` (redirect) in the write patterns but handle `|` separately.

### 1.9 LOW: `datetime.utcnow()` is deprecated in Python 3.12+

Multiple code samples use `datetime.utcnow().isoformat() + "Z"`. This is deprecated as of Python 3.12 and emits a DeprecationWarning. Some samples in Document 13 use `datetime.now(timezone.utc).isoformat()` correctly, but the session registry and other code still uses the deprecated form.

**Fix:** Standardize on `datetime.now(timezone.utc).isoformat()` everywhere. Create a utility: `def utcnow_iso() -> str: return datetime.now(timezone.utc).isoformat()`.

---

## 2. PYDANTIC MODEL CONSISTENCY

### 2.1 Model inventory across all documents

I identified 18+ Pydantic models across 4 documents. Here is the complete registry:

| Model | Defined In | Registry Key | Agent Output? |
|-------|-----------|--------------|---------------|
| `ArtifactBase` | Doc 09 (Section 9) | -- (base class) | -- |
| `PlanOutput` | Doc 09 | `"plan"` | Yes (planner) |
| `ExecutionOutput` | Doc 09 | `"execution_result"` | Yes (executor) |
| `ReviewOutput` | Doc 09 | `"review"` | Yes (reviewer) |
| `TestOutput` | Doc 09 | `"test_result"` | Yes (tester) |
| `SimplificationOutput` | Doc 09 | `"simplification"` | Yes (simplifier) |
| `GapOutput` | Doc 09 | `"gap_report"` | Yes (gap-detector) |
| `DocsReportOutput` | Doc 09 | `"docs_report"` | Yes (tech-writer) |
| `IntentModel` | Doc 14 | `"intent"` | No (dispatcher) |
| `TaskModel` | Doc 14 | `"task"` | No (dispatcher) |
| `TaskManifestModel` | Doc 14 | `"task_manifest"` | No (dispatcher) |
| `ExecutionPlanModel` | Doc 14 | `"execution_plan"` | No (dispatcher) |
| `PipelineStateModel` | Doc 13 | -- (not in registry) | No (dispatcher) |
| `XpatcherConfig` | Doc 13 | -- (config, not artifact) | No |
| `PipelineStateFile` | Doc 02 | -- (I/O wrapper) | No |
| `ValidatedPipelineStateFile` | Doc 13 | -- (I/O wrapper) | No |
| **Appendix A duplicates** | Doc 12 | Same keys, different definitions | CONFLICT |

### 2.2 Consistency check results

**The models form a coherent system with two exceptions:**

1. **Appendix A duplicates** (see Issue 1.1 above) -- must be eliminated.

2. **`GapOutput.gaps` is `list[dict]` in Appendix A but `list[GapFinding]` in Section 9.** The Section 9 version is correct and uses a proper typed model. But `list[dict]` in the Appendix would pass validation for any dict, defeating schema enforcement.

3. **`iterations` field type in `PipelineStateModel`** is `dict[str, IterationTracker | dict[str, IterationTracker]]`. This is a union type that will be ambiguous during deserialization from YAML. Pydantic v2 handles discriminated unions well, but this is not a discriminated union -- it is an arbitrary nesting. The YAML example shows `plan_review` as a flat `IterationTracker` but `quality_loop` as a nested `dict[str, IterationTracker]`. This mixed-depth nesting will require a custom validator or a redesigned schema.

   **Fix:** Use separate fields: `plan_review_iterations: IterationTracker`, `task_review_iterations: dict[str, IterationTracker]`, `quality_iterations: dict[str, IterationTracker]`. This removes the union type ambiguity.

4. **`TaskModel` (Doc 14) vs `PlanPhaseTask` (Doc 09):** These represent the same concept (a task) at different pipeline stages. `PlanPhaseTask` is the planner's output; `TaskModel` is the dispatcher's enriched version. The field mapping is not documented. Which fields carry over? Is `TaskModel.acceptance_criteria` (a list of `AcceptanceCriterion` objects) derived from `PlanPhaseTask.acceptance` (a plain string)? The transformation logic is unspecified.

### 2.3 Missing validators

- `ReviewOutput` validates that `reject` needs findings, but does NOT validate that `request_changes` needs at least one `major` or `critical` finding. The semantic validator catches this as a warning (REV-VERDICT-001), but it should be an error in the Pydantic model itself.
- `ExecutionOutput` has no validator ensuring `status: "completed"` requires at least one entry in `files_changed`. An executor that claims completion but changed no files is likely a hallucination.
- `PlanOutput` has no cross-phase dependency validation. A task in phase-2 could depend on a task in phase-3, which is temporally impossible. This is caught by the semantic validator's cycle detection, but the ordering constraint (dependencies must point backward) is not explicit.

---

## 3. SUBPROCESS MANAGEMENT

### 3.1 `claude -p` invocation robustness

The `ClaudeSession.invoke()` method is the single most critical code path. Assessment:

| Aspect | Status | Notes |
|--------|--------|-------|
| Timeout handling | **Good** | `subprocess.run(timeout=...)` raises `subprocess.TimeoutExpired` |
| Exit code checking | **Missing** | `result.returncode` is captured in `AgentResult.exit_code` but never checked. A non-zero exit code from `claude -p` (e.g., auth failure, plugin load error) would produce garbled JSON, and the `json.loads(result.stdout)` would raise an unhandled `JSONDecodeError`. |
| Encoding | **Implicit** | `text=True` uses the system default encoding (UTF-8 on most systems). Fine for v1, but should be `encoding="utf-8"` explicitly for portability. |
| Large output buffering | **Acceptable for v1** | See Issue 1.6. Sequential execution limits total memory. |
| Stderr handling | **Missing** | `capture_output=True` captures stderr, but `result.stderr` is never examined. Agent errors, stack traces, and Claude CLI warnings go to stderr. These should be logged. |
| JSON envelope parsing | **Fragile** | `json.loads(result.stdout)` assumes the entire stdout is valid JSON. If Claude CLI prints warnings to stdout before the JSON (e.g., deprecation warnings), parsing fails. |
| `--plugin-dir` flag | **Uncertain** | Listed as open question OQ-2. If this flag does not exist, the entire plugin loading mechanism breaks. |
| `--agent` flag | **Uncertain** | Listed as open question OQ-1. Without this, agent selection must use `-p` with inline system prompts. |

**Implementation risk:** The two open questions (OQ-1, OQ-2) about Claude CLI flags are the single biggest implementation risk for Phase 1. If `--agent` and `--plugin-dir` do not exist, the invocation pattern must be redesigned to use `-p` with inline agent prompts and `--cwd` for the project directory. This would require rewriting the entire prompt assembly mechanism.

**Recommendation before Phase 1:** Verify these CLI flags exist by running `claude --help` and testing `claude -p "test" --agent explorer --plugin-dir ./test-plugin/`. If they do not exist, design the fallback immediately.

### 3.2 Missing: `TimeoutExpired` exception handling

The spec does not show how `subprocess.TimeoutExpired` is caught. When `subprocess.run()` times out, it raises an exception but does NOT kill the child process automatically in all cases. On Python 3.10+, the child is killed, but any grandchild processes (spawned by Claude Code internally) may survive.

**Fix:** Wrap the subprocess call in a try/except that:
1. Catches `TimeoutExpired`
2. Explicitly kills the process group (not just the process)
3. Records the timeout in pipeline state
4. Classifies as a transient error per the error taxonomy

---

## 4. CONCURRENCY

### 4.1 Threading analysis

For v1 (sequential execution), concurrency is limited to:
- Main thread: dispatch loop
- Signal handler: runs in main thread (Python constraint)
- Status queries: potentially from a separate terminal running `xpatcher status`

The `threading.Lock` in `PipelineStateFile` is sufficient for this. No deadlock risk because only one lock exists and all code paths acquire/release it without nesting.

### 4.2 v2 concerns (for awareness, not blocking)

- The merge lock in Section 2.6.1 and the `PipelineStateFile` lock are separate. If code ever needs both, it must acquire them in a consistent order or risk deadlock.
- The `SessionRegistry._save()` method writes to `sessions.yaml` without any locking. If two parallel agents complete simultaneously and both call `registry.register()`, the registry file could be corrupted. The session registry needs its own lock or should be integrated into the `PipelineStateFile`.
- File polling at 2-second intervals with 3+ concurrent agents means 3+ reads per 2 seconds of `pipeline-state.yaml`. Not a performance concern, but the file could be mid-write during a read. The atomic rename strategy handles this correctly (the read sees either the old or new file, never a partial write).

### 4.3 Race condition in cancellation

In `CancellationManager._update_task_states()`, the method reads task YAML files from `in-progress/` and moves them to `todo/`. If an agent is concurrently writing to one of these YAML files (via `_update_task_file_status`), the move could fail on some filesystems or the written data could be lost.

**Mitigation:** In v1, cancellation happens after agents are terminated (Step 2 in the sequence), so no agent is writing. This is safe. In v2, the SIGTERM/wait/SIGKILL sequence in `AgentTerminator` ensures agents are dead before task state updates.

---

## 5. DEPENDENCY MANAGEMENT

### 5.1 Declared dependencies

| Package | Used For | Version Constraint Needed? |
|---------|----------|---------------------------|
| `pydantic` | Schema validation | Yes: v2+ required (`model_dump`, `model_validator`, `field_validator` are v2 APIs) |
| `pyyaml` | YAML I/O | No: stable API |
| `rich` | TUI rendering | Yes: suggest >= 13.0 for `Live` display support |

### 5.2 Missing dependencies

| Missing Package | Needed For | Spec Reference |
|-----------------|-----------|----------------|
| None critical | -- | -- |

The dependency list is minimal and appropriate. The spec deliberately avoids external dependencies for the core dispatcher.

### 5.3 Implicit dependencies

- **`git` CLI**: Required for branching, diffing, committing. Version 2.25+ (for worktree support in v2). Doc 15 specifies this.
- **`claude` CLI**: Obviously required. Version compatibility is an open question.
- **`gh` CLI**: Optional, for PR creation. Correctly marked optional.
- **`yq` CLI**: Mentioned in examples but not required by the dispatcher code.

### 5.4 `pyproject.toml` not specified

The spec references `pyproject.toml` in the directory layout but never specifies its contents. This should define:
- Python version requirement (`requires-python = ">=3.10"`)
- Dependencies (`pydantic`, `pyyaml`, `rich`)
- Entry point (`[project.scripts] xpatcher = "src.dispatcher.core:main"`)
- Package structure

---

## 6. ERROR HANDLING

### 6.1 Error taxonomy implementability

The error taxonomy in Document 13 (Component 9) defines 16 error types across 3 categories (Transient, Permanent, User-Actionable). This is well-designed and implementable.

**One gap:** The `ErrorClassifier` class is referenced but its implementation is not shown in full. The classification logic (which exceptions map to which error types) needs to be specified. For example:
- `subprocess.TimeoutExpired` -> `AGENT_TIMEOUT` (transient)
- `json.JSONDecodeError` on claude output -> `MALFORMED_OUTPUT` (transient, retry in same session)
- `yaml.YAMLError` on artifact -> `MALFORMED_OUTPUT` (transient, retry)
- `pydantic.ValidationError` -> `SCHEMA_ERROR` (transient, retry with fix prompt)
- `git` command failure -> context-dependent (could be transient or permanent)

### 6.2 Unhandled error paths

1. **Claude CLI authentication failure**: If the API key is expired or rate-limited, `claude -p` returns a non-zero exit code with an error message on stderr. The spec never mentions API authentication errors. This should be classified as `USER_ACTIONABLE` with a clear message.

2. **Disk full during artifact write**: The atomic write pattern creates a temp file first. If the disk is full, `tempfile.mkstemp()` fails with `OSError`. The `except Exception` clause catches this and calls `os.unlink(tmp_path)`, but `tmp_path` may not have been created. The code handles this correctly (the `unlink` would fail silently), but there is no user-facing error message about disk space.

3. **Git repository in detached HEAD state**: `xpatcher start` creates a feature branch from the current branch. If the repo is in detached HEAD state, `git checkout -b` will work but the "base branch" detection will fail. The spec assumes the repo has a main/master branch.

4. **Missing `.xpatcher/` directory permissions**: If the project directory is read-only (e.g., mounted as read-only in Docker), the initial `.xpatcher/` directory creation fails. No error handling specified.

---

## 7. DRY-RUN: Phase 1 Implementation Walkthrough

### Phase 1 scope (from Doc 11):
1. `.claude-plugin/` directory with `plugin.json`
2. `explorer.md` agent
3. `/xpatcher:status` skill
4. `src/dispatcher/session.py` (ClaudeSession)
5. `src/dispatcher/state.py` (PipelineState)
6. `src/dispatcher/schemas.py` (Pydantic models)

### 7.1 `session.py` -- Can I build it from the spec?

**Yes, with modifications.**

The `ClaudeSession` class in Section 7.7 is nearly complete. I would need to:

1. Define `AgentInvocation` and `AgentResult` dataclasses (not defined anywhere in the spec). The `invoke()` method references `invocation.prompt`, `invocation.agent`, `invocation.session_id`, `invocation.max_turns`, `invocation.allowed_tools`, `invocation.timeout`, `invocation.bare_mode_off`. These fields are inferable but should be explicit.

2. Verify the Claude CLI flags (`--agent`, `--plugin-dir`, `--bare`, `--output-format json`). The `--bare` flag suppresses Claude Code's default system prompt. Need to confirm this exists.

3. Add exit code handling (currently missing).

4. Add stderr logging (currently missing).

5. Remove the `_extract_yaml()` method from `ClaudeSession` (use `ArtifactValidator` instead).

**Ambiguities:**
- What is `self.plugin_dir`? How is it set? (Inferred: constructor parameter)
- What is `self.project_dir`? How is it set? (Inferred: constructor parameter)
- Is `--output-format json` the correct flag name? Or is it `--output-format stream-json`? The spec uses both in different contexts (Section 7.1 uses `stream-json` for TUI streaming, Section 7.7 uses `json` for structured output).

**Estimated effort:** 1-2 hours for a clean implementation, plus 2-4 hours for CLI flag verification and edge case handling.

### 7.2 `state.py` -- Can I build it from the spec?

**Yes, straightforwardly.**

Three classes to implement:
1. `PipelineStateFile` (Section 2.3.2) -- atomic file I/O with locking
2. `PipelineStateModel` (Document 13, Component 7) -- Pydantic validation
3. `ValidatedPipelineStateFile` (Document 13) -- composition of both

The code is essentially provided. Implementation is mostly transcription + tests.

**Ambiguity:** The `PipelineStateFile.update()` method takes `**fields` as kwargs, which means it does a shallow dict merge. But `PipelineStateModel` has nested fields (e.g., `task_states`, `iterations`). Updating a nested field requires reading the current value, modifying it, and writing back. The simple `state.update(fields)` does not support deep merges.

**Example:** To update task-003's state from "ready" to "running", the caller must:
```python
state = state_file.read()
state["task_states"]["task-003"]["state"] = "running"
state_file.write(state)
```
Not: `state_file.update(task_states={"task-003": {"state": "running"}})` (this would REPLACE the entire task_states dict).

The `ValidatedPipelineStateFile.update()` has the same issue. `PipelineStateModel.update_and_save()` uses `updated_data.update(fields)` which is a shallow merge.

**Fix needed:** Either document that `update()` is only for top-level flat fields, or implement deep merge semantics for nested dicts. I would add a `update_task_state(task_id, **fields)` convenience method.

**Estimated effort:** 2-3 hours for clean implementation + tests.

### 7.3 `schemas.py` -- Can I build it from the spec?

**Yes, with careful source selection.**

The canonical schemas are in Section 9 (Document 09, lines 446-732). Additional models are in Document 14 (Component 11). The `SCHEMAS` registry in Document 14 shows the complete 11-type registry.

**What I would do:**
1. Copy the Section 9 models verbatim as the starting point.
2. Add the Document 14 models (`IntentModel`, `TaskModel`, `TaskManifestModel`, `ExecutionPlanModel`).
3. Add the shared enums and `TASK_ID_PATTERN` constant.
4. Merge the `SCHEMAS` registries.
5. Ignore Appendix A entirely.

**Ambiguity:** The Section 9 `SCHEMAS` registry has 7 entries. The Document 14 registry adds 4 more for a total of 11. But the Document 14 models (`IntentModel`, etc.) extend `ArtifactBase` and have `type` literals. However, they are "dispatcher-managed artifacts" -- the dispatcher writes them, not agents. Should they be in the same `SCHEMAS` registry used for agent output validation? The validator would never validate them against agent output, so including them in `SCHEMAS` is misleading. I would create a separate `DISPATCHER_SCHEMAS` registry.

**Estimated effort:** 3-4 hours (mostly careful transcription and deduplication).

### 7.4 Plugin files (`plugin.json`, `explorer.md`, status skill)

**Straightforward.** The spec provides complete contents for all three. Direct transcription.

**Estimated effort:** 30 minutes.

### 7.5 Phase 1 total estimate

| Component | Spec Clarity | Effort Estimate | Risk |
|-----------|-------------|-----------------|------|
| `session.py` | Good (need CLI flag verification) | 4-6 hours | HIGH (CLI flag uncertainty) |
| `state.py` | Excellent | 2-3 hours | LOW |
| `schemas.py` | Good (need deduplication) | 3-4 hours | LOW |
| Plugin files | Complete | 0.5 hours | LOW |
| **Integration + testing** | Moderate | 4-6 hours | MEDIUM |
| **Total Phase 1** | | **14-20 hours** | |

---

## 8. TESTING

### 8.1 Are the interfaces testable?

**Yes.** The code is well-structured for testing:

- `PipelineStateFile` is easily testable with `tempfile.NamedTemporaryFile`.
- `ArtifactValidator` has no side effects and takes string input, returns `ValidationResult`.
- `SemanticValidator` needs filesystem and git mocks but has a clean interface.
- `ClaudeSession` is the hardest to test because it calls `subprocess.run`. Would need to mock `subprocess.run` or use a fake `claude` command.

### 8.2 Gaps in testability

1. **No interface abstraction for `subprocess.run`**: The `ClaudeSession` class directly calls `subprocess.run`. For unit testing, this should be injected (e.g., a callable or a `SubprocessRunner` protocol).

2. **`SessionRegistry._save()` writes to disk**: The registry writes to a YAML file on every `register()` call. For unit tests, this should accept an in-memory store or a path override.

3. **`SemanticValidator` requires a real git repo**: The `_get_git_diff_files()` and `_get_git_log_hashes()` methods call `git diff` and `git log`. For unit tests, these should be injectable or mockable. The `_git_diff_cache` and `_git_log_cache` fields help (you can pre-populate them), but this is undocumented.

4. **No test fixtures defined for Phase 1**: Document 16 defines the test strategy and mentions golden fixtures, but no sample YAML fixtures are provided for any Pydantic model. I would need to create test fixtures for every model from scratch.

### 8.3 What I can test immediately

- All Pydantic models: valid data, invalid data, edge cases, enum values
- `ArtifactVersioner`: next_version, latest_version with various file layouts
- `PipelineStateFile`: read, write, update, atomic write behavior, concurrent access
- `ArtifactValidator`: extraction strategies (raw YAML, separator, code block, strip prose)
- `MalformedOutputRecovery`: retry logic, max attempts
- `SignalHandler`: flag setting (not the actual signal handling, which is hard to test)
- PreToolUse hook: policy enforcement for each agent type

---

## 9. MISSING SPECIFICATIONS (Things a developer must decide independently)

### 9.1 Unspecified but needed for Phase 1

| # | Missing Item | Impact | My Default Choice |
|---|-------------|--------|-------------------|
| 1 | `AgentInvocation` dataclass definition | Cannot build `session.py` without it | Define with fields visible in `invoke()` method |
| 2 | `AgentResult` dataclass definition | Cannot build `session.py` without it | Define with `session_id`, `raw_text`, `parsed`, `exit_code`, `usage` |
| 3 | CLI argument parsing (argparse/click) | Cannot build the `xpatcher` command | Use `argparse` (no extra dependency) |
| 4 | Logging framework choice | No structured logging defined | Use `logging` stdlib with JSONL formatter for agent logs, `rich.logging` for console |
| 5 | `pyproject.toml` contents | Cannot package or install | Define standard `[project]` section with deps |
| 6 | Config file loading (how `config.yaml` is found and loaded) | XpatcherConfig model exists but no loader | Implement resolution order from Doc 13 |
| 7 | Feature slug generation from user request | `xpatcher start "Add OAuth2 support"` -> `add-oauth2-support`? | Slugify with `re.sub(r'[^a-z0-9]+', '-', request.lower()).strip('-')[:50]` |
| 8 | Pipeline ID generation (`xp-YYYYMMDD-<short-hash>`) | How is the short hash generated? | `hashlib.sha256(feature_slug + timestamp).hexdigest()[:4]` |

### 9.2 Unspecified but needed for Phase 2

| # | Missing Item | Impact |
|---|-------------|--------|
| 9 | Prompt template system (how `<!-- At build time, the full schema is injected here -->` is implemented) | Critical for agent output reliability |
| 10 | Context bridge format (what exactly is passed between isolated sessions) | Critical for adversarial review isolation |
| 11 | How the dispatcher determines feature complexity (simple/medium/complex) for expert panel activation | Affects planning quality |
| 12 | Task file format (the actual YAML structure of `tasks/todo/task-001-*.yaml`) | `TaskModel` defines the schema but not the file layout |

---

## 10. IMPLEMENTATION RISKS (Things That Will Take Longer Than Expected)

### 10.1 HIGH RISK: Claude CLI flag compatibility

The entire architecture depends on CLI flags (`--agent`, `--plugin-dir`, `--bare`, `--output-format json`, `--resume`, `--max-turns`, `--allowedTools`) that are marked as open questions. If any of these do not exist or have different names, the session management and agent invocation code must be redesigned. This could add 1-2 weeks to Phase 1.

**Mitigation:** Day 1 of implementation should be a Claude CLI compatibility spike. Verify every flag.

### 10.2 HIGH RISK: YAML extraction reliability

LLMs do not reliably produce clean YAML. Despite explicit instructions to "start with --- on its own line" and "do NOT wrap in code blocks," agents will produce prose before/after YAML, use code fences, mix YAML with markdown, and occasionally produce invalid YAML syntax. The four extraction strategies in `ArtifactValidator` are a good start, but in practice, I expect the retry loop to fire on 15-30% of initial agent outputs.

**Mitigation:** Add a fifth extraction strategy: use an LLM call (Haiku, cheap) to extract the YAML from the raw output. This is a fallback of last resort but very effective.

### 10.3 MEDIUM RISK: TUI complexity

The Rich-based TUI with live progress panels, per-task timers, agent log streaming, keyboard-driven focus switching, and human gate prompts is a substantial UI project. The spec describes the visual output in detail but not the Rich API calls needed to implement it. `rich.live.Live`, `rich.table.Table`, `rich.panel.Panel`, `rich.progress.Progress` -- combining these correctly with asyncio subprocess streaming is non-trivial.

**Mitigation:** Phase 1 TUI should be minimal (print statements). Rich TUI is Phase 4. This is already in the roadmap.

### 10.4 MEDIUM RISK: State machine complexity

The pipeline has 16 stages + 5 meta states + per-task states with validated transitions. The transition table in Section 3.3 has 15+ entries plus the task-level transitions in Section 2.5 with 14 entries. Implementing validated transitions with proper error messages, rollback on invalid transitions, and persistence on every transition is straightforward but tedious. The risk is subtle bugs in transition validation that surface only during specific pipeline paths (e.g., gap re-entry after a skipped task).

**Mitigation:** Property-based testing (hypothesis) for state machine transitions. Generate random sequences of events and verify no invalid state is reachable.

### 10.5 LOW RISK: Two-document model problem

The spec is split across 17 documents with cross-references. This is good for organization but means an implementor must hold multiple documents in context simultaneously. For example, building the quality loop requires reading Sections 3.4, 6.1, 6.2, 6.4, 6.5.1, and the Pydantic models from Section 9 and Document 14. Tab fatigue is real.

**Mitigation:** Before implementation, create a single-file "implementor's cheat sheet" that flattens the key code samples and schemas into one reference document, organized by source file rather than by design section.

---

## 11. SUMMARY

### What's excellent
- Pydantic model system is well-designed with proper enums, validators, and a registry pattern
- File-based coordination with atomic writes is the right call for crash recovery
- Adversarial review isolation is architecturally enforced, not just prompt-based
- Error taxonomy is practical and implementable
- Signal handling is well thought out (two-tier SIGINT, state preservation)
- The spec provides runnable code, not pseudocode

### What needs fixing before implementation
1. Delete or deprecate Appendix A (Doc 12 Pydantic models) -- they conflict with Section 9
2. Verify Claude CLI flags exist (`--agent`, `--plugin-dir`, `--bare`) -- blocking risk
3. Define `AgentInvocation` and `AgentResult` dataclasses
4. Fix `ArtifactVersioner.latest_version()` lexicographic sort bug
5. Fix `PipelineStateFile.update()` DRY violation
6. Fix `BASH_WRITE_PATTERNS` pipe false positive
7. Standardize on `datetime.now(timezone.utc)` everywhere

### What can proceed as-is
- Phase 1 foundation (session.py, state.py, schemas.py) is buildable from spec
- Plugin structure and agent definitions are complete
- Quality loop flowchart is unambiguous
- DAG scheduler is well-specified
- Installation script is production-ready

---

*End of implementability review.*

# UX, TUI, Documentation & User Interaction Review

**Reviewer:** UX Engineer / Technical Writer perspective
**Date:** 2026-03-29
**Spec version:** 1.2 (Final Draft, merged with Addendum 01 + Addendum 02)
**Documents reviewed:** All 12 design subdocuments + master proposal

---

## Executive Assessment

The xpatcher specification describes a well-structured pipeline with thoughtful attention to transparency (elapsed timers, live progress, structured log files). The TUI mockups are detailed and the human gate prompt design is solid. However, the spec has significant gaps in the user-facing layer: an undocumented CLI command (`xpatcher skip`) appears in user output, there is no plan for user documentation or onboarding, the first-run experience has several pitfalls, and the TUI design makes terminal compatibility assumptions that need validation. The human gate at plan approval has no timeout and no remote notification mechanism, meaning an unattended pipeline blocks indefinitely with no way for the user to know. Overall, the internal machinery is well-specified, but the surface the user actually touches needs another design pass.

**Rating:** The pipeline internals are strong. The user-facing surface is 60-70% there. The issues below are fixable without architectural changes, but several are blocking for a usable v1.

---

## Strengths

1. **Transparent progress display.** The TUI mockup (Section 7.1) is one of the strongest parts of the spec. Persistent elapsed timers per stage, task-level detail during parallel execution, and a footer summary with active agents and token counts give the user genuine visibility. This is significantly better than a blank terminal with a spinner.

2. **Structured human gate prompts.** The plan approval prompt (Section 7.1) with numbered options, plan metadata, and a file path for inspection is well-designed. The YAML decision schema (Section 3.5) with labeled consequences per option is excellent for informed decision-making.

3. **Logs always written to disk.** Regardless of verbosity level, agent logs are captured to `.xpatcher/<feature>/logs/` as structured JSONL. This is the right call -- users should never lose debugging information because they forgot a flag.

4. **Layered verbosity.** Four distinct levels (default, `--verbose`, `--stream-logs`, `--quiet`) cover the range from "just tell me when it is done" to "show me everything." The table in Section 7.1 is clear and well-structured.

5. **Pipeline ID displayed prominently.** The spec explicitly calls out that `xp-20260328-a1b2` is shown at start with a copy-pasteable resume command (Section 2.7). This prevents the "how do I get back to this?" problem.

6. **File-based state is inspectable.** Users can always `cat .xpatcher/<feature>/pipeline-state.yaml` to see what is happening. This gives power users escape velocity when the TUI is insufficient.

7. **Preflight check on first run.** Auto-detection of project stack, tool validation, and `.gitignore` update (Section 11, Project Initialization) means the user does not need to manually configure anything per-project.

---

## Critical Issues

### C1. `xpatcher skip` is referenced but never defined

**Location:** Section 3.6 (Pipeline Flow), failure path output, line 4 of "What to do next."

The failure output tells the user:
```
4. Or skip stuck tasks: xpatcher skip task-005,008
```

But `xpatcher skip` does not appear anywhere in the CLI commands (Section 7.1), global flags, or any other section. A user following the failure output's instructions will get a "command not found" or "unknown command" error at exactly the moment they are already frustrated with a stuck pipeline.

**Impact:** A user hits a failure, follows the suggested fix, and gets a second failure. This is the worst possible UX at the worst possible moment.

**Recommendation:** Either (a) add `xpatcher skip <pipeline-id> <task-ids>` to the CLI commands in Section 7.1 with full documentation, or (b) remove the reference from the failure output and replace it with instructions to manually update task status. Option (a) is strongly preferred -- the skip command is genuinely useful.

### C2. Plan approval has no timeout and no notification

**Location:** Section 3.5 (Human Gate Design).

Hard gates "always block." Plan approval (Stage 5) and final completion (Stage 16) block indefinitely. There is no timeout, no notification mechanism, and no periodic reminder.

The spec does mention a YAML `defer` option with "Reminder in 24 hours," but this is in a YAML schema example only -- there is no implementation detail for how this reminder is delivered. The risk mitigation doc (Section 10) does not address this scenario. The user preferences note that v1 has no external notifications.

**Impact:** A 47-minute pipeline (per the happy path example) blocks at plan approval. If the user walks away, opens a different terminal, or closes their laptop lid, the pipeline is frozen until they manually check. On a shared CI/CD server (listed as an installation target in Section 2.3.1), this is a team-wide blocker.

**Recommendation:**
- Add a configurable soft timeout (default: 4 hours) for plan approval, after which the pipeline pauses and writes a clear state message.
- Add a `xpatcher pending` or `xpatcher gates` command that shows all pipelines waiting for human input.
- For v1 with no external notifications, at minimum print a terminal bell character (`\a`) when a gate is reached, so that terminal notification systems (iTerm2, tmux, etc.) can alert the user.
- Consider writing a file like `.xpatcher/<feature>/WAITING_FOR_APPROVAL` that tools like `watch` or shell prompts can detect.

### C3. No user documentation plan beyond a single roadmap line

**Location:** Section 11, Phase 5, Step 40.

Step 40 reads: "Write user-facing documentation. Test: New user can install and run pipeline from docs."

This is the entirety of the documentation plan. For a tool with 6+ CLI commands, 4 verbosity levels, 3 configuration layers, 16 pipeline stages, 10+ artifact types, 9 skills, and structured human gates, a single line is insufficient.

**Impact:** Without documentation, v1 adoption depends entirely on tribal knowledge. The spec itself is ~15,000 words of internal design rationale -- none of it is written for end users.

**Recommendation:** Add a documentation deliverables list to Phase 5 (or create a Phase 5.5). At minimum:
- Quick-start guide (install, first run, approve plan, see results)
- CLI reference (`xpatcher start|resume|status|list|cancel|logs|skip` with all flags)
- Configuration reference (config.yaml, .xpatcher.yaml, CLI flag precedence)
- Human gates guide (what each prompt means, how to make good decisions)
- Troubleshooting guide (stuck pipeline, failed tasks, how to inspect artifacts)
- Pipeline stages reference (what happens at each stage, what artifacts are produced)

---

## Major Issues

### M1. Tab key conflict for agent switching during parallel execution

**Location:** Section 7.1, Agent Log Streaming.

The spec says: "keyboard shortcut `Tab` to switch between parallel agents."

The `Tab` key is universally used for shell tab completion, readline completion, and terminal input. Capturing `Tab` inside a running TUI application (which the rich library supports) is technically possible but has consequences:

- If the TUI is running in "application mode" (alternate screen buffer), Tab can be captured safely, but the user loses the ability to use normal shell features.
- If the TUI is running inline (which the mockups suggest, since output accumulates), Tab cannot be reliably captured without entering raw input mode, which breaks Ctrl+C, arrow keys, and other expectations.
- In tmux, `Tab` is the default prefix key for many configurations.
- In VS Code's integrated terminal, `Tab` triggers IntelliSense suggestions.

**Recommendation:** Use a different key or key combination:
- `F1`-`F5` for switching between up to 5 parallel agents
- `` ` `` (backtick) which is rarely used in terminal interaction
- Number keys `1`, `2`, `3` to jump to specific agents
- Or make agent switching mouse-clickable via rich's click support

### M2. No Ctrl+C handling specification

**Location:** Not addressed anywhere in the spec.

The spec discusses pipeline resumption (Section 2.7) and crash recovery, but never specifies what happens when the user presses Ctrl+C. The behavior should differ by context:

| Context | Expected behavior |
|---------|------------------|
| During a human gate prompt | Cancel the prompt, return to pipeline (not kill it) |
| During agent execution | Graceful shutdown: kill current agents, save state, allow resume |
| During TUI display (no prompt) | Same as above -- graceful shutdown |
| Double Ctrl+C (rapid) | Force kill, best-effort state save |
| During `xpatcher logs` (read-only) | Immediate exit |

Without specifying this, the dispatcher may orphan Claude CLI subprocesses (which would continue consuming API tokens), leave state files in an inconsistent `in_progress` state, or lose the session ID needed for resume.

**Recommendation:** Add a "Signal Handling" section to the CLI specification:
- Single SIGINT: set a shutdown flag, wait for current agent turn to complete (up to 30s), save state, exit cleanly.
- Double SIGINT within 2 seconds: force-kill all child processes, write crash recovery state.
- SIGTERM: same as single SIGINT.
- Document the resume behavior after each type of interruption.

### M3. Pipeline ID requires lookup for most operations

**Location:** Section 7.1, Pipeline ID.

The pipeline ID format `xp-20260328-a1b2` is displayed at start and used everywhere. But the spec provides no shorthand for common cases:

- `xpatcher status` with no argument should show the most recent (or only active) pipeline. The spec says `xpatcher status [pipeline-id]` with brackets suggesting it is optional, but does not explain the default behavior.
- `xpatcher resume` with no argument should resume the most recent paused pipeline.
- `xpatcher logs` requires a pipeline ID, but the user may not remember it.
- There is no `xpatcher last` or `latest` alias.

The `xpatcher list` command exists but requires an extra step. Compare:
```bash
# Current spec (2 commands needed):
xpatcher list          # find the ID
xpatcher resume xp-20260328-a1b2  # use it

# Better UX:
xpatcher resume        # resumes most recent paused pipeline
xpatcher resume --latest  # explicit variant
```

**Recommendation:**
- Make `pipeline-id` optional for `resume`, `status`, `cancel`, and `logs` when there is exactly one active pipeline (or default to most recent).
- Add tab completion for pipeline IDs (bash/zsh completion script).
- Support partial ID matching: `xpatcher resume a1b2` matches `xp-20260328-a1b2`.

### M4. Configuration complexity is under-documented for users

**Location:** Section 2.3.1 (Resolution Order).

The four-layer config resolution (CLI flags > `.xpatcher.yaml` > `config.yaml` > defaults) is powerful but potentially confusing. The spec documents the resolution order but provides no guidance on:

- What settings exist in `config.yaml`? Only a partial `models:` block is shown (Section 4.10) and a partial `session_management:` block (Section 9). There is no complete config reference.
- When should a user use `.xpatcher.yaml` vs `config.yaml`? The distinction (project-specific vs global) is clear in concept but the spec provides only one 4-line example of `.xpatcher.yaml`.
- What CLI flags correspond to which config keys? `--model opus` appears in text (Section 2.3.1) but is not listed in the global flags table (Section 7.1).
- Can conflicting settings cause errors? What if `.xpatcher.yaml` sets `max_parallel_agents: 5` but the API rate limit is 3?

**Recommendation:**
- Create a full `config.yaml.example` with all available keys, defaults, and comments.
- Document the CLI-to-config mapping explicitly.
- Add a `xpatcher config show` command that dumps the effective merged configuration.
- Add validation warnings for conflicting settings.

### M5. Post-hoc log access relies on raw CLI tools

**Location:** Section 7.1, Accessing Logs After the Fact.

The spec suggests users run:
```bash
grep '"event":"error"' .xpatcher/auth-redesign/logs/*.jsonl
grep '"event":"tool_call"' .xpatcher/auth-redesign/logs/agent-planner-*.jsonl | jq .
tail -f .xpatcher/auth-redesign/logs/agent-executor-task-003-*.jsonl
```

This requires the user to: (a) know the feature slug, (b) know the log file naming convention, (c) know the JSONL event format, (d) have `jq` installed. The `xpatcher logs` command is specified (Section 7.1) with `--agent`, `--task`, and `--tail` flags, but its implementation is deferred to Phase 5, Step 37.

**Impact:** For the first 8+ weeks of development (Phases 1-4), there is no user-friendly log access. Developers testing the tool during early phases will rely on raw grep/jq, which is fine for the team but sets a bad precedent.

**Recommendation:**
- Move `xpatcher logs` implementation to Phase 4 (alongside TUI work), not Phase 5.
- Add `xpatcher logs --errors` as a shorthand for filtering error events.
- Add `xpatcher logs --last` to show the most recent agent invocation log.
- Consider adding `xpatcher logs --explain <task-id>` that summarizes what happened in human-readable form (this could use the explorer agent).

---

## Minor Issues

### m1. Color coding has no fallback for color-blind users or no-color terminals

**Location:** Section 7.1, Color Coding.

The spec defines six colors (blue, green, yellow, red, magenta, dim/gray). This is the only channel for conveying status information in the TUI. Users with red-green color blindness (8% of males) cannot distinguish red (error) from green (success). Additionally, the spec does not mention:

- `NO_COLOR` environment variable support (de facto standard: https://no-color.org/)
- `--no-color` CLI flag
- Behavior when `TERM=dumb` or output is piped to a file

The `rich` library supports all of these, but the spec should mandate it.

**Recommendation:**
- Add status indicators beyond color: `[OK]`, `[FAIL]`, `[WARN]`, `[!!]` prefixes in addition to color.
- Support `NO_COLOR` and `--no-color`.
- Use Unicode symbols as secondary indicators: checkmark, cross, warning triangle, clock.
- The TUI mockups already use `[checkmark]`, `[arrow]`, `[dot]` symbols -- confirm these are always present, not just when color is available.

### m2. Quiet mode is underspecified

**Location:** Section 7.1, Verbosity Levels.

`--quiet` is described as "One-line status updates only" with the progress panel hidden. But the spec does not show what quiet mode actually outputs. Questions:

- What does the one-line status look like? `[14:07:32] Stage 3/16: Plan Review (1m 30s)` ?
- Does quiet mode still show human gate prompts? It must, or the pipeline blocks silently.
- Is quiet mode suitable for CI/CD output (line-buffered, no ANSI escape codes)?
- Does quiet mode still show the pipeline ID and resume command at startup?

**Recommendation:** Add a mockup of quiet mode output, similar to the detailed TUI mockups already provided for default and verbose modes.

### m3. The `--config` flag path resolution is ambiguous

**Location:** Section 7.1, Global Flags.

`--config <path>` overrides the config file, defaulting to `~/xpatcher/config.yaml`. But:
- Is this an absolute path or relative to cwd?
- Does this replace the entire config stack or just the global layer?
- If `--config` points to a project-level config, does `.xpatcher.yaml` still apply?

**Recommendation:** Clarify in the flag description: `--config <path>` replaces the global config file only. Project-level `.xpatcher.yaml` is always loaded if present. Path is resolved relative to cwd.

### m4. Soft gate timeout for task review is mentioned but not shown to user

**Location:** Section 3.5, Human Gate Design.

Task review has a "30-minute soft gate" that auto-proceeds if no human intervenes. But the spec does not describe:
- How is the user informed this window exists?
- Is there a countdown visible in the TUI?
- Can the user extend the window?
- What message appears when auto-approval happens?

**Recommendation:** Add TUI mockup for soft gate display:
```
[!] Task Review auto-approval in 28m 15s (press Enter to review now, 'x' to extend)
```

### m5. Feature slug derivation is not specified

**Location:** Sections 5.1, 7.1.

The spec references feature slugs like `auth-redesign` throughout, but never explains how they are derived from the user's input. When a user runs:
```bash
xpatcher start "Replace JWT auth with session-based auth"
```

How does the pipeline derive `auth-redesign` from that string? Is it:
- Generated by the planner agent?
- Derived by the dispatcher via NLP/heuristic?
- Prompted from the user?
- A hash-based fallback?

This matters because the feature slug is the directory name, appears in branch names (`xpatcher/auth-redesign`), and is used in every artifact path.

**Recommendation:** Specify the slug derivation strategy. Suggested approach: (1) dispatcher generates a candidate slug from the input (lowercase, kebab-case, max 40 chars), (2) displays it to the user with an option to override, (3) validates uniqueness against existing `.xpatcher/` directories.

### m6. The `xpatcher list` output format is not specified

**Location:** Section 7.1.

`xpatcher list` is described as "List all pipelines (active, paused, completed)" but there is no mockup of its output. Users need to know:
- What columns are shown (ID, feature, status, elapsed time, last activity)?
- Is it sorted by most recent?
- Does it show pipelines across all projects or just the current one?
- How many pipelines are retained? Are completed pipelines shown forever?

**Recommendation:** Add an output mockup, e.g.:
```
ID                   Feature          Status     Elapsed   Last Activity
xp-20260328-a1b2     auth-redesign    running    47m 12s   2 min ago
xp-20260325-c3d4     add-caching      completed  32m 08s   3 days ago
xp-20260320-e5f6     fix-login-bug    cancelled  12m 44s   9 days ago
```

### m7. Error messages for common failures are not exemplified

**Location:** General.

The spec mentions "actionable error messages" (user preference) and provides the detailed failure output box (Section 3.6), but does not provide examples for common errors:

- What does the user see when Claude Code CLI is not installed?
- What happens when the API key is invalid or rate-limited?
- What does the user see when the project is not a git repository?
- What happens when `.xpatcher/` already exists from a previous run?
- What does the user see when disk space runs out mid-pipeline?

**Recommendation:** Add an "Error Messages" subsection to Section 7.1 with 5-8 examples of common error scenarios and their exact output, following the pattern: what went wrong, why, and what to do about it.

---

## First-Run Experience Walkthrough (new user perspective)

Walking through the spec as a new user trying to install and use xpatcher for the first time:

**Step 1: Discovery.** The user finds xpatcher. There is no README for end users (only this design spec). The master document has a "Quick Navigation" table, but it targets developers building xpatcher, not users running it. **Gap: no user-facing README.**

**Step 2: Installation.** The install script (Section 11) is clear and well-structured. It checks for Python 3.10+ and Claude Code CLI. It creates `~/xpatcher/`, installs dependencies, and creates the CLI entry point. The PATH guidance at the end is helpful. **This is good.** However:
- The script copies files from the current directory (`cp -r .claude-plugin/`), implying the user must first clone a repository. This step is not documented.
- There is no `pip install xpatcher` yet (Phase 5, Step 39). For v1, users must use the install script.
- The script does not verify the Claude Code API key is configured.

**Step 3: First run.** The user runs `xpatcher start "Add a login page"`. The preflight check runs, detects the project stack, creates `.xpatcher/`, and updates `.gitignore`. **This is good.** However:
- If the API key is not set, the first `claude -p` invocation will fail. The spec does not describe this error path.
- The user sees a pipeline ID and a progress panel. They do not know what "Stage 1: Intent Capture" means or how long it will take.

**Step 4: Plan approval.** The structured prompt appears with 4 options. The user is told to inspect `.xpatcher/auth-redesign/plan-v2.yaml`. **Problem:** a new user does not know how to read this YAML file, what to look for, or how to evaluate a plan. Option [4] "View full plan details" is mentioned but its behavior is not specified -- does it print the YAML? Open an editor? Show a summary?

**Step 5: Waiting.** The pipeline executes. The default TUI shows a progress panel. The user can watch or walk away. If they walk away, there is no notification when the pipeline finishes or when the completion gate blocks. **Gap: no completion notification.**

**Step 6: Completion.** The happy path output (Section 3.6) is excellent -- clear summary, branch name, PR URL, artifact paths, and warnings. A new user can understand what happened and what to do next.

**Overall first-run assessment:** Steps 2 (install) and 6 (completion) are well-designed. Steps 1 (discovery), 3 (first run error handling), 4 (plan evaluation), and 5 (waiting) have gaps that would frustrate a new user.

---

## CLI Command Completeness Check

| Command | Defined in 7.1? | Used in spec? | Notes |
|---------|-----------------|---------------|-------|
| `xpatcher start` | Yes | Yes | Well-documented |
| `xpatcher resume` | Yes | Yes | Needs optional pipeline-id |
| `xpatcher status` | Yes | Yes | Default behavior unspecified |
| `xpatcher list` | Yes | Yes | Output format unspecified |
| `xpatcher cancel` | Yes | Yes | Confirmation prompt? Cleanup behavior? |
| `xpatcher logs` | Yes | Yes | Deferred to Phase 5 |
| `xpatcher skip` | **No** | Yes (Section 3.6) | **Critical gap** -- referenced in failure output |
| `xpatcher config` | No | No | Recommended addition for config debugging |
| `xpatcher gates` / `xpatcher pending` | No | No | Recommended for checking blocked pipelines |

**Additional flags referenced but not in the global flags table:**
- `--model` is mentioned in Section 2.3.1 (`--model opus`) but not in the flags table.
- `--concurrency` is mentioned in Section 2.3.1 (`--concurrency 5`) but not in the flags table.
- `--agent` is mentioned in Section 11 (Open Question 1) but not in the flags table.

**Recommendation:** Audit all CLI references across the full spec and ensure every command and flag mentioned anywhere is documented in Section 7.1.

---

## TUI Compatibility Assessment

| Terminal Environment | Expected Status | Risk |
|---------------------|----------------|------|
| macOS Terminal.app | Works | Low -- rich library well-tested |
| iTerm2 | Works | Low |
| VS Code integrated terminal | Likely works | Medium -- rich sometimes has issues with VS Code's terminal emulator and alternate screen buffer |
| tmux | Works with caveats | Medium -- Tab key capture conflicts with tmux prefix; 256-color may need `TERM=xterm-256color` |
| screen | Partial | High -- screen has poor support for rich's live display updates; alternate screen buffer may not work |
| SSH session | Works if terminal supports it | Medium -- latency affects live updates; network drops lose state display |
| SSH + tmux | Compound risks | Medium -- all tmux issues plus SSH latency |
| CI/CD (non-interactive) | Broken | High -- no TTY, no interactive prompts. `--quiet` may work but human gates cannot. |
| Windows Terminal | Unknown | Medium -- rich library supports Windows Terminal but xpatcher is bash-first |
| Pipe to file (`xpatcher start ... > log.txt`) | Broken | High -- rich will either error or produce ANSI garbage in the file |

**Key concerns:**
1. The `rich` library uses alternate screen buffer for live displays by default. This means scrollback is lost when the TUI exits. Users cannot scroll up to see earlier stages.
2. Terminal width below 80 columns will break the TUI panel layouts shown in the mockups.
3. The spec does not address non-TTY scenarios (piped output, cron jobs, CI/CD).

**Recommendation:**
- Explicitly test and document supported terminal environments.
- Auto-detect TTY (`sys.stdout.isatty()`) and fall back to `--quiet` mode when no TTY is detected.
- Store TUI output in a log file alongside agent logs so that scrollback is never lost.
- Set a minimum terminal width (80 columns) and degrade gracefully below it.

---

## Human Gate Interaction Analysis

### Plan Approval Gate (Stage 5)

**Strengths:**
- Structured prompt with numbered options
- Shows metadata (plan version, phases, tasks, complexity, time in planning)
- Points to the plan file for inspection
- Option [2] opens an editor for feedback

**Gaps:**
- **No timeout.** Blocks forever. (See Critical Issue C2.)
- **Option [4] "View full plan details"** is listed but its behavior is not specified. Does it dump YAML? Show a summary? Open `$EDITOR`? Use `less`?
- **No "partially approve" option.** The user must approve the entire plan or reject it. If 11 of 12 tasks look good but 1 is wrong, the user must reject and re-plan everything.
- **No diff view.** If this is plan v2, there is no way to see what changed from v1 in the prompt itself. The user must manually diff the files.
- **No estimated cost or time.** The prompt shows complexity as "medium" but not estimated API cost or wall-clock time.

### Final Completion Gate (Stage 16)

**Strengths:**
- The happy path output is comprehensive and well-formatted.
- Shows branch name, PR URL, stage breakdown, and artifact paths.

**Gaps:**
- The spec does not show the actual prompt for the completion gate. The happy path output appears to be post-approval. What does the user see before approving? Is there a diff summary? Can they inspect individual tasks before approving the whole feature?
- **No "approve with comments" option** for PR description text.

### Escalation Gates (iteration limits exceeded)

**Strengths:**
- The spec mentions "Escalate to human with full review history" (Section 3.4).

**Gaps:**
- No mockup of the escalation prompt. What does the user see? How is the review history presented? What are the options (retry, skip, abort, manually fix)?
- The failure output (Section 3.6) references `xpatcher skip` which is undefined.

### Soft Gate (task review, 30-minute window)

**Gaps:**
- No visibility in the TUI that a soft gate is active.
- No way for the user to know they have a window to intervene.
- No way to extend the window.
- The 30-minute default is not configurable per the spec (it may be in config.yaml but this is not shown).

---

## Error Communication Assessment

The spec's approach to errors has two very different levels of quality:

**Well-specified error communication:**
- The failure path output box (Section 3.6) is excellent -- shows stuck tasks, reasons, time spent, and concrete next steps.
- Malformed output recovery (Section 9) has clear escalation paths with debug file references.
- The validation pipeline produces specific error messages with field paths (`SCHEMA_ERROR [phases -> 0 -> tasks -> 1 -> acceptance]: String should have at least 10 characters`).

**Under-specified error communication:**
- Installation errors: "ERROR: Python 3 not found" is shown, but not for other failures (pip install fails, disk full, permission denied).
- Runtime errors: What does the user see when an agent times out? When a git operation fails? When a worktree cannot be created?
- Configuration errors: What happens when config.yaml has invalid YAML? When a model alias resolves to a model the user does not have access to?
- Network errors: What does the user see when the Claude API returns 429 (rate limit), 500 (server error), or network timeout?

**Recommendation:** Create an error message catalog with at least these categories:
1. Installation / preflight errors (5-8 scenarios)
2. Agent invocation errors (timeout, API error, malformed output)
3. Git operation errors (merge conflict, worktree failure, push failure)
4. Configuration errors (invalid YAML, unknown keys, conflicting values)
5. State errors (corrupted state file, concurrent access, disk full)

Each entry should follow the pattern:
```
ERROR: <what went wrong>
  Cause: <why it happened>
  Fix: <what the user should do>
  Details: <path to log or debug file>
```

---

## Missing: User Documentation Plan

The spec has exactly one line about user documentation (Phase 5, Step 40). Based on the spec's complexity, the following documents are needed for v1 launch:

| Document | Priority | Audience | Content |
|----------|----------|----------|---------|
| README.md | P0 | All users | What xpatcher is, 1-minute install, hello-world example |
| Quick Start Guide | P0 | New users | Install, first run, approve plan, see results (5-minute tutorial) |
| CLI Reference | P0 | All users | Every command, every flag, examples |
| Configuration Guide | P1 | Power users | config.yaml, .xpatcher.yaml, resolution order, all settings |
| Pipeline Stages Guide | P1 | All users | What happens at each stage, what artifacts are produced, how long each takes |
| Human Gates Guide | P1 | All users | What each prompt means, how to evaluate a plan, when to reject |
| Troubleshooting Guide | P1 | All users | Common errors, stuck pipelines, how to inspect and fix |
| Artifact Reference | P2 | Power users | YAML schemas, file naming, querying with yq |
| Architecture Overview | P2 | Contributors | How xpatcher works internally (adapted from this spec) |

**Recommendation:** Add these as explicit deliverables in Phase 5 with effort estimates. The P0 documents should ship with v1; the P1 documents should follow within 2 weeks of launch.

---

## Missing: Onboarding / Tutorial

There is no tutorial, quickstart, or guided first-run experience in the spec. For a tool that orchestrates a 16-stage pipeline with AI agents, new users need scaffolding.

**Recommended onboarding flow:**

1. **First install:** After `install.sh` completes, print a "What's next?" block:
   ```
   xpatcher installed successfully.

   Quick start:
     cd /path/to/your/project
     xpatcher start "describe what you want to build"

   The pipeline will analyze your codebase, create a plan, and ask for
   your approval before making any changes.

   Documentation: https://...
   ```

2. **First run on a project:** When `xpatcher start` detects a new project (no `.xpatcher/` directory), show a brief explanation:
   ```
   First time running xpatcher on this project.
   Detecting project stack... Python 3.11 + pytest + ruff
   Creating .xpatcher/ directory...
   Adding .xpatcher/ to .gitignore...

   Pipeline stages: Plan -> Review -> Execute -> Test -> Simplify -> Gap Check -> Docs
   You will be asked to approve the plan before any code changes are made.
   ```

3. **Sample project:** Include a small sample project that users can clone and run xpatcher against to see the full pipeline without risking their own codebase.

---

## Accessibility Considerations

### Color blindness

As noted in minor issue m1, the TUI relies on color as the primary status indicator. The spec's own mockups use `[checkmark]`, `[arrow]`, and `[dot]` symbols, which is good, but these are not explicitly called out as accessibility features and there is no guarantee they will be used consistently.

**Recommendation:** Mandate that every color-coded element also has a non-color indicator (symbol, text label, or both). The mockups already partially do this -- make it a design rule.

### Screen readers

Terminal-based screen readers (e.g., BRLTTY, NVDA with terminal support) will struggle with rich's live-updating TUI panel. The `--quiet` mode may work as a screen-reader-friendly fallback, but this is not documented.

**Recommendation:** Document that `--quiet` mode is the recommended mode for screen reader users and ensure its output is linear and non-updating (no cursor repositioning).

### Keyboard navigation

The TUI uses `Tab` for agent switching, `q` to hide logs, and Enter for prompt responses. There is no documentation of all keyboard shortcuts or a help overlay (e.g., `?` to show available keys).

**Recommendation:** Add a `?` or `h` key that shows available keyboard shortcuts during TUI operation.

---

## Questions for Product Owner

1. **Is `xpatcher skip` a planned command?** If yes, it needs full specification. If no, the failure output in Section 3.6 needs to be updated. What are the exact semantics -- does skipping a task also skip its dependents?

2. **Is CI/CD headless mode a v1 requirement?** The installation model mentions "server-wide" installation, but human gates block forever. Should there be a `--non-interactive` or `--auto-approve` mode? What are the security implications?

3. **What is the retention policy for pipeline artifacts?** After 20 pipeline runs, `.xpatcher/` could contain gigabytes of YAML, JSONL logs, and debug dumps. Is there a cleanup command? An auto-prune policy?

4. **Should `xpatcher` support running without the TUI?** Some users may prefer plain text output (e.g., logging to a file, running in a basic SSH session). Is `--quiet` sufficient, or should there be a `--plain` mode that shows the same information as default but without rich formatting?

5. **What happens when the user modifies files manually during a pipeline run?** If the user edits a file that an agent is about to modify, is this detected? Does it cause a merge conflict? Is the user warned?

6. **Is there a `xpatcher undo` or rollback capability?** If the user approves the final completion and then realizes the changes are wrong, can they roll back? The spec mentions git branches but not an explicit undo command.

7. **How does xpatcher interact with existing `.claude/` project configuration?** If the project already has a `.claude/CLAUDE.md` with coding conventions, do xpatcher's agents read it? This could significantly improve code quality but is not mentioned.

8. **What happens if the user runs `xpatcher start` while another pipeline is active?** The spec says "single feature at a time" but does not describe the error message or the user's options (cancel the existing one, resume it, or force-start a new one).

---

## Recommendations

### Priority 1 (before v1 implementation)

1. **Define `xpatcher skip` command** or remove it from the failure output. (Critical issue C1.)
2. **Specify Ctrl+C / signal handling behavior.** (Major issue M2.)
3. **Add a user documentation plan** with specific deliverables and owners. (Critical issue C3.)
4. **Change the Tab key for agent switching** to avoid conflicts. (Major issue M1.)

### Priority 2 (during v1 implementation)

5. **Add terminal bell notification** at human gates (`\a` character).
6. **Make pipeline-id optional** where a sensible default exists. (Major issue M3.)
7. **Add `NO_COLOR` support** and symbol-based status indicators. (Minor issue m1.)
8. **Write quick-start guide and CLI reference** as part of Phase 4, not Phase 5.
9. **Move `xpatcher logs` implementation** from Phase 5 to Phase 4.
10. **Add quiet-mode output mockup** to the spec. (Minor issue m2.)
11. **Specify feature slug derivation.** (Minor issue m5.)

### Priority 3 (v1 polish / post-v1)

12. **Add `xpatcher config show`** for configuration debugging.
13. **Add TTY auto-detection** with fallback to quiet mode.
14. **Add `xpatcher gates`/`xpatcher pending`** for checking blocked pipelines.
15. **Create error message catalog** for the 20 most common failure scenarios.
16. **Build bash/zsh tab completion** for pipeline IDs and subcommands.
17. **Add plan diff view** to the plan approval gate (show changes from v(N-1) to v(N)).
18. **Add keyboard shortcut help overlay** (`?` key during TUI operation).

---

*Review complete. The spec's internal architecture is solid. The user-facing surface needs the gaps identified above addressed before v1 ships to external users. The most impactful changes are: defining `xpatcher skip`, handling Ctrl+C gracefully, and writing the quick-start guide.*

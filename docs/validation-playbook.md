# xpatcher v2 Validation Playbook

Repeatable end-to-end validation of the installed xpatcher pipeline against real open-source repositories.

## Prerequisites

- macOS or Linux
- Python 3.10+
- Claude Code CLI installed and authenticated (`claude` in PATH)
- Git
- ~$5 of Claude API budget per scenario

Verify:

```bash
python3 --version          # 3.10+
claude --version           # should print version
git --version
```

---

## Scenario A: Click — Add Echo Example with --count Option

A small, focused change touching 2-3 new files in a well-structured Python CLI library.
Expected tasks: 3 (init files, command implementation, tests).
Expected pipeline cost: ~$2.

### A.1 Install xpatcher

```bash
cd /path/to/xpatcher                         # xpatcher source checkout
export XPATCHER_HOME="$HOME/xpatcher-v2-val"
rm -rf "$XPATCHER_HOME"
./install.sh
```

Expect: "Installation complete!" with 9 agent definitions found, smoke test passing.

### A.2 Clone and prepare benchmark

```bash
mkdir -p "$HOME/bench"
rm -rf "$HOME/bench/click"
git clone --depth 1 https://github.com/pallets/click.git "$HOME/bench/click"
cd "$HOME/bench/click"
git checkout -B xpatcher-v2-bench
```

Verify the repo is clean:

```bash
git status                                    # clean working tree
python -m pytest tests/test_basic.py -q       # existing tests pass
```

### A.3 Run the pipeline

```bash
cd "$HOME/bench/click"
"$XPATCHER_HOME/bin/xpatcher" start \
  "Add a --count option to the echo example in examples/repo/echo.py that repeats the output N times, with tests in tests/test_examples.py" \
  --debug
```

The pipeline will pause at plan approval if clarifying questions were generated. When prompted:

```
Approve this specification? [y/n]: y
```

If it pauses and you closed the terminal, resume:

```bash
cd "$HOME/bench/click"
"$XPATCHER_HOME/bin/xpatcher" list            # find the pipeline ID
"$XPATCHER_HOME/bin/xpatcher" resume <id>     # approve when prompted
```

### A.4 Monitor progress

In another terminal:

```bash
export XPATCHER_HOME="$HOME/xpatcher-v2-val"
"$XPATCHER_HOME/bin/xpatcher" progress <pipeline-id>
```

Expected stage progression: Intent → Plan → Review (may loop once) → Plan Fix → Review → Approve → Tasks → TaskReview → Execution → Quality → Gap Detection → Docs → Done.

### A.5 Handle stuck tasks

If a task gets stuck (common cause: acceptance criteria use bash-specific syntax like `<(...)` that `/bin/sh` can't run):

```bash
"$XPATCHER_HOME/bin/xpatcher" skip <pipeline-id> task-002 --force-unblock
"$XPATCHER_HOME/bin/xpatcher" resume <pipeline-id> --from-stage task_execution
```

### A.6 Verify results

**Code changes:**

```bash
cd "$HOME/bench/click"
git log --oneline xpatcher-v2-bench..HEAD     # should show xpatcher commits
cat examples/repo/echo.py                     # the new echo command
```

**Functional test:**

```bash
python examples/repo/echo.py "hello world" --count 3
```

Expected output:

```
hello world
hello world
hello world
```

**Edge cases:**

```bash
python examples/repo/echo.py "test"              # default count=1: prints once
python examples/repo/echo.py "test" --count 0    # prints nothing (or error)
python examples/repo/echo.py --help               # shows --count option
```

**v2 artifacts:**

```bash
FEATURE_DIR=$(find "$XPATCHER_HOME/.xpatcher/projects" -name "pipeline-state.yaml" -path "*click*" -exec dirname {} \;)

# Lane files (should have 4-6 lanes)
ls "$FEATURE_DIR/lanes/"

# Context artifacts
ls "$FEATURE_DIR/context/"
cat "$FEATURE_DIR/context/repo-inventory.yaml"    # should detect Python, pytest

# Validation failure snapshots (if retries happened)
ls "$FEATURE_DIR/validation-failures/" 2>/dev/null

# Task packets
ls "$FEATURE_DIR/context/task-packets/"
```

### A.7 Clean up

```bash
"$XPATCHER_HOME/bin/xpatcher" delete <pipeline-id>
cd "$HOME/bench/click" && git checkout main
rm -rf "$HOME/bench/click"
```

---

## Scenario B: Rich — Add Sparkline Renderable

A multi-file feature in a large terminal UI library. Exercises deeper planning, multi-task dependencies, code review with architectural findings, and color gradient logic.
Expected tasks: 3+ (core class, export, tests).
Expected pipeline cost: ~$5.

### B.1 Install xpatcher

Same as A.1. If already installed, reinstall to pick up any source changes:

```bash
cd /path/to/xpatcher
export XPATCHER_HOME="$HOME/xpatcher-v2-val"
rm -rf "$XPATCHER_HOME"
./install.sh
```

### B.2 Clone and prepare benchmark

```bash
rm -rf "$HOME/bench/rich"
git clone --depth 1 https://github.com/Textualize/rich.git "$HOME/bench/rich"
cd "$HOME/bench/rich"
git checkout -B xpatcher-v2-bench
```

Verify:

```bash
python -m pytest tests/test_bar.py -q         # existing tests pass
ls rich/bar.py rich/progress_bar.py            # confirm source layout
```

### B.3 Write the request file

```bash
cat > /tmp/sparkline-request.md << 'EOF'
Add a Sparkline renderable to Rich.

A Sparkline is a compact inline visualization of numeric data using Unicode
block characters (▁▂▃▄▅▆▇█).

Requirements:

1. Create rich/sparkline.py with a Sparkline class implementing
   __rich_console__ and __rich_measure__.
2. Constructor: data (Sequence[float]), width (int | None), min_value/max_value
   (float | None), style (StyleType), colors (list[str] | None).
3. Map each value to one of 8 block chars based on min-max position. Resample
   data to fit width if set. Empty data renders as empty.
4. Edge cases: all-equal → mid-height bars, NaN/None → floor bar, negatives work.
5. Color gradient: interpolate across a color list using blend_rgb.
6. Export from rich/__init__.py.
7. Tests in tests/test_sparkline.py covering rendering, resampling, styles,
   gradients, edge cases, Console.print, and __rich_measure__.
8. Test command: python -m pytest tests/test_sparkline.py -q
EOF
```

### B.4 Run the pipeline

```bash
cd "$HOME/bench/rich"
"$XPATCHER_HOME/bin/xpatcher" start --file /tmp/sparkline-request.md --debug
```

Approve the plan when prompted. Monitor with `progress` in another terminal.

### B.5 Verify results

**Code exists:**

```bash
cd "$HOME/bench/rich"
git log --oneline xpatcher-v2-bench..HEAD
wc -l rich/sparkline.py                       # expect 150-200 lines
```

**Functional validation — run this script:**

```bash
cd "$HOME/bench/rich"
python3 -c "
from rich.sparkline import Sparkline, BLOCK_CHARS
from rich.console import Console
from io import StringIO
import math

failures = []

# 1. Basic: 8 values map to ascending blocks
buf = StringIO()
Console(file=buf, color_system=None, width=80).print(Sparkline([1,2,3,4,5,6,7,8]), end='')
out = buf.getvalue().strip()
if out != '▁▂▃▄▅▆▇█':
    failures.append(f'basic: expected ▁▂▃▄▅▆▇█, got {repr(out)}')

# 2. Empty data
buf = StringIO()
Console(file=buf, color_system=None, width=80).print(Sparkline([]), end='')
if buf.getvalue().strip():
    failures.append(f'empty: expected empty, got {repr(buf.getvalue())}')

# 3. All equal → mid-height
buf = StringIO()
Console(file=buf, color_system=None, width=80).print(Sparkline([5,5,5]), end='')
out = buf.getvalue().strip()
if not all(c == '▄' for c in out):
    failures.append(f'equal: expected all ▄, got {repr(out)}')

# 4. Width resampling
buf = StringIO()
Console(file=buf, color_system=None, width=80).print(Sparkline([1,5,3], width=6), end='')
if len(buf.getvalue().strip()) != 6:
    failures.append(f'resample: expected 6 chars, got {len(buf.getvalue().strip())}')

# 5. NaN → floor
buf = StringIO()
Console(file=buf, color_system=None, width=80).print(Sparkline([1, float('nan'), 3]), end='')
out = buf.getvalue().strip()
if len(out) >= 2 and out[1] != '▁':
    failures.append(f'nan: expected ▁ at index 1, got {repr(out[1])}')

# 6. Negatives
buf = StringIO()
Console(file=buf, color_system=None, width=80).print(Sparkline([-4,-2,0,2,4]), end='')
out = buf.getvalue().strip()
if out[0] != '▁' or out[-1] != '█':
    failures.append(f'negatives: min should be ▁ and max █, got {repr(out)}')

# 7. Color gradient renders without error
buf = StringIO()
Console(file=buf, color_system='truecolor', width=80).print(
    Sparkline([1,2,3,4,5], colors=['red','yellow','green']), end='')
if not buf.getvalue():
    failures.append('gradient: no output')

# 8. __rich_measure__
from rich.measure import Measurement
c = Console(file=StringIO())
m = Sparkline([1,2,3]).__rich_measure__(c, c.options)
if m != Measurement(3, 3):
    failures.append(f'measure: expected (3,3), got {m}')
m2 = Sparkline([1,2,3], width=10).__rich_measure__(c, c.options)
if m2 != Measurement(10, 10):
    failures.append(f'measure_width: expected (10,10), got {m2}')

# 9. Import from rich top-level
try:
    from rich.sparkline import Sparkline as _S
except ImportError as e:
    failures.append(f'import: {e}')

if failures:
    print('FAILURES:')
    for f in failures:
        print(f'  - {f}')
    exit(1)
else:
    print('ALL 9 CHECKS PASSED')
"
```

**v2 artifact inspection:**

```bash
FEATURE_DIR=$(find "$XPATCHER_HOME/.xpatcher/projects" -name "pipeline-state.yaml" -path "*rich*" -exec dirname {} \;)

echo "=== Lane files ==="
ls "$FEATURE_DIR/lanes/"
# Expect: spec_author, spec_review, manifest_author, manifest_review,
#         task_exec-task-001, task_exec-task-002, task_exec-task-003

echo "=== Context artifacts ==="
cat "$FEATURE_DIR/context/repo-inventory.yaml"
# Expect: primary_languages includes python, test_frameworks includes pytest

echo "=== Task packets ==="
ls "$FEATURE_DIR/context/task-packets/"

echo "=== Validation failures (retries that were auto-repaired) ==="
ls "$FEATURE_DIR/validation-failures/" 2>/dev/null

echo "=== Code review ==="
cat "$FEATURE_DIR/tasks/done/task-002-review-v1.yaml" 2>/dev/null | head -20
# Expect: verdict approve, findings about lazy-import pattern
```

### B.6 Clean up

```bash
"$XPATCHER_HOME/bin/xpatcher" delete <pipeline-id>
cd "$HOME/bench/rich" && git checkout main
rm -rf "$HOME/bench/rich"
```

---

## What to check after every run

### Pipeline health

| Check | How | Pass criteria |
|---|---|---|
| Pipeline reached execution | `xpatcher progress <id>` | Stage 11+ visited |
| Lane isolation | `ls $FEATURE_DIR/lanes/` | Different lanes for spec/manifest/exec |
| Validation retry worked | `ls $FEATURE_DIR/validation-failures/` | Files exist = retries happened and were repaired |
| Context artifacts created | `ls $FEATURE_DIR/context/` | `repo-inventory.yaml` and `feature-brief.yaml` present |
| Direct agent invocation | Debug log shows `--agent planner` etc. | No `@agent-...` delegation in prompts |
| Cost is reasonable | `xpatcher progress <id>` | < $3 for Click, < $6 for Rich |

### Code quality

| Check | How | Pass criteria |
|---|---|---|
| Commits exist | `git log --oneline` in target repo | `xpatcher(task-NNN)` commits present |
| Code runs | Execute the created command/module | Expected output |
| Tests pass | `python -m pytest tests/test_sparkline.py -q` | If tests were generated |
| No unrelated changes | `git diff --stat xpatcher-v2-bench..HEAD` | Only expected files changed |
| On feature branch | `git branch --show-current` | `xpatcher/...` branch name |

### v2 feature verification

| Feature | Evidence |
|---|---|
| Lane-scoped sessions | Lane files have different `session_id` values for spec_author vs manifest_author |
| Validation retry | `validation-failures/` directory has attempt snapshots; pipeline log shows "Validation repair succeeded" |
| Severity gating | Review with minor-only findings shows "auto-approved with warnings" in log |
| Bootstrap context | `context/repo-inventory.yaml` contains detected languages and test frameworks |
| Direct --agent | Debug log shows `--agent planner`, `--agent executor`, etc. on CLI commands |
| Budget tracking | `pipeline-state.yaml` contains `budget_checkpoints` and `lane_sessions` with `total_cost_usd` |

---

## Troubleshooting

**Pipeline pauses at plan approval unexpectedly:**
The intent had clarifying questions. Approve with `y` or resume with `xpatcher resume <id>`.

**Task stuck due to acceptance criteria syntax errors:**
Acceptance commands using bash-specific syntax (`<(...)`, multi-statement `python -c`) fail under `/bin/sh`. Skip and unblock:

```bash
xpatcher skip <id> <task-id> --force-unblock
xpatcher resume <id> --from-stage task_execution
```

**OAuth token expires mid-pipeline:**
Refresh: `claude auth login`, then `xpatcher resume <id> --from-stage <last-stage>`.

**Task execution blocked by dependency on stuck task:**
Skip the upstream task with `--force-unblock` to unblock dependents.

**Pipeline costs more than expected:**
Check `xpatcher progress <id>` — look at agent run counts (`x2`, `x3`). Validation retries and fix iterations add cost. Budget caps in `config.yaml` can limit this.

---

## Reset between runs

To start clean for a fresh validation:

```bash
export XPATCHER_HOME="$HOME/xpatcher-v2-val"

# Reinstall xpatcher from source
cd /path/to/xpatcher
rm -rf "$XPATCHER_HOME"
./install.sh

# Reset benchmark repo
cd "$HOME/bench/<repo>"
git reset --hard HEAD
git clean -fd
git checkout -B xpatcher-v2-bench
```

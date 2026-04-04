"""Terminal UI renderer for xpatcher pipeline."""

from __future__ import annotations

import sys
import time
import shutil
import re
import math
from datetime import datetime, timezone
from typing import TextIO


class TUIRenderer:
    """Simple terminal output for pipeline progress."""

    RESET = "\033[0m"
    BOLD = "\033[1m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    CYAN = "\033[36m"
    DIM = "\033[2m"

    _PIPELINE_FLOW = [
        ("Intent", {"intent_capture"}),
        ("Plan", {"planning"}),
        ("Review", {"plan_review", "plan_fix"}),
        ("Approve", {"plan_approval"}),
        ("Tasks", {"task_breakdown", "task_review", "task_fix", "prioritization", "execution_graph"}),
        ("Exec", {"task_execution"}),
        ("Quality", {"per_task_quality", "fix_iteration"}),
        ("Gaps", {"gap_detection"}),
        ("Docs", {"documentation"}),
        ("Done", {"completion", "done"}),
    ]
    _SPINNER_FRAMES = ("|", "/", "-", "\\")
    _HEARTBEATS = ("thinking", "checking context", "working", "waiting on tools")
    _ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
    _RECENT_SPLIT = " | "

    def __init__(self):
        self._start_time = datetime.now(timezone.utc)
        self._current_stage = ""
        self._pipeline_id = ""
        self._pipeline_label = ""
        self._claude_session_id = ""
        self._stage_key = ""
        self._task_id = ""
        self._lane = ""
        self._owner_agent = ""
        self._active_actor = ""
        self._activity = ""
        self._tool_name = ""
        self._loop_label = ""
        self._loop_current = 0
        self._loop_max = 0
        self._last_activity_at = 0.0
        self._recent_events: list[str] = []
        self._total_cost = 0.0
        self._live_enabled = False
        self._live_lines_rendered = 0
        self._spinner_index = 0
        self._last_non_terminal_stage = ""

    def configure_live_dashboard(self, enabled: bool) -> None:
        """Enable the in-place dashboard when the terminal supports it."""
        self._live_enabled = enabled and sys.stdout.isatty()
        if not self._live_enabled:
            self.clear_live()

    def set_pipeline(self, pipeline_id: str, label: str = "") -> None:
        self._pipeline_id = pipeline_id
        self._pipeline_label = label
        self._render_live()

    def set_stage(self, stage_key: str, task_id: str = "", lane: str = "", owner_agent: str = "") -> None:
        self._stage_key = stage_key
        self._task_id = task_id
        if lane:
            self._lane = lane
        if owner_agent:
            self._owner_agent = owner_agent
        if stage_key not in {"done", "failed", "blocked", "paused", "cancelled"}:
            self._last_non_terminal_stage = stage_key
        if stage_key not in {"plan_review", "plan_fix", "task_review", "task_fix", "per_task_quality", "fix_iteration", "gap_detection"}:
            self.clear_loop_progress()
        self._render_live()

    def set_invocation_context(self, lane: str = "", owner_agent: str = "", task_id: str = "", claude_session_id: str = "") -> None:
        if lane:
            self._lane = lane
        if owner_agent:
            self._owner_agent = owner_agent
        if task_id:
            self._task_id = task_id
        if claude_session_id:
            self._claude_session_id = claude_session_id
        if owner_agent and not self._active_actor:
            self._active_actor = owner_agent
        self._render_live()

    def update_activity(self, actor: str = "", tool_name: str = "", summary: str = "") -> None:
        now = time.monotonic()
        if actor:
            self._active_actor = actor
        if tool_name:
            self._tool_name = tool_name
        if summary:
            self._activity = summary
            self._last_activity_at = now
            prefix = self._activity_prefix()
            recent = f"{prefix} {summary}".strip()
            if not self._recent_events or self._recent_events[0] != recent:
                self._recent_events.insert(0, recent)
                self._recent_events = self._recent_events[:3]
        self._spinner_index = (self._spinner_index + 1) % len(self._SPINNER_FRAMES)
        self._render_live()

    def clear_activity(self) -> None:
        self._active_actor = ""
        self._activity = ""
        self._tool_name = ""
        self._render_live()

    def set_loop_progress(self, label: str, current: int, maximum: int) -> None:
        self._loop_label = label
        self._loop_current = current
        self._loop_max = maximum
        self._render_live()

    def clear_loop_progress(self) -> None:
        self._loop_label = ""
        self._loop_current = 0
        self._loop_max = 0
        self._render_live()

    def header(self, text: str):
        self._print(
            f"\n{self.BOLD}{self.CYAN}{'='*60}{self.RESET}\n"
            f"{self.BOLD}{self.CYAN}  {text}{self.RESET}\n"
            f"{self.BOLD}{self.CYAN}{'='*60}{self.RESET}\n"
        )
        self._render_live()

    def stage(self, text: str, stage_key: str = "", task_id: str = "", lane: str = "", owner_agent: str = ""):
        self._current_stage = text.split(":")[0].strip() if ":" in text else text.strip()
        if stage_key:
            self.set_stage(stage_key, task_id=task_id, lane=lane, owner_agent=owner_agent)
        elapsed = self._elapsed()
        self._print(f"{self._prefix()}{self.BOLD}{self.BLUE}▸ {text}{self.RESET} {self.DIM}[{elapsed}]{self.RESET}\n")
        self._render_live()

    def status(self, text: str):
        self._print(f"{self._prefix()}{self.DIM}{text}{self.RESET}\n")

    def success(self, text: str):
        self._print(f"{self._prefix()}{self.GREEN}✓ {text}{self.RESET}\n")

    def error(self, text: str):
        self._print(f"{self._prefix()}{self.RED}✗ {text}{self.RESET}\n", stream=sys.stderr)

    def warning(self, text: str):
        self._print(f"{self._prefix()}{self.YELLOW}⚠ {text}{self.RESET}\n")

    def info(self, text: str):
        self._print(f"{self._prefix()}{text}\n")

    def debug(self, text: str):
        self._print(
            f"{self._prefix()}{self.DIM}{self.CYAN}[debug]{self.RESET} {self.DIM}{text}{self.RESET}\n",
            stream=sys.stderr,
        )

    def human_gate(self, text: str):
        self._print(
            f"\n{self.BOLD}{self.YELLOW}{'─'*60}{self.RESET}\n"
            f"{self._prefix()}{self.BOLD}{self.YELLOW}🔒 {text}{self.RESET}\n"
            f"{self.BOLD}{self.YELLOW}{'─'*60}{self.RESET}\n"
        )
        print("\a", end="", flush=True)

    def agent_result(self, text: str, is_error: bool = False):
        """Show agent's final message with a colored left stripe."""
        color = self.RED if is_error else self.GREEN
        stripe = f"{color}│{self.RESET}"
        self._clear_live_lines()
        for line in text.splitlines():
            print(f"  {stripe} {self.DIM}{line}{self.RESET}")
        self._render_live()

    def cost_update(self, total_cost: float):
        self._total_cost = total_cost
        self._print(f"{self._prefix()}{self.DIM}Running cost: ${total_cost:.4f}{self.RESET}\n")

    def cost_summary(self, total_cost: float):
        self._total_cost = total_cost
        self._print(f"\n{self._prefix()}{self.BOLD}Total pipeline cost: ${total_cost:.4f}{self.RESET}\n")

    def prompt_approval(self, prompt: str) -> bool:
        self._clear_live_lines()
        try:
            response = input(f"\n  {self.BOLD}{prompt}{self.RESET}").strip().lower()
            return response in ("y", "yes")
        except (EOFError, KeyboardInterrupt):
            return False
        finally:
            self._render_live()

    def clear_live(self) -> None:
        self._clear_live_lines()
        self._live_enabled = False

    def _elapsed(self) -> str:
        delta = datetime.now(timezone.utc) - self._start_time
        total_sec = int(delta.total_seconds())
        minutes, seconds = divmod(total_sec, 60)
        if minutes > 0:
            return f"{minutes}m {seconds}s"
        return f"{seconds}s"

    def _ts(self) -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def _prefix(self) -> str:
        ts = self._ts()
        if self._current_stage:
            return f"{self.DIM}{ts} [{self._current_stage}]{self.RESET} "
        return f"{self.DIM}{ts}{self.RESET} "

    def _print(self, text: str, stream: TextIO | None = None) -> None:
        if stream is None:
            stream = sys.stdout
        self._clear_live_lines()
        stream.write(text)
        stream.flush()
        self._render_live()

    def _clear_live_lines(self) -> None:
        if not self._live_enabled or self._live_lines_rendered <= 0:
            return
        sys.stdout.write(f"\033[{self._live_lines_rendered}F")
        for index in range(self._live_lines_rendered):
            sys.stdout.write("\033[2K")
            if index < self._live_lines_rendered - 1:
                sys.stdout.write("\n")
        sys.stdout.write(f"\033[{max(self._live_lines_rendered - 1, 0)}F")
        sys.stdout.flush()
        self._live_lines_rendered = 0

    def _render_live(self) -> None:
        if not self._live_enabled or not self._pipeline_id:
            return
        lines = self._dashboard_lines()
        if not lines:
            return
        self._clear_live_lines()
        sys.stdout.write("\n".join(lines) + "\n")
        sys.stdout.flush()
        self._live_lines_rendered = self._rendered_row_count(lines)

    def _dashboard_lines(self) -> list[str]:
        return [
            self._fit_ansi_line(self._line_header()),
            self._fit_ansi_line(self._line_flow()),
            self._fit_ansi_line(self._line_current()),
            self._fit_ansi_line(self._line_activity()),
            self._fit_ansi_line(self._line_recent()),
        ]

    def _line_header(self) -> str:
        title_parts = [self._span("┌─", color=self.DIM), self._span(self._pipeline_id, color=self.CYAN, bold=True)]
        if self._pipeline_label:
            title_parts.extend([self._span("  ", color=self.DIM), self._span(self._pipeline_label)])
        title_parts.extend([
            self._span("  ", color=self.DIM),
            self._span("Cost", color=self.DIM),
            self._span(f" ${self._total_cost:.2f}"),
            self._span("  ", color=self.DIM),
            self._span("Elapsed", color=self.DIM),
            self._span(f" {self._elapsed()}"),
        ])
        return self._join_spans(title_parts)

    def _line_flow(self) -> str:
        parts = [self._span("│", color=self.DIM), self._span(" ", color=self.DIM)]
        flow_parts = []
        for index, segment in enumerate(self._flow_segments()):
            if index:
                flow_parts.append(self._span(" ", color=self.DIM))
            flow_parts.append(segment)
        parts.extend(flow_parts)
        return self._join_spans(parts)

    def _line_current(self) -> str:
        parts = [self._span("│", color=self.DIM), self._span(" ", color=self.DIM), self._span("Current:", color=self.DIM, bold=True), self._span(" ", color=self.DIM)]
        detail_parts = []
        if self._stage_key:
            detail_parts.append(self._span(self._stage_key, color=self.BLUE, bold=True))
        if self._task_id:
            detail_parts.extend(self._sep_parts())
            detail_parts.append(self._span(self._task_id, bold=True))
        if self._loop_label and self._loop_max:
            detail_parts.extend(self._sep_parts())
            detail_parts.append(self._span(f"{self._loop_label} {self._loop_current}/{self._loop_max}", color=self.YELLOW))
        if self._lane:
            detail_parts.extend(self._sep_parts())
            detail_parts.append(self._span(self._lane, color=self.DIM))
        if self._owner_agent:
            detail_parts.extend(self._sep_parts())
            detail_parts.append(self._span(self._owner_agent, color=self.CYAN, bold=True))
        if self._claude_session_id:
            detail_parts.extend(self._sep_parts())
            detail_parts.append(self._span("claude:", color=self.DIM))
            detail_parts.append(self._span(self._claude_session_id, color=self.CYAN))
        if not detail_parts:
            detail_parts.append(self._span("idle", color=self.DIM))
        parts.extend(detail_parts)
        return self._join_spans(parts)

    def _line_activity(self) -> str:
        actor = self._active_actor or self._owner_agent or "agent"
        activity = self._activity or self._heartbeat_text()
        spinner = self._SPINNER_FRAMES[self._spinner_index]
        tool_name = self._tool_name
        parts = [
            self._span("│", color=self.DIM),
            self._span(" ", color=self.DIM),
            self._span(spinner, color=self.CYAN),
            self._span(" ", color=self.DIM),
            self._span(actor, color=self.CYAN, bold=True),
        ]
        if tool_name:
            parts.extend([self._span(" · ", color=self.DIM), self._span(tool_name, color=self.BLUE, bold=True)])
        parts.extend([self._span("  ", color=self.DIM), self._span(activity)])
        return self._join_spans(parts)

    def _line_recent(self) -> str:
        parts = [self._span("└─", color=self.DIM), self._span(" ", color=self.DIM), self._span("Recent:", color=self.DIM), self._span(" ", color=self.DIM)]
        if not self._recent_events:
            parts.append(self._span("No tool activity yet", color=self.DIM))
            return self._join_spans(parts)
        for index, event in enumerate(self._recent_events[:3]):
            if index:
                parts.append(self._span(self._RECENT_SPLIT, color=self.DIM))
            parts.extend(self._styled_recent_event(event))
        return self._join_spans(parts)

    def _flow_segments(self) -> list[str]:
        current_group = self._group_for_stage(self._stage_key or self._last_non_terminal_stage)
        terminal = self._terminal_state()
        segments: list[str] = []
        for index, (label, _) in enumerate(self._PIPELINE_FLOW):
            status = "pending"
            if terminal == "done":
                status = "done"
            elif terminal in {"failed", "blocked", "paused", "cancelled"}:
                if current_group is not None and index < current_group:
                    status = "done"
                elif current_group is not None and index == current_group:
                    status = terminal
            elif current_group is not None:
                if index < current_group:
                    status = "done"
                elif index == current_group:
                    status = "current"
            segments.append(self._format_flow_segment(label, status))
        return segments

    def _terminal_state(self) -> str:
        if self._stage_key in {"done", "failed", "blocked", "paused", "cancelled"}:
            return self._stage_key
        return ""

    def _group_for_stage(self, stage_key: str) -> int | None:
        if not stage_key:
            return None
        for index, (_, stage_keys) in enumerate(self._PIPELINE_FLOW):
            if stage_key in stage_keys:
                return index
        return None

    def _format_flow_segment(self, label: str, status: str) -> str:
        icon = "·"
        color = self.DIM
        bold = False
        if status == "done":
            icon = "✓"
            color = self.GREEN
        elif status == "current":
            icon = "◐"
            color = self.CYAN
            bold = True
        elif status == "failed":
            icon = "✗"
            color = self.RED
        elif status in {"blocked", "paused", "cancelled"}:
            icon = "!"
            color = self.YELLOW
        return self._span(f"[{icon} {label}]", color=color, bold=bold)

    def _heartbeat_text(self) -> str:
        if self._tool_name == "Agent":
            return "waiting for subagent"
        if self._tool_name:
            return f"waiting on {self._tool_name}"
        if self._owner_agent and (not self._active_actor or self._active_actor == self._owner_agent):
            return "waiting for model"
        if self._active_actor and self._owner_agent and self._active_actor != self._owner_agent:
            return f"waiting for {self._active_actor}"
        if not self._last_activity_at:
            return self._HEARTBEATS[0]
        age = time.monotonic() - self._last_activity_at
        if age < 2:
            return self._activity or self._HEARTBEATS[0]
        heartbeat_index = int(age / 2) % len(self._HEARTBEATS)
        return self._HEARTBEATS[heartbeat_index]

    def _activity_prefix(self, actor: str | None = None) -> str:
        actor = actor or self._active_actor or self._owner_agent or "agent"
        if self._tool_name:
            return f"{actor} · {self._tool_name}"
        return actor

    def _styled_recent_event(self, event: str) -> list[str]:
        head, sep, tail = event.partition(" ")
        actor_parts = head.split("·", 1)
        spans = [self._span(actor_parts[0].strip(), color=self.CYAN)]
        if len(actor_parts) == 2:
            spans.extend([self._span(" · ", color=self.DIM), self._span(actor_parts[1].strip(), color=self.BLUE)])
        if sep:
            spans.extend([self._span(" ", color=self.DIM), self._span(tail)])
        return spans

    def _span(self, text: str, color: str = "", bold: bool = False) -> str:
        prefix = ""
        if color:
            prefix += color
        if bold:
            prefix += self.BOLD
        if not prefix:
            return text
        return f"{prefix}{text}{self.RESET}"

    def _join_spans(self, spans: list[str]) -> str:
        return "".join(spans)

    def _sep_parts(self) -> list[str]:
        return [self._span(" | ", color=self.DIM)]

    def _term_width(self) -> int:
        return max(60, shutil.get_terminal_size(fallback=(100, 24)).columns)

    def _fit_ansi_line(self, line: str) -> str:
        max_width = self._term_width() - 1
        if self._visible_len(line) <= max_width:
            return line

        out: list[str] = []
        visible = 0
        index = 0
        limit = max(1, max_width - 1)
        for match in self._ANSI_RE.finditer(line):
            if match.start() > index:
                chunk = line[index:match.start()]
                remaining = limit - visible
                if remaining <= 0:
                    break
                if len(chunk) <= remaining:
                    out.append(chunk)
                    visible += len(chunk)
                else:
                    out.append(chunk[:remaining])
                    visible += remaining
                    break
            out.append(match.group(0))
            index = match.end()
        if visible < limit and index < len(line):
            chunk = line[index:]
            remaining = limit - visible
            out.append(chunk[:remaining])
        out.append("…")
        if not "".join(out).endswith(self.RESET):
            out.append(self.RESET)
        return "".join(out)

    def _visible_len(self, line: str) -> int:
        return len(self._ANSI_RE.sub("", line))

    def _rendered_row_count(self, lines: list[str]) -> int:
        width = max(1, self._term_width() - 1)
        rows = 0
        for line in lines:
            rows += max(1, math.ceil(self._visible_len(line) / width))
        return rows

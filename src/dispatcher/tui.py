"""Terminal UI renderer for xpatcher pipeline."""

import sys
from datetime import datetime, timezone


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

    def __init__(self):
        self._start_time = datetime.now(timezone.utc)
        self._current_stage = ""

    def _ts(self) -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def _prefix(self) -> str:
        ts = self._ts()
        if self._current_stage:
            return f"{self.DIM}{ts} [{self._current_stage}]{self.RESET} "
        return f"{self.DIM}{ts}{self.RESET} "

    def header(self, text: str):
        print(f"\n{self.BOLD}{self.CYAN}{'='*60}{self.RESET}")
        print(f"{self.BOLD}{self.CYAN}  {text}{self.RESET}")
        print(f"{self.BOLD}{self.CYAN}{'='*60}{self.RESET}\n")

    def stage(self, text: str):
        self._current_stage = text.split(":")[0].strip() if ":" in text else text.strip()
        elapsed = self._elapsed()
        print(f"{self._prefix()}{self.BOLD}{self.BLUE}▸ {text}{self.RESET} {self.DIM}[{elapsed}]{self.RESET}")

    def status(self, text: str):
        print(f"{self._prefix()}{self.DIM}{text}{self.RESET}")

    def success(self, text: str):
        print(f"{self._prefix()}{self.GREEN}✓ {text}{self.RESET}")

    def error(self, text: str):
        print(f"{self._prefix()}{self.RED}✗ {text}{self.RESET}", file=sys.stderr)

    def warning(self, text: str):
        print(f"{self._prefix()}{self.YELLOW}⚠ {text}{self.RESET}")

    def info(self, text: str):
        print(f"{self._prefix()}{text}")

    def debug(self, text: str):
        print(f"{self._prefix()}{self.DIM}{self.CYAN}[debug]{self.RESET} {self.DIM}{text}{self.RESET}", file=sys.stderr)

    def human_gate(self, text: str):
        print(f"\n{self.BOLD}{self.YELLOW}{'─'*60}{self.RESET}")
        print(f"{self._prefix()}{self.BOLD}{self.YELLOW}🔒 {text}{self.RESET}")
        print(f"{self.BOLD}{self.YELLOW}{'─'*60}{self.RESET}")
        # Terminal bell
        print("\a", end="", flush=True)

    def agent_result(self, text: str, is_error: bool = False):
        """Show agent's final message with a colored left stripe."""
        color = self.RED if is_error else self.GREEN
        stripe = f"{color}│{self.RESET}"
        for line in text.splitlines():
            print(f"  {stripe} {self.DIM}{line}{self.RESET}")

    def cost_update(self, total_cost: float):
        print(f"{self._prefix()}{self.DIM}Running cost: ${total_cost:.4f}{self.RESET}")

    def cost_summary(self, total_cost: float):
        print(f"\n{self._prefix()}{self.BOLD}Total pipeline cost: ${total_cost:.4f}{self.RESET}")

    def prompt_approval(self, prompt: str) -> bool:
        try:
            response = input(f"\n  {self.BOLD}{prompt}{self.RESET}").strip().lower()
            return response in ("y", "yes")
        except (EOFError, KeyboardInterrupt):
            return False

    def _elapsed(self) -> str:
        delta = datetime.now(timezone.utc) - self._start_time
        total_sec = int(delta.total_seconds())
        minutes, seconds = divmod(total_sec, 60)
        if minutes > 0:
            return f"{minutes}m {seconds}s"
        return f"{seconds}s"

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

    def header(self, text: str):
        print(f"\n{self.BOLD}{self.CYAN}{'='*60}{self.RESET}")
        print(f"{self.BOLD}{self.CYAN}  {text}{self.RESET}")
        print(f"{self.BOLD}{self.CYAN}{'='*60}{self.RESET}\n")

    def stage(self, text: str):
        elapsed = self._elapsed()
        print(f"{self.BOLD}{self.BLUE}▸ {text}{self.RESET} {self.DIM}[{elapsed}]{self.RESET}")

    def status(self, text: str):
        print(f"  {self.DIM}{text}{self.RESET}")

    def success(self, text: str):
        print(f"  {self.GREEN}✓ {text}{self.RESET}")

    def error(self, text: str):
        print(f"  {self.RED}✗ {text}{self.RESET}", file=sys.stderr)

    def warning(self, text: str):
        print(f"  {self.YELLOW}⚠ {text}{self.RESET}")

    def info(self, text: str):
        print(f"  {text}")

    def debug(self, text: str):
        print(f"  {self.DIM}{self.CYAN}[debug]{self.RESET} {self.DIM}{text}{self.RESET}", file=sys.stderr)

    def human_gate(self, text: str):
        print(f"\n{self.BOLD}{self.YELLOW}{'─'*60}{self.RESET}")
        print(f"{self.BOLD}{self.YELLOW}  🔒 {text}{self.RESET}")
        print(f"{self.BOLD}{self.YELLOW}{'─'*60}{self.RESET}")
        # Terminal bell
        print("\a", end="", flush=True)

    def cost_update(self, total_cost: float):
        print(f"  {self.DIM}Running cost: ${total_cost:.4f}{self.RESET}")

    def cost_summary(self, total_cost: float):
        print(f"\n  {self.BOLD}Total pipeline cost: ${total_cost:.4f}{self.RESET}")

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

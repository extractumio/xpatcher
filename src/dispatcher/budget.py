"""Budget management for v2 pipeline.

Tracks costs per lane, per stage family, and total pipeline.
Enforces configurable budget caps with threshold-based behavior.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class BudgetCheckpoint:
    """A snapshot of budget state at a point in time."""
    scope: str  # lane name or "pipeline"
    cost_usd: float
    cap_usd: float
    pct: float
    timestamp: str = ""
    warning: str = ""

    def to_dict(self) -> dict:
        d = {"scope": self.scope, "cost_usd": self.cost_usd, "cap_usd": self.cap_usd, "pct": round(self.pct, 2)}
        if self.warning:
            d["warning"] = self.warning
        if self.timestamp:
            d["timestamp"] = self.timestamp
        return d


class BudgetManager:
    """Manages budget caps and threshold-based policy decisions."""

    WARN_PCT = 0.70
    TIGHTEN_PCT = 0.85
    BLOCK_PCT = 1.00

    def __init__(self, config: dict | None = None):
        self._config = config or {}
        self._costs: dict[str, float] = {}  # scope -> cumulative cost
        self._checkpoints: list[BudgetCheckpoint] = []

    def record_cost(self, scope: str, cost_usd: float) -> None:
        """Record cost against a scope (lane name or 'pipeline')."""
        self._costs[scope] = self._costs.get(scope, 0.0) + cost_usd
        # Always track pipeline total
        if scope != "pipeline":
            self._costs["pipeline"] = self._costs.get("pipeline", 0.0) + cost_usd

    def load_costs(self, costs: dict[str, float] | None) -> None:
        """Hydrate persisted costs."""
        self._costs = {}
        for scope, cost in (costs or {}).items():
            try:
                self._costs[scope] = float(cost)
            except (TypeError, ValueError):
                continue

    def get_cost(self, scope: str) -> float:
        return self._costs.get(scope, 0.0)

    def get_cap(self, scope: str) -> float:
        """Get budget cap for a scope. Returns 0 if no cap configured."""
        budgets = self._config.get("budgets", {})
        # Direct scope match
        if scope in budgets:
            return float(budgets[scope])
        # Strip task_exec:task-XXX to task_exec
        base = scope.split(":")[0]
        if base in budgets:
            return float(budgets[base])
        return 0.0

    def remaining(self, scope: str) -> float | None:
        """Return remaining budget for a scope, or None when uncapped."""
        cap = self.get_cap(scope)
        if cap <= 0:
            return None
        return max(0.0, cap - self.get_cost(scope))

    _MAX_CHECKPOINTS = 100

    def check(self, scope: str) -> BudgetCheckpoint:
        """Check budget status for a scope.

        Returns a BudgetCheckpoint with warning if threshold crossed.
        """
        cost = self.get_cost(scope)
        cap = self.get_cap(scope)
        if cap <= 0:
            return BudgetCheckpoint(scope=scope, cost_usd=cost, cap_usd=0.0, pct=0.0)

        pct = cost / cap
        warning = ""
        if pct >= self.BLOCK_PCT:
            warning = f"Budget exhausted for {scope}: ${cost:.2f} / ${cap:.2f}"
        elif pct >= self.TIGHTEN_PCT:
            warning = f"Budget near limit for {scope}: ${cost:.2f} / ${cap:.2f} ({pct:.0%})"
        elif pct >= self.WARN_PCT:
            warning = f"Budget warning for {scope}: ${cost:.2f} / ${cap:.2f} ({pct:.0%})"

        cp = BudgetCheckpoint(scope=scope, cost_usd=cost, cap_usd=cap, pct=pct, warning=warning)
        if warning:
            cp.timestamp = datetime.now(timezone.utc).isoformat()
            if len(self._checkpoints) < self._MAX_CHECKPOINTS:
                self._checkpoints.append(cp)
        return cp

    def should_block(self, scope: str) -> bool:
        """Return True if the scope has exceeded its budget cap."""
        cap = self.get_cap(scope)
        if cap <= 0:
            return False
        return self.get_cost(scope) >= cap

    def should_tighten_retry(self, scope: str) -> bool:
        """Return True if near budget cap (85%+) — prefer minimal retries."""
        cap = self.get_cap(scope)
        if cap <= 0:
            return False
        return self.get_cost(scope) / cap >= self.TIGHTEN_PCT

    def max_retries(self, scope: str, default: int = 2) -> int:
        """Return allowed retries based on budget state."""
        if self.should_block(scope):
            return 0
        if self.should_tighten_retry(scope):
            return 1
        return default

    def get_checkpoints(self) -> list[dict]:
        """Return all recorded budget checkpoints."""
        return [cp.to_dict() for cp in self._checkpoints]

    def get_all_costs(self) -> dict[str, float]:
        """Return all costs by scope."""
        return dict(self._costs)

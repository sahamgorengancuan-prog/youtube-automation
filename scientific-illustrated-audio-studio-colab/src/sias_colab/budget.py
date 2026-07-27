"""BudgetGuardian — the engine ledger extended with Higgsfield calls, reported
provider costs, and a paid-call permission check that enforces ALL safety
conditions (§7): arm switch + secret + mode allows + budget + previous gate +
explicit user action."""

from __future__ import annotations

from typing import Any

from sias.budget import BudgetLedger
from sias.schemas import RunBudget

from .exceptions import BudgetExceededError, PaidCallBlockedError


class BudgetGuardian(BudgetLedger):
    def __init__(self, budget: RunBudget, hard_stop: bool = True, max_higgsfield_calls: int = 5):
        super().__init__(budget, hard_stop)
        self.max_higgsfield_calls = max_higgsfield_calls
        self.higgsfield_calls = 0
        self.reported_costs_usd: list[float] = []

    def can_afford(self, kind: str, amount: float = 1) -> bool:  # extend engine kinds
        if kind == "higgsfield":
            return self.higgsfield_calls + amount <= self.max_higgsfield_calls
        return super().can_afford(kind, amount)

    def charge(self, kind: str, amount: float = 1, note: str = "") -> None:
        if kind == "higgsfield":
            allowed = self.can_afford(kind, amount)
            self.decisions.append({"kind": kind, "amount": amount, "allowed": allowed, "note": note})
            if not allowed:
                if self.hard_stop:
                    raise BudgetExceededError(
                        f"higgsfield call cap would be exceeded (+{amount})", stage="budget"
                    )
                return
            self.higgsfield_calls += int(amount)
            return
        super().charge(kind, amount, note)

    def report_cost(self, usd: float) -> None:
        if usd and usd > 0:
            self.reported_costs_usd.append(float(usd))

    def snapshot(self) -> dict[str, Any]:
        snap = super().snapshot()
        snap["higgsfield_calls"] = self.higgsfield_calls
        snap["max_higgsfield_calls"] = self.max_higgsfield_calls
        snap["reported_cost_usd"] = round(sum(self.reported_costs_usd), 4)
        return snap


def assert_paid_call_allowed(
    stage: str,
    *,
    arm_paid_calls: bool,
    secret_present: bool,
    mode_allows_stage: bool,
    budget_ok: bool,
    previous_gate_passed: bool,
    user_action_confirmed: bool,
) -> None:
    """Every condition must hold or the paid call is refused with the exact
    reason — no generic 'something went wrong'."""
    reasons = []
    if not arm_paid_calls:
        reasons.append("ARM_PAID_CALLS is false")
    if not secret_present:
        reasons.append("required secret missing")
    if not mode_allows_stage:
        reasons.append("stage not allowed by current run mode")
    if not budget_ok:
        reasons.append("incremental budget unavailable")
    if not previous_gate_passed:
        reasons.append("previous quality gate has not passed")
    if not user_action_confirmed:
        reasons.append("no explicit user action recorded")
    if reasons:
        raise PaidCallBlockedError("; ".join(reasons), stage=stage)

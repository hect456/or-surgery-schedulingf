"""
penalty.py — non-scheduling penalty w_c for the objective function.

  w_c = multiplier[p_c] * PenaltyFactor(dd_c)  +  1.2 * max_{c in C}(dd_c)

dd_c is the real number of days of slack left to a case's clinical deadline
(negative if already overdue, see PlanningInstance.days_to_deadline).
PenaltyFactor is a piecewise curve over that real value — its breakpoints
always mean "this many actual days overdue", for every priority tier. The
priority multiplier scales the curve's *output*, once, here. Nowhere else
in the codebase should a solver multiply a penalty by priority again — w_c
returned by compute_all_penalties() is already the complete value to use
in the objective.

The curve's shape — flat for a while, then escalating sharply once a case
crosses its deadline — is the standard way these models penalise missed
clinical wait-time targets; see Marques & Captivo (2015) for one published
instantiation. The breakpoints below are a sensible default, not a fixed
law: a hospital adopting this model would calibrate them against its own
clinical risk tolerance.

The 1.2x displacement term guarantees the non-scheduling penalty is always
larger than any tardiness coefficient a scheduled case could accrue, so the
solver only ever leaves a case unscheduled when there's genuinely no room
for it — never as a cheaper way to dodge a tardiness charge.
"""

from __future__ import annotations
from typing import Dict

from .types import SurgicalCase, PlanningInstance


def penalty_factor_curve(days_to_deadline: int) -> float:
    """Piecewise penalty factor, in priority-1-equivalent overdue days."""
    d = days_to_deadline
    if d >= 90:
        return 50
    elif d >= 60:
        return 100
    elif d >= 45:
        return 200
    elif d >= 30:
        return 250
    elif d >= 15:
        return 550
    elif d >= 0:
        return 800
    elif d >= -15:
        return 1000
    elif d >= -30:
        return 1500
    elif d >= -45:
        return 2000
    else:
        return 2000 + 20 * abs(d + 45)


def compute_penalty(
    instance: PlanningInstance,
    case: SurgicalCase,
    max_days_to_deadline: float,
) -> float:
    """w_c for one case, under this instance's priority policy."""
    mult = instance.priority_multiplier[case.priority]
    dtd = instance.days_to_deadline(case)
    fp = penalty_factor_curve(int(round(dtd)))
    displacement = 1.2 * max_days_to_deadline
    return mult * fp + displacement


def compute_all_penalties(instance: PlanningInstance) -> Dict[str, float]:
    """Return {case_id: w_c} for every case in the instance."""
    cases = instance.cases
    if not cases:
        return {}
    max_dtd = max(instance.days_to_deadline(c) for c in cases)
    return {c.id: compute_penalty(instance, c, max_dtd) for c in cases}

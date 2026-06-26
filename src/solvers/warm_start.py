"""
warm_start.py — Greedy-derived warm start, shared by every exact backend.

A fast constructive heuristic gives every exact solver (CBC, Gurobi, CP-SAT) a
feasible starting point to search around instead of starting from nothing —
standard production practice once an instance is large enough that "first
solution" matters. We hint only the *discrete assignment* decisions (which
case goes to which day+room, and which cases are left unscheduled) — not
exact clock times. The discrete assignment is where the real combinatorial
difficulty lives; hinting exact start times as well risks handing CP-SAT an
internally-inconsistent hint, because the greedy heuristic checks surgeon
workload as a daily *sum*, the same approximation the baseline uses, not
true cross-room non-overlap. Letting each solver work out feasible timings
from the hinted assignment avoids that risk entirely.
"""

from __future__ import annotations
from typing import Dict, Set, Tuple

from ..model.types import PlanningInstance


def greedy_warm_start(instance: PlanningInstance) -> Tuple[Dict[str, Tuple[str, str]], Set[str]]:
    """
    Returns (assigned, unscheduled):
      assigned[case_id]   = (day, room_id) for every case the greedy heuristic placed
      unscheduled         = set of case_ids the greedy heuristic could not place
    """
    from .greedy_solver import GreedySolver
    result = GreedySolver().solve(instance)
    assigned = {a.case_id: (a.day, a.room_id) for a in result.assignments}
    unscheduled = set(result.unscheduled_case_ids)
    return assigned, unscheduled

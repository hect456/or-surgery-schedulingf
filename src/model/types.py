"""
types.py — Core data structures for the elective surgery scheduling problem.

Plain dataclasses, no ORM and no solver imports, so the model is testable
and readable on its own before any solver touches it.

The priority tiers and their maximum waits (DEFAULT_MAX_WAIT_DAYS below)
follow the same shape used by several public health systems to manage
elective waiting lists — Portugal's SIGIC, the UK NHS's RTT targets,
Canadian provincial wait-time benchmarks. All of them rank cases by
clinical urgency and track how badly each tier's deadline gets missed,
rather than running one undifferentiated FIFO queue. The actual numbers
here are a reasonable starting point, not a hard requirement — every value
is a PlanningInstance field a hospital can override with its own policy.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Dict, List, Optional, Tuple


# ──────────────────────────────────────────────────────────────
# Enumerations
# ──────────────────────────────────────────────────────────────

class Priority(IntEnum):
    """
    Clinical priority of a case. Higher = more urgent, shorter maximum
    clinically-acceptable wait. Level 4 is treated as a hard constraint
    (must be scheduled on the first day of the horizon) rather than a
    soft tardiness penalty, since by the time the case reaches the
    planner its remaining slack is already (near) zero.
    """
    ROUTINE        = 1   # long elective wait acceptable
    ELEVATED       = 2   # shorter wait window
    URGENT         = 3   # short wait window
    EMERGENT_ADDON = 4   # must be done on day 1 of the horizon


class SurgeryScope(IntEnum):
    CONVENTIONAL = 1   # inpatient, overnight stay expected
    AMBULATORY   = 2   # day-case, same-day discharge


# ──────────────────────────────────────────────────────────────
# Default parameters (evidence-informed, instance-overridable)
# ──────────────────────────────────────────────────────────────

# Maximum clinically-acceptable waiting days per priority level.
# Defaults are loosely modelled on public-system wait-time tiers
# (e.g. SIGIC Portaria n.º 45/2008); treat as a configurable starting
# point, not a universal constant — every PlanningInstance can override it.
DEFAULT_MAX_WAIT_DAYS: Dict[Priority, int] = {
    Priority.ROUTINE:        270,
    Priority.ELEVATED:        60,
    Priority.URGENT:           15,
    Priority.EMERGENT_ADDON:    3,
}

# Relative penalty multipliers for the tardiness objective.
# Interpretation: 1 overdue day at priority p ≡ MULTIPLIER[p] overdue
# days at priority ROUTINE. Same evidence basis as above.
DEFAULT_PRIORITY_MULTIPLIER: Dict[Priority, float] = {
    Priority.ROUTINE:         1.0,
    Priority.ELEVATED:        4.5,
    Priority.URGENT:         18.0,
    Priority.EMERGENT_ADDON: 90.0,
}

DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri"]   # D = {1..5}, one work week


# ──────────────────────────────────────────────────────────────
# Core entities
# ──────────────────────────────────────────────────────────────

@dataclass
class Surgeon:
    """
    h ∈ H. A surgeon is identified by ID and constrained by daily/weekly
    operative time limits (k_{hd}^{day} and k_h^{week} in FORMULATION.md).
    Besides those two minute budgets, the solver also enforces an exact
    NoOverlap over the surgeon's own time windows (C8) — the budgets alone
    don't stop a surgeon from being double-booked across two rooms at the
    same minute, they only cap total hours.
    """
    id: str
    name: str
    service: str                          # s_c — surgical service / specialty
    daily_limit_min: int   = 240          # k_{hd}^{day} (minutes)
    weekly_limit_min: int  = 960          # k_h^{week}   (minutes)
    # availability[day] = True if surgeon available that day
    availability: Dict[str, bool] = field(default_factory=lambda: {d: True for d in DAYS})


@dataclass
class OperatingRoom:
    """
    r ∈ R_b for block b ∈ B.
    k_{dbr} = capacity in minutes on day d.
    service_assignment[day] = service code that owns the room that day
    (the room-service roster / "block schedule").
    """
    id: str
    block: str                            # b ∈ B
    service_assignment: Dict[str, str]    # day → service code ("" = unassigned)
    capacity_min: Dict[str, int]          # day → minutes available
    ambulatory_only: bool = False         # room restricted to day-case procedures


@dataclass
class SurgicalCase:
    """
    c ∈ C — one patient-procedure pair on the elective waiting list.

    Key time parameters:
      t_cir   = operative time (surgeon + room occupied)
      t_clean = room turnover/cleaning time after the case — instances.py
                sets this from procedure length rather than a flat constant
                (longer cases tend to need a longer reset and instrument
                changeover); the field itself is just a plain number, the
                solver doesn't care how it was derived
      t_tot   = t_cir + t_clean  (total room occupation time)

    Optional resources (see PlanningInstance for capacities):
      equipment      — shared equipment unit required (e.g. a C-arm /
                        imaging unit), or None if the case needs none.
      recovery_type  — downstream bed pool required after surgery
                        ("none" by default; e.g. "icu", "ward").
      recovery_los_days — length of stay in that bed pool, in days.
    """
    id: str
    patient_id: str
    service: str
    surgeon_id: str
    priority: Priority
    scope: SurgeryScope
    patient_age: int

    # Time parameters (minutes)
    t_cir: int
    t_clean: int = 20

    # Waiting-list entry (days already waited before the planning horizon)
    days_waiting: int = 0

    # Optional shared resources
    equipment: Optional[str] = None
    recovery_type: str = "none"
    recovery_los_days: int = 0

    @property
    def t_tot(self) -> int:
        """Total room occupation: t_c^{cir} + t_c^{clean}."""
        return self.t_cir + self.t_clean

    @property
    def must_schedule_day1(self) -> bool:
        """Priority EMERGENT_ADDON must be scheduled on the first planning day."""
        return self.priority == Priority.EMERGENT_ADDON

    @property
    def needs_recovery_bed(self) -> bool:
        return self.recovery_type != "none" and self.recovery_los_days > 0


@dataclass
class PlanningInstance:
    """
    Complete problem instance: all sets and parameters needed by any solver.
    Mirrors the mathematical sets C, D, B, R_b, S, H from FORMULATION.md.

    max_wait_days / priority_multiplier are instance-level so each hospital
    can plug in its own waiting-list policy without touching solver code —
    this is the "adaptable" part of the model (see DEFAULT_* above for the
    evidence-informed starting point we ship with).
    """
    name: str
    cases: List[SurgicalCase]
    surgeons: List[Surgeon]
    rooms: List[OperatingRoom]
    days: List[str] = field(default_factory=lambda: list(DAYS))

    max_wait_days: Dict[Priority, int] = field(
        default_factory=lambda: dict(DEFAULT_MAX_WAIT_DAYS))
    priority_multiplier: Dict[Priority, float] = field(
        default_factory=lambda: dict(DEFAULT_PRIORITY_MULTIPLIER))

    # Optional shared resources. Empty dict = resource unconstrained/unused.
    # equipment_capacity[(equipment_id, day)]   = units available that day
    # bed_capacity[(recovery_type, day)]        = beds available that day
    equipment_capacity: Dict[Tuple[str, str], int] = field(default_factory=dict)
    bed_capacity: Dict[Tuple[str, str], int] = field(default_factory=dict)

    # Optional site-specific room-day eligibility rule: (service, day, age_limit).
    # On that day, that service's rooms may only host patients with
    # patient_age <= age_limit (e.g. a designated paediatric block).
    # None = rule disabled. This shows how an ad hoc institutional carve-out
    # plugs in as one extra eligibility predicate without touching the core
    # formulation — real hospitals accumulate rules like this constantly.
    pediatric_block: Optional[Tuple[str, str, int]] = None

    alpha: float = 2.0                 # α > 1: urgency multiplier for overdue cases

    # Bed capacity is constant across the week. A stay starting late in the
    # horizon (e.g. Friday, 2-day length of stay) can run past the modeled
    # week into what would be the weekend, which typically runs on a
    # reduced staff roster in real hospitals. Instead of silently ignoring
    # that or forbidding it outright, every day of a stay past the horizon
    # is charged this penalty in the objective (0 disables it). This is a
    # policy knob a hospital sets, not a value derived from any source.
    weekend_bed_overflow_penalty: float = 50.0

    # Sequence-dependent room turnover. Only the optional CP Optimizer
    # backend (src/solvers/cp_optimizer_solver.py) uses these — it charges
    # turnover as a cost between whichever two cases land next to each
    # other in a room, rather than baking a flat t_clean into every case.
    # The primary CP-SAT model ignores both fields.
    same_service_turnover_min: int = 15    # back-to-back cases, same service
    cross_service_turnover_min: int = 35   # service switch — full changeover

    def __post_init__(self):
        self._validate()

    def _validate(self):
        surgeon_ids = {s.id for s in self.surgeons}
        for c in self.cases:
            assert c.surgeon_id in surgeon_ids, \
                f"Case {c.id}: unknown surgeon {c.surgeon_id}"
        assert self.alpha > 1.0, "alpha must be > 1"

    # ── Convenience lookups ──────────────────────
    @property
    def cases_by_id(self) -> Dict[str, SurgicalCase]:
        return {c.id: c for c in self.cases}

    @property
    def surgeons_by_id(self) -> Dict[str, Surgeon]:
        return {s.id: s for s in self.surgeons}

    @property
    def rooms_by_id(self) -> Dict[str, OperatingRoom]:
        return {r.id: r for r in self.rooms}

    def valid_days(self, case: SurgicalCase) -> List[str]:
        """D_c: days on which case c may be scheduled."""
        if case.must_schedule_day1:
            return [self.days[0]]
        return list(self.days)

    def room_service_match(self, room: OperatingRoom, case: SurgicalCase, day: str) -> bool:
        """a_{dbr}^s: True if room r is assigned to the service of case c on day d."""
        svc = room.service_assignment.get(day, "")
        return svc == case.service

    def violates_pediatric_block(self, case: SurgicalCase, day: str) -> bool:
        """True if scheduling this case on this day would breach the
        optional pediatric-block eligibility rule (see pediatric_block)."""
        if self.pediatric_block is None:
            return False
        svc, blocked_day, age_limit = self.pediatric_block
        return day == blocked_day and case.service == svc and case.patient_age > age_limit

    # ── Priority / deadline accessors (instance-configured) ──────────
    def max_wait(self, case: SurgicalCase) -> int:
        return self.max_wait_days[case.priority]

    def days_to_deadline(self, case: SurgicalCase) -> int:
        """dd_c - d_1: positive = days of slack left, negative = already overdue."""
        return self.max_wait(case) - case.days_waiting

    def is_overdue(self, case: SurgicalCase) -> bool:
        return self.days_to_deadline(case) < 0

    def has_equipment_limits(self) -> bool:
        return bool(self.equipment_capacity)

    def has_bed_limits(self) -> bool:
        return bool(self.bed_capacity) and any(c.needs_recovery_bed for c in self.cases)


# ──────────────────────────────────────────────────────────────
# Result types
# ──────────────────────────────────────────────────────────────

@dataclass
class Assignment:
    """
    A scheduled surgery: case c -> day d, room r.

    start_min / end_min are optional clock times *within the day*
    (minutes from room opening). The baseline MILP only reasons at
    day+room granularity and leaves these as None; the interval-based
    CP-SAT production model fills them in, since it schedules exact
    start times — this is the concrete, reportable difference between
    the two formulations.
    """
    case_id: str
    day: str
    room_id: str
    start_min: Optional[int] = None
    end_min: Optional[int] = None


@dataclass
class SolverResult:
    status: str                              # "Optimal", "Feasible", "Infeasible", etc.
    objective_value: Optional[float]
    assignments: List[Assignment]
    unscheduled_case_ids: List[str]
    solve_time_sec: float
    solver_name: str
    gap: Optional[float] = None              # MIP/CP gap (if available)

    def is_optimal(self) -> bool:
        return self.status.lower() in {"optimal", "feasible"}

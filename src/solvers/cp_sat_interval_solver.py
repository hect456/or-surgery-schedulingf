"""
cp_sat_interval_solver.py — the model this project is built around: an
interval-based constraint program, solved with Google OR-Tools CP-SAT.
FORMULATION.md has the reasoning behind picking CP over a MILP for this
problem; FORMULATION_CP.md has the full variables/objective/constraints
math (C1-C11). Constraint numbers in the comments below match that
document, so the two can be read side by side.

The short version of why CP-SAT and not a MILP: a linear capacity-sum
constraint ("total minutes used today <= room capacity") only checks that
a set of durations fits a day — it doesn't check that they can be placed
without colliding, and those are different statements once a resource is
shared across more than one room (a surgeon covering two rooms, a shared
imaging unit). CP-SAT's interval variables (start, size, end, optional
presence) plus AddNoOverlap / AddCumulative check actual time overlap
directly, which is what this problem is — a disjunctive resource-scheduling
problem, the textbook use case for those constraints.
"""

from __future__ import annotations
import os
from collections import defaultdict
from typing import Dict, Tuple

from ortools.sat.python import cp_model

from ..model.types import PlanningInstance, Assignment, SolverResult, Priority
from ..model.penalty import compute_all_penalties
from .base_solver import BaseSolver


class CPSATIntervalSolver(BaseSolver):
    """
    Interval-based CP-SAT model — the primary solver for this project.

    Variables (per feasible case/day/room slot — room-service match,
    ambulatory-only, pediatric block, surgeon availability already
    filtered out):
      presence[c,d,r]        : bool — case c assigned to (d, r)
      start[c,d,r]           : int  — start time in minutes from room opening
      end[c,d,r]             : int  — room-end (= start + t_tot when present)
      interval[c,d,r]        : optional interval [start, start+t_tot) —
                                ROOM occupancy (operative + cleaning time)
      surgeon_end[c,d,r]     : int  — surgeon-end (= start + t_cir)
      surgeon_interval[c,d,r]: optional interval [start, start+t_cir) —
                                the SURGEON's own time in the case, used
                                for the surgeon NoOverlap (C8) so the
                                surgeon can start a case in a different
                                room while this room is still being
                                cleaned — see C8 in FORMULATION_CP.md

    unscheduled[c] : bool — case c not scheduled (non-emergent cases only)
    day_of[c]      : int  — day index of c's surgery (only built for cases
                     that consume a downstream recovery bed)
    """

    name = "CP-SAT/Interval"

    def __init__(self, time_limit_sec: int = 120, mip_gap: float = 0.01,
                 log_search_progress: bool = False):
        super().__init__(time_limit_sec, mip_gap)
        self.log_search_progress = log_search_progress

    def _build_and_solve(self, instance: PlanningInstance) -> SolverResult:
        model = cp_model.CpModel()

        cases = instance.cases
        rooms = instance.rooms
        days = instance.days
        alpha = instance.alpha
        case_map = instance.cases_by_id
        surg_map = instance.surgeons_by_id
        penalties = compute_all_penalties(instance)
        # O(1) lookup for day-name → index; passed into recovery-bed builder
        # so it avoids calling list.index() in an inner loop.
        day_index = {d: i for i, d in enumerate(days)}

        # ── Feasible (c, d, r) candidate slots — same filter as baseline ──
        candidates = []
        for c in cases:
            for d in instance.valid_days(c):
                for r in rooms:
                    if not instance.room_service_match(r, c, d):
                        continue  # C4: room-service roster
                    if r.ambulatory_only and c.scope.value != 2:
                        continue  # C5: ambulatory-only rooms
                    if instance.violates_pediatric_block(c, d):
                        continue  # C6: pediatric-block rule
                    if not surg_map[c.surgeon_id].availability.get(d, True):
                        continue
                    candidates.append((c.id, d, r.id))

        # ── Interval variables ───────────────────────────────────────────
        # Two intervals per candidate slot, sharing the same start time but
        # different sizes — NOT one interval reused for both room and
        # surgeon constraints:
        #   interval[key]          : [start, start + t_tot)  — room occupancy
        #                             (operative time + cleaning/turnover)
        #   surgeon_interval[key]  : [start, start + t_cir)  — surgeon's own
        #                             time in the case (no cleaning)
        # The room needs to stay blocked for t_tot, but the surgeon is free
        # again as soon as t_cir ends — they can scrub into a different room
        # while this one is still being cleaned by nursing/support staff. If
        # both NoOverlap constraints used the same t_tot-sized interval, that
        # move would be wrongly forbidden. See FORMULATION_CP.md C8.
        presence: Dict[Tuple[str, str, str], object] = {}
        start: Dict[Tuple[str, str, str], object] = {}
        end: Dict[Tuple[str, str, str], object] = {}
        interval: Dict[Tuple[str, str, str], object] = {}
        surgeon_end: Dict[Tuple[str, str, str], object] = {}
        surgeon_interval: Dict[Tuple[str, str, str], object] = {}
        room_caps = {r.id: r.capacity_min for r in rooms}

        for (cid, d, rid) in candidates:
            c = case_map[cid]
            cap = room_caps[rid].get(d, 0)
            key = (cid, d, rid)
            presence[key] = model.NewBoolVar(f"pr_{cid}_{d}_{rid}")
            # Tighter upper bounds: start can be at most cap - t_tot (otherwise
            # the case would overflow the room's open time even if it starts alone).
            # CP-SAT would eventually infer this through interval propagation, but
            # providing it upfront reduces the initial domain and speeds up pruning.
            start_ub = max(0, cap - c.t_tot)
            end_ub = max(c.t_tot, cap)   # end = start + t_tot ≤ cap (tight)
            surg_end_ub = max(c.t_cir, cap)
            start[key] = model.NewIntVar(0, start_ub, f"st_{cid}_{d}_{rid}")
            end[key] = model.NewIntVar(c.t_tot, end_ub, f"en_{cid}_{d}_{rid}")
            interval[key] = model.NewOptionalIntervalVar(
                start[key], c.t_tot, end[key], presence[key], f"iv_{cid}_{d}_{rid}"
            )
            surgeon_end[key] = model.NewIntVar(c.t_cir, surg_end_ub, f"sgend_{cid}_{d}_{rid}")
            surgeon_interval[key] = model.NewOptionalIntervalVar(
                start[key], c.t_cir, surgeon_end[key], presence[key], f"sgiv_{cid}_{d}_{rid}"
            )

        # ── is_scheduled / unscheduled bookkeeping ───────────────────────
        # C2 (priority-4 locked to day 1) and C3 (schedule-or-penalise).
        is_scheduled: Dict[str, object] = {}
        unscheduled: Dict[str, object] = {}
        for c in cases:
            slots = [presence[k] for k in candidates if k[0] == c.id]
            if c.priority == Priority.EMERGENT_ADDON:
                d1 = days[0]
                d1_slots = [presence[(c.id, d, rid)] for (cid, d, rid) in candidates
                            if cid == c.id and d == d1]
                if d1_slots:
                    model.Add(sum(d1_slots) == 1)  # C2
                # also forbid any non-day-1 slot for this case
                other_slots = [presence[k] for k in candidates
                               if k[0] == c.id and k[1] != d1]
                for s in other_slots:
                    model.Add(s == 0)
                is_scheduled[c.id] = 1  # constant: always scheduled
            else:
                u = model.NewBoolVar(f"unsched_{c.id}")
                unscheduled[c.id] = u
                if slots:
                    model.Add(sum(slots) + u == 1)
                else:
                    model.Add(u == 1)
                sched = model.NewBoolVar(f"sched_{c.id}")
                model.Add(sched == 1 - u)
                is_scheduled[c.id] = sched

        # ── C1: at most one scheduled occurrence per patient per week ───
        patient_cases: Dict[str, list] = defaultdict(list)
        for c in cases:
            patient_cases[c.patient_id].append(c.id)
        for pid, cids in patient_cases.items():
            slots = [presence[k] for k in candidates if k[0] in set(cids)]
            if slots:
                model.Add(sum(slots) <= 1)

        # ── C7: room capacity via exact NoOverlap (replaces day-aggregate) ─
        by_room_day: Dict[Tuple[str, str], list] = defaultdict(list)
        for k in candidates:
            cid, d, rid = k
            by_room_day[d, rid].append(interval[k])
        for ivs in by_room_day.values():
            if len(ivs) > 1:
                model.AddNoOverlap(ivs)

        # ── C8: surgeon — exact NoOverlap on the SURGEON's own interval
        # (size t_cir, not the room's t_tot) + daily cap. Using the
        # surgeon-only interval is what lets the surgeon move to a
        # different room while this room is still being cleaned.
        by_surgeon_day: Dict[Tuple[str, str], list] = defaultdict(list)
        for k in candidates:
            cid, d, rid = k
            by_surgeon_day[case_map[cid].surgeon_id, d].append(surgeon_interval[k])
        for ivs in by_surgeon_day.values():
            if len(ivs) > 1:
                model.AddNoOverlap(ivs)

        for s in instance.surgeons:
            for d in days:
                if not s.availability.get(d, True):
                    continue
                terms = [case_map[cid].t_cir * presence[(cid, d2, rid)]
                         for (cid, d2, rid) in candidates
                         if d2 == d and case_map[cid].surgeon_id == s.id]
                if terms:
                    model.Add(sum(terms) <= s.daily_limit_min)

        # ── C9: surgeon weekly time limit (a separate minute budget — the
        # NoOverlap above stops double-booking, this caps total hours) ───
        for s in instance.surgeons:
            terms = [case_map[cid].t_cir * presence[(cid, d, rid)]
                     for (cid, d, rid) in candidates if case_map[cid].surgeon_id == s.id]
            if terms:
                model.Add(sum(terms) <= s.weekly_limit_min)

        # ── C10: shared equipment — exact AddCumulative (replaces day-count) ─
        if instance.has_equipment_limits():
            equip_ids = {e for (e, _d) in instance.equipment_capacity}
            for e in equip_ids:
                for d in days:
                    cap = instance.equipment_capacity.get((e, d))
                    if cap is None:
                        continue
                    ivs = [interval[k] for k in candidates
                           if k[1] == d and case_map[k[0]].equipment == e]
                    if ivs:
                        model.AddCumulative(ivs, [1] * len(ivs), cap)

        # ── Objective, part 1/2: the three-term tardiness formula, over
        # `presence` (built first so C11 below can append its overflow
        # penalty term to the same list) ─────────────────────────────────
        objective_terms = []
        for c in cases:
            dtd = instance.days_to_deadline(c)
            for d_idx, d in enumerate(instance.valid_days(c)):
                d_val = d_idx + 1
                for r in rooms:
                    key = (c.id, d, r.id)
                    if key not in presence:
                        continue
                    coeff = (dtd + d_val) if dtd >= 0 else (dtd + alpha * d_val)
                    objective_terms.append(int(round(coeff)) * presence[key])
        for cid, u in unscheduled.items():
            # penalties[cid] already includes the priority multiplier
            # (penalty.py) — don't multiply by priority again here.
            objective_terms.append(int(round(penalties[cid])) * u)

        # ── C11: downstream recovery/ICU bed AddCumulative ───────────────
        # Day-granularity resource: a case occupies a bed from its surgery
        # day for `recovery_los_days` days. Needs an interval representation
        # to state at all — a day-bucket model has no variable that means
        # "the day this case happens", only a fixed index a binary is
        # attached to. Also appends the weekend-overflow penalty (see
        # PlanningInstance docstring) for any stay extending past the
        # horizon.
        if instance.has_bed_limits():
            self._add_recovery_bed_constraints(
                model, instance, candidates, presence, is_scheduled,
                objective_terms, day_index
            )
        model.Minimize(sum(objective_terms))

        # ── Greedy warm-start hints ───────────────────────────────────────
        # Provide CP-SAT with a first-fit greedy assignment as search hints.
        # Hints are advisory: the solver ignores any that lead to conflict.
        # Even a rough hint improves the first incumbent significantly, which
        # lets the gap close faster within a fixed time budget.
        self._apply_greedy_hints(
            model, instance, candidates, presence, start, unscheduled, room_caps
        )

        # ── Solve ─────────────────────────────────────────────────────────
        # CP-SAT's default parallel portfolio (several complete-search
        # workers plus large-neighbourhood-search workers improving an
        # incumbent, sharing learned clauses) is the actual search engine
        # here — there's no hand-written branching strategy on top of it.
        # Capped at the machine's core count since oversubscribing workers
        # past physical cores adds contention, not search diversity.
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = float(self.time_limit_sec)
        solver.parameters.relative_gap_limit = self.mip_gap
        # Workers capped at 16 — beyond that, clause-sharing overhead outweighs
        # added search diversity on instances of this size.
        solver.parameters.num_search_workers = min(16, os.cpu_count() or 4)
        # linearization_level = 2: enables stronger LP relaxation cuts, which
        # tighten the lower bound faster and close the gap more aggressively.
        # Default is 1; increasing to 2 costs more per iteration but pays off
        # on medium-to-large instances where bound quality matters most.
        solver.parameters.linearization_level = 2
        solver.parameters.log_search_progress = self.log_search_progress
        status = solver.Solve(model)

        status_map = {
            cp_model.OPTIMAL: "Optimal",
            cp_model.FEASIBLE: "Feasible",
            cp_model.INFEASIBLE: "Infeasible",
            cp_model.MODEL_INVALID: "ModelInvalid",
            cp_model.UNKNOWN: "Unknown",
        }
        status_str = status_map.get(status, "Unknown")

        assignments, unscheduled_ids = [], []
        obj_val = None
        if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            obj_val = solver.ObjectiveValue()
            for k in candidates:
                if solver.Value(presence[k]) == 1:
                    cid, d, rid = k
                    assignments.append(Assignment(
                        case_id=cid, day=d, room_id=rid,
                        start_min=solver.Value(start[k]),
                        end_min=solver.Value(end[k]),
                    ))
            for cid, u in unscheduled.items():
                if solver.Value(u) == 1:
                    unscheduled_ids.append(cid)

        # Always compute the gap, even when status == OPTIMAL: with
        # relative_gap_limit set (here: self.mip_gap), CP-SAT's OPTIMAL means
        # "proven within that tolerance," not necessarily a literal 0%, so
        # the actual number is worth reporting rather than assumed.
        gap = None
        if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            try:
                best = solver.BestObjectiveBound()
                if obj_val is not None and obj_val != 0:
                    gap = abs(obj_val - best) / max(abs(obj_val), 1e-9)
            except Exception:
                pass

        return SolverResult(
            status=status_str,
            objective_value=obj_val,
            assignments=assignments,
            unscheduled_case_ids=unscheduled_ids,
            solve_time_sec=0.0,   # filled by BaseSolver
            solver_name=self.name,
            gap=gap,
        )

    @staticmethod
    def _add_recovery_bed_constraints(model, instance, candidates, presence, is_scheduled,
                                       objective_terms, day_index):
        """
        C11: recovery/ICU bed AddCumulative, day-granularity.
        day_index is the pre-built {day_name: int_index} dict — avoids
        calling list.index() (O(n)) inside an inner loop over candidates.
        """
        n_days = len(instance.days)
        overflow_penalty = int(round(instance.weekend_bed_overflow_penalty))

        beds_by_type: Dict[str, list] = defaultdict(list)
        for c in instance.cases:
            if not c.needs_recovery_bed:
                continue
            day_of = model.NewIntVar(0, n_days - 1, f"dayof_{c.id}")
            for (cid, d, rid) in candidates:
                if cid != c.id:
                    continue
                # O(1) lookup instead of list.index() O(n)
                model.Add(day_of == day_index[d]).OnlyEnforceIf(presence[(cid, d, rid)])

            sched = is_scheduled[c.id]
            bed_start = day_of
            bed_end = model.NewIntVar(0, n_days - 1 + c.recovery_los_days, f"bedend_{c.id}")
            model.Add(bed_end == bed_start + c.recovery_los_days)
            bed_iv = model.NewOptionalIntervalVar(
                bed_start, c.recovery_los_days, bed_end, sched, f"bed_{c.id}"
            ) if not isinstance(sched, int) else model.NewIntervalVar(
                bed_start, c.recovery_los_days, bed_end, f"bed_{c.id}"
            )
            beds_by_type[c.recovery_type].append(bed_iv)

            # Horizon-boundary handling (see PlanningInstance.
            # weekend_bed_overflow_penalty docstring): bed_capacity is
            # constant across the week, but a stay starting late in the
            # horizon can run past day index n_days-1 (Friday) into what
            # would be the weekend — a regime this model doesn't have a
            # separate, lower capacity for. Rather than silently approximate
            # it, charge each overflow day in the objective instead of
            # forbidding or ignoring it.
            if overflow_penalty > 0:
                # bed_end is the EXCLUSIVE end of [bed_start, bed_end); day
                # indices 0..n_days-1 are inside the modeled week, so the
                # number of days at/after index n_days (the first
                # unmodeled, weekend-like day) is max(0, bed_end - n_days).
                overflow = model.NewIntVar(0, c.recovery_los_days, f"bedoverflow_{c.id}")
                model.AddMaxEquality(overflow, [0, bed_end - n_days])
                objective_terms.append(overflow_penalty * overflow)

        for rtype, ivs in beds_by_type.items():
            caps = [cap for (t, d), cap in instance.bed_capacity.items() if t == rtype]
            if not caps or not ivs:
                continue
            capacity = min(caps)  # constant-capacity assumption (see module docstring)
            model.AddCumulative(ivs, [1] * len(ivs), capacity)

    @staticmethod
    def _apply_greedy_hints(model, instance, candidates, presence, start,
                            unscheduled, room_caps):
        """
        Build a fast first-fit greedy assignment and provide it to CP-SAT
        as search hints via model.AddHint().

        The greedy packs cases into the first room-day slot with enough
        remaining capacity, processing cases in urgency order (P4 first,
        then by days_to_deadline ascending so the most overdue come first).
        Surgeon non-overlap is NOT checked — the hint may contain surgeon
        conflicts. CP-SAT silently ignores any infeasible hint values and
        uses the rest as a warm start. Even a partial, imperfect hint
        dramatically improves the quality of the first incumbent.
        """
        # Sort: EMERGENT_ADDON first, then most overdue, then most urgent tier
        sorted_cases = sorted(
            instance.cases,
            key=lambda c: (-c.priority.value, instance.days_to_deadline(c)),
        )

        # next_min[(room_id, day)] = minutes already committed in that slot
        next_min: Dict[Tuple[str, str], int] = defaultdict(int)
        placed: set = set()
        pr_hint: Dict[Tuple[str, str, str], int] = {}
        st_hint: Dict[Tuple[str, str, str], int] = {}

        for c in sorted_cases:
            case_cands = sorted(
                [(d, rid) for (cid, d, rid) in candidates if cid == c.id],
                key=lambda t: t[0],   # try earlier days first
            )
            for d, rid in case_cands:
                cap = room_caps[rid].get(d, 0)
                used = next_min[rid, d]
                if used + c.t_tot <= cap:
                    pr_hint[c.id, d, rid] = 1
                    st_hint[c.id, d, rid] = used
                    next_min[rid, d] = used + c.t_tot
                    placed.add(c.id)
                    break

        # Apply hints to the model
        for (cid, d, rid) in candidates:
            k = (cid, d, rid)
            model.AddHint(presence[k], pr_hint.get(k, 0))
            if k in st_hint:
                model.AddHint(start[k], st_hint[k])

        # Hint unscheduled flags
        for cid, u in unscheduled.items():
            model.AddHint(u, 0 if cid in placed else 1)

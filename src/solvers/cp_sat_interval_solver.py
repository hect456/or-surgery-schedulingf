"""
cp_sat_interval_solver.py — Interval-Based Constraint Programming model,
solved with Google OR-Tools CP-SAT. This is the PRIMARY formulation —
see FORMULATION.md, sections 3 (why CP, not MILP), 7-9 (variables,
objective, constraints C1-C11). Every constraint below carries the same
C-number as FORMULATION.md §9, so the two can be read side by side.

Why interval-based CP-SAT (FORMULATION.md §3, condensed)?
-----------------------------------------------------------
A linear capacity-sum constraint ("total minutes used this day <= k") only
certifies that a set of durations *fits*; it does not certify they can be
placed *without colliding*, and the two are not equivalent once a resource
is shared across rooms (a surgeon working two rooms, a shared imaging
unit). CP-SAT's interval variables (start, size, end, optional presence)
plus AddNoOverlap / AddCumulative are the textbook tool for this class of
problem (job-shop / RCPSP-family disjunctive scheduling): exact,
branch-and-bound-free disjunctive reasoning via specialised global-constraint
propagation (Vilim 2004 for NoOverlap; Schutt et al. 2009 for Cumulative),
not a bigger/slower MILP. See FORMULATION.md §3 for the full argument and
§12 for the alternative MILP formulation (milp_baseline_solver.py) kept
purely as the empirical comparison point that justifies this choice.
"""

from __future__ import annotations
import os
from collections import defaultdict
from typing import Dict, Tuple

from ortools.sat.python import cp_model

from ..model.types import PlanningInstance, Assignment, SolverResult, Priority
from ..model.penalty import compute_all_penalties
from .base_solver import BaseSolver
from .warm_start import greedy_warm_start


class CPSATIntervalSolver(BaseSolver):
    """
    Interval-based CP-SAT production model.

    Variables (per feasible case/day/room slot, same eligibility filter as
    the baseline: room-service match, ambulatory-only, pediatric block,
    surgeon availability):
      presence[c,d,r] : bool — case c assigned to (d, r)
      start[c,d,r]    : int  — start time in minutes from room opening
      end[c,d,r]      : int  — end time (= start + t_tot when present)
      interval[c,d,r] : optional interval, present iff presence[c,d,r]

    unscheduled[c] : bool — case c not scheduled (non-emergent cases only)
    day_of[c]      : int  — day index of c's surgery (only built for cases
                     that consume a downstream recovery bed)
    """

    name = "CP-SAT/Interval"

    def __init__(self, time_limit_sec: int = 120, mip_gap: float = 0.01,
                 warm_start: bool = True, log_search_progress: bool = False):
        super().__init__(time_limit_sec, mip_gap)
        self.warm_start = warm_start                 # seed search with the greedy assignment
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
        presence: Dict[Tuple[str, str, str], object] = {}
        start: Dict[Tuple[str, str, str], object] = {}
        end: Dict[Tuple[str, str, str], object] = {}
        interval: Dict[Tuple[str, str, str], object] = {}
        room_caps = {r.id: r.capacity_min for r in rooms}

        for (cid, d, rid) in candidates:
            c = case_map[cid]
            cap = room_caps[rid].get(d, 0)
            key = (cid, d, rid)
            presence[key] = model.NewBoolVar(f"pr_{cid}_{d}_{rid}")
            start[key] = model.NewIntVar(0, max(cap, 0), f"st_{cid}_{d}_{rid}")
            end[key] = model.NewIntVar(0, max(cap, 0), f"en_{cid}_{d}_{rid}")
            interval[key] = model.NewOptionalIntervalVar(
                start[key], c.t_tot, end[key], presence[key], f"iv_{cid}_{d}_{rid}"
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

        # ── C8: surgeon — exact NoOverlap (no double-booking) + daily cap ─
        by_surgeon_day: Dict[Tuple[str, str], list] = defaultdict(list)
        for k in candidates:
            cid, d, rid = k
            by_surgeon_day[case_map[cid].surgeon_id, d].append(interval[k])
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

        # ── C9: surgeon weekly time limit (unchanged from baseline) ──────
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

        # ── C11: downstream recovery/ICU bed AddCumulative ───────────────
        # Day-granularity resource: a case occupies a bed from its surgery
        # day for `recovery_los_days` days. Not expressible in the
        # alternative day-bucket MILP (FORMULATION.md §12) — needs an
        # interval representation to even state correctly.
        if instance.has_bed_limits():
            self._add_recovery_bed_constraints(model, instance, candidates, presence, is_scheduled)

        # ── Objective: identical three-term formula, over `presence` ────
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
            c = case_map[cid]
            objective_terms.append(int(round(c.priority.value * penalties[cid])) * u)
        model.Minimize(sum(objective_terms))

        # ── Warm start: seed CP-SAT's portfolio search with the greedy
        # heuristic's (day, room) assignment. Only the discrete `presence`
        # and `unscheduled` decisions are hinted, not exact start times —
        # see warm_start.py for why. This is standard production practice
        # once an instance is large enough that "first incumbent" matters;
        # CP-SAT treats AddHint as a bias, not a hard constraint, so an
        # inconsistent or partial hint never risks correctness.
        if self.warm_start:
            assigned, unsched = greedy_warm_start(instance)
            for key, var in presence.items():
                cid, d, rid = key
                model.AddHint(var, 1 if assigned.get(cid) == (d, rid) else 0)
            for cid, u in unscheduled.items():
                model.AddHint(u, 1 if cid in unsched else 0)

        # ── Solve ─────────────────────────────────────────────────────────
        # num_search_workers: CP-SAT's parallel portfolio (LNS + multiple
        # complete-search strategies) is the actual state-of-the-art engine
        # here — we deliberately do NOT hand-write a custom decision
        # strategy on top of it; OR-Tools' own guidance is that the default
        # portfolio outperforms manual search hints absent deep structural
        # knowledge the model doesn't have. Capped at the machine's core
        # count (here: 8) since oversubscribing workers past physical cores
        # only adds contention, not search diversity.
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = float(self.time_limit_sec)
        solver.parameters.relative_gap_limit = self.mip_gap
        solver.parameters.num_search_workers = min(8, os.cpu_count() or 8)
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
        # "proven within that tolerance," exactly like Gurobi's default
        # termination rule — NOT necessarily a literal zero gap. Reporting
        # this honestly (rather than assuming OPTIMAL implies 0%) is the
        # only way to catch cases like a warm start changing which
        # within-tolerance incumbent gets accepted first.
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
    def _add_recovery_bed_constraints(model, instance, candidates, presence, is_scheduled):
        days = instance.days
        case_map = instance.cases_by_id
        n_days = len(days)

        beds_by_type: Dict[str, list] = defaultdict(list)
        for c in instance.cases:
            if not c.needs_recovery_bed:
                continue
            day_of = model.NewIntVar(0, n_days - 1, f"dayof_{c.id}")
            for (cid, d, rid) in candidates:
                if cid != c.id:
                    continue
                model.Add(day_of == instance.days.index(d)).OnlyEnforceIf(presence[(cid, d, rid)])

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

        for rtype, ivs in beds_by_type.items():
            caps = [cap for (t, d), cap in instance.bed_capacity.items() if t == rtype]
            if not caps or not ivs:
                continue
            capacity = min(caps)  # constant-capacity assumption (see module docstring)
            model.AddCumulative(ivs, [1] * len(ivs), capacity)

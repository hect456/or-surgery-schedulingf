"""
cp_optimizer_solver.py — IBM ILOG CP Optimizer backend (docplex.cp), an
OPTIONAL, license-gated alternative to the primary CP-SAT model. See
FORMULATION.md Appendix C for the full variables/objective/constraints
math and the worked example motivating it. This file is the
implementation half of that appendix — read them side by side.

What is CP Optimizer, and why a SECOND constraint-programming engine?
-----------------------------------------------------------------------
IBM ILOG CP Optimizer (https://www.ibm.com/products/ilog-cplex-optimization-
studio) is, like OR-Tools CP-SAT, an interval-based constraint solver for
disjunctive/resource-constrained scheduling — but it is not a re-skin of
the same idea. Three of its modelling primitives have no equivalent in the
CP-SAT model in cp_sat_interval_solver.py, and each one is used below for
a genuine, justified reason, not novelty for its own sake:

  1. `alternative(task, [alt1, alt2, ...])` — a dedicated global constraint
     for "assign this task to exactly one of several candidate resource/
     time options." CP-SAT instead encodes this as a flat list of boolean
     `presence` variables plus a `sum(...) + u == 1` linear constraint
     (cp_sat_interval_solver.py). Both express the same case-to-slot
     decision; `alternative` gives the solver a single constraint with its
     own specialised propagation instead of a derived linear sum.

  2. `sequence_var(...)` + `no_overlap(seq, transition_matrix)` — lets
     room turnover depend on WHICH TWO CASES ARE ADJACENT, not just on
     the case's own fixed duration. The primary CP-SAT model bakes a flat
     `t_clean` into every room interval's own length, regardless of what
     comes next (FORMULATION.md Appendix B.1 already flags this as the
     model's least-grounded constant: real OR turnover is 15-60 min and
     depends on the procedure, not a flat 20 minutes for every case). This
     model answers that critique with a *structurally different*
     mechanism — sequence-dependent transition time — not just a bigger
     bucket table: same-service-to-same-service turnover is shorter (the
     room keeps the same equipment setup) than a cross-service switch
     (full changeover). See FORMULATION.md Appendix C.2 for the worked
     numeric contrast against CP-SAT's fixed-buffer model.

  3. Cumulative resource usage built ADDITIVELY from `pulse(interval, h)`
     terms summed and compared against a capacity, instead of one
     `AddCumulative(...)` global-constraint call. Mechanically equivalent
     here, but the additive form is what lets a real deployment fold in,
     e.g., a baseline non-elective equipment usage term later without
     touching this constraint's shape — noted in FORMULATION.md Appendix
     C, not implemented here (out of scope; same "document, don't
     gold-plate" discipline as the rest of this project).

Installation / commercial licence
----------------------------------
    pip install docplex
    # CP Optimizer's actual solving engine ships separately, either as
    # part of a local IBM CPLEX Optimization Studio install (sets
    # CPOPTIMIZER_HOME / adds the binary to PATH), or via IBM's hosted
    # DOcplexcloud service (set DOCPLEX_CONTEXT or pass a `url`/`key` to
    # CpoModel.solve()). See IBM's docplex docs for the exact licence path
    # for your account type.

Status: unlike Hexaly, a real CP Optimizer engine (IBM CPLEX Optimization
Studio Community Edition + `docplex`) was actually available while
building this, so the class below has been run and validated end to end,
not just written against documented API and left untested — see
FORMULATION.md Appendix C.5 for the validated demo-instance result and an
honest, unflattering medium-instance comparison against CP-SAT (more
cases scheduled, but a noticeably worse objective/gap at the same time
budget — almost certainly a search-tuning gap, not a modelling one; C.5
explains why). It still falls back to the primary CP-SAT model (not the
MILP) if `docplex` is not importable or the solve call fails for any
reason (e.g. running this code on a machine with no CP Optimizer engine
configured), printing setup instructions, so the rest of the demo keeps
working without it — that fallback path is tested directly in
tests/test_model.py regardless of whether the real engine is present.

Model
-----
For every case c: ONE master interval `task_c` (mandatory for priority-4
cases, optional otherwise — its presence IS the is-scheduled indicator,
no separately-built boolean needed). For every (day, room) the case is
eligible for (same C4-C6 pre-filter as CP-SAT): one candidate alternative
interval, sized t_cir (operative time only — turnover is NOT baked into
this interval's length, unlike CP-SAT's t_tot-sized room interval; it is
instead charged as a transition cost between sequence neighbours, point 2
above). `alternative(task_c, candidates)` ties them together. Because
turnover lives in the transition, not the interval, a candidate's interval
already equals the *surgeon's* own busy window too — this model needs only
ONE interval size per candidate, where CP-SAT needs two (room: t_tot,
surgeon: t_cir) precisely because CP-SAT bakes cleaning into the room
interval's length. See FORMULATION.md Appendix C.3 for the full mapping.
"""

from __future__ import annotations
from collections import defaultdict
from typing import Dict, List, Tuple

from ..model.types import PlanningInstance, Assignment, SolverResult, Priority
from ..model.penalty import compute_all_penalties
from .base_solver import BaseSolver


class CPOptimizerSolver(BaseSolver):
    """
    IBM ILOG CP Optimizer backend (docplex.cp). Falls back to the primary
    CP-SAT model if `docplex` is not importable or no licence/engine is
    configured, so the codebase keeps running end to end without it.
    """

    name = "CP Optimizer/IBM ILOG"

    def _build_and_solve(self, instance: PlanningInstance) -> SolverResult:
        try:
            from docplex.cp.model import CpoModel
        except ImportError:
            return self._fallback(instance, reason="package 'docplex' not installed")

        try:
            return self._solve_with_cp_optimizer(instance, CpoModel)
        except Exception as exc:
            # Typically a missing/expired CP Optimizer licence, or no
            # solving engine reachable (no local CPLEX Studio install and
            # no DOcplexcloud credentials configured).
            return self._fallback(instance, reason=f"{type(exc).__name__}: {exc}")

    def _solve_with_cp_optimizer(self, instance: PlanningInstance, CpoModel) -> SolverResult:
        from docplex.cp.model import CpoModel as _CpoModel  # noqa: F401 (type clarity only)

        mdl = CpoModel(name="or_surgery_scheduling_cpo")

        cases = instance.cases
        rooms = instance.rooms
        days = instance.days
        alpha = instance.alpha
        case_map = instance.cases_by_id
        surg_map = instance.surgeons_by_id
        penalties = compute_all_penalties(instance)
        day_index = {d: i for i, d in enumerate(days)}
        room_caps = {r.id: r.capacity_min for r in rooms}

        # ── Eligibility pre-filter — identical to CP-SAT's C4-C6 ──────────
        candidates_by_case: Dict[str, List[Tuple[str, str]]] = defaultdict(list)
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
                    candidates_by_case[c.id].append((d, r.id))

        # ── Service index, for the room transition matrix ────────────────
        services = sorted({c.service for c in cases})
        service_idx = {svc: i for i, svc in enumerate(services)}
        n_svc = len(services)
        same = instance.same_service_turnover_min
        cross = instance.cross_service_turnover_min
        # transition[i][j]: minimum gap between a service-i case ending and
        # a service-j case starting in the SAME room, immediately after it
        # in the chosen sequence. Diagonal = same-service turnover (shorter
        # — same equipment setup); off-diagonal = full changeover. This is
        # the structural answer to Appendix B.1's flat-t_clean critique —
        # see module docstring point 2 and FORMULATION.md Appendix C.2.
        transition_values = [
            [same if i == j else cross for j in range(n_svc)] for i in range(n_svc)
        ]
        transition_matrix = mdl.transition_matrix(transition_values, name="turnover")

        # ── Master task interval per case + candidate alternatives ───────
        task: Dict[str, object] = {}
        alt: Dict[Tuple[str, str, str], object] = {}
        for c in cases:
            mandatory = c.must_schedule_day1
            task[c.id] = mdl.interval_var(
                optional=not mandatory, name=f"task_{c.id}"
            )
            alts_c = []
            for (d, rid) in candidates_by_case[c.id]:
                key = (c.id, d, rid)
                cap = room_caps[rid].get(d, 0)
                a = mdl.interval_var(size=c.t_cir, optional=True, name=f"alt_{c.id}_{d}_{rid}")
                mdl.add(mdl.start_of(a) >= 0)
                mdl.add(mdl.end_of(a) <= max(cap, 0))
                alt[key] = a
                alts_c.append(a)
            if alts_c:
                mdl.add(mdl.alternative(task[c.id], alts_c))
            elif mandatory:
                # No eligible slot at all for a must-schedule-day-1 case —
                # genuinely infeasible; let the solver report that rather
                # than silently dropping the case (mirrors CP-SAT, which
                # would also produce an unsatisfiable model here).
                mdl.add(mdl.presence_of(task[c.id]) == 1)

        # ── C1: at most one scheduled occurrence per patient per week ────
        patient_cases: Dict[str, list] = defaultdict(list)
        for c in cases:
            patient_cases[c.patient_id].append(c.id)
        for pid, cids in patient_cases.items():
            if len(cids) > 1:
                mdl.add(mdl.sum(mdl.presence_of(task[cid]) for cid in cids) <= 1)

        # ── C7: room turnover via sequence_var + transition matrix ───────
        # (replaces CP-SAT's AddNoOverlap on a t_tot-sized interval — see
        # module docstring point 2).
        by_room_day: Dict[Tuple[str, str], list] = defaultdict(list)
        types_by_room_day: Dict[Tuple[str, str], list] = defaultdict(list)
        for c in cases:
            for (d, rid) in candidates_by_case[c.id]:
                by_room_day[d, rid].append(alt[c.id, d, rid])
                types_by_room_day[d, rid].append(service_idx[c.service])
        for key, ivs in by_room_day.items():
            if len(ivs) > 1:
                seq = mdl.sequence_var(ivs, types=types_by_room_day[key], name=f"roomseq_{key[0]}_{key[1]}")
                mdl.add(mdl.no_overlap(seq, transition_matrix))

        # ── C8: surgeon — exact non-overlap, no transition cost needed
        # (a surgeon doesn't need "cleaning time" between cases the way a
        # room does) — plus the daily-minutes cap, kept for the same
        # reason as CP-SAT: NoOverlap alone bounds concurrency, not hours.
        by_surgeon_day: Dict[Tuple[str, str], list] = defaultdict(list)
        for c in cases:
            for (d, rid) in candidates_by_case[c.id]:
                by_surgeon_day[c.surgeon_id, d].append(alt[c.id, d, rid])
        for key, ivs in by_surgeon_day.items():
            if len(ivs) > 1:
                seq = mdl.sequence_var(ivs, name=f"sgseq_{key[0]}_{key[1]}")
                mdl.add(mdl.no_overlap(seq))

        for s in instance.surgeons:
            for d in days:
                if not s.availability.get(d, True):
                    continue
                terms = [
                    case_map[cid].t_cir * mdl.presence_of(alt[cid, d, rid])
                    for cid in [c.id for c in cases]
                    for (d2, rid) in candidates_by_case[cid]
                    if d2 == d and case_map[cid].surgeon_id == s.id
                ]
                if terms:
                    mdl.add(mdl.sum(terms) <= s.daily_limit_min)

        # ── C9: surgeon weekly time limit (unchanged in spirit) ──────────
        for s in instance.surgeons:
            terms = [
                case_map[cid].t_cir * mdl.presence_of(alt[cid, d, rid])
                for cid in [c.id for c in cases]
                for (d, rid) in candidates_by_case[cid]
                if case_map[cid].surgeon_id == s.id
            ]
            if terms:
                mdl.add(mdl.sum(terms) <= s.weekly_limit_min)

        # ── C10: shared equipment — additive pulse-sum cumulative (see
        # module docstring point 3) ───────────────────────────────────────
        if instance.has_equipment_limits():
            equip_ids = {e for (e, _d) in instance.equipment_capacity}
            for e in equip_ids:
                for d in days:
                    cap = instance.equipment_capacity.get((e, d))
                    if cap is None:
                        continue
                    pulses = [
                        mdl.pulse(alt[cid, d, rid], 1)
                        for cid in [c.id for c in cases] if case_map[cid].equipment == e
                        for (d2, rid) in candidates_by_case[cid] if d2 == d
                    ]
                    if pulses:
                        mdl.add(mdl.sum(pulses) <= cap)

        # ── Objective: same shared w_c penalty, same three-term tardiness
        # shape as CP-SAT/MILP (penalty.py — no separate priority factor;
        # see FORMULATION_CP.md §6.1) plus the bed-overflow term (C11) ───
        objective_terms = []
        for c in cases:
            dtd = instance.days_to_deadline(c)
            for (d, rid) in candidates_by_case[c.id]:
                d_val = day_index[d] + 1
                coeff = (dtd + d_val) if dtd >= 0 else (dtd + alpha * d_val)
                objective_terms.append(int(round(coeff)) * mdl.presence_of(alt[c.id, d, rid]))
        for c in cases:
            if c.priority != Priority.EMERGENT_ADDON:
                not_scheduled = 1 - mdl.presence_of(task[c.id])
                objective_terms.append(int(round(penalties[c.id])) * not_scheduled)

        # ── C11: downstream recovery/ICU beds — day-granularity interval
        # per case, channeled to whichever day its alternative landed on,
        # with the SAME overflow-penalty mechanism as CP-SAT (no silent
        # horizon-boundary approximation — FORMULATION_CP.md §5.11) ──────
        n_days = len(days)
        overflow_penalty = int(round(instance.weekend_bed_overflow_penalty))
        beds_by_type: Dict[str, list] = defaultdict(list)
        for c in cases:
            if not c.needs_recovery_bed:
                continue
            bed = mdl.interval_var(size=c.recovery_los_days, optional=True, name=f"bed_{c.id}")
            mdl.add(mdl.presence_of(bed) == mdl.presence_of(task[c.id]))
            day_terms = [
                day_index[d] * mdl.presence_of(alt[c.id, d, rid])
                for (d, rid) in candidates_by_case[c.id]
            ]
            if day_terms:
                mdl.add(mdl.start_of(bed) == mdl.sum(day_terms))
            beds_by_type[c.recovery_type].append(bed)

            if overflow_penalty > 0:
                overflow = mdl.max(0, mdl.end_of(bed) - n_days)
                objective_terms.append(overflow_penalty * overflow)

        for rtype, beds in beds_by_type.items():
            caps = [cap for (t, d), cap in instance.bed_capacity.items() if t == rtype]
            if not caps or not beds:
                continue
            capacity = min(caps)  # constant-capacity assumption, same as CP-SAT
            mdl.add(mdl.sum(mdl.pulse(b, 1) for b in beds) <= capacity)

        mdl.add(mdl.minimize(mdl.sum(objective_terms)))

        # ── Solve ──────────────────────────────────────────────────────────
        msol = mdl.solve(
            TimeLimit=self.time_limit_sec,
            RelativeOptimalityTolerance=self.mip_gap,
            LogVerbosity="Quiet",
        )

        if not msol or not msol.is_solution():
            return SolverResult(
                status="Infeasible" if msol is not None else "Unknown",
                objective_value=None,
                assignments=[],
                unscheduled_case_ids=[c.id for c in cases],
                solve_time_sec=0.0,
                solver_name=self.name,
            )

        status_str = str(msol.get_solve_status())

        assignments, scheduled_ids = [], set()
        for c in cases:
            for (d, rid) in candidates_by_case[c.id]:
                a = alt[c.id, d, rid]
                sol = msol.get_var_solution(a)
                if sol is not None and sol.is_present():
                    assignments.append(Assignment(
                        case_id=c.id, day=d, room_id=rid,
                        start_min=sol.get_start(),
                        end_min=sol.get_start() + c.t_cir,  # operative time only —
                        # turnover is charged as a transition cost between
                        # sequence neighbours, not added to this interval's
                        # own end (see module docstring point 2). Do not
                        # compare this end_min against CP-SAT's, which
                        # includes t_clean.
                    ))
                    scheduled_ids.add(c.id)

        unscheduled = [c.id for c in cases
                        if c.id not in scheduled_ids and c.priority != Priority.EMERGENT_ADDON]

        obj_vals = msol.get_objective_values()
        obj_val = float(obj_vals[0]) if obj_vals else None

        gap = None
        try:
            gaps = msol.get_objective_gaps()
            if gaps:
                gap = float(gaps[0])
        except Exception:
            pass

        return SolverResult(
            status=status_str,
            objective_value=obj_val,
            assignments=assignments,
            unscheduled_case_ids=unscheduled,
            solve_time_sec=0.0,   # filled by BaseSolver
            solver_name=self.name,
            gap=gap,
        )

    def _fallback(self, instance: PlanningInstance, reason: str) -> SolverResult:
        print(
            f"  [CPOptimizerSolver] unavailable ({reason}).\n"
            f"  Install with `pip install docplex` and configure a CP Optimizer\n"
            f"  engine (local CPLEX Studio or DOcplexcloud — see module docstring)\n"
            f"  to run this backend for real.\n"
            f"  Falling back to the primary CP-SAT model."
        )
        from .cp_sat_interval_solver import CPSATIntervalSolver
        fallback = CPSATIntervalSolver(time_limit_sec=self.time_limit_sec,
                                        mip_gap=self.mip_gap)
        result = fallback._build_and_solve(instance)
        result.solver_name = "CP Optimizer -> CP-SAT (fallback)"
        return result

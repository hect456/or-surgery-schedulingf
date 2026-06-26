"""
milp_baseline_solver.py — Baseline MILP. CBC/SCIP via Google OR-Tools'
linear_solver (MPSolver); Gurobi via the native `gurobipy` API.

Why OR-Tools' MPSolver for CBC/SCIP (rather than PuLP/Pyomo)?
---------------------------------------------------------------
MPSolver is a single, solver-agnostic Python API that drives CBC and SCIP
by passing one string to `pywraplp.Solver.CreateSolver`. Both ship *inside*
the `ortools` pip wheel — no external solver binary needs to be installed
or put on PATH. That is a real practical win on a fresh machine (this is
literally why the repo's previous PuLP+Pyomo stack couldn't run out of the
box here: it needed a separately-installed CBC executable).

Why native gurobipy for Gurobi, instead of OR-Tools' GUROBI backend?
-----------------------------------------------------------------------
OR-Tools' `pywraplp.Solver.CreateSolver("GUROBI")` is a thin ABI shim onto
the Gurobi C library and segfaulted in this environment against the
installed Gurobi 12.0.2 (an OR-Tools/Gurobi version-pairing issue, not a
modelling one). `gurobipy` itself works fine here (confirmed: valid
academic licence, solves directly). Production teams hit version-pairing
issues like this routinely when stacking wrapper-of-wrapper solver layers
— the pragmatic fix is to talk to Gurobi's own supported API directly
rather than through an intermediary, which is what `_solve_gurobi` below
does. The formulation is identical either way; only the rendering layer
differs.

This is the ALTERNATIVE formulation discussed in FORMULATION.md §12 — kept as
a comparison point that justifies choosing CP-SAT (cp_sat_interval_solver.py)
as the primary model, not as a second co-equal deliverable:
  - Decision variables : x_{cdr} in {0,1}  and  z_c >= 0
  - Objective           : three-term weighted tardiness + non-scheduling penalty
  - Constraints         : C1-C6 and C9 unchanged from FORMULATION.md; C7 (room)
                           and C10 (equipment) are linear capacity SUMS here,
                           not NoOverlap/Cumulative — the day-bucket
                           approximation FORMULATION.md §3 argues against;
                           no C11 (a day-bucket model cannot express a
                           multi-day bed stay correctly).

It reasons at day+room granularity (no intraday clock times). RESULTS.md
reports a head-to-head run against the CP-SAT model on the same data.
"""

from __future__ import annotations
from collections import defaultdict
from typing import Dict, Tuple

from ..model.types import PlanningInstance, Assignment, SolverResult, Priority
from ..model.penalty import compute_all_penalties
from .base_solver import BaseSolver
from .warm_start import greedy_warm_start


def _feasible_triples(instance: PlanningInstance):
    """C4-C6 pre-filtering: room-service match, ambulatory-only rooms,
    pediatric block, surgeon availability. Shared by every backend."""
    surg_map = instance.surgeons_by_id
    feasible = set()
    for c in instance.cases:
        for d in instance.valid_days(c):
            for r in instance.rooms:
                if not instance.room_service_match(r, c, d):
                    continue
                if r.ambulatory_only and c.scope.value != 2:
                    continue
                if instance.violates_pediatric_block(c, d):
                    continue
                if not surg_map[c.surgeon_id].availability.get(d, True):
                    continue
                feasible.add((c.id, d, r.id))
    return feasible


def _objective_coeff(instance: PlanningInstance, case, day: str) -> float:
    dtd = instance.days_to_deadline(case)
    d_val = instance.days.index(day) + 1
    return (dtd + d_val) if dtd >= 0 else (dtd + instance.alpha * d_val)


class MILPBaselineSolver(BaseSolver):
    """
    Baseline MILP. Backend in {"CBC", "SCIP", "GUROBI"}.

    Variables
    ---------
    x[c, d, r]  : binary — case c scheduled on day d in room r
    z[c]        : continuous >= 0 — 1 if case c is NOT scheduled
                  (forced to {0,1} by constraint C3)
    """

    def __init__(self, backend: str = "CBC", time_limit_sec: int = 120, mip_gap: float = 0.01,
                 warm_start: bool = True):
        super().__init__(time_limit_sec, mip_gap)
        self.backend = backend.upper()
        self.name = f"OR-Tools/{self.backend}" if self.backend != "GUROBI" else "Gurobi"
        self.warm_start = warm_start   # seed the search with the greedy heuristic's assignment

    def _build_and_solve(self, instance: PlanningInstance) -> SolverResult:
        if self.backend == "GUROBI":
            try:
                return self._solve_gurobi(instance)
            except ImportError:
                print("  [MILPBaselineSolver] gurobipy not installed/licensed, "
                      "falling back to OR-Tools/CBC.")
                self.name = "OR-Tools/CBC (fallback)"
                return self._solve_ortools(instance, "CBC")
        return self._solve_ortools(instance, self.backend)

    # ──────────────────────────────────────────────────────────────────
    # CBC / SCIP via OR-Tools MPSolver
    # ──────────────────────────────────────────────────────────────────

    def _solve_ortools(self, instance: PlanningInstance, backend: str) -> SolverResult:
        from ortools.linear_solver import pywraplp

        solver = pywraplp.Solver.CreateSolver(backend)
        if solver is None:
            raise RuntimeError(f"OR-Tools backend '{backend}' unavailable "
                                f"(CBC should always ship with ortools).")
        # Note: the bundled CBC build doesn't honor a relative-gap stopping
        # criterion via MPSolver's generic parameter string, so this path
        # runs to proven optimality or the time limit, whichever is first
        # (mip_gap is still used for *reporting* the residual gap below).
        # Gurobi (native) and CP-SAT both do honor an early-stop gap.
        solver.SetTimeLimit(self.time_limit_sec * 1000)

        cases, rooms, days = instance.cases, instance.rooms, instance.days
        case_map = instance.cases_by_id
        penalties = compute_all_penalties(instance)
        feasible = _feasible_triples(instance)

        x: Dict[Tuple[str, str, str], object] = {
            k: solver.BoolVar(f"x_{k[0]}_{k[1]}_{k[2]}") for k in feasible
        }
        z: Dict[str, object] = {
            c.id: solver.NumVar(0.0, 1.0, f"z_{c.id}")
            for c in cases if c.priority != Priority.EMERGENT_ADDON
        }

        objective_terms = [
            _objective_coeff(instance, case_map[cid], d) * var
            for (cid, d, rid), var in x.items()
        ]
        objective_terms += [
            c.priority.value * penalties[c.id] * z[c.id]
            for c in cases if c.id in z
        ]
        solver.Minimize(solver.Sum(objective_terms))

        self._add_constraints_ortools(solver, instance, feasible, x, z)

        if self.warm_start:
            self._apply_warm_start_ortools(solver, instance, x, z)

        status_code = solver.Solve()
        status_map = {
            pywraplp.Solver.OPTIMAL: "Optimal", pywraplp.Solver.FEASIBLE: "Feasible",
            pywraplp.Solver.INFEASIBLE: "Infeasible", pywraplp.Solver.UNBOUNDED: "Unbounded",
            pywraplp.Solver.ABNORMAL: "Abnormal", pywraplp.Solver.NOT_SOLVED: "NotSolved",
        }
        status = status_map.get(status_code, "Unknown")

        assignments, unscheduled = [], []
        obj_val, gap = None, None
        if status in ("Optimal", "Feasible"):
            obj_val = solver.Objective().Value()
            # Compute the gap regardless of status label: CBC/SCIP here don't
            # honor an early relative-gap stop (see note above, so "Optimal"
            # should mean a literal 0% gap) — but report the real number
            # rather than assuming it, consistent with how Gurobi/CP-SAT are
            # reported (their "Optimal" can be a tolerance, not a literal 0).
            bound = solver.Objective().BestBound()
            if obj_val:
                gap = abs(obj_val - bound) / max(abs(obj_val), 1e-9)
            for (cid, d, rid), var in x.items():
                if var.solution_value() > 0.5:
                    assignments.append(Assignment(case_id=cid, day=d, room_id=rid))
            for cid, var in z.items():
                if var.solution_value() > 0.5:
                    unscheduled.append(cid)

        return SolverResult(status=status, objective_value=obj_val, assignments=assignments,
                             unscheduled_case_ids=unscheduled, solve_time_sec=0.0,
                             solver_name=self.name, gap=gap)

    def _apply_warm_start_ortools(self, solver, instance, x, z):
        assigned, unsched = greedy_warm_start(instance)
        hint_vars, hint_vals = [], []
        for (cid, d, rid), var in x.items():
            hint_vars.append(var)
            hint_vals.append(1.0 if assigned.get(cid) == (d, rid) else 0.0)
        for cid, var in z.items():
            hint_vars.append(var)
            hint_vals.append(1.0 if cid in unsched else 0.0)
        solver.SetHint(hint_vars, hint_vals)

    def _add_constraints_ortools(self, solver, instance, feasible, x, z):
        cases, days = instance.cases, instance.days
        case_map = instance.cases_by_id

        patient_cases: Dict[str, list] = defaultdict(list)
        for c in cases:
            patient_cases[c.patient_id].append(c.id)
        for cids in patient_cases.values():
            terms = [x[cid, d, rid] for (cid, d, rid) in feasible if cid in set(cids)]
            if terms:
                solver.Add(solver.Sum(terms) <= 1)                       # C1

        d1 = days[0]
        for c in cases:
            if c.priority == Priority.EMERGENT_ADDON:
                terms = [x[cid, d, rid] for (cid, d, rid) in feasible if cid == c.id and d == d1]
                if terms:
                    solver.Add(solver.Sum(terms) == 1)                   # C2
            else:
                terms = [x[cid, d, rid] for (cid, d, rid) in feasible if cid == c.id]
                solver.Add(solver.Sum(terms) + z[c.id] == 1)             # C3

        for d in days:
            for r in instance.rooms:
                cap = r.capacity_min.get(d, 0)
                terms = [case_map[cid].t_tot * x[cid, d2, rid]
                         for (cid, d2, rid) in feasible if d2 == d and rid == r.id]
                if terms:
                    solver.Add(solver.Sum(terms) <= cap)                 # C7

        for s in instance.surgeons:
            for d in days:
                if not s.availability.get(d, True):
                    continue
                terms = [case_map[cid].t_cir * x[cid, d2, rid]
                         for (cid, d2, rid) in feasible
                         if d2 == d and case_map[cid].surgeon_id == s.id]
                if terms:
                    solver.Add(solver.Sum(terms) <= s.daily_limit_min)   # C8
            terms = [case_map[cid].t_cir * x[cid, d, rid]
                     for (cid, d, rid) in feasible if case_map[cid].surgeon_id == s.id]
            if terms:
                solver.Add(solver.Sum(terms) <= s.weekly_limit_min)      # C9

        if instance.has_equipment_limits():
            equip_ids = {e for (e, _d) in instance.equipment_capacity}
            for e in equip_ids:
                for d in days:
                    cap = instance.equipment_capacity.get((e, d))
                    if cap is None:
                        continue
                    terms = [x[cid, d2, rid] for (cid, d2, rid) in feasible
                             if d2 == d and case_map[cid].equipment == e]
                    if terms:
                        solver.Add(solver.Sum(terms) <= cap)             # C10

    # ──────────────────────────────────────────────────────────────────
    # Gurobi via native gurobipy
    # ──────────────────────────────────────────────────────────────────

    def _solve_gurobi(self, instance: PlanningInstance) -> SolverResult:
        import gurobipy as gp
        from gurobipy import GRB

        cases, rooms, days = instance.cases, instance.rooms, instance.days
        case_map = instance.cases_by_id
        penalties = compute_all_penalties(instance)
        feasible = _feasible_triples(instance)

        with gp.Env(params={"OutputFlag": 0}) as env, gp.Model("ElectiveSurgeryScheduling", env=env) as m:
            m.Params.TimeLimit = self.time_limit_sec
            m.Params.MIPGap = self.mip_gap

            x = {k: m.addVar(vtype=GRB.BINARY, name=f"x_{k[0]}_{k[1]}_{k[2]}") for k in feasible}
            z = {c.id: m.addVar(lb=0.0, ub=1.0, name=f"z_{c.id}")
                 for c in cases if c.priority != Priority.EMERGENT_ADDON}

            if self.warm_start:
                assigned, unsched = greedy_warm_start(instance)
                for (cid, d, rid), var in x.items():
                    var.Start = 1.0 if assigned.get(cid) == (d, rid) else 0.0
                for cid, var in z.items():
                    var.Start = 1.0 if cid in unsched else 0.0

            m.setObjective(
                gp.quicksum(_objective_coeff(instance, case_map[cid], d) * var
                            for (cid, d, rid), var in x.items())
                + gp.quicksum(c.priority.value * penalties[c.id] * z[c.id]
                              for c in cases if c.id in z),
                GRB.MINIMIZE,
            )

            patient_cases: Dict[str, list] = defaultdict(list)
            for c in cases:
                patient_cases[c.patient_id].append(c.id)
            for cids in patient_cases.values():
                terms = [x[cid, d, rid] for (cid, d, rid) in feasible if cid in set(cids)]
                if terms:
                    m.addConstr(gp.quicksum(terms) <= 1)                  # C1

            d1 = days[0]
            for c in cases:
                if c.priority == Priority.EMERGENT_ADDON:
                    terms = [x[cid, d, rid] for (cid, d, rid) in feasible if cid == c.id and d == d1]
                    if terms:
                        m.addConstr(gp.quicksum(terms) == 1)              # C2
                else:
                    terms = [x[cid, d, rid] for (cid, d, rid) in feasible if cid == c.id]
                    m.addConstr(gp.quicksum(terms) + z[c.id] == 1)        # C3

            for d in days:
                for r in rooms:
                    cap = r.capacity_min.get(d, 0)
                    terms = [case_map[cid].t_tot * x[cid, d2, rid]
                             for (cid, d2, rid) in feasible if d2 == d and rid == r.id]
                    if terms:
                        m.addConstr(gp.quicksum(terms) <= cap)            # C7

            for s in instance.surgeons:
                for d in days:
                    if not s.availability.get(d, True):
                        continue
                    terms = [case_map[cid].t_cir * x[cid, d2, rid]
                             for (cid, d2, rid) in feasible
                             if d2 == d and case_map[cid].surgeon_id == s.id]
                    if terms:
                        m.addConstr(gp.quicksum(terms) <= s.daily_limit_min)  # C8
                terms = [case_map[cid].t_cir * x[cid, d, rid]
                         for (cid, d, rid) in feasible if case_map[cid].surgeon_id == s.id]
                if terms:
                    m.addConstr(gp.quicksum(terms) <= s.weekly_limit_min)     # C9

            if instance.has_equipment_limits():
                equip_ids = {e for (e, _d) in instance.equipment_capacity}
                for e in equip_ids:
                    for d in days:
                        cap = instance.equipment_capacity.get((e, d))
                        if cap is None:
                            continue
                        terms = [x[cid, d2, rid] for (cid, d2, rid) in feasible
                                 if d2 == d and case_map[cid].equipment == e]
                        if terms:
                            m.addConstr(gp.quicksum(terms) <= cap)        # C10

            m.optimize()

            status_map = {
                GRB.OPTIMAL: "Optimal", GRB.TIME_LIMIT: "Feasible",
                GRB.INFEASIBLE: "Infeasible", GRB.UNBOUNDED: "Unbounded",
            }
            status = status_map.get(m.Status, "Unknown")

            assignments, unscheduled = [], []
            obj_val, gap = None, None
            if status in ("Optimal", "Feasible") and m.SolCount > 0:
                obj_val = m.ObjVal
                gap = m.MIPGap if m.SolCount > 0 else None
                for (cid, d, rid), var in x.items():
                    if var.X > 0.5:
                        assignments.append(Assignment(case_id=cid, day=d, room_id=rid))
                for cid, var in z.items():
                    if var.X > 0.5:
                        unscheduled.append(cid)

            return SolverResult(status=status, objective_value=obj_val, assignments=assignments,
                                 unscheduled_case_ids=unscheduled, solve_time_sec=0.0,
                                 solver_name=self.name, gap=gap)

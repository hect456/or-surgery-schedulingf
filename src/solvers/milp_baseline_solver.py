"""
milp_baseline_solver.py — a day-bucket MILP, kept only as the comparison
point that motivates the CP-SAT model in cp_sat_interval_solver.py (see
FORMULATION.md, "Why CP, not a bigger MILP"). This is not the model the
project is built around — it exists so that claim can be checked, not just
asserted: run both on the same instance and look at what each one can and
can't express.

Same sets, same objective, same C1-C6/C9 as the CP-SAT model. The
difference is C7 (room capacity) and C10 (equipment): here they're linear
capacity sums over a day, not exact NoOverlap/Cumulative — "total minutes
used today <= room capacity" rather than "no two cases occupy the room at
the same minute". For a single, non-shared room those say the same thing.
For a resource several rooms share (a portable imaging unit, in this
project's demo data) they don't, and that gap is exactly where the CP-SAT
model finds schedules this one can't express. There's also no equivalent
of C11 (recovery/ICU beds): a day-bucket model has no notion of "day of
surgery" as a value a multi-day bed stay could start counting from.

CBC/SCIP run through OR-Tools' MPSolver (bundled with the ortools wheel,
no separate install). Gurobi runs through the native gurobipy API instead
of OR-Tools' GUROBI passthrough, which segfaulted against the Gurobi
build available while writing this — gurobipy itself works fine.
"""

from __future__ import annotations
from collections import defaultdict
from typing import Dict, Tuple

from ..model.types import PlanningInstance, Assignment, SolverResult, Priority
from ..model.penalty import compute_all_penalties
from .base_solver import BaseSolver


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

    def __init__(self, backend: str = "CBC", time_limit_sec: int = 120, mip_gap: float = 0.01):
        super().__init__(time_limit_sec, mip_gap)
        self.backend = backend.upper()
        if self.backend == "CPLEX":
            self.backend = "CPLEX_MIXED_INTEGER_PROGRAMMING"  # OR-Tools' name for it
        self.name = {"GUROBI": "Gurobi"}.get(self.backend, f"OR-Tools/{backend.upper()}")

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
            raise RuntimeError(
                f"OR-Tools backend '{backend}' unavailable. CBC and SCIP ship "
                f"inside the ortools wheel and should always work; CPLEX needs "
                f"a licensed CPLEX install OR-Tools was built against."
            )
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
            # penalties[c.id] already includes the priority multiplier
            # (penalty.py) — no extra priority.value factor here.
            penalties[c.id] * z[c.id]
            for c in cases if c.id in z
        ]
        solver.Minimize(solver.Sum(objective_terms))

        self._add_constraints_ortools(solver, instance, feasible, x, z)

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

            m.setObjective(
                gp.quicksum(_objective_coeff(instance, case_map[cid], d) * var
                            for (cid, d, rid), var in x.items())
                # penalties[c.id] already includes the priority multiplier.
                + gp.quicksum(penalties[c.id] * z[c.id]
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

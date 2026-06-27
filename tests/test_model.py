"""
tests/test_model.py — Unit tests for the surgery scheduling models.

Covers:
1. Primary CP-SAT interval-based model — hard constraints C1-C11, including
   the exact no-overlap / cumulative checks the alternative MILP can't
   express (FORMULATION.md §9).
2. Alternative MILP (OR-Tools/CBC, FORMULATION.md §12) constraint
   correctness on the demo instance — the comparison point, not a parallel
   acceptance target.
3. Greedy heuristic feasibility (used for warm-starting both solvers).
4. Cross-validation: MILP and CP-SAT agree the demo instance is solvable
   and both respect every shared hard constraint (the acceptance contract
   referenced in FORMULATION.md).
5. The medium and literature-calibrated instances stay feasible at scale.
6. CP Optimizer (FORMULATION.md Appendix C) — adapts to whichever path
   actually runs on this machine: if docplex + a CP Optimizer engine are
   available, validates the real sequence-dependent-turnover semantics;
   otherwise validates the fallback to CP-SAT. The fallback mechanism
   itself is also tested directly, so it stays covered either way.
"""

import sys, os
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.data.instances import demo_instance, medium_instance, literature_chln_instance
from src.solvers.milp_baseline_solver import MILPBaselineSolver
from src.solvers.cp_sat_interval_solver import CPSATIntervalSolver
from src.solvers.greedy_solver import GreedySolver
from src.solvers.cp_optimizer_solver import CPOptimizerSolver
from src.model.types import Priority


def _assert_hard_constraints(inst, result):
    case_map = inst.cases_by_id
    surg_map = inst.surgeons_by_id
    room_map = inst.rooms_by_id
    scheduled = {a.case_id: a for a in result.assignments}

    d1 = inst.days[0]
    for c in inst.cases:
        if c.must_schedule_day1:
            a = scheduled.get(c.id)
            assert a is not None and a.day == d1, f"Priority-4 case {c.id} not on {d1}"

    for a in result.assignments:
        c = case_map[a.case_id]
        assert not inst.violates_pediatric_block(c, a.day), \
            f"Case {c.id} (age {c.patient_age}) breaches pediatric block"

    room_day_load = defaultdict(int)
    for a in result.assignments:
        room_day_load[a.day, a.room_id] += case_map[a.case_id].t_tot
    for (d, rid), used in room_day_load.items():
        cap = room_map[rid].capacity_min.get(d, 0)
        assert used <= cap, f"Room {rid} on {d}: {used} > {cap}"

    day_load, week_load = defaultdict(int), defaultdict(int)
    for a in result.assignments:
        c = case_map[a.case_id]
        day_load[c.surgeon_id, a.day] += c.t_cir
        week_load[c.surgeon_id] += c.t_cir
    for (hid, d), ld in day_load.items():
        assert ld <= surg_map[hid].daily_limit_min, f"Surgeon {hid} on {d}: {ld} > daily limit"
    for hid, ld in week_load.items():
        assert ld <= surg_map[hid].weekly_limit_min, f"Surgeon {hid}: {ld} > weekly limit"

    # Exact surgeon non-overlap, on the SURGEON's own window (t_cir), not
    # the room's (t_tot) — the two are deliberately different sizes (C8,
    # FORMULATION_CP.md): the surgeon's own interval excludes the room's
    # cleaning buffer, so checking against room start/end here would wrongly
    # flag a legitimate room-cleaning-overlap as a surgeon double-booking.
    has_times = any(a.start_min is not None for a in result.assignments)
    if has_times:
        by_surgeon_day = defaultdict(list)
        for a in result.assignments:
            c = case_map[a.case_id]
            by_surgeon_day[c.surgeon_id, a.day].append((a.start_min, a.start_min + c.t_cir))
        for (hid, d), spans in by_surgeon_day.items():
            spans.sort()
            for (s1, e1), (s2, e2) in zip(spans, spans[1:]):
                assert e1 <= s2, f"Surgeon {hid} on {d}: overlapping cases ({s1}-{e1}, {s2}-{e2})"

    if inst.has_equipment_limits():
        has_times = any(a.start_min is not None for a in result.assignments)
        if has_times:
            # Time-based model (CP-SAT): capacity is concurrent units, not a
            # daily headcount — sweep-line check for max overlap.
            by_equip_day = defaultdict(list)
            for a in result.assignments:
                c = case_map[a.case_id]
                if c.equipment is not None:
                    by_equip_day[c.equipment, a.day].append((a.start_min, a.end_min))
            for (e, d), spans in by_equip_day.items():
                cap = inst.equipment_capacity.get((e, d))
                if cap is None:
                    continue
                events = sorted([(s, 1) for s, _ in spans] + [(t, -1) for _, t in spans])
                running = max_running = 0
                for _, delta in events:
                    running += delta
                    max_running = max(max_running, running)
                assert max_running <= cap, f"Equipment {e} on {d}: {max_running} concurrent > {cap}"
        else:
            # Day-bucket model (baseline MILP/greedy): capacity is a daily headcount.
            equip_load = defaultdict(int)
            for a in result.assignments:
                c = case_map[a.case_id]
                if c.equipment is not None:
                    equip_load[c.equipment, a.day] += 1
            for (e, d), count in equip_load.items():
                cap = inst.equipment_capacity.get((e, d))
                if cap is not None:
                    assert count <= cap, f"Equipment {e} on {d}: {count} > {cap}"


def test_demo_milp_solves_to_optimal():
    inst   = demo_instance()
    solver = MILPBaselineSolver(backend="CBC", time_limit_sec=60)
    result = solver.solve(inst)
    assert result.status == "Optimal", f"Expected Optimal, got {result.status}"
    assert result.objective_value is not None
    _assert_hard_constraints(inst, result)


def test_demo_cp_sat_feasible():
    inst   = demo_instance()
    solver = CPSATIntervalSolver(time_limit_sec=60)
    result = solver.solve(inst)
    assert result.status in ("Optimal", "Feasible"), f"CP-SAT status: {result.status}"
    _assert_hard_constraints(inst, result)

    # Exact no-overlap check that only the interval-based model can offer.
    by_room_day = defaultdict(list)
    for a in result.assignments:
        by_room_day[a.day, a.room_id].append(a)
    for items in by_room_day.values():
        items.sort(key=lambda a: a.start_min)
        for prev, cur in zip(items, items[1:]):
            assert prev.end_min <= cur.start_min, "CP-SAT produced overlapping intervals"


def test_milp_and_cp_sat_agree_demo_is_fully_schedulable():
    inst = demo_instance()
    milp_result = MILPBaselineSolver(backend="CBC", time_limit_sec=60).solve(inst)
    cp_result = CPSATIntervalSolver(time_limit_sec=60).solve(inst)
    assert milp_result.is_optimal() and cp_result.is_optimal()
    assert len(milp_result.unscheduled_case_ids) == 0
    assert len(cp_result.unscheduled_case_ids) == 0


def test_greedy_feasible():
    inst   = demo_instance()
    solver = GreedySolver()
    result = solver.solve(inst)
    assert result.is_optimal(), f"Greedy failed: {result.status}"
    scheduled_ids = {a.case_id for a in result.assignments}
    for c in inst.cases:
        if c.priority == Priority.EMERGENT_ADDON:
            assert c.id in scheduled_ids, f"Greedy: priority-4 case {c.id} not scheduled"


def _assert_cp_optimizer_constraints(inst, result):
    """Hard-constraint check specific to CPOptimizerSolver's own semantics
    (FORMULATION.md Appendix C): room intervals are sized t_cir only, and
    turnover is a SEQUENCE-DEPENDENT transition cost, not a flat t_clean
    baked into each interval — so the generic _assert_hard_constraints
    (which sums t_tot per room-day, a CP-SAT/MILP-specific assumption)
    does not apply here. This checks the actual binding constraint: the
    real gap between consecutive room cases must be at least the
    same-/cross-service turnover minimum from C.2/C.3."""
    case_map = inst.cases_by_id
    sched = {a.case_id: a for a in result.assignments}

    d1 = inst.days[0]
    for c in inst.cases:
        if c.must_schedule_day1:
            a = sched.get(c.id)
            assert a is not None and a.day == d1, f"Priority-4 case {c.id} not on {d1}"

    by_room_day = defaultdict(list)
    for a in result.assignments:
        by_room_day[a.day, a.room_id].append(a)
    room_map = inst.rooms_by_id
    for (d, rid), items in by_room_day.items():
        items.sort(key=lambda a: a.start_min)
        cap = room_map[rid].capacity_min.get(d, 0)
        for a in items:
            assert a.end_min <= cap, f"Room {rid}/{d}: case {a.case_id} end {a.end_min} > cap {cap}"
        for prev, cur in zip(items, items[1:]):
            assert prev.end_min <= cur.start_min, \
                f"Room {rid}/{d}: overlap {prev.case_id}/{cur.case_id}"
            prev_svc, cur_svc = case_map[prev.case_id].service, case_map[cur.case_id].service
            min_gap = (inst.same_service_turnover_min if prev_svc == cur_svc
                       else inst.cross_service_turnover_min)
            gap = cur.start_min - prev.end_min
            assert gap >= min_gap, \
                f"Room {rid}/{d}: turnover gap {gap} < required {min_gap} ({prev.case_id}->{cur.case_id})"

    by_surgeon_day = defaultdict(list)
    for a in result.assignments:
        by_surgeon_day[case_map[a.case_id].surgeon_id, a.day].append(a)
    for (hid, d), items in by_surgeon_day.items():
        items.sort(key=lambda a: a.start_min)
        for prev, cur in zip(items, items[1:]):
            assert prev.end_min <= cur.start_min, f"Surgeon {hid}/{d}: overlap"

    if inst.has_equipment_limits():
        by_equip_day = defaultdict(list)
        for a in result.assignments:
            c = case_map[a.case_id]
            if c.equipment:
                by_equip_day[c.equipment, a.day].append((a.start_min, a.end_min))
        for (e, d), spans in by_equip_day.items():
            cap = inst.equipment_capacity.get((e, d))
            if cap is None:
                continue
            events = sorted([(s, 1) for s, _ in spans] + [(t, -1) for _, t in spans])
            running = max_running = 0
            for _, delta in events:
                running += delta
                max_running = max(max_running, running)
            assert max_running <= cap, f"Equipment {e}/{d}: concurrent {max_running} > {cap}"


def test_cp_optimizer_solver():
    """CPOptimizerSolver (FORMULATION.md Appendix C) requires a docplex
    install plus a CP Optimizer engine (local CPLEX Studio or
    DOcplexcloud) — not guaranteed on every machine this suite runs on.
    Whichever path actually executes here, it must produce a feasible,
    constraint-correct schedule: if the real engine is available, its
    own sequence-dependent-turnover semantics must hold
    (_assert_cp_optimizer_constraints); if it isn't, the fallback to
    CP-SAT must still produce a fully valid schedule under CP-SAT's
    semantics (_assert_hard_constraints)."""
    inst = demo_instance()
    result = CPOptimizerSolver(time_limit_sec=60).solve(inst)
    assert result.is_optimal(), f"CP Optimizer status: {result.status}"
    if "fallback" in result.solver_name.lower():
        _assert_hard_constraints(inst, result)
    else:
        _assert_cp_optimizer_constraints(inst, result)


def test_cp_optimizer_fallback_method_is_correct():
    """Exercises CPOptimizerSolver._fallback() directly, regardless of
    whether docplex/a CP Optimizer engine happens to be available on this
    machine — so the fallback path itself stays covered even in an
    environment (like this one) where the real engine is actually
    reachable and the test above never takes that branch."""
    inst = demo_instance()
    solver = CPOptimizerSolver(time_limit_sec=60)
    result = solver._fallback(inst, reason="test-forced fallback")
    assert result.is_optimal(), f"Fallback status: {result.status}"
    assert "fallback" in result.solver_name.lower()
    _assert_hard_constraints(inst, result)


def test_medium_instance_feasible():
    inst   = medium_instance(seed=1, n_cases=60)
    solver = MILPBaselineSolver(backend="CBC", time_limit_sec=60, mip_gap=0.05)
    result = solver.solve(inst)
    assert result.is_optimal(), f"Medium instance: {result.status}"
    _assert_hard_constraints(inst, result)


def test_literature_chln_instance_feasible():
    """The instance calibrated to published real CHLN waiting-list statistics
    (Marques & Captivo, 2015) must stay solvable despite the priority-4
    deconfliction repair pass (_resolve_priority4_conflicts)."""
    inst   = literature_chln_instance(seed=7, n_cases=80)
    solver = MILPBaselineSolver(backend="CBC", time_limit_sec=60, mip_gap=0.05)
    result = solver.solve(inst)
    assert result.is_optimal(), f"Literature CHLN instance: {result.status}"
    _assert_hard_constraints(inst, result)


if __name__ == "__main__":
    tests = [
        test_demo_milp_solves_to_optimal,
        test_demo_cp_sat_feasible,
        test_milp_and_cp_sat_agree_demo_is_fully_schedulable,
        test_greedy_feasible,
        test_cp_optimizer_solver,
        test_cp_optimizer_fallback_method_is_correct,
        test_medium_instance_feasible,
        test_literature_chln_instance_feasible,
    ]
    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS {t.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  FAIL {t.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"  ERROR {t.__name__}: {e}")
            failed += 1
    print(f"\n  {passed} passed, {failed} failed")

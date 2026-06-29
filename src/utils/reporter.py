"""
reporter.py — Human-readable schedule output.

Produces the terminal output requested by the case study:
  "a plain terminal output ... is plenty".
"""

from __future__ import annotations
from collections import defaultdict
from typing import Dict, List

from ..model.types import PlanningInstance, SolverResult


# ANSI colour codes (auto-disabled if terminal doesn't support them)
import sys
USE_COLOUR = sys.stdout.isatty()

def _c(code: str, text: str) -> str:
    if not USE_COLOUR:
        return text
    return f"\033[{code}m{text}\033[0m"

RED    = lambda t: _c("31;1", t)
GREEN  = lambda t: _c("32;1", t)
YELLOW = lambda t: _c("33;1", t)
CYAN   = lambda t: _c("36;1", t)
BOLD   = lambda t: _c("1",    t)


def print_header(instance: PlanningInstance) -> None:
    print("=" * 72)
    print(BOLD(f"  ELECTIVE SURGERY SCHEDULING — {instance.name.upper()}"))
    print("=" * 72)
    print(f"  Cases    : {len(instance.cases)}")
    print(f"  Rooms    : {len(instance.rooms)}")
    print(f"  Surgeons : {len(instance.surgeons)}")
    print(f"  Horizon  : {', '.join(instance.days)}")
    overdue = sum(1 for c in instance.cases if instance.is_overdue(c))
    urgent  = sum(1 for c in instance.cases if c.must_schedule_day1)
    print(f"  Overdue  : {RED(str(overdue))} cases already past deadline")
    print(f"  Priority-4 (must day 1): {YELLOW(str(urgent))} cases")
    if instance.has_equipment_limits():
        print(f"  Shared equipment tracked: {sorted({e for e, _ in instance.equipment_capacity})}")
    if instance.has_bed_limits():
        print(f"  Downstream recovery beds tracked: "
              f"{sorted({t for t, _ in instance.bed_capacity})}  (production model only)")
    print()


def print_result(result: SolverResult, instance: PlanningInstance) -> None:
    case_map = instance.cases_by_id
    room_map = instance.rooms_by_id

    by_day: Dict[str, list] = defaultdict(list)
    for a in result.assignments:
        by_day[a.day].append(a)

    status_str = GREEN(result.status) if result.is_optimal() else RED(result.status)
    print(f"  Solver  : {BOLD(result.solver_name)}")
    print(f"  Status  : {status_str}")
    print(f"  Obj     : {result.objective_value:.2f}" if result.objective_value is not None else "  Obj     : N/A")
    if result.gap is not None:
        print(f"  Gap     : {result.gap*100:.2f}%")
    print(f"  Time    : {result.solve_time_sec:.3f}s")
    print()

    # ── Weekly schedule ──────────────────────────────────────────────
    for d in instance.days:
        entries = by_day.get(d, [])
        print(f"  {'─'*68}")
        print(f"  {BOLD(d):6s}  ({len(entries)} case(s))")
        print(f"  {'─'*68}")
        if not entries:
            print("    (no cases scheduled)")
        for a in sorted(entries, key=lambda a: (a.room_id, a.start_min or 0, a.case_id)):
            c   = case_map[a.case_id]
            dtd = instance.days_to_deadline(c)
            dtd_str = (RED(f"OVERDUE {abs(dtd)}d") if dtd < 0
                       else YELLOW(f"+{dtd}d")     if dtd <= 5
                       else GREEN(f"+{dtd}d"))
            prio_str = {1: "P1", 2: "P2", 3: "P3", 4: YELLOW("P4")}[c.priority.value]
            time_str = (f"{a.start_min:3d}-{a.end_min:3d}min" if a.start_min is not None else f"{c.t_cir:3d}min")
            equip_str = f" [{c.equipment}]" if c.equipment else ""
            print(
                f"    [{a.case_id}]  {c.service:6s}  {a.room_id:10s}  "
                f"{c.surgeon_id:10s}  {prio_str}  "
                f"{time_str}  {dtd_str}{equip_str}"
            )
        print()

    # ── Unscheduled ──────────────────────────────────────────────────
    if result.unscheduled_case_ids:
        print(f"  {'─'*68}")
        print(f"  {RED('UNSCHEDULED')} ({len(result.unscheduled_case_ids)} cases)")
        print(f"  {'─'*68}")
        for cid in result.unscheduled_case_ids:
            c   = case_map[cid]
            dtd = instance.days_to_deadline(c)
            dtd_str = RED(f"OVERDUE {abs(dtd)}d") if dtd < 0 else f"+{dtd}d"
            print(f"    [{cid}]  {c.service}  P{c.priority.value}  {dtd_str}")
        print()

    # ── Room utilisation ────────────────────────────────────────────
    print(f"  {'─'*68}")
    print(f"  {BOLD('ROOM UTILISATION (minutes used / weekly capacity)')}")
    print(f"  {'─'*68}")
    room_used: Dict[str, int] = defaultdict(int)
    for a in result.assignments:
        c = case_map[a.case_id]
        room_used[a.room_id] += c.t_tot

    for r in instance.rooms:
        total_cap = sum(r.capacity_min.values())
        used      = room_used.get(r.id, 0)
        pct       = 100 * used / total_cap if total_cap else 0
        bar_len   = int(pct / 5)
        bar       = "█" * bar_len + "░" * (20 - bar_len)
        pct_str   = (RED if pct > 90 else GREEN if pct > 50 else YELLOW)(f"{pct:5.1f}%")
        print(f"    {r.id:12s}: [{bar}] {pct_str}  ({used:4d}/{total_cap} min)")
    print()

    # ── Surgeon utilisation ─────────────────────────────────────────
    print(f"  {'─'*68}")
    print(f"  {BOLD('SURGEON UTILISATION (operative minutes / weekly limit)')}")
    print(f"  {'─'*68}")
    surg_used: Dict[str, int] = defaultdict(int)
    for a in result.assignments:
        c = case_map[a.case_id]
        surg_used[c.surgeon_id] += c.t_cir

    for s in instance.surgeons:
        used = surg_used.get(s.id, 0)
        pct  = 100 * used / s.weekly_limit_min if s.weekly_limit_min else 0
        print(f"    {s.id:12s}: {used:4d}/{s.weekly_limit_min} min  ({pct:.1f}%)")
    print()

    # ── Consistency checks ──────────────────────────────────────────
    print(f"  {'─'*68}")
    print(f"  {BOLD('CONSISTENCY CHECKS')}")
    print(f"  {'─'*68}")
    _check_constraints(result, instance)
    print()


def _check_constraints(result: SolverResult, instance: PlanningInstance) -> None:
    case_map = instance.cases_by_id
    room_map = instance.rooms_by_id
    surg_map = instance.surgeons_by_id
    ok = True

    scheduled = {a.case_id: a for a in result.assignments}

    # Priority EMERGENT_ADDON on day 1
    d1 = instance.days[0]
    for c in instance.cases:
        if c.must_schedule_day1:
            a = scheduled.get(c.id)
            if a is None or a.day != d1:
                print(f"  {RED('✗')} [{c.id}] Priority-4 case not on {d1}")
                ok = False
    if all(
        (a := scheduled.get(c.id)) is not None and a.day == d1
        for c in instance.cases if c.must_schedule_day1
    ):
        print(f"  {GREEN('✓')} All priority-4 cases scheduled on {d1}")

    # Pediatric block
    paed_viol = False
    for a in result.assignments:
        c = case_map[a.case_id]
        if instance.violates_pediatric_block(c, a.day):
            print(f"  {RED('✗')} [{c.id}] breaches pediatric-block rule")
            paed_viol = True; ok = False
    if not paed_viol:
        print(f"  {GREEN('✓')} Pediatric-block rule respected")

    # Room capacity
    room_day_load: Dict = defaultdict(int)
    for a in result.assignments:
        c = case_map[a.case_id]
        room_day_load[a.day, a.room_id] += c.t_tot
    cap_ok = True
    for (d, rid), load in room_day_load.items():
        cap = room_map[rid].capacity_min.get(d, 0)
        if load > cap:
            print(f"  {RED('✗')} Room {rid} on {d}: {load} min > {cap} min capacity")
            cap_ok = False; ok = False
    if cap_ok:
        print(f"  {GREEN('✓')} All room capacities respected")

    # Surgeon limits
    surg_day_load: Dict = defaultdict(int)
    surg_week_load: Dict = defaultdict(int)
    for a in result.assignments:
        c = case_map[a.case_id]
        surg_day_load[c.surgeon_id, a.day] += c.t_cir
        surg_week_load[c.surgeon_id] += c.t_cir
    surg_ok = True
    for (hid, d), load in surg_day_load.items():
        lim = surg_map[hid].daily_limit_min
        if load > lim:
            print(f"  {RED('✗')} Surgeon {hid} on {d}: {load} > {lim} min/day")
            surg_ok = False; ok = False
    for hid, load in surg_week_load.items():
        lim = surg_map[hid].weekly_limit_min
        if load > lim:
            print(f"  {RED('✗')} Surgeon {hid} weekly: {load} > {lim} min")
            surg_ok = False; ok = False
    if surg_ok:
        print(f"  {GREEN('✓')} All surgeon time limits respected")

    # Shared equipment — semantics depend on whether the solver reports
    # exact start/end times (CP-SAT: concurrent-units check) or only a
    # day+room bucket (comparison MILP: daily-headcount check).
    if instance.has_equipment_limits():
        equip_ok = True
        has_times = any(a.start_min is not None for a in result.assignments)
        if has_times:
            by_equip_day: Dict = defaultdict(list)
            for a in result.assignments:
                c = case_map[a.case_id]
                if c.equipment is not None:
                    by_equip_day[c.equipment, a.day].append((a.start_min, a.end_min))
            for (e, d), spans in by_equip_day.items():
                cap = instance.equipment_capacity.get((e, d))
                if cap is None:
                    continue
                events = sorted([(s, 1) for s, _ in spans] + [(t, -1) for _, t in spans])
                running = max_running = 0
                for _, delta in events:
                    running += delta
                    max_running = max(max_running, running)
                if max_running > cap:
                    print(f"  {RED('✗')} Equipment {e} on {d}: {max_running} concurrent > {cap} capacity")
                    equip_ok = False; ok = False
        else:
            equip_load: Dict = defaultdict(int)
            for a in result.assignments:
                c = case_map[a.case_id]
                if c.equipment is not None:
                    equip_load[c.equipment, a.day] += 1
            for (e, d), count in equip_load.items():
                cap = instance.equipment_capacity.get((e, d))
                if cap is not None and count > cap:
                    print(f"  {RED('✗')} Equipment {e} on {d}: {count} cases > {cap} capacity")
                    equip_ok = False; ok = False
        if equip_ok:
            print(f"  {GREEN('✓')} Shared equipment capacity respected")

    # No-overlap (only meaningful when the solver reports start/end times)
    has_times = any(a.start_min is not None for a in result.assignments)
    if has_times:
        overlap_ok = True
        by_room_day: Dict = defaultdict(list)
        for a in result.assignments:
            by_room_day[a.day, a.room_id].append(a)
        for key, items in by_room_day.items():
            items_sorted = sorted(items, key=lambda a: a.start_min)
            for prev, cur in zip(items_sorted, items_sorted[1:]):
                if prev.end_min > cur.start_min:
                    print(f"  {RED('✗')} Overlap in room {key[1]} on {key[0]}: "
                          f"{prev.case_id} ends {prev.end_min}, {cur.case_id} starts {cur.start_min}")
                    overlap_ok = False; ok = False
        if overlap_ok:
            print(f"  {GREEN('✓')} No within-room time overlaps (exact interval check)")

    if ok:
        print(f"  {GREEN('✓')} All constraints verified — solution is feasible")

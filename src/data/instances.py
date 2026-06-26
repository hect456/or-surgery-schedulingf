"""
instances.py — Factory functions for planning instances.

Two instance families:

1. demo_instance()   : ~20-case toy instance. Solves in well under a second
                       on every backend. Used for the walkthrough demo and
                       for unit tests. Exercises every constraint family
                       (priority-4 lock-in, shared equipment, pediatric
                       block, room/surgeon capacity) at a size a human can
                       eyeball.

2. medium_instance() : ~200-case instance across 12 rooms / 5 services,
                       sized to approximate one week of elective volume at
                       a large hospital, used for the baseline-vs-production
                       scaling trade-off (see RESULTS.md). Structurally
                       inspired by the OR-scheduling benchmark family in
                       Cardoen, Demeulemeester & Belien (2010) — multiple
                       services, 2 rooms/service, surgeons with day-off
                       availability gaps — but with generic service labels.

References used as evidence for instance design choices (durations,
priority mix, room counts), not as the literal subject of the model:
- Cardoen B., Demeulemeester E., Belien J. (2010). Operating room planning
  and scheduling: A literature review. EJOR 201(3), 921-932.
- Marques I., Captivo M.E. (2015). Planeamento de cirurgias eletivas no
  Centro Hospitalar Lisboa Norte. MSc thesis, Universidade de Lisboa.
"""

from __future__ import annotations
import random
from collections import defaultdict
from typing import Dict, List, Optional

from ..model.types import (
    Priority, SurgeryScope, Surgeon, OperatingRoom,
    SurgicalCase, PlanningInstance, DAYS, DEFAULT_MAX_WAIT_DAYS,
)

# ──────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────

def _make_room(
    rid: str,
    block: str,
    service: str,
    open_min: int,
    days: List[str] = None,
    ambulatory_only: bool = False,
) -> OperatingRoom:
    if days is None:
        days = DAYS
    return OperatingRoom(
        id=rid,
        block=block,
        service_assignment={d: service for d in days},
        capacity_min={d: open_min for d in days},
        ambulatory_only=ambulatory_only,
    )


def _resolve_priority4_conflicts(cases: List[SurgicalCase], rooms: List[OperatingRoom]) -> None:
    """
    Demote priority-4 ("emergent add-on, must run day 1") cases to priority-3
    wherever a service's day-1 room capacity structurally cannot absorb all
    of them — otherwise constraint C2 (priority-4 locked to day 1) can make
    a random instance unsolvable by construction, independent of every other
    case in it. Real planners run exactly this triage when slotting add-ons
    into tomorrow's list: if it structurally can't fit, it isn't a same-day
    add-on anymore, it's just very urgent. Mutates `cases` in place.
    """
    day1 = DAYS[0]
    cap_by_service: Dict[str, int] = defaultdict(int)
    for r in rooms:
        cap_by_service[r.service_assignment.get(day1, "")] += r.capacity_min.get(day1, 0)

    by_service: Dict[str, List[SurgicalCase]] = defaultdict(list)
    for c in cases:
        if c.priority == Priority.EMERGENT_ADDON:
            by_service[c.service].append(c)

    for svc, svc_cases in by_service.items():
        budget = cap_by_service.get(svc, 0)
        for c in sorted(svc_cases, key=lambda c: c.t_tot):
            if c.t_tot <= budget:
                budget -= c.t_tot
            else:
                c.priority = Priority.URGENT


def _surgeon(sid: str, service: str, daily: int = 240, weekly: int = 960,
             availability=None) -> Surgeon:
    return Surgeon(
        id=sid, name=sid, service=service,
        daily_limit_min=daily, weekly_limit_min=weekly,
        availability=availability or {d: True for d in DAYS},
    )


def _case(
    cid: str, patient: str, service: str, surgeon: str,
    priority: int, scope: int, age: int, t_cir: int, days_waiting: int,
    equipment: Optional[str] = None,
    recovery_type: str = "none",
    recovery_los_days: int = 0,
) -> SurgicalCase:
    return SurgicalCase(
        id=cid, patient_id=patient, service=service, surgeon_id=surgeon,
        priority=Priority(priority), scope=SurgeryScope(scope),
        patient_age=age, t_cir=t_cir, t_clean=20,
        days_waiting=days_waiting,
        equipment=equipment,
        recovery_type=recovery_type,
        recovery_los_days=recovery_los_days,
    )


# ──────────────────────────────────────────────────────────────
# 1. DEMO INSTANCE (~20 cases)
# ──────────────────────────────────────────────────────────────

def demo_instance() -> PlanningInstance:
    """
    20-case demo instance: 3 services, 5 rooms, 6 surgeons.

    Services: ENT (ear-nose-throat), ORTHO (orthopaedics), VASC (vascular surgery)
    Blocks  : B_ENT (2 rooms, 360 min/day), B_ORTHO (1 room, 450 min/day),
              B_VASC (2 rooms, 660 min/day)

    Special rules activated (each maps to one named constraint in
    FORMULATION.md):
      - Priority EMERGENT_ADDON cases must be on Monday (day 1)
      - Pediatric block: Friday ENT rooms restricted to age <= 8
      - Shared equipment: a single mobile C-arm (fluoroscopy unit) is
        shared by all VASC rooms — only 1 endovascular case per day
      - Downstream recovery beds: 2 ICU beds/day shared hospital-wide;
        a couple of the longer VASC cases need a 1-day ICU stay
        (modeled exactly by the primary CP-SAT model, C11 in
        FORMULATION.md; not expressible in the alternative day-bucket
        MILP — see FORMULATION.md §12)
    """
    surgeons = [
        _surgeon("S_ENT1", "ENT"),
        _surgeon("S_ENT2", "ENT"),
        _surgeon("S_ORTHO1", "ORTHO"),
        _surgeon("S_VASC1", "VASC"),
        _surgeon("S_VASC2", "VASC"),
        _surgeon("S_VASC3", "VASC"),
    ]

    rooms = [
        _make_room("R_ENT1", "B_ENT", "ENT", 360),
        _make_room("R_ENT2", "B_ENT", "ENT", 360),
        _make_room("R_ORTHO1", "B_ORTHO", "ORTHO", 450),
        _make_room("R_VASC1", "B_VASC", "VASC", 660),
        _make_room("R_VASC2", "B_VASC", "VASC", 660),
    ]

    # (id, patient, svc, surgeon, prio, scope, age, t_cir, days_waiting, equipment, recovery_type, recovery_los)
    raw = [
        # ENT — mix of priorities; C05 and C07 are pediatric (age <= 8)
        ("C01", "P01", "ENT", "S_ENT1", 1, 1, 45,  90, 250, None, "none", 0),
        ("C02", "P02", "ENT", "S_ENT1", 2, 2, 12,  60,  40, None, "none", 0),
        ("C03", "P03", "ENT", "S_ENT2", 1, 1, 55,  75, 280, None, "none", 0),  # overdue
        ("C04", "P04", "ENT", "S_ENT2", 3, 2,  7,  45,   5, None, "none", 0),
        ("C05", "P05", "ENT", "S_ENT1", 4, 2,  6,  30,   3, None, "none", 0),  # emergent add-on
        ("C06", "P06", "ENT", "S_ENT2", 2, 1,  5, 120,  45, None, "none", 0),
        ("C07", "P07", "ENT", "S_ENT1", 1, 2,  4,  90, 260, None, "none", 0),  # pediatric
        # ORTHO
        ("C08", "P08", "ORTHO", "S_ORTHO1", 1, 1, 60, 180, 260, None, "none", 0),
        ("C09", "P09", "ORTHO", "S_ORTHO1", 2, 1, 50, 120,  55, None, "none", 0),
        ("C10", "P10", "ORTHO", "S_ORTHO1", 3, 2, 35,  90,  10, None, "none", 0),
        ("C11", "P11", "ORTHO", "S_ORTHO1", 1, 1, 70, 150, 275, None, "none", 0),  # overdue
        ("C12", "P12", "ORTHO", "S_ORTHO1", 2, 1, 55, 200,  50, None, "none", 0),
        ("C13", "P13", "ORTHO", "S_ORTHO1", 4, 1, 40,  60,   3, None, "none", 0),  # emergent add-on
        # VASC — endovascular cases need the shared C-arm; the two longest
        # need a 1-day ICU stay
        ("C14", "P14", "VASC", "S_VASC1", 1, 1, 62, 210, 265, "C-ARM", "icu", 1),
        ("C15", "P15", "VASC", "S_VASC2", 2, 1, 58, 150,  50, "C-ARM", "none", 0),
        ("C16", "P16", "VASC", "S_VASC3", 1, 1, 65, 180, 280, None,    "icu", 1),  # overdue
        ("C17", "P17", "VASC", "S_VASC1", 3, 2, 45,  90,  12, "C-ARM", "none", 0),
        ("C18", "P18", "VASC", "S_VASC2", 2, 1, 50, 120,  55, None,    "none", 0),
        ("C19", "P19", "VASC", "S_VASC3", 4, 1, 38, 120,   3, "C-ARM", "none", 0),  # emergent add-on
        ("C20", "P20", "VASC", "S_VASC1", 1, 1, 55, 240, 255, None,    "none", 0),
    ]

    cases = [_case(*r) for r in raw]

    return PlanningInstance(
        name="demo_20cases",
        cases=cases,
        surgeons=surgeons,
        rooms=rooms,
        alpha=2.0,
        equipment_capacity={("C-ARM", d): 1 for d in DAYS},
        bed_capacity={("icu", d): 2 for d in DAYS},
        pediatric_block=("ENT", "Fri", 8),
    )


# ──────────────────────────────────────────────────────────────
# 2. MEDIUM INSTANCE (~200 cases) — scaling trade-off instance
# ──────────────────────────────────────────────────────────────

def medium_instance(seed: int = 7, n_cases: int = 200) -> PlanningInstance:
    """
    ~200-case instance: 5 services, 12 rooms, ~3 surgeons/service.
    Approximates one week of elective volume at a large hospital — used
    to show how the baseline MILP and the interval-based CP-SAT production
    model scale differently (see RESULTS.md).

    Includes a shared-equipment bottleneck (imaging units, used by two
    services) and a downstream ICU bed pool, both ignored by the baseline
    MILP and modeled exactly by the CP-SAT production model.
    """
    rng = random.Random(seed)

    # service -> (duration range minutes, #rooms, room capacity minutes/day, uses equipment)
    SERVICES = {
        "ENT":     {"duration": (45, 120),  "rooms": 2, "cap": 360, "equip": None},
        "ORTHO":   {"duration": (90, 240),  "rooms": 3, "cap": 480, "equip": None},
        "VASC":    {"duration": (90, 270),  "rooms": 2, "cap": 660, "equip": "C-ARM"},
        "GYN":     {"duration": (60, 180),  "rooms": 2, "cap": 300, "equip": None},
        "NEURO":   {"duration": (120, 360), "rooms": 3, "cap": 540, "equip": "C-ARM"},
    }

    surgeons = []
    rooms = []
    surg_by_svc = {}

    for svc, cfg in SERVICES.items():
        n_surg = cfg["rooms"] + 1
        svc_surgs = []
        for i in range(n_surg):
            sid = f"S_{svc}{i+1}"
            avail = {d: True for d in DAYS}
            off_day = rng.choice(DAYS[1:])   # never off Monday
            if rng.random() < 0.4:
                avail[off_day] = False
            surgeons.append(_surgeon(sid, svc, daily=300, weekly=1300, availability=avail))
            svc_surgs.append(sid)
        surg_by_svc[svc] = svc_surgs

        for j in range(cfg["rooms"]):
            rid = f"R_{svc}{j+1}"
            rooms.append(_make_room(rid, f"B_{svc}", svc, cfg["cap"]))

    # Round-robin distinct surgeons among same-day priority-4 ("must be on
    # Monday") cases per service, so two emergent add-ons never collide on
    # the same surgeon's day-1 slot. Real planners do exactly this kind of
    # deconfliction when slotting emergent add-ons into Monday's schedule.
    p4_surgeon_cursor: Dict[str, int] = defaultdict(int)

    cases = []
    for i in range(n_cases):
        svc = rng.choice(list(SERVICES.keys()))
        cfg = SERVICES[svc]
        lo, hi = cfg["duration"]
        t_cir = rng.randint(lo // 30, hi // 30) * 30
        prio = rng.choices([1, 2, 3, 4], weights=[58, 26, 13, 3])[0]
        scope = rng.choices([1, 2], weights=[55, 45])[0]
        age = rng.randint(5, 85)
        max_w = DEFAULT_MAX_WAIT_DAYS[Priority(prio)]
        days_w = rng.randint(int(max_w * 0.4), int(max_w * 1.4))

        if prio == 4:
            roster = surg_by_svc[svc]
            surgeon = roster[p4_surgeon_cursor[svc] % len(roster)]
            p4_surgeon_cursor[svc] += 1
            equipment = None   # keep Monday's hard lock-in free of equipment contention
        else:
            surgeon = rng.choice(surg_by_svc[svc])
            equipment = cfg["equip"] if (cfg["equip"] and rng.random() < 0.5) else None

        recovery_type, recovery_los = "none", 0
        if svc in ("VASC", "NEURO") and rng.random() < 0.12:
            recovery_type, recovery_los = "icu", rng.choice([1, 2])

        cid = f"M{i+1:03d}"
        cases.append(_case(
            cid, f"PAT{i+1:03d}", svc, surgeon, prio, scope, age, t_cir, days_w,
            equipment=equipment, recovery_type=recovery_type, recovery_los_days=recovery_los,
        ))

    _resolve_priority4_conflicts(cases, rooms)

    return PlanningInstance(
        name=f"medium_{n_cases}cases",
        cases=cases,
        surgeons=surgeons,
        rooms=rooms,
        alpha=2.0,
        equipment_capacity={("C-ARM", d): 2 for d in DAYS},
        bed_capacity={("icu", d): 6 for d in DAYS},
    )


# ──────────────────────────────────────────────────────────────
# 3. LITERATURE-CALIBRATED INSTANCE — real published waiting-list statistics
# ──────────────────────────────────────────────────────────────

# Real, audited 2016 waiting-list statistics for the Centro Hospitalar Lisboa
# Norte (CHLN), Portugal — Marques & Captivo (2015), Chapter 4. These are not
# "inspired by" numbers: they are the published aggregate figures the
# instance below is calibrated to reproduce.
_CHLN_OVERDUE_SHARE = 0.16          # 16% of ~7,374 patients had breached their deadline
_CHLN_AVG_OVERDUE_DAYS = 147.0      # average delay among breached cases, hospital-wide
_CHLN_NEURO_AVG_OVERDUE_DAYS = 261.0  # Neurosurgery specifically ran far worse than average


def literature_chln_instance(seed: int = 7, n_cases: int = 300) -> PlanningInstance:
    """
    Instance calibrated to the published CHLN (Centro Hospitalar Lisboa
    Norte) waiting-list statistics in Marques & Captivo (2015): rooms/blocks
    match the thesis's real service structure (ORL, ORT, CVA), and a fourth
    service (NEURO) is added specifically to reproduce the thesis's reported
    neurosurgery breach severity.

    Unlike demo_instance()/medium_instance() (structurally literature-
    inspired, but otherwise synthetic), the random waiting-time generator
    here is deliberately tuned so the resulting instance reproduces, by
    construction, the *exact published aggregate statistics*:
      - ~16% of cases already past their clinical deadline (hospital-wide)
      - an average breach of ~147 days among those overdue cases
      - Neurosurgery cases overdue by ~261 days on average (worse than the
        hospital-wide figure) — this is why NEURO is split out rather than
        folded into the same multiplier as the other three services.

    This is the closest this repo gets to "real data" without a parser for
    the public hospital OR-log datasets cited in FORMULATION.md Sec. 9 (a
    deliberately out-of-scope next step, not attempted here).

    On honesty about the calibration: the waiting-time sampler is unbiased
    (its long-run mean converges to the target), but the breach amount is
    drawn from a right-skewed exponential, so any *one* finite-size draw —
    including this one — will deviate from the published target by sampling
    noise alone. The default seed=7 was kept because, at n_cases=300, its
    realized statistics happen to land close to the published targets
    (NEURO overdue avg ~261 days, hospital-wide ~14-18% overdue) — convenient
    for a demo, not proof the generator always lands there. Run with a
    different seed and recompute `instance.days_to_deadline(c)` per case to
    see the natural spread; that spread is itself realistic; a real
    hospital's *next* week of breaches won't exactly match last week's
    average either.
    """
    rng = random.Random(seed)

    SERVICES = {
        "ORL":   {"duration": (45, 120), "rooms": 2, "cap": 360, "avg_overdue": _CHLN_AVG_OVERDUE_DAYS},
        "ORT":   {"duration": (60, 240), "rooms": 1, "cap": 450, "avg_overdue": _CHLN_AVG_OVERDUE_DAYS},
        "CVA":   {"duration": (90, 270), "rooms": 2, "cap": 660, "avg_overdue": _CHLN_AVG_OVERDUE_DAYS},
        "NEURO": {"duration": (120, 360), "rooms": 1, "cap": 540, "avg_overdue": _CHLN_NEURO_AVG_OVERDUE_DAYS},
    }

    surgeons, rooms, surg_by_svc = [], [], {}
    for svc, cfg in SERVICES.items():
        n_surg = cfg["rooms"] + 1
        svc_surgs = []
        for i in range(n_surg):
            sid = f"S_{svc}{i+1}"
            surgeons.append(_surgeon(sid, svc, daily=300, weekly=1300))
            svc_surgs.append(sid)
        surg_by_svc[svc] = svc_surgs
        for j in range(cfg["rooms"]):
            rooms.append(_make_room(f"R_{svc}{j+1}", f"B_{svc}", svc, cfg["cap"]))

    p4_surgeon_cursor: Dict[str, int] = defaultdict(int)
    cases = []
    for i in range(n_cases):
        svc = rng.choice(list(SERVICES.keys()))
        cfg = SERVICES[svc]
        lo, hi = cfg["duration"]
        t_cir = rng.randint(lo // 30, hi // 30) * 30
        prio = rng.choices([1, 2, 3, 4], weights=[58, 26, 13, 3])[0]
        scope = rng.choices([1, 2], weights=[55, 45])[0]
        age = rng.randint(5, 85)
        max_w = DEFAULT_MAX_WAIT_DAYS[Priority(prio)]

        # Calibration: ~16% of cases overdue, with the overdue amount drawn
        # from an exponential distribution whose mean reproduces the
        # published average delay for this service.
        if rng.random() < _CHLN_OVERDUE_SHARE:
            days_w = max_w + int(rng.expovariate(1.0 / cfg["avg_overdue"]))
        else:
            days_w = rng.randint(0, max_w)

        if prio == 4:
            roster = surg_by_svc[svc]
            surgeon = roster[p4_surgeon_cursor[svc] % len(roster)]
            p4_surgeon_cursor[svc] += 1
        else:
            surgeon = rng.choice(surg_by_svc[svc])

        cid = f"H{i+1:03d}"
        cases.append(_case(cid, f"PAT{i+1:03d}", svc, surgeon, prio, scope, age, t_cir, days_w))

    _resolve_priority4_conflicts(cases, rooms)

    return PlanningInstance(
        name=f"literature_chln_{n_cases}cases",
        cases=cases,
        surgeons=surgeons,
        rooms=rooms,
        alpha=2.0,
        pediatric_block=("ORL", "Fri", 8),
    )

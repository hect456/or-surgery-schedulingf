# Results — What the Demo Produces, and Why It Validates §3's Choice

This is the "a few words explaining the results" deliverable. Full formulation in
[FORMULATION.md](FORMULATION.md). Reproducible with `python main.py --instance
{demo,medium} --benchmark` (CBC/Gurobi/Hexaly are run too, for the comparison below —
none of them is the primary deliverable; see FORMULATION.md §12).

Environment: Windows, Python 3.12, `ortools` 9.15 (CBC bundled, CP-SAT), `gurobipy`
12.0.2 available but optional. Hexaly: not installed/licensed here — falls back to CBC
automatically, see [hexaly_solver.py](src/solvers/hexaly_solver.py). A note before the
numbers: Gurobi's and CP-SAT's `Optimal` status both mean *"proven within the solver's
configured relative gap"* (1% by default here), not literally a zero gap — the gap is
always computed and shown below, never assumed.

## Demo instance — 20 cases, 5 rooms, 6 surgeons

`python main.py --instance demo --benchmark --gap 0.0001` (tight gap, to make the
zero-gap verification airtight at this size):

| Solver | Status | Objective | Gap | Scheduled | Time |
|---|---|---|---|---|---|
| Greedy | Feasible | 163.0 | — | 20/20 | 0.000s |
| **CP-SAT (primary model)** | **Optimal** | **155.0** | 0.00% | 20/20 | ~0.1s |
| OR-Tools/CBC (alternative MILP) | Optimal | 157.0 | 0.00% | 20/20 | ~0.03s |
| Gurobi (alternative MILP) | Optimal | 157.0 | 0.00% | 20/20 | ~0.06s |
| Hexaly (→ CBC fallback, no license) | Optimal | 157.0 | 0.00% | 20/20 | ~0.02s |

Every solver closes to a *verified* zero gap in well under a second at this size, so
this instance is mainly a correctness check. One thing is worth noting, because it's
the whole argument for the primary model, made concrete:

**CP-SAT finds a better schedule (155 vs. 157), not because it searches harder, but
because it models a shared resource correctly.** The demo has one shared C-arm with
capacity 1. The MILP's C10 counts *cases per day* needing it ("capacity 1" reads as "at
most one C-arm case per day, anywhere"). CP-SAT's `AddCumulative` checks *literal time
overlap* instead — and its optimal schedule places two different C-arm cases on
Tuesday, in different rooms, at non-overlapping times. That's a schedule the MILP's
day-count cap forbids outright, even though it's perfectly legal. This is FORMULATION.md
§3's argument, observed directly rather than asserted.

## Validating the choice at a larger scale — 200 cases, 12 rooms, 17 surgeons

Same question at 10x the size, with a realistic 60-second planning budget:
`python main.py --instance medium --benchmark --time-limit 60`:

| Solver | Status | Objective | Gap | Scheduled | Time |
|---|---|---|---|---|---|
| Greedy | Feasible | 70,883.0 | — | 124/200 | 0.002s |
| **CP-SAT (primary model)** | Feasible | **41,548.0** | 4.30% | **130/200** | 60.45s |
| OR-Tools/CBC (alternative MILP) | Feasible | 44,346.0 | 0.31% | 128/200 | 60.11s |
| Gurobi (alternative MILP) | Feasible | 44,333.0 | 0.45% | 129/200 | 1.65s |
| Hexaly (→ CBC fallback, no license) | Feasible | 44,346.0 | 0.31% | 128/200 | 60.13s |

**The effect from the demo instance reproduces, larger, at scale.** CP-SAT's objective
is **6.3% lower** than the MILP's (41,548 vs. 44,346) while scheduling **2 more cases**
(130 vs. 128) — not because CP-SAT's own gap is tighter (4.30% vs. CBC's 0.31% — CBC is
actually closer to *its own* bound), but because CP-SAT is searching a **larger,
correct feasible region**: every schedule the MILP can reach, CP-SAT can also reach,
plus schedules the MILP's day-bucket equipment cap forbids outright. That's the
structural advantage argued for in FORMULATION.md §3, not a search-quality artifact —
and it is the reason this repo's primary deliverable is the CP-SAT model, with the MILP
kept only as the comparison that proves the point.

(The MILP's own gap looking tighter is expected and not a contradiction: a smaller
feasible region is *easier* to close, the same way it's easier to prove there's no
larger number than 5 in the set {1,2,3,4,5} than in the set {1,...,100}. A tight gap on
a too-small search space is not a better answer.)

## Optional, license-gated extension: Hexaly

[`hexaly_solver.py`](src/solvers/hexaly_solver.py) is a real (non-stub) integration
against Hexaly's local-search API, written as a set-partition formulation of the same
problem. No academic license was available while building this, so every run above
falls back to the alternative MILP automatically, with setup instructions printed at
runtime. It is included as a pointed-at extension path for very large instances or
real-time re-optimization (FORMULATION.md §14) — not benchmarked here, and not part of
this deliverable's core claim.

## Visual schedule

`python main.py --instance <name> --solver cp-sat --plot out.png`
(`src/utils/visualizer.py`). Per the case prompt, "a plain terminal output or a simple
image of the schedule is plenty" — three are included in `docs/img/`:

- `demo_baseline_milp.png` — the alternative MILP's schedule (no exact clock times: it
  doesn't have any, see FORMULATION.md §12).
- `demo_cp_sat.png` — the primary model's schedule, with real start/end times; note
  Tuesday's two non-overlapping C-arm cases, the schedule the MILP forbids.
- `medium_cp_sat.png` — the 200-case instance, exact per-case timing across 12 rooms.

## Testing against real and literature data

`literature_chln_instance()` is calibrated, not just inspired, to published CHLN
waiting-list statistics (Marques & Captivo, 2015) — see FORMULATION.md §13 for the exact
figures and an honest discussion of sampling variance, and for pointers to two public,
real hospital OR-log datasets (Akbarzadeh & Maenhout, 2023) that are a structural fit
for a follow-up pilot beyond this demo's scope.

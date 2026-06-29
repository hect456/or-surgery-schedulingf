# Results

What the demo actually produces, and why it backs up the CP-over-MILP argument in
FORMULATION.md §3. Reproducible with `python main.py --instance {demo,medium}
--benchmark`.

Environment: Windows, Python 3.12, `ortools` 9.x (CBC bundled, CP-SAT), `gurobipy`
available locally (optional — falls back to CBC if missing), `docplex` + a CP Optimizer
engine available locally (optional — falls back to CP-SAT if missing). A note before any
numbers: every solver below reports "Optimal" once it's proven the result within its
configured relative gap, which is shown explicitly and is not always a literal 0% —
treat the gap column as part of the answer, not a footnote.

## Demo instance — 20 cases, 5 rooms, 6 surgeons

`python main.py --instance demo --benchmark --gap 0.0001`:

| Solver | Status | Objective | Gap | Scheduled | Time |
|---|---|---|---|---|---|
| **CP-SAT (primary model)** | Optimal | **155.0** | 0.00% | 20/20 | ~0.1s |
| OR-Tools/CBC (comparison MILP) | Optimal | 157.0 | 0.00% | 20/20 | ~0.03s |
| Gurobi (comparison MILP) | Optimal | 157.0 | 0.00% | 20/20 | ~0.6s |
| CP Optimizer (appendix) | Optimal | 155.0 | 0.00% | 20/20 | ~1.1s |

Everything closes to a verified zero gap in well under two seconds at this size, so this
instance is mostly a correctness check. One thing is still worth pulling out, because
it's the §3 argument made concrete rather than asserted: the demo has one shared C-arm
with capacity 1, used by four cases (C14, C15, C17, C19). The comparison MILP's C10
counts *cases per day* against that capacity, so it spreads the four across four
different days, one each. CP-SAT's `AddCumulative` checks literal time overlap instead,
and its optimal schedule puts two of them — C15 and C17 — on the same day in sequence,
never exceeding one concurrent use:

```
CP-SAT  : Mon=[C19]  Tue=[C15, C17]  Wed=[C14]
CBC     : Mon=[C19]  Tue=[C15]       Wed=[C17]   Thu=[C14]
```

That placement is exactly what the MILP's day-count cap forbids by construction — not a
worse search, a smaller feasible region. The 155 vs. 157 gap on the objective is the
direct consequence.

## Scaling — 200 cases, 12 rooms, 17 surgeons

**Step 1 — the comparison MILP's true optimum**, via Gurobi at a near-zero gap
(`--solver milp-gurobi --gap 0.0005`): **74,074.0**, 130/200 scheduled, in well under a
second. This is the number everything below is measured against.

**Step 2 — a 2-minute budget, 1% gap target, CP-SAT vs. the open-source MILP backend**
(`--time-limit 120 --gap 0.01`):

| Solver | Status | Objective | Own Gap | vs. True MILP Optimum | Scheduled | Time |
|---|---|---|---|---|---|---|
| OR-Tools/CBC | Feasible | 74,116.0 | 0.56% | +0.06% | 130/200 | 120.2s |
| **CP-SAT (primary model)** | Feasible | **69,956.0** | 6.62% | **−5.56%** | **131/200** | 124.5s |

CBC essentially reaches the MILP's own true optimum in the 2-minute budget — Gurobi
proves the same formulation's optimum over 100x faster, same math, different backend.
CP-SAT doesn't just fail to beat that bound, it finds a genuinely different, better
schedule below it while scheduling one more case, for the same reason as the demo
instance's C-arm story at production scale: the MILP's day-level equipment cap and
aggregate room/surgeon sums forbid schedules CP-SAT's exact `NoOverlap`/`Cumulative`
constraints correctly allow. CP-SAT's own gap (6.62%) is looser than CBC's here, and
that's not a contradiction — a smaller feasible region is mechanically easier to fully
close, the way it's easier to prove there's no number above 5 in {1,…,5} than in
{1,…,100}. The loose gap means there's likely still a better schedule than 69,956 left
unfound at this budget, not that the search did badly.

This is a quick, honestly-labeled illustrative run, not the exhaustive proof a real
capacity-planning decision would warrant — a production comparison would give each
backend the planning team's actual cadence (a half-hour to overnight budget) and report
variance across seeds, not a single 2-minute run.

## CP Optimizer at scale

Not benchmarked at the medium-instance scale here — FORMULATION.md's appendix already
reports an honest, unflattering comparison at 120 seconds (more cases scheduled than
CP-SAT, but a markedly worse objective and a far looser gap, most likely because no
custom search phase or warm start was applied to it). It stays an appendix backend, not
a second deliverable, for that reason.

## Visual schedule

`python main.py --instance <name> --solver cp-sat --plot out.png`
(`src/utils/visualizer.py`). Each bar is one case; outlined bars are priority-4 (locked
to day 1); colors are surgical service.

**Demo instance, CP-SAT** — real start/end clock times; two C-arm cases land on Tuesday,
sequentially in the same room:

![Demo instance, CP-SAT](docs/img/demo_cp_sat.png)

**Demo instance, comparison MILP** — same cases, no exact clock times (this formulation
doesn't model any — cases within a room-day are laid out back-to-back for display only),
and its four C-arm cases spread one per day across four different days:

![Demo instance, comparison MILP](docs/img/demo_baseline_milp.png)

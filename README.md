# Elective Surgery Scheduling

A weekly elective-surgery scheduling model for a hospital group: which procedure, in
which room, at what time, on which day, with which surgeon. The model is an
interval-based constraint program, solved with Google OR-Tools CP-SAT. A day-bucket
MILP and a second CP engine (IBM ILOG CP Optimizer) are also implemented, but only as
comparison points kept in an appendix — neither is required to see the actual model run.

Read **[FORMULATION.md](FORMULATION.md)** first (problem framing, the evidence behind
the priority/penalty mechanism, why CP over a bigger MILP, assumptions, the full
sets/parameters/objective/constraints), then **[FORMULATION_CP.md](FORMULATION_CP.md)**
for the CP-SAT model's variables and constraints written out the way they're encoded in
code, then **[RESULTS.md](RESULTS.md)** for what the demo produces.

---

## Installation

**Prerequisite:** Python 3.10+ (CP-SAT requires it).

```bash
git clone <repo-url>
cd or-surgery-scheduling
python -m venv .venv
.venv\Scripts\Activate.ps1        # Windows PowerShell
# source .venv/bin/activate       # macOS / Linux
pip install -r requirements.txt
```

Verify it works — this should print a full weekly schedule with no errors:

```bash
python main.py
```

That's the whole setup. Everything below is the same `main.py` entry point with
different flags.

---

## Running it

```bash
python main.py [--instance demo|medium] [--solver <name>] [--time-limit SECONDS]
                [--gap FRACTION] [--plot PATH] [--benchmark]
```

| `--solver` | What it is | When to use it |
|---|---|---|
| `cp-sat` (default) | The model — OR-Tools CP-SAT, interval-based | Always, unless you specifically want the comparison below |
| `milp-cbc` | Comparison MILP, open-source backend (no install needed) | To reproduce the CP-vs-MILP comparison in RESULTS.md |
| `milp-gurobi` / `milp-cplex` | Same MILP, commercial backend | Only if you have a license; otherwise stick to `milp-cbc` |
| `cp-optimizer` | A second CP engine (IBM ILOG) | Needs a license; falls back to `cp-sat` automatically if unavailable, see FORMULATION.md Appendix B |

`--instance demo` (default, 20 cases) is small enough to read by eye; `--instance
medium` (~200 cases) is where the CP-vs-MILP gap discussed in RESULTS.md actually shows
up. `--benchmark` runs CP-SAT next to every available comparison backend and prints a
table — that's what produced the numbers in RESULTS.md:

```bash
python main.py --instance demo --benchmark --gap 0.0001
```

`--plot PATH` saves a Gantt-style PNG of the resulting schedule. Every run also re-checks
every hard constraint on the printed schedule before showing it
(`src/utils/reporter.py`), so an inconsistent result would be caught, not just displayed.

**Tests** — the acceptance contract every solver's output is checked against:

```bash
python tests/test_model.py
```

---

## Repository structure

```
or-surgery-scheduling/
├── main.py                  # CLI entry point
├── FORMULATION.md           # problem, evidence, why CP over MILP, full model
│                             #   + Appendix A (comparison MILP), B (CP Optimizer), C (calibration notes)
├── FORMULATION_CP.md        # the CP-SAT model's variables/objective/constraints, code-mirrored
├── RESULTS.md               # what the demo produces
├── requirements.txt
│
├── src/
│   ├── model/
│   │   ├── types.py         # SurgicalCase, Surgeon, OperatingRoom, PlanningInstance, SolverResult
│   │   └── penalty.py       # w_c non-scheduling penalty
│   │
│   ├── solvers/
│   │   ├── base_solver.py            # solver-agnostic abstract interface
│   │   ├── cp_sat_interval_solver.py # the model — OR-Tools CP-SAT
│   │   ├── milp_baseline_solver.py   # comparison MILP — CBC/SCIP/Gurobi/CPLEX
│   │   └── cp_optimizer_solver.py    # optional second CP engine, falls back to CP-SAT
│   │
│   ├── data/
│   │   └── instances.py     # demo_instance() · medium_instance()
│   │
│   └── utils/
│       ├── reporter.py      # schedule printer + constraint checks
│       └── visualizer.py    # Gantt-style PNG export (--plot)
│
└── tests/
    └── test_model.py
```

---

## The model, briefly

**Sets:** cases, one work week of days, rooms, surgeons, a shared equipment type.

**Decision variables:** for every feasible (case, day, room) candidate, a presence
flag and a start time, bundled into two CP-SAT interval variables of different
sizes — one for the room (includes cleaning time), one for the surgeon (operative
time only) — plus an unscheduled flag per case.

**Objective:** minimize a weighted tardiness — prefer scheduling high-priority,
close-to-deadline cases earlier, penalize overdue cases more steeply the longer they
wait, and only pay the (dominant) non-scheduling penalty when a case genuinely can't
fit.

**Constraints:** one occurrence per patient per week, priority-4 cases locked to day 1,
schedule-or-penalize for everyone else, room-service/ambulatory/pediatric-block
eligibility, exact room and surgeon non-overlap, surgeon daily/weekly time limits, exact
shared-equipment concurrency, and a downstream recovery/ICU-bed constraint.

**Why CP-SAT, not a bigger MILP:** the problem is disjunctive resource-constrained
scheduling — exactly the structure `NoOverlap`/`Cumulative` exist for, with
polynomial-time propagation instead of a big-M disjunctive encoding. FORMULATION.md §4
makes the full argument; RESULTS.md checks it empirically.

---

## Results, headline

Full discussion in [RESULTS.md](RESULTS.md).

| Instance | CP-SAT | Comparison MILP (CBC) |
|---|---|---|
| Demo (20 cases) | obj **155.0**, 20/20 scheduled, optimal in ~0.1s | obj 157.0, 20/20 scheduled, optimal in ~0.03s |
| Medium (200 cases, 2-min budget) | obj **69,956**, **131**/200 scheduled | obj 74,116, 130/200 scheduled |

CP-SAT wins both, not by searching harder, but by checking actual time overlap on the
shared equipment instead of a daily headcount — RESULTS.md walks through exactly which
cases land where and why the comparison MILP can't reach that schedule at all.

![Demo instance, CP-SAT](docs/img/demo_cp_sat.png)

---

## Open questions

**Passing this off to a developer.** FORMULATION.md §13 has the full answer; short
version: this file plus the data dictionary in `src/model/types.py`, the solver code
with matching C-numbered comments, `tests/test_model.py` as the acceptance bar, and a
short glossary of the few domain terms that aren't self-explanatory — most
miscommunication on a project like this turns out to be vocabulary, not math.

**A reusable library of models.** FORMULATION.md §14 has the full answer; short
version: solver-agnostic data types at the bottom, a shared layer of reusable
constraint patterns (capacity sums, NoOverlap-based non-overlap, tiered-priority
tardiness, eligibility pre-filters), problem templates that compose those patterns, and
a thin solver-adapter layer on top — with the backend choice argued from the problem's
structure and checked empirically, the way this project argues CP over MILP, rather than
defaulted to whichever backend the team knows best.

---

## References

See [FORMULATION.md §15](FORMULATION.md#15-references) for the full list (Cardoen et
al. 2010; Marques & Captivo 2015; Denton et al. 2010; SIGIC; Akbarzadeh & Maenhout 2023;
Vilím 2004; Schutt et al. 2009; OR-Tools CP-SAT documentation).

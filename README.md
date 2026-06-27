# Elective Surgery Scheduling — Interval-Based Constraint Programming

> A weekly elective-surgery scheduling model for a large hospital group: which
> procedure, in which room, at what time, on which day, with which surgeon. The
> primary model is an **interval-based Constraint Programming** formulation solved with
> **Google OR-Tools CP-SAT**. A Mixed-Integer Program (OR-Tools/CBC, optionally Gurobi)
> is also implemented, purely as the comparison point that justifies that choice.
> **Hexaly** is wired up as an optional, license-gated extension for very large/real-time
> instances — not part of the core deliverable.

Read **[FORMULATION.md](FORMULATION.md)** first (problem framing, evidence, why CP over
MIP, assumptions, shared sets/parameters, and two appendices — the MIP in full detail,
and a parameter-justification audit), then **[FORMULATION_CP.md](FORMULATION_CP.md)**
(the CP-SAT model's full variables/objective/constraints math, including two
corrections found and fixed during review — documented with worked numeric examples,
not just asserted), then **[RESULTS.md](RESULTS.md)** for what the demo produces and
why it validates the CP-over-MIP choice.

---

## Quick Start

```bash
# Core dependency — bundles CBC (the comparison MILP) and CP-SAT (the primary model)
pip install ortools

# Run the 20-case demo on the primary model
python main.py --solver cp-sat
```

That's the whole setup: one `pip install`, one command. Everything else below is the
same `main.py` entry point with different flags.

---

## How to Run Each Algorithm

`main.py` doesn't hide the solver choice behind a config file — `--solver` picks the
algorithm directly, so it's explicit which one produced any given run:

```bash
python main.py [--instance demo|medium|chln] [--solver <name>] [--time-limit SECONDS]
                [--gap FRACTION] [--plot PATH] [--benchmark]
```

| `--solver` value | Algorithm | What it is | When to use it |
|---|---|---|---|
| `cp-sat` (default) | Interval-based **Constraint Programming** | OR-Tools CP-SAT: `NewOptionalIntervalVar` + `NoOverlap`/`Cumulative` global constraints, solved by CP-SAT's parallel-portfolio search. **The primary, production model.** | Always, unless you specifically want the comparison point below. |
| `milp-cbc` | **Mixed-Integer Program**, open-source backend | OR-Tools `linear_solver` (pywraplp) driving the bundled CBC solver — no separate install. Day-bucket formulation: presence binaries summed per room/day instead of exact time intervals. | To reproduce the MIP-vs-CP comparison in RESULTS.md, or if you have no CP-SAT/Gurobi available at all. |
| `milp-gurobi` | Same MIP, commercial backend | Identical formulation to `milp-cbc`, routed through native `gurobipy` instead of CBC. Converges to a *proven* optimum far faster (seconds vs. tens of minutes at the 200-case scale). | When you need the MIP's true optimum as a benchmark reference, not just a 30-minute feasible bound (see RESULTS.md Step 1). |
| `milp-scip` | Same MIP, SCIP backend | Another OR-Tools-bundled backend for the same MIP formulation, no extra install. | A free alternative to CBC if you want to cross-check solver-specific behavior. |
| `hexaly` | Local-search metaheuristic | Real (non-stub) integration against Hexaly's API, written as a set-partition formulation. **Falls back to `milp-cbc` automatically** if `hexaly` isn't installed/licensed, printing setup instructions. | Optional extension point for very large instances or same-day re-optimization (FORMULATION.md §14) — not required to see the core model run. |
| `greedy` | Constructive heuristic | Sorts cases by priority/deadline and packs them in greedily, no solver involved. | Sanity-check lower bound, or a warm-start signal for the other solvers — never the final answer. |

Two backend families, one objective: every solver above optimizes the *same* shared
`w_c` penalty (`src/model/penalty.py`) over the *same* `PlanningInstance` — only how
each one expresses "don't double-book a resource" differs (exact time intervals for
CP-SAT, aggregate day-level sums for the MIP family). That's the whole comparison
RESULTS.md is built on.

### Choosing an instance

| `--instance` value | Size | Purpose |
|---|---|---|
| `demo` (default) | 20 cases, 5 rooms, 6 surgeons | Small enough to read the printed schedule by eye; exercises every constraint family in one run. |
| `medium` | ~200 cases, 12 rooms, 17 surgeons | The scaling instance — this is where the CP-vs-MIP gap actually shows up (see Results below). |
| `chln` | ~300 cases, 6 rooms, 10 surgeons | A separate generator whose waiting-time distribution is calibrated to reproduce the *published, audited* CHLN waiting-list statistics (Marques & Captivo, 2015) — see "Testing Against Real Data" below. |

### Useful flags

- `--time-limit SECONDS` (default 120) — wall-clock budget given to the solver. CP-SAT
  and the MIP backends both report whatever they have when time runs out, plus the gap
  to their own best-known bound (never assume "Optimal" means a literally zero gap —
  it means "within the configured gap," always printed).
- `--gap FRACTION` (default 0.01 = 1%) — the relative-gap target the solver stops at
  once reached, e.g. `--gap 0.0001` for a near-exact close on a small instance.
- `--plot PATH` — saves a Gantt-style PNG of the resulting schedule (`src/utils/visualizer.py`):
  `python main.py --solver cp-sat --plot demo.png`.
- `--benchmark` — ignores `--solver` and runs *every* available backend (Greedy,
  CP-SAT, CBC, Gurobi if installed, Hexaly if licensed) back-to-back on the same
  instance, then prints a comparison table. This is what produced every table in
  RESULTS.md:
  ```bash
  python main.py --instance demo --benchmark --gap 0.0001
  ```

### Reading the output

Every run prints, per solver: **Status** (Optimal/Feasible — see the gap caveat
above), **Objective** (lower is better — it's a penalty total, not a count), **Gap**,
how many cases got **Scheduled** out of the total, and wall-clock **Time**, followed by
the actual weekly schedule (day → room → case, with clock times for CP-SAT or
arbitrary same-day ordering for the MIP, which doesn't model exact time at all — see
FORMULATION.md Appendix A). `src/utils/reporter.py` also re-checks every hard
constraint (no double-booking, no surgeon/room/equipment overrun) on the printed
schedule before showing it, so an inconsistent result would be caught, not just
displayed.

### Optional backends and tests

```bash
# Optional: commercial backend for the alternative MILP (requires a license)
pip install gurobipy

# Optional: third backend, license-gated, falls back to CBC if absent (see FORMULATION.md §12)
pip install hexaly

# Run the test suite (acceptance contract: hard constraints + cross-solver consistency)
python tests/test_model.py
```

---

## Repository Structure

```
or-surgery-scheduling/
├── main.py                       # CLI entry point + optional comparison mode
├── FORMULATION.md                # Master doc: problem, evidence, why CP, assumptions, shared sets/params
│                                  #   + Appendix A (MIP in detail) + Appendix B (parameter justification audit)
├── FORMULATION_CP.md             # Full CP-SAT math (variables/objective/constraints C1-C11)
│                                  #   + two corrections found during review, with worked numeric examples
├── RESULTS.md                    # Demo results + the CP-vs-MIP comparison that validates the choice
├── README.md                     # This file
├── requirements.txt
│
├── src/
│   ├── model/
│   │   ├── types.py              # SurgicalCase, Surgeon, OperatingRoom, PlanningInstance, SolverResult
│   │   └── penalty.py            # w_c non-scheduling penalty weight
│   │
│   ├── solvers/
│   │   ├── base_solver.py            # Abstract interface — solver-agnostic
│   │   ├── cp_sat_interval_solver.py # OR-Tools CP-SAT, interval-based — PRIMARY model
│   │   ├── milp_baseline_solver.py   # OR-Tools MPSolver (CBC) + native gurobipy — comparison MILP
│   │   ├── hexaly_solver.py          # Hexaly local-search backend — optional, graceful fallback
│   │   └── greedy_solver.py          # Constructive heuristic (warm-start / sanity bound)
│   │
│   ├── data/
│   │   └── instances.py          # demo_instance() · medium_instance() · literature_chln_instance()
│   │
│   └── utils/
│       ├── reporter.py           # Schedule printer + constraint consistency checks
│       └── visualizer.py         # Gantt-style PNG export (--plot)
│
├── tests/
│   └── test_model.py             # Acceptance tests for the primary model + the comparison MILP
│
└── docs/
    ├── img/                            # Generated schedule plots (see "Visual Schedule" below)
    └── or_surgery_scheduling_beamer.tex # Executive slide deck (LaTeX/Beamer)
```

The `.tex` deck mirrors this README/FORMULATION.md — compile with `pdflatex
or_surgery_scheduling_beamer.tex` (run twice) from inside `docs/`, or upload the file
plus `img/` to Overleaf.

---

## The Model, in Brief

**Sets:** cases $C$, days $D$ (one week), rooms $R$, surgeons $H$, shared equipment $E$.

**Decision variables:** for every feasible (case, day, room) candidate — `presence`
(scheduled there or not), `start`/`end` (exact clock time), bundled into one CP-SAT
interval variable; plus an `unscheduled` indicator per case.

**Objective:** minimize a three-term weighted tardiness — prefer scheduling
high-priority, close-to-deadline cases earlier; penalize overdue cases more steeply the
later they're deferred; pay a dominant penalty only when a case truly cannot be fit in.

**Constraints:** one case per patient/week, priority-4 cases locked to day 1,
schedule-or-penalize for everyone else, room-service eligibility, ambulatory/pediatric
carve-outs, exact room/surgeon non-overlap, surgeon daily/weekly time limits, exact
shared-equipment concurrency, and a downstream recovery/ICU-bed constraint.

**Why Constraint Programming, not a bigger MILP:** the problem is fundamentally
disjunctive resource-constrained scheduling — exactly the structure `NoOverlap` and
`Cumulative` were built for, with polynomial-time propagation instead of a big-M
disjunctive encoding. FORMULATION.md §3 makes the full argument; RESULTS.md shows it
empirically against a comparison MILP.

Full math, assumptions, and what's deliberately left out: **FORMULATION.md** (problem,
evidence, why-CP argument, assumptions) and **FORMULATION_CP.md** (the CP model's
complete variables/objective/constraints, plus two corrections — a priority/penalty
double-counting bug and a surgeon/room interval-sizing bug — found and fixed during
review, each documented with a worked numeric example).

---

## Results — the Demo, and Why It Validates the Model Choice

Full discussion in **[RESULTS.md](RESULTS.md)**. Headline:

### Demo instance (20 cases, 5 rooms, 6 surgeons)

| Solver | Status | Objective | Gap | Scheduled | Time |
|---|---|---|---|---|---|
| **CP-SAT (primary model)** | Optimal | **155.0** | 0.00% | 20/20 | ~0.1s |
| OR-Tools/CBC (comparison MILP) | Optimal | 157.0 | 0.00% | 20/20 | ~0.03s |

CP-SAT finds a *better* schedule, not by searching harder, but by modeling the shared
C-arm correctly: it checks literal time overlap (`AddCumulative`) instead of a
day-count cap, and so legally places multiple C-arm cases on the same day — in the
canonical run captured for the plot below, three of them, sequentially, on Monday — a
placement the MILP's coarser constraint forbids outright (RESULTS.md has the honest
caveat about which *exact* arrangement varies run to run among CP-SAT's tied optima;
the structural conclusion doesn't).

### Medium instance (200 cases, 12 rooms, 17 surgeons), 30-minute / 1% gap budget

True optimum of the alternative MILP (Gurobi, near-zero gap, ~14s): **74,305.0**,
130/200 scheduled. Both backends then given 30 minutes at a 1% gap target:

| Solver | Status | Objective | Own Gap | vs. True MILP Optimum | Scheduled | Time |
|---|---|---|---|---|---|---|
| OR-Tools/CBC | Feasible | 74,383.0 | 0.13% | +0.10% | 130/200 | 1800.6s |
| **CP-SAT (primary model)** | Feasible | **66,471.0** | 1.31% | **−10.54%** | **133/200** | 1801.2s |

CP-SAT's objective is **10.5% below the MILP's proven optimum** while scheduling
**3 more cases** — because it searches a strictly larger, correct feasible region, not
because its own convergence is tighter (it isn't: 1.31% vs. CBC's 0.13%). A smaller
feasible region is easier to fully close; that's not the same as being a better answer.
RESULTS.md spells this out in full, including the honest "Optimal ≠ 0% gap" caveat for
both backends.

---

## Visual Schedule (Gantt-style)

Per the case prompt: "a plain terminal output or a simple image of the schedule is
plenty." Generated with `python main.py --instance <name> --solver cp-sat --plot
out.png` (see `src/utils/visualizer.py`). Each bar is one case; outlined bars are
priority-4 (locked to day 1); colors are surgical service.

**Demo instance, primary CP-SAT model** — real start/end clock times; three C-arm cases
land on Monday alone, sequentially in one room — the schedule the comparison MILP's
day-count equipment cap forbids outright (it spreads its four C-arm cases one per day
across four different days instead — see the demo-instance plot below):

![Demo instance, CP-SAT interval-based](docs/img/demo_cp_sat.png)

**Demo instance, comparison MILP** — same cases, but with no exact clock times (the
MILP doesn't model any — cases within a room-day are laid out back-to-back in an
arbitrary order):

![Demo instance, OR-Tools/CBC comparison](docs/img/demo_baseline_milp.png)

**Medium instance (200 cases), primary CP-SAT model** — the scaling instance, with
exact per-case start times across all 12 rooms:

![Medium instance, CP-SAT interval-based](docs/img/medium_cp_sat.png)

---

## Testing Against Real Data

`demo_instance()`/`medium_instance()` are synthetic, literature-*structured*. A third
instance, `literature_chln_instance()` (`--instance chln`), is literature-*calibrated*:
its waiting-time generator reproduces, by construction, the published 2016 CHLN audit
statistics (Marques & Captivo, 2015) — see FORMULATION.md §13 for the numbers and an
explicit discussion of small-sample variance around them. For testing against actual
hospital OR logs at full scale, two CC BY-4.0 datasets are a direct fit:

- Akbarzadeh & Maenhout (2023). *Real life data for operating room scheduling problem*
  (Ghent University Hospital, May 2017). Mendeley Data.
  https://data.mendeley.com/datasets/n2v49z2vnp/2
- Akbarzadeh & Maenhout (2023). *RealLife operating room scheduling dataset,
  2021-Jan-May* — 20 weekly instances, 8 demand/flexibility configurations. Mendeley
  Data. https://data.mendeley.com/datasets/c8d342266x/1

See FORMULATION.md §13 for how their schema maps onto `PlanningInstance` (no
formulation changes needed, just a loader — intentionally not built here, per the
brief's own "small demo" framing).

---

## Open Questions

### 1. Passing the Torch

I'd hand a developer four things: **(1)** FORMULATION.md and FORMULATION_CP.md alongside
`src/model/types.py` — the dataclasses are the data dictionary, one source of truth.
**(2)** the solver code itself, where every constraint is labeled (C1…C11) and the
matching code carries the same label, so reading them side by side leaves no ambiguity.
**(3)**
`tests/test_model.py` as the acceptance contract — any reimplementation must pass the
same hard-constraint checks on the same demo instance. **(4)** a short glossary of the
handful of domain terms that aren't self-explanatory (room roster, ambulatory, priority
tiers) — most miscommunication on these projects is vocabulary, not math.

### 2. A Library of Models

Four layers, solver-agnostic except the bottom one. First, core data abstractions —
typed dataclasses, no solver imports — that any model is built on top of. Second,
reusable constraint *patterns* (capacity-sum, no-double-booking via NoOverlap,
tiered-priority tardiness objective, eligibility pre-filter) that recur across
scheduling problems, since nurse rostering and bed allocation need the same shapes, not
the same model. Third, problem templates that compose those patterns — this repo's
model is one such template. Fourth, a thin solver-adapter layer, one file per backend
family (MILP, CP, local search), so a new problem picks a backend without rewriting how
its constraints are expressed. The CP-vs-MILP comparison this repo runs is itself a
template for that last layer: justify the backend choice from the problem's structure
first, verify it empirically on a small instance, then commit — rather than defaulting
to whichever backend is most familiar.

---

## References

See **FORMULATION.md §16** for the full citation list (Cardoen et al. 2010; Marques &
Captivo 2015; Denton et al. 2010; SIGIC; Akbarzadeh & Maenhout 2023 real-data sources;
Vilím 2004; Schutt et al. 2009; OR-Tools CP-SAT documentation).

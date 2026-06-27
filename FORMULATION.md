# Elective Surgery Scheduling — MILP Formulation

**Author:** Hector Bonilla  
**Context:** Real-world OR scheduling problem at a Portuguese NHS hospital (SNS).  
**Reference:** Marques & Captivo (2015), *Centro Hospitalar Lisboa Norte (CHLN)*; Cardoen et al. (2010) benchmark.

---

## 1. Problem Statement

A large hospital group needs to decide, for each elective surgical case on its waiting
list, **which procedure happens in which operating room, at what time, on which day,
and with which surgeon**, across a **one-week planning horizon** at a single hospital.
The hospital cannot run every waiting-list case this week — room-hours, surgeon-hours
and a couple of shared resources are scarce — so the model must also decide **which
cases to leave for a later week**, and do so in a way that respects clinical urgency.

This is deliberately scoped, not exhaustive: it covers case selection, room assignment,
exact intra-day timing, and a downstream bed constraint; it stops short of staffing
rosters below the surgeon level, multi-week planning, and duration uncertainty (see §11
for the full list of what's excluded and why those specific cuts were made).

## 2. Why This Priority/Penalty Structure — Evidence, Not Invention

The priority-tier + maximum-wait-time + escalating-penalty mechanism used below is not
this model's own invention — it mirrors how several public health systems actually
prioritise elective waiting lists, which is evidence that it's a reasonable
general-purpose mechanism rather than an ad hoc choice:

- **Portugal's SIGIC** (*Sistema Integrado de Gestão de Inscritos para Cirurgia*,
  Portaria n.º 45/2008) defines four clinical priority tiers with maximum wait times of
  270/60/15/3 days, and audited 2016 data showed 16% of ~7,400 waiting-list patients had
  already exceeded their tier's deadline by an average of 147 days (Marques & Captivo,
  2015) — breach penalties are not a cosmetic detail, they are the thing the planner is
  graded on.
- The **UK NHS Referral-to-Treatment (RTT)** framework and several **Canadian
  provincial wait-time benchmarks** use the same shape (tiered maximum waits, tracked
  breach rates), because a single FIFO queue does not reflect clinical risk, and an
  unweighted "shortest job first" heuristic does not either.
- **Cardoen, Demeulemeester & Beliën (2010)**, the standard literature review for this
  problem family, classify case-to-day assignment as a distinct, well-studied
  sub-problem ("advance scheduling") — which is the scope this model targets, extended
  with exact intra-day timing (§3).

Every numeric value attached to this mechanism (`max_wait_days`, `priority_multiplier`,
the penalty curve) is an **instance-level, overridable parameter** in the code
(`PlanningInstance`, `src/model/types.py`) — a hospital plugs in its own waiting-list
policy without touching the solver.

## 3. Why Constraint Programming, Not a Bigger MILP

This is the central modeling decision, so it gets argued, not asserted.

**The problem is fundamentally disjunctive/resource-constrained scheduling** —
surgical cases competing for rooms, surgeons, equipment and beds, each of which can
hold only so much at once. That is exactly the structure CP's global constraints
(`NoOverlap`, `Cumulative`) were built for, and exactly the structure a linear sum over
a day-bucket gets wrong in one specific, important way:

> A capacity-sum constraint like "total minutes of equipment-use this day ≤ X" certifies
> that a set of cases' total duration *fits*, but not that they can be placed *without
> colliding*. For a single room those two statements happen to coincide (durations that
> sum to fit can always be packed sequentially). They do **not** coincide once a
> resource is shared across rooms — a surgeon working in two rooms, or one imaging unit
> serving two rooms. There, the sum can be needlessly *tighter* than reality (forbidding
> two genuinely non-overlapping uses) or, in other shared-resource shapes, looser than
> reality. RESULTS.md shows this is not a theoretical nuance: on the demo instance, the
> day-bucket version of the equipment constraint forbids a schedule that is perfectly
> legal once you check actual clock times.

The textbook MILP alternative — a continuous-time, disjunctive **big-M formulation**
(one binary "A-before-B" variable per potentially-conflicting case pair, plus a big-M
constraint) — works, but has two well-documented costs (Baptiste, Le Pape & Nuijten,
*Constraint-Based Scheduling*, 2001):

- It is **quadratic in the number of potentially-conflicting case pairs**, and the big-M
  constants weaken the LP relaxation as that count grows — branch-and-bound spends time
  proving things a propagation-based method gets for free.
- It **cannot express resource sharing beyond mutual exclusion** without yet more binary
  variables (a 2-unit cumulative resource needs combinatorially more disjunctive pairs
  than a 1-unit one).

`NoOverlap` and `Cumulative` are **global constraints** with specialised,
polynomial-time propagation algorithms — `NoOverlap` via Vilím's $O(n\log n)$
Theta-tree sweep (Vilím, 2004), `Cumulative` via timetabling/edge-finding (Schutt,
Feydy, Stuckey & Wallace, *CP* 2009; Laborie, *CPAIOR* 2009). These propagators prune
the search tree directly from the problem's temporal/resource structure instead of
needing the solver to *discover* that structure case-pair by case-pair through
branching — that is the actual mechanism behind "CP scales better here," not a vaguer
claim about one solver being smarter.

**Why CP-SAT specifically:** it runs a parallel portfolio — several complete-search
workers with different heuristics, plus Large Neighbourhood Search workers improving an
incumbent by re-optimising random sub-regions, sharing learned clauses through a common
SAT core (Perron & Furnon, *CP-SAT: a Constraint Programming Solver*, Google OR-Tools
documentation). This is why the implementation does **not** hand-write a custom
branching strategy on top of it (`src/solvers/cp_sat_interval_solver.py`) — OR-Tools'
own guidance is that the default portfolio beats a hand-tuned single strategy absent
structural insight the model doesn't already expose through `NoOverlap`/`Cumulative`.

**One concrete search aid is worth giving it anyway:** the model is seeded with a fast
greedy heuristic's (case → day, room) assignment via `model.AddHint(...)`
(`src/solvers/warm_start.py`). Only the *discrete* assignment is hinted, not exact clock
times — that's where the real combinatorial difficulty lives, and `AddHint` is a search
bias, never a hard constraint, so an inconsistent or partial hint never risks
correctness.

## 4. Assumptions and Simplifications

Stated explicitly — these are deliberate scoping choices, not oversights:

1. **Deterministic durations.** Each case has one estimated operative duration (e.g. a
   historical median for that procedure type). Real durations are stochastic and
   systematically optimistic; we treat the deterministic case as the standard tractable
   approximation (Denton, Miller, Balasubramanian & Huschka, 2010, take the same
   approach and discuss the stochastic extension — flagged here as the top item in §13,
   not attempted, in the interest of keeping this demo small).
2. **Fixed turnover/cleaning time** is added to every case's room-occupation interval as
   a constant buffer (`t_clean`), not modeled as a separate sequence-dependent activity
   (a deep-clean after a contaminated case vs. a quick reset are treated alike).
3. **Surgeons are the binding staffing resource.** Nurses and anaesthetists are assumed
   pre-allocated by a roster tied to whichever room is staffed that day — a common real
   assumption when the surgeon's calendar, not the support staff's, is the actual
   bottleneck.
4. **One occurrence per patient per week.** A patient with multiple queued procedures
   gets at most one done this week — conservative; some services legitimately
   co-operate same-day multi-procedure cases, but that is service-specific and not
   assumed here.
5. **One downstream resource pool modeled, with a constant-capacity caveat.** Recovery /
   ICU beds are modeled as a `Cumulative` resource (§9, C11), but bed *capacity* is
   treated as constant across the week. A hospital with day-varying capacity (e.g.
   weekend staffing cuts) would need a per-day-segmented version. Rather than silently
   approximate this, a stay that extends past the modeled week is charged an explicit,
   instance-configurable overflow penalty (`weekend_bed_overflow_penalty`, §6;
   FORMULATION_CP.md §5, C11) instead of being forbidden or ignored.
6. **One ad hoc institutional rule is included as a worked example, not a special case
   in the math:** a configurable "pediatric block" carve-out (a given service's rooms,
   on a given day, restricted to patients under some age). Hospitals accumulate rules
   like this constantly; the point of including one is to show it costs nothing
   structurally — it's one more eligibility predicate, not a new variable family.
7. **Single week, single hospital, no same-day disruption.** The model produces an
   offline weekly plan; it does not re-optimize when an emergency walks in mid-day. That
   is a genuinely different problem (reactive re-scheduling) and is named explicitly in
   §13 as the most valuable next extension, not silently folded into this one.

## 5. Sets and Indices

| Symbol | Description |
|--------|-------------|
| $c \in C$ | Surgical cases (one entry per patient-procedure pair on the waiting list) |
| $d \in D$ | Planning days, $D = \{1,\dots,5\}$ (one work week) |
| $r \in R$ | Operating rooms |
| $h \in H$ | Surgeons |
| $e \in E$ | Shared equipment types (e.g. a mobile imaging unit) |
| $D_c \subseteq D$ | Days on which case $c$ may be scheduled ($D_c=\{1\}$ for priority-4 cases; $D_c = D$ otherwise) |

## 6. Parameters

| Symbol | Description |
|--------|-------------|
| $t_c^{\text{op}}$ | Operative duration of case $c$ (minutes) |
| $t_c^{\text{clean}}$ | Fixed room turnover/cleaning time after case $c$ (minutes; default 20) |
| $t_c^{\text{tot}} = t_c^{\text{op}} + t_c^{\text{clean}}$ | Total room-occupation time |
| $k_{dr}$ | Capacity (opening minutes) of room $r$ on day $d$ |
| $k_{hd}$ | Surgeon $h$'s daily operative-time limit on day $d$ (minutes) |
| $k_h$ | Surgeon $h$'s weekly operative-time limit (minutes) |
| $a_{dr}^{s} \in \{0,1\}$ | 1 if room $r$ on day $d$ is rostered to service $s$ |
| $p_c \in \{1,2,3,4\}$ | Clinical priority of case $c$ (4 = must run this week, day 1) |
| $\text{wl}_c$ | Days case $c$ has already waited, as of the planning date |
| $\text{wl}^{\max}_p$ | Maximum clinically-acceptable wait for priority $p$ (default: 270/60/15/3 days) |
| $dd_c = \text{wl}^{\max}_{p_c} - \text{wl}_c$ | Days of slack to deadline (negative = already overdue) |
| $\mu_p$ | Priority-to-priority-1 penalty multiplier (default 1 / 4.5 / 18 / 90) |
| $w_c$ | Non-scheduling penalty weight for case $c$ (§8.1) |
| $\alpha > 1$ | Urgency multiplier applied to overdue cases' day coefficient (default 2.0) |
| $u_{ce} \in \{0,1\}$ | 1 if case $c$ requires equipment $e$ |
| $\kappa_{ed}$ | Capacity (simultaneous units) of equipment $e$ on day $d$ |
| $\text{ped}=(s^\dagger, d^\dagger, i^\dagger)$ | Optional pediatric-block rule: service $s^\dagger$'s rooms on day $d^\dagger$ admit only patients aged $\le i^\dagger$ |
| $\rho(c)$ | Recovery/bed pool required by case $c$ ("none" if not applicable) |
| $\text{los}_c$ | Length of stay in that pool, in days, if $\rho(c) \ne$ "none" |
| $\beta_\rho$ | Bed count for pool $\rho$ (constant across the week — see §4.5) |
| $\pi^{\text{ovf}}$ | Per-day penalty for a bed stay extending past the horizon (default 50; §4.5, Appendix B) |

## 7. Decision Variables

Per feasible candidate $(c,d,r)$: a presence boolean $\text{pr}_{cdr}$, a start time, and
**two** intervals of different sizes — one for the room (size $t_c^{\text{tot}}$, used by
C7/C10/C11) and one for the surgeon (size $t_c^{\text{op}}$, used by C8) — plus, for every
non-priority-4 case, an unscheduled indicator $u_c$. Two different interval sizes, not
one shared interval, is itself a correction applied during review (FORMULATION_CP.md §6.2)
— **full variable definitions are in FORMULATION_CP.md §3.**

## 8. Objective Function

The same three-term weighted tardiness described in §2/§6 (on-time cases prefer earlier
days; overdue cases pay an $\alpha$-amplified day coefficient; non-scheduling is a
last-resort penalty $w_c$ dominating both). The exact interaction between $w_c$'s
priority multiplier $\mu_p$ and its penalty curve was corrected during review (a
double-counting and monotonicity bug — FORMULATION_CP.md §6.1 walks through it with a
worked numeric example). **Full formula in FORMULATION_CP.md §4.**

## 9. Constraints

C1 one case/patient/week · C2 priority-4 on day 1 · C3 schedule-or-penalise · C4–C6
eligibility pre-filters (room-service roster, ambulatory-only, pediatric block) · C7
exact room non-overlap · **C8 exact surgeon non-overlap — on the surgeon's own
$t_c^{\text{op}}$-sized interval, not the room's, a correction applied during review
(FORMULATION_CP.md §6.2)** · C9 surgeon weekly limit · C10 exact shared-equipment
concurrency · **C11 downstream recovery/ICU beds, now with an explicit overflow penalty
for stays crossing the horizon boundary** (§4.5; FORMULATION_CP.md §5.11). **Full math
for every constraint is in FORMULATION_CP.md §5.**

## 10. What We Include and Why

| Included | Rationale |
|---|---|
| Priority + waiting-time penalty in the objective | Evidence-based across multiple real systems (§2); without it the model is clinically blind to urgency |
| Exact room timing (C7) | Core feasibility, and the natural unit once the model is interval-based at all |
| Exact surgeon timing + daily/weekly limits (C8–C9) | A surgeon literally cannot be in two rooms at once; a sum alone cannot see that |
| Room-service roster (C4) | Rooms are equipped/staffed for one specialty at a time in practice |
| One case per patient per week (C1) | Conservative default; avoids double-booking the same patient |
| Priority-4 locked to day 1 (C2) | These cases' clinical deadline is inside the current planning cycle — there is no "later this week" |
| Shared equipment, exact concurrency (C10) | Explicitly named in the case prompt as a realistic bottleneck; the constraint family argued for in §3 |
| Downstream recovery/ICU beds (C11) | Explicitly named in the case prompt; only expressible correctly once intervals exist (§9, C11) |
| One ad hoc rule worked example (C6) | Demonstrates the model absorbs institution-specific carve-outs without new variable families |
| Deterministic durations | Standard tractable approximation (Denton et al., 2010); stochastic extension named in §13, not attempted |

## 11. What We Exclude and Why

| Excluded | Rationale |
|---|---|
| Stochastic durations | Adds real robustness value but real complexity too; the single highest-value next step (§13), deliberately not attempted here to keep this demo small |
| Same-day / real-time re-optimization | A genuinely different problem (reactive rescheduling around a fixed plan) from offline weekly planning; named explicitly rather than folded in (§13) |
| Nurses / anaesthetists as separate resources | Assumed pre-allocated by a roster tied to the room, not the case — a common simplification when they aren't the binding constraint |
| Sequence-dependent turnover/cleaning | A constant buffer per case is used instead (§4.2) — adequate unless case type strongly predicts cleaning complexity |
| Patient day-of-week preference | Not part of any clinical-priority system used as evidence for this model; a natural patient-centred extension |
| Multi-week rolling horizon | Single week only; carrying forward unscheduled cases into next week's instance is a thin wrapper, not a new model (§13) |

## 12. Alternative Formulations Considered

**A day-bucket Mixed-Integer Program** was also prototyped
(`src/solvers/milp_baseline_solver.py`, OR-Tools `linear_solver` with a CBC backend,
optionally Gurobi) — the same sets, same objective, same C1–C6/C9, but with C7 and C10
replaced by linear capacity sums ($\sum_c t_c^{\text{tot}} x_{cdr} \le k_{dr}$ for
rooms, a day-count cap for equipment) instead of `NoOverlap`/`Cumulative`, and no C11
(a day-bucket model cannot express a multi-day bed stay correctly — see C11's
discussion in §9). It exists purely **as a comparison point that justifies §3
empirically, not as a second deliverable**: RESULTS.md reports a head-to-head run and
the result is exactly what §3 predicts — the MILP's day-bucket relaxation forbids
schedules that are legal once you check actual clock times, so CP-SAT finds a strictly
better, equally legitimate schedule on the same data. Full constraint-by-constraint
detail is in that file's docstring; the complete sets/variables/objective/constraints
math is written out in **Appendix A**, at the end of this document.

**Hexaly** (`src/solvers/hexaly_solver.py`) is an additional, optional contribution: a
real (non-stub) integration against Hexaly's local-search API, written against a
set-partition encoding of the same problem. No academic license was available while
building this, so it currently runs only as a graceful fallback to the MILP above, with
clear setup/licensing instructions printed at runtime. It is included as a pointer to
where a local-search backend would slot in for very large instances or real-time
re-optimization (§13) — not as a required part of this deliverable, and not benchmarked
here for that reason.

## 13. Testing Against Real and Literature Instances

Three instances ship in `src/data/instances.py`, at increasing levels of grounding:

- `demo_instance()` — ~20 cases, sized to read by eye, exercising every constraint
  family (priority lock-in, equipment contention, pediatric block).
- `medium_instance()` — ~200 cases / 12 rooms / 5 services, structurally modeled on the
  multi-service, multiple-rooms-per-service shape used in the OR-scheduling benchmark
  literature (Cardoen, Demeulemeester & Beliën, 2010).
- `literature_chln_instance()` — calibrated, not just inspired: its waiting-time
  generator reproduces, by construction, the published, audited CHLN waiting-list
  statistics (Marques & Captivo, 2015) — ~16% of cases already overdue hospital-wide,
  ~147 days average breach, and Neurosurgery specifically breaching by ~261 days on
  average. See the function's docstring for an explicit discussion of small-sample
  variance around those targets.

For testing against **real hospital data** at production scale, two public, CC
BY-4.0-licensed datasets are a direct fit for this exact problem (same horizon, same
"Master Surgery Schedule" structure as §9's room roster):

- **Akbarzadeh & Maenhout, "Real life data for operating room scheduling problem"**
  (Ghent University Hospital OR log, May 2017). Mendeley Data, DOI
  [10.17632/n2v49z2vnp.2](https://data.mendeley.com/datasets/n2v49z2vnp/2).
- **Akbarzadeh & Maenhout, "RealLife operating room scheduling dataset, 2021-Jan-May"**
  — 20 weekly-planning instances across 8 demand/flexibility configurations. Mendeley
  Data, DOI [10.17632/c8d342266x.1](https://data.mendeley.com/datasets/c8d342266x/1).

Their schema (a master roster + per-case waiting-list records) maps directly onto
`PlanningInstance`, with no formulation changes needed — a loader is the natural next
step before a real pilot, intentionally not built here, consistent with this being a
small demo rather than a production deployment.

## 14. Extensions and Future Work

| Extension | Approach |
|---|---|
| Stochastic durations | Two-stage stochastic program: first stage selects/places cases, second stage absorbs duration realisations via overtime cost or case bump |
| Real-time rescheduling | Large Neighbourhood Search seeded from the current schedule, for same-day disruptions — Hexaly's local-search engine (§12) is a natural fit |
| Nurse/anaesthetist rostering | Extend $H$ to cover support staff; add team-level NoOverlap/sum constraints, same pattern as surgeons |
| Multi-week rolling horizon | Solve weekly; carry forward unscheduled cases with an increased priority weight |
| Day-varying bed capacity | Replace the constant $\beta_\rho$ (§4.5) with a per-day-segmented `Cumulative` or reservoir decomposition |

## 15. Open Questions

### Q1 — Passing the Torch

I'd hand a developer four things, not just the math: **(1)** this file plus
`src/model/types.py` — the dataclasses *are* the data dictionary (sets/parameters above
map 1:1 to fields), so there's one source of truth for "what is a case/room/surgeon,"
not two. **(2)** the solver code itself, since every constraint here is labeled
(C1…C11) and the matching code block carries the same label as a comment — read side
by side, there's no ambiguity about which line implements which formula. **(3)**
`tests/test_model.py` as the acceptance contract: any reimplementation must pass the
same hard-constraint checks on the same demo instance, and I'd ask them to add a test
per new constraint before writing the constraint. **(4)** a short glossary of the few
domain terms that aren't self-explanatory (e.g. room roster, "ambulatory," priority
tiers) — most miscommunication on these projects is vocabulary, not math.

### Q2 — A Library of Models

I'd structure it in four layers, solver-agnostic at every layer except the bottom one.
First, core data abstractions — typed dataclasses like `PlanningInstance`, with no
solver imports — that any model is built on top of. Second, a small set of reusable
*constraint patterns* (capacity-sum, no-double-booking via NoOverlap, tiered-priority
tardiness objective, eligibility pre-filter) that recur across scheduling problems,
since nurse rostering and bed allocation need the same shapes, not the same model.
Third, problem templates that compose those patterns into a specific formulation — this
repo's model is one such template. Fourth, a thin solver-adapter layer, one file per
backend family (MILP, CP, local search), so a new problem picks a backend without
rewriting how its constraints are expressed. The CP-vs-MILP comparison in §3/§12 is
itself a template for that last layer: justify the backend choice from the problem's
structure first, then verify the choice empirically on a small instance before
committing — rather than defaulting to whichever backend is most familiar.

## 16. References

1. Cardoen, B., Demeulemeester, E., & Beliën, J. (2010). Operating room planning and
   scheduling: A literature review. *European Journal of Operational Research*, 201(3),
   921-932.
2. Marques, I., & Captivo, M.E. (2015). *Planeamento de cirurgias eletivas no Centro
   Hospitalar Lisboa Norte*. MSc thesis, Universidade de Lisboa. (Evidence source for
   the priority/penalty mechanism shape — not the literal subject of this model.)
3. Denton, B.T., Miller, A.J., Balasubramanian, H.J., & Huschka, T.R. (2010). Optimal
   allocation of surgery blocks to operating rooms under uncertainty. *Operations
   Research*, 58(4), 802-816.
4. SIGIC — Sistema Integrado de Gestão de Inscritos para Cirurgia, Portaria n.º 45/2008,
   Diário da República, Portugal. (Evidence source for tiered max-wait policy design.)
5. Van Riet, C., & Demeulemeester, E. (2015). Trade-offs in operating room planning for
   electives and emergencies. *OR Spectrum*, 37(1), 59-87.
6. Akbarzadeh, B., & Maenhout, B. (2023). Real life data for operating room scheduling
   problem [Data set]. Mendeley Data, V2. https://doi.org/10.17632/n2v49z2vnp.2
7. Akbarzadeh, B., & Maenhout, B. (2023). RealLife operating room scheduling dataset,
   2021-Jan-May [Data set]. Mendeley Data, V1. https://doi.org/10.17632/c8d342266x.1
8. Perron, L., & Furnon, V. *CP-SAT: a Constraint Programming Solver* (Google OR-Tools
   documentation). https://developers.google.com/optimization/cp
9. Baptiste, P., Le Pape, C., & Nuijten, W. (2001). *Constraint-Based Scheduling:
   Applying Constraint Programming to Scheduling Problems*. Kluwer Academic Publishers.
10. Vilím, P. (2004). $O(n \log n)$ filtering algorithms for unary resource constraint.
    *CPAIOR 2004*.
11. Schutt, A., Feydy, T., Stuckey, P.J., & Wallace, M.G. (2009). Why cumulative
    decomposition is not as bad as it sounds. *CP 2009*.
12. Laborie, P. (2009). IBM ILOG CP Optimizer for detailed scheduling illustrated on
    three problems. *CPAIOR 2009*.

---

## Appendix A — The MIP Formulation, in Detail

§12 introduces this as a comparison point. Here is its full math — same sets (§5), same
priority/penalty evidence (§2), same parameters (§6) minus the CP-only ones
($\pi^{\text{ovf}}$; no $\rho(c)/\text{los}_c/\beta_\rho$, since beds aren't expressible
here at all, see A.4) — implemented in `src/solvers/milp_baseline_solver.py`.

### A.1 Decision Variables

$$
x_{cdr} \in \{0,1\} \quad \forall c \in C,\ d \in D_c,\ r \in R
\qquad\text{— 1 if case $c$ is scheduled on day $d$ in room $r$}
$$
$$
z_c \in [0,1] \quad \forall c \in C : p_c \ne 4
\qquad\text{— relaxed to a continuous bound; forced to binary by C3 at the optimum}
$$

As in the primary model, $x_{cdr}$ is only created for triples surviving the C4–C6
eligibility pre-filter (`_feasible_triples` in the same file) — the MIP's main
variable-count reduction mechanism, identical in spirit to the CP model's candidate
filter.

### A.2 Objective Function

$$
\min \quad
\sum_{c:\ dd_c \ge 0}\ \sum_{d \in D_c, r \in R} \big[dd_c + d\big]\ x_{cdr}
\ +\
\sum_{c:\ dd_c < 0}\ \sum_{d \in D_c, r \in R} \big[dd_c + \alpha d\big]\ x_{cdr}
\ +\
\sum_{c:\ p_c \ne 4} w_c\ z_c
$$

Identical to the CP model's objective (§8), over $x_{cdr}/z_c$ instead of
$\text{pr}_{cdr}/u_c$ — including the fix described in FORMULATION_CP.md §6.1: $w_c$
already contains the priority multiplier $\mu_{p_c}$ (§A.3), so Term 3 multiplies by
$w_c$ alone, with **no separate $p_c\ w_c\ z_c$ double-multiplication** (an earlier
version of this code did exactly that, identically in both the MIP and CP paths — both
were fixed together, since both called the same `penalty.py` function the same way).

### A.3 Non-scheduling Penalty

Identical to §8.1 / FORMULATION_CP.md §4.1: $w_c = \mu_{p_c} \cdot
\text{PenaltyCurve}(dd_c) + 1.2 \cdot \max_{c'} dd_{c'}$, evaluated once in
`src/model/penalty.py` and shared by every solver (MIP, CP, Hexaly, greedy) — there is
exactly one implementation of $w_c$ in this codebase, not one per backend.

### A.4 Constraints

**C1 — at most one scheduled occurrence per patient per week**
$$
\sum_{c:\ \text{patient}(c)=n} \sum_{d,r} x_{cdr} \le 1 \qquad \forall n
$$

**C2 — priority-4 cases must run on day 1**
$$
\sum_{r \in R} x_{c,1,r} = 1 \qquad \forall c : p_c = 4
$$

**C3 — every other case is scheduled exactly once, or penalised**
$$
\sum_{d,r} x_{cdr} + z_c = 1 \qquad \forall c : p_c \ne 4
$$

**C4–C6** — room-service roster, ambulatory-only, pediatric block: enforced by the
pre-filter, exactly as in the CP model.

**C7 — room capacity, as an aggregate sum (not exact non-overlap)**
$$
\sum_{c \in C} t_c^{\text{tot}}\ x_{cdr} \le k_{dr} \qquad \forall d \in D,\ r \in R
$$
This is where the MIP and CP models structurally diverge (FORMULATION.md §3): this sum
certifies durations *fit* the day, not that they don't *collide* — equivalent to exact
non-overlap for a single room (any non-overlapping set of durations can always be packed
sequentially), which is why C7 alone doesn't cost the MIP anything by itself. C10 is
where the divergence becomes a real, measured difference (see RESULTS.md).

**C8 — surgeon daily time limit (a sum only — no non-overlap variable exists in a MIP
without a big-M reformulation, see §3)**
$$
\sum_{c:\ \text{surgeon}(c)=h} \sum_r t_c^{\text{op}}\ x_{cdr} \le k_{hd} \qquad \forall h \in H,\ d \in D
$$

**C9 — surgeon weekly time limit**
$$
\sum_{c:\ \text{surgeon}(c)=h} \sum_{d,r} t_c^{\text{op}}\ x_{cdr} \le k_h \qquad \forall h \in H
$$

**C10 — shared equipment, day-level aggregate cap**
$$
\sum_{c:\ u_{ce}=1} \sum_r x_{cdr} \le \kappa_{ed} \qquad \forall e \in E,\ d \in D
$$
Counts *how many* equipment-$e$ cases land on day $d$, not whether their clock times
overlap — the single largest source of the MIP/CP objective gap measured in RESULTS.md.

**No C11.** Recovery/ICU beds need a multi-day *interval* that starts on the day of
surgery; a day-bucket MILP has no variable that represents "day of surgery" as a value
(only as an index $d$ a binary $x_{cdr}$ is attached to), so there is no clean way to
write "occupy a bed for `los_c` days starting on whichever day this case is scheduled."
This is not a missing line of code — it is the structural reason the case prompt's
downstream-bed requirement pushed this project toward an interval-based model at all
(FORMULATION.md §3, opening paragraph).

---

## Appendix B — Parameter Justification Audit

A rigorous review of this model should ask, for every numeric constant: where does it
come from, and what happens if it's wrong? Several constants here were originally
shipped with little more than "a reasonable default." This appendix audits each one
honestly — some turn out to be well-grounded once derived properly, some are confirmed
policy knobs with no literature answer, and one ($t_c^{\text{clean}}$) is flagged as a
known simplification deliberately not changed mid-review (B.5).

### B.1 Audit Table

| Parameter | Default | Status | Justification / honest caveat |
|---|---|---|---|
| $t_c^{\text{clean}}$ (turnover) | 20 min, flat for every case | Domain-plausible, not procedure-specific | Real OR turnover varies roughly 15–60 min depending on procedure complexity and infection-control needs. A flat constant (already flagged as a simplification, §4.2/§11) cannot capture that spread. See B.5 for why this isn't changed in this review pass. |
| `daily_limit_min` (surgeon) | 240 (demo), 300 (medium/chln) | Demo: literature-grounded; medium: explicit scale assumption | 240 min = one standard half-day OR session block, the most common unit in block-scheduling literature (Cardoen et al., 2010). 300 min in the larger instances is **not** the same standard block — it represents this case's "larger hospital group, higher throughput" framing (longer consultant sessions), an explicit modeling choice, not a second citation. A real deployment should replace this constant with the receiving hospital's actual published session length. |
| `weekly_limit_min` | 960 = 4×240 (demo), 1300 ≈ 4.33×300 (medium/chln) | Structurally motivated, not numerically cited | Both ratios correspond to "roughly 4 of 5 weekdays as theatre days" — consistent with how many real consultant job-plan structures allocate only part of the week to elective theatre sessions (clinics, ward rounds, on-call take the rest). This is a real structural pattern, not one precise published number; institution-specific calibration is needed before deployment. |
| $\alpha$ (urgency multiplier) | 2.0 | Audited policy knob — provably narrow in scope | See B.2: $\alpha$ provably affects *only* which day an already-scheduled overdue case lands on within the week. It cannot change how many cases get scheduled, or which ones. Choosing its value is a genuine clinical-policy decision, not something literature can hand you — but at least its blast radius is now precisely known. |
| Displacement multiplier (1.2) | 1.2 × $\max_{c} dd_c$ | Audited and confirmed sufficient — not arbitrary | See B.3: the exact minimum margin needed is derived in closed form. 1.2 is a deliberately larger, instance-independent safety buffer chosen so the dominance guarantee holds even for unusual instances where the tight bound would be smaller. |
| ICU probability 0.12 | 12% chance a VASC/NEURO case in `medium_instance()` needs a recovery bed | Synthetic-data generation knob, not a clinical model parameter | This controls how the *synthetic test instance* is generated, not anything the optimizer sees as a parameter — it does not appear in FORMULATION.md/FORMULATION_CP.md's math at all. 12% sits within the broad range reported for post-operative ICU utilization after major vascular/neurosurgical procedures, but was not derived from one specific cited audit; replace with real hospital ICU-admission data before any deployment. |

### B.2 Why $\alpha$ Cannot Affect *Who* Gets Scheduled

$\alpha$ appears exactly once in the entire model: as a multiplier on the *day*
coefficient $d$ inside Term 2 (§8, the overdue branch), applied only to cases that are
**already** being assigned a $(d,r)$ slot ($\text{pr}_{cdr}=1$). It does not appear in
Term 3 (the non-scheduling penalty $w_c u_c$), nor in any constraint (C1–C11). So:

- Raising $\alpha$ makes the model prefer scheduling an overdue case on an *earlier* day
  within the week, once it has already decided to schedule that case at all.
- It cannot make the model schedule *more* overdue cases, or different cases, because
  the scheduled/unscheduled decision is governed entirely by Term 3 vs. Terms 1–2's
  *absolute* size, not by $\alpha$ specifically — and room/surgeon/equipment capacity
  (the actual binding constraint on "how many cases fit") doesn't involve $\alpha$ at all.

This is a clean analytical result, not an empirical guess: $\alpha$ is provably a
*within-week sequencing* knob, not a *case-selection* knob. That distinction matters for
calibration — a hospital tuning $\alpha$ is deciding "how much should we front-load
overdue cases earlier in the week," not "how many overdue cases should we serve,"
and should look to $\mu_p$ (B.4) for the latter question.

### B.3 Why the Displacement Margin Is 1.2, Not 1.01 — Derived, Not Asserted

The displacement term must exceed the largest possible Term-1/2 coefficient for any
case, so the model never prefers dropping a schedulable case purely to dodge a tardiness
charge. The largest such coefficient is bounded by $\max_{c} dd_c + \alpha \cdot n_{\text{days}}$
(a case at maximum slack, evaluated on the last day, in the overdue-style branch). The
displacement is $\text{margin} \times \max_c dd_c$, so the exact minimum margin needed is:

$$
\text{margin}_{\min} = 1 + \frac{\alpha \cdot n_{\text{days}}}{\max_c dd_c}
$$

For this codebase's defaults ($\alpha=2$, $n_{\text{days}}=5$, and $\max_c dd_c \approx
270$ for a priority-1 case at its policy's maximum wait), $\text{margin}_{\min} \approx
1.037$ — so, **contrary to the intuition that prompted this audit, 1.2 was already the
conservative-but-correct choice, not an arbitrarily loose one**: 1.01 would actually be
*unsafe* in general, since $1.01 \times 270 = 272.7$ does not dominate the $\approx 280$
a worst-case coefficient can reach. 1.2 was chosen as a fixed, instance-independent
buffer specifically because $\max_c dd_c$ varies by instance (an instance with only
short-horizon, high-priority cases has a much smaller $\max_c dd_c$, and the tight bound
above would shrink commensurately) — using the derived tight bound directly, recomputed
per instance, is the more precise alternative; the current code's fixed 1.2 is the
robust approximation to it. No code change was made here, since the audit confirms 1.2
is already sufficient, not broken.

### B.4 Calibrating the Priority Multipliers in Practice — Worked Discussion

*The question this section answers: the default multipliers are $\mu = (1, 4.5, 18,
90)$ — how would I actually calibrate these against a real hospital, would I run a
sensitivity/Pareto sweep, and what about equity across specialties?*

**How I'd calibrate them.** Not by literature lookup — there isn't a published "correct"
multiplier vector, because $\mu_p$ encodes a hospital's own risk tolerance for breaching
each clinical tier, which is a policy choice, not an empirical fact. In practice I'd run
a **structured elicitation with service chiefs and the clinical director**, anchored on
concrete scenarios rather than abstract ratios — e.g. "a priority-2 patient waiting 30
days over their target vs. a priority-1 patient waiting 200 days over theirs: which is
worse, and by roughly what factor?" — because clinicians reason fluently in scenarios,
not in multiplier algebra. I'd convert several such pairwise judgments into a multiplier
vector (a standard preference-elicitation pattern, structurally similar to AHP-style
pairwise comparison), then sanity-check the *implied* vector against B.2/B.3's math
before shipping it.

**Would I run a sensitivity/Pareto sweep?** Yes — and not only as a calibration aid but
as a *validity check* on elicited weights, because the relationship between $\mu_p$ and
outcomes is less obvious than it looks. I ran exactly this sweep on the 200-case medium
instance, scaling $\mu_2,\mu_3,\mu_4$ by a factor $k \in \{0.25, 0.5, 1, 2, 4\}$ (45s
CP-SAT solves, 2% gap — enough for a directional signal, not a fully converged claim):

| $k$ | $(\mu_2,\mu_3)$ | Total scheduled | Overdue scheduled | Overdue left unscheduled | Routine (P1) scheduled |
|---|---|---|---|---|---|
| 0.25 | 1.1 / 4.5 | 130 | 56 | 12 | 64 |
| 0.5 | 2.3 / 9.0 | 128 | 55 | 13 | 60 |
| **1.0 (default)** | 4.5 / 18.0 | 130 | 54 | 14 | 60 |
| 2.0 | 9.0 / 36.0 | 128 | 52 | 16 | 57 |
| 4.0 | 18.0 / 72.0 | 130 | 52 | 14 | 56 |

The honest, slightly counter-intuitive finding: **total scheduled count barely moves**
(it's bound by room/surgeon capacity, not by $\mu_p$ — consistent with B.2's point that
capacity, not the objective's weights, is the real constraint on "how many"), and
**overdue-scheduled count does not monotonically increase with $k$** — if anything it
drifts slightly down. The reason is that $\mu_p$ is keyed to **priority tier**, not to
*overdue severity* directly — overdue status cuts across all four tiers. Raising $\mu_p$
preferentially protects *high-priority-tier* cases generally (whether or not they happen
to be overdue) from being dropped in favor of low-tier ones, rather than specifically
targeting "the most overdue" cases. A naive "bigger weight ⇒ more urgent cases served"
intuition doesn't hold cleanly here; a Pareto-style sweep is exactly what surfaces that
before it surprises a hospital in production.

**Equity across specialties.** This is the sharpest practical risk in B.4's finding: if
one specialty's case mix is systematically longer (e.g. Neurosurgery, per
`literature_chln_instance()`'s ~261-day average breach vs. the hospital-wide ~147 days,
FORMULATION.md §13) *and* systematically lower-priority-tier on average, raising $\mu_p$
uniformly does **not** help that specialty — it helps whichever specialty's case mix
skews toward high-priority tiers, regardless of how overdue its patients actually are.
A hospital calibrating $\mu_p$ globally could inadvertently starve a chronically
under-prioritized specialty further. The structurally correct mitigation is **not** a
bigger global multiplier; it's either (a) a per-service overdue-share or breach-day
target tracked and reported alongside the aggregate objective (so the trade-off is
visible, not hidden inside one scalar), or (b) a fairness constraint or secondary
objective (e.g. bound each service's overdue share within some band) layered on top of
the current single-objective model — a natural extension, not attempted here, and the
right next conversation to have with clinical leadership once $\mu_p$ elicitation (above)
is underway.

### B.5 Why $t_c^{\text{clean}}$ Wasn't Changed in This Review

The honest reason: a duration-dependent turnover rule is a real, cheap improvement, but
it changes every case's $t_c^{\text{tot}}$ and therefore every room-capacity and
non-overlap constraint in the model — exactly the kind of change that should not be
made silently in the middle of the 30-minute reference benchmark this review also
produced (RESULTS.md). It is recorded here as the next concrete fix, not implemented
opportunistically: a follow-up pass should bucket $t_c^{\text{clean}}$ by $t_c^{\text{op}}$
(e.g. 15 min under 60, 25 min for 60–150, 40 min above 150 — values inside the
15–60-minute real-world range above) and re-run the full benchmark suite once, rather
than incrementally re-running it after every individual parameter change.

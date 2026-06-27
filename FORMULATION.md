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

**IBM ILOG CP Optimizer** (`src/solvers/cp_optimizer_solver.py`) is a second, optional
constraint-programming backend — not a re-skin of the primary CP-SAT model, but a
genuinely different set of CP modelling primitives applied to the same problem, chosen
specifically to answer Appendix B.1's flagged weakness (a flat, unsourced room-turnover
constant) with a structural fix rather than a bigger lookup table. No CP Optimizer
license was available while building this either, so it falls back to the primary
CP-SAT model, with setup instructions printed at runtime. Full detail in **Appendix C**.

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

Identical to §8.1 / FORMULATION_CP.md §4.1:
$w_c = \mu_{p_c} \cdot \text{PenaltyCurve}(dd_c) + 1.2 \cdot \max_{c'} dd_{c'}$, evaluated
once in `src/model/penalty.py` and shared by every solver (MIP, CP, Hexaly, greedy) — there is
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

For this codebase's defaults ($\alpha=2$, $n_{\text{days}}=5$, and
$\max_c dd_c \approx 270$ for a priority-1 case at its policy's maximum wait),
$\text{margin}_{\min} \approx 1.037$ — so, **contrary to the intuition that prompted this audit, 1.2 was already the
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

*The question this section answers: the default multipliers are
$\mu = (1, 4.5, 18, 90)$ — how would I actually calibrate these against a real hospital,
would I run a sensitivity/Pareto sweep, and what about equity across specialties?*

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

---

## Appendix C — A Second CP Strategy: IBM ILOG CP Optimizer

§12 introduces this as an optional, license-gated extension
(`src/solvers/cp_optimizer_solver.py`). This appendix documents it at the same level of
detail as Appendix A's MIP — not because it is a required deliverable, but because the
brief explicitly rewards justified choices over an elegant model with no rationale, and
"is there only one way to do CP here" is a fair question to have a real answer to.

### C.1 Why a Second CP Engine, Not Just a Second MILP

The primary model (FORMULATION_CP.md) already demonstrates CP-over-MIP. This appendix
demonstrates something narrower and, for this project, more useful: that *within* the CP
paradigm, the choice of modelling primitive still matters, and a second engine —
IBM ILOG CP Optimizer, via its `docplex.cp` API — exposes primitives CP-SAT does not
that directly answer a weakness this review already found and flagged, rather than
left unaddressed. Appendix B.1 audited $t_c^{\text{clean}}$ (room turnover) and found it
the least-grounded constant in the whole model: a flat 20 minutes for every case, when
real OR turnover is reported at 15–60 minutes depending on the procedure. B.5 proposed
bucketing it by $t_c^{\text{op}}$ as the next concrete fix — a real improvement, but
still a property of the case alone. CP Optimizer's `sequence_var` plus a *transition
matrix* on `no_overlap` makes turnover a property of the **ordered pair** of adjacent
cases instead — strictly more expressive, and the more accurate mechanism for what
actually drives OR turnover time in practice (a full equipment/instrument changeover
between two different specialties' cases vs. a quick re-prep between two cases of the
same kind).

### C.2 The Structural Difference, With a Worked Number

CP-SAT bakes cleaning into the room interval's own length: every candidate's room
interval has size $t_c^{\text{tot}} = t_c^{\text{op}} + t_c^{\text{clean}}$, so turnover
is charged identically regardless of what comes next (FORMULATION_CP.md §3). CP
Optimizer's version below instead sizes the interval at $t_c^{\text{op}}$ alone and
charges turnover as a transition cost between whichever two cases end up adjacent in a
room's chosen sequence:

| | Case A → Case B, same service | Case A → Case C, different service |
|---|---|---|
| CP-SAT (flat $t_c^{\text{clean}}=20$) | 20 min charged either way | 20 min charged either way |
| CP Optimizer (transition matrix) | 15 min (same-setup re-prep) | 35 min (full changeover) |

Neither number is "more correct" in the abstract — both are instance-configurable
defaults (`PlanningInstance.same_service_turnover_min` /
`cross_service_turnover_min`, `src/model/types.py`), not literature constants, exactly
like every other policy knob audited in Appendix B. What changed is **expressiveness**:
CP-SAT's formulation has no variable a turnover rule could even attach two cases'
identities to; CP Optimizer's does. This is the same kind of argument §3 makes for CP
over MIP, one level down — a more expressive primitive, not a bigger table of the same
kind of number.

**An honest limit of this demonstration, found while validating it (C.5):** C4's
room-service roster assigns each room to exactly one service per day in both
`demo_instance()` and `medium_instance()` — so within any one room-day, every candidate
case is automatically the same service, and the matrix's **cross-service** branch (35
min) is mathematically unreachable on either shipped instance, even though it is
correctly implemented and would engage the moment a room is rostered to more than one
service in a day. What the demo *does* demonstrate, on real data, is narrower but still
real: same-service turnover at 15 minutes, strictly below CP-SAT's flat 20-minute
buffer for every case regardless of service — see C.5 for the validated numbers.

### C.3 Sets, Variables, and Objective

Unchanged from FORMULATION_CP.md §2: $C, D, R, H, E$ and the shared parameters,
including the **same** $w_c$ from `penalty.py` (no separate priority factor — the
identical fix as FORMULATION_CP.md §6.1). One addition, used only here:

| Symbol | Description |
|---|---|
| $\sigma(c)$ | Surgical service of case $c$ — indexes the turnover transition matrix |
| $\tau_{\sigma\sigma'}$ | Minimum room-turnover minutes between a service-$\sigma$ case ending and a service-$\sigma'$ case starting next in the same room |

Decision variables, per case $c$:

$$
\text{task}_c \quad \text{— an optional interval (mandatory iff } p_c = 4 \text{)}
$$

and, for every $(d,r)$ surviving the same C4–C6 pre-filter as the primary model:

$$
\text{alt}_{cdr} \quad \text{— an optional interval of size } t_c^{\text{op}}
$$

with `task_c` and `alt_cdr` tied together by docplex.cp's `alternative` global
constraint: "exactly one of these candidate slots realizes this task" — the same
case-to-slot decision CP-SAT encodes as a flat list of boolean `presence` variables plus
a linear `sum(...) + u == 1` (FORMULATION_CP.md §3). $\text{alt}_{cdr}$ is sized
$t_c^{\text{op}}$ only (operative time, no cleaning baked in) — because turnover now
lives in the transition between sequence neighbours (C.4 below), this one interval
already represents the surgeon's own busy window too: unlike the primary model, which
needs two different interval sizes per candidate ($t_c^{\text{tot}}$ for the room,
$t_c^{\text{op}}$ for the surgeon, FORMULATION_CP.md §3) precisely because it bakes
cleaning into the room interval's length, this model needs only one.

Objective — identical three-term tardiness shape as FORMULATION_CP.md §4, expressed
over `presence_of(alt_cdr)` and `presence_of(task_c)` (docplex.cp's presence indicator)
instead of $\text{pr}_{cdr}$ and $u_c$, plus the same Term-4 bed-overflow addition
(C.4 below).

### C.4 Constraints

**C1** (one occurrence per patient/week) and **C2** (priority-4 lock-in) — same logic as
FORMULATION_CP.md, expressed over `presence_of(task_c)`: a sum-at-most-1 across a
patient's cases for C1, and `task_c` built non-optional (always present) for priority-4
cases for C2, so `alternative` forces exactly one of its day-1 alternatives to be chosen.

**C7 — room turnover, sequence-dependent (the central difference, C.2 above):** for
every $(d,r)$, a `sequence_var` $\Sigma_{dr}$ over that room's candidate intervals,
typed by each case's service $\sigma(c)$:

$$
\Sigma_{dr} \quad \text{— a sequence variable over } \{\text{alt}_{cdr}\}_c, \text{ typed by } \sigma(c) \qquad \forall d \in D,\ r \in R
$$

with `no_overlap` enforced on $\Sigma_{dr}$ against the transition matrix $\tau$ from
C.3's table — the actual call is `model.no_overlap(seq_dr, tau)` in
`cp_optimizer_solver.py`.

**C8 — surgeon, exact non-overlap, no transition cost** (a surgeon doesn't need
"cleaning time" between cases the way a room does): the same `sequence_var` idiom, this
time per surgeon/day, with `no_overlap` applied **without** a transition matrix:

$$
\Sigma_{hd} \quad \text{— a sequence variable over } \{\text{alt}_{cdr}\}_{c:\ \text{surgeon}(c)=h} \qquad \forall h \in H,\ d \in D
$$

— plus the same daily/weekly operative-minutes sums as the primary model (**C8**
secondary cap, **C9**), since `no_overlap` alone bounds concurrency, not total hours.

**C10 — shared equipment, additive cumulative:** one `pulse` term per candidate, summed
and capped, instead of a single global cumulative-constraint call:

$$
\sum_{c:\ u_{ce}=1} \text{pulse}(\text{alt}_{cdr}, 1) \ \le\ \kappa_{ed} \qquad \forall e \in E,\ d \in D
$$

built by summing one `pulse` term per candidate instead of one `AddCumulative` call —
mechanically equivalent on this instance, but the additive form is what would let a real
deployment add, say, a non-elective baseline usage term to the same expression later
without changing this constraint's shape (not implemented here — out of scope, same
discipline as B.5).

**C11 — downstream recovery/ICU beds**, same channel-to-day-of-surgery idea and the same
overflow-penalty mechanism as FORMULATION_CP.md §5.11 (no silent horizon-boundary
approximation), built from a day-granularity `bed_c` interval tied to $\text{task}_c$'s
presence and summed via `pulse` per bed pool, exactly mirroring C10's additive pattern.

### C.5 Status and Honesty

Unlike Hexaly (§12), a CP Optimizer engine *is* available in the environment this
project was built in — IBM CPLEX Optimization Studio Community Edition, with
`docplex` 2.32.264 — so this backend has actually been run and validated, not just
written against documented API and left unverified.

**What was checked, on `demo_instance()` (20 cases):** `CPOptimizerSolver(time_limit_sec=60)`
returns `Optimal`, objective **155.0** (identical to CP-SAT's, see RESULTS.md — expected,
since the objective depends only on which case lands on which day, not on intra-day
timing, and both models schedule all 20 cases on the same days here), 20/20 scheduled, 0
unscheduled. Verified directly, not just trusted: every room-turnover gap in the
returned schedule equals exactly `same_service_turnover_min` (15) between consecutive
same-service cases in a room — never less, confirming the transition matrix is actually
binding, not a no-op; no room or surgeon interval overlaps; the shared C-arm's
concurrent usage never exceeds capacity 1; both ICU-bed cases land on a feasible day.
`tests/test_model.py::test_cp_optimizer_solver` encodes this check
(`_assert_cp_optimizer_constraints`) and passes.

**A second, honest data point — 200-case instance, 120 seconds each (not the
30-minute/1%-gap budget RESULTS.md uses for the main CP-SAT-vs-MILP comparison, so
these numbers are not directly comparable to that table):**

| Solver | Status | Objective | Own Gap | Scheduled | Time |
|---|---|---|---|---|---|
| CP-SAT | Feasible | **71,242.0** | **7.9%** | 134/200 | 120.9s |
| CP Optimizer | Feasible | 74,751.0 | 75.6% | **143/200** | 121.0s |

A genuinely mixed, slightly unflattering result, reported as measured rather than
smoothed over: CP Optimizer schedules **9 more cases** in the same wall-clock time, but
lands on a **worse** (higher) objective with a **far** looser gap. The likely
explanation is search engineering, not modelling: CP-SAT's default parallel portfolio
(FORMULATION.md §3) is a mature, heavily-tuned search for exactly this constraint-
satisfaction family; CP Optimizer's automatic search was used here with no custom
search phase, no warm start, and no parameter tuning — the same gap this project's own
greedy-warm-start mechanism closes for CP-SAT (`src/solvers/warm_start.py`) was never
applied here. **This is the honest scope boundary of Appendix C**: it demonstrates a
more expressive *modelling primitive* (C.1–C.4), not a better-tuned *solver*, and at
this instance size, search tuning visibly matters more than the turnover model's extra
expressiveness. Closing that gap (custom search phases, a warm start analogous to
`warm_start.py`, a longer/properly-budgeted run) is exactly the kind of follow-up effort
deliberately not spent here, consistent with B.5's discipline of not gold-plating a
secondary, optional backend past what the brief's "small demo" framing asks for.

If `docplex` is missing or no engine/license is reachable on a *different* machine
running this code, it falls back to the primary CP-SAT model with a printed setup
message — that fallback path is tested directly too
(`test_cp_optimizer_fallback_method_is_correct`), independent of whether the real
engine happens to be present.

### C.6 What This Does and Doesn't Prove

It does not re-argue CP over MIP — FORMULATION.md §3 and RESULTS.md already do that,
empirically, on two instance sizes. What it adds is narrower: that the specific
modelling choices inside a CP formulation (one global constraint vs. a derived linear
sum, a transition matrix vs. a flat buffer, additive cumulative vs. a single global
call) are themselves engineering decisions with real trade-offs, not just two syntaxes
for the same idea — and that at least one of them (sequence-dependent turnover) directly
closes a gap this project's own parameter audit (Appendix B.1) already found and named,
rather than introducing a new, unrelated feature for its own sake. C.5's medium-instance
result is the other half of this honest scoping: a richer model is not a substitute for
a tuned search, and this appendix does not claim otherwise.

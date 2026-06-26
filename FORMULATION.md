# Elective Surgery Scheduling — MILP Formulation

**Author:** Operations Research Scientist  
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
   weekend staffing cuts) would need a per-day-segmented version — flagged here rather
   than silently assumed away.
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

## 7. Decision Variables

For every $(c,d,r)$ that survives the eligibility pre-filter — room-service roster
(C4), ambulatory-only (C5), pediatric block (C6), surgeon availability — the model
creates one interval:

$$
\text{pr}_{cdr} \in \{0,1\}
\qquad
\text{start}_{cdr} \in [0, k_{dr}]
\qquad
\text{end}_{cdr} \in [0, k_{dr}]
$$

$$
\text{iv}_{cdr} = \texttt{NewOptionalIntervalVar}\big(\text{start}_{cdr},\ t_c^{\text{tot}},\ \text{end}_{cdr},\ \text{pr}_{cdr}\big)
$$

$\text{pr}_{cdr}=1$ means case $c$ is scheduled on day $d$ in room $r$, starting at
$\text{start}_{cdr}$. $\text{iv}_{cdr}$ is *present* — i.e. it actually constrains
whatever room/surgeon/equipment it touches — iff $\text{pr}_{cdr}=1$; this is what lets
one family of global constraints (`NoOverlap`, `Cumulative`) reason correctly over every
candidate slot a case *could* occupy, without the solver first having to decide which
candidates are real.

> **Pre-filtering.** In code, candidates are only generated for triples that pass the
> room-service roster, ambulatory-only, pediatric-block and surgeon-availability checks
> — the main variable-count reduction mechanism (see the `candidates` list comprehension
> in `src/solvers/cp_sat_interval_solver.py`).

For every non-priority-4 case, an unscheduled indicator:
$$
u_c \in \{0,1\}, \qquad \sum_{d,r} \text{pr}_{cdr} + u_c = 1
$$

## 8. Objective Function

$$
\min \quad
\underbrace{\sum_{c:\,dd_c \ge 0}\ \sum_{d \in D_c, r \in R} \big[dd_c + d\big]\, \text{pr}_{cdr}}_{\text{Term 1 — on-time cases, prefer earlier days}}
\ +\
\underbrace{\sum_{c:\,dd_c < 0}\ \sum_{d \in D_c, r \in R} \big[dd_c + \alpha d\big]\, \text{pr}_{cdr}}_{\text{Term 2 — overdue cases, urgency multiplier }\alpha}
\ +\
\underbrace{\sum_{c:\,p_c \ne 4} p_c\, w_c\, u_c}_{\text{Term 3 — non-scheduling penalty}}
$$

(here $d \in \{1,\dots,5\}$ is the numeric index of the day, i.e. $d=1$ for the first
day of the horizon.)

**Reading it:** Term 1 prefers to schedule cases with little slack left sooner rather
than later; Term 2 does the same for already-overdue cases, but multiplies the day
coefficient by $\alpha>1$, so deferring an overdue case to later in the week is
disproportionately expensive. Term 3's weight $w_c$ (next) is calibrated to always
exceed any Term-1/2 coefficient, so the model leaves a case unscheduled only when no
feasible slot exists — never as a cheaper alternative to scheduling it late.

CP-SAT requires integer objective coefficients; each coefficient is rounded to the
nearest integer at model-build time (negligible at this objective's scale — coefficients
are in the tens-to-thousands range, so rounding error per term is $<1$).

### 8.1 Non-scheduling penalty $w_c$

$$
w_c = \text{PenaltyCurve}\big(dd_c \cdot \mu_{p_c}\big) + 1.2 \cdot \max_{c' \in C} dd_{c'}
$$

`PenaltyCurve` is a piecewise-increasing function of (priority-normalised) days to
deadline — sharp escalation as the deadline approaches and is breached (see
`src/model/penalty.py`; shape adapted from Marques & Captivo, 2015, §5.1). The
displacement term `1.2 * max(dd)` guarantees $w_c$ dominates every Term-1/2 coefficient,
which is what makes Term 3 a true last resort.

## 9. Constraints

**C1 — at most one scheduled occurrence per patient per week**
$$
\sum_{\substack{c \in C:\\ \text{patient}(c)=n}} \sum_{d,r} \text{pr}_{cdr} \le 1 \qquad \forall n
$$

**C2 — priority-4 cases must run on day 1**
$$
\sum_{r \in R} \text{pr}_{c,1,r} = 1 \qquad \forall c \in C : p_c = 4
$$
(every other candidate slot for that case is forced to 0.)

**C3 — every other case is scheduled exactly once, or penalised** — see §7,
$\sum_{d,r}\text{pr}_{cdr} + u_c = 1$.

**C4 — room-service roster** (enforced by the pre-filter, not a separate row): a
candidate $(c,d,r)$ is never generated when $a_{dr}^{\,\text{service}(c)} = 0$.

**C5 — ambulatory-only rooms admit only day-case scopes** (pre-filter).

**C6 — pediatric-block rule** (pre-filter): on day $d^\dagger$, service $s^\dagger$'s
rooms admit no case with patient age $> i^\dagger$.

**C7 — room capacity, via exact non-overlap**
$$
\texttt{AddNoOverlap}\big(\{\text{iv}_{cdr} : d, r \text{ fixed}\}\big) \qquad \forall d \in D,\, r \in R
$$
A room can run only one case at a time; cases are packed without collision, not merely
within a duration budget.

**C8 — surgeon: exact non-overlap, plus a daily time cap**
$$
\texttt{AddNoOverlap}\big(\{\text{iv}_{cdr} : \text{surgeon}(c)=h,\ d \text{ fixed}\}\big) \qquad \forall h \in H,\, d \in D
$$
$$
\sum_{c:\,\text{surgeon}(c)=h} \sum_r t_c^{\text{op}}\, \text{pr}_{cdr} \le k_{hd} \qquad \forall h, d
$$
A surgeon cannot be in two rooms at once (the `NoOverlap` term), and cannot exceed a
daily operating-time budget even across non-overlapping cases (the sum, kept alongside
it — `NoOverlap` alone doesn't bound total hours worked, only concurrency).

**C9 — surgeon weekly time limit**
$$
\sum_{c:\,\text{surgeon}(c)=h} \sum_{d,r} t_c^{\text{op}}\, \text{pr}_{cdr} \le k_h \qquad \forall h \in H
$$

**C10 — shared equipment, exact concurrency**
$$
\texttt{AddCumulative}\big(\{\text{iv}_{cdr} : u_{c,e}=1,\ d \text{ fixed}\},\ \text{demands}=1,\ \text{capacity}=\kappa_{ed}\big) \qquad \forall e \in E,\, d \in D
$$
This checks literal time overlap, not merely how many equipment-$e$ cases land on a
day — the constraint family argued for in §3, and the one RESULTS.md shows making a
measurable difference to the achievable objective.

**C11 — downstream recovery/ICU beds**

For each case $c$ with $\rho(c) \ne$ "none", channel whichever $(d,r)$ slot is chosen
into a day index, then treat the stay as its own interval:
$$
\text{dayof}_c = d_{\text{idx}} \quad \text{(via } \texttt{OnlyEnforceIf} \text{, whenever } \text{pr}_{cdr}=1\text{)}
$$
$$
\text{bed}_c = \texttt{NewOptionalIntervalVar}\big(\text{dayof}_c,\ \text{los}_c,\ \text{dayof}_c+\text{los}_c,\ \text{is\_scheduled}_c\big)
$$
$$
\texttt{AddCumulative}\big(\{\text{bed}_c : \rho(c)=\rho\},\ \text{demands}=1,\ \text{capacity}=\beta_\rho\big) \qquad \forall \rho
$$
This is the constraint the case prompt names directly ("downstream constraints such as
recovery/ICU or ward beds") and the clearest illustration of why an interval
representation was chosen over a day-bucket one: a day-bucket model has no correct way
to express a multi-day stay that *starts* on the day of surgery, so this constraint
could not have been added correctly to a coarser model without first becoming
interval-based.

All of C7–C11 are declared over the *same* interval $\text{iv}_{cdr}$ per candidate
slot — a case's interval is shared across every global constraint that resource
touches, so "this case's start is pinned by its room" propagates into "...which also
constrains its surgeon" without an explicit channeling constraint between the two; it
falls out of using one interval per slot rather than separate variables per resource.

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
detail is in that file's docstring, not duplicated here.

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

# Interval-Based Constraint Programming Formulation (CP-SAT)

This is the **primary, production model** for this case (FORMULATION.md §3 argues why
CP, not MIP, is the right paradigm here). This document carries the full math —
decision variables, objective, and every constraint C1–C11 — implemented in
[`src/solvers/cp_sat_interval_solver.py`](src/solvers/cp_sat_interval_solver.py), with
the same C-numbering as the code comments, so the two can be read side by side.

It assumes FORMULATION.md §1–§6 (problem statement, evidence, why-CP argument,
assumptions, sets, parameters) as context and does not repeat them. §6 of this document
is new: a rigorous, numeric walk-through of two corrections applied to this model during
review, written so a reader can verify the bug, the fix, and the fix's effect — not just
take "it's fixed" on faith.

---

## 1. Relationship to FORMULATION.md

FORMULATION.md is the master document: problem framing, evidence for the priority
mechanism, the why-CP-not-MIP argument, assumptions, and the shared sets/parameters.
This document is the detailed math for the CP-SAT model specifically — the MIP's
equally-detailed math lives in FORMULATION.md Appendix A, since it is the comparison
point, not a parallel primary deliverable (FORMULATION.md §12).

## 2. Sets and Parameters

Unchanged from FORMULATION.md §5–§6: $C, D, R, H, E$ and every $t_c^{\text{op}},
t_c^{\text{clean}}, t_c^{\text{tot}}, k_{dr}, k_{hd}, k_h, p_c, dd_c, \mu_p, w_c, \alpha,
u_{ce}, \kappa_{ed}$. Three additions, used only by this model (a day-bucket MILP cannot
express them — FORMULATION.md Appendix A.4):

| Symbol | Description |
|---|---|
| $\rho(c)$ | Recovery/bed pool required by case $c$ ("none" if not applicable) |
| $\text{los}_c$ | Length of stay in that pool, in days, if $\rho(c) \ne$ "none" |
| $\beta_\rho$ | Bed count for pool $\rho$ (constant across the week, §4.5) |
| $\pi^{\text{ovf}}$ | Per-day penalty for a bed stay extending past the horizon (default 50; new in this review, §5.11) |

## 3. Decision Variables

For every $(c,d,r)$ surviving the eligibility pre-filter (room-service roster C4,
ambulatory-only C5, pediatric block C6, surgeon availability):

$$
\text{pr}_{cdr} \in \{0,1\}
\qquad
\text{start}_{cdr} \in [0, k_{dr}]
$$

**Two intervals per candidate, not one** — this is corrected from an earlier version
that used a single interval for both (§6.2):

$$
\text{end}_{cdr} \in [0, k_{dr}], \qquad
\text{iv}_{cdr} = \texttt{NewOptionalIntervalVar}\big(\text{start}_{cdr},\ t_c^{\text{tot}},\ \text{end}_{cdr},\ \text{pr}_{cdr}\big)
\quad\text{— ROOM occupancy}
$$

$$
\text{sgend}_{cdr} \in [0, k_{dr}], \qquad
\text{sgiv}_{cdr} = \texttt{NewOptionalIntervalVar}\big(\text{start}_{cdr},\ t_c^{\text{op}},\ \text{sgend}_{cdr},\ \text{pr}_{cdr}\big)
\quad\text{— SURGEON's own time}
$$

Both intervals share the same `start` (the case begins at one clock time) but end at
different points: $\text{iv}_{cdr}$ runs through the post-operative cleaning buffer
($t_c^{\text{tot}} = t_c^{\text{op}}+t_c^{\text{clean}}$); $\text{sgiv}_{cdr}$ ends when
the surgeon's own involvement does ($t_c^{\text{op}}$ alone). $\text{iv}_{cdr}$ feeds C7,
C10, C11; $\text{sgiv}_{cdr}$ feeds C8 only.

Both intervals are *present* — i.e. actually constrain whatever resource they touch —
iff $\text{pr}_{cdr}=1$; this is what lets one family of global constraints
(`NoOverlap`, `Cumulative`) reason correctly over every candidate slot a case *could*
occupy, without the solver first having to decide which candidates are real.

> **Pre-filtering.** Candidates are only generated for triples passing the C4–C6
> eligibility checks — the main variable-count reduction mechanism (the `candidates`
> list comprehension in `cp_sat_interval_solver.py`).

For every non-priority-4 case, an unscheduled indicator:
$$
u_c \in \{0,1\}, \qquad \sum_{d,r} \text{pr}_{cdr} + u_c = 1
$$

## 4. Objective Function

$$
\min \quad
\underbrace{\sum_{c:\,dd_c \ge 0}\ \sum_{d \in D_c, r \in R} \big[dd_c + d\big]\, \text{pr}_{cdr}}_{\text{Term 1 — on-time cases, prefer earlier days}}
\ +\
\underbrace{\sum_{c:\,dd_c < 0}\ \sum_{d \in D_c, r \in R} \big[dd_c + \alpha d\big]\, \text{pr}_{cdr}}_{\text{Term 2 — overdue cases, urgency multiplier }\alpha}
\ +\
\underbrace{\sum_{c:\,p_c \ne 4} w_c\, u_c}_{\text{Term 3 — non-scheduling penalty}}
\ +\
\underbrace{\pi^{\text{ovf}} \sum_{c:\,\rho(c)\ne\text{none}} \text{overflow}_c}_{\text{Term 4 — bed-horizon overflow, new (§5.11)}}
$$

Term 3 multiplies by $w_c$ **alone** — no separate $p_c$ factor. §6.1 below documents
why that matters: an earlier version of this code multiplied by $p_c$ a second time,
double-counting priority and inverting the intended ordering in some cases.

CP-SAT requires integer objective coefficients; each is rounded to the nearest integer
at model-build time (negligible at this scale — coefficients are in the tens-to-thousands
range, rounding error per term is $<1$).

### 4.1 Non-scheduling Penalty $w_c$

$$
w_c = \mu_{p_c} \cdot \text{PenaltyCurve}(dd_c) + 1.2 \cdot \max_{c' \in C} dd_{c'}
$$

**Corrected order of operations (§6.1):** the multiplier $\mu_{p_c}$ scales the curve's
**output**. An earlier version scaled the curve's **input**
($\text{PenaltyCurve}(dd_c \cdot \mu_{p_c})$) instead — mathematically different, and the
source of both bugs in §6.1. `PenaltyCurve` is the piecewise-increasing function of the
**real, unscaled** $dd_c$ in `src/model/penalty.py` (shape adapted from Marques &
Captivo, 2015, §5.1) — its breakpoints (e.g. "$dd_c \ge -30$") always mean "this many
real days overdue", for every priority, which is only true once the curve's input is
never rescaled. The displacement term ($1.2 \times \max dd$) guarantees $w_c$ dominates
every Term-1/2 coefficient — audited and confirmed sufficient in FORMULATION.md
Appendix B.3.

## 5. Constraints

### 5.1–5.3 — Case disposition (unchanged in this review)

**C1** — one scheduled occurrence per patient per week:
$\sum_{c:\text{patient}(c)=n}\sum_{d,r}\text{pr}_{cdr}\le 1\ \forall n$.

**C2** — priority-4 cases: $\sum_r \text{pr}_{c,1,r}=1$, every other candidate slot for
that case forced to 0.

**C3** — schedule-or-penalise (§3 above).

### 5.4–5.6 — Eligibility (pre-filter, unchanged)

**C4** room-service roster · **C5** ambulatory-only rooms · **C6** pediatric block — all
three eliminate infeasible $(c,d,r)$ triples before the model is built, never appearing
as an explicit constraint row.

### 5.7 — C7: Room Capacity, Exact

$$
\texttt{AddNoOverlap}\big(\{\text{iv}_{cdr} : d, r \text{ fixed}\}\big) \qquad \forall d \in D,\, r \in R
$$

Declared over the **room** interval $\text{iv}_{cdr}$ (size $t_c^{\text{tot}}$): a room
runs one case at a time, and the next case cannot start until the previous one's
cleaning buffer has elapsed.

### 5.8 — C8: Surgeon, Exact Non-Overlap on the Surgeon's OWN Interval

$$
\texttt{AddNoOverlap}\big(\{\text{sgiv}_{cdr} : \text{surgeon}(c)=h,\ d \text{ fixed}\}\big) \qquad \forall h \in H,\, d \in D
$$
$$
\sum_{c:\,\text{surgeon}(c)=h} \sum_r t_c^{\text{op}}\, \text{pr}_{cdr} \le k_{hd} \qquad \forall h, d \quad \text{(kept, unchanged)}
$$

**Corrected (§6.2): declared over $\text{sgiv}_{cdr}$ (size $t_c^{\text{op}}$), not
$\text{iv}_{cdr}$ (size $t_c^{\text{tot}}$).** A surgeon cannot operate in two rooms at
once — but *is* free to start a second case in a different room the moment their first
case's *operative* portion ends, even while the first room is still being cleaned by
nursing/support staff. Using the room's longer interval for the surgeon's own
non-overlap (an earlier version did this) forbids that real, common pattern. The linear
sum is kept alongside the corrected `NoOverlap`, because `NoOverlap` alone bounds
concurrency, not total hours worked.

### 5.9 — C9: Surgeon Weekly Limit (unchanged)

$$
\sum_{c:\,\text{surgeon}(c)=h} \sum_{d,r} t_c^{\text{op}}\, \text{pr}_{cdr} \le k_h \qquad \forall h \in H
$$

### 5.10 — C10: Shared Equipment, Exact Concurrency (unchanged)

$$
\texttt{AddCumulative}\big(\{\text{iv}_{cdr} : u_{c,e}=1,\ d \text{ fixed}\},\ \text{demands}=1,\ \text{capacity}=\kappa_{ed}\big) \qquad \forall e \in E,\, d \in D
$$

Declared over the **room** interval (equipment sits in the room for the full
$t_c^{\text{tot}}$, cleaning included — unlike the surgeon, the device doesn't leave
early). Checks literal time overlap, not a day-count — the mechanism argued for in
FORMULATION.md §3 and measured in RESULTS.md.

### 5.11 — C11: Downstream Recovery/ICU Beds, Now With an Explicit Overflow Term

For each case $c$ with $\rho(c) \ne$ "none", channel whichever $(d,r)$ slot is chosen
into a day index, then treat the stay as its own interval:

$$
\text{dayof}_c = d_{\text{idx}} \quad \text{(via } \texttt{OnlyEnforceIf}\text{, whenever } \text{pr}_{cdr}=1\text{)}
$$
$$
\text{bed}_c = \texttt{NewOptionalIntervalVar}\big(\text{dayof}_c,\ \text{los}_c,\ \text{dayof}_c+\text{los}_c,\ 1-u_c\big)
$$
$$
\texttt{AddCumulative}\big(\{\text{bed}_c : \rho(c)=\rho\},\ \text{demands}=1,\ \text{capacity}=\beta_\rho\big) \qquad \forall \rho
$$

The bed interval's presence literal is $1-u_c$ (§3's unscheduled indicator, with the
convention $u_c\equiv 0$ for priority-4 cases, which C2 always schedules) — the bed only
actually occupies capacity for a case that is actually scheduled somewhere.

This is the constraint the case prompt names directly ("downstream constraints such as
recovery/ICU or ward beds") and the clearest illustration of why an interval
representation was chosen at all — a day-bucket model has no variable representing "day
of surgery" as a *value* (FORMULATION.md Appendix A.4), so this constraint could not
have been added to a coarser model without first becoming interval-based.

**New in this review — horizon-boundary overflow, not just a documented caveat.** Bed
capacity $\beta_\rho$ is constant across the week (§4.5), but $\text{bed}_c$'s end can
exceed the modeled horizon (e.g. a 2-day stay starting Friday spills into what would be
the weekend — a regime this constant-capacity model has no separate, lower capacity
for). Rather than silently approximate or forbid that, every day of overflow is now
charged in the objective (Term 4, §4):

$$
\text{overflow}_c = \max\big(0,\ (\text{dayof}_c + \text{los}_c) - n_{\text{days}}\big),
\qquad \pi^{\text{ovf}} \cdot \text{overflow}_c \ \text{added to the objective}
$$

implemented via `model.AddMaxEquality`. $\pi^{\text{ovf}}=0$ recovers the old, silent
behaviour exactly; the default of 50 makes crossing the boundary discouraged but not
infeasible — an explicit, instance-overridable policy choice
(`PlanningInstance.weekend_bed_overflow_penalty`), not a hidden one.

### 5.12 — Why C7–C11 Share Intervals, Not Channeling Constraints

C7, C10, and C11 are declared over the **room** interval $\text{iv}_{cdr}$; C8 is
declared over the **surgeon** interval $\text{sgiv}_{cdr}$. Both share the same `start`
variable. This is what lets CP-SAT propagate "this case's start is pinned by its room's
schedule" into "...which also constrains its surgeon's schedule" (and vice versa)
without an explicit channeling constraint between the two interval families — it falls
out of sharing one `start` per candidate slot, with two different `end`s for the two
different resources that case touches.

## 6. Two Corrections Applied During Review

Both bugs below were found by tracing the objective formula and the C8 constraint
construction line by line against FORMULATION.md's stated intent, not by a failing
test — the existing test suite did not catch either one, because it checked
daily/weekly *sums* (still correct under both bugs) but never (a) reconstructed $w_c$'s
exact formula from first principles, or (b) checked surgeon non-overlap on the
surgeon's own time window specifically (only the room's). A regression test for (b) was
added (`tests/test_model.py`, `_assert_hard_constraints`); (a) is best verified by the
worked numeric example below, since it's a question of *which* feasible schedule the
optimizer should prefer, not feasibility itself.

### 6.1 Priority/Penalty-Curve Double-Counting and Monotonicity Inversion

**The bug.** The old code computed `adjusted_days = dd_c * mult` and evaluated
`PenaltyCurve(adjusted_days)` — scaling the curve's *input*. Separately, every solver's
objective computed `c.priority.value * penalties[cid]` — multiplying $w_c$ by $p_c$
again. Two independent applications of priority, compounding.

**Worked example**, using the actual breakpoints in `penalty_factor_curve` (`d≥-15→1000`;
`d≥-45→2000`; else `2000 + 20·|d+45|`):

| Case | $p_c$ ($\mu_{p_c}$) | $dd_c$ (real days) | Old: curve input | Old: curve output | Old: × $p_c$ again | New: curve($dd_c$) | New: × $\mu_{p_c}$ |
|---|---|---|---|---|---|---|---|
| A | 4 (90) | $-1$ | $-1\times90=-90$ | 2900 | **4 × 2900 = 11,600** | 1000 | **90 × 1000 = 90,000** |
| B | 1 (1) | $-90$ | $-90\times1=-90$ | 2900 | **1 × 2900 = 2,900** | 2900 | **1 × 2900 = 2,900** |
| C | 1 (1) | $-100$ | $-100\times1=-100$ | 3100 | **1 × 3100 = 3,100** | 3100 | **1 × 3100 = 3,100** |

Two distinct failures, visible side by side:

1. **Double-counting:** under the old formula, Case A's curve input was scaled by 90
   (correctly capturing "1 day overdue at the most urgent tier is as bad as 90 days
   overdue at the least urgent one" — the *intended* design) and then **also**
   multiplied by $p_c=4$ in the objective, for a final weight 4× larger than the curve
   alone already specified. There was never supposed to be a second multiplication.
2. **Monotonicity inversion:** Case B (90 days overdue) and Case C (100 days overdue,
   strictly worse) get the *same* treatment of priority under the old scheme — fine on
   its own — but Case A (1 day overdue, priority 4) ends up at 11,600, nearly **4×
   worse than Case C's 3,100, despite Case C being 100x more overdue** in absolute
   terms. That ordering is an artifact of the redundant $\times p_c$, not of the curve's
   actual, carefully-designed severity scale.

The corrected formula (right two columns) keeps Case A's penalty enormous relative to B
and C — which is *intended*: a 1-day breach at the most urgent clinical tier should
dominate a 90-or-100-day breach at the least urgent one — while removing the
*redundant* second multiplication, and keeping B vs. C properly ordered by real
overdue-ness within the same tier (3100 > 2900, consistent with "more days overdue costs
more"). The fix is in `penalty.py` (curve now evaluated on real $dd_c$, multiplier
applied once, to the output) and in every solver's objective construction (the
redundant `priority.value *` factor removed from `cp_sat_interval_solver.py`,
`milp_baseline_solver.py` ×2 call sites, `hexaly_solver.py`, `greedy_solver.py`).

### 6.2 Surgeon Interval Conflated With Room Interval

**The bug.** C8's `NoOverlap` was declared over $\text{iv}_{cdr}$ — the **room**
interval, size $t_c^{\text{tot}}$ — instead of a surgeon-specific interval. Since the
same interval object was shared by C7 (room) and C8 (surgeon), the surgeon was
implicitly held "busy" for the entire room-occupation window, including the
post-operative cleaning buffer they have no involvement in.

**Worked example.** Surgeon H operates Case X in Room 1: $t_c^{\text{op}}=70$,
$t_c^{\text{clean}}=20$, $t_c^{\text{tot}}=90$, starting at $t=0$.

- **Old model:** Case Y (also surgeon H) cannot start before $t=90$ in *any* room,
  because C8 used the same $[0,90)$ interval as C7. The 20-minute window $[70,90)$ — when
  the surgeon is free but Room 1 is being cleaned — is wrongly forbidden to Case Y.
- **New model:** $\text{sgiv}_{cdr}$ for Case X is $[0,70)$. Case Y can now start as
  early as $t=70$ in Room 2 — the surgeon scrubs into a second room while support staff
  turns over the first, a real and common OR practice this fix specifically restores.

This is a genuine expansion of the feasible region, in the same direction as
FORMULATION.md §3's central argument (exact resource modeling recovers schedules a
coarser one forbids) — applied here to the surgeon resource instead of equipment. The
fix is in `cp_sat_interval_solver.py`: a second interval (`surgeon_interval`, size
$t_c^{\text{op}}$) is created per candidate alongside the room interval, and C8's
`NoOverlap` is declared over it instead of the room interval.

## 7. Complexity and What "Production-Ready" Means Here

This model is still NP-hard (room-capacity feasibility alone reduces to bin packing,
FORMULATION.md §1) — neither correction in §6 changes that worst-case fact, and neither
was a performance fix. What they change is *correctness of the objective's preferences*
(§6.1) and *size of the feasible region* (§6.2) — both orthogonal to the
practical-solvability argument in FORMULATION.md §3 (global-constraint propagation,
CP-SAT's parallel portfolio, warm-starting). RESULTS.md reports the measured
consequence of §3's argument at two scales; this document makes the corrected mechanism
exact.

## 8. References

See FORMULATION.md §16 for the full citation list (Cardoen et al. 2010; Marques &
Captivo 2015; Denton et al. 2010; SIGIC; Perron & Furnon; Baptiste, Le Pape & Nuijten
2001; Vilím 2004; Schutt et al. 2009; Laborie 2009).

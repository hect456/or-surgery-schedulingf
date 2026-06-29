# CP-SAT Model — Variables, Objective, Constraints

This is the math behind the model this project is built around
(`src/solvers/cp_sat_interval_solver.py`). FORMULATION.md covers the problem framing,
the evidence behind the priority mechanism, and the case for CP over a bigger MILP; this
document is the part that maps directly onto code — every constraint below carries the
same C-number as the comment next to it in the solver, so the two read side by side.

## 1. Sets and parameters

Unchanged from FORMULATION.md §4: $C, D, R, H, E$ and every $t_c^{op}, t_c^{clean},
t_c^{tot}, k_{dr}, k_{hd}, k_h, p_c, dd_c, \mu_p, w_c, \alpha, u_{ce}, \kappa_{ed}$, plus
the bed-pool parameters $\rho(c), \text{los}_c, \beta_\rho, \pi^{ovf}$ — these last four
only exist in this model; a day-bucket formulation has no value that means "day of
surgery" for a multi-day stay to start counting from (FORMULATION.md, Appendix A.3).

## 2. Decision variables

For every $(c,d,r)$ that survives the eligibility filter (room-service roster C4,
ambulatory-only C5, pediatric block C6, surgeon availability):

$$\text{pr}_{cdr}\in\{0,1\} \qquad \text{start}_{cdr}\in[0,k_{dr}]$$

Two interval variables share that same start time but represent two different resources,
each with its own end:

$$
\text{end}_{cdr}\in[0,k_{dr}], \quad
\text{iv}_{cdr}=\texttt{NewOptionalIntervalVar}(\text{start}_{cdr},\,t_c^{tot},\,\text{end}_{cdr},\,\text{pr}_{cdr})
\quad\text{— ROOM occupancy}
$$

$$
\text{sgend}_{cdr}\in[0,k_{dr}], \quad
\text{sgiv}_{cdr}=\texttt{NewOptionalIntervalVar}(\text{start}_{cdr},\,t_c^{op},\,\text{sgend}_{cdr},\,\text{pr}_{cdr})
\quad\text{— SURGEON's own time}
$$

The room's interval runs through the cleaning buffer ($t_c^{tot}=t_c^{op}+t_c^{clean}$);
the surgeon's ends as soon as the operation does. They have to be two separate
variables, not one shared between C7 and C8: a surgeon is free to scrub into a different
room the moment their own case ends, even while support staff are still cleaning the
first room, and collapsing both checks onto the room's longer interval would wrongly
forbid that. Both intervals are *present* — i.e. actually constrain whatever resource
they touch — exactly when $\text{pr}_{cdr}=1$, which is what lets `NoOverlap` and
`Cumulative` reason correctly over every candidate slot a case could occupy without the
solver first deciding which candidates are real.

Candidates are only generated for $(c,d,r)$ triples that pass eligibility — the model's
main variable-count reduction, done once up front rather than filtered out by
constraints later.

For every case that isn't priority-4:

$$u_c\in\{0,1\}, \qquad \sum_{d,r}\text{pr}_{cdr} + u_c = 1$$

## 3. Objective

$$
\min \quad
\underbrace{\sum_{c:\,dd_c\ge0}\sum_{d\in D_c,r}[dd_c+d]\,\text{pr}_{cdr}}_{\text{Term 1}}
+
\underbrace{\sum_{c:\,dd_c<0}\sum_{d\in D_c,r}[dd_c+\alpha d]\,\text{pr}_{cdr}}_{\text{Term 2}}
+
\underbrace{\sum_{c:\,p_c\ne4} w_c\,u_c}_{\text{Term 3}}
+
\underbrace{\pi^{ovf}\sum_{c:\,\rho(c)\ne\text{none}}\text{overflow}_c}_{\text{Term 4}}
$$

with $w_c = \mu_{p_c}\cdot\text{PenaltyCurve}(dd_c) + 1.2\cdot\max_{c'}dd_{c'}$
(`src/model/penalty.py`) — computed once, shared by every backend, so there is exactly
one place in the codebase that decides what an unscheduled case costs. CP-SAT requires
integer objective coefficients; each term is rounded to the nearest integer at
build time, which at the coefficient magnitudes here (tens to low thousands) introduces
rounding error well under 1 per term.

## 4. Constraints

**C1 — at most one occurrence per patient per week:**
$$\sum_{c:\,\text{patient}(c)=n}\sum_{d,r}\text{pr}_{cdr} \le 1 \qquad \forall n$$

**C2 — priority-4 cases run on day 1:** $\sum_r \text{pr}_{c,1,r}=1$, every other
candidate slot for that case forced to 0.

**C3 — every other case is scheduled exactly once, or counted as unscheduled** — already
written into the $u_c$ definition in §2.

**C4–C6 — eligibility** (room-service roster, ambulatory-only rooms, the pediatric
block): resolved during candidate generation; they never appear as constraint rows
because a triple that fails them never gets a variable.

**C7 — room capacity, exact, declared over the room interval:**
$$\texttt{AddNoOverlap}\big(\{\text{iv}_{cdr} : d,r \text{ fixed}\}\big) \qquad \forall d,r$$

A room runs one case at a time, and the next case can't start until the previous one's
cleaning buffer has elapsed — that buffer is already inside $\text{iv}_{cdr}$'s length.

**C8 — surgeon, exact non-overlap on the surgeon's own interval, plus a daily cap:**
$$\texttt{AddNoOverlap}\big(\{\text{sgiv}_{cdr} : \text{surgeon}(c)=h,\,d\text{ fixed}\}\big) \qquad \forall h,d$$
$$\sum_{c:\,\text{surgeon}(c)=h}\sum_r t_c^{op}\,\text{pr}_{cdr} \le k_{hd} \qquad \forall h,d$$

Declared over $\text{sgiv}_{cdr}$ (size $t_c^{op}$), not the room's interval — see §2 for
why. The minutes cap is kept alongside the NoOverlap because non-overlap alone bounds
concurrency, not total hours worked.

**C9 — surgeon weekly time limit:**
$$\sum_{c:\,\text{surgeon}(c)=h}\sum_{d,r} t_c^{op}\,\text{pr}_{cdr} \le k_h \qquad \forall h$$

**C10 — shared equipment, exact concurrency:**
$$\texttt{AddCumulative}\big(\{\text{iv}_{cdr} : u_{ce}=1,\,d\text{ fixed}\},\,\text{demands}=1,\,\text{capacity}=\kappa_{ed}\big) \qquad \forall e,d$$

Declared over the room interval, since the equipment sits in the room for the full
$t_c^{tot}$, cleaning included — unlike the surgeon, it doesn't leave early. This checks
literal time overlap rather than a day-count, which is the constraint FORMULATION.md §3
argues for and RESULTS.md measures.

**C11 — recovery/ICU beds, with an explicit horizon-overflow term.** For each case $c$
with $\rho(c)\ne\text{none}$, channel whichever $(d,r)$ slot gets chosen into a day
index, then treat the stay as its own interval:

$$\text{dayof}_c = d_{idx} \quad\text{(via \texttt{OnlyEnforceIf}, whenever }\text{pr}_{cdr}=1\text{)}$$
$$\text{bed}_c = \texttt{NewOptionalIntervalVar}(\text{dayof}_c,\,\text{los}_c,\,\text{dayof}_c+\text{los}_c,\,1-u_c)$$
$$\texttt{AddCumulative}\big(\{\text{bed}_c:\rho(c)=\rho\},\,\text{demands}=1,\,\text{capacity}=\beta_\rho\big) \qquad \forall \rho$$

The bed interval's presence literal is $1-u_c$, so it only occupies capacity for a case
that's actually scheduled. Bed capacity $\beta_\rho$ is constant across the week, but a
stay starting late in the horizon (a 2-day stay starting Friday, say) can run past it
into what would be the weekend — a regime this constant-capacity model has no separate,
lower capacity for. Rather than silently approximate that or forbid it outright, every
day of overflow is charged in the objective:

$$\text{overflow}_c = \max\big(0,\ (\text{dayof}_c+\text{los}_c) - n_{days}\big), \qquad \pi^{ovf}\cdot\text{overflow}_c \text{ added to Term 4}$$

via `AddMaxEquality`. Setting $\pi^{ovf}=0$ recovers the simpler, silent behavior;
the default of 50 makes crossing the boundary discouraged but not infeasible — an
explicit, instance-level policy choice (`PlanningInstance.weekend_bed_overflow_penalty`).

## 5. Why C7–C11 don't need an explicit channeling constraint between room and surgeon

C7, C10, and C11 are declared over the room interval $\text{iv}_{cdr}$; C8 is declared
over the surgeon interval $\text{sgiv}_{cdr}$. Both share the same `start` variable, so
CP-SAT propagates "this case's start is pinned by its room's schedule" into "...which
also constrains its surgeon's schedule," and vice versa, without an explicit constraint
linking the two — it falls directly out of sharing one `start` per candidate slot, with
two different `end`s for the two different resources that slot touches.

## 6. Search

CP-SAT runs with its default parallel portfolio (`num_search_workers`, capped at the
machine's core count) and a relative gap target rather than a hand-written branching
strategy — see FORMULATION.md §3 for why that's the right default here rather than a
shortcut. The reported gap is always the genuine bound-vs-incumbent gap, computed even
when the status is `Optimal`: with a relative gap target set, CP-SAT's `Optimal` means
"proven within that tolerance," the same convention Gurobi uses, not necessarily a
literal 0%.

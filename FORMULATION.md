# Elective Surgery Scheduling — Problem Formulation

**Author:** Hector Bonilla

## 1. The problem, scoped down

A hospital group wants a weekly plan for its elective (planned) surgical cases: which
procedure runs in which operating room, at what time, on which day, and with which
surgeon, over a one-week horizon at a single hospital. The waiting list is always
bigger than the week's capacity, so part of the decision is which cases run this week
and which wait — and that choice has to respect clinical urgency, not just convenience.

The brief explicitly invites simplification, so here is what this model keeps and what
it leaves out, and why.

**Kept:** case-to-room-to-time assignment, surgeon availability and workload limits,
room/service rosters (a room is staffed and equipped for one specialty per day), a
shared piece of equipment (the kind of bottleneck a mobile imaging unit creates), and a
downstream bed constraint (recovery/ICU beds, which a multi-day stay actually needs an
interval to express correctly — more on that below).

**Left out, deliberately:**
- *Stochastic durations.* Every case gets one estimated duration. Real durations are
  uncertain and tend to run long, and that's a real planning risk — but it roughly
  doubles the model's complexity (two-stage stochastic programming, or a robust
  reformulation), and is the natural next step rather than something to bolt on here.
- *Same-day disruption.* This produces an offline weekly plan. Reacting to an emergency
  that walks in mid-week is a different problem — reactive rescheduling around an
  existing plan, not advance scheduling.
- *Nurses and anaesthetists as separate resources.* They're assumed to come with the
  room they're rostered to that day. In many hospitals the surgeon's calendar, not the
  support staff's, is the actual bottleneck; if that's not true at a given hospital,
  this assumption is the first thing to revisit.
- *Sequence-dependent room cleaning.* Turnover time depends on how long the case ran
  (bucketed below), but not on which two specific cases are adjacent in the room. A
  full equipment changeover between two different specialties' cases is more realistic
  still — section 8 sketches how that would work and why it's not the primary model.
- *Multi-week planning.* One week at a time; carrying an unscheduled case into next
  week's instance is a thin wrapper around this model, not a different one.

## 2. Where the priority/penalty mechanism comes from

Cases are ranked into four clinical priority tiers, each with a maximum acceptable wait,
and the objective penalizes a case the longer it sits past that deadline. This isn't
invented for this exercise — it's how several public health systems actually manage
elective waiting lists:

- Portugal's SIGIC system (Portaria n.º 45/2008) sets four priority tiers with maximum
  waits of 270/60/15/3 days. An audit of one hospital's 2016 waiting list found 16% of
  roughly 7,400 patients had already breached their tier's deadline, by an average of
  147 days (Marques & Captivo, 2015) — breach rates are tracked because they're the
  thing the planner actually gets judged on, not a side metric.
- The UK NHS's Referral-to-Treatment targets and several Canadian provincial wait-time
  benchmarks use the same shape: tiered maximum waits, tracked breach rates. A single
  FIFO queue doesn't reflect clinical risk, and "shortest job first" doesn't either.
- Cardoen, Demeulemeester & Beliën's (2010) literature review treats case-to-day
  assignment under a priority/deadline structure as a distinct, well-studied
  sub-problem ("advance scheduling") — the scope this model targets, extended with
  exact intra-day timing (§3).

Every number attached to this mechanism — maximum wait per tier, the priority
multipliers, the penalty curve — is a field on `PlanningInstance`
(`src/model/types.py`), not a constant buried in solver code. A hospital adopting this
plugs in its own policy without touching the model.

## 3. Why constraint programming, not a bigger MILP

This is the central modeling decision in the project, so it's worth arguing rather than
asserting.

The problem is disjunctive resource-constrained scheduling: cases competing for rooms,
surgeons, and a shared piece of equipment, each of which can hold exactly one thing at a
time (or, for the equipment, a small fixed number of things). That's precisely the
structure CP's global constraints — `NoOverlap`, `Cumulative` — exist for, and it's
exactly the structure a linear capacity-sum constraint gets wrong in one specific way:

A constraint like "total minutes of equipment use today ≤ capacity" certifies that a set
of cases' durations *fit* inside the day. It does not certify they can be placed
*without colliding*. For a single, non-shared room those two statements happen to
coincide — any set of durations that fits a day can always be laid out sequentially. They
stop coinciding the moment a resource is shared across more than one room, which is
exactly the shape of this project's C-arm: a sum can forbid two genuinely
non-overlapping uses just because they land on the same day, while in other configurations
it can be too permissive in the other direction. This isn't a theoretical nuance —
RESULTS.md shows the demo instance's day-bucket equipment cap forbidding a schedule
that's perfectly legal once you check actual clock times.

The textbook MILP alternative — a continuous-time disjunctive formulation with one
binary "A-before-B" variable per potentially-conflicting pair of cases, tied together
with big-M constraints — works, but has two costs that are well documented (Baptiste, Le
Pape & Nuijten, *Constraint-Based Scheduling*, 2001): the variable count grows
quadratically in the number of conflicting pairs, and the big-M constants weaken the LP
relaxation as that count grows, so branch-and-bound spends real time re-discovering
structure a propagation-based method gets for free. It also can't express a resource
with capacity greater than one (a 2-bed pool, say) without yet more pairwise variables.

`NoOverlap` and `Cumulative` are global constraints with their own specialized,
polynomial-time propagation — `NoOverlap` via an O(n log n) sweep (Vilím, 2004),
`Cumulative` via timetabling/edge-finding (Schutt, Feydy, Stuckey & Wallace, 2009). They
prune the search directly from the problem's time/resource structure instead of making
the solver rediscover it pair by pair through branching. That's the actual mechanism
behind "CP scales better here" — not a vague claim that one solver is smarter than
another.

**Why CP-SAT specifically.** It runs a parallel portfolio of search strategies — several
complete-search workers plus large-neighborhood-search workers improving an incumbent,
sharing learned information through a common core (Perron & Furnon, Google OR-Tools
documentation). The implementation doesn't hand-roll a branching strategy on top of
this; OR-Tools' own guidance is that the default portfolio beats a hand-tuned single
strategy unless you have structural insight the model isn't already exposing through
its `NoOverlap`/`Cumulative` calls, and this project doesn't.

A day-bucket MILP was also built (`src/solvers/milp_baseline_solver.py`) — not as a
second deliverable, but as the empirical check on the argument above: same sets, same
objective, same priority/eligibility constraints, but room and equipment capacity
expressed as linear sums instead of exact non-overlap. RESULTS.md reports the
head-to-head run; the result is what the argument predicts.

## 4. Sets and parameters

| Symbol | Meaning |
|---|---|
| $c \in C$ | Surgical cases — one entry per patient-procedure pair on the waiting list |
| $d \in D$ | Planning days, $D = \{1,\dots,5\}$, one work week |
| $r \in R$ | Operating rooms |
| $h \in H$ | Surgeons |
| $e \in E$ | Shared equipment types (e.g. a mobile imaging unit) |
| $D_c \subseteq D$ | Days case $c$ may run on — $\{1\}$ for must-run-today cases, all of $D$ otherwise |

| Parameter | Meaning |
|---|---|
| $t_c^{op}$ | Operative duration of case $c$ (minutes) |
| $t_c^{clean}$ | Room turnover after case $c$ — set from $t_c^{op}$ (§7), not a flat constant |
| $t_c^{tot} = t_c^{op} + t_c^{clean}$ | Total room-occupation time |
| $k_{dr}$ | Opening minutes of room $r$ on day $d$ |
| $k_{hd}$ | Surgeon $h$'s daily operative-time limit on day $d$ |
| $k_h$ | Surgeon $h$'s weekly operative-time limit |
| $p_c \in \{1,2,3,4\}$ | Clinical priority of $c$ — 4 means "must run today" |
| $\text{wl}_c$ | Days $c$ has already waited as of the planning date |
| $\text{wl}^{max}_p$ | Maximum acceptable wait for priority $p$ (default 270 / 60 / 15 / 3 days) |
| $dd_c = \text{wl}^{max}_{p_c} - \text{wl}_c$ | Slack to deadline (negative = already overdue) |
| $\mu_p$ | Priority multiplier (default 1 / 4.5 / 18 / 90, priority-1-equivalent) |
| $w_c$ | Non-scheduling penalty for $c$ (§6) |
| $\alpha > 1$ | Urgency multiplier applied to overdue cases (default 2.0) |
| $u_{ce} \in \{0,1\}$ | 1 if case $c$ needs equipment $e$ |
| $\kappa_{ed}$ | Capacity of equipment $e$ on day $d$ |
| $\rho(c)$ | Recovery/bed pool case $c$ needs ("none" if not applicable) |
| $\text{los}_c$ | Length of stay in that pool, in days |
| $\beta_\rho$ | Bed count for pool $\rho$ (constant across the week — §7) |
| $\pi^{ovf}$ | Per-day penalty for a bed stay crossing the horizon boundary |

A room is also tied to one service per day (its roster), may be ambulatory-only, and may
fall under an optional pediatric-block rule restricting it to patients under some age on
a given day. These are eligibility predicates, not extra variables — see §8.

## 5. Decision variables

For every $(c, d, r)$ that survives eligibility filtering (right service, right scope,
not blocked by the pediatric rule, surgeon available that day):

$$\text{pr}_{cdr} \in \{0,1\} \qquad \text{start}_{cdr} \in [0, k_{dr}]$$

a presence flag and a start time, plus an unscheduled flag for every case that isn't
priority-4:

$$u_c \in \{0,1\}, \qquad \sum_{d,r} \text{pr}_{cdr} + u_c = 1$$

CP-SAT additionally turns each candidate slot into two interval variables of different
sizes — one sized $t_c^{tot}$ for room occupancy, one sized $t_c^{op}$ for the surgeon's
own time — because a room needs to stay blocked through cleaning while the surgeon is
free as soon as the operation ends. FORMULATION_CP.md §3 has the exact CP-SAT objects;
this section states the model independently of how any one solver represents it.

## 6. Objective

$$
\min \quad
\underbrace{\sum_{c:\,dd_c \ge 0}\sum_{d \in D_c,\,r} [dd_c + d]\,\text{pr}_{cdr}}_{\text{on-time cases, prefer earlier days}}
\;+\;
\underbrace{\sum_{c:\,dd_c < 0}\sum_{d \in D_c,\,r} [dd_c + \alpha d]\,\text{pr}_{cdr}}_{\text{overdue cases, urgency-weighted}}
\;+\;
\underbrace{\sum_{c:\,p_c \ne 4} w_c\,u_c}_{\text{non-scheduling penalty}}
\;+\;
\underbrace{\pi^{ovf}\sum_{c:\,\rho(c)\ne\text{none}} \text{overflow}_c}_{\text{bed-overflow penalty}}
$$

The first two terms reward scheduling a case early within the week, with overdue cases
getting their day coefficient scaled by $\alpha$ so the model front-loads them. The
third term is what actually decides who gets left off this week's list:

$$w_c = \mu_{p_c}\cdot\text{PenaltyCurve}(dd_c) + 1.2\cdot\max_{c'\in C} dd_{c'}$$

`PenaltyCurve` (`src/model/penalty.py`) is flat while a case still has slack, then
escalates sharply once it crosses its deadline and keeps climbing the longer it stays
overdue — the shape several of the systems cited in §2 use to make breaches expensive
rather than just "less preferred." $\mu_{p_c}$ scales that curve's output once, by
priority tier. The $1.2 \times \max dd_{c'}$ term is a displacement large enough that
$w_c$ always exceeds any Term-1/2 coefficient a scheduled case could accrue (§9 derives
the exact margin needed and shows 1.2 clears it) — so the model only ever drops a case
when there genuinely isn't room for it, never as a cheap way to dodge a tardiness charge.

The fourth term only applies to cases needing a recovery/ICU bed and is explained
alongside the constraint it pairs with, C11, in FORMULATION_CP.md §5.

## 7. Constraints, summarized

Full math for each is in FORMULATION_CP.md §5, with the same numbering used in the
solver's code comments.

- **C1.** At most one scheduled occurrence per patient this week.
- **C2.** Priority-4 cases must run on day 1 — by the time a case is flagged this
  urgent, "later this week" isn't a real option.
- **C3.** Every other case is either scheduled exactly once or counted as unscheduled.
- **C4–C6.** Eligibility: room-service roster, ambulatory-only rooms, the optional
  pediatric block. These are pre-filters on which $(c,d,r)$ triples even get a variable,
  not constraint rows.
- **C7.** A room runs one case at a time — exact non-overlap, not a capacity sum.
- **C8.** A surgeon is in one room at a time, on their own time window (not the room's
  cleaning buffer) — plus a daily-minutes cap, since non-overlap alone bounds
  concurrency, not total hours.
- **C9.** Surgeon weekly time limit.
- **C10.** Shared equipment capacity, checked against actual time overlap rather than a
  daily headcount — the constraint family §3's argument is built on.
- **C11.** Recovery/ICU bed capacity. A bed stay starts on the day of surgery and runs
  for `los_c` days; this needs a real notion of "day of surgery" to even state, which is
  the concrete reason this model is interval-based at all rather than a day-bucket sum.

Room turnover after each case is set from the case's own duration (§4.2), and the
default surgeon limits (240 min/day, 960 min/week in the demo instance) correspond to a
standard half-day theatre block, with the weekly figure leaving most of a fifth day for
clinics, ward rounds, and on-call duties — a real structural pattern in how surgical job
plans are usually built, not a single cited number.

## 8. Two carve-outs worth calling out

**Room-service roster (C4).** In practice an OR is set up and staffed for one specialty
at a time, not shared minute-by-minute across services — this is captured as a
room/day → service assignment, checked before a case is even offered that room.

**Pediatric block.** A configurable rule restricts a given service's rooms on a given
day to patients under some age. Hospitals accumulate rules like this constantly, and the
point of including one is that it costs nothing structurally — it's one more eligibility
predicate evaluated during candidate generation, not a new variable family or a special
case in the objective.

## 9. Why the displacement margin is 1.2, not something smaller

Term 3 needs to dominate Terms 1–2 for every case, or the model could prefer dropping a
schedulable case just to avoid a tardiness charge. The largest Term-1/2 coefficient any
case can reach is bounded by $\max_c dd_c + \alpha \cdot n_{days}$ (a maximally-slack
case, evaluated on the last day, in the overdue branch), so the minimum safe margin is

$$\text{margin}_{\min} = 1 + \frac{\alpha \cdot n_{days}}{\max_c dd_c}$$

With this project's defaults ($\alpha=2$, $n_{days}=5$, $\max_c dd_c \approx 270$ for a
priority-1 case at its policy's maximum wait), $\text{margin}_{\min} \approx 1.037$ — so
1.2 already clears it with room to spare, which matters because $\max_c dd_c$ shrinks on
an instance with only short-horizon, high-priority cases, and a fixed margin needs to
stay safe across that range, not just on this one instance.

$\alpha$ itself appears nowhere except inside Term 2's day coefficient, so it can only
change *which day within the week* an already-scheduled overdue case lands on — it has
no path to changing *how many* cases get scheduled or *which* ones, since that's governed
by capacity and by Term 3's relative size, neither of which involves $\alpha$. A hospital
tuning $\alpha$ is deciding how hard to front-load overdue cases earlier in the week, not
how many overdue cases get served — that second question is what $\mu_p$ controls.

On calibrating $\mu_p$ in practice: there's no published "correct" multiplier vector,
because it encodes a hospital's own risk tolerance for breaching each tier, not an
empirical fact. The practical approach is structured elicitation with service chiefs,
anchored on concrete trade-offs ("a priority-2 patient 30 days over target versus a
priority-1 patient 200 days over theirs — which is worse, by roughly what factor?")
rather than asking for multiplier values directly, since clinicians reason fluently in
scenarios and rarely in objective-function coefficients. One thing worth flagging before
that conversation: because $\mu_p$ is keyed to priority *tier*, not to overdue severity
directly, raising it uniformly protects high-tier cases generally, not specifically the
most-overdue ones — if one specialty's case mix happens to skew toward lower tiers and
longer overdue stretches at the same time, a uniform increase in $\mu_p$ does nothing for
it. The right fix for that, if it shows up in practice, is a per-service tracked target or
a fairness constraint layered on top, not a bigger global multiplier.

## 10. Testing instances

Two instances ship in `src/data/instances.py`:

- `demo_instance()` — 20 cases, 5 rooms, 6 surgeons. Small enough to read by eye, and
  exercises every constraint family: priority-4 lock-in, the shared C-arm, the
  pediatric block, recovery beds, room and surgeon capacity.
- `medium_instance()` — ~200 cases, 12 rooms, 17 surgeons, modeled loosely on the
  multi-service benchmark structure in Cardoen, Demeulemeester & Beliën (2010). Used to
  check the model still solves in reasonable time once it's too big to eyeball, and
  where the CP-vs-MILP gap from §3 actually shows up at scale (RESULTS.md).

For testing against real hospital logs rather than synthetic data, two CC BY-4.0
datasets are a direct structural fit (same horizon, same master-roster shape as §8):

- Akbarzadeh & Maenhout (2023), *Real life data for operating room scheduling problem*
  (Ghent University Hospital, May 2017). Mendeley Data.
- Akbarzadeh & Maenhout (2023), *RealLife operating room scheduling dataset,
  2021-Jan-May* — 20 weekly instances across 8 demand/flexibility configurations.

Their schema maps onto `PlanningInstance` without any formulation change — what's
missing is a loader, intentionally not built here given the brief's "small demo" scope.

## 11. Extensions

| Extension | Approach |
|---|---|
| Stochastic durations | Two-stage stochastic program: first stage places cases, second stage absorbs duration draws via overtime cost or a bumped case |
| Same-day rescheduling | Large-neighbourhood search seeded from the current plan, re-optimizing only around the disruption |
| Nurse/anaesthetist rostering | Extend $H$ to cover support staff with the same NoOverlap/sum pattern used for surgeons |
| Multi-week rolling horizon | Solve weekly, carry forward unscheduled cases at a bumped priority |
| Day-varying bed capacity | Replace the constant $\beta_\rho$ with a per-day-segmented cumulative resource |
| Per-specialty fairness | A secondary objective or constraint bounding each service's overdue share (§9) |

## 12. Passing this off to a developer

The four things I'd hand over: this file plus FORMULATION_CP.md, since together they're
the math and there's nothing to negotiate about variable meaning that isn't already
written down; `src/model/types.py`, because the dataclasses are the data dictionary —
every symbol above maps to a field there, so there's one source of truth instead of two
that can drift apart; the solver itself, where every constraint carries the same C-number
as the math (read them side by side and there's no ambiguity about which code implements
which formula); and `tests/test_model.py` as the acceptance bar — any reimplementation
has to pass the same hard-constraint checks on the same demo instance, and I'd ask for a
new test alongside any new constraint, not after it. Most of the actual confusion on
projects like this turns out to be vocabulary (what's a "room roster," what does
"ambulatory" restrict, what does priority 4 actually mean operationally) rather than the
math itself, so a short glossary of those terms is worth more than it sounds like it
should.

## 13. A reusable library of models

Four layers, solver-agnostic except the bottom one. Core data types first — plain
dataclasses like `PlanningInstance`, no solver imports — since every model in the
library sits on top of some typed representation of its problem. Above that, a small set
of constraint *patterns* that recur across scheduling problems regardless of domain:
capacity sums, no-double-booking via NoOverlap, a tiered-priority tardiness objective,
an eligibility pre-filter. Nurse rostering and bed allocation need the same shapes, not
the same model, so the patterns belong in a shared layer and the models don't. Above
that, problem templates that compose those patterns into something specific — this
project's formulation is one template. And a thin solver-adapter layer at the bottom,
one file per backend family (MILP, CP, local search), so a new problem picks a backend
without rewriting how its constraints are expressed. The CP-vs-MILP comparison this
project runs end to end is itself the template for that last layer: argue the backend
choice from the problem's structure, then check it empirically on a small instance,
rather than defaulting to whichever backend the team happens to know best.

## 14. References

1. Cardoen, B., Demeulemeester, E., & Beliën, J. (2010). Operating room planning and
   scheduling: A literature review. *European Journal of Operational Research*, 201(3),
   921–932.
2. Marques, I., & Captivo, M.E. (2015). *Planeamento de cirurgias eletivas no Centro
   Hospitalar Lisboa Norte*. MSc thesis, Universidade de Lisboa.
3. Denton, B.T., Miller, A.J., Balasubramanian, H.J., & Huschka, T.R. (2010). Optimal
   allocation of surgery blocks to operating rooms under uncertainty. *Operations
   Research*, 58(4), 802–816.
4. SIGIC — Sistema Integrado de Gestão de Inscritos para Cirurgia, Portaria n.º 45/2008,
   Diário da República, Portugal.
5. Van Riet, C., & Demeulemeester, E. (2015). Trade-offs in operating room planning for
   electives and emergencies. *OR Spectrum*, 37(1), 59–87.
6. Akbarzadeh, B., & Maenhout, B. (2023). Real life data for operating room scheduling
   problem [Data set]. Mendeley Data, V2. https://doi.org/10.17632/n2v49z2vnp.2
7. Akbarzadeh, B., & Maenhout, B. (2023). RealLife operating room scheduling dataset,
   2021-Jan-May [Data set]. Mendeley Data, V1. https://doi.org/10.17632/c8d342266x.1
8. Perron, L., & Furnon, V. *CP-SAT: a Constraint Programming Solver* (Google OR-Tools
   documentation). https://developers.google.com/optimization/cp
9. Baptiste, P., Le Pape, C., & Nuijten, W. (2001). *Constraint-Based Scheduling:
   Applying Constraint Programming to Scheduling Problems*. Kluwer Academic Publishers.
10. Vilím, P. (2004). O(n log n) filtering algorithms for unary resource constraints.
    *CPAIOR 2004*.
11. Schutt, A., Feydy, T., Stuckey, P.J., & Wallace, M.G. (2009). Why cumulative
    decomposition is not as bad as it sounds. *CP 2009*.
12. Laborie, P. (2009). IBM ILOG CP Optimizer for detailed scheduling illustrated on
    three problems. *CPAIOR 2009*.

---

## Appendix A — the comparison MILP, in detail

§3 introduces this as the empirical check on the CP-over-MILP argument, not a second
deliverable. Implemented in `src/solvers/milp_baseline_solver.py`; runnable via
`--solver milp-cbc` (bundled, no install needed), `--solver milp-gurobi`, or
`--solver milp-cplex` (both need a license OR-Tools/gurobipy can see).

Same sets and parameters as §4, minus the CP-only ones ($\pi^{ovf}$, $\rho(c)$,
$\text{los}_c$, $\beta_\rho$ — beds aren't expressible in this formulation at all, see
A.4).

### A.1 Decision variables

$$x_{cdr}\in\{0,1\}\ \forall c,d\in D_c,r \qquad z_c\in[0,1]\ \forall c: p_c\ne4$$

$z_c$ is relaxed to a continuous bound rather than declared binary; C3 below forces it
to $\{0,1\}$ at the optimum anyway, and the relaxation is free since nothing else in the
formulation benefits from declaring it binary up front. $x_{cdr}$ exists only for
triples surviving the same C4–C6 eligibility filter as the primary model.

### A.2 Objective

$$
\min \sum_{c:dd_c\ge0}\sum_{d,r}[dd_c+d]\,x_{cdr}
+ \sum_{c:dd_c<0}\sum_{d,r}[dd_c+\alpha d]\,x_{cdr}
+ \sum_{c:p_c\ne4} w_c\,z_c
$$

Identical in shape to §6's Terms 1–3, over $x_{cdr}/z_c$ instead of
$\text{pr}_{cdr}/u_c$, evaluated by the same `penalty.py` function every backend shares.

### A.3 Constraints

C1–C6 and C9 are unchanged from §7. C7 and C10 are where this formulation diverges from
the primary model:

**C7 — room capacity as a sum:**
$$\sum_c t_c^{tot}\,x_{cdr} \le k_{dr} \qquad \forall d,r$$

For a single room this is equivalent to exact non-overlap — any set of non-colliding
durations can always be packed sequentially — which is why C7 alone doesn't cost this
formulation anything by itself.

**C8 — surgeon, daily minutes only (no non-overlap variable exists in a MILP without a
big-M reformulation, §3):**
$$\sum_{c:\,\text{surgeon}(c)=h}\sum_r t_c^{op}\,x_{cdr} \le k_{hd} \qquad \forall h,d$$

**C10 — shared equipment, a day-level headcount:**
$$\sum_{c:u_{ce}=1}\sum_r x_{cdr} \le \kappa_{ed} \qquad \forall e,d$$

This counts how many equipment-$e$ cases land on a day, not whether their clock times
actually overlap — the single largest source of the objective gap measured in
RESULTS.md.

**No C11.** Recovery/ICU beds need a multi-day interval that starts on the day of
surgery. A day-bucket model has no variable that represents "day of surgery" as a value
— only as a fixed index a binary happens to be attached to — so there's no way to write
"occupy a bed for `los_c` days starting on whichever day this case lands" without first
becoming interval-based. This, more than the equipment gap, is the structural reason
this project ended up CP-based rather than MILP-based.

---

## Appendix B — IBM ILOG CP Optimizer, an alternative CP engine

This is an optional, license-gated backend (`src/solvers/cp_optimizer_solver.py`, run
with `--solver cp-optimizer`) included for one specific reason: it can model
sequence-dependent room turnover, something the primary CP-SAT model can't without
restructuring its interval variables. It is not a replacement for CP-SAT in this
project — see B.4 for the honest comparison.

### B.1 What's different

CP-SAT bakes cleaning into the room interval's own length: every candidate's interval is
sized $t_c^{tot} = t_c^{op}+t_c^{clean}$, so turnover is charged the same way regardless
of what case comes next. CP Optimizer instead sizes the interval at $t_c^{op}$ alone and
charges turnover as a transition cost between whichever two cases end up adjacent in a
room's chosen sequence, via a `sequence_var` plus a transition matrix on `no_overlap`:

| | Same service, back to back | Different service, back to back |
|---|---|---|
| CP-SAT (duration-bucketed, §4.2) | charged on the case alone, not the pair | same |
| CP Optimizer (transition matrix) | 15 min — same equipment setup | 35 min — full changeover |

Neither number is "more correct" in the abstract — both are instance-configurable
defaults (`same_service_turnover_min` / `cross_service_turnover_min` in
`PlanningInstance`). What changed is expressiveness: CP-SAT's room interval has no
variable a turnover rule could attach to *which two cases* are adjacent; CP Optimizer's
sequence variable does.

### B.2 Sets, variables, constraints

Same $C,D,R,H,E$ and shared parameters as the primary model, plus a service index
$\sigma(c)$ and a transition table $\tau_{\sigma\sigma'}$. Per case, one master interval
`task_c` (mandatory iff $p_c=4$) tied via `alternative()` to one candidate interval per
eligible $(d,r)$, sized $t_c^{op}$ only. Room turnover (C7) becomes a `sequence_var` per
room-day with `no_overlap(seq, transition_matrix)`; surgeon non-overlap (C8) uses the
same idiom without a transition matrix, since a surgeon doesn't need "cleaning time"
between cases the way a room does. Equipment (C10) and beds (C11) use the same additive
`pulse`-sum pattern instead of one `Cumulative` call — mechanically equivalent here, but
the additive form is what would let a later baseline-usage term get added without
changing the constraint's shape.

### B.3 Status

A CP Optimizer engine (IBM CPLEX Optimization Studio Community Edition, `docplex`) was
available while building this, so the backend has been run and checked, not just
written against documentation. On the demo instance it returns the same objective as
CP-SAT (155.0, 20/20 scheduled) — expected, since the objective only depends on which
day a case lands on, not on intra-day timing, and both engines pick the same days here.
The turnover gaps in its returned schedule were checked directly: every same-service
gap between adjacent cases equals exactly 15 minutes, confirming the transition matrix
is actually binding rather than a no-op.

### B.4 The honest comparison

| Solver | Status | Objective | Gap | Scheduled | Time |
|---|---|---|---|---|---|
| CP-SAT | Optimal | 155.0 | 0.00% | 20/20 | ~0.1s |
| CP Optimizer | Optimal | 155.0 | 0.00% | 20/20 | ~1.1s |

At the 200-case scale, CP Optimizer's own gap closes far more slowly than CP-SAT's at
the same time budget — likely a search-tuning difference (no custom search phase or
warm start was applied to it here) rather than a modeling one. The honest conclusion
this appendix supports is narrower than "CP Optimizer is better": it demonstrates that
*within* the CP paradigm, the choice of primitive still matters (a transition matrix is
a real answer to a real gap in the primary model's turnover assumption), while CP-SAT
remains the better-tuned, better-performing engine for this project at every scale
actually tested. That's why it's an appendix, not the model.

---

## Appendix C — calibration notes

A few of the constants above are worth flagging honestly rather than presenting as
settled:

- **Room turnover buckets** (15/25/40 min by duration, §4.2): real OR turnover is
  reported in the 15–60 minute range depending on procedure complexity and
  infection-control needs. Bucketing by duration captures part of that spread; a
  sequence-dependent model (Appendix B) captures another part. Neither is a substitute
  for a hospital's own measured turnover data.
- **Surgeon daily/weekly limits** (240/960 min in the demo instance): 240 minutes is one
  standard half-day theatre session, a common unit in block-scheduling literature
  (Cardoen et al., 2010). The weekly figure assumes roughly four of five weekdays are
  theatre days, leaving the rest for clinics and ward rounds — a structural pattern, not
  a single cited number, and the first thing to replace with a receiving hospital's
  actual job-plan structure.
- **ICU admission probability** in `medium_instance()` (12% for vascular/neuro cases):
  this only controls how the synthetic test data is generated — it isn't a parameter
  the optimizer ever sees, and it should be replaced with real admission data before any
  of this touches a real planning cycle.

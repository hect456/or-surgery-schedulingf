# Elective Surgery Scheduling — MILP Formulation

**Author:** Operations Research Scientist  Hector Bonilla
**Context:** Real-world OR scheduling problem at a Portuguese NHS hospital (SNS).  
**Reference:** Marques & Captivo (2015), *Centro Hospitalar Lisboa Norte (CHLN)*; Cardoen et al. (2010) benchmark.

---

## 1. Problem Statement

A large hospital group needs to schedule **elective surgical cases** (from the *Lista de Inscritos para Cirurgia*, LIC) across a **one-week planning horizon** (Monday–Friday). Every Friday, the planning team decides which cases from the waiting list will be performed the following week — *who*, *when*, and *in which room*.

The problem combines:
- **Case selection**: not all cases fit in one week (~7,000 patients on the CHLN waiting list).
- **Room allocation**: each room is assigned to a surgical service (the *MSS — Master Surgery Schedule*).
- **Surgeon scheduling**: each surgeon has daily and weekly operative time limits.
- **Priority rules**: the Portuguese SIGIC system mandates that scheduling respects both **clinical priority** and **waiting-list antiquity**.

---

## 2. Context & Motivation

### 2.1 Portuguese SNS / SIGIC Rules

The *Sistema Integrado de Gestão de Inscritos para Cirurgia* (SIGIC, Portaria n.º 45/2008) defines four clinical priority levels with maximum waiting times:

| Priority | Clinical Description     | Maximum Wait (days) |
|----------|--------------------------|---------------------|
| 1        | Normal                   | 270                 |
| 2        | Priority                 | 60                  |
| 3        | Very Priority            | 15                  |
| 4        | Deferred Urgent          | 3                   |

**Key operational fact (CHLN data, 2016):** Of 7,374 patients on the LIC, 16% had already exceeded their maximum wait time, with an average delay of 147 days. Neurosurgery cases waited an average of 261 days overdue. This makes the penalty structure in the objective function critical, not cosmetic.

### 2.2 What is *Advanced Scheduling*?

Following Cardoen et al. (2010), we focus on the **advance scheduling** phase: assigning cases to specific days and rooms, but **not** determining the exact sequence within a day (that is handled at execution time by the nursing team). This simplification is consistent with CHLN practice, confirmed in meetings with surgical directors.

---

## 3. Sets and Indices

| Symbol | Description |
|--------|-------------|
| $c \in C$ | Surgical cases (patient–procedure pairs on the LIC) |
| $d \in D$ | Planning days, $D = \{1, 2, 3, 4, 5\}$ (Mon–Fri) |
| $b \in B$ | Operating room blocks |
| $r \in R_b$ | Rooms within block $b$; write $R = \bigcup_b R_b$ |
| $s \in S$ | Surgical services (ORL, ORT, CVA, …) |
| $n \in N$ | Patients (a patient may have multiple LIC entries) |
| $h \in H$ | Surgeons |
| $D_c \subseteq D$ | Days on which case $c$ may be scheduled ($D_c = \{d_1\}$ for Priority 4; $D_c = D$ otherwise) |

---

## 4. Parameters

| Symbol | Description |
|--------|-------------|
| $d_1$ | First planning day (Monday) |
| $d_u$ | Last planning day (Friday) |
| $t_c^{\text{cir}}$ | Operative duration of case $c$ (minutes); deterministic estimate from CID-9-MC historical medians |
| $t_c^{\text{lim}}$ | Room cleaning/turnover time after case $c$ (minutes; default 20 min) |
| $t_c^{\text{tot}} = t_c^{\text{cir}} + t_c^{\text{lim}}$ | Total room occupation time |
| $k_{dbr}$ | Capacity of room $r$ in block $b$ on day $d$ (minutes of opening) |
| $k_{hd}^{\text{dia}}$ | Surgeon $h$'s daily operative time limit on day $d$ (minutes) |
| $k_h^{\text{sem}}$ | Surgeon $h$'s weekly operative time limit (minutes) |
| $a_{dbr}^s \in \{0,1\}$ | 1 if room $r$, block $b$, day $d$ is assigned to service $s$ (from MSS) |
| $p_c \in \{1,2,3,4\}$ | Clinical priority of case $c$ |
| $\text{amb}_c$ | Scope: 1 = conventional (inpatient), 2 = ambulatory (day-case) |
| $i_c$ | Age of patient associated with case $c$ |
| $wl_c^{\text{dia}}$ | Date case $c$ entered the waiting list (days before $d_1$) |
| $wl_c^{\text{max}}$ | Maximum waiting days for priority $p_c$: 270/60/15/3 |
| $dd_c = wl_c^{\text{dia}} + wl_c^{\text{max}} - d_1$ | Days remaining until deadline ($< 0$ = already overdue) |
| $w_c$ | Non-scheduling penalty for case $c$ (see §5.3) |
| $\alpha > 1$ | Urgency multiplier for overdue cases in the objective (default 2.0) |
| $n_c$ | Patient associated with case $c$ |
| $h_c$ | Surgeon assigned to case $c$ |
| $s_c$ | Service of case $c$ |
| $i$ | Paediatric age limit (8 years, ORL Friday circuit) |

---

## 5. Model

### 5.1 Decision Variables

$$
x_{cdbr} = \begin{cases} 1 & \text{if case } c \text{ is scheduled on day } d, \text{ block } b, \text{ room } r \\ 0 & \text{otherwise} \end{cases}
\quad \forall c \in C,\; d \in D_c,\; b \in B,\; r \in R_b
$$

$$
z_c \geq 0 \quad \forall c \in C
$$

$z_c$ is an auxiliary variable that equals 1 when case $c$ is **not** scheduled. It is defined in $\mathbb{R}^+$ but takes values in $\{0, 1\}$ by force of constraint (5.3). For Priority-4 cases, $z_c = 0$ is enforced explicitly.

> **Implementation note:** Pre-filtering eliminates $x_{cdbr}$ where $a_{dbr}^{s_c} = 0$. This reduces the number of binary variables by ~85% in the CHLN instance, giving a significant speed-up (Marques & Captivo, 2015, §5.3 rationale).

### 5.2 Constraints

**(5.1) One procedure per patient per week**

A patient with multiple LIC entries may only be called for one surgery per planning horizon. Services that routinely operate cooperatively handle this separately.

$$
\sum_{\substack{c \in C:\\ n_c = n}} \sum_{d \in D} \sum_{b \in B} \sum_{r \in R_b} x_{cdbr} \leq 1, \quad \forall n \in N
$$

**(5.2) Priority-4 (Deferred Urgent) on day 1**

$wl_c^{\text{max}} = 3$ days. Since planning is done on Friday for the following week, these cases must be performed on Monday or they will exceed the clinical limit.

$$
\sum_{b \in B} \sum_{r \in R_b} x_{c, d_1, b, r} = 1, \quad \forall c \in C : p_c = 4
$$

**(5.3) Schedule or penalise (non-urgent cases)**

Every non-Priority-4 case is either scheduled exactly once, or it incurs the penalty $w_c$:

$$
\sum_{d \in D} \sum_{b \in B} \sum_{r \in R_b} x_{cdbr} + z_c = 1, \quad \forall c \in C : p_c \neq 4
$$

**(5.4) Service–room assignment (MSS)**

Rooms may only host cases from the service assigned by the Master Surgery Schedule:

$$
\sum_{\substack{c \in C:\\ s_c = s}} x_{cdbr} \leq a_{dbr}^s \cdot M, \quad \forall s \in S,\; d \in D,\; b \in B,\; r \in R_b
$$

where $M = |C|$ is a sufficiently large constant. When $a_{dbr}^s = 0$, all associated $x_{cdbr}$ are forced to zero — this is the main variable-reduction mechanism.

**(5.5) Ambulatory-only block**

The *Bloco Ambulatório de Urologia* only hosts day-case procedures ($\text{amb}_c = 2$):

$$
\sum_{\substack{c \in C:\\ \text{amb}_c \neq 2,\; s_c = \text{URO}}} x_{cdbr} = 0, \quad \forall d \in D,\; r \in R_b : b = B_{\text{URO\_AMB}}
$$

**(5.6) ORL paediatric circuit (Friday)**

Every Friday the ORL block operates only patients aged $\leq i = 8$ years:

$$
\sum_{\substack{c \in C:\\ i_c > i,\; s_c = \text{ORL}}} x_{c, d_u, b, r} = 0, \quad \forall r \in R_b : b = B_{\text{ORL}}
$$

**(5.7) Room capacity — no overtime**

The total room occupation (surgery + cleaning) may not exceed the room's opening hours:

$$
\sum_{c \in C} t_c^{\text{tot}} \cdot x_{cdbr} \leq k_{dbr}, \quad \forall d \in D,\; b \in B,\; r \in R_b
$$

**(5.8) Surgeon daily time limit**

Each surgeon's daily operative time is bounded. This constraint also prevents double-booking (a surgeon in two rooms simultaneously), because $k_{hd}^{\text{dia}} = \min(\text{surgeon limit}, \text{max room capacity on day } d)$:

$$
\sum_{\substack{c \in C:\\ h_c = h}} \sum_{b \in B} \sum_{r \in R_b} t_c^{\text{cir}} \cdot x_{cdbr} \leq k_{hd}^{\text{dia}}, \quad \forall h \in H,\; d \in D
$$

**(5.9) Surgeon weekly time limit**

$$
\sum_{\substack{c \in C:\\ h_c = h}} \sum_{d \in D} \sum_{b \in B} \sum_{r \in R_b} t_c^{\text{cir}} \cdot x_{cdbr} \leq k_h^{\text{sem}}, \quad \forall h \in H
$$

**(5.10–5.11) Variable domains**

$$
x_{cdbr} \in \{0, 1\}, \quad \forall c \in C,\; d \in D_c,\; b \in B,\; r \in R_b
$$
$$
z_c \geq 0, \quad \forall c \in C
$$

### 5.3 Objective Function

The objective captures the two SIGIC principles — **priority** and **antiquity** — plus a strong penalty for non-scheduling:

$$
\min \underbrace{\sum_{\substack{c \in C:\\ dd_c \geq 0}} \sum_{d \in D} \sum_{b \in B} \sum_{r \in R_b} \bigl[(dd_c - d_1) + d\bigr] \cdot x_{cdbr}}_{\text{Term 1: on-time cases — prefer earlier scheduling}}
$$
$$
+ \underbrace{\sum_{\substack{c \in C:\\ dd_c < 0}} \sum_{d \in D} \sum_{b \in B} \sum_{r \in R_b} \bigl[(dd_c - d_1) + \alpha \cdot d\bigr] \cdot x_{cdbr}}_{\text{Term 2: overdue cases — urgency multiplier } \alpha > 1}
$$
$$
+ \underbrace{\sum_{c \in C} p_c \cdot w_c \cdot z_c}_{\text{Term 3: penalty for non-scheduling}}
$$

**Interpretation:**
- **Term 1:** For on-time cases, the coefficient $(dd_c - d_1) + d$ is larger when the deadline is far away and the chosen day is later in the week. The model prefers cases with less time remaining and earlier days.
- **Term 2:** For overdue cases, multiplying the day index by $\alpha > 1$ makes it more expensive to defer them to later in the week, effectively urgentising their scheduling.
- **Term 3:** $w_c = \text{PenaltyFactor}(p_c, dd_c) + 1.2 \cdot \max_{c'} dd_{c'}$ ensures this term dominates Terms 1–2, so the model always prefers to schedule rather than leave cases unscheduled when feasible.

**Priority multipliers** (Tabela 5.1, Marques & Captivo 2015):

| Priority | Multiplier | Interpretation |
|----------|-----------|----------------|
| 1        | ×1        | Base reference |
| 2        | ×4.5      | 1 overdue day in P2 ≡ 4.5 overdue days in P1 |
| 3        | ×18       | 1 overdue day in P3 ≡ 18 overdue days in P1 |
| 4        | ×90       | 1 overdue day in P4 ≡ 90 overdue days in P1 |

---

## 6. Model Classification and Complexity

- **Problem type:** Mixed-Integer Linear Programme (MILP).
- **NP-hardness:** Follows from reduction to Bin Packing (room capacity constraints alone are NP-hard).
- **Variable count (CHLN):** $|C| \cdot |D| \cdot \sum|R_b| \approx 7000 \times 5 \times 28 \approx 980,000$ binary variables before pre-filtering; ~130,000 after filtering via (5.4).
- **Practical solvability:** The block-diagonal structure (rooms partitioned by service) allows near-perfect decomposition. Marques & Captivo report optimal solutions in under 5 minutes with CPLEX on CHLN-scale instances.

---

## 7. What We Include and Why

| Included | Rationale |
|----------|-----------|
| SIGIC priority + antiquity in objective | Legally mandated by Portuguese NHS; without it, the model is clinically invalid |
| Room capacity constraint (5.7) | Core feasibility: overtime is forbidden by Portuguese labour law |
| Surgeon daily + weekly limits (5.8–5.9) | Prevents both overwork and double-booking (elegant double duty of one constraint) |
| Service–room assignment via MSS (5.4) | Each room requires specialty-specific equipment; moving equipment between rooms risks damage |
| ORL paediatric circuit (5.6) | Confirmed operational rule; violating it would cause patient safety concerns |
| Priority-4 on day 1 (5.2) | Clinical obligation; 72-hour window closes before any other weekday |
| One case per patient per week (5.1) | Conservative default; cooperative scheduling is service-specific and not uniformly agreed |
| Deterministic durations | Historical medians by service × CID-9-MC category; stochastic extension discussed below |

---

## 8. What We Exclude and Why

| Excluded | Rationale |
|----------|-----------|
| Within-day sequencing | Hospital confirmed *advanced scheduling* only; nursing teams handle ordering |
| Anaesthesiologists and nurses | MSS pre-allocates them; confirmed by anaesthesiology director as non-binding |
| Recovery/ICU bed constraints | Adds significant complexity; downstream constraint for future extension |
| Equipment sharing between rooms | Uncommon in practice; modelled implicitly by MSS |
| Stochastic surgery durations | Deterministic approximation is standard in the literature (Denton et al. 2010); adds tractability |
| Patient preferences for days | Not part of SIGIC protocol; relevant for future patient-centred extensions |

---

## 9. Open Questions (Interview)

### Q1: Passing the Torch

To hand this formulation to a developer:

1. **This markdown + code** — the formulation is self-contained and the code mirrors it exactly (same constraint numbering, same variable names).
2. **Entity–Relationship diagram** — show Tables: Cases, Patients, Surgeons, Rooms, MSS (service-room-day triples). The `PlanningInstance` dataclass already encodes this schema.
3. **Acceptance test suite** — `tests/test_model.py` has 7 tests that any correct implementation must pass: P4 on day 1, room capacity, surgeon limits, paediatric circuit, feasibility of greedy and MILP.
4. **Minimum reproducible instance** — the `demo_chln()` instance with 20 cases and known optimal objective value. The developer verifies their implementation against this before scaling.
5. **Domain glossary** — LIC, MSS, SIGIC, prioridade, âmbito, higienização — shared vocabulary prevents misunderstandings between OR scientists and engineers.

The key principle: *the code is the specification*. Constraint (5.7) in the markdown corresponds to `C57_RoomCapacity` in `pyomo_solver.py` and the analogous block in `pulp_cbc_solver.py`. A developer reading both documents simultaneously should have no ambiguity.

### Q2: A Library of Models

Organise the library around **four layers**:

1. **Core abstractions** (`BaseSolver`, `PlanningInstance`, typed data structures) — solver-agnostic, no external dependencies, fully unit-testable.
2. **Domain building blocks** (`CapacityConstraint`, `ResourceLimitConstraint`, `PriorityWeighting`, `TimeWindowConstraint`) — reusable across healthcare scheduling problems (nurse rostering, bed allocation, equipment assignment).
3. **Problem templates** (`SurgeryScheduler`, `NurseRoster`, `BedAllocation`) — compositions of domain blocks, configurable via YAML/JSON without touching Python code.
4. **Institution configurations** (`CHLN_Config`, `HospitalX_Config`) — override defaults (MSS structure, SIGIC parameters, special rules) for each client.

The solver layer is orthogonal: the same `ConcreteModel` is compiled to CBC, Gurobi, CPLEX, or HiGHS by passing a string flag. This lets us switch backends as licence availability or instance size demands, without rewriting the formulation.

---

## 10. Extensions and Future Work

| Extension | Approach |
|-----------|----------|
| Stochastic durations | Two-stage SP: first stage selects cases, second stage handles duration scenarios with recourse (overtime cost or case removal) |
| ICU/ward bed constraints | Add downstream capacity constraints; link to bed management module |
| Nurse and anaesthesiologist scheduling | Extend H to include staff teams; add team-level constraints |
| Multi-week rolling horizon | Solve weekly, carry-forward unscheduled cases with increased priority weight |
| Robust scheduling | Min-max regret formulation; buffer time between cases proportional to $\sigma$ of duration distribution |
| Real-time rescheduling | LNS (Large Neighbourhood Search) for intra-day disruptions (emergency cases, equipment failure) |

---

## 11. References

1. Marques, I., & Captivo, M.E. (2015). *Planeamento de cirurgias eletivas no Centro Hospitalar Lisboa Norte*. MSc Thesis, Universidade de Lisboa.
2. Cardoen, B., Demeulemeester, E., & Beliën, J. (2010). Operating room planning and scheduling: A literature review. *European Journal of Operational Research*, 201(3), 921–932.
3. Denton, B.T., Miller, A.J., Balasubramanian, H.J., & Huschka, T.R. (2010). Optimal allocation of surgery blocks to operating rooms under uncertainty. *Operations Research*, 58(4), 802–816.
4. SIGIC — Sistema Integrado de Gestão de Inscritos para Cirurgia. Portaria n.º 45/2008, Diário da República, Portugal.
5. Vanhoucke, M., Rodammer, F., Straeten, G., & Cardoen, B. (2007). *Operating Theatre Planning and Scheduling*. Springer.
6. Van Riet, C., & Demeulemeester, E. (2015). Trade-offs in operating room planning for electives and emergencies. *OR Spectrum*, 37(1), 59–87.

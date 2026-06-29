#!/usr/bin/env python3
"""
main.py — demo runner for elective surgery scheduling.

CP-SAT (src/solvers/cp_sat_interval_solver.py) is the model this project is
built around — see FORMULATION.md for why. Everything else --solver accepts
is an optional comparison point, off by default: a day-bucket MILP
(CBC/SCIP/Gurobi/CPLEX) and IBM CP Optimizer, both discussed only in
FORMULATION.md's appendix.

Usage
-----
    python main.py                          # demo instance, CP-SAT
    python main.py --instance medium        # ~200-case instance, CP-SAT
    python main.py --solver milp-cbc        # comparison MILP, open-source backend
    python main.py --solver milp-gurobi     # comparison MILP, Gurobi backend (needs a license)
    python main.py --solver milp-cplex      # comparison MILP, CPLEX backend (needs a license)
    python main.py --solver cp-optimizer    # second CP engine (needs a license; falls back to CP-SAT)
    python main.py --benchmark              # run every available solver, compare
    python main.py --time-limit 60          # solver time limit (seconds)

Prints a human-readable weekly schedule to stdout.
"""

import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

# Windows consoles often default to a legacy codepage that can't encode the
# box-drawing / arrow characters the reporter uses — force UTF-8 stdout.
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

from src.data.instances import demo_instance, medium_instance
from src.solvers.milp_baseline_solver import MILPBaselineSolver
from src.solvers.cp_sat_interval_solver import CPSATIntervalSolver
from src.solvers.cp_optimizer_solver import CPOptimizerSolver
from src.utils.reporter import print_header, print_result
from src.utils.visualizer import plot_schedule


INSTANCES = {
    "demo":      demo_instance,
    "medium":    medium_instance,
}


def get_solver(name: str, time_limit: int, gap: float):
    name = name.lower()
    if name in ("milp", "milp-cbc", "cbc"):
        return MILPBaselineSolver(backend="CBC", time_limit_sec=time_limit, mip_gap=gap)
    elif name in ("milp-gurobi", "gurobi"):
        return MILPBaselineSolver(backend="GUROBI", time_limit_sec=time_limit, mip_gap=gap)
    elif name in ("milp-scip", "scip"):
        return MILPBaselineSolver(backend="SCIP", time_limit_sec=time_limit, mip_gap=gap)
    elif name in ("milp-cplex", "cplex"):
        return MILPBaselineSolver(backend="CPLEX", time_limit_sec=time_limit, mip_gap=gap)
    elif name in ("cp-sat", "cpsat", "interval"):
        return CPSATIntervalSolver(time_limit_sec=time_limit, mip_gap=gap)
    elif name in ("cp-optimizer", "cpo", "ibm-cp"):
        return CPOptimizerSolver(time_limit_sec=time_limit, mip_gap=gap)
    else:
        print(f"Unknown solver '{name}'. Defaulting to cp-sat.")
        return CPSATIntervalSolver(time_limit_sec=time_limit, mip_gap=gap)


def main():
    parser = argparse.ArgumentParser(description="Elective Surgery Scheduling — demo runner")
    parser.add_argument("--instance", choices=list(INSTANCES.keys()), default="demo",
                         help="Which instance to solve (default: demo)")
    parser.add_argument("--solver", default="cp-sat",
                         help="cp-sat (default) | milp-cbc | milp-gurobi | milp-scip | milp-cplex | "
                              "cp-optimizer")
    parser.add_argument("--benchmark", action="store_true",
                         help="Run CP-SAT alongside the optional comparison backends and print "
                              "a table (see RESULTS.md)")
    parser.add_argument("--time-limit", type=int, default=120,
                         help="Solver time limit in seconds (default: 120)")
    parser.add_argument("--gap", type=float, default=0.01,
                         help="Relative MIP/CP gap tolerance (default: 0.01 = 1%%)")
    parser.add_argument("--plot", metavar="PATH",
                         help="Save a Gantt-style PNG of the resulting schedule to PATH")
    args = parser.parse_args()

    instance = INSTANCES[args.instance]()
    print_header(instance)

    if args.benchmark:
        _run_benchmark(instance, args.time_limit, args.gap)
    else:
        solver = get_solver(args.solver, args.time_limit, args.gap)
        result = solver.solve(instance)
        print_result(result, instance)
        if args.plot:
            plot_schedule(instance, result, args.plot)
            print(f"  Saved schedule plot to {args.plot}")


def _run_benchmark(instance, time_limit: int, gap: float):
    """Runs CP-SAT next to the optional comparison backends on the same
    instance, so the choice made in FORMULATION.md can be checked, not
    just asserted — this is what RESULTS.md is built from."""
    candidates = [
        ("CP-SAT/Interval", CPSATIntervalSolver(time_limit_sec=time_limit, mip_gap=gap)),
        ("OR-Tools/CBC",   MILPBaselineSolver(backend="CBC", time_limit_sec=time_limit, mip_gap=gap)),
        ("Gurobi",         MILPBaselineSolver(backend="GUROBI", time_limit_sec=time_limit, mip_gap=gap)),
        ("CP Optimizer",   CPOptimizerSolver(time_limit_sec=time_limit, mip_gap=gap)),
    ]

    results = []
    for label, s in candidates:
        print(f"  Running {label} ...")
        r = s.solve(instance)
        results.append(r)
        obj = f"{r.objective_value:.2f}" if r.objective_value is not None else "N/A"
        print(f"    -> {r.status}  obj={obj}  "
              f"scheduled={len(r.assignments)}/{len(instance.cases)}  "
              f"time={r.solve_time_sec:.3f}s")

    print()
    header = "  | Solver                 | Status    | Obj       | Gap     | Sched     | Time (s) |"
    rule = "  +" + "-" * 24 + "+" + "-" * 11 + "+" + "-" * 11 + "+" + "-" * 9 + "+" + "-" * 11 + "+" + "-" * 10 + "+"
    print(rule)
    print(header)
    print(rule)
    for r in results:
        sched = f"{len(r.assignments)}/{len(instance.cases)}"
        obj   = f"{r.objective_value:.1f}" if r.objective_value else "N/A"
        # Never assume a zero gap for an unreported value: Gurobi and CP-SAT
        # both terminate "Optimal" once within their configured relative
        # gap, which can be > 0% (see RESULTS.md).
        gap   = f"{r.gap*100:.2f}%" if r.gap is not None else "-"
        print(f"  | {r.solver_name:<22} | {r.status:<9} | {obj:<9} | {gap:<7} | {sched:<9} | "
              f"{r.solve_time_sec:<8.3f} |")
    print(rule)
    print()

    best = min((r for r in results if r.is_optimal()),
               key=lambda r: r.objective_value if r.objective_value is not None else float("inf"),
               default=results[-1])
    print(f"  Best solution from: {best.solver_name}")
    print_result(best, instance)


if __name__ == "__main__":
    main()

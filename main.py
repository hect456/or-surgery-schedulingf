#!/usr/bin/env python3
"""
main.py — Demo runner for Elective Surgery Scheduling.

The primary model is the interval-based CP-SAT solver (FORMULATION.md). The
MILP and Hexaly backends are optional comparison/extension points
(FORMULATION.md §12) — useful for `--benchmark`, not required to see the
primary model run.

Usage
-----
    python main.py                          # demo instance, primary CP-SAT model
    python main.py --instance medium        # ~200-case scaling instance
    python main.py --solver milp-cbc        # alternative MILP, for comparison
    python main.py --solver milp-gurobi     # alternative MILP, Gurobi backend
    python main.py --solver hexaly          # optional, falls back to CBC if unlicensed
    python main.py --solver greedy          # heuristic, no solver needed
    python main.py --benchmark              # run every available solver, compare
    python main.py --time-limit 60          # solver time limit (seconds)

The script prints a human-readable weekly schedule to stdout.
"""

import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

# Windows consoles often default to a legacy codepage that can't encode the
# box-drawing / arrow characters the reporter uses — force UTF-8 stdout.
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

from src.data.instances import demo_instance, medium_instance, literature_chln_instance
from src.solvers.milp_baseline_solver import MILPBaselineSolver
from src.solvers.cp_sat_interval_solver import CPSATIntervalSolver
from src.solvers.greedy_solver import GreedySolver
from src.solvers.hexaly_solver import HexalySolver
from src.utils.reporter import print_header, print_result
from src.utils.visualizer import plot_schedule


INSTANCES = {
    "demo":      demo_instance,
    "medium":    medium_instance,
    "chln":      literature_chln_instance,   # calibrated to real, published CHLN statistics
}


def get_solver(name: str, time_limit: int, gap: float):
    name = name.lower()
    if name in ("milp", "milp-cbc", "cbc"):
        return MILPBaselineSolver(backend="CBC", time_limit_sec=time_limit, mip_gap=gap)
    elif name in ("milp-gurobi", "gurobi"):
        return MILPBaselineSolver(backend="GUROBI", time_limit_sec=time_limit, mip_gap=gap)
    elif name in ("milp-scip", "scip"):
        return MILPBaselineSolver(backend="SCIP", time_limit_sec=time_limit, mip_gap=gap)
    elif name in ("cp-sat", "cpsat", "interval"):
        return CPSATIntervalSolver(time_limit_sec=time_limit, mip_gap=gap)
    elif name == "hexaly":
        return HexalySolver(time_limit_sec=time_limit, mip_gap=gap)
    elif name == "greedy":
        return GreedySolver()
    else:
        print(f"Unknown solver '{name}'. Defaulting to cp-sat (the primary model).")
        return CPSATIntervalSolver(time_limit_sec=time_limit, mip_gap=gap)


def main():
    parser = argparse.ArgumentParser(description="Elective Surgery Scheduling — demo runner")
    parser.add_argument("--instance", choices=list(INSTANCES.keys()), default="demo",
                         help="Which instance to solve (default: demo)")
    parser.add_argument("--solver", default="cp-sat",
                         help="cp-sat (primary) | milp-cbc | milp-gurobi | milp-scip | hexaly | greedy")
    parser.add_argument("--benchmark", action="store_true",
                         help="Optional: run every available solver and print a comparison "
                              "table (used to validate the cp-sat-vs-MILP choice in RESULTS.md)")
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
    """Optional comparison mode: runs the alternative MILP/Hexaly backends
    alongside the primary CP-SAT model so the choice argued for in
    FORMULATION.md §3 can be checked empirically (this is what RESULTS.md
    is built from) — not required to see the primary model run."""
    candidates = [
        ("Greedy",         GreedySolver()),
        ("CP-SAT/Interval", CPSATIntervalSolver(time_limit_sec=time_limit, mip_gap=gap)),
        ("OR-Tools/CBC",   MILPBaselineSolver(backend="CBC", time_limit_sec=time_limit, mip_gap=gap)),
        ("Gurobi",         MILPBaselineSolver(backend="GUROBI", time_limit_sec=time_limit, mip_gap=gap)),
        ("Hexaly",         HexalySolver(time_limit_sec=time_limit, mip_gap=gap)),
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
        # gap, which can be > 0% (see RESULTS.md). Show "-" only when the
        # solver genuinely doesn't track a bound (Greedy).
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

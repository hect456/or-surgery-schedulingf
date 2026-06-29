"""
visualizer.py — Simple Gantt-style PNG of a weekly schedule.

Per the case prompt: "Don't spend time on visualization ... a plain terminal
output or a simple image of the schedule is plenty." This module does the
minimum needed for that: one bar per scheduled case, one row per room, one
weekly timeline, colored by service.

If the solver reported exact start/end times (CP-SAT), bars use them as-is.
If not (the comparison MILP, which only reasons at day+room granularity),
cases sharing a (day, room) slot are laid out back-to-back in case-id order
purely for display — the MILP itself makes no claim about ordering.
"""

from __future__ import annotations
from collections import defaultdict

from ..model.types import PlanningInstance, SolverResult


def plot_schedule(instance: PlanningInstance, result: SolverResult, out_path: str,
                   title: str | None = None) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch

    case_map = instance.cases_by_id
    rooms = instance.rooms
    days = instance.days
    day_index = {d: i for i, d in enumerate(days)}
    room_row = {r.id: i for i, r in enumerate(rooms)}

    day_width = max(
        (cap for r in rooms for cap in r.capacity_min.values()), default=480
    ) + 40

    services = sorted({c.service for c in instance.cases})
    cmap = plt.get_cmap("tab10")
    color_of = {s: cmap(i % 10) for i, s in enumerate(services)}

    by_slot = defaultdict(list)
    for a in result.assignments:
        by_slot[(a.day, a.room_id)].append(a)

    fig, ax = plt.subplots(figsize=(16, 0.6 * len(rooms) + 2))

    for (d, rid), items in by_slot.items():
        has_times = all(a.start_min is not None for a in items)
        ordered = sorted(items, key=lambda a: a.start_min if has_times else a.case_id)
        cursor = 0
        x0 = day_index[d] * day_width
        y = room_row[rid]
        for a in ordered:
            c = case_map[a.case_id]
            if has_times:
                s, e = a.start_min, a.end_min
            else:
                s, e = cursor, cursor + c.t_tot
                cursor = e
            edge = "black" if c.must_schedule_day1 else "none"
            lw = 1.8 if c.must_schedule_day1 else 0.0
            ax.barh(y, e - s, left=x0 + s, height=0.8,
                    color=color_of[c.service], edgecolor=edge, linewidth=lw)
            if e - s >= 35:
                ax.text(x0 + s + (e - s) / 2, y, a.case_id, ha="center", va="center",
                        fontsize=6, color="white")

    for i, d in enumerate(days):
        ax.axvline(i * day_width, color="grey", linewidth=0.6, linestyle="--")
        ax.text(i * day_width + day_width / 2, len(rooms) - 0.3, d,
                 ha="center", va="bottom", fontsize=10, fontweight="bold")

    ax.set_yticks(range(len(rooms)))
    ax.set_yticklabels([r.id for r in rooms])
    ax.set_xlim(0, len(days) * day_width)
    ax.set_ylim(-0.6, len(rooms))
    ax.set_xlabel("Minutes from room opening, per day")
    ax.set_title(title or f"{instance.name} — {result.solver_name} (obj={result.objective_value:.1f})")

    legend = [Patch(facecolor=color_of[s], label=s) for s in services]
    legend.append(Patch(facecolor="white", edgecolor="black", linewidth=1.8, label="priority-4 (day-1 lock)"))
    ax.legend(handles=legend, loc="upper center", bbox_to_anchor=(0.5, -0.12),
              ncol=min(len(legend), 6), fontsize=8)

    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)

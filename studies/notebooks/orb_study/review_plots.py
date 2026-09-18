"""Chart styling for the ORB review notebooks (presentation only; not part of the frozen computation).

Fixed categorical order, thin marks, hairline grid, text in ink colours rather than series colours.
"""
from __future__ import annotations

import matplotlib.pyplot as plt

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
NEGATIVE = "#e34948"
NEUTRAL = "#b8b6ae"

plt.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "axes.edgecolor": AXIS, "axes.labelcolor": INK2, "axes.titlecolor": INK, "axes.titlesize": 11,
    "axes.titleweight": "bold", "axes.labelsize": 9, "xtick.color": MUTED, "ytick.color": MUTED,
    "xtick.labelsize": 8, "ytick.labelsize": 8, "font.family": ["Segoe UI", "DejaVu Sans", "sans-serif"],
    "legend.frameon": False, "legend.fontsize": 8, "lines.linewidth": 1.6, "lines.markersize": 6,
    "figure.dpi": 110,
})


def style(ax, grid_axis: str = "y") -> None:
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(AXIS)
        ax.spines[side].set_linewidth(0.8)
    ax.grid(axis=grid_axis, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.tick_params(length=0)


def zero_line(ax, y: float = 0.0, label: str | None = None, color: str = AXIS) -> None:
    ax.axhline(y, color=color, linewidth=1.0, zorder=1)
    if label:
        ax.annotate(label, xy=(1.0, y), xycoords=("axes fraction", "data"), xytext=(-4, 4),
                    textcoords="offset points", ha="right", va="bottom", fontsize=8, color=INK2)

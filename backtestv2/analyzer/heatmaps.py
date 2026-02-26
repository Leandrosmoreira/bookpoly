"""
Heatmap generators for backtest analysis.

Generates 3 heatmaps:
1. Win rate by probability zone x hour of day
2. PnL by spread x imbalance
3. Trade count by hour x day of week
"""

import matplotlib
matplotlib.use("Agg")

import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
from collections import defaultdict
from loader import TradeRow


def _build_grid(trades: list[TradeRow], row_fn, col_fn, val_fn, agg_fn):
    """
    Build a 2D grid from trades.

    Args:
        row_fn: Function to get row key from trade
        col_fn: Function to get column key from trade
        val_fn: Function to get value from trade
        agg_fn: Aggregation function (e.g., mean, count)

    Returns:
        (grid_dict, row_labels, col_labels)
    """
    buckets = defaultdict(list)
    rows_set = set()
    cols_set = set()

    for t in trades:
        r = row_fn(t)
        c = col_fn(t)
        v = val_fn(t)
        buckets[(r, c)].append(v)
        rows_set.add(r)
        cols_set.add(c)

    return buckets, sorted(rows_set), sorted(cols_set)


def _render_heatmap(
    data: list[list[float]],
    row_labels: list,
    col_labels: list,
    title: str,
    xlabel: str,
    ylabel: str,
    fmt: str = ".1f",
    cmap: str = "RdYlGn",
    vmin: float = None,
    vmax: float = None,
    figsize: tuple = (14, 6),
    style: str = "dark_background",
) -> plt.Figure:
    """Render a 2D heatmap with annotations."""
    try:
        plt.style.use(style)
    except OSError:
        plt.style.use("default")

    fig, ax = plt.subplots(figsize=figsize)

    # Create image
    im = ax.imshow(
        data, cmap=cmap, aspect="auto",
        vmin=vmin, vmax=vmax,
    )

    # Labels
    ax.set_xticks(range(len(col_labels)))
    ax.set_xticklabels(col_labels, fontsize=8)
    ax.set_yticks(range(len(row_labels)))
    ax.set_yticklabels(row_labels, fontsize=9)

    # Annotate cells
    for i in range(len(row_labels)):
        for j in range(len(col_labels)):
            val = data[i][j]
            if val != 0:
                text_color = "white" if abs(val) > (vmax or 1) * 0.6 else "black"
                ax.text(j, i, f"{val:{fmt}}", ha="center", va="center",
                        fontsize=7, color=text_color)

    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax.set_title(title)

    fig.colorbar(im, ax=ax, shrink=0.8)
    fig.tight_layout()
    return fig


def heatmap_zone_hour(
    trades: list[TradeRow],
    figsize: tuple = (14, 6),
    style: str = "dark_background",
) -> plt.Figure:
    """Win rate heatmap: probability zone x hour of day."""
    zone_order = ["danger", "caution", "safe", "neutral"]
    hours = list(range(24))

    buckets = defaultdict(list)
    for t in trades:
        zone = t.prob_zone
        hour = t.hour
        buckets[(zone, hour)].append(1 if t.won else 0)

    # Build grid
    data = []
    active_zones = [z for z in zone_order if any((z, h) in buckets for h in hours)]
    if not active_zones:
        active_zones = zone_order

    for zone in active_zones:
        row = []
        for hour in hours:
            vals = buckets.get((zone, hour), [])
            if vals:
                row.append(sum(vals) / len(vals) * 100)
            else:
                row.append(0)
        data.append(row)

    return _render_heatmap(
        data=data,
        row_labels=active_zones,
        col_labels=[str(h) for h in hours],
        title="Win Rate (%) by Probability Zone x Hour",
        xlabel="Hour (UTC)",
        ylabel="Probability Zone",
        fmt=".0f",
        cmap="RdYlGn",
        vmin=0,
        vmax=100,
        figsize=figsize,
        style=style,
    )


def heatmap_spread_imbalance(
    trades: list[TradeRow],
    figsize: tuple = (12, 6),
    style: str = "dark_background",
) -> plt.Figure:
    """PnL heatmap: spread bins x imbalance bins."""
    spread_bins = [
        ("0-1%", 0, 1),
        ("1-3%", 1, 3),
        ("3-5%", 3, 5),
        ("5%+", 5, 100),
    ]
    imb_bins = [
        ("-1 to -0.3", -1.0, -0.3),
        ("-0.3 to -0.1", -0.3, -0.1),
        ("-0.1 to 0.1", -0.1, 0.1),
        ("0.1 to 0.3", 0.1, 0.3),
        ("0.3 to 1", 0.3, 1.0),
    ]

    def _find_bin(val, bins):
        for label, lo, hi in bins:
            if lo <= val < hi:
                return label
        return bins[-1][0]  # Last bin

    buckets = defaultdict(list)
    for t in trades:
        s_label = _find_bin(t.spread_pct, spread_bins)
        i_label = _find_bin(t.imbalance, imb_bins)
        buckets[(i_label, s_label)].append(t.pnl)

    imb_labels = [b[0] for b in imb_bins]
    spread_labels = [b[0] for b in spread_bins]

    data = []
    for imb_label in imb_labels:
        row = []
        for s_label in spread_labels:
            vals = buckets.get((imb_label, s_label), [])
            if vals:
                row.append(sum(vals) / len(vals))
            else:
                row.append(0)
        data.append(row)

    # Find max abs value for symmetric colormap
    flat = [v for row in data for v in row if v != 0]
    vmax = max(abs(v) for v in flat) if flat else 0.1

    return _render_heatmap(
        data=data,
        row_labels=imb_labels,
        col_labels=spread_labels,
        title="Avg PnL ($) by Imbalance x Spread",
        xlabel="Spread (%)",
        ylabel="Imbalance",
        fmt=".3f",
        cmap="RdYlGn",
        vmin=-vmax,
        vmax=vmax,
        figsize=figsize,
        style=style,
    )


def heatmap_hour_weekday(
    trades: list[TradeRow],
    figsize: tuple = (14, 6),
    style: str = "dark_background",
) -> plt.Figure:
    """Trade count heatmap: hour x day of week."""
    days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    hours = list(range(24))

    buckets = defaultdict(int)
    for t in trades:
        buckets[(t.weekday, t.hour)] += 1

    data = []
    for d in range(7):
        row = []
        for h in hours:
            row.append(buckets.get((d, h), 0))
        data.append(row)

    max_count = max(max(row) for row in data) if data else 1

    return _render_heatmap(
        data=data,
        row_labels=days,
        col_labels=[str(h) for h in hours],
        title="Trade Count by Day x Hour",
        xlabel="Hour (UTC)",
        ylabel="Day of Week",
        fmt=".0f",
        cmap="YlOrRd",
        vmin=0,
        vmax=max_count,
        figsize=figsize,
        style=style,
    )


HEATMAP_FUNCTIONS = {
    "zone_hour": heatmap_zone_hour,
    "spread_imbalance": heatmap_spread_imbalance,
    "hour_weekday": heatmap_hour_weekday,
}

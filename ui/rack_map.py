"""
Rack map visualization for the Streamlit dashboard.

Each rack drawn as a grid: sections across the x-axis, rows up the y-axis,
row 1 (the floor) at the bottom - matching how the racks physically stand.

Accessibility choices:
  - Okabe-Ito colorblind-safe palette
  - Flag cells use a square-x symbol so Flag vs Move is distinguishable
    by shape, not just hue
  - Text color flips black/white per cell background for contrast

Hover on any cell shows every item inside: number, description, tier,
recommendation, yearly picks, and dollar value.
"""

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

STATUS_COLORS = {
    "Stay":      "#009E73",   # bluish green
    "Move":      "#E69F00",   # orange
    "Flag":      "#CC3311",   # vermillion + distinct symbol
    "unmatched": "#F0E442",   # yellow - item not in usage report
    "unknown":   "#9467bd",   # purple - unidentified stock
    "empty":     "#E8E8E8",   # light gray - available space
}
# black text on light backgrounds, white on dark
TEXT_COLORS = {
    "Stay": "white", "Move": "black", "Flag": "white",
    "unmatched": "black", "unknown": "white", "empty": "black",
}
STATUS_SYMBOLS = {s: ("square-x" if s == "Flag" else "square")
                  for s in STATUS_COLORS}
STATUS_PRIORITY = ["Flag", "Move", "Stay", "unmatched", "unknown", "empty"]
LEGEND_LABELS = {"unmatched": "No usage record", "unknown": "Unknown stock",
                 "empty": "Empty"}


def _cell_rollup(merged: pd.DataFrame, recs: pd.DataFrame) -> pd.DataFrame:
    """One row per physical cell: worst-case status, mix counts, hover text."""
    rec_map = (recs.set_index(["item_number", "current_cell"])["recommendation"]
               .to_dict()) if len(recs) else {}

    rows = []
    for (rack, section, row_num, cell_id), grp in merged.groupby(
            ["rack", "section", "row_num", "cell_id"]):
        statuses, lines = [], []
        occ_total = grp["occupancy"].max()
        for _, r in grp.iterrows():
            if r["match_status"] == "empty":
                statuses.append("empty")
                continue
            if r["match_status"] in ("unknown", "unmatched"):
                statuses.append(r["match_status"])
                lines.append("&#9888; UNKNOWN STOCK" if r["match_status"] == "unknown"
                             else f"{r['item_number']} - no usage record")
                continue
            status = rec_map.get((r["item_number"], cell_id), "Stay")
            statuses.append(status)
            desc = str(r.get("description", ""))[:34]
            lines.append(
                f"<b>{r['item_number']}</b> {desc}<br>"
                f"&nbsp;&nbsp;Tier {r['tier']} | {status} | "
                f"{int(r['yearly_usage'])}/yr | ${r['dollar_value']:,.0f}")

        worst = next((s for s in STATUS_PRIORITY if s in statuses), "empty")
        n_stay = statuses.count("Stay")
        n_total = len([s for s in statuses if s != "empty"])
        mix = (f" - {n_stay}/{n_total} items OK"
               if worst in ("Move", "Flag") and n_stay else "")
        rows.append({
            "rack": rack, "section": section, "row_num": int(row_num),
            "cell_id": cell_id, "status": worst,
            "occupancy": min(float(occ_total), 1.0),
            "hover": (f"<b>{cell_id}</b> ({occ_total:.0%} full){mix}<br>"
                      + ("<br>".join(lines) or "Empty - available space")),
        })
    return pd.DataFrame(rows)


def build_rack_figure(merged: pd.DataFrame, recs: pd.DataFrame,
                      mode: str = "recommendations") -> go.Figure:
    cells = _cell_rollup(merged, recs)
    racks = sorted(cells["rack"].unique())

    fig = make_subplots(rows=1, cols=len(racks), subplot_titles=racks,
                        shared_yaxes=True, horizontal_spacing=0.02)
    max_row = int(cells["row_num"].max())

    for i, rack in enumerate(racks, start=1):
        sub = cells[cells["rack"] == rack]
        sections = sorted(sub["section"].unique())
        sec_x = {s: j for j, s in enumerate(sections)}

        if mode == "utilization":
            colors = sub["occupancy"].apply(
                lambda v: f"rgba(204,51,17,{0.12 + 0.88 * v:.2f})")
            text_colors = sub["occupancy"].apply(
                lambda v: "white" if v > 0.55 else "black")
            symbols = "square"
            cell_text = sub["occupancy"].apply(lambda v: f"{v:.0%}")
        else:
            colors = sub["status"].map(STATUS_COLORS)
            text_colors = sub["status"].map(TEXT_COLORS)
            symbols = sub["status"].map(STATUS_SYMBOLS)
            cell_text = sub["section"] + sub["row_num"].astype(str)

        fig.add_trace(go.Scatter(
            x=sub["section"].map(sec_x), y=sub["row_num"],
            mode="markers+text",
            marker=dict(symbol=symbols, size=36, color=colors,
                        line=dict(width=1, color="#555555")),
            text=cell_text,
            textfont=dict(size=9, color=list(text_colors)),
            hovertext=sub["hover"], hoverinfo="text",
            showlegend=False,
        ), row=1, col=i)

        fig.update_xaxes(tickvals=list(sec_x.values()), ticktext=sections,
                         row=1, col=i, showgrid=False, zeroline=False)

    for c in range(1, len(racks) + 1):
        fig.update_yaxes(range=[0.4, max_row + 0.6], autorange=False,
                         tickvals=list(range(1, max_row + 1)),
                         showgrid=False, zeroline=False, row=1, col=c)
    fig.update_yaxes(title_text="Row (1 = floor)", row=1, col=1)

    if mode == "recommendations":
        for status in ["Stay", "Move", "Flag", "unmatched", "unknown", "empty"]:
            fig.add_trace(go.Scatter(
                x=[None], y=[None], mode="markers",
                marker=dict(symbol=STATUS_SYMBOLS[status], size=12,
                            color=STATUS_COLORS[status],
                            line=dict(width=1, color="#555555")),
                name=LEGEND_LABELS.get(status, status), showlegend=True))

    fig.update_layout(
        height=430, margin=dict(l=50, r=10, t=55, b=30),
        legend=dict(orientation="h", yanchor="bottom", y=-0.18),
        plot_bgcolor="white",
        hoverlabel=dict(bgcolor="white", font_size=12, align="left"),
    )
    return fig


# ---- API expected by app.py ----------------------------------------

def recommendation_map(merged, recs):
    """Rack map colored by recommendation status."""
    return build_rack_figure(merged, recs, mode="recommendations")


def utilization_heatmap(merged, recs):
    """Rack map shaded by cell occupancy."""
    return build_rack_figure(merged, recs, mode="utilization")

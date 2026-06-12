"""
New-facility layout map.

Same visual language as the existing rack map: racks side by side,
lettered columns on x, 7 rows on y with row 1 at the floor.

Colors (Okabe-Ito):
  A tier            #0072B2 blue
  A overstock (r4)  #56B4E9 light blue + diagonal-slash overlay
  B tier            #009E73 green
  C tier            #E69F00 orange
  CSHRWD hardwood   #8B6F47 brown/tan
  Empty             #E8E8E8 light gray
Cells needing manual review get a thick black border plus a warning
marker above the cell, layered over the tier color.

Cell labels show column+row (e.g. C4); the rack is the subplot title and
the full cell ID (N1-C-4) is in the hover, which also lists each item's
number, description, tier, current cell(s), and review reason.
"""

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

TIER_COLORS = {"A": "#0072B2", "A_os": "#56B4E9", "B": "#009E73",
               "C": "#E69F00", "hardwood": "#8B6F47", "empty": "#E8E8E8"}
TEXT_COLORS = {"A": "white", "A_os": "black", "B": "white",
               "C": "black", "hardwood": "white", "empty": "#888888"}


def _cell_rollup(assign: pd.DataFrame, cells: pd.DataFrame) -> pd.DataFrame:
    rows = []
    by_cell = assign.groupby("proposed_cell")
    for _, cell in cells.iterrows():
        cid = cell["cell_id"]
        if cid in by_cell.groups:
            grp = by_cell.get_group(cid)
            if grp["is_hardwood"].any():
                key = "hardwood"
            elif grp["is_overstock"].any():
                key = "A_os"
            else:
                key = grp.loc[grp["share"].idxmax(), "tier"]
            review = grp["review_flag"].any()
            lines = []
            for _, p in grp.iterrows():
                lines.append(
                    f"<b>{p['item_number']}</b> {str(p['description'])[:34]}<br>"
                    f"&nbsp;&nbsp;Tier {p['tier']}"
                    f"{' (overstock)' if p['is_overstock'] else ''} | "
                    f"{p['share']:.0%} of cell<br>"
                    f"&nbsp;&nbsp;From: {p['current_cells']}"
                    + (f"<br>&nbsp;&nbsp;&#9888; {p['review_reason']}"
                       if p['review_reason'] else ""))
            occupied = round(grp["share"].sum(), 2)
            hover = (f"<b>{cid}</b> ({cell['width_ft']} ft, {occupied:.0%} full)"
                     f"<br>" + "<br>".join(lines))
        else:
            key, review = "empty", False
            hover = f"<b>{cid}</b> ({cell['width_ft']} ft)<br>Empty - available"
        rows.append({"rack": cell["rack"], "column": cell["column"],
                     "row": int(cell["row"]), "cell_id": cid, "key": key,
                     "review": review, "hover": hover})
    return pd.DataFrame(rows)


def build_new_layout_figure(assign: pd.DataFrame, cells: pd.DataFrame) -> go.Figure:
    grid = _cell_rollup(assign, cells)
    racks = sorted(grid["rack"].unique())

    # Proportional widths (racks have 5/6/6/4 columns) keep cells square
    # and prevent the rack-title annotation collision seen with
    # subplot_titles on unequal racks.
    ncols = [grid[grid["rack"] == r]["column"].nunique() for r in racks]
    widths = [n / sum(ncols) for n in ncols]

    fig = make_subplots(rows=1, cols=len(racks), column_widths=widths,
                        shared_yaxes=True, horizontal_spacing=0.025)
    max_row = int(grid["row"].max())

    for i, rack in enumerate(racks, start=1):
        sub = grid[grid["rack"] == rack]
        sections = sorted(sub["column"].unique())
        sec_x = {s: j for j, s in enumerate(sections)}
        xs = sub["column"].map(sec_x)

        fig.add_trace(go.Scatter(
            x=xs, y=sub["row"], mode="markers+text",
            marker=dict(symbol="square", size=36,
                        color=sub["key"].map(TIER_COLORS),
                        line=dict(
                            width=sub["review"].map({True: 3, False: 1}),
                            color=sub["review"].map(
                                {True: "black", False: "#555555"}))),
            text=sub["column"] + sub["row"].astype(str),
            textfont=dict(size=9, color=list(sub["key"].map(TEXT_COLORS))),
            hovertext=sub["hover"], hoverinfo="text", showlegend=False,
        ), row=1, col=i)

        # diagonal-slash overlay on A-overstock cells
        os_cells = sub[sub["key"] == "A_os"]
        if len(os_cells):
            fig.add_trace(go.Scatter(
                x=os_cells["column"].map(sec_x), y=os_cells["row"],
                mode="markers",
                marker=dict(symbol="line-ne", size=30,
                            line=dict(width=2, color="#0072B2")),
                hoverinfo="skip", showlegend=False,
            ), row=1, col=i)

        # warning marker above review cells
        warn = sub[sub["review"]]
        if len(warn):
            fig.add_trace(go.Scatter(
                x=warn["column"].map(sec_x), y=warn["row"] + 0.33,
                mode="text", text="&#9888;",
                textfont=dict(size=11, color="black"),
                hoverinfo="skip", showlegend=False,
            ), row=1, col=i)

        fig.update_xaxes(tickvals=list(sec_x.values()), ticktext=sections,
                         row=1, col=i, showgrid=False, zeroline=False)

    for c in range(1, len(racks) + 1):
        fig.update_yaxes(range=[0.4, max_row + 0.75], autorange=False,
                         tickvals=list(range(1, max_row + 1)),
                         showgrid=False, zeroline=False, row=1, col=c)
    fig.update_yaxes(title_text="Row (1 = floor)", row=1, col=1)

    # Rack titles anchored to each subplot's actual domain center
    for i, rack in enumerate(racks, start=1):
        xaxis = fig.layout[f"xaxis{i if i > 1 else ''}"]
        x0, x1 = xaxis.domain
        fig.add_annotation(x=(x0 + x1) / 2, y=1.04, xref="paper", yref="paper",
                           text=f"<b>{rack}</b>", showarrow=False,
                           font=dict(size=13), xanchor="center")

    # legend
    legend_items = [("A tier (rows 1-3)", TIER_COLORS["A"], "square", 1),
                    ("A overstock (row 4)", TIER_COLORS["A_os"], "square", 1),
                    ("B tier (rows 4-5)", TIER_COLORS["B"], "square", 1),
                    ("C tier (rows 6-7)", TIER_COLORS["C"], "square", 1),
                    ("CSHRWD hardwood", TIER_COLORS["hardwood"], "square", 1),
                    ("Empty", TIER_COLORS["empty"], "square", 1),
                    ("Needs manual review", "#ffffff", "square", 3)]
    for name, color, symbol, lw in legend_items:
        fig.add_trace(go.Scatter(
            x=[None], y=[None], mode="markers",
            marker=dict(symbol=symbol, size=12, color=color,
                        line=dict(width=lw, color="black" if lw > 1 else "#555555")),
            name=name, showlegend=True))

    fig.update_layout(
        height=440, margin=dict(l=50, r=10, t=55, b=30),
        legend=dict(orientation="h", yanchor="bottom", y=-0.22),
        plot_bgcolor="white",
        hoverlabel=dict(bgcolor="white", font_size=12, align="left"))
    return fig

"""
Layout consolidation analysis for the Rack Slotting Optimizer.

Andersen is consolidating cutstock storage from the current layout
(~147x13' + 35x9' = 2,226 ft) to a smaller future layout
(140x13' + 7x9' = 1,883 ft, horizontal configuration).

This module answers the core question: does current inventory FIT in the
future layout, and what has to come out first if not?

All math is in linear feet: an item's footprint = occupancy_share x cell
width. Cells with implausible widths (< 1 ft - data entry errors) are
treated as the default width and flagged.
"""

import pandas as pd

# Future layout defaults from the facility redesign plan (editable in UI)
FUTURE_LAYOUT = {
    "cells_13ft": 140,
    "cells_9ft": 7,
}

DEFAULT_CELL_WIDTH = 13.0


def clean_widths(audit: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Replace implausible cell widths with the default and return the
    cleaned frame plus a table of flagged cells for the review bucket."""
    df = audit.copy()
    bad = df["cell_width_ft"].isna() | (df["cell_width_ft"] < 1)
    flagged = df.loc[bad, ["cell_id", "cell_width_ft"]].drop_duplicates("cell_id")
    df.loc[bad, "cell_width_ft"] = DEFAULT_CELL_WIDTH
    return df, flagged


def future_capacity_ft(layout: dict | None = None) -> float:
    cfg = layout or FUTURE_LAYOUT
    return cfg["cells_13ft"] * 13.0 + cfg["cells_9ft"] * 9.0


def fit_analysis(merged: pd.DataFrame, recs: pd.DataFrame | None = None,
                 layout: dict | None = None) -> dict:
    """Compare current inventory footprint against future layout capacity.

    Returns a dict of headline numbers for the dashboard:
      current_capacity_ft   - audited storage available today
      occupied_ft           - linear feet of inventory right now
      future_capacity_ft    - the consolidation target
      headroom_ft           - future capacity minus current occupancy
      idle_ft / idle_value  - footprint and dollars of Flag items; removing
                              them is the first consolidation lever
      headroom_after_idle_ft- headroom if all flagged idle stock is removed
      fits                  - bool, does it fit as-is
      fits_after_idle       - bool, does it fit after idle removal
    """
    audit, _ = clean_widths(merged)

    cells = audit.groupby("cell_id").agg(
        occ=("occupancy_share", "sum"),
        width=("cell_width_ft", "first"))
    current_capacity = cells["width"].sum()
    occupied = (cells["occ"].clip(upper=1.0) * cells["width"]).sum()

    future_cap = future_capacity_ft(layout)

    idle_ft = 0.0
    idle_value = 0.0
    if recs is not None and not recs.empty:
        flags = recs[recs["recommendation"] == "Flag"]
        if not flags.empty:
            widths = audit.drop_duplicates("cell_id").set_index("cell_id")["cell_width_ft"]
            idle_ft = float((flags["occupancy"] *
                             flags["current_cell"].map(widths).fillna(DEFAULT_CELL_WIDTH)).sum())
            idle_value = float(flags["dollar_value"].sum())

    headroom = future_cap - occupied
    return {
        "current_capacity_ft": round(current_capacity, 1),
        "occupied_ft": round(occupied, 1),
        "future_capacity_ft": round(future_cap, 1),
        "reduction_ft": round(current_capacity - future_cap, 1),
        "headroom_ft": round(headroom, 1),
        "idle_ft": round(idle_ft, 1),
        "idle_value": round(idle_value, 2),
        "headroom_after_idle_ft": round(headroom + idle_ft, 1),
        "fits": headroom >= 0,
        "fits_after_idle": (headroom + idle_ft) >= 0,
    }

"""
Excel export for the Rack Slotting Optimizer.

Produces a workbook the supervisor can print or work from on the floor:
  Recommendations - full per-item table sorted by action priority
  Move List       - just the moves, in execution order (targets first
                    assigned = highest priority)
  Flagged Items   - idle stock review list with dollar values
  Needs Review    - unknown '?' cells and unmatched item numbers
"""

import io
import pandas as pd


def build_export(recs: pd.DataFrame, merged: pd.DataFrame,
                 fit: dict | None = None) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xl:
        cols = ["recommendation", "item_number", "description", "tier", "role",
                "current_cell", "target_cell", "yearly_usage", "on_hand",
                "dollar_value", "reason"]
        full = recs.copy()
        order = {"Flag": 0, "Move": 1, "Stay": 2}
        full = full.sort_values(["recommendation", "yearly_usage"],
                                key=lambda s: s.map(order) if s.name == "recommendation" else s,
                                ascending=[True, False])
        full[[c for c in cols if c in full.columns]].to_excel(
            xl, sheet_name="Recommendations", index=False)

        moves = recs[recs["recommendation"] == "Move"]
        moves[[c for c in cols if c in moves.columns]].to_excel(
            xl, sheet_name="Move List", index=False)

        flags = recs[recs["recommendation"] == "Flag"].sort_values(
            "dollar_value", ascending=False)
        flags[[c for c in cols if c in flags.columns]].to_excel(
            xl, sheet_name="Flagged Items", index=False)

        review_unknown = (merged[merged["match_status"] == "unknown"]
                          [["cell_id", "location_label", "occupancy_share", "notes"]]
                          .drop_duplicates())
        review_unmatched = (merged[merged["match_status"] == "unmatched"]
                            [["item_number", "cell_id", "location_label", "occupancy_share"]]
                            .drop_duplicates())
        review_unknown.to_excel(xl, sheet_name="Needs Review", index=False,
                                startrow=1)
        review_unmatched.to_excel(xl, sheet_name="Needs Review", index=False,
                                  startrow=len(review_unknown) + 5)

        if fit:
            pd.DataFrame([fit]).T.rename(columns={0: "value"}).to_excel(
                xl, sheet_name="Layout Fit")
    return buf.getvalue()

"""Comprehensive Excel export for analysis, implementation, and Finance review."""
import io
import pandas as pd


def build_export(decisions, recs, merged, fit, review_summary=None, new_layout=None):
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xl:
        summary = pd.DataFrame([
            {"Metric": "Current rack capacity (LF)", "Value": fit.get("current_capacity_ft")},
            {"Metric": "New-layout capacity (LF)", "Value": fit.get("future_capacity_ft")},
            {"Metric": "Capacity reduction (LF)", "Value": fit.get("reduction_ft")},
            {"Metric": "Current occupied footprint (LF)", "Value": fit.get("occupied_ft")},
            {"Metric": "Fits in new layout", "Value": fit.get("fits_after_idle")},
            {"Metric": "Total inventory value under review", "Value": decisions["excess_value"].sum()},
        ])
        summary.to_excel(xl, sheet_name="Executive Summary", index=False)
        if review_summary is not None:
            review_summary.to_excel(xl, sheet_name="Executive Summary", index=False, startrow=len(summary) + 3)

        preferred = ["item_number", "description", "tier", "yearly_usage", "safety_stock",
                     "on_hand", "retention_ceiling", "recommended_keep_qty", "excess_qty",
                     "standard_cost", "excess_value", "review_group", "quantity_action",
                     "slotting_action", "current_locations", "suggested_locations", "decision_reason"]
        decisions[[c for c in preferred if c in decisions]].to_excel(
            xl, sheet_name="All Recommendations", index=False)

        for prefix, name in [("P7", "P7 Zero Usage"), ("P8", "P8 Active Overstock"),
                             ("P9", "P9 Safety Stock Adj")]:
            view = decisions[decisions["review_group"].str.startswith(prefix)]
            view[[c for c in preferred if c in view]].to_excel(xl, sheet_name=name, index=False)

        moves = decisions[decisions["slotting_action"].str.contains("Move|Re-slot", case=False, na=False)]
        moves[[c for c in preferred if c in moves]].to_excel(xl, sheet_name="Move List", index=False)

        unknown = merged[merged["match_status"].isin(["unknown", "unmatched"])].copy()
        unknown.to_excel(xl, sheet_name="Manual Review", index=False)
        if new_layout is not None and len(new_layout):
            new_layout.to_excel(xl, sheet_name="New Layout", index=False)

        for ws in xl.book.worksheets:
            ws.freeze_panes = "A2"
            ws.auto_filter.ref = ws.dimensions
            for col in ws.columns:
                width = min(max(len(str(c.value or "")) for c in col) + 2, 45)
                ws.column_dimensions[col[0].column_letter].width = width
    return buf.getvalue()

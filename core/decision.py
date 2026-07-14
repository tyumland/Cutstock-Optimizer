"""Inventory disposition and finance-review logic.

Separates quantity/disposition decisions from physical slotting decisions.
The default policy mirrors the validated Cutstock review: retain up to 2x
updated safety stock; route zero-usage, material active overstock, and smaller
active excess into separate review groups.
"""
from __future__ import annotations
import numpy as np
import pandas as pd


def build_inventory_decisions(
    classified: pd.DataFrame,
    retention_multiplier: float = 2.0,
    p8_min_excess_units: float = 100.0,
    p8_min_excess_value: float = 0.0,
    low_usage_threshold: float = 0.0,
) -> pd.DataFrame:
    df = classified.copy()
    for col in ["yearly_usage", "safety_stock", "on_hand", "standard_cost"]:
        if col not in df:
            df[col] = 0.0
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)

    df["retention_ceiling"] = (df["safety_stock"] * float(retention_multiplier)).round(2)
    df["recommended_keep_qty"] = np.minimum(df["on_hand"], df["retention_ceiling"]).round(2)
    # Active items with zero safety stock remain a review case rather than silently retained.
    df["excess_qty"] = (df["on_hand"] - df["recommended_keep_qty"]).clip(lower=0).round(2)
    df["excess_value"] = (df["excess_qty"] * df["standard_cost"]).round(2)
    df["retained_value"] = (df["recommended_keep_qty"] * df["standard_cost"]).round(2)

    zero_usage = (df["yearly_usage"] <= 0) & (df["on_hand"] > 0) & (df["excess_qty"] > 0)
    low_usage = (df["yearly_usage"] > 0) & (df["yearly_usage"] <= float(low_usage_threshold))
    active_excess = (df["yearly_usage"] > 0) & (df["excess_qty"] > 0)
    unit_material = (df["excess_qty"] >= float(p8_min_excess_units)) if p8_min_excess_units > 0 else pd.Series(False, index=df.index)
    value_material = (df["excess_value"] >= float(p8_min_excess_value)) if p8_min_excess_value > 0 else pd.Series(False, index=df.index)
    material = unit_material | value_material

    df["review_group"] = "Retain - No Immediate Finance Action"
    df.loc[df["on_hand"] <= 0, "review_group"] = "Not Stocked / No On-Hand"
    df.loc[active_excess & ~material, "review_group"] = "P9 - Safety-Stock Readjustment"
    df.loc[active_excess & material, "review_group"] = "P8 - Active Overstock Review"
    df.loc[zero_usage, "review_group"] = "P7 - Zero-Usage Review"
    df.loc[low_usage & (df["excess_qty"] > 0) & ~zero_usage, "review_group"] = (
        "P8 - Active Overstock Review" if p8_min_excess_units <= 0 else df.loc[
            low_usage & (df["excess_qty"] > 0) & ~zero_usage, "review_group"]
    )

    df["quantity_action"] = "Keep"
    df.loc[df["excess_qty"] > 0, "quantity_action"] = "Reduce Quantity and Keep"
    df.loc[zero_usage & (df["recommended_keep_qty"] <= 0), "quantity_action"] = "Review Full Quantity for Removal"
    df.loc[zero_usage & (df["recommended_keep_qty"] > 0), "quantity_action"] = "Review Excess for Removal"
    df.loc[df["on_hand"] <= 0, "quantity_action"] = "No Floor Stock"

    def reason(r):
        if r["on_hand"] <= 0:
            return "No on-hand quantity; no current rack space required."
        if r["review_group"].startswith("P7"):
            return (f"Zero recorded yearly usage. Keep ceiling is {r['retention_ceiling']:,.0f}; "
                    f"review {r['excess_qty']:,.0f} units (${r['excess_value']:,.0f}) for removal from the new layout.")
        if r["review_group"].startswith("P8"):
            return (f"Active item with material excess above the selected {retention_multiplier:g}x safety-stock policy. "
                    f"Keep {r['recommended_keep_qty']:,.0f}; review {r['excess_qty']:,.0f} units (${r['excess_value']:,.0f}).")
        if r["review_group"].startswith("P9"):
            return (f"Smaller active excess. Keep {r['recommended_keep_qty']:,.0f}; use the remaining "
                    f"{r['excess_qty']:,.0f} units (${r['excess_value']:,.0f}) to review safety stock and replenishment settings.")
        return f"Within the selected {retention_multiplier:g}x safety-stock retention ceiling."

    df["decision_reason"] = df.apply(reason, axis=1)
    return df


def merge_slotting_actions(decisions: pd.DataFrame, recs: pd.DataFrame) -> pd.DataFrame:
    """Create one understandable item-level action from quantity and cell decisions."""
    d = decisions.copy()
    if recs is None or recs.empty:
        d["slotting_action"] = "Not audited"
        d["current_locations"] = ""
        d["suggested_locations"] = ""
        return d

    r = recs.copy()
    agg = r.groupby("item_number", as_index=False).agg(
        current_locations=("current_cell", lambda s: ", ".join(sorted(set(map(str, s.dropna()))))),
        suggested_locations=("target_cell", lambda s: ", ".join(sorted(set(map(str, s.dropna()))))),
        move_records=("recommendation", lambda s: int((s == "Move").sum())),
        stay_records=("recommendation", lambda s: int((s == "Stay").sum())),
    )
    d = d.merge(agg, on="item_number", how="left")
    d["slotting_action"] = "Not found in physical audit"
    audited = d["current_locations"].fillna("").ne("")
    d.loc[audited, "slotting_action"] = "Keep in Current Location"
    d.loc[audited & (d["move_records"].fillna(0) > 0), "slotting_action"] = "Move / Re-slot Retained Quantity"
    d.loc[d["review_group"].str.startswith("P7"), "slotting_action"] = "Review for Removal"
    d.loc[d["review_group"].str.startswith("P8"), "slotting_action"] = "Reduce Quantity; Move Retained Stock as Needed"
    d.loc[d["review_group"].str.startswith("P9"), "slotting_action"] = "Adjust Safety Stock; Keep Retained Quantity"
    return d


def review_summary(decisions: pd.DataFrame) -> pd.DataFrame:
    return (decisions.groupby("review_group", as_index=False)
            .agg(items=("item_number", "nunique"),
                 excess_units=("excess_qty", "sum"),
                 review_value=("excess_value", "sum"))
            .sort_values("review_value", ascending=False))

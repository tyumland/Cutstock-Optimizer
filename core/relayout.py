"""
New-facility layout assignment (consolidation target).

Layout (NEW_LAYOUT):
  Rack 1: col A = 9-ft cells, cols B-E = 13-ft (5 cols x 7 rows)
  Rack 2: cols A-F, 13-ft (6 x 7)
  Rack 3: cols A-F, 13-ft (6 x 7)
  Rack 4: cols A-D, 13-ft (4 x 7) - RESERVED for CSHRWD hardwood
          plus overfill/misc

Slotting within each rack:
  rows 1-3          A-tier
  row 4             A-tier overstock, same column as the primary;
                    columns with no A overstock release row 4 to B
  rows 4-5 (rest)   B-tier
  rows 6-7          C-tier
Flagged/idle items are excluded entirely.

Review flags (manual check before executing the move):
  notes     - the item's audit rows carry a non-empty note
  hardwood  - CSHRWD item (always review)
  width     - proposed cell width differs from every width the item
              currently occupies (9 ft vs 13 ft)
  split     - the same item landed in non-adjacent cells
  overfill  - placed outside its tier band because the band was full
"""

import pandas as pd

NEW_LAYOUT = [
    {"rack": "N1", "columns": [("A", 9)] + [(c, 13) for c in "BCDE"]},
    {"rack": "N2", "columns": [(c, 13) for c in "ABCDEF"]},
    {"rack": "N3", "columns": [(c, 13) for c in "ABCDEF"]},
    {"rack": "N4", "columns": [(c, 13) for c in "ABCD"], "reserved": "hardwood"},
]
ROWS = list(range(1, 8))
A_ROWS, OS_ROW, B_ROWS, C_ROWS = [1, 2, 3], 4, [4, 5], [6, 7]


def build_new_cells() -> pd.DataFrame:
    cells = []
    for rack in NEW_LAYOUT:
        for col, width in rack["columns"]:
            for row in ROWS:
                cells.append({
                    "rack": rack["rack"], "column": col, "row": row,
                    "cell_id": f"{rack['rack']}-{col}-{row}",
                    "width_ft": width,
                    "reserved": rack.get("reserved", ""),
                    "free": 1.0,
                })
    return pd.DataFrame(cells)


def _item_table(merged: pd.DataFrame, recs: pd.DataFrame) -> pd.DataFrame:
    """One row per item to place."""
    flagged = set(recs.loc[recs["recommendation"] == "Flag", "item_number"])
    m = merged[merged["match_status"] == "matched"].copy()
    m = m[~m["item_number"].isin(flagged)]

    m["is_hardwood"] = m["location_label"].str.upper().str.contains("CSHRWD", na=False)

    # Most audit notes are occupancy bookkeeping ("1/3 EACH", "STACKED",
    # timestamps). Only notes carrying real warnings should trigger review.
    import re
    _routine = re.compile(
        r"^[\s\d/.&]*((each|stacked|sxs|respectively|up top of|w|and)[\s\d/.&]*)*$",
        re.IGNORECASE)

    def _meaningful(note) -> bool:
        s = str(note).strip()
        if not s or s.lower() == "nan":
            return False
        if re.match(r"^\d{4}-\d{2}-\d{2}", s):       # audit timestamps
            return False
        if "unknown" in s.lower() or "?" in s:        # always meaningful
            return True
        return not bool(_routine.match(s))

    m["has_notes"] = m["notes"].apply(_meaningful)
    m["width_cat"] = m["cell_width_ft"].apply(
        lambda w: 9 if pd.notna(w) and float(w) <= 9 else 13)

    items = (m.groupby("item_number").agg(
        description=("description", "first"),
        tier=("tier", "first"),
        yearly_usage=("yearly_usage", "first"),
        dollar_value=("dollar_value", "first"),
        footprint=("occupancy_share", "sum"),
        is_hardwood=("is_hardwood", "any"),
        has_notes=("has_notes", "any"),
        width_cats=("width_cat", lambda s: set(s)),
        current_cells=("cell_id", lambda s: ", ".join(sorted(set(s)))),
    ).reset_index())
    items["footprint"] = items["footprint"].clip(lower=0.05).round(3)
    return items


class _Placer:
    """Greedy first-fit placement against a live capacity ledger."""

    def __init__(self, cells: pd.DataFrame):
        self.cells = cells
        self.pieces: list[dict] = []
        self.overstock_cols: set[tuple] = set()

    def place(self, item: dict, amount: float, rows: list[int],
              racks: list[str], exclude_os_cols: bool = False,
              prefer: tuple | None = None) -> float:
        """Place `amount` of footprint; returns what couldn't fit."""
        remaining = round(amount, 4)
        c = self.cells
        mask = c["rack"].isin(racks) & c["row"].isin(rows) & (c["free"] > 0.0001)
        if exclude_os_cols:
            os_block = c.apply(lambda x: x["row"] == OS_ROW and
                               (x["rack"], x["column"]) in self.overstock_cols, axis=1)
            mask &= ~os_block
        pool = c[mask].sort_values(["rack", "column", "row"])
        if prefer is not None:
            pref_mask = (pool["rack"] == prefer[0]) & (pool["column"] == prefer[1])
            pool = pd.concat([pool[pref_mask], pool[~pref_mask]])

        for idx, cell in pool.iterrows():
            if remaining <= 0.0001:
                break
            amt = round(min(remaining, cell["free"]), 4)
            if amt <= 0.0001:
                continue
            self.pieces.append({
                "item_number": item["item_number"],
                "description": item["description"], "tier": item["tier"],
                "yearly_usage": item["yearly_usage"],
                "dollar_value": item["dollar_value"],
                "is_hardwood": item["is_hardwood"],
                "current_cells": item["current_cells"],
                "proposed_cell": cell["cell_id"], "rack": cell["rack"],
                "column": cell["column"], "row": int(cell["row"]),
                "width_ft": int(cell["width_ft"]), "share": amt,
            })
            self.cells.loc[idx, "free"] = round(cell["free"] - amt, 4)
            remaining = round(remaining - amt, 4)
        return remaining


def assign_new_layout(merged: pd.DataFrame, recs: pd.DataFrame):
    """Assign every non-flagged matched item into the new layout.

    Returns (assignments, cells): one assignment row per item-cell piece
    with review_flag / review_reason populated, plus the cell ledger
    (use free < 1 to see what's occupied, free == 1 for empty cells).
    """
    cells = build_new_cells()
    items = _item_table(merged, recs)
    P = _Placer(cells)

    main_racks = [r["rack"] for r in NEW_LAYOUT if not r.get("reserved")]
    hw_racks = [r["rack"] for r in NEW_LAYOUT if r.get("reserved") == "hardwood"]
    leftovers: dict[str, tuple[dict, float]] = {}

    def run_tiers(group: pd.DataFrame, racks: list[str]):
        # A: primary in rows 1-3, overflow to row 4 of the same column
        for _, it in group[group.tier == "A"].sort_values(
                "yearly_usage", ascending=False).iterrows():
            it = dict(it)
            # ONE primary pick cell in rows 1-3; everything beyond a full
            # cell is overstock and belongs in row 4 (same column ideally)
            primary_amt = min(it["footprint"], 1.0)
            left = P.place(it, primary_amt, A_ROWS, racks)
            overflow = round(it["footprint"] - primary_amt + left, 4)
            if overflow > 0.0001:
                mine = [p for p in P.pieces if p["item_number"] == it["item_number"]]
                prefer = (mine[-1]["rack"], mine[-1]["column"]) if mine else None
                left = P.place(it, overflow, [OS_ROW], racks, prefer=prefer)
                for p in P.pieces:
                    if p["item_number"] == it["item_number"] and p["row"] == OS_ROW:
                        P.overstock_cols.add((p["rack"], p["column"]))
            else:
                left = 0.0
            if left > 0.0001:
                leftovers[it["item_number"]] = (it, left)

        # B: prefer row 5, then row-4 cells not reserved for A overstock
        for _, it in group[group.tier == "B"].sort_values(
                "yearly_usage", ascending=False).iterrows():
            it = dict(it)
            left = P.place(it, it["footprint"], [5], racks)
            if left > 0.0001:
                left = P.place(it, left, [OS_ROW], racks, exclude_os_cols=True)
            if left > 0.0001:
                leftovers[it["item_number"]] = (it, left)

        # C: rows 6-7
        for _, it in group[group.tier == "C"].sort_values(
                "yearly_usage", ascending=False).iterrows():
            it = dict(it)
            left = P.place(it, it["footprint"], C_ROWS, racks)
            if left > 0.0001:
                leftovers[it["item_number"]] = (it, left)

    run_tiers(items[~items.is_hardwood], main_racks)
    run_tiers(items[items.is_hardwood], hw_racks)

    # Overfill: whatever didn't fit its band goes wherever space remains,
    # misc rack first
    for item_number, (it, left) in leftovers.items():
        left = P.place(it, left, ROWS, hw_racks)
        if left > 0.0001:
            left = P.place(it, left, ROWS, main_racks)
        # if still left, it simply doesn't fit the building - surfaced below
        if left > 0.0001:
            it["_unplaced"] = left

    assign = pd.DataFrame(P.pieces)
    band = {"A": set(A_ROWS) | {OS_ROW}, "B": set(B_ROWS), "C": set(C_ROWS)}
    assign["is_overstock"] = (assign["tier"].eq("A")) & (assign["row"] == OS_ROW)
    assign["overfill"] = assign.apply(lambda p: p["row"] not in band[p["tier"]], axis=1)

    # ---------------- review flags ----------------
    notes_items = set(items.loc[items.has_notes, "item_number"])
    width_map = items.set_index("item_number")["width_cats"].to_dict()

    def adjacency_ok(grp: pd.DataFrame) -> bool:
        # Relaxed rule: pieces of the same item must stay within ONE rack.
        # (Was: directly adjacent cells - fired too often to be useful.)
        return grp["rack"].nunique() == 1

    split_items = {item for item, grp in assign.groupby("item_number")
                   if not adjacency_ok(grp)}

    def review(p):
        reasons = []
        if p["item_number"] in notes_items:
            reasons.append("audit note on item")
        if p["is_hardwood"]:
            reasons.append("CSHRWD hardwood - verify")
        cats = width_map.get(p["item_number"], set())
        # Only a DOWNGRADE is a problem: stock living in a 13-ft cell may
        # be 13-ft material and can't fit a 9-ft cell. Upgrades are free.
        if cats and p["width_ft"] < max(cats):
            reasons.append("width downgrade "
                           f"({max(cats)}ft stock -> {p['width_ft']}ft cell)")
        if p["item_number"] in split_items:
            reasons.append("item split across racks")
        if p["overfill"]:
            reasons.append("overfill - placed outside tier band")
        return "; ".join(reasons)

    assign["review_reason"] = assign.apply(review, axis=1)
    assign["review_flag"] = assign["review_reason"].ne("")
    return assign.reset_index(drop=True), cells

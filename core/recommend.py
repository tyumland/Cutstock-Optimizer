"""
Recommendation engine for the Rack Slotting Optimizer.

For every item-cell record, outputs one of:
  Stay  - item's tier matches its current row band
  Move  - tier doesn't match; a specific target cell is suggested
  Flag  - item is idle (occupying space but not being picked)

Slotting rules (configurable in racks.yaml):
  A items      -> rows 1-3 (floor = row 1, most accessible)
  A overstock  -> row 4, same SECTION as the item's primary location
  B items      -> rows 4-5, but only row-4 cells not reserved for A overstock
  C items      -> rows 6-7

Overstock detection: if an A item occupies multiple cells, the cell in
rows 1-3 (or the lowest row if none) is its PRIMARY; every other cell
holding that item is overstock.

Target cell selection is greedy in priority order (A primary -> A
overstock -> B -> C, highest yearly usage first) against a live capacity
ledger, so two moves never get pointed at the same free space. Preference
order for targets: same section, then same rack, then anywhere.

Flag takes precedence over Move - there's no point relocating an item
that should be reviewed for removal.
"""

import pandas as pd

TIER_ROWS = {"A": [1, 2, 3], "A_overstock": [4], "B": [4, 5], "C": [6, 7]}


def _build_capacity_ledger(merged: pd.DataFrame) -> pd.DataFrame:
    """One row per physical cell with current free capacity (cell size = 1.0)."""
    cells = (
        merged.groupby(["rack", "section", "row_num", "cell_id"], as_index=False)
        .agg(occupied=("occupancy_share", "sum"),
             has_unknown=("is_unknown", "any"))
    )
    # Empty-cell records carry occupancy 0, so this works for them too
    cells["free"] = (1.0 - cells["occupied"]).clip(lower=0).round(4)
    return cells


def _find_target(ledger: pd.DataFrame, needed: float, allowed_rows: list[int],
                 prefer_section: str | None, prefer_rack: str | None) -> str | None:
    """Pick the best cell with enough free space in the allowed rows.

    Preference: same section -> same rack -> anywhere; within each group,
    most-free cell first (consolidates space).
    """
    cand = ledger[(ledger["row_num"].isin(allowed_rows)) & (ledger["free"] >= needed)]
    if cand.empty:
        return None
    for mask in (
        (cand["rack"].eq(prefer_rack) & cand["section"].eq(prefer_section)) if prefer_section else None,
        cand["rack"].eq(prefer_rack) if prefer_rack else None,
        pd.Series(True, index=cand.index),
    ):
        if mask is None:
            continue
        pool = cand[mask]
        if not pool.empty:
            return pool.sort_values("free", ascending=False).iloc[0]["cell_id"]
    return None


def generate_recommendations(merged: pd.DataFrame, rules: dict | None = None,
                             row_tolerance: int = 0) -> pd.DataFrame:
    """Produce the per-item recommendation table.

    `merged` is the output of join_audit_to_usage() on classified usage data
    (must include tier and idle_flag columns for matched items).

    row_tolerance: rows of slack before a misplacement becomes a Move.
      0 = strict (item must be exactly in its band)
      1 = incremental (one row outside the band counts as Stay)
      2 = relaxed
    Every Move also gets a move_priority (High/Medium/Low) based on how far
    out of band it sits and whether it's an A item, so the floor can work
    the list incrementally even in strict mode.
    """
    tier_rows = {k: rules.get(k, v) for k, v in TIER_ROWS.items()} if rules else dict(TIER_ROWS)

    ledger = _build_capacity_ledger(merged)
    ledger = ledger.set_index("cell_id", drop=False)

    work = merged[merged["match_status"] == "matched"].copy()

    # ---- Identify each A item's primary cell; everything else is overstock
    work["role"] = "single"
    for item, grp in work[work["tier"] == "A"].groupby("item_number"):
        if len(grp) == 1:
            continue
        in_band = grp[grp["row_num"].isin(tier_rows["A"])]
        primary_idx = (in_band if not in_band.empty else grp).sort_values("row_num").index[0]
        work.loc[grp.index, "role"] = "overstock"
        work.loc[primary_idx, "role"] = "primary"

    # ---- Row band each record SHOULD be in
    def allowed(row):
        if row["tier"] == "A":
            return tier_rows["A_overstock"] if row["role"] == "overstock" else tier_rows["A"]
        return tier_rows[row["tier"]]

    work["allowed_rows"] = work.apply(allowed, axis=1)
    work["band_distance"] = work.apply(
        lambda r: min(abs(r["row_num"] - ar) for ar in r["allowed_rows"]), axis=1)
    work["in_band"] = work["band_distance"] <= row_tolerance

    # ---- Assignment priority: A primary, A overstock, B, C; busiest first
    prio = {"A": 0, "B": 2, "C": 3}
    work["prio"] = work.apply(
        lambda r: 1 if (r["tier"] == "A" and r["role"] == "overstock") else prio[r["tier"]], axis=1)
    work = work.sort_values(["prio", "yearly_usage"], ascending=[True, False])

    # Sections whose row-4 cells get reserved for A overstock (B items must avoid)
    overstock_sections: set[tuple] = set(
        work.loc[(work["tier"] == "A") & (work["role"] == "overstock"), ["rack", "section"]]
        .itertuples(index=False, name=None)
    )

    recs = []
    for _, r in work.iterrows():
        rec = {
            "item_number": r["item_number"], "description": r.get("description"),
            "tier": r["tier"], "role": r["role"], "current_cell": r["cell_id"],
            "current_row": r["row_num"], "yearly_usage": r["yearly_usage"],
            "on_hand": r["on_hand"], "dollar_value": r["dollar_value"],
            "occupancy": r["occupancy_share"],
            "recommendation": None, "target_cell": None, "reason": None,
            "move_priority": None,
        }

        if r.get("idle_flag", False):
            rec["recommendation"] = "Flag"
            rec["reason"] = ("Idle: zero picks all year with stock on hand "
                             f"(${r['dollar_value']:,.2f} sitting). Review for removal "
                             "or relocate to rows 6-7.")
            recs.append(rec)
            continue

        if r["in_band"]:
            rec["recommendation"] = "Stay"
            rec["reason"] = (
                f"{r['tier']} item correctly placed in row {r['row_num']}."
                if r["band_distance"] == 0 else
                f"{r['tier']} item in row {r['row_num']}, {r['band_distance']} row(s) "
                "outside its band - within tolerance, leave it.")
            recs.append(rec)
            continue

        # Priority: A items and far-out-of-band placements first
        dist = r["band_distance"]
        if r["tier"] == "A" or dist >= 3:
            rec["move_priority"] = "High"
        elif dist == 2:
            rec["move_priority"] = "Medium"
        else:
            rec["move_priority"] = "Low"

        # ---- Needs to move: find a target and update the ledger
        allowed_rows = r["allowed_rows"]
        if r["tier"] == "B":
            # B can use row 5 freely, row 4 only outside overstock-reserved sections
            row4_ok = ledger[(ledger["row_num"] == 4)].apply(
                lambda c: (c["rack"], c["section"]) not in overstock_sections, axis=1)
            usable = ledger[(ledger["row_num"] == 5) |
                            ((ledger["row_num"] == 4) & row4_ok)]
        else:
            usable = ledger

        target = _find_target(usable, r["occupancy_share"], allowed_rows,
                              prefer_section=r["section"], prefer_rack=r["rack"])

        rec["recommendation"] = "Move"
        if target:
            rec["target_cell"] = target
            rec["reason"] = (f"{r['tier']} {'overstock' if r['role']=='overstock' else 'item'} "
                             f"in row {r['row_num']}; belongs in rows "
                             f"{'-'.join(map(str, allowed_rows))}. Suggested: {target}.")
            # Update ledger: free space at target shrinks, current cell frees up
            ledger.loc[target, "free"] = round(ledger.loc[target, "free"] - r["occupancy_share"], 4)
            if r["cell_id"] in ledger.index:
                ledger.loc[r["cell_id"], "free"] = round(
                    min(1.0, ledger.loc[r["cell_id"], "free"] + r["occupancy_share"]), 4)
        else:
            rec["reason"] = (f"Belongs in rows {'-'.join(map(str, allowed_rows))} but no cell "
                             "currently has enough free space. Re-run after executing other moves.")
        recs.append(rec)

    recs = pd.DataFrame(recs)

    # ---- Cascade passes: executing wave-1 moves frees cells, which can
    # unblock moves that initially had no target. Repeat until no progress.
    for _ in range(5):
        blocked = recs[(recs["recommendation"] == "Move") & recs["target_cell"].isna()]
        if blocked.empty:
            break
        progress = False
        for idx, r in blocked.iterrows():
            row_src = work[work["cell_id"].eq(r["current_cell"]) &
                           work["item_number"].eq(r["item_number"])]
            allowed_rows = row_src.iloc[0]["allowed_rows"] if not row_src.empty else []
            if r["tier"] == "B":
                row4_ok = ledger[(ledger["row_num"] == 4)].apply(
                    lambda c: (c["rack"], c["section"]) not in overstock_sections, axis=1)
                usable = ledger[(ledger["row_num"] == 5) |
                                ((ledger["row_num"] == 4) & row4_ok)]
            else:
                usable = ledger
            sec = row_src.iloc[0]["section"] if not row_src.empty else None
            rck = row_src.iloc[0]["rack"] if not row_src.empty else None
            target = _find_target(usable, r["occupancy"], allowed_rows,
                                  prefer_section=sec, prefer_rack=rck)
            if target:
                recs.loc[idx, "target_cell"] = target
                recs.loc[idx, "reason"] = (
                    f"{r['tier']} item; belongs in rows "
                    f"{'-'.join(map(str, allowed_rows))}. Suggested: {target} "
                    "(opens up after earlier moves are executed).")
                ledger.loc[target, "free"] = round(ledger.loc[target, "free"] - r["occupancy"], 4)
                if r["current_cell"] in ledger.index:
                    ledger.loc[r["current_cell"], "free"] = round(
                        min(1.0, ledger.loc[r["current_cell"], "free"] + r["occupancy"]), 4)
                progress = True
        if not progress:
            break

    return recs


def suggest_unknown_links(merged: pd.DataFrame, max_candidates: int = 5) -> pd.DataFrame:
    """For each unknown '?' cell, list candidate item numbers from the usage
    report that were never found in the physical audit - ranked by dollar
    value on hand (the most likely things to be sitting unidentified).
    Manual review decides the actual link.
    """
    audited = set(merged.loc[merged["match_status"] == "matched", "item_number"])
    usage_cols = ["item_number", "description", "tier", "on_hand", "dollar_value"]
    pool = (merged.drop_duplicates("item_number")
            if "description" in merged.columns else merged)

    # Rebuild the unaudited pool from columns present on matched rows
    # (caller can also pass items_not_audited() output via merge if preferred)
    unknown_cells = (merged[merged["match_status"] == "unknown"]
                     [["cell_id", "location_label", "occupancy", "notes"]]
                     .drop_duplicates("cell_id"))
    return unknown_cells.reset_index(drop=True)


def recommendation_summary(recs: pd.DataFrame) -> pd.DataFrame:
    """Rollup for dashboard summary cards."""
    return (recs.groupby("recommendation")
            .agg(items=("item_number", "count"),
                 total_value=("dollar_value", "sum"))
            .round(2).reset_index())

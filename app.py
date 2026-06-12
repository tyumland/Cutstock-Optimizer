"""
Rack Slotting Optimizer - Streamlit dashboard.

Run with:
    streamlit run app.py

Workflow for the supervisor:
  1. Upload the monthly usage report (.xls/.xlsx) and the physical audit (.xlsx)
  2. Review the KPI cards, rack map, and heatmap
  3. Work the Move List / Flagged Items tables
  4. Download the Excel export for the floor
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd
import streamlit as st

from core.ingest import (load_usage_report, load_physical_audit,
                         join_audit_to_usage, items_not_audited)
from core.classify import classify_abc, tier_summary
from core.recommend import generate_recommendations, recommendation_summary
from core.layout import fit_analysis, clean_widths, FUTURE_LAYOUT
from core.compare import (snapshot_label, save_snapshot, snapshot_idle_flags,
                          diff_against_previous, list_snapshots)
from ui.rack_map import recommendation_map, utilization_heatmap
from core.relayout import assign_new_layout
from ui.new_layout_map import build_new_layout_figure
from export import build_export

st.set_page_config(page_title="Rack Slotting Optimizer", layout="wide")
st.title("Cutstock Slotting")
st.caption("Upload this month's files, review the map, export the move list.")

# ---------------- Sidebar: uploads + settings ----------------
with st.sidebar:
    st.header("Monthly Upload")
    usage_file = st.file_uploader("Usage report (.xls / .xlsx)", type=["xls", "xlsx"])
    audit_file = st.file_uploader("Physical audit (.xlsx)", type=["xls", "xlsx"])

    tolerance_choice = st.radio(
        "Slotting strictness",
        ["Strict (exact band)", "Incremental (allow 1 row off)",
         "Relaxed (allow 2 rows off)"],
        index=1,
        help="Incremental treats items one row outside their band as "
             "'close enough' - recommended for working the list in waves.")
    row_tolerance = {"Strict (exact band)": 0,
                     "Incremental (allow 1 row off)": 1,
                     "Relaxed (allow 2 rows off)": 2}[tolerance_choice]

    with st.expander("Advanced settings", expanded=False):
        a_pct = st.slider("A-tier cumulative usage %", 50, 95, 80) / 100
        b_pct = st.slider("B-tier cumulative usage %", 80, 99, 95) / 100

        st.subheader("Monthly snapshot")
        snap_label = st.text_input("Snapshot label (year-month)", value=snapshot_label())
        idle_months = st.number_input("Idle after N consecutive snapshots",
                                      min_value=2, max_value=12, value=2,
                                      help="~30 days per snapshot; 2 = roughly 60 days")
        existing = list_snapshots()
        if existing:
            st.caption(f"Saved snapshots: {', '.join(existing)}")

        st.subheader("Future layout (consolidation target)")
        c13 = st.number_input("13-ft cells", value=FUTURE_LAYOUT["cells_13ft"], min_value=0)
        c9 = st.number_input("9-ft cells", value=FUTURE_LAYOUT["cells_9ft"], min_value=0)

if not (usage_file and audit_file):
    st.info("Upload both files in the sidebar to run the optimizer.")
    st.stop()


@st.cache_data(show_spinner="Reading files and generating recommendations...")
def run_pipeline(usage_bytes, usage_name, audit_bytes, audit_name, a, b,
                 label, n_idle, tol):
    up = Path("storage") / usage_name
    ap = Path("storage") / audit_name
    up.parent.mkdir(exist_ok=True)
    up.write_bytes(usage_bytes)
    ap.write_bytes(audit_bytes)

    usage = load_usage_report(str(up))
    audit = load_physical_audit(str(ap))
    classified = classify_abc(usage, a_pct=a, b_pct=b)
    # Upgrade idle flags to history-based logic when snapshots exist
    classified = snapshot_idle_flags(classified, label, min_snapshots=int(n_idle))
    merged = join_audit_to_usage(audit, classified)
    recs = generate_recommendations(merged, row_tolerance=tol)
    diff = diff_against_previous(recs, label)
    save_snapshot(classified, recs, label)
    return usage, audit, classified, merged, recs, diff


usage, audit, classified, merged, recs, diff = run_pipeline(
    usage_file.getvalue(), usage_file.name,
    audit_file.getvalue(), audit_file.name, a_pct, b_pct,
    snap_label, idle_months, row_tolerance)

fit = fit_analysis(merged, recs, {"cells_13ft": int(c13), "cells_9ft": int(c9)})

# ---------------- KPI cards ----------------
k1, k2, k3, k4, k5 = st.columns(5)
summary = recommendation_summary(recs).set_index("recommendation")
k1.metric("Stay", int(summary.loc["Stay", "items"]) if "Stay" in summary.index else 0)
k2.metric("Move", int(summary.loc["Move", "items"]) if "Move" in summary.index else 0)
k3.metric("Flagged idle", int(summary.loc["Flag", "items"]) if "Flag" in summary.index else 0,
          delta=f"-${summary.loc['Flag', 'total_value']:,.0f} sitting" if "Flag" in summary.index else None,
          delta_color="inverse")
k4.metric("Occupied (linear ft)", f"{fit['occupied_ft']:,.0f}")
k5.metric("Future layout headroom",
          f"{fit['headroom_ft']:,.0f} ft",
          delta=f"{fit['headroom_after_idle_ft']:,.0f} ft after idle removal")

st.download_button(
    "Export move list for the floor (Excel)",
    data=build_export(recs, merged, fit),
    file_name="rack_recommendations.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    type="primary")

# ---------------- Tabs ----------------
tab_map, tab_heat, tab_recs, tab_new, tab_changes, tab_fit, tab_review = st.tabs(
    ["Rack Map", "Utilization", "Recommendations", "New Layout", "Changes",
     "Layout Fit", "Needs Review"])

with tab_map:
    st.plotly_chart(recommendation_map(merged, recs), use_container_width=True)

with tab_heat:
    st.plotly_chart(utilization_heatmap(merged, recs), use_container_width=True)

with tab_recs:
    choice = st.radio("Show", ["All", "Move", "Flag", "Stay"], horizontal=True)
    table = recs if choice == "All" else recs[recs["recommendation"] == choice]
    if choice == "Move":
        prio = st.multiselect("Priority", ["High", "Medium", "Low"],
                              default=["High", "Medium", "Low"])
        table = table[table["move_priority"].isin(prio)]
    st.dataframe(
        table[["recommendation", "move_priority", "item_number", "description",
               "tier", "role", "current_cell", "target_cell", "yearly_usage",
               "on_hand", "dollar_value", "reason"]],
        use_container_width=True, height=480)

with tab_new:
    st.subheader("Proposed new-facility layout")
    assign_df, new_cells = assign_new_layout(merged, recs)
    st.plotly_chart(build_new_layout_figure(assign_df, new_cells),
                    use_container_width=True)
    n_items = assign_df["item_number"].nunique()
    n_review = assign_df.loc[assign_df["review_flag"], "item_number"].nunique()
    n_empty = int((new_cells["free"] >= 0.999).sum())
    c1, c2, c3 = st.columns(3)
    c1.metric("Items placed", n_items)
    c2.metric("Need manual review", n_review)
    c3.metric("Empty cells remaining", n_empty)

    st.markdown("**Assignments needing manual review**")
    reason_counts = (assign_df.loc[assign_df["review_flag"]]
                     .assign(reason=lambda d: d["review_reason"].str.split("; "))
                     .explode("reason")
                     .assign(rule=lambda d: d["reason"].str.split(" (", regex=False).str[0])
                     .groupby("rule")["item_number"].nunique()
                     .sort_values(ascending=False)
                     .rename("items affected").reset_index())
    st.dataframe(reason_counts, use_container_width=True, hide_index=True)
    st.dataframe(
        assign_df[assign_df["review_flag"]]
        [["item_number", "description", "tier", "current_cells",
          "proposed_cell", "share", "review_reason"]],
        use_container_width=True, height=260)

    # Items the relayout can't place: no tier (unmatched / unknown stock)
    untiered = merged[merged["match_status"].isin(["unmatched", "unknown"])][
        ["item_number", "cell_id", "location_label", "occupancy_share"]]
    if len(untiered):
        st.markdown("**Not assigned - no usage record to tier them** "
                    "(decide placement manually; candidates for Rack N4 misc)")
        st.dataframe(untiered, use_container_width=True, height=200)

    import io as _io
    buf2 = _io.BytesIO()
    with pd.ExcelWriter(buf2, engine="openpyxl") as xw:
        assign_df[["item_number", "description", "tier", "current_cells",
                   "proposed_cell", "share", "is_overstock", "review_flag",
                   "review_reason"]].to_excel(
            xw, sheet_name="New Layout Assignments", index=False)
        untiered.to_excel(xw, sheet_name="Unassigned (no tier)", index=False)
    st.download_button(
        "Export new-layout assignments (Excel)",
        data=buf2.getvalue(), file_name="new_layout_assignments.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

with tab_changes:
    st.subheader("Changes since last snapshot")
    if diff is None:
        st.info("This is the first saved snapshot. Upload next month's report "
                "and this tab will show exactly which cells changed, which "
                "items are newly flagged, and which issues were resolved. "
                "Idle flagging also upgrades automatically from the "
                "first-upload approximation (zero yearly picks) to the "
                "history rule (on-hand stayed above safety stock across "
                "consecutive snapshots).")
        if st.button("Generate demo comparison (synthetic last month)"):
            from core.compare import make_demo_previous
            prev_label = make_demo_previous(classified, recs, snap_label)
            st.success(f"Created synthetic snapshot '{prev_label}'. "
                       "Rerunning to show the comparison...")
            st.cache_data.clear()
            st.rerun()
    else:
        counts = diff["change"].value_counts()
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("New flags", int(counts.get("new_flag", 0)))
        c2.metric("Resolved", int(counts.get("resolved", 0)))
        c3.metric("Changed", int(counts.get("changed", 0)))
        c4.metric("Unchanged", int(counts.get("same", 0)))
        st.caption(f"Compared against snapshot: {diff['vs_snapshot'].iloc[0]}")
        show = st.multiselect(
            "Show change types",
            ["new_flag", "resolved", "changed", "new_item", "gone", "same"],
            default=["new_flag", "resolved", "changed", "new_item", "gone"])
        view = diff[diff["change"].isin(show)]
        st.dataframe(
            view[["change", "item_number", "current_cell", "recommendation",
                  "recommendation_prev", "target_cell"]],
            use_container_width=True, height=420)

with tab_fit:
    st.subheader("Consolidation fit check")
    c1, c2 = st.columns(2)
    c1.metric("Current capacity", f"{fit['current_capacity_ft']:,.0f} ft")
    c1.metric("Future capacity", f"{fit['future_capacity_ft']:,.0f} ft",
              delta=f"-{fit['reduction_ft']:,.0f} ft")
    c2.metric("Inventory footprint", f"{fit['occupied_ft']:,.0f} ft")
    c2.metric("Fits in future layout?",
              "Yes" if fit["fits"] else ("After idle removal" if fit["fits_after_idle"] else "No"))
    st.write(
        f"Removing the **{int(summary.loc['Flag', 'items']) if 'Flag' in summary.index else 0} "
        f"flagged idle items** (${fit['idle_value']:,.0f} of stock, ~{fit['idle_ft']:,.0f} linear ft) "
        f"raises headroom from {fit['headroom_ft']:,.0f} ft to "
        f"**{fit['headroom_after_idle_ft']:,.0f} ft**.")
    _, bad_widths = clean_widths(merged)
    if not bad_widths.empty:
        st.warning(f"Cells with implausible widths in the audit (treated as 13 ft): "
                   f"{', '.join(bad_widths['cell_id'])}")

with tab_review:
    st.subheader("Unknown stock ('?' during audit)")
    st.caption("Candidates: usage-report items never found in the audit, "
               "ranked by dollar value on hand. Match these on the floor.")
    unknown_cells = (merged[merged["match_status"] == "unknown"]
                     [["cell_id", "location_label", "occupancy_share", "notes"]]
                     .drop_duplicates())
    st.dataframe(unknown_cells, use_container_width=True)

    candidates = items_not_audited(merged.drop_duplicates("item_number"), classified)
    st.dataframe(
        candidates.nlargest(25, "dollar_value")
        [["item_number", "description", "tier", "on_hand", "dollar_value"]],
        use_container_width=True)

    st.subheader("Item numbers not in the usage report")
    unmatched = (merged[merged["match_status"] == "unmatched"]
                 [["item_number", "cell_id", "location_label"]].drop_duplicates())
    st.dataframe(unmatched, use_container_width=True)

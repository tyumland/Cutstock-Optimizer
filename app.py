"""Storage & Inventory Optimizer - guided Streamlit decision-support app."""
from pathlib import Path
import sys
import io
import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from core.ingest import load_usage_report, load_physical_audit, join_audit_to_usage, items_not_audited
from core.classify import classify_abc
from core.recommend import generate_recommendations
from core.layout import fit_analysis, FUTURE_LAYOUT
from core.relayout import assign_new_layout
from core.decision import build_inventory_decisions, merge_slotting_actions, review_summary
from ui.rack_map import recommendation_map, utilization_heatmap
from ui.new_layout_map import build_new_layout_figure
from export import build_export

st.set_page_config(page_title="Storage & Inventory Optimizer", page_icon="📦", layout="wide")
st.markdown("""
<style>
.block-container{padding-top:1.4rem;max-width:1500px}.small-note{color:#5f6b76;font-size:.9rem}
[data-testid="stMetric"]{background:#f6f8fa;border:1px solid #dfe3e8;border-radius:10px;padding:12px}
.approval{background:#edf5ff;border-left:5px solid #1f77b4;padding:14px 18px;border-radius:6px;margin:.5rem 0 1rem}
</style>""", unsafe_allow_html=True)

st.title("Storage & Inventory Optimizer")
st.caption("Upload a physical audit and usage/safety-stock report to identify what to keep, move, reduce, review for removal, and place in a new layout.")

with st.sidebar:
    st.header("1. Analysis setup")
    analysis_name = st.text_input("Analysis name", "Cutstock Future-State Review")
    st.caption("Required workflow: physical audit → usage and safety stock → validate → analyze")
    audit_file = st.file_uploader("Physical audit", type=["xls", "xlsx"], help="One row per item/cell with rack, row, position, occupancy, and cell width.")
    usage_file = st.file_uploader("Usage + updated safety stock", type=["xls", "xlsx"], help="Must include item number, yearly usage, updated safety stock, on-hand quantity, and standard cost.")
    final_layout_file = st.file_uploader("Approved new layout (optional)", type=["xlsx"], help="Upload a reviewed final layout instead of relying only on the generated proposal.")

    st.header("2. Decision policy")
    retention_multiplier = st.number_input("Retention ceiling (× safety stock)", 0.0, 10.0, 2.0, 0.25)
    p8_units = st.number_input("P8 material excess threshold (units)", 0.0, value=100.0, step=10.0)
    p8_value = st.number_input("P8 material excess threshold ($)", 0.0, value=0.0, step=100.0,
                               help="An item enters P8 if either the unit or value threshold is reached.")
    low_usage = st.number_input("Low-usage review threshold (annual units)", 0.0, value=0.0, step=1.0)

    st.header("3. New-layout capacity")
    cells13 = st.number_input("13-ft cells", min_value=0, value=int(FUTURE_LAYOUT["cells_13ft"]))
    cells9 = st.number_input("9-ft cells", min_value=0, value=int(FUTURE_LAYOUT["cells_9ft"]))
    row_tol = st.select_slider("Current-state slotting tolerance", options=[0, 1, 2], value=1,
                               format_func=lambda x: {0:"Strict",1:"Allow 1 row",2:"Allow 2 rows"}[x])

if not audit_file or not usage_file:
    st.info("Start by uploading the physical audit, followed by the usage and updated safety-stock report.")
    c1, c2, c3 = st.columns(3)
    c1.markdown("### 1 — Upload\nProvide the physical condition and current item locations.")
    c2.markdown("### 2 — Validate\nConfirm item matches, costs, safety stocks, and unknown material.")
    c3.markdown("### 3 — Act\nReview keep/move/removal decisions and export an implementation workbook.")
    st.stop()

@st.cache_data(show_spinner="Validating files and generating recommendations...")
def run_analysis(audit_bytes, audit_name, usage_bytes, usage_name, mult, p8u, p8v, low, tol, c13, c9):
    work = ROOT / "storage"
    work.mkdir(exist_ok=True)
    ap, up = work / audit_name, work / usage_name
    ap.write_bytes(audit_bytes); up.write_bytes(usage_bytes)
    audit = load_physical_audit(str(ap))
    usage = load_usage_report(str(up))
    classified = classify_abc(usage)
    merged = join_audit_to_usage(audit, classified)
    recs = generate_recommendations(merged, row_tolerance=int(tol))
    decisions = build_inventory_decisions(classified, mult, p8u, p8v, low)
    decisions = merge_slotting_actions(decisions, recs)
    fit = fit_analysis(merged, recs, {"cells_13ft": int(c13), "cells_9ft": int(c9)})
    return audit, usage, classified, merged, recs, decisions, fit

try:
    audit, usage, classified, merged, recs, decisions, fit = run_analysis(
        audit_file.getvalue(), audit_file.name, usage_file.getvalue(), usage_file.name,
        retention_multiplier, p8_units, p8_value, low_usage, row_tol, cells13, cells9)
except Exception as exc:
    st.error("The analysis could not run. Review the required columns and file formats below.")
    st.exception(exc)
    st.stop()

rsummary = review_summary(decisions)
review_value = float(decisions["excess_value"].sum())
p7_value = float(decisions.loc[decisions.review_group.str.startswith("P7"), "excess_value"].sum())
future_cap = fit["future_capacity_ft"]
reduction_pct = fit["reduction_ft"] / fit["current_capacity_ft"] * 100 if fit["current_capacity_ft"] else 0

st.subheader(analysis_name)
st.markdown(f"""<div class="approval"><b>Decision summary:</b> The analysis retains up to {retention_multiplier:g}× updated safety stock, separates zero-usage and active excess, and tests retained inventory against the New Layout. Removal from the New Layout is a review recommendation—not an automatic write-off or disposal decision.</div>""", unsafe_allow_html=True)

k1,k2,k3,k4,k5 = st.columns(5)
k1.metric("Current capacity", f"{fit['current_capacity_ft']:,.0f} LF")
k2.metric("New Layout capacity", f"{future_cap:,.0f} LF")
k3.metric("Capacity reduction", f"{fit['reduction_ft']:,.0f} LF", f"{reduction_pct:.1f}% less")
k4.metric("Value under review", f"${review_value:,.0f}")
k5.metric("P7 zero-usage review", f"${p7_value:,.0f}")

nav = st.tabs(["Overview", "Upload & Validation", "Recommendations", "Current State", "New Layout", "Finance & Removal Review", "Export"])

with nav[0]:
    st.subheader("Analysis overview")
    c1,c2 = st.columns([1,1])
    with c1:
        display = rsummary.copy()
        display["review_value"] = display["review_value"].map(lambda x: f"${x:,.0f}")
        st.dataframe(display, hide_index=True, use_container_width=True)
    with c2:
        st.markdown("**How to read the results**")
        st.markdown("- **Keep:** within the selected retention ceiling.\n- **Move / re-slot:** retained stock is in the wrong accessibility zone.\n- **P7:** zero recorded usage with stock/excess on hand.\n- **P8:** active, material overstock above the selected retention ceiling.\n- **P9:** smaller active excess best addressed through safety-stock or replenishment changes.\n- **Manual review:** unmatched, unknown, or incomplete source data.")
    st.info("Final disposition may include write-off, disposal, resale, alternate use, relocation, or retention after Finance, Operations, and Materials review.")

with nav[1]:
    st.subheader("Data validation")
    matched = int((merged.match_status == "matched").sum())
    unknown = int((merged.match_status == "unknown").sum())
    unmatched = int((merged.match_status == "unmatched").sum())
    not_audited = items_not_audited(audit, usage)
    a,b,c,d = st.columns(4)
    a.metric("Usage-report items", usage.item_number.nunique())
    b.metric("Matched audit records", matched)
    c.metric("Unknown / unmatched", unknown + unmatched)
    d.metric("Usage items not audited", not_audited.item_number.nunique())
    missing_cost = usage[(usage.on_hand > 0) & (usage.standard_cost <= 0)]
    missing_ss = usage[(usage.on_hand > 0) & (usage.safety_stock <= 0)]
    if len(missing_cost): st.warning(f"{len(missing_cost)} stocked items have no positive standard cost; review values may be understated.")
    if len(missing_ss): st.warning(f"{len(missing_ss)} stocked items have zero safety stock; the app routes active quantities to review rather than assuming they should be retained.")
    with st.expander("Unknown and unmatched audit records", expanded=(unknown+unmatched)>0):
        st.dataframe(merged[merged.match_status.isin(["unknown","unmatched"])][[c for c in ["rack","cell_id","location_label","item_number","occupancy_share","notes","match_status"] if c in merged]], use_container_width=True)
    with st.expander("Usage items not found in the audit"):
        st.dataframe(not_audited, use_container_width=True)

with nav[2]:
    st.subheader("Item recommendations")
    groups = ["All"] + sorted(decisions.review_group.unique().tolist())
    f1,f2,f3 = st.columns([2,2,3])
    group = f1.selectbox("Review group", groups)
    tier = f2.multiselect("ABC tier", ["A","B","C"], default=["A","B","C"])
    query = f3.text_input("Search item number or description")
    view = decisions[decisions.tier.isin(tier)]
    if group != "All": view = view[view.review_group == group]
    if query:
        q = query.upper()
        view = view[view.item_number.astype(str).str.upper().str.contains(q, na=False) | view.description.astype(str).str.upper().str.contains(q, na=False)]
    cols = ["item_number","description","tier","yearly_usage","safety_stock","on_hand","recommended_keep_qty","excess_qty","standard_cost","excess_value","review_group","slotting_action","current_locations","suggested_locations","decision_reason"]
    st.dataframe(view[[c for c in cols if c in view]], use_container_width=True, height=520, hide_index=True,
                 column_config={"standard_cost": st.column_config.NumberColumn(format="$%.2f"), "excess_value": st.column_config.NumberColumn(format="$%.2f")})

with nav[3]:
    st.subheader("Current State")
    st.caption("The physical audit shown as it exists today. Colors identify keep, move, removal-review, unmatched, unknown, and empty cells.")
    left,right = st.columns([2,1])
    left.plotly_chart(recommendation_map(merged, recs), use_container_width=True)
    right.plotly_chart(utilization_heatmap(merged, recs), use_container_width=True)

with nav[4]:
    st.subheader("New Layout")
    st.caption("Use the generated proposal for analysis, or upload an approved final-layout workbook to display the reviewed state.")
    if final_layout_file:
        xl = pd.ExcelFile(final_layout_file, engine="openpyxl")
        sheet = "Cell Layout" if "Cell Layout" in xl.sheet_names else xl.sheet_names[0]
        final_df = pd.read_excel(final_layout_file, sheet_name=sheet, engine="openpyxl")
        st.success(f"Displaying approved layout from '{sheet}'.")
        st.dataframe(final_df, use_container_width=True, height=560, hide_index=True)
        new_layout_export = final_df
    else:
        assign_df, new_cells = assign_new_layout(merged, recs)
        st.plotly_chart(build_new_layout_figure(assign_df, new_cells), use_container_width=True)
        c1,c2,c3 = st.columns(3)
        c1.metric("Items placed", assign_df.item_number.nunique())
        c2.metric("Manual-review items", assign_df.loc[assign_df.review_flag, "item_number"].nunique())
        c3.metric("Empty cells", int((new_cells.free >= .999).sum()))
        st.dataframe(assign_df[["item_number","description","tier","current_cells","proposed_cell","share","review_flag","review_reason"]], use_container_width=True, height=360, hide_index=True)
        new_layout_export = assign_df

with nav[5]:
    st.subheader("Finance & Removal Review")
    st.markdown("**Value under review is not automatically a write-off.** This page separates the phased decision groups and provides item-level backup.")
    cards = st.columns(3)
    for col, prefix, label in zip(cards,["P7","P8","P9"],["Zero usage","Active overstock","Safety-stock adjustment"]):
        v = decisions[decisions.review_group.str.startswith(prefix)]
        col.metric(f"{prefix} — {label}", f"${v.excess_value.sum():,.0f}", f"{v.item_number.nunique()} items")
    phase = st.radio("Review group", ["P7","P8","P9"], horizontal=True)
    phase_df = decisions[decisions.review_group.str.startswith(phase)]
    st.dataframe(phase_df[["item_number","description","yearly_usage","safety_stock","on_hand","recommended_keep_qty","excess_qty","excess_value","decision_reason"]], use_container_width=True, height=460, hide_index=True,
                 column_config={"excess_value": st.column_config.NumberColumn(format="$%.2f")})

with nav[6]:
    st.subheader("Export & implementation")
    st.write("Download a workbook containing the executive summary, all recommendations, P7/P8/P9 lists, move list, manual-review records, and New Layout details.")
    export_bytes = build_export(decisions, recs, merged, fit, rsummary, new_layout_export)
    st.download_button("Download complete analysis workbook", export_bytes,
                       file_name="storage_inventory_optimizer_results.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", type="primary")
    st.markdown("**Recommended implementation sequence**\n1. Resolve unknown and unmatched material.\n2. Review P7 zero-usage inventory.\n3. Approve retained quantities and P8 active excess.\n4. Adjust P9 safety-stock and replenishment settings.\n5. Execute retained-stock moves into the New Layout.\n6. Re-audit and confirm cell utilization.")

"""Readable current-state rack maps with rack filtering and full-width views."""
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

STATUS_COLORS = {"Stay":"#16866B","Move":"#E59D00","Flag":"#C83E2D","unmatched":"#F2D64B","unknown":"#7A5AA6","empty":"#E9EDF1"}
TEXT_COLORS = {"Stay":"white","Move":"#172033","Flag":"white","unmatched":"#172033","unknown":"white","empty":"#667085"}
STATUS_PRIORITY = ["Flag","Move","Stay","unmatched","unknown","empty"]
LEGEND_LABELS = {"Stay":"Keep","Move":"Move / Re-slot","Flag":"Review for Removal","unmatched":"No Usage Record","unknown":"Unknown Stock","empty":"Empty"}


def cell_rollup(merged: pd.DataFrame, recs: pd.DataFrame) -> pd.DataFrame:
    rec_map = recs.set_index(["item_number","current_cell"])["recommendation"].to_dict() if len(recs) else {}
    rows=[]
    for (rack,section,row_num,cell_id),grp in merged.groupby(["rack","section","row_num","cell_id"]):
        statuses=[]; lines=[]
        occ=float(pd.to_numeric(grp.get("occupancy",0), errors="coerce").fillna(0).max())
        for _,r in grp.iterrows():
            ms=r.get("match_status","matched")
            if ms=="empty": statuses.append("empty"); continue
            if ms in ("unknown","unmatched"):
                statuses.append(ms); lines.append("Unknown stock - verify" if ms=="unknown" else f"{r.get('item_number','')} - no usage record"); continue
            status=rec_map.get((r.get("item_number"),cell_id),"Stay"); statuses.append(status)
            lines.append(f"<b>{r.get('item_number','')}</b> {str(r.get('description',''))[:38]}<br>Tier {r.get('tier','')} | {LEGEND_LABELS.get(status,status)} | {float(r.get('yearly_usage',0)):,.0f}/yr")
        worst=next((s for s in STATUS_PRIORITY if s in statuses),"empty")
        rows.append({"rack":rack,"section":str(section),"row_num":int(row_num),"cell_id":cell_id,"status":worst,"occupancy":min(max(occ,0),1),"hover":f"<b>{cell_id}</b> - {occ:.0%} utilized<br>"+("<br>".join(lines) or "Empty - available space")})
    return pd.DataFrame(rows)


def rack_summary(merged, recs):
    cells=cell_rollup(merged,recs)
    return {"Keep":int((cells.status=="Stay").sum()),"Move / Re-slot":int((cells.status=="Move").sum()),"Review for Removal":int((cells.status=="Flag").sum()),"Unknown / unmatched":int(cells.status.isin(["unknown","unmatched"]).sum()),"Empty":int((cells.status=="empty").sum()),"Average utilization":float(cells.occupancy.mean()) if len(cells) else 0}


def build_rack_figure(merged, recs, mode="recommendations", selected_rack="All Racks"):
    cells=cell_rollup(merged,recs)
    if selected_rack!="All Racks": cells=cells[cells.rack==selected_rack]
    racks=sorted(cells.rack.unique())
    if not racks: return go.Figure()
    ncols=len(racks)
    widths=[cells[cells.rack==r].section.nunique() for r in racks]; widths=[w/sum(widths) for w in widths]
    fig=make_subplots(rows=1,cols=ncols,subplot_titles=racks,column_widths=widths,shared_yaxes=True,horizontal_spacing=.025)
    max_row=int(cells.row_num.max())
    marker_size=58 if ncols==1 else (44 if ncols<=3 else 34)
    text_size=13 if ncols==1 else (10 if ncols<=3 else 8)
    for i,rack in enumerate(racks,1):
        sub=cells[cells.rack==rack].copy(); sections=sorted(sub.section.unique()); sec_x={s:j for j,s in enumerate(sections)}
        if mode=="utilization":
            colors=sub.occupancy.apply(lambda v:f"rgba(206,52,35,{.10+.90*v:.2f})"); text_colors=sub.occupancy.apply(lambda v:"white" if v>.55 else "#172033"); text=sub.occupancy.map(lambda v:f"{v:.0%}"); symbols="square"
        else:
            colors=sub.status.map(STATUS_COLORS); text_colors=sub.status.map(TEXT_COLORS); text=sub.section+sub.row_num.astype(str); symbols=sub.status.map(lambda s:"square-x" if s=="Flag" else "square")
        fig.add_trace(go.Scatter(x=sub.section.map(sec_x),y=sub.row_num,mode="markers+text",marker=dict(symbol=symbols,size=marker_size,color=colors,line=dict(width=1.2,color="#475467")),text=text,textfont=dict(size=text_size,color=list(text_colors)),hovertext=sub.hover,hoverinfo="text",showlegend=False),row=1,col=i)
        fig.update_xaxes(tickvals=list(sec_x.values()),ticktext=sections,title_text="Section",showgrid=False,zeroline=False,row=1,col=i)
    for c in range(1,ncols+1): fig.update_yaxes(range=[.35,max_row+.65],tickvals=list(range(1,max_row+1)),showgrid=False,zeroline=False,row=1,col=c)
    fig.update_yaxes(title_text="Row (1 = floor)",row=1,col=1)
    if mode=="recommendations":
        for s in STATUS_PRIORITY:
            fig.add_trace(go.Scatter(x=[None],y=[None],mode="markers",marker=dict(symbol="square-x" if s=="Flag" else "square",size=12,color=STATUS_COLORS[s],line=dict(width=1,color="#475467")),name=LEGEND_LABELS[s],showlegend=True))
    height=610 if ncols==1 else 500
    fig.update_layout(height=height,margin=dict(l=55,r=20,t=65,b=80),legend=dict(orientation="h",yanchor="top",y=-.12,x=.5,xanchor="center"),plot_bgcolor="white",paper_bgcolor="white",font=dict(color="#172033"),hoverlabel=dict(bgcolor="white",font_size=12,align="left"))
    return fig


def recommendation_map(merged,recs,selected_rack="All Racks"): return build_rack_figure(merged,recs,"recommendations",selected_rack)
def utilization_heatmap(merged,recs,selected_rack="All Racks"): return build_rack_figure(merged,recs,"utilization",selected_rack)

"""Front-facing pallet elevation for an approved New Layout workbook."""
import math
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

COLORS={"A":"#367DB5","B":"#2A9D78","C":"#E3A126","HW":"#8A6F4D","REVIEW":"#775DA6","UNKNOWN":"#775DA6"}

def _norm(df):
    d=df.copy(); d.columns=[str(c).strip() for c in d.columns]
    rename={"Cell":"cell","Rack":"rack","Col":"col","Row":"row","Cell Width(ft)":"cell_width","Item Number":"item_number","Description":"description","Tier":"tier","Length(ft)":"length_ft","Orientation":"orientation","% of Cell":"pct_cell","Audit Location":"audit_location","Verify Reason":"verify_reason","Audit Notes":"audit_notes"}
    d=d.rename(columns=rename)
    for c in rename.values():
        if c not in d: d[c]=""
    d["rack"]=d.rack.astype(str).str.strip(); d["col"]=d.col.astype(str).str.strip(); d["row"]=pd.to_numeric(d.row,errors="coerce").fillna(0).astype(int); d["pct_cell"]=pd.to_numeric(d.pct_cell,errors="coerce").fillna(0).clip(lower=0,upper=100)
    d["tier"]=d.tier.fillna("REVIEW").astype(str).str.upper().str.strip(); d["item_number"]=d.item_number.astype(str).str.replace(".0$","",regex=True)
    return d[d.row>0]

def layout_summary(df):
    d=_norm(df); return {"Items placed":int(d.item_number.nunique()),"Pallets placed":int(len(d)),"Sideways pallets":int(d.orientation.astype(str).str.lower().eq("sideways").sum()),"Review items":int(d.loc[d.tier.isin(["REVIEW","UNKNOWN"]), "item_number"].nunique()),"Occupied cells":int(d.cell.nunique())}

def build_front_facing_layout(df, selected_rack="All Racks"):
    d=_norm(df)
    if selected_rack!="All Racks": d=d[d.rack==selected_rack]
    racks=sorted(d.rack.unique())
    if not racks: return go.Figure()
    widths=[d[d.rack==r].col.nunique() for r in racks]; widths=[w/sum(widths) for w in widths]
    fig=make_subplots(rows=1,cols=len(racks),subplot_titles=racks,column_widths=widths,shared_yaxes=True,horizontal_spacing=.035)
    max_row=max(6,int(d.row.max())); pallet_count=len(d)
    for idx,rack in enumerate(racks,1):
        rd=d[d.rack==rack]; cols=sorted(rd.col.unique()); xpos={c:i for i,c in enumerate(cols)}
        for row in range(1,max_row+1):
            for col in cols:
                x=xpos[col]; fig.add_shape(type="rect",x0=x-.48,x1=x+.48,y0=row-.42,y1=row+.42,line=dict(color="#667085",width=1),fillcolor="#F2F4F7",layer="below",row=1,col=idx)
                cg=rd[(rd.col==col)&(rd.row==row)]
                cursor=x-.47
                for _,p in cg.iterrows():
                    width=max(.08,.94*float(p.pct_cell)/100); x0=cursor; x1=min(x+.47,cursor+width); cursor=x1
                    tier=p.tier if p.tier in COLORS else ("HW" if "HARDWOOD" in str(p.description).upper() else "REVIEW")
                    color=COLORS.get(tier,COLORS["REVIEW"])
                    verify_text=str(p.verify_reason).strip()
                    review=(verify_text.lower() not in ("", "nan", "none")) or tier in ("REVIEW","UNKNOWN")
                    hover=(f"<b>{p.item_number}</b><br>{p.description}<br>Cell: {p.cell}<br>Tier: {p.tier}<br>Cell share: {p.pct_cell:.0f}%<br>Length: {p.length_ft}<br>Orientation: {p.orientation}<br>From: {p.audit_location}"+(f"<br><b>Verify:</b> {p.verify_reason}" if review else "")+(f"<br>Notes: {p.audit_notes}" if str(p.audit_notes).lower()!="nan" else ""))
                    fig.add_trace(go.Scatter(x=[(x0+x1)/2],y=[row],mode="markers",marker=dict(symbol="square",size=8,color=color,opacity=0),hovertext=[hover],hoverinfo="text",showlegend=False),row=1,col=idx)
                    fig.add_shape(type="rect",x0=x0,x1=x1,y0=row-.38,y1=row+.38,line=dict(color="#111827" if review else "#475467",width=3 if review else 1),fillcolor=color,layer="below",row=1,col=idx)
                    block_width=x1-x0
                    label=str(p.item_number)
                    if len(racks)==1:
                        min_width=.12; font_size=11 if block_width>=.28 else 9
                    else:
                        min_width=.18; font_size=7
                    if block_width>=min_width:
                        if len(label)>8 and block_width<.34:
                            split_at=(len(label)+1)//2
                            label=label[:split_at]+"<br>"+label[split_at:]
                        fig.add_annotation(x=(x0+x1)/2,y=row,text=label,showarrow=False,font=dict(size=font_size,color="white"),xanchor="center",yanchor="middle",align="center",row=1,col=idx)
                    if str(p.orientation).lower()=="sideways": fig.add_shape(type="line",x0=x0+.02,x1=x1-.02,y0=row-.33,y1=row+.33,line=dict(color="rgba(255,255,255,.65)",width=1,dash="dot"),row=1,col=idx)
        fig.update_xaxes(tickvals=list(xpos.values()),ticktext=cols,title_text="Column",range=[-.6,len(cols)-.4],showgrid=False,zeroline=False,row=1,col=idx)
    for c in range(1,len(racks)+1): fig.update_yaxes(range=[.45,max_row+.55],tickvals=list(range(1,max_row+1)),showgrid=False,zeroline=False,row=1,col=c)
    fig.update_yaxes(title_text="Row (1 = floor)",row=1,col=1)
    for key,label in [("A","A tier"),("B","B tier"),("C","C tier"),("HW","Hardwood"),("REVIEW","Manual review")]: fig.add_trace(go.Scatter(x=[None],y=[None],mode="markers",marker=dict(symbol="square",size=13,color=COLORS[key],line=dict(color="#111827",width=2 if key=="REVIEW" else 1)),name=label,showlegend=True))
    fig.update_layout(height=650 if len(racks)==1 else 520,margin=dict(l=60,r=20,t=65,b=90),legend=dict(orientation="h",y=-.14,x=.5,xanchor="center"),plot_bgcolor="white",paper_bgcolor="white",font=dict(color="#172033"),hoverlabel=dict(bgcolor="white",font_size=12,align="left"))
    return fig

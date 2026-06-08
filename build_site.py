"""
build_site.py — assembles the single self-contained deliverable HTML.

The page is now data-driven: the pipeline writes a compact `data.json`, the page
renders all charts client-side from it (Plotly), and a small poller re-fetches
data.json every minute and re-renders in place — so the page updates itself with no
reload. The written memo (the four challenge sections + data-quality section) is
server-rendered for first paint and progressively hydrated.

Charts:
  • Fig.01 Live liquidation density (with a hover crosshair + unified long/short read)
    — toggles to a time × price liquidation HEATMAP reconstructed from history.
  • Fig.02 Cascade Fragility gauge.
  • Fig.03 CFI history with regime bands.

Design language unchanged: "editorial markets terminal" (Fraunces / IBM Plex Mono /
Hanken Grotesk, warm ink, gold/emerald/red).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd

from heatmap import build_heatmap
from storage import LATEST_SNAPSHOT_JSON, load_map_history, load_metrics_history
from viz import _gaussian_smooth, export_memo_png

ROOT = os.path.dirname(os.path.abspath(__file__))
SITE_DIR = os.path.join(ROOT, "site")
OUT_HTML = os.path.join(SITE_DIR, "index.html")
SITE_DATA = os.path.join(SITE_DIR, "data.json")
DOCS_DIR = os.path.join(ROOT, "docs")
DOCS_HTML = os.path.join(DOCS_DIR, "index.html")
DOCS_DATA = os.path.join(DOCS_DIR, "data.json")


# --------------------------------------------------------------- formatting
def _money(x: float) -> str:
    for unit, div in (("B", 1e9), ("M", 1e6), ("k", 1e3)):
        if abs(x) >= div:
            return f"${x / div:.2f}{unit}"
    return f"${x:,.0f}"


def _confidence(snapshot: dict[str, Any]) -> tuple[str, str]:
    cov = snapshot["coverage"]["coverage_ratio"]
    n = snapshot["coverage"]["n_btc_positions"]
    if cov >= 0.30 and n >= 40:
        return "High", "var(--long)"
    if cov >= 0.15 and n >= 20:
        return "Medium", "var(--gold)"
    return "Low", "var(--short)"


def _kpi(label: str, value: str, sub: str = "", accent: str = "") -> str:
    bar = accent or "var(--hair-strong)"
    return (f'<div class="kpi"><span class="kpi-bar" style="background:{bar}"></span>'
            f'<div class="kpi-label">{label}</div>'
            f'<div class="kpi-value">{value}</div>'
            f'<div class="kpi-sub">{sub}</div></div>')


# --------------------------------------------------------------- data payload
def build_payload(snapshot: dict[str, Any], history: pd.DataFrame,
                  heatmap: dict[str, Any] | None) -> dict[str, Any]:
    """The compact JSON the page renders from (and re-fetches for live updates)."""
    h = pd.DataFrame(snapshot["histogram"]).sort_values("price_mid")
    raw_long = h["long_notional"].to_numpy().astype(float)
    raw_short = h["short_notional"].to_numpy().astype(float)
    win = np.ones(5)
    ladder = {
        "price": [round(float(x), 1) for x in h["price_mid"]],
        "long": [round(float(x)) for x in _gaussian_smooth(raw_long)],
        "short": [round(float(x)) for x in _gaussian_smooth(raw_short)],
        "long_win": [round(float(x)) for x in np.convolve(raw_long, win, mode="same")],
        "short_win": [round(float(x)) for x in np.convolve(raw_short, win, mode="same")],
    }
    hist = []
    if not history.empty:
        for r in history.sort_values("timestamp").itertuples():
            hist.append({"t": r.timestamp, "cfi": float(r.cfi)})
    return {
        "generated_at": snapshot["provenance"]["generated_at"],
        "market": snapshot["market"],
        "signals": snapshot["signals"],
        "coverage": snapshot["coverage"],
        "quality": snapshot["quality"],
        "ladder": ladder,
        "history": hist,
        "heatmap": heatmap,
    }


# --------------------------------------------------------------- static CSS/JS
_CSS = r"""
  :root {
    --ink:#0b0a09; --ink-2:#100e0c; --card:#15120f; --card-2:#1a1612;
    --hair:#241f18; --hair-strong:#352d22;
    --bone:#ece6da; --muted:#9a8f7d; --faint:#6f665a;
    --gold:#e8a13a; --long:#3fb98c; --short:#e5564e;
    --mono:"IBM Plex Mono",ui-monospace,SFMono-Regular,monospace;
    --serif:"Fraunces",Georgia,serif; --body:"Hanken Grotesk",system-ui,sans-serif;
  }
  * { box-sizing:border-box; }
  html { scroll-behavior:smooth; }
  body { margin:0; color:var(--bone); font-family:var(--body); line-height:1.65;
    background:
      radial-gradient(1100px 520px at 78% -8%, rgba(232,161,58,0.10), transparent 60%),
      radial-gradient(900px 600px at 0% 0%, rgba(63,185,140,0.05), transparent 55%), var(--ink);
    -webkit-font-smoothing:antialiased; }
  body::before { content:""; position:fixed; inset:0; pointer-events:none; z-index:0; opacity:.5;
    background-image:linear-gradient(var(--hair) 1px, transparent 1px),
      linear-gradient(90deg, var(--hair) 1px, transparent 1px); background-size:64px 64px,64px 64px;
    -webkit-mask-image:radial-gradient(120% 80% at 50% 0%, #000 30%, transparent 90%);
            mask-image:radial-gradient(120% 80% at 50% 0%, #000 30%, transparent 90%); }
  .wrap { position:relative; z-index:1; max-width:1080px; margin:0 auto; padding:46px 22px 90px; }
  ::selection { background:rgba(232,161,58,0.28); color:#fff; }
  ::-webkit-scrollbar { width:11px; } ::-webkit-scrollbar-thumb { background:var(--hair-strong); border-radius:6px; }
  @keyframes fadeUp { from { opacity:0; transform:translateY(16px); } to { opacity:1; transform:none; } }
  .reveal { opacity:0; animation:fadeUp .7s cubic-bezier(.2,.7,.2,1) forwards; }
  @media (prefers-reduced-motion:reduce) { .reveal { animation:none; opacity:1; } }
  .eyebrow { font-family:var(--mono); font-size:11.5px; letter-spacing:.28em; text-transform:uppercase;
    color:var(--muted); display:flex; gap:12px; align-items:center; flex-wrap:wrap; }
  .eyebrow .dot { width:4px; height:4px; border-radius:50%; background:var(--gold); }
  h1 { font-family:var(--serif); font-weight:600; font-optical-sizing:auto;
    font-size:clamp(34px,6vw,62px); line-height:1.02; letter-spacing:-.015em; margin:18px 0 6px; }
  h1 .amp { font-style:italic; font-weight:400; color:var(--gold); }
  h1 .l2 { display:block; font-weight:400; color:var(--muted); font-size:.62em; letter-spacing:0; margin-top:6px; }
  .dek { font-size:17px; color:#cabfae; max-width:60ch; margin:14px 0 0; }
  .rail { display:flex; gap:9px; flex-wrap:wrap; margin-top:22px; }
  .tag { font-family:var(--mono); font-size:11.5px; letter-spacing:.04em; padding:5px 11px;
    border:1px solid var(--hair-strong); border-radius:999px; color:var(--muted);
    display:inline-flex; align-items:center; gap:7px; background:rgba(255,255,255,0.012); }
  .tag b { color:var(--bone); font-weight:500; }
  .tag.btn { cursor:pointer; background:rgba(232,161,58,0.10); border-color:var(--gold); color:var(--gold); font:inherit; font-family:var(--mono); }
  .tag.btn:hover { background:rgba(232,161,58,0.20); }
  .tag.btn:disabled { opacity:.55; cursor:default; }
  .live-dot { width:7px; height:7px; border-radius:50%; background:var(--long); position:relative; }
  .live-dot::after { content:""; position:absolute; inset:-4px; border-radius:50%;
    border:1px solid var(--long); animation:pulse 2s ease-out infinite; }
  @keyframes pulse { 0% { transform:scale(.6); opacity:.9; } 100% { transform:scale(1.8); opacity:0; } }
  .flash { animation:flashk .9s ease; }
  @keyframes flashk { 0% { color:var(--gold); } 100% { color:var(--muted); } }
  .rule { height:1px; background:linear-gradient(90deg,var(--hair-strong),transparent); margin:40px 0 0; }
  .kpis { display:grid; grid-template-columns:repeat(4,1fr); gap:13px; margin:26px 0 6px; }
  .kpi { position:relative; background:linear-gradient(180deg,var(--card),var(--ink-2));
    border:1px solid var(--hair); border-radius:13px; padding:15px 15px 13px; overflow:hidden;
    transition:transform .25s ease, border-color .25s ease; }
  .kpi:hover { transform:translateY(-3px); border-color:var(--hair-strong); }
  .kpi-bar { position:absolute; top:0; left:0; right:0; height:2px; opacity:.85; }
  .kpi-label { font-family:var(--mono); color:var(--faint); font-size:10.5px; letter-spacing:.14em; text-transform:uppercase; }
  .kpi-value { font-family:var(--mono); font-size:25px; font-weight:500; margin-top:7px; color:var(--bone); font-variant-numeric:tabular-nums; }
  .kpi-value .u { font-size:13px; color:var(--faint); }
  .kpi-sub { font-family:var(--mono); color:var(--muted); font-size:11px; margin-top:3px; }
  .fig-toggle { display:flex; gap:8px; margin:18px 0 0; }
  .toggle-btn { font-family:var(--mono); font-size:12px; letter-spacing:.04em; color:var(--muted);
    background:var(--ink-2); border:1px solid var(--hair); border-radius:9px; padding:7px 14px; cursor:pointer;
    transition:all .2s ease; }
  .toggle-btn:hover { border-color:var(--hair-strong); color:var(--bone); }
  .toggle-btn.active { color:var(--ink); background:var(--gold); border-color:var(--gold); font-weight:600; }
  figure.chart { margin:10px 0 16px; background:linear-gradient(180deg,var(--card),var(--ink-2));
    border:1px solid var(--hair); border-radius:15px; padding:8px 10px 4px; }
  figcaption { font-family:var(--mono); font-size:11px; letter-spacing:.16em; text-transform:uppercase;
    color:var(--faint); padding:9px 8px 0; display:flex; justify-content:space-between; }
  figcaption b { color:var(--muted); font-weight:500; }
  .legend { display:flex; gap:18px; padding:8px 10px 0; flex-wrap:wrap; }
  .lg { font-family:var(--mono); font-size:11.5px; color:var(--muted); display:inline-flex; align-items:center; gap:7px; }
  .lg i { width:18px; height:3px; border-radius:2px; display:inline-block; }
  #ladder,#heatmap { min-height:540px; } #gauge,#history { min-height:250px; }
  .empty { color:var(--muted); font-family:var(--mono); font-size:13px; text-align:center; padding:120px 20px; }
  .two { display:grid; grid-template-columns:1fr 1fr; gap:13px; }
  section.memo { margin-top:46px; }
  .sec-head { display:flex; align-items:baseline; gap:16px; border-bottom:1px solid var(--hair); padding-bottom:12px; margin:0 0 18px; }
  .sec-num { font-family:var(--serif); font-size:30px; color:var(--gold); font-weight:600; line-height:1; font-variant-numeric:tabular-nums; }
  .sec-head h2 { font-family:var(--serif); font-weight:600; font-size:25px; letter-spacing:-.01em; margin:0; }
  .memo p, .memo li { font-size:15.5px; color:#cdc3b3; max-width:70ch; }
  .memo h3 { font-family:var(--mono); font-size:12px; letter-spacing:.12em; text-transform:uppercase; color:var(--gold); margin:22px 0 4px; }
  .memo strong { color:var(--bone); font-weight:600; } .memo em { color:#dcd2c2; }
  ul { padding-left:20px; } li { margin:5px 0; }
  code { font-family:var(--mono); background:var(--ink-2); border:1px solid var(--hair); padding:1px 6px; border-radius:5px; font-size:12.5px; color:#e9c98c; }
  .callout { border-left:2px solid var(--gold); background:linear-gradient(90deg,rgba(232,161,58,0.07),transparent); padding:13px 18px; margin:16px 0; border-radius:0 10px 10px 0; }
  .callout.warn { border-left-color:var(--short); background:linear-gradient(90deg,rgba(229,86,78,0.08),transparent); }
  .callout p { margin:0; max-width:none; }
  table { width:100%; border-collapse:collapse; margin:14px 0; font-size:13.5px; }
  th { font-family:var(--mono); text-align:left; color:var(--faint); font-weight:500; font-size:10.5px; letter-spacing:.1em; text-transform:uppercase; padding:9px 10px; border-bottom:1px solid var(--hair-strong); }
  td { padding:9px 10px; border-bottom:1px solid var(--hair); color:#cdc3b3; }
  td:nth-child(2) { font-family:var(--mono); color:var(--bone); font-variant-numeric:tabular-nums; }
  footer { margin-top:54px; padding-top:18px; border-top:1px solid var(--hair); color:var(--faint); font-family:var(--mono); font-size:12px; line-height:1.8; }
  footer .live { color:var(--muted); }
  @media (max-width:820px) { .kpis { grid-template-columns:repeat(2,1fr); } .two { grid-template-columns:1fr; } }
"""

_JS = r"""
const LONG='#3fb98c', SHORT='#e5564e', GOLD='#e8a13a', FONT='#ece6da', MUTED='#9a8f7d',
      GRID='rgba(150,128,92,0.13)', MONO='IBM Plex Mono, ui-monospace, monospace';
const CFG={displayModeBar:false, responsive:true};
let GEN=null, VIEW='live';

function money(x){const a=Math.abs(x);
  if(a>=1e9)return '$'+(x/1e9).toFixed(2)+'B'; if(a>=1e6)return '$'+(x/1e6).toFixed(2)+'M';
  if(a>=1e3)return '$'+(x/1e3).toFixed(2)+'k'; return '$'+Math.round(x).toLocaleString();}
function num(x){return Math.round(x).toLocaleString();}
function baseLayout(title){return {
  title:{text:title, font:{size:14,color:FONT,family:MONO}, x:0.01},
  paper_bgcolor:'rgba(0,0,0,0)', plot_bgcolor:'rgba(0,0,0,0)',
  font:{color:FONT,family:MONO,size:12}, margin:{l:74,r:64,t:42,b:46}, showlegend:false,
  hoverlabel:{bgcolor:'#15120f',bordercolor:GRID,font:{family:MONO,color:FONT}},
  xaxis:{gridcolor:GRID,zerolinecolor:GRID,linecolor:GRID,tickfont:{color:MUTED,size:11}},
  yaxis:{gridcolor:GRID,zerolinecolor:GRID,linecolor:GRID,tickfont:{color:MUTED,size:11}} };}

function confidence(d){const c=d.coverage.coverage_ratio,n=d.coverage.n_btc_positions;
  if(c>=0.30&&n>=40)return['High','var(--long)']; if(c>=0.15&&n>=20)return['Medium','var(--gold)'];
  return['Low','var(--short)'];}
function kpiCard(label,value,sub,accent){return '<div class="kpi"><span class="kpi-bar" style="background:'+
  (accent||'var(--hair-strong)')+'"></span><div class="kpi-label">'+label+'</div><div class="kpi-value">'+
  value+'</div><div class="kpi-sub">'+sub+'</div></div>';}
function setText(id,t){const e=document.getElementById(id); if(e)e.textContent=t;}
function setBadge(id,t,c){const e=document.getElementById(id); if(e){e.textContent=t; e.style.color=c;}}

function renderKpis(d){
  const m=d.market,s=d.signals,cov=d.coverage, fa=m.funding_hourly*24*365, asym=s.asymmetry;
  const bias=asym>0?'short side · upside-squeeze fuel':'long side · downside-flush fuel';
  const biasC=asym>0?'var(--short)':'var(--long)';
  const regC={calm:'var(--long)',elevated:'var(--gold)',fragile:'var(--short)'}[s.regime];
  const cf=confidence(d), w5=s.near_band_usd['0.05'].total, w2=s.near_band_usd['0.02'].total;
  document.getElementById('kpis').innerHTML=[
    kpiCard('BTC mark','$'+num(m.mark_px),'oracle $'+num(m.oracle_px)),
    kpiCard('Cascade Fragility Index',s.cfi.toFixed(1)+"<span class='u'>/100</span>",'regime · '+s.regime.toUpperCase(),regC),
    kpiCard('Long / short asymmetry',(asym>=0?'+':'')+asym.toFixed(2),bias,biasC),
    kpiCard('Liquidable within ±5%',money(w5),'±2% · '+money(w2),'var(--gold)'),
    kpiCard('Funding (annualized)',(fa>=0?'+':'')+(fa*100).toFixed(1)+'%',(m.funding_hourly>=0?'+':'')+(m.funding_hourly*100).toFixed(4)+'% / h'),
    kpiCard('Open interest',money(m.oi_usd),num(m.oi_btc)+' BTC'),
    kpiCard('Sample coverage',(cov.coverage_ratio*100).toFixed(1)+'%',Math.round(cov.n_btc_positions)+' pos · '+Math.round(cov.n_wallets_queried)+' wallets'),
    kpiCard('Signal confidence',cf[0],'coverage × sample depth',cf[1]),
  ].join('');
  setBadge('regime-badge',s.regime.toUpperCase(),regC);
  setBadge('conf-badge',cf[0],cf[1]);
}

function renderLadder(d){
  const L=d.ladder, mark=d.market.mark_px;
  const traces=[
    {x:L.long,y:L.price,type:'scatter',mode:'lines',name:'long',customdata:L.long_win,
     line:{color:LONG,width:2,shape:'spline',smoothing:0.8},fill:'tozerox',fillcolor:'rgba(63,185,140,0.18)',
     hovertemplate:'long ≈ $%{customdata:,.0f}<extra></extra>'},
    {x:L.short,y:L.price,type:'scatter',mode:'lines',name:'short',customdata:L.short_win,
     line:{color:SHORT,width:2,shape:'spline',smoothing:0.8},fill:'tozerox',fillcolor:'rgba(229,86,78,0.18)',
     hovertemplate:'short ≈ $%{customdata:,.0f}<extra></extra>'}];
  const lay=baseLayout('Liquidation density by price level — where open BTC leverage triggers');
  lay.height=540; lay.hovermode='y unified';
  lay.xaxis=Object.assign(lay.xaxis,{title:{text:'Liquidable notional density (smoothed) — area ∝ notional',font:{color:MUTED,size:11}},tickprefix:'$',tickformat:'~s',rangemode:'tozero',zeroline:false});
  lay.yaxis=Object.assign(lay.yaxis,{title:{text:'Liquidation price (USD)',font:{color:MUTED,size:11}},tickprefix:'$',tickformat:',.0f',showgrid:false,
     showspikes:true,spikemode:'across',spikethickness:1,spikecolor:GOLD,spikedash:'dot',spikesnap:'cursor'});
  lay.shapes=[{type:'rect',xref:'paper',x0:0,x1:1,yref:'y',y0:mark*0.95,y1:mark*1.05,fillcolor:GOLD,opacity:0.05,line:{width:0}},
              {type:'line',xref:'paper',x0:0,x1:1,yref:'y',y0:mark,y1:mark,line:{color:GOLD,width:1.3,dash:'dot'}}];
  lay.annotations=[{xref:'paper',x:1.005,xanchor:'left',y:mark,yref:'y',text:'mark<br>$'+num(mark),showarrow:false,align:'left',font:{color:GOLD,family:MONO,size:10.5}}];
  Plotly.react('ladder',traces,lay,CFG);
}

function renderGauge(d){
  const cfi=d.signals.cfi;
  const tr={type:'indicator',mode:'gauge+number',value:cfi,number:{font:{size:40,color:FONT,family:MONO},suffix:'/100'},
    gauge:{axis:{range:[0,100],tickcolor:MUTED,tickfont:{color:MUTED,size:10}},bar:{color:FONT,thickness:0.22},
      bgcolor:'rgba(0,0,0,0)',borderwidth:0,
      steps:[{range:[0,25],color:'rgba(63,185,140,0.30)'},{range:[25,50],color:'rgba(232,161,58,0.30)'},{range:[50,100],color:'rgba(229,86,78,0.32)'}],
      threshold:{line:{color:GOLD,width:4},value:cfi}}};
  Plotly.react('gauge',[tr],{paper_bgcolor:'rgba(0,0,0,0)',font:{color:FONT,family:MONO},margin:{l:26,r:26,t:18,b:6},height:250},CFG);
}

function renderHistory(d){
  const H=d.history, x=H.map(p=>p.t), y=H.map(p=>p.cfi);
  const lay=baseLayout('Cascade Fragility Index — accumulating every ~10 min');
  lay.height=250; lay.margin={l:50,r:20,t:42,b:34};
  lay.yaxis=Object.assign(lay.yaxis,{range:[0,100],title:{text:'CFI',font:{color:MUTED,size:11}}});
  lay.xaxis=Object.assign(lay.xaxis,{title:'',type:'date'});
  lay.shapes=[{type:'rect',xref:'paper',x0:0,x1:1,yref:'y',y0:0,y1:25,fillcolor:LONG,opacity:0.07,line:{width:0}},
              {type:'rect',xref:'paper',x0:0,x1:1,yref:'y',y0:25,y1:50,fillcolor:GOLD,opacity:0.07,line:{width:0}},
              {type:'rect',xref:'paper',x0:0,x1:1,yref:'y',y0:50,y1:100,fillcolor:SHORT,opacity:0.07,line:{width:0}}];
  const tr={x:x,y:y,type:'scatter',mode:'lines+markers',line:{color:GOLD,width:2,shape:'spline',smoothing:0.4},
    marker:{size:5,color:GOLD},fill:'tozeroy',fillcolor:'rgba(232,161,58,0.07)',
    hovertemplate:'%{x|%b %d %H:%M}<br>CFI %{y:.1f}<extra></extra>'};
  Plotly.react('history',[tr],lay,CFG);
}

function renderHeatmap(d){
  const hm=d.heatmap, el=document.getElementById('heatmap');
  if(!hm||!hm.x||hm.x.length===0){ el.innerHTML='<div class="empty">heatmap builds up as hourly snapshots accumulate…</div>'; return; }
  const heat={type:'heatmap',x:hm.x,y:hm.y,z:hm.z,zsmooth:'best',
    colorscale:[[0,'rgba(11,10,9,0)'],[0.12,'rgba(80,55,22,0.65)'],[0.40,'#a86a1f'],[0.72,'#e8a13a'],[1,'#e5564e']],
    colorbar:{title:{text:'liq $ (mean/hr)',side:'right',font:{color:MUTED,size:9}},tickfont:{color:MUTED,size:9},thickness:9,len:0.9,outlinewidth:0,tickprefix:'$',tickformat:'~s'},
    hovertemplate:'%{x|%b %d · %Hh}<br>$%{y:,.0f}<br>liq ≈ $%{z:,.0f}<extra></extra>'};
  const markline={x:hm.x,y:hm.mark,type:'scatter',mode:'lines',name:'mark',
    line:{color:'#f4ece0',width:1.5},hovertemplate:'mark $%{y:,.0f}<extra></extra>'};
  const lay=baseLayout('Liquidation heatmap — hourly liquidable notional by price (white = mark)');
  lay.height=540; lay.margin={l:74,r:30,t:42,b:46};
  lay.yaxis=Object.assign(lay.yaxis,{title:{text:'Price (USD)',font:{color:MUTED,size:11}},tickprefix:'$',tickformat:',.0f'});
  lay.xaxis=Object.assign(lay.xaxis,{title:{text:'Time (UTC, hourly)',font:{color:MUTED,size:11}},type:'date'});
  Plotly.react('heatmap',[heat,markline],lay,CFG);
}

function renderQuality(d){
  const cov=d.coverage,q=d.quality,m=d.market,s=d.signals;
  setText('q-cov',(cov.coverage_ratio*100).toFixed(1)+'%');
  setText('q-pos',Math.round(cov.n_btc_positions)+' / '+Math.round(cov.n_wallets_queried));
  setText('q-null',q.n_null_liqpx+' dropped'); setText('q-dust',q.n_dust_filtered+' dropped');
  setText('q-far',q.n_far_filtered+' dropped');
  setText('q-recon','sampled '+money(cov.sampled_notional_usd)+' vs OI '+money(m.oi_usd));
  setText('q-cov2',(cov.coverage_ratio*100).toFixed(0)+'%');
  setText('foot-live','BTC $'+num(m.mark_px)+' · CFI '+s.cfi.toFixed(1)+' · asymmetry '+(s.asymmetry>=0?'+':'')+s.asymmetry.toFixed(2)+' · coverage '+(cov.coverage_ratio*100).toFixed(1)+'%');
}

function setFresh(){ if(!GEN)return;
  const mins=Math.max(0,Math.round((Date.now()-GEN.getTime())/60000));
  const label=mins<1?'just now':(mins<60?mins+' min ago':Math.floor(mins/60)+'h '+(mins%60)+'m ago');
  const el=document.getElementById('freshness'); if(el)el.innerHTML='updated <b>'+label+'</b>'+(mins>25?' · STALE':'');
}

function renderAll(d){
  GEN=new Date(d.generated_at);
  renderKpis(d); renderLadder(d); renderGauge(d); renderHistory(d); renderHeatmap(d); renderQuality(d); setFresh();
  if(VIEW==='heatmap') Plotly.Plots.resize('heatmap'); else Plotly.Plots.resize('ladder');
}

function showView(v){ VIEW=v;
  document.getElementById('ladder-wrap').style.display = v==='heatmap'?'none':'block';
  document.getElementById('heatmap-wrap').style.display = v==='heatmap'?'block':'none';
  document.querySelectorAll('.toggle-btn').forEach(b=>b.classList.toggle('active',b.dataset.v===v));
  Plotly.Plots.resize(v==='heatmap'?'heatmap':'ladder');
}

async function poll(){
  try{
    const r=await fetch('./data.json?t='+Date.now(),{cache:'no-store'}); if(!r.ok)return;
    const j=await r.json();
    if(j && j.generated_at!==window.__DATA__.generated_at){
      window.__DATA__=j; renderAll(j);
      const f=document.getElementById('freshness'); if(f){f.classList.remove('flash'); void f.offsetWidth; f.classList.add('flash');}
    }
  }catch(e){}
}

// Manual "↻" button. By default it force-refreshes to the freshest committed
// point and polls aggressively for ~3 min to catch a brand-new one the moment it lands.
// Set DISPATCH_URL to a tiny workflow-dispatch proxy (see workers/dispatch-worker.js)
// to make the button trigger a REAL on-demand extraction.
const DISPATCH_URL = "";
async function manualUpdate(){
  const btn=document.getElementById('update-btn'); if(!btn)return;
  btn.disabled=true; const orig=btn.textContent; btn.textContent='↻ updating…';
  const startGen=window.__DATA__.generated_at, t0=Date.now();
  if(DISPATCH_URL){ try{ await fetch(DISPATCH_URL,{method:'POST',mode:'cors'}); }catch(e){} }
  const iv=setInterval(async()=>{
    try{
      const r=await fetch('./data.json?t='+Date.now(),{cache:'no-store'}); const j=await r.json();
      if(j && j.generated_at!==window.__DATA__.generated_at){
        window.__DATA__=j; renderAll(j);
        const f=document.getElementById('freshness'); if(f){f.classList.remove('flash'); void f.offsetWidth; f.classList.add('flash');}
      }
      if((j && j.generated_at!==startGen) || Date.now()-t0>180000){ clearInterval(iv); btn.disabled=false; btn.textContent=orig; }
    }catch(e){ if(Date.now()-t0>180000){ clearInterval(iv); btn.disabled=false; btn.textContent=orig; } }
  },4000);
}

document.querySelectorAll('.toggle-btn').forEach(b=>b.addEventListener('click',()=>showView(b.dataset.v)));
const _ub=document.getElementById('update-btn'); if(_ub) _ub.addEventListener('click',manualUpdate);
renderAll(window.__DATA__);
setInterval(poll,60000);
setInterval(setFresh,20000);
"""


# --------------------------------------------------------------- HTML assembly
def _hero(snapshot: dict[str, Any]) -> str:
    regime = snapshot["signals"]["regime"]
    regime_color = {"calm": "var(--long)", "elevated": "var(--gold)",
                    "fragile": "var(--short)"}[regime]
    conf, conf_color = _confidence(snapshot)
    return f"""
<header class="reveal" style="animation-delay:.02s">
  <div class="eyebrow"><span>Faro · Head of Data challenge</span><span class="dot"></span>
    <span>Hyperliquid · BTC perps</span><span class="dot"></span><span>live proof of concept</span></div>
  <h1>Liquidation Pressure Map
      <span class="l2"><span class="amp">&amp;</span> Cascade Fragility Index</span></h1>
  <p class="dek">A <em>forward-looking</em> risk metric: where the currently-open BTC
     leverage on Hyperliquid would be force-liquidated — and how fragile that makes the
     market structure right now. Updates itself every ~10 min, no reload.</p>
  <div class="rail">
     <span class="tag">regime <b id="regime-badge" style="color:{regime_color}">{regime.upper()}</b></span>
     <span class="tag">confidence <b id="conf-badge" style="color:{conf_color}">{conf}</b></span>
     <span class="tag"><span class="live-dot"></span><span id="freshness">live</span></span>
     <span class="tag">auto-refresh <b>~60s</b></span>
     <button id="update-btn" class="tag btn" title="Fetch the freshest point now (a new point is produced every ~10 min; configure a dispatch endpoint for true on-demand extraction)">↻</button>
  </div>
</header>
<div class="rule reveal" style="animation-delay:.06s"></div>
"""


def _charts() -> str:
    return """
<div class="fig-toggle reveal" style="animation-delay:.12s">
  <button class="toggle-btn active" data-v="live">▸ Live map</button>
  <button class="toggle-btn" data-v="heatmap">▦ Heatmap · over time</button>
</div>
<figure class="chart reveal" style="animation-delay:.14s">
  <div id="ladder-wrap">
    <figcaption><span>Fig.01 — Liquidation density</span><b>hover for price · long · short</b></figcaption>
    <div class="legend">
      <span class="lg"><i style="background:var(--long)"></i>Long liquidations · flush risk (below mark)</span>
      <span class="lg"><i style="background:var(--short)"></i>Short liquidations · squeeze risk (above mark)</span>
    </div>
    <div id="ladder"></div>
  </div>
  <div id="heatmap-wrap" style="display:none">
    <figcaption><span>Fig.01b — Liquidation heatmap</span><b>hourly · since first data</b></figcaption>
    <div id="heatmap"></div>
  </div>
</figure>
<div class="two">
  <figure class="chart reveal" style="animation-delay:.18s">
    <figcaption><span>Fig.02 — Fragility gauge</span><b>0–100</b></figcaption><div id="gauge"></div></figure>
  <figure class="chart reveal" style="animation-delay:.22s">
    <figcaption><span>Fig.03 — CFI history</span><b>regime bands</b></figcaption><div id="history"></div></figure>
</div>
"""


def _kpis_initial(snapshot: dict[str, Any]) -> str:
    """Server-rendered KPI cards for first paint / no-JS fallback (JS re-renders)."""
    m, sig, cov = snapshot["market"], snapshot["signals"], snapshot["coverage"]
    fa = m["funding_hourly"] * 24 * 365
    asym = sig["asymmetry"]
    bias = "short side · upside-squeeze fuel" if asym > 0 else "long side · downside-flush fuel"
    bias_c = "var(--short)" if asym > 0 else "var(--long)"
    reg_c = {"calm": "var(--long)", "elevated": "var(--gold)", "fragile": "var(--short)"}[sig["regime"]]
    conf, conf_c = _confidence(snapshot)
    w5 = sig["near_band_usd"]["0.05"]["total"]
    w2 = sig["near_band_usd"]["0.02"]["total"]
    return "".join([
        _kpi("BTC mark", f"${m['mark_px']:,.0f}", f"oracle ${m['oracle_px']:,.0f}"),
        _kpi("Cascade Fragility Index", f"{sig['cfi']:.1f}<span class='u'>/100</span>",
             f"regime · {sig['regime'].upper()}", reg_c),
        _kpi("Long / short asymmetry", f"{asym:+.2f}", bias, bias_c),
        _kpi("Liquidable within ±5%", _money(w5), f"±2% · {_money(w2)}", "var(--gold)"),
        _kpi("Funding (annualized)", f"{fa:+.1%}", f"{m['funding_hourly']:+.4%} / h"),
        _kpi("Open interest", _money(m["oi_usd"]), f"{m['oi_btc']:,.0f} BTC"),
        _kpi("Sample coverage", f"{cov['coverage_ratio']:.1%}",
             f"{int(cov['n_btc_positions'])} pos · {int(cov['n_wallets_queried'])} wallets"),
        _kpi("Signal confidence", conf, "coverage × sample depth", conf_c),
    ])


def _memo(snapshot: dict[str, Any]) -> str:
    m, cov, q, prov = (snapshot["market"], snapshot["coverage"],
                       snapshot["quality"], snapshot["provenance"])
    return f"""
<section class="memo reveal" style="animation-delay:.10s">
  <div class="sec-head"><span class="sec-num">01</span><h2>Product &amp; Data Insight</h2></div>
<p><strong>The metric.</strong> A <strong>Liquidation Pressure Map</strong>: a density map of
the price levels at which <em>currently-open</em> leveraged BTC positions on Hyperliquid would
be force-liquidated, split by side (longs liquidate below price, shorts above). From it we
derive the <strong>Cascade Fragility Index (CFI, 0–100)</strong> — how much liquidable notional
sits <em>close</em> to the mark — and the <strong>Long/Short Asymmetry (−1…+1)</strong>.</p>
<div class="callout"><p><strong>The specific gap in Faro.</strong> Faro already surfaces
<em>Liquidation Volume</em> — liquidations that <strong>already executed</strong> (a
backward-looking flow). This map is its orthogonal complement: <strong>latent</strong>
liquidation risk from positions still <strong>open</strong>, located <strong>at the prices</strong>
where they would trigger. Volume tells you the cascade <em>happened</em>; this tells you the fuel
is <em>there, at these levels, right now</em>.</p></div>
<p><strong>Trader question.</strong> “Is risk building beneath the surface, and where are the
trigger levels?” Which side is more vulnerable to a squeeze; is structure fragile or resilient
<em>before</em> the move. <strong>Most relevant for</strong> BTC perps intraday-to-swing risk:
stop placement, squeeze hunting, sizing into crowded structure. <strong>Source:</strong>
<code>metaAndAssetCtxs</code> (mark/oi/funding) + <code>clearinghouseState</code> per wallet
(<code>szi</code>, <code>liquidationPx</code>, <code>positionValue</code>); wallet universe from
the leaderboard feed (PoC), fills-WebSocket discovery in production.</p>

  <div class="sec-head" style="margin-top:38px"><span class="sec-num">02</span><h2>Pipeline — Airflow DAG</h2></div>
<p>The DAG <code>liquidation_pressure_dag.py</code> runs on a fixed cadence as clear tasks; the
same functions drive the live runner.</p>
<table>
<tr><th>Stage</th><th>Does</th><th>Reliability concern handled</th></tr>
<tr><td>extract_market_context</td><td>mark / oi / funding</td><td>retry+backoff; anchor for distances</td></tr>
<tr><td>extract_positions</td><td>clearinghouseState over the universe</td><td>per-wallet errors tolerated &amp; counted</td></tr>
<tr><td>validate</td><td>schema, range, null/dust, freshness</td><td>bad rows quarantined; thin samples flagged</td></tr>
<tr><td>transform</td><td>map + CFI + asymmetry</td><td>pure function, unit-testable</td></tr>
<tr><td>load</td><td>JSON + CSV/JSONL series + SQLite mirror</td><td>append-only, union, idempotent</td></tr>
</table>
<p><strong>Backfill</strong>: the map is <em>not</em> backfillable (the API returns only current
account state), so the CFI series accumulates forward; only wallet selection uses history.</p>

  <div class="sec-head" style="margin-top:38px"><span class="sec-num">03</span><h2>Trader-Facing Read</h2></div>
<p>Cascades are reflexive: a liquidation pushes price into the next one. The danger is not the
<em>amount</em> of leverage but <em>how close its triggers sit to price</em> — which is what the
CFI measures.</p>
<ul>
<li><strong style="color:var(--short)">Bullish / squeeze:</strong> asymmetry strongly positive,
dense short cluster just above price → an upside poke can ignite a squeeze.</li>
<li><strong style="color:var(--long)">Bearish / flush:</strong> asymmetry negative, dense long
cluster just below → a downside poke can cascade into a flush.</li>
<li><strong style="color:var(--gold)">Risk-warning:</strong> CFI in the fragile band = large
liquidable notional within a few % of price; small moves can turn violent.</li>
</ul>
<p><strong>Not for</strong> price forecasting or timing — clusters can sit unlit and act as
magnets, not walls; it is a conditional risk geography over a sample. <strong>Pair with</strong>
funding/OI, spot aggressor flow, and Faro’s realized Liquidation Volume.</p>

  <div class="sec-head" style="margin-top:38px"><span class="sec-num">04</span><h2>Visualization in Faro</h2></div>
<ul>
<li><strong>Chart:</strong> the density ladder (long vs short, mark line, ±5% band) with a hover
crosshair, a toggle to the time × price heatmap, plus a CFI gauge and regime-banded history.</li>
<li><strong>Overlays:</strong> mark/oracle, funding sign, OI; optionally Faro’s realized
Liquidation Volume on the same price axis (fuel → ignition).</li>
<li><strong>Placement:</strong> the BTC perp / derivatives page, positioning &amp; risk tab; and an
agent-readable signal (regime + asymmetry + nearest cluster) with a confidence level.</li>
</ul>

  <div class="sec-head" style="margin-top:38px"><span class="sec-num">05</span><h2>Data Quality, Freshness &amp; Reconciliation</h2></div>
<table>
<tr><th>Check</th><th>Value</th><th>Handling</th></tr>
<tr><td>Freshness</td><td><span id="fresh-cell">live</span></td><td>stale &gt; 2 cadences → alert + banner</td></tr>
<tr><td>Coverage vs OI</td><td><span id="q-cov">{cov['coverage_ratio']:.1%}</span></td><td>explicit bound, never hidden</td></tr>
<tr><td>Positions / wallets</td><td><span id="q-pos">{int(cov['n_btc_positions'])} / {int(cov['n_wallets_queried'])}</span></td><td>thin sample → confidence downgraded</td></tr>
<tr><td>liquidationPx = null</td><td><span id="q-null">{q['n_null_liqpx']} dropped</span></td><td>cross-margin; not placeable per-position</td></tr>
<tr><td>Dust (&lt; $10k)</td><td><span id="q-dust">{q['n_dust_filtered']} dropped</span></td><td>dust returns garbage liquidationPx</td></tr>
<tr><td>Far / degenerate (&gt; 60%)</td><td><span id="q-far">{q['n_far_filtered']} dropped</span></td><td>numerically unreliable</td></tr>
</table>
<p><strong>Reconciliation.</strong> No endpoint returns the full book, so we reconcile what we
<em>can</em>: <span id="q-recon">sampled {_money(cov['sampled_notional_usd'])} vs OI {_money(m['oi_usd'])}</span>
— the coverage ratio — and mark vs oracle drift. Honest statement: <em>a high-coverage sample of
the most active wallets, not a census.</em></p>
<p><strong>History integrity.</strong> Every CFI reading is appended to an immutable ledger (union
semantics, atomic writes) and snapshotted to git each run, and each snapshot's full histogram is
kept for the heatmap — so nothing is silently dropped and the whole series is rebuildable from git
history via <code>rebuild_history.py</code>.</p>
<div class="callout warn"><p><strong>Provenance &amp; caveat.</strong> {prov['caveat']} The
leaderboard/activity feed used for wallet discovery is an <em>undocumented</em> Hyperliquid frontend
endpoint, a PoC stand-in for production fills-WebSocket discovery.</p></div>

  <div class="sec-head" style="margin-top:38px"><span class="sec-num">06</span><h2>Methodology</h2></div>
<p>For each qualifying position with liquidable notional <em>N</em> and distance
<em>d = |liqPx − mark| / mark</em>, proximity weight <code>K(d) = exp(−d/τ)</code>, τ = 8%.
<strong>CFI</strong> = 100 · Σ N·K(d) / Σ N (notional-weighted average proximity; smooth kernel so
a dust position on the mark can’t blow it up). <strong>Asymmetry</strong> = (short_pressure −
long_pressure) / (sum). The Fig.01 curve is gaussian-smoothed for legibility (area ∝ notional);
hover reports real notional within ±1.25%. Regime bands (calm &lt; 25, elevated &lt; 50, else
fragile) are illustrative pending calibration on the accumulating history.</p>

  <div class="sec-head" style="margin-top:38px"><span class="sec-num">07</span><h2>Wallet Universe, Coverage &amp; Path to Production</h2></div>
<h3>How the sample is built (current PoC)</h3>
<p><strong>Sequence.</strong> (1) Pull the public leaderboard/activity feed (~38k addresses).
(2) Rank by <strong>activity_score = week volume + month volume</strong> and take the
<strong>top 2,000</strong> — turnover is the best proxy for wallets actively carrying
directional perp risk. (3) Query their live state concurrently via
<code>clearinghouseState</code>, rate-limited to ~7.5 req/s (≈75% of the 1,200-weight/min
budget; that call costs weight 2). (4) Keep those holding a qualifying open BTC position
(notional ≥ $10k, <code>liquidationPx</code> present, within 60% of mark) — typically a few
hundred at any instant — and aggregate them into the map.</p>
<p><strong>Two selection layers, made explicit.</strong> The <em>2,000</em> are chosen once a
day by activity; the on-map subset is <em>not</em> a second pick — it is simply whichever of the
2,000 currently hold a live qualifying BTC position, which changes every snapshot as positions
open and close.</p>
<h3>Why this sample is enough for the PoC</h3>
<p>Liquidable open interest is heavily concentrated in the most active wallets, so a
high-activity sample captures the cascade-relevant structure — the large, near-price clusters
that actually move price — while the long tail is many tiny positions that barely affect a
cascade. This run reaches <strong><span id="q-cov2">{cov['coverage_ratio']:.0%}</span> of
reported BTC OI</strong>. (Exchange OI is one-sided, so total open-position notional ≈ 2× OI;
~half of reported OI is still a large, representative slice of what matters.)</p>
<h3>Keeping it relevant over time</h3>
<p>The universe is <strong>rebuilt daily</strong>, re-ranked by recent activity, so wallets that
go quiet drop out and newly-active ones enter automatically — the list can't rot into dormant
addresses. Staleness is tracked by a persisted build timestamp (robust to CI file mtimes), not
the file date.</p>
<h3>What “the real metric” looks like (production &amp; backfill — described, not built)</h3>
<ul>
<li><strong>Full population, not a sample:</strong> subscribe to the Hyperliquid fills WebSocket
and keep a rolling registry of <em>every</em> address trading BTC, ranked by trailing volume —
discovery becomes continuous instead of a daily leaderboard snapshot.</li>
<li><strong>Event-sourced position engine:</strong> derive each wallet's open position by applying
its fills + funding + liquidations in real time, instead of polling <code>clearinghouseState</code>
per wallet. This scales to the whole market and lifts the rate-limit ceiling on coverage → ~100%
of OI.</li>
<li><strong>Historical backfill:</strong> replay the historical fill/funding/liquidation tape
(per-wallet <code>userFillsByTime</code> plus an archival node / data export for the full feed)
from each wallet's first trade forward, reconstructing the open-position book — and thus the
liquidation map and CFI — at any past timestamp. Caveat: per-wallet fill history is capped, so
genuine genesis-to-now reconstruction needs the archival feed and a snapshot anchor for positions
opened before the available window.</li>
<li><strong>Continuous reconciliation:</strong> check the engine's derived aggregate OI against the
exchange's reported OI per asset every cycle as a live data-quality gate; divergence flags a missed
fill or a bug.</li>
</ul>
</section>
<footer>
  <span class="live" id="foot-live">BTC ${m['mark_px']:,.0f}</span><br/>
  Proof-of-concept for the Faro Head of Data challenge. Not investment advice — a sampled,
  forward-looking risk estimate with the caveats stated above.
</footer>
"""


def render_html(payload: dict[str, Any], snapshot: dict[str, Any]) -> str:
    head = (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'/>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'/>"
        "<title>Liquidation Pressure Map &amp; Cascade Fragility Index — BTC · Hyperliquid</title>"
        "<link rel='preconnect' href='https://fonts.googleapis.com'>"
        "<link rel='preconnect' href='https://fonts.gstatic.com' crossorigin>"
        "<link href='https://fonts.googleapis.com/css2?family=Fraunces:ital,opsz,wght@0,9..144,400;0,9..144,600;1,9..144,400&family=Hanken+Grotesk:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500;600&display=swap' rel='stylesheet'>"
        "<script src='https://cdn.plot.ly/plotly-2.35.2.min.js' charset='utf-8'></script>"
        "<style>" + _CSS + "</style></head>"
    )
    body = ("<body><div class='wrap'>"
            + _hero(snapshot)
            + '<div id="kpis" class="kpis reveal" style="animation-delay:.10s">'
            + _kpis_initial(snapshot) + "</div>"
            + _charts()
            + _memo(snapshot)
            + "</div>")
    tail = ("<script>window.__DATA__=" + json.dumps(payload) + ";</script>"
            + "<script>" + _JS + "</script></body></html>")
    return head + body + tail


# --------------------------------------------------------------- entrypoints
def generate_site(snapshot_path: str = LATEST_SNAPSHOT_JSON, out_path: str = OUT_HTML,
                  data_path: str = SITE_DATA) -> str:
    if not os.path.exists(snapshot_path):
        raise FileNotFoundError(f"No snapshot at {snapshot_path}; run the pipeline first.")
    with open(snapshot_path) as f:
        snapshot = json.load(f)
    history = load_metrics_history()
    heatmap = build_heatmap(load_map_history())
    payload = build_payload(snapshot, history, heatmap)

    os.makedirs(os.path.dirname(data_path), exist_ok=True)
    with open(data_path, "w") as f:
        json.dump(payload, f)

    try:
        export_memo_png(snapshot)  # best-effort static PNG for the memo
    except Exception as exc:  # noqa: BLE001
        print(f"[render] PNG export skipped: {exc}")

    html = render_html(payload, snapshot)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        f.write(html)
    return out_path


def publish_to_docs(src_html: str = OUT_HTML, src_data: str = SITE_DATA) -> str:
    """Copy the page AND data.json into docs/ — what GitHub Pages serves."""
    import shutil
    os.makedirs(DOCS_DIR, exist_ok=True)
    shutil.copyfile(src_html, DOCS_HTML)
    shutil.copyfile(src_data, DOCS_DATA)
    return DOCS_HTML


if __name__ == "__main__":
    out = generate_site()
    publish_to_docs()
    print("site ->", out, "· data + docs published")
    print("generated at", datetime.now(timezone.utc).isoformat(timespec="seconds"))

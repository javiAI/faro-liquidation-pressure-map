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
from liquidation_map import REGIME_BANDS
from storage import LATEST_SNAPSHOT_JSON, load_map_history, load_metrics_history
from viz import _gaussian_smooth, export_memo_png

# Single source of truth for the regime/confidence visual policy. The thresholds live
# in liquidation_map.REGIME_BANDS; here we add the colour mapping and confidence cutoffs,
# and thread ALL of them into the page via window.__CFG__ so the client JS never hardcodes
# its own copy (the bands are explicitly slated for recalibration).
REGIME_COLORS = {"calm": "var(--long)", "elevated": "var(--gold)", "fragile": "var(--short)"}
CONF_HIGH = (0.30, 40)   # (min coverage_ratio, min positions) → "High"
CONF_MED = (0.15, 20)    # → "Medium"; else "Low"

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
    if cov >= CONF_HIGH[0] and n >= CONF_HIGH[1]:
        return "High", "var(--long)"
    if cov >= CONF_MED[0] and n >= CONF_MED[1]:
        return "Medium", "var(--gold)"
    return "Low", "var(--short)"


def _kpi(label: str, value: str, sub: str = "", accent: str = "") -> str:
    bar = accent or "var(--hair-strong)"
    return (f'<div class="kpi"><span class="kpi-bar" style="background:{bar}"></span>'
            f'<div class="kpi-label">{label}</div>'
            f'<div class="kpi-value">{value}</div>'
            f'<div class="kpi-sub">{sub}</div></div>')


# --------------------------------------------------------------- data payload
CONSISTENT_MIN_WALLETS = 1000   # history/heatmap only show the comparable-coverage era


def consistent_cutoff(history: pd.DataFrame, min_wallets: int = CONSISTENT_MIN_WALLETS):
    """First timestamp at which the wallet universe reached the current (large) size.

    We bumped the universe from a few hundred to ~2,000 wallets mid-flight; points from
    before that have far lower coverage, so mixing them into the CFI series / heatmap is
    apples-to-oranges. Trimming to this cutoff keeps the displayed history consistent.
    """
    if history.empty or "n_wallets" not in history.columns:
        return None
    big = history[history["n_wallets"] >= min_wallets]
    return str(big["timestamp"].min()) if not big.empty else None


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
        "long_raw": [round(float(x)) for x in raw_long],      # for the bar view
        "short_raw": [round(float(x)) for x in raw_short],
        "long_win": [round(float(x)) for x in np.convolve(raw_long, win, mode="same")],
        "short_win": [round(float(x)) for x in np.convolve(raw_short, win, mode="same")],
    }
    hist = []
    if not history.empty:
        for r in history.sort_values("timestamp").itertuples():
            price = float(r.mark_px) if "mark_px" in history.columns else None
            hist.append({"t": r.timestamp, "cfi": float(r.cfi), "price": price})
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
  /* click-to-open info "?" on each card + custom tooltip popover */
  .kpi .info { position:absolute; top:9px; right:9px; width:17px; height:17px; border-radius:50%;
    border:1px solid var(--hair-strong); background:var(--ink-2); color:var(--muted); font-family:var(--mono);
    font-size:10px; line-height:15px; text-align:center; cursor:pointer; padding:0; transition:all .15s ease; }
  .kpi .info:hover, .kpi .info.on { color:#1a1206; background:var(--gold); border-color:var(--gold); }
  .tag.fresh { display:inline-flex; align-items:center; gap:6px; }
  .tag .info { position:static; width:15px; height:15px; line-height:13px; font-size:9.5px; border-radius:50%;
    border:1px solid var(--hair-strong); background:var(--ink-2); color:var(--muted); font-family:var(--mono);
    text-align:center; cursor:pointer; padding:0; }
  .tag .info:hover, .tag .info.on { color:#1a1206; background:var(--gold); border-color:var(--gold); }
  #cardtip { position:absolute; z-index:3000; max-width:280px; background:var(--card-2);
    border:1px solid var(--hair-strong); border-left:2px solid var(--gold); border-radius:11px;
    padding:12px 14px; font-family:var(--body); font-size:12.8px; line-height:1.55; color:#dccfba;
    box-shadow:0 14px 38px rgba(0,0,0,0.55); opacity:0; transform:translateY(-4px); pointer-events:none;
    transition:opacity .15s ease, transform .15s ease; }
  #cardtip.show { opacity:1; transform:none; }
  /* expand-to-fullscreen button + state */
  .expand-btn { font-family:var(--mono); font-size:13px; color:var(--muted); background:var(--ink-2);
    border:1px solid var(--hair); border-radius:7px; width:30px; height:26px; cursor:pointer; transition:all .18s ease; }
  .expand-btn:hover { color:var(--gold); border-color:var(--gold); }
  figure.chart.expanded { position:fixed; inset:2.5vh 2.5vw; z-index:2500; margin:0; overflow:auto;
    box-shadow:0 0 0 100vmax rgba(8,6,4,0.82); }
  body.has-expanded { overflow:hidden; }
  .fig-ctl { display:flex; align-items:center; gap:10px; }
  .row13 { display:grid; grid-template-columns:1fr 2fr; gap:13px; }
  /* primary segmented tab control (clearly switchable) */
  .seg { display:inline-flex; background:var(--ink-2); border:1px solid var(--hair-strong); border-radius:12px;
    padding:4px; gap:4px; margin:20px 0 0; box-shadow:inset 0 1px 0 rgba(255,255,255,0.02); }
  .seg button { font-family:var(--mono); font-size:13px; letter-spacing:.02em; color:var(--muted);
    background:transparent; border:0; padding:9px 20px; border-radius:8px; cursor:pointer; transition:all .2s ease; }
  .seg button:hover { color:var(--bone); }
  .seg button.active { background:var(--gold); color:#1a1206; font-weight:600;
    box-shadow:0 2px 10px rgba(232,161,58,0.25); }
  /* secondary toggle (density/bars, resolution) */
  .subseg { display:inline-flex; gap:6px; }
  .subseg button { font-family:var(--mono); font-size:11px; letter-spacing:.03em; color:var(--muted);
    background:var(--ink-2); border:1px solid var(--hair); border-radius:7px; padding:5px 11px; cursor:pointer; transition:all .18s ease; }
  .subseg button:hover { color:var(--bone); border-color:var(--hair-strong); }
  .subseg button.active { color:#1a1206; background:var(--gold); border-color:var(--gold); font-weight:600; }
  figure.chart { margin:14px 0 16px; background:linear-gradient(180deg,var(--card),var(--ink-2));
    border:1px solid var(--hair); border-radius:15px; padding:8px 10px 4px; }
  .fig-top { display:flex; align-items:center; justify-content:space-between; gap:12px; padding-right:6px; }
  figcaption { font-family:var(--mono); font-size:11px; letter-spacing:.16em; text-transform:uppercase;
    color:var(--faint); padding:9px 8px 0; display:flex; justify-content:space-between; }
  figcaption b { color:var(--muted); font-weight:500; }
  .explain { font-size:13px; line-height:1.55; color:var(--muted); padding:6px 10px 2px; max-width:78ch; margin:0; }
  .explain b { color:#cabfae; font-weight:500; }
  .legend { display:flex; gap:18px; padding:6px 10px 0; flex-wrap:wrap; }
  .lg { font-family:var(--mono); font-size:11.5px; color:var(--muted); display:inline-flex; align-items:center; gap:7px; }
  .lg i { width:18px; height:3px; border-radius:2px; display:inline-block; }
  #ladder,#heatmap { min-height:540px; } #cfiprice,#gauge { min-height:340px; }
  .empty { color:var(--muted); font-family:var(--mono); font-size:13px; text-align:center; padding:120px 20px; }
  .modebar { background:transparent !important; }
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
  @media (max-width:820px) { .kpis { grid-template-columns:repeat(2,1fr); } .row13 { grid-template-columns:1fr; } }
"""

_JS = r"""
const LONG='#3fb98c', SHORT='#e5564e', GOLD='#e8a13a', FONT='#ece6da', MUTED='#9a8f7d',
      GRID='rgba(150,128,92,0.13)', MONO='IBM Plex Mono, ui-monospace, monospace';
const CFG={displayModeBar:'hover', scrollZoom:true, displaylogo:false,
  modeBarButtonsToRemove:['lasso2d','select2d','autoScale2d','toggleSpikelines'], responsive:true};
const POLICY=window.__CFG__||{};   // regime bands/colors + confidence cutoffs (single source)
let GEN=null, VIEW='live', LADDER_MODE='density', HEAT_RES='hour';

function money(x){const a=Math.abs(x);
  if(a>=1e9)return '$'+(x/1e9).toFixed(2)+'B'; if(a>=1e6)return '$'+(x/1e6).toFixed(2)+'M';
  if(a>=1e3)return '$'+(x/1e3).toFixed(2)+'k'; return '$'+Math.round(x).toLocaleString();}
function num(x){return Math.round(x).toLocaleString();}
function baseLayout(title){return {
  title:{text:title, font:{size:14,color:FONT,family:MONO}, x:0.01},
  paper_bgcolor:'rgba(0,0,0,0)', plot_bgcolor:'rgba(0,0,0,0)',
  font:{color:FONT,family:MONO,size:12}, margin:{l:74,r:64,t:42,b:46}, showlegend:false,
  modebar:{bgcolor:'rgba(0,0,0,0)',color:MUTED,activecolor:GOLD},
  hoverlabel:{bgcolor:'#15120f',bordercolor:GRID,font:{family:MONO,color:FONT}},
  xaxis:{gridcolor:GRID,zerolinecolor:GRID,linecolor:GRID,tickfont:{color:MUTED,size:11}},
  yaxis:{gridcolor:GRID,zerolinecolor:GRID,linecolor:GRID,tickfont:{color:MUTED,size:11}} };}

function confidence(d){const c=d.coverage.coverage_ratio,n=d.coverage.n_btc_positions,k=POLICY.conf;
  if(c>=k.highCov&&n>=k.highN)return['High','var(--long)']; if(c>=k.medCov&&n>=k.medN)return['Medium','var(--gold)'];
  return['Low','var(--short)'];}
function kpiCard(label,value,sub,accent,tip){
  const info = tip ? '<button class="info" data-tip="'+tip.replace(/"/g,'&quot;')+'">?</button>' : '';
  return '<div class="kpi">'+info+'<span class="kpi-bar" style="background:'+(accent||'var(--hair-strong)')+
    '"></span><div class="kpi-label">'+label+'</div><div class="kpi-value">'+value+'</div><div class="kpi-sub">'+sub+'</div></div>';}
function setText(id,t){const e=document.getElementById(id); if(e)e.textContent=t;}
function setBadge(id,t,c){const e=document.getElementById(id); if(e){e.textContent=t; e.style.color=c;}}

function renderKpis(d){
  // Ordered by trader priority: the signal first (what + which side + how much),
  // then market context, then how much of the market we see + how much to trust it.
  const m=d.market,s=d.signals,cov=d.coverage, fa=m.funding_hourly*24*365, asym=s.asymmetry;
  const bias=asym>0?'shorts more exposed · squeeze-up fuel':'longs more exposed · flush-down fuel';
  const biasC=asym>0?'var(--short)':'var(--long)';
  const regC=POLICY.regimeColors[s.regime];
  const cf=confidence(d), k=POLICY.conf;
  const w5=s.near_band_usd['0.05'].total, w2=s.near_band_usd['0.02'].total;
  const covPct=cov.coverage_ratio*100;
  document.getElementById('kpis').innerHTML=[
    kpiCard('Cascade Fragility Index',s.cfi.toFixed(1)+"<span class='u'>/100</span>",
      'regime · '+s.regime.toUpperCase(),regC,
      'How explosive the setup is. Low: the liquidation triggers sit far from the price, so a move fizzles out. High: they are stacked right on top of the price, so a small move can snowball. The colour is the regime — green calm, amber building, red fragile.'),
    kpiCard('Long / short asymmetry',(asym>=0?'+':'')+asym.toFixed(2),bias,biasC,
      'Which side is more exposed. Positive: more shorts would be liquidated just above the price (a squeeze higher). Negative: more longs would be liquidated just below (a flush lower). Near zero is balanced.'),
    kpiCard('Liquidable within ±5%',money(w5),'±2% · '+money(w2),'',
      'How much money gets force-liquidated if the price moves about 5% from here (and the tighter 2%) — the fuel sitting closest to the current price.'),
    kpiCard('BTC mark','$'+num(m.mark_px),'oracle $'+num(m.oracle_px),'',
      'The price Hyperliquid uses to decide liquidations (the mark price), shown next to its oracle price.'),
    kpiCard('Open interest',money(m.oi_usd),num(m.oi_btc)+' BTC','',
      'The total size of all open BTC perpetual positions on Hyperliquid right now.'),
    kpiCard('Funding (annualized)',(fa>=0?'+':'')+(fa*100).toFixed(1)+'%',
      (m.funding_hourly>=0?'+':'')+(m.funding_hourly*100).toFixed(4)+'% / h','',
      'What perp traders pay each other to keep a position open, shown as a yearly rate. Positive means longs are paying shorts — the crowd is leaning long.'),
    kpiCard('OI captured',covPct.toFixed(0)+'%',money(cov.sampled_notional_usd)+' of '+money(m.oi_usd)+' OI','',
      'How much of the whole market we can see. We track the most active wallets; this is the share of total open interest they currently make up.'),
    kpiCard('Signal confidence',cf[0],covPct.toFixed(0)+'% coverage · '+Math.round(cov.n_btc_positions)+' positions',cf[1],
      'How much to trust this reading. We only see a sample of wallets, so the more of the market we cover and the more live positions we find, the higher the confidence — High, Medium or Low.'),
  ].join('');
  setBadge('regime-badge',s.regime.toUpperCase(),regC);
  setBadge('conf-badge',cf[0],cf[1]);
}

function renderLadder(d){
  const L=d.ladder, mark=d.market.mark_px, bars=(LADDER_MODE==='bars');
  let traces;
  if(bars){
    traces=[
      {x:L.long_raw,y:L.price,type:'bar',orientation:'h',name:'long',marker:{color:'rgba(63,185,140,0.85)'},
       hovertemplate:'$%{y:,.0f}<br>long $%{x:,.0f}<extra></extra>'},
      {x:L.short_raw,y:L.price,type:'bar',orientation:'h',name:'short',marker:{color:'rgba(229,86,78,0.85)'},
       hovertemplate:'$%{y:,.0f}<br>short $%{x:,.0f}<extra></extra>'}];
  } else {
    traces=[
      {x:L.long,y:L.price,type:'scatter',mode:'lines',name:'long',customdata:L.long_win,
       line:{color:LONG,width:2,shape:'spline',smoothing:0.8},fill:'tozerox',fillcolor:'rgba(63,185,140,0.18)',
       hovertemplate:'long ≈ $%{customdata:,.0f}<extra></extra>'},
      {x:L.short,y:L.price,type:'scatter',mode:'lines',name:'short',customdata:L.short_win,
       line:{color:SHORT,width:2,shape:'spline',smoothing:0.8},fill:'tozerox',fillcolor:'rgba(229,86,78,0.18)',
       hovertemplate:'short ≈ $%{customdata:,.0f}<extra></extra>'}];
  }
  const lay=baseLayout('');   // figcaption + explanation above name the chart; keep the plot uncluttered
  lay.height=540; lay.margin={l:78,r:66,t:22,b:56}; lay.hovermode='y unified'; if(bars) lay.barmode='overlay';
  lay.xaxis=Object.assign(lay.xaxis,{title:{text:bars?'Liquidable notional per price level (USD)':'Liquidable notional density (smoothed) — area ∝ notional',font:{color:MUTED,size:11}},tickprefix:'$',tickformat:'~s',rangemode:'tozero',zeroline:false});
  lay.yaxis=Object.assign(lay.yaxis,{title:{text:'Liquidation price (USD)',font:{color:MUTED,size:11}},tickprefix:'$',tickformat:',.0f',showgrid:false,
     showspikes:true,spikemode:'across',spikethickness:1,spikecolor:GOLD,spikedash:'dot',spikesnap:'cursor'});
  lay.shapes=[{type:'rect',xref:'paper',x0:0,x1:1,yref:'y',y0:mark*0.95,y1:mark*1.05,fillcolor:GOLD,opacity:0.05,line:{width:0}},
              {type:'line',xref:'paper',x0:0,x1:1,yref:'y',y0:mark,y1:mark,line:{color:GOLD,width:1.3,dash:'dot'}}];
  lay.annotations=[{xref:'paper',x:1.005,xanchor:'left',y:mark,yref:'y',text:'mark<br>$'+num(mark),showarrow:false,align:'left',font:{color:GOLD,family:MONO,size:10.5}}];
  Plotly.react('ladder',traces,lay,CFG);
}

function renderGauge(d){
  const cfi=d.signals.cfi;
  const tr={type:'indicator',mode:'gauge+number',value:cfi,number:{font:{size:38,color:FONT,family:MONO},suffix:'/100'},
    gauge:{axis:{range:[0,100],tickcolor:MUTED,tickfont:{color:MUTED,size:10}},bar:{color:FONT,thickness:0.22},
      bgcolor:'rgba(0,0,0,0)',borderwidth:0,
      steps:[{range:[0,POLICY.calm],color:'rgba(63,185,140,0.30)'},{range:[POLICY.calm,POLICY.elev],color:'rgba(232,161,58,0.30)'},{range:[POLICY.elev,100],color:'rgba(229,86,78,0.32)'}],
      threshold:{line:{color:GOLD,width:4},value:cfi}}};
  Plotly.react('gauge',[tr],{paper_bgcolor:'rgba(0,0,0,0)',font:{color:FONT,family:MONO},
    margin:{l:30,r:30,t:26,b:12},height:340,modebar:{bgcolor:'rgba(0,0,0,0)',color:MUTED,activecolor:GOLD}},CFG);
}

function renderCfiPrice(d){
  // Unified: Cascade Fragility Index (left axis, regime bands) vs BTC price (right axis).
  const H=d.history, x=H.map(p=>p.t), cfi=H.map(p=>p.cfi), price=H.map(p=>p.price);
  const lay=baseLayout('');   // named by the figcaption + explanation above; keep the plot airy
  lay.height=350; lay.margin={l:60,r:66,t:34,b:56}; lay.hovermode='x unified'; lay.showlegend=true;
  lay.legend={orientation:'h',y:1.18,x:0,bgcolor:'rgba(0,0,0,0)',font:{size:11.5,color:MUTED}};
  lay.yaxis=Object.assign(lay.yaxis,{range:[0,100],title:{text:'CFI',font:{color:GOLD,size:11}},tickfont:{color:GOLD,size:11}});
  lay.yaxis2={overlaying:'y',side:'right',tickprefix:'$',tickformat:'~s',showgrid:false,zeroline:false,
    title:{text:'BTC price',font:{color:'#cdbfa8',size:11}},tickfont:{color:'#cdbfa8',size:11}};
  lay.xaxis=Object.assign(lay.xaxis,{type:'date',title:''});
  lay.shapes=[{type:'rect',xref:'paper',x0:0,x1:1,yref:'y',y0:0,y1:POLICY.calm,fillcolor:LONG,opacity:0.06,line:{width:0}},
              {type:'rect',xref:'paper',x0:0,x1:1,yref:'y',y0:POLICY.calm,y1:POLICY.elev,fillcolor:GOLD,opacity:0.06,line:{width:0}},
              {type:'rect',xref:'paper',x0:0,x1:1,yref:'y',y0:POLICY.elev,y1:100,fillcolor:SHORT,opacity:0.06,line:{width:0}}];
  const traces=[
    {x:x,y:cfi,name:'CFI',yaxis:'y',type:'scatter',mode:'lines',line:{color:GOLD,width:2,shape:'spline',smoothing:0.4},
     fill:'tozeroy',fillcolor:'rgba(232,161,58,0.06)',hovertemplate:'CFI %{y:.1f}<extra></extra>'},
    {x:x,y:price,name:'BTC price',yaxis:'y2',type:'scatter',mode:'lines',line:{color:'#cdbfa8',width:1.5},
     hovertemplate:'$%{y:,.0f}<extra></extra>'}];
  Plotly.react('cfiprice',traces,lay,CFG);
}

function renderHeatmap(d){
  const all=d.heatmap, el=document.getElementById('heatmap');
  const hm = all ? all[HEAT_RES] : null;
  if(!hm||!hm.x||hm.x.length===0){ el.innerHTML='<div class="empty">heatmap builds up as snapshots accumulate…</div>'; return; }
  const per = HEAT_RES==='min10'?'10-min':(HEAT_RES==='day'?'day':'hour');
  const heat={type:'heatmap',x:hm.x,y:hm.y,z:hm.z,zsmooth:'best',
    colorscale:[[0,'rgba(11,10,9,0)'],[0.12,'rgba(80,55,22,0.65)'],[0.40,'#a86a1f'],[0.72,'#e8a13a'],[1,'#e5564e']],
    colorbar:{title:{text:'liq $ / '+per,side:'right',font:{color:MUTED,size:9}},tickfont:{color:MUTED,size:9},thickness:9,len:0.9,outlinewidth:0,tickprefix:'$',tickformat:'~s'},
    hovertemplate:'%{x|%b %d · %H:%M}<br>$%{y:,.0f}<br>liq ≈ $%{z:,.0f}<extra></extra>'};
  const markline={x:hm.x,y:hm.mark,type:'scatter',mode:'lines',name:'mark',
    line:{color:'#f4ece0',width:1.5},hovertemplate:'mark $%{y:,.0f}<extra></extra>'};
  const lay=baseLayout('');   // figcaption + explanation above name the chart
  lay.height=540; lay.margin={l:78,r:34,t:22,b:56};
  lay.yaxis=Object.assign(lay.yaxis,{title:{text:'Price (USD)',font:{color:MUTED,size:11}},tickprefix:'$',tickformat:',.0f'});
  lay.xaxis=Object.assign(lay.xaxis,{title:{text:'Time (UTC, '+per+')',font:{color:MUTED,size:11}},type:'date'});
  if(hm.x.length>1) lay.xaxis.range=[hm.x[0],hm.x[hm.x.length-1]];  // data reaches the right edge (mark too)
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
  renderKpis(d); renderLadder(d); renderHeatmap(d); renderGauge(d); renderCfiPrice(d); renderQuality(d); setFresh();
  Plotly.Plots.resize(VIEW==='heatmap'?'heatmap':'ladder'); Plotly.Plots.resize('cfiprice');
}

function showView(v){ VIEW=v;
  document.getElementById('ladder-wrap').style.display = v==='heatmap'?'none':'block';
  document.getElementById('heatmap-wrap').style.display = v==='heatmap'?'block':'none';
  document.querySelectorAll('#primary-tabs button').forEach(b=>b.classList.toggle('active',b.dataset.v===v));
  Plotly.Plots.resize(v==='heatmap'?'heatmap':'ladder');
}
function setLadderMode(mode){ LADDER_MODE=mode;
  document.querySelectorAll('#ladder-mode button').forEach(b=>b.classList.toggle('active',b.dataset.m===mode));
  renderLadder(window.__DATA__);
}
function setHeatRes(res){ HEAT_RES=res;
  document.querySelectorAll('#heatmap-res button').forEach(b=>b.classList.toggle('active',b.dataset.r===res));
  renderHeatmap(window.__DATA__);
}

// --- click-to-open card tooltip (styled, not the native title) ---
const tipEl=document.createElement('div'); tipEl.id='cardtip'; document.body.appendChild(tipEl);
function hideTip(){ tipEl.classList.remove('show'); tipEl._owner=null;
  document.querySelectorAll('.info.on').forEach(x=>x.classList.remove('on')); }
document.addEventListener('click',function(e){
  const b=e.target.closest('.info');
  if(b){ e.stopPropagation();
    if(tipEl.classList.contains('show') && tipEl._owner===b){ hideTip(); return; }
    hideTip(); tipEl.textContent=b.getAttribute('data-tip'); tipEl._owner=b; b.classList.add('on');
    tipEl.classList.add('show');
    const r=b.getBoundingClientRect(), w=tipEl.offsetWidth;
    let left=r.left+window.scrollX+r.width/2-w/2;
    left=Math.max(10,Math.min(left,window.innerWidth-w-12));
    tipEl.style.left=left+'px'; tipEl.style.top=(r.bottom+window.scrollY+9)+'px';
  } else if(!e.target.closest('#cardtip')){ hideTip(); }
});

// --- expand any figure to (near) fullscreen ---
function toggleExpand(figId){
  const fig=document.getElementById(figId); if(!fig)return;
  const exp=fig.classList.toggle('expanded');
  document.body.classList.toggle('has-expanded', !!document.querySelector('figure.chart.expanded'));
  fig.querySelectorAll('.expand-btn').forEach(b=>{b.textContent=exp?'✕':'⤢'; b.title=exp?'Close (Esc)':'Expand';});
  setTimeout(()=>{
    if(exp){ const h=Math.round(window.innerHeight*0.9-150);
      fig.querySelectorAll('.js-plotly-plot').forEach(d=>Plotly.relayout(d,{height:Math.max(380,h)})); }
    else { renderAll(window.__DATA__); }   // restore default sizes
  },70);
}
document.addEventListener('keydown',e=>{ if(e.key==='Escape')
  document.querySelectorAll('figure.chart.expanded').forEach(f=>toggleExpand(f.id)); });

// Fetch the latest data.json; if it's a newer snapshot, re-render in place + flash.
// Returns the parsed payload (or null), so callers can act on whether it changed.
async function refreshFromServer(){
  const r=await fetch('./data.json?t='+Date.now(),{cache:'no-store'}); if(!r.ok)return null;
  const j=await r.json();
  if(j && j.generated_at!==window.__DATA__.generated_at){
    window.__DATA__=j; renderAll(j);
    const f=document.getElementById('freshness'); if(f){f.classList.remove('flash'); void f.offsetWidth; f.classList.add('flash');}
  }
  return j;
}

async function poll(){ try{ await refreshFromServer(); }catch(e){} }

// Manual "↻" button. By default it force-refreshes to the freshest committed
// point and polls aggressively for ~3 min to catch a brand-new one the moment it lands.
// Set DISPATCH_URL to a tiny workflow-dispatch proxy (see workers/dispatch-worker.js)
// to make the button trigger a REAL on-demand extraction.
const DISPATCH_URL = "";
async function manualUpdate(){
  const btn=document.getElementById('update-btn'); if(!btn)return;
  btn.disabled=true; const orig=btn.textContent; btn.textContent='↻ updating…';
  const startGen=window.__DATA__.generated_at, t0=Date.now();
  const stop=()=>{ clearInterval(iv); btn.disabled=false; btn.textContent=orig; };
  if(DISPATCH_URL){ try{ await fetch(DISPATCH_URL,{method:'POST',mode:'cors'}); }catch(e){} }
  const iv=setInterval(async()=>{
    try{ const j=await refreshFromServer();
      if((j && j.generated_at!==startGen) || Date.now()-t0>180000) stop();
    }catch(e){ if(Date.now()-t0>180000) stop(); }
  },4000);
}

document.querySelectorAll('#primary-tabs button').forEach(b=>b.addEventListener('click',()=>showView(b.dataset.v)));
document.querySelectorAll('#ladder-mode button').forEach(b=>b.addEventListener('click',()=>setLadderMode(b.dataset.m)));
document.querySelectorAll('#heatmap-res button').forEach(b=>b.addEventListener('click',()=>setHeatRes(b.dataset.r)));
document.querySelectorAll('.expand-btn').forEach(b=>b.addEventListener('click',()=>toggleExpand(b.dataset.fig)));
const _ub=document.getElementById('update-btn'); if(_ub) _ub.addEventListener('click',manualUpdate);
renderAll(window.__DATA__);
setInterval(poll,60000);
setInterval(setFresh,20000);
"""


# --------------------------------------------------------------- HTML assembly
def _hero(snapshot: dict[str, Any]) -> str:
    regime = snapshot["signals"]["regime"]
    regime_color = REGIME_COLORS[regime]
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
     <span class="tag fresh"><span class="live-dot"></span><span id="freshness">live</span><button class="info" data-tip="A fresh reading is computed about every 10 minutes (when the pipeline runs). The page quietly checks for a new one every 60 seconds and updates in place — no reload. So &quot;updated 4 min ago&quot; just means the newest reading is 4 minutes old.">?</button></span>
     <span class="tag">new reading <b>~10 min</b></span>
     <button id="update-btn" class="tag btn" title="Fetch the freshest reading now (a new one is produced ~every 10 min; configure a dispatch endpoint for true on-demand extraction)">↻</button>
  </div>
</header>
<div class="rule reveal" style="animation-delay:.06s"></div>
"""


def _charts() -> str:
    return """
<div class="seg reveal" id="primary-tabs" style="animation-delay:.12s">
  <button class="active" data-v="live">Liquidation map</button>
  <button data-v="heatmap">History heatmap</button>
</div>
<figure class="chart reveal" id="fig-map" style="animation-delay:.14s">
  <div id="ladder-wrap">
    <div class="fig-top">
      <figcaption><span>Fig.01 — Liquidation map (now)</span></figcaption>
      <div class="fig-ctl">
        <div class="subseg" id="ladder-mode">
          <button class="active" data-m="density">density</button>
          <button data-m="bars">bars</button>
        </div>
        <button class="expand-btn" data-fig="fig-map" title="Expand">⤢</button>
      </div>
    </div>
    <p class="explain">Where the open BTC leverage we track would be <b>force-liquidated</b>, by price.
       <b>Teal = longs</b> (liquidate below price — a downside flush); <b>red = shorts</b> (liquidate
       above — a squeeze up). Gold line = current mark. <b>density</b> is a smoothed profile (area ∝ notional);
       <b>bars</b> shows the exact per-level notional. Hover for the amount; drag/scroll to zoom, ⤢ to expand.</p>
    <div class="legend">
      <span class="lg"><i style="background:var(--long)"></i>Long · flush risk (below mark)</span>
      <span class="lg"><i style="background:var(--short)"></i>Short · squeeze risk (above mark)</span>
    </div>
    <div id="ladder"></div>
  </div>
  <div id="heatmap-wrap" style="display:none">
    <div class="fig-top">
      <figcaption><span>Fig.01b — History heatmap</span></figcaption>
      <div class="fig-ctl">
        <div class="subseg" id="heatmap-res">
          <button data-r="min10">10-min</button>
          <button class="active" data-r="hour">hourly</button>
          <button data-r="day">daily</button>
        </div>
        <button class="expand-btn" data-fig="fig-map" title="Expand">⤢</button>
      </div>
    </div>
    <p class="explain">The same liquidation field <b>over time</b>: rows are price levels, columns are time,
       brighter = more liquidable notional parked there. The <b>white line is the mark-price path</b>.
       Pick a resolution — 10-minute snapshots, hourly, or daily averages.</p>
    <div id="heatmap"></div>
  </div>
</figure>
<div class="row13">
  <figure class="chart reveal" id="fig-gauge" style="animation-delay:.18s">
    <div class="fig-top">
      <figcaption><span>Fig.02 — Fragility now</span></figcaption>
      <button class="expand-btn" data-fig="fig-gauge" title="Expand">⤢</button>
    </div>
    <p class="explain">The current Cascade Fragility Index as a 0–100 “fear gauge” —
       green calm, amber building, red fragile.</p>
    <div id="gauge"></div>
  </figure>
  <figure class="chart reveal" id="fig-cfip" style="animation-delay:.22s">
    <div class="fig-top">
      <figcaption><span>Fig.03 — Fragility &amp; price over time</span></figcaption>
      <button class="expand-btn" data-fig="fig-cfip" title="Expand">⤢</button>
    </div>
    <p class="explain">The <b>Cascade Fragility Index</b> (gold, left) against <b>BTC price</b> (white, right).
       Bands mark calm / building / fragile regimes — watch fragility build or bleed off as price moves.</p>
    <div id="cfiprice"></div>
  </figure>
</div>
"""


def _kpis_initial(snapshot: dict[str, Any]) -> str:
    """Server-rendered KPI cards for first paint / no-JS fallback (JS re-renders)."""
    m, sig, cov = snapshot["market"], snapshot["signals"], snapshot["coverage"]
    fa = m["funding_hourly"] * 24 * 365
    asym = sig["asymmetry"]
    bias = "shorts more exposed · squeeze-up fuel" if asym > 0 else "longs more exposed · flush-down fuel"
    bias_c = "var(--short)" if asym > 0 else "var(--long)"
    reg_c = REGIME_COLORS[sig["regime"]]
    conf, conf_c = _confidence(snapshot)
    w5 = sig["near_band_usd"]["0.05"]["total"]
    w2 = sig["near_band_usd"]["0.02"]["total"]
    cov_pct = cov["coverage_ratio"] * 100
    return "".join([
        _kpi("Cascade Fragility Index", f"{sig['cfi']:.1f}<span class='u'>/100</span>",
             f"regime · {sig['regime'].upper()}", reg_c),
        _kpi("Long / short asymmetry", f"{asym:+.2f}", bias, bias_c),
        _kpi("Liquidable within ±5%", _money(w5), f"±2% · {_money(w2)}"),
        _kpi("BTC mark", f"${m['mark_px']:,.0f}", f"oracle ${m['oracle_px']:,.0f}"),
        _kpi("Open interest", _money(m["oi_usd"]), f"{m['oi_btc']:,.0f} BTC"),
        _kpi("Funding (annualized)", f"{fa:+.1%}", f"{m['funding_hourly']:+.4%} / h"),
        _kpi("OI captured", f"{cov_pct:.0f}%",
             f"{_money(cov['sampled_notional_usd'])} of {_money(m['oi_usd'])} OI"),
        _kpi("Signal confidence", conf,
             f"{cov_pct:.0f}% coverage · {int(cov['n_btc_positions'])} positions", conf_c),
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
hover reports real notional within ±1.25%. Regime bands (calm &lt; {int(REGIME_BANDS['calm_max'])},
elevated &lt; {int(REGIME_BANDS['elevated_max'])}, else fragile) are illustrative pending
calibration on the accumulating history.</p>

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
    cfg = {"calm": REGIME_BANDS["calm_max"], "elev": REGIME_BANDS["elevated_max"],
           "regimeColors": REGIME_COLORS,
           "conf": {"highCov": CONF_HIGH[0], "highN": CONF_HIGH[1],
                    "medCov": CONF_MED[0], "medN": CONF_MED[1]}}
    tail = ("<script>window.__CFG__=" + json.dumps(cfg)
            + ";window.__DATA__=" + json.dumps(payload) + ";</script>"
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
    map_history = load_map_history()

    # trim both the CFI series and the heatmap to the comparable-coverage era
    cutoff = consistent_cutoff(history)
    if cutoff:
        history = history[history["timestamp"] >= cutoff]
        map_history = [r for r in map_history if r["timestamp"] >= cutoff]

    heatmap = build_heatmap(map_history)
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

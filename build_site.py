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
        has_asym = "asymmetry" in history.columns
        for r in history.sort_values("timestamp").itertuples():
            price = float(r.mark_px) if "mark_px" in history.columns else None
            asym = float(r.asymmetry) if (has_asym and pd.notna(r.asymmetry)) else None
            hist.append({"t": r.timestamp, "cfi": float(r.cfi), "price": price,
                         "asym": asym})
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
  #cardtip { position:absolute; z-index:3000; max-width:300px; max-height:58vh; overflow-y:auto; background:var(--card-2);
    border:1px solid var(--hair-strong); border-left:2px solid var(--gold); border-radius:11px;
    padding:12px 14px; font-family:var(--body); font-size:12.8px; line-height:1.55; color:#dccfba;
    box-shadow:0 14px 38px rgba(0,0,0,0.55); opacity:0; transform:translateY(-4px); pointer-events:none;
    transition:opacity .15s ease, transform .15s ease; }
  #cardtip b { color:#cabfae; font-weight:600; }
  #cardtip.show { opacity:1; transform:none; }
  /* expand-to-fullscreen button + state */
  .expand-btn { font-family:var(--mono); font-size:13px; color:var(--muted); background:var(--ink-2);
    border:1px solid var(--hair); border-radius:7px; width:30px; height:26px; cursor:pointer; transition:all .18s ease; }
  .expand-btn:hover { color:var(--gold); border-color:var(--gold); }
  figure.chart.expanded { position:fixed; inset:2.5vh 2.5vw; z-index:2500; margin:0; overflow:hidden;
    display:flex; flex-direction:column; box-shadow:0 0 0 100vmax rgba(8,6,4,0.82); }
  /* in fullscreen the chart area grows to fill; the chart divs follow (autosize).
     !important so this always wins over the compact floating-panel heights when a figure
     is expanded straight out of the sticky PiP (those selectors carry more id-specificity). */
  figure.chart.expanded #ladder-wrap, figure.chart.expanded #heatmap-wrap {
    flex:1 1 auto; min-height:0; display:flex; flex-direction:column; }
  figure.chart.expanded #ladder, figure.chart.expanded #heatmap,
  figure.chart.expanded .row-body { flex:1 1 auto !important; min-height:0; height:auto !important; }
  figure.chart.expanded #gauge, figure.chart.expanded #cfiprice { height:auto !important; min-height:0; }
  /* in fullscreen keep the explainer visible and readable; the chart fills the remaining height
     (display:block !important overrides the floating-panel collapse when expanding from the PiP) */
  figure.chart.expanded .explain { display:block !important; max-width:none; padding:8px 14px 10px; font-size:14px; }
  figure.chart.expanded .legend { display:flex !important; padding:2px 14px 8px; }
  body.has-expanded { overflow:hidden; }
  /* floating "picture-in-picture" charts: when the charts scroll out of view they pop into a
     draggable / resizable overlay on the right. It's an OVERLAY (position:fixed) — it never
     reflows the page, so the article keeps its full, readable width. Wide screens only. */
  #pip-bar { display:none; }
  .pip-rz { display:none; }
  #charts.floating { position:fixed; z-index:1800; margin:0; overflow:visible;
    min-width:320px; min-height:220px; max-width:96vw; max-height:96vh;
    display:flex; flex-direction:column; border:1px solid var(--hair-strong); border-radius:15px;
    background:linear-gradient(180deg,var(--card),var(--ink-2)); box-shadow:0 30px 84px rgba(0,0,0,0.66); }
  #charts.floating #pip-bar { display:flex; align-items:center; gap:10px; flex:0 0 auto; z-index:6;
    padding:9px 12px; cursor:move; user-select:none; touch-action:none; border-radius:14px 14px 0 0;
    background:var(--card-2); border-bottom:1px solid var(--hair); }
  #pip-bar .pip-grip { flex:1; font-family:var(--mono); font-size:10.5px; letter-spacing:.14em;
    text-transform:uppercase; color:var(--muted); display:flex; align-items:center; gap:8px; min-width:0; }
  #pip-bar .pip-grip .gtxt { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  #pip-bar .pip-grip .dots { color:var(--faint); letter-spacing:.05em; flex:0 0 auto; }
  #pip-close { flex:0 0 auto; font-family:var(--mono); font-size:13px; color:var(--muted); background:var(--ink-2);
    border:1px solid var(--hair); border-radius:7px; width:28px; height:26px; cursor:pointer; transition:all .18s ease; }
  #pip-close:hover { color:var(--gold); border-color:var(--gold); }
  /* inner scroll area (only the content scrolls; the frame + handles stay put) */
  #charts.floating #pip-scroll { flex:1 1 auto; min-height:0; overflow:auto; padding:0 11px 12px;
    overscroll-behavior:contain; }
  /* resize grips on every edge and corner */
  #charts.floating .pip-rz { display:block; position:absolute; z-index:7; }
  #charts.floating .pip-rz.n { top:-3px; left:12px; right:12px; height:9px; cursor:ns-resize; }
  #charts.floating .pip-rz.s { bottom:-3px; left:12px; right:12px; height:9px; cursor:ns-resize; }
  #charts.floating .pip-rz.e { right:-3px; top:12px; bottom:12px; width:9px; cursor:ew-resize; }
  #charts.floating .pip-rz.w { left:-3px; top:12px; bottom:12px; width:9px; cursor:ew-resize; }
  #charts.floating .pip-rz.ne { top:-4px; right:-4px; width:16px; height:16px; cursor:nesw-resize; z-index:8; }
  #charts.floating .pip-rz.nw { top:-4px; left:-4px; width:16px; height:16px; cursor:nwse-resize; z-index:8; }
  #charts.floating .pip-rz.se { bottom:-4px; right:-4px; width:16px; height:16px; cursor:nwse-resize; z-index:8; }
  #charts.floating .pip-rz.sw { bottom:-4px; left:-4px; width:16px; height:16px; cursor:nesw-resize; z-index:8; }
  /* compact the charts so both figures read well inside the floating window */
  #charts.floating .seg { margin-top:6px; }
  #charts.floating figure.chart { margin:10px 0 12px; }
  #charts.floating #ladder, #charts.floating #heatmap { height:300px; }
  #charts.floating #cfiprice, #charts.floating #gauge { height:240px; }
  /* keep Fig.02 as gauge (left) + series (right), matching the article's “Left… / Right…” text */
  #charts.floating .reveal { opacity:1; animation:none; }
  .fig-ctl { display:flex; align-items:center; gap:10px; }
  .row-body { display:grid; grid-template-columns:1fr 2fr; gap:13px; align-items:stretch; }
  .pip-hide { display:none !important; }   /* view toggle hides the inactive map/heatmap wrap */
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
  /* "?" that reveals a figure's explainer (used only in the floating panel, like the KPI cards) */
  .explain-info { display:none; align-items:center; justify-content:center; width:18px; height:18px; border-radius:50%;
    border:1px solid var(--hair-strong); background:var(--ink-2); color:var(--muted); font-family:var(--mono);
    font-size:10px; line-height:1; cursor:pointer; padding:0; flex:0 0 auto; }
  .explain-info:hover, .explain-info.on { color:#1a1206; background:var(--gold); border-color:var(--gold); }
  /* in the sticky panel each figure's text is collapsed by default; the "?" shows it on click */
  #charts.floating .explain, #charts.floating .legend { display:none; }
  #charts.floating .explain-info { display:inline-flex; }
  figure.chart.expanded .explain-info { display:none !important; }
  .legend { display:flex; gap:18px; padding:6px 10px 0; flex-wrap:wrap; }
  .lg { font-family:var(--mono); font-size:11.5px; color:var(--muted); display:inline-flex; align-items:center; gap:7px; }
  .lg i { width:18px; height:3px; border-radius:2px; display:inline-block; }
  #ladder,#heatmap { height:540px; } #cfiprice,#gauge { height:350px; }
  .empty { color:var(--muted); font-family:var(--mono); font-size:13px; text-align:center; padding:120px 20px; }
  .modebar { background:transparent !important; }
  /* the whole memo column is one uniform measure (left-aligned), so tables/callouts/headers
     no longer run wider than the prose — and the freed right-hand space is given to the
     sticky charts panel (its default width starts just right of this column). */
  section.memo { margin-top:46px; max-width:624px; }
  .sec-head { display:flex; align-items:baseline; gap:16px; border-bottom:1px solid var(--hair); padding-bottom:12px; margin:0 0 18px; }
  .sec-num { font-family:var(--serif); font-size:30px; color:var(--gold); font-weight:600; line-height:1; font-variant-numeric:tabular-nums; }
  .sec-head h2 { font-family:var(--serif); font-weight:600; font-size:25px; letter-spacing:-.01em; margin:0; }
  .memo p, .memo li { font-size:15.5px; color:#cdc3b3; max-width:none; }
  .memo h3 { font-family:var(--mono); font-size:12px; letter-spacing:.12em; text-transform:uppercase; color:var(--gold); margin:22px 0 4px; }
  .memo strong { color:var(--bone); font-weight:600; } .memo em { color:#dcd2c2; }
  .wrap a { color:var(--gold); text-decoration:underline; text-underline-offset:3px; text-decoration-thickness:1px; transition:color .15s ease; }
  .wrap a:hover { color:#f3c171; }
  ul { padding-left:20px; } li { margin:5px 0; }
  code { font-family:var(--mono); background:var(--ink-2); border:1px solid var(--hair); padding:1px 6px; border-radius:5px; font-size:12.5px; color:#e9c98c; }
  .callout { border-left:2px solid var(--gold); background:linear-gradient(90deg,rgba(232,161,58,0.07),transparent); padding:13px 18px; margin:16px 0; border-radius:0 10px 10px 0; }
  .callout.warn { border-left-color:var(--short); background:linear-gradient(90deg,rgba(229,86,78,0.08),transparent); }
  .callout p { margin:0; max-width:none; }
  .lead { font-size:16.5px; line-height:1.6; color:#d6cbb9; max-width:none; margin:0 0 6px; }
  /* page-opening intro in the hero — full width, matching the charts below */
  .hero-intro { font-size:15px; line-height:1.62; color:#c8bdac; max-width:none; margin:12px 0 0; }
  .hero-intro strong { color:var(--bone); font-weight:600; } .hero-intro em { color:#dcd2c2; }
  .callout.reading { border-left-color:var(--long); background:linear-gradient(90deg,rgba(63,185,140,0.08),transparent); }
  .reading-tag { font-family:var(--mono); font-size:10px; letter-spacing:.18em; text-transform:uppercase; color:var(--long); margin-bottom:6px; }
  #reading-line { font-size:14.5px; color:#ded2bf; }
  .schema { font-family:var(--mono); font-size:12px; line-height:1.6; color:#cdbfa8; background:var(--ink-2);
    border:1px solid var(--hair); border-radius:9px; padding:13px 15px; margin:12px 0; overflow-x:auto; white-space:pre; }
  .schema b { color:var(--gold); font-weight:600; }
  .appx-divider { display:flex; align-items:center; gap:14px; margin:50px 0 8px; }
  .appx-divider::before, .appx-divider::after { content:""; height:1px; background:var(--hair-strong); flex:1; }
  .appx-divider span { font-family:var(--mono); font-size:10.5px; letter-spacing:.18em; text-transform:uppercase; color:var(--faint); white-space:nowrap; }
  table { width:100%; border-collapse:collapse; margin:14px 0; font-size:13.5px; }
  th { font-family:var(--mono); text-align:left; color:var(--faint); font-weight:500; font-size:10.5px; letter-spacing:.1em; text-transform:uppercase; padding:9px 10px; border-bottom:1px solid var(--hair-strong); }
  td { padding:9px 10px; border-bottom:1px solid var(--hair); color:#cdc3b3; }
  td:nth-child(2) { font-family:var(--mono); color:var(--bone); font-variant-numeric:tabular-nums; }
  footer { margin-top:54px; padding-top:18px; border-top:1px solid var(--hair); color:var(--faint); font-family:var(--mono); font-size:12px; line-height:1.8; }
  footer .live { color:var(--muted); }
  @media (max-width:820px) { .kpis { grid-template-columns:repeat(2,1fr); } .row-body { grid-template-columns:1fr; } }
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
  title:{text:title, font:{size:14,color:FONT,family:MONO}, x:0.01}, autosize:true,
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
  lay.margin={l:78,r:66,t:22,b:56}; lay.hovermode='y unified'; if(bars) lay.barmode='overlay';
  lay.xaxis=Object.assign(lay.xaxis,{title:{text:bars?'Liquidable notional per price level (USD)':'Liquidable notional density (smoothed) — area ∝ notional',font:{color:MUTED,size:11}},tickprefix:'$',tickformat:'~s',rangemode:'tozero',zeroline:false});
  lay.yaxis=Object.assign(lay.yaxis,{title:{text:'Liquidation price (USD)',font:{color:MUTED,size:11}},tickprefix:'$',tickformat:',.0f',showgrid:false,
     range:[mark*0.70,mark*1.30],   // open at mark ±30% (the zone that matters); data runs to ±60% — pan/zoom out to see it
     showspikes:true,spikemode:'across',spikethickness:1,spikecolor:GOLD,spikedash:'dot',spikesnap:'cursor'});
  lay.shapes=[{type:'rect',xref:'paper',x0:0,x1:1,yref:'y',y0:mark*0.95,y1:mark*1.05,fillcolor:GOLD,opacity:0.05,line:{width:0}},
              {type:'line',xref:'paper',x0:0,x1:1,yref:'y',y0:mark,y1:mark,line:{color:GOLD,width:1.3,dash:'dot'}}];
  lay.annotations=[{xref:'paper',x:1.005,xanchor:'left',y:mark,yref:'y',text:'mark<br>$'+num(mark),showarrow:false,align:'left',font:{color:GOLD,family:MONO,size:10.5}}];
  Plotly.react('ladder',traces,lay,CFG);
}

function renderGauge(d){
  const cfi=d.signals.cfi;
  // Scale the big number / ticks / margins to the container so they never overflow the arc
  // (the gauge gets small inside the floating panel and large when expanded).
  const el=document.getElementById('gauge'); const w=(el&&el.clientWidth)?el.clientWidth:300;
  const numSize=Math.max(15,Math.min(40,Math.round(w*0.135)));
  const tickSize=Math.max(7,Math.min(10,Math.round(w*0.032)));
  const mx=Math.max(14,Math.min(30,Math.round(w*0.09)));
  const tr={type:'indicator',mode:'gauge+number',value:cfi,number:{font:{size:numSize,color:FONT,family:MONO},suffix:'/100'},
    gauge:{axis:{range:[0,100],tickcolor:MUTED,tickfont:{color:MUTED,size:tickSize}},bar:{color:FONT,thickness:0.22},
      bgcolor:'rgba(0,0,0,0)',borderwidth:0,
      steps:[{range:[0,POLICY.calm],color:'rgba(63,185,140,0.30)'},{range:[POLICY.calm,POLICY.elev],color:'rgba(232,161,58,0.30)'},{range:[POLICY.elev,100],color:'rgba(229,86,78,0.32)'}],
      threshold:{line:{color:GOLD,width:4},value:cfi}}};
  Plotly.react('gauge',[tr],{paper_bgcolor:'rgba(0,0,0,0)',font:{color:FONT,family:MONO},autosize:true,
    margin:{l:mx,r:mx,t:24,b:10},modebar:{bgcolor:'rgba(0,0,0,0)',color:MUTED,activecolor:GOLD}},CFG);
}

function renderCfiPrice(d){
  // Stacked, shared time axis. Top: Cascade Fragility Index (left, regime bands) vs
  // BTC price (right). Bottom (narrow): long/short asymmetry — fill split by sign
  // (red = short-side fuel above price, teal = long-side fuel below), neutral value line.
  const H=d.history, x=H.map(p=>p.t), cfi=H.map(p=>p.cfi), price=H.map(p=>p.price);
  const asym=H.map(p=>p.asym==null?null:p.asym);
  const aPos=asym.map(v=>v==null?null:Math.max(v,0)), aNeg=asym.map(v=>v==null?null:Math.min(v,0));
  const lay=baseLayout('');   // named by the figcaption + explanation above; keep the plot airy
  lay.margin={l:60,r:66,t:34,b:46}; lay.hovermode='x unified'; lay.showlegend=true;
  lay.legend={orientation:'h',y:1.16,x:0,bgcolor:'rgba(0,0,0,0)',font:{size:11.5,color:MUTED}};
  // ONE shared x-axis for both panels (anchored to the bottom strip so the date ticks sit at
  // the very bottom). A single axis is what lets 'x unified' hover span BOTH panels at once —
  // hovering either shows CFI + price + asymmetry together, with one full-height guide line.
  lay.xaxis=Object.assign(lay.xaxis,{type:'date',anchor:'y3',title:'',
    showspikes:true,spikemode:'across',spikethickness:1,spikecolor:'rgba(232,161,58,0.55)',spikedash:'dot',spikesnap:'cursor'});
  // top panel (domain 0.30–1): CFI left, BTC price right (overlay)
  lay.yaxis=Object.assign(lay.yaxis,{domain:[0.30,1],range:[0,100],title:{text:'CFI',font:{color:GOLD,size:11}},tickfont:{color:GOLD,size:11}});
  lay.yaxis2={overlaying:'y',side:'right',tickprefix:'$',tickformat:'~s',showgrid:false,zeroline:false,
    title:{text:'BTC price',font:{color:'#cdbfa8',size:11}},tickfont:{color:'#cdbfa8',size:11}};
  // bottom panel (domain 0–0.22): asymmetry in [-1,1], baseline at 0
  lay.yaxis3={domain:[0,0.22],range:[-1,1],anchor:'x',zeroline:true,zerolinewidth:1,zerolinecolor:'rgba(202,191,174,0.45)',
    gridcolor:GRID,tickvals:[-1,0,1],ticktext:['long-side','0','short-side'],tickfont:{color:MUTED,size:9}};
  lay.shapes=[{type:'rect',xref:'paper',x0:0,x1:1,yref:'y',y0:0,y1:POLICY.calm,fillcolor:LONG,opacity:0.06,line:{width:0}},
              {type:'rect',xref:'paper',x0:0,x1:1,yref:'y',y0:POLICY.calm,y1:POLICY.elev,fillcolor:GOLD,opacity:0.06,line:{width:0}},
              {type:'rect',xref:'paper',x0:0,x1:1,yref:'y',y0:POLICY.elev,y1:100,fillcolor:SHORT,opacity:0.06,line:{width:0}}];
  const traces=[
    {x:x,y:cfi,name:'CFI',yaxis:'y',type:'scatter',mode:'lines',line:{color:GOLD,width:2,shape:'spline',smoothing:0.4},
     fill:'tozeroy',fillcolor:'rgba(232,161,58,0.06)',
     customdata:asym.map(v=>v==null?'n/a':(v>=0?'+':'')+v.toFixed(2)),
     hovertemplate:'CFI %{y:.1f}<br>asymmetry %{customdata}<extra></extra>'},
    {x:x,y:price,name:'BTC price',yaxis:'y2',type:'scatter',mode:'lines',line:{color:'#cdbfa8',width:1.5},
     hovertemplate:'$%{y:,.0f}<extra></extra>'},
    {x:x,y:aPos,yaxis:'y3',type:'scatter',mode:'lines',fill:'tozeroy',fillcolor:'rgba(229,86,78,0.25)',
     line:{width:0},hoverinfo:'skip',showlegend:false},
    {x:x,y:aNeg,yaxis:'y3',type:'scatter',mode:'lines',fill:'tozeroy',fillcolor:'rgba(63,185,140,0.25)',
     line:{width:0},hoverinfo:'skip',showlegend:false},
    {x:x,y:asym,name:'asymmetry',yaxis:'y3',type:'scatter',mode:'lines',connectgaps:false,
     line:{color:'#cabfae',width:1.3},hovertemplate:'asymmetry %{y:+.2f} · %{customdata}<extra></extra>',
     customdata:asym.map(v=>v==null?'':(v>0.02?'short-side (squeeze-up fuel)':v<-0.02?'long-side (flush-down fuel)':'balanced'))}];
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
  lay.margin={l:78,r:34,t:22,b:56};
  lay.yaxis=Object.assign(lay.yaxis,{title:{text:'Price (USD)',font:{color:MUTED,size:11}},tickprefix:'$',tickformat:',.0f'});
  const mk=d.market&&d.market.mark_px;
  if(mk) lay.yaxis.range=[mk*0.70,mk*1.30];   // open at the latest mark ±30%; data runs to ±60% — pan/zoom out
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

function renderReading(d){
  const el=document.getElementById('reading-line'); if(!el)return;
  const s=d.signals,m=d.market,cov=d.coverage,asym=s.asymmetry;
  const side = asym>0.05 ? 'tilted to the short side — more short positions would be liquidated just above price, which is upside-squeeze fuel'
            : asym<-0.05 ? 'tilted to the long side — more longs would be liquidated just below price, which is downside-flush fuel'
            : 'fairly balanced between the two sides';
  const read = s.regime==='calm' ? 'the fuel sits well away from the price, so a move has to travel before it runs into anything — conditions are structurally orderly'
            : s.regime==='elevated' ? 'a real amount of fuel is gathering near the price, so it is worth watching for a trigger'
            : 'large liquidable size is parked within a few percent of the price, where moves turn violent — a place to tighten risk, not add it';
  el.innerHTML='Right now the Cascade Fragility Index reads <b>'+s.cfi.toFixed(1)+'/100</b> ('+s.regime+
    '). The book is '+side+', with about <b>'+money(s.near_band_usd['0.05'].total)+'</b> liquidable within 5% of the <b>$'+
    num(m.mark_px)+'</b> mark. <b>Read:</b> '+read+'. This is built from the <b>'+(cov.coverage_ratio*100).toFixed(0)+
    '%</b> of open interest we currently see.';
}

function setFresh(){ if(!GEN)return;
  const mins=Math.max(0,Math.round((Date.now()-GEN.getTime())/60000));
  const label=mins<1?'just now':(mins<60?mins+' min ago':Math.floor(mins/60)+'h '+(mins%60)+'m ago');
  const el=document.getElementById('freshness'); if(el)el.innerHTML='updated <b>'+label+'</b>'+(mins>25?' · STALE':'');
}

function renderAll(d){
  GEN=new Date(d.generated_at);
  renderKpis(d); renderLadder(d); renderHeatmap(d); renderGauge(d); renderCfiPrice(d); renderQuality(d); renderReading(d); setFresh();
  ['gauge','cfiprice',VIEW==='heatmap'?'heatmap':'ladder'].forEach(id=>{
    const el=document.getElementById(id); if(el && el.classList.contains('js-plotly-plot')) Plotly.Plots.resize(el);
  });
}

function showView(v){ VIEW=v;
  // toggle a class (not inline display) so the flex-fill of figure.chart.expanded #*-wrap
  // still applies in fullscreen — an inline display would override it and squash the chart.
  document.getElementById('ladder-wrap').classList.toggle('pip-hide', v==='heatmap');
  document.getElementById('heatmap-wrap').classList.toggle('pip-hide', v!=='heatmap');
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
    hideTip();
    if(b.classList.contains('explain-info')){            // figure "?" -> show that figure's explainer (HTML)
      const cont=b.closest('.fig-top').parentElement, ex=cont&&cont.querySelector('.explain');
      tipEl.innerHTML = ex ? ex.innerHTML : '';
    } else { tipEl.textContent=b.getAttribute('data-tip'); }
    tipEl._owner=b; b.classList.add('on'); tipEl.classList.add('show');
    const r=b.getBoundingClientRect(), w=tipEl.offsetWidth, h=tipEl.offsetHeight;
    let left=r.left+window.scrollX+r.width/2-w/2;
    left=Math.max(10,Math.min(left,window.innerWidth-w-12));
    let top=r.bottom+window.scrollY+9;
    if(r.bottom+h+16>window.innerHeight && r.top-h-9>0) top=r.top+window.scrollY-h-9;  // flip above if no room below
    tipEl.style.left=left+'px'; tipEl.style.top=top+'px';
  } else if(!e.target.closest('#cardtip')){ hideTip(); }
});

// Let Plotly re-read the new box for every visible chart inside a figure (after layout settles).
function resizeFigCharts(fig){ if(!fig)return;
  requestAnimationFrame(()=>setTimeout(()=>{
    fig.querySelectorAll('.js-plotly-plot').forEach(d=>{ if(d.offsetParent===null)return;
      if(d.id==='gauge' && window.__DATA__) renderGauge(window.__DATA__); else Plotly.Plots.resize(d); });
  },40));
}

// --- expand any figure to (near) fullscreen ---
// Charts are autosize with CSS-driven heights, so expanding just changes the container
// (CSS .expanded makes the chart fill it) and we let Plotly re-read the new box.
function toggleExpand(figId){
  const fig=document.getElementById(figId); if(!fig)return;
  const exp=fig.classList.toggle('expanded');
  document.body.classList.toggle('has-expanded', !!document.querySelector('figure.chart.expanded'));
  fig.querySelectorAll('.expand-btn').forEach(b=>{b.textContent=exp?'✕':'⤢'; b.title=exp?'Close (Esc)':'Expand';});
  resizeFigCharts(fig);
}
document.addEventListener('keydown',e=>{ if(e.key==='Escape')
  document.querySelectorAll('figure.chart.expanded').forEach(f=>toggleExpand(f.id)); });

// --- floating "picture-in-picture" charts (wide screens only) ---
// When the whole charts block scrolls out of view it pops into a fixed, draggable, resizable
// overlay so the live charts stay on screen while you read the memo. It NEVER reflows the page
// (it's an overlay), so the article keeps its full, readable width. Scroll back up and it
// returns home automatically; the ✕ dismisses it until you scroll back to the charts.
const MIN_FLOAT_W=1180;            // narrower than this, the charts just stay inline
const FLOAT_AT=8, DOCK_BACK=140;   // hysteresis (px): float once the section is past the top edge
const PIP_MINW=320, PIP_MINH=220;  // resize limits (kept in sync with the CSS min-*)
let FLOATING=false, dismissed=false, dragging=false, userMoved=false, pipGeom=null, _rt=null;
const chartsEl=()=>document.getElementById('charts');
const slotEl =()=>document.getElementById('charts-slot');

function resizeCharts(){
  clearTimeout(_rt); _rt=setTimeout(()=>{
    const g=document.getElementById('gauge');   // re-render so the gauge number rescales to the new size
    if(g && g.classList.contains('js-plotly-plot') && g.offsetParent!==null && window.__DATA__) renderGauge(window.__DATA__);
    ['cfiprice','ladder','heatmap'].forEach(id=>{ const el=document.getElementById(id);
      if(el && el.classList.contains('js-plotly-plot') && el.offsetParent!==null) Plotly.Plots.resize(el); });
  },70);
}
// Default: fill the available height, and as wide as the right gutter allows (no overlap with the
// article). Only when the gutter is too small does it fall back to a usable width flush to the edge.
function defaultGeom(){
  const EDGE=16, TOP=70, BOT=18, GAP=18;
  const vw=window.innerWidth, vh=window.innerHeight;
  // start just right of the (now narrow, uniform) memo column — that freed space is the panel's
  const memo=document.querySelector('section.memo'), wrap=document.querySelector('.wrap');
  const ref=memo?memo.getBoundingClientRect().right:(wrap?wrap.getBoundingClientRect().right:vw);
  let left=Math.round(ref+GAP), width=Math.round(vw-EDGE-left);
  if(width<PIP_MINW){ width=PIP_MINW; left=Math.max(EDGE, vw-EDGE-width); }  // narrow gap: stay usable
  width=Math.min(width, Math.round(vw*0.74));
  return {left, top:TOP, width, height:Math.round(vh-TOP-BOT)};
}
function clampGeom(){ const w=Math.max(pipGeom.width||0, PIP_MINW);   // use the intended width, not a stale offsetWidth
  pipGeom.left=Math.max(4,Math.min(pipGeom.left, window.innerWidth-w-4));
  pipGeom.top =Math.max(4,Math.min(pipGeom.top,  window.innerHeight-44)); }
function applyGeom(){ const c=chartsEl();
  c.style.left=pipGeom.left+'px'; c.style.top=pipGeom.top+'px';
  c.style.width=pipGeom.width+'px'; c.style.height=pipGeom.height+'px'; }
// Default height = just the two figures (measured at the current width), capped at the viewport.
function fitHeight(){ const c=chartsEl(); const availH=window.innerHeight-70-18;
  const prev=c.style.height; c.style.height='auto'; const natural=c.offsetHeight+2; c.style.height=prev;
  return Math.min(Math.max(natural,PIP_MINH), availH); }

function setFloat(on){
  if(on===FLOATING)return;
  const c=chartsEl(), slot=slotEl(); if(!c||!slot)return;
  if(on){
    slot.style.height=c.offsetHeight+'px';   // reserve the in-flow space so nothing jumps
    c.classList.add('floating');
    if(!pipGeom || !userMoved){
      pipGeom=defaultGeom(); applyGeom();    // apply width first so the content wraps to it
      pipGeom.height=fitHeight();            // then shrink to fit the two figures (capped at viewport)
    }
    clampGeom(); applyGeom();
    document.body.classList.add('has-pip'); FLOATING=true;
  } else {
    c.classList.remove('floating'); document.body.classList.remove('has-pip');
    c.style.left=c.style.top=c.style.width=c.style.height='';
    slot.style.height=''; FLOATING=false;
  }
  resizeCharts();
}
function closePip(){ setFloat(false); dismissed=true; }   // ✕: dismiss until re-armed by scrolling back

function onScroll(){
  const slot=slotEl(); if(!slot)return;
  if(window.innerWidth<MIN_FLOAT_W){ if(FLOATING) setFloat(false); return; }
  const b=slot.getBoundingClientRect().bottom;
  if(FLOATING){
    if(b>DOCK_BACK){ setFloat(false); dismissed=false; }   // section back in view -> home + re-arm
  } else {
    if(b>DOCK_BACK) dismissed=false;                        // re-arm while the section is on screen
    if(b<FLOAT_AT && !dismissed) setFloat(true);            // lost the charts -> float
  }
}

function startResize(dir, e){
  e.preventDefault(); e.stopPropagation();
  const c=chartsEl(), r=c.getBoundingClientRect();
  const sx=e.clientX, sy=e.clientY, o={left:r.left, top:r.top, width:r.width, height:r.height};
  const maxW=Math.round(window.innerWidth*0.96), maxH=Math.round(window.innerHeight*0.96);
  const tgt=e.currentTarget; userMoved=true;
  try{tgt.setPointerCapture(e.pointerId);}catch(_){}
  const move=ev=>{
    let dx=ev.clientX-sx, dy=ev.clientY-sy, L=o.left, T=o.top, W=o.width, H=o.height;
    if(dir.includes('e')) W=o.width+dx;
    if(dir.includes('s')) H=o.height+dy;
    if(dir.includes('w')){ W=o.width-dx; L=o.left+dx; }
    if(dir.includes('n')){ H=o.height-dy; T=o.top+dy; }
    if(W<PIP_MINW){ if(dir.includes('w')) L-=(PIP_MINW-W); W=PIP_MINW; }
    if(W>maxW){ if(dir.includes('w')) L+=(W-maxW); W=maxW; }
    if(H<PIP_MINH){ if(dir.includes('n')) T-=(PIP_MINH-H); H=PIP_MINH; }
    if(H>maxH){ if(dir.includes('n')) T+=(H-maxH); H=maxH; }
    L=Math.max(4,Math.min(L, window.innerWidth-W-4)); T=Math.max(4,Math.min(T, window.innerHeight-44));
    pipGeom={left:Math.round(L), top:Math.round(T), width:Math.round(W), height:Math.round(H)};
    applyGeom(); resizeCharts();
  };
  const up=ev=>{ try{tgt.releasePointerCapture(ev.pointerId);}catch(_){}
    tgt.removeEventListener('pointermove',move); tgt.removeEventListener('pointerup',up); resizeCharts(); };
  tgt.addEventListener('pointermove',move); tgt.addEventListener('pointerup',up);
}

function initPip(){
  const c=chartsEl(), bar=document.getElementById('pip-bar'), close=document.getElementById('pip-close');
  if(!c||!bar)return;
  if(close) close.addEventListener('click',e=>{ e.stopPropagation(); closePip(); });
  // drag by the title bar
  bar.addEventListener('pointerdown',e=>{
    if(e.target.closest('#pip-close'))return;
    dragging=true; userMoved=true; try{bar.setPointerCapture(e.pointerId);}catch(_){}
    const sx=e.clientX, sy=e.clientY, bl=pipGeom.left, bt=pipGeom.top;
    const move=ev=>{ if(!dragging)return;
      pipGeom.left=bl+(ev.clientX-sx); pipGeom.top=bt+(ev.clientY-sy); clampGeom();
      c.style.left=pipGeom.left+'px'; c.style.top=pipGeom.top+'px'; };
    const up=ev=>{ dragging=false; try{bar.releasePointerCapture(ev.pointerId);}catch(_){}
      bar.removeEventListener('pointermove',move); bar.removeEventListener('pointerup',up); };
    bar.addEventListener('pointermove',move); bar.addEventListener('pointerup',up); e.preventDefault();
  });
  // resize from every edge / corner
  c.querySelectorAll('.pip-rz').forEach(h=>h.addEventListener('pointerdown',e=>startResize(h.dataset.rz,e)));
  if(window.ResizeObserver){
    new ResizeObserver(()=>{ if(!FLOATING)return;
      pipGeom.width=c.offsetWidth; pipGeom.height=c.offsetHeight; resizeCharts(); }).observe(c);
  }
  window.addEventListener('scroll',onScroll,{passive:true});
  window.addEventListener('resize',()=>{ if(!FLOATING)return;
    if(window.innerWidth<MIN_FLOAT_W){ setFloat(false); return; }
    if(!userMoved){ pipGeom=defaultGeom(); applyGeom(); pipGeom.height=fitHeight(); }  // refit to viewport until customised
    clampGeom(); applyGeom(); resizeCharts(); });
  onScroll();
}

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
initPip();
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
  <p class="lead">This is my entry for the Faro Head of Data challenge: use Faro as an active trader,
     find one piece of data that is missing and would matter to a trader — not a nice-to-have dashboard
     stat — and take it from an idea to a working proof of concept. I started from a real trading
     question and then built the data to answer it, instead of starting from a dataset and looking for a
     use for it.</p>
  <p class="hero-intro">I also treated it as a product question, not just a deliverable. This page is a
     proposal for how a metric like this could live <em>inside</em> Faro — a detail view a trader opens
     from a signal to look at it properly (the price levels, the regime, how it has moved) instead of a
     single number on a dashboard. The same pattern would fit other Faro metrics; this is one worked
     example, running live on Hyperliquid BTC. The metric, the Airflow DAG and the full pipeline are in
     the repo: <a href="https://github.com/javiAI/faro-liquidation-pressure-map" target="_blank" rel="noopener">github.com/javiAI/faro-liquidation-pressure-map</a>.</p>
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
<div id="charts-slot">
<div id="charts">
<div id="pip-bar"><span class="pip-grip"><span class="dots">⠿</span><span class="gtxt">Live charts · drag to move · resize from any edge</span></span><button id="pip-close" title="Return charts to the page">✕</button></div>
<div id="pip-scroll">
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
        <button class="info explain-info" title="What this shows">?</button>
        <button class="expand-btn" data-fig="fig-map" title="Expand">⤢</button>
      </div>
    </div>
    <p class="explain">Where the open BTC leverage we track would be <b>force-liquidated</b>, by price.<br>
       <b>Teal = longs</b> (liquidate below price — a downside flush); <b>red = shorts</b> (liquidate above — a squeeze up). Gold line = current mark.<br>
       <b>density</b> is a smoothed profile (area ∝ notional); <b>bars</b> shows the exact per-level notional.<br>
       Hover for the amount; drag/scroll to zoom, ⤢ to expand.</p>
    <div class="legend">
      <span class="lg"><i style="background:var(--long)"></i>Long · flush risk (below mark)</span>
      <span class="lg"><i style="background:var(--short)"></i>Short · squeeze risk (above mark)</span>
    </div>
    <div id="ladder"></div>
  </div>
  <div id="heatmap-wrap" class="pip-hide">
    <div class="fig-top">
      <figcaption><span>Fig.01b — History heatmap</span></figcaption>
      <div class="fig-ctl">
        <div class="subseg" id="heatmap-res">
          <button data-r="min10">10-min</button>
          <button class="active" data-r="hour">hourly</button>
          <button data-r="day">daily</button>
        </div>
        <button class="info explain-info" title="What this shows">?</button>
        <button class="expand-btn" data-fig="fig-map" title="Expand">⤢</button>
      </div>
    </div>
    <p class="explain">The same liquidation field <b>over time</b>: rows are price levels, columns are time, brighter = more liquidable notional parked there.<br>
       The <b>white line is the mark-price path</b>.<br>
       Pick a resolution — 10-minute snapshots, hourly, or daily averages.</p>
    <div id="heatmap"></div>
  </div>
</figure>
<figure class="chart reveal" id="fig-frag" style="animation-delay:.18s">
  <div class="fig-top">
    <figcaption><span>Fig.02 — Fragility: now &amp; over time</span></figcaption>
    <div class="fig-ctl">
      <button class="info explain-info" title="What this shows">?</button>
      <button class="expand-btn" data-fig="fig-frag" title="Expand">⤢</button>
    </div>
  </div>
  <p class="explain"><b>Left:</b> the current Cascade Fragility Index as a 0–100 “fear gauge” — green calm, amber building, red fragile.<br>
     <b>Right (top):</b> that index (gold) against <b>BTC price</b> (white) over time, with regime bands — watch fragility build or bleed off as price moves.<br>
     <b>Right (strip below):</b> the long/short <b>asymmetry</b> on the same timeline. It is <b>not</b> a “go long / go short” signal — it shows <b>which side of the book holds more liquidation fuel just past the current price</b>:<br>
     · <b>above 0 (red, “short-side”)</b> — more <b>short</b> positions would be force-liquidated just <b>above</b> price; their forced buying can <b>squeeze price up</b>.<br>
     · <b>below 0 (teal, “long-side”)</b> — more <b>long</b> positions would be force-liquidated just <b>below</b> price; their forced selling can <b>flush price down</b>.<br>
     Near 0 means the two sides are roughly balanced.</p>
  <div class="row-body">
    <div id="gauge"></div>
    <div id="cfiprice"></div>
  </div>
</figure>
</div><!-- /#pip-scroll -->
<i class="pip-rz n" data-rz="n"></i><i class="pip-rz s" data-rz="s"></i><i class="pip-rz e" data-rz="e"></i><i class="pip-rz w" data-rz="w"></i><i class="pip-rz ne" data-rz="ne"></i><i class="pip-rz nw" data-rz="nw"></i><i class="pip-rz se" data-rz="se"></i><i class="pip-rz sw" data-rz="sw"></i>
</div><!-- /#charts -->
</div><!-- /#charts-slot -->
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


def _reading(snapshot: dict[str, Any]) -> str:
    """A plain-English read of the current live snapshot (server first paint; JS keeps it live)."""
    s, m, cov = snapshot["signals"], snapshot["market"], snapshot["coverage"]
    w5 = _money(s["near_band_usd"]["0.05"]["total"])
    asym = s["asymmetry"]
    if asym > 0.05:
        side = ("tilted to the short side — more short positions would be liquidated just above "
                "price, which is upside-squeeze fuel")
    elif asym < -0.05:
        side = ("tilted to the long side — more longs would be liquidated just below price, "
                "which is downside-flush fuel")
    else:
        side = "fairly balanced between the two sides"
    if s["regime"] == "calm":
        read = ("the fuel sits well away from the price, so a move has to travel before it runs "
                "into anything — conditions are structurally orderly")
    elif s["regime"] == "elevated":
        read = "a real amount of fuel is gathering near the price, so it is worth watching for a trigger"
    else:
        read = ("large liquidable size is parked within a few percent of the price, where moves "
                "turn violent — a place to tighten risk, not add it")
    return (f"Right now the Cascade Fragility Index reads <b>{s['cfi']:.1f}/100</b> "
            f"({s['regime']}). The book is {side}, with about <b>{w5}</b> liquidable within 5% of "
            f"the <b>${m['mark_px']:,.0f}</b> mark. <b>Read:</b> {read}. This is built from the "
            f"<b>{cov['coverage_ratio']:.0%}</b> of open interest we currently see.")


def _memo(snapshot: dict[str, Any]) -> str:
    m, cov, q, prov = (snapshot["market"], snapshot["coverage"],
                       snapshot["quality"], snapshot["provenance"])
    return f"""
<section class="memo reveal" style="animation-delay:.10s">

<p class="lead">Faro already shows <em>Liquidation Volume</em> — the liquidations that have
<strong>already fired</strong>. The other half is missing: not what just got liquidated, but
<strong>where the leverage that is still open would get liquidated next</strong>. That gap is the
metric below.</p>

<div class="callout reading"><div class="reading-tag">▮ current reading · live</div>
<p id="reading-line">{_reading(snapshot)}</p></div>

  <div class="sec-head"><span class="sec-num">01</span><h2>Product &amp; Data Insight</h2></div>
<p>The metric is a <strong>Liquidation Pressure Map</strong> for BTC perpetuals on Hyperliquid: a
density of the price levels where currently-open leveraged positions would be force-liquidated,
separated by side. Longs liquidate below the price; shorts liquidate above. On top of the map I
compute two numbers a trader can actually watch:</p>
<ul>
<li><strong>Cascade Fragility Index (CFI, 0–100)</strong> — how much liquidable size sits
<em>close</em> to the current price. It answers “is risk building beneath the surface?”. Closer and
bigger means a small move can force liquidations that push price into the next cluster — a cascade.</li>
<li><strong>Long/Short Asymmetry (−1…+1)</strong> — which side is the fuel. It answers “are we set
up to squeeze up or flush down?”.</li>
</ul>
<p>Why this adds something Faro doesn't already have: it is the <em>forward</em> side of the
Liquidation Volume Faro shows today. Volume is what already happened; this is risk that hasn't fired
yet, with a price attached to it. They answer different questions, and a trader reads them together —
one says the fuel is sitting there, the other says it caught.</p>
<p>It matters most for <strong>BTC perps, intraday to swing</strong>. In practice it changes where
you put a stop (don't leave it inside a cluster you could get run into), whether you fade or join a
move heading toward a cluster, and how big you go into crowded positioning. <strong>Source data</strong>
is all public and needs no key: <code>metaAndAssetCtxs</code> for the mark price (the price Hyperliquid
liquidates against), oracle, funding and open interest; <code>clearinghouseState</code> per wallet for
the signed size, the exchange's own <code>liquidationPx</code>, and the position notional. The catch:
no public endpoint returns the whole book, so the map is built from a sample of wallets (Appendix C).</p>

  <div class="sec-head" style="margin-top:40px"><span class="sec-num">02</span><h2>Proof-of-Concept Airflow DAG</h2></div>
<p>The DAG (<code>liquidation_pressure_dag.py</code>) is thin on purpose: it sequences reusable
modules and does nothing clever itself. All the real logic — the API client, the metric, the storage
layer — lives in plain importable functions, so the same code runs whether Airflow drives it or the
small runner behind the live page does. That logic is unit-tested: <code>pytest -q</code> covers the
CFI and asymmetry maths (including that near-mark dust can't spike the index), the cleaning filters,
and the loss-resistant history — run it before any deploy. Flow:
<code>refresh_universe → extract_market_context → extract_positions → validate → transform → load</code>.</p>
<p><strong>Scheduling cadence.</strong> Every ~10 minutes. Open positions and the mark price move
continuously, so a stale map misleads; but positions don't turn over second to second and I won't
hammer a free API, so 10 minutes is the balance between freshness and load. The wallet universe is
refreshed on a far slower beat — once a day — because re-ranking the leaderboard is a ~30 MB
download and the set of <em>most active</em> wallets barely changes within a day. The loop sleeps
adaptively to hold the cadence even as a run takes longer.</p>
<p><strong>Source extraction.</strong> Two sources, two cadences. The wallet universe comes from
Hyperliquid's public leaderboard feed (daily). Per run, <code>clearinghouseState</code> is queried
for every wallet. The <code>/info</code> endpoint is weight-limited to 1,200 weight per minute per
IP and that call costs 2, so the hard ceiling is 600 calls/min. I run a thread pool behind a shared
token bucket pinned at ~7.5 req/s (≈75% of the ceiling) — enough headroom that retries and the
occasional <code>metaAndAssetCtxs</code> (weight 20) never trip the limit. That fetches ~2,000
wallets in about 4.5 minutes with zero rate-limit errors.</p>
<p><strong>Data validation.</strong> Two tiers. Hard checks fail the run and quarantine the data:
a non-positive mark, or mark/oracle drift beyond 5% (a sign something is wrong upstream). Soft
checks pass the data through but flag it. The per-position cleaning drops three things, each for a
reason I hit in the real data: <code>liquidationPx = null</code> (cross-margin positions, where the
trigger depends on the whole account and isn't placeable per position); dust below $10k notional
(which returns nonsense triggers — I watched a $1 position report a liquidation price of $1.8
trillion); and anything more than 60% away from the mark (numerically unreliable and irrelevant to
a near-term cascade). Every count is stored, never hidden.</p>
<p><strong>Transformation logic.</strong> A pure function, so it's testable and the DAG calls it
directly: bucket each kept position by its <code>liquidationPx</code>, weight by notional, and take
the CFI as the notional-weighted average proximity using a smooth kernel <code>K(d)=exp(−d/τ)</code>,
τ = 8%. I chose the exponential kernel over a raw <code>1/d</code> precisely so a dust position
sitting on the mark can't send the index to infinity. Full formula in Appendix B.</p>
<p><strong>Storage and output-table design.</strong> Three artifacts, each matched to its job:</p>
<div class="schema">liq_map_snapshot    timestamp <b>PK</b>, mark_px, oracle_px, funding_hourly, oi_usd,
                    cfi, regime, asymmetry, liq_within_2/5/10pct_usd,
                    n_wallets, n_positions, coverage_ratio,
                    n_null_liqpx, n_dust_filtered, n_far_filtered      &#8594; one row per run (header)
liq_map_histogram   (timestamp, price_mid) <b>PK</b>, distance_pct,
                    long_notional, short_notional                      &#8594; per-price buckets (the map)
cfi_history.jsonl   append-only ledger, one immutable line per run     &#8594; canonical CFI series</div>
<p>The part worth calling out is how the series is stored: an <strong>append-only ledger with union
semantics and atomic writes</strong>, never a read-modify-write. Each run appends one immutable line
first, then rebuilds the CSV view as the union of (ledger ∪ existing CSV ∪ new row). A stale or empty
read can therefore <em>add</em> a point but never <em>drop</em> one — which is exactly the failure you
worry about in something that keeps accumulating for months. In production these become warehouse
tables; the shapes are the same.</p>
<p><strong>Failure handling and observability.</strong> Tasks retry with exponential backoff.
Per-wallet errors are tolerated and counted rather than failing the whole map — a map missing three
wallets out of two thousand beats no map — and the run only hard-fails if the wallet error rate
clears 50%. Alerts fire on staleness beyond two cadences, a drop in coverage, or a spike in the error rate.
Every published reading also carries its own provenance, freshness, coverage and a confidence label,
so whatever reads it — a trader or an AI agent — can downgrade or skip it instead of quoting a number
it can't stand behind.</p>
<p><strong>Backfill.</strong> One limit, up front: the map can't be backfilled from the public API,
because <code>clearinghouseState</code> only returns each account's <em>current</em> state — there is
no “state as of last Tuesday”. So the CFI series builds forward from first deploy, and only the wallet
<em>selection</em> looks at history. The full backfill path — event-sourcing the open-position book
from the fills feed — is in Appendix C.</p>

  <div class="sec-head" style="margin-top:40px"><span class="sec-num">03</span><h2>Trader-Facing Explanation</h2></div>
<p>Strip out the engineering and here is the trade. Liquidation cascades feed on themselves: a forced
sell pushes price lower, which trips the next forced sell. What makes a market dangerous isn't
<em>how much</em> leverage is on — it's <em>how close the triggers sit to the current price</em>.
The CFI measures that, so it rises before a fragile move, not after it.</p>
<p>How to read it:</p>
<ul>
<li><strong style="color:var(--short)">Squeeze setup (bullish fuel)</strong> — asymmetry well
positive with a fat short cluster just above price. The shorts are the fuel; a poke higher can
light them and run price up into the cluster.</li>
<li><strong style="color:var(--long)">Flush risk (bearish fuel)</strong> — asymmetry negative with
longs stacked just below. A poke lower can cascade into a long flush.</li>
<li><strong style="color:var(--gold)">Risk warning</strong> — CFI in the red band: large size is
parked within a few percent of price on at least one side. This is when you tighten risk, not when
you add.</li>
<li><strong style="color:var(--long)">Calm</strong> — CFI low: the fuel is far away, so a move has
to travel before it finds any, and moves tend to stay orderly.</li>
</ul>
<p><strong>What it is not.</strong> Not a forecast and not a timing trigger. Clusters can sit unlit
for days, and the levels behave more like magnets than walls — price often drifts <em>toward</em> a
big cluster before anything happens. Read it as a map of where the market is structurally fragile,
not as a buy or sell signal. And it is a sample of the book, not the whole thing.</p>
<p><strong>Pair it with, before you act:</strong> funding and OI (is the exposed side actually
crowded, and paying to stay on?), spot aggressor flow / CVD (is anyone really pushing toward the
cluster?), and the obvious one inside Faro — the realized Liquidation Volume, to confirm whether the
fuel you're watching actually caught.</p>
<div class="callout"><p><strong>Putting it together — a worked example.</strong> Say BTC is grinding
up toward a thick band of short liquidations about 2% above the mark. Fig.01 shows that as a red wall
and the asymmetry sits positive. The heatmap (Fig.01b) shows the band has been building for hours, not
just this minute, and the CFI has climbed into the amber regime while price went sideways. Add what
Faro already shows: funding is positive and open interest is rising, so the crowd is leaning long and
the shorts above are real money paying to stay short. Put together, that reads as: there is fuel above,
it has been stacking, and the structure is getting fragile — a spot where a push through the level can
squeeze, and where you would not want a stop sitting inside the cluster. If Faro's Liquidation Volume
then spikes at that price, the fuel caught. That is the case for sitting this next to the metrics Faro
already has, in one view.</p></div>

  <div class="sec-head" style="margin-top:40px"><span class="sec-num">04</span><h2>Visualization Recommendation</h2></div>
<p>How I'd put it in front of a trader (the live charts above are the working mock):</p>
<ul>
<li><strong>Primary — the density ladder.</strong> Liquidable notional by price level: longs below
the mark in teal, shorts above in red, with the mark line and a ±5% band. A smoothed density by
default (clean; area is proportional to notional) with a one-click switch to exact bars when a
trader wants the precise number at a level. Hover gives a crosshair and the dollar amount.</li>
<li><strong>The same field over time — a heatmap.</strong> Price on the vertical, time on the
horizontal, brightness = liquidable notional, with the mark-price path drawn over it. A resolution
toggle (10-minute / hourly / daily) lets you go from “what changed this hour” to “how has the
structure built over the week”. This is where you <em>see</em> fragility accumulate.</li>
<li><strong>The index itself — gauge plus dual-axis history.</strong> A 0–100 gauge for the
at-a-glance regime, beside the CFI plotted against BTC price on a second axis with green/amber/red
regime bands. CFI rising while price chops sideways is the textbook “risk building beneath the
surface”.</li>
</ul>
<p><strong>Time horizon:</strong> the map is a live snapshot; the history and heatmap read best over
hours to days. <strong>Thresholds and annotations</strong> worth adding: the regime bands,
the asymmetry sign, and a callout on the single nearest large cluster (“$X of short fuel at +2.1%”).
<strong>Overlays:</strong> mark/oracle, funding sign, and the one I'd push hardest for — Faro's own
realized Liquidation Volume on the same price axis, so a trader sees fuel and ignition in one view.
<strong>Where it lives:</strong> the BTC perp / derivatives page, in a positioning-and-risk tab, and
exposed to Faro's agents as a structured, confidence-tagged signal (regime, asymmetry, nearest
cluster, coverage) so the AI can cite it — and hedge or refuse when coverage is thin.</p>

  <div class="appx-divider"><span>Appendices · data judgment &amp; the path to production</span></div>

  <div class="sec-head"><span class="sec-num">A</span><h2>Data Quality, Freshness &amp; Reconciliation</h2></div>
<p>Data quality is the part that has to be right, so here is the current run in full:</p>
<table>
<tr><th>Check</th><th>Value</th><th>How it is handled</th></tr>
<tr><td>Freshness</td><td><span id="fresh-cell">live</span></td><td>stale &gt; 2 cadences → alert + banner</td></tr>
<tr><td>Coverage vs OI</td><td><span id="q-cov">{cov['coverage_ratio']:.1%}</span></td><td>reported as an explicit bound, never hidden</td></tr>
<tr><td>Positions / wallets</td><td><span id="q-pos">{int(cov['n_btc_positions'])} / {int(cov['n_wallets_queried'])}</span></td><td>thin sample → confidence downgraded</td></tr>
<tr><td>liquidationPx = null</td><td><span id="q-null">{q['n_null_liqpx']} dropped</span></td><td>cross-margin; not placeable per position</td></tr>
<tr><td>Dust (&lt; $10k)</td><td><span id="q-dust">{q['n_dust_filtered']} dropped</span></td><td>dust returns garbage liquidationPx</td></tr>
<tr><td>Far from the mark (&gt; 60%)</td><td><span id="q-far">{q['n_far_filtered']} dropped</span></td><td>numerically unreliable</td></tr>
</table>
<p><strong>Reconciliation.</strong> No endpoint returns the full book, so I reconcile what I
<em>can</em>: <span id="q-recon">sampled {_money(cov['sampled_notional_usd'])} against OI {_money(m['oi_usd'])}</span>
— the coverage ratio above — and mark versus oracle drift, which flags a data-quality event when it
widens. What this is: a high-coverage sample of the most active wallets, not the full book.
<strong>History integrity:</strong> every CFI reading lands in the append-only ledger and is
snapshotted to git each run, and each snapshot keeps its full histogram for the heatmap — so nothing
is silently dropped, and the whole series can be rebuilt from git history via
<code>rebuild_history.py</code>.</p>
<div class="callout warn"><p><strong>Provenance &amp; caveat.</strong> {prov['caveat']} The
leaderboard/activity feed used for wallet discovery is an <em>undocumented</em> Hyperliquid frontend
endpoint — treated as a PoC stand-in for the production fills-WebSocket discovery described below.</p></div>

  <div class="sec-head" style="margin-top:38px"><span class="sec-num">B</span><h2>Methodology</h2></div>
<p>For each qualifying position with liquidable notional <em>N</em> and fractional distance to its
trigger <em>d = |liqPx − mark| / mark</em>, the proximity weight is <code>K(d) = exp(−d/τ)</code>
with τ = 8%. Then <strong>CFI = 100 · Σ N·K(d) / Σ N</strong> — the notional-weighted average
proximity, bounded 0–100; the smooth kernel is what stops a single dust position on the mark from
blowing it up. <strong>Asymmetry = (short_pressure − long_pressure) / (short_pressure +
long_pressure)</strong>, where side pressure is Σ N·K(d) on that side. The Fig.01 density curve is
gaussian-smoothed for legibility (area stays proportional to notional); hover reports the real
notional within ±1.25% of a level.</p>
<p><strong>Two distances, two jobs.</strong> The keep filter drops any position whose trigger sits
more than 60% from the mark — at that range the price is usually garbage, not real risk. The map is
built over that same ±60%, but it opens zoomed to the mark ±30% (the band that matters for a near-term
cascade); pan or zoom out on the chart to see the rest. Either way the kernel makes anything past
roughly 20% count for almost nothing in the CFI.</p>
<p><strong>On the regime bands.</strong> The cut-offs (calm &lt; {int(REGIME_BANDS['calm_max'])},
elevated &lt; {int(REGIME_BANDS['elevated_max'])}, else fragile) are provisional. I set them by hand
from the range the CFI takes in the readings so far — low in calm books, climbing as big clusters pull
in toward the mark — not from a long history, because there isn't one yet: the series only accumulates
forward and the map can't be backfilled from the public API. They should be recalibrated once enough
history builds up, or off a proper backtest once the production backfill in Appendix C exists, rather
than treated as fixed.</p>

  <div class="sec-head" style="margin-top:38px"><span class="sec-num">C</span><h2>Wallet Universe, Coverage &amp; Path to Production</h2></div>
<h3>How the sample is built today</h3>
<p>(1) Pull the public leaderboard feed (~38k addresses). (2) Rank by <strong>activity_score = week
volume + month volume</strong> and keep the top 2,000 — turnover is a good proxy for wallets that
are actually carrying perp risk (ranking by account value pulls in spot holders and idle vaults with
no perp exposure, which I checked early on). (3) Query their live state concurrently, within
the rate budget above. (4) Keep whichever currently hold a qualifying open BTC position (notional ≥
$10k, <code>liquidationPx</code> present, within 60% of mark) — a few hundred at any instant — and
aggregate them. The 2,000 is a daily, activity-ranked pick; the on-map subset is not a second
selection, just whichever of those 2,000 hold a live position this snapshot.</p>
<h3>Why this sample is enough for a proof of concept</h3>
<p>Liquidable open interest is concentrated in the most active wallets, so a high-activity sample
captures what matters for a cascade — the large clusters near price — while the long tail is many
tiny positions that barely move anything. This run reaches
<strong><span id="q-cov2">{cov['coverage_ratio']:.0%}</span> of reported BTC open interest</strong>.
(Exchange OI is one-sided, so total open-position notional is roughly twice the reported figure;
even so, capturing ~half of the reported OI is a large, representative slice of what matters.) The
universe is rebuilt daily and re-ranked by recent activity, so quiet wallets drop out and newly
active ones come in on their own — the list won't drift into dead addresses.</p>
<h3>What the real, production version looks like (described, not built)</h3>
<ul>
<li><strong>Full population, not a sample.</strong> Subscribe to the Hyperliquid fills WebSocket and
keep a rolling registry of every address trading BTC, ranked by trailing volume — discovery becomes
continuous rather than a daily snapshot.</li>
<li><strong>Event-sourced position engine.</strong> Derive each wallet's open position by applying
its fills, funding and liquidations in real time instead of polling <code>clearinghouseState</code>
per wallet. This scales to the whole market and removes the rate-limit ceiling on coverage,
approaching ~100% of OI.</li>
<li><strong>Historical backfill.</strong> Replay the historical fill/funding/liquidation tape
(per-wallet <code>userFillsByTime</code> plus an archival node or data export for the full feed)
from each wallet's first trade forward, reconstructing the open-position book — and therefore the
liquidation map and CFI — at any past timestamp. Caveat: per-wallet fill history is capped, so a
true genesis-to-now reconstruction needs the archival feed and a snapshot anchor for positions
opened before the available window.</li>
<li><strong>Continuous reconciliation.</strong> Check the engine's derived aggregate OI against the
exchange's reported OI every cycle as a live data-quality gate; a divergence flags a missed fill or
a bug before it reaches a trader.</li>
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

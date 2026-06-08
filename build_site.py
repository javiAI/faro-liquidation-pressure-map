"""
build_site.py — assembles the single self-contained deliverable HTML.

Output: site/index.html (and a published copy at docs/index.html for GitHub Pages).
One page that contains BOTH the written memo (the four required challenge sections
plus a data-quality / freshness / reconciliation section) AND the live,
auto-updating visualization.

Design language: "editorial markets terminal" — warm ink background with grain and
a hairline grid, a high-contrast display serif (Fraunces) paired with a monospace
for all numerics (IBM Plex Mono) and a refined grotesque for prose (Hanken Grotesk).
Gold signal accent; emerald/red for long/short. Staggered reveal on load. The intent
is a serious research-note feel, deliberately away from generic dashboard aesthetics.

Live numbers are injected from data/latest_snapshot.json so the memo always describes
the current reading.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

from storage import LATEST_SNAPSHOT_JSON, load_metrics_history
from viz import (build_gauge_figure, build_history_figure, build_ladder_figure,
                 export_memo_png, fig_to_div)

ROOT = os.path.dirname(os.path.abspath(__file__))
SITE_DIR = os.path.join(ROOT, "site")
OUT_HTML = os.path.join(SITE_DIR, "index.html")
DOCS_HTML = os.path.join(ROOT, "docs", "index.html")  # GitHub Pages serves /docs


# --------------------------------------------------------------- formatting
def _money(x: float) -> str:
    for unit, div in (("B", 1e9), ("M", 1e6), ("k", 1e3)):
        if abs(x) >= div:
            return f"${x / div:.2f}{unit}"
    return f"${x:,.0f}"


def _confidence(snapshot: dict[str, Any]) -> tuple[str, str]:
    """A simple, explainable confidence label from coverage + sample depth."""
    cov = snapshot["coverage"]["coverage_ratio"]
    n = snapshot["coverage"]["n_btc_positions"]
    if cov >= 0.30 and n >= 40:
        return "High", "#3fb98c"
    if cov >= 0.15 and n >= 20:
        return "Medium", "#e8a13a"
    return "Low", "#e5564e"


def _kpi(label: str, value: str, sub: str = "", accent: str = "") -> str:
    bar = accent or "var(--hair-strong)"
    return (f'<div class="kpi"><span class="kpi-bar" style="background:{bar}"></span>'
            f'<div class="kpi-label">{label}</div>'
            f'<div class="kpi-value">{value}</div>'
            f'<div class="kpi-sub">{sub}</div></div>')


# --------------------------------------------------------------- HTML render
def render_html(snapshot: dict[str, Any], ladder_div: str, gauge_div: str,
                history_div: str) -> str:
    m = snapshot["market"]
    sig = snapshot["signals"]
    cov = snapshot["coverage"]
    q = snapshot["quality"]
    prov = snapshot["provenance"]

    mark = m["mark_px"]
    funding_annual = m["funding_hourly"] * 24 * 365
    asym = sig["asymmetry"]
    bias = "short side · upside-squeeze fuel" if asym > 0 else "long side · downside-flush fuel"
    bias_color = "var(--short)" if asym > 0 else "var(--long)"
    regime = sig["regime"]
    regime_color = {"calm": "var(--long)", "elevated": "var(--gold)",
                    "fragile": "var(--short)"}[regime]
    conf, conf_color = _confidence(snapshot)
    within5 = sig["near_band_usd"]["0.05"]["total"]
    within2 = sig["near_band_usd"]["0.02"]["total"]

    kpis = "".join([
        _kpi("BTC mark", f"${mark:,.0f}", f"oracle ${m['oracle_px']:,.0f}"),
        _kpi("Cascade Fragility Index", f"{sig['cfi']:.1f}<span class='u'>/100</span>",
             f"regime · {regime.upper()}", regime_color),
        _kpi("Long / short asymmetry", f"{asym:+.2f}", bias, bias_color),
        _kpi("Liquidable within ±5%", _money(within5), f"±2% · {_money(within2)}", "var(--gold)"),
        _kpi("Funding (annualized)", f"{funding_annual:+.1%}", f"{m['funding_hourly']:+.4%} / h"),
        _kpi("Open interest", _money(m["oi_usd"]), f"{m['oi_btc']:,.0f} BTC"),
        _kpi("Sample coverage", f"{cov['coverage_ratio']:.1%}",
             f"{int(cov['n_btc_positions'])} pos · {int(cov['n_wallets_queried'])} wallets"),
        _kpi("Signal confidence", conf, "coverage × sample depth", conf_color),
    ])

    grain = ("data:image/svg+xml;utf8,"
             "<svg xmlns='http://www.w3.org/2000/svg' width='160' height='160'>"
             "<filter id='n'><feTurbulence type='fractalNoise' baseFrequency='0.85' "
             "numOctaves='2' stitchTiles='stitch'/></filter>"
             "<rect width='100%25' height='100%25' filter='url(%23n)' opacity='0.5'/></svg>")

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Liquidation Pressure Map &amp; Cascade Fragility Index — BTC · Hyperliquid</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Fraunces:ital,opsz,wght@0,9..144,400;0,9..144,600;1,9..144,400&family=Hanken+Grotesk:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js" charset="utf-8"></script>
<style>
  :root {{
    --ink:#0b0a09; --ink-2:#100e0c; --card:#15120f; --card-2:#1a1612;
    --hair:#241f18; --hair-strong:#352d22;
    --bone:#ece6da; --muted:#9a8f7d; --faint:#6f665a;
    --gold:#e8a13a; --long:#3fb98c; --short:#e5564e;
    --mono:"IBM Plex Mono",ui-monospace,SFMono-Regular,monospace;
    --serif:"Fraunces",Georgia,serif;
    --body:"Hanken Grotesk",system-ui,sans-serif;
  }}
  * {{ box-sizing:border-box; }}
  html {{ scroll-behavior:smooth; }}
  body {{
    margin:0; color:var(--bone); font-family:var(--body); line-height:1.65;
    background:
      radial-gradient(1100px 520px at 78% -8%, rgba(232,161,58,0.10), transparent 60%),
      radial-gradient(900px 600px at 0% 0%, rgba(63,185,140,0.05), transparent 55%),
      var(--ink);
    -webkit-font-smoothing:antialiased;
  }}
  /* hairline grid + film grain atmosphere */
  body::before {{
    content:""; position:fixed; inset:0; pointer-events:none; z-index:0; opacity:.5;
    background-image:
      linear-gradient(var(--hair) 1px, transparent 1px),
      linear-gradient(90deg, var(--hair) 1px, transparent 1px);
    background-size:64px 64px, 64px 64px;
    -webkit-mask-image:radial-gradient(120% 80% at 50% 0%, #000 30%, transparent 90%);
            mask-image:radial-gradient(120% 80% at 50% 0%, #000 30%, transparent 90%);
  }}
  body::after {{
    content:""; position:fixed; inset:0; pointer-events:none; z-index:0;
    background-image:url("{grain}"); opacity:.04; mix-blend-mode:overlay;
  }}
  .wrap {{ position:relative; z-index:1; max-width:1080px; margin:0 auto; padding:46px 22px 90px; }}
  ::selection {{ background:rgba(232,161,58,0.28); color:#fff; }}
  ::-webkit-scrollbar {{ width:11px; }} ::-webkit-scrollbar-thumb {{ background:var(--hair-strong); border-radius:6px; }}

  /* staggered reveal */
  @keyframes fadeUp {{ from {{ opacity:0; transform:translateY(16px); }} to {{ opacity:1; transform:none; }} }}
  .reveal {{ opacity:0; animation:fadeUp .7s cubic-bezier(.2,.7,.2,1) forwards; }}
  @media (prefers-reduced-motion:reduce) {{ .reveal {{ animation:none; opacity:1; }} }}

  /* hero */
  .eyebrow {{ font-family:var(--mono); font-size:11.5px; letter-spacing:.28em; text-transform:uppercase;
              color:var(--muted); display:flex; gap:12px; align-items:center; flex-wrap:wrap; }}
  .eyebrow .dot {{ width:4px; height:4px; border-radius:50%; background:var(--gold); }}
  h1 {{ font-family:var(--serif); font-weight:600; font-optical-sizing:auto;
        font-size:clamp(34px,6vw,62px); line-height:1.02; letter-spacing:-.015em;
        margin:18px 0 6px; }}
  h1 .amp {{ font-style:italic; font-weight:400; color:var(--gold); }}
  h1 .l2 {{ display:block; font-weight:400; color:var(--muted); font-size:.62em; letter-spacing:0; margin-top:6px; }}
  .dek {{ font-size:17px; color:#cabfae; max-width:60ch; margin:14px 0 0; }}
  .rail {{ display:flex; gap:9px; flex-wrap:wrap; margin-top:22px; }}
  .tag {{ font-family:var(--mono); font-size:11.5px; letter-spacing:.04em; padding:5px 11px;
          border:1px solid var(--hair-strong); border-radius:999px; color:var(--muted);
          display:inline-flex; align-items:center; gap:7px; background:rgba(255,255,255,0.012); }}
  .tag b {{ color:var(--bone); font-weight:500; }}
  .live-dot {{ width:7px; height:7px; border-radius:50%; background:var(--long); position:relative; }}
  .live-dot::after {{ content:""; position:absolute; inset:-4px; border-radius:50%;
                      border:1px solid var(--long); animation:pulse 2s ease-out infinite; }}
  @keyframes pulse {{ 0% {{ transform:scale(.6); opacity:.9; }} 100% {{ transform:scale(1.8); opacity:0; }} }}

  .rule {{ height:1px; background:linear-gradient(90deg,var(--hair-strong),transparent); margin:40px 0 0; }}

  /* KPI grid */
  .kpis {{ display:grid; grid-template-columns:repeat(4,1fr); gap:13px; margin:26px 0 6px; }}
  .kpi {{ position:relative; background:linear-gradient(180deg,var(--card),var(--ink-2));
          border:1px solid var(--hair); border-radius:13px; padding:15px 15px 13px; overflow:hidden;
          transition:transform .25s ease, border-color .25s ease; }}
  .kpi:hover {{ transform:translateY(-3px); border-color:var(--hair-strong); }}
  .kpi-bar {{ position:absolute; top:0; left:0; right:0; height:2px; opacity:.85; }}
  .kpi-label {{ font-family:var(--mono); color:var(--faint); font-size:10.5px; letter-spacing:.14em;
                text-transform:uppercase; }}
  .kpi-value {{ font-family:var(--mono); font-size:25px; font-weight:500; margin-top:7px; color:var(--bone);
                font-variant-numeric:tabular-nums; }}
  .kpi-value .u {{ font-size:13px; color:var(--faint); }}
  .kpi-sub {{ font-family:var(--mono); color:var(--muted); font-size:11px; margin-top:3px; }}

  /* figures */
  figure.chart {{ margin:16px 0; background:linear-gradient(180deg,var(--card),var(--ink-2));
                  border:1px solid var(--hair); border-radius:15px; padding:8px 10px 4px; }}
  figcaption {{ font-family:var(--mono); font-size:11px; letter-spacing:.16em; text-transform:uppercase;
                color:var(--faint); padding:9px 8px 0; display:flex; justify-content:space-between; }}
  figcaption b {{ color:var(--muted); font-weight:500; }}
  .legend {{ display:flex; gap:18px; padding:8px 10px 0; flex-wrap:wrap; }}
  .lg {{ font-family:var(--mono); font-size:11.5px; color:var(--muted); display:inline-flex;
         align-items:center; gap:7px; }}
  .lg i {{ width:18px; height:3px; border-radius:2px; display:inline-block; }}
  .two {{ display:grid; grid-template-columns:1fr 1fr; gap:13px; }}

  /* memo */
  section.memo {{ margin-top:46px; }}
  .sec-head {{ display:flex; align-items:baseline; gap:16px; border-bottom:1px solid var(--hair);
               padding-bottom:12px; margin:0 0 18px; }}
  .sec-num {{ font-family:var(--serif); font-size:30px; color:var(--gold); font-weight:600; line-height:1;
              font-variant-numeric:tabular-nums; }}
  .sec-head h2 {{ font-family:var(--serif); font-weight:600; font-size:25px; letter-spacing:-.01em; margin:0; }}
  .memo p, .memo li {{ font-size:15.5px; color:#cdc3b3; max-width:70ch; }}
  .memo strong {{ color:var(--bone); font-weight:600; }}
  .memo em {{ color:#dcd2c2; }}
  .memo h3 {{ font-family:var(--mono); font-size:12px; letter-spacing:.12em; text-transform:uppercase;
              color:var(--gold); margin:24px 0 4px; }}
  ul {{ padding-left:20px; }} li {{ margin:5px 0; }}
  code {{ font-family:var(--mono); background:var(--ink-2); border:1px solid var(--hair); padding:1px 6px;
          border-radius:5px; font-size:12.5px; color:#e9c98c; }}
  .callout {{ border-left:2px solid var(--gold); background:linear-gradient(90deg,rgba(232,161,58,0.07),transparent);
              padding:13px 18px; margin:16px 0; border-radius:0 10px 10px 0; }}
  .callout.warn {{ border-left-color:var(--short); background:linear-gradient(90deg,rgba(229,86,78,0.08),transparent); }}
  .callout p {{ margin:0; max-width:none; }}
  table {{ width:100%; border-collapse:collapse; margin:14px 0; font-size:13.5px; }}
  th {{ font-family:var(--mono); text-align:left; color:var(--faint); font-weight:500; font-size:10.5px;
        letter-spacing:.1em; text-transform:uppercase; padding:9px 10px; border-bottom:1px solid var(--hair-strong); }}
  td {{ padding:9px 10px; border-bottom:1px solid var(--hair); color:#cdc3b3; }}
  td:nth-child(2) {{ font-family:var(--mono); color:var(--bone); font-variant-numeric:tabular-nums; }}

  footer {{ margin-top:54px; padding-top:18px; border-top:1px solid var(--hair); color:var(--faint);
            font-family:var(--mono); font-size:12px; line-height:1.8; }}
  footer .live {{ color:var(--muted); }}
  @media (max-width:820px) {{ .kpis {{ grid-template-columns:repeat(2,1fr); }} .two {{ grid-template-columns:1fr; }} }}
</style>
</head>
<body>
<div class="wrap">

<header class="reveal" style="animation-delay:.02s">
  <div class="eyebrow">
    <span>Faro · Head of Data challenge</span><span class="dot"></span>
    <span>Hyperliquid · BTC perps</span><span class="dot"></span>
    <span>proof of concept</span>
  </div>
  <h1>Liquidation Pressure Map
      <span class="l2"><span class="amp">&amp;</span> Cascade Fragility Index</span></h1>
  <p class="dek">A <em>forward-looking</em> risk metric: where the currently-open BTC
     leverage on Hyperliquid would be force-liquidated — and how fragile that makes the
     market structure right now.</p>
  <div class="rail">
     <span class="tag">regime <b style="color:{regime_color}">{regime.upper()}</b></span>
     <span class="tag">confidence <b style="color:{conf_color}">{conf}</b></span>
     <span class="tag"><span class="live-dot"></span><span id="freshness">updating…</span></span>
     <span class="tag">cadence <b>~10 min</b></span>
     <span class="tag">source <b>Hyperliquid API</b></span>
  </div>
</header>

<div class="rule reveal" style="animation-delay:.06s"></div>

<div class="kpis reveal" style="animation-delay:.10s">{kpis}</div>

<figure class="chart reveal" style="animation-delay:.14s">
  <figcaption><span>Fig.01 — Liquidation density</span><b>smoothed · sampled wallets</b></figcaption>
  <div class="legend">
    <span class="lg"><i style="background:var(--long)"></i>Long liquidations · flush risk (below mark)</span>
    <span class="lg"><i style="background:var(--short)"></i>Short liquidations · squeeze risk (above mark)</span>
  </div>
  {ladder_div}
</figure>
<div class="two">
  <figure class="chart reveal" style="animation-delay:.18s">
    <figcaption><span>Fig.02 — Fragility gauge</span><b>0–100</b></figcaption>
    {gauge_div}
  </figure>
  <figure class="chart reveal" style="animation-delay:.22s">
    <figcaption><span>Fig.03 — CFI history</span><b>regime bands</b></figcaption>
    {history_div}
  </figure>
</div>

<section class="memo reveal" style="animation-delay:.10s">
  <div class="sec-head"><span class="sec-num">01</span><h2>Product &amp; Data Insight</h2></div>
<p><strong>The metric.</strong> A <strong>Liquidation Pressure Map</strong>: a density
map of the price levels at which <em>currently-open</em> leveraged BTC positions on
Hyperliquid would be force-liquidated, split by side (longs liquidate below price,
shorts above). From it we derive two signals: the <strong>Cascade Fragility Index
(CFI, 0–100)</strong> — how much liquidable notional sits <em>close</em> to the mark
price — and the <strong>Long/Short Asymmetry (−1…+1)</strong> — which side is the more
combustible.</p>
<div class="callout"><p><strong>The specific gap in Faro.</strong> Faro already surfaces
<em>Liquidation Volume</em> — liquidations that <strong>already executed</strong> (a
backward-looking flow of realized events). The map proposed here is its orthogonal
complement on the time axis: it measures <strong>latent</strong> liquidation risk from
positions that are <strong>still open</strong>, and — crucially — it tells you
<strong>at which prices</strong> that risk is parked. Liquidation Volume tells you the
cascade <em>happened</em>; the Fragility Map tells you the fuel is <em>there, at these
levels, right now</em>.</p></div>
<p><strong>The trader question it answers.</strong> “Is risk building beneath the
surface, and where are the trigger levels?” Concretely: where are the magnet/cascade
levels a move would accelerate into; which side is more vulnerable to a squeeze; and is
the overall structure fragile or resilient <em>before</em> the move, not after.</p>
<p><strong>Where it is most relevant.</strong> BTC perpetuals on Hyperliquid — the most
liquid, highest-leverage venue surface Faro covers — for intraday-to-swing risk
management: stop placement, squeeze hunting, and sizing into/out of crowded structure.</p>
<p><strong>Source data (all public, no auth).</strong>
<code>metaAndAssetCtxs</code> → mark price (the liquidation reference), oracle, funding,
open interest. <code>clearinghouseState</code> per wallet → <code>szi</code> (signed
size), <code>liquidationPx</code> (exchange-computed trigger), <code>positionValue</code>
(notional). Wallet universe → the public leaderboard/activity feed (PoC), with
continuous discovery from the fills WebSocket in production.</p>

  <div class="sec-head" style="margin-top:38px"><span class="sec-num">02</span><h2>Pipeline — Airflow DAG</h2></div>
<p>The DAG <code>liquidation_pressure_dag.py</code> runs on a fixed cadence and is
structured as clear tasks; the same functions are reused by the live runner
(<code>run_pipeline.py</code>) that drives this page.</p>
<table>
<tr><th>Stage</th><th>Does</th><th>Reliability concern handled</th></tr>
<tr><td>extract_market_context</td><td>mark / oi / funding</td><td>retry+backoff; anchor for all distances</td></tr>
<tr><td>extract_positions</td><td>clearinghouseState over the universe</td><td>per-wallet errors tolerated &amp; counted</td></tr>
<tr><td>validate</td><td>schema, range, null/dust, freshness</td><td>bad rows quarantined; thin samples flagged</td></tr>
<tr><td>transform</td><td>map + CFI + asymmetry</td><td>pure function, unit-testable</td></tr>
<tr><td>load</td><td>JSON + CSV series + SQLite mirror</td><td>idempotent upserts by run timestamp</td></tr>
</table>
<p><strong>Scheduling</strong> every ~10 min; universe refresh daily.
<strong>Failure handling</strong>: task retries with exponential backoff; alerts on
staleness, coverage collapse, or wallet-error spikes. <strong>Backfill</strong>: the map
is <em>not</em> backfillable — the API only returns the current state of each account, so
past maps cannot be reconstructed. The CFI <em>time-series</em> therefore accumulates
forward from first deploy; only wallet selection uses history.</p>

  <div class="sec-head" style="margin-top:38px"><span class="sec-num">03</span><h2>Trader-Facing Read</h2></div>
<p><strong>Why it is insightful.</strong> Cascades are reflexive: a liquidation pushes
price, which triggers the next liquidation. The danger is not the <em>amount</em> of
leverage but <em>how close its triggers sit to the current price</em>. The CFI measures
exactly that proximity-weighted concentration, so it rises before a fragile move.</p>
<ul>
<li><strong style="color:var(--short)">Bullish / squeeze setup:</strong> asymmetry strongly
positive with a dense short cluster just above price → shorts are the fuel; an upside poke
can ignite a squeeze toward that level.</li>
<li><strong style="color:var(--long)">Bearish / flush risk:</strong> asymmetry negative with
a dense long cluster just below price → longs are the fuel; a downside poke can cascade
into a flush.</li>
<li><strong style="color:var(--gold)">Risk-warning:</strong> CFI in the fragile band means
large liquidable notional is parked within a few percent of price — small moves can become
violent. Calm = the fuel is far away.</li>
</ul>
<p><strong>What it should NOT be used for.</strong> Not a price <em>forecast</em> and not a
timing trigger — clusters can sit unlit for a long time, and levels often act as
<em>magnets</em> rather than walls. It is a <em>conditional risk geography</em>, not a
directional signal, and it reflects a <em>sample</em> of the market (see coverage).</p>
<p><strong>Pair it with.</strong> Funding &amp; OI (is the crowded side paying to stay on?),
spot CVD / aggressor flow (is anyone pushing toward the cluster?), and Faro’s existing
realized <em>Liquidation Volume</em> (did the latent fuel actually ignite?).</p>

  <div class="sec-head" style="margin-top:38px"><span class="sec-num">04</span><h2>Visualization in Faro</h2></div>
<ul>
<li><strong>Chart type:</strong> the horizontal liquidation-density ladder above
(notional by price, long vs short) with the mark line and ±5% band, plus the CFI gauge and
a regime-banded history sparkline.</li>
<li><strong>Time horizon:</strong> the map is a live snapshot; the CFI history reads best
over hours-to-days to watch fragility build or bleed off.</li>
<li><strong>Overlays:</strong> mark/oracle, funding sign, OI; optionally Faro’s realized
Liquidation Volume on the same price axis (fuel → ignition).</li>
<li><strong>Thresholds:</strong> green/amber/red CFI bands, annotated asymmetry, alertable
cluster-proximity triggers.</li>
<li><strong>Placement:</strong> the BTC perp / derivatives page, in a positioning &amp; risk
tab, and as an agent-readable signal (regime + asymmetry + nearest cluster) the AI can cite
with a confidence level.</li>
</ul>

  <div class="sec-head" style="margin-top:38px"><span class="sec-num">05</span><h2>Data Quality, Freshness &amp; Reconciliation</h2></div>
<p>The part a Head of Data is actually accountable for. Current run:</p>
<table>
<tr><th>Check</th><th>Value</th><th>Handling</th></tr>
<tr><td>Freshness</td><td><span id="fresh-cell">live</span></td><td>stale &gt; 2 cadences → alert + banner</td></tr>
<tr><td>Coverage vs OI</td><td>{cov['coverage_ratio']:.1%}</td><td>reported as an explicit bound, never hidden</td></tr>
<tr><td>Positions / wallets</td><td>{int(cov['n_btc_positions'])} / {int(cov['n_wallets_queried'])}</td><td>thin sample → confidence downgraded</td></tr>
<tr><td>liquidationPx = null</td><td>{q['n_null_liqpx']} dropped</td><td>cross-margin; trigger not placeable per-position</td></tr>
<tr><td>Dust (&lt; $10k)</td><td>{q['n_dust_filtered']} dropped</td><td>dust returns garbage liquidationPx</td></tr>
<tr><td>Far / degenerate (&gt; 60%)</td><td>{q['n_far_filtered']} dropped</td><td>numerically unreliable; irrelevant near-term</td></tr>
</table>
<p><strong>Reconciliation.</strong> Because no endpoint returns the full position book, we
cannot reconcile to 100% of OI. Instead we reconcile what we <em>can</em>: sampled
liquidable notional ({_money(cov['sampled_notional_usd'])}) against total OI
({_money(m['oi_usd'])}) — the coverage ratio above — and mark vs oracle drift (a large gap
flags a data-quality event). The honest statement: <em>a high-coverage sample of the most
active wallets, not a census.</em></p>
<div class="callout warn"><p><strong>Provenance &amp; caveat.</strong> {prov['caveat']} The
leaderboard/activity feed used for wallet discovery is an <em>undocumented</em> Hyperliquid
frontend endpoint, treated as a PoC stand-in for a production fills-WebSocket discovery
process.</p></div>

  <div class="sec-head" style="margin-top:38px"><span class="sec-num">06</span><h2>Methodology</h2></div>
<p>For each qualifying position <em>i</em> with liquidable notional <em>N<sub>i</sub></em>
and fractional distance to its trigger <em>d<sub>i</sub> = |liqPx − mark| / mark</em>, the
proximity weight is <code>K(d) = exp(−d/τ)</code> with τ = 8%.</p>
<ul>
<li><strong>CFI</strong> = 100 · Σ N<sub>i</sub>·K(d<sub>i</sub>) / Σ N<sub>i</sub> — the
notional-weighted average proximity (bounded 0–100; a smooth kernel is used so a single
dust position on the mark can’t blow up the index, unlike a raw 1/d weight).</li>
<li><strong>Asymmetry</strong> = (short_pressure − long_pressure) / (short_pressure +
long_pressure), side pressure = Σ N·K(d).</li>
</ul>
<p>Regime bands (calm &lt; 25, elevated &lt; 50, else fragile) are illustrative starting
points, to be calibrated on the accumulating CFI history.</p>
</section>

<footer>
  <span class="live">Generated {prov['generated_at']} UTC · BTC ${mark:,.0f} · CFI {sig['cfi']:.1f} · asymmetry {asym:+.2f} · coverage {cov['coverage_ratio']:.1%}</span><br/>
  Proof-of-concept for the Faro Head of Data challenge. Not investment advice — a sampled,
  forward-looking risk estimate with the caveats stated above.
</footer>
</div>

<script>
  const generated = new Date("{prov['generated_at']}Z".replace("+00:00Z","Z"));
  function tick() {{
    const mins = Math.max(0, Math.round((Date.now() - generated.getTime())/60000));
    const label = mins < 1 ? "just now" : (mins < 60 ? mins + " min ago"
                  : Math.floor(mins/60) + "h " + (mins%60) + "m ago");
    const stale = mins > 25;
    const el = document.getElementById("freshness");
    if (el) el.innerHTML = "updated <b>" + label + "</b>" + (stale ? " · STALE" : "");
    const fc = document.getElementById("fresh-cell");
    if (fc) fc.textContent = "updated " + label;
  }}
  tick(); setInterval(tick, 20000);
</script>
</body>
</html>
"""


def publish_to_docs(src: str = OUT_HTML, dst: str = DOCS_HTML) -> str:
    """Copy the rendered page into docs/ — the directory GitHub Pages serves.

    The page is self-contained (Plotly + fonts via CDN, data inlined), so a single
    index.html under docs/ is all Pages needs. site/ stays the local working output.
    """
    import shutil
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copyfile(src, dst)
    return dst


def generate_site(snapshot_path: str = LATEST_SNAPSHOT_JSON,
                  out_path: str = OUT_HTML) -> str:
    if not os.path.exists(snapshot_path):
        raise FileNotFoundError(f"No snapshot at {snapshot_path}; run the pipeline first.")
    with open(snapshot_path) as f:
        snapshot = json.load(f)
    history = load_metrics_history()

    ladder = build_ladder_figure(snapshot)
    gauge = build_gauge_figure(snapshot)
    hist = build_history_figure(history)
    try:
        export_memo_png(snapshot)  # best-effort static PNG for the memo
    except Exception as exc:  # noqa: BLE001
        print(f"[render] PNG export skipped: {exc}")

    html = render_html(
        snapshot,
        fig_to_div(ladder, "ladder"),
        fig_to_div(gauge, "gauge"),
        fig_to_div(hist, "history"),
    )
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        f.write(html)
    return out_path


if __name__ == "__main__":
    out = generate_site()
    pub = publish_to_docs(out)
    print("site ->", out, "· published ->", pub)
    print("generated at", datetime.now(timezone.utc).isoformat(timespec="seconds"))

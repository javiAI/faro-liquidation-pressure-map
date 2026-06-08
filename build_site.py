"""
build_site.py — assembles the single self-contained deliverable HTML.

Output: site/index.html — one page that contains BOTH
  (a) the 2-4 page written memo (the four required challenge sections + a data-
      quality / freshness / reconciliation section), and
  (b) the LIVE, auto-updating visualization (regenerated every ~10 min by the
      pipeline and re-deployed to GitHub Pages).

Everything the page needs to be judged for trust — provenance, freshness,
coverage, confidence — is rendered inline. Live numbers are injected from
data/latest_snapshot.json so the memo always describes the current reading.
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
        return "High", "#14b8a6"
    if cov >= 0.15 and n >= 20:
        return "Medium", "#f59e0b"
    return "Low", "#ef4444"


def _kpi(label: str, value: str, sub: str = "", color: str = "#e5e7eb") -> str:
    return (f'<div class="kpi"><div class="kpi-label">{label}</div>'
            f'<div class="kpi-value" style="color:{color}">{value}</div>'
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
    bias_color = "#ef4444" if asym > 0 else "#14b8a6"
    regime = sig["regime"]
    regime_color = {"calm": "#14b8a6", "elevated": "#f59e0b", "fragile": "#ef4444"}[regime]
    conf, conf_color = _confidence(snapshot)
    within5 = sig["near_band_usd"]["0.05"]["total"]
    within2 = sig["near_band_usd"]["0.02"]["total"]

    kpis = "".join([
        _kpi("BTC mark", f"${mark:,.0f}", f"oracle ${m['oracle_px']:,.0f}"),
        _kpi("Cascade Fragility Index", f"{sig['cfi']:.1f}<span class='u'>/100</span>",
             f"regime: {regime.upper()}", regime_color),
        _kpi("Long/Short asymmetry", f"{asym:+.2f}", bias, bias_color),
        _kpi("Liquidable within ±5%", _money(within5), f"±2%: {_money(within2)}"),
        _kpi("Funding (annualized)", f"{funding_annual:+.1%}", f"{m['funding_hourly']:+.4%}/h"),
        _kpi("Open interest", _money(m["oi_usd"]), f"{m['oi_btc']:,.0f} BTC"),
        _kpi("Sample coverage", f"{cov['coverage_ratio']:.1%}",
             f"{int(cov['n_btc_positions'])} pos · {int(cov['n_wallets_queried'])} wallets"),
        _kpi("Signal confidence", conf, "coverage × sample depth", conf_color),
    ])

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Liquidation Pressure Map &amp; Cascade Fragility Index — BTC · Hyperliquid</title>
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js" charset="utf-8"></script>
<style>
  :root {{ --bg:#0b0f17; --card:#111827; --line:#1f2937; --fg:#e5e7eb; --muted:#9ca3af;
           --accent:#fbbf24; --teal:#14b8a6; --red:#ef4444; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--fg);
          font-family:Inter,system-ui,-apple-system,Segoe UI,Roboto,sans-serif; line-height:1.6; }}
  .wrap {{ max-width:1080px; margin:0 auto; padding:28px 20px 80px; }}
  header.hero {{ border-bottom:1px solid var(--line); padding-bottom:18px; margin-bottom:24px; }}
  h1 {{ font-size:26px; margin:0 0 6px; letter-spacing:-0.3px; }}
  h2 {{ font-size:20px; margin:38px 0 10px; border-left:3px solid var(--accent);
        padding-left:10px; }}
  h3 {{ font-size:16px; margin:22px 0 6px; color:#cbd5e1; }}
  .sub {{ color:var(--muted); font-size:14px; }}
  .badge {{ display:inline-block; padding:3px 10px; border-radius:999px; font-size:12px;
            font-weight:600; }}
  .freshbar {{ display:flex; gap:10px; align-items:center; margin-top:10px; flex-wrap:wrap; }}
  .kpis {{ display:grid; grid-template-columns:repeat(4,1fr); gap:12px; margin:18px 0 8px; }}
  .kpi {{ background:var(--card); border:1px solid var(--line); border-radius:12px; padding:12px 14px; }}
  .kpi-label {{ color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:.4px; }}
  .kpi-value {{ font-size:22px; font-weight:700; margin-top:2px; }}
  .kpi-value .u {{ font-size:13px; color:var(--muted); font-weight:500; }}
  .kpi-sub {{ color:var(--muted); font-size:12px; }}
  .chart {{ background:var(--card); border:1px solid var(--line); border-radius:12px;
            padding:8px; margin:14px 0; }}
  .two {{ display:grid; grid-template-columns:1fr 1fr; gap:14px; }}
  p, li {{ font-size:15px; color:#d1d5db; }}
  .memo p, .memo li {{ color:#cbd5e1; }}
  strong {{ color:#f3f4f6; }}
  .callout {{ background:#0f1b2d; border:1px solid #1e3a5f; border-radius:10px;
              padding:12px 16px; margin:12px 0; }}
  .warn {{ background:#2a1410; border:1px solid #7f1d1d; }}
  table {{ width:100%; border-collapse:collapse; margin:10px 0; font-size:14px; }}
  th, td {{ text-align:left; padding:8px 10px; border-bottom:1px solid var(--line); }}
  th {{ color:var(--muted); font-weight:600; font-size:12px; text-transform:uppercase; }}
  code {{ background:#0f172a; padding:2px 6px; border-radius:5px; font-size:13px; color:#93c5fd; }}
  .pill {{ font-size:12px; padding:2px 8px; border-radius:6px; background:#0f172a;
           border:1px solid var(--line); color:var(--muted); }}
  footer {{ margin-top:50px; padding-top:18px; border-top:1px solid var(--line);
            color:var(--muted); font-size:13px; }}
  @media (max-width:820px) {{ .kpis {{ grid-template-columns:repeat(2,1fr); }} .two {{ grid-template-columns:1fr; }} }}
</style>
</head>
<body>
<div class="wrap">

<header class="hero">
  <h1>Liquidation Pressure Map &amp; Cascade Fragility Index</h1>
  <div class="sub">A forward-looking risk metric for BTC perps on Hyperliquid —
     proof-of-concept for the Faro <em>Head of Data</em> challenge.</div>
  <div class="freshbar">
     <span class="badge" style="background:{regime_color}22;color:{regime_color}">
        REGIME: {regime.upper()}</span>
     <span class="badge" style="background:{conf_color}22;color:{conf_color}">
        CONFIDENCE: {conf}</span>
     <span id="freshness" class="pill">computing freshness…</span>
     <span class="pill">cadence ~10 min · GitHub Actions → Pages</span>
     <span class="pill">source: Hyperliquid public API</span>
  </div>
</header>

<div class="kpis">{kpis}</div>

<div class="chart">{ladder_div}</div>
<div class="two">
  <div class="chart">{gauge_div}</div>
  <div class="chart">{history_div}</div>
</div>

<div class="memo">

<h2>1 · Product &amp; Data Insight</h2>
<p><strong>The metric.</strong> A <strong>Liquidation Pressure Map</strong>: a density
map of the price levels at which <em>currently-open</em> leveraged BTC positions on
Hyperliquid would be force-liquidated, split by side (longs liquidate below price,
shorts above). From it we derive two signals: the <strong>Cascade Fragility Index
(CFI, 0–100)</strong> — how much liquidable notional sits <em>close</em> to the mark
price — and the <strong>Long/Short Asymmetry (−1…+1)</strong> — which side is the more
combustible.</p>

<div class="callout">
<strong>The specific gap in Faro.</strong> Faro already surfaces <em>Liquidation
Volume</em> — liquidations that have <strong>already executed</strong> (a backward-looking
flow of realized events). The map proposed here is its orthogonal complement on the
time axis: it measures <strong>latent</strong> liquidation risk from positions that are
<strong>still open</strong>, and — crucially — it tells you <strong>at which prices</strong>
that risk is parked. Liquidation Volume tells you the cascade <em>happened</em>; the
Fragility Map tells you the fuel is <em>there, at these levels, right now</em>.</div>

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

<h2>2 · Pipeline (Airflow DAG, proof-of-concept)</h2>
<p>The DAG <code>dags/liquidation_pressure_dag.py</code> runs on a fixed cadence and is
structured as five clear tasks; the same functions are reused by the live runner
(<code>run_pipeline.py</code>) that GitHub Actions calls for this page.</p>
<table>
<tr><th>Stage</th><th>What it does</th><th>Reliability concern handled</th></tr>
<tr><td><code>extract_market_context</code></td><td>metaAndAssetCtxs → mark/oi/funding</td><td>retry+backoff; anchor for all distances</td></tr>
<tr><td><code>extract_positions</code></td><td>clearinghouseState over the wallet universe (rate-limited)</td><td>per-wallet errors tolerated &amp; counted, not fatal</td></tr>
<tr><td><code>validate</code></td><td>schema, range sanity, null/dust detection, freshness</td><td>bad rows quarantined; thin samples flagged low-confidence</td></tr>
<tr><td><code>transform</code></td><td>build map + CFI + asymmetry</td><td>pure function, unit-testable</td></tr>
<tr><td><code>load</code></td><td>JSON snapshot + append CFI to time-series + SQLite mirror</td><td>idempotent upserts by run timestamp</td></tr>
</table>
<p><strong>Scheduling:</strong> every 10 min (<code>*/10 * * * *</code>); universe refresh
daily. <strong>Failure handling:</strong> task retries with exponential backoff; alerts
on (a) staleness &gt; 2 cadences, (b) coverage collapse, (c) wallet-error-rate spike.
<strong>Backfill:</strong> the map itself is <em>not</em> backfillable — the API only
returns the current state of each account, so past liquidation maps cannot be
reconstructed. We are explicit about this: the CFI <em>time-series</em> accumulates
forward from first deploy; only the wallet-selection step uses history.</p>

<h2>3 · Trader-Facing Explanation</h2>
<p><strong>Why it is insightful.</strong> Cascades are reflexive: a liquidation pushes
price, which triggers the next liquidation. The danger is not the <em>amount</em> of
leverage but <em>how close its triggers sit to the current price</em>. The CFI measures
exactly that proximity-weighted concentration, so it rises before a fragile move, not
after.</p>
<p><strong>How to read it.</strong></p>
<ul>
<li><strong>Bullish / squeeze setup:</strong> asymmetry strongly positive with a dense
short cluster just above price → shorts are the fuel; an upside poke can ignite a
short squeeze toward that level.</li>
<li><strong>Bearish / flush risk:</strong> asymmetry negative with a dense long cluster
just below price → longs are the fuel; a downside poke can cascade into a long flush.</li>
<li><strong>Risk-warning:</strong> CFI in the <span style="color:var(--red)">fragile</span>
band means large liquidable notional is parked within a few percent of price on either
side — small moves can become violent. Calm = the fuel is far away.</li>
</ul>
<p><strong>What it should NOT be used for.</strong> It is not a price <em>forecast</em>
and not a timing trigger — clusters can sit unlit for a long time, and the levels often
act as <em>magnets</em> rather than walls. It is a <em>conditional risk geography</em>,
not a directional signal. It also reflects a <em>sample</em> of the market (see
coverage), not every position.</p>
<p><strong>Pair it with.</strong> Funding &amp; OI (is the crowded side paying to stay
on?), spot CVD / aggressor flow (is anyone pushing toward the cluster?), and Faro’s
existing realized <em>Liquidation Volume</em> (did the latent fuel actually ignite?).</p>

<h2>4 · Visualization Recommendation</h2>
<ul>
<li><strong>Chart type:</strong> a horizontal liquidation-density ladder (notional by
price level, long vs short) with the mark price line and a ±5% proximity band — exactly
the top chart above — plus a CFI gauge and a regime-banded CFI history sparkline.</li>
<li><strong>Time horizon:</strong> the map is a live snapshot; the CFI history reads best
over hours-to-days to see fragility build or bleed off.</li>
<li><strong>Overlays:</strong> mark/oracle, funding sign, OI; optionally Faro’s realized
Liquidation Volume on the same price axis to show fuel → ignition.</li>
<li><strong>Thresholds / regimes:</strong> green/amber/red CFI bands (calm/elevated/
fragile), annotated asymmetry, and alertable cluster-proximity triggers.</li>
<li><strong>Where in Faro:</strong> the BTC perp / derivatives page, in a “positioning &amp;
risk” tab, and as an agent-readable signal (regime + asymmetry + nearest cluster) the
AI can cite with a confidence level.</li>
</ul>

<h2>5 · Data Quality, Freshness &amp; Reconciliation</h2>
<p>This is the part a Head of Data is actually accountable for. Current run:</p>
<table>
<tr><th>Check</th><th>Value</th><th>How it is handled</th></tr>
<tr><td>Freshness</td><td><span id="fresh-cell">see badge</span></td><td>stale &gt; 2 cadences → alert + banner</td></tr>
<tr><td>Coverage vs market OI</td><td>{cov['coverage_ratio']:.1%} ({_money(cov['sampled_notional_usd'])} of {_money(m['oi_usd'])})</td><td>reported as an explicit bound, never hidden</td></tr>
<tr><td>Positions kept / queried</td><td>{int(cov['n_btc_positions'])} kept · {int(cov['n_wallets_queried'])} wallets</td><td>thin sample → confidence downgraded</td></tr>
<tr><td><code>liquidationPx = null</code></td><td>{q['n_null_liqpx']} dropped</td><td>cross-margin; trigger not placeable per-position</td></tr>
<tr><td>Dust filtered (&lt; $10k)</td><td>{q['n_dust_filtered']} dropped</td><td>dust returns garbage liquidationPx</td></tr>
<tr><td>Far / degenerate (&gt; 60%)</td><td>{q['n_far_filtered']} dropped</td><td>numerically unreliable; irrelevant to near-term cascade</td></tr>
</table>
<p><strong>Reconciliation.</strong> Because no endpoint returns the full position book,
we cannot reconcile to 100% of OI. Instead we reconcile what we <em>can</em>: sampled
liquidable notional against total OI (the coverage ratio above), and mark vs oracle
price (drift &gt; a threshold flags a data-quality event). The honest statement is:
<em>this is a high-coverage sample of the most active wallets, not a census.</em></p>
<div class="callout warn"><strong>Provenance &amp; caveat.</strong> {prov['caveat']}
The leaderboard/activity feed used for wallet discovery is an <em>undocumented</em>
Hyperliquid frontend endpoint and is treated as a PoC stand-in for a production
fills-WebSocket discovery process.</div>

<h2>6 · Methodology</h2>
<p>For each qualifying position <em>i</em> with liquidable notional <em>N<sub>i</sub></em>
and fractional distance to its trigger <em>d<sub>i</sub> = |liqPx − mark| / mark</em>, the
proximity weight is <code>K(d) = exp(−d/τ)</code> with τ = 8%. Then:</p>
<ul>
<li><strong>CFI</strong> = 100 · Σ N<sub>i</sub>·K(d<sub>i</sub>) / Σ N<sub>i</sub> — the
notional-weighted average proximity (bounded 0–100; a smooth kernel is used so a single
dust position on the mark can’t blow up the index, unlike a raw 1/d weight).</li>
<li><strong>Asymmetry</strong> = (short_pressure − long_pressure) / (short_pressure +
long_pressure), where side pressure = Σ N·K(d) on that side.</li>
</ul>
<p>Regime bands (calm &lt; {25}, elevated &lt; {50}, else fragile) are illustrative
starting points to be calibrated on the accumulating CFI history.</p>

</div>

<footer>
  Generated {prov['generated_at']} UTC · BTC mark ${mark:,.0f} · CFI {sig['cfi']:.1f} ·
  asymmetry {asym:+.2f} · coverage {cov['coverage_ratio']:.1%}.<br/>
  Proof-of-concept for the Faro Head of Data challenge. Not investment advice; a sampled,
  forward-looking risk estimate with the caveats stated above.
</footer>
</div>

<script>
  // Client-side freshness so the "X ago" is correct whenever the page is viewed.
  const generated = new Date("{prov['generated_at']}Z".replace("+00:00Z","Z"));
  function tick() {{
    const mins = Math.max(0, Math.round((Date.now() - generated.getTime())/60000));
    const label = mins < 1 ? "just now" : mins + " min ago";
    const stale = mins > 20;
    const el = document.getElementById("freshness");
    el.textContent = "updated " + label + (stale ? " · STALE" : "");
    el.style.color = stale ? "#ef4444" : "#9ca3af";
    el.style.borderColor = stale ? "#7f1d1d" : "#1f2937";
    const fc = document.getElementById("fresh-cell");
    if (fc) fc.textContent = "updated " + label;
  }}
  tick(); setInterval(tick, 30000);
</script>
</body>
</html>
"""


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


def publish_to_docs(src: str = OUT_HTML, dst: str = DOCS_HTML) -> str:
    """Copy the rendered page into docs/ — the directory GitHub Pages serves.

    The page is fully self-contained (Plotly via CDN, data inlined), so a single
    index.html under docs/ is all Pages needs. Keeping docs/ as the published copy
    lets the live deploy be a plain branch-based Pages site (no special Pages
    permissions), while site/ stays the local working output.
    """
    import shutil
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copyfile(src, dst)
    return dst


if __name__ == "__main__":
    out = generate_site()
    pub = publish_to_docs(out)
    print("site ->", out, "· published ->", pub)
    print("generated at", datetime.now(timezone.utc).isoformat(timespec="seconds"))

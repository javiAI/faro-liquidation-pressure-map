# Liquidation Pressure Map & Cascade Fragility Index — BTC on Hyperliquid

> Proof-of-concept for the **Faro · Head of Data** challenge.
> A *forward-looking* liquidation-risk metric: where the **currently-open** BTC
> leverage on Hyperliquid would be force-liquidated, and how fragile that makes the
> structure right now.

**Live demo:** https://javiai.github.io/faro-liquidation-pressure-map/
**Single-page deliverable:** [`site/index.html`](site/index.html) — the memo (4 sections
+ appendices) **and** the live, auto-updating charts in one file.

---

## The one-line idea

Faro already shows **Liquidation Volume** — liquidations that *already executed*
(backward-looking flow). This metric is the other half on the time axis: the **latent**
liquidation risk from positions that are *still open*, placed **at the prices where they
would trigger**. From the map we derive:

- **Cascade Fragility Index (CFI, 0–100)** — proximity-weighted concentration of
  liquidable notional near the mark. High = a small move can set off a cascade.
- **Long/Short Asymmetry (−1…+1)** — which side carries more fuel near price
  (squeeze-up vs flush-down).

## Why it's defensible (the data judgment)

- No public endpoint returns the whole position book, so — like Glassnode/Coinglass —
  we **aggregate a high-activity wallet sample** (top ~2,000 by recent volume) and
  **report coverage explicitly** (~half of reported BTC OI) instead of pretending it's a
  census.
- The exchange's `liquidationPx` is **null for cross-margin** and **garbage for dust**;
  the pipeline detects and filters both (counts shown in the data-quality panel).
- The map is **not backfillable** (the API only returns *current* account state), so the
  CFI accumulates as a **forward time-series** — stated up front, not hidden.

## Architecture

```
Hyperliquid public API ──► hl_client.py ──► liquidation_map.py ──► storage.py ──► build_site.py
   /info  + leaderboard       (retries,        (CFI, asymmetry,     (JSON + CSV +    (single HTML:
                               rate-limit)       histogram)          SQLite mirror)   memo + charts)
                                   ▲                                       │
                              wallets.py                                   ▼
                        (top ~2,000 by activity)                   site/index.html ──► GitHub Pages

Orchestration:
  • dags/liquidation_pressure_dag.py  — Airflow PoC (the artifact to evaluate)
  • run_pipeline.py + .github/workflows/update.yml — the live runner (loops every ~10 min)
```

The DAG and the runner call the **same** functions — one implementation of the logic,
two ways to drive it.

## Files

| File | Role |
|---|---|
| `validate_api.py` | Live API + schema check (handy to run first) |
| `hl_client.py` | API client: retries/backoff, shared token-bucket rate limit, parsing |
| `wallets.py` | Wallet universe (top-N by recent activity, cached to CSV) |
| `liquidation_map.py` | The metric: cleaning filters, histogram, CFI, asymmetry |
| `storage.py` | Output layer: JSON snapshot, CSV history, append-only ledger, SQLite mirror |
| `heatmap.py` | Rebuilds the time × price heatmap from the per-run histograms |
| `build_site.py` | Assembles the single deliverable `site/index.html` (+ `data.json`) |
| `run_pipeline.py` | Thin end-to-end runner (what GitHub Actions calls) |
| `dags/liquidation_pressure_dag.py` | Airflow DAG (proof-of-concept) |
| `rebuild_history.py` | Reconstruct the CFI / heatmap history from git snapshots |
| `tests/` | `pytest` unit tests (metric maths, filters, loss-resistant storage) |
| `.github/workflows/update.yml` | Cron runner + Pages deploy |

---

## Try it locally

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

Everything below runs from a fresh clone. Pick what you want to see.

### 1. See the deliverable — no network needed

`build_site.py` rebuilds the page from the **last committed snapshot** in `data/`, so you
can open the exact memo + charts without hitting any API:

```bash
python build_site.py        # regenerates site/index.html + site/data.json from data/
open site/index.html        # (Linux: xdg-open · Windows: start)
```

### 2. Run the tests

```bash
pip install -r requirements-dev.txt   # adds pytest
pytest -q
```

The suite covers the parts worth trusting: the CFI / asymmetry maths (including that a
near-mark dust position can't spike the index, and that asymmetry follows *proximity*,
not raw size), the cleaning filters (null / dust / >60%-away), and the append-only
history (a stale or empty read can add a point but never drop one).

### 3. Run the full pipeline against the live API

This extracts → validates → transforms → loads → renders, exactly like the scheduled
job. The first run downloads the ~30 MB leaderboard once to build the wallet universe;
later runs reuse the cache. A small sample finishes in seconds:

```bash
python run_pipeline.py --wallets 200    # try 50 for a faster pass; default is 2000
open site/index.html                    # the page now reflects the fresh run
```

It writes `data/latest_snapshot.json`, appends `data/metrics_history.csv` and
`data/cfi_history.jsonl`, updates `data/warehouse.sqlite`, and regenerates the site.
Run `python validate_api.py` first if you just want to confirm the API and field schema
are live.

### 4. Run / inspect the Airflow DAG

The DAG is deliberately thin: each task (`refresh_universe → extract_market_context →
extract_positions → validate → transform → load`) calls the **same** functions
`run_pipeline.py` does. So the simplest way to *see* what every task does — extraction,
validation counts, the CFI/asymmetry transform, the load — is just to run the runner
above; the output is exactly what the DAG would produce per task.

To run it inside Airflow, the file is a standard `@dag`/`@task` module: it already adds
the repo root to `sys.path`, so dropping it into an Airflow deployment's `dags/` folder
is enough (Airflow is intentionally **not** a pipeline dependency, so the live demo and
the tests stay light). `schedule="*/10 * * * *"` and `catchup=False` are deliberate —
the metric is not backfillable from the public API (see the DAG docstring and Appendix C
of the memo). `python dags/liquidation_pressure_dag.py` also imports cleanly if Airflow
is installed, which is a quick way to confirm the DAG parses.

---

## Deploy the live demo (free)

The pipeline renders `site/index.html` and copies it to `docs/index.html`, which is what
GitHub Pages serves.

1. Push the repo to GitHub.
2. **Settings → Pages → Build and deployment → Source: Deploy from a branch →
   `main` / `/docs`.** Immediate public link, no extra permissions.
3. *(Auto-update)* grant the token the `workflow` scope once
   (`gh auth refresh -h github.com -s workflow`) and keep
   `.github/workflows/update.yml`. The `update-liquidation-map` workflow rebuilds the map
   every ~10 min (and on **Actions → Run workflow**) and commits `docs/index.html` + the
   accumulating `data/` history back; Pages serves the new `docs/` automatically.

Note: GitHub's cron is best-effort (can be delayed), so the workflow runs an internal
~10-min loop and relaunches itself hourly.

## What this is **not**

Not a price forecast and not a timing trigger — clusters can sit unlit, and the levels act
more like magnets than walls. It is a *conditional risk geography* over a wallet **sample**.
Pair it with funding / OI, spot aggressor flow, and Faro's realized Liquidation Volume
before acting. Not investment advice.

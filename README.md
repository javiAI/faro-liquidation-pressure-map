# Liquidation Pressure Map & Cascade Fragility Index — BTC on Hyperliquid

> Proof-of-concept for the **Faro · Head of Data** challenge.
> A *forward-looking* liquidation-risk metric: where the **currently-open** BTC
> leverage on Hyperliquid would be force-liquidated, and how fragile that makes the
> structure right now.

**Live demo:** _add the GitHub Pages URL here after first deploy_
**Single-page deliverable:** [`site/index.html`](site/index.html) — the 2–4 page memo
**and** the live, auto-updating chart in one file.

---

## The one-line idea

Faro already shows **Liquidation Volume** — liquidations that *already executed*
(backward-looking flow). This metric is its orthogonal complement on the time axis:
**latent** liquidation risk from positions that are *still open*, located **at the
prices where they would trigger**. From the map we derive:

- **Cascade Fragility Index (CFI, 0–100)** — proximity-weighted concentration of
  liquidable notional near the mark. High = a small move can ignite a reflexive cascade.
- **Long/Short Asymmetry (−1…+1)** — which side carries more proximate liquidation fuel
  (squeeze-up vs flush-down).

## Why it's defensible (the data judgment)

- No public endpoint returns the whole position book, so — like Glassnode/Coinglass —
  we **aggregate a high-activity wallet sample** (~300 wallets) and **report coverage
  explicitly** (≈20% of OI) rather than pretending it's a census.
- The exchange's `liquidationPx` is **null for cross-margin** and **garbage for dust**;
  the pipeline detects and filters both (visible in the data-quality panel).
- The map is **not backfillable** (the API only returns *current* account state), so the
  CFI is accumulated as a **forward time-series** — stated up front, not hidden.

## Architecture

```
Hyperliquid public API ──► hl_client.py ──► liquidation_map.py ──► storage.py ──► build_site.py
   /info  + leaderboard       (retries,        (CFI, asymmetry,     (JSON + CSV +    (single HTML:
                               rate-limit)       histogram)          SQLite mirror)   memo + charts)
                                   ▲                                       │
                              wallets.py                                   ▼
                          (~300 by activity)                       site/index.html ──► GitHub Pages

Orchestration:
  • dags/liquidation_pressure_dag.py  — Airflow PoC (the artifact to evaluate)
  • run_pipeline.py + .github/workflows/update.yml — the live runner (cron every ~10 min)
```

Both the DAG and the GitHub Actions runner call the **same** functions — one
implementation of the logic, two ways to drive it.

## Files

| File | Role |
|---|---|
| `validate_api.py` | Step 1 — live API validation (run this first) |
| `hl_client.py` | Defensive client: retries/backoff, soft rate-limit, parsing |
| `wallets.py` | Step 3 — ~300-wallet universe by recent activity (cached CSV) |
| `liquidation_map.py` | Step 2 — histogram + Cascade Fragility Index + asymmetry |
| `storage.py` | Output layer: JSON snapshot, CSV history, SQLite warehouse mirror |
| `viz.py` | Step 5 — ladder chart, CFI gauge, history; static memo PNG |
| `build_site.py` | Assembles the single deliverable `site/index.html` |
| `run_pipeline.py` | Thin end-to-end runner (what GitHub Actions calls) |
| `dags/liquidation_pressure_dag.py` | Step 4 — Airflow DAG (proof-of-concept) |
| `.github/workflows/update.yml` | Cron runner + Pages deploy |

## Run it locally

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python validate_api.py                 # Step 1: confirm the API + schema live
python wallets.py --refresh --n 300    # build the wallet universe (one ~30MB fetch)
python run_pipeline.py --wallets 300   # extract→validate→transform→load→render
open site/index.html                   # the deliverable
```

A run queries ~300 wallets in ~100s (gentle rate-limiting) and writes
`data/latest_snapshot.json`, appends `data/metrics_history.csv`, updates
`data/warehouse.sqlite`, and regenerates `site/index.html`.

## Deploy the live demo (free)

The pipeline renders `site/index.html` (local working copy) and publishes a copy to
`docs/index.html`, which is what GitHub Pages serves.

1. Push this repo to GitHub.
2. **Settings → Pages → Build and deployment → Source: Deploy from a branch →
   `main` / `/docs`.** This gives an immediate public link with no extra permissions.
3. *(For auto-update)* grant the GitHub CLI/token the `workflow` scope once
   (`gh auth refresh -h github.com -s workflow`) and commit
   `.github/workflows/update.yml`. The `update-liquidation-map` workflow then runs
   every ~10 min (and on **Actions → Run workflow**), rebuilds the map, and commits
   `docs/index.html` + the accumulating `data/` history back — Pages serves the new
   `docs/` automatically.

Caveats: GitHub's cron is best-effort (can be delayed) and scheduled workflows pause
after ~60 days of repo inactivity.

## What this is **not**

Not a price forecast and not a timing trigger — clusters can sit unlit, and levels
often act as magnets, not walls. It is a *conditional risk geography* over a wallet
**sample**. Pair it with funding/OI, spot aggressor flow, and Faro's realized
Liquidation Volume before acting. Not investment advice.

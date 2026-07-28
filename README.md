# FPL Dossier

Two independent deployables that share a repo but never share a failure mode.

```
.
├── logger/                  # Deployable 1: the daily market logger (LIVE)
│   ├── log_snapshot.py      #   one self-contained script
│   └── requirements.txt     #   depends only on `requests`
├── .github/workflows/
│   └── daily-logger.yml     #   the logger's always-on runner (GitHub Actions)
├── data/snapshots/          # the record it builds -- one CSV per day, per season
│   └── 2026-27/
│       └── 2026-07-28.csv
└── dashboard/               # Deployable 2: the Streamlit dashboard (later)
    └── app.py               #   the existing workbench; extended pre-season
```

The two run on **different engines**: the logger on GitHub Actions, the dashboard
on Streamlit Community Cloud. That is what keeps them isolated -- breaking or
redeploying the dashboard can never stop the logger.

## Deployable 1 — The daily logger

**What it does:** once a day, takes one snapshot of the FPL market for every
player — price, ownership, and transfer flows — and commits it to
`data/snapshots/<season>/<date>.csv`.

**Why it exists:** this history *cannot be backfilled*. The live FPL API only
ever shows "now", so every day we don't log is signal lost forever. It's the
foundation several later tools depend on (bandwagon early-warning, price-timing,
"he was X% owned when we called him").

**How it runs:** the `daily-logger` GitHub Action, on a `22:30 UTC` cron. It runs
on GitHub's infrastructure — not a laptop, not the dashboard's host.

**Why it never fails silently:**
- Retries with backoff, and a browser `User-Agent` to get past the FPL API's
  datacenter-IP blocking.
- Refuses to write a partial/empty snapshot (validates player count).
- Writes atomically (temp file → rename), so a crash can't leave a corrupt CSV.
- On any failure it **exits non-zero and opens a GitHub issue** — plus GitHub's
  built-in failed-run email. A missing day is a missing file, visible at a glance.
- Idempotent: re-running the same day overwrites that day's file, so a same-day
  failure is recoverable via **Actions → Daily FPL logger → Run workflow**.
- The commit step rebases-and-retries, so a human push to the dashboard can't
  make the daily snapshot fail to land.

**Design guarantees (do not break):**
- *Operationally separate.* The logger imports nothing from `dashboard/` and has
  its own minimal dependencies. Tinkering with the dashboard cannot take it down.
- *Exact arithmetic in code.* Every logged value is a raw API field, stored
  verbatim. No interpretation, no derived metrics — so anything our
  accountability record depends on is exact and reproducible. LLM interpretation
  only ever happens on top of these already-computed numbers.

### Snapshot columns

All raw FPL API fields. `now_cost` / `cost_change_*` are integer tenths
(`75` = £7.5); division happens at read time, never at write time.

| Column | Meaning |
|---|---|
| `snapshot_utc` | exact ISO-8601 fetch time (UTC) |
| `snapshot_date` | UTC date of the fetch (one file = one date) |
| `event` | next/current gameweek id, for context |
| `id` | element id (stable within a season) |
| `web_name`, `team`, `position` | identifiers |
| `now_cost` | price in tenths of a million |
| `cost_change_event`, `cost_change_start` | price change this event / since season start |
| `selected_by_percent` | ownership % |
| `transfers_in_event`, `transfers_out_event` | transfers this event |
| `transfers_in`, `transfers_out` | season totals |

### Run it locally

```bash
pip install -r logger/requirements.txt
python logger/log_snapshot.py
# writes data/snapshots/<season>/<today>.csv
```

## Deployable 2 — The dashboard

The existing Streamlit workbench in `dashboard/`. Deployed separately (Streamlit
Community Cloud, entrypoint `dashboard/app.py`). Pre-season it grows the calls
ledger, data-pack generator, and accountability tracker as pages reading the
logger's `data/snapshots/`. See [dashboard/README.md](dashboard/README.md).

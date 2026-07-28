# FPL Dossier

Two independent deployables that share a repo but never share a failure mode.

```
.
├── logger/                     # Deployable 1: the market logger (LIVE)
│   ├── log_snapshot.py         #   the capture job, two modes
│   ├── check_gaps.py           #   asserts the record has no holes
│   └── requirements.txt        #   depends only on `requests`
├── .github/workflows/
│   ├── daily-logger.yml        #   daily runner    (cron 22:30 UTC)
│   ├── deadline-logger.yml     #   deadline runner (hourly gate, fires pre-deadline)
│   └── gap-check.yml           #   weekly: did the runs actually HAPPEN?
├── data/snapshots/             # the daily record -- one CSV per day, per season
│   └── 2026-27/2026-07-28.csv
├── data/deadlines/             # the deadline record -- one CSV per gameweek
│   └── 2026-27/gw01.csv
└── dashboard/                  # Deployable 2: the Streamlit dashboard (later)
    └── app.py                  #   the existing workbench; extended pre-season
```

The two run on **different engines**: the logger on GitHub Actions, the dashboard
on Streamlit Community Cloud. That is what keeps them isolated -- breaking or
redeploying the dashboard can never stop the logger.

## Deployable 1 — The market logger

**What it does:** takes a snapshot of the FPL market for every player — price,
ownership, transfer flows, and availability — and commits it to this repo. Two
records, from one script:

| Record | Path | When |
|---|---|---|
| **Daily** | `data/snapshots/<season>/<date>.csv` | every day at 22:30 UTC |
| **Deadline** | `data/deadlines/<season>/gw<NN>.csv` | ~25 min before each gameweek deadline |

**Why it exists:** this history *cannot be backfilled*. The live FPL API only
ever shows "now", so every capture we miss is signal lost forever. It's the
foundation several later tools depend on (bandwagon early-warning, price-timing,
"he was X% owned when we called him").

**Why the deadline capture is separate:** `transfers_in_event` /
`transfers_out_event` **reset the moment a deadline passes**. The final
pre-deadline figures — the ones an accountability record actually needs — live
for a few hours and then exist nowhere. The daily 22:30 UTC run fires *after*
that reset on a deadline day, so it can never capture them. The two records are
kept in separate directories so the daily run cannot overwrite a deadline
capture later the same day.

**How it runs:** two GitHub Actions, on GitHub's infrastructure — not a laptop,
not the dashboard's host.
- `daily-logger` — a plain `22:30 UTC` cron.
- `deadline-logger` — GitHub cron can't be dynamic and FPL deadlines move every
  week, so this runs **hourly** and gates: a ~15-second `curl` + `jq` check (no
  checkout, no Python) asks when the next deadline is and exits unless it's
  within 120 minutes. That window catches two runs per deadline, so a
  GitHub-delayed run can't cost us the gameweek.

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
- **A weekly gap check catches the failure the others can't see.** The two capture
  workflows alert when a run *fails*; neither can tell you about a run that never
  *happened* — and that's the likelier outcome, since GitHub disables schedules
  after 60 days of repo inactivity and cron delivery isn't guaranteed. No run → no
  failure → no issue → silence, indistinguishable from success. So `gap-check.yml`
  asks the other question weekly: *does the record actually contain what it
  should?* It asserts freshness, no missing dates, a capture per passed deadline,
  and no truncated files — read-only, so it can never damage what it audits.

**Design guarantees (do not break):**
- *Operationally separate.* The logger imports nothing from `dashboard/` and has
  its own minimal dependencies. Tinkering with the dashboard cannot take it down.
- *Exact arithmetic in code.* Every logged value is a raw API field, stored
  verbatim. No interpretation, no derived metrics — so anything our
  accountability record depends on is exact and reproducible. LLM interpretation
  only ever happens on top of these already-computed numbers.

### Snapshot columns

31 columns, identical in both records. All raw FPL API fields except three
capture-metadata columns. `now_cost` / `cost_change_*` are integer tenths
(`75` = £7.5); division happens at read time, never at write time.

The API exposes ~105 fields per player. We take 31, on one rule: log it if it's
**point-in-time** — overwritten the moment it changes, so it can never be
recovered — or cheap context that makes the record self-contained. The rest are
derivable after the fact and are skipped on purpose.

| Column | Meaning |
|---|---|
| `snapshot_utc` | exact ISO-8601 fetch time (UTC) |
| `snapshot_date` | UTC date of the fetch |
| `snapshot_kind` | `daily` or `deadline` — survives concatenation |
| `event` | gameweek id this capture is anchored to |
| `deadline_time` | that gameweek's deadline (raw API field, UTC) |
| `id` | element id (stable within a season) |
| `web_name`, `team`, `position` | identifiers |
| `now_cost` | price in tenths of a million |
| `cost_change_event`, `cost_change_start` | price change this event / since season start |
| `selected_by_percent` | ownership % |
| `transfers_in_event`, `transfers_out_event` | transfers this event — **reset at each deadline** |
| `transfers_in`, `transfers_out` | season totals |
| `status` | `a`vailable / `d`oubtful / `i`njured / `s`uspended / `u`navailable |
| `news` | the injury/availability note shown at capture time |
| `news_added` | **when that note appeared**, to the microsecond |
| `chance_of_playing_this_round`, `chance_of_playing_next_round` | 0–100, or **empty when the API says null** |
| `penalties_order`, `direct_freekicks_order`, `corners_and_indirect_freekicks_order` | set-piece duties — **these change mid-season and nothing else records when** |
| `form` | rolling 30-day figure the API recomputes continuously |
| `event_points`, `total_points`, `minutes` | scoring state to date |
| `ep_this`, `ep_next` | **FPL's own expected-points forecast** |

Three groups here are as unbackfillable as the market fields:

- **Availability** (`status` / `news` / `news_added` / `chance_of_playing_*`) is
  overwritten the instant the news changes. It's what makes "we called him while
  he was a 75% doubt" provable rather than remembered.
- **Set-piece duties** change during a season. When a club's penalty taker
  switches in October, nothing records the date unless it was logged.
- **`ep_next`** is a *forecast* — once the gameweek is played, what was predicted
  beforehand is gone from every endpoint. It doubles as a free benchmark to grade
  our own calls against.

`minutes_to_deadline` is deliberately **not** a column — it's derivable at read
time from `snapshot_utc` and `deadline_time`, and derived metrics don't belong at
write time. Likewise `chance_of_playing_next_round` is written empty, never `0`,
when the API returns null: `0` means "ruled out" and null means "nothing flagged",
and collapsing them would invent injuries.

### Run it locally

```bash
pip install -r logger/requirements.txt

python logger/log_snapshot.py              # -> data/snapshots/<season>/<today>.csv
python logger/log_snapshot.py --deadline   # -> data/deadlines/<season>/gw<NN>.csv
python logger/check_gaps.py                # audit the record; exit 1 if holes
```

`--deadline` refuses to write unless it's within 180 minutes *before* a real
deadline — writing post-deadline data under a `gw<NN>.csv` filename would look
correct and silently corrupt the record. `--force` bypasses that, for recovery.

## Deployable 2 — The dashboard

The existing Streamlit workbench in `dashboard/`. Deployed separately (Streamlit
Community Cloud, entrypoint `dashboard/app.py`). Pre-season it grows the calls
ledger, data-pack generator, and accountability tracker as pages reading the
logger's `data/snapshots/`. See [dashboard/README.md](dashboard/README.md).

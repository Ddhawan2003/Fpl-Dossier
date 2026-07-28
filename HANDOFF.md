# Project Handoff / Full Context

*Read this first. It's the single source of truth for what this repo is, how it's
built, what's done, and what's pending. Written to bring a fresh session (human or
AI) fully up to speed.*

Repo: https://github.com/Ddhawan2003/Fpl-Dossier · Last major update: 2026-07-27

---

## 1. What this is

A **Fantasy Premier League (FPL) content operation** — a two-person team (one CS/
data-science builder, one sports writer) publishing a weekly newsletter plus
Twitter/X and Instagram. Positioning: **data-informed FPL analysis**, where the
moat is (a) a top-10k finisher's credibility and (b) an **honest, public track
record** of every call, graded over time.

This repo holds the **tooling** behind that operation. It is **two independent
deployables in one monorepo**:

1. **The daily logger** (`logger/`) — LIVE. An always-on job that photographs the
   FPL market once a day and files it away. This is the priority foundation.
2. **The dashboard** (`dashboard/`) — the existing Streamlit "workbench"; extended
   later (pre-season) into the ledger / data-pack / accountability tools.

---

## 2. Two hard architectural rules (NEVER violate)

1. **The logger stays operationally SEPARATE from the dashboard.** It must never
   miss a day, so it cannot depend on the dashboard being up or on someone
   breaking it while tinkering. Enforcement: the logger runs on **GitHub Actions**,
   the dashboard on **Streamlit Cloud** — different engines. The logger imports
   nothing from `dashboard/` and depends only on `requests`. Breaking or
   redeploying the dashboard cannot stop the logger.

2. **Exact arithmetic stays in deterministic code, never in an LLM.** Any number
   the accountability record depends on ("he was 4% owned when we called him")
   must be computed in code and be reproducible. Every logged value is a **raw
   API field, stored verbatim** — no interpretation, no derived metrics at write
   time. An LLM may interpret numbers, but only on top of already-computed exact
   ones.

---

## 3. Repo structure

```
Fpl-Dossier/
├── HANDOFF.md                    # this file
├── README.md                     # public-facing overview of both deployables
├── .gitattributes                # forces LF (protects the Actions shell script)
├── .gitignore
├── logger/                       # DEPLOYABLE 1 — the market logger
│   ├── log_snapshot.py           #   the entire job, self-contained, two modes
│   └── requirements.txt          #   `requests` only
├── .github/workflows/
│   ├── daily-logger.yml          #   daily runner (cron 22:30 UTC)
│   └── deadline-logger.yml       #   deadline runner (hourly gate, fires pre-deadline)
├── data/snapshots/               # the daily record — one CSV per day, per season
│   └── 2026-27/
│       └── 2026-07-28.csv
├── data/deadlines/               # the deadline record — one CSV per gameweek
│   └── 2026-27/
│       └── gw01.csv              #   written ~T-25min before each deadline
└── dashboard/                    # DEPLOYABLE 2 — the Streamlit workbench
    ├── app.py                    #   Streamlit Cloud entrypoint is dashboard/app.py
    ├── requirements.txt          #   streamlit / pandas / requests / jinja2
    ├── .streamlit/config.toml    #   dark theme
    ├── README.md                 #   dashboard's own deploy notes (partly stale)
    └── CONTEXT.md                #   debug notes for the dashboard's deploy bug
```

History note: the dashboard used to be nested at `fpl-dossier-master/fpl-dossier/`.
It was flattened to `dashboard/` on 2026-07-27 (git renames, history preserved).

---

## 4. Deployable 1 — the market logger (LIVE)

One script, `logger/log_snapshot.py`, run by **two independent workflows** that
build **two separate records**. Both fetch `bootstrap-static` and write one row
per player; they differ only in when they fire and where they write.

### 4a. The daily record — `data/snapshots/<season>/<YYYY-MM-DD>.csv`

**Command:** `python logger/log_snapshot.py` (no flags).

**Schedule:** GitHub Actions cron `30 22 * * *` = **22:30 UTC daily**. GitHub cron
is UTC-only and ignores DST, and runs can be delayed under load; 22:30 UTC keeps
the snapshot safely **before** FPL's ~01:30 UK price change in both BST and GMT.
Also triggerable manually (`workflow_dispatch`).

**Why one file per UTC date:** a missing day is a visibly missing file, which is
gap detection for free. Re-running the same day overwrites it (idempotent).

### 4b. The deadline record — `data/deadlines/<season>/gw<NN>.csv`

**Command:** `python logger/log_snapshot.py --deadline`.

**Why this exists, and why it is not optional:** `transfers_in_event` and
`transfers_out_event` **reset to zero the instant a deadline passes**. The final
pre-deadline transfer and ownership figures — the exact numbers behind "he was
4% owned when we called him" — exist for a few hours and are then gone from every
endpoint forever. A 22:30 UTC daily snapshot can be up to ~12 hours stale
relative to a Saturday 10:30 UTC deadline, and on a deadline day it fires *after*
the reset, capturing the next gameweek's counters starting from zero.

**It writes to a separate directory on purpose.** If it shared
`data/snapshots/`, the 22:30 UTC daily run would overwrite the deadline capture
later the same day and destroy it.

**How it is scheduled**, given that GitHub cron cannot be dynamic and FPL
deadlines move weekly (Fri 18:30, Sat 10:30, Tue 18:15…): the workflow runs
**hourly at :05** and gates. A `curl` + `jq` step (no checkout, no Python, no
pip — ~15 seconds) asks the API when the next deadline is and exits immediately
unless it falls inside the next **120 minutes**. ~23 runs a day are no-ops.

That 120-minute window deliberately catches **two** hourly runs before each
deadline (≈T-85min and ≈T-25min). The later overwrites the earlier with a closer
capture; if GitHub delays the later run past the deadline, the script refuses to
write and the T-85min capture still stands. Redundancy is the point.

**Three guards in the script itself** (`resolve_deadline`), because a file named
`gw07.csv` holding post-deadline data would look correct and silently corrupt
the record:
- Refuses if the deadline has **already passed** (counters have reset).
- Refuses if the deadline is more than **180 minutes** away (not a real capture).
- `--force` bypasses both, for manual recovery only.

The gate compares timestamps as **strings**, not via jq's `fromdateiso8601` —
that function is backed by `strptime` and is missing from some jq builds. A gate
that silently errors is a gate that never fires.

### 4c. Snapshot columns (both records, 24 columns, identical schema)

All raw FPL fields except the five capture-metadata columns. `now_cost` /
`cost_change_*` are integer tenths (`75` = £7.5) — division happens at read time.

| Column | Meaning |
|---|---|
| `snapshot_utc` | exact ISO-8601 fetch time (UTC) |
| `snapshot_date` | UTC date of the fetch |
| `snapshot_kind` | `daily` or `deadline` — survives concatenation, when the path is lost |
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
| `chance_of_playing_next_round` | 0–100, or **empty when the API says null** |
| `form` | rolling 30-day figure the API recomputes continuously |
| `event_points`, `total_points`, `minutes` | scoring state to date |

`minutes_to_deadline` is deliberately **not** a column — it is derivable at read
time from `snapshot_utc` and `deadline_time`, and rule 2 forbids derived metrics
at write time.

**Null is not zero.** `chance_of_playing_next_round` is written as an empty cell
when the API returns null, never as `0`. In the 2026-07-28 snapshot that is 515
fit players (null) versus 28 explicitly ruled out (`0`) and 18 at `75`.
Defaulting null to 0 would have invented 515 injuries that never existed.

**Why it never fails silently** (a silent gap is the one unrepairable error):
- Retries with exponential backoff (5 attempts) + a browser `User-Agent` — the
  FPL API is known to 403/timeout from datacenter IPs, which GitHub runners are.
- Validates player count (≥300) — refuses to write a partial/empty snapshot that
  would masquerade as a good day.
- Atomic write (temp file → `os.replace`) — a crash can't leave a corrupt CSV.
- On any failure: **exits non-zero AND opens a GitHub issue** (label
  `logger-failure`), plus GitHub's built-in failed-run email.
- Idempotent: re-running the same UTC day overwrites that day's file, so a
  same-day failure is recoverable via **Actions → Daily FPL logger → Run workflow**.
- The commit step rebase-retries, so a human push to the dashboard can't knock
  the daily snapshot's push out.
- The deadline job's failure issue **de-duplicates by title** — the job can fire
  twice per window, and two runs must not open two issues.
- The deadline job's hourly gate **warns rather than fails** if it can't reach
  the API, because failing would open 24 issues a day during an FPL outage. Each
  deadline gets multiple gate attempts, so one bad poll is survivable.

**Run locally:**
```bash
pip install -r logger/requirements.txt
python logger/log_snapshot.py              # -> data/snapshots/<season>/<today>.csv
python logger/log_snapshot.py --deadline   # -> data/deadlines/<season>/gw<NN>.csv
                                           #    (exits 1 unless within 180 min of a deadline)
python logger/log_snapshot.py --deadline --force   # bypass the guard; recovery only
```

**Storage decision (CSV, not SQLite):** one CSV per day is git-native — tiny
commits, human-inspectable, and a missing day is a visibly missing file (free gap
detection). SQLite would be a binary blob rewritten on every commit. A full season
is ~15–30 MB of text. If query speed is ever needed, build a SQLite/parquet
*cache* from the CSVs, but the CSVs stay the source of truth.

---

## 5. Deployable 2 — the dashboard (needs work)

The existing Streamlit workbench: live player stats, xGI vs actual returns,
fixture difficulty, ownership, net transfers, set-piece takers. Internal tool for
content prep, not public.

- **Entrypoint (Streamlit Cloud):** `dashboard/app.py` — this MUST be updated in
  the Streamlit Cloud app settings, because the file moved from its old nested path.
- **Known open bug:** deployment was attempted and "the app isn't working" — never
  diagnosed. See `dashboard/CONTEXT.md` for the suspected culprits (st.rerun
  version, FPL API 403 from Streamlit's IPs, pandas Styler fragility, off-season
  empty `events`). Reproduce locally with `streamlit run dashboard/app.py`.
- **Future (pre-season):** add the calls ledger, data-pack generator, and
  accountability tracker as separate **pages** that read `data/snapshots/` from
  this same repo (which is why they share one repo — the read is free).

---

## 6. Current state (2026-07-28)

**The logger is COMPLETE and needs no further deployment.** Both manual GitHub
steps that earlier versions of this doc listed as pending are done, and there is
proof in the history: commit `3a94049` is authored by `fpl-logger[bot]` at
`2026-07-28 03:50:13 +0000`. That identity only exists inside the runner, and the
push could only have landed with "Read and write permissions" already enabled. So
the full fetch → validate → write → commit → push loop has run end-to-end from
GitHub's datacenter IPs, and the FPL API did not block it.

DONE:
- Logger built, run on GitHub Actions, and confirmed committing (563 players).
- Repo consolidated into the clean `logger/` + `dashboard/` + `data/` monorepo.
- Deadline-anchored capture added (`deadline-logger.yml`, `--deadline` mode),
  with the hourly gate verified against the live API at T-45min, T-119min, and
  just-past-deadline (where it correctly rolls forward to the next gameweek).
- Schema widened to 24 columns: added the unbackfillable point-in-time fields
  (`status`, `news`, `chance_of_playing_next_round`, `form`, `event_points`,
  `total_points`, `minutes`) plus `snapshot_kind` and `deadline_time`.

Deployment note: `deadline-logger.yml` needs **no manual setup**. Repo-wide
Actions write permission is already on, so it begins polling hourly as soon as it
is on `main`. GitHub can take a few minutes to register a newly added cron.

STILL OPEN — the one real hole in the logger:
- **No gap detector.** The failure issue fires when a run *fails*, not when a run
  *never happens*. GitHub disables scheduled workflows after 60 days of
  repository inactivity (one easily-missed email), and Actions can be disabled or
  quota-capped. In all of those cases: no run → no failure → no issue → silence,
  which is indistinguishable from success. A weekly workflow that asserts "there
  is a file for each of the last N days, and a `gw<NN>.csv` for each passed
  deadline" and opens an issue otherwise would close this.

Also pending (dashboard, separate task):
- Update the Streamlit Cloud entrypoint to `dashboard/app.py`.
- **Likely cause of the "app isn't working" bug, not yet confirmed:**
  `dashboard/app.py` calls `requests.get(...)` with **no `User-Agent` header**,
  so it goes out as `python-requests/2.x`. That is the exact datacenter-IP
  blocking the logger carries a browser UA to defeat. It explains the symptom
  precisely — works locally (residential IP), fails on Streamlit Cloud
  (datacenter IP). With no `raise_for_status()`, a 403 HTML body hits `.json()`,
  raises, and lands in the red "Could not reach the FPL API" box → `st.stop()`.
  Fix is two lines. Not on the suspect list in `dashboard/CONTEXT.md`.
- `.devcontainer/devcontainer.json` still points at the pre-flatten path
  `fpl-dossier-master/fpl-dossier/app.py`, so Codespaces launches nothing.
- `dashboard/README.md` still describes deploying with `app.py` at the repo root.

---

## 7. Roadmap context (why the order is what it is)

Pre-season (now → mid-Aug 2026) is the only real build runway; once the season
starts, the ~2–3 hrs/week goes entirely to publishing. Tier-1 foundations to build
before Gameweek 1, in order:

1. **Market logger** — DONE (this repo): daily + deadline-anchored capture.
2. **Calls ledger** — log every recommendation before deadline (pick, confidence,
   one-line rationale, which of 5 lenses drove it). The moat in raw form.
3. **Data-pack generator** — one button turns the dashboard into a paste-ready
   weekly markdown block for the newsletter. Saves 20–30 min/week.
4. **Accountability engine** — auto-grades the ledger vs a benchmark model and vs
   the crowd; the public "here's our honest record" visual.

Items 2–4 will live as dashboard pages reading `data/snapshots/` + a ledger store.
Full detail: the operating manual and build roadmap (the two source docs the
operation is run from — kept by the team, not in this repo).

---

## 8. Key facts a fresh session needs

- **FPL API:** `https://fantasy.premierleague.com/api/bootstrap-static/` (players,
  teams, events) and `.../api/fixtures/`. No auth for public endpoints. Undocumented
  schema; fields occasionally rename between seasons. 403s/timeouts from datacenter
  IPs unless a browser `User-Agent` is sent.
- **GW1 deadline is `2026-08-21T17:30:00Z`** (read from the live API on
  2026-07-28). That is the hard deadline for anything that must exist before the
  record starts — it is when the first deadline capture fires, and when the
  transfer counters begin resetting weekly.
- **Off-season timing:** as of late July 2026 the 2026-27 season hasn't started, so
  transfer fields read 0 and GW1 is flagged as `is_next`. The pipeline is already
  season-ready; it fills with signal once the market moves. Note that `status` /
  `news` are *already* live and moving (46 players carried injury notes on
  2026-07-28), so the point-in-time columns earn their keep before GW1.
- **UTC everywhere:** snapshots are dated by UTC date; GitHub cron is UTC.
- **The local `Downloads/fpl-dossier-debug-handoff/` folder is STALE** (old
  structure, not a git repo). This GitHub repo is the source of truth — always
  work from a fresh clone of it.

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
├── logger/                       # DEPLOYABLE 1 — the daily market logger
│   ├── log_snapshot.py           #   the entire job, self-contained
│   └── requirements.txt          #   `requests` only
├── .github/workflows/
│   └── daily-logger.yml          #   the logger's always-on runner (cron 22:30 UTC)
├── data/snapshots/               # the record it builds
│   └── 2026-27/
│       └── 2026-07-28.csv        #   one CSV per day, per season
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

## 4. Deployable 1 — the daily logger (LIVE)

**What it does:** once a day, fetches `bootstrap-static` from the FPL API and
writes one row per player to `data/snapshots/<season>/<YYYY-MM-DD>.csv`, then
commits it back to this repo.

**Why it matters most:** this history **cannot be backfilled**. The live FPL API
only ever shows "now", so every un-logged day is signal lost forever. It's the
prerequisite for later tools (bandwagon early-warning, price-timing, "X% owned
when we called him").

**Schedule:** GitHub Actions cron `30 22 * * *` = **22:30 UTC daily**. GitHub cron
is UTC-only and ignores DST, and runs can be delayed under load; 22:30 UTC keeps
the snapshot safely **before** FPL's ~01:30 UK price change in both BST and GMT.
Also triggerable manually (`workflow_dispatch`).

**Snapshot columns** (all raw FPL fields; `now_cost`/`cost_change_*` are integer
tenths, e.g. `75` = £7.5 — division happens at read time):

| Column | Meaning |
|---|---|
| `snapshot_utc` | exact ISO-8601 fetch time (UTC) |
| `snapshot_date` | UTC date (one file = one date) |
| `event` | next/current gameweek id, for context |
| `id` | element id (stable within a season) |
| `web_name`, `team`, `position` | identifiers |
| `now_cost` | price in tenths of a million |
| `cost_change_event`, `cost_change_start` | price change this event / since season start |
| `selected_by_percent` | ownership % |
| `transfers_in_event`, `transfers_out_event` | transfers this event |
| `transfers_in`, `transfers_out` | season totals |

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

**Run locally:**
```bash
pip install -r logger/requirements.txt
python logger/log_snapshot.py     # writes data/snapshots/<season>/<today>.csv
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

## 6. Current state (2026-07-27)

DONE:
- Logger built, tested locally (563 players, exit 0), and pushed.
- Repo consolidated into the clean `logger/` + `dashboard/` + `data/` monorepo.
- Workflow, `.gitattributes`, README all in place. First snapshot committed.

PENDING — two manual GitHub steps (require the repo owner's account):
1. **Enable Actions write access:** Settings → Actions → General → Workflow
   permissions → **"Read and write permissions"** → Save. Without this the daily
   commit and the failure-issue are denied.
2. **Test the runner now** (don't wait for 22:30 UTC): Actions → Daily FPL logger
   → **Run workflow**. A green run that adds a `snapshot:` commit confirms the full
   fetch→commit→push loop works from GitHub's IPs.

Also pending (dashboard, separate task):
- Update the Streamlit Cloud entrypoint to `dashboard/app.py`.
- Diagnose the "app isn't working" bug.

---

## 7. Roadmap context (why the order is what it is)

Pre-season (now → mid-Aug 2026) is the only real build runway; once the season
starts, the ~2–3 hrs/week goes entirely to publishing. Tier-1 foundations to build
before Gameweek 1, in order:

1. **Daily logger** — DONE (this repo).
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
- **Off-season timing:** as of late July 2026 the 2026-27 season hasn't started, so
  transfer fields read 0 and GW1 is flagged as `is_next`. The pipeline is already
  season-ready; it fills with signal once the market moves.
- **UTC everywhere:** snapshots are dated by UTC date; GitHub cron is UTC.
- **The local `Downloads/fpl-dossier-debug-handoff/` folder is STALE** (old
  structure, not a git repo). This GitHub repo is the source of truth — always
  work from a fresh clone of it.

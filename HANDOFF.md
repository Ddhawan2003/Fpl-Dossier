# Project Handoff / Full Context

*Read this first. It's the single source of truth for what this repo is, how it's
built, what's done, and what's pending. Written to bring a fresh session (human or
AI) fully up to speed.*

Repo: https://github.com/Ddhawan2003/Fpl-Dossier · Last major update: 2026-07-28

**Start here if you are new:** §6 is current state, **§6a is what to do next**,
§7 is the revised roadmap.

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
│   ├── log_snapshot.py           #   the capture job, self-contained, two modes
│   ├── check_gaps.py             #   asserts the record has no holes
│   └── requirements.txt          #   `requests` only
├── .github/workflows/
│   ├── daily-logger.yml          #   daily runner (cron 22:30 UTC)
│   ├── deadline-logger.yml       #   deadline runner (hourly gate, fires pre-deadline)
│   └── gap-check.yml             #   weekly: did the runs actually HAPPEN?
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

### 4c. Snapshot columns (both records, 45 columns, identical schema)

All raw FPL fields except the three capture-metadata columns. `now_cost` /
`cost_change_*` are integer tenths (`75` = £7.5) — division happens at read time.

The API exposes ~105 fields per player; the logger takes 45. The selection rule:
log it if it is **point-in-time** (overwritten the moment it changes, so it can
never be recovered) or cheap context that makes the record self-contained.
Everything else is derivable after the fact and is skipped on purpose.

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
| `news_added` | **when that note appeared** — microsecond precision, straight from the API |
| `chance_of_playing_this_round`, `chance_of_playing_next_round` | 0–100, or **empty when the API says null** |
| `penalties_order` | set-piece duty — **changes mid-season, nothing else records when** |
| `direct_freekicks_order` | as above |
| `corners_and_indirect_freekicks_order` | as above |
| `form` | rolling 30-day figure the API recomputes continuously |
| `event_points`, `total_points`, `minutes`, `starts` | scoring state to date; `starts` is the "nailed on" signal |
| `goals_scored`, `assists`, `clean_sheets`, `goals_conceded`, `saves` | actual returns |
| `defensive_contribution` | **DefCon** — worth 2 pts/match at 10+ (DEF) or 12+ (MID/FWD) |
| `clearances_blocks_interceptions`, `tackles`, `recoveries` | DefCon's components, kept so the split stays inspectable |
| `expected_goals`, `expected_assists`, `expected_goal_involvements`, `expected_goals_conceded` | the underlying numbers those actuals are measured against |
| `ep_this`, `ep_next` | **FPL's own expected-points forecast.** The most perishable data in the response: once a gameweek is played, what was predicted beforehand is gone. Also a free benchmark to grade our calls against. |

Live sample (2026-07-28): `ep_next` populated for 563/563 (Haaland 4.0);
`news_added` for 50; `penalties_order` for 64 (Haaland `1`);
`ep_this` / `chance_of_playing_this_round` empty, correct off-season.

**The actuals + expected group is a different category, and the docs should not
oversell it.** Unlike `ep_next` (a forecast, gone once the match is played) or
`news_added` (overwritten the instant news changes), these are **cumulative
counters and therefore recoverable** from `element-summary/<id>/history`. They
are logged for three smaller reasons: the record stays self-contained (no
~600-request reconstruction per gameweek), Opta's retroactive revisions to past
matches are captured as they happen, and they cost nothing on a call we already
make. Adding them in October would have lost nothing — that is not true of the
groups above.

**Over/underperformance is deliberately not a column**, nor are the `_per_90`
variants. Both are derived, and rule 2 keeps derived values out of the record.
They are exactly reproducible at read time: Haaland's 28.17 xGI over 2953
minutes gives `28.17 / (2953/90) = 0.86`, matching FPL's own published figure to
the decimal.

**Log xG and xA separately, not just xGI.** Haaland is the worked example: 27
goals against 25.50 xG is barely overperforming, but 8 assists against 2.67 xA
is a large overperformance. Combined as xGI (+6.83) the two are indistinguishable
and the actual story — the finishing is normal, the assists are the outlier — is
lost.

### 4d. The gap detector — `logger/check_gaps.py` + `gap-check.yml`

The daily and deadline workflows each open an issue when a run **fails**. Neither
can tell you about a run that never **happened** — and that is the more likely
outcome (GitHub disables schedules after 60 days of repo inactivity; Actions can
be quota-capped; cron delivery is not guaranteed). No run → no failure → no issue
→ silence, indistinguishable from success.

So this job asks the other question: *does the record actually contain what it
should?* Weekly, Mondays 09:00 UTC, in its own workflow so it does not share a
failure mode with what it checks. It is **read-only** (`contents: read`) — it can
never damage the record it audits. Running it also counts as repository activity,
which helps keep GitHub from marking the schedules dormant in the first place.

Four assertions:
1. **Freshness** — the newest snapshot *anywhere* is ≤2 days old. This is the
   alarm that matters, and it is deliberately **season-agnostic**: `season_for()`
   rolls over on 1 July but that season's directory does not exist until that
   night's run, so a season-keyed check would cry wolf every year.
2. **No holes** — a file for every UTC date from the first capture to yesterday
   (never today; the daily run is at 22:30 UTC).
3. **Deadline coverage** — a `gw<NN>.csv` for every deadline that has passed
   *since logging began*. Earlier gameweeks were never ours to capture.
4. **No truncation** — every file has ≥300 data rows. A short CSV is a gap
   wearing a valid filename.

Verified against fixtures: mid-record holes and a truncated file are both caught;
an empty new-season directory correctly does **not** alarm; a stale record does.

```bash
python logger/check_gaps.py                  # current season
python logger/check_gaps.py --season 2026-27 # a specific one
```

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
- **Known open bug:** deployment was attempted and "the app isn't working". See
  §6 for the strongest suspect (missing `User-Agent`), and `dashboard/CONTEXT.md`
  for the older, weaker candidates. Reproduce locally with
  `streamlit run dashboard/app.py`.

### 5a. Two data sources, split by question type

The dashboard reads **both** the live API and the logger's output, and the split
matters:

| Question | Source |
|---|---|
| "What is Haaland's price *right now*?" | live FPL API — fresher, no history needed |
| "What *was* it on 19 Aug, and how has it moved?" | `data/snapshots/` + `data/deadlines/` |

The existing panels stay on the live API. Everything historical — and the whole
accountability record — reads the CSVs out of this same checkout, which is free
because it is one repo. **This does not violate rule 1**: the dependency runs
dashboard → logger's *output files*, never the reverse. Break the dashboard and
tonight's snapshot still lands.

Caveat: Streamlit Cloud redeploys on every push to `main`, and the logger pushes
daily, so the app reboots roughly once a day. Harmless, but it explains a restart
that would otherwise look mysterious.

### 5b. Rebuilt around the 11 content sections (DONE 2026-07-28)

The three inherited panels (Differentials / Regression watch / Set-piece takers)
were generic FPL heuristics, not how this team actually works. The team's blog
template (see §9) defines **11 named sections** with word counts and a tone, so
the dashboard now has one tab per blog section, in publishing order — labelled
with the template's own emoji — plus a `📋 Table` utility tab.

**Files:** `dashboard/data.py` (new) owns *where numbers come from* — the live
API with correct headers, the snapshot history, fixtures, and trend helpers.
`dashboard/app.py` owns layout and the sections, and deals only in DataFrames.

**Read-only.** It shows candidates and numbers; it does not record picks.
Recording is what turns this into the ledger, and that needs the writeback
decision in §7b made first.

Each tab carries its word-count target from the template, and most carry a
**"Copy for the post"** markdown block — that is the data pack, delivered
without any of the ledger's complexity.

Three sections are **human-only by design** (Opening, Eye Test, Closing) and say
so. Eye Test especially: automating the mechanical sections exists to buy back
time for that one, so it must never sprout a table.

**Honest gating rather than plausible nonsense.** Trend sections check how much
history exists and say "1 snapshot stored, need 2+" instead of rendering flat
lines. A banner fires when `form`/`event_points` are zeroed while `total_points`
etc. still hold last season's values (§8).

**`ep_next` is coarse pre-season** — values bunch at 4.0/3.3/2.8/2.0 with heavy
ties, so an unfiltered "top expected points" returns goalkeepers and defenders as
captain candidates. Captain therefore defaults to MID/FWD and Differentials
excludes GKP, both adjustable, with a warning that the ordering is close to
arbitrary until GW1. It becomes a real ranking once the season starts.

### 5c. Underlying numbers in the workbench (added 2026-07-30)

Over/underperformance vs expected goals is wired into the four sections that
already ask for it, **not a new tab**: Buy/Sell/Hold (overperformers = the sell
case, the old "Regression watch" reborn where it belongs), Differentials
(underperformers = the "he's due" case), Scout Selection (xGI/90 for chance
creation, xGC/90 for defensive value) and Captain (xGI/90 alongside xPts).

xGI/90 sits next to xPts on the Captain tab for a measured reason: the backtest
in §10 shows FPL's projection runs ~1 point high on premiums, and xGI/90 is an
independent check on whether an expensive player is actually creating chances.

**DefCon (added 2026-07-30).** Defensive contribution is worth 2 points a match
at 10+ CBIT (defenders) or 12+ CBIRT (mid/fwd); keepers are ineligible. Since the
points are all-or-nothing per match and the API gives only season totals, the
workbench shows **DefCon/90 against the position bar** — averaging above it is
the best available proxy for "clears it most weeks" without per-match data.

Two things worth knowing:
- FPL's `defensive_contribution` **already applies the position formula**, verified
  against live data (Senesi, DEF: 419 = 357 CBI + 62 tackles, excluding his 155
  recoveries; Anderson, MID: 515 = 209 CBIT + 306 recoveries). Compare it directly
  against the bar rather than recomputing from components — the components are
  logged only so the split stays inspectable if FPL changes the rule.
- **The thresholds are not in the API.** `element_stats` names the stat but
  publishes no threshold, so `DEFCON_BAR` in `dashboard/data.py` transcribes the
  Premier League's published rules. If FPL changes them, nothing here will notice;
  that constant is the single place to correct.

It appears on Captain, Bench Order, Scout Selection, Differentials and All
players. Bench Order is where it matters most — a cheap defender or holding
midfielder clearing the bar is the bench-fodder meta — so `DefCon/90` sits
immediately after price and ownership there rather than behind a horizontal
scroll. On Scout the dedicated table is **expanded by default**: for cheap
defensive players this is the primary reason to own them, not secondary
analysis.

(It was initially left off Captain on the grounds that 2 points does not decide
an armband. Added on request, and the reasoning holds up: a captain who also
clears the bar has a higher floor on a blank week.)

**Two guards, both load-bearing:**
- `MIN_MINUTES_FOR_UNDERLYING = 450` in `dashboard/data.py`. Below roughly five
  full matches these numbers are noise; shown unguarded in September they flag
  every hot starter as "due a regression" purely because three games of xG says
  nothing. Players under the floor get blank cells, not numbers.
- The copy says **"a question, not a verdict"**, because persistent
  overperformance is not always luck — elite finishers beat xG every season.
  Distinguishing lucky from good needs a multi-season prior, which is precisely
  what the logger is accumulating.

All five new blocks sit behind collapsed expanders. The workbench had grown to 18
tables and the busiest tab was stacking five; secondary analysis is one click
away so each tab still opens on the thing it is for.

Implementation trap worth remembering: computing per-90 with
`.replace(0, pd.NA)` flips the column to object dtype, and `NAType` has no
`__round__`, which takes down the whole frame on the next line. Use
`.where(mins > 0)` so the columns stay float and NaN stays float NaN.

This supersedes the older "5 lenses" idea for tagging ledger entries: the
**section is the lens**, by construction. A pick made in the Differentials section
is a differentials call; nothing needs tagging by hand.

Roughly a third of the template is number-fetching (Captain shortlists, ownership
%, price/rise timing, minutes and price trends for Scout Selection). That third
is what the dashboard automates. **Eye Test — the longest and strongest section —
gets no automation at all.** The entire point of automating the mechanical third
is to buy back time for the section that actually differentiates the product.

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
- Schema widened to **45 columns** (§4c), in four passes. The 24-column pass added `status`,
  `news`, `chance_of_playing_next_round`, `form`, `event_points`, `total_points`,
  `minutes`, `snapshot_kind`, `deadline_time`; the 31-column pass added
  `ep_next`, `ep_this`, `news_added`, `chance_of_playing_this_round` and the
  three set-piece order fields.
- **24-column schema confirmed round-tripping from the Linux runner** — bot
  commit `c57d130` (2026-07-28 daily run) has the full header. This was the one
  path that could previously only be tested locally.
- **Gap detector shipped** (§4d) — the last hole in the logger is closed.
- Dashboard fetch bug fixed: browser `User-Agent` + `raise_for_status()`.
- `.devcontainer/devcontainer.json` repointed at `dashboard/app.py`;
  `dashboard/README.md` deploy steps corrected.

Deployment note: new workflows need **no manual setup**. Repo-wide Actions write
permission is already on, so a workflow begins running as soon as it is on
`main`. GitHub can take a few minutes to register a newly added cron.

Dashboard — resolved:
- ✅ Streamlit Cloud entrypoint set to `dashboard/app.py`; the app deploys.
- ✅ Fetch bug fixed (browser `User-Agent` + `raise_for_status()`). The missing
  header was the cause: `requests` went out as `python-requests/2.x` into the
  datacenter-IP blocking the logger carries a UA to defeat, which is why it
  worked locally and failed deployed.
- ✅ Rebuilt around the 11 sections (§5b), replacing the misleading panels.
- ✅ Old set-piece panel's arbitrary `sp_rows[:8]` slice gone — Scout Selection
  now sorts takers by ownership. (37 players hold a first-choice set-piece role
  as of 2026-07-28, so this has real data now, not only in-season.)

Verified by executing `app.py` end-to-end headlessly against the live API and
real snapshot files: 10 tables and 3 paste blocks render, both intended warnings
fire, both history gates fire, no errors. `streamlit run` alone does **not** prove
this — the script body only executes when a client connects, so a column error in
a section stays hidden behind an HTTP 200.

---

## 6a. NEXT UP — agreed sequencing (updated 2026-07-28)

- ✅ ~~**Seven more logger columns**~~ — done. A later pass added the actuals +
  expected-goals group, and a fourth added DefCon; schema is now 45 columns (§4c).
- ✅ ~~**Gap detector**~~ — done (§4d).
- ✅ ~~**Dashboard fetch bug**~~ — `User-Agent` + `raise_for_status()` added.
- ✅ ~~**devcontainer / dashboard README**~~ — stale pre-flatten paths corrected.

- ✅ ~~**Dashboard rebuild**~~ — done, read-only, 11 sections (§5b).

Expect the workbench to look sparse for a while, and do **not** mistake that for
breakage. Trend sections need a week or two of snapshots (1 stored as of
2026-07-28); performance fields are 2025-26 until GW1 (§8); `ep_next` is coarse
until the season starts. Sections that cannot say anything yet say so explicitly.

**1. NOW — decide the ledger writeback, then build it.** Recording a pick is what
   turns the read-only workbench into the calls ledger, and the whole moat rests
   on it. Streamlit Cloud has a read-only checkout, so this needs the
   git-vs-mutable-store decision in §7b. **This is the piece most likely to eat a
   week**, and it must exist before GW1 for the record to start with the season.

**2. AFTER A FEW GAMEWEEKS — an external points model as a benchmark.**
   Not before. `ep_next` is enough in the meantime, and an external model should
   not become load-bearing until it has survived a few gameweeks. See §7c.

**3. THEN — the accountability engine.** Starved until ~GW4–5 (§7).

**Open, non-blocking:** the team's **FPL team ID** (`entry/{id}/`). Bench Order
was reframed on 2026-07-28 as *reader-facing* advice — the cheap players everyone
owns and how to order them — so it no longer needs the squad. A team ID would
still auto-fill the "Our Team" Instagram story and let the tool sanity-check a
bench order against what is actually owned. Nice-to-have, nothing blocked.

**Open, needs a call:** Bench Order currently weights toward **nailed-on**
starters. The alternative is weighting toward upside (a cheap player with a good
fixture who might actually return). Stylistic choice about the advice given, not
something the data settles.

---

## 7. Roadmap (revised 2026-07-28 — four items collapsed to three)

Pre-season (now → 2026-08-21) is the only real build runway; once the season
starts, the ~2–3 hrs/week goes entirely to publishing.

**The old roadmap listed ledger, data-pack and dashboard as three separate things.
They are not.** Once the dashboard is rebuilt around the 11 blog sections, they
become **one page at three moments**:

| Moment | What it is called |
|---|---|
| Tuesday — 11 sections, candidates + numbers, nothing recorded | the **dashboard** |
| Thursday — you pick; the pick is timestamped and the market state stapled on | the **ledger** |
| Friday — hit Copy, paste into Substack | the **data pack** |
| Six weeks later — it grades what you picked | the **accountability engine** |

So the remaining build is:

1. **Market logger** — DONE: daily + deadline-anchored capture.
2. **The 11-section workbench** — dashboard + ledger + data pack, one artifact.
3. **Accountability engine** — a separate page that reads the ledger and the
   snapshots. Last not because it is hard but because it is **starved**: it needs
   ledger entries *and* played gameweeks, so before ~GW4–5 it can only say
   "no data yet".

### 7a. The three questions (use this framing when explaining it)

- **Logger** — *what was true?* Automatic, every day. Running.
- **Ledger** — *what did we say?* You, once a week, at the moment of picking.
- **Engine** — *what happened?* Automatic, weeks later.

The ledger is written **before the outcome is known** and must be unchangeable
afterwards. That gap is the entire product: if entries could be edited once
results were in, the record is just memory, and memory flatters. It also means
the ledger must record **misses** — a record containing only wins is marketing,
and publishing the bad calls is what makes the good ones believable.

### 7b. Build read-only first (the de-risking decision)

Build the 11-section workbench **read-only** to begin with: live data, no
recording, no writeback. That is immediately useful, it is most of the value, and
it is roughly a fifth of the difficulty. Add selection-and-record as a second
pass. That way there is a working workbench before GW1 even if the ledger
writeback turns out to be a slog.

**The unresolved question, and the thing most likely to eat a week:** recording a
call means **writing data back**, and Streamlit Cloud has a read-only checkout —
it cannot push to the repo without a token. Everything else here is reading files,
which is trivial. Two options, not yet decided:

- **Write to git** — tamper-evident, publicly timestamped, matches the moat
  argument. More work (needs a PAT, and a commit path that cannot collide with
  the logger's own pushes).
- **Write to a Google Sheet or similar** — fast to build, but a mutable timestamp
  is much weaker evidence, and evidence is the entire point.

Second known risk: if the ledger only understands the 11 sections, a call that
does not fit a slot never gets recorded. The team's own Twitter template
anticipates this ("extra dilemmas welcome"), so a free-form slot is needed
alongside the structured ones.

### 7c. External points model — conditions before adopting one

Agreed direction, deliberately deferred. Two rules make it work or fail:

1. **It is a benchmark to beat, not a source of picks.** If Differentials are
   ranked by whatever an external model says, the operation is reselling that
   model. The valuable version is the *disagreement*: "the model's top
   differential was X, we went with Y, here's why" — then the engine grades both.
2. **If it feeds a grade, its predictions must be logged daily.** Prediction sites
   do not keep public archives. Grading a GW6 call in December requires what the
   model said *in GW6*. The moment an external model is load-bearing for
   accountability, it becomes another unbackfillable time series we have to own —
   otherwise the grade is not reproducible and rule 2 is broken.

Candidates: **FPLReview** (best regarded, paid, redistribution restricted — read
the terms before republishing its numbers; private research use is a different
thing), **FPL Form** (has offered free exports). Terms not yet verified. Also note
the dependency risk: an external model can go paid, change format, or disappear
mid-season.

### 7d. Rejected: agents crawling Twitter / Instagram / FPL blogs

Considered and turned down on 2026-07-28. Splitting it in two:

- **Fact-gathering** (team news, pressers, predicted lineups) — genuinely useful,
  low-judgment, worth doing eventually. RSS from a few *websites* only.
- **Opinion aggregation** — rejected. It makes the output downstream of the same
  voices readers already follow, and it destroys the accountability premise: a
  call sourced from consensus grades *them*, not us. An LLM-summarised "the
  community is ~80% on X" is also an interpretation, not a measurement, so it can
  never feed a grade without breaking rule 2.

Also: **the logger already provides a better crowd signal than scraping would** —
`selected_by_percent` and the transfer flows are the revealed preferences of ~11M
managers, measured exactly, daily. §7 item 4 always needed a "vs the crowd"
benchmark, and that is it.

Practical blockers if it is ever revisited: Twitter/X API is ~$100+/month and
scraping breaches ToS; Instagram is hostile to automation and most FPL content
there is text rendered *inside images*; HTML scraping breaks silently on layout
changes. The killer is maintenance — a multi-source crawler breaks constantly, and
the team has 2–3 hrs/week that is supposed to go to publishing.

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
- **The off-season data trap.** As of 2026-07-28 the live API serves a mix that
  will mislead any analysis or UI work: `form` and `event_points` are `0` for all
  563 players, but `total_points`, `minutes`, `goals_scored`, `assists` and
  `expected_goal_involvements` still hold **2025-26** values (Haaland: 239 pts,
  2953 min, 28.17 xGI). 400/563 are non-zero; the 163 zeros are new signings and
  promoted-club players. Anything computing over performance fields right now is
  describing last season while appearing current. Those two columns will reset to
  0 when 2026-27 scoring begins, and the record will capture the exact day of the
  discontinuity. **This is now the most likely way someone misreads the data.**
- **UTC everywhere:** snapshots are dated by UTC date; GitHub cron is UTC.
- **The local `Downloads/fpl-dossier-debug-handoff/` folder is STALE** (old
  structure, not a git repo). This GitHub repo is the source of truth — always
  work from a fresh clone of it.

---

## 9. The content templates (the source of the 11 sections)

The team has a written blog/Twitter/Instagram template pack (a PDF kept by the
team, not in this repo). It is what §5b's rebuild is derived from, so a fresh
session needs its shape:

**Blog (Substack) — 11 sections, in publishing order:** Opening (100–150w) ·
**Captain** (ranked 1–4, 120–180w) · **Buy / Sell / Hold** (150–200w) · **Transfer
Roadmap** (this week / next week / by GW+2–3, 150–200w) · **Differentials** (3
players *with ownership %*, 150–200w) · **Bench Order** (50–75w) · **Scout
Selection** (fixture run, price trend, minutes trend, 75–100w) · **Eye Test**
(200–300w, explicitly the strongest and most personal section) · **50:50 Calls**
(100–150w) · **Chip Strategy** (conditional) · **Closing** (60–100w). Total
~1,300–2,000 words. Tone: light and funny, "a mate talking you through his team,
not a stats report."

**Twitter/X:** not a template — a *conversion*. The finished blog post becomes a
Bakar-style Q&A thread, every section reframed as "Q: ...", answers keeping their
original depth, **captain last** as the closing argument.

**Instagram:** 6 posts (7 in chip weeks) + 3 recurring stories, released on a tier
system — Tier 1 (Transfer Roadmap, Scout + Eye Test) early in the week, Tier 2
(Buy/Sell/Hold, Differentials, 50:50) mid-week, **Tier 3 (Captain) closest to the
deadline**.

Three consequences for the tooling:

1. **The template asks for logger data by name.** Buy/Sell/Hold wants "price/
   ownership context"; Transfer Roadmap wants "price-rise timing"; Differentials
   requires an ownership % per player; Scout Selection wants "price trend, minutes
   trend". These are not features to invent — they are already required fields on
   a form the team fills in by hand every week.
2. **The tiered release creates a staleness hazard.** Tier 1 content publishes
   days before the deadline. If a recommended player is flagged on the Thursday,
   published content is silently wrong. The team's own checklist has "Captain
   preference order cross-checked against latest team news" as a *manual* step —
   the logger's daily `status` / `news` / `news_added` capture makes a
   "what changed since you drafted this" check automatic. High-value, cheap.
3. **The blog sections *are* the calls.** Captain 1–4, Buy/Sell/Hold, 3
   Differentials, the 50:50 lean, the Scout pick — that is the ledger's content,
   which is why §7 collapses the ledger into the workbench rather than building it
   separately.

---

## 10. How good is FPL's own expected-points figure? (measured 2026-07-30)

Backtested, not assumed. **`ep_next` is not archived by the API** — neither
`element-summary/<id>/history` nor `history_past` carries it — but the community
archive at `vaastav/Fantasy-Premier-League` preserves it as an `xP` column in
`data/<season>/gws/merged_gw.csv`, alongside `total_points`. Two full seasons,
~57,000 player-gameweeks.

**It beats every naive baseline, in both seasons.** Pearson r against actual
points:

| Population | FPL xP | Last GW's pts | Mean of last 3 | Season avg |
|---|---|---|---|---|
| Everyone | **.67 / .70** | .40 / .38 | .48 / .47 | .51 / .48 |
| Played (mins>0) | **.52 / .56** | .17 / .15 | .23 / .22 | .28 / .25 |
| Started (60+ mins) | **.48 / .52** | .10 / .08 | .14 / .14 | .20 / .17 |

**Read the second row, not the first.** The r≈0.70 headline is inflated: most of
that skill is correctly predicting that non-players score zero. Among players who
actually featured — the only population you would ever captain from — it is
r≈0.52, and MAE is **1.8–2.1 points per player per gameweek**. A captaincy call
is usually decided on a 2–3 point expected gap, so the model's own error is about
the size of the decision.

**The errors are systematic, and wrong in the two places that matter most:**

| xP band | Predicts | Actually delivers |
|---|---|---|
| 0–1 | 0.45 | **1.56** |
| 3–4 | 3.58 | 3.58 |
| 6+ | 7.90 | **6.90** |

Mid-distribution is near-perfectly calibrated. It **underrates fringe players**
(it cannot predict who starts) and **overrates premiums by ~1 point**. Premiums
are where captaincy lives; fringe players are where differentials live.

**Working rules that follow from this:**
- Use it to build a shortlist and to order the bench (mid-range, well calibrated).
- Never let it break a 50:50 between two premiums. A gap under ~2 points is a
  coin flip; 3+ means the direction is probably right.
- It has no useful opinion on minutes, which is its single biggest weakness — and
  exactly what the paid models claim to fix.
- Never publish a single-player point prediction. "6 points" means "6 ± 2".

**The bar an external model must clear is now a number:** beat r≈0.52 among
players who featured (§7c).

**Caveat:** the archive is community-scraped and the capture time within each
gameweek is unknown. If some rows were scraped after kickoff, FPL may already
have updated the figure, which would inflate these correlations — so treat them
as an **upper bound**. Our own deadline captures fix this permanently: `ep_next`
stored at a known moment (~T-25min) with an exact timestamp. From GW1 the same
test can be run on our own data, and that version is publishable.

Reproduce: `analysis/backtest_xp.py` (Spearman is computed as Pearson-on-ranks
to avoid scipy, which is ABI-broken against the installed numpy).

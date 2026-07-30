# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

`HANDOFF.md` is the long-form source of truth for this project (why it exists, current
state, roadmap). Read it when you need the *why*; this file is the operational summary.

## What this repo is

Tooling for a two-person Fantasy Premier League content operation. It is **two
independent deployables in one monorepo**, deliberately running on different engines:

| Deployable | Runs on | State |
|---|---|---|
| `logger/` — daily + deadline market logger | GitHub Actions | LIVE, complete |
| `dashboard/` — Streamlit content workbench | Streamlit Community Cloud | LIVE, read-only; ledger writeback not built |

The dashboard is laid out as the **11 sections of the team's blog template**, in publishing
order, so the tool mirrors the document being written. `dashboard/data.py` owns all data
access (live API + snapshot history + trends); `dashboard/app.py` owns layout and sections
and deals only in DataFrames. Three sections (Opening, Eye Test, Closing) are **human-only by
design** and must never sprout a table — automating the mechanical ones exists to buy back
time for the Eye Test.

**Testing a Streamlit change:** `streamlit run` proves only that the server boots — the script
body executes when a client connects, so a column error inside a section hides behind an
HTTP 200. Execute `app.py` with a stubbed `streamlit` module to actually exercise every
section against the live API.

## Commands

There is **no test suite, linter, or build step**. Verification is running the thing.

```bash
# Logger (only dependency is `requests`)
pip install -r logger/requirements.txt
python logger/log_snapshot.py              # daily    -> data/snapshots/<season>/<today>.csv
python logger/log_snapshot.py --deadline   # deadline -> data/deadlines/<season>/gw<NN>.csv
python logger/log_snapshot.py --deadline --force   # bypass the window guard; recovery only

python logger/check_gaps.py                # audit the record; exit 1 if it has holes
python logger/check_gaps.py --season 2026-27

# Dashboard
pip install -r dashboard/requirements.txt
streamlit run dashboard/app.py             # http://localhost:8501
```

`--deadline` exits 1 unless run within 180 minutes *before* a real deadline. That is
correct behaviour, not a bug — see "Never write a bad capture" below.

Validating a workflow change without waiting for a cron:

```bash
python -c "import yaml; yaml.safe_load(open('.github/workflows/deadline-logger.yml'))"
```

To exercise the deadline gate's logic at an arbitrary time, run its `jq` expression with a
synthetic `$now` ISO string rather than the real clock — that is how the T-119min / T-45min /
just-past-deadline cases were verified.

Manual runs: **Actions → Daily FPL logger / Deadline FPL snapshot → Run workflow.**

## The two rules that must not be broken

**1. The logger is never downstream of anything.** It must not miss a day, so it cannot
depend on the dashboard being up or on someone breaking it while tinkering. `logger/`
imports nothing from `dashboard/` and depends only on `requests`. The dashboard reading the
logger's *output CSVs* is fine and is the plan — that direction is safe. The reverse never is.

**2. Exact arithmetic lives in deterministic code, never in an LLM.** Every logged value is
a raw API field stored verbatim: no interpretation, no rounding, no derived metrics at write
time. `minutes_to_deadline` is deliberately absent because it is derivable at read time from
`snapshot_utc` + `deadline_time`. An LLM may interpret numbers, but only on top of
already-computed exact ones.

## Why the logger is built the way it is

**The data cannot be backfilled.** `bootstrap-static` is a "now" endpoint with no history.
Every missed capture is signal lost permanently, and `transfers_in_event` /
`transfers_out_event` additionally **reset to zero at every deadline**. This drives
every design choice below, and it is why a *silent* gap is the only unrepairable error.

**Two records, two directories, on purpose.** `data/deadlines/` is separate from
`data/snapshots/` because they would otherwise collide: the 22:30 UTC daily run would
overwrite a same-day deadline capture and destroy it.

**Never write a bad capture.** The script fails loudly rather than producing a file that
looks legitimate. It refuses on: <300 players returned; a deadline that has already passed
(counters have reset); a deadline more than 180 minutes away. Writes are atomic (temp file →
`os.replace`). On failure it exits non-zero *and* opens a GitHub issue labelled
`logger-failure`. Preserve all of this when editing.

**The gap detector answers the question the others can't.** `logger/check_gaps.py` +
`gap-check.yml` (weekly, Mondays 09:00 UTC, read-only). The capture workflows alert when a
run *fails*; neither sees a run that never *happened* — no run → no failure → no issue →
silence. So this asserts the record's contents instead: freshness (newest snapshot ≤2 days
old, checked **across all seasons** — a season-keyed check would false-alarm every 1 July
when `season_for()` rolls over before that night's run creates the directory), no missing
dates, a capture per passed deadline, and no file under 300 rows.

**The deadline scheduler.** GitHub cron cannot be dynamic and FPL deadlines move weekly, so
`deadline-logger.yml` runs hourly at :05 and gates on a ~15s `curl` + `jq` check placed
*before* checkout/Python/pip, so the ~23 daily no-ops stay near-free. The 120-minute window
deliberately spans **two** hourly runs per deadline (≈T-85min and ≈T-25min): the later
overwrites the earlier with a closer capture, and if GitHub delays it past the deadline the
script refuses and the earlier capture stands. One scheduling delay must never cost a
gameweek.

Two subtleties in that workflow that look like mistakes but are not:
- The gate compares ISO-8601 timestamps as **strings**, not via jq's `fromdateiso8601` —
  that function is backed by `strptime` and is absent from some jq builds. A gate that
  silently errors is a gate that never fires.
- The gate **warns instead of failing** when the API is unreachable, because failing would
  open 24 issues a day during an FPL outage. Each deadline gets multiple gate attempts.

**The FPL API 403s requests from datacenter IPs** unless a browser `User-Agent` is sent.
GitHub runners and Streamlit Cloud are both datacenter IPs, a laptop is not — which is why a
missing header fails *only* once deployed. Both `logger/log_snapshot.py` and
`dashboard/data.py` carry the header; do not remove it from either.

## Snapshot schema semantics

41 columns, identical in both records. The API exposes ~105 fields per player; the selection
rule is "log it if it is point-in-time, or cheap context that makes the record
self-contained." Traps that are not obvious from the header:

- `now_cost`, `cost_change_*` are **integer tenths** (`155` = £15.5m). Division happens at
  read time. Never store floats — they drift.
- `chance_of_playing_*` is **empty for null, never `0`**. Empty means nothing flagged; `0`
  means ruled out. Collapsing them invents injuries (515 vs 28 in a live sample). The same
  `raw()` helper applies to every nullable field — never default one to `0`.
- `ep_next` is FPL's own forecast and the most perishable field logged: once a gameweek is
  played it cannot be recovered. It is also the free benchmark until an external model is
  adopted, and the correct way to rank Differentials (`form` is useless off-season).
- Set-piece order fields **change mid-season**; the record is the only thing that will know
  when. They are populated *now* (64 players had a `penalties_order` on 2026-07-28) — do not
  assume they are empty off-season.
- The **actuals + expected-goals group is a weaker case than the rest**, and the docs should
  not oversell it. These are cumulative counters, so they *are* recoverable from
  `element-summary/<id>/history`. They are logged to keep the record self-contained, to catch
  Opta's retroactive revisions, and because they are free on a call we already make — not
  because they would be lost.
- **Log `expected_goals` and `expected_assists` separately, never only `xGI`.** Haaland is the
  case: 27 goals vs 25.50 xG is normal finishing, but 8 assists vs 2.67 xA is a big
  overperformance. Combined as xGI (+6.83) the two are indistinguishable and the real story
  disappears.
- Over/underperformance and every `_per_90` figure are **derived at read time in
  `dashboard/data.py`**, never stored. Verified exactly reproducible: 28.17 xGI over 2953
  minutes → 0.86, matching FPL's published per-90.
- `MIN_MINUTES_FOR_UNDERLYING = 450` gates every expected-goals view. Below ~5 full matches
  the numbers are noise and would flag every hot starter as "due a regression". Sub-floor
  players get blank cells, not numbers.
- **Deriving per-90: use `.where(mins > 0)`, not `.replace(0, pd.NA)`.** The latter flips the
  column to object dtype and `NAType` has no `__round__`, which takes down the whole frame.
- `transfers_in_event` / `transfers_out_event` **reset at each deadline**. Diffing them
  across a deadline boundary yields a large negative number — segment by `event`, or diff the
  cumulative `transfers_in` / `transfers_out` instead.
- `event` is the **next** gameweek, not the one being played. During GW5's matches it reads
  `6`. This is correct: it aligns with the transfer counters, which after GW5's deadline are
  already accumulating toward GW6.
- `id` is stable **within a season only**. Never join across season directories on `id`.
  `web_name` is neither unique nor immutable.
- `selected_by_percent` and `form` arrive as strings; cast at read time.
- `snapshot_kind` (`daily`/`deadline`) exists so the distinction survives concatenation once
  the file path is gone.

**Storage is CSV, not SQLite, on purpose:** git-native, tiny diffs, human-inspectable, and a
missing day is a visibly missing file (free gap detection). A season is ~16 MB of text. If
query speed is ever needed, build a SQLite/parquet *cache* from the CSVs — the CSVs stay the
source of truth.

## Off-season data trap (as of 2026-07-28)

The 2026-27 season has not started (GW1 deadline: **2026-08-21T17:30:00Z**). The live API
currently serves a mix that will mislead any analysis or UI work:

- `form` and `event_points` are `0` for all 563 players.
- `total_points`, `minutes`, `goals_scored`, `assists`, `expected_goal_involvements` still
  hold **2025-26** values (Haaland: 239 pts, 2953 min, 28.17 xGI). 400/563 are non-zero; the
  163 zeros are new signings and promoted-club players.
- `selected_by_percent`, `status` and `news` are already live and moving.

Anything computing over performance fields right now is describing last season while
appearing current. Label the vintage or gate the feature until GW1.

## Deployment

**Logger — already live, nothing to do.** Repo-wide Actions "Read and write permissions" is
enabled (proven by commit `3a94049`, authored by `fpl-logger[bot]` from inside a runner).
Scheduled workflows only fire from the **default branch**, so a workflow change is inert
until it is merged to `main`.

**Dashboard — Streamlit Community Cloud**, entrypoint `dashboard/app.py`. It redeploys on
every push to `main`, which means the daily logger commit reboots it roughly once a day.

`.gitattributes` forces LF repo-wide. Do not disable it — this repo is edited from Windows,
and CRLF would break the workflows' bash.

## Known issues / open work

- ~~No gap detector.~~ Shipped 2026-07-28 — see above.
- **Dashboard "isn't working" on Streamlit Cloud — fix applied, not yet confirmed.** The
  cause was almost certainly a missing `User-Agent` (see above). `app.py` now sends browser
  headers and calls `raise_for_status()`. This **cannot be verified locally** — a residential
  IP is never blocked — so it needs a check against the deployed URL. Also verify the Cloud
  entrypoint is set to `dashboard/app.py`; it moved on 2026-07-27 and a stale entrypoint
  fails the deploy before any code runs. `dashboard/CONTEXT.md` lists older, weaker suspects.
- **Fixing the fetch is not sufficient.** Once it loads, the panels are still misleading
  off-season (see the data trap above): Differentials sorts by an all-zero `Form` column and
  returns five arbitrary names. The 11-section rebuild is what actually fixes this.
- `dashboard/app.py` set-piece panel slices `sp_rows[:8]` in player-id order, which is
  effectively alphabetical-by-team, not by importance.

## Roadmap (revised 2026-07-28 — read HANDOFF §6a and §7 before starting work)

Pre-season is the only real build runway; once the season starts the team's ~2–3 hrs/week
goes to publishing. GW1 deadline: **2026-08-21T17:30:00Z**.

**Done 2026-07-28/30:** schema widened to 41 columns; gap detector shipped; dashboard fetch bug
fixed (browser UA + `raise_for_status()`); Streamlit Cloud entrypoint corrected and the app
deploys; devcontainer and `dashboard/README.md` repointed; **dashboard rebuilt around the 11
content sections**, read-only, with `dashboard/data.py` owning all data access.

**Next actions, in order:**

1. **Ledger writeback.** Recording a pick is what turns the read-only workbench into the
   calls ledger, and the entire moat rests on it. Needs the git-vs-mutable-store decision
   (HANDOFF §7b) — Streamlit Cloud has a read-only checkout. Most likely to eat a week, and
   it should exist before GW1 so the record starts with the season.
2. **External points model as a benchmark** — deferred until a few gameweeks in. `ep_next`
   is enough in the meantime.
3. **Accountability engine** — starved until ~GW4–5.

Open, non-blocking: the team's FPL team ID would auto-fill the "Our Team" story and let the
tool sanity-check bench order against what is actually owned. Bench Order itself was reframed
as reader-facing advice (the cheap players everyone owns), so it no longer needs the squad.
Open, needs a call: bench ranking currently weights **nailed-on** over upside.

**The old four-item roadmap collapsed to three.** Dashboard, calls ledger and data-pack
generator are not separate features — once the dashboard is rebuilt around the team's 11 blog
sections they are *one page at three moments*: browse (dashboard) → pick, which timestamps
the call and staples on the market state (ledger) → Copy as markdown (data pack). The
**accountability engine** stays separate and comes last, not because it is hard but because
it is starved: it needs ledger entries *and* played gameweeks, so it says "no data yet" until
roughly GW4–5.

Build the workbench **read-only first** — most of the value, a fifth of the difficulty. The
unresolved question is how the ledger writes back: Streamlit Cloud has a read-only checkout,
so recording a call needs either a PAT-authenticated git push (tamper-evident, matches the
moat argument) or an easier mutable store (weaker evidence — and evidence is the point).

Rejected on 2026-07-28: agents crawling Twitter/Instagram/FPL blogs for *opinion*. It makes
the output downstream of the same voices readers already follow, and a call sourced from
consensus grades them, not us. Fact-gathering (team news) via RSS is still fine later. The
logger already supplies a better crowd signal than scraping would — ownership and transfer
flows are ~11M managers' revealed preferences, measured exactly. See HANDOFF §7d.

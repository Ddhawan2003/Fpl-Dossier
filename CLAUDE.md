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
| `dashboard/` — Streamlit research workbench | Streamlit Community Cloud | deployed but broken; see "Known issues" |

## Commands

There is **no test suite, linter, or build step**. Verification is running the thing.

```bash
# Logger (only dependency is `requests`)
pip install -r logger/requirements.txt
python logger/log_snapshot.py              # daily    -> data/snapshots/<season>/<today>.csv
python logger/log_snapshot.py --deadline   # deadline -> data/deadlines/<season>/gw<NN>.csv
python logger/log_snapshot.py --deadline --force   # bypass the window guard; recovery only

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
GitHub runners and Streamlit Cloud are both datacenter IPs. `logger/log_snapshot.py` carries
one; `dashboard/app.py` does not (see "Known issues").

## Snapshot schema semantics

24 columns, identical in both records. Traps that are not obvious from the header:

- `now_cost`, `cost_change_*` are **integer tenths** (`155` = £15.5m). Division happens at
  read time. Never store floats — they drift.
- `chance_of_playing_next_round` is **empty for null, never `0`**. Empty means nothing
  flagged; `0` means ruled out. Collapsing them invents injuries (515 vs 28 in a live sample).
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

- **No gap detector.** The failure issue fires when a run *fails*, not when a run *never
  happens* (GitHub disables schedules after 60 days of repo inactivity; Actions can be
  quota-capped). No run → no failure → no issue → silence, indistinguishable from success.
  This is the one remaining hole in the logger.
- **Dashboard "isn't working" on Streamlit Cloud.** Strong suspect, not yet confirmed:
  `dashboard/app.py` calls `requests.get(...)` with **no `User-Agent`**, so it is subject to
  the datacenter-IP blocking the logger carries a browser UA to defeat — explaining why it
  works locally and fails deployed. With no `raise_for_status()`, a 403 HTML body reaches
  `.json()`, raises, and lands in the red "Could not reach the FPL API" box → `st.stop()`.
  `dashboard/CONTEXT.md` lists older suspects; this one is not on that list.
- `dashboard/app.py` set-piece panel slices `sp_rows[:8]` in player-id order, which is
  effectively alphabetical-by-team, not by importance.
- `.devcontainer/devcontainer.json` still points at the pre-flatten path
  `fpl-dossier-master/fpl-dossier/app.py`, so Codespaces launches nothing.
- `dashboard/README.md` still describes deploying with `app.py` at the repo root.

## Roadmap (revised 2026-07-28 — read HANDOFF §6a and §7 before starting work)

Pre-season is the only real build runway; once the season starts the team's ~2–3 hrs/week
goes to publishing. GW1 deadline: **2026-08-21T17:30:00Z**.

**Next actions, in order:**

1. **Six more logger columns** — `ep_next` (+`ep_this`), `news_added`,
   `chance_of_playing_this_round`, and the three set-piece order fields. ~15 minutes, and
   *perishable*: `ep_next` is FPL's own forecast (535/563 populated), and a forecast is gone
   the moment the gameweek plays. Set-piece duties change mid-season with no record of when.
   Do this before the dashboard — never let a long task block a short perishable one.
2. **The dashboard** — fix the fetch (browser UA + `raise_for_status()`), confirm against the
   *deployed* URL since the 403 only reproduces from a datacenter IP, then rebuild around the
   11 content sections. Everything else is blocked behind this.
3. **External points model as a benchmark** — deferred until a few gameweeks in. `ep_next`
   ranks Differentials in the meantime.

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

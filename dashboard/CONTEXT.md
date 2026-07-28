# Context: Gameweek Dossier (FPL Streamlit app) — debugging handoff

## What this is
A Streamlit dashboard for Fantasy Premier League (FPL) content research/prep.
Pulls live player stats, fixture difficulty, ownership, and set-piece data,
and surfaces it in three summary panels plus a sortable/filterable table.
Built for internal use (two people prepping weekly content), not public-facing.

## Current status
Deployment attempted on **Streamlit Community Cloud**. User reports "app isn't
working" — no specific error captured yet. Need to reproduce and diagnose.

## Files (all in the project root unless noted)
- `app.py` — the entire app (single file, ~200 lines)
- `requirements.txt` — `streamlit>=1.36`, `pandas>=2.0`, `requests>=2.31`
- `.streamlit/config.toml` — dark theme config (colors only, shouldn't affect function)
- `README.md` — deploy steps (GitHub push → share.streamlit.io → New app → app.py as entrypoint)

## Architecture / data flow
1. `load_fpl_data()` — `@st.cache_data(ttl=900)` wrapped function, does two
   `requests.get()` calls directly to:
   - `https://fantasy.premierleague.com/api/bootstrap-static/` (players, teams, gameweek events)
   - `https://fantasy.premierleague.com/api/fixtures/` (all fixtures with difficulty ratings)
2. `build_dataframe()` — turns the raw JSON into a pandas DataFrame, computing:
   - `xGI` from `expected_goal_involvements` (string in API, cast to float)
   - `Actual G+A` from `goals_scored + assists`
   - `Over/Under` = actual minus xGI (regression indicator)
   - `Next 5 FDR` = average fixture difficulty over the next 5 unplayed fixtures for that player's team
   - `Net Transfers` = `transfers_in_event - transfers_out_event`
3. Three panel sections (Differentials / Regression watch / Set piece takers) filter
   this DataFrame and render as styled HTML rows via `st.markdown(..., unsafe_allow_html=True)`.
4. Main table: filtered by position/search/availability, styled with
   `DataFrame.style.apply(highlight_rows, axis=1)`, rendered via `st.dataframe`.

## Known risk points to check first (most likely culprits)

1. **`st.rerun()` compatibility** — this API was renamed from `st.experimental_rerun()`
   in Streamlit ~1.27+. If Streamlit Cloud resolved an older pinned version despite
   `requirements.txt`, this line in the Refresh button handler could throw
   `AttributeError: module 'streamlit' has no attribute 'rerun'`. Check the deployed
   Python/Streamlit version in the Cloud app's logs.

2. **FPL API request blocked from Streamlit Cloud's IP range** — the official FPL
   API has no public rate-limit docs but is known to occasionally 403 or timeout
   requests from cloud/datacenter IPs (Streamlit Cloud, Render, etc. share ranges
   with bots). If `requests.get(...)` raises `ConnectionError`, `Timeout`, or
   returns a non-200, the app should show the `st.error(...)` block near the top
   of `app.py` — check if that's what "not working" means, vs. a hard crash.

3. **`.style.apply()` + `.format()` chain fragility** — pandas Styler API has had
   breaking changes across versions; if `pandas>=2.0` resolves to something
   Streamlit's `st.dataframe` doesn't render cleanly, this could throw or silently
   render blank. Worth testing `st.dataframe(display_df)` alone (no `.style`) as
   an isolation step.

4. **`expected_goal_involvements` or other FPL fields missing/renamed** — the FPL
   API schema isn't officially documented and fields occasionally change between
   seasons. If any `p.get(...)` call assumes a field exists but it's been renamed,
   values silently become `0`/`None` rather than crashing — check if the app loads
   but shows empty/zero data rather than erroring.

5. **Off-season timing** — as of when this was built (mid-July 2026), the FPL
   season is in its off-season gap (2025-26 concluded, 2026-27 not yet started).
   `bootstrap-static` may return incomplete `events` (no `is_next`/`is_current`
   event flagged), which would make `next_event` `None` — check any code path
   that assumes `next_event` exists.

## What to paste into Claude Code along with this file
- The **exact error message / traceback** from the Streamlit Cloud app logs
  (Manage app → logs, in the Streamlit Cloud dashboard), or a screenshot description
  of what the deployed page actually shows (blank page, red error box, stuck loading, etc.)
- Whether it fails locally too (`streamlit run app.py`) or only on Streamlit Cloud
- The Python version Streamlit Cloud is using (visible in the app logs)

## Full source
See `app.py` in this same handoff bundle.

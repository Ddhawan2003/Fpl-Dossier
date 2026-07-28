# Gameweek Dossier

Internal FPL prep dashboard: live player stats, xGI vs actual returns, fixture
difficulty, ownership, set-piece takers, and price-change signals — pulled
directly from the official FPL API.

> This app lives in `dashboard/` inside the [Fpl-Dossier](../) monorepo. All
> commands below run from the **repo root**, not from this directory.

## Run it locally

```bash
pip install -r dashboard/requirements.txt
streamlit run dashboard/app.py
```

It should open at `http://localhost:8501`.

## Deploy to Streamlit Community Cloud (free)

The repo already exists and is already connected. You only need this section
when creating the app or repointing it.

1. **Go to** [share.streamlit.io](https://share.streamlit.io) and sign in with
   your GitHub account.

2. Click **"New app"**, select the repo and branch (`main`), and set the main
   file path to **`dashboard/app.py`**.

   ⚠️ This is the setting that breaks after a reorganisation. The app used to
   live at `fpl-dossier-master/fpl-dossier/app.py` and was flattened to
   `dashboard/` on 2026-07-27. If the entrypoint in the Streamlit Cloud app
   settings still points at an old path, the deploy fails before any of this
   code runs.

3. Click **Deploy**. First build takes a minute or two.

4. **Updating it later**: any push to `main` redeploys automatically — including
   the logger's nightly `snapshot:` commit, so expect the app to reboot roughly
   once a day. That is normal, not a fault.

## Notes

- Data refreshes every 15 minutes automatically (via `st.cache_data(ttl=900)`),
  or instantly with the **Refresh data** button in the app.
- **The FPL API blocks datacenter IPs** unless a browser `User-Agent` is sent.
  Streamlit Cloud runs on datacenter IPs, your laptop does not — so a missing
  header fails *only* once deployed. `app.py` sends the header and calls
  `raise_for_status()`; don't remove either. See `CONTEXT.md` for the history.
- Free tier apps can sleep after a period of inactivity — the first visit after
  a while takes a few extra seconds to spin back up.
- The dashboard may read the logger's output (`data/snapshots/`,
  `data/deadlines/`) straight from the checkout. That dependency runs one way
  only: nothing in `logger/` may ever import from here.

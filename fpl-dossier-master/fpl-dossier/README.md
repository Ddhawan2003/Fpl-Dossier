# Gameweek Dossier

Internal FPL prep dashboard: live player stats, xGI vs actual returns, fixture
difficulty, ownership, set-piece takers, and price-change signals — pulled
directly from the official FPL API.

## Run it locally first (optional but recommended)

```bash
pip install -r requirements.txt
streamlit run app.py
```

It should open at `http://localhost:8501`.

## Deploy to Streamlit Community Cloud (free)

1. **Create a GitHub repo** and push these three items to it:
   - `app.py`
   - `requirements.txt`
   - `.streamlit/config.toml`

   ```bash
   git init
   git add .
   git commit -m "Gameweek dossier"
   git branch -M main
   git remote add origin https://github.com/<your-username>/<repo-name>.git
   git push -u origin main
   ```

2. **Go to** [share.streamlit.io](https://share.streamlit.io) and sign in with
   your GitHub account.

3. Click **"New app"**, select the repo and branch (`main`), and set the main
   file path to `app.py`.

4. Click **Deploy**. First build takes a minute or two. You'll get a URL like
   `https://<something>.streamlit.app` — that's your live app, share it with
   your co-founder or keep it private via the app's sharing settings.

5. **Updating it later**: any time you push a new commit to `main`, the
   deployed app redeploys automatically. No extra steps needed.

## Notes

- Data refreshes every 15 minutes automatically (via `st.cache_data(ttl=900)`),
  or instantly with the **Refresh data** button in the app.
- This calls the official FPL API directly — no proxy needed, since Streamlit
  runs server-side (CORS only applies to browser requests, which is why the
  earlier browser-based version needed a workaround).
- Free tier apps on Streamlit Community Cloud can go to sleep after a period
  of inactivity — the first visit after a while will just take a few extra
  seconds to spin back up.

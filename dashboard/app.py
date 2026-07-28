import streamlit as st
import pandas as pd
import requests
from datetime import datetime

st.set_page_config(page_title="Gameweek Dossier", layout="wide", page_icon="⚽")

# ---------- Styling ----------
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap');
html, body, [class*="css"]  { font-family: 'IBM Plex Mono', monospace; }
h1, h2, h3 { font-family: 'Space Grotesk', sans-serif !important; }
.gw-badge {
    display:inline-block; font-family:'IBM Plex Mono', monospace; font-size:0.85rem;
    letter-spacing:0.12em; color:#c99a3e; border:1px solid #c99a3e;
    padding:4px 10px; border-radius:2px; margin-bottom:8px;
}
.panel-row { display:flex; justify-content:space-between; padding:4px 0; border-bottom:1px solid rgba(255,255,255,0.08); font-size:0.85rem; }
.tag { font-family:'IBM Plex Mono', monospace; font-size:0.7rem; padding:2px 6px; border-radius:2px; }
.tag-diff { background:rgba(201,154,62,0.18); color:#c99a3e; }
.tag-risk { background:rgba(184,83,47,0.18); color:#e0764e; }
.tag-buy  { background:rgba(62,143,138,0.18); color:#5cb8b2; }
</style>
""", unsafe_allow_html=True)

POS_NAMES = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}

# The FPL API 403s / times out for requests from datacenter IPs unless a
# browser-like User-Agent is sent. Streamlit Community Cloud runs on datacenter
# IPs; your laptop does not -- which is exactly why this app worked locally and
# failed once deployed. logger/log_snapshot.py has carried these headers from
# day one for the same reason.
FPL_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
    ),
    "Accept": "application/json",
}


# ---------- Data loading ----------
def _get_json(url):
    """Fetch JSON, failing loudly on a non-200.

    Without raise_for_status() a 403 returns an HTML error page, .json() raises
    a confusing JSONDecodeError, and the real cause (blocked request) is hidden.
    """
    resp = requests.get(url, headers=FPL_HEADERS, timeout=15)
    resp.raise_for_status()
    return resp.json()


@st.cache_data(ttl=900, show_spinner=False)
def load_fpl_data():
    bootstrap = _get_json("https://fantasy.premierleague.com/api/bootstrap-static/")
    fixtures = _get_json("https://fantasy.premierleague.com/api/fixtures/")
    return bootstrap, fixtures


def build_dataframe(bootstrap, fixtures):
    teams = {t["id"]: t for t in bootstrap["teams"]}
    events = bootstrap["events"]
    next_event = next((e for e in events if e.get("is_next")), None) or \
                 next((e for e in events if e.get("is_current")), None)

    upcoming = sorted(
        [f for f in fixtures if not f.get("finished") and f.get("event") is not None],
        key=lambda f: f["event"],
    )
    by_team = {tid: [] for tid in teams}
    for f in upcoming:
        if f["team_h"] in by_team:
            by_team[f["team_h"]].append(f["team_h_difficulty"])
        if f["team_a"] in by_team:
            by_team[f["team_a"]].append(f["team_a_difficulty"])
    by_team = {tid: v[:5] for tid, v in by_team.items()}

    rows = []
    for p in bootstrap["elements"]:
        xgi = float(p.get("expected_goal_involvements") or 0)
        actual_gi = (p.get("goals_scored") or 0) + (p.get("assists") or 0)
        team_id = p["team"]
        next5 = by_team.get(team_id, [])
        fix_run = sum(next5) / len(next5) if next5 else None
        net_transfers = (p.get("transfers_in_event") or 0) - (p.get("transfers_out_event") or 0)
        rows.append({
            "Player": p["web_name"],
            "Team": teams[team_id]["short_name"],
            "pos": p["element_type"],
            "Pos": POS_NAMES.get(p["element_type"], "?"),
            "Price": p["now_cost"] / 10,
            "Own %": float(p.get("selected_by_percent") or 0),
            "Form": float(p.get("form") or 0),
            "xGI": round(xgi, 2),
            "Actual G+A": actual_gi,
            "Over/Under": round(actual_gi - xgi, 2),
            "Next 5 FDR": round(fix_run, 2) if fix_run is not None else None,
            "Net Transfers": net_transfers,
            "status": p.get("status"),
            "news": p.get("news") or "",
            "minutes": p.get("minutes") or 0,
            "pen_order": p.get("penalties_order"),
            "fk_order": p.get("direct_freekicks_order"),
            "cor_order": p.get("corners_and_indirect_freekicks_order"),
        })
    df = pd.DataFrame(rows)
    return df, next_event


# ---------- Load ----------
with st.spinner("Fetching live FPL data..."):
    try:
        bootstrap, fixtures = load_fpl_data()
        df, next_event = build_dataframe(bootstrap, fixtures)
        load_error = None
    except Exception as e:
        df, next_event, load_error = pd.DataFrame(), None, str(e)

# ---------- Header ----------
gw_label = f"GW {next_event['id']}" if next_event else "GW --"
st.markdown(f'<div class="gw-badge">{gw_label}</div>', unsafe_allow_html=True)

col_title, col_refresh = st.columns([5, 1])
with col_title:
    st.title("Gameweek Dossier")
    if next_event and next_event.get("deadline_time"):
        try:
            dl = datetime.fromisoformat(next_event["deadline_time"].replace("Z", "+00:00"))
            st.caption(f"Deadline: {dl.strftime('%a %d %b, %H:%M UTC')}")
        except Exception:
            pass
    st.caption("Internal prep sheet — underlying stats, fixtures, minutes, set pieces, ownership")
with col_refresh:
    if st.button("Refresh data"):
        st.cache_data.clear()
        st.rerun()

if load_error:
    st.error(f"Could not reach the FPL API right now: {load_error}. Try Refresh in a moment.")
    st.stop()

# ---------- Quick panels ----------
p1, p2, p3 = st.columns(3)

with p1:
    st.subheader("Differentials")
    st.caption("Low ownership, strong underlying form")
    diffs = df[(df["Own %"] < 10) & (df["minutes"] > 270) & (df["status"] == "a")] \
        .sort_values("Form", ascending=False).head(5)
    if diffs.empty:
        st.caption("No qualifying players yet.")
    for _, r in diffs.iterrows():
        st.markdown(
            f'<div class="panel-row"><span>{r["Player"]} '
            f'<span style="opacity:0.6;font-size:0.75rem">{r["Team"]}</span></span>'
            f'<span class="tag tag-diff">{r["Own %"]:.1f}% · form {r["Form"]:.1f}</span></div>',
            unsafe_allow_html=True,
        )

with p2:
    st.subheader("Regression watch")
    st.caption("Actual returns well above expected — cool-off risk")
    risk = df[(df["minutes"] > 270) & (df["Over/Under"] >= 1.5)] \
        .sort_values("Over/Under", ascending=False).head(5)
    if risk.empty:
        st.caption("Nothing flagged this week.")
    for _, r in risk.iterrows():
        st.markdown(
            f'<div class="panel-row"><span>{r["Player"]} '
            f'<span style="opacity:0.6;font-size:0.75rem">{r["Team"]}</span></span>'
            f'<span class="tag tag-risk">+{r["Over/Under"]:.1f} vs xGI</span></div>',
            unsafe_allow_html=True,
        )

with p3:
    st.subheader("Set piece takers")
    st.caption("Penalties · direct free kicks · corners (1st choice)")
    sp_rows = []
    for _, r in df.iterrows():
        roles = []
        if r["pen_order"] == 1: roles.append("PEN")
        if r["fk_order"] == 1: roles.append("FK")
        if r["cor_order"] == 1: roles.append("COR")
        if roles:
            sp_rows.append((r["Player"], r["Team"], " · ".join(roles)))
    if not sp_rows:
        st.caption("No data yet.")
    for name, team, roles in sp_rows[:8]:
        st.markdown(
            f'<div class="panel-row"><span>{name} '
            f'<span style="opacity:0.6;font-size:0.75rem">{team}</span></span>'
            f'<span class="tag tag-buy">{roles}</span></div>',
            unsafe_allow_html=True,
        )

st.divider()

# ---------- Controls ----------
c1, c2, c3 = st.columns([2, 3, 2])
with c1:
    pos_choice = st.selectbox("Position", ["All", "GKP", "DEF", "MID", "FWD"])
with c2:
    search = st.text_input("Search player or team", "")
with c3:
    avail_only = st.checkbox("Available only", value=False)

filtered = df.copy()
if pos_choice != "All":
    filtered = filtered[filtered["Pos"] == pos_choice]
if avail_only:
    filtered = filtered[filtered["status"] == "a"]
if search:
    q = search.lower()
    filtered = filtered[
        filtered["Player"].str.lower().str.contains(q) | filtered["Team"].str.lower().str.contains(q)
    ]

filtered["Status"] = filtered.apply(
    lambda r: "Available" if r["status"] == "a" else (r["news"] or str(r["status"]).upper()), axis=1
)

display_cols = ["Player", "Team", "Pos", "Price", "Own %", "Form", "xGI",
                 "Actual G+A", "Over/Under", "Next 5 FDR", "Net Transfers", "Status"]
display_df = filtered[display_cols].sort_values("Form", ascending=False)


def highlight_rows(row):
    styles = [""] * len(row)
    if row["Status"] not in ("Available",):
        styles = ["background-color: rgba(184,83,47,0.12)"] * len(row)
    elif row["Over/Under"] <= -1.5:
        styles = ["background-color: rgba(62,143,138,0.10)"] * len(row)
    return styles


st.dataframe(
    display_df.style.apply(highlight_rows, axis=1).format({
        "Price": "£{:.1f}", "Own %": "{:.1f}%", "Form": "{:.1f}",
        "xGI": "{:.2f}", "Over/Under": "{:+.2f}", "Next 5 FDR": "{:.2f}",
        "Net Transfers": "{:+,.0f}",
    }, na_rep="—"),
    use_container_width=True,
    hide_index=True,
    height=560,
)

st.caption(
    "Data from the official FPL API, fetched live. Unofficial internal tool for content prep — "
    "verify anything published against fantasy.premierleague.com directly."
)

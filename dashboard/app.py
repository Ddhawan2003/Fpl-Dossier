"""Gameweek Dossier -- the weekly content workbench.

Laid out as the eleven sections of the blog template, in publishing order, so
the tool mirrors the document being written rather than a generic FPL table.
Each tab carries its word-count target and a paste-ready markdown block.

Read-only by design for now: it shows candidates and numbers, it does not record
picks. Recording is what turns this into the calls ledger, and that needs the
git-vs-mutable-store decision made first (HANDOFF §7b).
"""

from datetime import datetime, timezone

import pandas as pd
import streamlit as st

import data as fpl

st.set_page_config(page_title="Gameweek Dossier", layout="wide", page_icon="⚽")

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
.target { font-size:0.75rem; opacity:0.55; letter-spacing:0.08em; text-transform:uppercase; }
.human { border-left:2px solid #c99a3e; padding:8px 14px; opacity:0.85; font-size:0.9rem; }
</style>
""", unsafe_allow_html=True)

# Word-count targets and tone notes, lifted from the blog template so the
# workbench states the brief rather than assuming it is remembered.
TARGETS = {
    "Opening": "100–150 words",
    "Captain": "120–180 words · ranked 1–4",
    "Buy / Sell / Hold": "150–200 words across the three",
    "Transfer Roadmap": "150–200 words",
    "Differentials": "150–200 words · 3 players with ownership %",
    "Bench Order": "50–75 words",
    "Scout Selection": "75–100 words",
    "Eye Test": "200–300 words · your strongest section",
    "50:50 Calls": "100–150 words",
    "Chip Strategy": "100–200 words · only when relevant",
    "Closing": "60–100 words",
}


def target(name: str) -> None:
    st.markdown(f'<div class="target">Target: {TARGETS[name]}</div>', unsafe_allow_html=True)


def human_only(note: str) -> None:
    st.markdown(f'<div class="human">{note}</div>', unsafe_allow_html=True)


def paste_block(md: str) -> None:
    """Paste-ready markdown. st.code gives a copy button for free."""
    with st.expander("Copy for the post"):
        st.code(md.strip(), language="markdown")


def need_history(have: int, want: int = fpl.MIN_HISTORY_DAYS) -> None:
    st.info(
        f"Not enough history yet — {have} daily snapshot(s) stored, need {want}+. "
        "The logger adds one every night at 22:30 UTC; this fills itself in."
    )


# ---------- Load ----------
with st.spinner("Fetching live FPL data..."):
    try:
        bootstrap, fixtures = fpl.load_live()
        df = fpl.build_players(bootstrap, fixtures)
        teams = {t["id"]: t for t in bootstrap["teams"]}
        vintage = fpl.detect_vintage(bootstrap)
        ev = fpl.next_event(bootstrap)
        load_error = None
    except Exception as e:  # noqa: BLE001
        df, teams, ev, vintage, load_error = pd.DataFrame(), {}, None, {}, str(e)

season = fpl.current_season()
history = fpl.load_history(season)
hist_dates = fpl.history_dates(history)

# ---------- Header ----------
gw_label = "GW {}".format(ev["id"]) if ev else "GW --"
st.markdown(f'<div class="gw-badge">{gw_label}</div>', unsafe_allow_html=True)
col_title, col_refresh = st.columns([5, 1])
with col_title:
    st.title("Gameweek Dossier")
    if ev and ev.get("deadline_time"):
        try:
            dl = datetime.fromisoformat(ev["deadline_time"].replace("Z", "+00:00"))
            left = dl - datetime.now(timezone.utc)
            st.caption(
                f"Deadline: {dl.strftime('%a %d %b, %H:%M UTC')} "
                f"({left.days}d {left.seconds // 3600}h away)"
                if left.total_seconds() > 0
                else f"Deadline: {dl.strftime('%a %d %b, %H:%M UTC')} (passed)"
            )
        except Exception:  # noqa: BLE001
            st.caption(f"Deadline: {ev['deadline_time']}")
    st.caption("Internal prep sheet — one tab per blog section, in publishing order")
with col_refresh:
    if st.button("Refresh data"):
        st.cache_data.clear()
        st.rerun()

if load_error:
    st.error(f"Could not reach the FPL API: {load_error}")
    st.stop()

if vintage.get("stale_performance"):
    st.warning(f"⚠️ {vintage['note']}")

st.caption(
    f"Snapshot history: **{len(hist_dates)}** day(s) stored for {season}"
    + (f" ({hist_dates[0]} → {hist_dates[-1]})" if hist_dates else "")
)

TABS = ["👋 Opening", "🧢 Captain", "🔁 Buy/Sell/Hold", "🗺 Roadmap", "🎯 Differentials",
        "🪑 Bench", "🔍 Scout", "👁 Eye Test", "🎲 50:50", "🃏 Chips", "🏁 Closing", "📋 Table"]
t = st.tabs(TABS)

# ---------- 1. Opening ----------
with t[0]:
    target("Opening")
    human_only(
        "No data by design. Hook the reader — a self-deprecating line about last "
        "week's rank, a joke about the state of the game, or a tease of what's coming. "
        "Conversational, like catching up with a mate."
    )
    st.caption(
        "Once the calls ledger exists this tab will hand you last week's callback "
        "automatically: what we said, and what actually happened."
    )

# ---------- 2. Captain ----------
with t[1]:
    target("Captain")
    st.caption(
        "Ranked by FPL's own expected points (`ep_next`) — the only forward-looking "
        "number available, and the reason `form` is not used here (it reads 0.0 for "
        "every player until the season starts)."
    )
    if vintage.get("stale_performance"):
        st.warning(
            "⚠️ `ep_next` is **coarse pre-season** — values bunch at 4.0/3.3/2.8/2.0 with "
            "heavy ties, so this ordering is close to arbitrary until GW1. Treat it as a "
            "shortlist, not a ranking. Ties break on ownership as a rough quality proxy."
        )
    cap_pos = st.multiselect("Positions", ["GKP", "DEF", "MID", "FWD"], default=["MID", "FWD"],
                             help="Captains are almost always mids and forwards.")
    caps = df[df["Available"] & df["Pos"].isin(cap_pos)] \
        .sort_values(["xP next", "Own %"], ascending=[False, False]).head(12)
    st.dataframe(
        caps[["Player", "Team", "Pos", "Price", "xP next", "Own %", "Next 5 FDR", "Fixtures"]],
        hide_index=True, use_container_width=True,
    )
    top4 = caps.head(4)
    paste_block("\n".join(
        f"{i}. **{r['Player']}** ({r['Team']}) — xP {r['xP next']:.1f}, "
        f"{r['Own %']:.1f}% owned, next: {r['Fixtures'].split(' ')[0] if r['Fixtures'] else 'TBC'}"
        for i, (_, r) in enumerate(top4.iterrows(), 1)
    ))

# ---------- 3. Buy / Sell / Hold ----------
with t[2]:
    target("Buy / Sell / Hold")
    st.caption("The template asks for *price and ownership context* — that is this tab.")

    price = fpl.movement(history, "now_cost", days=7)
    owned = fpl.movement(history, "selected_by_percent", days=7)

    if price.empty:
        need_history(len(hist_dates))
    else:
        merged = price.merge(df[["id", "Player", "Team", "Price"]], on="id", how="inner")
        merged["Δ price"] = merged["delta"] / 10
        movers = merged[merged["delta"] != 0].sort_values("delta", ascending=False)
        c1, c2 = st.columns(2)
        with c1:
            st.subheader("Risers")
            st.dataframe(movers.head(10)[["Player", "Team", "Price", "Δ price"]],
                         hide_index=True, use_container_width=True)
        with c2:
            st.subheader("Fallers")
            st.dataframe(movers.tail(10).sort_values("delta")[["Player", "Team", "Price", "Δ price"]],
                         hide_index=True, use_container_width=True)

    st.subheader("Newly flagged")
    flagged = df[(~df["Available"]) & (df["Own %"] >= 1.0)].sort_values("Own %", ascending=False)
    if flagged.empty:
        st.caption("Nobody widely owned is currently flagged.")
    else:
        st.dataframe(flagged[["Player", "Team", "Own %", "Flag", "news"]].head(15),
                     hide_index=True, use_container_width=True)

    if not owned.empty:
        st.subheader("Ownership swings (7d)")
        osw = owned.merge(df[["id", "Player", "Team"]], on="id", how="inner")
        osw = osw.reindex(osw["delta"].abs().sort_values(ascending=False).index)
        osw["Δ own %"] = osw["delta"].round(1)
        st.dataframe(osw.head(10)[["Player", "Team", "then", "now", "Δ own %"]],
                     hide_index=True, use_container_width=True)

# ---------- 4. Transfer Roadmap ----------
with t[3]:
    target("Transfer Roadmap")
    st.caption(
        "Fixture swings are the engine of a roadmap: which teams' runs turn good "
        "over the next few gameweeks. Averages the next 5 fixtures' difficulty."
    )
    runs = fpl.team_fixtures(fixtures, horizon=5)
    rows = []
    for tid, fx in runs.items():
        if not fx:
            continue
        rows.append({
            "Team": teams.get(tid, {}).get("short_name", "?"),
            "Next 5 FDR": round(sum(f[3] for f in fx) / len(fx), 2),
            "Fixtures": " ".join(
                f"{teams.get(o, {}).get('short_name', '?')}{'(H)' if h else '(A)'}"
                for _, o, h, _ in fx
            ),
        })
    ticker = pd.DataFrame(rows).sort_values("Next 5 FDR")
    st.dataframe(ticker, hide_index=True, use_container_width=True)
    paste_block("\n".join(
        f"- **{r['Team']}** (FDR {r['Next 5 FDR']}): {r['Fixtures']}"
        for _, r in ticker.head(5).iterrows()
    ))

# ---------- 5. Differentials ----------
with t[4]:
    target("Differentials")
    c1, c2 = st.columns([2, 3])
    with c1:
        own_cap = st.slider("Ownership ceiling (%)", 1.0, 25.0, 10.0, 0.5)
    with c2:
        diff_pos = st.multiselect("Positions", ["GKP", "DEF", "MID", "FWD"],
                                  default=["DEF", "MID", "FWD"], key="diff_pos",
                                  help="Keepers are rarely the differential story.")
    diffs = df[(df["Own %"] < own_cap) & df["Available"] & df["Pos"].isin(diff_pos)] \
        .sort_values(["xP next", "Own %"], ascending=[False, True]).head(15)
    st.dataframe(
        diffs[["Player", "Team", "Pos", "Price", "Own %", "xP next", "Next 5 FDR", "Fixtures"]],
        hide_index=True, use_container_width=True,
    )

    owned = fpl.movement(history, "selected_by_percent", days=7)
    if owned.empty:
        st.caption(
            "⏳ Ownership *direction* needs a few days of snapshots. It matters: a player "
            "at 8% and climbing is a very different call from 8% and flat, and only the "
            "stored history knows which one you are looking at."
        )
    else:
        trend = diffs.merge(owned[["id", "delta"]], on="id", how="left")
        trend["Δ own 7d"] = trend["delta"].round(1)
        st.dataframe(trend[["Player", "Team", "Own %", "Δ own 7d", "xP next"]].head(10),
                     hide_index=True, use_container_width=True)

    paste_block("\n".join(
        f"{i}. **{r['Player']}** ({r['Own %']:.1f}%) — xP {r['xP next']:.1f}, {r['Team']}"
        for i, (_, r) in enumerate(diffs.head(3).iterrows(), 1)
    ))

# ---------- 6. Bench Order ----------
with t[5]:
    target("Bench Order")
    st.caption(
        "For the reader's bench, not ours — the cheap players almost everyone owns. "
        "An autosub only fires for a bench player who **actually played**: a bench1 who "
        "got 0 minutes is skipped, not blocking. So order by expected points among "
        "those likely to play, with the keeper ranked separately (a bench GK only ever "
        "subs for the starting GK)."
    )
    st.caption("Currently weighted toward **nailed-on** starters — say the word to flip it toward upside.")

    fodder = df[(df["Price"] <= 4.5) & (df["Own %"] >= 0.5)].copy()
    fodder["Nailed"] = fodder["Starts"]
    fodder = fodder.sort_values(["Available", "Nailed", "xP next"], ascending=[False, False, False])

    c1, c2 = st.columns([1, 2])
    with c1:
        st.subheader("Bench GK")
        st.dataframe(fodder[fodder["Pos"] == "GKP"].head(5)[
            ["Player", "Team", "Price", "Own %", "xP next", "Flag"]],
            hide_index=True, use_container_width=True)
    with c2:
        st.subheader("Outfield bench")
        st.dataframe(fodder[fodder["Pos"] != "GKP"].head(10)[
            ["Player", "Team", "Pos", "Price", "Own %", "Starts", "xP next", "Flag"]],
            hide_index=True, use_container_width=True)

    if vintage.get("stale_performance"):
        st.caption("⚠️ `Starts` and `Mins` are last season's until GW1, so 'nailed' is a 2025-26 read.")

# ---------- 7. Scout Selection ----------
with t[6]:
    target("Scout Selection")
    st.caption("The template asks for fixture run, price trend and minutes trend.")

    sp = df[df.apply(fpl.set_piece_roles, axis=1) != ""].copy()
    sp["Set pieces"] = sp.apply(fpl.set_piece_roles, axis=1)
    st.subheader("Set-piece takers (first choice)")
    st.dataframe(
        sp.sort_values("Own %", ascending=False)[
            ["Player", "Team", "Pos", "Price", "Own %", "Set pieces", "Next 5 FDR"]].head(20),
        hide_index=True, use_container_width=True,
    )

    st.subheader("Price trend")
    price = fpl.movement(history, "now_cost", days=14)
    if price.empty:
        need_history(len(hist_dates))
    else:
        pt = price.merge(df[["id", "Player", "Team", "Price", "Own %"]], on="id", how="inner")
        pt["Δ price 14d"] = pt["delta"] / 10
        st.dataframe(pt[pt["delta"] != 0].sort_values("delta", ascending=False)
                     .head(12)[["Player", "Team", "Price", "Δ price 14d", "Own %"]],
                     hide_index=True, use_container_width=True)

# ---------- 8. Eye Test ----------
with t[7]:
    target("Eye Test")
    human_only(
        "No data by design, and deliberately so. This is the practitioner's read — "
        "body language, tactical role changes, a manager's substitution pattern, "
        "movement off the ball. Automating the mechanical sections exists to buy "
        "back time for this one."
    )

# ---------- 9. 50:50 Calls ----------
with t[8]:
    target("50:50 Calls")
    names = df.sort_values("Own %", ascending=False)["Player"].tolist()
    c1, c2 = st.columns(2)
    with c1:
        a = st.selectbox("Option A", names, index=0)
    with c2:
        b = st.selectbox("Option B", names, index=min(1, len(names) - 1))
    cols = ["Player", "Team", "Pos", "Price", "Own %", "xP next", "Next 5 FDR",
            "Starts", "Pts", "Flag", "Fixtures"]
    st.dataframe(df[df["Player"].isin([a, b])][cols], hide_index=True, use_container_width=True)

# ---------- 10. Chip Strategy ----------
with t[9]:
    target("Chip Strategy")
    st.caption("Doubles and blanks in the fixture list — the whole basis of chip timing.")
    counts = fpl.fixture_counts(fixtures, teams)
    if counts.empty:
        st.caption("No upcoming fixtures scheduled.")
    else:
        pivot = counts.pivot_table(index="Team", columns="event", values="fixtures",
                                   fill_value=0).astype(int)
        doubles = counts[counts["fixtures"] >= 2]
        if doubles.empty:
            st.caption("No double gameweeks currently scheduled.")
        else:
            st.subheader("Doubles")
            st.dataframe(doubles[["event", "Team", "fixtures"]], hide_index=True)
        st.subheader("Fixtures per gameweek")
        st.dataframe(pivot, use_container_width=True)

# ---------- 11. Closing ----------
with t[10]:
    target("Closing")
    human_only(
        "No data by design. Recap the headline call in a line, a bit of self-aware "
        "humour about how confident you are, and tease next week if there is "
        "something worth teasing."
    )

# ---------- Utility table ----------
with t[11]:
    c1, c2, c3 = st.columns([2, 3, 2])
    with c1:
        pos = st.selectbox("Position", ["All", "GKP", "DEF", "MID", "FWD"])
    with c2:
        q = st.text_input("Search player or team", "")
    with c3:
        avail = st.checkbox("Available only", value=False)

    view = df.copy()
    if pos != "All":
        view = view[view["Pos"] == pos]
    if avail:
        view = view[view["Available"]]
    if q:
        ql = q.lower()
        view = view[view["Player"].str.lower().str.contains(ql)
                    | view["Team"].str.lower().str.contains(ql)]

    st.dataframe(
        view[["Player", "Team", "Pos", "Price", "Own %", "xP next", "Form", "xGI",
              "G+A", "Over/Under", "Next 5 FDR", "Net transfers", "Flag"]]
        .sort_values("xP next", ascending=False),
        hide_index=True, use_container_width=True, height=560,
    )

st.caption(
    "Live data from the official FPL API; trends from the logger's daily snapshots. "
    "Internal tool — verify anything published against fantasy.premierleague.com."
)

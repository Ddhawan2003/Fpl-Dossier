"""Gameweek Dossier -- the weekly content workbench.

One tab per section of the blog template, in publishing order, so the tool
mirrors the document being written.

UI rules, deliberately enforced:
  * Every tab opens on data. At most one line of text above it.
  * Explanation lives in HANDOFF.md, never on screen. If a number needs a
    paragraph to justify it, that paragraph is documentation.
  * State of the world is one status strip, not a stack of banners.
  * FPL vocabulary, never API vocabulary: "xPts" and "Owned", not `ep_next`
    and `selected_by_percent`. The writer is not the one reading the schema.

Read-only: it shows candidates, it does not record picks. Recording is the
calls ledger and needs the writeback decision made first (HANDOFF §7b).
"""

from datetime import datetime, timezone

import pandas as pd
import streamlit as st

import data as fpl

st.set_page_config(page_title="Gameweek Dossier", layout="wide", page_icon="⚽")

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap');

:root {
  --gold:#c99a3e; --ink:#eeece4; --muted:#8b8578;
  --ok:#5cb8b2; --warn:#e0a94e; --bad:#e0764e; --line:rgba(238,236,228,0.10);
}

html, body, [class*="css"] { font-family:'IBM Plex Mono', monospace; }
h1,h2,h3 { font-family:'Space Grotesk', sans-serif !important; letter-spacing:-0.01em; }

/* Reclaim the dead space Streamlit puts above the first element. */
.block-container { padding-top:2.2rem; padding-bottom:3rem; max-width:1500px; }

/* --- masthead ------------------------------------------------------- */
.mast { display:flex; align-items:baseline; gap:14px; margin-bottom:2px; }
.mast h1 { font-size:1.55rem; margin:0; }
.mast .gw { font-size:0.78rem; letter-spacing:0.14em; color:var(--gold);
            border:1px solid var(--gold); padding:2px 8px; border-radius:2px; }

/* --- status strip: the one place state of the world is stated -------- */
.strip { display:flex; flex-wrap:wrap; gap:6px; margin:12px 0 18px; }
.chip { font-size:0.72rem; letter-spacing:0.04em; padding:4px 9px; border-radius:3px;
        border:1px solid var(--line); background:rgba(238,236,228,0.03); color:var(--ink); }
.chip b { font-weight:600; }
.chip i { font-style:normal; color:var(--muted); margin-right:6px;
          text-transform:uppercase; letter-spacing:0.1em; font-size:0.66rem; }
.chip.warn { border-color:rgba(224,169,78,0.45); background:rgba(224,169,78,0.07); }
.chip.warn b { color:var(--warn); }
.chip.ok b { color:var(--ok); }

/* --- section heads --------------------------------------------------- */
.head { display:flex; align-items:baseline; justify-content:space-between;
        border-bottom:1px solid var(--line); padding-bottom:6px; margin:2px 0 14px; }
.head h3 { font-size:1.02rem; margin:0; }
.head .target { font-size:0.68rem; color:var(--muted); letter-spacing:0.08em;
                text-transform:uppercase; }
.note { font-size:0.76rem; color:var(--muted); margin:-6px 0 12px; }
.writer { border-left:2px solid var(--gold); padding:10px 14px; margin:4px 0 8px;
          background:rgba(201,154,62,0.05); font-size:0.86rem; color:var(--ink); }
.sub { font-size:0.72rem; color:var(--muted); letter-spacing:0.1em;
       text-transform:uppercase; margin:16px 0 6px; }

/* --- tabs: tighter, quieter ------------------------------------------ */
.stTabs [data-baseweb="tab-list"] { gap:1px; border-bottom:1px solid var(--line); }
.stTabs [data-baseweb="tab"] { padding:7px 13px; font-size:0.78rem; }
[data-testid="stDataFrame"] { font-size:0.82rem; }
</style>
""", unsafe_allow_html=True)

TARGETS = {
    "Opening": "100–150 words", "Captain": "120–180 · ranked 1–4",
    "Buy / Sell / Hold": "150–200 across three", "Transfer Roadmap": "150–200 words",
    "Differentials": "150–200 · 3 picks", "Bench Order": "50–75 words",
    "Scout Selection": "75–100 words", "Eye Test": "200–300 · your strongest",
    "50:50 Calls": "100–150 words", "Chip Strategy": "100–200 · when relevant",
    "Closing": "60–100 words",
}

# FPL vocabulary, and only the columns a decision actually needs.
FMT = {
    "Price": st.column_config.NumberColumn("£", format="%.1f"),
    "Own %": st.column_config.NumberColumn("Owned", format="%.1f%%"),
    "xP next": st.column_config.NumberColumn("xPts", format="%.1f"),
    "Next 5 FDR": st.column_config.NumberColumn("FDR", format="%.1f"),
    "Δ price": st.column_config.NumberColumn("Δ £", format="%+.1f"),
    "Δ price 14d": st.column_config.NumberColumn("Δ £ 14d", format="%+.1f"),
    "Δ own": st.column_config.NumberColumn("Δ Owned", format="%+.1f"),
    "Fixtures": st.column_config.TextColumn("Next fixtures"),
    "Set pieces": st.column_config.TextColumn("Set pieces"),
    "Flag": st.column_config.TextColumn("Status"),
    "Starts": st.column_config.NumberColumn("Starts", format="%d"),
}


def head(name: str) -> None:
    st.markdown(
        f'<div class="head"><h3>{name}</h3>'
        f'<span class="target">{TARGETS[name]}</span></div>',
        unsafe_allow_html=True,
    )


def note(text: str) -> None:
    st.markdown(f'<div class="note">{text}</div>', unsafe_allow_html=True)


def sub(text: str) -> None:
    st.markdown(f'<div class="sub">{text}</div>', unsafe_allow_html=True)


def writer(text: str) -> None:
    st.markdown(f'<div class="writer">{text}</div>', unsafe_allow_html=True)


def table(frame: pd.DataFrame, cols: list[str], height: int | None = None) -> None:
    # Only pass `height` when it is set. Recent Streamlit validates the argument
    # and rejects None outright, so `height=None` raises rather than meaning
    # "default" -- and Streamlit Cloud runs a newer build than most local envs,
    # which is exactly how this reached production unnoticed.
    kwargs = {
        "hide_index": True,
        "width": "stretch",
        "column_config": {k: v for k, v in FMT.items() if k in cols},
    }
    if height is not None:
        kwargs["height"] = height
    st.dataframe(frame[cols], **kwargs)


def paste(md: str) -> None:
    with st.expander("Copy for the post"):
        st.code(md.strip(), language="markdown")


# ---------- Load ----------
with st.spinner("Fetching live FPL data…"):
    try:
        bootstrap, fixtures = fpl.load_live()
        df = fpl.build_players(bootstrap, fixtures)
        teams = {t["id"]: t for t in bootstrap["teams"]}
        vintage = fpl.detect_vintage(bootstrap)
        ev = fpl.next_event(bootstrap)
        load_error = None
    except Exception as e:  # noqa: BLE001
        df, teams, ev, vintage, load_error = pd.DataFrame(), {}, None, {}, str(e)

if load_error:
    st.error(f"Could not reach the FPL API — {load_error}")
    st.stop()

season = fpl.current_season()
history = fpl.load_history(season)
hist_dates = fpl.history_dates(history)
enough_history = len(hist_dates) >= fpl.MIN_HISTORY_DAYS

# ---------- Masthead + status strip ----------
gw = f"GW{ev['id']}" if ev else "GW —"
top, refresh = st.columns([6, 1])
with top:
    st.markdown(
        f'<div class="mast"><h1>Gameweek Dossier</h1><span class="gw">{gw}</span></div>',
        unsafe_allow_html=True,
    )
with refresh:
    if st.button("Refresh", width="stretch"):
        st.cache_data.clear()
        st.rerun()

chips = []
if ev and ev.get("deadline_time"):
    try:
        dl = datetime.fromisoformat(ev["deadline_time"].replace("Z", "+00:00"))
        left = dl - datetime.now(timezone.utc)
        when = (f"{left.days}d {left.seconds // 3600}h" if left.total_seconds() > 0 else "passed")
        chips.append(("Deadline", f"{dl.strftime('%a %d %b %H:%M')} · {when}", ""))
    except Exception:  # noqa: BLE001
        pass
chips.append(("Snapshots", f"{len(hist_dates)} day{'s' if len(hist_dates) != 1 else ''}",
              "ok" if enough_history else "warn"))
if vintage.get("stale_performance"):
    chips.append(("Season stats", "2025-26 until GW1", "warn"))
    chips.append(("xPts", "provisional pre-season", "warn"))

st.markdown(
    '<div class="strip">' + "".join(
        f'<span class="chip {tone}"><i>{label}</i><b>{value}</b></span>'
        for label, value, tone in chips
    ) + "</div>",
    unsafe_allow_html=True,
)

TABS = ["Opening", "Captain", "Buy/Sell/Hold", "Roadmap", "Differentials", "Bench",
        "Scout", "Eye Test", "50:50", "Chips", "Closing", "All players"]
t = st.tabs(TABS)

# ---------- Opening ----------
with t[0]:
    head("Opening")
    writer("Hook the reader. Self-deprecating line about last week, a joke about "
           "the state of the game, or a tease of what's coming.")

# ---------- Captain ----------
with t[1]:
    head("Captain")
    pos = st.multiselect("Positions", ["GKP", "DEF", "MID", "FWD"],
                         default=["MID", "FWD"], label_visibility="collapsed")
    caps = df[df["Available"] & df["Pos"].isin(pos)] \
        .sort_values(["xP next", "Own %"], ascending=[False, False]).head(10)
    table(caps, ["Player", "Team", "Price", "xP next", "Own %", "Fixtures"])
    paste("\n".join(
        f"{i}. **{r['Player']}** ({r['Team']}) — xPts {r['xP next']:.1f}, {r['Own %']:.1f}% owned"
        for i, (_, r) in enumerate(caps.head(4).iterrows(), 1)))

# ---------- Buy / Sell / Hold ----------
with t[2]:
    head("Buy / Sell / Hold")
    price = fpl.movement(history, "now_cost", days=7)
    owned = fpl.movement(history, "selected_by_percent", days=7)

    if price.empty:
        note(f"Price movement needs {fpl.MIN_HISTORY_DAYS}+ daily snapshots — "
             f"{len(hist_dates)} stored. Fills in nightly.")
    else:
        m = price.merge(df[["id", "Player", "Team", "Price"]], on="id")
        m["Δ price"] = m["delta"] / 10
        m = m[m["delta"] != 0].sort_values("delta", ascending=False)
        a, b = st.columns(2)
        with a:
            sub("Risers")
            table(m.head(8), ["Player", "Team", "Price", "Δ price"])
        with b:
            sub("Fallers")
            table(m.tail(8).sort_values("delta"), ["Player", "Team", "Price", "Δ price"])

    sub("Flagged & widely owned")
    flagged = df[(~df["Available"]) & (df["Own %"] >= 1.0)].sort_values("Own %", ascending=False)
    if flagged.empty:
        note("Nobody above 1% ownership is currently flagged.")
    else:
        table(flagged.head(10), ["Player", "Team", "Own %", "Flag"])

    if not owned.empty:
        sub("Ownership swings · 7 days")
        o = owned.merge(df[["id", "Player", "Team"]], on="id")
        o["Δ own"] = o["delta"].round(1)
        o = o.reindex(o["delta"].abs().sort_values(ascending=False).index)
        table(o.head(8), ["Player", "Team", "Δ own"])

# ---------- Roadmap ----------
with t[3]:
    head("Transfer Roadmap")
    runs = fpl.team_fixtures(fixtures, horizon=5)
    ticker = pd.DataFrame([
        {"Team": teams.get(tid, {}).get("short_name", "?"),
         "Next 5 FDR": round(sum(f[3] for f in fx) / len(fx), 2),
         "Fixtures": " ".join(f"{teams.get(o, {}).get('short_name', '?')}"
                              f"{'(H)' if h else '(A)'}" for _, o, h, _ in fx)}
        for tid, fx in runs.items() if fx
    ]).sort_values("Next 5 FDR")
    table(ticker, ["Team", "Next 5 FDR", "Fixtures"], height=420)
    paste("\n".join(f"- **{r['Team']}** (FDR {r['Next 5 FDR']}): {r['Fixtures']}"
                    for _, r in ticker.head(5).iterrows()))

# ---------- Differentials ----------
with t[4]:
    head("Differentials")
    c1, c2 = st.columns([1, 2])
    with c1:
        cap = st.slider("Ownership ceiling", 1.0, 25.0, 10.0, 0.5,
                        label_visibility="collapsed")
    with c2:
        dpos = st.multiselect("Positions", ["GKP", "DEF", "MID", "FWD"],
                              default=["DEF", "MID", "FWD"], key="dpos",
                              label_visibility="collapsed")
    diffs = df[(df["Own %"] < cap) & df["Available"] & df["Pos"].isin(dpos)] \
        .sort_values(["xP next", "Own %"], ascending=[False, True]).head(12)

    trend = fpl.movement(history, "selected_by_percent", days=7)
    if not trend.empty:
        diffs = diffs.merge(trend[["id", "delta"]], on="id", how="left")
        diffs["Δ own"] = diffs["delta"].round(1)
        table(diffs, ["Player", "Team", "Price", "Own %", "Δ own", "xP next", "Fixtures"])
    else:
        note(f"Ownership direction needs {fpl.MIN_HISTORY_DAYS}+ snapshots — "
             f"{len(hist_dates)} stored. A player at 8% climbing is a different call from 8% flat.")
        table(diffs, ["Player", "Team", "Price", "Own %", "xP next", "Fixtures"])

    paste("\n".join(f"{i}. **{r['Player']}** ({r['Own %']:.1f}%) — {r['Team']}, xPts {r['xP next']:.1f}"
                    for i, (_, r) in enumerate(diffs.head(3).iterrows(), 1)))

# ---------- Bench ----------
with t[5]:
    head("Bench Order")
    note("Cheap, widely-owned bench fodder — ranked toward nailed-on starters.")
    fodder = df[(df["Price"] <= 4.5) & (df["Own %"] >= 0.5)] \
        .sort_values(["Available", "Starts", "xP next"], ascending=[False, False, False])
    a, b = st.columns([1, 2])
    with a:
        sub("Goalkeeper")
        table(fodder[fodder["Pos"] == "GKP"].head(5),
              ["Player", "Team", "Price", "Own %", "Flag"])
    with b:
        sub("Outfield")
        table(fodder[fodder["Pos"] != "GKP"].head(10),
              ["Player", "Team", "Pos", "Price", "Own %", "Starts", "Flag"])

# ---------- Scout ----------
with t[6]:
    head("Scout Selection")
    sp = df[df.apply(fpl.set_piece_roles, axis=1) != ""].copy()
    sp["Set pieces"] = sp.apply(fpl.set_piece_roles, axis=1)
    sub("Set-piece takers")
    table(sp.sort_values("Own %", ascending=False).head(15),
          ["Player", "Team", "Price", "Own %", "Set pieces", "Next 5 FDR"])

    sub("Price trend · 14 days")
    pt = fpl.movement(history, "now_cost", days=14)
    if pt.empty:
        note(f"Needs {fpl.MIN_HISTORY_DAYS}+ daily snapshots — {len(hist_dates)} stored.")
    else:
        pt = pt.merge(df[["id", "Player", "Team", "Price", "Own %"]], on="id")
        pt["Δ price 14d"] = pt["delta"] / 10
        table(pt[pt["delta"] != 0].sort_values("delta", ascending=False).head(10),
              ["Player", "Team", "Price", "Δ price 14d", "Own %"])

# ---------- Eye Test ----------
with t[7]:
    head("Eye Test")
    writer("No data by design. Body language, tactical role changes, substitution "
           "patterns, movement off the ball — the read the numbers can't give you.")

# ---------- 50:50 ----------
with t[8]:
    head("50:50 Calls")
    names = df.sort_values("Own %", ascending=False)["Player"].tolist()
    a, b = st.columns(2)
    with a:
        pa = st.selectbox("A", names, index=0, label_visibility="collapsed")
    with b:
        pb = st.selectbox("B", names, index=min(1, len(names) - 1), label_visibility="collapsed")
    table(df[df["Player"].isin([pa, pb])],
          ["Player", "Team", "Pos", "Price", "Own %", "xP next", "Next 5 FDR", "Flag", "Fixtures"])

# ---------- Chips ----------
with t[9]:
    head("Chip Strategy")
    counts = fpl.fixture_counts(fixtures, teams)
    if counts.empty:
        note("No upcoming fixtures scheduled.")
    else:
        doubles = counts[counts["fixtures"] >= 2]
        if doubles.empty:
            note("No double gameweeks currently scheduled.")
        else:
            sub("Doubles")
            st.dataframe(doubles[["event", "Team", "fixtures"]], hide_index=True, width="stretch")
        sub("Fixtures per gameweek")
        st.dataframe(
            counts.pivot_table(index="Team", columns="event", values="fixtures",
                               fill_value=0).astype(int),
            width="stretch", height=400,
        )

# ---------- Closing ----------
with t[10]:
    head("Closing")
    writer("Recap the headline call in a line, a bit of self-aware humour about "
           "how confident you are, and tease next week if there's something worth teasing.")

# ---------- All players ----------
with t[11]:
    c1, c2, c3 = st.columns([2, 3, 2])
    with c1:
        p = st.selectbox("Position", ["All", "GKP", "DEF", "MID", "FWD"])
    with c2:
        q = st.text_input("Search", "", placeholder="Player or team")
    with c3:
        only = st.checkbox("Available only")
    v = df.copy()
    if p != "All":
        v = v[v["Pos"] == p]
    if only:
        v = v[v["Available"]]
    if q:
        ql = q.lower()
        v = v[v["Player"].str.lower().str.contains(ql) | v["Team"].str.lower().str.contains(ql)]
    table(v.sort_values("xP next", ascending=False),
          ["Player", "Team", "Pos", "Price", "Own %", "xP next", "Next 5 FDR", "Flag"], height=560)

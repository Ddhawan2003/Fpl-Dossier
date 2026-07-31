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

/* --- ticker legend: the one key the grid needs ------------------------ */
.legend { display:flex; align-items:center; gap:3px; margin:8px 0 2px; }
.legend span { width:26px; height:16px; border-radius:2px; font-size:0.62rem;
               color:var(--ink); display:flex; align-items:center; justify-content:center; }
.legend em { font-style:normal; color:var(--muted); font-size:0.68rem;
             letter-spacing:0.08em; margin-left:8px; text-transform:uppercase; }

/* --- quality floor ---------------------------------------------------- */
:focus-visible { outline:2px solid var(--gold); outline-offset:2px; }
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { animation-duration:0.01ms !important;
    animation-iteration-count:1 !important; transition-duration:0.01ms !important; }
}
@media (max-width: 640px) {
  .block-container { padding-top:1.4rem; }
  .mast h1 { font-size:1.25rem; }
  .head { flex-direction:column; align-items:flex-start; gap:2px; }
}
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
    "Goals": st.column_config.NumberColumn("G", format="%d"),
    "xG": st.column_config.NumberColumn("xG", format="%.1f"),
    "Assists": st.column_config.NumberColumn("A", format="%d"),
    "xA": st.column_config.NumberColumn("xA", format="%.1f"),
    "Form": st.column_config.NumberColumn("Form", format="%.1f"),
    "Next": st.column_config.TextColumn("Next"),
    "DefCon": st.column_config.NumberColumn("DefCon", format="%d"),
    "DefCon/90": st.column_config.NumberColumn("DefCon/90", format="%.1f"),
    "Bar": st.column_config.NumberColumn("Bar", format="%d"),
    "vs bar": st.column_config.NumberColumn("vs bar", format="%+.1f"),
    "xGI/90": st.column_config.NumberColumn("xGI/90", format="%.2f"),
    "xG/90": st.column_config.NumberColumn("xG/90", format="%.2f"),
    "xGC/90": st.column_config.NumberColumn("xGC/90", format="%.2f"),
    "Over/Under": st.column_config.NumberColumn("vs xGI", format="%+.1f"),
    "Mins": st.column_config.NumberColumn("Mins", format="%d"),
    "G+A": st.column_config.NumberColumn("G+A", format="%d"),
}


# FPL's own fixture-difficulty ramp, darkened to sit on a near-black ground.
# The hues are kept (green easy → red hard) because an FPL manager already reads
# that scale without a legend; only the luminance is adapted to the dark theme.
FDR_RAMP = {1: "#0e6b3d", 2: "#1a8a52", 3: "#3a4150", 4: "#9c3b3b", 5: "#6b1f2e"}


def fdr_styles(values: pd.DataFrame) -> pd.DataFrame:
    """CSS per cell for the ticker, keyed off real difficulty integers.

    Built with an explicit loop rather than DataFrame.map, which only exists in
    pandas >= 2.1 while requirements allow >= 2.0.
    """
    out = pd.DataFrame("", index=values.index, columns=values.columns)
    for row in values.index:
        for col in values.columns:
            v = values.loc[row, col]
            if pd.isna(v):
                out.loc[row, col] = "color:#5a5750;"  # blank gameweek
            else:
                out.loc[row, col] = (
                    f"background-color:{FDR_RAMP[min(5, max(1, int(round(v))))]};"
                    "color:#eeece4;"
                )
    return out


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
    # Select only columns that actually exist. frame[cols] raises KeyError on the
    # first missing label, which takes down the entire page for one absent
    # column -- and pandas/Streamlit versions differ between here and Streamlit
    # Cloud, so a column present locally is not guaranteed present there. A
    # missing column should degrade one table, not the app.
    present = [c for c in cols if c in frame.columns]
    missing = [c for c in cols if c not in frame.columns]

    kwargs = {
        "hide_index": True,
        "width": "stretch",
        "column_config": {k: v for k, v in FMT.items() if k in present},
    }
    if height is not None:
        kwargs["height"] = height
    st.dataframe(frame[present], **kwargs)
    if missing:
        note("Unavailable in this view: " + ", ".join(missing))


def sorted_by(frame: pd.DataFrame, col: str, ascending: bool = True) -> pd.DataFrame:
    """sort_values that tolerates the column being absent.

    Pairs with table()'s missing-column handling: between them, a column that
    exists locally but not on Streamlit Cloud degrades the table it belongs to
    rather than crashing the whole page.
    """
    if col not in frame.columns:
        return frame
    return frame.sort_values(col, ascending=ascending)


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
        # Keep the type: some exceptions stringify to "", and `if load_error:`
        # would then be falsy, skipping the guard below and letting the page
        # continue with an empty frame -- which resurfaces far away as an opaque
        # KeyError instead of the real cause.
        df, teams, ev, vintage = pd.DataFrame(), {}, None, {}
        load_error = f"{type(e).__name__}: {e}"

if load_error is not None:
    st.error(f"Could not load FPL data — {load_error}")
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
    # Actual beside expected, so the over/underperformance is readable straight
    # off the row rather than needing a separate table. xGI/90 stays because raw
    # totals favour whoever played more minutes.
    table(caps, ["Player", "Team", "Price", "Own %", "Form", "xP next",
                 "Goals", "xG", "Assists", "xA", "xGI/90", "DefCon/90",
                 "Next 5 FDR", "Next"])
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
        with st.expander("Ownership swings · 7 days"):
            o = owned.merge(df[["id", "Player", "Team"]], on="id")
            o["Δ own"] = o["delta"].round(1)
            o = o.reindex(o["delta"].abs().sort_values(ascending=False).index)
            table(o.head(8), ["Player", "Team", "Δ own"])

    with st.expander("Overperforming their underlying numbers"):
        note("A question, not a verdict — elite finishers beat xG every season. "
             f"Minimum {fpl.MIN_MINUTES_FOR_UNDERLYING} minutes played.")
        hot = fpl.underlying_sample(df)
        hot = sorted_by(hot[hot["Own %"] >= 1.0], "Over/Under", ascending=False).head(8)
        table(hot, ["Player", "Team", "Mins", "G+A", "xGI", "Over/Under", "Next 5 FDR"])

# ---------- Roadmap ----------
with t[3]:
    head("Transfer Roadmap")
    labels, values = fpl.fixture_ticker(fixtures, teams, horizon=6)
    if labels.empty:
        note("No upcoming fixtures scheduled.")
    else:
        st.dataframe(
            labels.style.apply(lambda _: fdr_styles(values), axis=None),
            width="stretch", height=min(760, 38 * len(labels) + 40),
        )
        st.markdown(
            '<div class="legend">'
            + "".join(f'<span style="background:{FDR_RAMP[i]}">{i}</span>' for i in range(1, 6))
            + "<em>easier → harder</em></div>",
            unsafe_allow_html=True,
        )
        best = values.mean(axis=1).sort_values().head(5)
        paste("\n".join(
            f"- **{team}** (FDR {score:.1f}): "
            + ", ".join(labels.loc[team].tolist())
            for team, score in best.items()
        ))

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
    trend_cols = []
    if not trend.empty:
        # map() rather than merge(): no suffix collisions, and no risk of
        # duplicating rows if the history ever contains an id twice. The column
        # is only requested when it was actually created, so the table never
        # asks for something that may not be there.
        diffs["Δ own"] = diffs["id"].map(
            trend.drop_duplicates("id").set_index("id")["delta"]
        ).round(1)
        trend_cols = ["Δ own"]
    else:
        note(f"Ownership direction needs {fpl.MIN_HISTORY_DAYS}+ snapshots — "
             f"{len(hist_dates)} stored. A player at 8% climbing is a different call from 8% flat.")

    table(diffs, ["Player", "Team", "Price", "Own %"] + trend_cols +
                 ["Form", "xP next", "Goals", "xG", "Assists", "xA",
                  "xGI/90", "DefCon/90", "Next 5 FDR", "Next"])

    with st.expander("Underperforming their underlying numbers · the 'he's due' case"):
        note("Creating chances, returns not arriving yet. Low ownership plus fixtures "
             "turning is the strongest differential argument there is.")
        due = fpl.underlying_sample(df)
        due = sorted_by(
            due[(due["Own %"] < cap) & due["Available"] & due["Pos"].isin(dpos)],
            "Over/Under").head(8)
        table(due, ["Player", "Team", "Price", "Own %", "G+A", "xGI", "Over/Under", "Next 5 FDR"])

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
        # A cheap defender or holding midfielder who clears the DefCon bar is
        # worth far more than his price suggests -- this is the bench-fodder meta.
        # DefCon sits immediately after price/ownership: Bench Order is about
        # finding cheap players who actually score, and that is the column
        # answering it. It should not be behind a horizontal scroll.
        table(fodder[fodder["Pos"] != "GKP"].head(10),
              ["Player", "Team", "Pos", "Price", "Own %",
               "DefCon/90", "vs bar", "Starts", "Flag"])

# ---------- Scout ----------
with t[6]:
    head("Scout Selection")
    sp = df[df.apply(fpl.set_piece_roles, axis=1) != ""].copy()
    sp["Set pieces"] = sp.apply(fpl.set_piece_roles, axis=1)
    sub("Set-piece takers")
    table(sp.sort_values("Own %", ascending=False).head(15),
          ["Player", "Team", "Price", "Own %", "Set pieces", "Next 5 FDR"])

    with st.expander("Chance creation · xGI per 90"):
        creators = fpl.underlying_sample(df)
        creators = sorted_by(creators[creators["Available"]], "xGI/90", ascending=False).head(12)
        table(creators, ["Player", "Team", "Pos", "Price", "Own %", "xGI/90", "Next 5 FDR"])

    with st.expander("Defensive value · expected goals conceded per 90"):
        backs = fpl.underlying_sample(df)
        backs = sorted_by(
            backs[backs["Available"] & backs["Pos"].isin(["GKP", "DEF"])],
            "xGC/90").head(10)
        table(backs, ["Player", "Team", "Pos", "Price", "Own %", "CS", "xGC/90", "Next 5 FDR"])

    # Open by default: for cheap defenders and holding midfielders this is the
    # primary reason to own them, not secondary analysis.
    with st.expander("DefCon · who clears the 2-point bar", expanded=True):
        note("Defenders need 10+ CBIT a match, midfielders and forwards 12+ CBIRT. "
             "Averaging above the bar is the best proxy without per-match data. "
             "Keepers are not eligible.")
        dc = fpl.underlying_sample(df)
        dc = sorted_by(
            dc[dc["Available"] & dc["Pos"].isin(["DEF", "MID", "FWD"])],
            "vs bar", ascending=False).head(15)
        table(dc, ["Player", "Team", "Pos", "Price", "Own %",
                   "DefCon", "DefCon/90", "Bar", "vs bar", "Next 5 FDR"])

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
          ["Player", "Team", "Pos", "Price", "Own %", "Form", "xP next", "Goals", "xG",
           "Assists", "xA", "xGI/90", "DefCon/90", "vs bar", "Next 5 FDR", "Flag"], height=560)

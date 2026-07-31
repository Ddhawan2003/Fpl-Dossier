"""Data layer for the Gameweek Dossier workbench.

One place that owns *where numbers come from*, so the section code in app.py
only ever deals with a DataFrame.

Two sources, split by question type:

  * The **live FPL API** answers "what is true right now?" -- prices, ownership,
    availability, fixtures. Fresher than anything stored, and needs no history.
  * The **logger's CSVs** (`data/snapshots/`, `data/deadlines/`) answer "what
    was true, and how has it moved?" Every trend in the workbench comes from
    here, because the API has no memory at all.

The dependency runs one way only: this module reads the logger's *output
files*. Nothing in `logger/` may ever import from here. Breaking the dashboard
must never be able to stop tonight's capture.
"""

from __future__ import annotations

import glob
import os
from datetime import datetime, timezone

import pandas as pd
import requests
import streamlit as st

BOOTSTRAP_URL = "https://fantasy.premierleague.com/api/bootstrap-static/"
FIXTURES_URL = "https://fantasy.premierleague.com/api/fixtures/"

# The FPL API 403s / times out for datacenter IPs unless a browser-like
# User-Agent is sent. Streamlit Community Cloud runs on datacenter IPs; a laptop
# does not -- which is exactly why this app once worked locally and failed when
# deployed. logger/log_snapshot.py has carried these headers from day one.
FPL_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

POS_NAMES = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Trends need at least two captures to subtract. With one file everything is a
# flat line, which would be worse than saying "not enough history yet".
MIN_HISTORY_DAYS = 2

# Over/underperformance against expected goals is noise in small samples: three
# games of xG says nothing, and shown unguarded it flags every hot starter as
# "due a regression". Roughly five full matches is the floor where the number
# starts to mean something.
MIN_MINUTES_FOR_UNDERLYING = 450

# Defensive contribution ("DefCon") thresholds, worth 2 points per match:
# defenders need 10+ CBIT, midfielders and forwards 12+ CBIRT (CBIT plus ball
# recoveries). Keepers are not eligible.
#
# These numbers are NOT exposed by the API -- `element_stats` names the stat but
# publishes no threshold -- so they are transcribed from the Premier League's
# published rules. If FPL ever changes them, nothing here will notice, and this
# constant is the thing to correct.
DEFCON_BAR = {"DEF": 10, "MID": 12, "FWD": 12}

# Columns build_players() derives rather than reads. Downstream code sorts and
# filters on these, so a missing one surfaces as an opaque KeyError hundreds of
# lines away. Streamlit Cloud runs a newer pandas than most local environments
# and has twice produced a column here that exists locally but not there, so
# build_players() guarantees all of them exist and are float64 before returning.
DERIVED_NUMERIC = [
    "Over/Under", "xGI/90", "xG/90", "xA/90", "xGC/90", "DefCon/90", "Bar", "vs bar",
]


# --------------------------------------------------------------------------
# Live API
# --------------------------------------------------------------------------
def _get_json(url: str) -> dict | list:
    """Fetch JSON, failing loudly on a non-200.

    Without raise_for_status() a 403 returns an HTML error page, .json() raises
    a confusing JSONDecodeError, and the real cause is hidden.
    """
    resp = requests.get(url, headers=FPL_HEADERS, timeout=20)
    resp.raise_for_status()
    return resp.json()


@st.cache_data(ttl=900, show_spinner=False)
def load_live():
    """bootstrap-static + fixtures. Cached 15 min; Refresh clears it."""
    return _get_json(BOOTSTRAP_URL), _get_json(FIXTURES_URL)


# --------------------------------------------------------------------------
# Season / vintage
# --------------------------------------------------------------------------
def season_for(date: datetime) -> str:
    """FPL season label, e.g. 2026-07-28 -> '2026-27'. Mirrors the logger."""
    start = date.year if date.month >= 7 else date.year - 1
    return f"{start}-{str(start + 1)[-2:]}"


def current_season() -> str:
    return season_for(datetime.now(timezone.utc))


def next_event(bootstrap: dict) -> dict | None:
    events = bootstrap.get("events", [])
    return next((e for e in events if e.get("is_next")), None) or \
        next((e for e in events if e.get("is_current")), None)


def detect_vintage(bootstrap: dict) -> dict:
    """Work out whether performance fields describe THIS season or the last one.

    Off-season the API serves a mix that will mislead anything computing over
    it: `form` and `event_points` are zeroed for everyone, while `total_points`,
    `minutes`, `goals_scored`, `assists` and `expected_goal_involvements` still
    hold last season's totals. Rendering those as current is worse than showing
    nothing, because it looks entirely plausible.
    """
    els = bootstrap.get("elements", [])
    if not els:
        return {"stale_performance": False, "note": ""}

    form_all_zero = all(float(p.get("form") or 0) == 0 for p in els)
    points_all_zero = all((p.get("event_points") or 0) == 0 for p in els)
    has_totals = any((p.get("total_points") or 0) > 0 for p in els)

    stale = form_all_zero and points_all_zero and has_totals
    ev = next_event(bootstrap)
    label = f"GW{ev['id']}" if ev else "the new season"

    return {
        "stale_performance": stale,
        "note": (
            f"Pre-season: `form` and `event_points` are zero for every player, but "
            f"`total_points`, `minutes`, `goals`, `assists` and `xGI` still hold "
            f"**last season's** figures. Anything below computed from those describes "
            f"2025-26, not {label}. Price, ownership, availability and `ep_next` are live."
        ) if stale else "",
    }


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------
def team_fixtures(fixtures: list, horizon: int = 5) -> dict:
    """Per-team upcoming fixtures: [(event, opponent_id, is_home, difficulty)]."""
    upcoming = sorted(
        (f for f in fixtures if not f.get("finished") and f.get("event") is not None),
        key=lambda f: (f["event"], f.get("kickoff_time") or ""),
    )
    out: dict[int, list] = {}
    for f in upcoming:
        out.setdefault(f["team_h"], []).append(
            (f["event"], f["team_a"], True, f["team_h_difficulty"])
        )
        out.setdefault(f["team_a"], []).append(
            (f["event"], f["team_h"], False, f["team_a_difficulty"])
        )
    return {tid: v[:horizon] for tid, v in out.items()}


def fixture_ticker(fixtures: list, teams: dict, horizon: int = 6):
    """Teams x next N gameweeks, as (opponent labels, difficulty values).

    The fixture ticker is the most native artifact in FPL's world -- a manager
    reads a coloured grid of upcoming opponents at a glance, without a legend.
    Returning the difficulty alongside the label lets the caller colour it on
    FPL's own 1-5 ramp, so the colour encodes a real API integer rather than
    decorating the table.

    Doubles collapse into one cell ("ARS(H) CHE(A)") carrying the mean
    difficulty; blanks stay empty, which is the information.
    """
    upcoming = sorted(
        (f for f in fixtures if not f.get("finished") and f.get("event") is not None),
        key=lambda f: f["event"],
    )
    if not upcoming:
        return pd.DataFrame(), pd.DataFrame()

    events = sorted({f["event"] for f in upcoming})[:horizon]
    cells: dict[int, dict[int, list]] = {}
    for f in upcoming:
        if f["event"] not in events:
            continue
        cells.setdefault(f["team_h"], {}).setdefault(f["event"], []).append(
            (teams.get(f["team_a"], {}).get("short_name", "?"), "H", f["team_h_difficulty"])
        )
        cells.setdefault(f["team_a"], {}).setdefault(f["event"], []).append(
            (teams.get(f["team_h"], {}).get("short_name", "?"), "A", f["team_a_difficulty"])
        )

    cols = [f"GW{e}" for e in events]
    labels, values = {}, {}
    for tid, by_event in cells.items():
        name = teams.get(tid, {}).get("short_name", "?")
        labels[name] = []
        values[name] = []
        for e in events:
            fx = by_event.get(e, [])
            if not fx:
                labels[name].append("—")
                values[name].append(float("nan"))
            else:
                labels[name].append(" ".join(f"{o}({h})" for o, h, _ in fx))
                values[name].append(sum(d for _, _, d in fx) / len(fx))

    label_df = pd.DataFrame.from_dict(labels, orient="index", columns=cols)
    value_df = pd.DataFrame.from_dict(values, orient="index", columns=cols)
    order = value_df.mean(axis=1).sort_values().index
    return label_df.loc[order], value_df.loc[order]


def fixture_counts(fixtures: list, teams: dict) -> pd.DataFrame:
    """Fixtures per team per gameweek -- 2 is a double, 0 a blank."""
    rows = []
    for f in fixtures:
        if f.get("event") is None or f.get("finished"):
            continue
        rows.append({"event": f["event"], "team": f["team_h"]})
        rows.append({"event": f["event"], "team": f["team_a"]})
    if not rows:
        return pd.DataFrame(columns=["event", "team", "fixtures"])
    df = pd.DataFrame(rows).groupby(["event", "team"]).size().reset_index(name="fixtures")
    df["Team"] = df["team"].map(lambda t: teams.get(t, {}).get("short_name", "?"))
    return df


# --------------------------------------------------------------------------
# The master player frame
# --------------------------------------------------------------------------
def build_players(bootstrap: dict, fixtures: list, horizon: int = 5) -> pd.DataFrame:
    teams = {t["id"]: t for t in bootstrap["teams"]}
    upcoming = team_fixtures(fixtures, horizon)

    rows = []
    for p in bootstrap["elements"]:
        tid = p["team"]
        runs = upcoming.get(tid, [])
        fdr = sum(r[3] for r in runs) / len(runs) if runs else None
        opponents = " ".join(
            f"{teams.get(o, {}).get('short_name', '?')}{'(H)' if h else '(A)'}"
            for _, o, h, _ in runs
        )
        rows.append({
            "id": p["id"],
            "Player": p["web_name"],
            "Team": teams.get(tid, {}).get("short_name", "?"),
            "team_id": tid,
            "Pos": POS_NAMES.get(p["element_type"], "?"),
            "pos_id": p["element_type"],
            "Price": p["now_cost"] / 10,
            "now_cost": p["now_cost"],
            "Own %": float(p.get("selected_by_percent") or 0),
            "xP next": float(p.get("ep_next") or 0),
            "Form": float(p.get("form") or 0),
            "Mins": p.get("minutes") or 0,
            "Starts": p.get("starts") or 0,
            "Pts": p.get("total_points") or 0,
            "xG": float(p.get("expected_goals") or 0),
            "xA": float(p.get("expected_assists") or 0),
            "xGI": float(p.get("expected_goal_involvements") or 0),
            "xGC": float(p.get("expected_goals_conceded") or 0),
            "Goals": p.get("goals_scored") or 0,
            "Assists": p.get("assists") or 0,
            "CS": p.get("clean_sheets") or 0,
            "DefCon": p.get("defensive_contribution") or 0,
            "G+A": (p.get("goals_scored") or 0) + (p.get("assists") or 0),
            "Next 5 FDR": round(fdr, 2) if fdr is not None else None,
            "Fixtures": opponents,
            # Just the next opponent. The five-deep string is too wide for the
            # decision tables; the full run lives on the Roadmap ticker.
            "Next": (
                f"{teams.get(runs[0][1], {}).get('short_name', '?')}"
                f"{'(H)' if runs[0][2] else '(A)'}" if runs else ""
            ),
            "Net transfers": (p.get("transfers_in_event") or 0) - (p.get("transfers_out_event") or 0),
            "status": p.get("status"),
            "news": p.get("news") or "",
            "chance": p.get("chance_of_playing_next_round"),
            "pen": p.get("penalties_order"),
            "fk": p.get("direct_freekicks_order"),
            "cor": p.get("corners_and_indirect_freekicks_order"),
        })

    df = pd.DataFrame(rows)

    # Derived at READ time, never stored. The logger keeps only raw API fields,
    # so every number below is reproducible from the record by subtraction or
    # division -- which is the point of the rule, not a limitation of it.
    df["Over/Under"] = (df["G+A"] - df["xGI"]).round(2)

    # .where() leaves float NaN for zero-minute players, keeping these columns
    # numeric. pd.NA would flip them to object dtype, and NAType has no
    # __round__, so the whole frame would blow up on the next line.
    per90 = (df["Mins"] / 90).where(df["Mins"] > 0)
    for out, src in [("xGI/90", "xGI"), ("xG/90", "xG"),
                     ("xA/90", "xA"), ("xGC/90", "xGC"), ("DefCon/90", "DefCon")]:
        df[out] = (df[src] / per90).round(2)

    # DefCon points are all-or-nothing per match, so what matters is whether a
    # player clears his position's bar often. Averaging above it is the best
    # proxy available without per-match data. Keepers are not eligible.
    # to_numeric rather than trusting .map()'s inferred dtype: `Pos` is a string
    # column, GKP has no bar, and how a given pandas represents that miss (NaN
    # vs pd.NA, float vs nullable) varies by version. Forcing float64 makes the
    # subtraction and round below behave the same everywhere.
    df["Bar"] = pd.to_numeric(df["Pos"].map(DEFCON_BAR), errors="coerce")
    df["vs bar"] = (df["DefCon/90"] - df["Bar"]).round(2)

    # Below the minutes floor these are noise, so blank them rather than print a
    # number nobody should act on. It bites hardest on DefCon/90: a player with
    # one minute and one clearance reads as 90.0 per 90.
    thin = df["Mins"] < MIN_MINUTES_FOR_UNDERLYING
    df.loc[thin, ["Over/Under", "xGI/90", "xG/90", "xA/90", "xGC/90",
                  "DefCon/90", "vs bar"]] = float("nan")

    # Output contract. Whatever a pandas version does with dtypes above, every
    # derived column exists and is numeric by the time this returns -- so a
    # version difference degrades a cell to blank instead of taking down the
    # page with a KeyError from a tab three hundred lines away.
    for col in DERIVED_NUMERIC:
        if col not in df.columns:
            df[col] = float("nan")
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df["Available"] = df["status"] == "a"
    df["Flag"] = df.apply(_flag_label, axis=1)
    return df


def underlying_sample(df: pd.DataFrame) -> pd.DataFrame:
    """Players with enough minutes for expected-goals numbers to mean anything."""
    return df[df["Mins"] >= MIN_MINUTES_FOR_UNDERLYING]


def _flag_label(r) -> str:
    if r["status"] == "a":
        return ""
    chance = "" if r["chance"] is None else f" ({int(r['chance'])}%)"
    return {
        "d": "Doubt", "i": "Injured", "s": "Suspended", "u": "Unavailable",
    }.get(r["status"], str(r["status"]).upper()) + chance


def set_piece_roles(r) -> str:
    roles = []
    if r["pen"] == 1:
        roles.append("PEN")
    if r["fk"] == 1:
        roles.append("FK")
    if r["cor"] == 1:
        roles.append("COR")
    return " · ".join(roles)


# --------------------------------------------------------------------------
# History (the logger's output)
# --------------------------------------------------------------------------
@st.cache_data(ttl=1800, show_spinner=False)
def load_history(season: str) -> pd.DataFrame:
    """Every daily snapshot for a season, concatenated.

    Returns an empty frame when the logger has not produced anything yet, so
    callers can gate cleanly rather than crash.
    """
    paths = sorted(glob.glob(os.path.join(REPO_ROOT, "data", "snapshots", season, "*.csv")))
    if not paths:
        return pd.DataFrame()

    frames = []
    for path in paths:
        try:
            frames.append(pd.read_csv(path))
        except Exception:  # noqa: BLE001 -- one bad file must not blank the page
            continue
    if not frames:
        return pd.DataFrame()

    # Older files legitimately have fewer columns (the schema was widened twice);
    # concat fills the gaps with NaN, which is what we want.
    df = pd.concat(frames, ignore_index=True)
    for col in ("now_cost", "selected_by_percent", "transfers_in", "transfers_out",
                "minutes", "total_points", "ep_next"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def history_dates(history: pd.DataFrame) -> list[str]:
    if history.empty or "snapshot_date" not in history.columns:
        return []
    return sorted(history["snapshot_date"].dropna().unique().tolist())


def movement(history: pd.DataFrame, column: str, days: int = 7) -> pd.DataFrame:
    """Change in `column` per player over the last `days` of captures.

    Returns columns: id, <column>_then, <column>_now, delta. Empty frame when
    there is not enough history, so sections can say so honestly instead of
    rendering a page of zeros.
    """
    dates = history_dates(history)
    if len(dates) < MIN_HISTORY_DAYS or column not in history.columns:
        return pd.DataFrame()

    latest = dates[-1]
    earlier = dates[max(0, len(dates) - 1 - days)]
    if earlier == latest:
        return pd.DataFrame()

    # drop_duplicates guards against an id appearing twice for one date, which
    # would otherwise multiply rows through every downstream join.
    now = history[history["snapshot_date"] == latest][["id", column]] \
        .drop_duplicates("id").rename(columns={column: "now"})
    then = history[history["snapshot_date"] == earlier][["id", column]] \
        .drop_duplicates("id").rename(columns={column: "then"})
    merged = now.merge(then, on="id", how="inner")
    merged["delta"] = merged["now"] - merged["then"]
    merged["from_date"] = earlier
    merged["to_date"] = latest
    return merged

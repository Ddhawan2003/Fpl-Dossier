#!/usr/bin/env python3
"""Daily + deadline-anchored FPL market logger.

Takes a snapshot of the FPL "market" for every player -- price, ownership,
transfer flows, and point-in-time availability -- and writes it as a dated CSV.
This history CANNOT be backfilled: the live FPL API only ever shows "now", so
every capture we miss is signal lost forever. Because of that, this script is
built to fail LOUDLY (non-zero exit) rather than write a partial, stale, or
mislabelled snapshot that would masquerade as a good capture.

Two modes, two separate records:

  daily     (default)      -> data/snapshots/<season>/<YYYY-MM-DD>.csv
                             One capture per UTC day. A missing day is a
                             visibly missing file (free gap detection).

  --deadline               -> data/deadlines/<season>/gw<NN>.csv
                             One capture per gameweek, taken in the last
                             couple of hours before that gameweek's deadline.
                             This is the one that matters for accountability:
                             transfers_in_event / transfers_out_event RESET at
                             every deadline, so the final pre-deadline transfer
                             and ownership figures exist for a few hours and
                             then are gone forever. It writes to a separate
                             directory precisely so the 22:30 UTC daily run
                             cannot overwrite it later the same day.

Design rules this file honours:
  * Self-contained: depends only on `requests` + the stdlib. It imports nothing
    from the dashboard, so nothing done to the dashboard can take it down.
  * Exact arithmetic only: every value written is a raw API field, stored
    verbatim. No interpretation, no rounding, no derived metrics. Anything our
    accountability record depends on ("he was 4% owned when we called him") is
    read straight back out of these files, reproducibly. Note that
    `minutes_to_deadline` is deliberately NOT a column -- it is derivable at
    read time from snapshot_utc and deadline_time, both of which are recorded.
"""

from __future__ import annotations

import argparse
import csv
import io
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

BOOTSTRAP_URL = "https://fantasy.premierleague.com/api/bootstrap-static/"

# The FPL API is known to 403 / time out for requests from datacenter IPs
# (GitHub Actions runners are datacenter IPs). A browser-like User-Agent
# reliably gets us past that; the retry loop covers the rest.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

MAX_ATTEMPTS = 5
BACKOFF_BASE_SECONDS = 5

# A healthy bootstrap has ~600+ players. If we get far fewer, something is
# wrong (partial response, off-season reset mid-fetch) -- treat it as a failure
# rather than writing a garbage day that looks legitimate.
MIN_EXPECTED_PLAYERS = 300

# How early before a deadline a --deadline capture is still considered valid.
# The workflow gate fires inside a 120-minute window; this is deliberately
# looser so a GitHub-delayed-but-still-pre-deadline run is not rejected on a
# technicality. A capture AFTER the deadline is always refused -- by then the
# transfer counters have already reset and the numbers are the next gameweek's.
DEADLINE_MAX_LEAD_MINUTES = 180

POS_NAMES = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}

# The columns we log, in order. Every one is a raw API field except the three
# capture-metadata columns at the top (snapshot_utc / snapshot_date /
# snapshot_kind -- what we fetched and when). now_cost and the cost_change_*
# fields are integer tenths (75 == GBP 7.5); we store them raw so the record is
# exact and the dashboard does any division at read time.
#
# The API exposes ~105 fields per player. We take these deliberately: everything
# here is either point-in-time (overwritten the moment it changes, so it cannot
# be recovered later) or cheap context that makes the record self-contained. The
# rest are derivable after the fact and are skipped on purpose.
COLUMNS = [
    # --- capture metadata ---------------------------------------------------
    "snapshot_utc",          # exact ISO-8601 fetch time (UTC)
    "snapshot_date",         # UTC date of the fetch
    "snapshot_kind",         # "daily" or "deadline" -- survives concatenation
    "event",                 # gameweek id this capture is anchored to
    "deadline_time",         # that gameweek's deadline (raw API field, UTC)
    # --- identity -----------------------------------------------------------
    "id",                    # element id -- stable within a season
    "web_name",
    "team",                  # team short name (e.g. ARS)
    "position",              # GKP/DEF/MID/FWD
    # --- market -------------------------------------------------------------
    "now_cost",              # price in tenths of a million
    "cost_change_event",     # price change during the current event
    "cost_change_start",     # price change since season start
    "selected_by_percent",   # ownership %
    "transfers_in_event",    # transfers in during the current event (RESETS at deadline)
    "transfers_out_event",   # transfers out during the current event (RESETS at deadline)
    "transfers_in",          # total transfers in this season
    "transfers_out",         # total transfers out this season
    # --- point-in-time availability, equally unbackfillable -----------------
    "status",                # a=available, d=doubtful, i=injured, s=suspended, u=unavailable
    "news",                  # the injury/availability note shown at capture time
    "news_added",            # when that note appeared -- sharper than inferring from dailies
    "chance_of_playing_this_round",  # 0-100, or EMPTY when the API says null
    "chance_of_playing_next_round",  # 0-100, or EMPTY when the API says null
    # --- set-piece duties: these CHANGE mid-season and nothing records when --
    "penalties_order",
    "direct_freekicks_order",
    "corners_and_indirect_freekicks_order",
    # --- performance to date ------------------------------------------------
    "form",                  # rolling 30-day figure the API recomputes continuously
    "event_points",          # points in the current event
    "total_points",          # season total to date
    "minutes",               # minutes played to date
    "starts",                # times named in the XI -- the "nailed on" signal
    # --- actual returns -----------------------------------------------------
    # Unlike the fields above these are cumulative counters, so they ARE
    # recoverable later from element-summary/<id>/history. They are logged for
    # three smaller reasons: the record stays self-contained (no 600-request
    # reconstruction), Opta revisions to past matches are captured as they
    # happen, and they cost nothing on an API call we already make.
    "goals_scored",
    "assists",
    "clean_sheets",
    "goals_conceded",
    "saves",
    # --- defensive contribution ("DefCon", scoring rule since 2025-26) -------
    # FPL already applies the position formula to `defensive_contribution`:
    # CBIT for defenders, CBIRT (adds ball recoveries) for mid/fwd. Verified
    # against live data -- Senesi (DEF) 419 = 357 CBI + 62 tackles, excluding
    # his 155 recoveries; Anderson (MID) 515 = 209 CBIT + 306 recoveries. The
    # components are logged too so the split stays inspectable if FPL ever
    # changes the formula.
    "defensive_contribution",
    "clearances_blocks_interceptions",
    "tackles",
    "recoveries",
    # --- underlying (expected) numbers --------------------------------------
    # Paired with the actuals above, these give over/underperformance at READ
    # time. The difference is deliberately not a column: it is a subtraction,
    # and derived metrics do not belong in the record. Nor are the _per_90
    # variants logged -- they are exactly reproducible from these and `minutes`
    # (Haaland: 28.17 xGI / (2953/90) = 0.86, matching FPL's published figure).
    "expected_goals",
    "expected_assists",
    "expected_goal_involvements",
    "expected_goals_conceded",
    # --- FPL's own forecast: the most perishable data in the response -------
    # Once a gameweek is played, what was predicted beforehand is gone. Logging
    # it also gives a free benchmark to grade our own calls against.
    "ep_this",               # expected points, current event (null off-season)
    "ep_next",               # expected points, next event
]


def log(msg: str) -> None:
    """Print with a UTC timestamp so Action logs are readable."""
    print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] {msg}", flush=True)


def raw(value):
    """Pass an API value straight through, mapping null to an empty cell.

    Deliberately NOT defaulting to 0: for chance_of_playing_next_round, 0 means
    "no chance of playing" and null means "nothing flagged". Collapsing those
    would invent an injury that never existed.
    """
    return "" if value is None else value


def parse_iso(ts: str) -> datetime:
    """Parse an FPL ISO-8601 timestamp ('2026-08-15T10:30:00Z') as aware UTC."""
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def fetch_bootstrap() -> dict:
    """Fetch bootstrap-static with retries + exponential backoff.

    Raises RuntimeError if every attempt fails, so the caller can exit non-zero.
    """
    last_error = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            log(f"Fetching bootstrap-static (attempt {attempt}/{MAX_ATTEMPTS})...")
            resp = requests.get(BOOTSTRAP_URL, headers=HEADERS, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            n = len(data.get("elements", []))
            if n < MIN_EXPECTED_PLAYERS:
                raise ValueError(
                    f"Only {n} players in response (expected >= {MIN_EXPECTED_PLAYERS}); "
                    "refusing to write a partial snapshot."
                )
            log(f"OK: {n} players received.")
            return data
        except Exception as e:  # noqa: BLE001 -- we want to retry on anything
            last_error = e
            log(f"Attempt {attempt} failed: {e!r}")
            if attempt < MAX_ATTEMPTS:
                sleep_s = BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))
                log(f"Backing off {sleep_s}s before retry...")
                time.sleep(sleep_s)

    raise RuntimeError(f"All {MAX_ATTEMPTS} attempts to reach the FPL API failed: {last_error!r}")


def season_for(date: datetime) -> str:
    """FPL season label for a date, e.g. 2026-07-27 -> '2026-27'.

    Seasons start in August; anything from July onward belongs to the season
    that begins that calendar year, everything earlier to the previous one.
    """
    year = date.year
    start = year if date.month >= 7 else year - 1
    return f"{start}-{str(start + 1)[-2:]}"


def current_event(data: dict) -> dict | None:
    """The gameweek a *daily* capture is anchored to.

    Unchanged from the original logger so the daily record's `event` column
    keeps the exact same meaning it has always had.
    """
    events = data.get("events", [])
    return next((e for e in events if e.get("is_next")), None) or \
        next((e for e in events if e.get("is_current")), None)


def upcoming_deadline_event(data: dict, now: datetime) -> dict | None:
    """The soonest gameweek whose deadline is still in the future.

    A *deadline* capture is anchored to this rather than to is_next, because
    the filename and the window guard must both be driven by the actual
    deadline timestamp we are racing, not by a flag we would have to trust.
    """
    future = [
        e for e in data.get("events", [])
        if e.get("deadline_time") and parse_iso(e["deadline_time"]) > now
    ]
    future.sort(key=lambda e: e["deadline_time"])
    return future[0] if future else None


def build_rows(data: dict, snapshot_utc: datetime, kind: str, event: dict | None) -> list[dict]:
    teams = {t["id"]: t for t in data["teams"]}

    event_id = event["id"] if event else ""
    deadline_time = (event or {}).get("deadline_time") or ""

    iso = snapshot_utc.isoformat(timespec="seconds")
    date_str = snapshot_utc.strftime("%Y-%m-%d")

    rows = []
    for p in data["elements"]:
        team = teams.get(p["team"], {})
        rows.append({
            "snapshot_utc": iso,
            "snapshot_date": date_str,
            "snapshot_kind": kind,
            "event": event_id,
            "deadline_time": deadline_time,
            "id": p["id"],
            "web_name": p["web_name"],
            "team": team.get("short_name", ""),
            "position": POS_NAMES.get(p["element_type"], "?"),
            "now_cost": p["now_cost"],
            "cost_change_event": p.get("cost_change_event", 0),
            "cost_change_start": p.get("cost_change_start", 0),
            "selected_by_percent": p.get("selected_by_percent", ""),
            "transfers_in_event": p.get("transfers_in_event", 0),
            "transfers_out_event": p.get("transfers_out_event", 0),
            "transfers_in": p.get("transfers_in", 0),
            "transfers_out": p.get("transfers_out", 0),
            "status": raw(p.get("status")),
            "news": raw(p.get("news")),
            "news_added": raw(p.get("news_added")),
            "chance_of_playing_this_round": raw(p.get("chance_of_playing_this_round")),
            "chance_of_playing_next_round": raw(p.get("chance_of_playing_next_round")),
            "penalties_order": raw(p.get("penalties_order")),
            "direct_freekicks_order": raw(p.get("direct_freekicks_order")),
            "corners_and_indirect_freekicks_order": raw(
                p.get("corners_and_indirect_freekicks_order")
            ),
            "form": raw(p.get("form")),
            "event_points": p.get("event_points", 0),
            "total_points": p.get("total_points", 0),
            "minutes": p.get("minutes", 0),
            "starts": p.get("starts", 0),
            "goals_scored": p.get("goals_scored", 0),
            "assists": p.get("assists", 0),
            "clean_sheets": p.get("clean_sheets", 0),
            "goals_conceded": p.get("goals_conceded", 0),
            "saves": p.get("saves", 0),
            "defensive_contribution": p.get("defensive_contribution", 0),
            "clearances_blocks_interceptions": p.get("clearances_blocks_interceptions", 0),
            "tackles": p.get("tackles", 0),
            "recoveries": p.get("recoveries", 0),
            "expected_goals": raw(p.get("expected_goals")),
            "expected_assists": raw(p.get("expected_assists")),
            "expected_goal_involvements": raw(p.get("expected_goal_involvements")),
            "expected_goals_conceded": raw(p.get("expected_goals_conceded")),
            "ep_this": raw(p.get("ep_this")),
            "ep_next": raw(p.get("ep_next")),
        })
    return rows


def write_snapshot(rows: list[dict], out_path: str) -> None:
    """Write atomically: full file to a temp path, then rename into place.

    A crash mid-write can never leave a half-written CSV that looks like a
    valid capture. Rename is atomic on the same filesystem.
    """
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    tmp_path = out_path + ".tmp"

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=COLUMNS)
    writer.writeheader()
    writer.writerows(rows)

    with open(tmp_path, "w", newline="", encoding="utf-8") as f:
        f.write(buf.getvalue())
    os.replace(tmp_path, out_path)


def resolve_daily(data: dict, snapshot_utc: datetime, repo_root: str) -> tuple[dict | None, str]:
    """Anchor + output path for a daily capture."""
    season = season_for(snapshot_utc)
    date_str = snapshot_utc.strftime("%Y-%m-%d")
    out_path = os.path.join(repo_root, "data", "snapshots", season, f"{date_str}.csv")
    return current_event(data), out_path


def resolve_deadline(
    data: dict, snapshot_utc: datetime, repo_root: str, force: bool
) -> tuple[dict, str]:
    """Anchor + output path for a deadline capture, with the window guard.

    Raises RuntimeError rather than writing anything if we are outside the
    valid pre-deadline window. Writing a file named gw07.csv that actually
    contains post-deadline (already-reset) counters would be worse than having
    no file at all: it would look correct and silently corrupt the record.
    """
    event = upcoming_deadline_event(data, snapshot_utc)
    if event is None:
        raise RuntimeError(
            "No gameweek with a future deadline found in bootstrap-static "
            "(season over, or the events list is empty off-season)."
        )

    deadline = parse_iso(event["deadline_time"])
    lead = deadline - snapshot_utc
    lead_minutes = lead.total_seconds() / 60
    log(
        f"Next deadline: GW{event['id']} at {event['deadline_time']} "
        f"(T-{lead_minutes:.0f} min)."
    )

    season = season_for(snapshot_utc)
    out_path = os.path.join(repo_root, "data", "deadlines", season, f"gw{event['id']:02d}.csv")

    if force:
        log("--force set: skipping the pre-deadline window guard.")
        return event, out_path

    if lead <= timedelta(0):
        raise RuntimeError(
            f"Deadline for GW{event['id']} has already passed; transfer counters have "
            "reset. Refusing to write post-deadline data under a pre-deadline filename."
        )
    if lead > timedelta(minutes=DEADLINE_MAX_LEAD_MINUTES):
        raise RuntimeError(
            f"Too early: GW{event['id']} deadline is {lead_minutes:.0f} min away, "
            f"limit is {DEADLINE_MAX_LEAD_MINUTES} min. Not a deadline capture."
        )

    return event, out_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Take an FPL market snapshot (daily by default).",
    )
    parser.add_argument(
        "--deadline",
        action="store_true",
        help="Take a deadline-anchored capture into data/deadlines/<season>/gw<NN>.csv "
             "instead of the daily file. Only valid shortly before a deadline.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="With --deadline, bypass the pre-deadline window guard. For manual recovery only.",
    )
    args = parser.parse_args(argv)

    snapshot_utc = datetime.now(timezone.utc)
    kind = "deadline" if args.deadline else "daily"
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    try:
        data = fetch_bootstrap()
        if args.deadline:
            event, out_path = resolve_deadline(data, snapshot_utc, repo_root, args.force)
        else:
            event, out_path = resolve_daily(data, snapshot_utc, repo_root)
        rows = build_rows(data, snapshot_utc, kind, event)
    except Exception as e:  # noqa: BLE001
        log(f"FATAL: {e!r}")
        log(f"No {kind} snapshot written. This capture is a permanent gap unless re-run in time.")
        return 1

    if os.path.exists(out_path):
        # For daily, a same-day re-run is a straight recovery. For deadline, a
        # later capture is strictly closer to the deadline, so overwriting an
        # earlier one is the desired behaviour -- and the guard above has
        # already proven we are still pre-deadline.
        log(f"Note: {os.path.relpath(out_path, repo_root)} already exists -- overwriting.")

    write_snapshot(rows, out_path)
    log(f"Wrote {len(rows)} rows ({kind}) -> {os.path.relpath(out_path, repo_root)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

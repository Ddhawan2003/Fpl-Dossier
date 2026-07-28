#!/usr/bin/env python3
"""Daily FPL market logger.

Takes one snapshot per day of the FPL "market" for every player -- price,
ownership, and transfer flows -- and writes it as a dated CSV. This history
CANNOT be backfilled: the live FPL API only ever shows "now", so every day we
fail to log is signal lost forever. Because of that, this script is built to
fail LOUDLY (non-zero exit) rather than write a partial or empty snapshot that
would masquerade as a good day.

Design rules this file honours:
  * Self-contained: depends only on `requests` + the stdlib. It imports nothing
    from the dashboard, so nothing done to the dashboard can take it down.
  * Exact arithmetic only: every value written is a raw API field, stored
    verbatim. No interpretation, no rounding, no derived metrics. Anything our
    accountability record depends on ("he was 4% owned when we called him") is
    read straight back out of these files, reproducibly.
"""

from __future__ import annotations

import csv
import io
import os
import sys
import time
from datetime import datetime, timezone

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

POS_NAMES = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}

# The columns we log, in order. Every one is a raw API field. now_cost and the
# cost_change_* fields are integer tenths (75 == GBP 7.5); we store them raw so
# the record is exact and the dashboard does any division at read time.
COLUMNS = [
    "snapshot_utc",          # exact ISO-8601 fetch time (UTC)
    "snapshot_date",         # UTC date of the fetch (one file == one of these)
    "event",                 # next/current gameweek id, for context
    "id",                    # element id -- stable within a season
    "web_name",
    "team",                  # team short name (e.g. ARS)
    "position",              # GKP/DEF/MID/FWD
    "now_cost",              # price in tenths of a million
    "cost_change_event",     # price change during the current event
    "cost_change_start",     # price change since season start
    "selected_by_percent",   # ownership %
    "transfers_in_event",    # transfers in during the current event
    "transfers_out_event",   # transfers out during the current event
    "transfers_in",          # total transfers in this season
    "transfers_out",         # total transfers out this season
]


def log(msg: str) -> None:
    """Print with a UTC timestamp so Action logs are readable."""
    print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] {msg}", flush=True)


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


def build_rows(data: dict, snapshot_utc: datetime) -> list[dict]:
    teams = {t["id"]: t for t in data["teams"]}
    events = data.get("events", [])
    next_event = next((e for e in events if e.get("is_next")), None) or \
        next((e for e in events if e.get("is_current")), None)
    event_id = next_event["id"] if next_event else ""

    iso = snapshot_utc.isoformat(timespec="seconds")
    date_str = snapshot_utc.strftime("%Y-%m-%d")

    rows = []
    for p in data["elements"]:
        team = teams.get(p["team"], {})
        rows.append({
            "snapshot_utc": iso,
            "snapshot_date": date_str,
            "event": event_id,
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
        })
    return rows


def write_snapshot(rows: list[dict], out_path: str) -> None:
    """Write atomically: full file to a temp path, then rename into place.

    A crash mid-write can never leave a half-written CSV that looks like a
    valid day. Rename is atomic on the same filesystem.
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


def main() -> int:
    snapshot_utc = datetime.now(timezone.utc)
    season = season_for(snapshot_utc)
    date_str = snapshot_utc.strftime("%Y-%m-%d")

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out_path = os.path.join(repo_root, "data", "snapshots", season, f"{date_str}.csv")

    try:
        data = fetch_bootstrap()
        rows = build_rows(data, snapshot_utc)
    except Exception as e:  # noqa: BLE001
        log(f"FATAL: {e!r}")
        log("No snapshot written. This day will be a gap unless re-run manually today.")
        return 1

    if os.path.exists(out_path):
        log(f"Note: {out_path} already exists -- overwriting (idempotent re-run).")

    write_snapshot(rows, out_path)
    log(f"Wrote {len(rows)} rows -> {os.path.relpath(out_path, repo_root)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

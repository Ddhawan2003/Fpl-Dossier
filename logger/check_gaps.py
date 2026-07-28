#!/usr/bin/env python3
"""Gap detector for the FPL market logger.

The daily and deadline workflows each open an issue when a run FAILS. Neither
can tell you about a run that never HAPPENED -- and that is the more likely
outcome. GitHub disables scheduled workflows after 60 days of repository
inactivity (one easily-missed email), Actions can be disabled or quota-capped,
and cron delivery is explicitly not guaranteed. In every one of those cases
there is no run, so no failure, so no issue: silence, which is
indistinguishable from success. You would find out weeks later, and by
definition the missing captures are unrepairable.

This script is the missing check. Instead of asking "did the last run
succeed?", it asks "does the record actually contain what it should?" -- which
is the question that catches a logger that has quietly stopped.

It runs from its own weekly workflow, deliberately separate from the jobs it
checks, so it does not share their failure mode.

What it asserts, for the current season:
  * a daily snapshot exists for every UTC date from the first one ever taken
    through yesterday (today's has not been taken yet at most run times);
  * a deadline capture exists for every gameweek whose deadline has passed
    since logging began;
  * no snapshot is suspiciously small (a truncated file is a silent gap
    wearing a valid filename).

Exit 0 = the record is intact. Exit 1 = something is missing; the workflow
turns that into a GitHub issue.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from datetime import datetime, timedelta, timezone

# Both modules live in logger/ and ship together, so sharing the fetch logic
# (retries, backoff, the browser User-Agent) keeps one definition rather than
# letting two copies drift apart.
from log_snapshot import (
    MIN_EXPECTED_PLAYERS,
    fetch_bootstrap,
    log,
    parse_iso,
    season_for,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def data_row_count(path: str) -> int:
    """Number of data rows (excluding the header) in a snapshot CSV."""
    with open(path, newline="", encoding="utf-8") as f:
        return sum(1 for _ in csv.DictReader(f))


def daily_dir(season: str) -> str:
    return os.path.join(REPO_ROOT, "data", "snapshots", season)


def deadline_dir(season: str) -> str:
    return os.path.join(REPO_ROOT, "data", "deadlines", season)


def present_daily_dates(season: str) -> list[str]:
    d = daily_dir(season)
    if not os.path.isdir(d):
        return []
    return sorted(
        f[: -len(".csv")] for f in os.listdir(d)
        if f.endswith(".csv") and not f.endswith(".tmp")
    )


MAX_SNAPSHOT_AGE_DAYS = 2


def newest_snapshot_anywhere() -> tuple[str, str] | None:
    """(season, date) of the most recent daily snapshot across all seasons.

    Deliberately season-agnostic. season_for() rolls over to the new season on
    1 July, but that season's directory does not exist until that night's run --
    so a check keyed to the current season alone would cry wolf every year on
    the first morning of July. "When did we last capture anything?" has no such
    edge case.
    """
    root = os.path.join(REPO_ROOT, "data", "snapshots")
    if not os.path.isdir(root):
        return None
    best = None
    for season in os.listdir(root):
        for date in present_daily_dates(season):
            if best is None or date > best[1]:
                best = (season, date)
    return best


def check_freshness(now: datetime, problems: list[str]) -> None:
    """The primary 'is the logger still alive?' assertion."""
    newest = newest_snapshot_anywhere()
    if newest is None:
        problems.append("No daily snapshots exist anywhere under data/snapshots/.")
        return

    season, date = newest
    age = (now.date() - datetime.strptime(date, "%Y-%m-%d").date()).days
    if age > MAX_SNAPSHOT_AGE_DAYS:
        problems.append(
            f"Most recent snapshot is {date} ({season}), {age} days old. "
            f"The logger appears to have STOPPED -- this is the alarm that matters."
        )
    log(f"Freshness: newest snapshot {date} ({season}), {age} day(s) old.")


def check_daily(season: str, now: datetime, problems: list[str]) -> str | None:
    """Verify one snapshot per UTC date within a season. Returns the first date."""
    dates = present_daily_dates(season)
    if not dates:
        # Not an error on its own: at season rollover the new directory is
        # legitimately empty for a few hours. check_freshness() is what catches
        # a genuinely stopped logger.
        log(f"Daily: no files yet for {season} (normal just after season rollover).")
        return None

    first = dates[0]
    # Today's snapshot is taken at 22:30 UTC, so it legitimately does not exist
    # for most of the day. Only ever assert through yesterday.
    yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")

    expected = []
    cursor = datetime.strptime(first, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end = datetime.strptime(yesterday, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    while cursor <= end:
        expected.append(cursor.strftime("%Y-%m-%d"))
        cursor += timedelta(days=1)

    missing = sorted(set(expected) - set(dates))
    if missing:
        shown = ", ".join(missing[:10]) + (f" (+{len(missing) - 10} more)" if len(missing) > 10 else "")
        problems.append(f"Missing {len(missing)} daily snapshot(s): {shown}")

    log(f"Daily: {len(dates)} file(s), {first} -> {dates[-1]}, {len(missing)} missing.")
    return first


def check_deadlines(season: str, now: datetime, since: str | None, problems: list[str]) -> None:
    """Verify one capture per gameweek whose deadline has already passed.

    Only gameweeks whose deadline fell after logging began are checked -- older
    ones were never ours to capture and would be permanent false alarms.
    """
    if since is None:
        return

    try:
        data = fetch_bootstrap()
    except Exception as e:  # noqa: BLE001
        problems.append(f"Could not reach the FPL API to list gameweek deadlines: {e!r}")
        return

    passed = [
        e for e in data.get("events", [])
        if e.get("deadline_time")
        and parse_iso(e["deadline_time"]) < now
        and e["deadline_time"][:10] >= since
    ]

    if not passed:
        log("Deadlines: none have passed since logging began -- nothing to check yet.")
        return

    d = deadline_dir(season)
    missing = [e for e in passed if not os.path.isfile(os.path.join(d, f"gw{e['id']:02d}.csv"))]

    if missing:
        shown = ", ".join(f"GW{e['id']} ({e['deadline_time']})" for e in missing[:10])
        problems.append(f"Missing {len(missing)} deadline capture(s): {shown}")

    log(f"Deadlines: {len(passed)} passed since {since}, {len(missing)} missing.")


def check_file_sizes(season: str, problems: list[str]) -> None:
    """Catch truncated files -- a short CSV is a gap wearing a valid filename."""
    checked = undersized = 0
    for d in (daily_dir(season), deadline_dir(season)):
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            if not name.endswith(".csv"):
                continue
            path = os.path.join(d, name)
            checked += 1
            try:
                n = data_row_count(path)
            except Exception as e:  # noqa: BLE001
                problems.append(f"Could not read {os.path.relpath(path, REPO_ROOT)}: {e!r}")
                undersized += 1
                continue
            if n < MIN_EXPECTED_PLAYERS:
                problems.append(
                    f"{os.path.relpath(path, REPO_ROOT)} has only {n} rows "
                    f"(expected >= {MIN_EXPECTED_PLAYERS}) -- truncated capture."
                )
                undersized += 1

    log(f"Sizes: {checked} file(s) checked, {undersized} suspicious.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Assert the logger's record has no gaps.")
    parser.add_argument(
        "--season",
        help="Season label to check, e.g. 2026-27. Defaults to the current season.",
    )
    args = parser.parse_args(argv)

    now = datetime.now(timezone.utc)
    season = args.season or season_for(now)
    log(f"Checking season {season} as of {now.isoformat(timespec='seconds')}...")

    problems: list[str] = []
    check_freshness(now, problems)
    first = check_daily(season, now, problems)
    check_deadlines(season, now, first, problems)
    check_file_sizes(season, problems)

    if problems:
        log(f"FAIL: {len(problems)} problem(s) found.")
        for p in problems:
            log(f"  - {p}")
        return 1

    log("OK: the record is intact.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

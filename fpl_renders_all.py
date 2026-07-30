#!/usr/bin/env python3
"""
FPL -> FootyRenders pipeline: per-player tag lookup, current-kit only.

FootyRenders files a render under whatever club a player was wearing when the
render was CUT, not their current club. So instead of crawling
/premier-league/{club}/ (which only finds players already cut for that
specific club), this hits each player's /tag/{slug}/ page directly -- that
page aggregates every render of that player across every club they've ever
worn -- and then filters by the breadcrumb club on each candidate render page
to keep ONLY a render in their current kit. If no current-kit render exists,
the player is just marked no-render and we move on to the next one.

New in this version:
  --clubs "Liverpool,Arsenal"   only process these FPL clubs (test on a couple)
  --limit-clubs N               only process the first N clubs (alphabetical)
  --overwrite                   redo players even if their PNG already exists
  (default) resume              skip players whose PNG is already on disk, and
                                merge into the existing render_report.csv

Examples:
  python fpl_renders_all.py --clubs "Bournemouth"     # one club, quick test
  python fpl_renders_all.py --limit-clubs 2           # first two clubs
  python fpl_renders_all.py                           # whole league, resuming

Requires:  pip install requests
"""

import os
import re
import csv
import time
import argparse
import unicodedata
import requests

BASE = "https://www.footyrenders.com"
FPL_URL = "https://fantasy.premierleague.com/api/bootstrap-static/"
OUT_DIR = "renders"
REPORT = "render_report.csv"
HEADERS = {"User-Agent": "Mozilla/5.0 (render-fetch; personal FPL project)"}

MAX_CANDIDATES = 5
MIN_RATIO = 1.6
DELAY = 1.0

# Maps FPL's team["name"] string -> FootyRenders club path, used only to
# figure out which breadcrumb slug counts as "current kit" for that club.
# If a club is missing here, lookup still happens -- current-kit filtering
# just can't be applied, so nothing will match (safe: falls through to
# no-render for that player rather than crashing or skipping the club).
FR_CLUB = {
    "Arsenal": "premier-league/arsenal",
    "Aston Villa": "premier-league/aston-villa",
    "Bournemouth": "premier-league/bournemouth",
    "Brentford": "premier-league/brentford",
    "Brighton": "premier-league/brighton-hove-albion",
    "Chelsea": "premier-league/chelsea",
    "Coventry City": "other-leagues/championship/coventry-city",
    "Crystal Palace": "premier-league/crystal-palace",
    "Everton": "premier-league/everton",
    "Fulham": "premier-league/fulham",
    "Hull City": "other-leagues/championship/hull-city",
    "Leeds": "other-leagues/championship/leeds-united",
    "Liverpool": "premier-league/liverpool",
    "Man City": "premier-league/manchester-city",
    "Man Utd": "premier-league/manchester-united",
    "Newcastle": "premier-league/newcastle",
    "Nott'm Forest": "premier-league/nottingham-forest",
    "Spurs": "premier-league/tottenham",
    "Sunderland": "other-leagues/championship/sunderland",
}

# Paths under BASE that are never a player's render page (nav/meta pages that
# happen to match the tag-page link regex).
EXCLUDE_PREFIXES = {
    "tag", "nations", "category", "page", "search", "fifa", "tutorials",
    "request", "upload", "about", "contact", "contributors",
    "become-a-contributor", "disclaimer", "privacy-policy", "faq",
    "rotw-archive", "cdn",
}

# NFKD strip alone misses letters that don't decompose into base+accent
# (Đ, Ł, Ø, etc.), which matters for names like Đorđe Petrović.
TRANSLIT = str.maketrans({
    "Đ": "D", "đ": "d", "Ł": "L", "ł": "l", "Ø": "O", "ø": "o",
    "Þ": "Th", "þ": "th", "Æ": "Ae", "æ": "ae",
})


def get_fpl_players():
    r = requests.get(FPL_URL, headers=HEADERS, timeout=30)
    r.raise_for_status()
    data = r.json()
    teams = {t["id"]: t["name"] for t in data["teams"]}
    return [
        {"name": f"{e['first_name']} {e['second_name']}".strip(), "team": teams[e["team"]]}
        for e in data["elements"]
    ]


def slugify(name):
    name = name.translate(TRANSLIT)
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    name = re.sub(r"[^a-zA-Z0-9]+", "-", name).strip("-").lower()
    return name


def tag_slug_candidates(name):
    """Try full name, first+last, then last-name-only / first-name-only,
    since FootyRenders tags are whatever the site/cutter labeled the player,
    which doesn't always match FPL's full legal name."""
    tokens = name.split()
    candidates = [slugify(name)]
    if len(tokens) > 2:
        candidates.append(slugify(f"{tokens[0]} {tokens[-1]}"))
    if len(tokens) > 1:
        candidates.append(slugify(tokens[-1]))
        candidates.append(slugify(tokens[0]))
    seen, out = set(), []
    for c in candidates:
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def path_segments(url):
    path = url[len(BASE):].strip("/")
    return path.split("/")


def fetch_tag_render_links(slug):
    r = requests.get(f"{BASE}/tag/{slug}/", headers=HEADERS, timeout=30)
    if r.status_code != 200:
        return []
    hrefs = re.findall(rf'href="{re.escape(BASE)}/([a-z0-9\-/]+)/"', r.text)
    links = []
    for path in hrefs:
        segs = path.split("/")
        if len(segs) >= 3 and segs[0] not in EXCLUDE_PREFIXES:
            links.append(f"{BASE}/{path}/")
    return links


def club_slug_of(team):
    path = FR_CLUB.get(team)
    return path.rsplit("/", 1)[-1] if path else None


def render_details(url):
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    page = r.text
    m = re.search(r'property=["\']og:image["\']\s+content=["\']([^"\']+\.png)', page)
    png = m.group(1) if m else None
    if not png:
        for c in re.findall(r'https://www\.footyrenders\.com/render/[^"\'\s)]+\.png', page):
            if not re.search(r'-\d+x\d+\.png$', c):
                png = c
                break
    if not png:
        return None
    w = re.search(r'og:image:width["\']\s+content=["\'](\d+)', page)
    h = re.search(r'og:image:height["\']\s+content=["\'](\d+)', page)
    if w and h:
        return png, int(w.group(1)), int(h.group(1))
    res = re.search(r'Resolution:\s*(\d+)\s*x\s*(\d+)', page)
    if res:
        return png, int(res.group(1)), int(res.group(2))
    return png, None, None


def pick_best(candidate_urls):
    scored = []
    for url in candidate_urls[:MAX_CANDIDATES]:
        d = render_details(url)
        time.sleep(DELAY)
        if not d:
            continue
        png, w, h = d
        ratio = (h / w) if (w and h) else 0
        scored.append((ratio, png, w, h))
    if not scored:
        return None
    standing = [s for s in scored if s[0] >= MIN_RATIO]
    return max(standing or scored, key=lambda s: s[0])


def pick_current_kit(urls, team_club_slug):
    """Only consider renders whose breadcrumb club matches the player's
    current FPL club. Returns None if there's no current-kit render."""
    if not team_club_slug:
        return None
    current_kit_urls = [u for u in urls if path_segments(u)[-2] == team_club_slug]
    return pick_best(current_kit_urls) if current_kit_urls else None


def safe_name(name):
    return re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_")


def png_path(player):
    return os.path.join(OUT_DIR, f"{safe_name(player['name'])}.png")


FIELDNAMES = ["player", "team", "matched_slug", "png_url", "size", "status"]


def load_prior():
    prior = {}
    if os.path.exists(REPORT):
        with open(REPORT, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                prior[(row["player"], row["team"])] = row
    return prior


def write_report(results):
    """Write the CSV now, so progress survives a Ctrl+C or crash mid-run."""
    with open(REPORT, "w", newline="", encoding="utf-8") as f:
        wr = csv.DictWriter(f, fieldnames=FIELDNAMES)
        wr.writeheader()
        for row in results.values():
            wr.writerow({k: row.get(k, "") for k in FIELDNAMES})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--clubs", help='comma-separated FPL club names, e.g. "Liverpool,Arsenal"')
    ap.add_argument("--limit-clubs", type=int, help="only the first N clubs (alphabetical)")
    ap.add_argument("--overwrite", action="store_true", help="redo players even if PNG exists")
    args = ap.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)
    players = get_fpl_players()
    print(f"{len(players)} FPL players fetched.")

    by_club = {}
    for p in players:
        by_club.setdefault(p["team"], []).append(p)

    clubs = sorted(by_club.keys())
    if args.clubs:
        wanted = {c.strip() for c in args.clubs.split(",")}
        missing = wanted - set(clubs)
        if missing:
            print(f"  note: not in FPL club list: {', '.join(sorted(missing))}")
        clubs = [c for c in clubs if c in wanted]
    if args.limit_clubs:
        clubs = clubs[: args.limit_clubs]
    print(f"Processing {len(clubs)} club(s): {', '.join(clubs)}\n")

    prior = load_prior()
    results = dict(prior)  # start from prior, update as we go
    counts = {"fetched": 0, "skipped": 0, "no-render": 0, "error": 0}

    for team in clubs:
        squad = by_club[team]
        print(f"=== {team}  ({len(squad)} players) ===")
        team_club_slug = club_slug_of(team)  # None if not in FR_CLUB -- fine,
                                              # current-kit filter just won't match

        todo = [p for p in squad if args.overwrite or not os.path.exists(png_path(p))]
        for p in squad:
            if p not in todo:  # already have the file -> resume-skip
                key = (p["name"], team)
                if key not in results:
                    results[key] = {"player": p["name"], "team": team, "matched_slug": "",
                                    "png_url": "", "size": "", "status": "fetched"}
                counts["skipped"] += 1

        if not todo:
            print("  all players already downloaded — skipping\n")
            write_report(results)
            continue

        print(f"  {len(todo)} players to fetch (tag lookup)")

        for p in todo:
            row = {"player": p["name"], "team": team, "matched_slug": "",
                   "png_url": "", "size": "", "status": "no-render"}
            try:
                urls, used_slug = [], None
                for slug in tag_slug_candidates(p["name"]):
                    urls = fetch_tag_render_links(slug)
                    time.sleep(DELAY)
                    if urls:
                        used_slug = slug
                        break

                if urls:
                    row["matched_slug"] = used_slug
                    best = pick_current_kit(urls, team_club_slug)
                    if best:
                        ratio, png, w, h = best
                        img = requests.get(png, headers=HEADERS, timeout=60)
                        img.raise_for_status()
                        with open(png_path(p), "wb") as f:
                            f.write(img.content)
                        row.update(png_url=png, size=f"{w}x{h}", status="fetched")
                        counts["fetched"] += 1
                        print(f"    OK  {p['name']:<24} {w}x{h}")
                        time.sleep(DELAY)
                    else:
                        counts["no-render"] += 1
                else:
                    counts["no-render"] += 1
            except requests.RequestException as e:
                row["status"] = f"error: {e}"
                counts["error"] += 1

            results[(p["name"], team)] = row
        write_report(results)  # save after every club, not just at the end
        print()

    print("Summary (this run):")
    for k, v in counts.items():
        print(f"  {k:16} {v}")
    print(f"\nImages in ./{OUT_DIR}/ , report in {REPORT}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Generate the daily football summary HTML page.

Usage:
    python3 football/generate_summary.py [--date YYYYMMDD] [--out DIR]
                                         [--offline FIXTURE.json]
                                         [--no-details] [--standings N]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import report  # noqa: E402
import fotmob  # noqa: E402
from fotmob import (  # noqa: E402
    LEAGUES,
    Client,
    collect_day,
    extract_scorers,
    extract_stats,
    standings_rows,
)

SOURCES = ["FotMob public JSON API (matches, matchDetails, league tables)"]

# How many days back / forward to scan for results and fixtures.
RESULT_LOOKBACK_DAYS = 1
FIXTURE_LOOKAHEAD_DAYS = 2


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--date", help="anchor date YYYYMMDD (default: today)")
    p.add_argument("--out", default=".", help="output directory")
    p.add_argument("--offline", help="use a saved JSON payload instead of network")
    p.add_argument("--no-details", action="store_true",
                   help="skip per-match detail calls (scorers/stats)")
    p.add_argument("--standings", type=int, default=6,
                   help="competitions per region to show standings for")
    p.add_argument("--tz-offset", type=int, default=None,
                   help="fixed UTC offset hours for display (default: local)")
    p.add_argument("--max-detail", type=int, default=40,
                   help="cap on per-match detail fetches")
    return p.parse_args(argv)


def collect_all(client: Client, anchor: dt.date, enrich: bool = True,
                max_detail: int = 40,
                now: dt.datetime | None = None) -> tuple[list[dict], dict]:
    """Collect results (anchor + previous day) and fixtures (next day).

    The fixture window is centred on the wall clock, not on `anchor`: with a
    historical `--date` the anchor+1 day is long past, so scanning it would
    list fixtures that already kicked off as "upcoming". When `now` is given
    the scan starts at the local date containing `now` and runs two days
    forward, which always covers the real next 24 hours.
    """
    seen: dict[int, dict] = {}
    windows: dict[str, list[str]] = {}

    def add_day(d: dt.date, label: str) -> list[dict]:
        key = d.strftime("%Y%m%d")
        windows.setdefault(label, []).append(key)
        matches = collect_day(client, key)
        for m in matches:
            prev = seen.get(m["id"])
            if prev:
                # keep the richer copy (a later scan may see it finished)
                if prev["status"] == "SCHEDULED" and m["status"] != "SCHEDULED":
                    seen[m["id"]] = m
            else:
                seen[m["id"]] = m
        return matches

    fixture_base = (now.astimezone().date() if now is not None else anchor)
    add_day(anchor - dt.timedelta(days=RESULT_LOOKBACK_DAYS), "results")
    add_day(anchor, "results")
    for i in range(FIXTURE_LOOKAHEAD_DAYS + 1):
        d = fixture_base + dt.timedelta(days=i)
        if d in (anchor, anchor - dt.timedelta(days=RESULT_LOOKBACK_DAYS)):
            continue  # already fetched for the results window
        add_day(d, "fixtures")

    matches = sorted(seen.values(),
                     key=lambda m: (m["utc"] or "", m["league"], m["home"]))

    if enrich:
        budget = max_detail
        for m in matches:
            if m["status"] == "SCHEDULED" or budget <= 0:
                continue
            # A cancelled match has no events or stats to fetch, so spending
            # one of the detail budget on it only starves a real match.
            if m["status"] == "Cancelled":
                continue
            try:
                details = client.match_details(m["id"])
            except Exception as exc:  # noqa: BLE001 - degrade gracefully
                print(f"  ! details for {m['id']} failed: {exc}", file=sys.stderr)
                continue
            if not details:
                continue
            budget -= 1
            m["goals"] = extract_scorers(details)
            m["stats"] = extract_stats(details)
            info = (((details.get("content") or {}).get("matchFacts") or {})
                    .get("infoBox") or {})
            stadium = info.get("Stadium") or {}
            m["venue"] = stadium.get("name")
            m["attendance"] = (info.get("Attendance")
                               or stadium.get("attendance"))
            # A shootout score lives on the header status, not in the events.
            hstatus = (details.get("header") or {}).get("status") or {}
            reason = hstatus.get("reason") or {}
            pens = reason.get("penalties")
            if pens and len(pens) == 2:
                m["penalties"] = list(pens)
            if hstatus.get("whoLostOnPenalties"):
                m["lostOnPenalties"] = hstatus["whoLostOnPenalties"]
    return matches, windows


def load_standings(client: Client, per_region: int) -> list[tuple[dict, list]]:
    """Fetch standings for up to `per_region` competitions in each region."""
    out = []
    counts: dict[str, int] = {"Europe": 0, "MENA": 0}
    for lg in LEAGUES:
        region = lg["region"]
        if counts.get(region, 0) >= per_region:
            continue
        try:
            data = client.league(lg["id"])
        except Exception as exc:  # noqa: BLE001
            print(f"  ! standings for {lg['name']} failed: {exc}", file=sys.stderr)
            continue
        rows = standings_rows(data, "all", limit=10)
        if rows:
            entry = dict(lg)
            entry["legend"] = fotmob.extract_standings(data).get("legend", [])
            out.append((entry, rows))
            counts[region] = counts.get(region, 0) + 1
    return out


def run(argv=None) -> str:
    args = parse_args(argv)
    if args.offline:
        payload = json.load(open(args.offline, encoding="utf-8"))
        matches = payload["matches"]
        windows = payload.get("windows", {})
        standings = [(lg, rows) for lg, rows in payload.get("standings", [])]
        now = dt.datetime.fromisoformat(payload["now"])
        offset = now.utcoffset() or dt.timedelta(hours=0)
        tz = dt.timezone(offset)
        anchor = now.astimezone(tz).date()
    else:
        client = Client()
        anchor = (dt.datetime.strptime(args.date, "%Y%m%d").date()
                  if args.date else dt.date.today())
        now = dt.datetime.now().astimezone()
        tz = now.tzinfo
        print(f"[{now:%Y-%m-%d %H:%M %Z}] scanning {anchor} window...", file=sys.stderr)
        matches, windows = collect_all(
            client, anchor, enrich=not args.no_details,
            max_detail=args.max_detail, now=now)
        print(f"  matched {len(matches)} fixtures; "
              f"{client.calls} API calls", file=sys.stderr)
        standings = load_standings(client, args.standings)

    html_text = report.render_html(matches, standings, now, tz, windows,
                                   SOURCES, report_date=anchor)

    out_dir = args.out
    os.makedirs(out_dir, exist_ok=True)
    # Name the file after the reporting day (the anchor), not the wall clock.
    # Otherwise a backfill run for an old --date would overwrite today's file.
    if args.offline:
        stamp = now.strftime("%Y%m%d")
    elif args.date:
        stamp = args.date
    else:
        stamp = now.strftime("%Y%m%d")
    path = os.path.join(out_dir, f"football_summary_{stamp}.html")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(html_text)

    if args.offline:
        with open(os.path.join(out_dir, f"football_summary_{stamp}.json"),
                  "w", encoding="utf-8") as fh:
            json.dump({"matches": matches, "windows": windows,
                       "standings": [[lg, rows] for lg, rows in standings],
                       "now": now.isoformat()}, fh, ensure_ascii=False)
    print(f"wrote {path} ({len(html_text)} bytes, "
          f"{len(matches)} matches, {len(standings)} standings tables)")
    return path


if __name__ == "__main__":
    run()

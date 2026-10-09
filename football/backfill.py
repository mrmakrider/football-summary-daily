#!/usr/bin/env python3
"""Recover daily reports that the 07:00 scheduler missed.

The scheduler only runs while the AndroidHarness app is alive, so a day can
be silently skipped (app killed, device asleep, battery optimiser). This
scans the output directory for gaps and regenerates them in one pass.

    python3 football/backfill.py                 # fill gaps in summaries/
    python3 football/backfill.py --days 7        # only the last 7 days
    python3 football/backfill.py --dry-run       # report gaps, fetch nothing
    python3 football/backfill.py --force         # also rebuild existing days
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import generate_summary as gs  # noqa: E402

DEFAULT_OUT = os.path.abspath(os.path.join(HERE, "..", "summaries"))
FILENAME_DATE_LEN = 8  # YYYYMMDD


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", default=DEFAULT_OUT, help="output directory")
    p.add_argument("--days", type=int, default=14,
                   help="how many days back to look for gaps (default 14)")
    p.add_argument("--dry-run", action="store_true",
                   help="list the missing days without generating anything")
    p.add_argument("--force", action="store_true",
                   help="regenerate days that already have a report")
    p.add_argument("--standings", type=int, default=6,
                   help="competitions per region to show standings for")
    p.add_argument("--max-detail", type=int, default=40,
                   help="cap on per-match detail fetches")
    p.add_argument("--no-details", action="store_true",
                   help="skip per-match detail calls (scorelines only)")
    p.add_argument("--pause", type=float, default=1.0,
                   help="seconds to wait between days (politeness)")
    return p.parse_args(argv)


def existing_days(out_dir: str) -> set[dt.date]:
    """Dates that already have a report in `out_dir`."""
    found: set[dt.date] = set()
    try:
        names = os.listdir(out_dir)
    except FileNotFoundError:
        return found
    prefix = "football_summary_"
    for name in names:
        if not name.startswith(prefix) or not name.endswith(".html"):
            continue
        stamp = name[len(prefix):-len(".html")]
        if len(stamp) != FILENAME_DATE_LEN or not stamp.isdigit():
            continue
        try:
            found.add(dt.datetime.strptime(stamp, "%Y%m%d").date())
        except ValueError:
            continue
    return found


def find_gaps(out_dir: str, days: int, today: dt.date | None = None
              ) -> tuple[list[dt.date], set[dt.date]]:
    """Return (missing days oldest-first, days already present)."""
    today = today or dt.date.today()
    start = today - dt.timedelta(days=days - 1)
    have = existing_days(out_dir)
    missing = []
    for i in range(days):
        d = start + dt.timedelta(days=i)
        if d not in have:
            missing.append(d)
    return missing, have


def main(argv=None) -> int:
    args = parse_args(argv)
    today = dt.date.today()
    missing, have = find_gaps(args.out, args.days, today)
    if args.force:
        # Rebuild every day in the window, not only the missing ones.
        start = today - dt.timedelta(days=args.days - 1)
        missing = [start + dt.timedelta(days=i) for i in range(args.days)]

    print(f"scanning {args.out} for the last {args.days} days "
          f"({today - dt.timedelta(days=args.days - 1)} .. {today})")
    if args.force:
        print(f"  --force: rebuilding all {len(missing)} day(s)")
    else:
        print(f"  {len(have)} day(s) already present, {len(missing)} missing")
        if missing:
            print("  missing: " + ", ".join(d.isoformat() for d in missing))
        if not missing:
            print("nothing to backfill.")
            return 0

    if args.dry_run:
        print("\n--dry-run: no network calls made.")
        return 0

    written = []
    failed = []
    for i, day in enumerate(missing):
        stamp = day.strftime("%Y%m%d")
        print(f"[{i + 1}/{len(missing)}] rebuilding {stamp} ...", flush=True)
        argv_run = ["--date", stamp, "--out", args.out,
                    "--standings", str(args.standings),
                    "--max-detail", str(args.max_detail)]
        if args.no_details:
            argv_run.append("--no-details")
        try:
            path = gs.run(argv_run)
        except Exception as exc:  # noqa: BLE001 - one bad day must not abort
            print(f"  ! {stamp} failed: {exc}", file=sys.stderr)
            failed.append(stamp)
            continue
        written.append(os.path.basename(path))
        if i + 1 < len(missing):
            time.sleep(args.pause)

    print(f"\ndone: {len(written)} written, {len(failed)} failed")
    for name in written:
        print(f"  + {name}")
    for stamp in failed:
        print(f"  ! {stamp} (retry this date later)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

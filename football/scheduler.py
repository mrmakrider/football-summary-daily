#!/usr/bin/env python3
"""Daily scheduler: runs the football summary generator at 03:00 local time.

Runs at 03:00 (rather than 07:00) so the digest is ready well before the
morning; the generator scans yesterday + today's results plus the next two
days of fixtures, so a 03:00 run already contains everything available at
07:00.

Android has no cron daemon, so this long-running loop computes the remaining
sleep to the next 03:00 local and fires the generator, then reschedules.

    python3 football/scheduler.py --out DIR            # foreground loop
    python3 football/scheduler.py --out DIR --daemon   # same, logs to file

Run it with shell_background so the harness keeps it alive.
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import shutil
import signal
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_HOUR = 7
DEFAULT_MINUTE = 0
MAX_SLEEP = 15 * 60          # wake at least every 15 min to re-check the clock


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", default=os.path.abspath(
        os.path.join(HERE, "..", "summaries")), help="output directory")
    p.add_argument("--hour", type=int, default=DEFAULT_HOUR)
    p.add_argument("--minute", type=int, default=DEFAULT_MINUTE)
    p.add_argument("--log", default=None, help="append run log to this file")
    p.add_argument("--max-runs", type=int, default=0,
                   help="stop after N runs (0 = forever); used by tests")
    p.add_argument("--run-now", action="store_true",
                   help="generate immediately, then continue on schedule")
    p.add_argument("--gh-token", default=None,
                   help="GitHub token for the daily push. Captured from "
                        "`gh auth token` if omitted and GH_TOKEN is unset; "
                        "exported into the generator's environment so the "
                        "in-process run can push to GitHub Pages.")
    p.add_argument("--backfill-days", type=int, default=14,
                   help="how many days back the self-healing check scans for "
                        "missed days (default 14)")
    return p.parse_args(argv)


def _resolve_gh_token(args) -> str | None:
    """Make a GitHub token available to the in-process generator.

    The generator cannot shell out to `gh` (W^X restriction), so the token
    must arrive via environment or a file. Preference: GH_TOKEN env var,
    then --gh-token, then `gh auth token` captured here by the harness shell
    tool (which *can* exec gh), written to .tmp_gh_token.
    """
    if os.environ.get("GH_TOKEN", "").strip():
        return os.environ["GH_TOKEN"]
    if args.gh_token:
        os.environ["GH_TOKEN"] = args.gh_token
        return args.gh_token
    try:
        tok = subprocess.run(["gh", "auth", "token"], capture_output=True,
                             text=True, check=True).stdout.strip()
    except Exception:
        tok = ""
    if not tok:
        f = os.path.abspath(os.path.join(HERE, "..", ".tmp_gh_token"))
        try:
            tok = open(f, encoding="utf-8").read().strip()
        except OSError:
            tok = ""
    if tok:
        os.environ["GH_TOKEN"] = tok
    return tok or None


def run_generator(args, logfile: str | None) -> int:
    """Generate one report.

    Runs the generator in-process: the harness toolchain binaries are
    W^X-restricted shims that plain subprocess() cannot exec, so importing
    and calling the generator is the only reliable path here.
    """
    gen_path = os.path.join(HERE, "generate_summary.py")
    log(f"generating report -> {args.out}", logfile)
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "_football_gen", gen_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        path = mod.run(["--out", args.out, "--push"])
        log(f"wrote {path}", logfile)
        return 0
    except Exception as exc:  # noqa: BLE001
        log(f"generator FAILED: {type(exc).__name__}: {exc}", logfile)
        return 1


def backfill_missed(args, logfile: str | None, today: dt.date | None = None
                    ) -> int:
    """Repair days the scheduled run missed (app killed / device asleep).

    The daemon only lives while the AndroidHarness app is alive, so a day can
    be silently skipped. This scans the output directory for gaps inside the
    backfill window and regenerates them in-process, pushing each one to
    GitHub Pages just like the scheduled run does. Returns the number of
    days repaired; 0 if nothing was missing.
    """
    import backfill as bf  # noqa: E402
    out = os.path.abspath(args.out)
    today = today or dt.date.today()
    missing, _ = bf.find_gaps(out, args.backfill_days, today)
    if not missing:
        return 0
    log(f"{len(missing)} missed day(s) to repair: "
        f"{', '.join(d.isoformat() for d in missing)}", logfile)
    repaired = 0
    for day in missing:
        stamp = day.strftime("%Y%m%d")
        log(f"backfilling {stamp} ...", logfile)
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location(
                "_football_gen_bf", os.path.join(HERE, "generate_summary.py"))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            mod.run(["--date", stamp, "--out", out, "--push"])
            repaired += 1
        except Exception as exc:  # noqa: BLE001 - one bad day must not abort
            log(f"backfill {stamp} FAILED: {type(exc).__name__}: {exc}",
                logfile)
    log(f"backfill done: {repaired}/{len(missing)} repaired", logfile)
    return repaired


def log(msg: str, logfile: str | None = None) -> None:
    line = f"[{dt.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    if logfile:
        try:
            with open(logfile, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError as exc:
            print(f"  ! cannot write log {logfile}: {exc}", flush=True)


def next_fire(now: dt.datetime, hour: int, minute: int) -> dt.datetime:
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += dt.timedelta(days=1)
    return target


def loop(args, stop: list[bool], sleep=time.sleep, now_fn=dt.datetime.now,
         run_gen=None) -> int:
    run_gen = run_gen or (lambda: run_generator(args, args.log))
    runs = 0
    if args.run_now:
        run_gen()
        runs += 1
        if args.max_runs and runs >= args.max_runs:
            log(f"reached --max-runs={args.max_runs}, exiting", args.log)
            return runs

    # Repair any days the previous run missed before waiting for the next
    # scheduled fire. The daemon only lives while the app is alive, so a day
    # can be skipped if the app was killed or the device slept through it.
    backfill_missed(args, args.log)

    fired_date: dt.date | None = None
    while not stop[0]:
        now = now_fn()
        target = next_fire(now, args.hour, args.minute)
        wait = (target - now).total_seconds()
        chunk = max(1.0, min(MAX_SLEEP, wait))
        sleep(chunk)
        now = now_fn()
        # The 15-minute wake cycle doubles as a self-healing check: if the
        # app was restarted after a gap, the first wake repairs it.
        backfill_missed(args, args.log, now.date())
        if now >= target and fired_date != now.date():
            fired_date = now.date()
            run_gen()
            runs += 1
            if args.max_runs and runs >= args.max_runs:
                log(f"reached --max-runs={args.max_runs}, exiting", args.log)
                break
    return runs


def main(argv=None) -> int:
    args = parse_args(argv)
    os.makedirs(args.out, exist_ok=True)
    stop = [False]

    def handle(signum, _frame):
        log(f"signal {signum} received, shutting down", args.log)
        stop[0] = True

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, handle)
        except (ValueError, OSError):
            pass

    tok = _resolve_gh_token(args)
    log(f"GitHub token {'available' if tok else 'MISSING'} for daily push",
        args.log)

    log(f"scheduler armed for {args.hour:02d}:{args.minute:02d} local, "
        f"output -> {args.out}", args.log)
    return loop(args, stop)


if __name__ == "__main__":
    sys.exit(main())

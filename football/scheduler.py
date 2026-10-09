#!/usr/bin/env python3
"""Daily scheduler: runs the football summary generator at 07:00 local time.

Android has no cron daemon, so this long-running loop computes the remaining
sleep to the next 07:00 local and fires the generator, then reschedules.

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
    return p.parse_args(argv)


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
        path = mod.run(["--out", args.out])
        log(f"wrote {path}", logfile)
        return 0
    except Exception as exc:  # noqa: BLE001
        log(f"generator FAILED: {type(exc).__name__}: {exc}", logfile)
        return 1


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

    fired_date: dt.date | None = None
    while not stop[0]:
        now = now_fn()
        target = next_fire(now, args.hour, args.minute)
        wait = (target - now).total_seconds()
        chunk = max(1.0, min(MAX_SLEEP, wait))
        sleep(chunk)
        now = now_fn()
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

    log(f"scheduler armed for {args.hour:02d}:{args.minute:02d} local, "
        f"output -> {args.out}", args.log)
    return loop(args, stop)


if __name__ == "__main__":
    sys.exit(main())

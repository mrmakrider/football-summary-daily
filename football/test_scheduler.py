#!/usr/bin/env python3
"""Tests for the daily scheduler (no real waiting, no network)."""
from __future__ import annotations

import datetime as dt
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import scheduler  # noqa: E402


class FakeClock:
    """Monotonic fake clock advanced by the sleep function."""

    def __init__(self, start: dt.datetime):
        self.current = start

    def __call__(self) -> dt.datetime:
        return self.current

    def now(self) -> dt.datetime:
        return self.current

    def sleep(self, seconds: float):
        self.current += dt.timedelta(seconds=seconds)


class TestNextFire(unittest.TestCase):

    def test_before_target_same_day(self):
        now = dt.datetime(2026, 9, 27, 3, 15)
        self.assertEqual(scheduler.next_fire(now, 7, 0),
                         dt.datetime(2026, 9, 27, 7, 0))

    def test_after_target_rolls_to_next_day(self):
        now = dt.datetime(2026, 9, 27, 7, 0, 1)
        self.assertEqual(scheduler.next_fire(now, 7, 0),
                         dt.datetime(2026, 9, 28, 7, 0))

    def test_exact_target_rolls_forward(self):
        """At exactly 07:00:00 we must not immediately re-fire the same slot."""
        now = dt.datetime(2026, 9, 27, 7, 0, 0)
        self.assertEqual(scheduler.next_fire(now, 7, 0),
                         dt.datetime(2026, 9, 28, 7, 0))

    def test_midnight_crossing(self):
        now = dt.datetime(2026, 9, 27, 23, 59, 30)
        self.assertEqual(scheduler.next_fire(now, 7, 0),
                         dt.datetime(2026, 9, 28, 7, 0))


class TestLoop(unittest.TestCase):

    def setUp(self):
        self.calls = []

    def _args(self, **kw):
        argv = ["--out", tempfile.mkdtemp(), "--max-runs", "1"]
        for k, v in kw.items():
            argv += [f"--{k.replace('_', '-')}", str(v)]
        return scheduler.parse_args(argv)

    def test_fires_at_0700(self):
        clock = FakeClock(dt.datetime(2026, 9, 27, 6, 55))
        args = self._args()
        scheduler.loop(args, [False], sleep=clock.sleep, now_fn=clock,
                       run_gen=lambda: self.calls.append(clock.now()))
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0].hour, 7)
        self.assertEqual(self.calls[0].minute, 0)

    def test_does_not_fire_early(self):
        clock = FakeClock(dt.datetime(2026, 9, 27, 6, 0))
        args = self._args()
        # max-runs 1 means it stops on first fire; guard with a stop flag that
        # trips if it fires before 07:00.
        stop = [False]

        def watch():
            if clock.now() < dt.datetime(2026, 9, 27, 7, 0):
                stop[0] = True
            self.calls.append(clock.now())

        scheduler.loop(args, stop, sleep=clock.sleep, now_fn=clock,
                       run_gen=watch)
        for t in self.calls:
            self.assertGreaterEqual(t, dt.datetime(2026, 9, 27, 7, 0))

    def test_fires_once_per_day(self):
        """Simulate two consecutive days without letting the loop end."""
        clock = FakeClock(dt.datetime(2026, 9, 27, 6, 59))
        args = scheduler.parse_args(["--out", tempfile.mkdtemp()])
        stop = [False]

        def gen():
            self.calls.append(clock.now())
            if len(self.calls) >= 2:
                stop[0] = True

        scheduler.loop(args, stop, sleep=clock.sleep, now_fn=clock,
                       run_gen=gen)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.calls[0].date(), dt.date(2026, 9, 27))
        self.assertEqual(self.calls[1].date(), dt.datetime(2026, 9, 28).date())

    def test_does_not_double_fire_same_slot(self):
        """Loop must not emit twice for one 07:00 even if polled often."""
        clock = FakeClock(dt.datetime(2026, 9, 27, 6, 59, 50))
        args = scheduler.parse_args(["--out", tempfile.mkdtemp()])
        stop = [False]
        # Stop from outside the callback: the callback is only allowed to fire.
        guard = {"count": 0}

        def gen():
            guard["count"] += 1
            self.calls.append(clock.now())

        real_sleep = clock.sleep

        def sleep_then_watch(sec):
            real_sleep(sec)
            if clock.now() >= dt.datetime(2026, 9, 27, 7, 30):
                stop[0] = True

        scheduler.loop(args, stop, sleep=sleep_then_watch, now_fn=clock,
                       run_gen=gen)
        self.assertEqual(guard["count"], 1, f"fired {guard['count']}x")

    def test_run_now_generates_immediately(self):
        clock = FakeClock(dt.datetime(2026, 9, 27, 6, 59))
        args = scheduler.parse_args(["--out", tempfile.mkdtemp(),
                                     "--run-now", "--max-runs", "1"])
        scheduler.loop(args, [False], sleep=clock.sleep, now_fn=clock,
                       run_gen=lambda: self.calls.append(clock.now()))
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0].hour, 6)

    def test_custom_hour_minute(self):
        clock = FakeClock(dt.datetime(2026, 9, 27, 4, 0))
        args = scheduler.parse_args(["--out", tempfile.mkdtemp(),
                                     "--hour", "5", "--minute", "30",
                                     "--max-runs", "1"])
        scheduler.loop(args, [False], sleep=clock.sleep, now_fn=clock,
                       run_gen=lambda: self.calls.append(clock.now()))
        self.assertEqual(self.calls[0], dt.datetime(2026, 9, 27, 5, 30))

    def test_sleep_chunks_are_bounded(self):
        """Long waits are chunked so clock changes are picked up."""
        seen = []
        clock = FakeClock(dt.datetime(2026, 9, 27, 0, 0))
        args = scheduler.parse_args(["--out", tempfile.mkdtemp(),
                                     "--max-runs", "1"])
        orig = clock.sleep

        def spy(sec):
            seen.append(sec)
            orig(sec)

        scheduler.loop(args, [False], sleep=spy, now_fn=clock,
                       run_gen=lambda: None)
        self.assertTrue(all(s <= scheduler.MAX_SLEEP for s in seen))


class TestRunGenerator(unittest.TestCase):

    def test_generates_real_file(self):
        """End-to-end: run_generator produces an HTML file via the network."""
        out = tempfile.mkdtemp(prefix="schedgen_")
        args = scheduler.parse_args(["--out", out])
        rc = scheduler.run_generator(args, None)
        self.assertEqual(rc, 0, "generator returned non-zero")
        files = [f for f in os.listdir(out)
                 if f.startswith("football_summary_") and f.endswith(".html")]
        self.assertTrue(files, f"no summary html in {os.listdir(out)}")
        body = open(os.path.join(out, files[0]), encoding="utf-8").read()
        self.assertIn("<!DOCTYPE html>", body)

    def test_failure_is_reported_and_logged(self):
        out = tempfile.mkdtemp(prefix="schedfail_")
        logfile = os.path.join(out, "run.log")
        # Make the output path impossible: a regular file where a parent
        # directory is required, so the generator must fail cleanly.
        blocker = os.path.join(out, "blocker")
        with open(blocker, "w", encoding="utf-8") as fh:
            fh.write("not a directory")
        args = scheduler.parse_args(["--out", os.path.join(blocker, "sub")])
        rc = scheduler.run_generator(args, logfile)
        self.assertNotEqual(rc, 0)
        self.assertIn("FAILED", open(logfile, encoding="utf-8").read())

    def test_log_file_is_appended(self):
        out = tempfile.mkdtemp(prefix="schedlog_")
        logfile = os.path.join(out, "run.log")
        args = scheduler.parse_args(["--out", out])
        scheduler.log("first line", logfile)
        scheduler.log("second line", logfile)
        body = open(logfile, encoding="utf-8").read()
        self.assertIn("first line", body)
        self.assertIn("second line", body)
        self.assertEqual(len(body.strip().splitlines()), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""Runner for the daily football summary scheduler.

Usage (start it detached so it survives leaving the app):

    python3 football/run_daily.py [--hour 7] [--minute 0]

Designed to be launched with the background shell of the harness.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import scheduler  # noqa: E402

if __name__ == "__main__":
    out = os.path.abspath(os.path.join(HERE, "..", "summaries"))
    argv = sys.argv[1:]
    if not any(a == "--out" for a in argv):
        argv += ["--out", out]
    sys.exit(scheduler.main(argv))

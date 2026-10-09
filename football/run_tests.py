#!/usr/bin/env python3
"""Run the whole football-summary test suite."""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

if __name__ == "__main__":
    suite = unittest.TestSuite()
    loader = unittest.TestLoader()
    for mod in ("tests", "test_scheduler"):
        suite.addTests(loader.loadTestsFromNames([mod]))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)

#!/usr/bin/env python3
"""Offline tests for the football summary pipeline.

Runs without network access: every HTTP call is served by a fake opener
backed by saved API payloads.
"""
from __future__ import annotations

import datetime as dt
import io
import tempfile
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import fotmob  # noqa: E402
import report  # noqa: E402
import generate_summary as gs  # noqa: E402
import backfill  # noqa: E402


# --------------------------------------------------------------------------
# Fake HTTP layer
# --------------------------------------------------------------------------
DAY_FIXTURE = {
    "leagues": [
        {"primaryId": 47, "ccode": "ENG", "name": "Premier League", "matches": [
            {"id": 1, "leagueId": 47, "time": "20.09.2026 15:30",
             "home": {"id": 10, "name": "Arsenal", "score": 3},
             "away": {"id": 11, "name": "Chelsea", "score": 1},
             "status": {"utcTime": "2026-09-20T14:30:00.000Z",
                        "finished": True, "started": True,
                        "cancelled": False, "awarded": False,
                        "scoreStr": "3 - 1",
                        "reason": {"short": "FT",
                                   "shortKey": "finished"}}}]},
        {"primaryId": 536, "ccode": "KSA", "name": "Saudi Pro League", "matches": [
            {"id": 2, "leagueId": 536, "time": "21.09.2026 19:00",
             "home": {"id": 20, "name": "Al Hilal", "score": None},
             "away": {"id": 21, "name": "Al Nassr", "score": None},
             "status": {"utcTime": "2026-09-21T16:00:00.000Z",
                        "finished": False, "started": False,
                        "cancelled": False, "awarded": False}}]},
        {"primaryId": 9999, "ccode": "FRA", "name": "Untracked League", "matches": [
            {"id": 3, "home": {"name": "A", "score": 1},
             "away": {"name": "B", "score": 0},
             "status": {"utcTime": "2026-09-20T14:30:00.000Z",
                        "finished": True, "started": True,
                        "reason": {"shortKey": "finished"}}}]},
    ]}

DETAILS_FIXTURE = {
    "content": {
        "matchFacts": {
            "events": {"events": [
                {"type": "Goal", "timeStr": "45 + 4", "isHome": True,
                 "nameStr": "Late Boy", "player": {"name": "Late Boy"},
                 "assistStr": "assist by Helper"},
                {"type": "Goal", "timeStr": 9, "isHome": False,
                 "nameStr": "Early Girl", "player": {"name": "Early Girl"}},
                {"type": "Goal", "timeStr": 12, "isHome": True,
                 "nameStr": "Own Man", "ownGoal": True,
                 "player": {"name": "Own Man"}},
                {"type": "Card", "timeStr": 20, "isHome": True,
                 "nameStr": "Cardy", "card": "Yellow"},
                {"type": "Substitution", "timeStr": 61, "isHome": False},
            ]},
            "infoBox": {"Stadium": {"name": "Emirates Stadium",
                                    "attendance": "60412"}},
        },
        "stats": {"Periods": {"All": {"stats": [
            {"title": "Top stats", "stats": [
                {"title": "Ball possession", "key": "bp", "stats": [64, 36]},
                {"title": "Total shots", "key": "ts", "stats": [20, 15]},
            ]}]}}},
    }}

LEAGUE_FIXTURE = {
    "table": [{"data": {
        "leagueName": "Premier League", "ccode": "ENG",
        "legend": [],
        "table": {"all": [
            {"name": "Arsenal", "idx": 1, "played": 5, "wins": 4, "draws": 1,
             "losses": 0, "scoresStr": "13-4", "goalConDiff": 9, "pts": 13,
             "qualColor": "#2AD572"},
            {"name": "Chelsea", "idx": 2, "played": 5, "wins": 3, "draws": 1,
             "losses": 1, "scoresStr": "10-6", "goalConDiff": 4, "pts": 10,
             "qualColor": None},
            {"name": "Liverpool", "idx": 3, "played": 5, "wins": 3,
             "draws": 1, "losses": 1, "scoresStr": "9-7", "goalConDiff": 2,
             "pts": 10, "qualColor": None},
        ]}}}]}


class FakeResponse:
    def __init__(self, payload):
        self._buf = io.BytesIO(json.dumps(payload).encode())
        self.headers = {}

    def read(self):
        return self._buf.read()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


SHOOTOUT_FIXTURE = {
    "header": {"status": {
        "utcTime": "2026-10-01T06:10:00.000Z",
        "finished": True, "started": True,
        "cancelled": False, "awarded": False,
        "scoreStr": "1 - 1",
        "reason": {"short": "Pen", "shortKey": "penalties_short",
                   "long": "Pen 3 - 5", "longKey": "afterpenalties",
                   "penalties": [3, 5]},
        "whoLostOnPenalties": "Panama", "whoLostOnAggregated": ""}},
    "content": {"matchFacts": {"events": {"events": [
        {"type": "Goal", "timeStr": 13, "isHome": False,
         "nameStr": "Stamenic", "player": {"name": "Stamenic"}}]}}},
}


def _shootout_match():
    """A normalised match finished on penalties, as the day feed reports it."""
    st = SHOOTOUT_FIXTURE["header"]["status"]
    return fotmob.normalise_match(
        {"id": 6315046, "home": {"id": 1, "name": "Panama", "score": 1},
         "away": {"id": 2, "name": "New Zealand", "score": 1},
         "status": st}, 114, "International Friendlies", "International")


class FakeOpener:
    """Serves the fixtures above and records every requested URL."""

    def __init__(self, fail_times=0):
        self.urls = []
        self.fail_times = fail_times

    def __call__(self, req, timeout=None):
        url = req.full_url
        self.urls.append(url)
        if self.fail_times > 0:
            self.fail_times -= 1
            raise OSError("simulated network error")
        if "matches?date=" in url:
            return FakeResponse(DAY_FIXTURE)
        if "matchDetails" in url:
            return FakeResponse(DETAILS_FIXTURE)
        if "leagues?id=" in url:
            return FakeResponse(LEAGUE_FIXTURE)
        raise AssertionError(f"unexpected URL: {url}")


def make_client(fail_times=0):
    opener = FakeOpener(fail_times)
    c = fotmob.Client(sleep=lambda _s: None, opener=opener, pause=0)
    return c, opener


# --------------------------------------------------------------------------
# Tests: data layer
# --------------------------------------------------------------------------
class TestFetching(unittest.TestCase):

    def test_collect_day_includes_all_competitions(self):
        c, _ = make_client()
        ms = fotmob.collect_day(c, "20260920")
        ids = {m["id"] for m in ms}
        self.assertIn(1, ids, "Premier League match missing")
        self.assertIn(2, ids, "Saudi match missing")
        self.assertIn(3, ids, "untracked league must now be included")
        # Regions are inferred for unknown leagues
        regions = {m["id"]: m["region"] for m in ms}
        self.assertEqual(regions[1], "Europe")
        self.assertEqual(regions[2], "MENA")
        self.assertEqual(regions[3], "Europe")

    def test_normalise_status_and_region(self):
        c, _ = make_client()
        ms = {m["id"]: m for m in fotmob.collect_day(c, "20260920")}
        self.assertEqual(ms[1]["status"], "FT")
        self.assertEqual(ms[1]["region"], "Europe")
        self.assertEqual(ms[1]["country"], "England")
        self.assertEqual(ms[2]["status"], "SCHEDULED")
        self.assertEqual(ms[2]["region"], "MENA")
        self.assertEqual(ms[2]["country"], "Saudi Arabia")
        self.assertIsNone(ms[2]["homeScore"])
        self.assertEqual(ms[3]["country"], "France")

    def test_scorers_sorted_and_cleaned(self):
        goals = fotmob.extract_scorers(DETAILS_FIXTURE)
        minutes = [g["minute"] for g in goals]
        self.assertEqual(minutes, [9, 12, "45 + 4"])
        self.assertEqual(goals[0]["player"], "Early Girl")
        self.assertEqual(goals[2]["player"], "Late Boy")
        self.assertEqual(goals[2]["assist"], "Helper")
        self.assertIn("(OG)", goals[1]["player"])

    def test_cards_and_subs_are_not_scorers(self):
        goals = fotmob.extract_scorers(DETAILS_FIXTURE)
        names = [g["player"] for g in goals]
        self.assertNotIn("Cardy", names)

    def test_minute_helpers(self):
        self.assertEqual(fotmob.minute_sort_key("45 + 4"), (45, 4))
        self.assertEqual(fotmob.minute_sort_key(9), (9, 0))
        self.assertEqual(fotmob.format_minute("45 + 4"), "45+4'")
        self.assertEqual(fotmob.format_minute(9), "9'")
        self.assertLess(fotmob.minute_sort_key("45 + 4"),
                        fotmob.minute_sort_key("46"))

    def test_stats_extracted(self):
        stats = fotmob.extract_stats(DETAILS_FIXTURE)
        self.assertIn(("Total shots", 20, 15), stats)
        self.assertIn(("Ball possession", 64, 36), stats)

    def test_standings_rows(self):
        c, _ = make_client()
        rows = fotmob.standings_rows(c.league(47), "all", limit=10)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["team"], "Arsenal")
        self.assertEqual(rows[0]["pts"], 13)
        self.assertEqual(rows[1]["qualColor"], None)

    def test_retry_then_succeed(self):
        c, opener = make_client(fail_times=2)
        d = c.day("20260920")
        self.assertIn("leagues", d)
        self.assertEqual(c.calls, 1)

    def test_retry_exhausted_raises_fetcherror(self):
        opener = FakeOpener(fail_times=99)
        c = fotmob.Client(sleep=lambda _s: None, opener=opener, retries=2)
        with self.assertRaises(fotmob.FetchError):
            c.day("20260920")

    def test_caching_avoids_duplicate_calls(self):
        c, opener = make_client()
        c.day("20260920")
        n = len(opener.urls)
        c.day("20260920")
        self.assertEqual(len(opener.urls), n)

    def test_penalty_shootout_is_not_plain_ft(self):
        # FotMob sends shortKey "penalties_short" (not "afterpenalties"), so
        # the old key list never matched and a shootout rendered as a 1-1 draw.
        m = _shootout_match()
        self.assertEqual(m["status"], "PEN")
        self.assertEqual(m["homeScore"], 1)
        self.assertEqual(m["awayScore"], 1)

    def test_extra_time_and_shootout_keys_both_map(self):
        def status_for(short_key, long_key=None):
            ev = {"home": {"name": "A", "score": 0},
                  "away": {"name": "B", "score": 0},
                  "status": {"finished": True, "started": True,
                             "reason": {"shortKey": short_key,
                                        "longKey": long_key or short_key}}}
            return fotmob._status(ev)
        self.assertEqual(status_for("penalties_short"), "PEN")
        self.assertEqual(status_for("afterpenalties"), "PEN")
        self.assertEqual(status_for("aet"), "AET")
        self.assertEqual(status_for("fulltime_short"), "FT")


# --------------------------------------------------------------------------
# Tests: rendering
# --------------------------------------------------------------------------
class TestRendering(unittest.TestCase):

    def setUp(self):
        self.c, _ = make_client()
        self.matches = [dict(m) for m in fotmob.collect_day(self.c, "20260920")]
        det = self.c.match_details(1)
        for m in self.matches:
            if m["id"] == 1:
                m["goals"] = fotmob.extract_scorers(det)
                m["stats"] = fotmob.extract_stats(det)
        self.standings = [({"name": "Premier League", "region": "Europe"},
                            fotmob.standings_rows(self.c.league(47)))]

    def render(self):
        now = dt.datetime(2026, 9, 21, 7, 0, tzinfo=dt.timezone.utc)
        return report.render_html(
            self.matches, self.standings, now, dt.timezone.utc,
            {"results": ["20260920"], "fixtures": ["20260921"]},
            ["FotMob test fixture"])

    def test_page_is_valid_html_skeleton(self):
        h = self.render()
        self.assertTrue(h.startswith("<!DOCTYPE html>"))
        self.assertIn("<meta name=\"viewport\"", h)
        self.assertIn("</html>", h)

    def test_all_four_sections_present(self):
        h = self.render()
        for heading in ("Results", "Match Reports", "Upcoming Fixtures",
                        "League Standings"):
            self.assertIn(heading, h)

    def test_results_include_score_and_scorers(self):
        h = self.render()
        self.assertIn("Arsenal", h)
        self.assertIn("3 &ndash; 1", h)
        self.assertIn("Late Boy", h)
        self.assertIn("45+4", h)
        self.assertIn("45+4&#x27;", h)
        self.assertIn("Total shots", h)

    def test_cards_show_country(self):
        h = self.render()
        self.assertIn("England", h)
        self.assertIn("Saudi Arabia", h)
        self.assertIn("France", h)

    def test_scorer_summary_folds_repeated_scorers(self):
        # A hat-trick must read "X 12, 40, 77", not "X, X, X".
        goals = [
            {"minute": "12'", "player": "Ace", "team": "home"},
            {"minute": "40'", "player": "Ace", "team": "home"},
            {"minute": "77'", "player": "Ace", "team": "home"},
            {"minute": "55'", "player": "Zed", "team": "away"},
        ]
        s = report._scorer_summary(goals)
        self.assertEqual(s, "Ace 12, 40, 77; Zed 55")
        self.assertEqual(s.count("Ace"), 1)

    def test_hat_trick_marked_on_card(self):
        goals = [
            {"minute": "12'", "player": "Ace", "team": "home", "assist": None},
            {"minute": "40'", "player": "Ace", "team": "home", "assist": None},
            {"minute": "77'", "player": "Ace", "team": "home", "assist": None},
        ]
        h = report._scorer_list(goals)
        self.assertIn('3&times;', h)
        self.assertEqual(h.count("badge tally"), 3)

    def test_single_goal_has_no_tally_badge(self):
        goals = [{"minute": "12'", "player": "Solo", "team": "home",
                  "assist": None}]
        self.assertNotIn("badge tally", report._scorer_list(goals))

    def test_score_rendered_exactly_once_per_card(self):
        # Regression: the score cell used to be emitted for the home row AND
        # the away row, so every result card showed the score twice.
        h = self.render()
        # Two completed matches (IDs 1 and 3) => two scoreboxes, one per card.
        self.assertEqual(h.count('<span class="scorebox final">'), 2)
        self.assertEqual(h.count("3 &ndash; 1"), 1)
        self.assertEqual(h.count("1 &ndash; 0"), 1)
        self.assertNotIn('<div class="teams">', h)

    def test_long_team_names_keep_score_on_one_line(self):
        # The score must share a column grid with the names so that a long
        # club name cannot push it onto a second line.
        ms = [dict(self.matches[0],
                   home="Manchester United Football Club",
                   away="AFC Bournemouth")]
        now = dt.datetime(2026, 9, 21, 7, 0, tzinfo=dt.timezone.utc)
        h = report.render_html(ms, [], now, dt.timezone.utc, {}, ["s"])
        box = h.split('<span class="scorebox final">')[1].split("</span></span>")[0]
        # home name, score, away name -> exactly three grid children
        self.assertEqual(box.count('<span class="tname">'), 2)
        self.assertEqual(box.count('<span class="sc">'), 1)

    def test_upcoming_respects_24h_clock_window(self):
        # A SCHEDULED match outside now+24h must not be listed as upcoming.
        base = self.matches[0]
        soon = dict(base, id=9001, status="SCHEDULED", home="Soon FC",
                    away="Imminent FC", homeScore=None, awayScore=None,
                    utc="2026-09-21T20:00:00.000Z")   # +13h
        later = dict(base, id=9002, status="SCHEDULED", home="Later FC",
                     away="Aftermath FC", homeScore=None, awayScore=None,
                     utc="2026-09-23T20:00:00.000Z")   # +61h
        past = dict(base, id=9003, status="SCHEDULED", home="Past FC",
                    away="Yesterday FC", homeScore=None, awayScore=None,
                    utc="2026-09-19T20:00:00.000Z")    # -35h
        now = dt.datetime(2026, 9, 21, 7, 0, tzinfo=dt.timezone.utc)
        h = report.render_html([soon, later, past], [], now,
                               dt.timezone.utc, {}, ["s"])
        fx = h.split("Upcoming Fixtures")[1].split("League Standings")[0]
        self.assertIn("Soon FC", fx)
        self.assertNotIn("Later FC", fx)
        self.assertNotIn("Yesterday FC", fx)

    def test_upcoming_empty_state_states_the_window(self):
        h = report.render_html([], [], dt.datetime(2026, 9, 21, 7, 0,
                                                   tzinfo=dt.timezone.utc),
                               dt.timezone.utc, {}, ["s"])
        self.assertIn("Mon 21 Sep 07:00", h)
        self.assertIn("Tue 22 Sep 07:00", h)

    def test_upcoming_fixture_rendered_without_score(self):
        # Fixtures live in their own table, so no scoreline should be printed.
        h = self.render()
        self.assertIn("Al Hilal", h)
        self.assertIn("Al Nassr", h)
        fx_section = h.split("Upcoming Fixtures")[1].split("League Standings")[0]
        self.assertNotIn("score", fx_section)
        self.assertIn("Al Hilal <span class=\"muted\">vs</span> Al Nassr", fx_section)

    def test_standings_values_rendered(self):
        h = self.render()
        self.assertIn("Liverpool", h)
        self.assertIn("13-4", h)

    def test_escapes_html_in_team_names(self):
        ms = [dict(self.matches[0], home="<script>x</script>", away="B",
                   status="SCHEDULED", homeScore=None, awayScore=None,
                   utc="2026-09-21T16:00:00.000Z")]
        now = dt.datetime(2026, 9, 21, 7, 0, tzinfo=dt.timezone.utc)
        h = report.render_html(ms, [], now, dt.timezone.utc, {}, ["s"])
        self.assertNotIn("<script>x</script>", h)
        self.assertIn("&lt;script&gt;", h)

    def test_insight_text_keeps_club_casing(self):
        m = {"home": "Manchester City", "away": "Everton",
             "homeScore": 3, "awayScore": 0,
             "stats": [("Total shots", 15, 4)]}
        text = report._insight(m)
        self.assertIn("Manchester City", text)
        self.assertIn("Everton", text)
        # No sentence should start with a lower-cased club name.
        for sent in text.split(". "):
            self.assertTrue(
                sent[0].isupper() or sent[0].isdigit(),
                f"sentence not capitalised: {sent!r}")

    def test_insight_for_goalless_draw(self):
        text = report._insight({"home": "A", "away": "B", "homeScore": 0,
                                "awayScore": 0, "stats": []})
        self.assertIn("Honours were even", text)
        self.assertIn("goalless", text)

    def test_insight_for_big_win(self):
        text = report._insight({"home": "A", "away": "B", "homeScore": 5,
                                "awayScore": 0, "stats": []})
        self.assertIn("comprehensive", text)

    def test_shootout_card_shows_penalty_outcome(self):
        m = _shootout_match()
        m["penalties"] = [3, 5]
        m["lostOnPenalties"] = "Panama"
        now = dt.datetime(2026, 10, 1, 12, 0, tzinfo=dt.timezone.utc)
        h = report.render_html([m], [], now, dt.timezone.utc, {}, ["s"])
        self.assertIn('class="badge pen"', h)
        self.assertIn('class="pens"', h)
        # The shootout score must be visible, not lost behind a 1 - 1 draw,
        # and the side that lost it must be named.
        self.assertIn("3 &ndash; 5", h)
        self.assertIn("New Zealand", h)
        self.assertIn("on penalties", h)

    def test_shootout_counts_as_completed_not_live(self):
        m = _shootout_match()
        now = dt.datetime(2026, 10, 1, 12, 0, tzinfo=dt.timezone.utc)
        h = report.render_html([m], [], now, dt.timezone.utc, {}, ["s"])
        self.assertIn("1 completed, 0 live", h)

    def test_cancelled_match_is_not_reported_as_a_result(self):
        # A cancelled fixture arrives with 0-0 and finished=False; it must not
        # be counted as a completed match or rendered with a scoreline.
        m = dict(self.matches[0], id=7, status="Cancelled",
                 home="Sri Lanka", away="Djibouti", homeScore=0, awayScore=0)
        now = dt.datetime(2026, 9, 21, 7, 0, tzinfo=dt.timezone.utc)
        h = report.render_html([m], [], now, dt.timezone.utc, {}, ["s"])
        self.assertIn("0 completed, 0 live", h)
        self.assertNotIn("Sri Lanka", h)

    def test_insight_mentions_winning_margin(self):
        text = report._insight({"home": "A", "away": "B", "homeScore": 5,
                                "awayScore": 0, "stats": []})
        self.assertIn("5-goal victory over B", text)

    def test_empty_state_renders(self):
        now = dt.datetime(2026, 9, 21, 7, 0, tzinfo=dt.timezone.utc)
        h = report.render_html([], [], now, dt.timezone.utc, {}, ["s"])
        self.assertIn("No completed matches", h)
        self.assertIn("No fixtures scheduled", h)
        self.assertIn("Standings could not be loaded", h)

    def test_timezone_conversion_applied(self):
        now = dt.datetime(2026, 9, 21, 7, 0, tzinfo=dt.timezone.utc)
        tz = dt.timezone(dt.timedelta(hours=3))
        h = report.render_html(self.matches, self.standings, now, tz, {}, ["s"])
        self.assertIn("17:30", h)  # 14:30 UTC -> 17:30 +03


# --------------------------------------------------------------------------
# Tests: end-to-end generate_summary (offline)
# --------------------------------------------------------------------------
class TestPipeline(unittest.TestCase):

    def test_run_writes_file_and_json(self):
        import tempfile
        opener = FakeOpener()
        orig = fotmob.Client

        def patched(*a, **kw):
            kw["opener"] = opener
            kw["sleep"] = lambda _s: None
            return orig(*a, **kw)

        fotmob.Client = patched
        gs.Client = patched
        try:
            with tempfile.TemporaryDirectory() as tmp:
                path = gs.run(["--date", "20260920", "--out", tmp,
                               "--standings", "2"])
                self.assertTrue(os.path.exists(path))
                # The file is named after the reporting day (--date), not the
                # wall clock, so a backfill never overwrites a newer report.
                self.assertTrue(path.endswith("football_summary_20260920.html"),
                                f"wrong filename: {path}")
                with open(path, encoding="utf-8") as fh:
                    html = fh.read()
                self.assertIn("Arsenal", html)
                # The fake opener only serves 20260920/21, so a run anchored
                # on 2026-09-27 must not invent fixtures for the next 24h.
                self.assertNotIn("Al Hilal", html)
                self.assertEqual(len(opener.urls) > 0, True)
        finally:
            fotmob.Client = orig
            gs.Client = orig

    def test_collect_all_window_dedupes(self):
        c, _ = make_client()
        anchor = dt.date(2026, 9, 21)
        matches, windows = gs.collect_all(c, anchor, enrich=False)
        ids = [m["id"] for m in matches]
        self.assertEqual(len(ids), len(set(ids)), "duplicate match ids")
        self.assertIn("fixtures", windows)
        self.assertIn("results", windows)


# --------------------------------------------------------------------------
# Tests: backfill gap detection
# --------------------------------------------------------------------------
class TestBackfill(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def _write_day(self, stamp: str):
        with open(os.path.join(self.out, f"football_summary_{stamp}.html"),
                  "w", encoding="utf-8") as fh:
            fh.write("<html></html>")

    def test_existing_days_are_detected(self):
        for stamp in ("20260928", "20260929", "20260930"):
            self._write_day(stamp)
        have = backfill.existing_days(self.out)
        self.assertEqual(have, {dt.date(2026, 9, 28), dt.date(2026, 9, 29),
                                dt.date(2026, 9, 30)})

    def test_unrelated_files_are_ignored(self):
        self._write_day("20260930")
        for junk in ("notes.txt", "football_summary_2026.html",
                     "football_summary_abcdefgh.html", "index.html"):
            with open(os.path.join(self.out, junk), "w") as fh:
                fh.write("x")
        have = backfill.existing_days(self.out)
        self.assertEqual(have, {dt.date(2026, 9, 30)})

    def test_gaps_are_reported_oldest_first(self):
        self._write_day("20260929")
        today = dt.date(2026, 9, 30)
        missing, _ = backfill.find_gaps(self.out, 3, today)
        self.assertEqual(missing, [dt.date(2026, 9, 28), dt.date(2026, 9, 30)])

    def test_no_gaps_when_every_day_present(self):
        for stamp in ("20260928", "20260929", "20260930"):
            self._write_day(stamp)
        missing, _ = backfill.find_gaps(self.out, 3, dt.date(2026, 9, 30))
        self.assertEqual(missing, [])

    def test_backfilled_page_is_titled_with_its_own_date(self):
        # A backfill renders a past day today; the heading must still name the
        # day being reported, not the day the file happened to be written.
        now = dt.datetime(2026, 10, 1, 2, 0, tzinfo=dt.timezone.utc)
        h = report.render_html([], [], now, dt.timezone.utc, {}, ["s"],
                               report_date=dt.date(2026, 9, 30))
        self.assertIn("30 September 2026", h)
        self.assertNotIn(">01 October 2026", h)

    def test_default_report_date_is_today(self):
        now = dt.datetime(2026, 10, 1, 2, 0, tzinfo=dt.timezone.utc)
        h = report.render_html([], [], now, dt.timezone.utc, {}, ["s"])
        self.assertIn("01 October 2026", h)

    def test_force_targets_every_day_in_the_window(self):
        # --force must rebuild present days too, so a rendered page can be
        # refreshed after a rendering fix.
        self._write_day("20260929")
        today = dt.date(2026, 9, 30)
        missing, _ = backfill.find_gaps(self.out, 3, today)
        self.assertEqual(missing, [dt.date(2026, 9, 28), dt.date(2026, 9, 30)])
        rc = backfill.main(["--out", self.out, "--days", "3", "--force",
                            "--dry-run"])
        self.assertEqual(rc, 0)

    def test_dry_run_writes_nothing(self):
        today = dt.date.today()
        stamp = (today - dt.timedelta(days=2)).strftime("%Y%m%d")
        rc = backfill.main(["--out", self.out, "--days", "3",
                            "--dry-run"])
        self.assertEqual(rc, 0)
        self.assertFalse(
            os.path.exists(os.path.join(self.out, f"football_summary_{stamp}.html")),
            "--dry-run must not create any report")

    def test_no_gaps_exits_cleanly(self):
        today = dt.date.today()
        for i in range(3):
            self._write_day((today - dt.timedelta(days=i)).strftime("%Y%m%d"))
        rc = backfill.main(["--out", self.out, "--days", "3", "--dry-run"])
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""FotMob data client used by the daily football summary generator.

Public JSON endpoints, accessed with browser-like headers (the CDN rejects
requests with a bare/curl User-Agent). No API key required.
"""
from __future__ import annotations

import gzip
import json
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Iterable

BASE = "https://www.fotmob.com/api/data"

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

HEADERS = {
    "User-Agent": UA,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.fotmob.com/",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-site",
    "Sec-Fetch-Dest": "empty",
}

# Target competitions. `region` is used for grouping in the report.
LEAGUES: list[dict[str, Any]] = [
    # Europe - club
    {"id": 47, "name": "Premier League", "region": "Europe"},
    {"id": 87, "name": "LaLiga", "region": "Europe"},
    {"id": 55, "name": "Serie A", "region": "Europe"},
    {"id": 54, "name": "Bundesliga", "region": "Europe"},
    {"id": 53, "name": "Ligue 1", "region": "Europe"},
    {"id": 42, "name": "Champions League", "region": "Europe"},
    {"id": 73, "name": "Europa League", "region": "Europe"},
    {"id": 10615, "name": "Conference League (Qual./League Phase)",
     "region": "Europe"},
    # Europe - national teams
    {"id": 9806, "name": "UEFA Nations League A", "region": "Europe"},
    {"id": 9807, "name": "UEFA Nations League B", "region": "Europe"},
    {"id": 9808, "name": "UEFA Nations League C", "region": "Europe"},
    {"id": 9809, "name": "UEFA Nations League D", "region": "Europe"},
    {"id": 10195, "name": "World Cup Qualification UEFA", "region": "Europe"},
    # MENA - club
    {"id": 536, "name": "Saudi Pro League", "region": "MENA"},
    {"id": 538, "name": "UAE Pro League", "region": "MENA"},
    {"id": 535, "name": "Qatar Stars League", "region": "MENA"},
    {"id": 519, "name": "Egyptian Premier League", "region": "MENA"},
    {"id": 530, "name": "Moroccan Botola Pro", "region": "MENA"},
    {"id": 544, "name": "Tunisia Ligue Professionnelle 1", "region": "MENA"},
    # MENA - national teams
    {"id": 10608, "name": "Africa Cup of Nations Qualification", "region": "MENA"},
    {"id": 10196, "name": "World Cup Qualification CAF", "region": "MENA"},
    {"id": 10609, "name": "Asian Cup Qualification", "region": "MENA"},
    {"id": 10197, "name": "World Cup Qualification AFC", "region": "MENA"},
    {"id": 10198, "name": "World Cup Qualification CONCACAF", "region": "MENA"},
    {"id": 523, "name": "Persian Gulf Pro League", "region": "MENA"},
    {"id": 329, "name": "Arabian Gulf Cup (Khaleeji)", "region": "MENA"},
    # International friendlies (filtered to our regions by the data)
    {"id": 114, "name": "International Friendlies", "region": "International"},
]

LEAGUE_BY_ID = {lg["id"]: lg for lg in LEAGUES}


class FetchError(RuntimeError):
    pass


class Client:
    def __init__(self, timeout: int = 25, retries: int = 3,
                 pause: float = 0.25, sleep=time.sleep, opener=None):
        self.timeout = timeout
        self.retries = retries
        self.pause = pause
        self._sleep = sleep
        self._open = opener or urllib.request.urlopen
        self.calls = 0
        self.cache: dict[str, Any] = {}

    def _get(self, path: str, params: dict | None = None) -> Any:
        url = f"{BASE}/{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        if url in self.cache:
            return self.cache[url]
        last: Exception | None = None
        for attempt in range(self.retries):
            try:
                req = urllib.request.Request(url, headers=HEADERS)
                with self._open(req, timeout=self.timeout) as resp:
                    raw = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                data = json.loads(raw.decode("utf-8", "replace"))
                self.calls += 1
                self.cache[url] = data
                self._sleep(self.pause)
                return data
            except Exception as exc:  # noqa: BLE001 - retried below
                last = exc
                if attempt < self.retries - 1:
                    self._sleep((1.0 + attempt) * (0.7 + random.random() * 0.6))
        raise FetchError(f"{url}: {last}")

    # ---- public endpoints -------------------------------------------------
    def day(self, yyyymmdd: str) -> dict:
        return self._get("matches", {"date": yyyymmdd}) or {}

    def match_details(self, match_id: int | str) -> dict:
        return self._get("matchDetails", {"matchId": match_id}) or {}

    def league(self, league_id: int) -> dict:
        return self._get("leagues", {"id": league_id}) or {}


# ---- normalisation helpers ------------------------------------------------

def minute_sort_key(value) -> tuple[int, int]:
    """Sort '45', '45+4', '90+8' chronologically (stoppage after regulation)."""
    import re
    m = re.match(r"^\s*(\d+)(?:\s*\+\s*(\d+))?", str(value or ""))
    if not m:
        return (999, 999)
    base = int(m.group(1))
    extra = int(m.group(2) or 0)
    return (base, extra)


def format_minute(value) -> str:
    """Render FotMob's '45 + 4' as a compact '45+4'."""
    import re
    m = re.match(r"^\s*(\d+)\s*(?:\+\s*(\d+))?", str(value or ""))
    if not m:
        return str(value or "")
    return f"{m.group(1)}+{m.group(2)}'" if m.group(2) else f"{m.group(1)}'"


def _status(ev: dict) -> str:
    st = ev.get("status") or {}
    reason = (st.get("reason") or {}).get("shortKey") or ""
    if st.get("cancelled"):
        return "Cancelled"
    # FotMob uses "penalties_short" in reason.shortKey and "afterpenalties"
    # in reason.longKey; both mean the tie was settled from the shootout.
    if reason in ("penalties_short", "afterpenalties"):
        return "PEN"
    if reason in ("aet", "afterextra", "extra_time"):
        return "AET"
    if reason in ("finished", "fulltime", "fulltime_short"):
        return "FT"
    if st.get("finished"):
        # Fall back to the long key before giving up on the status: a day
        # feed that omits shortKey still says "afterpenalties"/"aet" there.
        long_key = (st.get("reason") or {}).get("longKey") or ""
        if long_key == "afterpenalties":
            return "PEN"
        if long_key in ("aet", "afterextra"):
            return "AET"
        return "FT"
    if st.get("started"):
        return "LIVE"
    return "SCHEDULED"


def normalise_match(ev: dict, league_id: int, league_name: str,
                    league_region: str) -> dict:
    st = ev.get("status") or {}
    utc = st.get("utcTime")
    home = ev.get("home") or {}
    away = ev.get("away") or {}
    return {
        "id": ev.get("id"),
        "leagueId": league_id,
        "league": league_name,
        "region": league_region,
        "utc": utc,
        "home": home.get("name") or home.get("longName") or "Home",
        "away": away.get("name") or away.get("longName") or "Away",
        "homeId": home.get("id"),
        "awayId": away.get("id"),
        "homeScore": home.get("score"),
        "awayScore": away.get("score"),
        "status": _status(ev),
        "scoreStr": st.get("scoreStr") or "",
    }


def collect_day(client: Client, yyyymmdd: str) -> list[dict]:
    """Return normalised matches for the given date, filtered to target leagues."""
    data = client.day(yyyymmdd)
    out: list[dict] = []
    for lg in data.get("leagues") or []:
        lid = lg.get("primaryId")
        meta = LEAGUE_BY_ID.get(lid)
        if not meta:
            continue
        for ev in lg.get("matches") or []:
            out.append(normalise_match(ev, meta["id"], meta["name"],
                                       meta["region"]))
    out.sort(key=lambda m: (m["utc"] or "", m["league"], m["home"]))
    return out


def extract_scorers(details: dict) -> list[dict]:
    events = (((details.get("content") or {}).get("matchFacts") or {})
              .get("events") or {})
    goals: list[dict] = []
    for e in events.get("events") or []:
        if e.get("type") != "Goal":
            continue
        p = e.get("player") or {}
        name = e.get("nameStr") or p.get("name") or "Unknown"
        if e.get("ownGoal"):
            name = f"{name} (OG)"
        goals.append({
            "minute": e.get("timeStr"),
            "team": "home" if e.get("isHome") else "away",
            "player": name,
            "assist": (e.get("assistStr") or "").replace("assist by ", "").strip()
                       or None,
            "penalty": bool(e.get("isPenaltyShootoutEvent")) or None,
        })
    goals.sort(key=lambda g: (minute_sort_key(g["minute"]), g["team"]))
    return goals


def extract_stats(details: dict) -> list[tuple[str, int | None, int | None]]:
    stats = ((details.get("content") or {}).get("stats") or {}).get("Periods") or {}
    allp = stats.get("All") or {}
    out: list[tuple[str, int | None, int | None]] = []
    for group in allp.get("stats") or []:
        for s in group.get("stats") or []:
            v = s.get("stats") or []
            if len(v) == 2:
                out.append((s.get("title") or s.get("key"),
                            v[0], v[1]))
    return out[:10]


def extract_standings(league_data: dict) -> dict:
    tabs = league_data.get("table") or []
    if not tabs:
        return {}
    d = tabs[0].get("data") or {}
    tbl = d.get("table") or {}
    groups = []
    for key, label in (("all", "all"), ("home", "home"), ("away", "away")):
        rows = tbl.get(key) or []
        if rows:
            groups.append((label, rows))
    return {
        "league": d.get("leagueName"),
        "ccode": d.get("ccode"),
        "pageUrl": d.get("pageUrl"),
        "legend": d.get("legend") or [],
        "groups": groups,
    }


def standings_rows(league_data: dict, group: str = "all",
                    limit: int = 10) -> list[dict]:
    tabs = league_data.get("table") or []
    if not tabs:
        return []
    tbl = (tabs[0].get("data") or {}).get("table") or {}
    rows = tbl.get(group) or []
    out = []
    for r in rows[:limit]:
        out.append({
            "pos": r.get("idx"),
            "team": r.get("name"),
            "short": r.get("shortName"),
            "played": r.get("played"),
            "wins": r.get("wins"),
            "draws": r.get("draws"),
            "losses": r.get("losses"),
            "gf": r.get("scoresStr"),
            "gd": r.get("goalConDiff"),
            "pts": r.get("pts"),
            "qualColor": r.get("qualColor"),
        })
    return out


def group_by_region(matches: Iterable[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {"Europe": [], "MENA": []}
    for m in matches:
        out.setdefault(m["region"], []).append(m)
    return out

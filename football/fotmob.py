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
# The day feed returns ~145 leagues; collect_day() is now inclusive so every
# European / MENA competition that appears gets captured even if it is not
# listed here.  LEAGUES still drives (a) explicit region overrides and
# (b) which competitions get standings tables fetched.
LEAGUES: list[dict[str, Any]] = [
    # ---- Europe: top-flight club leagues ----
    {"id": 47, "name": "Premier League", "region": "Europe"},
    {"id": 87, "name": "LaLiga", "region": "Europe"},
    {"id": 55, "name": "Serie A", "region": "Europe"},
    {"id": 54, "name": "Bundesliga", "region": "Europe"},
    {"id": 53, "name": "Ligue 1", "region": "Europe"},
    {"id": 57, "name": "Eredivisie", "region": "Europe"},
    {"id": 61, "name": "Liga Portugal", "region": "Europe"},
    {"id": 71, "name": "Süper Lig", "region": "Europe"},
    {"id": 64, "name": "Scottish Premiership", "region": "Europe"},
    {"id": 40, "name": "Belgian Pro League", "region": "Europe"},
    {"id": 51, "name": "Swiss Super League", "region": "Europe"},
    {"id": 50, "name": "Austrian Bundesliga", "region": "Europe"},
    {"id": 46, "name": "Danish Superliga", "region": "Europe"},
    {"id": 67, "name": "Allsvenskan", "region": "Europe"},
    {"id": 59, "name": "Norwegian Eliteserien", "region": "Europe"},
    {"id": 43, "name": "Finnish Veikkausliiga", "region": "Europe"},
    {"id": 58, "name": "Greek Super League", "region": "Europe"},
    {"id": 60, "name": "Czech First League", "region": "Europe"},
    {"id": 196, "name": "Polish Ekstraklasa", "region": "Europe"},
    {"id": 135, "name": "Romanian Liga 1", "region": "Europe"},
    {"id": 252, "name": "Croatian HNL", "region": "Europe"},
    {"id": 231, "name": "Serbian SuperLiga", "region": "Europe"},
    {"id": 133, "name": "Bulgarian First League", "region": "Europe"},
    {"id": 270, "name": "Ukrainian Premier League", "region": "Europe"},
    {"id": 212, "name": "Hungarian NB I", "region": "Europe"},
    {"id": 173, "name": "Slovenian PrvaLiga", "region": "Europe"},
    {"id": 229, "name": "Bosnian Premier League", "region": "Europe"},
    {"id": 338, "name": "Macedonian First League", "region": "Europe"},
    {"id": 260, "name": "Albanian Superliga", "region": "Europe"},
    {"id": 215, "name": "Icelandic Úrvalsdeild", "region": "Europe"},
    {"id": 226, "name": "Latvian Virsliga", "region": "Europe"},
    {"id": 66, "name": "Lithuanian A Lyga", "region": "Europe"},
    {"id": 248, "name": "Estonian Meistriliiga", "region": "Europe"},
    {"id": 78, "name": "Faroe Islands Premier League", "region": "Europe"},
    {"id": 250, "name": "Welsh Premier League", "region": "Europe"},
    {"id": 262, "name": "Northern Irish Premiership", "region": "Europe"},
    # ---- Europe: second divisions & cups ----
    {"id": 82, "name": "English Championship", "region": "Europe"},
    {"id": 83, "name": "English League One", "region": "Europe"},
    {"id": 84, "name": "English League Two", "region": "Europe"},
    {"id": 85, "name": "English National League", "region": "Europe"},
    {"id": 140, "name": "LaLiga2", "region": "Europe"},
    {"id": 8968, "name": "Primera Federación", "region": "Europe"},
    {"id": 9138, "name": "Segunda Federación", "region": "Europe"},
    {"id": 146, "name": "2. Bundesliga", "region": "Europe"},
    {"id": 208, "name": "3. Liga", "region": "Europe"},
    {"id": 86, "name": "Serie B", "region": "Europe"},
    {"id": 147, "name": "Serie C", "region": "Europe"},
    {"id": 185, "name": "Liga Portugal 2", "region": "Europe"},
    {"id": 197, "name": "Polish I Liga", "region": "Europe"},
    {"id": 8974, "name": "J. League 2", "region": "Europe"},
    # ---- Europe: continental club competitions ----
    {"id": 42, "name": "UEFA Champions League", "region": "International"},
    {"id": 73, "name": "UEFA Europa League", "region": "International"},
    {"id": 10615, "name": "UEFA Conference League", "region": "International"},
    # ---- Europe: national teams ----
    {"id": 9806, "name": "UEFA Nations League A", "region": "International"},
    {"id": 9807, "name": "UEFA Nations League B", "region": "International"},
    {"id": 9808, "name": "UEFA Nations League C", "region": "International"},
    {"id": 9809, "name": "UEFA Nations League D", "region": "International"},
    # ---- MENA: top-flight club leagues ----
    {"id": 536, "name": "Saudi Pro League", "region": "MENA"},
    {"id": 538, "name": "UAE Pro League", "region": "MENA"},
    {"id": 535, "name": "Qatar Stars League", "region": "MENA"},
    {"id": 519, "name": "Egyptian Premier League", "region": "MENA"},
    {"id": 530, "name": "Moroccan Botola Pro", "region": "MENA"},
    {"id": 544, "name": "Tunisian Ligue 1", "region": "MENA"},
    {"id": 9372, "name": "Iranian Azadegan League", "region": "MENA"},
    {"id": 523, "name": "Persian Gulf Pro League", "region": "MENA"},
    {"id": 127, "name": "Israeli Ligat ha'Al", "region": "MENA"},
    {"id": 176, "name": "Jordanian Pro League", "region": "MENA"},
    {"id": 273, "name": "Omani League", "region": "MENA"},
    {"id": 335, "name": "Bahraini Premier League", "region": "MENA"},
    {"id": 337, "name": "Kuwaiti Premier League", "region": "MENA"},
    {"id": 199, "name": "Lebanese Premier League", "region": "MENA"},
    {"id": 274, "name": "Iraqi Premier League", "region": "MENA"},
    {"id": 249, "name": "Syrian Premier League", "region": "MENA"},
    {"id": 339, "name": "Palestinian Premier League", "region": "MENA"},
    {"id": 533, "name": "Sudan Premier League", "region": "MENA"},
    {"id": 336, "name": "Libyan Premier League", "region": "MENA"},
    # ---- MENA: cups & second tiers ----
    {"id": 9490, "name": "MENA Cup", "region": "MENA"},
    {"id": 10195, "name": "World Cup Qualification UEFA", "region": "Europe"},
    # MENA - national teams
    {"id": 10608, "name": "Africa Cup of Nations Qualification", "region": "MENA"},
    {"id": 10196, "name": "World Cup Qualification CAF", "region": "MENA"},
    {"id": 10609, "name": "Asian Cup Qualification", "region": "MENA"},
    {"id": 10197, "name": "World Cup Qualification AFC", "region": "MENA"},
    {"id": 10198, "name": "World Cup Qualification CONCACAF", "region": "MENA"},
    # International friendlies (filtered to our regions by the data)
    {"id": 114, "name": "International Friendlies", "region": "International"},
]

LEAGUE_BY_ID = {lg["id"]: lg for lg in LEAGUES}

# MENA country keywords used to infer region for leagues not in LEAGUE_BY_ID.
MENA_KEYWORDS = (
    "saudi", "emirati", "uae", "qatar", "egypt", "egyptian", "morocco",
    "moroccan", "tunisia", "tunisian", "algeria", "algerian", "jordan",
    "oman", "bahrain", "kuwait", "lebanon", "iraqi", "iraq", "palestine",
    "syria", "yemen", "libya", "libyan", "sudan", "israel", "israeli",
    "iranian", "persian gulf", "azadegan",
)

# ISO 3166-1 alpha-3 -> English country name, covering every ccode that
# appears in FotMob league feeds (Europe, MENA and beyond).
COUNTRY_NAMES: dict[str, str] = {
    "AFG": "Afghanistan", "ALB": "Albania", "ALG": "Algeria",
    "AND": "Andorra", "ANG": "Angola", "ARG": "Argentina",
    "ARM": "Armenia", "AUS": "Australia", "AUT": "Austria",
    "AZE": "Azerbaijan", "BHR": "Bahrain", "BDI": "Burundi",
    "BEL": "Belgium", "BEN": "Benin", "BLR": "Belarus",
    "BIH": "Bosnia and Herzegovina", "BOL": "Bolivia", "BOT": "Botswana",
    "BRA": "Brazil", "BRU": "Brunei", "BUL": "Bulgaria",
    "BFA": "Burkina Faso", "CAM": "Cambodia", "CAN": "Canada",
    "CHI": "Chile", "CHN": "China", "COL": "Colombia",
    "CRC": "Costa Rica", "CRO": "Croatia", "CUB": "Cuba",
    "CYP": "Cyprus", "CZE": "Czechia", "COD": "DR Congo",
    "DEN": "Denmark", "DJI": "Djibouti", "DOM": "Dominican Republic",
    "ECU": "Ecuador", "EGY": "Egypt", "ENG": "England",
    "EQG": "Equatorial Guinea", "ERI": "Eritrea", "EST": "Estonia",
    "ESW": "Eswatini", "ETH": "Ethiopia", "FRO": "Faroe Islands",
    "FIN": "Finland", "FRA": "France", "GAB": "Gabon",
    "GAM": "Gambia", "GEO": "Georgia", "GER": "Germany",
    "GHA": "Ghana", "GRE": "Greece", "GLP": "Guadeloupe",
    "GUA": "Guatemala", "GUI": "Guinea", "GBS": "Guinea-Bissau",
    "GUY": "Guyana", "HAI": "Haiti", "HON": "Honduras",
    "HKG": "Hong Kong", "HUN": "Hungary", "ISL": "Iceland",
    "IND": "India", "IDN": "Indonesia", "IRN": "Iran",
    "IRQ": "Iraq", "IRL": "Ireland", "ISR": "Israel",
    "ITA": "Italy", "CIV": "Ivory Coast", "JAM": "Jamaica",
    "JPN": "Japan", "JOR": "Jordan", "KAZ": "Kazakhstan",
    "KEN": "Kenya", "KUW": "Kuwait", "KGZ": "Kyrgyzstan",
    "LAO": "Laos", "LVA": "Latvia", "LBN": "Lebanon",
    "LES": "Lesotho", "LBR": "Liberia", "LBY": "Libya",
    "LTU": "Lithuania", "LUX": "Luxembourg", "MAD": "Madagascar",
    "MWI": "Malawi", "MAS": "Malaysia", "MDV": "Maldives",
    "MLI": "Mali", "MLT": "Malta", "MTN": "Mauritania",
    "MRI": "Mauritius", "MEX": "Mexico", "MDA": "Moldova",
    "MNG": "Mongolia", "MNE": "Montenegro", "MAR": "Morocco",
    "MOZ": "Mozambique", "MYA": "Myanmar", "NAM": "Namibia",
    "NED": "Netherlands", "NZL": "New Zealand", "NCA": "Nicaragua",
    "NIG": "Niger", "NGA": "Nigeria", "MKD": "North Macedonia",
    "NIR": "Northern Ireland", "NOR": "Norway", "OMA": "Oman",
    "PAK": "Pakistan", "PAN": "Panama", "PNG": "Papua New Guinea",
    "PAR": "Paraguay", "PER": "Peru", "PHI": "Philippines",
    "POL": "Poland", "POR": "Portugal", "QAT": "Qatar",
    "CGO": "Republic of the Congo", "ROU": "Romania", "RUS": "Russia",
    "RWA": "Rwanda", "KSA": "Saudi Arabia", "SCO": "Scotland",
    "SEN": "Senegal", "SRB": "Serbia", "SEY": "Seychelles",
    "SLE": "Sierra Leone", "SIN": "Singapore", "SVK": "Slovakia",
    "SVN": "Slovenia", "SOL": "Solomon Islands", "SOM": "Somalia",
    "RSA": "South Africa", "KOR": "South Korea", "SSD": "South Sudan",
    "ESP": "Spain", "SRI": "Sri Lanka", "SDN": "Sudan",
    "SUR": "Suriname", "SWE": "Sweden", "SUI": "Switzerland",
    "SYR": "Syria", "TPE": "Taiwan", "TJK": "Tajikistan",
    "TAN": "Tanzania", "THA": "Thailand", "TGO": "Togo",
    "TRI": "Trinidad and Tobago", "TUN": "Tunisia", "TUR": "Türkiye",
    "TKM": "Turkmenistan", "UGA": "Uganda", "UKR": "Ukraine",
    "UAE": "United Arab Emirates", "USA": "USA", "URU": "Uruguay",
    "UZB": "Uzbekistan", "VAN": "Vanuatu", "VEN": "Venezuela",
    "VIE": "Vietnam", "WAL": "Wales", "YEM": "Yemen",
    "ZAM": "Zambia", "ZIM": "Zimbabwe",
}


def country_name(ccode: str | None) -> str:
    """Return the English country name for a FotMob ccode (or '' if unknown)."""
    if not ccode:
        return ""
    return COUNTRY_NAMES.get(str(ccode).upper(), "")


def infer_region(league_id: int | None, league_name: str) -> str:
    """Determine the region for a league.

    Priority: explicit LEAGUE_BY_ID mapping  >  MENA name heuristic
    >  default Europe.
    """
    if league_id is not None:
        meta = LEAGUE_BY_ID.get(league_id)
        if meta:
            return meta["region"]
    name_lower = (league_name or "").lower()
    if any(kw in name_lower for kw in MENA_KEYWORDS):
        return "MENA"
    return "Europe"


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
                    league_region: str, ccode: str | None = None) -> dict:
    st = ev.get("status") or {}
    utc = st.get("utcTime")
    home = ev.get("home") or {}
    away = ev.get("away") or {}
    return {
        "id": ev.get("id"),
        "leagueId": league_id,
        "league": league_name,
        "region": league_region,
        "ccode": (ccode or "").upper(),
        "country": country_name(ccode),
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
    """Return normalised matches for the given date, all competitions.

    Every match in the day feed is captured, not just a hard-coded list.
    Region is resolved via LEAGUE_BY_ID when available, otherwise via the
    infer_region() heuristic (MENA name keywords, default Europe).
    """
    data = client.day(yyyymmdd)
    out: list[dict] = []
    for lg in data.get("leagues") or []:
        lid = lg.get("primaryId")
        lname = lg.get("name") or ""
        ccode = lg.get("ccode")
        region = infer_region(lid, lname)
        for ev in lg.get("matches") or []:
            out.append(normalise_match(ev, lid or 0, lname, region, ccode))
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
    out: dict[str, list[dict]] = {"Europe": [], "MENA": [], "International": []}
    for m in matches:
        out.setdefault(m["region"], []).append(m)
    return out

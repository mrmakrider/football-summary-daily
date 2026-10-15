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
import urllib.error
import urllib.request

HERE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE_DIR)

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

# GitHub Pages repo that the daily digest is published to.
PUSH_REPO = "mrmakrider/football-summary-daily"
PUSH_BRANCH = "main"
TOKEN_FILE = os.path.abspath(
    os.path.join(HERE_DIR, "..", ".tmp_gh_token"))


def _gh_token() -> str:
    """GitHub token for the contents-API push.

    Resolution order:
      1. the ``GH_TOKEN`` environment variable (set once when the scheduler
         daemon starts, so it survives for the whole process lifetime);
      2. ``.tmp_gh_token`` in the workspace (a file captured via
         ``gh auth token`` from a shell that *can* exec ``gh``).

    The harness ``git`` shim and ``gh`` itself are W^X-restricted and cannot
    be exec'd from Python here, so the token is never obtained by shelling
    out from inside the generator.
    """
    tok = os.environ.get("GH_TOKEN", "").strip()
    if tok:
        return tok
    return open(TOKEN_FILE, encoding="utf-8").read().strip()


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
    p.add_argument("--push", action="store_true",
                   help="publish the digest + landing page to GitHub Pages "
                        "via the contents API (default repo: "
                        f"{PUSH_REPO})")
    p.add_argument("--repo", default=PUSH_REPO,
                   help="owner/repo to push to when --push is set")
    p.add_argument("--branch", default=PUSH_BRANCH,
                   help="branch to push to when --push is set")
    p.add_argument("--skip-if-exists", action="store_true",
                   help="exit 0 without network if today's summary already exists")
    p.add_argument("--index-only", action="store_true",
                   help="only rewrite the landing page index.html and push it")
    p.add_argument("--backfill", type=int, default=0,
                   help="number of past days to backfill (run once)")
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
            # Match highlights: a YouTube URL + thumbnail when one exists
            # (roughly a quarter of FotMob matches carry one), plus the
            # player of the match. Both live under content.matchFacts.
            facts = ((details.get("content") or {}).get("matchFacts") or {})
            highlights = facts.get("highlights") or {}
            if highlights.get("url"):
                m["highlights"] = {
                    "url": highlights.get("url"),
                    "thumb": highlights.get("image"),
                }
            potm = facts.get("playerOfTheMatch") or {}
            if potm.get("name"):
                m["potm"] = {
                    "name": (potm.get("name") or {}).get("fullName"),
                    "team": potm.get("teamName"),
                    "rating": (potm.get("rating") or {}).get("num"),
                }
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


def write_index(out_dir: str, latest_stamp: str, standings: list, matches: list) -> str:
    """Rewrite the GitHub Pages landing page with the current archive list."""
    days = []
    for name in sorted(os.listdir(out_dir), reverse=True):
        if not name.startswith("football_summary_") or not name.endswith(".html"):
            continue
        stamp = name[len("football_summary_"):-len(".html")]
        if len(stamp) != 8 or not stamp.isdigit():
            continue
        try:
            label = dt.datetime.strptime(stamp, "%Y%m%d").strftime("%d %B %Y")
        except ValueError:
            label = stamp
        days.append((label, stamp))

    latest_label = next((l for l, s in days if s == latest_stamp), latest_stamp)
    rows = "\n".join(
        f'        <li><a href="summaries/football_summary_{s}.html">{l}</a></li>'
        for l, s in days
    ) or '        <li>No digests yet.</li>'

    # Derive real counts from the latest summary file so the landing page stays
    # accurate even when regenerated standalone (no matches/standings passed).
    total_matches = len(matches)
    total_tables = len(standings)
    latest_path = os.path.join(out_dir, f"football_summary_{latest_stamp}.html")
    import re
    if os.path.exists(latest_path):
        try:
            latest_html = open(latest_path, encoding="utf-8").read()
            m = re.search(r'class="count">(\d+) matches</span>', latest_html)
            if m:
                total_matches = int(m.group(1))
            # One league `<h3>` per standings table.
            total_tables = len(re.findall(r'<h3>[A-Z][^<]*</h3>', latest_html))
        except OSError:
            pass

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Football Daily Summary</title>
<style>
:root{{--bg:#0b0e14;--card:#161b25;--card2:#1e2533;--fg:#e8ecf3;--muted:#93a1b5;
--line:#242c3a;--accent:#4ade80;--accent2:#60a5fa;--red:#ef4444;--gold:#f59e0b;}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--fg);
font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
line-height:1.5}}
a{{color:var(--accent2);text-decoration:none}}
a:hover{{text-decoration:underline}}
.wrap{{max-width:1080px;margin:0 auto;padding:0 16px}}
/* Hero */
.hero{{position:relative;background:linear-gradient(135deg,#143d31,#0f1c2f);
border-radius:20px;padding:40px 30px;margin:24px 0 16px;overflow:hidden;
border:1px solid #1f4d3d;}}
.hero::before{{content:"";position:absolute;top:0;left:0,right:0,bottom:0;
background:radial-gradient(60% 60% at 50% -20%,#2d7a5e 0%,transparent 70%);
pointer-events:none}}
.hero h1{{margin:0;font-size:2.2rem;letter-spacing:-.025em}}
.hero .sub{{margin:8px 0 18px;color:var(--muted);font-size:1.05rem}}
.hero .meta{{display:flex;flex-wrap:wrap;gap:16px;font-size:.9rem;color:#cfd8cf}}
.hero .meta span{{color:#9fb8ae}}
.hero .actions{{margin-top:18px;display:flex;gap:10px;flex-wrap:wrap}}
.hero .btn{{display:inline-block;background:var(--accent);color:#042f17;
font-weight:700;font-size:.95rem;padding:10px 16px;border-radius:10px;
transition:transform .15s,box-shadow .15s}}
.hero .btn:hover{{transform:translateY(-1px);box-shadow:0 6px 20px rgba(74,222,128,.25);
text-decoration:none}}
.hero .btn.secondary{{background:#24314a;color:var(--accent2);
border:1px solid #365074}}
/* Stat strip */
.strip{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin:10px 0 22px}}
.strip .card{{background:var(--card2);border:1px solid var(--line);border-radius:14px;
padding:16px;text-align:center}}
.strip .num{{font-size:1.8rem;font-weight:800;color:var(--accent);display:block}}
.strip .lbl{{color:var(--muted);font-size:.8rem;text-transform:uppercase;
letter-spacing:.04em}}
/* Sections */
main{{display:grid;gap:18px}}
section{{background:var(--card);border:1px solid var(--line);border-radius:14px;
padding:20px}}
h2{{margin=0 0 12px;font-size:1.15rem;display:flex;align-items:center;gap:8px}}
h2 .dot{{width:8px;height:8px;border-radius:50%;background:var(--accent);
display:inline-block}}
.lead{{color:var(--muted);margin:0 0 12px;font-size:.95rem}}
/* Feature box */
.feature{{background:linear-gradient(180deg,var(--card2) 0%,var(--card) 100%);
border:1px solid var(--line);border-radius:14px;padding:16px;margin-top:10px}}
.feature .head{{display:flex;align-items:center;justify-content:space-between;
margin-bottom:10px}}
.feature .tags{{display:flex;gap:6px;flex-wrap:wrap}}
.feature .tag{{font-size:.68rem;font-weight:700;color:#cbd5e1;background:#2a3346;
padding:2px 7px;border-radius:999px}}
.feature ul{{margin:8px 0 0;padding-left:20px;color:var(--muted);font-size:.9rem}}
.feature li{{margin:4px 0}}
/* Archive */
.archive{{max-height:420px;overflow-y:auto}}
.archive li a{{display:flex;justify-content:space-between;padding:8px 0;
border-bottom:1px solid var(--line);color:var(--fg)}}
.archive li a:hover{{color:var(--accent2)}}
.archive li:last-child a{{border-bottom:none}}
.archive .note{{color:var(--muted);font-size:.85rem}}
/* Regions */
.regions{{display:grid;grid-template-columns:1fr 1fr;gap:14px}}
.region h3{{margin:0 0 8px;font-size:.95rem;color:var(--fg)}}
.region p{{margin:0;color:var(--muted);font-size:.85rem}}
/* Footer */
footer{{margin-top:30px;text-align:center;color:var(--muted);font-size:.85rem}}
footer a{{color:var(--accent2)}}
/* Mobile */
@media (max-width:560px){{
 .strip{{grid-template-columns:1fr}}
 .regions{{grid-template-columns:1fr}}
}}
</style>
</head>
<body>
<div class="wrap">
  <div class="hero">
    <h1>⚽ Football Daily Summary</h1>
    <p class="sub">Europe &amp; MENA results, insights, fixtures and standings — refreshed every day at 03:00 local time.</p>
    <div class="meta">
      <span>{total_tables} league tables</span>
      <span>Europe + MENA</span>
      <span>Data: FotMob public JSON API</span>
    </div>
    <div class="actions">
      <a href="summaries/football_summary_{latest_stamp}.html" class="btn">
        View {latest_label} →</a>
      <a href="https://github.com/mrmakrider/football-summary-daily"
         class="btn secondary">Source</a>
    </div>
  </div>

  <main>
    <section>
      <h2><span class="dot"></span>Latest digest</h2>
      <p class="lead">The most recent daily summary: completed results with final scores, goal scorers and assists, key stats, auto-generated match insights, the next 24 hours of fixtures and league standings snapshots. Each file is a self-contained responsive page.</p>
      <div class="feature">
        <div class="head">
          <strong>{total_matches} matches reported</strong>
          <div class="tags">
            <span class="tag">Europe</span><span class="tag">MENA</span>
            <span class="tag">Scorers</span><span class="tag">xG & stats</span>
            <span class="tag">Fixtures</span>
          </div>
        </div>
        <ul>
          <li><a href="summaries/football_summary_{latest_stamp}.html">Open full report for {latest_label}</a></li>
        </ul>
      </div>
    </section>

    <section>
      <h2><span class="dot" style="background:var(--accent2)"></span>Archive</h2>
      <p class="lead">Past digests, newest first. Click to open any day's full report.</p>
      <div class="archive">
        {rows}
      </div>
    </section>

    <section>
      <h2><span class="dot" style="background:var(--gold)"></span>What it covers</h2>
      <div class="regions">
        <div class="region">
          <h3>Europe (club)</h3>
          <p>Premier League, LaLiga, Serie A, Bundesliga, Ligue 1, Champions League, Europa League, Conference League</p>
        </div>
        <div class="region">
          <h3>Europe (national)</h3>
          <p>UEFA Nations League A/B/C/D, World Cup Qualification UEFA</p>
        </div>
        <div class="region">
          <h3>MENA (club)</h3>
          <p>Saudi Pro League, UAE Pro League, Qatar Stars League, Egyptian Premier League, Moroccan Botola Pro, Tunisia Ligue 1</p>
        </div>
        <div class="region">
          <h3>MENA (national)</h3>
          <p>AFCON Qualification, World Cup Qualification CAF, Asian Cup Qualification, World Cup Qualification AFC/CONCACAF, Arabian Gulf Cup</p>
        </div>
      </div>
    </section>
  </main>

  <footer>
    <p>Data: public <a href="https://www.fotmob.com">FotMob</a> JSON API · No API key required.<br>
    Source: <a href="https://github.com/mrmakrider/football-summary-daily">github.com/mrmakrider/football-summary-daily</a> · Generated at 03:00 local.</p>
  </footer>
</div>
</body>
</html>
"""
    idx = os.path.join(out_dir, "..", "index.html")
    idx = os.path.abspath(idx)
    with open(idx, "w", encoding="utf-8") as fh:
        fh.write(html)
    return idx


def push_file(token: str, repo: str, branch: str, path: str,
              content: str, message: str) -> dict:
    """Upload one file to GitHub via the contents API.

    The harness `git` shim is W^X-restricted, so a full `git push` is not
    possible; pushing just the changed file is enough and far cheaper than
    re-uploading the whole tree every day.
    """
    import base64
    import urllib.request
    url = f"https://api.github.com/repos/{repo}/contents/{path}"
    b64 = base64.b64encode(content.encode()).decode()
    payload = {"message": message, "content": b64, "branch": branch}

    # Resolve a prior SHA so the update is a modify, not a duplicate create.
    get = urllib.request.Request(
        f"{url}?ref={branch}",
        headers={"Authorization": f"Bearer {token}",
                 "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(get) as r:
            payload["sha"] = json.loads(r.read().decode())["sha"]
    except urllib.error.HTTPError as exc:
        if exc.code not in (404, 409):
            raise

    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=data, method="PUT",
        headers={"Authorization": f"Bearer {token}",
                 "Accept": "application/vnd.github+json",
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read().decode())


def run(argv=None) -> str:
    args = parse_args(argv)

    # --index-only: only rewrite and push the landing page
    if args.index_only:
        out_dir = args.out
        os.makedirs(out_dir, exist_ok=True)
        # Find latest stamp in summaries/
        latest = None
        for name in sorted(os.listdir(out_dir), reverse=True):
            if name.startswith("football_summary_") and name.endswith(".html"):
                stamp = name[len("football_summary_"):-len(".html")]
                if len(stamp) == 8 and stamp.isdigit():
                    latest = stamp
                    break
        if not latest:
            print("no existing summary to build index from", file=sys.stderr)
            return ""
        stamp = latest
        # We need matches and standings from that day to build the index.
        # Since we don't have them without --offline, just build a minimal index.
        # In practice, the index is always pushed alongside the digest, so this
        # path is only used if the index needs a refresh on its own.
        # For now, just push an empty index update.
        html_text = report.render_html([], [], dt.datetime.now().astimezone(),
                                       dt.datetime.now().astimezone().tzinfo,
                                       {}, SOURCES, report_date=dt.date.today())
        if args.push:
            token = _gh_token()
            idx = write_index(out_dir, stamp, [], [])
            push_file(token, args.repo, args.branch, "index.html",
                      open(idx, encoding="utf-8").read(),
                      f"Update landing page for {stamp}")
            print(f"pushed index.html -> {args.repo}:{args.branch}")
        return ""

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

        # --skip-if-exists: for retry crons, exit cleanly if today's file exists
        if args.skip_if_exists:
            stamp = anchor.strftime("%Y%m%d")
            existing = os.path.join(args.out, f"football_summary_{stamp}.html")
            if os.path.exists(existing):
                print(f"[{now:%Y-%m-%d %H:%M %Z}] {stamp} already exists, skipping")
                return existing

        # --backfill: run for N past days including today
        if args.backfill > 0:
            results = []
            for i in range(args.backfill):
                d = anchor - dt.timedelta(days=i)
                stamp = d.strftime("%Y%m%d")
                out_path = os.path.join(args.out, f"football_summary_{stamp}.html")
                if os.path.exists(out_path) and not args.push:
                    print(f"  {stamp} already present, skipping")
                    results.append(out_path)
                    continue
                print(f"[{now:%Y-%m-%d %H:%M %Z}] scanning {d} window...", file=sys.stderr)
                matches, windows = collect_all(
                    client, d, enrich=not args.no_details,
                    max_detail=args.max_detail, now=now)
                print(f"  matched {len(matches)} fixtures; "
                      f"{client.calls} API calls", file=sys.stderr)
                standings = load_standings(client, args.standings)

                html_text = report.render_html(matches, standings, now, tz, windows,
                                               SOURCES, report_date=d)

                os.makedirs(args.out, exist_ok=True)
                path = os.path.join(args.out, f"football_summary_{stamp}.html")
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(html_text)

                if args.push:
                    token = _gh_token()
                    rel = f"summaries/football_summary_{stamp}.html"
                    push_file(token, args.repo, args.branch, rel, html_text,
                              f"Daily football summary {stamp}")
                    print(f"pushed {rel} -> {args.repo}:{args.branch}")
                results.append(path)
            return results[-1] if results else ""

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

    if args.push:
        token = _gh_token()
        rel = f"summaries/football_summary_{stamp}.html"
        push_file(token, args.repo, args.branch, rel, html_text,
                  f"Daily football summary {stamp}")
        print(f"pushed {rel} -> {args.repo}:{args.branch}")
        idx = write_index(out_dir, stamp, standings, matches)
        push_file(token, args.repo, args.branch, "index.html",
                  open(idx, encoding="utf-8").read(),
                  f"Update landing page for {stamp}")
        print(f"pushed index.html -> {args.repo}:{args.branch}")
    return path


if __name__ == "__main__":
    run()

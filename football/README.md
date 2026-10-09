# Daily Football Summary

Generates a responsive HTML digest of football results, insights, upcoming
fixtures and league standings for **Europe** and the **MENA** region, and can
run itself **every day at 07:00 local time**.

## What it covers

| Region | Competitions |
|---|---|
| Europe (club) | Premier League, LaLiga, Serie A, Bundesliga, Ligue 1, Champions League, Europa League, Conference League |
| Europe (national) | UEFA Nations League A/B/C/D, World Cup Qualification UEFA |
| MENA (club) | Saudi Pro League, UAE Pro League, Qatar Stars League, Egyptian Premier League, Moroccan Botola Pro, Tunisia Ligue 1 |
| MENA (national) | Africa Cup of Nations Qualification, World Cup Qualification CAF, Asian Cup Qualification, World Cup Qualification AFC, World Cup Qualification CONCACAF, Arabian Gulf Cup |
| International | International Friendlies (filtered to tracked nations) |

## Data source

All data comes from the public [FotMob](https://www.fotmob.com) JSON API
(`https://www.fotmob.com/api/data/...`). No API key, no account, no scraping of
HTML. The endpoints used are:

| Endpoint | Purpose |
|---|---|
| `matches?date=YYYYMMDD` | all fixtures for a day, across every competition |
| `matchDetails?matchId=ID` | goal scorers, assists, cards, team stats, venue, attendance |
| `leagues?id=ID` | league table, qualification colours and legend |

The CDN rejects requests with a bare/curl `User-Agent`, so the client sends a
normal browser `User-Agent` plus `Sec-Fetch-*` headers. Every call is retried
with exponential backoff, and successful responses are cached per run.

## Output

A single self-contained file, `football_summary_<YYYYMMDD>.html`, written to
`summaries/` (configurable). It contains:

1. **Results** — every completed match with final score, goal scorers with
   minutes and assists, venue/attendance, and key stats (possession, shots,
   shots on target, corners, xG …), split into Europe and MENA.
2. **Match reports & insights** — the most notable games of the window get a
   one-paragraph narrative generated from the scoreline, goal list and stats.
   Scorers are folded by player with their minutes (`Martinelli 18, 43, 50`)
   and repeated scorers carry a `2×`/`3×` badge on the match card.
3. **Upcoming fixtures** — the next 24 hours, filtered on the **wall clock**
   (`now < kick-off <= now + 24h`), not just on "status is SCHEDULED", and
   grouped by competition. The window is printed under the heading, and the
   empty state names it too. The fixture scan is centred on *today* even when
   `--date` points at a past day, so historical runs never list matches that
   already kicked off.
4. **League standings** — top 8 for the first 4 competitions in each region,
   with colour-coded European/continental qualification spots and a legend.

The page is responsive: a dark card layout that collapses to a single column
below 640px, with no horizontal overflow. Each result card renders the
scoreline **once**, in a 3-column grid shared with the club names, so a long
club name can never duplicate or displace the score.

## Usage

Generate once, right now:

```bash
python3 football/generate_summary.py --out summaries
```

Options: `--date YYYYMMDD` (anchor day for the *results* window only),
`--out DIR`, `--standings N` (competitions per region), `--no-details` (skip
per-match detail calls), `--max-detail N` (cap detail fetches),
`--offline SNAP.json` (re-render a saved payload without network).

Run the tests (fully offline except one deliberate end-to-end case):

```bash
python3 football/run_tests.py
```

## Recovering a missed day

Because the scheduler only lives as long as the app process, a day can be
skipped entirely (app killed, device asleep, battery optimiser). `backfill.py`
scans the output directory for gaps and rebuilds them:

```bash
python3 football/backfill.py --dry-run     # list the missing days, fetch nothing
python3 football/backfill.py               # rebuild them (default: last 14 days)
python3 football/backfill.py --days 30     # widen the window
```

Options: `--out DIR`, `--days N`, `--force` (also rebuild existing days),
`--dry-run`, `--standings N`, `--max-detail N`, `--no-details`,
`--pause SECONDS` between days.

Each report is named after its **reporting day**, not the wall clock, so a
backfill for an old date can never overwrite a newer report.

## The 07:00 daily schedule

Android has **no cron daemon**, so there is no `crontab` entry to add. Instead
`football/scheduler.py` is a long-running loop that computes the remaining
sleep to the next 07:00 local time, runs the generator, and reschedules itself.
It wakes at least every 15 minutes so a clock/date change is picked up, and it
fires at most once per calendar day.

Start it as a background process (it then stays alive as long as the
AndroidHarness app is running):

```bash
python3 football/run_daily.py --hour 7 --minute 0 --log scheduler.log
```

Notes and limits:

- The generator runs **in-process** inside the scheduler, not as a subprocess.
  The harness toolchain binaries are W^X-restricted shims that must be launched
  through the dynamic linker, which a plain `subprocess()` call cannot do.
- The schedule only fires while the app (and its background service) is alive.
  There is no OS-level wake-up on Android that a normal app can register for an
  arbitrary 07:00 trigger; if the device or app is off at 07:00 the run happens
  at the next opportunity after start-up.
- Every run appends to `football/scheduler.log`; failures are logged and the
  loop continues to the next day rather than crashing.

## Files

```
football/
  fotmob.py            API client, league roster, normalisation helpers
  report.py            HTML rendering (CSS, all four sections)
  generate_summary.py  pipeline entry point -> writes the HTML file
  scheduler.py         07:00 self-rescheduling loop
  run_daily.py         thin wrapper used to start the scheduler
  backfill.py          rebuilds reports for days the scheduler missed
  tests.py             data + rendering tests (offline fakes)
  test_scheduler.py    scheduling logic + real end-to-end generation
  run_tests.py         runs both suites
```

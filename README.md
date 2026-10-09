# Football Daily Summary

Generates a responsive HTML digest of football results, insights, upcoming
fixtures and league standings for **Europe** and the **MENA** region, and can
run itself **every day at 07:00 local time**.

Data comes from the public [FotMob](https://www.fotmob.com) JSON API — no API
key, no account, no HTML scraping.

See [`football/README.md`](football/README.md) for the full run book.

## Quick start

```bash
pip install -r requirements.txt      # stdlib only, nothing to install
python3 football/generate_summary.py --out summaries
python3 football/run_daily.py --hour 7 --minute 0
```

## Output

`summaries/football_summary_<YYYYMMDD>.html` — a single self-contained file:

1. **Results** — completed matches with final scores, goal scorers (minutes +
   assists), venue/attendance and key stats (possession, shots, shots on
   target, corners, xG), split into Europe and MENA.
2. **Match reports & insights** — auto-generated notes for notable games.
3. **Upcoming fixtures** — next 24 hours.
4. **League standings** — snapshots for tracked competitions.

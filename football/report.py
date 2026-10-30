"""Build the daily HTML summary page."""
from __future__ import annotations

import datetime as dt
import html
from typing import Any

from fotmob import (
    LEAGUES,
    Client,
    collect_day,
    extract_scorers,
    extract_stats,
    group_by_region,
    standings_rows,
)

REGION_ORDER = ["Europe", "MENA", "International"]
REGION_LABEL = {"Europe": "Europe", "MENA": "MENA", "International": "International"}


def esc(x: Any) -> str:
    return html.escape(str(x if x is not None else ""), quote=True)


def _fmt_time(utc: str | None, tz: dt.tzinfo) -> str:
    if not utc:
        return "TBC"
    try:
        d = dt.datetime.fromisoformat(utc.replace("Z", "+00:00"))
    except ValueError:
        return "TBC"
    return d.astimezone(tz).strftime("%a %d %b · %H:%M")


def _fmt_kickoff(utc: str | None, tz: dt.tzinfo) -> str:
    if not utc:
        return "TBC"
    try:
        d = dt.datetime.fromisoformat(utc.replace("Z", "+00:00"))
    except ValueError:
        return "TBC"
    return d.astimezone(tz).strftime("%H:%M")


def _score_cell(m: dict) -> str:
    """A single combined scoreline, laid out as a home/away grid.

    Kept as one element so the two team names and the two numbers share a
    common column grid; otherwise long club names push the score onto two
    lines. The score must only be rendered once per card - see _match_card.
    """
    if m["status"] == "SCHEDULED" or m["homeScore"] is None:
        return ('<span class="scorebox pending">'
                '<span class="tname"></span><span class="sc">vs</span>'
                '<span class="tname"></span></span>')
    tag = "live" if m["status"] == "LIVE" else "final"
    return (f'<span class="scorebox {tag}">'
            f'<span class="tname">{esc(m["home"])}</span>'
            f'<span class="sc">{esc(m["homeScore"])} &ndash; {esc(m["awayScore"])}</span>'
            f'<span class="tname">{esc(m["away"])}</span></span>')


def _stat_rows(stats: list[tuple]) -> str:
    if not stats:
        return '<p class="muted small">No detailed stats available.</p>'
    out = ['<div class="statgrid">']
    for title, h, a in stats:
        out.append(
            '<div class="stat">'
            f'<span class="sv">{esc(h)}</span>'
            f'<span class="st">{esc(title)}</span>'
            f'<span class="sv">{esc(a)}</span></div>')
    out.append("</div>")
    return "".join(out)


from fotmob import format_minute as fmt_minute  # noqa: E402


def _scorer_list(goals: list[dict]) -> str:
    if not goals:
        return ""
    counts: dict[str, int] = {}
    for g in goals:
        counts[g["player"]] = counts.get(g["player"], 0) + 1
    items = []
    for g in goals:
        assist = (f' <span class="assist">(ast. {esc(g["assist"])})</span>'
                  if g.get("assist") else "")
        n = counts[g["player"]]
        mark = (' <span class="badge tally" title="multiple goals">'
                f'{n}&times;</span>') if n > 1 else ""
        items.append(
            f'<li class="scorer"><span class="min">'
            f'{esc(fmt_minute(g["minute"]))}</span>'
            f'<span class="pname">{esc(g["player"])}{mark}{assist}</span></li>')
    return '<ul class="scorers">' + "".join(items) + "</ul>"


def _scorer_summary(goals: list[dict], limit: int = 6) -> str:
    """Human scoreline: 'Smith 12', 'Foden 45+1, 78' (hat-tricks folded)."""
    order: list[str] = []
    tally: dict[str, list[str]] = {}
    for g in goals or []:
        name = g["player"]
        if name not in tally:
            tally[name] = []
            order.append(name)
        tally[name].append(str(fmt_minute(g["minute"])).rstrip("'"))
    bits = [f"{n} {', '.join(ms)}" for n, ms in tally.items()]
    if len(bits) > limit:
        bits = bits[:limit] + [f"and {len(bits) - limit} more"]
    return "; ".join(bits)


def _insight(m: dict) -> str:
    """Build a short narrative line for a completed match."""
    hs, as_ = m.get("homeScore"), m.get("awayScore")
    if hs is None or as_ is None:
        return ""
    total = hs + as_
    margin = abs(hs - as_)
    sentences: list[str] = []

    if margin >= 2:
        winner = m["home"] if hs > as_ else m["away"]
        loser = m["away"] if hs > as_ else m["home"]
        if margin >= 3:
            verdict = (f"{winner} produced a comprehensive {margin}-goal "
                       f"victory over {loser}")
        else:
            verdict = f"{winner} beat {loser} by two goals"
        sentences.append(verdict)
    elif margin == 1:
        winner = m["home"] if hs > as_ else m["away"]
        loser = m["away"] if hs > as_ else m["home"]
        sentences.append(f"{winner} edged it by a single goal against {loser}")
    else:
        if m.get("status") == "PEN":
            pens = m.get("penalties") or []
            lost = m.get("lostOnPenalties")
            if lost and pens and len(pens) == 2:
                winner = m["away"] if lost == m["home"] else m["home"]
                loser = m["home"] if lost == m["home"] else m["away"]
                sentences.append(
                    f"The tie was level at the end of regular time, and "
                    f"{winner} won the shootout {pens[0]}-{pens[1]} "
                    f"against {loser}")
            else:
                sentences.append("Honours were even before the shootout")
        else:
            sentences.append("Honours were even in a draw")

    if total >= 6:
        sentences.append("It was a high-scoring thriller")
    elif total == 0:
        sentences.append("in a cagey goalless contest")
    elif total == 1:
        sentences.append("in a low-scoring affair")

    shots = next((s for s in (m.get("stats") or [])
                  if str(s[0]).lower() == "total shots"), None)
    if shots and shots[1] is not None and shots[2] is not None:
        leader = m["home"] if shots[1] >= shots[2] else m["away"]
        sentences.append(f"Shot count {shots[1]}-{shots[2]} favoured {leader}")
    return ". ".join(s[0].upper() + s[1:] for s in sentences) + "."


def _highlights_block(m: dict) -> str:
    """YouTube match highlights + player of the match, when available.

    FotMob only carries highlights for a minority of matches (roughly a
    quarter), so this is optional and degrades to nothing when absent.
    """
    parts: list[str] = []
    hl = m.get("highlights") or {}
    if hl.get("url"):
        thumb = hl.get("thumb") or ""
        thumb_html = (f'<img src="{esc(thumb)}" alt="" loading="lazy" '
                      f'onerror="this.parentElement.style.display=\'none\'">'
                      if thumb else "")
        parts.append(
            f'<a class="hl" href="{esc(hl["url"])}" target="_blank" '
            f'rel="noopener noreferrer">'
            f'<span class="hl-tag">▶ Highlights</span>'
            f'{thumb_html}'
            f'<span class="hl-cap">Watch full highlights</span>'
            f'</a>')
    potm = m.get("potm") or {}
    if potm.get("name"):
        rating = (f' ({esc(potm["rating"])})' if potm.get("rating") else "")
        team = (f' · {esc(potm["team"])}' if potm.get("team") else "")
        parts.append(
            f'<span class="potm">⭐ Player of the match: '
            f'<b>{esc(potm["name"])}</b>{team}{rating}</span>')
    if not parts:
        return ""
    return f'<div class="extras">{"".join(parts)}</div>'


def _match_card(m: dict, tz: dt.tzinfo, detail: bool = True) -> str:
    goal_block = _scorer_list(m.get("goals", [])) if m.get("goals") else ""
    stats_block = _stat_rows(m.get("stats", [])) if detail else ""
    insight = ""
    if m.get("status") in ("FT", "AET", "PEN") and m.get("homeScore") is not None:
        insight = f'<p class="insight">{esc(_insight(m))}</p>'
    meta = []
    if m.get("venue"):
        meta.append(esc(m["venue"]))
    if m.get("attendance"):
        meta.append(f'Att. {esc(m["attendance"])}')
    meta_html = f'<p class="meta">{" · ".join(meta)}</p>' if meta else ""
    badge = ('<span class="badge ft">FT</span>' if m.get("status") == "FT"
             else f'<span class="badge {m.get("status","sched").lower()}">'
                  f'{esc(m.get("status"))}</span>')
    # A shootout decided after a level scoreline: show who won it, otherwise
    # the 1-1 reads as a draw and the penalty result is lost.
    pens = m.get("penalties")
    pen_block = ""
    if m.get("status") == "PEN" and pens and len(pens) == 2:
        home_won = (m.get("homeScore") == m.get("awayScore")
                    and m.get("lostOnPenalties") != m.get("home"))
        winner = m["home"] if home_won else m["away"]
        loser = m["away"] if home_won else m["home"]
        pen_block = (f'<p class="pens">{esc(winner)} beat {esc(loser)} '
                     f'<b>{esc(pens[0])} &ndash; {esc(pens[1])}</b> '
                     f'on penalties</p>')
    country_attr = esc(m.get("country")) if m.get("country") else ""
    return f"""
    <article class="card" data-country="{country_attr}">
      <header class="card-head">
        <span class="league">{esc(m['league'])}</span>
        {f'<span class="country">{esc(m["country"])}</span>' if m.get("country") else ''}
        {badge}
        <span class="kickoff">{esc(_fmt_time(m.get('utc'), tz))}</span>
      </header>
      {_score_cell(m)}
      {pen_block}
      {meta_html}
      {insight}
      {goal_block}
      {_highlights_block(m)}
      {stats_block}
    </article>"""


def _fixture_row(m: dict, tz: dt.tzinfo) -> str:
    return f"""
      <tr>
        <td class="fx-time">{esc(_fmt_kickoff(m.get('utc'), tz))}</td>
        <td class="fx-teams">{esc(m['home'])} <span class="muted">vs</span> {esc(m['away'])}</td>
      </tr>"""


def _standings_table(lg: dict, rows: list[dict], max_rows: int = 8,
                     legend: list | None = None) -> str:
    if not rows:
        return (f'<div class="standings"><h3>{esc(lg["name"])}</h3>'
                '<p class="muted small">Standings unavailable.</p></div>')
    body = []
    for r in rows[:max_rows]:
        qual = (f'<span class="dot" style="background:{esc(r["qualColor"])}"'
                f' title="European competition place"></span>'
                if r.get("qualColor") else "")
        body.append(
            f'<tr><td class="pos">{esc(r["pos"])}{qual}</td>'
            f'<td class="tm">{esc(r["team"])}</td>'
            f'<td>{esc(r["played"])}</td><td>{esc(r["wins"])}</td>'
            f'<td>{esc(r["draws"])}</td><td>{esc(r["losses"])}</td>'
            f'<td>{esc(r["gf"])}</td><td>{esc(r["gd"])}</td>'
            f'<td class="pts">{esc(r["pts"])}</td></tr>')
    legend_html = ""
    if legend:
        chips = "".join(
            f'<span class="chip"><span class="dot" style="background:'
            f'{esc(x.get("color"))}"></span>{esc(x.get("title"))}</span>'
            for x in legend if x.get("color") and x.get("title"))
        if chips:
            legend_html = f'<p class="legend">{chips}</p>'
    country = f' <span class="country muted">{esc(lg["country"])}</span>' if lg.get("country") else ""
    country_attr = esc(lg.get("country")) if lg.get("country") else ""
    return f"""
    <div class="standings" data-country="{country_attr}">
      <h3>{esc(lg['name'])}{country}</h3>
      <table class="tbl">
        <thead><tr><th>#</th><th>Team</th><th>P</th><th>W</th><th>D</th>
        <th>L</th><th>GF:GA</th><th>GD</th><th>Pts</th></tr></thead>
        <tbody>{''.join(body)}</tbody>
      </table>
      {legend_html}
    </div>"""


CSS = """
:root{--bg:#0e1117;--card:#161b25;--card2:#1c2230;--fg:#e8ecf3;--muted:#93a1b5;
--accent:#4ade80;--accent2:#60a5fa;--line:#242c3a;}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
line-height:1.5;}
.wrap{max-width:1080px;margin:0 auto;padding:16px}
header.page{background:linear-gradient(135deg,#16233c,#0e1117);padding:28px 16px;
border-bottom:1px solid var(--line)}
header.page .wrap{padding-bottom:4px}
h1{margin:0 0 6px;font-size:1.6rem;letter-spacing:-.02em}
.sub{color:var(--muted);margin:0;font-size:.95rem}
h2{font-size:1.2rem;margin:28px 0 12px;padding-bottom:6px;
border-bottom:1px solid var(--line)}
h2 .count{color:var(--muted);font-size:.85rem;font-weight:400;margin-left:8px}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;
padding:14px}
.card-head{display:flex;align-items:center;gap:8px;margin-bottom:10px;
font-size:.75rem;color:var(--muted);flex-wrap:wrap}
.league{color:var(--accent2);font-weight:600;text-transform:uppercase;
letter-spacing:.04em}
.country{color:#7dd3b0;font-weight:500;text-transform:capitalize;
letter-spacing:0;padding:1px 8px;border:1px solid #1f3a2e;border-radius:999px;
background:#142a1e;font-size:.68rem}
.country.muted{color:var(--muted);border-color:var(--line);background:transparent;
font-size:.75rem;padding:0}
.badge{padding:1px 7px;border-radius:999px;font-size:.68rem;font-weight:700;
background:#2a3346;color:#cbd5e1}
.badge.ft{background:#14532d;color:#86efac}
.badge.live{background:#7f1d1d;color:#fecaca}
.badge.pen,.badge.aet{background:#4a2a0b;color:#fdba74}
.pens{display:inline-block;margin:0 0 6px;padding:2px 9px;border-radius:8px;
font-size:.78rem;background:#3a2410;color:#fdba74;font-weight:600;
font-variant-numeric:tabular-nums}
.pens b{color:#fff}
.kickoff{margin-left:auto}
/* Match highlights + player of the match, shown below the scoreline when
   FotMob carries them (a minority of matches). */
.extras{display:flex;flex-wrap:wrap;align-items:center;gap:8px;
margin:8px 0 0;font-size:.8rem}
.hl{display:inline-flex;align-items:center;gap:8px;background:#1c2230;
border:1px solid var(--line);border-radius:10px;padding:4px 8px 4px 4px;
color:var(--fg);text-decoration:none;transition:border-color .15s}
.hl:hover{border-color:var(--accent2)}
.hl img{width:64px;height:36px;object-fit:cover;border-radius:6px}
.hl-cap{color:var(--muted)}
.hl-tag{background:#7f1d1d;color:#fecaca;font-size:.62rem;font-weight:700;
padding:1px 6px;border-radius:999px;letter-spacing:.03em}
.potm{color:var(--accent);font-weight:600}
.potm b{color:var(--fg)}
/* Home / score / away share one column grid, so a long club name can never
   push the score onto its own line or duplicate it. */
.scorebox{display:grid;grid-template-columns:1fr auto 1fr;align-items:center;
gap:2px 10px;margin:0 0 2px}
.scorebox .tname{font-weight:600;min-width:0;overflow-wrap:break-word}
/* The away name is the second <span class="tname">, i.e. the 3rd grid child. */
.scorebox .tname:nth-child(3){text-align:right}
@media (max-width:400px){
 .scorebox{grid-template-columns:1fr;gap:2px;text-align:center}
 .scorebox .tname:nth-child(3){text-align:center}
 .scorebox .sc{justify-self:center}
}
.scorebox .sc{font-variant-numeric:tabular-nums;font-weight:700;
background:var(--card2);padding:2px 10px;border-radius:6px;min-width:62px;
text-align:center}
.scorebox.pending .sc{color:var(--muted);font-weight:400}
.badge.tally{background:#1e3a5f;color:#93c5fd;padding:0 5px;font-size:.7rem;
margin-left:2px}
.meta{color:var(--muted);font-size:.78rem;margin:8px 0 0}
.insight{margin:8px 0 0;font-size:.85rem;color:#cbd5e1;border-left:3px solid var(--accent);
padding-left:8px}
.scorers{list-style:none;margin:10px 0 0;padding:0;font-size:.85rem}
.scorer{display:flex;gap:8px;padding:2px 0}
.min{color:var(--muted);min-width:34px;text-align:right;
font-variant-numeric:tabular-nums}
.assist{color:var(--muted);font-size:.95em}
.statgrid{display:grid;grid-template-columns:repeat(2,1fr);gap:4px 12px;
margin-top:10px;font-size:.8rem}
.stat{display:flex;align-items:center;gap:6px}
.stat .sv{min-width:26px;text-align:right;font-variant-numeric:tabular-nums;
color:#cbd5e1}
.stat .st{flex:1;text-align:center;color:var(--muted);font-size:.72rem}
table.tbl,table.fx{width:100%;border-collapse:collapse;font-size:.83rem}
table.tbl th{color:var(--muted);font-weight:600;font-size:.72rem;text-align:right;
padding:4px 6px;border-bottom:1px solid var(--line);white-space:nowrap}
table.tbl th:nth-child(2),table.tbl td.tm{text-align:left}
table.tbl td{padding:4px 6px;text-align:right;border-bottom:1px solid #1e2534;
font-variant-numeric:tabular-nums;white-space:nowrap}
table.tbl td.pos{width:26px;min-width:26px}
table.tbl td.tm{min-width:130px;white-space:normal}
table.tbl td.pts{font-weight:700;color:var(--accent)}
table.tbl tr:nth-child(even){background:#131824}
.dot{display:inline-block;width:7px;height:7px;border-radius:2px;
margin-left:4px;vertical-align:middle}
.standings{background:var(--card);border:1px solid var(--line);border-radius:12px;
padding:12px;overflow-x:auto}
.standings h3{margin:0 0 8px;font-size:.95rem}
.standings-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));
gap:12px}
table.fx td{padding:6px 8px;border-bottom:1px solid #1e2534}
tr.fxgroup td{color:var(--accent2);font-size:.75rem;font-weight:600;
text-transform:uppercase;letter-spacing:.04em;padding-top:12px}
.fx-time{color:var(--muted);font-size:.78rem;white-space:nowrap;width:90px}
.fx-teams{font-weight:600}
.legend{margin:8px 0 0;font-size:.7rem;color:var(--muted);
display:flex;flex-wrap:wrap;gap:4px 10px}
.chip{display:inline-flex;align-items:center;gap:4px}
.muted{color:var(--muted)}
.small{font-size:.8rem}
.empty{background:var(--card);border:1px dashed var(--line);border-radius:12px;
padding:18px;color:var(--muted)}
.filterbar{display:flex;align-items:center;gap:10px;flex-wrap:wrap;
background:var(--card);border:1px solid var(--line);border-radius:12px;
padding:10px 14px;margin-bottom:4px;position:sticky;top:8px;z-index:5;
backdrop-filter:blur(6px)}
.filterbar label{font-size:.85rem;font-weight:600;color:var(--muted)}
.filterbar select{background:var(--card2);color:var(--fg);border:1px solid var(--line);
border-radius:8px;padding:6px 10px;font-size:.88rem;min-width:170px}
.filterbar .muted{font-size:.8rem}
@media (max-width:640px){
 .filterbar select{flex:1;min-width:0}
}
footer.page{margin-top:36px;padding:16px;border-top:1px solid var(--line);
color:var(--muted);font-size:.78rem}
a{color:var(--accent2)}
@media (max-width:640px){
 header.page h1{font-size:1.3rem}
 .cards{grid-template-columns:1fr}
 .wrap{padding:12px}
 .statgrid{grid-template-columns:1fr}
 .standings{padding:10px}
 table.tbl{font-size:.76rem}
 table.tbl td,table.tbl th{padding:4px 3px}
 table.tbl td.tm{min-width:96px}
 .fx-teams{font-size:.9rem}
}
"""


def render_html(day_matches: list[dict], standings: list[tuple[dict, list]],
                now: dt.datetime, tz: dt.tzinfo,
                data_windows: dict[str, list[str]],
                sources: list[str],
                report_date: dt.date | None = None) -> str:
    """Render the summary page.

    `now` is the wall clock (used for the "next 24 hours" window and the
    generated-at stamp). `report_date` is the day actually being reported on;
    it defaults to `now`'s date. They differ during a backfill, where a past
    day is rendered today - without it the heading would mislabel the page.
    """
    day = report_date or now.astimezone(tz).date()
    finished = [m for m in day_matches if m.get("status") in ("FT", "AET", "PEN")]
    live = [m for m in day_matches if m.get("status") == "LIVE"]

    # "Next 24 hours" must be a real clock window, not just "status ==
    # SCHEDULED": the scan covers a couple of days, so filter on kick-off.
    horizon = now + dt.timedelta(hours=24)
    upcoming = []
    for m in day_matches:
        if m.get("status") != "SCHEDULED" or not m.get("utc"):
            continue
        try:
            ko = dt.datetime.fromisoformat(m["utc"].replace("Z", "+00:00"))
        except ValueError:
            continue
        if ko <= now or ko > horizon:
            continue
        upcoming.append(m)
    upcoming.sort(key=lambda m: (m["utc"] or "", m["league"], m["home"]))
    regions = group_by_region(finished + live)
    notable = sorted(finished, key=lambda m: -(abs((m.get("homeScore") or 0)
                                                  - (m.get("awayScore") or 0))
                                                + (m.get("homeScore") or 0)
                                                + (m.get("awayScore") or 0)))[:6]

    parts: list[str] = []
    parts.append(f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Football Daily Summary &ndash; {day:%d %B %Y}</title>
<style>{CSS}</style>
</head><body>
<header class="page"><div class="wrap">
<h1>&#9917; Football Daily Summary</h1>
<p class="sub">{day:%A %d %B %Y} &middot; generated {now:%H:%M %Z} &middot;
{len(finished)} completed, {len(live)} live, {len(upcoming)} upcoming
&middot; Europe · MENA · International</p>
</div></header>
	<div class="wrap">""")

    countries = sorted({m.get("country") for m in finished + live if m.get("country")})
    opts = "".join(f'<option value="{esc(c)}">{esc(c)}</option>'
                   for c in countries)
    parts.append(f"""
<div class="filterbar">
  <label for="country-filter">🌍 Filter by country</label>
  <select id="country-filter">
    <option value="all">All countries</option>
    {opts}
  </select>
  <span id="filter-count" class="muted small"></span>
</div>
<div id="filter-empty" class="empty" hidden>No matches for the selected country.</div>
""")

    for region in REGION_ORDER:
        ms = regions.get(region, [])
        parts.append(f'<h2>Results &middot; {REGION_LABEL[region]}'
                     f'<span class="count">{len(ms)} match'
                     f'{"es" if len(ms) != 1 else ""}</span></h2>')
        if not ms:
            parts.append('<div class="empty">No completed matches in the '
                         f'{REGION_LABEL[region]} competitions for this '
                         'reporting window.</div>')
            continue
        by_league: dict[str, list[dict]] = {}
        for m in ms:
            by_league.setdefault(m["league"], []).append(m)
        cards = []
        for lg in sorted(by_league, key=lambda n: [l["name"] for l in LEAGUES].index(n)
                         if n in [l["name"] for l in LEAGUES] else 99):
            for m in by_league[lg]:
                cards.append(_match_card(m, tz))
        parts.append('<div class="cards">' + "".join(cards) + "</div>")

    parts.append('<h2>Match Reports &amp; Insights</h2>')
    if notable:
        items = []
        for m in notable:
            hs, as_ = m.get("homeScore"), m.get("awayScore")
            scorers = _scorer_summary(m.get("goals") or [])
            txt = (f'<strong>{esc(m["home"])} {esc(hs)}&ndash;{esc(as_)} '
                   f'{esc(m["away"])}</strong> ({esc(m["league"])}): '
                   f'{esc(_insight(m))}')
            if scorers:
                txt += f' Scorers: {esc(scorers)}.'
            items.append(f'<li>{txt}</li>')
        parts.append('<div class="standings"><ul class="insight-list">'
                     + "".join(items) + "</ul></div>")
    else:
        parts.append('<div class="empty">No notable completed matches to '
                     'summarise for this window.</div>')

    tz_label = esc(tz.tzname(None) or "local")
    parts.append('<h2>Upcoming Fixtures &middot; next 24 hours</h2>'
                 f'<p class="muted small window-note">'
                 f'{now:%a %d %b %H:%M} &rarr; {horizon:%a %d %b %H:%M}'
                 f' ({tz_label})</p>')
    if upcoming:
        rows = []
        by_league = {}
        for m in upcoming:
            by_league.setdefault(m["league"], []).append(m)
        order = [l["name"] for l in LEAGUES]
        for lg in sorted(by_league, key=lambda n: order.index(n)
                         if n in order else 99):
            country = ""
            for m in by_league[lg]:
                if m.get("country"):
                    country = m["country"]
                    break
            country_html = (f' <span class="country muted">{esc(country)}</span>'
                            if country else "")
            rows.append(f'<tr class="fxgroup"><td colspan="2">'
                        f'{esc(lg)}{country_html}</td></tr>')
            for m in by_league[lg]:
                rows.append(_fixture_row(m, tz))
        parts.append('<div class="standings"><table class="fx">'
                     + "".join(rows) + "</table></div>")
    else:
        parts.append('<div class="empty">No fixtures scheduled between '
                     f'{now:%a %d %b %H:%M} and {horizon:%a %d %b %H:%M} '
                     'for the tracked competitions. Matchday is over - the '
                     'next round of fixtures will appear here after the '
                     'daily 07:00 refresh.</div>')

    parts.append('<h2>League Standings</h2>')
    if standings:
        blocks = "".join(_standings_table(lg, rows, legend=lg.get("legend"))
                         for lg, rows in standings)
        parts.append('<div class="standings-grid">' + blocks + "</div>")
    else:
        parts.append('<div class="empty">Standings could not be loaded.</div>')

    win_desc = ", ".join(f"{k} ({len(v)} days)" for k, v in data_windows.items())
    parts.append(f"""
<footer class="page">
<p><strong>Data sources:</strong> {' &middot; '.join(esc(s) for s in sources)}</p>
<p>Reporting window scanned: {esc(win_desc)}. Kick-off times shown in
{esc(tz.tzname(None) or 'local time')}. Standings are the latest published
tables from the data provider.</p>
<p>Match insight lines are auto-generated from scoreline, goal and stat data.
Generated automatically &middot; this page is informational only.</p>
</footer>
</div>
<script>
(function () {{
  var filter = document.getElementById('country-filter');
  if (!filter) return;
  var cards = Array.prototype.slice.call(document.querySelectorAll('.card'));
  var blocks = Array.prototype.slice.call(document.querySelectorAll('.standings'));
  var empty = document.getElementById('filter-empty');
  var count = document.getElementById('filter-count');
  function apply() {{
    var v = filter.value;
    var shown = 0;
    cards.forEach(function (c) {{
      var match = (v === 'all') || (c.getAttribute('data-country') === v);
      c.style.display = match ? '' : 'none';
      if (match) shown += 1;
    }});
    blocks.forEach(function (b) {{
      var match = (v === 'all') || (b.getAttribute('data-country') === v);
      b.style.display = match ? '' : 'none';
    }});
    empty.hidden = (v === 'all') || shown > 0;
    count.textContent = (v === 'all') ? '' : (shown + ' match' + (shown === 1 ? '' : 'es'));
  }}
  filter.addEventListener('change', apply);
  apply();
}})();
</script>
</body></html>""")
    return "".join(parts)

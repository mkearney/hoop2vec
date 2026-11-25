"""Static page shells: docs/index.html (choose men's or women's) and docs/<league>/index.html (the app).

Both leagues share docs/assets/app.js + style.css; each league's data lives in docs/<league>/data/.
Run ``python -m hoop2vec site`` after changing this file (the daily job does it too).
"""
from __future__ import annotations

from .config import SITE_ROOT

BALL = ('<svg class="brand-ball" viewBox="0 0 64 64" aria-hidden="true"><circle cx="32" cy="32" r="30" fill="#eb6834"/>'
        '<path d="M2 32h60M32 2v60M10 10c12 10 12 34 0 44M54 10c-12 10-12 34 0 44" stroke="#111" stroke-width="3.5" fill="none"/></svg>')
ICON = ("data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'><circle cx='32' cy='32' r='30' fill='%23eb6834'/>"
        "<path d='M2 32h60M32 2v60M10 10c12 10 12 34 0 44M54 10c-12 10-12 34 0 44' stroke='%23111' stroke-width='3' fill='none'/></svg>")
HEAD = '''<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <meta name="description" content="{desc}">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@500&display=swap" rel="stylesheet">
  <link rel="stylesheet" href="{css}">
  <link rel="icon" href="{icon}">
</head>
'''
THEME_BTN = ('<button class="icon-btn" id="theme" title="Toggle theme" aria-label="Toggle dark mode"><svg viewBox="0 0 24 24" '
             'width="16" height="16" aria-hidden="true"><circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" stroke-width="2"/>'
             '<path d="M12 3a9 9 0 0 1 0 18z" fill="currentColor"/></svg></button>')
LEAGUES = {
    "mens": {"name": "Men's", "full": "men's college basketball", "pro": "NBA",
             "src": "barttorvik.com, ESPN, Basketball-Reference, Covers, Rotowire",
             "blurb": "Ratings, players, NBA prospects, forecasts and the 2027 bracket."},
    "womens": {"name": "Women's", "full": "women's college basketball", "pro": "WNBA", "src": "ESPN, Basketball-Reference",
               "blurb": "Ratings, players, WNBA prospects, forecasts and the 2027 bracket."},
    "nba": {"name": "NBA", "full": "the NBA", "src": "ESPN",
            "blurb": "Ratings, players, forecasts and playoff odds with the play-in."},
    "wnba": {"name": "WNBA", "full": "the WNBA", "src": "ESPN",
             "blurb": "Ratings, players, forecasts and playoff odds."},
}
PRO_LEAGUES = ("nba", "wnba")
NAV = [("ratings", "Ratings"), ("players", "Players"), ("prospects", "Prospects"), ("bracket", "Bracket"),
       ("playoffs", "Playoffs"), ("matchup", "Matchup Lab"), ("games", "Games"), ("styles", "Styles"), ("method", "Method")]


def nav(league: str) -> str:
    skip = {"prospects", "bracket"} if league in PRO_LEAGUES else {"playoffs"}
    return "\n".join(f'        <a href="#/{k}" data-route="{k}">{v}</a>' for k, v in NAV if k not in skip)


def switcher(league: str) -> str:
    return ('<nav class="leagues" aria-label="League">' + "".join(
        f'<a href="../{lg}/"{" class=\"on\" aria-current=\"page\"" if lg == league else ""}>{L["name"]}</a>'
        for lg, L in LEAGUES.items()) + "</nav>")


def league_page(league: str) -> str:
    L = LEAGUES[league]
    pro = league in PRO_LEAGUES
    title = (f"hoop2vec: {L['name']} Ratings, Forecasts and Playoff Odds" if pro
             else f"hoop2vec: {L['name']} College Basketball Ratings and Forecasts")
    desc = (f"Daily {L['name']} power ratings, game forecasts, player values, playoff odds and team styles, from "
            "leak-free models built on free public data." if pro else
            f"Daily {L['full']} power ratings, player values, {L['pro']} prospects, team style identities "
            "and the 2027 NCAA bracket, from leak-free models built on free public data.")
    return HEAD.format(title=title, desc=desc, css="../assets/style.css", icon=ICON) + f'''<body data-league="{league}">
  <header class="topbar">
    <div class="wrap topbar-inner">
      <a class="brand" href="../">{BALL}<span>hoop2vec</span></a>
      <nav class="nav" id="nav" aria-label="Sections">
{nav(league)}
      </nav>
      <div class="top-right">
        <span class="updated" id="updated"></span>
        {switcher(league)}
        {THEME_BTN}
      </div>
    </div>
  </header>
  <main class="wrap" id="app"><div class="loading">Loading…</div></main>
  <footer class="wrap footer">
    <span>hoop2vec · {L['full']} · refreshed daily · free public data: {L['src']}</span>
    <a href="https://github.com/mkearney/hoop2vec">source</a>
  </footer>
  <div id="tooltip" class="tooltip" role="tooltip"></div>
  <script src="../assets/app.js"></script>
</body>
</html>
'''


def chooser_page() -> str:
    def card(lg):
        L = LEAGUES[lg]
        return f'''
        <a class="choice card" href="{lg}/" data-league="{lg}">
          <span class="big">{L['name']}</span>
        <span class="muted small">{L['blurb']}</span>
        <span class="top5" id="top-{lg}"></span>
        <span class="go">Open {L['name'] if lg in PRO_LEAGUES else L['name'].lower()} →</span>
      </a>'''
    group = lambda title, lgs: (f'\n      <div class="group"><h2 class="choices-head">{title}</h2><div class="group-cards">'
                                + "".join(card(lg) for lg in lgs) + "\n      </div></div>")
    cards = group("College", ("mens", "womens")) + group("Pro", PRO_LEAGUES)
    return HEAD.format(title="hoop2vec: Basketball Ratings and Forecasts",
                       desc="Daily ratings, game forecasts and player values for men's and women's college basketball, the NBA and the WNBA, plus the 2027 NCAA tournaments and pro playoff odds. Built on free public data.",
                       css="assets/style.css", icon=ICON) + f'''<body class="chooser">
  <header class="topbar"><div class="wrap topbar-inner"><a class="brand" href="./">{BALL}<span>hoop2vec</span></a>
    <div class="top-right" style="margin-left:auto">{THEME_BTN}</div></div></header>
  <main class="wrap">
    <section class="choose-hero">
      <h1>Basketball ratings and forecasts</h1>
      <p class="sub">Every team in men's and women's Division I, the NBA and the WNBA gets a new rating each morning. The same models forecast games, value players and price out the 2027 NCAA tournaments and the pro playoffs. All of it runs on free public data. Each forecast uses only what was known before tip-off, and the models are graded on seasons they never saw.</p>
    </section>
    <section class="choices">{cards}
    </section>
  </main>
  <footer class="wrap footer"><span>hoop2vec · free public data only</span><a href="https://github.com/mkearney/hoop2vec">source</a></footer>
  <script>
  (() => {{
    const root = document.documentElement;
    try {{ const t = localStorage.getItem("hoop2vec-theme"); if (t) root.dataset.theme = t; }} catch (e) {{}}
    document.getElementById("theme").addEventListener("click", () => {{
      const dark = root.dataset.theme ? root.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
      root.dataset.theme = dark ? "light" : "dark";
      try {{ localStorage.setItem("hoop2vec-theme", root.dataset.theme); }} catch (e) {{}}
    }});
    const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({{"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}}[c]));
    for (const lg of ["mens", "womens", "nba", "wnba"]) {{
      fetch(lg + "/data/ratings.json", {{ cache: "no-cache" }}).then((r) => r.ok ? r.json() : null).then((d) => {{
        if (!d) return;
        document.getElementById("top-" + lg).innerHTML = "<b>Top 5 today</b>" + d.teams.slice(0, 5).map((t) =>
          `<span class="row">${{t.logo ? `<img class="logo" src="${{esc(t.logo)}}" alt="" loading="lazy">` : ""}}<span>${{t.rank}}. ${{esc(t.team)}}</span></span>`).join("");
      }}).catch(() => {{}});
    }}
  }})();
  </script>
</body>
</html>
'''


def write():
    (SITE_ROOT / "index.html").write_text(chooser_page())
    for lg in LEAGUES:
        d = SITE_ROOT / lg
        d.mkdir(parents=True, exist_ok=True)
        (d / "index.html").write_text(league_page(lg))
    (SITE_ROOT / ".nojekyll").touch()

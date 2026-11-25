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
    "mens": {"name": "Men's", "other": "womens", "other_name": "Women's", "pro": "NBA",
             "src": "barttorvik.com, ESPN, Basketball-Reference, Covers, Rotowire"},
    "womens": {"name": "Women's", "other": "mens", "other_name": "Men's", "pro": "WNBA",
               "src": "ESPN, Basketball-Reference"},
}


def league_page(league: str) -> str:
    L = LEAGUES[league]
    title = f"hoop2vec: {L['name']} College Basketball Ratings and Forecasts"
    desc = (f"Daily {L['name'].lower()} college basketball power ratings, player values, {L['pro']} prospects, team style identities "
            "and the 2027 NCAA bracket, from leak-free models built on free public data.")
    return HEAD.format(title=title, desc=desc, css="../assets/style.css", icon=ICON) + f'''<body data-league="{league}">
  <header class="topbar">
    <div class="wrap topbar-inner">
      <a class="brand" href="../">{BALL}<span>hoop2vec</span></a>
      <span class="league-pill" title="Showing {L['name'].lower()} college basketball">{L['name']}</span>
      <nav class="nav" id="nav" aria-label="Sections">
        <a href="#/ratings" data-route="ratings">Ratings</a>
        <a href="#/players" data-route="players">Players</a>
        <a href="#/prospects" data-route="prospects">Prospects</a>
        <a href="#/bracket" data-route="bracket">Bracket</a>
        <a href="#/matchup" data-route="matchup">Matchup Lab</a>
        <a href="#/games" data-route="games">Games</a>
        <a href="#/styles" data-route="styles">Styles</a>
        <a href="#/method" data-route="method">Method</a>
      </nav>
      <div class="top-right">
        <span class="updated" id="updated"></span>
        <a class="switch" href="../{L['other']}/" title="Switch to {L['other_name'].lower()} college basketball">{L['other_name']} →</a>
        {THEME_BTN}
      </div>
    </div>
  </header>
  <main class="wrap" id="app"><div class="loading">Loading…</div></main>
  <footer class="wrap footer">
    <span>hoop2vec · {L['name'].lower()} college basketball · refreshed daily · free public data: {L['src']}</span>
    <a href="https://github.com/mkearney/hoop2vec">source</a>
  </footer>
  <div id="tooltip" class="tooltip" role="tooltip"></div>
  <script src="../assets/app.js"></script>
</body>
</html>
'''


def chooser_page() -> str:
    cards = "".join(f'''
      <a class="choice card" href="{lg}/" data-league="{lg}">
        <span class="kind">{LEAGUES[lg]['name']} college basketball</span>
        <span class="big">{LEAGUES[lg]['name']}</span>
        <span class="muted small">Ratings, players, {LEAGUES[lg]['pro']} prospects, game forecasts, the 2027 bracket and team styles.</span>
        <span class="top5" id="top-{lg}"></span>
        <span class="go">Open {LEAGUES[lg]['name'].lower()} →</span>
      </a>''' for lg in ("mens", "womens"))
    return HEAD.format(title="hoop2vec: College Basketball Ratings and Forecasts",
                       desc="Daily ratings, game forecasts, player values and pro prospects for men's and women's college basketball, plus the 2027 NCAA tournaments. Built on free public data.",
                       css="assets/style.css", icon=ICON) + f'''<body class="chooser">
  <header class="topbar"><div class="wrap topbar-inner"><a class="brand" href="./">{BALL}<span>hoop2vec</span></a>
    <div class="top-right" style="margin-left:auto">{THEME_BTN}</div></div></header>
  <main class="wrap">
    <section class="choose-hero">
      <h1>College basketball ratings and forecasts</h1>
      <p class="sub">Every Division I team gets a new rating each morning, men's and women's. The same models forecast games, value players, rank pro prospects and price out the 2027 NCAA tournament. All of it runs on free public data. Each forecast uses only what was known before tip-off, and the models are graded on seasons they never saw.</p>
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
    for (const lg of ["mens", "womens"]) {{
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

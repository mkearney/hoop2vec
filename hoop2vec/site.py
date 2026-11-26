"""Static page shells: docs/index.html (choose a league) and docs/<league>/index.html (the app).

Every league shares docs/assets/app.js + style.css; each league's data lives in docs/<league>/data/.
Each publish rewrites these pages so link previews stay current: the day's summary goes into the description and
Open Graph / Twitter tags, next to a share image drawn from the same data (share.py).
Run ``python -m hoop2vec site`` after changing this file (every publish does it too).
"""
from __future__ import annotations

import html
import json

from .config import SITE_ROOT

from . import brand

LOGO = brand.mark() + brand.wordmark()          # the official logo: ⟨●⟩ hoop2vec
ICON = brand.favicon()
FONTS_CSS = ("https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@500"
             "&display=swap")
# Cloudflare Web Analytics: cookieless page-view counts (dash.cloudflare.com > Analytics & Logs > Web Analytics).
ANALYTICS = ("<script type='module' src='https://static.cloudflareinsights.com/beacon.min.js' "
             "data-cf-beacon='{\"token\": \"203a51614dc84183b570be1615329ed5\"}'></script>")


ABOUT = {"mens": "NCAA Division I men's basketball", "womens": "NCAA Division I women's basketball",
         "nba": "National Basketball Association", "wnba": "Women's National Basketball Association"}


def structured(*, title: str, desc: str, url: str, image: str | None, S: dict, league: str | None) -> dict:
    """schema.org graph: the site, this page (exact update time, image, what it's about) and, on a league page,
    today's top five as a ranked list of teams. The app rewrites the time and the list from the data it loads."""
    from .share import SITE_URL
    site_id = f"{SITE_URL}/#website"
    page = {"@type": "WebPage", "@id": f"{url}#webpage", "url": url, "name": title, "description": desc,
            "inLanguage": "en", "isPartOf": {"@id": site_id}}
    if S.get("iso"):
        page["dateModified"] = S["iso"]
    if image:
        page["primaryImageOfPage"] = {"@type": "ImageObject", "url": image, "width": 1200, "height": 630}
    if league:
        page["about"] = {"@type": "SportsOrganization", "name": ABOUT[league]}
        if S.get("top"):
            page["mainEntity"] = {"@type": "ItemList", "name": "Power ratings: top 5", "itemListOrder": "https://schema.org/ItemListOrderAscending",
                                  "numberOfItems": len(S["top"]), "itemListElement": [
                                      {"@type": "ListItem", "position": t["rank"], "item": {"@type": "SportsTeam", "name": t["team"]}}
                                      for t in S["top"]]}
    else:
        page["about"] = [{"@type": "SportsOrganization", "name": n} for n in ABOUT.values()]
    site = {"@type": "WebSite", "@id": site_id, "url": f"{SITE_URL}/", "name": "hoop2vec",
            "description": "Basketball ratings and forecasts from free public data.", "inLanguage": "en"}
    return {"@context": "https://schema.org", "@graph": [site, page]}


def head(*, title: str, desc: str, path: str, root: str, img: str | None, stamp: str | None, img_alt: str,
         modified: str | None = None, home: bool = False, S: dict | None = None, league: str | None = None) -> str:
    """<head> with everything link previews and browsers read: description, canonical URL, Open Graph and Twitter
    card tags (the day's summary and share image, versioned by date so platforms refetch), icons, theme color and
    a small schema.org record."""
    from .share import SITE_URL
    def e(x: str) -> str:  # attributes are double-quoted: escape &, <, > and " (apostrophes stay readable)
        return html.escape(x, quote=False).replace('"', "&quot;")
    url = f"{SITE_URL}/{path}"
    image = f"{SITE_URL}/{img}" + (f"?v={stamp}" if stamp else "") if img else None
    ld = structured(title=title, desc=desc, url=url, image=image, S=S or {}, league=league)
    og_img = (f'''
  <meta property="og:image" content="{e(image)}">
  <meta property="og:image:type" content="image/png">
  <meta property="og:image:width" content="1200">
  <meta property="og:image:height" content="630">
  <meta property="og:image:alt" content="{e(img_alt)}">
  <meta name="twitter:image" content="{e(image)}">
  <meta name="twitter:image:alt" content="{e(img_alt)}">''' if image else "")
    return f'''<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{e(title)}</title>
  <meta name="description" content="{e(desc)}">
  <link rel="canonical" href="{e(url)}">
  <meta name="theme-color" content="#f6f6f4" media="(prefers-color-scheme: light)">
  <meta name="theme-color" content="#121211" media="(prefers-color-scheme: dark)">
  <meta property="og:type" content="website">
  <meta property="og:site_name" content="hoop2vec">
  <meta property="og:locale" content="en_US">
  <meta property="og:title" content="{e(title)}">
  <meta property="og:description" content="{e(desc)}">
  <meta property="og:url" content="{e(url)}">
  <meta name="twitter:card" content="{"summary_large_image" if image else "summary"}">
  <meta name="twitter:title" content="{e(title)}">
  <meta name="twitter:description" content="{e(desc)}">{og_img}
  <link rel="icon" href="{ICON}" type="image/svg+xml">
  <link rel="icon" href="{root}assets/icon-32.png" sizes="32x32" type="image/png">
  <link rel="apple-touch-icon" href="{root}assets/apple-touch-icon.png">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="{FONTS_CSS}" rel="stylesheet">
  <link rel="stylesheet" href="{root}assets/style.css">
  <script type="application/ld+json" id="ld">{json.dumps(ld, ensure_ascii=False).replace("</", "<\\/")}</script>
  {ANALYTICS}
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


def _modified(S: dict) -> str | None:
    return f"{S['stamp'][:4]}-{S['stamp'][4:6]}-{S['stamp'][6:]}" if S.get("stamp") else None


def league_page(league: str, S: dict | None = None) -> str:
    from .share import summary
    L = LEAGUES[league]
    S = S or summary(league)
    pro = league in PRO_LEAGUES
    title = (f"{L['name']} power ratings and playoff odds · hoop2vec" if pro
             else f"{L['name']} college basketball ratings and forecasts · hoop2vec")
    has_img = (SITE_ROOT / league / "share.png").exists()
    alt = (f"hoop2vec {L['name']} {'power ratings' if pro else 'college basketball ratings'}"
           + (f", updated {S['updated']}" if S.get("updated") else "") + (f". {S['headline']}." if S.get("headline") else "")
           + (" Top 5: " + ", ".join(t["team"] for t in S["top"]) + "." if S.get("top") else ""))
    return head(title=title, desc=S["desc"], path=f"{league}/", root="../", img=f"{league}/share.png" if has_img else None,
                stamp=S.get("stamp"), img_alt=alt, modified=_modified(S), S=S, league=league) + f'''<body data-league="{league}">
  <header class="topbar">
    <div class="wrap topbar-inner">
      <a class="brand" href="../" aria-label="hoop2vec home">{LOGO}</a>
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
  {brand.templates()}
  <script src="../assets/app.js"></script>
</body>
</html>
'''


def _top5_html(S: dict) -> str:
    """Today's top five, written into the page (crawlers and no-JS readers see it; the app refreshes it)."""
    if not S.get("top"):
        return ""
    e = html.escape
    return "<b>Top 5 today</b>" + "".join(
        f'<span class="row">{f"<img class=\"logo\" src=\"{e(t["logo"])}\" alt=\"\" loading=\"lazy\">" if t.get("logo") else ""}'
        f'<span>{t["rank"]}. {e(t["team"])}</span></span>' for t in S["top"])


HOW = [("vector", "Ratings", "Every team is a vector of offense, defense and tempo, re-solved each morning from the games so far."),
       ("neural", "Matchup network", "A neural network learns team embeddings and scores how any two teams fit together."),
       ("latent", "Team identities", "Factor analysis places every team in a style space and names its archetype."),
       ("arc", "Forecasts", "Each game gets a margin and a win probability, graded on seasons the model never saw.")]


def how_html() -> str:
    return "".join(f'\n        <div class="how-item">{brand.art(k)}<div><h3>{t}</h3><p>{d}</p></div></div>' for k, t, d in HOW)


def chooser_page(summaries: dict | None = None) -> str:
    from .share import build_summaries
    summaries = summaries or build_summaries()

    def card(lg):
        L = LEAGUES[lg]
        return f'''
        <a class="choice card" href="{lg}/" data-league="{lg}">
          <span class="big">{L['name']}</span>
        <span class="muted small">{L['blurb']}</span>
        <span class="top5" id="top-{lg}">{_top5_html(summaries.get(lg, {}))}</span>
        <span class="go">Open {L['name'] if lg in PRO_LEAGUES else L['name'].lower()} →</span>
      </a>'''
    group = lambda title, lgs: (f'\n      <div class="group"><h2 class="choices-head">{title}</h2><div class="group-cards">'
                                + "".join(card(lg) for lg in lgs) + "\n      </div></div>")
    cards = group("College", ("mens", "womens")) + group("Pro", PRO_LEAGUES)
    H = summaries.get("home", {})
    alt = "hoop2vec: basketball ratings and forecasts for men's and women's college, the NBA and the WNBA" + "".join(
        f". {LEAGUES[lg]['name']} top 3: " + ", ".join(t["team"] for t in summaries[lg]["top"][:3])
        for lg in LEAGUES if summaries.get(lg, {}).get("top"))
    return head(title="hoop2vec · Basketball ratings and forecasts", desc=H.get("desc", ""), path="", root="",
                img="share.png" if (SITE_ROOT / "share.png").exists() else None, stamp=H.get("stamp"), img_alt=alt + ".",
                modified=_modified(H), home=True, S=H) + f'''<body class="chooser">
  <header class="topbar"><div class="wrap topbar-inner"><a class="brand" href="./" aria-label="hoop2vec home">{LOGO}</a>
    <div class="top-right" style="margin-left:auto">{THEME_BTN}</div></div></header>
  <main class="wrap">
    <section class="choose-hero">
      {brand.mark("hero-mark")}
      <h1>Basketball ratings and forecasts</h1>
      <p class="sub">Every team in men's and women's Division I, the NBA and the WNBA gets a new rating each morning. The same models forecast games, value players and price out the 2027 NCAA tournaments and the pro playoffs. All of it runs on free public data. Each forecast uses only what was known before tip-off, and the models are graded on seasons they never saw.</p>
    </section>
    <section class="choices">{cards}
    </section>
    <section class="how" aria-labelledby="how-head">
      <h2 class="choices-head" id="how-head">How it works</h2>
      <div class="how-grid">{how_html()}
      </div>
      <a class="how-more" href="mens/#/method">Read the full method →</a>
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
        // structured data follows the freshest league data (the page itself may be a cached copy)
        const m = /^(\\d{{4}}-\\d{{2}}-\\d{{2}}) (\\d{{2}}:\\d{{2}})/.exec(d.updated || ""), el = document.getElementById("ld");
        if (m && el) {{
          try {{
            const ld = JSON.parse(el.textContent), page = ld["@graph"].find((x) => x["@type"] === "WebPage"), iso = `${{m[1]}}T${{m[2]}}:00Z`;
            if (!page.dateModified || iso > page.dateModified) {{ page.dateModified = iso; el.textContent = JSON.stringify(ld).replace(/<\\//g, "<\\\\/"); }}
          }} catch (e) {{}}
        }}
      }}).catch(() => {{}});
    }}
  }})();
  </script>
</body>
</html>
'''


def write():
    from .share import build
    summaries = build()  # also redraws the share images
    (SITE_ROOT / "index.html").write_text(chooser_page(summaries))
    for lg in LEAGUES:
        d = SITE_ROOT / lg
        d.mkdir(parents=True, exist_ok=True)
        (d / "index.html").write_text(league_page(lg, summaries.get(lg)))
    (SITE_ROOT / ".nojekyll").touch()

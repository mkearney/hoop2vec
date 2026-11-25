"""Link previews: page summaries and 1200x630 share images built from the day's data.

Crawlers (iMessage, Slack, X, Facebook, LinkedIn, Discord) read static <meta> tags and never run the app, so every
publish rewrites them: site.py puts the summary in the description and Open Graph / Twitter tags, and this module
draws docs/<league>/share.png (league, date, the day's headline and the top five) and docs/share.png (all four
leagues). Fonts are bundled (hoop2vec/fonts, Inter, SIL OFL) so local runs and CI draw the same image.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import io
import json
import os
from pathlib import Path

from .config import ROOT, SITE_ROOT

SITE_URL = os.environ.get("HOOP2VEC_SITE_URL", "https://hoop2vec.net").rstrip("/")
W, H = 1200, 630
FONTS = Path(__file__).resolve().parent / "fonts"
LOGO_CACHE = ROOT / "data" / "logos"
C = {"bg": "#f6f6f4", "surface": "#fcfcfb", "line": "#e3e2de", "text": "#111111", "text2": "#52514e",
     "text3": "#8e8d86", "accent": "#2a78d6", "hot": "#eb6834"}
NAMES = {"mens": ("Men's", "college basketball"), "womens": ("Women's", "college basketball"),
         "nba": ("NBA", "power ratings"), "wnba": ("WNBA", "power ratings")}
FULL = {"mens": "Men's college basketball", "womens": "Women's college basketball", "nba": "NBA", "wnba": "WNBA"}


# ------------------------------------------------------------------ summaries (text)
def _load(league: str, name: str):
    f = SITE_ROOT / league / "data" / name
    try:
        return json.loads(f.read_text())
    except Exception:
        return None


def _date(updated: str | None) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(updated)[:10])
    except ValueError:
        return None


def _iso(updated: str | None) -> str | None:
    """'2026-11-25 17:27 UTC' -> '2026-11-25T17:27:00Z' (the data's own update time, for structured data)."""
    try:
        return dt.datetime.strptime(str(updated).replace(" UTC", ""), "%Y-%m-%d %H:%M").strftime("%Y-%m-%dT%H:%M:00Z")
    except ValueError:
        return None


def _pct(p: float) -> str:
    return "<1%" if p < 0.005 else f"{round(100 * p)}%"


def summary(league: str) -> dict:
    """The day's facts for a league page: top teams, the headline line, the meta description."""
    r = _load(league, "ratings.json")
    out = {"league": league, "top": [], "headline": None, "updated": None, "stamp": None}
    if not r or not r.get("teams"):
        out["desc"] = _desc(league, None)
        return out
    d = _date(r.get("updated"))
    out["updated"] = f"{d:%b} {d.day}, {d.year}" if d else None
    out["stamp"] = f"{d:%Y%m%d}" if d else None
    out["iso"] = _iso(r.get("updated"))
    out["top"] = [{"rank": t["rank"], "team": t["team"], "power": t.get("power"), "logo": t.get("logo")}
                  for t in sorted(r["teams"], key=lambda t: t["rank"])[:5]]
    no1 = out["top"][0]["team"]
    fact = None
    if league in ("nba", "wnba"):
        p = _load(league, "playoffs.json")
        if p and p.get("phase") == "complete" and p.get("champion"):
            out["headline"] = f"{p['season']} champion: {p['champion']}"
            fact = f"The {p['season']} season is over: the {p['champion']} won the title."
        elif p and p.get("teams"):
            fav = max(p["teams"], key=lambda t: t["p_title"])
            out["headline"] = f"Title favorite: {fav['team']}, {_pct(fav['p_title'])}"
            fact = _fact(no1, fav["team"], fav["p_title"])
    else:
        b = _load(league, "bracket.json")
        if b and b.get("advancement"):
            fav = max(b["advancement"], key=lambda a: a.get("champ") or 0)
            out["headline"] = f"Title favorite: {fav['team']}, {_pct(fav['champ'])}"
            fact = _fact(no1, fav["team"], fav["champ"])
    if not out["headline"]:
        out["headline"] = f"No. 1 today: {no1}"
        fact = f"No. 1 today: {no1}."
    out["desc"] = _desc(league, fact)
    return out


def _fact(no1: str, fav: str, p: float) -> str:
    if fav == no1:
        return f"No. 1 today and the title favorite: {no1} ({_pct(p)})."
    return f"No. 1 today: {no1}. Title favorite: {fav} ({_pct(p)})."


def _desc(league: str, fact: str | None) -> str:
    base = {"mens": "Men's college basketball power ratings, game forecasts, player values, NBA prospects and 2027 "
                    "NCAA tournament odds, updated every morning.",
            "womens": "Women's college basketball power ratings, game forecasts, player values, WNBA prospects and "
                      "2027 NCAA tournament odds, updated every morning.",
            "nba": "NBA power ratings, game forecasts, player values and playoff odds, updated every morning.",
            "wnba": "WNBA power ratings, game forecasts, player values and playoff odds, updated every morning.",
            "home": "Power ratings, game forecasts and player values for men's and women's college basketball, the "
                    "NBA and the WNBA, updated every morning from free public data."}[league]
    return f"{base} {fact}" if fact else base


# ------------------------------------------------------------------ drawing
def _font(weight: str, size: int):
    from PIL import ImageFont
    try:
        return ImageFont.truetype(str(FONTS / f"Inter-{weight}.otf"), size)
    except OSError:
        return ImageFont.load_default(size)


def _fit(d, text: str, font, width: int) -> str:
    if d.textlength(text, font=font) <= width:
        return text
    while text and d.textlength(text + "…", font=font) > width:
        text = text[:-1]
    return text.rstrip() + "…"


def _ball(img, x: int, y: int, size: int):
    """The site's basketball mark (the same paths as the SVG logo), drawn at 4x and scaled down for smooth edges."""
    from PIL import Image, ImageDraw
    s = size * 4
    k = s / 64.0                                   # SVG viewBox is 64 x 64
    m = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(m)
    d.ellipse((2 * k, 2 * k, 62 * k, 62 * k), fill=C["hot"])
    w = max(2, round(3.5 * k))

    def bez(p0, p1, p2, p3, n=40):
        pts = []
        for i in range(n + 1):
            t = i / n
            a, b, c, e = (1 - t) ** 3, 3 * (1 - t) ** 2 * t, 3 * (1 - t) * t ** 2, t ** 3
            pts.append(((a * p0[0] + b * p1[0] + c * p2[0] + e * p3[0]) * k, (a * p0[1] + b * p1[1] + c * p2[1] + e * p3[1]) * k))
        return pts
    d.line([(2 * k, 32 * k), (62 * k, 32 * k)], fill="#111111", width=w)
    d.line([(32 * k, 2 * k), (32 * k, 62 * k)], fill="#111111", width=w)
    d.line(bez((10, 10), (22, 20), (22, 44), (10, 54)), fill="#111111", width=w, joint="curve")
    d.line(bez((54, 10), (42, 20), (42, 44), (54, 54)), fill="#111111", width=w, joint="curve")
    mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(mask).ellipse((2 * k, 2 * k, 62 * k, 62 * k), fill=255)
    m.putalpha(Image.composite(m.getchannel("A"), Image.new("L", (s, s), 0), mask))
    img.alpha_composite(m.resize((size, size), Image.LANCZOS), (x, y))


def _logo(url: str | None, size: int):
    """Team logo (ESPN CDN), cached on disk; None if unavailable."""
    if not url:
        return None
    from PIL import Image
    LOGO_CACHE.mkdir(parents=True, exist_ok=True)
    f = LOGO_CACHE / (hashlib.sha1(url.encode()).hexdigest()[:16] + ".png")
    try:
        if not f.exists():
            from .http import get
            f.write_bytes(get(url, timeout=20, retries=1).content)
        im = Image.open(io.BytesIO(f.read_bytes())).convert("RGBA")
        im.thumbnail((size, size), Image.LANCZOS)
        return im
    except Exception:
        return None


def _brand(img, d, x: int, y: int):
    _ball(img, x, y, 40)
    d.text((x + 54, y + 20), "hoop2vec", font=_font("ExtraBold", 32), fill=C["text"], anchor="lm")


def render_league(league: str, S: dict, path: Path):
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (W, H), C["bg"])
    d = ImageDraw.Draw(img)
    _brand(img, d, 64, 56)
    big, small = NAMES[league]
    d.text((64, 232), big, font=_font("ExtraBold", 104), fill=C["text"], anchor="ls")
    d.text((64, 286), small, font=_font("SemiBold", 40), fill=C["text2"], anchor="ls")
    if S.get("updated"):
        d.text((64, 380), f"Updated {S['updated']}", font=_font("Medium", 30), fill=C["text2"], anchor="ls")
    if S.get("headline"):
        d.text((64, 432), _fit(d, S["headline"], _font("SemiBold", 34), 530), font=_font("SemiBold", 34),
               fill=C["accent"], anchor="ls")
    d.text((64, 574), SITE_URL.split("://", 1)[-1] + f"/{league}", font=_font("Medium", 24), fill=C["text3"], anchor="ls")
    # top five
    x0, y0, x1, y1 = 640, 56, 1136, 574
    d.rounded_rectangle((x0, y0, x1, y1), radius=28, fill=C["surface"], outline=C["line"], width=2)
    d.text((x0 + 36, y0 + 52), "TOP 5 TODAY", font=_font("SemiBold", 22), fill=C["text3"], anchor="ls")
    d.text((x1 - 36, y0 + 52), "POWER", font=_font("SemiBold", 22), fill=C["text3"], anchor="rs")
    row_h = 84
    for i, t in enumerate(S.get("top", [])[:5]):
        cy = y0 + 112 + i * row_h
        if i:
            d.line((x0 + 36, cy - row_h // 2, x1 - 36, cy - row_h // 2), fill=C["line"], width=2)
        d.text((x0 + 36, cy), str(t["rank"]), font=_font("SemiBold", 30), fill=C["text3"], anchor="lm")
        lg = _logo(t.get("logo"), 48)
        if lg is not None:
            img.alpha_composite(lg, (x0 + 80 + (48 - lg.width) // 2, cy - lg.height // 2))
        name = _fit(d, t["team"], _font("SemiBold", 32), 250)
        d.text((x0 + 144, cy), name, font=_font("SemiBold", 32), fill=C["text"], anchor="lm")
        if t.get("power") is not None:
            v = t["power"]
            d.text((x1 - 36, cy), f"{'+' if v > 0 else '−' if v < 0 else ''}{abs(v):.1f}", font=_font("Medium", 30),
                   fill=C["text2"], anchor="rm")
    path.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(path, optimize=True)


def render_home(summaries: dict, path: Path):
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (W, H), C["bg"])
    d = ImageDraw.Draw(img)
    _brand(img, d, 56, 48)
    d.text((56, 178), "Basketball ratings and forecasts", font=_font("ExtraBold", 62), fill=C["text"], anchor="ls")
    upd = next((s["updated"] for s in summaries.values() if s.get("updated")), None)
    sub = "Men's and women's college, NBA and WNBA" + (f" · updated {upd}" if upd else "")
    d.text((56, 228), sub, font=_font("Medium", 28), fill=C["text2"], anchor="ls")
    cw, gap, top = (W - 2 * 56 - 3 * 20) // 4, 20, 272
    for k, lg in enumerate(("mens", "womens", "nba", "wnba")):
        S = summaries.get(lg, {})
        x = 56 + k * (cw + gap)
        d.rounded_rectangle((x, top, x + cw, H - 48), radius=22, fill=C["surface"], outline=C["line"], width=2)
        d.text((x + 24, top + 58), NAMES[lg][0], font=_font("ExtraBold", 42), fill=C["text"], anchor="ls")
        d.text((x + 24, top + 92), ("COLLEGE" if lg in ("mens", "womens") else "PRO") + " · TOP 3", font=_font("SemiBold", 18),
               fill=C["text3"], anchor="ls")
        for i, t in enumerate(S.get("top", [])[:3]):
            cy = top + 140 + i * 58
            lo = _logo(t.get("logo"), 34)
            if lo is not None:
                img.alpha_composite(lo, (x + 24 + (34 - lo.width) // 2, cy - lo.height // 2))
            d.text((x + 70, cy), _fit(d, t["team"], _font("SemiBold", 22), cw - 86),  # order = rank
                   font=_font("SemiBold", 22), fill=C["text"], anchor="lm")
    path.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(path, optimize=True)


def render_icons(out_dir: Path):
    """PNG icons for the platforms that ignore SVG favicons (iOS home screen, older browsers)."""
    from PIL import Image
    for name, size, pad in (("apple-touch-icon.png", 180, 22), ("icon-512.png", 512, 40), ("icon-32.png", 32, 1)):
        f = out_dir / name
        if f.exists():
            continue
        img = Image.new("RGBA", (size, size), C["bg"] if name.startswith("apple") else (0, 0, 0, 0))
        _ball(img, pad, pad, size - 2 * pad)
        img.save(f, optimize=True)


def build_summaries() -> dict:
    out = {lg: summary(lg) for lg in NAMES}
    out["home"] = {"desc": _desc("home", None),
                   "stamp": max((s["stamp"] for s in out.values() if s.get("stamp")), default=None),
                   "iso": max((s["iso"] for s in out.values() if s.get("iso")), default=None)}
    return out


def build() -> dict:
    """Summaries for every page, and their share images. Never raises (link previews must not break a publish)."""
    out = build_summaries()
    try:
        for lg in NAMES:
            if out[lg]["top"]:
                render_league(lg, out[lg], SITE_ROOT / lg / "share.png")
        render_home(out, SITE_ROOT / "share.png")
        render_icons(SITE_ROOT / "assets")
    except Exception as e:  # missing Pillow or fonts: keep the text metadata, skip the images
        print("  share images skipped:", repr(e))
    return out

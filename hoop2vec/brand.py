"""The hoop2vec identity: the bracket mark, the wordmark, and four spot illustrations.

The official logo is the bracket mark (a basketball between vector brackets, ⟨●⟩) next to the wordmark
"hoop2vec" with an orange 2. The mark also stands alone (favicon, app icons, loading state, home hero).
The spot illustrations sit on the pages they describe: Vector Seam (ratings), Neural Net (the matchup network),
Shot Arc (game forecasts) and Latent Space (team identities).

Inline SVGs carry no colors of their own: classes map to theme tokens in style.css (``.art .ink`` etc.), so every
mark follows the light/dark theme. The favicon and the PNG icons, which can't read the page's CSS, carry their own.
"""
from __future__ import annotations

import math


def _r(x: float) -> str:
    return f"{x:.1f}".rstrip("0").rstrip(".")


# ------------------------------------------------------------------ the mark: ⟨●⟩
# Drawn on a 120-unit grid, cropped to the ink: brackets with round caps span x 9.5–110.5, y 22.5–97.5.
MARK_VIEWBOX = "9 22 102 76"
BRACKETS = "M33 27L14 60L33 93M87 27L106 60L87 93"
BALL_R = 25
_a = math.radians(40)
_sx, _sy = 60 - BALL_R * math.sin(_a), 60 - BALL_R * math.cos(_a)
SEAMS = (f"M35 60H85M60 35V85M{_r(_sx)} {_r(_sy)}Q52.5 60 {_r(_sx)} {_r(120 - _sy)}"
         f"M{_r(120 - _sx)} {_r(_sy)}Q67.5 60 {_r(120 - _sx)} {_r(120 - _sy)}")


def mark(cls: str = "brand-mark") -> str:
    """The bracket mark for inline use. The seams are drawn in the background color (``--art-cut``)."""
    return (f'<svg class="art {cls}" viewBox="{MARK_VIEWBOX}" aria-hidden="true">'
            f'<path class="s-ink" d="{BRACKETS}" fill="none" stroke-width="9" stroke-linecap="round" stroke-linejoin="round"/>'
            f'<circle class="f-ball" cx="60" cy="60" r="{BALL_R}"/>'
            f'<path class="s-cut" d="{SEAMS}" fill="none" stroke-width="3.4"/></svg>')


def wordmark() -> str:
    return '<span class="brand-word">hoop<em>2</em>vec</span>'


def favicon() -> str:
    """SVG favicon as a data URI: the mark on a square canvas, seams cut out with a mask so any tab color shows
    through, brackets dark on light browsers and light on dark ones."""
    svg = ("<svg xmlns='http://www.w3.org/2000/svg' viewBox='8 8 104 104'>"
           "<style>.k{stroke:#111110}@media (prefers-color-scheme:dark){.k{stroke:#f2f0eb}}</style>"
           f"<mask id='m'><circle cx='60' cy='60' r='{BALL_R}' fill='#fff'/>"
           f"<path d='{SEAMS}' stroke='#000' stroke-width='4' fill='none'/></mask>"
           f"<path class='k' d='{BRACKETS}' fill='none' stroke-width='10' stroke-linecap='round' stroke-linejoin='round'/>"
           f"<circle cx='60' cy='60' r='{BALL_R + 1}' fill='#eb6834' mask='url(#m)'/></svg>")
    return "data:image/svg+xml," + svg.replace("#", "%23")


# ------------------------------------------------------------------ spot illustrations (120 x 120)
def _vector() -> str:
    a = math.radians(45)
    x0, y0 = 54 - 40 * math.sin(a), 60 - 40 * math.cos(a)
    x1 = 108 - x0
    return (f'<circle class="f-ball" cx="54" cy="60" r="40"/>'
            f'<path class="s-ink" d="M54 20V100M{_r(x0)} {_r(y0)}Q46 60 {_r(x0)} {_r(120 - y0)}M{_r(x1)} {_r(y0)}Q62 60 {_r(x1)} {_r(120 - y0)}" '
            f'fill="none" stroke-width="5"/>'
            f'<path class="s-ink" d="M14 60H104" stroke-width="5" stroke-linecap="round"/>'
            f'<path class="f-ink s-ink" d="M99 49L113 60L99 71Z" stroke-width="3" stroke-linejoin="round"/>')


def _neural() -> str:
    layers = [[(27, 24), (47, 24), (73, 24), (93, 24)], [(38, 62), (60, 62), (82, 62)], [(49, 98), (71, 98)]]
    edges = "".join(f"M{p[0]} {p[1]}L{q[0]} {q[1]}" for A, B in zip(layers, layers[1:]) for p in A for q in B)
    out = f'<path class="s-ink" d="{edges}" fill="none" stroke-width="2.2" stroke-linecap="round" opacity=".9"/>'
    out += '<path class="s-ball" d="M15 24H105" stroke-width="12" stroke-linecap="round"/>'
    out += "".join(f'<circle class="f-cut" cx="{x}" cy="{y}" r="4"/>' for x, y in layers[0])
    out += "".join(f'<circle class="f-ink s-cut" cx="{x}" cy="{y}" r="6" stroke-width="2.5"/>' for x, y in layers[1])
    out += "".join(f'<circle class="f-blue s-cut" cx="{x}" cy="{y}" r="6.5" stroke-width="2.5"/>' for x, y in layers[2])
    return out


def _arc() -> str:
    p0, p1, p2 = 98, 18, 40                          # quadratic through (0, 98), (.5, 18), (1, 40); x = 12 + 72t
    qa = 2 * (p2 - 2 * p1 + p0)
    qb = p2 - p0 - qa
    pts = [(12 + 72 * t, qa * t * t + qb * t + p0) for t in (k / 2000 * 0.9 for k in range(2001))]
    cum = [0.0]
    for p, q in zip(pts, pts[1:]):
        cum.append(cum[-1] + math.hypot(q[0] - p[0], q[1] - p[1]))
    dots, n = "", 8
    for i in range(n):
        target = cum[-1] * i / (n - 1)
        x, y = pts[min(range(len(cum)), key=lambda j: abs(cum[j] - target))]
        f = i / (n - 1)
        dots += f'<circle class="f-ink" cx="{_r(x)}" cy="{_r(y)}" r="{1.9 + 1.9 * f:.2f}" opacity="{0.45 + 0.55 * f:.2f}"/>'
    return (dots + '<path class="s-ink" d="M112 26V66" stroke-width="5" stroke-linecap="round"/>'
            '<path class="s-ink" d="M72 62L80 92M106 62L98 92M72 62L98 92M106 62L80 92M89 62L76 78M89 62L102 78" '
            'stroke-width="2.6" stroke-linecap="round" fill="none"/>'
            '<circle class="f-ball" cx="89" cy="44" r="11"/>'
            '<path class="s-cut" d="M78 44H100M89 33V55" stroke-width="2"/>'
            '<path class="s-ball" d="M70 62H108" stroke-width="6" stroke-linecap="round"/>')


def _latent() -> str:
    a = math.radians(48)
    ex, ey = 60 - 40 * math.sin(a), 60 - 40 * math.cos(a)
    ex2 = 120 - ex
    nodes = [(60, 20), (60, 100), (20, 60), (100, 60)] + [(60 + sx * 40 * math.sin(a), 60 + sy * 40 * math.cos(a))
                                                          for sx in (-1, 1) for sy in (-1, 1)]
    pts = [(44, 44, 3.2), (78, 40, 2.4), (80, 79, 3.2), (41, 82, 2.4), (70, 50, 2.2), (49, 70, 2.2)]
    return (f'<circle class="f-soft s-ink" cx="60" cy="60" r="40" stroke-width="2.5"/>'
            f'<path class="s-ink" d="M20 60H100M60 20V100M{_r(ex)} {_r(ey)}Q50 60 {_r(ex)} {_r(120 - ey)}'
            f'M{_r(ex2)} {_r(ey)}Q70 60 {_r(ex2)} {_r(120 - ey)}" fill="none" stroke-width="2.5"/>'
            + "".join(f'<circle class="f-blue" cx="{x}" cy="{y}" r="{r}"/>' for x, y, r in pts)
            + "".join(f'<circle class="f-ink s-cut" cx="{_r(x)}" cy="{_r(y)}" r="5" stroke-width="2.5"/>' for x, y in nodes)
            + '<circle class="f-ball s-cut" cx="60" cy="60" r="8" stroke-width="2.5"/>')


ART = {"vector": _vector, "neural": _neural, "arc": _arc, "latent": _latent}


def art(name: str, cls: str = "spot") -> str:
    return f'<svg class="art {cls}" viewBox="0 0 120 120" aria-hidden="true">{ART[name]()}</svg>'


def templates() -> str:
    """<template> elements holding the mark and the spot art, read by app.js (one source for the SVGs)."""
    return "".join(f'<template id="art-{k}">{art(k)}</template>' for k in ART) + f'<template id="art-mark">{mark("spot")}</template>'


# ------------------------------------------------------------------ raster (share images, PNG icons)
def draw_mark(img, x: float, y: float, height: float, ink: str = "#111110", ball: str = "#eb6834", cut: str | None = None):
    """Draw the mark with Pillow, its ink box at (x, y), ``height`` pixels tall (width = height x 102/76).
    Drawn at 4x and scaled down. ``cut`` is the seam color (default: transparent cutouts)."""
    from PIL import Image, ImageDraw
    k = 4 * height / 76.0
    W, H = round(102 * k), round(76 * k)
    tx = lambda px, py: ((px - 9) * k, (py - 22) * k)  # noqa: E731  grid -> layer pixels
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    w = 9 * k
    for pts in (((33, 27), (14, 60), (33, 93)), ((87, 27), (106, 60), (87, 93))):
        d.line([tx(*p) for p in pts], fill=ink, width=round(w), joint="curve")
        for p in (pts[0], pts[2]):
            cx, cy = tx(*p)
            d.ellipse((cx - w / 2, cy - w / 2, cx + w / 2, cy + w / 2), fill=ink)
    # ball with seams: seams are cut from the ball's mask, then optionally painted
    bx, by = tx(60, 60)
    r = BALL_R * k
    ball_mask = Image.new("L", (W, H), 0)
    ImageDraw.Draw(ball_mask).ellipse((bx - r, by - r, bx + r, by + r), fill=255)
    seam = Image.new("L", (W, H), 0)
    sd = ImageDraw.Draw(seam)
    sw = round(3.4 * k)
    sd.line([tx(35, 60), tx(85, 60)], fill=255, width=sw)
    sd.line([tx(60, 35), tx(60, 85)], fill=255, width=sw)

    def quad(p0, p1, p2, n=48):
        return [tx((1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * p1[0] + t * t * p2[0],
                   (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * p1[1] + t * t * p2[1]) for t in (i / n for i in range(n + 1))]
    sd.line(quad((_sx, _sy), (52.5, 60), (_sx, 120 - _sy)), fill=255, width=sw, joint="curve")
    sd.line(quad((120 - _sx, _sy), (67.5, 60), (120 - _sx, 120 - _sy)), fill=255, width=sw, joint="curve")
    from PIL import ImageChops
    solid = Image.new("RGBA", (W, H), ball)
    layer.paste(solid, (0, 0), ImageChops.subtract(ball_mask, seam))
    if cut:
        layer.paste(Image.new("RGBA", (W, H), cut), (0, 0), ImageChops.multiply(ball_mask, seam))
    layer = layer.resize((max(1, round(W / 4)), max(1, round(H / 4))), Image.LANCZOS)
    img.alpha_composite(layer, (round(x), round(y)))
    return layer.width

"""Project-wide paths and constants."""
from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Which competition this process works on. Everything below (paths, data sources, game length) follows from it.
# Set HOOP2VEC_LEAGUE=womens (or pass --womens to the CLI) for the women's game.
LEAGUE = os.environ.get("HOOP2VEC_LEAGUE", "mens")
if LEAGUE not in ("mens", "womens"):
    raise ValueError(f"HOOP2VEC_LEAGUE must be 'mens' or 'womens', not {LEAGUE!r}")
WOMENS = LEAGUE == "womens"

DATA = ROOT / "data" / LEAGUE       # local cache (not committed)
RAW = DATA / "raw"
PROC = DATA / "processed"
CACHE = DATA / "cache"
HIST = ROOT / "history" / LEAGUE    # compact committed copy of completed seasons
ARTIFACTS = ROOT / "artifacts" / LEAGUE  # trained models, crosswalk, team metadata, backtests (committed)
OVERRIDES = ROOT / "overrides" / LEAGUE
SITE_ROOT = ROOT / "docs"           # GitHub Pages (main branch, /docs): chooser page + shared assets
SITE = SITE_ROOT / LEAGUE           # docs/mens or docs/womens
SITE_DATA = SITE / "data"

for _p in (RAW, PROC, CACHE, ARTIFACTS, SITE_DATA):
    _p.mkdir(parents=True, exist_ok=True)

ESPN_SPORT = "womens-college-basketball" if WOMENS else "mens-college-basketball"
ESPN_BASE = f"https://site.api.espn.com/apis/site/v2/sports/basketball/{ESPN_SPORT}"
REG_PERIODS = 4 if WOMENS else 2          # women's games: four 10-minute quarters; men's: two 20-minute halves
PERIOD_SECONDS = 600 if WOMENS else 1200
PRO = "WNBA" if WOMENS else "NBA"

# men's: Torvik games + player logs since 2008, ESPN play-by-play since 2015.
# women's: ESPN only; box scores are complete from 2019 and play-by-play from 2022.
FIRST_SEASON = 2019 if WOMENS else 2008
FIRST_PBP_SEASON = 2020 if WOMENS else 2015
FIRST_TRAIN_SEASON = 2020 if WOMENS else 2009

USER_AGENT = os.environ.get("HOOP2VEC_UA", "hoop2vec-research/1.0 (non-commercial academic research)")
# minimum seconds between requests to a host (barttorvik.com asks for a 10s crawl delay)
HOST_MIN_INTERVAL = {"barttorvik.com": 10.0, "basketball-reference.com": 6.0}  # robots: 10s, 3s crawl delays


def today() -> dt.date:
    override = os.environ.get("HOOP2VEC_TODAY")
    if override:
        return dt.date.fromisoformat(override)
    return dt.date.today()


def season_for(d: dt.date) -> int:
    """Season label = calendar year in which the season ends (2026-27 -> 2027)."""
    return d.year + 1 if d.month >= 8 else d.year


def current_season() -> int:
    return season_for(today())


def season_dates(season: int) -> tuple[dt.date, dt.date]:
    return dt.date(season - 1, 11, 1), dt.date(season, 4, 12)

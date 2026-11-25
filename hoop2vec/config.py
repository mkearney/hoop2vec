"""Project-wide paths and constants."""
from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Which competition this process works on. Everything below (paths, data sources, game length) follows from it.
# Set HOOP2VEC_LEAGUE=womens|nba|wnba (or pass --womens / --nba / --wnba to the CLI); the default is men's college.
LEAGUES = ("mens", "womens", "nba", "wnba")
LEAGUE = os.environ.get("HOOP2VEC_LEAGUE", "mens")
if LEAGUE not in LEAGUES:
    raise ValueError(f"HOOP2VEC_LEAGUE must be one of {LEAGUES}, not {LEAGUE!r}")
WOMENS = LEAGUE == "womens"          # women's college (ESPN-built data)
PRO_LEAGUE = LEAGUE in ("nba", "wnba")  # pro leagues: ESPN-built data, playoff series instead of a bracket
COLLEGE = not PRO_LEAGUE
ESPN_DATA = LEAGUE != "mens"         # everything from ESPN (men's college uses barttorvik.com first)

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

ESPN_SPORT = {"mens": "mens-college-basketball", "womens": "womens-college-basketball", "nba": "nba", "wnba": "wnba"}[LEAGUE]
ESPN_BASE = f"https://site.api.espn.com/apis/site/v2/sports/basketball/{ESPN_SPORT}"
REG_PERIODS = 2 if LEAGUE == "mens" else 4  # men's college: two 20-minute halves; everyone else: four quarters
PERIOD_SECONDS = {"mens": 1200, "womens": 600, "nba": 720, "wnba": 600}[LEAGUE]
GAME_MINUTES = REG_PERIODS * PERIOD_SECONDS / 60   # 40, or 48 in the NBA
PRO = "WNBA" if LEAGUE in ("womens", "wnba") else "NBA"   # the pro league a college player is projected to
LEAGUE_NAME = {"mens": "men's college", "womens": "women's college", "nba": "NBA", "wnba": "WNBA"}[LEAGUE]

# men's: Torvik games + player logs since 2008, ESPN play-by-play since 2015.
# women's: ESPN only; box scores are complete from 2019 and play-by-play from 2020.
# NBA / WNBA: ESPN box scores and play-by-play since 2008.
FIRST_SEASON = {"mens": 2008, "womens": 2019, "nba": 2008, "wnba": 2008}[LEAGUE]
FIRST_PBP_SEASON = {"mens": 2015, "womens": 2020, "nba": 2008, "wnba": 2008}[LEAGUE]
FIRST_TRAIN_SEASON = {"mens": 2009, "womens": 2020, "nba": 2009, "wnba": 2009}[LEAGUE]

USER_AGENT = os.environ.get("HOOP2VEC_UA", "hoop2vec-research/1.0 (non-commercial academic research)")
# minimum seconds between requests to a host (barttorvik.com asks for a 10s crawl delay)
HOST_MIN_INTERVAL = {"barttorvik.com": 10.0, "basketball-reference.com": 6.0}  # robots: 10s, 3s crawl delays


def today() -> dt.date:
    override = os.environ.get("HOOP2VEC_TODAY")
    if override:
        return dt.date.fromisoformat(override)
    return dt.date.today()


def season_for(d: dt.date) -> int:
    """Season label = calendar year in which the season ends (2026-27 -> 2027). The WNBA plays inside one calendar
    year (May-October), so its label is that year, and the offseason belongs to the season just finished."""
    if LEAGUE == "wnba":
        return d.year if d.month >= 4 else d.year - 1
    if LEAGUE == "nba" and dt.date(2020, 7, 1) <= d <= dt.date(2020, 11, 30):
        return 2020  # the 2019-20 season finished in the Orlando bubble in October 2020
    return d.year + 1 if d.month >= 8 else d.year


def current_season() -> int:
    return season_for(today())


# expected possessions per game are clipped to this range (college ~70 per 40 min, WNBA ~80, NBA ~100 per 48)
POSS_RANGE = {"mens": (55, 85), "womens": (55, 85), "nba": (85, 118), "wnba": (65, 95)}[LEAGUE]


def season_start(season: int) -> dt.date:
    """Day 0 of a season for the model's day-of-season feature (about when real games start)."""
    if LEAGUE == "nba":
        return {2020: dt.date(2019, 10, 22), 2021: dt.date(2020, 12, 22)}.get(season, dt.date(season - 1, 10, 20))
    if LEAGUE == "wnba":
        return dt.date(season, 5, 15)
    return dt.date(season - 1, 11, 1)


def season_dates(season: int) -> tuple[dt.date, dt.date]:
    """First and last calendar day that can hold a game of this season (preseason and All-Star games are filtered
    later by season type)."""
    if LEAGUE == "nba":
        if season == 2020:
            return dt.date(2019, 10, 1), dt.date(2020, 10, 15)
        if season == 2021:
            return dt.date(2020, 12, 20), dt.date(2021, 7, 25)
        return dt.date(season - 1, 10, 1), dt.date(season, 6, 30)
    if LEAGUE == "wnba":
        return dt.date(season, 4, 15), dt.date(season, 11, 10)
    return dt.date(season - 1, 11, 1), dt.date(season, 4, 12)

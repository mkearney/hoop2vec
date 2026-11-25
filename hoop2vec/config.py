"""Project-wide paths and constants."""
from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"            # local cache (not committed)
RAW = DATA / "raw"
PROC = DATA / "processed"
CACHE = DATA / "cache"
HIST = ROOT / "history"         # compact committed copy of completed seasons
ARTIFACTS = ROOT / "artifacts"  # trained models, crosswalk, team metadata, backtests (committed)
OVERRIDES = ROOT / "overrides"
SITE = ROOT / "docs"            # GitHub Pages (main branch, /docs)
SITE_DATA = SITE / "data"

for _p in (RAW, PROC, CACHE, ARTIFACTS, SITE_DATA):
    _p.mkdir(parents=True, exist_ok=True)

FIRST_SEASON = 2008      # first season Torvik publishes games + player logs for
FIRST_PBP_SEASON = 2015  # first season of usable ESPN play-by-play (style layer)
FIRST_TRAIN_SEASON = 2009

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

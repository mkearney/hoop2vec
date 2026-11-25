"""Basketball-Reference season tables (NBA outcomes for the prospect models).

One page per NBA season (``/leagues/NBA_<year>_advanced.html``, allowed by robots.txt), requested at most every
6 seconds (robots.txt asks for 3) and cached on disk; completed seasons are committed in ``history/nba_seasons.parquet``,
so a normal run fetches only the season in progress.
"""
from __future__ import annotations

import time

import pandas as pd
from bs4 import BeautifulSoup

from ..config import RAW
from ..http import cached_bytes

BASE = "https://www.basketball-reference.com"
OUT = RAW / "bbref"
NUM = ["age", "g", "gs", "mp", "per", "ts_pct", "usg_pct", "ws", "ws_per_48", "obpm", "dbpm", "bpm", "vorp",
       "tpar", "ftr", "orb_pct", "drb_pct", "trb_pct", "ast_pct", "stl_pct", "blk_pct", "tov_pct", "ows", "dws"]
PER_GAME = ["pts", "trb", "ast", "stl", "blk", "tov", "fg_pct", "fg3_pct", "fg3a", "ft_pct", "mpg"]


def _table(season: int, page: str, table_ids, refresh: bool = False):
    url = f"{BASE}/leagues/NBA_{season}_{page}.html"
    path = OUT / f"NBA_{season}_{page}.html"
    try:
        raw = cached_bytes(url, path, refresh=refresh)
    except Exception as e:
        if "429" not in str(e):
            raise
        time.sleep(90)  # rate limited: wait once, politely
        raw = cached_bytes(url, path, refresh=refresh)
    soup = BeautifulSoup(raw, "lxml")
    for tid in table_ids:
        t = soup.find("table", id=tid)
        if t is not None:
            return t
    raise ValueError(f"no table on {url}")


def _rows(table) -> pd.DataFrame:
    rows = []
    for tr in table.find("tbody").find_all("tr"):
        if "thead" in (tr.get("class") or []):
            continue
        cell = tr.find(attrs={"data-stat": "name_display"}) or tr.find(attrs={"data-stat": "player"})
        if cell is None or not cell.get_text(strip=True):
            continue
        rec = {"bbref_id": cell.get("data-append-csv") or (cell.a["href"].split("/")[-1].split(".")[0] if cell.a else None),
               "name": cell.get_text(strip=True).rstrip("*")}
        for td in tr.find_all("td"):
            rec[td.get("data-stat")] = td.get_text(strip=True)
        rows.append(rec)
    return pd.DataFrame(rows)


def per_game(season: int, refresh: bool = False) -> pd.DataFrame:
    """Per-game box score line per player (season totals row for traded players)."""
    df = _rows(_table(season, "per_game", ("per_game_stats", "per_game"), refresh))
    ren = {"pts_per_g": "pts", "trb_per_g": "trb", "ast_per_g": "ast", "stl_per_g": "stl", "blk_per_g": "blk",
           "tov_per_g": "tov", "fg_pct": "fg_pct", "fg3_pct": "fg3_pct", "fg3a_per_g": "fg3a", "ft_pct": "ft_pct",
           "mp_per_g": "mpg"}
    df = df.rename(columns=ren)
    for c in PER_GAME:
        df[c] = pd.to_numeric(df[c], errors="coerce") if c in df else float("nan")
    df = df.drop_duplicates("bbref_id", keep="first")
    df["season"] = season
    return df[["season", "bbref_id"] + PER_GAME]


def advanced(season: int, refresh: bool = False) -> pd.DataFrame:
    """Per-player advanced stats for an NBA season (season label = year it ends). One row per player (season totals)."""
    df = _rows(_table(season, "advanced", ("advanced", "advanced_stats"), refresh))
    ren = {"team_name_abbr": "team", "team_id": "team", "games": "g", "games_started": "gs",
           "fg3a_per_fga_pct": "tpar", "fta_per_fga_pct": "ftr"}
    df = df.rename(columns={k: v for k, v in ren.items() if k in df.columns})
    for c in NUM:
        df[c] = pd.to_numeric(df[c], errors="coerce") if c in df else float("nan")
    # traded players: keep the season-total row (listed first)
    df = df.drop_duplicates("bbref_id", keep="first")
    df["season"] = season
    return df[["season", "bbref_id", "name", "team", "pos"] + NUM]


def season_table(season: int, refresh: bool = False) -> pd.DataFrame:
    """Advanced + per-game columns for one NBA season."""
    return advanced(season, refresh).merge(per_game(season, refresh), on=["season", "bbref_id"], how="left")

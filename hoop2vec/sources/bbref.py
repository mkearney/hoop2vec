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
NUM = ["age", "g", "gs", "mp", "per", "ts_pct", "usg_pct", "ws", "ws_per_48", "obpm", "dbpm", "bpm", "vorp"]


def advanced(season: int, refresh: bool = False) -> pd.DataFrame:
    """Per-player advanced stats for an NBA season (season label = year it ends). One row per player (season totals)."""
    url = f"{BASE}/leagues/NBA_{season}_advanced.html"
    try:
        raw = cached_bytes(url, OUT / f"NBA_{season}_advanced.html", refresh=refresh)
    except Exception as e:
        if "429" in str(e):  # rate limited: wait once, politely, then try again
            time.sleep(90)
            raw = cached_bytes(url, OUT / f"NBA_{season}_advanced.html", refresh=refresh)
        else:
            raise
    soup = BeautifulSoup(raw, "lxml")
    table = soup.find("table", id="advanced") or soup.find("table", id="advanced_stats")
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
            ds = td.get("data-stat")
            rec[ds] = td.get_text(strip=True)
        rows.append(rec)
    df = pd.DataFrame(rows)
    ren = {"team_name_abbr": "team", "team_id": "team", "pos": "pos", "games": "g", "g": "g", "games_started": "gs",
           "gs": "gs", "mp": "mp", "per": "per", "ts_pct": "ts_pct", "usg_pct": "usg_pct", "ws": "ws",
           "ws_per_48": "ws_per_48", "obpm": "obpm", "dbpm": "dbpm", "bpm": "bpm", "vorp": "vorp", "age": "age"}
    df = df.rename(columns={k: v for k, v in ren.items() if k in df.columns})
    for c in NUM:
        df[c] = pd.to_numeric(df[c], errors="coerce") if c in df else float("nan")
    # traded players: keep the season-total row (listed first)
    df = df.drop_duplicates("bbref_id", keep="first")
    df["season"] = season
    return df[["season", "bbref_id", "name", "team", "pos"] + NUM]

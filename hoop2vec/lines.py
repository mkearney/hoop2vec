"""Closing betting lines (benchmark only, never a model input) from ESPN's pickcenter."""
from __future__ import annotations

import pandas as pd

from .config import HIST, PROC

LINES = HIST / "lines.parquet"


def collect(seasons=None) -> pd.DataFrame:
    """Gather lines from ingested play-by-play game tables (and the committed history file)."""
    frames = [pd.read_parquet(LINES)] if LINES.exists() else []
    for d in sorted((PROC / "pbp").glob("*/games.parquet")):
        s = int(d.parent.name)
        if seasons and s not in seasons:
            continue
        g = pd.read_parquet(d)
        if "home_line" not in g:
            continue
        g = g[g["home_line"].notna() | g["total_line"].notna()]
        frames.append(pd.DataFrame({"season": s, "event_id": g["game_id"].astype(str), "home_id": g["home_id"].astype(str),
                                    "away_id": g["away_id"].astype(str), "home_line": g["home_line"].astype(float),
                                    "total_line": g["total_line"].astype(float), "provider": g["line_provider"]}))
    if not frames:
        return pd.DataFrame(columns=["season", "event_id", "home_id", "away_id", "home_line", "total_line", "provider"])
    return pd.concat(frames, ignore_index=True).drop_duplicates("event_id", keep="last")


def save(lines: pd.DataFrame):
    HIST.mkdir(exist_ok=True)
    lines.to_parquet(LINES, compression="zstd", compression_level=19, index=False)


def for_games(feat: pd.DataFrame, games: pd.DataFrame, lines: pd.DataFrame) -> pd.DataFrame:
    """Line from team_a's perspective (expected margin of team_a), keyed by (season, gid)."""
    g = games[["season", "gid", "event_id", "a_id"]].dropna(subset=["event_id"]).copy()
    g["event_id"] = g["event_id"].astype(str)
    g = g.merge(lines[["event_id", "home_id", "home_line", "total_line", "provider"]], on="event_id")
    g = g[g["home_line"].notna()]
    g["line_a"] = (-g["home_line"]).where(g["a_id"].astype(str) == g["home_id"], g["home_line"])
    g["line_a"] = g["line_a"].astype(float)
    g["book"] = ~g["provider"].fillna("").str.lower().isin(["consensus", "numberfire", ""])  # a real sportsbook's line
    return g[["season", "gid", "line_a", "total_line", "book"]]

"""Incrementally ingest ESPN game summaries (box score + play-by-play + closing line) for one season.

Each summary is parsed on the fly into compact tables under data/processed/pbp/<season>/ (raw JSON is
never kept): games, team_games, player_games, lineups. Completed seasons only need this once (to build the
style tables in artifacts/style/); the daily job fetches just the new games of the current season.
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import timedelta

import pandas as pd

from .config import PROC, RAW, current_season, today
from .sources.pbp import BASE, get_json, parse_summary, scoreboard, season_dates

TABLES = ("games", "team_games", "player_games", "lineups")
PBP = PROC / "pbp"


def season_dir(season: int):
    return PBP / str(season)


def load_schedule(season: int, refresh_recent: bool) -> pd.DataFrame:
    f = RAW / "pbp_schedule" / f"{season}.parquet"
    f.parent.mkdir(parents=True, exist_ok=True)
    t = today()
    old = pd.read_parquet(f) if f.exists() else pd.DataFrame(columns=["game_id", "date", "completed", "season_type"])
    have = set(old["date"]) if len(old) else set()
    days = [d for d in season_dates(season, end=t)
            if d.isoformat() not in have or (refresh_recent and (t - d) <= timedelta(days=3))]
    rows = []
    with ThreadPoolExecutor(8) as ex:
        for res in ex.map(scoreboard, days):
            rows += res
    new = pd.DataFrame(rows)
    if len(new):
        old = old[~old["date"].isin(set(new["date"]))] if len(old) else old
    empty = sorted({d.isoformat() for d in days} - (set(new["date"]) if len(new) else set()))
    filler = pd.DataFrame({"game_id": [None] * len(empty), "date": empty, "completed": False, "season_type": None})
    sched = pd.concat([x for x in (old, new, filler) if len(x)], ignore_index=True)
    # never cache "no games" for recent days: they may be filled in later
    sched = sched[~(sched["game_id"].isna() & (pd.to_datetime(sched["date"]).dt.date >= t - timedelta(days=1)))]
    sched.to_parquet(f, index=False)
    return sched[sched["game_id"].notna()].drop_duplicates("game_id")


def _fetch(gid: str, season: int):
    js = get_json(f"{BASE}/summary?event={gid}")
    if not js:
        return None
    try:
        return parse_summary(js, season)
    except Exception as e:  # keep crawling; log
        print(f"  parse error {gid}: {e!r}", flush=True)
        return None


def ingest_season(season: int, workers: int = 8, refresh_recent: bool | None = None):
    refresh_recent = season >= current_season() if refresh_recent is None else refresh_recent
    t0 = time.time()
    sched = load_schedule(season, refresh_recent)
    sched = sched[sched["completed"].astype(bool)]
    from .config import PRO_LEAGUE
    if PRO_LEAGUE:  # regular season (2), playoffs (3), NBA play-in (5); no preseason or All-Star exhibitions
        st = pd.to_numeric(sched["season_type"], errors="coerce")
        sched = sched[st.isin([2, 3, 5]) & ~sched.get("note", pd.Series("", index=sched.index)).fillna("")
                      .str.contains("All-Star|Rising Stars|Celebrity", case=False)]
    out = season_dir(season)
    out.mkdir(parents=True, exist_ok=True)
    existing = {t: pd.read_parquet(out / f"{t}.parquet") if (out / f"{t}.parquet").exists() else None for t in TABLES}
    done = set(existing["games"]["game_id"]) if existing["games"] is not None else set()
    todo = [g for g in sched["game_id"] if g not in done]
    print(f"[pbp {season}] {len(sched)} completed games, {len(todo)} to fetch", flush=True)
    acc = {t: [] for t in TABLES}
    n = 0

    def flush():
        for t in TABLES:
            if not acc[t]:
                continue
            df = pd.DataFrame(acc[t])
            if existing[t] is not None:
                df = pd.concat([existing[t], df], ignore_index=True)
            df.to_parquet(out / f"{t}.parquet", index=False)
            existing[t] = df
            acc[t] = []

    with ThreadPoolExecutor(workers) as ex:
        futs = {ex.submit(_fetch, g, season): g for g in todo}
        for fu in as_completed(futs):
            r = fu.result()
            if r is None:
                continue
            g, tg, pg, lu = r
            acc["games"].append(g)
            acc["team_games"] += tg
            acc["player_games"] += pg
            acc["lineups"] += lu
            n += 1
            if n % 1000 == 0:
                flush()
                print(f"  [pbp {season}] {n}/{len(todo)} ({time.time() - t0:.0f}s)", flush=True)
    flush()
    print(f"[pbp {season}] {n} new games in {time.time() - t0:.0f}s", flush=True)
    return n

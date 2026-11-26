"""Live updates: re-publish a league as its games finish, between the full overnight refreshes.

The overnight job (``daily``) rebuilds everything. During the day the live workflow runs every 10 minutes:

check     Standard library only (nothing to install): reads ESPN's scoreboard for yesterday and today (US Eastern)
          for every league in season and prints the leagues with a finished game that the last live publish had
          not seen. A run with nothing new ends here, in a few seconds.
run       ``python -m hoop2vec live --<league>``: the daily pipeline in its live form (pipeline.run("live")): only the
          new games are fetched, past seasons come from the cache, and slow pieces that a game doesn't move (NBA
          prospects, the model training) are skipped. Then the league's site data is kept under live/<league>/
          with the list of finished games it includes.
overlay   Before a Pages deploy: puts each league's live data over the committed docs/ wherever the live copy is
          newer than the committed one (so the next overnight refresh takes over again by itself).

live/ is not committed: the workflow keeps it in the Actions cache, so live updates never touch the git history.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import (ESPN_BASE, LEAGUE, LEAGUES, PRO_LEAGUE, ROOT, SITE_ROOT, USER_AGENT, current_season,
                     season_dates)

LIVE = ROOT / "live"
EASTERN = ZoneInfo("America/New_York")  # ESPN's scoreboard dates are US Eastern
PRO_TYPES = {2, 3, 5}  # regular season, playoffs, NBA play-in (the same filter as the ingest)


def state_dir(league: str = LEAGUE) -> Path:
    return LIVE / league


def in_season(now: dt.datetime | None = None) -> bool:
    d = (now or dt.datetime.now(EASTERN)).date()
    start, end = season_dates(current_season())
    return start <= d <= end + dt.timedelta(days=1)


def _scoreboard(day: dt.date) -> dict:
    url = f"{ESPN_BASE}/scoreboard?dates={day:%Y%m%d}" + ("&limit=100" if PRO_LEAGUE else "&groups=50&limit=500")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def finished_games(now: dt.datetime | None = None) -> list[str]:
    """ESPN ids of the games finished yesterday or today (US Eastern), sorted."""
    today = (now or dt.datetime.now(EASTERN)).date()
    out = set()
    for day in (today - dt.timedelta(days=1), today):
        for e in _scoreboard(day).get("events", []):
            comp = (e.get("competitions") or [{}])[0]
            st = comp.get("status", e.get("status", {})).get("type", {})
            if not st.get("completed"):
                continue
            if PRO_LEAGUE and (e.get("season") or {}).get("type") not in PRO_TYPES:
                continue  # preseason, All-Star games
            out.add(str(e["id"]))
    return sorted(out)


def seen() -> set[str] | None:
    f = state_dir() / "seen.json"
    try:
        return set(json.loads(f.read_text())["games"])
    except (OSError, ValueError, KeyError):
        return None


def new_games() -> list[str]:
    """Finished games the last live publish of this league doesn't include ([] out of season)."""
    if not in_season():
        return []
    done, have = finished_games(), seen()
    if os.environ.get("HOOP2VEC_LIVE_FORCE"):  # manual run: refresh whatever is in season
        return done or ["?"]
    if have is None:  # no live publish yet (or the cache expired): one run to catch up
        return done or ["?"]
    return [g for g in done if g not in have]


def check_all() -> list[str]:
    """Leagues with new finished games. Each league is checked in its own process (config is per league)."""
    leagues = []
    for lg in LEAGUES:
        env = {**os.environ, "HOOP2VEC_LEAGUE": lg}
        r = subprocess.run([sys.executable, "-m", "hoop2vec.live", "check-one"], env=env, cwd=ROOT,
                           capture_output=True, text=True)
        out = (r.stdout.strip().splitlines() or ["error"])[-1]
        print(f"{lg}: {out}" + (f"\n{r.stderr.strip()}" if r.returncode else ""), flush=True)
        if r.returncode == 0 and out.startswith("new"):
            leagues.append(lg)
    return leagues


def run():
    """Live refresh of this league; keeps the site data and the finished games it includes under live/."""
    done = finished_games()  # before the refresh: a game that ends during the run is picked up next time
    from .pipeline import run as pipeline_run
    pipeline_run("live")
    src = SITE_ROOT / LEAGUE / "data"
    dst = state_dir() / "data"
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    (state_dir() / "seen.json").write_text(json.dumps({"games": done, "at": _now_utc()}))


def _now_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _updated(d: Path) -> str:
    try:
        return json.loads((d / "meta.json").read_text()).get("updated") or ""
    except (OSError, ValueError):
        return ""


def overlay() -> list[str]:
    """Copy live data over docs/<league>/data where it is newer than the committed data. Returns the leagues used."""
    used = []
    for lg in LEAGUES:
        live, docs = LIVE / lg / "data", SITE_ROOT / lg / "data"
        if not live.is_dir():
            continue
        lu, du = _updated(live), _updated(docs)
        if lu > du:  # "%Y-%m-%d %H:%M UTC" strings sort by time
            shutil.copytree(live, docs, dirs_exist_ok=True)
            used.append(lg)
        print(f"{lg}: live {lu or '-'} vs committed {du or '-'} -> {'live' if lg in used else 'committed'}")
    return used


def _github_output(**kv):
    f = os.environ.get("GITHUB_OUTPUT")
    if f:
        with open(f, "a") as fh:
            for k, v in kv.items():
                fh.write(f"{k}={v}\n")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "check-one":
        n = new_games()
        print(f"new {len(n)}" if n else ("none" if in_season() else "off-season"))
    elif cmd == "check":
        lg = check_all()
        print(json.dumps(lg))
        _github_output(leagues=json.dumps(lg))
    elif cmd == "overlay":
        overlay()
    else:
        raise SystemExit("usage: python -m hoop2vec.live check | check-one | overlay  (live refresh: python -m hoop2vec live --<league>)")

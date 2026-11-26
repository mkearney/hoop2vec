"""Player pages: career lines, shot zones and shot locations for every player on this season's rosters.

docs/<league>/data/players/<team-slug>.json   one file per team, loaded when one of its player pages opens:
    {"team", "season", "players": {pid: {"career": [season line, ...],
                                          "zones": {season: {zone: [fga, fgm]}},
                                          "shots": {season: [[x, y, zone index, fga, fgm], ...]}}}}
docs/<league>/data/shots_league.json          league attempts and makes by zone, per season (the comparison)

Career lines: men's college from barttorvik.com's player-season tables (every D-I season since 2008), everyone else
from ESPN box scores (women's college since 2019, NBA and WNBA since 2008). Zones come from ESPN play-by-play (men's
since 2015, women's since 2020, pros since 2008). How precisely ESPN located shots varies by season, so each season
gets the finest zone split its data supports (``scheme``). Shot locations (ESPN's court x, y in feet, basket at
x = 25, y = 0) exist from when the ingest started keeping them: the current season, plus any past season backfilled
with ``python -m hoop2vec shots <seasons>``.
"""
from __future__ import annotations

import json
import re

import numpy as np
import pandas as pd

from .config import ARTIFACTS, ESPN_DATA, FIRST_PBP_SEASON, FIRST_SEASON, PROC, SITE_DATA

ZONES = ("rim", "smid", "lmid", "c3", "ab3")
SHOT_SEASONS = 5          # most recent seasons of shot locations kept per player (zones cover every season)
SYNTH_PID = 90_000_000    # men's: ESPN athletes not matched to a Torvik player get this + their ESPN id
MAX_Y = 41                # drop heaves from past half court out of the location chart (zones still count them)
ZCOLS = [f"{z}_{k}" for z in ZONES for k in ("a", "m")]


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-")


def _r(v, d=1):
    return None if v is None or not np.isfinite(v) else round(float(v), d)


# ------------------------------------------------------------------ zones
def scheme(tot: dict) -> str:
    """Finest zone split a season's play-by-play supports, from its league totals ({zone: [fga, fgm]}):
    'full'   court coordinates: rim, short mid (<= 14 ft), long mid, corner three, above-the-break three
    'dist'   shot distances but no coordinates: rim, short mid, long mid, three
    'coarse' neither: rim (layups, dunks, tips), other twos, three."""
    a = {z: float((tot.get(z) or (0, 0))[0]) for z in ZONES}
    if a["smid"] / max(a["smid"] + a["lmid"], 1) < 0.1:
        return "coarse"
    if a["c3"] / max(a["c3"] + a["ab3"], 1) < 0.12:
        return "dist"
    return "full"


def collapse(z: dict, sch: str) -> dict:
    """Merge the five parser zones into the season's scheme; zones with no attempts are left out."""
    g = lambda k: [int((z.get(k) or (0, 0))[0]), int((z.get(k) or (0, 0))[1])]  # noqa: E731
    add = lambda *ks: [sum(g(k)[0] for k in ks), sum(g(k)[1] for k in ks)]  # noqa: E731
    if sch == "full":
        out = {k: g(k) for k in ZONES}
    elif sch == "dist":
        out = {"rim": g("rim"), "smid": g("smid"), "lmid": g("lmid"), "three": add("c3", "ab3")}
    else:
        out = {"rim": g("rim"), "mid": add("smid", "lmid"), "three": add("c3", "ab3")}
    return {k: v for k, v in out.items() if v[0] > 0}


def zone_counts(pg: pd.DataFrame) -> pd.DataFrame:
    """Per player (athlete_id, team_id): field-goal attempts and makes by zone from the play-by-play columns."""
    d = pd.DataFrame({"athlete_id": pg["athlete_id"].astype(str), "team_id": pg["team_id"].astype(str)})
    for c in ZCOLS:
        d[c] = pd.to_numeric(pg[f"p_{c}"], errors="coerce").fillna(0) if f"p_{c}" in pg else 0.0
    d = d.groupby(["athlete_id", "team_id"], as_index=False)[ZCOLS].sum()
    return d[d[[f"{z}_a" for z in ZONES]].sum(axis=1) > 0]


def _season_zones(season: int, cur: int):
    """(player zone counts, name table) for a season; completed seasons are cached under PROC/careers."""
    from .ingest import season_dir
    src = season_dir(season) / "player_games.parquet"
    if not src.exists():
        return None, None
    cache = PROC / "careers"
    cache.mkdir(parents=True, exist_ok=True)
    fz, fn = cache / f"zones_{season}.parquet", cache / f"names_{season}.parquet"
    if season < cur and fz.exists() and fn.exists() and fz.stat().st_mtime >= src.stat().st_mtime:
        return pd.read_parquet(fz), pd.read_parquet(fn)
    pg = pd.read_parquet(src)
    z = zone_counts(pg)
    names = pg[["athlete_id", "team_id", "name"]].astype(str).drop_duplicates(["athlete_id", "team_id"])
    if season < cur:
        z.to_parquet(fz, index=False)
        names.to_parquet(fn, index=False)
    return z, names


# ------------------------------------------------------------------ men's: ESPN athletes -> Torvik players
def match_espn(names: pd.DataFrame, ps: pd.DataFrame) -> dict:
    """ESPN athlete id -> Torvik pid by (season, team, normalized name), or by a last name that is unique on that
    team that season. ``names``: season, athlete_id, team (hoop2vec name), name. ``ps``: season, team, player, pid.
    An athlete keeps the pid matched most often across seasons."""
    from .sources.injuries import norm_name
    ps = ps.dropna(subset=["pid"]).assign(nk=lambda d: d["player"].map(norm_name))
    full = {(s, t, n): int(p) for s, t, n, p in zip(ps["season"], ps["team"], ps["nk"], ps["pid"])}
    ps = ps.assign(ln=ps["nk"].str.split().str[-1])
    one = ps[ps.groupby(["season", "team", "ln"])["nk"].transform("size") == 1]
    by_last = {(s, t, n): int(p) for s, t, n, p in zip(one["season"], one["team"], one["ln"], one["pid"])}
    votes = {}
    for s, a, t, nm in zip(names["season"], names["athlete_id"], names["team"], names["name"]):
        nk = norm_name(nm)
        p = full.get((s, t, nk)) or (by_last.get((s, t, nk.split()[-1])) if nk else None)
        if p is not None:
            votes.setdefault(str(a), {}).setdefault(p, 0)
            votes[str(a)][p] += 1
    return {a: max(v, key=v.get) for a, v in votes.items()}


# ------------------------------------------------------------------ career lines
def _career_mens(pids: set) -> pd.DataFrame:
    from .prospects import college
    c = college(refresh_current=False)
    c = c[c["pid"].isin(pids)].copy()
    gp = c["gp"].where(c["gp"] > 0)
    fga = c["twopa"] + c["tpa"]
    fgm = c["efg"] / 100 * fga - 0.5 * c["tpm"]
    return pd.DataFrame({
        "pid": c["pid"].astype(int), "season": c["season"].astype(int), "team": c["team"], "cls": c["cls"],
        "g": c["gp"], "mpg": c["mpg"], "ppg": c["pts_pg"], "rpg": c["trb_pg"], "apg": c["ast_pg"], "spg": c["stl_pg"],
        "bpg": c["blk_pg"], "fg": 100 * fgm / fga.where(fga > 0), "tp": 100 * c["tpm"] / c["tpa"].where(c["tpa"] > 0),
        "ft": 100 * c["ftm"] / c["fta"].where(c["fta"] > 0), "tpa": c["tpa"] / gp, "ts": c["ts"], "usg": c["usg"],
        "val": c["bpm"]})


def _career_espn(pids: set, seasons) -> pd.DataFrame:
    from . import players as PL
    ps = PL.load_player_seasons(seasons, refresh_current=False)
    ps = ps[pd.to_numeric(ps["pid"], errors="coerce").isin(pids)].copy()
    g = ps["gp"].where(ps["gp"] > 0)
    tsa = 2 * (ps["fga"] + 0.44 * ps["fta"])
    return pd.DataFrame({
        "pid": pd.to_numeric(ps["pid"]).astype(int), "season": ps["season"].astype(int), "team": ps["team"],
        "cls": ps.get("cls"), "g": ps["gp"], "mpg": ps["mpg"], "ppg": ps["pts"] / g, "rpg": (ps["orb"] + ps["drb"]) / g,
        "apg": ps["ast"] / g, "spg": ps["stl"] / g, "bpg": ps["blk"] / g,
        "fg": 100 * ps["fgm"] / ps["fga"].where(ps["fga"] > 0), "tp": 100 * ps["tpm"] / ps["tpa"].where(ps["tpa"] > 0),
        "ft": 100 * ps["ftm"] / ps["fta"].where(ps["fta"] > 0), "tpa": ps["tpa"] / g,
        "ts": 100 * ps["pts"] / tsa.where(tsa > 0), "usg": np.nan, "val": ps["bpm"]})


CAREER_KEYS = ("season", "team", "cls", "g", "mpg", "ppg", "rpg", "apg", "spg", "bpg", "fg", "tp", "ft", "tpa", "ts",
               "usg", "val")


def career_rows(df: pd.DataFrame) -> dict:
    """pid -> list of season lines (oldest first), rounded, without empty fields."""
    out = {}
    df = df.sort_values(["pid", "season", "g"])
    for r in df.itertuples(index=False):
        row = {}
        for k in CAREER_KEYS:
            v = getattr(r, k, None)
            if v is None or (isinstance(v, float) and not np.isfinite(v)) or v is pd.NA:
                continue
            if isinstance(v, (float, np.floating)):
                v = round(float(v), 1)
            elif isinstance(v, np.integer):
                v = int(v)
            row[k] = v
        out.setdefault(int(r.pid), []).append(row)
    return out


# ------------------------------------------------------------------ shot locations
def shot_bins(shots: pd.DataFrame) -> dict:
    """athlete_id -> [[x, y, zone index, fga, fgm], ...] (one-foot cells; ESPN's coordinates are whole feet)."""
    s = shots[(shots["y"] <= MAX_Y) & shots["x"].between(0, 50)]
    if not len(s):
        return {}
    s = s.assign(zi=s["zone"].map({z: i for i, z in enumerate(ZONES)}).fillna(4).astype(int),
                 athlete_id=s["athlete_id"].astype(str), made=s["made"].astype(int))
    b = s.groupby(["athlete_id", "x", "y", "zi"]).agg(a=("made", "size"), m=("made", "sum")).reset_index()
    out = {}
    for r in b.itertuples(index=False):
        out.setdefault(r.athlete_id, []).append([int(r.x), int(r.y), int(r.zi), int(r.a), int(r.m)])
    return out


# ------------------------------------------------------------------ publish
def publish(players: pd.DataFrame, season: int) -> int:
    """Write the per-team player files and shots_league.json for this season's players.json rows (pid, team).
    Returns the number of players written."""
    from .ingest import season_dir
    players = players[["pid", "team"]].dropna().drop_duplicates()
    players = players.assign(pid=players["pid"].astype(int))
    pids = set(players["pid"])
    seasons = list(range(FIRST_SEASON, season + 1))
    meta = pd.read_csv(ARTIFACTS / "team_meta.csv", dtype={"espn_id": str})
    id2team = dict(zip(meta["espn_id"].astype(str), meta["team"]))

    # ESPN athlete id -> site pid (the same id except in men's college, keyed by Torvik ids)
    per_season, names = {}, []
    for s in range(FIRST_PBP_SEASON, season + 1):
        z, nm = _season_zones(s, season)
        if z is None:
            continue
        per_season[s] = z
        names.append(nm.assign(season=s))
    if ESPN_DATA:
        to_pid = lambda a: int(a) if str(a).isdigit() else None  # noqa: E731
    else:
        from . import players as PL
        a2p = {}
        if names:
            nm = pd.concat(names, ignore_index=True)
            nm["team"] = nm["team_id"].map(id2team)
            ps = PL.load_player_seasons(seasons, refresh_current=False)[["season", "team", "player", "pid"]]
            ps = ps.assign(pid=pd.to_numeric(ps["pid"], errors="coerce"))
            a2p = match_espn(nm.dropna(subset=["team"]), ps)
        synth = {str(p - SYNTH_PID): p for p in pids if p >= SYNTH_PID}
        to_pid = lambda a: a2p.get(str(a)) or synth.get(str(a))  # noqa: E731

    league, zones = {}, {}
    for s, z in per_season.items():
        tot = {k: [float(z[f"{k}_a"].sum()), float(z[f"{k}_m"].sum())] for k in ZONES}
        sch = scheme(tot)
        league[str(s)] = {"scheme": sch, "zones": collapse(tot, sch)}
        z = z.assign(pid=z["athlete_id"].map(to_pid))
        z = z[z["pid"].isin(pids)].groupby("pid")[ZCOLS].sum()
        for pid, r in z.iterrows():
            zz = collapse({k: (r[f"{k}_a"], r[f"{k}_m"]) for k in ZONES}, sch)
            if zz:
                zones.setdefault(int(pid), {})[str(s)] = zz

    shots, located = {}, []
    for s in range(season, max(FIRST_PBP_SEASON, season - SHOT_SEASONS + 1) - 1, -1):
        f = season_dir(s) / "shots.parquet"
        if not f.exists():
            continue
        sh = pd.read_parquet(f)
        if not len(sh):
            continue
        located.append(s)
        for a, cells in shot_bins(sh).items():
            p = to_pid(a)
            if p in pids:
                prev = shots.setdefault(p, {}).get(str(s))
                shots[p][str(s)] = cells if prev is None else _merge_cells(prev, cells)
    for s in located:
        if str(s) in league:
            league[str(s)]["located"] = True

    career = career_rows(_career_espn(pids, seasons) if ESPN_DATA else _career_mens(pids))

    out_dir = SITE_DATA / "players"
    out_dir.mkdir(parents=True, exist_ok=True)
    keep = set()
    n = 0
    for team, grp in players.groupby("team"):
        body = {}
        for pid in sorted(set(grp["pid"])):
            rec = {"career": career.get(pid, []), "zones": zones.get(pid, {}), "shots": shots.get(pid, {})}
            if any(rec.values()):
                body[str(pid)] = rec
        name = f"{_slug(team)}.json"
        keep.add(name)
        with open(out_dir / name, "w") as fh:
            json.dump({"team": team, "season": season, "players": body}, fh, separators=(",", ":"))
        n += len(body)
    for f in out_dir.glob("*.json"):  # teams no longer in the league
        if f.name not in keep:
            f.unlink()
    with open(SITE_DATA / "shots_league.json", "w") as fh:
        json.dump({"season": season, "zones": list(ZONES), "seasons": league}, fh, separators=(",", ":"))
    return n


def _merge_cells(a: list, b: list) -> list:
    acc = {}
    for x, y, z, n, m in a + b:
        k = (x, y, z)
        o = acc.get(k, (0, 0))
        acc[k] = (o[0] + n, o[1] + m)
    return [[x, y, z, n, m] for (x, y, z), (n, m) in acc.items()]

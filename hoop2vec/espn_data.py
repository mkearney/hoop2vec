"""Game and player data built entirely from ESPN's public API (no Torvik): women's college, NBA and WNBA.

Produces the same tables the men's pipeline gets from barttorvik.com, so everything downstream (ratings, players,
game models, bracket / playoffs, identities, prospects) runs unchanged:

* games        Torvik-style games table (team_a/team_b, loc, box scores, venues, NCAA rounds/seeds) from ESPN
               scoreboards (schedule, venues, seeds) + the play-by-play ingest (team box scores).
* conferences  ESPN standings per season.
* team meta    ESPN team directory + home city from home-game venues.
* athletes     height and class year per player from ESPN's athlete endpoint (fetched once, cached, committed).
* player games player box scores with a box-score plus/minus per game. The value of a game is the player's
               box-score line (per-40 rates through a regression fit at team level) plus an equal share of what
               his team's opponent-adjusted performance in that game leaves unexplained, so a team's minute-weighted
               player values add up to its game. Opponent strength is the opponent's pre-game Kalman rating, so a
               game value only ever uses information available before tip-off plus that game itself.
* player seasons  season aggregates of the above (for priors and display).
"""
from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

from .config import (
    ARTIFACTS,
    ESPN_SPORT,
    FIRST_SEASON,
    GAME_MINUTES,
    HIST,
    LEAGUE,
    PRO_LEAGUE,
    PROC,
    RAW,
    REG_PERIODS,
    current_season,
)
from .http import get

BOX = ["fgm", "fga", "tpm", "tpa", "ftm", "fta", "orb", "drb", "trb", "ast", "stl", "blk", "tov", "pf", "pts"]
RATE_COLS = ["twopm", "twopa", "tpm", "tpa", "ftm", "fta", "orb", "drb", "ast", "tov", "stl", "blk", "pf"]
CLS = {1: "Fr", 2: "So", 3: "Jr", 4: "Sr", 5: "Sr"}
POS_ROLE = {"G": "Combo G", "PG": "Pure PG", "SG": "Wing G", "F": "Wing F", "SF": "Wing F", "PF": "Stretch 4",
            "C": "C", "F-C": "PF/C", "C-F": "PF/C", "G-F": "Wing G", "F-G": "Wing F"}
ATHLETES = HIST / "athletes.parquet"
HT_RANGE = (60, 84) if LEAGUE in ("womens", "wnba") else (66, 92)   # plausible heights (inches)


def cls_label(years: pd.Series) -> pd.Series:
    """College: class from ESPN experience years (1=Fr .. 4+=Sr). Pros: career year, "R" for a rookie."""
    if PRO_LEAGUE:
        y = years.clip(lower=0)
        return y.map(lambda v: None if pd.isna(v) else "R" if v < 1 else str(int(v) + 1))
    return years.clip(1, 5).map(CLS)


def _pbp_tables(season: int):
    from .ingest import season_dir
    d = season_dir(season)
    if not (d / "games.parquet").exists():
        return None
    out = {t: pd.read_parquet(d / f"{t}.parquet") for t in ("games", "team_games", "player_games")}
    for t in out.values():
        for c in ("game_id", "team_id", "opp_id", "home_id", "away_id", "athlete_id"):
            if c in t:
                t[c] = t[c].astype(str)
    return out


# ------------------------------------------------------------------ teams / conferences
def _past_teams(have: set) -> pd.DataFrame:
    """Pro leagues: franchises in the game data that ESPN's current team list no longer has (relocated, sold or
    folded), with name and logo from ESPN's team endpoint. Cached."""
    from .config import ESPN_BASE
    from .ingest import PBP
    f = RAW / "past_teams.json"
    cache = json.loads(f.read_text()) if f.exists() else {}
    seen = {}
    for d in sorted(PBP.glob("*/games.parquet")):
        g = pd.read_parquet(d, columns=["home_id", "home_name", "away_id", "away_name"])
        for side in ("home", "away"):
            seen.update(dict(zip(g[f"{side}_id"].astype(str), g[f"{side}_name"])))
    rows = []
    for tid, name in seen.items():
        if tid in have:
            continue
        if tid not in cache:
            try:
                t = get(f"{ESPN_BASE}/teams/{tid}", timeout=30).json().get("team", {})
            except Exception:
                t = {}
            logos = t.get("logos") or []
            cache[tid] = {"espn_id": tid, "location": t.get("location") or name, "name": t.get("displayName") or name,
                          "short": t.get("shortDisplayName"), "abbr": t.get("abbreviation"), "nickname": t.get("nickname"),
                          "color": t.get("color"), "alt_color": t.get("alternateColor"),
                          "logo": logos[0]["href"] if logos else None}
        rows.append(cache[tid])
    f.write_text(json.dumps(cache))
    return pd.DataFrame(rows)


def team_directory() -> pd.DataFrame:
    from .sources import espn
    t = espn.teams()
    if PRO_LEAGUE:
        extra = _past_teams(set(t["espn_id"].astype(str)))
        if len(extra):
            extra = extra[~extra["name"].fillna("").str.contains(r"All-Star|Team |Stars|World|Stripes|\bConf\b|Conference",
                                                                  case=False)
                          & ~extra["name"].isin(["East", "West", "USA", "Rest of World"])]
            t = pd.concat([t, extra], ignore_index=True)
        # pro fans call teams by nickname (Lakers, Clippers, Liberty), and cities repeat (two Los Angeles teams)
        nick = t["short"].fillna(t["nickname"]).fillna(t["name"])
        t["team"] = np.where(nick.duplicated(keep=False), t["name"], nick)
        return t
    loc = t["location"].fillna(t["name"])
    dup = loc.duplicated(keep=False)
    t["team"] = np.where(dup, t["name"], loc)  # unique display key (ESPN location, full name if ambiguous)
    return t


def conferences(seasons) -> pd.DataFrame:
    """D-I conference membership per season from ESPN standings (cached per season)."""
    d = team_directory()
    id2team = dict(zip(d["espn_id"].astype(str), d["team"]))
    frames = []
    for s in seasons:
        f = RAW / "standings" / f"{s}.json"
        f.parent.mkdir(parents=True, exist_ok=True)
        if f.exists() and s < current_season():
            js = json.loads(f.read_text())
        else:
            try:
                grp = "" if PRO_LEAGUE else "&group=50"
                js = get(f"https://site.web.api.espn.com/apis/v2/sports/basketball/{ESPN_SPORT}/standings"
                         f"?region=us&lang=en&contentorigin=espn{grp}&season={s}", timeout=60).json()
                f.write_text(json.dumps(js))
            except Exception as e:
                print("standings", s, e)
                continue
        rows = []
        for c in js.get("children", []):
            lab = c.get("abbreviation") or c.get("name")
            for ent in (c.get("standings") or {}).get("entries", []):
                t = id2team.get(str(ent["team"]["id"]))
                if t:
                    st = {x.get("name"): x.get("value") for x in ent.get("stats", [])}
                    lab2 = lab.title() if PRO_LEAGUE and isinstance(lab, str) else lab
                    rows.append({"season": s, "team": t, "conf": lab2 if PRO_LEAGUE else (str(lab).upper() if len(str(lab)) <= 6 else lab),
                                 "seed": st.get("playoffSeed"), "w": st.get("wins"), "l": st.get("losses")})
        frames.append(pd.DataFrame(rows))
    c = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["season", "team", "conf"])
    for k in ("t_adjoe", "t_adjde", "t_barthag"):
        c[k] = np.nan
    return c.drop_duplicates(["season", "team"])


# ------------------------------------------------------------------ athletes (height, class)
def athletes(ids) -> pd.DataFrame:
    """Height (inches) and latest class year (years of experience) per ESPN athlete id; cached + committed."""
    have = pd.read_parquet(ATHLETES) if ATHLETES.exists() else pd.DataFrame(columns=["athlete_id", "height", "exp", "pos"])
    todo = sorted(set(map(str, ids)) - set(have["athlete_id"].astype(str)))
    if todo:
        base = f"https://sports.core.api.espn.com/v2/sports/basketball/leagues/{ESPN_SPORT}/athletes"

        def one(a):
            try:
                r = get(f"{base}/{a}", timeout=30, retries=2).json()
                return {"athlete_id": a, "height": r.get("height"), "exp": (r.get("experience") or {}).get("years"),
                        "pos": (r.get("position") or {}).get("abbreviation")}
            except Exception:
                return {"athlete_id": a, "height": None, "exp": None, "pos": None}
        print(f"  athletes: fetching {len(todo)}", flush=True)
        with ThreadPoolExecutor(8) as ex:
            new = pd.DataFrame(list(ex.map(one, todo)))
        have = pd.concat([have, new], ignore_index=True)
        have["athlete_id"] = have["athlete_id"].astype(str)
        HIST.mkdir(parents=True, exist_ok=True)
        have.to_parquet(ATHLETES, index=False)
    have["athlete_id"] = have["athlete_id"].astype(str)
    have["height"] = pd.to_numeric(have["height"], errors="coerce").where(lambda h: h.between(*HT_RANGE))
    have["exp"] = pd.to_numeric(have["exp"], errors="coerce")
    return have.drop_duplicates("athlete_id", keep="last").set_index("athlete_id")


# ------------------------------------------------------------------ games
def build_games(seasons, eg: pd.DataFrame, d: pd.DataFrame, conf: pd.DataFrame) -> pd.DataFrame:
    """Torvik-style games table from ESPN scoreboards + play-by-play team box scores."""
    from .games import ncaa_region, ncaa_round
    id2team = dict(zip(d["espn_id"].astype(str), d["team"]))
    d1 = set(zip(conf["season"], conf["team"]))
    cmap = dict(zip(zip(conf["season"], conf["team"]), conf["conf"]))
    out = []
    for s in seasons:
        e = eg[eg["season"] == s].copy()
        if e.empty:
            continue
        e = e[e["home_id"].astype(str).isin(id2team) & e["away_id"].astype(str).isin(id2team)]
        pbp = _pbp_tables(s)
        box = {}
        if pbp is not None:
            tg = pbp["team_games"]
            for r in tg.itertuples():
                box[(r.game_id, r.team_id)] = r
            per = pbp["games"].set_index("game_id")["n_periods"]
        else:
            per = pd.Series(dtype=float)
        for r in e.itertuples():
            a_id, b_id = str(r.away_id), str(r.home_id)          # team_a = away side (Torvik lists the away team first)
            ta, tb = id2team[a_id], id2team[b_id]
            neutral = str(r.neutral) == "True"
            done = str(r.completed) == "True"
            row = {"gid": f"E{r.event_id}", "event_id": str(r.event_id), "date": pd.Timestamp(r.date), "season": s,
                   "neutral": neutral, "team_a": ta, "team_b": tb, "a_id": a_id, "b_id": b_id, "played": done,
                   "pts_a": pd.to_numeric(r.away_score, errors="coerce"), "pts_b": pd.to_numeric(r.home_score, errors="coerce"),
                   "loc": 0 if neutral else -1, "torvik_p_a": np.nan, "city": r.city, "state": r.state, "notes": r.notes,
                   "conf_game": str(r.conf_game) == "True", "season_type": r.season_type}
            notes = "" if pd.isna(r.notes) else str(r.notes)
            nr = ncaa_round(notes, s)
            row["ncaa_round"] = nr
            row["region"] = ncaa_region(notes)
            row["seed_a"], row["seed_b"] = (r.away_rank, r.home_rank) if nr is not None else (np.nan, np.nan)
            both_d1 = (s, ta) in d1 and (s, tb) in d1
            if PRO_LEAGUE:  # season types: 1 preseason, 2 regular, 3 playoffs, 5 NBA play-in; "nond1" = not counted
                st = int(pd.to_numeric(r.season_type, errors="coerce") or 2)
                row["ncaa_round"], row["region"], row["seed_a"], row["seed_b"] = (0 if st == 5 else None), None, np.nan, np.nan
                row["type"] = ("nond1" if not both_d1 or st == 1 else "post" if st == 3 else  # st 1 = preseason "conftourney" if st == 5 else
                               "conf" if cmap.get((s, ta)) == cmap.get((s, tb)) else "nonconf")
            else:
                row["type"] = None
            row["type"] = row["type"] or ("post" if (nr is not None or str(r.season_type) == "3") else
                           "conftourney" if ("tournament" in notes.lower() or "championship" in notes.lower()) else
                           "conf" if row["conf_game"] else "nonconf") if both_d1 else "nond1"
            A, B = box.get((str(r.event_id), a_id)), box.get((str(r.event_id), b_id))
            if done and A is not None and B is not None:
                for side, X in (("a", A), ("b", B)):
                    for k, src in (("fgm", "fgm"), ("fga", "fga"), ("tpm", "tpm"), ("tpa", "tpa"), ("ftm", "ftm"), ("fta", "fta"),
                                   ("orb", "oreb"), ("drb", "dreb"), ("trb", "reb"), ("ast", "ast"), ("stl", "stl"), ("blk", "blk"),
                                   ("tov", "tov"), ("pf", "pf")):
                        row[f"{side}_{k}"] = float(getattr(X, src))
                    row[f"{side}_pts"] = row[f"pts_{side}"]
                pa = A.fga - A.oreb + A.tov + 0.475 * A.fta
                pb = B.fga - B.oreb + B.tov + 0.475 * B.fta
                row["poss"] = (pa + pb) / 2
                n = float(per.get(str(r.event_id), REG_PERIODS) or REG_PERIODS)
                row["ot"] = int(max(0, n - REG_PERIODS))
                row["minutes"] = 5 * GAME_MINUTES + 25.0 * row["ot"]
            else:
                row["poss"], row["ot"], row["minutes"] = np.nan, 0, 5 * GAME_MINUTES
            row["a_won"] = bool(done and row["pts_a"] > row["pts_b"])
            out.append(row)
    g = pd.DataFrame(out)
    for side in ("a", "b"):
        for k in BOX:
            if f"{side}_{k}" not in g:
                g[f"{side}_{k}"] = np.nan
    if PRO_LEAGUE:
        g = playoff_rounds(g)
    g["ncaa_round"] = pd.to_numeric(g["ncaa_round"], errors="coerce")
    g["is_ncaa"] = g["ncaa_round"].notna() & (g["ncaa_round"] > 0 if PRO_LEAGUE else True)
    for c in ("seed_a", "seed_b"):
        g[c] = pd.to_numeric(g[c], errors="coerce").where(g["is_ncaa"])
    g = g.drop_duplicates(["season", "gid"], keep="last")
    return g


def _stage(note) -> str | None:
    """'East 1st Round - Game 2' -> '1ST ROUND'; 'WNBA Finals - Game 3' -> 'WNBA FINALS' (conference and game number
    dropped, so both conferences' series of a round share a stage)."""
    if not isinstance(note, str) or not note.strip():
        return None
    n = re.sub(r"\s*-?\s*GAME\s*\d+.*$", "", note.upper())
    n = re.sub(r"\b(EASTERN|WESTERN|EAST|WEST)\b( CONFERENCE| CONF\.?)?", "", n)
    return re.sub(r"\s+", " ", n).strip(" -") or None


def playoff_rounds(g: pd.DataFrame) -> pd.DataFrame:
    """Playoff round (1 = first round ... last = Finals) and conference for every playoff game. ESPN's notes change
    wording across years, so a round is defined by the data: each series (season + the two teams) takes its most
    common stage label, and stages are numbered by when they start. Play-in games are round 0."""
    g = g.copy()
    po = g["type"] == "post"
    g.loc[po, "series"] = [f"{s}:{'|'.join(sorted((a, b)))}" for s, a, b in zip(g.loc[po, "season"], g.loc[po, "team_a"], g.loc[po, "team_b"])]
    g.loc[po, "game_no"] = g.loc[po, "notes"].str.extract(r"(?i)game\s*(\d+)", expand=False).astype(float)
    g.loc[po, "stage"] = g.loc[po, "notes"].map(_stage)
    for s, x in g[po].groupby("season"):
        ser = x.groupby("series").agg(stage=("stage", lambda v: v.dropna().mode().iat[0] if v.notna().any() else None),
                                      start=("date", "min"))
        st_start = ser.dropna(subset=["stage"]).groupby("stage")["start"].min().sort_values()
        rank = {k: i + 1 for i, k in enumerate(st_start.index)}
        ser["round"] = ser["stage"].map(rank)
        # a series without notes: the round whose start is closest before its own start
        miss = ser["round"].isna()
        for k in ser.index[miss]:
            before = st_start[st_start <= ser.at[k, "start"]]
            ser.at[k, "round"] = rank[before.index[-1]] if len(before) else 1
        g.loc[x.index, "ncaa_round"] = x["series"].map(ser["round"]).to_numpy()
        conf = x["notes"].str.extract(r"(?i)\b(east|west)", expand=False).str.title()
        g.loc[x.index, "region"] = conf.to_numpy()
    return g


def attach_geo(g: pd.DataFrame, meta: pd.DataFrame) -> pd.DataFrame:
    from . import geo
    m = meta.drop_duplicates("espn_id").set_index("espn_id")
    lat_a, lon_a = g["a_id"].map(m["lat"]), g["a_id"].map(m["lon"])
    lat_b, lon_b = g["b_id"].map(m["lat"]), g["b_id"].map(m["lon"])
    vll = [geo.geocode(c, s) if isinstance(c, str) and c else None for c, s in zip(g["city"], g["state"])]
    vlat = pd.Series([x[0] if x else np.nan for x in vll], index=g.index)
    vlon = pd.Series([x[1] if x else np.nan for x in vll], index=g.index)
    g["v_lat"] = np.where(g["loc"] == -1, lat_b, vlat)   # away team listed first: home venue = team_b's city
    g["v_lon"] = np.where(g["loc"] == -1, lon_b, vlon)
    g["dist_a"] = geo.haversine_km(lat_a, lon_a, g["v_lat"], g["v_lon"])
    g["dist_b"] = geo.haversine_km(lat_b, lon_b, g["v_lat"], g["v_lon"])
    return g


# ------------------------------------------------------------------ players
def _player_box(seasons, d: pd.DataFrame) -> pd.DataFrame:
    id2team = dict(zip(d["espn_id"].astype(str), d["team"]))
    frames = []
    for s in seasons:
        t = _pbp_tables(s)
        if t is None:
            continue
        p = t["player_games"]
        p = p[(p["min"] > 0) & ~p["dnp"].fillna(False).astype(bool)].copy()
        p["season"] = s
        p["team"] = p["team_id"].map(id2team)
        p["muid"] = "E" + p["game_id"]
        p["twopm"], p["twopa"] = p["fgm"] - p["tpm"], p["fga"] - p["tpa"]
        p = p.rename(columns={"oreb": "orb", "dreb": "drb"})
        frames.append(p[p["team"].notna()])
    return pd.concat(frames, ignore_index=True)


# Torvik's men's BPM (2022-26, players with 20+ mpg): correlation of the offensive / defensive parts with height
HT_CORR_TARGET = {"obpm": 0.02, "dbpm": 0.43}


def _height_neutralize(pb: pd.DataFrame) -> dict:
    """Team-level box regressions credit a team's rebounding and interior finishing to whoever got the board or made
    the shot, so bigs come out far ahead (raw: corr(value, height) 0.59 vs 0.27 in Torvik's men's BPM, mostly on
    offense). Shift credit by height *within each team-game* (each team's minute-weighted total is unchanged), with
    the per-inch shift chosen so the season-level height correlations match Torvik's men's BPM. In place; returns
    the shifts."""
    key = [pb["season"], pb["muid"], pb["team"]]
    h = pb["inches"].where(pb["inches"].between(*HT_RANGE))
    wm = (h * pb["min"]).groupby(key).transform("sum") / pb["min"].where(h.notna()).groupby(key).transform("sum")
    dev = (h - wm).fillna(0.0)                       # unknown height -> no shift
    ps = pd.DataFrame({"season": pb["season"], "pid": pb["athlete_id"], "min": pb["min"], "h": h, "dev": dev,
                       "obpm": pb["obpm"], "dbpm": pb["dbpm"]})
    for c in ("dev", "obpm", "dbpm"):
        ps[c] = ps[c] * ps["min"]
    a = ps.groupby(["season", "pid"]).agg(min=("min", "sum"), g=("min", "size"), h=("h", "last"), dev=("dev", "sum"),
                                         obpm=("obpm", "sum"), dbpm=("dbpm", "sum"))
    a = a[(a["min"] / a["g"] >= 20) & (a["g"] >= 10) & a["h"].notna()]
    out = {}
    for c, target in HT_CORR_TARGET.items():
        v, dv = (a[c] / a["min"]).to_numpy(), (a["dev"] / a["min"]).to_numpy()

        def corr(g, v=v, dv=dv):
            return np.corrcoef(a["h"], v - g * dv)[0, 1]
        lo, hi = -3.0, 3.0                              # corr falls as the shift grows: bisection
        for _ in range(60):
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if corr(mid) > target else (lo, mid)
        out[c] = round((lo + hi) / 2, 4)
        pb[c] = pb[c] - out[c] * dev
    pb["bpm"] = pb["obpm"] + pb["dbpm"]
    return out


def player_games(games: pd.DataFrame, d: pd.DataFrame, kpre: pd.DataFrame) -> pd.DataFrame:
    """Per player-game box plus/minus (obpm/dbpm/bpm), Torvik's units: a team's 5 x minute-weighted mean = its
    opponent-adjusted game margin per 100 possessions."""
    seasons = sorted(games["season"].unique())
    pb = _player_box(seasons, d)
    g = games[games["played"] & games["poss"].notna()].merge(kpre, on=["season", "gid"], how="left")
    sides = []
    for me, op in (("a", "b"), ("b", "a")):
        sides.append(pd.DataFrame({
            "season": g["season"], "muid": g["gid"], "team": g[f"team_{me}"], "opp": g[f"team_{op}"], "date": g["date"],
            "y_o": 100 * g[f"pts_{me}"] / g["poss"] - g["k_mu"] - g[f"{op}_k_d"],      # opponent-adjusted offense
            "y_d": g["k_mu"] - 100 * g[f"pts_{op}"] / g["poss"] + g[f"{op}_k_o"]}))   # opponent-adjusted defense
    side = pd.concat(sides, ignore_index=True).dropna(subset=["y_o", "y_d"])
    pb = pb.merge(side, on=["season", "muid", "team"], how="inner")
    m = pb["min"].clip(lower=1)
    for c in RATE_COLS:
        pb[f"r_{c}"] = 40 * pb[c] / m
    tm = pb.groupby(["season", "muid", "team"])["min"].transform("sum")
    pb["w"] = pb["min"] / tm
    R = [f"r_{c}" for c in RATE_COLS]
    team = pb[R].mul(pb["w"], axis=0).groupby([pb["season"], pb["muid"], pb["team"]]).sum()
    ty = pb.groupby(["season", "muid", "team"])[["y_o", "y_d"]].first()
    team = team.join(ty)
    X = np.c_[team[R].to_numpy(), np.ones(len(team))]
    coef = {}
    for y in ("y_o", "y_d"):
        b = np.linalg.lstsq(X, team[y].to_numpy(), rcond=None)[0]
        coef[y] = b
        fit_team = pd.Series(X @ b, index=team.index)
        player_fit = pb[R].to_numpy() @ b[:-1] + b[-1]
        resid = pb.set_index(["season", "muid", "team"]).index.map(team[y] - fit_team).to_numpy(float)
        pb["obpm" if y == "y_o" else "dbpm"] = (player_fit + resid) / 5.0
    pb["bpm"] = pb["obpm"] + pb["dbpm"]
    (ARTIFACTS / "box_value_coef.json").write_text(json.dumps({k: dict(zip(R + ["const"], np.round(v, 4).tolist())) for k, v in coef.items()}, indent=1))
    pb["pid"] = pd.to_numeric(pb["athlete_id"], errors="coerce")
    ath = athletes(pb["athlete_id"].unique())
    last = pb.groupby("pid")["season"].transform("max")
    exp_now = pb["athlete_id"].map(ath["exp"])
    pb["cls"] = cls_label(exp_now - (last - pb["season"]))
    pb["inches"] = pb["athlete_id"].map(ath["height"])
    gam = _height_neutralize(pb)
    cj = json.loads((ARTIFACTS / "box_value_coef.json").read_text())
    cj["height_shift_per_inch"] = gam
    (ARTIFACTS / "box_value_coef.json").write_text(json.dumps(cj, indent=1))
    pb["pos"] = pb["pos"].fillna(pb["athlete_id"].map(ath["pos"]))
    pb["player"] = pb["name"]
    pb["opponent"], pb["loc"] = pb["opp"], "N"
    pb["poss"], pb["ortg"], pb["usage"] = np.nan, np.nan, np.nan
    keep = ["season", "date", "muid", "team", "opponent", "loc", "player", "pid", "cls", "inches", "min", "ortg", "usage",
            "bpm", "obpm", "dbpm", "pts", "poss", "pos"] + BOX[:-1] + ["twopm", "twopa"]
    keep = list(dict.fromkeys(c for c in keep if c in pb.columns))
    return pb[keep]


def player_seasons(pg: pd.DataFrame, conf: pd.DataFrame) -> pd.DataFrame:
    """Season aggregates per player-team (the men's Torvik player-season table, from our own game values)."""
    q = pg.copy()
    for c in ("bpm", "obpm", "dbpm"):
        q[f"w_{c}"] = q[c] * q["min"]
    agg = q.groupby(["season", "pid", "team"]).agg(
        player=("player", "last"), cls=("cls", "last"), gp=("muid", "nunique"), mins=("min", "sum"),
        w_bpm=("w_bpm", "sum"), w_obpm=("w_obpm", "sum"), w_dbpm=("w_dbpm", "sum"), inches=("inches", "last"),
        pos=("pos", "last"), **{c: (c, "sum") for c in ["pts", "fgm", "fga", "tpm", "tpa", "ftm", "fta", "orb", "drb",
                                                        "ast", "stl", "blk", "tov", "pf"] if c in q}).reset_index()
    for c in ("bpm", "obpm", "dbpm"):
        agg[c] = agg[f"w_{c}"] / agg["mins"].clip(lower=1)
    agg["mpg"] = agg["mins"] / agg["gp"].clip(lower=1)
    team_min = agg.groupby(["season", "team"])["mins"].transform("sum")
    agg["min_pct"] = 100 * agg["mins"] / (team_min / 5).clip(lower=1)
    agg["ht"] = agg["inches"].map(lambda h: f"{int(h // 12)}-{int(round(h % 12))}" if pd.notna(h) else None)
    agg["role"] = agg["pos"].map(POS_ROLE)
    agg["rec_rank"] = np.nan
    cm = conf.drop_duplicates(["season", "team"]).set_index(["season", "team"])["conf"]
    agg["conf"] = [cm.get((s, t)) for s, t in zip(agg["season"], agg["team"])]
    return agg.drop(columns=[c for c in agg.columns if c.startswith("w_")])


# ------------------------------------------------------------------ entry
def build(refresh: bool = True):
    """games, meta, pg, ps, conf for the women's game (writes PROC files the shared loaders read)."""
    from . import games as D
    from .ratings import kalman as KAL
    cur = current_season()
    seasons = list(range(FIRST_SEASON, cur + 1))
    eg = D.espn_games(seasons, refresh_current=refresh)
    d = team_directory()
    conf = conferences(seasons)
    cw = d[["team", "espn_id"]].assign(votes=1, share=1.0)
    cw.to_csv(ARTIFACTS / "crosswalk.csv", index=False)
    meta = D.team_meta(cw, eg)
    meta.to_csv(ARTIFACTS / "team_meta.csv", index=False)
    g = attach_geo(build_games(seasons, eg, d, conf), meta)
    g.to_parquet(PROC / "games.parquet")
    # Kalman pre-game states anchor the player values (opponent strength known before tip-off)
    pb = _player_box(seasons, d)
    cont = KAL.continuity(pb.assign(pid=pd.to_numeric(pb["athlete_id"], errors="coerce")))
    kpre, _ = KAL.pregame(g[g["type"] != "nond1"], cont)
    pg = player_games(g[g["type"] != "nond1"], d, kpre)
    for s, x in pg.groupby("season"):
        x.astype({"cls": "string", "loc": "string"}).to_parquet(PROC / f"pg_{s}.parquet")
    ps = player_seasons(pg, conf)
    for s, x in ps.groupby("season"):
        x.to_parquet(PROC / f"ps_{s}.parquet")
    conf.to_parquet(PROC / "conf.parquet")
    return g, meta, pg, ps, conf


# ------------------------------------------------------------------ college player-seasons (prospect models)
def college_players(seasons) -> pd.DataFrame:
    """Torvik-style player-season table (same column names as sources.torvik.FULL_COLS) from ESPN box scores:
    rates use the team's season totals scaled by the player's minutes share. Not available here: age (ESPN has no
    birthdates for college players), recruiting rank, Torvik's ORtg/PORPAG."""
    d = team_directory()
    pb = _player_box(seasons, d)
    games = pd.read_parquet(PROC / "games.parquet")
    g = games[games["played"] & games["poss"].notna() & (games["type"] != "nond1")]
    tt = []
    for me, op in (("a", "b"), ("b", "a")):
        t = pd.DataFrame({"season": g["season"], "team": g[f"team_{me}"], "Tmin": g["minutes"], "poss": g["poss"]})
        for k in ("fgm", "fga", "tpa", "fta", "tov", "orb", "drb"):
            t[f"T{k}"] = g[f"{me}_{k}"]
        for k in ("orb", "drb", "fga", "tpa"):
            t[f"O{k}"] = g[f"{op}_{k}"]
        tt.append(t)
    T = pd.concat(tt).groupby(["season", "team"]).sum().reset_index()
    sums = ["min", "pts", "fgm", "fga", "tpm", "tpa", "ftm", "fta", "orb", "drb", "ast", "stl", "blk", "tov", "pf"]
    pcols = [c for c in ("p_rim_a", "p_rim_m", "p_smid_a", "p_lmid_a", "p_dunk_m") if c in pb]
    a = pb.groupby(["season", "athlete_id", "team"]).agg(player=("name", "last"), gp=("game_id", "nunique"), pos=("pos", "last"),
                                                         **{c: (c, "sum") for c in sums + pcols}).reset_index()
    a = a.merge(T, on=["season", "team"], how="left")
    pgv = pd.concat([pd.read_parquet(PROC / f"pg_{s}.parquet") for s in seasons if (PROC / f"pg_{s}.parquet").exists()])
    pgv = pgv.assign(athlete_id=pgv["pid"].astype("Int64").astype(str))
    for c in ("bpm", "obpm", "dbpm"):
        pgv[f"w{c}"] = pgv[c] * pgv["min"]
    v = pgv.groupby(["season", "athlete_id", "team"]).agg(wbpm=("wbpm", "sum"), wobpm=("wobpm", "sum"), wdbpm=("wdbpm", "sum"),
                                                          vmin=("min", "sum"), cls=("cls", "last"), inches=("inches", "last")).reset_index()
    a = a.merge(v, on=["season", "athlete_id", "team"], how="left")
    f = a["min"] / (a["Tmin"] / 5).clip(lower=1)                     # share of team minutes (1 = every minute)
    poss_used = a["fga"] + 0.44 * a["fta"] + a["tov"]
    out = pd.DataFrame({
        "season": a["season"], "pid": pd.to_numeric(a["athlete_id"], errors="coerce"), "player": a["player"], "team": a["team"],
        "gp": a["gp"], "mpg": a["min"] / a["gp"].clip(lower=1), "min_pct": 100 * f,
        "usg": 100 * poss_used / (f * (a["Tfga"] + 0.44 * a["Tfta"] + a["Ttov"])).clip(lower=1e-6),
        "efg": 100 * (a["fgm"] + 0.5 * a["tpm"]) / a["fga"].where(a["fga"] > 0),
        "ts": 100 * a["pts"] / (2 * (a["fga"] + 0.44 * a["fta"])).where(a["fga"] + a["fta"] > 0),
        "orb_pct": 100 * a["orb"] / (f * (a["Torb"] + a["Odrb"])).clip(lower=1e-6),
        "drb_pct": 100 * a["drb"] / (f * (a["Tdrb"] + a["Oorb"])).clip(lower=1e-6),
        "ast_pct": 100 * a["ast"] / (f * a["Tfgm"] - a["fgm"]).clip(lower=1),
        "to_pct": 100 * a["tov"] / poss_used.where(poss_used > 0),
        "blk_pct": 100 * a["blk"] / (f * (a["Ofga"] - a["Otpa"])).clip(lower=1e-6),
        "stl_pct": 100 * a["stl"] / (f * a["poss"]).clip(lower=1e-6),
        "ftr": 100 * a["fta"] / a["fga"].where(a["fga"] > 0), "pfr": 40 * a["pf"] / a["min"].clip(lower=1),
        "ftm": a["ftm"], "fta": a["fta"], "tpm": a["tpm"], "tpa": a["tpa"], "twopa": a["fga"] - a["tpa"],
        "ast_tov": a["ast"] / a["tov"].where(a["tov"] > 0),
        "rim_m": a.get("p_rim_m"), "rim_a": a.get("p_rim_a"),
        "mid_a": (a["p_smid_a"] + a["p_lmid_a"]) if "p_smid_a" in a else np.nan, "dunk_m": a.get("p_dunk_m"),
        "bpm": a["wbpm"] / a["vmin"].clip(lower=1), "obpm": a["wobpm"] / a["vmin"].clip(lower=1),
        "dbpm": a["wdbpm"] / a["vmin"].clip(lower=1), "gbpm": np.nan, "porpag": np.nan, "dporpag": np.nan,
        "ortg": np.nan, "drtg": np.nan, "pts_pg": a["pts"] / a["gp"].clip(lower=1), "trb_pg": (a["orb"] + a["drb"]) / a["gp"].clip(lower=1),
        "ast_pg": a["ast"] / a["gp"].clip(lower=1), "stl_pg": a["stl"] / a["gp"].clip(lower=1), "blk_pg": a["blk"] / a["gp"].clip(lower=1),
        "cls": a["cls"], "ht": a["inches"].map(lambda h: f"{int(h // 12)}-{int(round(h % 12))}" if pd.notna(h) else None),
        "role": a["pos"].map(POS_ROLE), "rec_rank": np.nan, "dob": None, "pick": np.nan})
    conf = conferences(seasons)
    cm = conf.set_index(["season", "team"])["conf"]
    out["conf"] = [cm.get((s, t)) for s, t in zip(out["season"], out["team"])]
    out = out.dropna(subset=["pid"])
    out["pid"] = out["pid"].astype(int)
    # a player who changed teams mid-season keeps the team where they played the most minutes
    return out.sort_values("mpg").drop_duplicates(["season", "pid"], keep="last").reset_index(drop=True)

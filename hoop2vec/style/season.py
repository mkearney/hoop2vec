"""Season-level style pipeline (from mmkm): ESPN play-by-play -> opponent-adjusted, shrunk team-season table."""
from __future__ import annotations

import re
import warnings

import numpy as np
import pandas as pd

from .. import history
from ..config import ARTIFACTS, CACHE, PROC, current_season
from ..ingest import season_dir
from .adjust import adjust
from .features import EXECUTION, FEATURES, is_pbp_feature, team_game_table
from .roster import espn_roster, torvik_roster

warnings.filterwarnings("ignore", category=pd.errors.PerformanceWarning)
warnings.filterwarnings("ignore", category=pd.errors.ParserWarning)
STYLE_DIR = ARTIFACTS / "style"


def crosswalk(season: int) -> pd.DataFrame:
    """D-I teams of a season: Torvik name <-> ESPN team id (columns torvik, team_id)."""
    from ..config import WOMENS
    cw = pd.read_csv(ARTIFACTS / "crosswalk.csv", dtype={"espn_id": str})
    f = PROC / f"tv_games_{season}.parquet"
    if WOMENS:  # women's games are built from ESPN (womens_data); team names = the identity crosswalk
        g = pd.read_parquet(PROC / "games.parquet")
        g = g[g["season"] == season]
    else:
        g = None
    g = g if g is not None else pd.read_parquet(f) if (season >= current_season() and f.exists()) else (
        pd.read_parquet(history.path("tv_games", season)) if history.path("tv_games", season).exists()
        else pd.read_parquet(f))
    g = g[g["type"] != "nond1"]
    teams = set(g["team_a"]) | set(g["team_b"])
    x = cw[cw["team"].isin(teams)].rename(columns={"team": "torvik", "espn_id": "team_id"})
    return x[["torvik", "team_id"]].drop_duplicates("team_id").reset_index(drop=True)


def _inches(s):
    m = re.match(r"(\d+)-(\d+)", str(s))
    return int(m.group(1)) * 12 + int(m.group(2)) if m else np.nan


def torvik_players(season: int) -> pd.DataFrame:
    """Torvik player-season table with the columns the personnel layer needs."""
    from ..players import load_player_seasons
    p = load_player_seasons([season], refresh_current=False)
    p = p.rename(columns={"player": "name", "rec_rank": "rec_score"})
    for c in ("gp", "mpg", "min_pct", "blk_pct", "bpm", "rec_score"):
        p[c] = pd.to_numeric(p.get(c), errors="coerce")
    if "blk_pct" not in p and "blk" in p:  # women's table: blocks per 40 minutes as the rim-protection proxy
        p["blk_pct"] = 40 * pd.to_numeric(p["blk"], errors="coerce") / pd.to_numeric(p["mins"], errors="coerce").clip(lower=1)
    p["ht_in"] = p["ht"].map(_inches) if "ht" in p else np.nan
    from ..config import WOMENS
    lo, hi = (58, 84) if WOMENS else (66, 91)
    p.loc[(p["ht_in"] < lo) | (p["ht_in"] > hi), "ht_in"] = np.nan
    p["class_n"] = p["cls"].map({"Fr": 1, "So": 2, "Jr": 3, "Sr": 4}).astype(float)
    p["minutes"] = p["mpg"].fillna(0) * p["gp"].fillna(0)
    if "role" not in p:
        p["role"] = None
    return p


def conferences(season: int) -> pd.Series:
    from ..features import conferences as confs
    c = confs([season])
    return c.drop_duplicates("team").set_index("team")["conf"]


def load_season(season: int):
    d = season_dir(season)
    tabs = {t: pd.read_parquet(d / f"{t}.parquet") for t in ("games", "team_games", "player_games", "lineups")}
    for t in tabs.values():
        for c in ("game_id", "team_id", "opp_id", "home_id", "away_id", "athlete_id"):
            if c in t:
                t[c] = t[c].astype(str)
    xw = crosswalk(season)
    tg = team_game_table(tabs["team_games"], tabs["games"], set(xw["team_id"]))
    return tabs, xw, tg


DESCRIPTOR_FEATS = {"pullup_rate", "iso_rate", "drive_rate", "lob_rate", "hook_rate"}
DISTANCE_FEATS = {"smid_rate", "lmid_rate", "two_dist"}
COORD_FEATS = {"c3_share"}


def coverage(tg: pd.DataFrame) -> dict:
    """Which PBP-derived feature families ESPN actually populated this season."""
    t = tg[tg["pbp_ok"]]
    s = t[["p_fga", "fga", "tpa", "p_driving_a", "p_pullup_a", "p_two_dist_n", "p_c3_a", "p_three_a"]].sum()
    return dict(
        descriptors=bool((s["p_driving_a"] + s["p_pullup_a"]) / max(s["p_fga"], 1) > 0.03),
        distance=bool(s["p_two_dist_n"] / max(s["fga"] - s["tpa"], 1) > 0.75),
        coords=bool(s["p_c3_a"] / max(s["p_three_a"], 1) > 0.12),
        pbp_share=float(tg["pbp_ok"].mean()),
    )


def unavailable(cov: dict) -> set:
    bad = set()
    if not cov["descriptors"]:
        bad |= DESCRIPTOR_FEATS
    if not cov["distance"]:
        bad |= DISTANCE_FEATS
    if not cov["coords"]:
        bad |= COORD_FEATS
    return bad


def all_specs():
    for f, (num, den, block, kind, label) in FEATURES.items():
        yield f, num, den, "style"
    for f, (num, den, kind, label) in EXECUTION.items():
        yield f, num, den, "exec"


def build_season(season: int, asof: str | None = None, priors: dict | None = None,
                 half_life_days: float | None = None, loaded=None):
    """Return (team_table, meta, residuals) for one season as of a date (inclusive)."""
    tabs, xw, tg = loaded or load_season(season)
    if asof is not None:
        tg = tg[tg["date"] <= pd.Timestamp(asof)]
    teams = sorted(set(xw["team_id"]))
    tw = None
    if half_life_days:
        age = (tg["date"].max() - tg["date"]).dt.days.to_numpy(float)
        tw = 0.5 ** (age / half_life_days)
    cols = {"team_id": teams}
    meta, resids = {}, {}
    cov = coverage(tg)
    bad = unavailable(cov)
    for f, num, den, kind in all_specs():
        if f in bad:
            for c in ("off", "def"):
                cols[f"{c}_{f}"] = np.full(len(teams), np.nan)
                cols[f"{c}_{f}_sd"] = np.full(len(teams), np.nan)
            meta[f] = dict(mu=np.nan, unavailable=True)
            continue
        sub = tg[tg["pbp_ok"]] if is_pbp_feature(num, den) else tg
        tws = None if tw is None else tw[tg.index.get_indexer(sub.index)]
        sub = sub.reset_index(drop=True)
        po = pdf = None
        if priors and f in priors:
            po, pdf = priors[f]
        r, m, res = adjust(sub, num, den, teams, prior_o=po, prior_d=pdf, time_w=tws,
                           min_den=3.0 if FEATURES.get(f, ("", "", "", "rate"))[3] == "rate" else 1.0)
        for c in ("off", "off_sd", "def", "def_sd", "vol_off", "vol_def"):
            cols[f"{c.split('_')[0]}_{f}{'_' + c.split('_')[1] if '_' in c else ''}"] = r[c].to_numpy()
        meta[f] = m
        if f in ("three_rate", "rim_rate", "poss_len", "trans_rate", "ast_fgm"):
            resids[f] = res
    out = pd.DataFrame(cols)
    # tempo (symmetric) via ppp-like adjust on game possessions per 40
    tg2 = tg.assign(tempo_num=tg["tempo"], one=1.0).reset_index(drop=True)
    r, m, _ = adjust(tg2, "tempo_num", "one", teams, time_w=tw)
    out["tempo"] = (r["off"] + r["def"] - m["mu"]).to_numpy()
    out["games"] = tg.groupby("team_id").size().reindex(teams).fillna(0).to_numpy()
    out["adj_o"] = out["off_ppp"] * 100
    out["adj_d"] = out["def_ppp"] * 100
    out["adj_em"] = out["adj_o"] - out["adj_d"]
    # roster
    gids = set(tg["game_id"])
    er = espn_roster(tabs["player_games"], tabs["lineups"], gids)
    tr = torvik_roster(torvik_players(season), xw)
    out = out.merge(er, on="team_id", how="left").merge(tr, on="team_id", how="left")
    names = tabs["team_games"].groupby("team_id")[["team_name", "team_abbr"]].first()
    out = out.merge(names, left_on="team_id", right_index=True, how="left")
    out = out.merge(xw[["team_id", "torvik"]], on="team_id", how="left")
    out["conf"] = out["torvik"].map(conferences(season))
    out["season"] = season
    out["asof"] = str(tg["date"].max().date()) if len(tg) else None
    meta["_coverage"] = cov
    return out, meta, resids


def priors_from(prev: pd.DataFrame, cur_teams: list[str], rho: dict[str, tuple[float, float]], meta_prev: dict):
    """Prior means for this season = rho * last season's deviation from the league mean (+ this mean ~ prev mean)."""
    p = prev.set_index("team_id").reindex(cur_teams)
    out = {}
    for f, num, den, kind in all_specs():
        mu = meta_prev.get(f, {}).get("mu", np.nan)
        if not np.isfinite(mu) or f"off_{f}" not in p:
            continue
        ro, rd = rho.get(f, (0.5, 0.5))
        dev_o = (p[f"off_{f}"] - mu).fillna(0).to_numpy() * ro
        dev_d = (p[f"def_{f}"] - mu).fillna(0).to_numpy() * rd
        out[f] = (dev_o, dev_d)
    return out


def estimate_persistence(tables: dict[int, pd.DataFrame]) -> dict[str, tuple[float, float]]:
    """Year-over-year regression slope of each team's adjusted deviation (style persistence)."""
    rho = {}
    seasons = sorted(tables)
    for f, num, den, kind in all_specs():
        xs_o, ys_o, xs_d, ys_d = [], [], [], []
        for a, b in zip(seasons[:-1], seasons[1:]):
            A, B = tables[a].set_index("team_id"), tables[b].set_index("team_id")
            j = A.index.intersection(B.index)
            for side, xs, ys in (("off", xs_o, ys_o), ("def", xs_d, ys_d)):
                x = A.loc[j, f"{side}_{f}"] - A[f"{side}_{f}"].mean()
                y = B.loc[j, f"{side}_{f}"] - B[f"{side}_{f}"].mean()
                xs.append(x.to_numpy()); ys.append(y.to_numpy())
        def slope(xs, ys):
            x, y = np.concatenate(xs), np.concatenate(ys)
            ok = np.isfinite(x) & np.isfinite(y)
            return float(np.clip(np.sum(x[ok] * y[ok]) / max(np.sum(x[ok] ** 2), 1e-12), 0, 0.95))
        rho[f] = (slope(xs_o, ys_o), slope(xs_d, ys_d))
    return rho

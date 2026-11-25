"""Pro leagues: player impact from lineups (RAPM) and a box-score model fitted to it (the BPM recipe).

Why. A box-score plus/minus fitted at team level (what the women's college site uses) credits a team's rebounds,
blocks and close-range makes to whoever recorded them, so efficient low-usage centers come out on top: the old PER
bias. The NBA and WNBA play-by-play has every five-player lineup, so impact can be measured directly.

1. RAPM. For every lineup stint (per team per game: possessions, points for and against) the opponent-adjusted
   offensive and defensive efficiency (vs the opponent's *pre-game* rating) is regressed on the five players on
   the floor, with a ridge penalty. Fit on rolling three-season windows, weighted by possessions.
2. Box model. Each player's RAPM (offense and defense separately) is regressed on his box-score rates per 100
   possessions on the floor (scoring volume and efficiency, threes, free throws, assists, turnovers, rebounds,
   steals, blocks, fouls) plus height, weighted by possessions. That gives weights that reward creation and spacing
   the way lineup data says they matter, the way Basketball-Reference's BPM is built.
3. Game values (espn_data.player_games) apply the box model to each player's game line, then add a team adjustment
   so the team's minute-weighted total equals its opponent-adjusted result in that game.

The box weights are global constants fitted on all completed seasons (like any BPM); no team's or player's own
future results enter a game value.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
from scipy import sparse

from .config import ARTIFACTS, current_season

LAMBDA = 3000.0       # ridge penalty, in possessions
WINDOW = 3            # seasons per RAPM fit
BOX = ["pts", "fga", "tpa", "fta", "ast", "tov", "orb", "drb", "stl", "blk", "pf"]
COEF_PATH = ARTIFACTS / "box_rapm.json"


def _stints(season: int, kpre: pd.DataFrame, id2team: dict, games: pd.DataFrame) -> pd.DataFrame:
    from .ingest import season_dir
    f = season_dir(season) / "lineups.parquet"
    if not f.exists():
        return pd.DataFrame()
    L = pd.read_parquet(f)
    L["game_id"], L["team_id"] = L["game_id"].astype(str), L["team_id"].astype(str)
    L["team"] = L["team_id"].map(id2team)
    g = games[games["season"] == season].merge(kpre, on=["season", "gid"], how="left")
    g = g[g["played"] & (g["type"] != "nond1")]
    rows = []
    for me, op in (("a", "b"), ("b", "a")):
        rows.append(pd.DataFrame({"game_id": g["event_id"].astype(str), "team": g[f"team_{me}"],
                                  "opp_d": g[f"{op}_k_d"], "opp_o": g[f"{op}_k_o"], "mu": g["k_mu"],
                                  "home": np.where(g["loc"] == 0, 0.0, np.where(me == "b", 1.0, -1.0))}))
    side = pd.concat(rows)
    L = L.merge(side, on=["game_id", "team"], how="inner")
    L = L[(L["off_poss"] + L["def_poss"]) > 0]
    L["season"] = season
    return L


def fit_rapm(L: pd.DataFrame, lam: float = LAMBDA, h: float = 1.5):
    """Possession-weighted ridge: offense rows y = 100 pts/poss - mu - opp_d - h*home = sum of the five o_p;
    defense rows y = mu + opp_o - 100 pts_allowed/poss + ... = sum of the five d_p (higher = better defense)."""
    players = pd.Index(sorted({p for s in L["lineup"] for p in s.split("|")}))
    n = len(players)
    lin = L["lineup"].str.split("|")
    idx = [players.get_indexer(x) for x in lin]
    r = np.repeat(np.arange(len(L)), [len(x) for x in idx])
    c = np.concatenate(idx)
    X = sparse.csr_matrix((np.ones(len(c)), (r, c)), shape=(len(L), n))
    out = {}
    for kind in ("o", "d"):
        if kind == "o":
            w = L["off_poss"].to_numpy(float)
            y = 100 * L["pts_for"].fillna(0).to_numpy(float) / np.maximum(w, 1) - L["mu"] - L["opp_d"] - h * L["home"]
        else:
            w = L["def_poss"].to_numpy(float)
            y = L["mu"] + L["opp_o"] - 100 * L["pts_against"].fillna(0).to_numpy(float) / np.maximum(w, 1) + h * L["home"]
        y = np.asarray(y, float)
        ok = (w > 0) & np.isfinite(y)
        Xw = X[ok].multiply(w[ok][:, None]).tocsr()
        A = (X[ok].T @ Xw).toarray() + lam * np.eye(n)
        b = np.linalg.solve(A, Xw.T @ y[ok])
        out[kind] = pd.Series(b, index=players)
    poss = pd.Series(np.asarray(X.T @ (L["off_poss"] + L["def_poss"]).to_numpy(float)).ravel() / 2, index=players)
    return pd.DataFrame({"o": out["o"], "d": out["d"], "poss": poss})


def _box_rates(pb: pd.DataFrame) -> pd.DataFrame:
    """Per-100-possession box rates per player over a span, + height."""
    a = pb.groupby("athlete_id").agg(**{c: (c, "sum") for c in BOX}, poss=("on_poss", "sum"), min=("min", "sum"),
                                     inches=("inches", "last"), name=("name", "last"))
    for c in BOX:
        a[f"r_{c}"] = 100 * a[c] / a["poss"].clip(lower=1)
    return a


FEATS = [f"r_{c}" for c in BOX] + ["ht", "r_ast_ht"]


def design(a: pd.DataFrame, ht_mu: float) -> pd.DataFrame:
    X = a[[f"r_{c}" for c in BOX]].copy()
    X["ht"] = (a["inches"] - ht_mu).fillna(0)
    X["r_ast_ht"] = a["r_ast"] * X["ht"]   # a 6'10" passer is rarer (and worth more) than a 6'2" one
    return X[FEATS]


def fit_box(windows: list[tuple[pd.DataFrame, pd.DataFrame]], alpha: float = 50.0) -> dict:
    """Regress RAPM on box rates (pooled windows, weighted by possessions); separate offense / defense models."""
    from sklearn.linear_model import Ridge
    rows = []
    for R, A in windows:
        j = A.join(R[["o", "d"]], how="inner")
        rows.append(j[j["poss"] >= 500])
    D = pd.concat(rows)
    ht_mu = float(np.average(D["inches"].dropna(), weights=D.loc[D["inches"].notna(), "poss"]))
    X = design(D, ht_mu)
    mu, sd = X.mean(), X.std().replace(0, 1)
    Z = (X - mu) / sd
    out = {"ht_mu": ht_mu, "mean": mu.to_dict(), "sd": sd.to_dict(), "n": int(len(D))}
    for k in ("o", "d"):
        m = Ridge(alpha=alpha).fit(Z, D[k], sample_weight=D["poss"])
        out[k] = {"coef": dict(zip(FEATS, m.coef_.tolist())), "intercept": float(m.intercept_)}
        p = m.predict(Z)
        w = D["poss"]
        out[f"r2_{k}"] = float(1 - np.average((D[k] - p) ** 2, weights=w) / np.average((D[k] - np.average(D[k], weights=w)) ** 2, weights=w))
    return out


def predict_box(rates: pd.DataFrame, coef: dict) -> pd.DataFrame:
    """Box-model offense / defense per 100 possessions for rows with r_* columns and inches."""
    X = design(rates, coef["ht_mu"])
    Z = (X - pd.Series(coef["mean"])) / pd.Series(coef["sd"])
    return pd.DataFrame({k: Z.to_numpy() @ np.array([coef[k]["coef"][f] for f in FEATS]) + coef[k]["intercept"]
                         for k in ("o", "d")}, index=rates.index)


def build(games, kpre, pb, d) -> dict:
    """Fit RAPM windows on completed seasons and the box model on top; write artifacts/<league>/box_rapm.json."""
    id2team = dict(zip(d["espn_id"].astype(str), d["team"]))
    seasons = sorted(s for s in games["season"].unique() if s < current_season())
    st = {s: _stints(s, kpre, id2team, games) for s in seasons}
    windows, tops = [], {}
    for end in seasons[WINDOW - 1:]:
        span = [s for s in range(end - WINDOW + 1, end + 1) if s in st and len(st[s])]
        if len(span) < WINDOW:
            continue
        R = fit_rapm(pd.concat([st[s] for s in span], ignore_index=True))
        A = _box_rates(pb[pb["season"].isin(span)])
        windows.append((R, A))
        tops[end] = R
    coef = fit_box(windows)
    COEF_PATH.write_text(json.dumps(coef, indent=1))
    return coef, tops

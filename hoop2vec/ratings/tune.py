"""Tune the dynamic (Kalman) rating parameters for a league: ``python -m hoop2vec tune [--nba|--wnba|...]``.

The defaults were tuned on men's college basketball (mmkm's research). Pro leagues differ a lot: 30 or 15 teams,
82 or 44 games, home court, roster turnover through trades and free agency instead of graduation. The parameters
(observation noise, daily drift, blowout cap, home edge, season carry-over) are chosen by Nelder-Mead to minimize
the log loss of the ratings' own pre-game win probabilities on the training seasons, then checked on the most
recent seasons, which the search never sees. Written to artifacts/<league>/rating_params.json, used by every
later run.

The search starts from the league's current settings. When those start seasons from the roster prior (prior_w > 0),
the engine gets the priors the last feature build saved (data/<league>/processed/priors.parquet) and the prior
weight and the 3-point luck share are tuned too.
"""
from __future__ import annotations

import json
from dataclasses import asdict, replace

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.stats import norm

from ..config import ARTIFACTS, PROC, current_season
from . import kalman as KAL

KEYS = ["R", "q", "cap", "h", "rho0", "rho1", "rho_t", "carry_var", "init_var"]
PRIOR_KEYS = ["prior_w", "luck3_d"]


def _eval(p, G, cont, y, mask, prior=None):
    eng = KAL.RatingEngine(p)
    pre = eng.run(G.drop(columns=["a_home", "gid"]), cont, prior=prior)
    pre = pre.set_index("game_id").reindex(G["game_id"]).reset_index()
    m, _ = KAL.RatingEngine.pre_margin(pre, G, p.h)
    m = m.to_numpy()
    ok = mask & np.isfinite(m)
    sig = np.std(y[ok] - m[ok])
    pr = np.clip(norm.cdf(m[ok] / sig), 1e-4, 1 - 1e-4)
    w = y[ok] > 0
    return float(-np.mean(w * np.log(pr) + (~w) * np.log(1 - pr)))


def tune(test_seasons: int = 3, maxiter: int = 250) -> dict:
    from ..players import load_player_games
    games = pd.read_parquet(PROC / "games.parquet")
    seasons = sorted(games["season"].unique())
    pg = load_player_games(seasons, refresh_current=False)
    cont = KAL.continuity(pg)
    G = KAL.to_engine_games(games[games["season"] < current_season()])
    y = (G["home_score"] - G["away_score"]).to_numpy(float)
    s = G["season"].to_numpy()
    last = int(s.max())
    train = (s >= s.min() + 1) & (s <= last - test_seasons)
    test = s > last - test_seasons
    base = KAL.load_params()  # the league's current settings (defaults when it has none)
    prior, keys = None, list(KEYS)
    if base.prior_w > 0 and (PROC / "priors.parquet").exists():
        pr = pd.read_parquet(PROC / "priors.parquet")
        prior = {(a, b): (c, d) for a, b, c, d in pr[["season", "team", "eff_o", "eff_d"]].itertuples(index=False)}
        keys += PRIOR_KEYS
    x0 = np.array([getattr(base, k) for k in keys])

    def f(x):
        p = replace(base, **{k: float(v) for k, v in zip(keys, x)})
        if p.R <= 10 or p.q < 0 or p.cap < 5 or not (0 <= p.rho0 <= 1) or p.carry_var < 0 or p.init_var < 1 \
                or not (0 <= p.luck3_d <= 1) or not (0 <= p.prior_w <= 2):
            return 9.0
        return _eval(p, G, cont, y, train, prior)
    res = minimize(f, x0, method="Nelder-Mead", options=dict(maxiter=maxiter, xatol=1e-3, fatol=1e-6, adaptive=True))
    best = replace(base, **{k: float(v) for k, v in zip(keys, res.x)})
    out = {"default_test": _eval(base, G, cont, y, test, prior), "tuned_test": _eval(best, G, cont, y, test, prior),
           "default_train": _eval(base, G, cont, y, train, prior), "tuned_train": float(res.fun),
           "test_seasons": [last - test_seasons + 1, last]}
    if out["tuned_test"] < out["default_test"]:
        (ARTIFACTS / KAL.PARAMS_FILE).write_text(json.dumps(asdict(best), indent=1))
        out["saved"] = True
    out["params"] = {k: round(v, 4) for k, v in asdict(best).items()}
    return out

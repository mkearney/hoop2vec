"""Opponent + venue adjustment with empirical-Bayes (hierarchical) shrinkage.

For one feature y = num/den on offense rows:
    y_g = mu + h*home_g + o_off(g) + d_def(g) + e_g,   Var(e_g) = a/den_g + s2
    o_i ~ N(prior_o_i, so2),  d_j ~ N(prior_d_j, sd2)
so2/sd2 (between-team spread) and a/s2 (noise) are estimated from the data (EM / method of moments).
Returns posterior means and SDs, so every team-level style number carries its own uncertainty.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import scipy.sparse as sp


def adjust(df: pd.DataFrame, num: str, den: str, teams: list[str], prior_o=None, prior_d=None,
           time_w: np.ndarray | None = None, n_iter: int = 6, min_den: float = 1.0):
    d = df[df[den] >= min_den]
    if time_w is not None:
        time_w = np.asarray(time_w)[df.index.get_indexer(d.index)]
    y = (d[num] / d[den]).to_numpy(float)
    n = d[den].to_numpy(float)
    tix = {t: i for i, t in enumerate(teams)}
    T = len(teams)
    oi = d["team_id"].map(tix).to_numpy()
    di = d["opp_id"].map(tix).to_numpy()
    R = len(d)
    rows = np.repeat(np.arange(R), 4)
    cols = np.c_[np.zeros(R, int), np.ones(R, int), 2 + oi, 2 + T + di].ravel()
    vals = np.c_[np.ones(R), d["home"].to_numpy(float), np.ones(R), np.ones(R)].ravel()
    X = sp.csr_matrix((vals, (rows, cols)), shape=(R, 2 + 2 * T))
    P = 2 + 2 * T
    po = np.zeros(T) if prior_o is None else np.nan_to_num(np.asarray(prior_o, float))
    pd_ = np.zeros(T) if prior_d is None else np.nan_to_num(np.asarray(prior_d, float))
    m = np.r_[0, 0, po, pd_]

    # initial noise model and team spread
    yv = np.var(y) if R > 2 else 0.0
    if not np.isfinite(yv) or yv < 1e-12:  # feature not recorded / constant: no information
        nan = np.full(T, np.nan)
        out = pd.DataFrame({"team_id": teams, "off": nan, "off_sd": nan, "def": nan, "def_sd": nan,
                            "n_off": 0, "n_def": 0, "vol_off": nan, "vol_def": nan})
        return out, dict(mu=np.nan, home=np.nan, so=np.nan, sd=np.nan, a=np.nan, s2=np.nan, rows=R), \
            pd.DataFrame(columns=["game_id", "team_id", "opp_id", "resid", "z"])
    a, s2 = yv * np.median(n) * 0.5, yv * 0.25
    so2 = sd2 = yv * 0.15
    tw = np.ones(R) if time_w is None else time_w
    for _ in range(n_iter):
        w = tw / (a / n + s2)
        lam = np.r_[1e-6, 1e-6, np.full(T, 1 / so2), np.full(T, 1 / sd2)]
        XtW = X.T.multiply(w).tocsr()
        A = (XtW @ X).toarray() + np.diag(lam)
        b = XtW @ y + lam * m
        L = np.linalg.cholesky(A)
        theta = np.linalg.solve(L.T, np.linalg.solve(L, b))
        Ainv_diag = np.sum(np.linalg.inv(L) ** 2, axis=0)
        # EM updates for between-team variances
        o, dd = theta[2:2 + T], theta[2 + T:]
        vo, vd = Ainv_diag[2:2 + T], Ainv_diag[2 + T:]
        seen_o = np.bincount(oi, minlength=T) > 0
        seen_d = np.bincount(di, minlength=T) > 0
        so2 = max(np.mean(((o - po) ** 2 + vo)[seen_o]), 1e-10)
        sd2 = max(np.mean(((dd - pd_) ** 2 + vd)[seen_d]), 1e-10)
        # method of moments for noise: E[r^2] = a/n + s2
        r = y - X @ theta
        Z = np.c_[1 / n, np.ones(R)]
        coef, *_ = np.linalg.lstsq(Z * np.sqrt(n)[:, None], (r ** 2) * np.sqrt(n), rcond=None)
        a = max(coef[0], 1e-8)
        s2 = max(coef[1], 1e-3 * yv)
    var_r = a / n + s2
    z2 = r ** 2 / var_r
    out = pd.DataFrame({
        "team_id": teams,
        "off": theta[2:2 + T] + theta[0], "off_sd": np.sqrt(Ainv_diag[2:2 + T]),
        "def": theta[2 + T:] + theta[0], "def_sd": np.sqrt(Ainv_diag[2 + T:]),
        "n_off": np.bincount(oi, minlength=T), "n_def": np.bincount(di, minlength=T),
        "vol_off": np.bincount(oi, weights=z2, minlength=T) / np.maximum(np.bincount(oi, minlength=T), 1),
        "vol_def": np.bincount(di, weights=z2, minlength=T) / np.maximum(np.bincount(di, minlength=T), 1),
    })
    meta = dict(mu=theta[0], home=theta[1], so=np.sqrt(so2), sd=np.sqrt(sd2), a=a, s2=s2, rows=R)
    resid = pd.DataFrame({"game_id": d["game_id"].values, "team_id": d["team_id"].values,
                          "opp_id": d["opp_id"].values, "resid": r, "z": r / np.sqrt(var_r)})
    return out, meta, resid

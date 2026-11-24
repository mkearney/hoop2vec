"""Linear game model (from mmkm): margin ~ Normal(mu, sigma) from dynamic ratings, style, availability and context.

mu  = b_k * kalman_margin + b_r * ridge_margin + home + travel + rest + availability
      + style main effects + learned offense x defense interactions (ridge-penalized)
sigma = s0 * sqrt(poss / 68)

All features are point-in-time (see ratings.py / snapshots.py / players.py).
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
from scipy.stats import norm

CORE = ["k_margin", "r_margin", "loc", "travel", "rest", "avail"]


def frame(df: pd.DataFrame) -> pd.DataFrame:
    """hoop2vec per-game table (team_a perspective) -> the linear model's design columns."""
    poss = df["poss_exp"].to_numpy(float) if "poss_exp" in df else np.full(len(df), 68.0)
    X = pd.DataFrame({
        "poss": poss,
        "k_margin": df["k_margin"].to_numpy(float),
        "r_margin": ((df["a_adjem"] - df["b_adjem"]).fillna(0) * poss / 100).to_numpy(float),
        "loc": df["loc"].to_numpy(float),
        "travel": (df["b_dist"] - df["a_dist"]).fillna(0).to_numpy(float),
        "rest": (df["a_rest"].clip(upper=7) - df["b_rest"].clip(upper=7)).fillna(0).to_numpy(float),
        "avail": ((df["a_avail_delta"] - df["b_avail_delta"]).fillna(0) * poss / 100).to_numpy(float),
        "y": (df["margin"] if "margin" in df else pd.Series(np.nan, index=df.index)).to_numpy(float),
    })
    for c in [c for c in df.columns if c[:4] in ("a_of", "a_df", "a_ro", "b_of", "b_df", "b_ro") and c[4:].isdigit()]:
        X[c] = df[c].to_numpy(float)
    return X


def rest_days(G: pd.DataFrame) -> pd.DataFrame:
    long = pd.concat([G[["game_id", "date", "season", "home_id"]].rename(columns={"home_id": "team_id"}),
                      G[["game_id", "date", "season", "away_id"]].rename(columns={"away_id": "team_id"})])
    long = long.sort_values("date")
    long["rest"] = long.groupby(["season", "team_id"])["date"].diff().dt.days.clip(upper=7).fillna(7)
    return long.set_index(["game_id", "team_id"])["rest"]


def build_features(G, pre, snaps, avail, trav, h) -> pd.DataFrame:
    from .ratings import RatingEngine
    G = G.reset_index(drop=True)
    pre = pre.set_index("game_id").reindex(G["game_id"]).reset_index()
    X = pd.DataFrame({"game_id": G["game_id"], "season": G["season"], "date": G["date"],
                      "tourney": G["tourney"].to_numpy(), "neutral": G["neutral"].to_numpy()})
    km, poss = RatingEngine.pre_margin(pre, G, 0.0)  # home handled separately
    X["k_margin"] = km.to_numpy()
    X["poss"] = poss.to_numpy()
    X["loc"] = np.where(G["neutral"], 0.0, 1.0)
    X["k_sd"] = np.sqrt(pre[["k_PoA", "k_PdA", "k_PoB", "k_PdB"]].sum(1)).to_numpy() * X["poss"] / 100
    # snapshot (ridge ratings + style factors) as of the day before
    snaps = snaps.sort_values("asof")
    for side, col, pfx in (("home", "home_id", "a_"), ("away", "away_id", "b_")):
        x = G[["game_id", "season", "date", col]].rename(columns={col: "team_id"}).assign(
            key=lambda d: d["date"] - pd.Timedelta(days=1)).sort_values("key")
        m = pd.merge_asof(x, snaps, left_on="key", right_on="asof", by=["season", "team_id"], direction="backward")
        m = m.set_index("game_id").reindex(G["game_id"])
        for c in [c for c in snaps.columns if c.startswith(("of", "df", "ro")) and not c.endswith("_sd")] + ["adj_em"]:
            X[pfx + c] = m[c].to_numpy()
    X["r_margin"] = (X["a_adj_em"] - X["b_adj_em"]).fillna(0) * X["poss"] / 100
    # travel (neutral & road): log-miles difference, home team perspective
    t = trav.set_index("game_id").reindex(G["game_id"])
    X["travel"] = (np.log1p(t["away_miles"].fillna(500)) - np.log1p(t["home_miles"].fillna(0))).to_numpy()
    X["home_miles"] = t["home_miles"].to_numpy(); X["away_miles"] = t["away_miles"].to_numpy()
    r = rest_days(G)
    X["rest"] = (r.reindex(pd.MultiIndex.from_arrays([G["game_id"], G["home_id"]])).to_numpy()
                 - r.reindex(pd.MultiIndex.from_arrays([G["game_id"], G["away_id"]])).to_numpy())
    av = avail.set_index(["game_id", "team_id"])["avail_adj"]
    ah = av.reindex(pd.MultiIndex.from_arrays([G["game_id"], G["home_id"]])).fillna(0).to_numpy()
    aa = av.reindex(pd.MultiIndex.from_arrays([G["game_id"], G["away_id"]])).fillna(0).to_numpy()
    X["avail"] = (ah - aa) * X["poss"] / 100
    X["a_avail"], X["b_avail"] = ah, aa
    for c in ("k_oA", "k_dA", "k_oB", "k_dB", "k_tA", "k_tB", "k_PoA", "k_PdA", "k_PoB", "k_PdB"):
        X[c] = pre[c].to_numpy()
    X["a_rest"] = r.reindex(pd.MultiIndex.from_arrays([G["game_id"], G["home_id"]])).to_numpy()
    X["b_rest"] = r.reindex(pd.MultiIndex.from_arrays([G["game_id"], G["away_id"]])).to_numpy()
    X["conf_game"] = G["conf_game"].fillna(False).astype(float).to_numpy()
    X["post"] = (G["season_type"] == 3).astype(float).to_numpy()
    X["home_id"], X["away_id"] = G["home_id"].to_numpy(), G["away_id"].to_numpy()
    X["y"] = (G["home_score"] - G["away_score"]).to_numpy(float)
    return X


def style_terms(X: pd.DataFrame) -> pd.DataFrame:
    of = sorted(c[2:] for c in X.columns if c.startswith("a_of"))
    df = sorted(c[2:] for c in X.columns if c.startswith("a_df"))
    ro = sorted(c[2:] for c in X.columns if c.startswith("a_ro"))
    S = {}
    for c in of + df + ro:
        S[f"d_{c}"] = X[f"a_{c}"] - X[f"b_{c}"]
    for o in of:
        for d in df:
            S[f"{o}x{d}"] = X[f"a_{o}"] * X[f"b_{d}"] - X[f"b_{o}"] * X[f"a_{d}"]
    for i, r1 in enumerate(ro):
        for r2 in ro[i + 1:]:
            S[f"{r1}x{r2}"] = X[f"a_{r1}"] * X[f"b_{r2}"] - X[f"b_{r1}"] * X[f"a_{r2}"]
    return pd.DataFrame(S).fillna(0.0)


class GameModel:
    def __init__(self, alpha_style: float = 3000.0, use_style: bool = True):
        self.alpha = alpha_style
        self.use_style = use_style

    def _design(self, X):
        core = X[CORE].fillna(0.0).to_numpy()
        if not self.use_style:
            return core, []
        S = style_terms(X)
        return np.c_[core, S.to_numpy()], list(S.columns)

    def fit(self, X: pd.DataFrame):
        Z, scols = self._design(X)
        y = X["y"].to_numpy()
        # antisymmetric: swapping teams flips every feature and the margin -> no intercept, no centering
        self.mu_, self.sd_ = np.zeros(Z.shape[1]), np.sqrt(np.mean(Z ** 2, 0))
        self.sd_[self.sd_ == 0] = 1
        Zs = Z / self.sd_
        pen = np.r_[np.full(len(CORE), 1e-3), np.full(len(scols), self.alpha)]
        A = Zs.T @ Zs + np.diag(pen)
        self.coef_ = np.linalg.solve(A, Zs.T @ y)
        self.icpt_ = 0.0
        self.cols_ = CORE + scols
        r = y - self.predict_mu(X)
        w = np.sqrt(X["poss"].to_numpy() / 68)
        self.s0_ = float(np.sqrt(np.mean((r / w) ** 2)))
        return self

    def predict_mu(self, X):
        Z, _ = self._design(X)
        return self.icpt_ + ((Z - self.mu_) / self.sd_) @ self.coef_

    def predict(self, X):
        mu = self.predict_mu(X)
        sig = self.s0_ * np.sqrt(X["poss"].to_numpy() / 68)
        return mu, sig, norm.cdf(mu / sig)

    def coefficients(self) -> pd.Series:
        return pd.Series(self.coef_ / self.sd_, index=self.cols_)

    def to_json(self) -> dict:
        return dict(cols=self.cols_, coef=(self.coef_ / self.sd_).tolist(),
                    icpt=float(self.icpt_ - np.sum(self.coef_ * self.mu_ / self.sd_)), s0=self.s0_)


def logloss(y, p):
    p = np.clip(p, 1e-4, 1 - 1e-4)
    w = y > 0
    return float(-np.mean(w * np.log(p) + (~w) * np.log(1 - p)))

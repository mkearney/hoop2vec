"""Forward-chained (season-ahead) backtest shared by every candidate model.

For each test season S, every model is fit only on games from seasons before S and then predicts every
D-I game of S from point-in-time features (ratings as of the day before, availability knowable before
tip-off). The same games are scored for every model, and for the games where ESPN carries a closing
spread we score the betting market too - the honest yardstick for leakage: a model that "beats Vegas"
by a lot is almost always seeing the future.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
from scipy.stats import norm

from ..config import ARTIFACTS, FIRST_TRAIN_SEASON
from . import linear as LIN
from . import train as TR


# ------------------------------------------------------------------ metrics
def log_loss(y, p):
    p = np.clip(np.asarray(p, float), 1e-4, 1 - 1e-4)
    y = np.asarray(y, float)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def metrics(y_margin, p, mu=None) -> dict:
    y = (np.asarray(y_margin) > 0).astype(float)
    p = np.asarray(p, float)
    out = {"n": int(len(y)), "log_loss": log_loss(y, p), "brier": float(np.mean((p - y) ** 2)),
           "accuracy": float(np.mean((p > 0.5) == (y > 0.5)))}
    if mu is not None:
        out["rmse"] = float(np.sqrt(np.mean((np.asarray(y_margin) - np.asarray(mu)) ** 2)))
    return out


def paired_ci(y_margin, p1, p2, n_boot=2000, seed=0):
    """Bootstrap 95% CI of log-loss(p1) - log-loss(p2) on the same games."""
    y = (np.asarray(y_margin) > 0).astype(float)
    l1 = -(y * np.log(np.clip(p1, 1e-4, 1)) + (1 - y) * np.log(np.clip(1 - p1, 1e-4, 1)))
    l2 = -(y * np.log(np.clip(p2, 1e-4, 1)) + (1 - y) * np.log(np.clip(1 - p2, 1e-4, 1)))
    d = l1 - l2
    rng = np.random.default_rng(seed)
    b = [d[rng.integers(0, len(d), len(d))].mean() for _ in range(n_boot)]
    return float(d.mean()), float(np.percentile(b, 2.5)), float(np.percentile(b, 97.5))


# ------------------------------------------------------------------ simple rating baselines
def _tempo(df):
    return ((df["a_adjt"] + df["b_adjt"]) / 200.0).fillna(0.68)


def design(df: pd.DataFrame, kind: str) -> np.ndarray:
    if kind == "efficiency":
        t = _tempo(df)
        return np.column_stack([(df["a_adjem"] - df["b_adjem"]) * t, df["loc"],
                                (df["a_avail_delta"] - df["b_avail_delta"]) * t])
    if kind == "kalman":
        return np.column_stack([df["k_margin"], df["loc"]])
    if kind == "elo":
        return np.column_stack([(df["a_elo"] - df["b_elo"]) / 100.0, df["loc"]])
    raise ValueError(kind)


class OLSNormal:
    """margin ~ X b (no intercept: antisymmetric), sigma from residuals -> P = Phi(mu / sigma)."""

    def __init__(self, kind):
        self.kind = kind

    def fit(self, df):
        X = np.nan_to_num(design(df, self.kind))
        y = df["margin"].to_numpy(float)
        self.b = np.linalg.lstsq(X, y, rcond=None)[0]
        self.s = float(np.std(y - X @ self.b))
        return self

    def predict(self, df):
        mu = np.nan_to_num(design(df, self.kind)) @ self.b
        return mu, np.full(len(mu), self.s), norm.cdf(mu / self.s)


class LinearGame:
    """mmkm's regularized antisymmetric linear game model (dynamic + snapshot ratings, site, rest, availability)."""

    def __init__(self, use_style=False):
        self.m = LIN.GameModel(use_style=use_style)

    def fit(self, df):
        self.m.fit(LIN.frame(df))
        return self

    def predict(self, df):
        return self.m.predict(LIN.frame(df))


class Net:
    def __init__(self, n_models=3, feats=None, seed=0):
        self.n_models, self.feats, self.seed = n_models, feats, seed

    def fit(self, df):
        self.smeans = TR.season_means(df)
        self.models, self.norm = TR.train_models(df, self.smeans, n_models=self.n_models, seed=self.seed,
                                                 feats=self.feats)
        return self

    def predict(self, df):
        sm = TR.season_means(df)
        smeans = pd.concat([self.smeans, sm[~sm.index.isin(self.smeans.index)]])
        return TR.predict_df(self.models, self.norm, smeans, df, feats=self.feats)


def add_derived(feat: pd.DataFrame) -> pd.DataFrame:
    f = feat.copy()
    f["poss_exp"] = (f["k_mut"] + f["a_k_t"] + f["b_k_t"]).clip(55, 85)
    f["k_margin"] = ((f["a_k_o"] - f["a_k_d"]) - (f["b_k_o"] - f["b_k_d"])) * f["poss_exp"] / 100.0
    f["margin"] = f["pts_a"] - f["pts_b"]
    return f


def pregame_view(df: pd.DataFrame) -> pd.DataFrame:
    """What could be known before tip-off: availability from recent missed games only."""
    d = df.copy()
    d["a_avail_delta"] = d["a_avail_delta_pre"]
    d["b_avail_delta"] = d["b_avail_delta_pre"]
    return d


# ------------------------------------------------------------------ driver
def run(feat: pd.DataFrame, models: dict, seasons, lines: pd.DataFrame | None = None, verbose=True):
    """models: name -> factory() returning an object with fit(df) / predict(df) -> (mu, sigma, p)."""
    f = add_derived(feat)
    f = f[f["played"] & f["margin"].notna() & (f["season"] >= FIRST_TRAIN_SEASON)]
    out = []
    for S in seasons:
        tr = f[f["season"] < S]
        te = pregame_view(f[f["season"] == S])
        if te.empty:
            continue
        P = te[["season", "date", "gid", "team_a", "team_b", "loc", "is_ncaa", "ncaa_round", "seed_a", "seed_b",
                "margin", "torvik_p_a"]].copy()
        for name, make in models.items():
            mu, sg, p = make().fit(tr).predict(te)
            P[f"mu_{name}"], P[f"p_{name}"] = np.asarray(mu, float), np.asarray(p, float)
            P[f"sg_{name}"] = np.asarray(sg, float) * np.ones(len(P))
        out.append(P)
        if verbose:
            y = P["margin"]
            msg = " ".join(f"{k}={log_loss(y > 0, P[f'p_{k}']):.4f}" for k in models)
            print(f"  {S}: n={len(P)} {msg}", flush=True)
    P = pd.concat(out, ignore_index=True)
    if lines is not None and len(lines):
        cols = [c for c in ("season", "gid", "line_a", "book") if c in lines]
        P = P.merge(lines[cols].drop_duplicates(["season", "gid"]), on=["season", "gid"], how="left")
    return P


def blend(P: pd.DataFrame, a: str, b: str, w: float, name: str | None = None) -> str:
    """Margin blend w*a + (1-w)*b with mixture variance; adds mu_/sg_/p_ columns, returns the name."""
    name = name or f"{a}+{b}"
    mu = w * P[f"mu_{a}"] + (1 - w) * P[f"mu_{b}"]
    var = w * P[f"sg_{a}"] ** 2 + (1 - w) * P[f"sg_{b}"] ** 2 + w * (1 - w) * (P[f"mu_{a}"] - P[f"mu_{b}"]) ** 2
    P[f"mu_{name}"], P[f"sg_{name}"] = mu, np.sqrt(var)
    P[f"p_{name}"] = norm.cdf(mu / np.sqrt(var))
    return name


def summarize(P: pd.DataFrame, names, ref=None) -> dict:
    ref = ref or names[0]
    y = P["margin"].to_numpy()
    subsets = {
        "all": np.ones(len(P), bool),
        "after_dec1": (P["date"].dt.month <= 4) | (P["date"].dt.month == 12),
        "ncaa": P["is_ncaa"].fillna(False).to_numpy(bool),
    }
    if "line_a" in P:
        has = P["line_a"].notna().to_numpy()
        subsets["line_games"] = has
        subsets["ncaa_line_games"] = has & subsets["ncaa"]
        if "book" in P:
            book = P["book"].fillna(False).astype(bool).to_numpy()
            subsets["consensus_line_games"] = has & ~book
            subsets["book_line_games"] = has & book
    res = {}
    for sub, m in subsets.items():
        m = np.asarray(m, bool)
        if m.sum() == 0:
            continue
        r = {k: metrics(y[m], P.loc[m, f"p_{k}"].to_numpy(), P.loc[m, f"mu_{k}"].to_numpy()) for k in names}
        if sub.endswith("line_games"):
            lm = P.loc[m, "line_a"].to_numpy()
            s = float(np.sqrt(np.mean((y[m] - lm) ** 2)))
            r["market"] = metrics(y[m], norm.cdf(lm / s), lm)
        for k in names:
            if k != ref:
                d, lo, hi = paired_ci(y[m], P.loc[m, f"p_{k}"].to_numpy(), P.loc[m, f"p_{ref}"].to_numpy())
                r[k]["vs_ref"] = {"ref": ref, "delta": d, "lo": lo, "hi": hi}
        res[sub] = r
    return res


def save(res: dict, P: pd.DataFrame, name="backtest"):
    (ARTIFACTS / f"{name}.json").write_text(json.dumps(res, indent=1, default=float))
    keep = [c for c in P.columns if not c.endswith(("_tmp", "_x", "_t"))]
    Q = P[keep].copy()
    for c in Q.columns:
        if c.startswith(("mu_", "p_", "sg_")):
            Q[c] = Q[c].astype("float32")
    Q.to_parquet(ARTIFACTS / f"{name}_games.parquet", index=False, compression="zstd", compression_level=19)

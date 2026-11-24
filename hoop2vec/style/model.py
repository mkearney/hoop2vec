"""Latent style model: quality-residualized, standardized features -> varimax factors -> soft archetypes.

Fit once on historical team-seasons (data/model/style_model.json); daily refresh only *projects* teams.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
from sklearn.decomposition import FactorAnalysis
from sklearn.mixture import GaussianMixture

from .features import FEATURES
from .roster import ROSTER_FEATURES

QUALITY = ["adj_o", "adj_d"]


def block_columns():
    off = [f"off_{f}" for f in FEATURES]
    de = [f"def_{f}" for f in FEATURES]
    ro = [f for f, (lab, b) in ROSTER_FEATURES.items() if b == "roster"]
    return {"offense": off, "defense": de, "roster": ro}


def sd_column(c: str) -> str | None:
    if c.startswith(("off_", "def_")):
        return c + "_sd"
    return None


def _standardize(df: pd.DataFrame, cols, hist_sd: pd.Series):
    """Center within season (removes era drift), scale by historical within-season SD."""
    Z = df[cols].copy()
    Z = Z - df.groupby("season")[cols].transform("mean")
    return (Z / hist_sd[cols]).clip(-4, 4)


def parallel_analysis(Z: np.ndarray, n_iter: int = 20, seed: int = 0) -> int:
    rng = np.random.default_rng(seed)
    ev = np.sort(np.linalg.eigvalsh(np.corrcoef(Z.T)))[::-1]
    rnd = []
    for _ in range(n_iter):
        R = np.column_stack([rng.permutation(Z[:, j]) for j in range(Z.shape[1])])
        rnd.append(np.sort(np.linalg.eigvalsh(np.corrcoef(R.T)))[::-1])
    thr = np.percentile(np.array(rnd), 95, axis=0)
    return int(np.sum(ev > thr))


class StyleModel:
    def __init__(self, spec: dict | None = None):
        self.spec = spec or {}

    # ------------------------------------------------------------------ fit
    def fit(self, hist: pd.DataFrame, k: dict | None = None, n_arch: dict | None = None, seed: int = 0):
        k = k or {}
        n_arch = n_arch or {"offense": 7, "defense": 6, "roster": 6}
        blocks = block_columns()
        spec = {"blocks": {}}
        allc = sum(blocks.values(), [])
        dev = hist[allc] - hist.groupby("season")[allc].transform("mean")
        hist_sd = dev.std()
        qz = _standardize(hist, QUALITY, dev[QUALITY].std() if False else
                          (hist[QUALITY] - hist.groupby("season")[QUALITY].transform("mean")).std())
        for b, cols in blocks.items():
            cols = [c for c in cols if c in hist and hist[c].notna().mean() > 0.95 and hist_sd[c] > 0]
            Z = _standardize(hist, cols, hist_sd).fillna(0.0)
            # quality removal (pooled OLS on standardized AdjO / AdjD)
            Q = np.c_[np.ones(len(Z)), qz.to_numpy()]
            beta, *_ = np.linalg.lstsq(Q, Z.to_numpy(), rcond=None)
            R = Z.to_numpy() - Q @ beta
            rsd = R.std(0)
            R = R / rsd
            kk = k.get(b) or int(np.clip(parallel_analysis(R), 4, 9))
            fa = FactorAnalysis(kk, rotation="varimax", random_state=seed).fit(R)
            L = fa.components_.T  # features x factors
            # transform matrix (FA posterior mean is linear in x)
            A = fa.transform(np.eye(len(cols))) - fa.transform(np.zeros((1, len(cols))))
            sign = np.sign(L[np.abs(L).argmax(0), np.arange(kk)])
            L, A = L * sign, A * sign
            S = R @ A
            order = np.argsort(-np.sum(L ** 2, 0))  # by variance explained
            L, A, S = L[:, order], A[:, order], S[:, order]
            ssd = S.std(0)
            A, S = A / ssd, S / ssd
            gm = GaussianMixture(n_arch[b], covariance_type="full", n_init=8, random_state=seed).fit(S)
            spec["blocks"][b] = dict(
                cols=cols, hist_sd=hist_sd[cols].tolist(), beta=beta.tolist(), resid_sd=rsd.tolist(),
                loadings=L.tolist(), A=A.tolist(), k=kk,
                var_explained=(np.sum(L ** 2, 0) / len(cols)).tolist(),
                hist_scores=np.round(S, 4).tolist(),
                gmm=dict(weights=gm.weights_.tolist(), means=gm.means_.tolist(),
                         covariances=gm.covariances_.tolist()),
            )
        spec["quality_sd"] = (hist[QUALITY] - hist.groupby("season")[QUALITY].transform("mean")).std().tolist()
        spec["train_seasons"] = sorted(int(s) for s in hist["season"].unique())
        self.spec = spec
        return self

    # ------------------------------------------------------------------ transform
    def transform(self, df: pd.DataFrame, season_means: pd.DataFrame | None = None):
        """Project team-season rows. season_means: per-season feature means to center on (defaults to df)."""
        out = {}
        qsd = pd.Series(self.spec["quality_sd"], index=QUALITY)
        qc = df[QUALITY] - df.groupby("season")[QUALITY].transform("mean")
        qz = (qc / qsd).to_numpy()
        for b, s in self.spec["blocks"].items():
            cols = s["cols"]
            X = df.reindex(columns=cols)
            mu = df.groupby("season")[cols].transform("mean") if season_means is None else \
                season_means.reindex(columns=cols).loc[df["season"]].set_axis(df.index)
            hsd = np.array(s["hist_sd"])
            Z = ((X - mu) / hsd).clip(-4, 4).to_numpy()
            miss = np.isnan(Z)
            Z = np.where(miss, 0.0, Z)
            Q = np.c_[np.ones(len(Z)), qz]
            R = (Z - Q @ np.array(s["beta"])) / np.array(s["resid_sd"])
            A = np.array(s["A"])
            S = R @ A
            # uncertainty: feature posterior SDs (opponent-adjust model) propagated linearly
            zsd = np.zeros_like(Z)
            for j, c in enumerate(cols):
                sc = sd_column(c)
                if sc and sc in df:
                    zsd[:, j] = df[sc].to_numpy() / hsd[j] / s["resid_sd"][j]
                else:  # roster features: scale noise with sample size
                    g = df["games"].clip(lower=1).to_numpy() if "games" in df else np.full(len(df), 30)
                    zsd[:, j] = 0.15 * np.sqrt(30 / g)
            zsd = np.where(miss, 1.5, np.nan_to_num(zsd, nan=1.5))
            Ssd = np.sqrt((zsd ** 2) @ (A ** 2))
            gm = self._gmm(b)
            P = gm.predict_proba(S)
            hist = np.array(s["hist_scores"])
            pct = np.stack([(hist[:, j][None, :] < S[:, j][:, None]).mean(1) for j in range(S.shape[1])], 1)
            out[b] = dict(scores=S, sd=Ssd, membership=P, pct=pct, z=Z, missing=miss)
        return out

    def _gmm(self, b):
        g = self.spec["blocks"][b]["gmm"]
        gm = GaussianMixture(len(g["weights"]), covariance_type="full")
        gm.weights_ = np.array(g["weights"])
        gm.means_ = np.array(g["means"])
        gm.covariances_ = np.array(g["covariances"])
        gm.precisions_cholesky_ = np.linalg.cholesky(np.linalg.inv(gm.covariances_))
        return gm

    def save(self, path):
        path.write_text(json.dumps(self.spec))

    @classmethod
    def load(cls, path):
        return cls(json.loads(path.read_text()))

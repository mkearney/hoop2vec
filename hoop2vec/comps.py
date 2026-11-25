"""NBA comparisons for college prospects.

Two kinds, both built on a learned translation from college to NBA:

1. Translation model. A multi-output ridge regression maps a college player-season (the prospect FEATURES) to
   the NBA profile he went on to have in his first NBA seasons: usage, true shooting, 3-point and free-throw rate,
   rebound/assist/steal/block/turnover rates and BPM, all minutes-weighted. It's trained on every college player
   who reached the NBA (500+ minutes) since 2009. Each dimension's out-of-sample R^2 is measured forward-chained
   by class. Distances use all dimensions equally (R^2 weights validated slightly worse) plus height.

2. College comps: past college players at the same stage (same class year, age within 1.5 years) whose
   *projected* NBA profile is closest to the prospect's (plus height), shown with what they
   actually became. "Comp hit rate": the share of the 25 most similar college profiles (everyone, not just NBA
   players) that reached the NBA / became rotation players.

3. "Plays like": real NBA players (incl. internationals) whose *actual* NBA profile is closest to the prospect's
   projected one, with a position-group check.

The metric is validated: for held-out classes, the average actual NBA profile of a player's five comps (chosen
from earlier classes) should predict his real NBA profile. We compare against comps chosen on raw college stats.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import WOMENS
from .prospects import FEATURES, OFFSET, WINDOW

# WNBA pages on basketball-reference have no DRB%/BPM: total rebound rate and Win Shares per 40 stand in
NBA_DIMS = ["usg_pct", "ts_pct", "tpar", "ftr", "orb_pct", "drb_pct", "ast_pct", "stl_pct", "blk_pct", "tov_pct", "bpm"]
if WOMENS:
    NBA_DIMS = [{"drb_pct": "trb_pct", "bpm": "ws_per_40"}.get(d, d) for d in NBA_DIMS]
DIM_LABELS = {"usg_pct": "Usage", "ts_pct": "True shooting", "tpar": "3PA rate", "ftr": "FT rate", "orb_pct": "Off. reb%",
              "drb_pct": "Def. reb%", "ast_pct": "Assist%", "stl_pct": "Steal%", "blk_pct": "Block%", "tov_pct": "Turnover%",
              "bpm": "BPM", "trb_pct": "Total reb%", "ws_per_40": "WS / 40"}
LINE = ["pts", "trb", "ast", "stl", "blk", "fg3_pct", "fg3a", "mpg"]
MIN_NBA_MP = 250 if WOMENS else 500       # early-career minutes needed for a usable pro profile (WNBA seasons are ~40 games)
POOL_MP = 1000 if WOMENS else 2000        # "plays like" pool: pro players with at least this many minutes
VALIDATE_FIRST = 2021 if WOMENS else 2013
POS_GROUP = {"PG": "G", "SG": "G", "G": "G", "SF": "W", "GF": "W", "F": "W", "PF": "B", "C": "B", "FC": "B"}
ROLE_GROUP = {"Pure PG": "G", "Scoring PG": "G", "Combo G": "G", "Wing G": "W", "Wing F": "W", "Stretch 4": "B",
              "PF/C": "B", "C": "B"}
RAW_COLS = ["age", "ht", "r_bpm", "r_obpm", "r_dbpm", "r_usg", "r_ts", "r_ast_pct", "r_to_pct", "r_blk_pct", "r_stl_pct",
            "r_tpa40", "r_drb_pct", "r_orb_pct", "r_ftr", "rec"]
if WOMENS:
    RAW_COLS = [c for c in RAW_COLS if c not in ("age", "rec")]


def _pos_group(pos: str) -> str | None:
    p = str(pos or "").split("-")[0]
    return POS_GROUP.get(p)


def _wavg(df: pd.DataFrame, cols, w="mp") -> pd.DataFrame:
    ww = df[w].clip(lower=0)
    out = {}
    for c in cols:
        v = df[c]
        m = v.notna() & (ww > 0)
        out[c] = float(np.average(v[m], weights=ww[m])) if m.any() else np.nan
    return pd.Series(out)


def nba_profiles(nba: pd.DataFrame, link: pd.DataFrame, last: pd.Series):
    """(a) first-WINDOW-season NBA profile for every matched college player; (b) whole-span profile of every NBA
    player with 2,000+ minutes (the "plays like" pool)."""
    nba = nba.copy()
    for c in NBA_DIMS + LINE:
        if c not in nba:
            nba[c] = np.nan
    # (a) early-career profile of college players
    o = link.merge(last.rename("last").reset_index(), on="pid").merge(nba, on="bbref_id")
    o = o[(o["season"] >= o["last"] + OFFSET) & (o["season"] < o["last"] + OFFSET + WINDOW) & (o["mp"] > 0)]
    early = o.groupby("pid").apply(lambda d: pd.concat([_wavg(d, NBA_DIMS + LINE),
                                                        pd.Series({"nba_mp": d["mp"].sum(), "nba_ws": d["ws"].sum(),
                                                                   "nba_seasons": d["season"].nunique(),
                                                                   "pos": d.sort_values("mp")["pos"].iloc[-1]})]),
                                   include_groups=False)
    early = early[early["nba_mp"] >= MIN_NBA_MP]
    # (b) every NBA player's profile across all seasons in the data
    g = nba[nba["mp"] > 0].groupby("bbref_id")
    car = g.apply(lambda d: pd.concat([_wavg(d, NBA_DIMS + LINE),
                                       pd.Series({"name": d["name"].iloc[-1], "mp": d["mp"].sum(), "ws": d["ws"].sum(),
                                                  "from": int(d["season"].min()), "to": int(d["season"].max()),
                                                  "pos": d.sort_values("mp")["pos"].iloc[-1]})]), include_groups=False)
    car = car[car["mp"] >= POOL_MP]
    car["group"] = car["pos"].map(_pos_group)
    return early, car


class Translator:
    """College features -> standardized NBA profile (multi-output ridge)."""

    def __init__(self, alpha: float = 30.0):
        self.alpha = alpha

    def fit(self, X: pd.DataFrame, Y: pd.DataFrame):
        from sklearn.impute import SimpleImputer
        from sklearn.linear_model import Ridge
        from sklearn.pipeline import make_pipeline
        from sklearn.preprocessing import StandardScaler
        self.ymu, self.ysd = Y.mean(), Y.std().replace(0, 1)
        Z = ((Y - self.ymu) / self.ysd).fillna(0.0)
        self.m = make_pipeline(SimpleImputer(strategy="median"), StandardScaler(), Ridge(alpha=self.alpha)).fit(X[FEATURES], Z)
        return self

    def predict_z(self, X: pd.DataFrame) -> np.ndarray:
        return self.m.predict(X[FEATURES])

    def to_raw(self, Z: np.ndarray) -> pd.DataFrame:
        return pd.DataFrame(Z * self.ysd.to_numpy() + self.ymu.to_numpy(), columns=NBA_DIMS)


def _r2(y, p):
    y, p = np.asarray(y, float), np.asarray(p, float)
    m = ~np.isnan(y)
    return float(1 - np.sum((y[m] - p[m]) ** 2) / np.sum((y[m] - y[m].mean()) ** 2)) if m.sum() > 5 else np.nan


def _final_rows(T, early):
    """Final college season of every player with an early NBA profile (training rows for translation)."""
    F = T[T["final"] & T["pid"].isin(early.index)].copy()
    return F.join(early[NBA_DIMS].add_prefix("y_"), on="pid")


def validate(T, early, first=VALIDATE_FIRST, weights: np.ndarray | None = None, ht_sd: float | None = None) -> dict:
    """Forward-chained by class: translation R^2 per NBA dimension, and comps quality (learned vs raw metric).
    ``weights``: per-dimension weights for the learned comp distance (the live R^2 weights); 1 if None."""
    F = _final_rows(T, early)
    Ycols = [f"y_{d}" for d in NBA_DIMS]
    preds, rows = [], []
    for Y in range(first, int(F["season"].max()) + 1):
        tr, te = F[F["season"] < Y], F[F["season"] == Y]
        if len(te) < 5 or len(tr) < (40 if WOMENS else 100):
            continue
        tm = Translator().fit(tr, tr[Ycols].set_axis(NBA_DIMS, axis=1))
        pz = tm.predict_z(te)
        preds.append(pd.DataFrame(tm.to_raw(pz).to_numpy(), index=te.index, columns=NBA_DIMS))
        # comps: 5 nearest earlier-class NBA players, learned space vs raw standardized college stats
        zt = tm.predict_z(tr)
        w = np.ones(len(NBA_DIMS)) if weights is None else weights
        for name, A, B in (("learned", zt, pz),
                           ("raw", *_raw_space(tr, te))):
            for i, b in enumerate(B):
                d2 = (((A - b) * (w if name == "learned" else 1)) ** 2).sum(1)
                if name == "learned" and ht_sd:
                    d2 = d2 + np.nan_to_num(((tr["ht"].to_numpy() - te["ht"].iloc[i]) / ht_sd) ** 2)
                d = np.sqrt(d2)
                nn = np.argsort(d)[:5]
                rows.append({"metric": name, "idx": te.index[i], **{f"c_{k}": tr[f"y_{k}"].iloc[nn].mean() for k in NBA_DIMS}})
    P = pd.concat(preds)
    r2 = {k: _r2(F.loc[P.index, f"y_{k}"], P[k]) for k in NBA_DIMS}
    C = pd.DataFrame(rows)
    comp_r2 = {}
    for name, g in C.groupby("metric"):
        g = g.set_index("idx")
        comp_r2[name] = {k: _r2(F.loc[g.index, f"y_{k}"], g[f"c_{k}"]) for k in NBA_DIMS}
    return {"translation_r2": r2, "comp_r2": comp_r2, "n_players": int(len(P)),
            "classes": [int(F.loc[P.index, "season"].min()), int(F.loc[P.index, "season"].max())]}


def _raw_space(tr, te):
    mu, sd = tr[RAW_COLS].mean(), tr[RAW_COLS].std().replace(0, 1)
    return ((tr[RAW_COLS] - mu) / sd).fillna(0).to_numpy(), ((te[RAW_COLS] - mu) / sd).fillna(0).to_numpy()


HT_SD = 4.0  # inches of height difference that cost as much as one SD of projected NBA profile (validated)


def _line(r) -> dict:
    return {k: (None if pd.isna(r.get(k)) else round(float(r.get(k)), 3 if k == "fg3_pct" else 1)) for k in LINE}


def build(T, early, car, prospects: pd.DataFrame, k_college=5, k_style=5, k_rate=25) -> list[dict]:
    """Comps for each prospect row (same index order)."""
    F = _final_rows(T, early)
    Ycols = [f"y_{d}" for d in NBA_DIMS]
    tm = Translator().fit(F, F[Ycols].set_axis(NBA_DIMS, axis=1))
    w = np.ones(len(NBA_DIMS))  # equal weights validated better than R^2 weights (see module docstring)
    # historical pool: every college player-season of a finished career (classes with an NBA window started)
    H = T[(T["last"] < prospects["season"].max()) & (T["mpg"].fillna(0) >= 10) & (T["age"].notna() | WOMENS)].copy()
    H["nba"] = H["pid"].isin(early.index)
    Hz = tm.predict_z(H)
    Pz = tm.predict_z(prospects)
    # NBA profile pool, standardized the same way as the translation targets
    cz = ((car[NBA_DIMS] - tm.ymu) / tm.ysd).to_numpy()
    cz = np.nan_to_num(cz)
    proj = tm.to_raw(Pz)
    ht_sd = HT_SD
    out = []
    for i, (idx, p) in enumerate(prospects.iterrows()):
        same = (H["cls_n"] == p["cls_n"]) & (((H["age"] - p["age"]).abs() <= 1.5) | WOMENS)  # no free birth dates for women
        cand = np.flatnonzero(same.to_numpy())
        d = np.sqrt((((Hz[cand] - Pz[i]) * w) ** 2).sum(1) + ((H["ht"].to_numpy()[cand] - p["ht"]) / ht_sd) ** 2
                    if not np.isnan(p["ht"]) else (((Hz[cand] - Pz[i]) * w) ** 2).sum(1))
        d = np.nan_to_num(d, nan=1e9)
        order = cand[np.argsort(d)]
        dsort = np.sort(d)
        # hit rate only over finished careers (complete NBA window), so recent players don't read as misses
        mature = H["mature"].fillna(False).astype(bool).to_numpy()[order]
        near = H.iloc[order[mature][:k_rate]]
        rate = {"n": int(len(near)), "nba": float(near["nba"].mean()) if len(near) else None,
                "success": float(near["success"].fillna(False).astype(bool).mean()) if len(near) else None}
        nba_only = [j for j in order if H["nba"].iat[j]]
        seen, comps = set(), []
        scale = float(np.median(dsort[:k_rate])) if len(dsort) else 1.0
        for j in nba_only:
            h = H.iloc[j]
            if h["pid"] in seen or h["pid"] == p["pid"]:
                continue
            seen.add(h["pid"])
            e = early.loc[h["pid"]]
            dh = 0.0 if np.isnan(p["ht"]) or np.isnan(h["ht"]) else ((h["ht"] - p["ht"]) / ht_sd) ** 2
            dj = float(np.sqrt((((Hz[j] - Pz[i]) * w) ** 2).sum() + dh))
            comps.append({"name": h["player"], "season": int(h["season"]), "team": h["team"], "cls": h["cls"],
                          "pick": None if pd.isna(h["pick"]) else int(h["pick"]), "ws": round(float(e["nba_ws"]), 1),
                          "seasons": int(e["nba_seasons"]), "pos": e["pos"], "line": _line(e),
                          "sim": round(100 * float(np.exp(-0.5 * (dj / max(scale, 1e-6)) ** 2)), 0)})
            if len(comps) >= k_college:
                break
        # plays like: NBA players with the closest actual profile to this projected one
        grp = _pos_group(p.get("pos")) if WOMENS else ROLE_GROUP.get(p.get("role"))
        dd = np.sqrt((((cz - Pz[i]) * w) ** 2).sum(1))
        if grp:
            dd = dd + np.where(car["group"].to_numpy() == grp, 0.0, 0.75)
        sty = []
        for j in np.argsort(dd)[:k_style]:
            c = car.iloc[j]
            sty.append({"name": c["name"], "pos": c["pos"], "years": f"{int(c['from'])}–{int(c['to'])}", "ws": round(float(c["ws"]), 1),
                        "bpm": None if pd.isna(c.get("bpm")) else round(float(c["bpm"]), 1),
                        "ws_per_40": None if pd.isna(c.get("ws_per_40")) else round(float(c["ws_per_40"]), 3), "line": _line(c)})
        pct = {k: (None if pd.isna(v) else round(float((car[k] < v).mean()), 3)) for k, v in proj.iloc[i].items()}
        out.append({"college": comps, "style": sty, "rate": rate,
                    "proj": {k: (None if pd.isna(v) else round(float(v), 3)) for k, v in proj.iloc[i].items()}, "proj_pct": pct})
    return out

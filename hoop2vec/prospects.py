"""NBA prospect models: who gets drafted (and how high), and who succeeds in the NBA.

Data (free, public):
* barttorvik.com player-season tables, 2008 onward: box-score rates, Torvik BPM/PORPAG, shot locations,
  recruiting score, height, role, date of birth, and the player's NBA draft pick (the draft label).
* basketball-reference.com advanced tables, one page per NBA season: minutes, Win Shares, BPM and VORP
  (the NBA-success label), matched to college players by name, timing and age.

Labels
* drafted / pick: the pick a player went at in the draft right after a season, counted only on his final college
  season ("drafted this June"). Earlier seasons of a future pick are "not drafted this year".
* NBA outcome: his first four NBA seasons after leaving college: minutes, Win Shares, VORP, BPM. A player
  counts as an **NBA success** if he averaged rotation-level production: 4-year Win Shares >= SUCCESS_WS.
  A player who never reaches the NBA has an outcome of zero.

Everything a model sees is knowable at the time: rates through that season, age on draft day, team strength,
recruiting, the previous season. The current season (still in progress) is shrunk toward last season and the
population average, the same way for training and live rows.
"""
from __future__ import annotations

import datetime as dt
import json

import numpy as np
import pandas as pd

from . import history
from .config import ARTIFACTS, PRO, PROC, WOMENS, current_season
from .sources.injuries import norm_name

if WOMENS:
    # WNBA: 3-round draft in April, rookie season the same calendar year; 40-44 game seasons (so smaller Win Shares)
    FIRST, PRO_FIRST = 2019, 2018   # college data (ESPN box scores) / first WNBA season fetched
    WINDOW, OFFSET = 3, 0           # outcome = WNBA seasons S..S+2 after a final college season S
    SUCCESS_WS, STAR_WS = 2.5, 5.0  # 3-year WNBA Win Shares: a rotation player (~top 15% of picks) / a star-level start
    THRESHOLDS = (4, 12, 24, 36)    # top 4, first round, top 24, drafted (three rounds)
    FIRST_ROUND = 12
else:
    FIRST, PRO_FIRST = 2008, 2009
    WINDOW, OFFSET = 4, 1           # outcome = NBA seasons S+1..S+4 after a final college season S
    SUCCESS_WS = 8.0                # 4-year Win Shares: an established NBA rotation player
    STAR_WS = 20.0                  # 4-year Win Shares: a starter-plus / star-level start
    THRESHOLDS = (5, 14, 30, 60)
    FIRST_ROUND = 30
K_TOP, K_LOT, K_3, K_ALL = (f"p{k}" for k in THRESHOLDS)  # column names of the cumulative draft probabilities
K_FR = f"p{FIRST_ROUND}"
ROLES = ["Pure PG", "Scoring PG", "Combo G", "Wing G", "Wing F", "Stretch 4", "PF/C", "C"]
CLS_N = {"Fr": 1, "So": 2, "Jr": 3, "Sr": 4}
PRIOR_PREV_MIN = 400.0    # last season's weight (minutes) in this season's shrunk rates
PRIOR_POP_MIN = 150.0     # population mean's weight (minutes)

RATES = ["ortg", "usg", "efg", "ts", "orb_pct", "drb_pct", "ast_pct", "to_pct", "blk_pct", "stl_pct", "ftr", "pfr",
         "bpm", "obpm", "dbpm", "gbpm", "porpag", "dporpag", "drtg", "ft_pct", "tp_pct", "tpa40", "pts40", "rim_rate",
         "rim_pct", "mid_rate", "dunk40", "ast_tov"]
FEATURES = (["age", "ht", "cls_n", "rec", "has_rec", "n_seasons", "min_pct", "mpg", "team_em", "conf_em"]
            + [f"r_{c}" for c in RATES] + ["prev_bpm", "d_bpm"] + [f"role_{r}" for r in ROLES])
if WOMENS:  # no free birth dates or recruiting ranks for women's college players
    FEATURES = [c for c in FEATURES if c not in ("age", "rec", "has_rec", "r_ortg", "r_drtg", "r_gbpm", "r_porpag",
                                                 "r_dporpag", "r_dunk40")]  # nor Torvik-only ratings


# ------------------------------------------------------------------ data
def college(seasons=None, refresh_current: bool = False) -> pd.DataFrame:
    """Torvik player-season tables: committed history for completed seasons + live file for the current one.
    Women's: the same columns built from ESPN box scores, with WNBA draft picks attached."""
    from .sources import torvik
    cur = current_season()
    seasons = seasons or list(range(FIRST, cur + 1))
    if WOMENS:
        from .womens_data import college_players
        c = college_players(seasons)
        dr = wnba_drafts()
        if len(dr):
            c = attach_wnba_picks(c, dr)
        return c
    frames = []
    hist = pd.read_parquet(history.path("college_players")) if history.path("college_players").exists() else None
    for s in seasons:
        if hist is not None and s < cur and (hist["season"] == s).any():
            frames.append(hist[hist["season"] == s])
            continue
        try:
            d = torvik.player_seasons_full(s, refresh=refresh_current if s >= cur else False)
            frames.append(d)
            if s >= cur:  # keep a committed snapshot for runs that can't reach Torvik
                history._w(d.astype({c: "string" for c in d.columns if d[c].dtype == object}),
                           history.path("snap_college_players", s))
        except Exception as e:  # current season unreachable: use the snapshot if there is one
            snap = history.path("snap_college_players", s)
            if snap.exists():
                frames.append(pd.read_parquet(snap))
            else:
                print("college players", s, e)
    c = pd.concat(frames, ignore_index=True)
    c["pid"] = pd.to_numeric(c["pid"], errors="coerce")
    c = c.dropna(subset=["pid"]).drop_duplicates(["season", "pid"], keep="last")
    c["pid"] = c["pid"].astype(int)
    for col in c.columns:
        if col not in ("player", "team", "conf", "cls", "ht", "role", "dob"):
            c[col] = pd.to_numeric(c[col], errors="coerce")
    return c


def nba_seasons() -> pd.DataFrame:
    """Pro seasons (NBA, or WNBA for the women's site) from basketball-reference (committed history)."""
    p = history.path("nba_seasons")
    if p.exists():
        n = pd.read_parquet(p)
    else:
        from .sources import bbref
        fetch = bbref.wnba_season if WOMENS else bbref.season_table
        n = pd.concat([fetch(s) for s in range(PRO_FIRST, current_season())], ignore_index=True)
    for c in ("age", "vorp", "bpm"):
        if c not in n:
            n[c] = np.nan
    return n


def wnba_drafts() -> pd.DataFrame:
    p = history.path("wnba_drafts")
    if p.exists():
        return pd.read_parquet(p)
    from .sources import bbref
    return pd.concat([bbref.wnba_draft(y) for y in range(FIRST, current_season())], ignore_index=True)


def attach_wnba_picks(c: pd.DataFrame, dr: pd.DataFrame) -> pd.DataFrame:
    """Draft pick per college player: same normalized name, drafted the April after her last college season
    (college name checked when it disambiguates). Stored like Torvik's pick column (on every season)."""
    c = c.copy()
    last = c.groupby("pid")["season"].max()
    team_last = c.sort_values("season").groupby("pid")["team"].last()
    car = pd.DataFrame({"last": last, "team": team_last, "nk": c.sort_values("season").groupby("pid")["player"].last().map(_nk)})
    dr = dr.assign(nk=dr["name"].map(_nk))
    m = car.reset_index().merge(dr, on="nk")
    m = m[(m["year"] - m["last"]).between(0, 1)]
    tk = lambda x: norm_name(str(x)).replace("state", "st").split(" ")[0]  # noqa: E731
    m["college_ok"] = [tk(a) == tk(b) for a, b in zip(m["team"], m["college"])]
    m = m.sort_values(["college_ok", "pick"], ascending=[False, True]).drop_duplicates("pid").drop_duplicates(["year", "pick"])
    c["pick"] = c["pid"].map(m.set_index("pid")["pick"])
    c.attrs["wnba_link"] = m[["pid", "bbref_id"]].dropna()
    return c


HIST_COLS = ["season", "pid", "player", "team", "conf", "cls", "ht", "role", "dob", "pick", "rec_rank", "gp", "mpg",
             "min_pct", "ortg", "usg", "efg", "ts", "orb_pct", "drb_pct", "ast_pct", "to_pct", "blk_pct", "stl_pct", "ftr",
             "pfr", "bpm", "obpm", "dbpm", "gbpm", "porpag", "dporpag", "drtg", "ast_tov", "ftm", "fta", "tpm", "tpa",
             "twopa", "rim_m", "rim_a", "mid_a", "dunk_m", "pts_pg", "trb_pg", "ast_pg", "stl_pg", "blk_pg"]


def save_history(col: pd.DataFrame, nba: pd.DataFrame):
    """Compact committed copies (completed seasons only)."""
    keep = col[col["season"] < current_season()].copy()
    keep = keep[[c for c in keep.columns if c in HIST_COLS]]
    for c in keep.columns:
        if keep[c].dtype == "float64":
            keep[c] = keep[c].astype("float32")
        elif keep[c].dtype == object:
            keep[c] = keep[c].astype("string")
    history._w(keep, history.path("college_players"))
    history._w(nba, history.path("nba_seasons"))


def _inches(s):
    try:
        a, b = str(s).split("-")
        v = int(a) * 12 + int(b)
        return float(v) if 66 <= v <= 91 else np.nan
    except ValueError:
        return np.nan


def draft_day(season: int) -> pd.Timestamp:
    return pd.Timestamp(dt.date(season, 6, 25))


# ------------------------------------------------------------------ NBA matching + outcomes
def _nk(s) -> str:
    return norm_name(str(s).replace(",", " "))


def match_nba(col: pd.DataFrame, nba: pd.DataFrame) -> pd.DataFrame:
    """Torvik pid -> basketball-reference id, one-to-one.

    Pass 1 (everyone): same normalized name, NBA debut 1-5 seasons after the last college season, and age on Feb 1 of
    the debut season within a year of Basketball-Reference's. Pass 2 (drafted players only, names often differ:
    "Bam" Adebayo, "Bub" Carrington): same last name, unique candidate with matching debut window and age.
    NBA players already in the league in the first season of our NBA data can't be told apart from debuts, so
    they are never matched."""
    if "dob" not in col:
        col = col.assign(dob=None)
    car = col.sort_values("season").groupby("pid").agg(name=("player", "last"), last=("season", "max"),
                                                       dob=("dob", "last"), pick=("pick", "max"))
    car["nk"] = car["name"].map(_nk)
    car["ln"] = car["nk"].str.split().str[-1]
    car["dob"] = pd.to_datetime(car["dob"], errors="coerce")
    deb = nba.sort_values("season").groupby("bbref_id").agg(name=("name", "first"), debut=("season", "min"),
                                                            age=("age", "first"))
    deb = deb[deb["debut"] > nba["season"].min()]
    deb["nk"] = deb["name"].map(_nk)
    deb["ln"] = deb["nk"].str.split().str[-1]

    def candidates(key):
        pr = car.reset_index().merge(deb.reset_index(), on=key, suffixes=("", "_n"))
        gap = pr["debut"] - pr["last"]
        pr = pr[(gap >= OFFSET) & (gap <= OFFSET + WINDOW)]
        feb1 = pd.to_datetime(pr["debut"].astype(str) + "-02-01")
        age = ((feb1 - pr["dob"]).dt.days / 365.25).apply(np.floor)
        pr = pr[(age.isna()) | ((age - pr["age"]).abs() <= 1)]
        return pr.assign(gap=(pr["debut"] - pr["last"]), drafted=pr["pick"].notna())

    used_p, used_b, out = set(), set(), []
    for r in getattr(col, "attrs", {}).get("wnba_link", pd.DataFrame(columns=["pid", "bbref_id"])).itertuples():
        if r.pid not in used_p and r.bbref_id not in used_b:  # women's: drafted players link directly via the draft page
            used_p.add(r.pid); used_b.add(r.bbref_id)
            out.append((r.pid, r.bbref_id))
    p1 = candidates("nk").sort_values(["gap", "drafted"], ascending=[True, False])
    for r in p1.itertuples():
        if r.pid in used_p or r.bbref_id in used_b:
            continue
        used_p.add(r.pid); used_b.add(r.bbref_id)
        out.append((r.pid, r.bbref_id))
    p2 = candidates("ln")
    p2 = p2[p2["drafted"] & ~p2["pid"].isin(used_p) & ~p2["bbref_id"].isin(used_b)]
    uniq = p2.groupby("pid")["bbref_id"].transform("nunique").eq(1) & p2.groupby("bbref_id")["pid"].transform("nunique").eq(1)
    for r in p2[uniq].drop_duplicates("pid").itertuples():
        out.append((r.pid, r.bbref_id))
    return pd.DataFrame(out, columns=["pid", "bbref_id"])


def outcomes(col: pd.DataFrame, nba: pd.DataFrame, link: pd.DataFrame) -> pd.DataFrame:
    """First WINDOW NBA seasons after leaving college, per pid (zeros if he never played)."""
    last = col.groupby("pid")["season"].max().rename("last")
    o = link.merge(last, left_on="pid", right_index=True).merge(nba, on="bbref_id")
    o = o[(o["season"] >= o["last"] + OFFSET) & (o["season"] < o["last"] + OFFSET + WINDOW)]
    o["bpm_x_mp"] = o["bpm"] * o["mp"]
    g = o.groupby("pid").agg(nba_seasons=("season", "nunique"), nba_mp=("mp", "sum"), nba_ws=("ws", "sum"),
                             nba_vorp=("vorp", "sum"), bpm_x_mp=("bpm_x_mp", "sum"), nba_g=("g", "sum"))
    g["nba_bpm"] = g["bpm_x_mp"] / g["nba_mp"].clip(lower=1)
    g = g.drop(columns="bpm_x_mp")
    out = pd.DataFrame(index=last.index).join(g).join(link.set_index("pid"))
    for c in ("nba_seasons", "nba_mp", "nba_ws", "nba_vorp", "nba_g"):
        out[c] = out[c].fillna(0.0)
    out["last"] = last
    # complete outcome window, and an exit after the first season of NBA data (see match_nba)
    out["mature"] = (out["last"] + OFFSET + WINDOW - 1 <= nba["season"].max()) & (out["last"] >= nba["season"].min() + 1 - OFFSET)
    out["success"] = out["nba_ws"] >= SUCCESS_WS
    out["star"] = out["nba_ws"] >= STAR_WS
    return out


# ------------------------------------------------------------------ features
def team_context(seasons) -> pd.DataFrame:
    from .features import conferences
    c = conferences(seasons)
    if WOMENS:  # no T-Rank: our own adjusted efficiency margin, as of each team's last game of the season
        sn = pd.read_parquet(PROC / "snapshots.parquet").sort_values("snap")
        em = sn.groupby(["season", "team"])["adjem"].last().rename("team_em").reset_index()
        c = c.drop(columns=["team_em"], errors="ignore").merge(em, on=["season", "team"], how="left")
    else:
        c = c.assign(team_em=c["t_adjoe"] - c["t_adjde"])
    c["conf_em"] = c.groupby(["season", "conf"])["team_em"].transform("mean")
    return c[["season", "team", "team_em", "conf_em"]].drop_duplicates(["season", "team"])


def _raw_rates(c: pd.DataFrame) -> pd.DataFrame:
    mins = (c["mpg"] * c["gp"]).clip(lower=1)
    fga = (c["twopa"] + c["tpa"]).clip(lower=1)
    r = pd.DataFrame(index=c.index)
    for k in ("ortg", "usg", "efg", "ts", "orb_pct", "drb_pct", "ast_pct", "to_pct", "blk_pct", "stl_pct", "ftr", "pfr",
              "bpm", "obpm", "dbpm", "gbpm", "porpag", "dporpag", "drtg", "ast_tov"):
        r[k] = c[k]
    r["ft_pct"] = c["ftm"] / c["fta"].where(c["fta"] > 0)
    r["tp_pct"] = c["tpm"] / c["tpa"].where(c["tpa"] > 0)
    r["tpa40"] = 40 * c["tpa"] / mins
    r["pts40"] = 40 * c["pts_pg"] * c["gp"] / mins
    r["rim_rate"] = c["rim_a"] / fga
    r["rim_pct"] = c["rim_m"] / c["rim_a"].where(c["rim_a"] > 0)
    r["mid_rate"] = c["mid_a"] / fga
    r["dunk40"] = 40 * c["dunk_m"] / mins
    r["minutes"] = mins
    return r.replace([np.inf, -np.inf], np.nan)


def features(col: pd.DataFrame) -> pd.DataFrame:
    """One row per player-season with leak-free features (rates shrunk toward last season + population)."""
    c = col.sort_values(["pid", "season"]).reset_index(drop=True)
    c = c[c["gp"].fillna(0) > 0].reset_index(drop=True)
    raw = _raw_rates(c)
    m = raw["minutes"].to_numpy()
    prev_idx = c.groupby("pid").cumcount()
    has_prev = (prev_idx > 0) & (c["pid"].shift(1) == c["pid"]) & (c["season"].shift(1) == c["season"] - 1)
    pop = raw.groupby(c["season"])[RATES].transform("mean")  # this season's population average (prior)
    X = pd.DataFrame(index=c.index)
    w_prev = np.where(has_prev, np.minimum(raw["minutes"].shift(1).fillna(0), PRIOR_PREV_MIN), 0.0)
    for k in RATES:
        cur = raw[k].to_numpy(float)
        prv = raw[k].shift(1).to_numpy(float)
        pv = pop[k].to_numpy(float)
        num = np.nan_to_num(cur) * np.where(np.isnan(cur), 0, m) + np.nan_to_num(prv) * np.where(np.isnan(prv), 0, w_prev) \
            + np.nan_to_num(pv) * PRIOR_POP_MIN
        den = np.where(np.isnan(cur), 0, m) + np.where(np.isnan(prv), 0, w_prev) + PRIOR_POP_MIN
        X[f"r_{k}"] = num / den
    dob = pd.to_datetime(c["dob"], errors="coerce")
    X["age"] = (pd.to_datetime(c["season"].astype(str) + "-06-25") - dob).dt.days / 365.25
    X["ht"] = c["ht"].map(_inches)
    X["cls_n"] = c["cls"].map(CLS_N)
    X["rec"] = c["rec_rank"].fillna(0.0)
    X["has_rec"] = c["rec_rank"].notna().astype(float)
    X["n_seasons"] = c.groupby("pid").cumcount() + 1
    X["min_pct"], X["mpg"] = c["min_pct"], c["mpg"]
    X["prev_bpm"] = np.where(has_prev, raw["bpm"].shift(1), np.nan)
    X["d_bpm"] = raw["bpm"] - X["prev_bpm"]
    for r in ROLES:
        X[f"role_{r}"] = (c["role"] == r).astype(float)
    ctx = team_context(sorted(c["season"].unique()))
    X = X.join(c[["season", "team"]].merge(ctx, on=["season", "team"], how="left")[["team_em", "conf_em"]])
    meta = c[["season", "pid", "player", "team", "conf", "cls", "ht", "role", "dob", "pick", "gp",
              "pts_pg", "trb_pg", "ast_pg", "stl_pg", "blk_pg", "bpm", "obpm", "dbpm", "usg", "ts", "ortg", "ast_pct",
              "to_pct", "blk_pct", "stl_pct", "orb_pct", "drb_pct", "rec_rank", "tpm", "tpa"]].rename(
        columns={"ht": "ht_str", "usg": "usg_raw", "ts": "ts_raw", "bpm": "bpm_raw"})
    return pd.concat([meta, X[FEATURES]], axis=1)


def build_table(refresh_current: bool = False):
    """Features + labels for every player-season 2008..now."""
    col = college(refresh_current=refresh_current)
    nba = nba_seasons()
    done = col[col["season"] < current_season()]
    done.attrs = col.attrs
    link = match_nba(done, nba)
    out = outcomes(col[col["season"] < current_season()], nba, link)
    T = features(col)
    last = col.groupby("pid")["season"].max()
    T["final"] = (T["season"] == T["pid"].map(last)) & (T["season"] < current_season())
    T["drafted"] = T["final"] & T["pick"].notna()
    T["pick_now"] = np.where(T["drafted"], T["pick"], np.nan)
    o = out.reindex(T["pid"])
    for c in ("nba_seasons", "nba_mp", "nba_ws", "nba_vorp", "nba_bpm", "nba_g", "success", "star", "mature", "bbref_id"):
        T[c] = o[c].to_numpy()
    T["last"] = T["pid"].map(last)
    for c in ("age", "rec", "has_rec"):  # not modeled for women (no free data), kept as empty columns
        if c not in T:
            T[c] = np.nan
    return T, col, nba, link


# ------------------------------------------------------------------ models
def _hgb(kind="clf", seed=0):
    from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
    kw = dict(max_iter=300, learning_rate=0.05, max_leaf_nodes=15, min_samples_leaf=40, l2_regularization=1.0,
              random_state=seed)
    return HistGradientBoostingClassifier(**kw) if kind == "clf" else HistGradientBoostingRegressor(**kw)


def _logit(cols):
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.impute import SimpleImputer
    from sklearn.preprocessing import StandardScaler
    return cols, make_pipeline(SimpleImputer(strategy="median"), StandardScaler(), LogisticRegression(C=0.5, max_iter=2000))


# the "simple scout" baseline: recruiting + BPM + age (women's: no recruiting/age data -> class + BPM + height)
BASE_COLS = ["cls_n", "r_bpm", "ht"] if WOMENS else ["rec", "has_rec", "r_bpm", "age"]


def draft_rows(T):
    """Rows a draft model learns from: completed seasons with real playing time."""
    return T[(T["season"] < current_season()) & (T["mpg"].fillna(0) >= 5)]


def success_rows(T):
    """Rows the success model learns from: every season of players whose NBA outcome window is complete."""
    return T[T["mature"].fillna(False).astype(bool) & (T["mpg"].fillna(0) >= 5)]


class DraftModel:
    """Cumulative classifiers P(pick <= k this June) for k in THRESHOLDS; the board orders by their sum."""

    def __init__(self, kind="hgb", seed=0):
        self.kind, self.seed = kind, seed

    def fit(self, D):
        self.m = {}
        for k in THRESHOLDS:
            y = (D["pick_now"] <= k).fillna(False).to_numpy()
            if self.kind == "hgb":
                self.m[k] = _hgb(seed=self.seed).fit(D[FEATURES], y)
            else:
                cols, est = _logit(BASE_COLS)
                self.m[k] = (cols, est.fit(D[cols], y))
        return self

    def predict(self, D) -> pd.DataFrame:
        out = pd.DataFrame(index=D.index)
        prev = None
        for k in THRESHOLDS:
            if self.kind == "hgb":
                p = self.m[k].predict_proba(D[FEATURES])[:, 1]
            else:
                cols, est = self.m[k]
                p = est.predict_proba(D[cols])[:, 1]
            if prev is not None:
                p = np.maximum(p, prev)  # cumulative probabilities must not decrease with k
            out[f"p{k}"] = p
            prev = p
        out["draft_score"] = out[[f"p{k}" for k in THRESHOLDS]].sum(1)
        return out


class SuccessModel:
    """P(NBA success), P(star start) and expected 4-year NBA Win Shares from college features only.

    Regularized GLMs on every feature (standardized): ridge logistic for the two probabilities, Poisson regression
    for Win Shares. With ~15-20 NBA successes per class, these beat gradient boosting out of sample (boosting
    overfits; see the backtest), and feeding them the draft model's projection added nothing."""

    def __init__(self, kind="glm", C=0.02 if WOMENS else 0.05):  # women: fewer successes -> more shrinkage (backtest-tuned)
        self.kind, self.C = kind, C

    def _pipe(self, est):
        from sklearn.impute import SimpleImputer
        from sklearn.pipeline import make_pipeline
        from sklearn.preprocessing import StandardScaler
        return make_pipeline(SimpleImputer(strategy="median"), StandardScaler(), est)

    def fit(self, D):
        from sklearn.linear_model import LogisticRegression, PoissonRegressor
        ys, yst = D["success"].astype(bool).to_numpy(), D["star"].astype(bool).to_numpy()
        if self.kind == "glm":
            self.cols = FEATURES
            self.s = self._pipe(LogisticRegression(C=self.C, max_iter=4000)).fit(D[self.cols], ys)
            self.st = self._pipe(LogisticRegression(C=self.C, max_iter=4000)).fit(D[self.cols], yst)
            self.r = self._pipe(PoissonRegressor(alpha=0.5, max_iter=3000)).fit(D[self.cols], D["nba_ws"].clip(lower=0).to_numpy(float))
        else:  # baseline: recruiting + BPM + age
            self.cols, est = _logit(BASE_COLS)
            self.s = est.fit(D[self.cols], ys)
        return self

    def predict(self, D) -> pd.DataFrame:
        out = pd.DataFrame(index=D.index)
        out["p_success"] = self.s.predict_proba(D[self.cols])[:, 1]
        if self.kind == "glm":
            out["p_star"] = np.minimum(self.st.predict_proba(D[self.cols])[:, 1], out["p_success"])
            out["exp_ws"] = self.r.predict(D[self.cols])
        return out

    def coefficients(self) -> pd.Series:
        """Standardized log-odds weights of the success model (what drives P(success))."""
        return pd.Series(self.s[-1].coef_[0], index=self.cols).sort_values()


# ------------------------------------------------------------------ backtest
def _auc(y, s):
    from sklearn.metrics import roc_auc_score
    y = np.asarray(y, bool)
    return float(roc_auc_score(y, s)) if 0 < y.sum() < len(y) else np.nan


def _ll(y, p):
    p = np.clip(np.asarray(p, float), 1e-4, 1 - 1e-4)
    y = np.asarray(y, float)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def backtest(T, first=None):
    """Forward-chained by draft year: train on seasons before Y, score season Y (draft) / players leaving in Y
    (success). Benchmarks: a recruiting+BPM+age logistic baseline, and for success the actual draft slot."""
    from scipy.stats import spearmanr
    first = first or (2021 if WOMENS else 2012)  # first test class (needs >= 2 earlier classes to train on)
    D = draft_rows(T)
    res_d, rows_d = [], []
    for Y in range(first, current_season()):
        tr, te = D[D["season"] < Y], D[D["season"] == Y]
        if te.empty or te["drafted"].sum() == 0:
            continue
        pm = DraftModel("hgb").fit(tr).predict(te)
        pb = DraftModel("logit").fit(tr).predict(te)
        g = te[["season", "pid", "player", "team", "pick_now", "drafted"]].join(pm).join(pb.add_prefix("base_"))
        rows_d.append(g)
        dr = g[g["drafted"]]
        rank_m = g["draft_score"].rank(ascending=False)
        rank_b = g["base_draft_score"].rank(ascending=False)
        res_d.append({"year": Y, "n_drafted": int(len(dr)),
                      "auc_drafted": _auc(g["drafted"], g[K_ALL]), "auc_drafted_base": _auc(g["drafted"], g["base_" + K_ALL]),
                      "ll_drafted": _ll(g["drafted"], g[K_ALL]), "ll_drafted_base": _ll(g["drafted"], g["base_" + K_ALL]),
                      "auc_lottery": _auc(g["pick_now"] <= THRESHOLDS[1], g[K_LOT]), "auc_lottery_base": _auc(g["pick_now"] <= THRESHOLDS[1], g["base_" + K_LOT]),
                      "spearman_pick": float(spearmanr(rank_m[dr.index], dr["pick_now"]).statistic),
                      "spearman_pick_base": float(spearmanr(rank_b[dr.index], dr["pick_now"]).statistic),
                      "top30_hits": int(g.sort_values("draft_score", ascending=False).head(FIRST_ROUND)["drafted"].sum()),
                      "top30_hits_base": int(g.sort_values("base_draft_score", ascending=False).head(FIRST_ROUND)["drafted"].sum())})
    S = success_rows(T)
    res_s, rows_s = [], []
    for Y in range(first, int(S["last"].max()) + 1):
        tr = S[S["last"] < Y]
        te = S[(S["last"] == Y) & (S["season"] == Y)]  # draft-time view: each player's final college season
        if te.empty or te["success"].sum() == 0:
            continue
        pm = SuccessModel("glm").fit(tr).predict(te)
        pb = SuccessModel("base").fit(tr).predict(te)
        g = te[["season", "pid", "player", "team", "pick_now", "drafted", "success", "star", "nba_ws", "nba_mp"]].join(pm) \
            .join(pb.add_prefix("base_"))
        rows_s.append(g)
        dr = g[g["drafted"]]
        res_s.append({"year": Y, "n": int(len(g)), "n_success": int(g["success"].sum()), "n_drafted": int(len(dr)),
                      "auc": _auc(g["success"], g["p_success"]), "auc_base": _auc(g["success"], g["base_p_success"]),
                      "ll": _ll(g["success"], g["p_success"]), "ll_base": _ll(g["success"], g["base_p_success"]),
                      "auc_drafted_model": _auc(dr["success"], dr["p_success"]),
                      "auc_drafted_pick": _auc(dr["success"], -dr["pick_now"]),
                      "spearman_ws_model": float(spearmanr(dr["exp_ws"], dr["nba_ws"]).statistic) if len(dr) > 5 else np.nan,
                      "spearman_ws_pick": float(spearmanr(-dr["pick_now"], dr["nba_ws"]).statistic) if len(dr) > 5 else np.nan})
    return pd.DataFrame(res_d), pd.concat(rows_d), pd.DataFrame(res_s), pd.concat(rows_s)


def summarize_backtest(rd, gd, rs, gs) -> dict:
    """Pooled metrics (all test years together) + per-year tables for the site."""
    from scipy.stats import spearmanr
    out = {"draft": {}, "success": {}}
    dr = gd[gd["drafted"]]
    rm = gd.groupby("season")["draft_score"].rank(ascending=False)
    rb = gd.groupby("season")["base_draft_score"].rank(ascending=False)
    out["draft"] = {
        "years": [int(gd["season"].min()), int(gd["season"].max())], "n_rows": int(len(gd)), "n_drafted": int(len(dr)),
        "model": {"auc_drafted": _auc(gd["drafted"], gd[K_ALL]), "auc_lottery": _auc(gd["pick_now"] <= THRESHOLDS[1], gd[K_LOT]),
                  "ll_drafted": _ll(gd["drafted"], gd[K_ALL]), "spearman_pick": float(spearmanr(rm[dr.index], dr["pick_now"]).statistic),
                  "top30_hits": float(rd["top30_hits"].mean())},
        "baseline": {"auc_drafted": _auc(gd["drafted"], gd["base_" + K_ALL]), "auc_lottery": _auc(gd["pick_now"] <= THRESHOLDS[1], gd["base_" + K_LOT]),
                     "ll_drafted": _ll(gd["drafted"], gd["base_" + K_ALL]), "spearman_pick": float(spearmanr(rb[dr.index], dr["pick_now"]).statistic),
                     "top30_hits": float(rd["top30_hits_base"].mean())},
        "per_year": rd.to_dict(orient="records"),
    }
    d2 = gs[gs["drafted"]]
    out["success"] = {
        "years": [int(gs["season"].min()), int(gs["season"].max())], "n": int(len(gs)), "n_success": int(gs["success"].sum()),
        "n_drafted": int(len(d2)), "success_ws": SUCCESS_WS, "star_ws": STAR_WS, "window": WINDOW,
        "model": {"auc_all": _auc(gs["success"], gs["p_success"]), "ll_all": _ll(gs["success"], gs["p_success"]),
                  "auc_drafted": _auc(d2["success"], d2["p_success"]),
                  "spearman_ws_drafted": float(spearmanr(d2["exp_ws"], d2["nba_ws"]).statistic)},
        "baseline": {"auc_all": _auc(gs["success"], gs["base_p_success"]), "ll_all": _ll(gs["success"], gs["base_p_success"]),
                     "auc_drafted": _auc(d2["success"], d2["base_p_success"])},
        "draft_slot": {"auc_drafted": _auc(d2["success"], -d2["pick_now"]),
                       "spearman_ws_drafted": float(spearmanr(-d2["pick_now"], d2["nba_ws"]).statistic)},
        "per_year": rs.to_dict(orient="records"),
    }
    # combined: does the model add to the draft slot? (logistic on both, pooled, forward-chained by year)
    from sklearn.linear_model import LogisticRegression
    p = np.full(len(d2), np.nan)
    d2 = d2.assign(lp=np.log(d2["pick_now"]), lm=np.log(d2["p_success"].clip(1e-4)))
    for Y in sorted(d2["season"].unique()):
        tr, te = d2[d2["season"] < Y], d2["season"] == Y
        if tr["season"].nunique() < 3:
            continue
        m = LogisticRegression().fit(tr[["lp", "lm"]], tr["success"].astype(bool))
        p[te.to_numpy()] = m.predict_proba(d2.loc[te, ["lp", "lm"]])[:, 1]
    ok = ~np.isnan(p)
    out["success"]["combined"] = {"auc_drafted": _auc(d2["success"].to_numpy()[ok], p[ok]),
                                  "slot_only_auc_same_games": _auc(d2["success"].to_numpy()[ok], -d2["pick_now"].to_numpy()[ok]),
                                  "model_only_auc_same_games": _auc(d2["success"].to_numpy()[ok], d2["p_success"].to_numpy()[ok])}
    return out


# ------------------------------------------------------------------ live board + site JSON
def _sanitize(x):
    """JSON-safe: numpy -> python, NaN/inf -> None, floats rounded."""
    if isinstance(x, dict):
        return {k: _sanitize(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_sanitize(v) for v in x]
    if isinstance(x, (np.bool_, bool)):
        return bool(x)
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (np.floating, float)):
        return None if not np.isfinite(x) else round(float(x), 4)
    if x is pd.NA or x is pd.NaT:
        return None
    return x


LABELS = {
    "age": "Age on draft day", "ht": "Height", "cls_n": "Class", "rec": "Recruiting score", "has_rec": "Ranked recruit",
    "n_seasons": "College seasons", "min_pct": "Share of team minutes", "mpg": "Minutes per game",
    "team_em": "Team strength (adjusted margin)" if WOMENS else "Team strength (T-Rank margin)", "conf_em": "Conference strength", "r_ortg": "Offensive rating",
    "r_usg": "Usage", "r_efg": "eFG%", "r_ts": "True shooting", "r_orb_pct": "Off. rebound rate", "r_drb_pct": "Def. rebound rate",
    "r_ast_pct": "Assist rate", "r_to_pct": "Turnover rate", "r_blk_pct": "Block rate", "r_stl_pct": "Steal rate",
    "r_ftr": "Free-throw rate", "r_pfr": "Fouls per 40", "r_bpm": "Box plus/minus", "r_obpm": "Offensive BPM",
    "r_dbpm": "Defensive BPM", "r_gbpm": "Game BPM", "r_porpag": "Points over replacement", "r_dporpag": "Def. points over replacement",
    "r_drtg": "Defensive rating", "r_ft_pct": "FT%", "r_tp_pct": "3P%", "r_tpa40": "3PA per 40", "r_pts40": "Points per 40",
    "r_rim_rate": "Rim attempt share", "r_rim_pct": "Rim FG%", "r_mid_rate": "Mid-range share", "r_dunk40": "Dunks per 40",
    "r_ast_tov": "Assist/turnover", "prev_bpm": "Last season's BPM", "d_bpm": "BPM improvement",
    **{f"role_{r}": f"Role: {r}" for r in ROLES},
}
COMP_COLS = ["age", "ht", "r_bpm", "r_obpm", "r_dbpm", "r_usg", "r_ts", "r_ast_pct", "r_to_pct", "r_blk_pct", "r_stl_pct",
             "r_tpa40", "r_drb_pct", "r_orb_pct", "r_ftr", "rec"]
BT_PATH = ARTIFACTS / "prospects_backtest.json"


def comps(hist: pd.DataFrame, rows: pd.DataFrame, k: int = 3) -> list[list[dict]]:
    """Nearest drafted college players (final season, complete NBA outcome) in standardized feature space."""
    H = hist.dropna(subset=["age", "ht", "r_bpm"])
    mu, sd = H[COMP_COLS].mean(), H[COMP_COLS].std().replace(0, 1)
    A = ((H[COMP_COLS] - mu) / sd).fillna(0).to_numpy()
    B = ((rows[COMP_COLS] - mu) / sd).fillna(0).to_numpy()
    out = []
    for b in B:
        d = np.sqrt(((A - b) ** 2).sum(1))
        idx = np.argsort(d)[:k]
        out.append([{"name": H.iloc[i]["player"], "season": int(H.iloc[i]["season"]), "team": H.iloc[i]["team"],
                     "pick": int(H.iloc[i]["pick_now"]), "ws": round(float(H.iloc[i]["nba_ws"]), 1)} for i in idx])
    return out


def run_backtest(T) -> dict:
    """Forward-chained backtest + retrospectives; stored in artifacts (rerun in `backtest`/`all` modes)."""
    rd, gd, rs, gs = backtest(T)
    S = summarize_backtest(rd, gd, rs, gs)
    # calibration of P(success) (pooled forward predictions)
    bins = [0, 0.02, 0.05, 0.15, 0.3, 0.5, 1.01]
    cal = []
    for lo, hi in zip(bins[:-1], bins[1:]):
        m = (gs["p_success"] >= lo) & (gs["p_success"] < hi)
        if m.sum():
            cal.append({"lo": lo, "hi": min(hi, 1.0), "n": int(m.sum()), "pred": float(gs.loc[m, "p_success"].mean()),
                        "obs": float(gs.loc[m, "success"].mean())})
    S["success"]["calibration"] = cal
    # Platt map fitted on the out-of-sample predictions (applied to the live P(success); slope < 1 = less extreme)
    from sklearn.linear_model import LogisticRegression
    lg = np.log(gs["p_success"].clip(1e-5, 1 - 1e-5) / (1 - gs["p_success"].clip(1e-5, 1 - 1e-5))).to_numpy()[:, None]
    pc = LogisticRegression(C=1e6).fit(lg, gs["success"].astype(bool))
    S["success"]["platt"] = {"a": float(pc.intercept_[0]), "b": float(pc.coef_[0][0])}
    ly = int(gd["season"].max())
    last = gd[gd["season"] == ly].sort_values("draft_score", ascending=False).head(30)
    S["draft"]["last_draft"] = {"year": ly, "board": [
        {"rank": i + 1, "name": r.player, "team": r.team, K_FR: round(float(getattr(r, K_FR)), 3), "pick": None if pd.isna(r.pick_now) else int(r.pick_now)}
        for i, r in enumerate(last.itertuples())]}
    my = int(gs["season"].max())
    cls = gs[gs["season"] == my].sort_values("p_success", ascending=False).head(20)
    S["success"]["last_class"] = {"year": my, "board": [
        {"rank": i + 1, "name": r.player, "team": r.team, "p_success": round(float(r.p_success), 3),
         "pick": None if pd.isna(r.pick_now) else int(r.pick_now), "ws": round(float(r.nba_ws), 1), "success": bool(r.success)}
        for i, r in enumerate(cls.itertuples())]}
    try:
        from . import comps as CP
        col = college()
        nba = nba_seasons()
        done = col[col["season"] < current_season()]
        early, _ = CP.nba_profiles(nba, match_nba(done, nba), done.groupby("pid")["season"].max())
        v_raw = CP.validate(T, early)
        v = CP.validate(T, early, ht_sd=CP.HT_SD)
        S["comps"] = {"translation_r2": v["translation_r2"], "learned": v["comp_r2"]["learned"], "raw": v_raw["comp_r2"]["raw"],
                      "n_players": v["n_players"], "classes": v["classes"], "labels": CP.DIM_LABELS}
    except Exception as e:  # comps validation is informative only
        print("comps validation skipped:", e)
    BT_PATH.write_text(json.dumps(_sanitize(S), indent=1))
    return S


def publish(T, write_json, season=None, n_board=400, nba=None, link=None):
    """Fit the final models on all completed seasons and write docs/data/prospects.json."""
    season = season or current_season()
    D, S = draft_rows(T), success_rows(T)
    dm, sm = DraftModel("hgb").fit(D), SuccessModel("glm").fit(S)
    cur = T[(T["season"] == season) & (T["mpg"].fillna(0) >= 5) & (T["age"].notna() | WOMENS)].copy()
    if cur.empty:
        return None
    cur = cur.join(dm.predict(cur)).join(sm.predict(cur))
    bt = json.loads(BT_PATH.read_text()) if BT_PATH.exists() else None
    platt = (bt or {}).get("success", {}).get("platt")
    if platt and platt["b"] <= 1:  # only ever soften: a slope > 1 fitted on a few dozen successes would sharpen noise
        z = np.log(cur["p_success"].clip(1e-5, 1 - 1e-5) / (1 - cur["p_success"].clip(1e-5, 1 - 1e-5)))
        cur["p_success"] = 1 / (1 + np.exp(-(platt["a"] + platt["b"] * z)))
        cur["p_star"] = np.minimum(cur["p_star"], cur["p_success"])
    cur["board"] = cur["draft_score"].rank(ascending=False, method="first").astype(int)
    cur["succ_rank"] = cur["p_success"].rank(ascending=False, method="first").astype(int)
    show = cur[(cur["board"] <= n_board) | (cur["succ_rank"] <= 200)].sort_values("board")
    hist = T[T["final"] & T["drafted"] & T["mature"].fillna(False).astype(bool)]
    from . import comps as CP
    nba = nba if nba is not None else nba_seasons()
    done = T[T["season"] < season]
    link = link if link is not None else match_nba(college()[lambda c: c["season"] < season], nba)
    early, car = CP.nba_profiles(nba, link, done.groupby("pid")["season"].max())
    cp = CP.build(T, early, car, show)
    coef = sm.coefficients()
    players = []
    for (i, r), c in zip(show.iterrows(), cp):
        players.append({
            "pid": int(r.pid), "name": r.player, "team": r.team, "conf": r.conf, "cls": r.cls, "age": r.age, "ht": r.ht_str,
            "role": r.role if isinstance(r.role, str) else None, "rec": r.rec_rank, "g": r.gp, "mpg": r.mpg,
            "pts": r.pts_pg, "reb": r.trb_pg, "ast": r.ast_pg, "stl": r.stl_pg, "blk": r.blk_pg, "bpm": r.bpm_raw,
            "obpm": r.obpm, "dbpm": r.dbpm, "usg": r.usg_raw, "ts": r.ts_raw, "ast_pct": r.ast_pct, "to_pct": r.to_pct,
            "blk_pct": r.blk_pct, "stl_pct": r.stl_pct, "tp_pct": (100 * r.tpm / r.tpa) if r.tpa else None, "tpa": r.tpa / max(r.gp, 1),
            "board": int(r.board), "succ_rank": int(r.succ_rank), "draft_score": r.draft_score, **{f"p{k}": r[f"p{k}"] for k in THRESHOLDS},
            "p_success": r.p_success, "p_star": r.p_star, "exp_ws": r.exp_ws, **c})
    hist_rates = {}
    tiers = list(zip((1,) + tuple(t + 1 for t in THRESHOLDS[:-1]), THRESHOLDS))
    for lo, hi in tiers:
        x = hist[hist["pick_now"].between(lo, hi)]
        hist_rates[f"{lo}-{hi}"] = {"n": int(len(x)), "success": float(x["success"].mean()), "star": float(x["star"].mean()),
                                    "median_ws": float(x["nba_ws"].median())}
    out = {"season": season, "draft_year": season, "n_players": int(len(cur)), "window": WINDOW, "pro": PRO,
           "womens": WOMENS, "thresholds": list(THRESHOLDS), "pro_first": PRO_FIRST, "pro_last": int(nba["season"].max()),
           "success_ws": SUCCESS_WS, "star_ws": STAR_WS, "train": {"draft_years": [int(D["season"].min()), int(D["season"].max())],
           "success_classes": [int(S["last"].min()), int(S["last"].max())], "n_draft_rows": int(len(D)), "n_success_rows": int(len(S))},
           "by_slot": hist_rates,
           "drivers": {"up": [{"f": LABELS.get(k, k), "w": round(float(v), 3)} for k, v in coef.tail(8)[::-1].items()],
                       "down": [{"f": LABELS.get(k, k), "w": round(float(v), 3)} for k, v in coef.head(6).items()]},
           "backtest": bt, "players": players}
    out = _sanitize(out)
    write_json("prospects.json", out)
    return out


def update_history():
    """After an NBA season ends: refetch completed NBA seasons and refresh the committed college history."""
    from .sources import bbref
    fetch = bbref.wnba_season if WOMENS else bbref.season_table
    nba = pd.concat([fetch(s) for s in range(PRO_FIRST, current_season())], ignore_index=True)
    if WOMENS:
        history._w(pd.concat([bbref.wnba_draft(y) for y in range(FIRST, current_season())]), history.path("wnba_drafts"))
    save_history(college(), nba)

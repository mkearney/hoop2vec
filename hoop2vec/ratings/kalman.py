"""Dynamic team ratings: a Glicko/Kalman-style filter on offensive & defensive efficiency and tempo.

State per team (points per 100 possessions vs. league average, neutral court):
    o  offense (higher = better),  d  defense allowed (lower = better),  t  tempo (possessions/40 above avg)
each with a variance. For each game, A's offensive efficiency y = 100*ptsA/poss is observed:
    y = mu + o_A + d_B + h*loc_A + e,   Var(e) = R * 70/poss
Updates use a diagonal (per-team independent) Kalman step; residuals are Huber-capped so blowouts
don't swamp the signal. Between games team variances grow by q per day; between seasons ratings
regress toward a prior whose weight depends on returning minutes (roster continuity).

Every game gets the PRE-game state recorded, so the output is leak-free by construction.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from ..config import GAME_MINUTES, POSS_RANGE


@dataclass
class RatingParams:
    R: float = 150.0            # obs noise variance (pts/100)^2 for a 70-poss game
    q: float = 0.08             # daily random-walk variance
    cap: float = 30.0           # Huber cap on residual (pts/100)
    h: float = 1.6              # home edge per side (pts/100): home offense +h, away offense -h
    rho0: float = 0.45          # season carryover with zero returning minutes
    rho1: float = 0.40          # extra carryover per unit returning minutes share
    rho_t: float = 0.25         # extra carryover per unit transfer-in share
    carry_var: float = 18.0     # extra variance at season start
    init_var: float = 60.0      # variance for a brand-new team
    new_mean: float = -6.0      # starting o (and -d) for brand-new D-I teams
    tR: float = 9.0             # tempo obs noise
    tq: float = 0.01
    t_rho: float = 0.6


class RatingEngine:
    def __init__(self, params: RatingParams | None = None):
        self.p = params or RatingParams()

    def run(self, games: pd.DataFrame, cont: pd.DataFrame | None = None, record: bool = True,
            init: pd.DataFrame | None = None, init_mu: tuple | None = None):
        """init: saved end-of-season state (team_id, o, d, t, Po, Pd, Pt, season) to continue from."""
        p = self.p
        g = games.sort_values(["date", "game_id"]).reset_index(drop=True)
        ids = np.r_[g["home_id"].to_numpy(), g["away_id"].to_numpy()]
        if init is not None:
            ids = np.r_[init["team_id"].to_numpy(), ids]
        teams = pd.Index(pd.unique(ids))
        T = len(teams)
        o = np.zeros(T); d = np.zeros(T); t = np.zeros(T)
        Po = np.full(T, p.init_var); Pd = np.full(T, p.init_var); Pt = np.full(T, 25.0)
        last = np.full(T, -1, dtype=np.int64)  # last day index each team was updated
        seen_season = np.full(T, -1)
        if init is not None:
            k = teams.get_indexer(init["team_id"])
            o[k], d[k], t[k] = init["o"], init["d"], init["t"]
            Po[k], Pd[k], Pt[k] = init["Po"], init["Pd"], init["Pt"]
            seen_season[k] = init["season"]
        hi = teams.get_indexer(g["home_id"]); ai = teams.get_indexer(g["away_id"])
        loc = np.where(g["neutral"].to_numpy(), 0.0, 1.0)
        poss = g["poss"].to_numpy(float)
        mins = g["minutes"].to_numpy(float)
        yh = 100 * g["home_score"].to_numpy(float) / poss
        ya = 100 * g["away_score"].to_numpy(float) / poss
        tempo = poss * GAME_MINUTES / mins
        day = ((g["date"] - g["date"].min()).dt.days).to_numpy()
        season = g["season"].to_numpy()
        cmap = {}
        if cont is not None:
            for r in cont.itertuples():
                cmap[(r.season, r.team_id)] = (r.returning, r.transfer_in)
        mu, mu_t = init_mu if init_mu else (103.0, 68.0)
        out = np.zeros((len(g), 12)) if record else None
        cur_season = None
        first_season = (season.min() if len(season) else None) if init is None else -1
        for i in range(len(g)):
            s = season[i]
            if s != cur_season:
                cur_season = s
            for k in (hi[i], ai[i]):
                if seen_season[k] != s:  # season rollover for this team
                    if seen_season[k] == -1:
                        nm = 0.0 if s == first_season else p.new_mean
                        o[k], d[k] = nm, -nm
                        Po[k] = Pd[k] = p.init_var
                    else:
                        ret, tin = cmap.get((s, teams[k]), (0.55, 0.05))
                        rho = np.clip(p.rho0 + p.rho1 * ret + p.rho_t * tin, 0, 1)
                        o[k] *= rho; d[k] *= rho
                        Po[k] = rho ** 2 * Po[k] + p.carry_var
                        Pd[k] = rho ** 2 * Pd[k] + p.carry_var
                        t[k] *= p.t_rho; Pt[k] = p.t_rho ** 2 * Pt[k] + 4.0
                    seen_season[k] = s
                    last[k] = day[i]
                dt = max(0, day[i] - last[k])
                Po[k] += p.q * dt; Pd[k] += p.q * dt; Pt[k] += p.tq * dt
                last[k] = day[i]
            A, B = hi[i], ai[i]
            if record:
                out[i] = (o[A], d[A], o[B], d[B], Po[A], Pd[A], Po[B], Pd[B], t[A], t[B], mu, mu_t)
            Rg = p.R * 70.0 / poss[i]
            # home offense vs away defense
            for (off, de, y, sgn) in ((A, B, yh[i], 1.0), (B, A, ya[i], -1.0)):
                pred = mu + o[off] + d[de] + sgn * p.h * loc[i]
                r = np.clip(y - pred, -p.cap, p.cap)
                S = Po[off] + Pd[de] + Rg
                o[off] += Po[off] / S * r
                d[de] += Pd[de] / S * r
                Po[off] -= Po[off] ** 2 / S
                Pd[de] -= Pd[de] ** 2 / S
                mu += 0.002 * (y - sgn * p.h * loc[i] - mu)
            # tempo
            tp = mu_t + t[A] + t[B]
            r = np.clip(tempo[i] - tp, -15, 15)
            S = Pt[A] + Pt[B] + p.tR
            t[A] += Pt[A] / S * r; t[B] += Pt[B] / S * r
            Pt[A] -= Pt[A] ** 2 / S; Pt[B] -= Pt[B] ** 2 / S
            mu_t += 0.002 * (tempo[i] - mu_t)
        self.state = dict(teams=teams, o=o, d=d, t=t, Po=Po, Pd=Pd, Pt=Pt, mu=mu, mu_t=mu_t,
                          season=seen_season, last_day=last, day0=g["date"].min())
        if not record:
            return None
        cols = ["k_oA", "k_dA", "k_oB", "k_dB", "k_PoA", "k_PdA", "k_PoB", "k_PdB", "k_tA", "k_tB", "k_mu", "k_mut"]
        pre = pd.DataFrame(out, columns=cols)
        pre["game_id"] = g["game_id"].to_numpy()
        return pre

    @staticmethod
    def pre_margin(pre: pd.DataFrame, games: pd.DataFrame, h: float):
        """Pre-game expected home margin (points) from the ratings alone."""
        poss = (pre["k_mut"] + pre["k_tA"] + pre["k_tB"]).clip(*POSS_RANGE)
        loc = np.where(games["neutral"].to_numpy(), 0.0, 1.0)
        em = (pre["k_oA"] - pre["k_dA"]) - (pre["k_oB"] - pre["k_dB"]) + 2 * h * loc
        return em * poss / 100, poss

    def save_state(self) -> pd.DataFrame:
        s = self.state
        return pd.DataFrame({"team_id": s["teams"], "o": s["o"], "d": s["d"], "t": s["t"], "Po": s["Po"],
                             "Pd": s["Pd"], "Pt": s["Pt"], "season": s["season"], "mu": s["mu"], "mu_t": s["mu_t"]})

    def team_table(self) -> pd.DataFrame:
        s = self.state
        return pd.DataFrame({"team_id": s["teams"], "k_o": s["o"], "k_d": s["d"], "k_t": s["t"],
                             "k_o_sd": np.sqrt(s["Po"]), "k_d_sd": np.sqrt(s["Pd"]), "k_season": s["season"],
                             "adj_o": s["mu"] + s["o"], "adj_d": s["mu"] + s["d"], "adj_em": s["o"] - s["d"],
                             "tempo": s["mu_t"] + s["t"]})


def params_dict(p: RatingParams) -> dict:
    return asdict(p)


# ---------------------------------------------------------------------------- Torvik-keyed adapters
PARAMS_FILE = "rating_params.json"


def load_params() -> RatingParams:
    import json

    from ..config import ARTIFACTS
    f = ARTIFACTS / PARAMS_FILE
    return RatingParams(**json.loads(f.read_text())) if f.exists() else RatingParams()


def to_engine_games(games: pd.DataFrame) -> pd.DataFrame:
    """Torvik games table (team_a/team_b, loc from team_a's side) -> engine layout (home/away, neutral)."""
    g = games[(games["type"] != "nond1") & games["played"] & games["poss"].notna() & (games["poss"] > 40)
              & games["pts_a"].notna()].copy()
    a_home = g["loc"].to_numpy() >= 0
    return pd.DataFrame({
        "game_id": (g["season"].astype(str) + ":" + g["gid"].astype(str)).to_numpy(),  # Torvik gids repeat across seasons
        "gid": g["gid"].to_numpy(), "season": g["season"].to_numpy(), "date": g["date"].to_numpy(),
        "home_id": np.where(a_home, g["team_a"], g["team_b"]), "away_id": np.where(a_home, g["team_b"], g["team_a"]),
        "home_score": np.where(a_home, g["pts_a"], g["pts_b"]).astype(float),
        "away_score": np.where(a_home, g["pts_b"], g["pts_a"]).astype(float),
        "neutral": (g["loc"] == 0).to_numpy(), "poss": g["poss"].to_numpy(float),
        "minutes": (g["minutes"].fillna(5 * GAME_MINUTES).clip(lower=5 * GAME_MINUTES) / 5.0).to_numpy(float),
        "a_home": a_home,
    })


def continuity(pg: pd.DataFrame) -> pd.DataFrame:
    """Share of last season's team minutes that returns (same player id, same team) and the share of
    incoming transfers' previous minutes (their old-team share). Rosters are public before the season, so
    knowing *who* is on a team is point-in-time; minutes are only ever taken from the previous season."""
    m = pg[pg["min"] > 0].groupby(["season", "team", "pid"])["min"].sum().reset_index()
    m["share"] = m["min"] / m.groupby(["season", "team"])["min"].transform("sum")
    out = []
    for s in sorted(m["season"].unique())[1:]:
        prev = m[m["season"] == s - 1][["team", "pid", "share"]]
        cur = m[m["season"] == s][["team", "pid"]].drop_duplicates()
        ret = prev.merge(cur, on=["team", "pid"]).groupby("team")["share"].sum().rename("returning")
        mv = prev.rename(columns={"team": "old"}).merge(cur, on="pid")
        mv = mv[mv["old"] != mv["team"]].groupby("team")["share"].sum().rename("transfer_in")
        df = pd.concat([ret, mv], axis=1).fillna(0.0).reset_index().rename(columns={"index": "team"})
        df["season"] = s
        out.append(df)
    c = pd.concat(out, ignore_index=True).rename(columns={"team": "team_id"})
    c["transfer_in"] = c["transfer_in"].clip(0, 1)
    return c


def pregame(games: pd.DataFrame, cont: pd.DataFrame, params: RatingParams | None = None):
    """Pre-game Kalman state for every played D-I game, re-keyed to team_a/team_b.

    Returns (per-game frame keyed by gid with a_k_o, a_k_d, a_k_t, a_k_sd, b_..., k_mu, k_mut), engine)."""
    p = params or load_params()
    G = to_engine_games(games)
    eng = RatingEngine(p)
    pre = eng.run(G.drop(columns=["a_home", "gid"]), cont)
    pre = pre.set_index("game_id").reindex(G["game_id"]).reset_index()
    ah = G["a_home"].to_numpy()
    pick = lambda h, a: np.where(ah, pre[h].to_numpy(), pre[a].to_numpy())  # noqa: E731
    out = pd.DataFrame({"season": G["season"].to_numpy(), "gid": G["gid"].to_numpy()})
    out["a_k_o"], out["b_k_o"] = pick("k_oA", "k_oB"), pick("k_oB", "k_oA")
    out["a_k_d"], out["b_k_d"] = pick("k_dA", "k_dB"), pick("k_dB", "k_dA")
    out["a_k_t"], out["b_k_t"] = pick("k_tA", "k_tB"), pick("k_tB", "k_tA")
    out["a_k_sd"] = np.sqrt(pick("k_PoA", "k_PoB") + pick("k_PdA", "k_PdB"))
    out["b_k_sd"] = np.sqrt(pick("k_PoB", "k_PoA") + pick("k_PdB", "k_PdA"))
    out["k_mu"], out["k_mut"] = pre["k_mu"].to_numpy(), pre["k_mut"].to_numpy()
    return out, eng


def state_for_unplayed(eng: RatingEngine, teams, asof) -> pd.DataFrame:
    """Latest state (with variance grown to ``asof``) for scheduled games and live predictions."""
    s = eng.state
    idx = {t: i for i, t in enumerate(s["teams"])}
    day = (pd.Timestamp(asof) - pd.Timestamp(s["day0"])).days
    rows = []
    for t in teams:
        i = idx.get(t)
        if i is None:
            rows.append((t, np.nan, np.nan, np.nan, np.nan))
            continue
        gap = max(0, day - int(s["last_day"][i]))
        q = eng.p.q * gap
        rows.append((t, s["o"][i], s["d"][i], s["t"][i], float(np.sqrt(s["Po"][i] + s["Pd"][i] + 2 * q))))
    return pd.DataFrame(rows, columns=["team", "k_o", "k_d", "k_t", "k_sd"]).set_index("team")

"""Player-quality layer: player values, roster strength, availability (injury) effects.

Value unit = BPM-style points per 100 possessions above an average D-I player.
Team roster strength R = sum_i f_i * v_i where f_i = share of team minutes (sums to 5),
which approximates a team's net rating per 100 possessions.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import espn_fallback as espn_season
from . import history
from .config import PROC, current_season
from .sources import torvik

K_MIN = 300.0          # prior strength in minutes for in-season player values
K_NEW = K_MIN          # tested 75 (faster box-score trust for unmatched newcomers): worse agreement
NEW_PRIOR = -3.0       # prior value for unmatched ESPN-fallback newcomers
SYNTH_PID = 90_000_000 # ids >= this are ESPN athletes not matched to a Torvik player
V_REPL = -2.0          # value of the minutes a missing player hands to the bench
PART_DECAY = 0.95      # per-game recency decay for participation (mirrors rating recency)
CLS_NUM = {"Fr": 1, "So": 2, "Jr": 3, "Sr": 4}


def load_player_games(seasons, refresh_current=True) -> pd.DataFrame:
    frames = []
    for s in seasons:
        p = PROC / f"pg_{s}.parquet"
        if s >= current_season():
            # current season comes from espn_season.current_player_games (Torvik snapshot + ESPN box
            # scores); the daily job never downloads Torvik's player-game json
            if p.exists():
                frames.append(pd.read_parquet(p))
            continue
        if p.exists():
            frames.append(pd.read_parquet(p))
            continue
        if history.has("pg", s):
            frames.append(history.load_pg(s))
            continue
        try:
            d = torvik.player_games(s)
        except Exception as e:
            print("player games", s, e)
            continue
        d = d.astype({"cls": "string", "loc": "string"})
        d.to_parquet(p)
        frames.append(d)
    return pd.concat(frames, ignore_index=True)


def load_player_seasons(seasons, refresh_current=True) -> pd.DataFrame:
    frames = []
    for s in seasons:
        p = PROC / f"ps_{s}.parquet"
        if p.exists() and (espn_season.active(s) or not (refresh_current and s >= current_season())):
            frames.append(pd.read_parquet(p))
            continue
        if history.has("ps", s):
            frames.append(history.load_ps(s))
            continue
        try:
            d = torvik.player_seasons(s)
        except Exception as e:
            print("player seasons", s, e)
            continue
        for c in d.columns:
            if d[c].dtype == object:
                d[c] = d[c].astype("string")
        d.to_parquet(p)
        frames.append(d)
    return pd.concat(frames, ignore_index=True)


def player_priors(ps: pd.DataFrame, pg: pd.DataFrame) -> pd.DataFrame:
    """Prior value for each (season, pid) from the previous season + class + recruiting."""
    ps = ps.copy()
    ps["mins"] = pd.to_numeric(ps["mpg"], errors="coerce") * pd.to_numeric(ps["gp"], errors="coerce")
    for c in ("bpm", "obpm", "dbpm", "rec_rank"):
        ps[c] = pd.to_numeric(ps[c], errors="coerce")
    ps["cls_n"] = ps["cls"].map(CLS_NUM)
    prev = ps[["season", "pid", "team", "bpm", "obpm", "dbpm", "mins", "cls_n"]].copy()
    prev["season"] += 1
    prev = prev.rename(columns={"team": "prev_team", "bpm": "p_bpm", "obpm": "p_obpm", "dbpm": "p_dbpm",
                                "mins": "p_mins", "cls_n": "p_cls"})
    prev = prev.sort_values("p_mins").drop_duplicates(["season", "pid"], keep="last")

    # everyone who appears in a season (from game logs; covers players w/o season row yet)
    roster = (pg.groupby(["season", "pid"]).agg(team=("team", "last"), cls=("cls", "last"),
                                                 inches=("inches", "median")).reset_index())
    rec = ps[["season", "pid", "rec_rank"]].drop_duplicates(["season", "pid"])
    r = roster.merge(prev, on=["season", "pid"], how="left").merge(rec, on=["season", "pid"], how="left")

    # class progression (fit: mean change in BPM for returning players, by previous class)
    both = ps[["season", "pid", "bpm", "mins"]].merge(prev, on=["season", "pid"])
    both = both[(both["mins"] > 200) & (both["p_mins"] > 200)]
    prog = (both.assign(d=both["bpm"] - both["p_bpm"]).groupby("p_cls")["d"].mean()).to_dict()
    # newcomer prior from recruiting rank (fit on first-season players)
    first = ps[~ps.set_index(["season", "pid"]).index.isin(prev.set_index(["season", "pid"]).index)]
    first = first[(first["mins"] > 150) & first["rec_rank"].notna()]
    if len(first) > 50:
        b1, b0 = np.polyfit(first["rec_rank"], first["bpm"], 1, w=np.sqrt(first["mins"]))
    else:
        b1, b0 = 0.08, -6.0
    newcomer_base = -4.5  # unranked newcomers (incl. low-major transfers w/o data)

    shrink = r["p_mins"].fillna(0) / (r["p_mins"].fillna(0) + 250.0)
    bump = r["p_cls"].map(prog).fillna(0.0)
    fresh = np.where(r["rec_rank"].notna(), b0 + b1 * r["rec_rank"].fillna(0), newcomer_base)
    base = np.where(r["p_bpm"].notna(), shrink * r["p_bpm"].fillna(0) + (1 - shrink) * (-3.0), fresh)
    r["prior_v"] = base + bump
    # split offense/defense in proportion to last season's split (or 60/40 default)
    o_share = (r["p_obpm"] - r["p_dbpm"]).notna()
    r["prior_o"] = np.where(o_share, r["prior_v"] - (shrink * r["p_dbpm"].fillna(0) + (1 - shrink) * -1.0),
                            0.6 * r["prior_v"])
    r["prior_d"] = r["prior_v"] - r["prior_o"]
    r["returning"] = (r["prev_team"] == r["team"]).fillna(False).astype(bool)
    r["experience"] = pd.to_numeric(r["cls"].map(CLS_NUM), errors="coerce").astype("float64")
    return r[["season", "pid", "team", "prior_v", "prior_o", "prior_d", "returning", "experience",
              "inches", "p_mins", "rec_rank"]]


def team_player_features(games: pd.DataFrame, pg: pd.DataFrame, priors: pd.DataFrame,
                         availability: dict | None = None):
    """Point-in-time player-based features for every team-game, plus a live state.

    Returns (per_game DataFrame keyed by season/gid/team, live dict team->player table).
    ``availability``: {(team, pid): prob_available} used for the live (next game) state.
    """
    availability = availability or {}
    def num(col, fill=np.nan):  # nullable/NA-safe -> plain float64
        return pd.to_numeric(priors[col], errors="coerce").astype("float64").fillna(fill).to_numpy()

    pri = {(a, b): (c, d, e, f, g_, h) for a, b, c, d, e, f, g_, h in zip(
        priors["season"].to_numpy(), pd.to_numeric(priors["pid"], errors="coerce").fillna(-1).astype(int).to_numpy(),
        num("prior_v", -3.0), num("prior_o", -1.8), num("prior_d", -1.2), num("inches"), num("experience"),
        priors["returning"].fillna(False).astype(float).to_numpy())}
    out_rows = []
    live = {}
    g = games[games["type"] != "nond1"]
    long = pd.concat([
        g[["season", "date", "gid", "team_a", "played", "minutes"]].rename(columns={"team_a": "team"}),
        g[["season", "date", "gid", "team_b", "played", "minutes"]].rename(columns={"team_b": "team"}),
    ])
    long["minutes"] = long["minutes"].fillna(200.0)
    pgi = pg[pg["min"] > 0]
    pg_by = {k: v for k, v in pgi.groupby(["season", "team"])}
    cur = current_season()
    for (season, team), tg in long.groupby(["season", "team"]):
        tg = tg.sort_values("date")
        played = tg[tg["played"]]
        pgt = pg_by.get((season, team))
        gids = played["gid"].tolist()
        n = len(gids)
        if pgt is None or n == 0:
            for r in tg.itertuples():
                out_rows.append((season, r.gid, team) + (np.nan,) * 10)
            continue
        pids = sorted(pgt["pid"].dropna().astype(int).unique())
        pidx = {p: i for i, p in enumerate(pids)}
        gidx = {gg: j for j, gg in enumerate(gids)}
        M = np.zeros((n, len(pids)))
        B = np.zeros((n, len(pids)))
        BO = np.zeros((n, len(pids)))
        BD = np.zeros((n, len(pids)))
        sub = pgt[pgt["muid"].isin(gidx)]
        jj = sub["muid"].map(gidx).to_numpy()
        ii = sub["pid"].astype(int).map(pidx).to_numpy()
        mins = sub["min"].to_numpy(float)
        M[jj, ii] = mins
        B[jj, ii] = mins * sub["bpm"].fillna(0).to_numpy()
        BO[jj, ii] = mins * sub["obpm"].fillna(0).to_numpy()
        BD[jj, ii] = mins * sub["dbpm"].fillna(0).to_numpy()
        gm = played["minutes"].to_numpy(float) / 5.0  # minutes per player-slot (40 or 45 ...)
        P = (M > 0).astype(float)

        pr = [pri.get((season, p), _NOPRI) for p in pids]
        pv, po, pdd, inch, expn, ret = (np.array(x, dtype=float) for x in zip(*pr))
        # expected minute shares before the first game: a typical rotation curve assigned by prior value
        # rank (never this season's actual minutes, which would leak who turned out to be good)
        f_season = expected_shares(pv)

        z = np.zeros((1, len(pids)))
        cm = np.vstack([z, np.cumsum(M, 0)])        # exclusive cumsums -> row j = before game j
        cb = np.vstack([z, np.cumsum(B, 0)])
        cbo = np.vstack([z, np.cumsum(BO, 0)])
        cbd = np.vstack([z, np.cumsum(BD, 0)])
        cp = np.vstack([z, np.cumsum(P, 0)])
        cg = np.concatenate([[0], np.cumsum(gm)])
        part = np.zeros((n + 1, len(pids)))
        acc = np.zeros(len(pids))
        wsum = 0.0
        for j in range(1, n + 1):
            acc = acc * PART_DECAY + P[j - 1]
            wsum = wsum * PART_DECAY + 1.0
            part[j] = acc / wsum
        part[0] = (f_season > 0).astype(float)

        # ESPN-fallback newcomers (synthetic ids, no recruiting info): trust box scores sooner
        K = np.where(np.array(pids) >= SYNTH_PID, K_NEW, K_MIN)
        pv = np.where(np.array(pids) >= SYNTH_PID, NEW_PRIOR, pv)
        V = (pv * K + cb) / (K + cm)
        VO = (po * K + cbo) / (K + cm)
        VD = (pdd * K + cbd) / (K + cm)
        F = np.where(cg[:, None] > 0, cm / np.maximum(cg[:, None], 1e-9), f_season[None, :])
        WF = F / np.maximum(F.sum(1, keepdims=True), 1e-9)
        SP = np.where(cp > 0, cm / np.maximum(cp, 1) / 40.0, 0.0)
        R = (F * V).sum(1)
        Ro = (F * VO).sum(1)
        Rd = (F * VD).sum(1)
        hm = ~np.isnan(inch)
        height = (WF[:, hm] * inch[hm]).sum(1) / np.maximum(WF[:, hm].sum(1), 1e-9)
        em = ~np.isnan(expn)
        exper = (WF[:, em] * expn[em]).sum(1) / np.maximum(WF[:, em].sum(1), 1e-9)
        reg = SP >= 0.25
        star = np.where(reg.any(1), np.where(reg, V, -99).max(1), V.max(1))
        Fs = -np.sort(-F, axis=1)
        bench = Fs[:, 5:].sum(1) / np.maximum(Fs.sum(1), 1e-9)
        cont = (WF * ret).sum(1)
        A = np.vstack([P, np.zeros((1, len(pids)))])
        A[n] = [availability.get((team, int(p)), _default_avail(P, i)) for i, p in enumerate(pids)]
        delta = ((A - part) * SP * (V - V_REPL)).sum(1)
        delta[0] = 0.0
        # pre-game knowable availability (recent missed games only) for honest backtests
        Apre = _heuristic_avail_matrix(P)
        delta_pre = ((Apre - part) * SP * (V - V_REPL)).sum(1)
        delta_pre[0] = 0.0
        delta_pre[n] = delta[n]
        feats_by_j = list(zip(R, Ro, Rd, delta, height, exper, star, bench, cont, delta_pre))
        if season >= cur:
            live[team] = pd.DataFrame({
                "pid": pids, "value": V[n], "o_value": VO[n], "d_value": VD[n], "share": F[n],
                "share_play": SP[n], "participation": part[n], "avail": A[n], "games": cp[n],
                "minutes": cm[n],
                "last_played_idx": [int(np.nonzero(P[:, i])[0].max()) if P[:, i].any() else -1
                                    for i in range(len(pids))],
                "team_games": n,
            })
        played_set = set(gids)
        for r in tg.itertuples():
            if r.gid in played_set:
                fj = feats_by_j[gidx[r.gid]]
            else:
                fj = feats_by_j[n]  # scheduled game: latest state
            out_rows.append((season, r.gid, team) + fj)
        if season >= cur:
            out_rows.append((season, "__live__", team) + feats_by_j[n])
    cols = ["season", "gid", "team", "roster", "roster_o", "roster_d", "avail_delta", "height", "experience",
            "star", "bench", "continuity", "avail_delta_pre"]
    return pd.DataFrame(out_rows, columns=cols), live


# average sorted minute share of a D-I rotation (shares sum to 5); fit on 2009-2026 team-seasons
MINUTES_CURVE = np.array([0.811, 0.746, 0.683, 0.615, 0.542, 0.456, 0.372, 0.287, 0.209, 0.136, 0.080,
                          0.041, 0.022, 0.013, 0.009, 0.006])


def expected_shares(prior_v: np.ndarray) -> np.ndarray:
    """Preseason expected minute shares: best prior value gets the biggest typical share, and so on."""
    n = len(prior_v)
    if n == 0:
        return np.zeros(0)
    order = np.argsort(-np.nan_to_num(prior_v, nan=-10.0), kind="stable")
    curve = np.r_[MINUTES_CURVE, np.full(max(0, n - len(MINUTES_CURVE)), 0.002)][:n]
    out = np.zeros(n)
    out[order] = curve
    return out * 5.0 / max(out.sum(), 1e-9)


def minutes_curve(pg: pd.DataFrame) -> np.ndarray:
    """(Re)estimate MINUTES_CURVE from player-game logs."""
    m = pg[pg["min"] > 0].groupby(["season", "team", "pid"])["min"].sum().reset_index()
    m["f"] = 5 * m["min"] / m.groupby(["season", "team"])["min"].transform("sum")
    m["r"] = m.groupby(["season", "team"])["f"].rank(ascending=False, method="first").astype(int)
    return m[m["r"] <= 16].groupby("r")["f"].mean().to_numpy()


# ------------------------------------------------------------------ de-leaking Torvik game values
def deleak_bpm(pg: pd.DataFrame, games: pd.DataFrame, kpre: pd.DataFrame, conf: pd.DataFrame) -> pd.DataFrame:
    """Re-base Torvik's per-game player plus/minus on *pre-game* team strength.

    Torvik's game values are nearly a deterministic blend (R^2 = 0.998 at team level) of the game's raw box-score
    margin, the opponent's end-of-season T-Rank rating and the team's OWN end-of-season T-Rank rating (about half
    of each game value). Summed over a roster that leaks the team's final strength into every early-season game,
    which is why a one-term roster rating used to beat closing betting lines. We estimate the blend per season
    (offense and defense separately) and swap both end-of-season ratings for the pre-game Kalman ratings.
    The same transform is applied to training and live data, so features mean the same thing in both.
    """
    pg = pg.copy()
    tv = conf.dropna(subset=["t_adjoe"]).drop_duplicates(["season", "team"])
    mo = tv.groupby("season")["t_adjoe"].transform("mean")
    md = tv.groupby("season")["t_adjde"].transform("mean")
    fin_o = dict(zip(zip(tv["season"], tv["team"]), tv["t_adjoe"] - mo))   # offense (+ = good)
    fin_d = dict(zip(zip(tv["season"], tv["team"]), md - tv["t_adjde"]))   # defense (+ = good)
    g = games[games["played"] & games["poss"].notna()][["gid", "season", "team_a", "team_b", "pts_a", "pts_b", "poss"]]
    g = g.merge(kpre[["season", "gid", "a_k_o", "a_k_d", "b_k_o", "b_k_d", "k_mu"]], on=["season", "gid"], how="left")
    side = []
    for me, op in (("a", "b"), ("b", "a")):
        side.append(pd.DataFrame({
            "gid": g["gid"], "season": g["season"], "team": g[f"team_{me}"], "opp": g[f"team_{op}"],
            "raw_o": 100 * g[f"pts_{me}"] / g["poss"] - g["k_mu"], "raw_d": g["k_mu"] - 100 * g[f"pts_{op}"] / g["poss"],
            "opp_pre_o": g[f"{op}_k_o"], "opp_pre_d": -g[f"{op}_k_d"],
            "own_pre_o": g[f"{me}_k_o"], "own_pre_d": -g[f"{me}_k_d"]}))
    side = pd.concat(side, ignore_index=True)
    ko = list(zip(side["season"], side["opp"]))
    km = list(zip(side["season"], side["team"]))
    side["opp_fin_o"] = [fin_o.get(k, np.nan) for k in ko]
    side["opp_fin_d"] = [fin_d.get(k, np.nan) for k in ko]
    side["own_fin_o"] = [fin_o.get(k, np.nan) for k in km]
    side["own_fin_d"] = [fin_d.get(k, np.nan) for k in km]
    q = pg[pg["min"] > 0]
    agg = (q.assign(wo=q["min"] * q["obpm"], wd=q["min"] * q["dbpm"])
           .groupby(["season", "muid", "team"]).agg(wo=("wo", "sum"), wd=("wd", "sum"), m=("min", "sum")).reset_index())
    agg["So"], agg["Sd"] = 5 * agg["wo"] / agg["m"], 5 * agg["wd"] / agg["m"]
    side = side.merge(agg[["season", "muid", "team", "So", "Sd"]], left_on=["season", "gid", "team"],
                      right_on=["season", "muid", "team"], how="left")
    coef = {}
    for s, d in side.groupby("season"):
        c = {}
        for y, raw, opp, own in (("So", "raw_o", "opp_fin_d", "own_fin_o"), ("Sd", "raw_d", "opp_fin_o", "own_fin_d")):
            ok = d[[y, raw, opp, own]].notna().all(axis=1)
            if ok.sum() < 500:
                c[y] = (0.0, 0.0)
                continue
            X = np.c_[np.ones(ok.sum()), d.loc[ok, raw], d.loc[ok, opp], d.loc[ok, own]]
            b = np.linalg.lstsq(X, d.loc[ok, y].to_numpy(), rcond=None)[0]
            c[y] = (float(b[2]), float(b[3]))
        coef[int(s)] = c
    co = side["season"].map(lambda s: coef.get(int(s), {}).get("So", (0.0, 0.0)))
    cd = side["season"].map(lambda s: coef.get(int(s), {}).get("Sd", (0.0, 0.0)))

    def gap(pre, fin):  # final -> pre-game; no change where either is unknown
        return (side[pre] - side[fin]).fillna(0.0)
    side["shift_o"] = (co.str[0] * gap("opp_pre_d", "opp_fin_d") + co.str[1] * gap("own_pre_o", "own_fin_o")) / 5
    side["shift_d"] = (cd.str[0] * gap("opp_pre_o", "opp_fin_o") + cd.str[1] * gap("own_pre_d", "own_fin_d")) / 5
    sh = side.drop_duplicates(["season", "gid", "team"]).set_index(["season", "gid", "team"])[["shift_o", "shift_d"]]
    k = pd.MultiIndex.from_arrays([pg["season"].astype(int), pg["muid"].astype(str), pg["team"].astype(str)])
    so = sh["shift_o"].reindex(k).fillna(0.0).to_numpy(copy=True)
    sd = sh["shift_d"].reindex(k).fillna(0.0).to_numpy(copy=True)
    # rows built from ESPN box scores (current-season fallback) never contained Torvik's season terms
    espn_rows = (pd.to_numeric(pg["pid"], errors="coerce").fillna(0).to_numpy() >= SYNTH_PID)
    if "ortg" in pg:
        espn_rows |= (pg["ortg"].isna() & (pg["season"] >= current_season())).to_numpy()
    so[espn_rows] = 0.0
    sd[espn_rows] = 0.0
    pg["obpm"] = pg["obpm"] + so
    pg["dbpm"] = pg["dbpm"] + sd
    pg["bpm"] = pg["obpm"] + pg["dbpm"]
    pg.attrs["deleak_coef"] = coef
    return pg


def _heuristic_avail_matrix(P: np.ndarray) -> np.ndarray:
    """Row j = expected availability for game j using games < j only (rows 0..n)."""
    n, k = P.shape
    A = np.ones((n + 1, k))
    ever = np.vstack([np.zeros((1, k)), np.cumsum(P, 0)]) > 0
    for j in range(n + 1):
        l1 = P[j - 1] if j >= 1 else np.ones(k)
        l2 = P[j - 2] if j >= 2 else np.ones(k)
        l3 = P[j - 3] if j >= 3 else np.ones(k)
        a = np.where(l1 > 0, 1.0, np.where(l2 > 0, 0.65, np.where(l3 > 0, 0.3, 0.05)))
        A[j] = np.where(ever[j], a, 0.0)
    return A


_NOPRI = (-3.0, -1.8, -1.2, np.nan, np.nan, 0.0)


def _default_avail(P: np.ndarray, i: int) -> float:
    """Heuristic availability from recent participation when no injury info exists."""
    if P.shape[0] == 0:
        return 1.0
    played_ever = P[:, i].any()
    if not played_ever:
        return 0.0
    last = P[-3:, i]
    if len(last) >= 3 and last.sum() == 0:
        return 0.05  # missed 3 straight: almost surely out
    if len(last) >= 2 and last[-2:].sum() == 0:
        return 0.3
    if last[-1] == 0:
        return 0.65
    return 1.0

"""Build the leak-free, per-game modelling table and the live team state."""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import memo
from . import players as PL
from .config import PROC, current_season, today
from .ratings import efficiency as EF
from .ratings import elo as ELO
from .ratings import kalman as KAL
from .sources import torvik

STYLE = [s for s in EF.STATS if s not in ("eff", "pace")]
TEAM_FEATS = (
    ["eff_o", "eff_d", "adjem", "adjt", "games", "elo"]
    + [f"{s}_{side}" for s in STYLE for side in ("o", "d")]
    + ["roster", "roster_o", "roster_d", "avail_delta", "height", "experience", "star", "bench",
       "continuity", "form", "rest", "dist"]
    + ["k_o", "k_d", "k_t", "k_sd"]
)


def conferences(seasons) -> pd.DataFrame:
    from . import espn_fallback as espn_season
    from . import history
    from .config import ESPN_DATA
    if ESPN_DATA:  # ESPN standings (built in espn_data); no T-Rank ratings, so no de-leak is needed or applied
        f = PROC / "conf.parquet"
        if f.exists():
            c = pd.read_parquet(f)
            return c[c["season"].isin(list(seasons))].reset_index(drop=True)
        from .espn_data import conferences as wconf
        return wconf(seasons)

    frames = []
    hist = pd.read_parquet(history.path("conf")) if history.path("conf").exists() else None
    for s in seasons:
        if hist is not None and s < current_season() and (hist["season"] == s).any():
            frames.append(hist[hist["season"] == s])
            continue
        if espn_season.active(s) and (PROC / f"conf_{s}.parquet").exists():
            frames.append(pd.read_parquet(PROC / f"conf_{s}.parquet"))
            continue
        try:
            t = torvik.team_results(s)
        except Exception as e:
            print("team_results", s, e)
            continue
        frames.append(t)
    out = pd.concat(frames, ignore_index=True)
    # current season: the T-Rank ratings that went into the frozen Torvik player-game snapshot (used to
    # de-leak those game values), identical whether or not Torvik is reachable today
    cur = current_season()
    if cur in seasons and history.path("snap_conf", cur).exists():
        sc = pd.read_parquet(history.path("snap_conf", cur)).drop_duplicates("team").set_index("team")
        m = out["season"] == cur
        for c in ("t_adjoe", "t_adjde", "t_barthag"):
            out.loc[m, c] = out.loc[m, "team"].map(sc[c]).to_numpy()
    return out


def _final_ratings(rows_by_season, teams_by_season, priors_by_season=None):
    out = {}
    for s, rows in rows_by_season.items():
        pr = priors_by_season.get(s) if priors_by_season else None
        sv = EF.SeasonSolver(rows, teams_by_season[s], pr, prior_games=0.0 if pr is None else 2.0)
        f = sv.solve(np.datetime64(rows["date"].max() + pd.Timedelta(days=1)))
        out[s] = f
    return out


class _StoredSnaps:
    """The last run's snapshots.parquet: a completed season's rating snapshots are reused when they were computed
    from the same inputs (the input hash of each season is stored in the file's metadata), which gives exactly what
    recomputing would. Read only when a season can be reused."""

    def __init__(self, path=None):
        self.path = path or PROC / "snapshots.parquet"
        self.keys, self.df = {}, None
        try:
            import json

            import pyarrow.parquet as pq
            meta = pq.read_schema(self.path).metadata or {}
            attrs = json.loads(meta.get(b"PANDAS_ATTRS", b"{}"))
            self.keys = attrs.get("snap_keys") or {}
        except Exception:  # no previous file (fresh cache) or an older one without keys
            self.keys = {}

    def has(self, season, key) -> bool:
        return self.keys.get(str(season)) == key

    def season(self, season) -> pd.DataFrame:
        if self.df is None:
            self.df = pd.read_parquet(self.path)
        return self.df[self.df["season"] == season].drop(columns="season").reset_index(drop=True)


def fit_priors(final: dict, roster0: pd.DataFrame):
    """Fit how last season's ratings + current roster predict this season's final ratings."""
    recs = []
    for s, f in final.items():
        if s - 1 not in final:
            continue
        prev = final[s - 1].set_index("team")
        cur = f.set_index("team")
        prev = prev[~prev.index.duplicated()]
        row = {"season": s, "team": cur.index.to_numpy()}
        for st in EF.STATS:
            for side in ("o", "d"):
                c = f"{st}_{side}"
                row[c] = (cur[c] - cur[c].mean()).to_numpy()
                row[f"prev_{c}"] = (prev[c] - prev[c].mean()).reindex(cur.index).to_numpy()
        recs.append(pd.DataFrame(row))
    d = pd.concat(recs, ignore_index=True).merge(roster0, on=["season", "team"], how="left")
    coefs = {}
    for st in EF.STATS:
        for side in ("o", "d"):
            c = f"{st}_{side}"
            cols = [f"prev_{c}"] + (["roster_o", "roster_d"] if st == "eff" else [])
            sub = d.dropna(subset=[c] + cols)
            if st == "eff" and _enough(sub):
                X = np.column_stack([sub[cols].to_numpy(), np.ones(len(sub))])
                beta = np.linalg.lstsq(X, sub[c].to_numpy(), rcond=None)[0]
                # fallback (no previous-season row, e.g. new D-I team): roster only
                sub2 = d.dropna(subset=[c, "roster_o", "roster_d"])
                X2 = np.column_stack([sub2[["roster_o", "roster_d"]].to_numpy(), np.ones(len(sub2))])
                beta2 = np.linalg.lstsq(X2, sub2[c].to_numpy(), rcond=None)[0]
                coefs[c] = {"cols": cols, "beta": beta.tolist(), "beta_noprev": beta2.tolist()}
            else:
                x = sub[f"prev_{c}"].to_numpy()
                slope = float((x * sub[c]).sum() / max((x * x).sum(), 1e-9))
                coefs[c] = {"cols": [f"prev_{c}"], "beta": [slope, 0.0], "beta_noprev": [0.0]}
    return coefs


def _enough(sub):
    # 4 coefficients; college seasons have ~350 teams, but a pro league's first fits (12 to 30 teams a season) need
    # a lower bar to use the roster terms at all
    return len(sub) > 60


def season_ahead_coefs(final: dict, roster0: pd.DataFrame, seasons) -> dict:
    """Prior coefficients for each season, fit only on seasons before it (and never on the current season, which
    has no final ratings yet), so a backtest season's prior never learns from that season or later ones. The first
    seasons, before any pair of completed seasons exists, borrow the earliest honest fit (they are training data
    only, never a backtest season)."""
    cur = current_season()
    out = {}
    for s in seasons:
        past = {t: f for t, f in final.items() if t < min(s, cur)}
        out[s] = fit_priors(past, roster0) if any(t - 1 in past for t in past) else None
    earliest = next((c for c in out.values() if c is not None), {})
    return {s: (c if c is not None else earliest) for s, c in out.items()}


def make_prior(season, final, roster0, coefs, teams):
    prev = final.get(season - 1)
    r0 = roster0[roster0["season"] == season].drop_duplicates("team").set_index("team")
    ro = r0["roster_o"].reindex(teams).to_numpy(float)
    rd = r0["roster_d"].reindex(teams).to_numpy(float)
    out = pd.DataFrame({"team": teams})
    for c, cf in coefs.items():
        if prev is not None:
            pser = prev.set_index("team")[c]
            pv = (pser - pser.mean()).reindex(teams).to_numpy(float)
        else:
            pv = np.full(len(teams), np.nan)
        if c.startswith("eff_") and len(cf["beta"]) < 4:  # too little data for the roster terms: last season only
            v = np.where(~np.isnan(pv), cf["beta"][0] * pv, 6.0 if c == "eff_d" else -6.0)
        elif c.startswith("eff_"):
            b, b2 = cf["beta"], cf["beta_noprev"]
            full = b[0] * pv + b[1] * ro + b[2] * rd + b[3]
            noprev = b2[0] * ro + b2[1] * rd + b2[2]
            weak = 6.0 if c == "eff_d" else -6.0  # unknown team: assume below average
            v = np.where(~np.isnan(full), full,
                         np.where(~np.isnan(noprev), noprev, np.where(~np.isnan(pv), 0.6 * pv, weak)))
        else:
            v = np.where(~np.isnan(pv), cf["beta"][0] * pv, 0.0)
        out[c] = v
    return out


PRIOR_GAMES = 5.0  # weight of the preseason prior in the efficiency ratings, in games


def build(games: pd.DataFrame, pg: pd.DataFrame, ps: pd.DataFrame, conf: pd.DataFrame,
          availability: dict | None = None, prior_games: float | None = None):
    """Returns (features per game, snapshots, live dict, extras)."""
    prior_games = PRIOR_GAMES if prior_games is None else prior_games
    g = games[(games["type"] != "nond1")].copy()
    g = g.sort_values(["date", "gid"]).reset_index(drop=True)
    played = g[g["played"] & g["poss"].notna() & (g["poss"] > 0)]
    seasons = sorted(g["season"].unique())

    # --- dynamic (Kalman) ratings: games only, so they can anchor everything else point-in-time
    cont = KAL.continuity(pg)
    kpre, keng = KAL.pregame(g, cont)
    print(f"  kalman ratings: {len(kpre):,} games", flush=True)

    # --- players (Torvik game values re-based on pre-game opponent strength)
    pg = PL.deleak_bpm(pg, g, kpre, conf)
    pri = PL.player_priors(ps, pg)
    from .sources import injuries as INJ
    pf, live_players = PL.team_player_features(g, pg, pri, availability, reports=INJ.load_archive(pg))
    # roster strength at season start (for preseason priors)
    first_gid = (pd.concat([g[["season", "date", "gid", "team_a"]].rename(columns={"team_a": "team"}),
                            g[["season", "date", "gid", "team_b"]].rename(columns={"team_b": "team"})])
                 .sort_values("date").drop_duplicates(["season", "team"]))
    roster0 = first_gid.merge(pf, on=["season", "gid", "team"])[["season", "team", "roster_o", "roster_d"]]

    # --- efficiency: final (no prior) ratings per season -> fit priors -> point-in-time snapshots
    rows_by = {s: EF.team_game_rows(played[played["season"] == s]) for s in seasons}
    rows_by = {s: r for s, r in rows_by.items() if len(r)}
    teams_by = {s: sorted(set(g.loc[g["season"] == s, "team_a"]) | set(g.loc[g["season"] == s, "team_b"]))
                for s in seasons}
    final0 = _final_ratings(rows_by, teams_by)
    coefs_by = season_ahead_coefs(final0, roster0, seasons)
    coefs = coefs_by.get(current_season(), coefs_by[seasons[-1]])
    snaps = []
    snap_keys = {}
    stored = _StoredSnaps()
    priors_used = {}
    for s in seasons:
        teams = teams_by[s]
        prior = make_prior(s, final0, roster0, coefs_by[s], teams)
        priors_used[s] = prior
        dates = sorted(g.loc[g["season"] == s, "date"].unique())
        if s == current_season():  # no snapshots for future dates (nothing new to learn)
            dates = [d for d in dates if pd.Timestamp(d) <= pd.Timestamp(today())]
        extra = [pd.Timestamp(today()) + pd.Timedelta(days=1)] if s == current_season() else \
            [pd.Timestamp(max(dates)) + pd.Timedelta(days=1)]
        rows = rows_by.get(s)
        if rows is None or rows.empty:
            continue
        snap_dates = list(dates) + extra
        k = memo.key(rows, teams, prior, snap_dates, prior_games, (EF.HALF_LIFE_DAYS, EF.WEIGHT_FLOOR, EF.STATS))
        reuse = s < current_season() and stored.has(s, k)  # completed season, same inputs as the last run
        sn = stored.season(s) if reuse else EF.season_snapshots(rows, teams, prior, snap_dates, prior_games=prior_games)
        sn["season"] = s
        snaps.append(sn)
        snap_keys[str(s)] = k
        print(f"  ratings {s}: {len(dates)} snapshots" + (" (unchanged, from the last run)" if reuse else ""), flush=True)
    snaps = pd.concat(snaps, ignore_index=True)
    snaps.attrs["snap_keys"] = snap_keys  # saved with snapshots.parquet, so the next run can reuse these seasons

    # --- second Kalman pass: start each season from the roster-based prior instead of the league average. The
    # prior needs de-leaked player values, which need the first pass, so the first pass stays for that step only.
    pri_tab = pd.concat([pr.assign(season=s) for s, pr in priors_used.items()], ignore_index=True)
    pri_tab[["season", "team", "eff_o", "eff_d"]].to_parquet(PROC / "priors.parquet")  # also read by ratings/tune.py
    if KAL.load_params().prior_w > 0:
        prior_map = {(s, t): (o, d) for s, t, o, d in pri_tab[["season", "team", "eff_o", "eff_d"]].itertuples(index=False)}
        kpre, keng = KAL.pregame(g, cont, prior=prior_map)
        print("  kalman ratings: second pass from the roster prior", flush=True)

    # --- elo
    elo_g, elo_final = ELO.run(g, **ELO.load_params())

    # --- long (team-perspective) rows to attach team features
    def side(prefix, opp):
        d = g[["season", "date", "gid", f"team_{prefix}", f"team_{opp}", "loc", f"dist_{prefix}"]].copy()
        d.columns = ["season", "date", "gid", "team", "opp", "loc", "dist"]
        if prefix == "b":
            d["loc"] = -d["loc"]
        d["side"] = prefix
        return d

    long = pd.concat([side("a", "b"), side("b", "a")], ignore_index=True)
    long = long.merge(snaps.rename(columns={"snap": "date"}), on=["season", "date", "team"], how="left")
    long = long.merge(pf, on=["season", "gid", "team"], how="left")
    e = pd.concat([elo_g[["gid", "season", "elo_a"]].rename(columns={"elo_a": "elo"}).assign(side="a"),
                   elo_g[["gid", "season", "elo_b"]].rename(columns={"elo_b": "elo"}).assign(side="b")])
    long = long.merge(e, on=["season", "gid", "side"], how="left")
    kl = pd.concat([kpre[["season", "gid"] + [f"{x}_k_{c}" for c in ("o", "d", "t", "sd")]]
                    .rename(columns=lambda c: c[2:] if c[:2] in ("a_", "b_") else c).assign(side=x)
                    for x in ("a", "b")], ignore_index=True)
    long = long.merge(kl, on=["season", "gid", "side"], how="left")
    # scheduled games: latest Kalman state
    todo = long["k_o"].isna()
    if todo.any():
        ks = KAL.state_for_unplayed(keng, sorted(long.loc[todo, "team"].unique()), today())
        for c in ("k_o", "k_d", "k_t", "k_sd"):
            long.loc[todo, c] = long.loc[todo, "team"].map(ks[c]).to_numpy()

    # rest days
    long = long.sort_values(["team", "date"])
    long["rest"] = long.groupby(["season", "team"])["date"].diff().dt.days.clip(upper=10).fillna(10)

    # form: mean residual (actual - expected efficiency margin) over the previous 5 games
    res = g[["season", "gid", "pts_a", "pts_b", "poss", "played"]]
    long = long.merge(res, on=["season", "gid"], how="left")
    sign = np.where(long["side"] == "a", 1, -1)
    long["margin100"] = sign * 100 * (long["pts_a"] - long["pts_b"]) / long["poss"]
    opp_em = long[["season", "date", "team", "adjem"]].rename(columns={"team": "opp", "adjem": "opp_adjem"})
    long = long.merge(opp_em.drop_duplicates(["season", "date", "opp"]), on=["season", "date", "opp"], how="left")
    long["resid"] = long["margin100"] - (long["adjem"] - long["opp_adjem"] + 2 * long["hca"] * long["loc"])
    long = long.sort_values(["team", "date"])
    long["form"] = (long.groupby(["season", "team"])["resid"]
                    .transform(lambda x: x.shift(1).rolling(5, min_periods=1).mean())).fillna(0.0)
    long["dist"] = np.log1p(long["dist"].fillna(long["dist"].median()))
    # live form = rolling mean of the last 5 *played* residuals (incl. the most recent game)
    lp = long[long["resid"].notna()].sort_values(["team", "date"])
    team_form = lp.groupby(["season", "team"])["resid"].apply(lambda x: x.tail(5).mean())
    last_date = lp.groupby(["season", "team"])["date"].max()

    keep = ["season", "gid", "side"] + TEAM_FEATS + ["team", "hca", "mu", "avail_delta_pre"]
    A = long[long["side"] == "a"][keep].drop(columns="side").add_prefix("a_")
    B = long[long["side"] == "b"][keep].drop(columns="side").add_prefix("b_")
    A = A.rename(columns={"a_season": "season", "a_gid": "gid"})
    B = B.rename(columns={"b_season": "season", "b_gid": "gid"})
    feat = g[["season", "date", "gid", "team_a", "team_b", "loc", "type", "played", "pts_a", "pts_b", "poss",
              "is_ncaa", "ncaa_round", "seed_a", "seed_b", "torvik_p_a", "neutral", "a_id", "b_id",
              "city", "state", "v_lat", "v_lon", "region"]]
    feat = feat.merge(A.drop(columns=["a_team"]), on=["season", "gid"]).merge(B.drop(columns=["b_team"]), on=["season", "gid"])
    cmap = conf.drop_duplicates(["season", "team"]).set_index(["season", "team"])["conf"]
    feat["conf_a"] = [cmap.get((s, t)) for s, t in zip(feat["season"], feat["team_a"])]
    feat["conf_b"] = [cmap.get((s, t)) for s, t in zip(feat["season"], feat["team_b"])]
    feat["same_conf"] = (feat["conf_a"] == feat["conf_b"]).astype(float)
    feat["is_conftourney"] = (feat["type"] == "conftourney").astype(float)
    feat["is_post"] = ((feat["type"] == "post") | feat["is_ncaa"]).astype(float)
    from .config import season_start
    feat["day"] = (feat["date"] - pd.to_datetime(feat["season"].map(season_start))).dt.days
    feat["margin"] = feat["pts_a"] - feat["pts_b"]
    feat = feat.merge(kpre[["season", "gid", "k_mu", "k_mut"]], on=["season", "gid"], how="left")
    feat["k_mu"] = feat["k_mu"].fillna(float(keng.state["mu"]))
    feat["k_mut"] = feat["k_mut"].fillna(float(keng.state["mu_t"]))
    extras = {"coefs": coefs, "elo_final": elo_final, "team_form": team_form, "last_date": last_date, "priors": priors_used, "player_priors": pri,
              "player_feats": pf, "kalman": keng, "continuity": cont, "deleak": pg.attrs.get("deleak_coef")}
    return feat, snaps, live_players, extras

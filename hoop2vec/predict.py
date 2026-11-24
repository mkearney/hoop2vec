"""Live team state, arbitrary matchup predictions, power ratings, upcoming games."""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import geo
from .config import current_season, today
from .features import TEAM_FEATS
from .model import linear as LIN
from .model import train as TR
from .ratings import kalman as KAL

STATE_FEATS = [c for c in TEAM_FEATS if c not in ("rest", "dist")]


def team_state(snaps: pd.DataFrame, extras: dict, conf: pd.DataFrame, meta: pd.DataFrame,
               season: int | None = None) -> pd.DataFrame:
    season = season or current_season()
    s = snaps[snaps["season"] == season]
    last = s[s["snap"] == s["snap"].max()].set_index("team")
    pf = extras["player_feats"]
    live = pf[(pf["season"] == season) & (pf["gid"] == "__live__")].set_index("team")
    st = last.join(live.drop(columns=["season", "gid"]), how="left")
    st["elo"] = pd.Series(extras["elo_final"]).reindex(st.index)
    eng = extras.get("kalman")
    if eng is not None:  # dynamic ratings as of today (variance grown since each team's last game)
        ks = KAL.state_for_unplayed(eng, st.index, today())
        for c in ("k_o", "k_d", "k_t", "k_sd"):
            st[c] = ks[c].reindex(st.index)
        st["k_mu"], st["k_mut"] = float(eng.state["mu"]), float(eng.state["mu_t"])
    form = extras["team_form"]
    st["form"] = form.loc[season].reindex(st.index).fillna(0.0) if season in form.index.get_level_values(0) else 0.0
    st = st[st["games"] > 0].copy()  # D-I teams that have played this season
    # impute missing player-layer features (rare data gaps) from efficiency so the net stays in-distribution
    miss = st["roster"].isna()
    if miss.any():
        st.loc[miss, "roster"] = st.loc[miss, "adjem"]
        st.loc[miss, "roster_o"] = st.loc[miss, "eff_o"] - st["mu"].mean()
        st.loc[miss, "roster_d"] = st["mu"].mean() - st.loc[miss, "eff_d"]
        st.loc[miss, "avail_delta"] = 0.0
        for c in ("height", "experience", "star", "bench", "continuity"):
            st.loc[miss, c] = st[c].median() if st[c].notna().any() else np.nan
    c = conf[conf["season"] == season].drop_duplicates("team").set_index("team")["conf"]
    st["conf"] = c.reindex(st.index)
    m = meta.drop_duplicates("team").set_index("team")
    for col in ("espn_id", "location", "name", "abbr", "color", "alt_color", "logo", "lat", "lon", "city", "state"):
        if col in m.columns:
            st[col] = m[col].reindex(st.index)
    st["season"] = season
    return st


def pair_frame(state: pd.DataFrame, pairs: pd.DataFrame) -> pd.DataFrame:
    """pairs: team_a, team_b, loc (+ optional v_lat, v_lon, is_ncaa, is_conftourney, is_post, day, rest_a, rest_b)."""
    p = pairs.copy().reset_index(drop=True)
    season = int(state["season"].iloc[0])
    p["season"] = season
    for side, col in (("a", "team_a"), ("b", "team_b")):
        sub = state.reindex(p[col])
        for f in STATE_FEATS:
            p[f"{side}_{f}"] = sub[f].to_numpy()
        p[f"{side}_rest"] = p.get(f"rest_{side}", pd.Series(4.0, index=p.index)).fillna(4.0).to_numpy()
        if "v_lat" in p:
            d = geo.haversine_km(sub["lat"].to_numpy(), sub["lon"].to_numpy(), p["v_lat"].to_numpy(),
                                 p["v_lon"].to_numpy())
            d = np.where(np.isnan(d), 800.0, d)
        else:
            d = np.full(len(p), 800.0)
        p[f"{side}_dist"] = np.log1p(d)
        p[f"conf_{side}"] = sub["conf"].to_numpy()
    for c, dflt in (("is_ncaa", False), ("is_conftourney", 0.0), ("is_post", 0.0)):
        if c not in p:
            p[c] = dflt
    if "day" not in p:
        p["day"] = (pd.Timestamp(today()) - pd.Timestamp(f"{season - 1}-11-01")).days
    p["same_conf"] = (p["conf_a"] == p["conf_b"]).astype(float)
    p["played"] = False
    if "k_mut" in state:
        p["k_mut"] = float(state["k_mut"].iloc[0])
        p["poss_exp"] = (p["k_mut"] + p["a_k_t"] + p["b_k_t"]).clip(55, 85)
        p["k_margin"] = ((p["a_k_o"] - p["a_k_d"]) - (p["b_k_o"] - p["b_k_d"])) * p["poss_exp"] / 100.0
    return p


def blend_predict(bundle: dict, df: pd.DataFrame):
    """Final game model: margin blend of the matchup network and the linear game model (weight from backtest).

    Returns (mu, sigma, p, parts) where parts holds the network and linear margins separately."""
    parts = {}
    w = bundle.get("w_net", 1.0)
    mus, sgs = [], []
    if bundle.get("nets") is not None and w > 0:
        models, norm, smeans = bundle["nets"]
        mu, sg, _ = TR.predict_df(models, norm, smeans, df)
        parts["net"] = mu
        mus.append((w, mu)); sgs.append((w, sg))
    if bundle.get("linear") is not None and w < 1:
        mu, sg, _ = bundle["linear"].predict(LIN.frame(df))
        parts["linear"] = mu
        mus.append((1 - w, mu)); sgs.append((1 - w, sg))
    mu = sum(a * m for a, m in mus)
    var = sum(a * s ** 2 for a, s in sgs)
    if len(mus) == 2:
        var = var + w * (1 - w) * (mus[0][1] - mus[1][1]) ** 2
    from scipy.stats import norm as N
    sg = np.sqrt(var)
    return mu, sg, N.cdf(mu / sg), parts


def predict_pairs(bundle, state, pairs):
    df = pair_frame(state, pairs).copy()
    mu, sg, pr, parts = blend_predict(bundle, df)
    df["margin"] = mu
    df["sigma"] = sg
    df["p_a"] = pr
    for k, v in parts.items():
        df[f"margin_{k}"] = v
    tempo = (df["a_adjt"] + df["b_adjt"]) / 2
    mu_eff = (df["a_eff_o"] + df["b_eff_d"]) / 2
    total = 2 * mu_eff.fillna(105) * tempo.fillna(68) / 100
    df["pts_a"] = total / 2 + mu / 2
    df["pts_b"] = total / 2 - mu / 2
    return df


REF_GROUP = 100  # power = margin per 100 possessions vs the top-N teams (tournament-calibre opposition)


def power_ratings(bundle, state: pd.DataFrame) -> pd.DataFrame:
    """Round-robin through the final (blended) game model on a neutral floor.

    power_net = average predicted margin **per 100 possessions** against the top ``REF_GROUP`` teams;
    power = power_net re-centered on the D-I average (0 = average team), so SOS and Power read naturally.
    Per-possession keeps it tempo-neutral (fast teams' point margins are not inflated); the
    strong reference group measures what matters in March instead of how badly a team
    beats the weakest D-I teams.
    """
    teams = state.index.tolist()
    n = len(teams)
    ia, ib = np.triu_indices(n, 1)
    pairs = pd.DataFrame({"team_a": np.array(teams)[ia], "team_b": np.array(teams)[ib], "loc": 0})
    df = predict_pairs(bundle, state, pairs)
    tempo = state["adjt"].fillna(state["adjt"].mean()).to_numpy()
    poss = (tempo[ia] + tempo[ib]) / 2
    M = np.zeros((n, n))
    M100 = np.zeros((n, n))
    P = np.full((n, n), 0.5)
    M[ia, ib] = df["margin"].to_numpy()
    M[ib, ia] = -M[ia, ib]
    M100[ia, ib] = 100 * df["margin"].to_numpy() / poss
    M100[ib, ia] = -M100[ia, ib]
    P[ia, ib] = df["p_a"].to_numpy()
    P[ib, ia] = 1 - P[ia, ib]
    vs_all = M100.sum(1) / (n - 1)
    ref = np.argsort(-vs_all)[: min(REF_GROUP, n - 1)]
    mask = np.zeros((n, n), bool)
    mask[:, ref] = True
    np.fill_diagonal(mask, False)
    out = pd.DataFrame(index=teams)
    out["power_net"] = (M100 * mask).sum(1) / mask.sum(1)
    out["power_all"] = vs_all
    # Power = the final game model's own view (what the forecasts and the bracket use)
    # Re-centered so 0 = an average D-I team: the margin vs a top-100 opponent minus the D-I average of that
    # same quantity. Gaps between teams (and every forecast) are unchanged; ranks are identical.
    out["power"] = out["power_net"] - out["power_net"].mean()
    out["win_vs_avg"] = P.sum(1) / (n - 1)
    top = np.argsort(-out["power"].to_numpy())[:25]
    out["win_vs_top25"] = P[:, top].mean(1)
    out = out.sort_values("power", ascending=False)
    out["rank"] = np.arange(1, n + 1)
    return out, (teams, M, P)


def upcoming(bundle, state, games: pd.DataFrame, days: int = 7) -> pd.DataFrame:
    t0 = pd.Timestamp(today())
    g = games[(~games["played"]) & (games["date"] >= t0) & (games["date"] <= t0 + pd.Timedelta(days=days))
              & (games["type"] != "nond1")]
    g = g[g["team_a"].isin(state.index) & g["team_b"].isin(state.index)]
    if g.empty:
        return pd.DataFrame()
    pairs = g[["team_a", "team_b", "loc", "v_lat", "v_lon", "date", "type", "gid", "city", "state"]].copy()
    pairs["is_conftourney"] = (pairs["type"] == "conftourney").astype(float)
    pairs["is_post"] = (pairs["type"] == "post").astype(float)
    pairs["day"] = (pairs["date"] - pd.Timestamp(f"{int(state['season'].iloc[0]) - 1}-11-01")).dt.days
    last = state.get("last_date")
    df = predict_pairs(bundle, state, pairs)
    df["date"] = pairs["date"].to_numpy()
    df["gid"] = pairs["gid"].to_numpy()
    df["city"] = pairs["city"].to_numpy()
    return df

"""End-to-end pipeline: data -> point-in-time features -> (train / backtest) -> identities -> site JSON."""
from __future__ import annotations

import datetime as dt
import json
import math
import pickle
import re
import time

import numpy as np
import pandas as pd

from . import bracket as BR
from . import features as F
from . import games as D
from . import players as PL
from . import predict as PR
from .config import (ARTIFACTS, FIRST_SEASON, FIRST_TRAIN_SEASON, POSS_RANGE, PROC, SITE_DATA, current_season,
                     season_start, today)
from .model import backtest as BT
from .model import linear as LIN
from .model import train as TR
from .sources import espn
from .sources import injuries as INJ

NET_PATH = ARTIFACTS / "model.pt"
LINEAR_PATH = ARTIFACTS / "linear.json"
BLEND_PATH = ARTIFACTS / "blend.json"


def log(msg):
    print(f"[{dt.datetime.now():%H:%M:%S}] {msg}", flush=True)


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-")


def _clean(v):
    if v is pd.NA or v is pd.NaT or v is None:
        return None
    if isinstance(v, (np.floating, float)):
        return None if (math.isnan(v) or math.isinf(v)) else round(float(v), 4)
    if isinstance(v, np.integer):
        return int(v)
    if isinstance(v, (pd.Timestamp, dt.date)):
        return v.strftime("%Y-%m-%d")
    if isinstance(v, np.bool_):
        return bool(v)
    return v


def records(df: pd.DataFrame) -> list[dict]:
    return [{k: _clean(v) for k, v in r.items()} for r in df.to_dict(orient="records")]


def write_json(name, obj):
    p = SITE_DATA / name
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w") as f:
        json.dump(obj, f, separators=(",", ":"), default=_clean, allow_nan=False)


def utcnow() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


# ------------------------------------------------------------------------ stages
def stage_data(refresh=True, refit=False):
    from . import espn_fallback, history
    from . import lines as L
    from .config import ESPN_DATA
    if ESPN_DATA:
        from . import espn_data
        cur = current_season()
        from .ingest import ingest_season, season_dir
        for s in range(FIRST_SEASON, cur):  # a fresh data cache (e.g. CI) needs the completed seasons once
            if not (season_dir(s) / "player_games.parquet").exists():
                log(f"play-by-play + box scores {s} (ESPN, one-time)")
                ingest_season(s)
        if refresh:
            log("play-by-play + box scores (current season, ESPN)")
            ingest_season(cur)
        log("games, players and conferences (ESPN)")
        out = espn_data.build(refresh=refresh, refit_box=refit)
        lines = L.collect()
        if len(lines):
            L.save(lines)
        return out
    seasons = list(range(FIRST_SEASON, current_season() + 1))
    cur = current_season()
    log("fetching games (Torvik + ESPN)")
    games, meta = D.build(seasons, refresh_current=refresh)
    log("player data (history + Torvik snapshot + ESPN box scores)")
    cw = pd.read_csv(ARTIFACTS / "crosswalk.csv", dtype={"espn_id": str})
    espn_fallback.current_player_games(cur, games, cw)
    pg = PL.load_player_games(seasons, refresh_current=refresh)
    ps = PL.load_player_seasons(seasons, refresh_current=refresh)
    conf = F.conferences(seasons)
    if not espn_fallback.active(cur) and (PROC / f"tv_games_{cur}.parquet").exists():
        # games + player-season table only; the player-game snapshot stays frozen (no json fetches)
        history.save_snapshot(cur, pd.read_parquet(PROC / f"tv_games_{cur}.parquet"), None, ps[ps["season"] == cur])
        log("updated Torvik snapshot (games, player seasons) in history/")
    if refresh:
        from .ingest import ingest_season
        log("play-by-play (current season)")
        try:
            ingest_season(cur)
        except Exception as e:  # identities are optional for the rest of the pipeline
            log(f"  play-by-play ingest failed: {e}")
    lines = L.collect()
    if len(lines):
        L.save(lines)
    return games, meta, pg, ps, conf


def stage_features(games, meta, pg, ps, conf):
    season = current_season()
    cur = pg[pg["season"] == season][["team", "pid", "player"]].drop_duplicates("pid", keep="last")
    log("injury report")
    avail, inj_report = INJ.availability(meta, cur)
    log(f"  {len(inj_report)} injury rows, {len(avail)} matched to players")
    log("building point-in-time features")
    feat, snaps, live, extras = F.build(games, pg, ps, conf, availability=avail)
    feat.to_parquet(PROC / "features.parquet")
    snaps.to_parquet(PROC / "snapshots.parquet")
    with open(PROC / "live_extras.pkl", "wb") as f:
        pickle.dump((live, extras, inj_report), f)
    return feat, snaps, live, extras, inj_report


def training_rows(feat):
    f = BT.add_derived(feat)
    return f[(f["season"] >= FIRST_TRAIN_SEASON) & (f["season"] < current_season()) & f["played"] & f["margin"].notna()]


def stage_train(feat, n_models=TR.N_MODELS):
    """Fit both game models on completed seasons only, so this season's tracking is out-of-sample."""
    f = training_rows(feat)
    smeans = TR.season_means(BT.add_derived(feat[feat["season"] >= FIRST_TRAIN_SEASON]))
    log(f"training {n_models} matchup networks on {len(f):,} games")
    models, norm = TR.train_models(f, smeans, n_models=n_models)
    TR.save(models, norm, smeans, NET_PATH)
    log("fitting the linear game model")
    lin = BT.LinearGame().fit(f).m
    LINEAR_PATH.write_text(json.dumps(lin.to_json()))
    with open(ARTIFACTS / "linear.pkl", "wb") as fh:
        pickle.dump(lin, fh)
    return models, norm, smeans, lin


MODEL_LABELS = {
    "seed": ("Seed difference", "Seed"), "elo": ("Elo", "Elo"), "efficiency": ("Adjusted efficiency (AdjEM)", "AdjEM"),
    "kalman": ("Dynamic (Kalman) rating", "Kalman"), "linear": ("Linear game model", "Linear"),
    "net_mmai": ("Matchup network, core features", "Net (core)"), "net": ("Matchup network (all features)", "Net"),
    "blend": ("hoop2vec blend", "Blend"),
}


def stage_backtest(feat, seasons=None, n_models=3):
    """Season-ahead backtest of every candidate; picks the blend weight; writes artifacts + site JSON."""
    from . import lines as L
    log("season-ahead backtest")
    # first test season needs a few training seasons before it (women's: train 2020-21 -> test 2022 onward)
    seasons = seasons or list(range(FIRST_TRAIN_SEASON + 2 if FIRST_TRAIN_SEASON >= 2015 else 2012, current_season()))
    games = pd.read_parquet(PROC / "games.parquet")
    la = L.for_games(feat, games, L.collect())
    mm = [c for c in F.TEAM_FEATS if not c.startswith("k_")]
    models = {"elo": lambda: BT.OLSNormal("elo"), "efficiency": lambda: BT.OLSNormal("efficiency"),
              "kalman": lambda: BT.OLSNormal("kalman"), "linear": lambda: BT.LinearGame(),
              "net_mmai": lambda: BT.Net(n_models=n_models, feats=mm), "net": lambda: BT.Net(n_models=n_models)}
    P = BT.run(feat, models, seasons, lines=la)
    w_best, lls = choose_blend(P)
    BLEND_PATH.write_text(json.dumps({"w_net": w_best, "grid": {str(k): v for k, v in lls.items()}}, indent=1))
    names = list(models) + ["blend"]
    res = BT.summarize(P, names, ref="linear")
    BT.save(res, P, "backtest")
    publish_backtest(res, P, names)
    from .config import PRO_LEAGUE
    if PRO_LEAGUE:  # every past playoff series, predicted before game 1 by the season-ahead models
        from . import playoffs as PO
        sb = PO.backtest_series(P, games, names)  # P already holds the honest blend column
        PO.BT_PATH.write_text(json.dumps(sb, indent=1))
        log(f"  series backtest: {sb.get('n', 0)} series")
    log(f"  blend weight on the network: {w_best}")
    return res


BLEND_GRID = np.round(np.arange(0, 1.01, 0.1), 2)


def _best_w(P):
    y = (P["margin"] > 0).to_numpy()
    lls = {}
    for w in BLEND_GRID:
        BT.blend(P, "net", "linear", float(w), "tmp")
        lls[float(w)] = BT.log_loss(y, P["p_tmp"])
    return float(min(lls, key=lls.get)), lls


def choose_blend(P):
    """Network weight for the live model (all backtest seasons) and an honest 'blend' column in which every
    season uses only the weight chosen on *earlier* test seasons (0.5 until two seasons are available)."""
    mu = np.zeros(len(P)); sg = np.zeros(len(P))
    for S in sorted(P["season"].unique()):
        prev = P[P["season"] < S]
        w = _best_w(prev.copy())[0] if prev["season"].nunique() >= 2 else 0.5
        m = (P["season"] == S).to_numpy()
        sub = P.loc[m].copy()
        BT.blend(sub, "net", "linear", w, "x")
        mu[m], sg[m] = sub["mu_x"], sub["sg_x"]
    from scipy.stats import norm as N
    P["mu_blend"], P["sg_blend"], P["p_blend"] = mu, sg, N.cdf(mu / sg)
    w_all, lls = _best_w(P.copy())
    return w_all, lls


def publish_backtest(res, P, names):
    ncaa = P[P["is_ncaa"].fillna(False).astype(bool)]
    per = []
    for s, d in ncaa.groupby("season"):
        row = {"season": int(s), "n": int(len(d))}
        for k in names:
            row[k] = BT.log_loss(d["margin"] > 0, d[f"p_{k}"])
        per.append(row)
    write_json("backtest.json", {
        "seasons": [int(P["season"].min()), int(P["season"].max())], "final": "blend",
        "models": [{"key": k, "label": MODEL_LABELS.get(k, (k, k))[0], "short": MODEL_LABELS.get(k, (k, k))[1]} for k in names],
        "subsets": res, "per_season": per})


# ------------------------------------------------------------------------ model bundle
def load_bundle(feat=None):
    models, norm, smeans = TR.load(NET_PATH)
    if feat is not None:  # current-season league means from today's data
        sm = TR.season_means(BT.add_derived(feat[feat["season"] >= current_season()]))
        smeans = pd.concat([smeans[~smeans.index.isin(sm.index)], sm]).sort_index()
    with open(ARTIFACTS / "linear.pkl", "rb") as fh:
        lin = pickle.load(fh)
    w = json.loads(BLEND_PATH.read_text())["w_net"] if BLEND_PATH.exists() else 0.5
    return {"nets": (models, norm, smeans), "linear": lin, "w_net": w}


# ------------------------------------------------------------------------ publish
def stage_publish(games, meta, pg, conf, feat, snaps, live, extras, inj_report, is_live=False):
    season = current_season()
    from . import site
    site.write()
    bundle = load_bundle(feat)
    models, norm, smeans = bundle["nets"]
    state = PR.team_state(snaps, extras, conf, meta, season)
    log(f"power ratings for {len(state)} teams (network weight {bundle['w_net']})")
    power, (teams, M, P) = PR.power_ratings(bundle, state)
    st = state.join(power)
    st["slug"] = [slug(t) for t in st.index]
    st["k_em"] = st["k_o"] - st["k_d"]

    for c, asc in (("adjem", False), ("eff_o", False), ("eff_d", True), ("adjt", False), ("elo", False),
                   ("roster", False), ("k_em", False)):
        st[f"{c}_rank"] = st[c].rank(ascending=asc, method="min")

    gcur = games[(games["season"] == season) & (games["type"] != "nond1")]
    played = gcur[gcur["played"]]
    from .config import PRO_LEAGUE
    rec_games = played[played["type"].isin(["conf", "nonconf"])] if PRO_LEAGUE else played  # pros: regular-season record
    rec = {}
    for r in rec_games.itertuples():
        for t, won in ((r.team_a, r.pts_a > r.pts_b), (r.team_b, r.pts_b > r.pts_a)):
            w, l_ = rec.get(t, (0, 0))
            rec[t] = (w + won, l_ + (not won))
    st["wins"] = [rec.get(t, (0, 0))[0] for t in st.index]
    st["losses"] = [rec.get(t, (0, 0))[1] for t in st.index]
    opp_power = st["power"].to_dict()
    sos = {}
    for t in st.index:
        opps = list(played.loc[played["team_a"] == t, "team_b"]) + list(played.loc[played["team_b"] == t, "team_a"])
        vals = [opp_power[o] for o in opps if o in opp_power]
        sos[t] = float(np.mean(vals)) if vals else np.nan
    st["sos"] = pd.Series(sos)
    st["sos_rank"] = st["sos"].rank(ascending=False, method="min")
    hist = update_power_history(st["rank"])
    prev_i = max(0, len(hist["dates"]) - 8)
    st["rank_prev"] = [(hist["ranks"].get(t) or [None] * len(hist["dates"]))[prev_i] if len(hist["dates"]) > 1 else None
                       for t in st.index]
    # live injury impact in points per game (at team tempo)
    st["injury_pts"] = st["avail_delta"].fillna(0) * st["adjt"] / 100

    # identities (style archetypes) for the ratings table
    ident = stage_identity(season)
    if ident is not None:
        arch = {c["id"]: {"o": c["blocks"]["offense"]["primary"], "d": c["blocks"]["defense"]["primary"],
                          "r": c["blocks"]["roster"]["primary"]} for c in ident}
        st["arch"] = [arch.get(str(e)) for e in st["espn_id"]]

    cols = ["slug", "rank", "rank_prev", "power", "power_net", "win_vs_avg", "win_vs_top25", "conf", "wins", "losses",
            "eff_o", "eff_d", "adjem", "adjt", "k_em", "k_sd", "elo", "roster", "avail_delta", "injury_pts", "sos", "games",
            "form", "eff_o_rank", "eff_d_rank", "adjem_rank", "adjt_rank", "elo_rank", "roster_rank", "sos_rank", "k_em_rank",
            "espn_id", "location", "abbr", "color", "alt_color", "logo", "height", "experience", "continuity", "star", "bench",
            "arch"] + [f"{x}_{y}" for x in F.STYLE for y in ("o", "d")]
    cols = [c for c in cols if c in st.columns]
    out = st[cols].copy()
    out.insert(0, "team", out.index)
    write_json("ratings.json", {"season": season, "updated": utcnow(), "league_eff": _clean(float(st["mu"].mean())),
                                "teams": records(out.sort_values("rank"))})

    # ---- in-browser model (network weights + linear coefficients + team vectors)
    TR.export_web(models, norm, SITE_DATA / "model.json",
                  extra={"linear": {k: v for k, v in bundle["linear"].to_json().items()}, "w_net": bundle["w_net"],
                         "mu_t": float(state["k_mut"].iloc[0]), "poss_range": list(POSS_RANGE)})
    from .config import PRO_LEAGUE
    pro_extra = {}
    if PRO_LEAGUE:  # Matchup Lab: playoff context day and the series format
        from . import playoffs as PO
        lp = games[(games["season"] == season - 1) & (games["type"] == "post")]
        cfg = PO.load_config()
        pro_extra = {"post_day": int((lp["date"].min() - pd.Timestamp(season_start(season - 1))).days) if len(lp) else 180,
                     "playoff_format": {"rounds": cfg["rounds"], "patterns": cfg.get("patterns", {})}}
    vec_df = PR.pair_frame(state, pd.DataFrame({"team_a": state.index, "team_b": state.index, "loc": 0}))
    z = norm(TR.team_matrix(vec_df, "a", smeans))
    raw = {t: {"k_o": _clean(st.at[t, "k_o"]), "k_d": _clean(st.at[t, "k_d"]), "k_t": _clean(st.at[t, "k_t"]),
               "adjem": _clean(st.at[t, "adjem"]), "avail": _clean(st.at[t, "avail_delta"])} for t in state.index}
    write_json("vectors.json", {"features": F.TEAM_FEATS, "teams": {t: [round(float(v), 4) for v in z[i]] for i, t in enumerate(state.index)},
                                "raw": raw, "loc": {t: [_clean(st.at[t, "lat"]), _clean(st.at[t, "lon"])] for t in st.index},
                                "conf": {t: st.at[t, "conf"] for t in st.index},
                                "day": int((pd.Timestamp(today()) - pd.Timestamp(season_start(season))).days),
                                "venues": [] if PRO_LEAGUE else venue_list(), **pro_extra})

    # ---- games: upcoming predictions + this season's results with pre-game forecasts
    log("game predictions")
    up = PR.upcoming(bundle, state, gcur, days=200)  # whole remaining schedule (team pages); site lists the next week
    if len(up):
        ptsg = st["power"].reindex(up["team_a"]).to_numpy() - st["power"].reindex(up["team_b"]).to_numpy()
        neutral = PR.predict_pairs(bundle, state, up[["team_a", "team_b"]].assign(loc=0))
        up["edge"] = neutral["margin"].to_numpy() - ptsg * up["poss_exp"].to_numpy() / 100
    recent, acc = season_tracking(feat, season, bundle)
    gcols = ["date", "team_a", "team_b", "loc", "city", "p_a", "margin", "pts_a", "pts_b", "edge"]
    soon = up[up["date"] <= pd.Timestamp(today()) + pd.Timedelta(days=7)] if len(up) else up
    upcoming = records(soon[gcols].sort_values("date")) if len(soon) else []
    write_json("games.json", {"upcoming": upcoming, "recent": recent[-500:], "season_accuracy": acc})

    log("team pages")
    write_team_pages(st, snaps, season, live, pg, inj_report, gcur, feat, bundle, up)

    log("players")
    pdf = write_players(st, live, pg, inj_report, season)
    log("player pages (careers, shot zones, shot locations)")
    try:
        from . import careers
        log(f"  {careers.publish(pdf, season)} players")
    except Exception as e:  # player pages are optional for the rest of the site
        log(f"  player pages skipped: {e!r}")

    if PRO_LEAGUE:
        log("playoffs (season simulation, play-in, series)")
        from . import playoffs as PO
        po = PO.publish(bundle, st, games, conf, feat, season)
        po["backtest"] = json.loads(PO.BT_PATH.read_text()) if PO.BT_PATH.exists() else None
        po["updated"] = utcnow()
        write_json("playoffs.json", po)
        log(f"  {po['phase']}: favorite {po['teams'][0]['team']} ({po['teams'][0]['p_title']:.0%})")
    else:
        log("bracket")
        write_json("bracket.json", make_bracket(bundle, st, power, games, meta, season))
        if not is_live:  # season-level player tables: one game doesn't move them (the overnight job refreshes them)
            stage_prospects()

    from . import espn_fallback
    source = "espn" if espn_fallback.active(season) else "torvik"
    write_json("meta.json", {
        "updated": utcnow(), "season": season, "source": source, "torvik_through": espn_fallback.snapshot_date(season),
        "n_teams": int(len(st)), "n_games_train": int(feat["played"].sum()), "w_net": bundle["w_net"],
        "seasons": [int(feat["season"].min()), int(feat["season"].max())],
        "injuries": records(inj_report.drop(columns=["pid"], errors="ignore")) if len(inj_report) else [],
        "season_accuracy": acc,
    })
    log("publish done")


def stage_prospects(refresh=False, backtest=False):
    """NBA prospect models (draft order + NBA success) -> prospects.json. Optional: rerun their backtest."""
    from . import prospects as PRO
    try:
        log("NBA prospects")
        T, _, nba, link = PRO.build_table(refresh_current=refresh)
        if backtest or not PRO.BT_PATH.exists():
            log("  prospect backtest (forward-chained by draft year)")
            PRO.run_backtest(T)
        out = PRO.publish(T, write_json, nba=nba, link=link)
        log(f"  {len(out['players']) if out else 0} prospects on the board")
    except Exception as e:  # never block the rest of the site
        log(f"  prospects skipped: {e!r}")


def season_tracking(feat, season, bundle):
    """This season's played games with the forecast the model would have made the day before."""
    from . import lines as L
    f = BT.add_derived(feat[(feat["season"] == season) & feat["played"]])
    if f.empty:
        return [], None
    f = BT.pregame_view(f)
    mu, sg, p, _ = PR.blend_predict(bundle, f)
    f["pred_margin"], f["p_a"] = mu, p
    f = f.sort_values("date")
    recent = records(f[["date", "team_a", "team_b", "loc", "pts_a", "pts_b", "p_a", "pred_margin"]])
    y = f["margin"].to_numpy() > 0
    acc = {"n": int(len(f)), "accuracy": float(np.mean((f["p_a"] > 0.5) == y)), "log_loss": BT.log_loss(y, f["p_a"]),
           "mae": float(np.mean(np.abs(f["margin"] - f["pred_margin"])))}
    try:
        la = L.for_games(f, pd.read_parquet(PROC / "games.parquet"), L.collect([season]))
        m = f.merge(la, on=["season", "gid"])
        if len(m) >= 50:
            from scipy.stats import norm as N
            s = float(np.sqrt(np.mean((m["margin"] - m["line_a"]) ** 2)))
            acc.update({"market_n": int(len(m)), "market_log_loss": BT.log_loss(m["margin"] > 0, N.cdf(m["line_a"] / s)),
                        "model_log_loss_on_market_games": BT.log_loss(m["margin"] > 0, m["p_a"])})
    except Exception as e:  # lines are a benchmark only
        log(f"  market comparison skipped: {e}")
    return recent, acc


def update_power_history(rank: pd.Series) -> dict:
    f = SITE_DATA / "power_history.json"
    h = json.loads(f.read_text()) if f.exists() else {"dates": [], "ranks": {}}
    day = today().isoformat()
    if h["dates"] and h["dates"][-1] == day:  # re-run on the same day: replace today's column
        h["dates"].pop()
        h["ranks"] = {k: v[:len(h["dates"])] for k, v in h["ranks"].items()}
    h["dates"].append(day)
    n = len(h["dates"])
    for t in set(h["ranks"]) | set(rank.index):
        v = (h["ranks"].get(t, []) + [None] * n)[:n - 1]
        v.append(int(rank[t]) if t in rank.index else None)
        h["ranks"][t] = v
    if n > 150:
        h["dates"] = h["dates"][-150:]
        h["ranks"] = {k: v[-150:] for k, v in h["ranks"].items()}
    write_json("power_history.json", h)
    return h


def stage_identity(season):
    """Live style identities (play-by-play) for the current season; None if the PBP data is missing."""
    try:
        from .style import identity as ID
        model = ID.load_model()
        df, rec, meta = ID.live_tables(season)
        df.to_parquet(PROC / f"style_{season}.parquet")
        ID.publish_dictionary(model)
        _, _, cards = ID.publish_season(season, df, meta, model, rec)
        log(f"  identities for {len(cards)} teams")
        return cards
    except Exception as e:
        log(f"  identities skipped: {e!r}")
        f = SITE_DATA / f"season_{season}.json"
        return json.loads(f.read_text())["teams"] if f.exists() else None


def venue_list():
    v = []
    cfg = BR.load_config()
    fr = cfg.get("first_round_sites", BR.DEFAULT_SITES["first_round_sites"])
    op = cfg.get("opening_sites", BR.DEFAULT_SITES["opening_sites"])
    groups = (("Opening round", [] if op == "campus" else op),
              ("First/second round", [] if fr == "campus" else fr),
              ("Regional", [r["site"] for r in cfg.get("regions", BR.DEFAULT_SITES["regions"])]),
              ("Final Four", [cfg.get("final_four_site", BR.DEFAULT_SITES["final_four_site"])]))
    for name, lst in groups:
        for s in lst:
            site = BR._site(s)
            v.append({"group": name, "city": site["city"], "lat": _clean(site["lat"]), "lon": _clean(site["lon"])})
    return v


def _espn_box(season, st) -> pd.DataFrame:
    """This season's ESPN box-score totals per player (play-by-play ingest), keyed by (team, normalized name)."""
    from .ingest import season_dir
    from .sources.injuries import norm_name
    f = season_dir(season) / "player_games.parquet"
    if not f.exists():
        return pd.DataFrame()
    b = pd.read_parquet(f)
    b = b[(b["min"] > 0) & ~b["dnp"].fillna(False).astype(bool)].copy()
    id2team = {str(e): t for t, e in st["espn_id"].items() if isinstance(e, str)}
    b["team"] = b["team_id"].astype(str).map(id2team)
    b = b[b["team"].notna()]
    b["reb"] = b["oreb"] + b["dreb"]
    agg = b.groupby(["team", "athlete_id"]).agg(
        name=("name", "last"), pos=("pos", "last"), g=("game_id", "nunique"), gs=("starter", "sum"), min=("min", "sum"),
        **{c: (c, "sum") for c in ("pts", "reb", "oreb", "dreb", "ast", "stl", "blk", "tov", "pf", "fgm", "fga",
                                   "tpm", "tpa", "ftm", "fta")}).reset_index()
    agg["nk"] = agg["name"].map(norm_name)
    # advanced rates from the box scores (the ESPN-only leagues have no Torvik tables): team and opponent totals
    tg = b.groupby(["game_id", "team"])[["min", "fgm", "fga", "tpa", "fta", "tov", "oreb", "dreb"]].sum().reset_index()
    op = tg.merge(tg, on="game_id", suffixes=("", "_o"))
    op = op[op["team"] != op["team_o"]]
    op["poss_o"] = op["fga_o"] - op["oreb_o"] + op["tov_o"] + 0.44 * op["fta_o"]
    T = op.groupby("team").agg(Tmin=("min", "sum"), Tfgm=("fgm", "sum"), Tfga=("fga", "sum"), Tfta=("fta", "sum"),
                               Ttov=("tov", "sum"), Torb=("oreb", "sum"), Tdrb=("dreb", "sum"), Oorb=("oreb_o", "sum"),
                               Odrb=("dreb_o", "sum"), Ofga=("fga_o", "sum"), Otpa=("tpa_o", "sum"), Oposs=("poss_o", "sum"))
    a = agg.join(T, on="team")
    f = a["min"] / (a["Tmin"] / 5).clip(lower=1)           # share of the team's minutes
    used = a["fga"] + 0.44 * a["fta"] + a["tov"]
    pos = lambda x: x.where(x > 0)  # noqa: E731
    agg["usg"] = 100 * used / pos(f * (a["Tfga"] + 0.44 * a["Tfta"] + a["Ttov"]))
    agg["ts"] = 100 * a["pts"] / pos(2 * (a["fga"] + 0.44 * a["fta"]))
    agg["efg"] = 100 * (a["fgm"] + 0.5 * a["tpm"]) / pos(a["fga"])
    agg["ast_pct"] = 100 * a["ast"] / pos(f * a["Tfgm"] - a["fgm"])
    agg["to_pct"] = 100 * a["tov"] / pos(used)
    agg["orb_pct"] = 100 * a["oreb"] / pos(f * (a["Torb"] + a["Odrb"]))
    agg["drb_pct"] = 100 * a["dreb"] / pos(f * (a["Tdrb"] + a["Oorb"]))
    agg["blk_pct"] = 100 * a["blk"] / pos(f * (a["Ofga"] - a["Otpa"]))
    agg["stl_pct"] = 100 * a["stl"] / pos(f * a["Oposs"])
    return agg


def write_players(st, live, pg, inj_report, season):
    """players.json: every D-I player this season. Model value/impact/availability + ESPN per-game box stats +
    Torvik's advanced rates. Value is the point-in-time, prior-shrunk box plus/minus (pts per 100 vs an average D-I
    player); impact = minutes share x (value - replacement), i.e. what he adds to his team's rating per 100."""
    from .sources.injuries import norm_name
    names = pg[pg["season"] == season].drop_duplicates("pid", keep="last").set_index("pid")
    ps = PL.load_player_seasons([season], refresh_current=False)
    ps = ps.assign(pid=pd.to_numeric(ps["pid"], errors="coerce")).dropna(subset=["pid"])
    ps = ps.drop_duplicates("pid", keep="last").set_index("pid")
    box = _espn_box(season, st)
    bidx = {}
    if len(box):
        for r in box.itertuples():
            bidx[(r.team, r.nk)] = r
        last = box.assign(ln=box["nk"].str.split().str[-1])
        cnt = last.groupby(["team", "ln"])["nk"].transform("size")
        by_last = {(t, ln): r for t, ln, c, r in zip(last["team"], last["ln"], cnt, last.itertuples()) if c == 1}
    inj = {}
    if len(inj_report):
        for r in inj_report.dropna(subset=["pid"]).itertuples():
            inj[(r.team, int(r.pid))] = f"{r.status} ({r.injury})" if getattr(r, "injury", "") else str(r.status)
    num = lambda v: None if v is None or (isinstance(v, float) and not np.isfinite(v)) else v  # noqa: E731
    rows = []
    for team, lp in live.items():
        if team not in st.index:
            continue
        for p in lp.itertuples():
            if p.games <= 0 or p.minutes <= 0:
                continue
            pid = int(p.pid)
            nm = names["player"].get(pid, str(pid))
            nk = norm_name(nm)
            b = bidx.get((team, nk)) or (by_last.get((team, nk.split()[-1])) if len(box) and nk else None)
            t = ps.loc[pid] if pid in ps.index else None
            tv = (lambda c: num(pd.to_numeric(t.get(c), errors="coerce")) if t is not None else None)
            r = {"pid": pid, "name": nm, "team": team, "conf": st.at[team, "conf"], "cls": _clean(names["cls"].get(pid)),
                 "ht": _clean(names["inches"].get(pid)), "pos": b.pos if b is not None else None,
                 "role": (str(t.get("role")) if t is not None and isinstance(t.get("role"), str) else None),
                 "rec": tv("rec_rank"), "g": int(p.games), "mpg": p.minutes / max(p.games, 1),
                 "min_pct": 100 * p.share / 5, "value": p.value, "o": p.o_value, "d": p.d_value,
                 "impact": p.share * (p.value - PL.V_REPL), "avail": p.avail, "status": inj.get((team, pid)),
                 "usg": tv("usg"), "ortg": tv("ortg"), "ts": tv("ts"), "efg": tv("efg"), "ast_pct": tv("ast_pct"),
                 "to_pct": tv("to_pct"), "orb_pct": tv("orb_pct"), "drb_pct": tv("drb_pct"), "blk_pct": tv("blk_pct"),
                 "stl_pct": tv("stl_pct")}
            if b is not None and b.g > 0:
                g = b.g
                r.update({"gs": int(b.gs), "ppg": b.pts / g, "rpg": b.reb / g, "apg": b.ast / g, "spg": b.stl / g,
                          "bpg": b.blk / g, "tpg": b.tov / g, "tpm": b.tpm / g,
                          "fg_pct": 100 * b.fgm / b.fga if b.fga else None, "tp_pct": 100 * b.tpm / b.tpa if b.tpa else None,
                          "ft_pct": 100 * b.ftm / b.fta if b.fta else None,
                          "ts_box": 100 * b.pts / (2 * (b.fga + 0.44 * b.fta)) if (b.fga + b.fta) else None})
                for c in ("usg", "ts", "efg", "ast_pct", "to_pct", "orb_pct", "drb_pct", "blk_pct", "stl_pct"):
                    if r.get(c) is None:  # ESPN-only leagues: rates from the box scores
                        r[c] = num(float(getattr(b, c))) if pd.notna(getattr(b, c)) else None
            rows.append(r)
    df = pd.DataFrame(rows)
    for c in ("value", "impact"):
        df[f"{c}_rank"] = df[c].where((df["g"] >= 3) & (df["mpg"] >= 10)).rank(ascending=False, method="min")
    df = df.sort_values("impact", ascending=False)
    fl = [c for c in df.columns if df[c].dtype.kind == "f" and not c.endswith("_rank")]
    df[fl] = df[fl].round(2)
    write_json("players.json", {"season": season, "updated": utcnow(), "replacement": PL.V_REPL,
                                "players": [{k: _clean(v) for k, v in rec.items() if _clean(v) is not None}
                                            for rec in df.to_dict(orient="records")]})
    log(f"  {len(df)} players")
    return df


def write_team_pages(st, snaps, season, live, pg, inj_report, gcur, feat, bundle, up):
    s = snaps[snaps["season"] == season].copy()
    s["rk"] = s.groupby("snap")["adjem"].rank(ascending=False, method="min")
    hist = {t: g for t, g in s.groupby("team")}
    names = pg[pg["season"] == season].drop_duplicates("pid", keep="last").set_index("pid")
    inj = inj_report.set_index("player") if len(inj_report) else None
    pred = {}
    f = BT.add_derived(feat[(feat["season"] == season) & feat["played"]])
    if len(f):
        f = BT.pregame_view(f)
        mu, _, p, _ = PR.blend_predict(bundle, f)
        pred.update({g: (pp, m) for g, pp, m in zip(f["gid"], p, mu)})
    if len(up):
        pred.update({g: (pp, m) for g, pp, m in zip(up["gid"], up["p_a"], up["margin"])})
    for t, row in st.iterrows():
        h = hist.get(t)
        history = []
        if h is not None:
            h = h.sort_values("snap")
            history = [{"date": d.strftime("%Y-%m-%d"), "adjem": _clean(a), "o": _clean(o), "d": _clean(dd), "rank": _clean(r)}
                       for d, a, o, dd, r in zip(h["snap"], h["adjem"], h["eff_o"], h["eff_d"], h["rk"])]
        roster = []
        lp = live.get(t)
        if lp is not None:
            for p in lp.sort_values("share", ascending=False).itertuples():
                if p.minutes <= 0 and p.avail <= 0:
                    continue
                nm = names["player"].get(p.pid, str(p.pid))
                status = None
                if inj is not None and nm in inj.index:
                    r_ = inj.loc[nm]
                    r_ = r_.iloc[0] if isinstance(r_, pd.DataFrame) else r_
                    if r_["team"] == t:
                        status = f"{r_['status']} ({r_['injury']})" if r_["injury"] else r_["status"]
                roster.append({"player": nm, "pid": int(p.pid), "cls": _clean(names["cls"].get(p.pid)), "inches": _clean(names["inches"].get(p.pid)),
                               "value": _clean(p.value), "o": _clean(p.o_value), "d": _clean(p.d_value),
                               "mpg": _clean(p.minutes / max(p.games, 1)), "games": int(p.games), "share": _clean(p.share),
                               "avail": _clean(p.avail), "status": status})
        sched = []
        tg = gcur[(gcur["team_a"] == t) | (gcur["team_b"] == t)].sort_values("date")
        for r in tg.itertuples():
            is_a = r.team_a == t
            p, m = pred.get(r.gid, (None, None))
            if p is not None and not is_a:
                p, m = 1 - p, -m
            sched.append({"date": r.date.strftime("%Y-%m-%d"), "opp": r.team_b if is_a else r.team_a,
                          "loc": int(r.loc if is_a else -r.loc), "played": bool(r.played),
                          "pts": _clean(r.pts_a if is_a else r.pts_b), "opp_pts": _clean(r.pts_b if is_a else r.pts_a),
                          "p": _clean(p), "margin": _clean(m), "type": r.type})
        write_json(f"teams/{row['slug']}.json", {"team": t, "history": history, "roster": roster, "schedule": sched})


def make_bracket(bundle, st, power, games, meta, season):
    cfg = BR.load_config(season)
    name2team = dict(zip(meta["espn_id"].astype(str), meta["team"]))
    actual = None
    try:
        actual = BR.actual_bracket(espn.tournament_games(season), name2team, cfg)
    except Exception as e:
        print("espn tournament lookup failed:", e)
    fld = None
    if cfg.get("bracket"):  # manual bracket in YAML wins
        br = {"regions": cfg["bracket"]["regions"], "final_four": cfg["final_four_pairs"], "projected": False}
        for r in br["regions"]:
            r["site"] = BR._site(r.get("site"))
        pods = BR.assign_pods(br, st, cfg)
    elif actual:
        br = actual
        pods = actual.get("pod_sites") or BR.assign_pods(br, st, cfg)
    else:
        fld = BR.project_field(st, power, games, cfg)
        br = BR.projected_bracket(fld, cfg)
        pods = BR.assign_pods(br, st, cfg)
    tree = BR.build_tree(br, pods, cfg)
    BR.solve(tree, bundle, st, season)
    adv = BR.advancement(tree)
    BR.picks(tree, "likely")
    likely, ep_likely = BR.tree_json(tree), BR.expected_points(tree)
    BR.picks(tree, "pool")
    pool, ep_pool = BR.tree_json(tree), BR.expected_points(tree)
    adv = adv.join(st[["slug", "power", "conf", "logo", "rank"]], how="left")
    adv.insert(0, "team", adv.index)
    out = {"projected": bool(br.get("projected")), "season": season,
           "regions": [r["name"] for r in br["regions"]], "final_four": br["final_four"],
           "advancement": records(adv), "likely": likely, "pool": pool,
           "expected_points": {"likely": round(ep_likely, 2), "pool": round(ep_pool, 2)},
           "field_size": int(sum(len(v) for r in br["regions"] for v in r["slots"].values())),
           "note": ("Projected field: auto-bids go to each conference's tournament champion (or highest-rated team "
                    "until decided); at-large bids and seeds follow Power. 76-team format with a 12-game "
                    "Opening Round.") if br.get("projected") else "Official bracket."}
    if fld is not None:
        out["field"] = records(fld.reset_index().rename(columns={"index": "team"})[["team", "conf", "bid", "overall", "opening", "power"]])
    return out


# ------------------------------------------------------------------------ entry
def load_cached():
    games = pd.read_parquet(PROC / "games.parquet")
    meta = pd.read_csv(ARTIFACTS / "team_meta.csv", dtype={"espn_id": str})
    seasons = list(range(FIRST_SEASON, current_season() + 1))
    pg = PL.load_player_games(seasons, refresh_current=False)
    conf = F.conferences(seasons)
    feat = pd.read_parquet(PROC / "features.parquet")
    snaps = pd.read_parquet(PROC / "snapshots.parquet")
    with open(PROC / "live_extras.pkl", "rb") as f:
        live, extras, inj = pickle.load(f)
    return games, meta, pg, conf, feat, snaps, live, extras, inj


def run(mode="daily"):
    """mode: daily | train | backtest | all | publish | offline | live. ``live`` is the daily refresh as games finish
    during the day (live.py): ESPN only (the men's Torvik download stays with the overnight job), completed seasons
    from the cache (memo.py), no training, and the slow pieces a game doesn't move (prospects) are left as they are."""
    t0 = time.time()
    if mode == "publish":  # re-publish from cached features (fast iteration)
        stage_publish(*load_cached())
        log(f"finished in {time.time() - t0:.0f}s")
        return
    is_live = mode == "live"
    if is_live:
        import os
        os.environ["HOOP2VEC_FORCE_ESPN"] = "1"  # men's: the current season from ESPN on top of the Torvik snapshot
    games, meta, pg, ps, conf = stage_data(refresh=mode != "offline", refit=mode == "all")
    feat, snaps, live, extras, inj = stage_features(games, meta, pg, ps, conf)
    if mode in ("train", "all") or not NET_PATH.exists() or not (ARTIFACTS / "linear.pkl").exists():
        if is_live:
            raise SystemExit("live mode needs the trained models (artifacts/); run the daily job first")
        stage_train(feat)
    if mode in ("backtest", "all"):
        stage_backtest(feat)
        from .config import PRO_LEAGUE
        if not PRO_LEAGUE:
            from . import prospects as PRO
            PRO.run_backtest(PRO.build_table()[0])
    if not (feat["season"].eq(current_season()) & feat["played"]).any():
        log("no games played yet this season - keeping last published site")
        return
    stage_publish(games, meta, pg, conf, feat, snaps, live, extras, inj, is_live=is_live)
    log(f"finished in {time.time() - t0:.0f}s")

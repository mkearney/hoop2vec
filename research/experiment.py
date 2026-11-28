"""Run one modelling experiment on the season-ahead backtest, compare it with a baseline run, and log it.

    python research/experiment.py NAME [--womens|--nba|--wnba] [--param KEY=VALUE ...] [--baseline NAME]
                                       [--train-realized] [--nets 3] [--note "..."]

Features are rebuilt from the local data cache (data/<league>, filled by any normal run) for completed seasons only,
so the current season never enters. Every test season is predicted by models trained on earlier seasons
(hoop2vec/model/backtest.py), and the blend weight for each season comes from earlier test seasons only.

Results are split three ways: "dev" (every test season but the last two: decide on these), "holdout" (the last two:
confirm on these, rarely), and "all". Predictions go to research/results/<league>/NAME.parquet (not committed) and a
summary line to research/experiments.jsonl (committed), with paired 95% intervals against the baseline run. A change
is worth keeping when its dev interval excludes zero and the holdout doesn't disagree.

--param overrides one dynamic-rating setting for this run (see hoop2vec/ratings/kalman.py RatingParams), e.g.
--param prior_w=1 --param luck3_d=0.25. --set overrides a module constant for this run, e.g.
--set ratings.efficiency.HALF_LIFE_DAYS=60 or --set players.K_MIN=200 (module path under hoop2vec).
--train-realized trains on realized availability (the old behaviour). --nets 0 skips the matchup network (fast).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
LOG = ROOT / "research" / "experiments.jsonl"


def git_rev() -> str:
    try:
        rev = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "hoop2vec"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
        return rev + ("+dirty" if dirty else "")
    except Exception:
        return "unknown"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("name")
    for lg in ("mens", "womens", "nba", "wnba"):
        ap.add_argument(f"--{lg}", dest="league", action="store_const", const=lg)
    ap.add_argument("--param", action="append", default=[], help="dynamic-rating setting KEY=VALUE for this run")
    ap.add_argument("--set", action="append", default=[], help="module constant MODULE.NAME=VALUE for this run")
    ap.add_argument("--baseline", default="baseline", help="earlier run to compare against (default: baseline)")
    ap.add_argument("--train-realized", action="store_true", help="train on realized availability (old behaviour)")
    ap.add_argument("--nets", type=int, default=3, help="matchup networks per test season (stage_backtest uses 3)")
    ap.add_argument("--note", default="")
    args = ap.parse_args()
    league = args.league or "mens"

    # the league and any rating overrides must be in place before hoop2vec is imported
    os.environ["HOOP2VEC_LEAGUE"] = league
    params = {k: float(v) for k, v in (x.split("=", 1) for x in args.param)}
    if params:
        os.environ["HOOP2VEC_RATING_PARAMS"] = json.dumps(params)

    import numpy as np
    import pandas as pd

    from hoop2vec import features as F
    from hoop2vec import lines as L
    from hoop2vec import players as PL
    from hoop2vec.config import FIRST_SEASON, FIRST_TRAIN_SEASON, PROC, current_season
    from hoop2vec.model import backtest as BT
    from hoop2vec.pipeline import choose_blend

    import importlib
    sets = {}
    for x in args.set:
        target, val = x.split("=", 1)
        mod, name = target.rsplit(".", 1)
        m = importlib.import_module(f"hoop2vec.{mod}")
        old = getattr(m, name)
        setattr(m, name, type(old)(float(val)) if isinstance(old, (int, float)) else val)
        sets[target] = getattr(m, name)
    BT.TRAIN_PREGAME = not args.train_realized
    HOLD = getattr(BT, "HOLDOUT_SEASONS", 2)
    t0 = time.time()
    cur = current_season()
    seasons = list(range(FIRST_SEASON, cur))
    games = pd.read_parquet(PROC / "games.parquet")
    games = games[games["season"] < cur]
    pg = PL.load_player_games(seasons, refresh_current=False)
    ps = PL.load_player_seasons(seasons, refresh_current=False)
    conf = F.conferences(seasons)
    feat = F.build(games, pg[pg["season"] < cur], ps[ps["season"] < cur], conf,
                   prior_games=getattr(F, "PRIOR_GAMES", 5.0))[0]
    print(f"features: {len(feat):,} games ({time.time() - t0:.0f}s)", flush=True)

    test = list(range(FIRST_TRAIN_SEASON + 2 if FIRST_TRAIN_SEASON >= 2015 else 2012, cur))
    models = {"elo": lambda: BT.OLSNormal("elo"), "efficiency": lambda: BT.OLSNormal("efficiency"),
              "kalman": lambda: BT.OLSNormal("kalman"), "linear": lambda: BT.LinearGame()}
    if args.nets > 0:
        models["net"] = lambda: BT.Net(n_models=args.nets)
    P = BT.run(feat, models, test, lines=L.for_games(feat, games, L.collect()), verbose=False)
    names = list(models)
    if args.nets > 0:
        choose_blend(P)
        names.append("blend")
    res = BT.summarize(P, names, ref="linear")

    out = ROOT / "research" / "results" / league
    out.mkdir(parents=True, exist_ok=True)
    keep = ["season", "date", "gid", "margin", "is_ncaa"] + [c for c in P.columns if c.startswith(("mu_", "sg_", "p_"))]
    P[keep].to_parquet(out / f"{args.name}.parquet", index=False)

    y = (P["margin"] > 0).to_numpy()
    hold = P["season"].isin(test[-HOLD:]).to_numpy()
    split = {"dev": ~hold, "holdout": hold, "all": np.ones(len(P), bool)}
    summary = {k: {s: round(BT.log_loss(y[m], P.loc[m, f"p_{k}"]), 5) for s, m in split.items()} for k in names}
    for k in names:
        summary[k]["accuracy"] = round(float(np.mean((P[f"p_{k}"] > 0.5) == y)), 5)

    vs = {}
    base_f = out / f"{args.baseline}.parquet"
    if base_f.exists() and args.baseline != args.name:
        B = pd.read_parquet(base_f)
        M = P[["season", "gid", "margin"] + [f"p_{k}" for k in names]].merge(
            B[["season", "gid"] + [f"p_{k}" for k in names if f"p_{k}" in B]], on=["season", "gid"], suffixes=("", "_base"))
        hm = M["season"].isin(test[-HOLD:]).to_numpy()
        for k in names:
            if f"p_{k}_base" not in M:
                continue
            vs[k] = {}
            for s, m in (("dev", ~hm), ("holdout", hm)):
                d, lo, hi = BT.paired_ci(M.loc[m, "margin"], M.loc[m, f"p_{k}"].to_numpy(), M.loc[m, f"p_{k}_base"].to_numpy())
                vs[k][s] = [round(d, 5), round(lo, 5), round(hi, 5)]

    bench = {}
    for sub, key in (("book_line_games", "market"), ("torvik_games", "torvik")):
        if sub in res and key in res[sub]:
            bench[sub] = {"n": res[sub][key]["n"], key: round(res[sub][key]["log_loss"], 5),
                          names[-1]: round(res[sub][names[-1]]["log_loss"], 5)}

    print(f"\n{args.name} ({league}), test seasons {test[0]}-{test[-1]}, holdout {test[-HOLD:]}")
    print(f"{'model':11s} {'dev':>8s} {'holdout':>8s} {'all':>8s} {'acc':>7s}   vs {args.baseline}: dev [95% CI] / holdout")
    for k in names:
        r = summary[k]
        line = f"{k:11s} {r['dev']:8.4f} {r['holdout']:8.4f} {r['all']:8.4f} {r['accuracy']:7.4f}"
        if k in vs:
            dv, ho = vs[k]["dev"], vs[k]["holdout"]
            line += f"   {dv[0]:+.4f} [{dv[1]:+.4f}, {dv[2]:+.4f}] / {ho[0]:+.4f}"
        print(line)
    for sub, b in bench.items():
        print(f"{sub}: n={b['n']} " + " ".join(f"{k}={v}" for k, v in b.items() if k != "n"))
    print(f"({time.time() - t0:.0f}s)")

    rec = {"name": args.name, "league": league, "when": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M"),
           "code": git_rev(), "params": params, "set": sets, "train_realized": args.train_realized, "nets": args.nets,
           "note": args.note, "test_seasons": [test[0], test[-1]], "holdout": test[-HOLD:],
           "log_loss": summary, "vs_baseline": {"baseline": args.baseline, **vs} if vs else None, "benchmarks": bench}
    with open(LOG, "a") as fh:
        fh.write(json.dumps(rec) + "\n")


if __name__ == "__main__":
    main()

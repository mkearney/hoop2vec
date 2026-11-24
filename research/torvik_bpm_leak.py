"""Evidence for the leak: what Torvik's per-game player plus/minus is made of.

Team-level game value S = 5 x minute-weighted mean player o/d-BPM in a game. Regress it on the game's raw
box-score efficiency, the opponent's end-of-season T-Rank rating and the team's OWN end-of-season rating.
Result (2017/2019/2023): R^2 rises from ~0.91 to ~0.998 once the team's own final rating is included, with
weights of about 0.52 (raw), 0.48 (opponent final), 0.52 (own final). So every early-season game value already
contains about half of the team's end-of-season strength.

python research/torvik_bpm_leak.py
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hoop2vec import history  # noqa: E402

conf = pd.read_parquet(history.path("conf"))
rows = []
for season in (2017, 2019, 2023):
    g = pd.read_parquet(history.path("tv_games", season))
    g = g[(g.type != "nond1") & g.played & g.poss.notna()]
    tv = conf[conf.season == season].drop_duplicates("team").set_index("team")
    mo, md = tv.t_adjoe.mean(), tv.t_adjde.mean()
    pg = history.load_pg(season)
    pg = pg[pg["min"] > 0]
    agg = (pg.assign(wo=pg["min"] * pg.obpm, wd=pg["min"] * pg.dbpm).groupby(["muid", "team"])
           .agg(wo=("wo", "sum"), wd=("wd", "sum"), m=("min", "sum")).reset_index())
    agg["So"], agg["Sd"] = 5 * agg.wo / agg.m, 5 * agg.wd / agg.m
    for me, op in (("a", "b"), ("b", "a")):
        d = pd.DataFrame({"gid": g.gid, "team": g[f"team_{me}"], "opp": g[f"team_{op}"],
                          "raw_o": 100 * g[f"pts_{me}"] / g.poss, "raw_d": -100 * g[f"pts_{op}"] / g.poss})
        d = d.merge(agg, left_on=["gid", "team"], right_on=["muid", "team"])
        d["own_o"], d["own_d"] = d.team.map(tv.t_adjoe) - mo, md - d.team.map(tv.t_adjde)
        d["opp_o"], d["opp_d"] = d.opp.map(tv.t_adjoe) - mo, md - d.opp.map(tv.t_adjde)
        rows.append(d)
d = pd.concat(rows).dropna()
for y, cols in (("So", ["raw_o", "opp_d"]), ("So", ["raw_o", "opp_d", "own_o"]),
                ("Sd", ["raw_d", "opp_o"]), ("Sd", ["raw_d", "opp_o", "own_d"])):
    X = np.c_[np.ones(len(d)), d[cols].to_numpy()]
    b, *_ = np.linalg.lstsq(X, d[y].to_numpy(), rcond=None)
    r = d[y] - X @ b
    print(f"{y} ~ {' + '.join(cols):28s} weights {np.round(b[1:], 3)}  R2 {1 - r.var() / d[y].var():.4f}")

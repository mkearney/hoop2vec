"""Personnel / rotation features per team-season (as of a date).

ESPN player box + on-court stints -> usage structure, depth, lineup stability, star on/off.
Barttorvik player-season          -> size, role mix, rim protector, experience, recruiting talent.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

GUARD_ROLES = {"Pure PG", "Scoring PG", "Combo G", "Wing G"}
WING_ROLES = {"Wing G", "Wing F", "Stretch 4"}
BIG_ROLES = {"PF/C", "C"}

ROSTER_FEATURES = {
    # name: (label, block)   block "roster" feeds personnel identity; "talent" is shown but kept out of style
    "usage_hhi":     ("Usage concentration (HHI)", "roster"),
    "top_usage":     ("Top player's usage share", "roster"),
    "ast_conc":      ("Share of assists by top playmaker", "roster"),
    "eff_rotation":  ("Effective rotation size", "roster"),
    "bench_min":     ("Bench minutes share", "roster"),
    "lineup_conc":   ("Minutes in top-5 lineups", "roster"),
    "star_onoff":    ("Star on/off net swing (shrunk, per 100)", "roster"),
    "guard_usage":   ("Usage share by guards", "roster"),
    "big_three":     ("Bigs' 3PA share of their FGA", "roster"),
    "guard_reb":     ("Rebound share by guards", "roster"),
    "height":        ("Minutes-weighted height (in)", "roster"),
    "front_height":  ("Frontcourt height (top-2 bigs, in)", "roster"),
    "height_spread": ("Height spread in rotation (in)", "roster"),
    "wing_share":    ("Minutes to wings / stretch 4s", "roster"),
    "big_share":     ("Minutes to traditional bigs", "roster"),
    "rim_protector": ("Best rim protector block%", "roster"),
    "experience":    ("Experience (min-weighted class)", "talent"),
    "recruit":       ("Recruiting pedigree (min-weighted)", "talent"),
    "bpm_top3":      ("Top-3 player BPM (talent)", "talent"),
}


def espn_roster(pg: pd.DataFrame, lineups: pd.DataFrame, game_ids: set) -> pd.DataFrame:
    pg = pg[pg["game_id"].isin(game_ids) & (pg["min"] > 0)].copy()
    # drop games whose box score is incomplete (missing players): team minutes must look like a full game
    tm = pg.groupby(["team_id", "game_id"])["min"].transform("sum")
    pg = pg[(tm >= 185) & (tm <= 330)]
    pg["use"] = pg["fga"] + 0.44 * pg["fta"] + pg["tov"]
    pg["reb"] = pg["oreb"] + pg["dreb"]
    pg["guard"] = pg["pos"].astype(str).str.startswith("G")
    pg["big"] = pg["pos"].astype(str).isin(["F", "C", "F-C"])
    for c in ("p_on_off_poss", "p_on_def_poss", "p_on_pts_for", "p_on_pts_against", "p_on_sec"):
        if c not in pg:
            pg[c] = 0.0
        pg[c] = pg[c].fillna(0)
    P = pg.groupby(["team_id", "athlete_id"]).agg(
        name=("name", "first"), min=("min", "sum"), use=("use", "sum"), ast=("ast", "sum"), reb=("reb", "sum"), fga=("fga", "sum"),
        tpa=("tpa", "sum"), guard=("guard", "max"), big=("big", "max"), starter_min=("min", lambda s: 0),
        on_op=("p_on_off_poss", "sum"), on_dp=("p_on_def_poss", "sum"), on_pf=("p_on_pts_for", "sum"),
        on_pa=("p_on_pts_against", "sum")).reset_index()
    bench = pg[~pg["starter"]].groupby("team_id")["min"].sum()
    rows = []
    # team totals from stints: max over players of (on + off) isn't available, use game-level sums
    gl = pg.groupby(["team_id", "game_id"]).agg(pf=("p_on_pts_for", "sum"), pa=("p_on_pts_against", "sum"),
                                                 op=("p_on_off_poss", "sum"), dp=("p_on_def_poss", "sum"))
    team_tot = (gl / 5).groupby("team_id").sum()
    for tid, t in P.groupby("team_id"):
        tm = t["min"].sum()
        if tm <= 0:
            continue
        ms = t["min"] / tm
        us = t["use"] / max(t["use"].sum(), 1)
        star = t.loc[(t["use"] * t["min"]).idxmax()]
        tt = team_tot.loc[tid] if tid in team_tot.index else None
        onoff = np.nan
        if tt is not None and star["on_op"] > 0:
            off_op, off_dp = tt["op"] - star["on_op"], tt["dp"] - star["on_dp"]
            if off_op > 5 and off_dp > 5:
                on_net = 100 * (star["on_pf"] / star["on_op"] - star["on_pa"] / max(star["on_dp"], 1))
                off_net = 100 * ((tt["pf"] - star["on_pf"]) / off_op - (tt["pa"] - star["on_pa"]) / off_dp)
                k = (off_op + off_dp) / 2
                onoff = (on_net - off_net) * k / (k + 250.0)  # shrink toward 0
        bigs = t[t["big"]]
        rows.append(dict(
            team_id=tid, star_name=star["name"], star_usage=float(us.loc[star.name]), usage_hhi=float((us ** 2).sum()), top_usage=float(us.max()),
            ast_conc=float(t["ast"].max() / max(t["ast"].sum(), 1)),
            eff_rotation=float(1 / (ms ** 2).sum()), bench_min=float(bench.get(tid, 0) / tm),
            star_onoff=onoff, guard_usage=float(us[t["guard"]].sum()),
            big_three=float(bigs["tpa"].sum() / max(bigs["fga"].sum(), 1)) if len(bigs) else np.nan,
            guard_reb=float(t.loc[t["guard"], "reb"].sum() / max(t["reb"].sum(), 1)),
        ))
    out = pd.DataFrame(rows)
    lu = lineups[lineups["game_id"].isin(game_ids)]
    if len(lu):
        agg = lu.groupby(["team_id", "lineup"])["sec"].sum().reset_index()
        conc = agg.groupby("team_id")["sec"].apply(lambda s: s.nlargest(5).sum() / max(s.sum(), 1))
        out["lineup_conc"] = out["team_id"].map(conc)
    else:
        out["lineup_conc"] = np.nan
    return out


def torvik_roster(tp: pd.DataFrame, xw: pd.DataFrame) -> pd.DataFrame:
    tp = tp.merge(xw[["torvik", "team_id"]], left_on="team", right_on="torvik", how="inner")
    tp = tp[tp["minutes"] > 0]
    rows = []
    for tid, t in tp.groupby("team_id"):
        w = t["minutes"] / t["minutes"].sum()
        rot = t[t["min_pct"] >= 30]
        hts = rot["ht_in"].dropna()
        bigs = rot[rot["role"].isin(BIG_ROLES | {"Stretch 4"})].nlargest(2, "minutes")
        hw = w[t["ht_in"].notna()]
        rows.append(dict(
            team_id=tid,
            height=float((t["ht_in"] * w).sum() / hw.sum()) if hw.sum() > 0 else np.nan,
            front_height=float(np.average(bigs["ht_in"].fillna(hts.mean() if len(hts) else t["ht_in"].mean()),
                                          weights=bigs["minutes"])) if len(bigs) else np.nan,
            height_spread=float(hts.std()) if len(hts) > 2 else np.nan,
            wing_share=float(w[t["role"].isin(WING_ROLES)].sum()),
            big_share=float(w[t["role"].isin(BIG_ROLES)].sum()),
            rim_protector=float(t.loc[t["min_pct"] >= 40, "blk_pct"].max()) if (t["min_pct"] >= 40).any() else 0.0,
            experience=float((t["class_n"].fillna(2.5) * w).sum()),
            recruit=float((t["rec_score"].fillna(0) * w).sum()),
            bpm_top3=float(t.loc[t["min_pct"] >= 25, "bpm"].nlargest(3).mean()) if (t["min_pct"] >= 25).any() else np.nan,
        ))
    return pd.DataFrame(rows)

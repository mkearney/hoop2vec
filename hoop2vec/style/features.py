"""Team-game style features.

Every adjustable feature is written from the OFFENSE's point of view as numerator / denominator on the
offense row of a game (using the defending team's row where needed, e.g. blocks). Opponent adjustment
then yields, per team:
  off_<feat>  what this team's offense does (adjusted for defenses faced)
  def_<feat>  what offenses do against this team's defense (adjusted for offenses faced)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..config import GAME_MINUTES

# name: (numerator expr, denominator expr, block, kind, label)
#   block: which identity the off/def parameters feed. "both" = off param -> offense, def param -> defense.
#   kind: "rate" (binomial-ish proportion) or "mean" (continuous average)
FEATURES = {
    # --- pace & possession
    "poss_len":      ("p_poss_len_sum", "p_poss_len_n", "both", "mean", "Avg possession length (s)"),
    "trans_rate":    ("p_trans_a", "p_poss", "both", "rate", "Transition shots per possession"),
    "trans_drb":     ("p_trans_drb_a", "p_poss_drb", "both", "rate", "Push after defensive rebounds"),
    "trans_steal":   ("p_trans_steal_a", "p_poss_steal", "both", "rate", "Run-outs after steals"),
    "trans_make":    ("p_trans_make_a", "p_poss_make", "both", "rate", "Push after opponent makes"),
    "late_clock":    ("p_late_a", "p_timed_fga", "both", "rate", "Late-clock shot share"),
    # --- shot profile
    "rim_rate":      ("p_rim_a", "p_fga", "both", "rate", "Rim attempt share"),
    "mid_rate":      ("p_mid_a", "p_fga", "both", "rate", "Mid-range (2PT jumper) share"),
    "smid_rate":     ("p_smid_a", "p_fga", "both", "rate", "Short mid-range (5-14 ft) share"),
    "lmid_rate":     ("p_lmid_a", "p_fga", "both", "rate", "Long mid-range share"),
    "three_rate":    ("tpa", "fga", "both", "rate", "3PA rate"),
    "c3_share":      ("p_c3_a", "p_three_a", "both", "rate", "Corner share of 3PA"),
    "dunk_rate":     ("p_dunk_a", "p_fga", "both", "rate", "Dunk share of FGA"),
    "ft_rate":       ("fta", "fga", "both", "mean", "Free throw rate (FTA/FGA)"),
    "two_dist":      ("p_two_dist_sum", "p_two_dist_n", "both", "mean", "Avg 2PT shot distance (ft)"),
    # --- creation & ball movement
    "ast_fgm":       ("ast", "fgm", "both", "rate", "Assisted share of makes"),
    "ast_rim":       ("p_rim_ast", "p_rim_m", "both", "rate", "Assisted share at rim"),
    "ast_mid":       ("p_mid_ast", "p_mid_m", "both", "rate", "Assisted share mid-range"),
    "ast_three":     ("p_three_ast", "p_three_m", "both", "rate", "Assisted share of 3s"),
    "pullup_rate":   ("p_pullup_a", "p_fga", "both", "rate", "Pull-up jumper share"),
    "iso_rate":      ("p_iso_a", "p_fga", "both", "rate", "Step-back/turnaround/fade share (iso proxy)"),
    "drive_rate":    ("p_drive_a", "p_fga", "both", "rate", "Driving layup/floater share"),
    "hook_rate":     ("p_hook_a", "p_fga", "both", "rate", "Hook shot share (post-up proxy)"),
    "lob_rate":      ("p_alleyoop_a", "p_fga", "both", "rate", "Alley-oop share (PnR/lob proxy)"),
    # --- possession management & disruption
    "tov_rate":      ("tov", "poss", "both", "rate", "Turnover rate"),
    "live_tov":      ("p_tov_live", "poss", "both", "rate", "Live-ball turnovers per possession"),
    "press_tov":     ("p_tov_press_early", "poss", "both", "rate", "Backcourt/early turnovers per poss (press proxy)"),
    "shotclock_tov": ("p_tov_shotclock", "poss", "both", "rate", "Shot-clock violations per poss"),
    "blocked":       ("opp_blk", "two_a", "both", "rate", "Share of 2PA blocked"),
    "fouled":        ("opp_p_def_fouls", "poss", "both", "rate", "Defensive fouls drawn per poss"),
    "fouled_early":  ("opp_p_def_fouls_early", "poss", "both", "rate", "Early-clock fouls drawn per poss (pressure proxy)"),
    # --- rebounding
    "orb_rate":      ("oreb", "orb_chances", "both", "rate", "Offensive rebound rate"),
    "putback_rate":  ("p_putback_a", "p_orb_pbp", "both", "rate", "Putback attempts per OREB"),
}

# execution / efficiency (opponent-adjusted the same way; used for quality + strengths/vulnerabilities)
EXECUTION = {
    "ppp":       ("pts", "poss", "mean", "Points per possession"),
    "efg":       ("efg_pts", "fga", "mean", "Effective FG%"),
    "rim_fg":    ("p_rim_m", "p_rim_a", "rate", "Rim FG%"),
    "mid_fg":    ("p_mid_m", "p_mid_a", "rate", "Mid-range FG%"),
    "three_fg":  ("tpm", "tpa", "rate", "3P%"),
    "ft_pct":    ("ftm", "fta", "rate", "FT%"),
    "trans_ppp": ("p_trans_pts", "trans_poss", "mean", "Points per transition chance"),
    "late_fg":   ("p_late_m", "p_late_a", "rate", "Late-clock FG%"),
}


_Z = ["rim", "smid", "lmid", "c3", "ab3"]
_K = ["dunk", "tip", "layup", "hook", "jumper", "pullup", "stepback", "turnaround", "driving", "floater", "alleyoop"]
PBP_COLS = ([f"p_{z}_{x}" for z in _Z for x in ("a", "m", "ast", "unast")] + [f"p_{k}_{x}" for k in _K for x in ("a", "m")] +
            ["p_" + c for c in ("fga", "fgm", "poss", "poss_make", "poss_drb", "poss_steal", "poss_deadball", "poss_unknown",
                                "poss_len_sum", "poss_len_n", "trans_a", "trans_m", "trans_pts", "trans_rim_a", "trans_drb_a",
                                "trans_steal_a", "trans_make_a", "trans_deadball_a", "trans_ft_trips", "late_a", "late_m",
                                "timed_fga", "shot_t_sum", "two_dist_sum", "two_dist_n", "putback_a", "putback_m", "orb_pbp",
                                "drb_pbp", "tov_pbp", "tov_live", "tov_dead", "tov_shotclock", "tov_travel", "tov_offfoul",
                                "tov_press", "tov_early", "stl_pbp", "blk_pbp", "fouls_pbp", "def_fouls", "def_fouls_early",
                                "off_fouls", "timeouts", "ft_a", "ft_m", "ft_trips", "and1", "tech_fta")])


def team_game_table(tg: pd.DataFrame, games: pd.DataFrame, d1: set[str]) -> pd.DataFrame:
    """One row per (game, offense team) with all numerators/denominators; D-I vs D-I only."""
    tg = tg.copy()
    tg = tg[tg["team_id"].isin(d1) & tg["opp_id"].isin(d1)]
    missing = [c for c in PBP_COLS if c not in tg]
    if missing:
        tg = pd.concat([tg, pd.DataFrame(0.0, index=tg.index, columns=missing)], axis=1)
    g = games.set_index("game_id")
    for c in [c for c in tg.columns if c.startswith("p_")]:
        tg[c] = tg[c].fillna(0.0)
    opp = tg.set_index(["game_id", "team_id"])
    o = opp.reindex(pd.MultiIndex.from_arrays([tg["game_id"], tg["opp_id"]]))
    o.index = tg.index
    tg["opp_blk"] = o["blk"].values
    tg["opp_dreb"] = o["dreb"].values
    tg["opp_p_def_fouls"] = o["p_def_fouls"].values
    tg["opp_p_def_fouls_early"] = o["p_def_fouls_early"].values
    tg = tg[tg["opp_dreb"].notna()]
    tg["poss"] = (tg["fga"] - tg["oreb"] + tg["tov"] + 0.44 * tg["fta"]).clip(lower=40)
    o_poss = (o["fga"] - o["oreb"] + o["tov"] + 0.44 * o["fta"]).clip(lower=40)
    tg["game_poss"] = 0.5 * (tg["poss"] + o_poss.loc[tg.index])
    from ..config import REG_PERIODS
    n_ot = games.set_index("game_id").reindex(tg["game_id"])["n_periods"].fillna(REG_PERIODS).values - REG_PERIODS
    tg["minutes"] = GAME_MINUTES + 5 * np.clip(n_ot, 0, None)
    tg["tempo"] = tg["game_poss"] * GAME_MINUTES / tg["minutes"]
    tg["two_a"] = tg["fga"] - tg["tpa"]
    tg["orb_chances"] = tg["oreb"] + tg["opp_dreb"]
    tg["p_three_a"] = tg["p_c3_a"] + tg["p_ab3_a"]
    tg["p_three_m"] = tg.get("p_c3_m", 0) + tg.get("p_ab3_m", 0)
    tg["p_three_ast"] = tg.get("p_c3_ast", 0) + tg.get("p_ab3_ast", 0)
    tg["p_mid_a"] = tg.get("p_smid_a", 0) + tg.get("p_lmid_a", 0)
    tg["p_mid_m"] = tg.get("p_smid_m", 0) + tg.get("p_lmid_m", 0)
    tg["p_mid_ast"] = tg.get("p_smid_ast", 0) + tg.get("p_lmid_ast", 0)
    tg["p_iso_a"] = tg.get("p_stepback_a", 0) + tg.get("p_turnaround_a", 0)
    tg["p_drive_a"] = tg.get("p_driving_a", 0) + tg.get("p_floater_a", 0)
    tg["p_tov_press_early"] = tg.get("p_tov_press", 0) + tg.get("p_tov_early", 0)
    tg["efg_pts"] = tg["fgm"] + 0.5 * tg["tpm"]
    tg["trans_poss"] = tg.get("p_trans_a", 0) + tg.get("p_trans_ft_trips", 0)
    for c in ["p_poss_len_sum", "p_poss_len_n", "p_trans_a", "p_poss", "p_trans_drb_a", "p_poss_drb",
              "p_trans_steal_a", "p_poss_steal", "p_trans_make_a", "p_poss_make", "p_late_a", "p_timed_fga",
              "p_rim_a", "p_fga", "p_smid_a", "p_lmid_a", "p_c3_a", "p_dunk_a", "p_two_dist_sum", "p_two_dist_n",
              "p_rim_ast", "p_rim_m", "p_pullup_a", "p_hook_a", "p_alleyoop_a", "p_tov_live", "p_tov_shotclock",
              "p_putback_a", "p_orb_pbp", "p_trans_pts", "p_late_m"]:
        if c not in tg:
            tg[c] = 0.0
    tg["date"] = pd.to_datetime(g.reindex(tg["game_id"])["date"].values)
    tg["season_type"] = g.reindex(tg["game_id"])["season_type"].values
    tg["pbp"] = g.reindex(tg["game_id"])["pbp"].fillna(False).astype(bool).values
    # games with broken PBP (very few shots parsed vs box FGA) can't feed PBP features
    tg["pbp_ok"] = tg["pbp"] & (tg["p_fga"] >= 0.9 * tg["fga"]) & (tg["p_fga"] <= 1.1 * tg["fga"] + 1)
    tg["home"] = np.where(tg["neutral"], 0, np.where(tg["is_home"], 1, -1))
    return tg.reset_index(drop=True)


def is_pbp_feature(num: str, den: str) -> bool:
    return num.startswith(("p_", "opp_p_")) or den.startswith("p_")

"""Archived injury reports: status calibration and the pre-game availability override."""
import numpy as np
import pandas as pd

import hoop2vec.config as C
from hoop2vec import players as PL
from hoop2vec.sources import injuries as INJ


def _archive(tmp_path, rows):
    d = pd.DataFrame(rows)
    d.to_parquet(tmp_path / "injuries_2027.parquet")
    return d


def test_status_rates_and_load_archive(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "HIST", tmp_path)
    monkeypatch.setattr(INJ, "MIN_STATUS_CASES", 2)
    days = ["2026-11-20", "2026-11-21", "2026-11-22", "2026-11-23"]
    _archive(tmp_path, [{"date": d, "team": "Duke", "pid": 7, "player": "A B", "status": "Questionable",
                         "injury": "", "avail": 0.5, "source": "covers"} for d in days]
             + [{"date": days[0], "team": "Duke", "pid": 8, "player": "C D", "status": "Out", "injury": "",
                 "avail": 0.0, "source": "covers"},
                {"date": days[0], "team": "Duke", "pid": 8, "player": "C D", "status": "out", "injury": "",
                 "avail": 0.3, "source": "manual"}])
    # Duke played every day; player 7 played on 3 of the 4 days
    pg = pd.DataFrame({"date": days * 2, "team": "Duke", "pid": [7] * 4 + [9] * 4,
                       "min": [20, 0, 25, 30] + [10] * 4})
    rates = INJ.status_rates(pg)
    assert abs(rates["questionable"] - 0.75) < 1e-9
    assert "out" not in rates  # one feed case only (the manual row is left out)
    a = INJ.load_archive(pg)
    assert set(a.columns) == {"season", "date", "team", "pid", "avail"}
    q = a[a["pid"] == 7]["avail"].unique()
    assert np.allclose(q, 0.75)  # measured rate replaces the fixed 0.5
    assert a.loc[a["pid"] == 8, "avail"].item() == 0.3  # the hand-entered override wins


def test_report_overrides_pregame_availability():
    games = pd.DataFrame({"season": 2027, "date": pd.to_datetime(["2026-11-10", "2026-11-12", "2026-11-14"]),
                          "gid": ["g1", "g2", "g3"], "team_a": "Duke", "team_b": ["X", "Y", "Z"], "played": True,
                          "minutes": 200.0, "type": "reg"})
    pg = pd.DataFrame({"season": 2027, "team": "Duke", "pid": [1, 1, 1, 2, 2, 2], "muid": ["g1", "g2", "g3"] * 2,
                       "min": [30.0, 30.0, 30.0, 20.0, 20.0, 20.0], "bpm": [8.0] * 3 + [0.0] * 3,
                       "obpm": [5.0] * 3 + [0.0] * 3, "dbpm": [3.0] * 3 + [0.0] * 3})
    pri = pd.DataFrame({"season": 2027, "pid": [1, 2], "prior_v": [5.0, 0.0], "prior_o": [3.0, 0.0],
                        "prior_d": [2.0, 0.0], "inches": [78.0, 80.0], "experience": [3.0, 2.0],
                        "returning": [True, True]})
    base = pd.DataFrame(PL._season_player_features(games, pg, pri, {}, 2027)[0], columns=PL._PF_COLS)
    rep = pd.DataFrame({"season": [2027], "date": ["2026-11-14"], "team": ["Duke"], "pid": [1], "avail": [0.0]})
    withrep = pd.DataFrame(PL._season_player_features(games, pg, pri, {}, 2027, rep)[0], columns=PL._PF_COLS)
    b = base.set_index(["gid", "team"])["avail_delta_pre"]
    w = withrep.set_index(["gid", "team"])["avail_delta_pre"]
    assert np.isclose(b[("g2", "Duke")], w[("g2", "Duke")])  # no report that day: unchanged
    assert w[("g3", "Duke")] < b[("g3", "Duke")] - 1.0  # the star reported out: the team is weaker before tip-off

"""Player pages: shot parsing, zone schemes, shot cells, ESPN -> Torvik matching and the per-team export."""
import json

import pandas as pd


def test_shot_info_keeps_court_location():
    from hoop2vec.sources.pbp import shot_info
    s = shot_info({"text": "X makes 25-foot three point jumper", "coordinate": {"x": 15, "y": 23}})
    assert (s["x"], s["y"], s["zone"], s["three"]) == (15, 23, "ab3", True)
    c = shot_info({"text": "X misses 23-foot three point jumper", "coordinate": {"x": 2, "y": 3}})
    assert c["zone"] == "c3"
    # ESPN's placeholder coordinates (free throws, untracked shots) are dropped
    bad = shot_info({"text": "X makes 12-foot jumper", "coordinate": {"x": -214748340, "y": -214748365}})
    assert bad["x"] is None and bad["y"] is None and bad["zone"] == "smid"


def test_scheme_follows_what_espn_recorded():
    from hoop2vec.careers import collapse, scheme
    full = {"rim": [300, 180], "smid": [150, 60], "lmid": [100, 38], "c3": [60, 23], "ab3": [240, 82]}
    assert scheme(full) == "full"
    no_coords = dict(full, c3=[0, 0], ab3=[300, 105])           # every three looks above the break
    assert scheme(no_coords) == "dist"
    no_dist = dict(no_coords, smid=[0, 0], lmid=[250, 98])      # every non-rim two looks long
    assert scheme(no_dist) == "coarse"
    assert collapse(full, "full") == full
    assert collapse(full, "dist") == {"rim": [300, 180], "smid": [150, 60], "lmid": [100, 38], "three": [300, 105]}
    assert collapse(full, "coarse") == {"rim": [300, 180], "mid": [250, 98], "three": [300, 105]}
    assert "c3" not in collapse(dict(full, c3=[0, 0]), "full")   # empty zones are left out


def test_zone_counts_sum_play_by_play_columns():
    from hoop2vec.careers import zone_counts
    pg = pd.DataFrame({"athlete_id": ["1", "1", "2", "3"], "team_id": ["9", "9", "9", "9"],
                       "p_rim_a": [3, 2, 1, None], "p_rim_m": [2, 1, 0, None], "p_ab3_a": [4, 0, 0, None],
                       "p_ab3_m": [1, 0, 0, None]})
    z = zone_counts(pg).set_index("athlete_id")
    assert z.loc["1", "rim_a"] == 5 and z.loc["1", "rim_m"] == 3 and z.loc["1", "ab3_a"] == 4
    assert z.loc["1", "c3_a"] == 0              # columns ESPN never produced count as zero
    assert "3" not in z.index                   # no attempts, no row


def test_shot_bins_aggregate_one_foot_cells():
    from hoop2vec.careers import _merge_cells, shot_bins
    sh = pd.DataFrame({"athlete_id": ["7", "7", "7", "7", "8"], "x": [25, 25, 3, 25, 10], "y": [0, 0, 2, 45, 20],
                       "made": [True, False, True, True, False], "zone": ["rim", "rim", "c3", "ab3", "lmid"]})
    b = shot_bins(sh)
    assert sorted(b["7"]) == [[3, 2, 3, 1, 1], [25, 0, 0, 2, 1]]   # the 45-foot heave is off the chart
    assert b["8"] == [[10, 20, 2, 1, 0]]
    assert sorted(_merge_cells([[25, 0, 0, 2, 1]], [[25, 0, 0, 3, 3], [1, 1, 3, 1, 0]])) == [[1, 1, 3, 1, 0], [25, 0, 0, 5, 4]]


def test_match_espn_names_to_torvik_ids():
    from hoop2vec.careers import match_espn
    ps = pd.DataFrame({"season": [2026, 2026, 2026, 2027], "team": ["Duke", "Duke", "Duke", "Duke"],
                       "player": ["Cooper Flagg Jr.", "Tyrese Proctor", "Sion James", "Tyrese Proctor"],
                       "pid": [11, 12, 13, 12]})
    names = pd.DataFrame({"season": [2026, 2026, 2026, 2027, 2026], "athlete_id": ["a", "b", "c", "b", "d"],
                          "team": ["Duke", "Duke", "Duke", "Duke", "UNC"],
                          "name": ["Cooper Flagg", "T. Proctor", "Sion James", "Tyrese Proctor", "Sion James"]})
    m = match_espn(names, ps)
    assert m == {"a": 11, "b": 12, "c": 13}     # suffix ignored, unique last name on the team, wrong team unmatched


def test_publish_writes_team_files(tmp_path, monkeypatch):
    from hoop2vec import careers, ingest
    from hoop2vec import players as PL
    pbp = tmp_path / "pbp"
    for s, scale in ((2026, 1), (2027, 2)):
        d = pbp / str(s)
        d.mkdir(parents=True)
        pd.DataFrame({"athlete_id": ["101", "102", "900"], "team_id": ["1", "1", "2"], "name": ["A", "B", "Z"],
                      "p_rim_a": [10 * scale, 4, 50], "p_rim_m": [6 * scale, 2, 30], "p_smid_a": [5, 1, 40],
                      "p_smid_m": [2, 0, 18], "p_lmid_a": [3, 0, 30], "p_lmid_m": [1, 0, 12],
                      "p_c3_a": [2, 0, 20], "p_c3_m": [1, 0, 8], "p_ab3_a": [8, 2, 60], "p_ab3_m": [3, 1, 21]}
                     ).to_parquet(d / "player_games.parquet")
    pd.DataFrame({"game_id": ["g"] * 3, "team_id": ["1"] * 3, "athlete_id": ["101", "101", "102"],
                  "x": [25, 25, 40], "y": [1, 1, 20], "made": [True, False, True], "zone": ["rim", "rim", "lmid"]}
                 ).to_parquet(pbp / "2027" / "shots.parquet")
    art = tmp_path / "art"
    art.mkdir()
    pd.DataFrame({"team": ["Aces", "Lynx"], "espn_id": ["1", "2"]}).to_csv(art / "team_meta.csv", index=False)
    site = tmp_path / "site"
    site.mkdir()
    ps = pd.DataFrame({"season": [2026, 2027, 2027], "pid": [101, 101, 102], "team": ["Aces", "Aces", "Aces"],
                       "cls": ["R", "2", "R"], "gp": [30, 10, 5], "mpg": [20.0, 25.0, 8.0], "pts": [300, 150, 10],
                       "fgm": [110, 55, 4], "fga": [240, 120, 10], "tpm": [20, 10, 1], "tpa": [60, 30, 3],
                       "ftm": [60, 30, 1], "fta": [75, 40, 2], "orb": [20, 8, 1], "drb": [60, 30, 4], "ast": [90, 40, 1],
                       "stl": [20, 9, 0], "blk": [5, 2, 0], "bpm": [1.5, 2.5, -3.0]})
    monkeypatch.setattr(ingest, "PBP", pbp)
    monkeypatch.setattr(careers, "PROC", tmp_path / "proc")
    monkeypatch.setattr(careers, "SITE_DATA", site)
    monkeypatch.setattr(careers, "ARTIFACTS", art)
    monkeypatch.setattr(careers, "ESPN_DATA", True)
    monkeypatch.setattr(careers, "FIRST_SEASON", 2026)
    monkeypatch.setattr(careers, "FIRST_PBP_SEASON", 2026)
    monkeypatch.setattr(PL, "load_player_seasons", lambda seasons, refresh_current=True: ps)
    (site / "players").mkdir()
    (site / "players" / "old-team.json").write_text("{}")

    n = careers.publish(pd.DataFrame({"pid": [101, 102], "team": ["Aces", "Aces"]}), 2027)
    assert n == 2
    assert not (site / "players" / "old-team.json").exists()      # stale team files are removed
    d = json.loads((site / "players" / "aces.json").read_text())
    a = d["players"]["101"]
    assert [r["season"] for r in a["career"]] == [2026, 2027]
    assert a["career"][1]["ppg"] == 15.0 and a["career"][1]["fg"] == 45.8
    assert a["zones"]["2027"]["rim"] == [20, 12]
    assert a["shots"]["2027"] == [[25, 1, 0, 2, 1]]
    assert "2026" not in a["shots"]
    lg = json.loads((site / "shots_league.json").read_text())
    assert lg["seasons"]["2027"]["scheme"] == "full" and lg["seasons"]["2027"]["located"] is True
    assert "located" not in lg["seasons"]["2026"]
    assert lg["seasons"]["2027"]["zones"]["rim"] == [74, 44]       # every player, not just this season's rosters

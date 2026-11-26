"""Live updates: the scoreboard check, the overlay used by the Pages deploy, and the completed-season memo."""
import datetime as dt
import json

import numpy as np
import pandas as pd

from hoop2vec import live, memo


def _event(gid, completed, season_type=2):
    return {"id": gid, "season": {"type": season_type},
            "competitions": [{"status": {"type": {"completed": completed}}}]}


def test_new_games_are_finished_games_not_yet_published(tmp_path, monkeypatch):
    monkeypatch.setattr(live, "LIVE", tmp_path)
    monkeypatch.setattr(live, "in_season", lambda now=None: True)
    boards = {dt.date(2026, 11, 25): [_event("1", True), _event("2", True)],
              dt.date(2026, 11, 26): [_event("3", True), _event("4", False)]}
    monkeypatch.setattr(live, "_scoreboard", lambda day: {"events": boards.get(day, [])})
    now = dt.datetime(2026, 11, 26, 21, 0, tzinfo=live.EASTERN)
    assert live.finished_games(now) == ["1", "2", "3"]
    monkeypatch.setattr(live, "finished_games", lambda now=None: ["1", "2", "3"])
    assert live.new_games() == ["1", "2", "3"]           # nothing published yet: catch up
    (tmp_path / live.LEAGUE).mkdir()
    (tmp_path / live.LEAGUE / "seen.json").write_text(json.dumps({"games": ["1", "2"]}))
    assert live.new_games() == ["3"]
    (tmp_path / live.LEAGUE / "seen.json").write_text(json.dumps({"games": ["1", "2", "3"]}))
    assert live.new_games() == []
    monkeypatch.setenv("HOOP2VEC_LIVE_FORCE", "1")       # manual run
    assert live.new_games() == ["1", "2", "3"]


def test_pro_preseason_games_dont_count(monkeypatch):
    monkeypatch.setattr(live, "PRO_LEAGUE", True)
    monkeypatch.setattr(live, "_scoreboard", lambda day: {"events": [_event("9", True, 1), _event("8", True, 3)]})
    assert live.finished_games(dt.datetime(2026, 4, 20, tzinfo=live.EASTERN)) == ["8"]


def test_overlay_uses_live_data_only_when_newer(tmp_path, monkeypatch):
    site, lv = tmp_path / "docs", tmp_path / "live"
    monkeypatch.setattr(live, "SITE_ROOT", site)
    monkeypatch.setattr(live, "LIVE", lv)

    def put(root, lg, updated, games):
        d = root / lg / "data"
        d.mkdir(parents=True)
        (d / "meta.json").write_text(json.dumps({"updated": updated}))
        (d / "games.json").write_text(games)

    put(site, "mens", "2026-11-26 10:30 UTC", "committed")
    put(lv, "mens", "2026-11-26 23:50 UTC", "live")        # later in the day: live wins
    put(site, "nba", "2026-11-27 10:40 UTC", "committed")
    put(lv, "nba", "2026-11-27 04:10 UTC", "live")         # last night's live vs this morning's daily
    assert live.overlay() == ["mens"]
    assert (site / "mens" / "data" / "games.json").read_text() == "live"
    assert (site / "nba" / "data" / "games.json").read_text() == "committed"


def test_memo_reuses_only_identical_inputs(tmp_path, monkeypatch):
    monkeypatch.setattr(memo, "DIR", tmp_path)
    df = pd.DataFrame({"a": [1.0, 2.0], "b": ["x", "y"]})
    calls = []

    def fn():
        calls.append(1)
        return df["a"].sum()

    assert memo.cached("step-2020", (df, [1, 2]), fn) == 3.0
    assert memo.cached("step-2020", (df.copy(), [1, 2]), fn) == 3.0   # same inputs: from disk
    assert len(calls) == 1
    df2 = df.assign(a=[1.0, 2.5])
    assert memo.cached("step-2020", (df2, [1, 2]), lambda: df2["a"].sum()) == 3.5
    assert len(list(tmp_path.glob("step-2020.*.pkl"))) == 1             # the old result is replaced
    assert memo.key(df, np.arange(3)) != memo.key(df, np.arange(4))
    assert memo.key(df) != memo.key(df.astype({"a": "float32"}))

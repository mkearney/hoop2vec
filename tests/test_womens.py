import numpy as np
import pandas as pd


def test_womens_ncaa_rounds_all_note_formats():
    from hoop2vec.games import WNCAA_RE, _womens_round
    cases = {
        "NCAA Women's Championship - Regional 4 in Spokane - 1st Round": 1,
        "NCAA Women's Championship - Regional 3 in Birmingham - First Four": 0,
        "NCAA WOMEN'S CHAMPIONSHIP PRES. BY CAPITAL ONE - ALBANY REGION - 2nd ROUND": 2,
        "NCAA Women's Championship pres. by Capital One - Alamo Region - Semifinal": 3,   # 2021 bubble naming
        "NCAA Women's Championship pres. by Capital One - Alamo Region - Final": 4,
        "NCAA Women's Basketball Championship - Regional 1 in Albany - Elite 8": 4,
        "NCAA Women's Championship - Final Four": 5,
        "NCAA Women's Championship - National Championship": 6,
    }
    for note, rnd in cases.items():
        assert WNCAA_RE.search(note), note
        assert _womens_round(note.upper()) == rnd, note
    # conference tournaments and early-season events are not the NCAA tournament
    for note in ("ATLANTIC 10 WOMEN'S CHAMPIONSHIP - QUARTERFINAL", "Baha Mar Women's Championship - Championship Game"):
        assert not WNCAA_RE.search(note)


def test_height_shift_keeps_team_totals():
    from hoop2vec.womens_data import _height_neutralize
    rng = np.random.default_rng(0)
    rows = []
    for g in range(60):
        for team in ("A", "B"):
            for p in range(8):
                rows.append({"season": 2026, "muid": f"g{g}", "team": team, "athlete_id": f"{team}{p}",
                             "min": 25.0 if p < 5 else 5.0, "inches": 66 + 2 * p + (np.nan if p == 7 else 0)})
    pb = pd.DataFrame(rows)
    pb["obpm"] = 0.6 * (pb["inches"].fillna(70) - 72) + rng.normal(0, 2, len(pb))  # value tied to height
    pb["dbpm"] = rng.normal(0, 2, len(pb))
    tot = lambda d: (d["obpm"] * d["min"]).groupby([d["muid"], d["team"]]).sum()  # noqa: E731
    before = tot(pb)
    gam = _height_neutralize(pb)
    assert gam["obpm"] > 0                                        # credit moves away from height
    assert np.allclose(before.to_numpy(), tot(pb).to_numpy())     # each team-game total is unchanged
    assert np.allclose(pb["bpm"], pb["obpm"] + pb["dbpm"])


def test_site_pages(tmp_path, monkeypatch):
    from hoop2vec import site
    monkeypatch.setattr(site, "SITE_ROOT", tmp_path)
    site.write()
    home = (tmp_path / "index.html").read_text()
    assert 'href="mens/"' in home and 'href="womens/"' in home
    for lg, other in (("mens", "womens"), ("womens", "mens")):
        page = (tmp_path / lg / "index.html").read_text()
        assert f'data-league="{lg}"' in page and f'href="../{other}/"' in page and "../assets/app.js" in page

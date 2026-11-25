"""Fast checks of the properties the models rely on (run: python -m pytest -q)."""
import numpy as np
import pandas as pd
import torch

from hoop2vec import players as PL
from hoop2vec.model import linear as LIN
from hoop2vec.model.net import MatchupNet


def test_network_is_antisymmetric():
    torch.manual_seed(0)
    net = MatchupNet(d_team=12).eval()
    xa, xb = torch.randn(64, 12), torch.randn(64, 12)
    ctx = torch.randn(64, 7)
    mu_ab, s_ab = net(xa, xb, ctx)
    mu_ba, s_ba = net(xb, xa, MatchupNet.swap_ctx(ctx))
    assert torch.allclose(mu_ab, -mu_ba, atol=1e-5)
    assert torch.allclose(s_ab, s_ba, atol=1e-5)


def test_linear_model_is_antisymmetric():
    rng = np.random.default_rng(0)
    n = 2000
    X = pd.DataFrame({c: rng.normal(size=n) for c in LIN.CORE})
    X["poss"] = 68.0
    X["y"] = 3 * X["k_margin"] + 2 * X["loc"] + rng.normal(scale=10, size=n)
    m = LIN.GameModel(use_style=False).fit(X)
    flip = X.copy()
    for c in LIN.CORE:
        flip[c] = -flip[c]
    assert np.allclose(m.predict_mu(X), -m.predict_mu(flip))


def test_expected_shares_follow_prior_value():
    v = np.array([1.0, 5.0, -2.0, 3.0, 0.0, -4.0, 2.0, -1.0, 4.0])
    s = PL.expected_shares(v)
    assert abs(s.sum() - 5.0) < 1e-9
    order = np.argsort(-v)
    assert np.all(np.diff(s[order]) <= 1e-12)


def test_deleak_replaces_final_ratings_with_pregame():
    """Synthetic Torvik-like game values = raw + 0.5 own final + 0.5 opp final -> after de-leaking they track pre-game."""
    rng = np.random.default_rng(1)
    teams = [f"T{i}" for i in range(40)]
    fin = pd.Series(rng.normal(scale=8, size=40), index=teams)
    rows, pg = [], []
    for g in range(3000):
        a, b = rng.choice(40, 2, replace=False)
        ta, tb = teams[a], teams[b]
        pre_a, pre_b = fin[ta] + rng.normal(scale=4), fin[tb] + rng.normal(scale=4)
        pts_a, pts_b = 70 + rng.normal(scale=8), 70 + rng.normal(scale=8)
        gid = f"g{g}"
        rows.append(dict(gid=gid, season=2020, team_a=ta, team_b=tb, pts_a=pts_a, pts_b=pts_b, poss=70.0, played=True,
                         a_k_o=pre_a / 2, a_k_d=-pre_a / 2, b_k_o=pre_b / 2, b_k_d=-pre_b / 2, k_mu=100.0))
        for t, o, me, op in ((ta, tb, pts_a, pts_b), (tb, ta, pts_b, pts_a)):
            raw_o, raw_d = 100 * me / 70 - 100, 100 - 100 * op / 70
            for k in range(5):
                pg.append(dict(season=2020, muid=gid, team=t, pid=k, min=40.0,
                               obpm=(0.5 * raw_o + 0.25 * fin[t] + 0.25 * fin[o]) / 5,
                               dbpm=(0.5 * raw_d + 0.25 * fin[t] + 0.25 * fin[o]) / 5))
    games = pd.DataFrame(rows)
    kpre = games[["season", "gid", "a_k_o", "a_k_d", "b_k_o", "b_k_d", "k_mu"]]
    conf = pd.DataFrame({"season": 2020, "team": teams, "t_adjoe": 100 + fin.values / 2, "t_adjde": 100 - fin.values / 2})
    pg = pd.DataFrame(pg)
    out = PL.deleak_bpm(pg, games, kpre, conf)
    team = out.groupby(["muid", "team"])["obpm"].mean().reset_index().merge(games, left_on="muid", right_on="gid")
    own_fin = team["team"].map(fin).to_numpy()
    resid = team["obpm"].to_numpy()
    # after de-leaking, the game value should carry ~no weight on the team's own final rating beyond its pre-game one
    own_pre = np.where(team["team"] == team["team_a"], team["a_k_o"] * 2, team["b_k_o"] * 2)
    X = np.c_[np.ones(len(team)), own_pre, own_fin]
    b = np.linalg.lstsq(X, resid, rcond=None)[0]
    assert abs(b[2]) < 0.02 < abs(b[1])


def test_draft_probabilities_are_cumulative():
    """P(pick <= 5) <= P(pick <= 14) <= P(pick <= 30) <= P(pick <= 60) for every player."""
    from hoop2vec import prospects as PR
    rng = np.random.default_rng(2)
    n = 3000
    D = pd.DataFrame({c: rng.normal(size=n) for c in PR.FEATURES})
    score = D["r_bpm"] + 0.5 * D["rec"]
    pick = np.where(score > 2.2, np.ceil(rng.uniform(1, 60, n)), np.nan)
    D["pick_now"] = pick
    out = PR.DraftModel("logit").fit(D).predict(D)
    P = out[[f"p{k}" for k in PR.THRESHOLDS]].to_numpy()
    assert np.all(np.diff(P, axis=1) >= -1e-12)


def test_sanitize_makes_json_safe():
    import json
    from hoop2vec.prospects import _sanitize
    x = _sanitize({"a": np.float64("nan"), "b": [np.int64(3), np.float32(1.23456)], "c": np.bool_(True)})
    assert json.loads(json.dumps(x, allow_nan=False)) == {"a": None, "b": [3, 1.2346], "c": True}


def test_translator_recovers_a_linear_mapping():
    """College features -> NBA profile: a clean linear signal should be recovered almost exactly."""
    from hoop2vec import comps as CP
    from hoop2vec.prospects import FEATURES
    rng = np.random.default_rng(3)
    n = 1500
    X = pd.DataFrame({c: rng.normal(size=n) for c in FEATURES})
    Y = pd.DataFrame({d: 2.0 * X[FEATURES[i]] + 10 + rng.normal(scale=0.1, size=n) for i, d in enumerate(CP.NBA_DIMS)})
    tm = CP.Translator(alpha=1.0).fit(X, Y)
    pred = tm.to_raw(tm.predict_z(X))
    for d in CP.NBA_DIMS:
        assert np.corrcoef(pred[d], Y[d])[0, 1] > 0.99

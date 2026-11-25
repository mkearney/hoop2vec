import numpy as np
import pandas as pd

NBA_CFG = {"by_conference": True, "direct": 6, "play_in": True, "rounds": [7, 7, 7, 7],
           "patterns": {7: [1, 1, 0, 0, 1, 0, 1]}, "bracket": [[1, 8], [4, 5], [3, 6], [2, 7]]}


def test_series_probabilities():
    from hoop2vec.playoffs import series_win_prob
    pat = [1, 1, 0, 0, 1, 0, 1]
    assert abs(series_win_prob(0.5, 0.5, 7, pat) - 0.5) < 1e-12
    assert abs(series_win_prob(0.6, 0.6, 7, pat) - 0.710208) < 1e-6          # textbook best-of-7 at 60%
    assert abs(series_win_prob(0.6, 0.5, 7, pat, 3, 0) - 0.96) < 1e-12       # up 3-0: lose 4 straight = .5*.4*.5*.4
    assert series_win_prob(0.6, 0.5, 7, pat, 4, 2) == 1.0 and series_win_prob(0.6, 0.5, 7, pat, 1, 4) == 0.0
    v = series_win_prob(np.array([0.7, 0.3]), np.array([0.6, 0.2]), 3, [1, 0, 1])   # vectorized
    assert np.allclose(v, [0.7 * 0.6 + 0.7 * 0.4 * 0.7 + 0.3 * 0.6 * 0.7, 0.3 * 0.2 + 0.3 * 0.8 * 0.3 + 0.7 * 0.2 * 0.3])
    # home court is worth something: the home-court team wins more often than with the pattern reversed
    assert series_win_prob(0.6, 0.45, 7, pat) > series_win_prob(0.6, 0.45, 7, [1 - x for x in pat])


def test_tiebreak_head_to_head_then_conference():
    from hoop2vec.playoffs import order_group
    w = np.array([50, 50, 50, 40])
    h2h = np.zeros((4, 4), int)
    h2h[1, 0], h2h[0, 1] = 3, 1          # team 1 beat team 0 three times out of four
    h2h[2, 0] = h2h[0, 2] = h2h[2, 1] = h2h[1, 2] = 2
    cw = np.array([30, 20, 35, 30])
    order = order_group([0, 1, 2, 3], w, h2h, cw, np.random.default_rng(0), use_conf=True)
    # three-way tie at 50 wins: combined head-to-head 1: 5-3, 2: 4-4, 0: 3-5 -> 1, 2, 0; then 3
    assert order == [1, 2, 0, 3]


def test_field_from_actual_first_round():
    from hoop2vec.playoffs import _field_from_series
    teams = [f"E{i}" for i in range(1, 9)]
    idx = {t: i for i, t in enumerate(teams)}
    conf_of = {t: "East" for t in teams}
    fixed_w = np.array([60, 55, 50, 48, 47, 45, 42, 40])
    ser = pd.DataFrame({"round": 1, "hi": ["E1", "E4", "E3", "E2"], "lo": ["E8", "E5", "E6", "E7"]})
    field = _field_from_series(ser, NBA_CFG, idx, conf_of, fixed_w, np.zeros((8, 8), int))
    assert field == {"East": teams}


def test_live_series_is_respected():
    """A simulated matchup that is already 3-0 in reality is priced from 3-0, not from 0-0."""
    from hoop2vec.playoffs import N_SIMS, Sim
    M = np.array([[0.0, -3.0], [3.0, 0.0]])     # team 1 is better: wins by 3 at home, team 0 wins by -3 at home
    S = np.full((2, 2), 12.0)
    sim = Sim(["A", "B"], {"A": "East", "B": "East"}, NBA_CFG, M, S, np.zeros(2), np.random.default_rng(1))
    A, B = np.zeros(N_SIMS, int), np.ones(N_SIMS, int)
    w_fresh = (sim.series(A, B, 7, {}) == 0).mean()
    w_up3 = (sim.series(A, B, 7, {(0, 1): (3, 0)}) == 0).mean()
    assert w_fresh < 0.5 < w_up3 and w_up3 > 0.85

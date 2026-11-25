"""Pro playoffs (NBA / WNBA): the rest of the regular season, tiebreakers, the play-in and best-of-N series.

College basketball ends in a single-elimination bracket (bracket.py). The pros play a long regular season into
seeded best-of-N series, so the forecast is built differently:

1. Regular season. Every remaining game is priced by the final game model (venue, travel, rest). Each simulated
   season also shifts every team's strength by a random amount whose size is learned from history: how far a
   team's rating moved between this point of a season and its end. Results already played are fixed.
2. Standings. Wins, then head-to-head record among the tied teams, then conference record (NBA), then a coin flip.
   The official tiebreakers have a few more steps (division titles, record vs playoff teams); those rarely matter.
   Once the regular season is over, ESPN's official seeds are used instead.
3. Postseason. The format comes from overrides/<league>/playoffs.yaml: NBA play-in (7v8, 9v10, then loser v
   winner), conference brackets and best-of-7 series with a 2-2-1-1-1 home pattern; WNBA top 8 overall, best-of-3
   (1-1-1) then best-of-7. A series is solved exactly from the two home/away game probabilities and its home
   pattern, starting from the current series score, so games already played are always respected.
4. Backtest. Every past playoff series is predicted before its first game with the season-ahead backtest game
   models (trained only on earlier seasons), and scored against what happened.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import yaml
from scipy.stats import norm

from .config import ARTIFACTS, OVERRIDES, PRO_LEAGUE, season_start, today

N_SIMS = 10000
BT_PATH = ARTIFACTS / "playoffs_backtest.json"


def load_config() -> dict:
    return yaml.safe_load((OVERRIDES / "playoffs.yaml").read_text())


def pattern(cfg: dict, n: int) -> list[int]:
    pats = cfg.get("patterns") or {}
    p = pats.get(n) or pats.get(str(n))
    if p:
        return list(p)
    return {1: [1], 3: [1, 0, 1], 5: [1, 1, 0, 0, 1], 7: [1, 1, 0, 0, 1, 0, 1]}.get(n, [1 - k % 2 for k in range(n)])


def series_win_prob(ph, pa, n: int, sites, wa: int = 0, wb: int = 0):
    """P(A wins a best-of-n series). ph = P(A wins a game A hosts), pa = P(A wins a game B hosts) (arrays ok);
    sites[k] = 1 if A hosts game k+1. Starts from a series score of wa-wb."""
    need = n // 2 + 1
    ph, pa = np.asarray(ph, float), np.asarray(pa, float)
    one, zero = np.ones(np.broadcast(ph, pa).shape), np.zeros(np.broadcast(ph, pa).shape)
    memo = {}

    def f(a, b):
        if a >= need:
            return one
        if b >= need:
            return zero
        if (a, b) not in memo:
            p = ph if sites[a + b] else pa
            memo[(a, b)] = p * f(a + 1, b) + (1 - p) * f(a, b + 1)
        return memo[(a, b)]
    return f(wa, wb)


# ------------------------------------------------------------------ data helpers
def regular_season(games: pd.DataFrame, season: int, cfg: dict) -> pd.DataFrame:
    g = games[(games["season"] == season) & games["type"].isin(["conf", "nonconf"])]
    pat = cfg.get("not_in_standings")
    if pat:
        g = g[~g["notes"].fillna("").str.contains(pat, case=False, regex=True)]
    return g


def actual_series(games: pd.DataFrame, season: int) -> pd.DataFrame:
    """Playoff series of a season as ESPN has them so far: teams, round, wins, games played and who hosted each."""
    po = games[(games["season"] == season) & (games["type"] == "post")].sort_values("date")
    rows = []
    for key, x in po.groupby("series", sort=False):
        x = x.sort_values("date")
        g1 = x.iloc[0]
        hi, lo = g1["team_b"], g1["team_a"]  # game 1 host (ESPN's home side, also in the 2020 bubble) = home court
        done = x[x["played"]]
        wins = {hi: 0, lo: 0}
        sites = []
        for r in done.itertuples():
            w = r.team_a if r.pts_a > r.pts_b else r.team_b
            wins[w] = wins.get(w, 0) + 1
            sites.append(0 if r.loc == 0 and r.neutral else int(r.team_b == hi))
        rows.append({"series": key, "round": int(g1["ncaa_round"]) if pd.notna(g1["ncaa_round"]) else None,
                     "conf": g1.get("region"), "hi": hi, "lo": lo, "w_hi": wins[hi], "w_lo": wins[lo],
                     "played": len(done), "sites": sites, "start": g1["date"], "neutral": bool(g1["neutral"])})
    return pd.DataFrame(rows)


def home_edge(games: pd.DataFrame, before_season: int) -> float:
    """League home-court edge in points from earlier regular seasons (for pricing a series' other venue)."""
    g = games[(games["season"] < before_season) & games["type"].isin(["conf", "nonconf"]) & games["played"]
              & (games["loc"] != 0) & ~games["neutral"].astype(bool)]
    g = g[g["season"] >= before_season - 5]
    return float((g["pts_b"] - g["pts_a"]).mean()) if len(g) > 200 else 2.5


# ------------------------------------------------------------------ team strength uncertainty
def drift_curve(feat: pd.DataFrame) -> tuple[float, float]:
    """How far a team's rating moves between a point of the regular season and its end, as a function of the
    share of its season still to play: var(end - now) ~ a + c * f, in points per 100 possessions."""
    f = feat[feat["played"] & feat["type"].isin(["conf", "nonconf"])]
    rows = []
    for side in ("a", "b"):
        rows.append(pd.DataFrame({"season": f["season"], "team": f[f"team_{side}"], "date": f["date"],
                                  "em": f[f"{side}_k_o"] - f[f"{side}_k_d"]}))
    L = pd.concat(rows).dropna().sort_values("date")
    g = L.groupby(["season", "team"])
    L["n"] = g.cumcount()
    L["N"] = g["em"].transform("size")
    L["end"] = g["em"].transform("last")
    L = L[L["N"] >= 20]
    L["f"] = 1 - L["n"] / L["N"]
    L["d2"] = (L["end"] - L["em"]) ** 2
    b = L.groupby(pd.cut(L["f"], np.linspace(0, 1, 11)), observed=True).agg(f=("f", "mean"), v=("d2", "mean"))
    c, a = np.polyfit(b["f"], b["v"], 1)
    return max(float(a), 0.0), max(float(c), 0.0)


# ------------------------------------------------------------------ matchup matrix
def home_matrix(bundle, state: pd.DataFrame, teams: list[str], day: int, post: bool = True):
    """M[i, j] = model margin (points, team i's view) when team i hosts team j; S = game sigma."""
    from . import predict as PR
    n = len(teams)
    ii, jj = np.where(~np.eye(n, dtype=bool))
    st = state.reindex(teams)
    pairs = pd.DataFrame({"team_a": np.array(teams)[jj], "team_b": np.array(teams)[ii], "loc": -1,
                          "v_lat": st["lat"].to_numpy()[ii], "v_lon": st["lon"].to_numpy()[ii],
                          "is_post": float(post), "rest_a": 2.0, "rest_b": 2.0, "day": day})
    df = PR.predict_pairs(bundle, state, pairs)
    M, S = np.zeros((n, n)), np.ones((n, n))
    M[ii, jj] = -df["margin"].to_numpy()
    S[ii, jj] = df["sigma"].to_numpy()
    return M, S


def state_asof(feat: pd.DataFrame, live: pd.DataFrame, season: int, cutoff) -> pd.DataFrame:
    """Team state as the model saw it before a date: each team's pre-game features from its first game on or after
    ``cutoff`` (nothing happened to it in between). Teams without such a game keep their live state."""
    from .predict import STATE_FEATS
    st = live.copy()
    f = feat[(feat["season"] == season) & (feat["date"] >= pd.Timestamp(cutoff))].sort_values("date")
    seen = set()
    for r in f.itertuples(index=False):
        for side in ("a", "b"):
            t = getattr(r, f"team_{side}")
            if t in seen or t not in st.index:
                continue
            seen.add(t)
            for c in STATE_FEATS:
                v = getattr(r, f"{side}_{c}", None)
                if v is not None and not pd.isna(v):
                    st.at[t, c] = v
    if len(f) and "k_mut" in f:
        st["k_mut"] = float(f["k_mut"].iloc[0])
    return st


# ------------------------------------------------------------------ simulation
class Sim:
    def __init__(self, teams, conf_of, cfg, M, S, tau, rng):
        self.teams, self.cfg, self.M, self.S, self.rng = list(teams), cfg, M, S, rng
        self.T = len(teams)
        self.idx = {t: i for i, t in enumerate(self.teams)}
        self.conf = np.array([conf_of.get(t) for t in self.teams], dtype=object)
        self.delta = rng.standard_normal((N_SIMS, self.T)) * tau[None, :]

    # one game (vectorized over sims): does `h` (hosting) beat `a`?  Played games are fixed.
    def game(self, h, a, fixed: dict):
        mu = self.M[h, a] + self.delta[np.arange(N_SIMS), h] - self.delta[np.arange(N_SIMS), a]
        win = self.rng.random(N_SIMS) < norm.cdf(mu / self.S[h, a])
        for (x, y), x_won in fixed.items():
            m1, m2 = (h == x) & (a == y), (h == y) & (a == x)
            win[m1], win[m2] = x_won, not x_won
        return win

    def series(self, A, B, n, live: dict):
        """A has home court. live[(x, y)] = (wins_x, wins_y) for series already under way."""
        s = np.arange(N_SIMS)
        ph = norm.cdf((self.M[A, B] + self.delta[s, A] - self.delta[s, B]) / self.S[A, B])
        pa = 1 - norm.cdf((self.M[B, A] + self.delta[s, B] - self.delta[s, A]) / self.S[B, A])
        sites = pattern(self.cfg, n)
        wa, wb = np.zeros(N_SIMS, int), np.zeros(N_SIMS, int)
        for (x, y), (wx, wy) in live.items():
            m1, m2 = (A == x) & (B == y), (A == y) & (B == x)
            wa[m1], wb[m1] = wx, wy
            wa[m2], wb[m2] = wy, wx
        p = np.empty(N_SIMS)
        for (i, j) in set(zip(wa, wb)):
            m = (wa == i) & (wb == j)
            p[m] = series_win_prob(ph[m], pa[m], n, sites, i, j)
        return np.where(self.rng.random(N_SIMS) < p, A, B)


def order_group(members, w, h2h, cw, rng, use_conf):
    """Teams of a group best-first: wins, head-to-head among the tied, conference record (NBA), coin flip."""
    key = sorted(members, key=lambda t: -w[t])
    out, i = [], 0
    while i < len(key):
        j = i
        while j + 1 < len(key) and w[key[j + 1]] == w[key[i]]:
            j += 1
        tied = np.array(key[i:j + 1])
        if len(tied) > 1:
            hh = h2h[np.ix_(tied, tied)]
            pct = hh.sum(1) / np.maximum(hh.sum(1) + hh.sum(0), 1)
            sec = cw[tied] if use_conf else np.zeros(len(tied))
            tied = tied[np.lexsort((rng.random(len(tied)), -sec, -pct))]
        out += list(tied)
        i = j + 1
    return out


def simulate(sim: Sim, fixed_w, fixed_h2h, rem: pd.DataFrame, official_seeds: dict | None,
             playin_results: dict, live_series: dict, field_fixed: dict | None = None, playin_teams=()):
    """Monte Carlo of the rest of the season. ``official_seeds``: group -> teams in seed order once the regular
    season is over; ``field_fixed``: group -> the 8 playoff teams in seed order once the first round is set."""
    cfg, T, rng = sim.cfg, sim.T, sim.rng
    s = np.arange(N_SIMS)
    W = np.tile(np.asarray(fixed_w, np.int32), (N_SIMS, 1))
    H2H = np.tile(np.asarray(fixed_h2h, np.int16), (N_SIMS, 1, 1))
    if len(rem):
        ia, ib = rem["ia"].to_numpy(), rem["ib"].to_numpy()
        mu = rem["mu"].to_numpy()[None, :] + sim.delta[:, ia] - sim.delta[:, ib]
        win_a = rng.random(mu.shape) < norm.cdf(mu / rem["sg"].to_numpy()[None, :])
        for k in range(len(rem)):
            a, b, wa = ia[k], ib[k], win_a[:, k]
            W[:, a] += wa
            W[:, b] += ~wa
            H2H[:, a, b] += wa
            H2H[:, b, a] += ~wa
    same = sim.conf[:, None] == sim.conf[None, :]
    CW = (H2H * same[None]).sum(2)
    groups = sorted(set(sim.conf)) if cfg.get("by_conference") else [None]
    members = {g: [i for i in range(T) if g is None or sim.conf[i] == g] for g in groups}
    nseed = cfg.get("direct", 8) + (4 if cfg.get("play_in") else 0)
    SEED = {g: np.zeros((N_SIMS, min(nseed, len(members[g]))), int) for g in groups}
    if field_fixed:
        pass
    elif official_seeds:
        for g in groups:
            order = [sim.idx[t] for t in official_seeds[g]][: SEED[g].shape[1]]
            SEED[g][:] = order
    else:
        for k in range(N_SIMS):
            for g in groups:
                SEED[g][k] = order_group(members[g], W[k], H2H[k], CW[k], rng, bool(cfg.get("by_conference")))[: SEED[g].shape[1]]
    R = len(cfg["rounds"])
    reach = np.zeros((N_SIMS, T, R + 1), bool)   # reach[:, t, r] = plays in round r (1..R); [:, t, 0] = in the play-in
    champ = np.zeros((N_SIMS, T), bool)
    seedrank = np.full((N_SIMS, T), 99)
    field = {}
    for g in groups:
        if field_fixed:
            F = np.tile(np.array([sim.idx[t] for t in field_fixed[g]]), (N_SIMS, 1))
            for k in range(F.shape[1]):
                seedrank[s, F[:, k]] = k + 1
            for t in playin_teams:
                if sim.conf[sim.idx[t]] == g or g is None:
                    reach[s, sim.idx[t], 0] = True
            field[g] = F
            continue
        sd = SEED[g]
        for k in range(sd.shape[1]):
            seedrank[s, sd[:, k]] = k + 1
        d = cfg.get("direct", 8)
        if cfg.get("play_in"):
            t7, t8, t9, t10 = sd[:, 6], sd[:, 7], sd[:, 8], sd[:, 9]
            for t in (t7, t8, t9, t10):
                reach[s, t, 0] = True
            w1 = sim.game(t7, t8, playin_results)
            s7, l1 = np.where(w1, t7, t8), np.where(w1, t8, t7)
            w910 = np.where(sim.game(t9, t10, playin_results), t9, t10)
            s8 = np.where(sim.game(l1, w910, playin_results), l1, w910)
            final = np.column_stack([sd[:, :d], s7, s8])
        else:
            final = sd[:, :d]
        field[g] = final
    # bracket rounds
    alive = {}
    for g in groups:
        F = field[g]
        pairs = [(F[:, a - 1], F[:, b - 1]) for a, b in cfg["bracket"]]
        alive[g] = pairs

    def home_first(X, Y):
        """Home court: better seed within a group in round 1 (given); later, the better record (NBA) or seed."""
        if cfg.get("by_conference"):
            better = (W[s, X] > W[s, Y]) | ((W[s, X] == W[s, Y]) & (seedrank[s, X] <= seedrank[s, Y]))
        else:
            better = seedrank[s, X] <= seedrank[s, Y]
        return np.where(better, X, Y), np.where(better, Y, X)

    winners = {}
    for g in groups:
        ws = []
        for A, B in alive[g]:
            reach[s, A, 1] = True
            reach[s, B, 1] = True
            ws.append(sim.series(A, B, cfg["rounds"][0], live_series))
        winners[g] = ws
    r = 1
    group_rounds = R - 1 if cfg.get("by_conference") else R
    while r < group_rounds:
        for g in groups:
            ws, nxt = winners[g], []
            for k in range(0, len(ws), 2):
                A, B = home_first(ws[k], ws[k + 1])
                reach[s, A, r + 1] = True
                reach[s, B, r + 1] = True
                nxt.append(sim.series(A, B, cfg["rounds"][r], live_series))
            winners[g] = nxt
        r += 1
    if cfg.get("by_conference"):  # Finals between the conference champions
        A, B = home_first(winners[groups[0]][0], winners[groups[1]][0])
        reach[s, A, R] = True
        reach[s, B, R] = True
        ch = sim.series(A, B, cfg["rounds"][R - 1], live_series)
    else:
        ch = winners[groups[0]][0]
    champ[s, ch] = True
    return {"W": W, "reach": reach, "champ": champ, "seedrank": seedrank}


# ------------------------------------------------------------------ publish
def _rank(teams, idx, conf_of, fixed_w, h2h, cfg):
    """Final regular-season order of a group (wins, head-to-head, conference record); no coin flips."""
    same = np.array([[conf_of.get(a) == conf_of.get(b) for b in idx] for a in idx])
    cw = (h2h * same).sum(1)
    rng = np.random.default_rng(0)
    order = order_group([idx[t] for t in teams], fixed_w, h2h, cw, rng, bool(cfg.get("by_conference")))
    inv = {i: t for t, i in idx.items()}
    return [inv[i] for i in order]


def _official_seeds(conf, season, cfg, idx, conf_of, fixed_w, h2h) -> dict:
    """Seeds once the regular season is over: ESPN's official conference seeds in the NBA; league-wide order by
    record (and the same tiebreakers) in the WNBA, whose ESPN seeds are per conference."""
    c = conf[(conf["season"] == season) & conf["team"].isin(idx)].copy()
    c["seed"] = pd.to_numeric(c.get("seed"), errors="coerce")
    if cfg.get("by_conference"):
        if c["seed"].notna().all() and len(c):
            return {g: x.sort_values("seed")["team"].tolist() for g, x in c.groupby("conf")}
        return {g: _rank([t for t in idx if conf_of.get(t) == g], idx, conf_of, fixed_w, h2h, cfg)
                for g in sorted({conf_of.get(t) for t in idx})}
    return {None: _rank(list(idx), idx, conf_of, fixed_w, h2h, cfg)}


def _field_from_series(ser, cfg, idx, conf_of, fixed_w, h2h) -> dict | None:
    """The 8-team field per group in seed order from the actual first-round series: the home-court teams are seeds
    1-4 (in standings order) and each opponent takes the seed that bracket pairs with it."""
    r1 = ser[ser["round"] == 1]
    if r1.empty:
        return None
    pair_of = {}
    for a, b in cfg["bracket"]:
        pair_of[a], pair_of[b] = b, a
    groups = sorted({conf_of.get(t) for t in r1["hi"]}) if cfg.get("by_conference") else [None]
    out = {}
    for g in groups:
        x = r1 if g is None else r1[r1["hi"].map(conf_of) == g]
        if len(x) != len(cfg["bracket"]):
            return None
        his = _rank(list(x["hi"]), idx, conf_of, fixed_w, h2h, cfg)
        seeds = {}
        for k, t in enumerate(his):
            seeds[k + 1] = t
            seeds[pair_of[k + 1]] = x.loc[x["hi"] == t, "lo"].iloc[0]
        out[g] = [seeds[k] for k in sorted(seeds)]
    return out


def _pre_series(bundle, feat, live_state, season, ser, day, h_edge):
    """P(home-court team wins) for an actual series, as the model saw it the day before game 1."""
    st = state_asof(feat, live_state, season, ser["start"])
    M, S = home_matrix(bundle, st, [ser["hi"], ser["lo"]], day)
    ph, pa = norm.cdf(M[0, 1] / S[0, 1]), 1 - norm.cdf(M[1, 0] / S[1, 0])
    n = 2 * max(ser["w_hi"], ser["w_lo"], 1) - 1 if ser["done"] else ser["n"]
    return float(series_win_prob(ph, pa, n, ser["sites_full"]))


def publish(bundle, st: pd.DataFrame, games: pd.DataFrame, conf: pd.DataFrame, feat: pd.DataFrame, season: int,
            seed: int = 0) -> dict:
    """playoffs.json: standings + projected wins, playoff odds by round, the (projected or actual) bracket."""
    from . import predict as PR
    cfg = load_config()
    teams = [t for t in st.index if t in set(conf.loc[conf["season"] == season, "team"])] or list(st.index)
    idx = {t: i for i, t in enumerate(teams)}
    conf_of = conf[conf["season"] == season].drop_duplicates("team").set_index("team")["conf"].to_dict()
    reg = regular_season(games, season, cfg)
    reg = reg[reg["team_a"].isin(idx) & reg["team_b"].isin(idx)]
    played = reg[reg["played"]]
    T = len(teams)
    fixed_w, fixed_l, h2h = np.zeros(T, int), np.zeros(T, int), np.zeros((T, T), int)
    for r in played.itertuples():
        a, b = idx[r.team_a], idx[r.team_b]
        w, l_ = (a, b) if r.pts_a > r.pts_b else (b, a)
        fixed_w[w] += 1
        fixed_l[l_] += 1
        h2h[w, l_] += 1
    rem_games = reg[~reg["played"]]
    rem = pd.DataFrame(columns=["ia", "ib", "mu", "sg"])
    if len(rem_games):
        up = PR.upcoming(bundle, st, rem_games.assign(date=rem_games["date"].clip(lower=pd.Timestamp(today()))), days=400)
        if len(up):
            rem = pd.DataFrame({"ia": up["team_a"].map(idx).to_numpy(), "ib": up["team_b"].map(idx).to_numpy(),
                                "mu": up["margin"].to_numpy(), "sg": up["sigma"].to_numpy()}).dropna()
            rem[["ia", "ib"]] = rem[["ia", "ib"]].astype(int)
    # games not on the schedule yet (the NBA sets each team's last two after the Cup group stage): pair the teams that
    # are short at random and play those games on a neutral floor
    sched = (fixed_w + fixed_l) + (np.bincount(np.r_[rem["ia"], rem["ib"]].astype(int), minlength=T) if len(rem) else 0)
    short = np.clip(int(cfg.get("games", 0)) - sched, 0, None) if cfg.get("games") and len(rem) else np.zeros(T, int)
    slots = np.repeat(np.arange(T), short)
    np.random.default_rng(seed).shuffle(slots)
    phantom = [(int(a), int(b)) for a, b in zip(slots[0::2], slots[1::2]) if a != b]
    # uncertainty in team strength between now and the playoffs (from history), in points
    a0, c0 = drift_curve(feat)
    total = sched + (np.bincount(np.r_[[a for a, _ in phantom], [b for _, b in phantom]].astype(int), minlength=T) if phantom else 0)
    frac = np.where(total > 0, 1 - (fixed_w + fixed_l) / np.maximum(total, 1), 1.0)
    poss = float(st["adjt"].mean()) if "adjt" in st else 100.0
    tau = np.sqrt(a0 + c0 * frac) * poss / 100
    post = games[(games["season"] == season) & games["type"].isin(["post", "conftourney"])]
    last_post = games[(games["season"] == season - 1) & (games["type"] == "post")]
    day = int((last_post["date"].min() - pd.Timestamp(season_start(season - 1))).days) if len(last_post) else 180
    reg_done = len(rem_games) == 0 and len(played) > 0
    official = _official_seeds(conf, season, cfg, idx, conf_of, fixed_w, h2h) if reg_done else None
    # playoff state so far
    ser = actual_series(games, season) if (post["type"] == "post").any() else pd.DataFrame()
    n_by_round = {i + 1: n for i, n in enumerate(cfg["rounds"])}
    live = {}
    if len(ser):
        ns, dones, fulls = [], [], []
        max_round = ser["round"].max()
        for _, x in ser.iterrows():
            n = n_by_round.get(x["round"], 7)
            wmax = max(x["w_hi"], x["w_lo"])
            # a series is over if a team reached the configured wins, or the next round has started without it
            # (then its length is read from the result: the winner's wins = n // 2 + 1)
            over = wmax >= n // 2 + 1 or (x["round"] < max_round and wmax > x["w_hi"] + x["w_lo"] - wmax)
            if over and wmax < n // 2 + 1:
                n = 2 * wmax - 1
            full = list(x["sites"]) + pattern(cfg, n)[len(x["sites"]):]
            ns.append(n)
            dones.append(bool(over))
            fulls.append(full + [1] * max(0, n - len(full)))
            if x["hi"] in idx and x["lo"] in idx:
                live[(idx[x["hi"]], idx[x["lo"]])] = (int(x["w_hi"]), int(x["w_lo"]))
        ser["n"], ser["done"], ser["sites_full"] = ns, dones, fulls
    field = _field_from_series(ser, cfg, idx, conf_of, fixed_w, h2h) if len(ser) else None
    playin_teams = sorted(set(post.loc[post["type"] == "conftourney", "team_a"]) | set(post.loc[post["type"] == "conftourney", "team_b"]))
    playin = {}
    for r in post[(post["type"] == "conftourney") & post["played"]].itertuples():
        if r.team_a in idx and r.team_b in idx:
            playin[(idx[r.team_b], idx[r.team_a])] = bool(r.pts_b > r.pts_a)
    champion = None
    if len(ser):
        fin = ser[ser["round"] == len(cfg["rounds"])]
        if len(fin) and bool(fin["done"].iloc[0]):
            f0 = fin.iloc[0]
            champion = f0["hi"] if f0["w_hi"] > f0["w_lo"] else f0["lo"]
    phase = "complete" if champion else "postseason" if (reg_done and len(post)) else "regular" if not reg_done else "pre_playoffs"

    def run(state, rem_df, fixed_live, fixed_playin, tau_v):
        M, S = home_matrix(bundle, state, teams, day)
        if phantom and len(rem_df):
            ph = pd.DataFrame({"ia": [a for a, _ in phantom], "ib": [b for _, b in phantom],
                               "mu": [(M[a, b] - M[b, a]) / 2 for a, b in phantom], "sg": [S[a, b] for a, b in phantom]})
            rem_df = pd.concat([rem_df, ph], ignore_index=True)
        sim = Sim(teams, conf_of, cfg, M, S, tau_v, np.random.default_rng(seed))
        return simulate(sim, fixed_w, h2h, rem_df, official, fixed_playin, fixed_live, field, playin_teams), M, S

    out_now, M, S = run(st, rem, live, playin, tau)
    pre = None
    if phase in ("postseason", "complete") and len(post):
        # the same model before the first postseason game: what it expected going in (out of sample for the
        # current season: the deployed models are trained on earlier seasons only)
        r1 = post[post["type"] == "post"]
        st0 = state_asof(feat, st, season, (r1 if len(r1) else post)["date"].min())
        pre, _, _ = run(st0, rem.iloc[:0], {}, {}, np.sqrt(a0) * poss / 100 * np.ones(T))
    rounds = cfg.get("round_names") or [f"Round {i + 1}" for i in range(len(cfg["rounds"]))]
    res = out_now
    rows = []
    for t, i in idx.items():
        Wt = res["W"][:, i]
        row = {"team": t, "conf": conf_of.get(t), "w": int(fixed_w[i]), "l": int(fixed_l[i]), "g_total": int(total[i]),
               "proj_w": float(Wt.mean()), "w_lo": float(np.percentile(Wt, 10)), "w_hi": float(np.percentile(Wt, 90)),
               "p_playoffs": float(res["reach"][:, i, 1].mean()), "p_title": float(res["champ"][:, i].mean()),
               "p_round": [float(res["reach"][:, i, r].mean()) for r in range(1, len(cfg["rounds"]) + 1)],
               "p_seed1": float((res["seedrank"][:, i] == 1).mean()),
               "seed_mean": float(np.where(res["seedrank"][:, i] < 99, res["seedrank"][:, i], np.nan)[res["seedrank"][:, i] < 99].mean())
               if (res["seedrank"][:, i] < 99).any() else None}
        if cfg.get("play_in"):
            row["p_playin"] = float(res["reach"][:, i, 0].mean())
            row["p_top6"] = float((res["seedrank"][:, i] <= cfg.get("direct", 6)).mean())
        if pre is not None:
            row["pre_title"] = float(pre["champ"][:, i].mean())
            row["pre_round"] = [float(pre["reach"][:, i, r].mean()) for r in range(1, len(cfg["rounds"]) + 1)]
        rows.append(row)
    series_out = []
    if len(ser):
        for _, x in ser.sort_values(["round", "start"]).iterrows():
            ph = norm.cdf(M[idx[x["hi"]], idx[x["lo"]]] / S[idx[x["hi"]], idx[x["lo"]]]) if x["hi"] in idx else np.nan
            pa = 1 - norm.cdf(M[idx[x["lo"]], idx[x["hi"]]] / S[idx[x["lo"]], idx[x["hi"]]]) if x["hi"] in idx else np.nan
            p_now = float(series_win_prob(ph, pa, int(x["n"]), x["sites_full"], int(x["w_hi"]), int(x["w_lo"])))
            try:
                p_pre = _pre_series(bundle, feat, st, season, x, day, None)
            except Exception:
                p_pre = None
            series_out.append({"round": int(x["round"]), "round_name": rounds[int(x["round"]) - 1] if x["round"] else "",
                               "conf": x["conf"] if isinstance(x["conf"], str) else None, "hi": x["hi"], "lo": x["lo"],
                               "w_hi": int(x["w_hi"]), "w_lo": int(x["w_lo"]), "n": int(x["n"]), "done": bool(x["done"]),
                               "p_hi": p_now, "p_hi_pre": p_pre, "start": str(x["start"].date())})
    seeds = {str(g): v for g, v in (field or official or {}).items()} or None
    return {"season": season, "phase": phase, "champion": champion, "n_sims": N_SIMS, "format": cfg,
            "rounds": rounds, "drift": {"a": a0, "c": c0}, "teams": sorted(rows, key=lambda r: -r["p_title"]),
            "series": series_out, "official_seeds": seeds,
            "remaining_games": int(len(rem_games)), "unscheduled_games": len(phantom), "home_edge": home_edge(games, season)}


# ------------------------------------------------------------------ backtest: every past series, before game 1
def backtest_series(P: pd.DataFrame, games: pd.DataFrame, models: list[str]) -> dict:
    """Pre-series P(home-court team wins) from each season-ahead backtest model, scored against the result."""
    cfg = load_config()
    rows = []
    for season in sorted(P["season"].unique()):
        ser = actual_series(games, season)
        if ser.empty:
            continue
        h = home_edge(games, season)
        Ps = P[P["season"] == season].set_index("gid")
        po = games[(games["season"] == season) & (games["type"] == "post")]
        for _, x in ser.iterrows():
            done = max(x["w_hi"], x["w_lo"])
            if x["played"] < 1:
                continue
            n = 2 * done - 1
            if max(x["w_hi"], x["w_lo"]) < n // 2 + 1:
                continue
            g1 = po[po["series"] == x["series"]].sort_values("date").iloc[0]
            if g1["gid"] not in Ps.index:
                continue
            r = Ps.loc[g1["gid"]]
            hi_home = g1["team_b"] == x["hi"]
            sites = list(x["sites"]) + pattern(cfg, n)[len(x["sites"]):]
            sites = sites + [1] * (n - len(sites))
            out = {"season": int(season), "round": x["round"], "hi": x["hi"], "lo": x["lo"], "n": n,
                   "hi_won": bool(x["w_hi"] > x["w_lo"]), "neutral": bool(x["neutral"])}
            for m in models:
                mu, sg = r.get(f"mu_{m}"), r.get(f"sg_{m}")
                if mu is None or pd.isna(mu):
                    continue
                mu_hi = -mu if hi_home else mu           # game 1, home-court team's view
                if x["neutral"]:
                    ph = pa = norm.cdf(mu_hi / sg)
                else:
                    ph, pa = norm.cdf(mu_hi / sg), norm.cdf((mu_hi - 2 * h) / sg)
                out[f"p_{m}"] = float(series_win_prob(ph, pa, n, sites))
            rows.append(out)
    D = pd.DataFrame(rows)
    if D.empty:
        return {}
    y = D["hi_won"].to_numpy(float)
    res = {"n": int(len(D)), "seasons": [int(D["season"].min()), int(D["season"].max())],
           "home_court_won": float(y.mean()), "models": {}}

    def score(p):
        p = np.clip(p, 1e-4, 1 - 1e-4)
        return {"log_loss": float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))), "brier": float(np.mean((p - y) ** 2)),
                "accuracy": float(np.mean((p > 0.5) == (y > 0.5)))}
    # baseline: home court wins at the rate it did in earlier seasons
    base = np.array([D.loc[D["season"] < s, "hi_won"].mean() if (D["season"] < s).any() else 0.7 for s in D["season"]])
    res["models"]["home_court"] = score(np.nan_to_num(base, nan=0.7))
    for m in models:
        if f"p_{m}" in D:
            ok = D[f"p_{m}"].notna()
            if ok.all():
                res["models"][m] = score(D[f"p_{m}"].to_numpy())
    res["by_round"] = {int(k): {"n": int(len(x)), "home_court_won": float(x["hi_won"].mean())}
                       for k, x in D.groupby("round") if pd.notna(k)}
    return res


def is_pro() -> bool:
    return PRO_LEAGUE

"""One-term linear models vs the betting line: mmai's original features (before) vs hoop2vec's (after).

Writes artifacts/leak_check.json (shown on the site's Method page). Needs a features.parquet + games.parquet
built by the original mmai repository for the "before" rows:

python research/leak_check.py /path/to/mmai/data/processed
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
MMAI = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("../mmai/data/processed")
HERE = Path(__file__).resolve().parents[1] / "data" / "processed"
import numpy as np, pandas as pd
from scipy.stats import norm
import hoop2vec.lines as L
from hoop2vec.model.backtest import log_loss
from hoop2vec.config import ARTIFACTS
lines = L.collect()
def prep(fp, gp):
    feat = pd.read_parquet(fp).drop_duplicates(["season", "gid"])
    games = pd.read_parquet(gp)
    f = feat.merge(L.for_games(feat, games, lines), on=["season", "gid"], how="left")
    f = f[f.played & f.pts_a.notna()].copy()
    f["margin"] = f.pts_a - f.pts_b
    f["t"] = (f.a_adjt + f.b_adjt) / 200
    return f
before = prep(MMAI / "features.parquet", MMAI / "games.parquet")
after = prep(HERE / "features.parquet", HERE / "games.parquet")
terms = {"roster": lambda d: (d.a_roster - d.b_roster) * d.t, "adjem": lambda d: (d.a_adjem - d.b_adjem) * d.t}
if "a_k_o" in after:
    after["poss"] = (after.k_mut + after.a_k_t + after.b_k_t).clip(55, 85)
    terms_after = dict(terms, kalman=lambda d: ((d.a_k_o - d.a_k_d) - (d.b_k_o - d.b_k_d)) * d.poss / 100)
seasons = [s for s in range(2015, 2027) if (after.season.eq(s) & after.line_a.notna()).sum() > 1000]
rows = {}
def score(f, name, fn, S):
    tr = f[(f.season < S) & (f.season >= 2010)].dropna(subset=["margin"])
    te = f[(f.season == S) & f.line_a.notna()]
    xtr, xte = np.nan_to_num(fn(tr)), np.nan_to_num(fn(te))
    X = np.c_[xtr, tr["loc"]]; b = np.linalg.lstsq(X, tr.margin, rcond=None)[0]; s = np.std(tr.margin - X @ b)
    return log_loss(te.margin > 0, norm.cdf((np.c_[xte, te["loc"]] @ b) / s))
out = {"seasons": seasons, "rows": []}
for lab, f, fn in [("Betting line (ESPN)", after, None), ("mmai roster rating (before fix)", before, terms["roster"]),
                   ("hoop2vec roster rating (after fix)", after, terms["roster"]), ("mmai AdjEM (before)", before, terms["adjem"]),
                   ("hoop2vec AdjEM (after)", after, terms["adjem"]), ("hoop2vec Kalman rating", after, terms_after["kalman"])]:
    vals = []
    for S in seasons:
        if fn is None:
            te = f[(f.season == S) & f.line_a.notna()]
            s = np.sqrt(np.mean((te.margin - te.line_a) ** 2)); vals.append(log_loss(te.margin > 0, norm.cdf(te.line_a / s)))
        else:
            vals.append(score(f, lab, fn, S))
    out["rows"].append({"label": lab, "values": vals})
    print(lab, np.round(vals, 4), flush=True)
(ARTIFACTS / "leak_check.json").write_text(json.dumps(out, indent=1))

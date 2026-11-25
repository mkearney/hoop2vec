"""Fit a league's team-identity model from its play-by-play seasons: ``python -m hoop2vec style [--womens]``.

1. Opponent-adjusted, shrunk team-season style tables for every PBP season (two passes: the second uses last
   season, scaled by the estimated year-to-year persistence, as each team's prior).
2. Quality-residualized factor analysis + Gaussian-mixture archetypes on the completed seasons (StyleModel).
3. Names. The men's dimensions and archetypes were named by hand (artifacts/mens/style/style_names.json). For
   another league, each fitted factor is matched to the men's factor with the most similar loadings (Hungarian
   assignment on |cosine|, sign-aligned), and each archetype to the men's archetype whose centroid is nearest
   after that mapping. Matches below ``MIN_COS`` keep a generic name for hand-editing. Men's historical examples
   are dropped. The result is written once and can then be edited by hand (it is never overwritten unless
   ``--rename`` is passed).
"""
from __future__ import annotations

import json
import time

import numpy as np
import pandas as pd

from ..config import ROOT, current_season
from ..ingest import season_dir
from .model import StyleModel
from .season import (
    STYLE_DIR,
    build_season,
    estimate_persistence,
    load_season,
    priors_from,
)

MIN_COS = 0.45
REF_NAMES = ROOT / "artifacts" / "mens" / "style" / "style_names.json"
REF_MODEL = ROOT / "artifacts" / "mens" / "style" / "style_model.json"


def _jsonable(o):
    if isinstance(o, dict):
        return {k: _jsonable(v) for k, v in o.items()}
    if isinstance(o, (np.floating, np.integer)):
        return o.item()
    return o


def seasons_available(first: int = 2008):
    return [s for s in range(first, current_season() + 1) if (season_dir(s) / "lineups.parquet").exists()]


def build_tables(seasons=None):
    seasons = seasons or seasons_available()
    cur = current_season()
    STYLE_DIR.mkdir(parents=True, exist_ok=True)
    loaded, p1, metas = {}, {}, {}
    for s in seasons:
        t = time.time()
        loaded[s] = load_season(s)
        p1[s], metas[s], _ = build_season(s, loaded=loaded[s])
        print(f"  style pass 1 {s}: {len(p1[s])} teams ({time.time() - t:.0f}s)", flush=True)
    done = [s for s in seasons if s != cur]
    rho = estimate_persistence({s: p1[s] for s in done})
    (STYLE_DIR / "persistence.json").write_text(json.dumps(rho, indent=1))
    final, fmeta = {}, {}
    for s in seasons:
        pri = None
        if s - 1 in final:
            teams = sorted(set(loaded[s][1]["team_id"]))
            pri = priors_from(final[s - 1], teams, rho, fmeta[s - 1])
        final[s], fmeta[s], _ = build_season(s, priors=pri, loaded=loaded[s])
        print(f"  style pass 2 {s}: coverage={fmeta[s]['_coverage']}", flush=True)
    last = max(done)  # the last completed season is the prior for the live season's daily refresh
    final[last].to_parquet(STYLE_DIR / f"team_season_{last}.parquet", index=False)
    (STYLE_DIR / f"meta_{last}.json").write_text(json.dumps(_jsonable(fmeta[last])))
    return final, fmeta


def fit(tabs, metas, n_arch=None):
    cur = current_season()
    train = [s for s in tabs if s != cur and metas[s]["_coverage"]["pbp_share"] > 0.7]
    hist = pd.concat([tabs[s] for s in train], ignore_index=True)
    hist = hist[hist["games"] >= 15].reset_index(drop=True)
    model = StyleModel().fit(hist, n_arch=n_arch)
    model.save(STYLE_DIR / "style_model.json")
    hist.to_parquet(STYLE_DIR / "train_table.parquet", index=False)
    (STYLE_DIR / "profiles.json").unlink(missing_ok=True)  # recomputed from the new model on first use
    print(f"  style model: {len(hist)} team-seasons {train}; factors", {b: s["k"] for b, s in model.spec["blocks"].items()})
    return model


def transfer_names(model: StyleModel, ref_model: StyleModel | None = None, ref_names: dict | None = None) -> dict:
    """Carry the hand-written men's names over to a newly fitted model where the dimensions match (see module doc)."""
    from scipy.optimize import linear_sum_assignment
    ref_model = ref_model or StyleModel.load(REF_MODEL)
    ref_names = ref_names or json.loads(REF_NAMES.read_text())
    out = {}
    for b, s in model.spec["blocks"].items():
        r = ref_model.spec["blocks"][b]
        common = [c for c in s["cols"] if c in r["cols"]]
        L = pd.DataFrame(s["loadings"], index=s["cols"]).loc[common].to_numpy()
        Lr = pd.DataFrame(r["loadings"], index=r["cols"]).loc[common].to_numpy()
        C = (L / np.linalg.norm(L, axis=0)).T @ (Lr / np.linalg.norm(Lr, axis=0))  # women's x men's factors
        rows, cols = linear_sum_assignment(-np.abs(C))
        fmap = {}  # women's factor -> (men's factor, sign)
        factors = []
        rn = ref_names.get(b, {})
        for i in range(s["k"]):
            if i in rows and abs(C[i, cols[list(rows).index(i)]]) >= MIN_COS:
                j = cols[list(rows).index(i)]
                sg = 1 if C[i, j] > 0 else -1
                fmap[i] = (j, sg)
                f = dict(rn["factors"][j]) if j < len(rn.get("factors", [])) else {}
                if sg < 0:
                    f["low"], f["high"] = f.get("high", "High"), f.get("low", "Low")
                f["matched"] = round(float(abs(C[i, j])), 2)
                factors.append(f)
            else:
                top = np.argsort(-np.abs(L[:, i]))[:3]
                factors.append({"name": f"{b.title()} factor {i + 1}", "low": "Low", "high": "High",
                                "desc": "Top loadings: " + ", ".join(common[t] for t in top), "matched": None})
        # archetypes: centroid in the men's factor space (matched factors only), nearest men's archetype
        G = np.array(s["gmm"]["means"])
        Gr = np.array(r["gmm"]["means"])
        mapped = np.zeros((len(G), Gr.shape[1]))
        for i, (j, sg) in fmap.items():
            mapped[:, j] = sg * G[:, i]
        used = sorted({j for j, _ in fmap.values()})
        D = ((mapped[:, None, used] - Gr[None, :, used]) ** 2).sum(-1) if used else np.zeros((len(G), len(Gr)))
        ar, ac = linear_sum_assignment(D)
        archs = []
        for a in range(len(G)):
            if a in ar:
                j = ac[list(ar).index(a)]
                x = dict(rn["archetypes"][j]) if j < len(rn.get("archetypes", [])) else {"name": f"{b.title()} type {a + 1}", "desc": ""}
                x.pop("history", None)
                x["matched_dist"] = round(float(np.sqrt(D[a, j])), 2)
                archs.append(x)
            else:
                archs.append({"name": f"{b.title()} type {a + 1}", "desc": ""})
        out[b] = {"factors": factors, "archetypes": archs}
    return out


def run(rename: bool = False, n_arch=None):
    tabs, metas = build_tables()
    model = fit(tabs, metas, n_arch=n_arch)
    names_f = STYLE_DIR / "style_names.json"
    if rename or not names_f.exists():
        names_f.write_text(json.dumps(transfer_names(model), indent=1))
        print(f"  wrote {names_f} (review by hand)")
    publish_history(tabs, metas, model)
    return model


def publish_history(tabs, metas, model):
    """Identity cards for every completed PBP season (the site's season picker); the live season is published daily."""
    from .identity import publish_season
    for s in sorted(tabs):
        if s < current_season() and metas[s]["_coverage"]["pbp_share"] > 0.7:
            publish_season(s, tabs[s], metas[s], model)
            print(f"  identity cards {s}")

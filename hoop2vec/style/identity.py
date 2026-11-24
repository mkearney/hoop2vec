"""Team identity layer (from mmkm): live style tables, identity cards and the style dictionary for the site.

The style model (quality-residualized factor analysis + Gaussian-mixture archetypes, hand-named in
artifacts/style/style_names.json) is fit once per season on completed seasons and only *projected* daily.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from ..config import SITE_DATA
from .cards import archetype_profiles, build_cards, load_names, model_dict
from .model import StyleModel
from .season import STYLE_DIR, build_season, load_season, priors_from

PROFILES = STYLE_DIR / "profiles.json"


def load_model() -> StyleModel:
    return StyleModel.load(STYLE_DIR / "style_model.json")


def profiles(model: StyleModel):
    """Membership-weighted feature profile of every archetype (cached; needs the training table once)."""
    if PROFILES.exists():
        d = json.loads(PROFILES.read_text())
        return {b: np.array(v) for b, v in d["profiles"].items()}, d["cols"]
    hist = pd.read_parquet(STYLE_DIR / "train_table.parquet")
    prof, cols = archetype_profiles(model, hist, model.transform(hist))
    PROFILES.write_text(json.dumps({"cols": cols, "profiles": {b: np.round(v, 4).tolist() for b, v in prof.items()}}))
    return prof, cols


def sanitize(x):
    if isinstance(x, (np.floating, np.integer)):
        x = x.item()
    if isinstance(x, np.bool_):
        return bool(x)
    if isinstance(x, dict):
        return {k: sanitize(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [sanitize(v) for v in x]
    if isinstance(x, float) and not np.isfinite(x):
        return None
    return x


def directory() -> pd.DataFrame:
    """ESPN team directory (team_id, location, short, logo, color) from the team metadata table."""
    from ..config import ARTIFACTS
    m = pd.read_csv(ARTIFACTS / "team_meta.csv", dtype={"espn_id": str})
    return m.rename(columns={"espn_id": "team_id"})[["team_id", "location", "short", "logo", "color", "team"]]


def live_tables(season: int):
    """Current-season style table (full season + recent ~8 games) with priors from last season."""
    loaded = load_season(season)
    prev_f = STYLE_DIR / f"team_season_{season - 1}.parquet"
    pri = None
    if prev_f.exists():
        prev = pd.read_parquet(prev_f)
        meta_prev = json.loads((STYLE_DIR / f"meta_{season - 1}.json").read_text())
        rho = {k: tuple(v) for k, v in json.loads((STYLE_DIR / "persistence.json").read_text()).items()}
        pri = priors_from(prev, sorted(set(loaded[1]["team_id"])), rho, meta_prev)
    df, meta, _ = build_season(season, priors=pri, loaded=loaded)
    rec, _, _ = build_season(season, priors=pri, half_life_days=21, loaded=loaded)
    return df.reset_index(drop=True), rec.reset_index(drop=True), meta


def publish_dictionary(model: StyleModel, method_html: str = ""):
    names = load_names()
    prof, cols = profiles(model)
    md = model_dict(model, names, prof, cols, method_html)
    SITE_DATA.mkdir(parents=True, exist_ok=True)
    (SITE_DATA / "style.json").write_text(json.dumps(sanitize(md), separators=(",", ":")))
    return md


def publish_season(season: int, df: pd.DataFrame, meta: dict, model: StyleModel, recent: pd.DataFrame | None = None):
    names = load_names()
    prof, cols = profiles(model)
    df = df[df["games"] >= 1].reset_index(drop=True)
    tr = model.transform(df)
    rtr = None
    if recent is not None:
        rec = recent.set_index("team_id").reindex(df["team_id"]).reset_index()
        rtr = model.transform(rec)
    cards = build_cards(df, tr, model, names, prof, cols, directory(), recent_tr=rtr, coverage=meta["_coverage"])
    out = dict(season=season, asof=df["asof"].dropna().max(), coverage=meta["_coverage"], teams=cards)
    (SITE_DATA / f"season_{season}.json").write_text(json.dumps(sanitize(out), separators=(",", ":"), allow_nan=False))
    seasons = sorted(int(f.stem.split("_")[1]) for f in SITE_DATA.glob("season_*.json"))
    (SITE_DATA / "seasons.json").write_text(json.dumps(dict(seasons=seasons, current=max(seasons))))
    return df, tr, cards

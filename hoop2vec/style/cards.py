"""Human interpretation layer: model.json (dictionary of styles) + season_<S>.json (team identity cards)."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from ..config import ARTIFACTS
from .features import EXECUTION, FEATURES
from .roster import ROSTER_FEATURES

MODEL = ARTIFACTS / "style"

BLOCK_LABEL = {"offense": "Offense", "defense": "Defense", "roster": "Personnel"}


# ------------------------------------------------------------------ labels & formatting
def feat_label(c: str) -> str:
    if c.startswith("off_"):
        f = c[4:]
        lab = FEATURES.get(f, EXECUTION.get(f, (None,) * 5))
        return lab[4] if f in FEATURES else lab[3]
    if c.startswith("def_"):
        f = c[4:]
        lab = FEATURES[f][4] if f in FEATURES else EXECUTION[f][3]
        return f"Opp. {lab[0].lower() + lab[1:]} allowed"
    return ROSTER_FEATURES.get(c, (c,))[0] if c in ROSTER_FEATURES else c


PCT_FEATS = {f for f, v in FEATURES.items() if v[3] == "rate"} | {"rim_fg", "mid_fg", "three_fg", "ft_pct", "late_fg", "efg"}


def fmt_value(c: str, v: float) -> str:
    if v is None or not np.isfinite(v):
        return "–"
    base = c[4:] if c.startswith(("off_", "def_")) else c
    if base in PCT_FEATS:
        return f"{100 * v:.1f}%"
    if base in ("ft_rate",):
        return f"{100 * v:.1f}"
    if base in ("poss_len",):
        return f"{v:.1f}s"
    if base in ("two_dist",):
        return f"{v:.1f} ft"
    if base in ("ppp", "trans_ppp"):
        return f"{v:.2f}"
    if base in ("height", "front_height"):
        return f"{int(v // 12)}'{v % 12:.1f}\""
    if base in ("height_spread",):
        return f"{v:.1f} in"
    if base in ("usage_hhi",):
        return f"{v:.3f}"
    if base in ("top_usage", "ast_conc", "bench_min", "lineup_conc", "guard_usage", "big_three", "guard_reb",
                "wing_share", "big_share"):
        return f"{100 * v:.0f}%"
    if base in ("eff_rotation",):
        return f"{v:.1f} players"
    if base in ("star_onoff",):
        return f"{v:+.1f}"
    return f"{v:.1f}"


# ------------------------------------------------------------------ model dictionary
CLASH = [
    dict(off="off_rim_rate", def_="def_rim_rate", label="Rim attack", why="Rim attempts taken vs. rim attempts the defense concedes"),
    dict(off="off_three_rate", def_="def_three_rate", label="3-point volume", why="3PA rate vs. 3PA rate allowed"),
    dict(off="off_orb_rate", def_="def_orb_rate", label="Offensive glass", why="Offensive rebounding vs. boards conceded"),
    dict(off="off_tov_rate", def_="def_tov_rate", label="Ball security vs. pressure", why="Low turnovers vs. a defense that doesn't force them", inv=True),
    dict(off="off_trans_rate", def_="def_trans_rate", label="Transition", why="Pushes the pace vs. lets opponents run"),
    dict(off="off_ft_rate", def_="def_ft_rate", label="Free throws", why="Draws fouls vs. fouls a lot"),
    dict(off="off_ast_fgm", def_="def_ast_fgm", label="Ball movement", why="Assisted offense vs. allows assisted looks"),
    dict(off="off_smid_rate", def_="def_smid_rate", label="Short mid-range / floaters", why="Lives in the paint/elbows vs. concedes it"),
    dict(off="off_pullup_rate", def_="def_pullup_rate", label="Pull-up jumpers", why="Pull-up shooting vs. a defense that concedes pull-ups (drop-coverage signature)"),
    dict(off="off_c3_share", def_="def_c3_share", label="Corner 3s", why="Corner-three creation vs. corner threes allowed"),
    dict(off="off_putback_rate", def_="def_putback_rate", label="Second-chance finishes", why="Putbacks per offensive board vs. putbacks allowed"),
    dict(off="off_blocked", def_="def_blocked", label="Shot blocking", why="Avoids blocks vs. a defense that doesn't block shots", inv=True),
    dict(off="off_lob_rate", def_="def_lob_rate", label="Lobs / roll finishes", why="Alley-oop finishes vs. lobs allowed"),
]


def archetype_profiles(model, hist: pd.DataFrame, tr) -> dict:
    """Membership-weighted mean z of every raw feature per archetype (for signatures + matchup matching)."""
    from .model import block_columns
    cols = sum(block_columns().values(), [])
    cols = [c for c in cols if c in hist]
    dev = hist[cols] - hist.groupby("season")[cols].transform("mean")
    Z = (dev / dev.std()).fillna(0)
    out = {}
    for b in model.spec["blocks"]:
        P = tr[b]["membership"]
        out[b] = (P.T @ Z.to_numpy()) / P.sum(0)[:, None]
    return out, cols


def pad_names(model, names: dict) -> dict:
    out = {}
    for b, s in model.spec["blocks"].items():
        nm = names.get(b, {})
        fs = list(nm.get("factors", []))
        fs += [{"name": f"{BLOCK_LABEL[b]} factor {i + 1}", "low": "Low", "high": "High", "desc": ""} for i in range(len(fs), s["k"])]
        ar = list(nm.get("archetypes", []))
        ar += [{"name": f"{BLOCK_LABEL[b]} type {a + 1}", "desc": ""} for a in range(len(ar), len(s["gmm"]["weights"]))]
        out[b] = {"factors": fs[:s["k"]], "archetypes": ar[:len(s["gmm"]["weights"])]}
    return out


def model_dict(model, names: dict, profiles, prof_cols, method_html: str) -> dict:
    names = pad_names(model, names)
    blocks = {}
    for b, s in model.spec["blocks"].items():
        nm = names[b]
        L = np.array(s["loadings"])
        factors = []
        for i in range(s["k"]):
            order = np.argsort(-np.abs(L[:, i]))
            fn = nm["factors"][i] if i < len(nm["factors"]) else {"name": f"{BLOCK_LABEL[b]} factor {i + 1}", "low": "Low", "high": "High", "desc": ""}
            factors.append(dict(**fn, var=round(s["var_explained"][i], 3),
                                loadings=[dict(feat=s["cols"][j], label=feat_label(s["cols"][j]), w=round(float(L[j, i]), 3))
                                          for j in order[:10] if abs(L[j, i]) >= 0.15]))
        G = np.array(s["gmm"]["means"])
        archs = []
        for a in range(len(G)):
            an = nm["archetypes"][a] if a < len(nm["archetypes"]) else {"name": f"{BLOCK_LABEL[b]} type {a + 1}", "desc": ""}
            sig = []
            for i in np.argsort(-np.abs(G[a])):
                if abs(G[a, i]) < 0.55 or len(sig) >= 4:
                    continue
                sig.append(factors[i]["high"] if G[a, i] > 0 else factors[i]["low"])
            archs.append(dict(**an, signature=sig, centroid=np.round(G[a], 3).tolist(),
                              share=round(float(s["gmm"]["weights"][a]), 3)))
        blocks[b] = dict(factors=factors, archetypes=archs)
    return dict(blocks=blocks, clash=[{"off": c["off"], "def": c["def_"], "label": c["label"], "why": c["why"],
                                       "inv": c.get("inv", False)} for c in CLASH],
                n_train=len(model.spec["blocks"]["offense"]["hist_scores"]),
                train_seasons=model.spec["train_seasons"], method_html=method_html)


# ------------------------------------------------------------------ team cards
def _pct(series: pd.Series) -> pd.Series:
    return series.rank(pct=True) * 100


def build_cards(df: pd.DataFrame, tr: dict, model, names: dict, profiles, prof_cols, directory: pd.DataFrame,
                recent_tr: dict | None = None, coverage: dict | None = None) -> list[dict]:
    names = pad_names(model, names)
    df = df.reset_index(drop=True)
    feat_cols = [c for c in df.columns if (c.startswith(("off_", "def_")) and not c.endswith("_sd"))
                 or c in ROSTER_FEATURES]
    P = {c: _pct(df[c]) for c in feat_cols if df[c].notna().any()}
    mu = {c: df[c].mean() for c in P}
    df["rank"] = df["adj_em"].rank(ascending=False, method="min").astype(int)
    df["rank_o"] = df["adj_o"].rank(ascending=False, method="min").astype(int)
    df["rank_d"] = df["adj_d"].rank(ascending=True, method="min").astype(int)
    tempo_p = _pct(df["tempo"])
    d = directory.set_index("team_id") if directory is not None else pd.DataFrame()
    arch_names = {b: [a["name"] for a in names[b]["archetypes"]] for b in names}
    fac = {b: names[b]["factors"] for b in names}
    cards = []
    for i, r in df.iterrows():
        def p(c):
            v = P.get(c)
            return float(v.iloc[i]) if v is not None and np.isfinite(v.iloc[i]) else None

        def val(c):
            return fmt_value(c, r.get(c, np.nan))

        blocks = {}
        traits = []
        for b, res in tr.items():
            S, SD, PM, PC = res["scores"][i], res["sd"][i], res["membership"][i], res["pct"][i]
            o = np.argsort(-PM)
            blocks[b] = dict(scores=np.round(S, 3).tolist(), sd=np.round(SD, 3).tolist(), pct=np.round(PC, 3).tolist(),
                             members=np.round(PM, 3).tolist(), primary=int(o[0]), secondary=int(o[1]))
            for k in range(len(S)):
                if PC[k] >= 0.88 or PC[k] <= 0.12:
                    f = fac[b][k]
                    traits.append(dict(block=b, idx=k, ext=abs(PC[k] - 0.5),
                                       text=f"{f['high'] if PC[k] > 0.5 else f['low']} ({BLOCK_LABEL[b].lower()}, {round(PC[k] * 100)} pct)"))
        traits.sort(key=lambda x: -x["ext"])
        traits = traits[:5]

        # ---- evidence: top-loading features of the team's signature traits (fallback: biggest factors)
        ev, seen = [], set()
        picks = [(t["block"], t["idx"]) for t in traits] or [(b, 0) for b in tr]
        for b, k in picks:
            s = model.spec["blocks"][b]
            L = np.array(s["loadings"])[:, k]
            for j in np.argsort(-np.abs(L))[:3]:
                c = s["cols"][j]
                if c in seen or p(c) is None:
                    continue
                seen.add(c)
                ev.append(dict(label=feat_label(c), value=val(c), avg=fmt_value(c, mu[c]), pct=round(p(c))))
        for c in ("off_ppp", "def_ppp"):
            if c not in seen and p(c) is not None:
                ev.append(dict(label=feat_label(c) if c == "off_ppp" else "Opp. points per possession allowed",
                               value=val(c), avg=fmt_value(c, mu[c]), pct=round(p(c))))

        strengths, weaknesses = rules(r, p, val)
        fav, risky = matchups(r, p, profiles, prof_cols, arch_names)

        # ---- recent change
        changes = []
        recent = None
        if recent_tr is not None:
            recent = {b: np.round(recent_tr[b]["scores"][i], 3).tolist() for b in recent_tr}
            if r["games"] >= 8:
                for b in recent_tr:
                    for k, (a, c) in enumerate(zip(tr[b]["scores"][i], recent_tr[b]["scores"][i])):
                        if abs(c - a) >= 0.7:
                            f = fac[b][k]
                            changes.append(f"Lately more {(f['high'] if c > a else f['low']).lower()} "
                                           f"({f['name']}, {c - a:+.1f} SD over the last ~8 games)")
        # ---- confidence
        g = float(r["games"])
        sharp = float(np.mean([max(tr[b]["membership"][i]) for b in tr]))
        sdm = float(np.mean([np.mean(tr[b]["sd"][i]) for b in tr]))
        score = 100 * (0.45 * min(1, g / 20) + 0.3 * sharp + 0.25 * max(0, 1 - sdm / 0.8))
        if coverage and not coverage.get("descriptors", True):
            score -= 8
        level = "High" if score >= 70 else "Medium" if score >= 50 else "Low"
        notes = [f"{int(g)} D-I games"]
        notes.append("clear archetype fit" if sharp > 0.7 else "blend of archetypes" if sharp < 0.5 else "fairly clear fit")
        if g < 8:
            notes.append("early season: partly based on last season's profile")
        if coverage and not coverage.get("descriptors", True):
            notes.append("shot-type detail not recorded by ESPN this season")

        # ---- context
        ctx = [f"Plays at {r['tempo']:.1f} possessions per 40 ({round(tempo_p.iloc[i])} pct for pace)"]
        if isinstance(r.get("star_name"), str):
            so = r.get("star_onoff")
            ctx.append(f"Top option: {r['star_name']} ({100 * r.get('star_usage', 0):.0f}% of team usage)"
                       + (f"; on/off net swing {so:+.1f} pts/100 (shrunk)" if np.isfinite(so) else ""))
        vol = np.nanmean([r.get(f"vol_{f}_off", np.nan) for f in ("three_rate", "rim_rate", "poss_len", "trans_rate", "ast_fgm")])
        if np.isfinite(vol):
            ctx.append("Style is very consistent from game to game" if vol < 0.85 else
                       "Style shifts a lot from game to game: adaptable or inconsistent" if vol > 1.25 else
                       "Typical game-to-game style variation")
        if np.isfinite(r.get("experience", np.nan)):
            ctx.append(f"Talent context: experience {r['experience']:.2f} (1 = all freshmen, 4 = all seniors), "
                       f"recruiting pedigree {r.get('recruit', 0):.0f}/100" + (f", top-3 BPM {r['bpm_top3']:.1f}" if np.isfinite(r.get('bpm_top3', np.nan)) else ""))

        oa, da, ra = (arch_names[b][blocks[b]["primary"]] for b in ("offense", "defense", "roster"))
        head = f"{oa} offense, {da} defense, {ra} roster."
        if traits:
            head += " Signature: " + "; ".join(t["text"].split(" (")[0].lower() for t in traits[:2]) + "."
        dirow = d.loc[r["team_id"]] if r["team_id"] in d.index else {}
        feats = {}
        clash_cols = {x["off"] for x in CLASH} | {x["def_"] for x in CLASH}
        for c in P:
            if c in clash_cols:
                pv = p(c)
                if pv is not None:
                    feats[c] = [round(float(r[c]), 4), round(pv)]
        cards.append(dict(
            id=r["team_id"], name=(f"{dirow['location']}" if len(dirow) else None) or r.get("team_name"),
            full=r.get("team_name"), short=dirow.get("short") if len(dirow) else None, abbr=r.get("team_abbr"),
            conf=r.get("conf"), logo=dirow.get("logo") if len(dirow) else None, color=dirow.get("color") if len(dirow) else None,
            games=int(g), rank=int(r["rank"]), rank_o=int(r["rank_o"]), rank_d=int(r["rank_d"]),
            adj_o=round(float(r["adj_o"]), 1), adj_d=round(float(r["adj_d"]), 1), adj_em=round(float(r["adj_em"]), 1),
            tempo=round(float(r["tempo"]), 1), blocks=blocks, recent=recent, traits=[{k: v for k, v in t.items() if k != "ext"} for t in traits],
            headline=head, evidence=ev[:12], strengths=strengths, weaknesses=weaknesses,
            matchups=dict(favorable=fav, risky=risky), changes=changes, context=ctx,
            confidence=dict(level=level, score=round(score), notes=notes), feats=feats,
        ))
    return cards


# ------------------------------------------------------------------ rules
def rules(r, p, val):
    S, W = [], []

    def add(lst, cond, prio, text):
        if cond:
            lst.append((prio, text))

    g = lambda c: p(c) if p(c) is not None else 50  # noqa: E731
    # offense
    add(S, g("off_rim_rate") >= 70 and g("off_rim_fg") >= 65, g("off_rim_fg"),
        f"Lives at the rim and finishes: {val('off_rim_rate')} of shots at the rim, {val('off_rim_fg')} FG there")
    add(S, g("off_three_rate") >= 65 and g("off_three_fg") >= 65, g("off_three_fg"),
        f"High-volume, accurate shooting: {val('off_three_rate')} 3PA rate at {val('off_three_fg')}")
    add(S, g("off_three_rate") < 65 and g("off_three_fg") >= 88, g("off_three_fg"), f"Elite 3-point accuracy ({val('off_three_fg')})")
    add(S, g("off_orb_rate") >= 80, g("off_orb_rate"), f"Owns the offensive glass: rebounds {val('off_orb_rate')} of its misses")
    add(S, g("off_tov_rate") <= 18, 100 - g("off_tov_rate"), f"Takes care of the ball: {val('off_tov_rate')} turnover rate")
    add(S, g("off_trans_rate") >= 70 and g("off_trans_ppp") >= 60, g("off_trans_ppp"),
        f"Dangerous in transition: {val('off_trans_rate')} of possessions end in a transition shot, {val('off_trans_ppp')} pts per chance")
    add(S, g("off_ft_rate") >= 82, g("off_ft_rate"), f"Gets to the line (FTA rate {val('off_ft_rate')})")
    add(S, g("off_ft_pct") >= 85, g("off_ft_pct"), f"Closes games at the line ({val('off_ft_pct')} FT)")
    add(S, g("off_late_fg") >= 85, g("off_late_fg"), f"Executes late in the shot clock ({val('off_late_fg')} FG)")
    add(S, g("off_ast_fgm") >= 85, g("off_ast_fgm"), f"Ball movement: {val('off_ast_fgm')} of makes are assisted")
    add(W, g("off_three_rate") >= 65 and g("off_three_fg") <= 35, g("off_three_rate"),
        f"Lives by the 3 without making them: {val('off_three_rate')} 3PA rate, {val('off_three_fg')}")
    add(W, g("off_tov_rate") >= 80, g("off_tov_rate"), f"Turnover-prone: {val('off_tov_rate')} turnover rate")
    add(W, g("off_live_tov") >= 85, g("off_live_tov"), f"Live-ball turnovers feed opponents' break ({val('off_live_tov')} per poss)")
    add(W, g("off_ft_pct") <= 15, 100 - g("off_ft_pct"), f"Poor free throw shooting ({val('off_ft_pct')}), a risk in close games")
    add(W, g("off_rim_fg") <= 18, 100 - g("off_rim_fg"), f"Struggles to finish at the rim ({val('off_rim_fg')})")
    add(W, g("off_blocked") >= 85, g("off_blocked"), f"Gets shots blocked often ({val('off_blocked')} of 2PA)")
    add(W, g("off_late_clock") >= 75 and g("off_late_fg") <= 35, g("off_late_clock"),
        f"Bogs down late in the clock: {val('off_late_clock')} of shots come late, {val('off_late_fg')} FG")
    add(W, g("off_orb_rate") <= 12, 100 - g("off_orb_rate"), f"One-and-done possessions: rebounds only {val('off_orb_rate')} of misses")
    so = r.get("star_onoff", np.nan)
    if np.isfinite(so) and so >= 12 and isinstance(r.get("star_name"), str):
        W.append((80 + so, f"Star-dependent: {r['star_onoff']:+.1f} pts/100 swing with {r['star_name']} on vs. off (shrunk)"))
    add(W, g("usage_hhi") >= 90, g("usage_hhi"), f"Offense concentrated in few hands (top usage share {val('top_usage')})")
    # defense
    add(S, g("def_rim_fg") <= 20, 100 - g("def_rim_fg"), f"Rim protection: opponents shoot {val('def_rim_fg')} at the rim")
    add(S, g("def_tov_rate") >= 82, g("def_tov_rate"), f"Forces turnovers on {val('def_tov_rate')} of possessions")
    add(S, g("def_orb_rate") <= 18, 100 - g("def_orb_rate"), f"Finishes defensive possessions: allows only {val('def_orb_rate')} offensive rebounds")
    add(S, g("def_three_rate") <= 18, 100 - g("def_three_rate"), f"Runs shooters off the line: opponents' 3PA rate {val('def_three_rate')}")
    add(S, g("def_ft_rate") <= 18, 100 - g("def_ft_rate"), f"Defends without fouling (opp. FTA rate {val('def_ft_rate')})")
    add(S, g("def_trans_rate") <= 15, 100 - g("def_trans_rate"), f"Gets back: opponents rarely score in transition ({val('def_trans_rate')} of poss.)")
    add(S, g("def_blocked") >= 88, g("def_blocked"), f"Shot blocking: blocks {val('def_blocked')} of opponents' 2PA")
    add(W, g("def_three_rate") >= 82, g("def_three_rate"), f"Concedes lots of 3PA ({val('def_three_rate')}); hot-shooting teams can bury it")
    add(W, g("def_orb_rate") >= 82, g("def_orb_rate"), f"Gives up offensive rebounds ({val('def_orb_rate')})")
    add(W, g("def_ft_rate") >= 82, g("def_ft_rate"), f"Fouls a lot (opp. FTA rate {val('def_ft_rate')})")
    add(W, g("def_trans_rate") >= 82, g("def_trans_rate"), f"Leaky in transition ({val('def_trans_rate')} of opp. possessions)")
    add(W, g("def_rim_rate") >= 80 and g("def_rim_fg") >= 60, g("def_rim_rate"),
        f"Opponents get to the rim ({val('def_rim_rate')} of shots) and finish ({val('def_rim_fg')})")
    add(W, g("def_tov_rate") <= 12, 100 - g("def_tov_rate"), f"Rarely forces turnovers ({val('def_tov_rate')})")
    S = [t for _, t in sorted(S, key=lambda x: -x[0])][:5]
    W = [t for _, t in sorted(W, key=lambda x: -x[0])][:5]
    return S, W


PAIRS = [  # (offense feature, defense feature, plain-English what)
    ("off_three_rate", "def_three_rate", "3-point volume"),
    ("off_rim_rate", "def_rim_rate", "rim attempts"),
    ("off_orb_rate", "def_orb_rate", "offensive rebounding"),
    ("off_trans_rate", "def_trans_rate", "transition chances"),
    ("off_ft_rate", "def_ft_rate", "free throws"),
    ("off_smid_rate", "def_smid_rate", "short mid-range looks"),
    ("off_pullup_rate", "def_pullup_rate", "pull-up jumpers"),
    ("off_ast_fgm", "def_ast_fgm", "assisted shots"),
]


def matchups(r, p, profiles, cols, arch_names):
    fav, risky = [], []
    ci = {c: j for j, c in enumerate(cols)}
    po, pdf = profiles["offense"], profiles["defense"]
    for oc, dc, what in PAIRS:
        if oc not in ci or dc not in ci:
            continue
        # this team's DEFENSE vs offense archetypes that rely on `what`
        dp = p(dc)
        if dp is not None:
            a = int(np.argmax(po[:, ci[oc]]))
            if po[a, ci[oc]] > 0.4:
                if dp >= 78:
                    risky.append((dp, f"{arch_names['offense'][a]} offenses: they generate {what}, and this defense allows a lot of it ({round(dp)} pct)"))
                elif dp <= 22:
                    fav.append((100 - dp, f"{arch_names['offense'][a]} offenses: this defense takes away {what} ({round(100 - dp)} pct suppression)"))
        # this team's OFFENSE vs defense archetypes
        op = p(oc)
        if op is not None and op >= 75:
            hi = int(np.argmax(pdf[:, ci[dc]]))
            lo = int(np.argmin(pdf[:, ci[dc]]))
            if pdf[hi, ci[dc]] > 0.4:
                fav.append((op, f"{arch_names['defense'][hi]} defenses: they concede {what}, this offense's bread and butter ({round(op)} pct)"))
            if pdf[lo, ci[dc]] < -0.4:
                risky.append((op, f"{arch_names['defense'][lo]} defenses: they take away {what}, which this offense leans on"))
    dedupe = lambda L: list(dict.fromkeys(t for _, t in sorted(L, key=lambda x: -x[0])))[:3]  # noqa: E731
    return dedupe(fav), dedupe(risky)


def load_names() -> dict:
    return json.loads((MODEL / "style_names.json").read_text())

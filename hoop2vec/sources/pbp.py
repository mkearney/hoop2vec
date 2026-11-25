"""ESPN play-by-play: schedule discovery + game summary parsing (the style layer's raw material).

Each game summary is parsed on the fly into compact rows (we never keep the raw JSON):
  - games:        one row per game (teams, score, neutral flag, venue, tournament note)
  - team_games:   one row per team per game (box score + play-by-play derived counts)
  - player_games: one row per player per game (box score + PBP shot diet + on-court stints)
  - lineups:      seconds / possessions / points per 5-man lineup per game
"""
from __future__ import annotations

import json
import re
import time
import urllib.request
from collections import defaultdict
from datetime import date, timedelta

from ..config import ESPN_BASE, PERIOD_SECONDS, REG_PERIODS, USER_AGENT

BASE = ESPN_BASE
UA = {"User-Agent": USER_AGENT}


def get_json(url: str, tries: int = 4, timeout: int = 30):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code in (400, 404):
                return None
            time.sleep(1.5 * (i + 1))
        except Exception:
            time.sleep(1.5 * (i + 1))
    return None


def season_dates(season: int, end: date | None = None):
    """Season 2026 = 2025-26. Regular season starts early Nov; tourney ends early April."""
    d, last = date(season - 1, 11, 1), date(season, 4, 12)
    if end is not None:
        last = min(last, end)
    while d <= last:
        yield d
        d += timedelta(days=1)


def scoreboard(day: date):
    js = get_json(f"{BASE}/scoreboard?dates={day:%Y%m%d}&groups=50&limit=500")
    out = []
    for e in (js or {}).get("events", []):
        comp = e["competitions"][0]
        st = comp.get("status", e.get("status", {})).get("type", {})
        notes = comp.get("notes") or []
        out.append(dict(game_id=e["id"], date=day.isoformat(), completed=bool(st.get("completed")),
                        season_type=e.get("season", {}).get("type"),
                        note=notes[0].get("headline", "") if notes else ""))
    return out


# --------------------------------------------------------------------------- helpers
PERIOD_LEN = lambda p: PERIOD_SECONDS if p <= REG_PERIODS else 300  # noqa: E731  halves or quarters, then 5-min OTs


def clock_secs(s: str) -> float:
    s = str(s or "0")
    if ":" in s:
        m, sec = s.split(":")
        return int(m) * 60 + float(sec)
    try:
        return float(s)
    except ValueError:
        return 0.0


def elapsed(period: int, clock: str) -> float:
    base = sum(PERIOD_LEN(p) for p in range(1, period))
    return base + PERIOD_LEN(period) - clock_secs(clock)


DIST_RE = re.compile(r"(\d+)-foot")
FT_RE = re.compile(r"free throw (\d) of (\d)", re.I)


def shot_info(p: dict) -> dict:
    t = (p.get("text") or "").lower()
    three = "three point" in t
    m = DIST_RE.search(t)
    dist = int(m.group(1)) if m else None
    c = p.get("coordinate") or {}
    x, y = c.get("x"), c.get("y")
    if x is None or not (-5 <= x <= 55) or not (-10 <= (y or -99) <= 60):
        x = y = None
    if dist is None and x is not None:
        dist = round(((x - 25) ** 2 + y ** 2) ** 0.5)
    typ = (p.get("type") or {}).get("text", "")
    kind = ("dunk" if ("dunk" in t or typ == "DunkShot") else
            "tip" if ("tip" in t or typ == "TipShot") else
            "layup" if ("layup" in t or typ == "LayUpShot") else
            "hook" if "hook" in t else "jumper")
    rim = kind in ("dunk", "tip", "layup") or (not three and dist is not None and dist <= 4)
    if three:
        zone = "c3" if (y is not None and y <= 8) else "ab3"
    elif rim:
        zone = "rim"
    elif dist is not None and dist <= 14:
        zone = "smid"
    else:
        zone = "lmid"
    return dict(
        three=three, dist=dist, kind=kind, zone=zone,
        pullup="pullup" in t or "pull up" in t, stepback="step back" in t,
        turnaround=("turnaround" in t or "fadeaway" in t), driving="driving" in t,
        floater="floating" in t or "floater" in t, alleyoop="alley oop" in t,
        assisted="assist" in t,
    )


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def made_att(v):
    try:
        a, b = str(v).split("-")
        return float(a), float(b)
    except ValueError:
        return 0.0, 0.0


# --------------------------------------------------------------------------- parse
def _pickcenter(js: dict) -> dict:
    """Closing spread / total from ESPN's pickcenter (benchmark only). home_line < 0 = home favored."""
    for pc in js.get("pickcenter") or []:
        sp, ou = pc.get("spread"), pc.get("overUnder")
        try:
            sp = float(sp) if sp is not None else None
        except (TypeError, ValueError):
            sp = None
        try:
            ou = float(ou) if ou is not None else None
        except (TypeError, ValueError):
            ou = None
        if sp is None and ou is None:
            continue
        home_fav = bool((pc.get("homeTeamOdds") or {}).get("favorite"))
        away_fav = bool((pc.get("awayTeamOdds") or {}).get("favorite"))
        line = None
        if sp is not None:
            line = -abs(sp) if home_fav else abs(sp) if away_fav else sp
        return dict(home_line=line, total_line=ou, line_provider=(pc.get("provider") or {}).get("name"))
    return dict(home_line=None, total_line=None, line_provider=None)


def parse_summary(js: dict, season: int):
    comp = js["header"]["competitions"][0]
    teams = {c["homeAway"]: c for c in comp["competitors"]}
    if "home" not in teams or "away" not in teams:
        return None
    hid, aid = teams["home"]["team"]["id"], teams["away"]["team"]["id"]
    opp = {hid: aid, aid: hid}
    gi = js.get("gameInfo", {})
    venue = gi.get("venue", {})
    notes = comp.get("notes") or []
    game = dict(
        game_id=js["header"]["id"], season=season, date=comp.get("date", "")[:10],
        season_type=js["header"].get("season", {}).get("type"),
        home_id=hid, away_id=aid,
        home_name=teams["home"]["team"].get("displayName"), away_name=teams["away"]["team"].get("displayName"),
        home_score=num(teams["home"].get("score")), away_score=num(teams["away"].get("score")),
        neutral=bool(comp.get("neutralSite")), conf_game=bool(comp.get("conferenceCompetition")),
        note=notes[0].get("headline", "") if notes else "",
        venue=venue.get("fullName"), venue_city=venue.get("address", {}).get("city"),
        venue_state=venue.get("address", {}).get("state"), attendance=gi.get("attendance"),
        n_periods=max([len(c.get("linescores", [])) for c in comp["competitors"]] + [0]),
    )
    game.update(_pickcenter(js))
    if not game["n_periods"]:
        return None

    # ---------------- team box
    tg = {}
    for t in js.get("boxscore", {}).get("teams", []):
        tid = t["team"]["id"]
        st = {s["name"]: s.get("displayValue") for s in t.get("statistics", [])}
        fgm, fga = made_att(st.get("fieldGoalsMade-fieldGoalsAttempted"))
        tpm, tpa = made_att(st.get("threePointFieldGoalsMade-threePointFieldGoalsAttempted"))
        ftm, fta = made_att(st.get("freeThrowsMade-freeThrowsAttempted"))
        tg[tid] = dict(
            game_id=game["game_id"], season=season, team_id=tid, opp_id=opp.get(tid),
            is_home=tid == hid, neutral=game["neutral"],
            team_name=t["team"].get("displayName"), team_abbr=t["team"].get("abbreviation"),
            pts=game["home_score"] if tid == hid else game["away_score"],
            fgm=fgm, fga=fga, tpm=tpm, tpa=tpa, ftm=ftm, fta=fta,
            oreb=num(st.get("offensiveRebounds")), dreb=num(st.get("defensiveRebounds")),
            reb=num(st.get("totalRebounds")), ast=num(st.get("assists")), stl=num(st.get("steals")),
            blk=num(st.get("blocks")), tov=num(st.get("totalTurnovers") or st.get("turnovers")),
            pf=num(st.get("fouls")), tech=num(st.get("technicalFouls")),
        )
    if len(tg) != 2:
        return None

    # ---------------- player box
    players, starters, pinfo = {}, defaultdict(set), {}
    for blk in js.get("boxscore", {}).get("players", []):
        tid = blk["team"]["id"]
        for sblock in blk.get("statistics", [])[:1]:
            keys = sblock.get("keys", [])
            for a in sblock.get("athletes", []):
                ath = a.get("athlete", {})
                pid = ath.get("id")
                if not pid:
                    continue
                vals = dict(zip(keys, a.get("stats", [])))
                fgm, fga = made_att(vals.get("fieldGoalsMade-fieldGoalsAttempted"))
                tpm, tpa = made_att(vals.get("threePointFieldGoalsMade-threePointFieldGoalsAttempted"))
                ftm, fta = made_att(vals.get("freeThrowsMade-freeThrowsAttempted"))
                pinfo[pid] = tid
                if a.get("starter"):
                    starters[tid].add(pid)
                players[pid] = dict(
                    game_id=game["game_id"], season=season, team_id=tid, athlete_id=pid,
                    name=ath.get("displayName"), pos=(ath.get("position") or {}).get("abbreviation"),
                    jersey=ath.get("jersey"), starter=bool(a.get("starter")), dnp=bool(a.get("didNotPlay")),
                    min=num(vals.get("minutes")), pts=num(vals.get("points")), fgm=fgm, fga=fga, tpm=tpm,
                    tpa=tpa, ftm=ftm, fta=fta, oreb=num(vals.get("offensiveRebounds")),
                    dreb=num(vals.get("defensiveRebounds")), ast=num(vals.get("assists")),
                    tov=num(vals.get("turnovers")), stl=num(vals.get("steals")), blk=num(vals.get("blocks")),
                    pf=num(vals.get("fouls")),
                )

    plays = js.get("plays") or []
    pbp_ok = len(plays) > 150
    pf = _parse_pbp(plays, hid, aid, opp, starters, pinfo, game) if pbp_ok else None
    game["pbp"] = pbp_ok
    lineups = []
    if pf:
        tcounts, pcounts, lineups, meta = pf
        game.update(meta)
        for tid in tg:
            tg[tid].update({f"p_{k}": v for k, v in tcounts[tid].items()})
        for pid, d in pcounts.items():
            if pid in players:
                players[pid].update({f"p_{k}": v for k, v in d.items()})
    return game, list(tg.values()), list(players.values()), lineups


def _parse_pbp(plays, hid, aid, opp, starters, pinfo, game):
    shot_clock = 35 if game["season"] <= 2015 else 30
    T = {hid: defaultdict(float), aid: defaultdict(float)}
    P = defaultdict(lambda: defaultdict(float))
    on = {t: set(starters.get(t, set())) for t in (hid, aid)}
    last_act = defaultdict(float)
    lineup_acc = defaultdict(lambda: defaultdict(float))  # (tid, lineup) -> stats

    off, t0, trig, reset_t = None, 0.0, "period", 0.0
    pending = None
    last_t, cur_period = 0.0, 1
    last_orb_t = {hid: -99, aid: -99}
    n_shots = n_sync = 0
    poss_lengths = {hid: [], aid: []}

    def lkey(tid):
        return tuple(sorted(on[tid])) if len(on[tid]) == 5 else None

    def advance(t):
        nonlocal last_t
        dt = max(0.0, t - last_t)
        if dt:
            for tid in (hid, aid):
                for pid in on[tid]:
                    P[pid]["on_sec"] += dt
                k = lkey(tid)
                if k:
                    lineup_acc[(tid, k)]["sec"] += dt
        last_t = t

    def start_poss(team, t, why):
        nonlocal off, t0, trig, reset_t
        if off is not None and off in poss_lengths:
            poss_lengths[off].append(t - t0)
        off, t0, trig, reset_t = team, t, why, t
        if team is None:
            return
        T[team]["poss"] += 1
        T[team][f"poss_{why}"] += 1
        d = opp[team]
        for pid in on[team]:
            P[pid]["on_off_poss"] += 1
        for pid in on[d]:
            P[pid]["on_def_poss"] += 1
        k = lkey(team)
        if k:
            lineup_acc[(team, k)]["off_poss"] += 1
        k = lkey(d)
        if k:
            lineup_acc[(d, k)]["def_poss"] += 1

    def touch(pid, tid, t):
        if pid is None or tid not in on:
            return
        last_act[pid] = t
        if pid not in on[tid]:
            on[tid].add(pid)
            if len(on[tid]) > 5:
                drop = min((p for p in on[tid] if p != pid), key=lambda p: last_act.get(p, -1))
                on[tid].discard(drop)

    def credit_points(tid, pts):
        for pid in on[tid]:
            P[pid]["on_pts_for"] += pts
        for pid in on[opp[tid]]:
            P[pid]["on_pts_against"] += pts
        k = lkey(tid)
        if k:
            lineup_acc[(tid, k)]["pts_for"] += pts
        k = lkey(opp[tid])
        if k:
            lineup_acc[(opp[tid], k)]["pts_against"] += pts

    for i, p in enumerate(plays):
        typ = p.get("type", {}).get("text", "")
        txt = p.get("text", "") or ""
        low = txt.lower()
        per = p.get("period", {}).get("number", 1)
        t = elapsed(per, p.get("clock", {}).get("displayValue", "0:00"))
        tid = (p.get("team") or {}).get("id")
        parts = [x.get("athlete", {}).get("id") for x in p.get("participants", []) or []]
        actor = parts[0] if parts else None

        if per != cur_period:
            advance(sum(PERIOD_LEN(q) for q in range(1, per)))
            cur_period = per
            pending = None
            start_poss(None, last_t, "period")
            off = None
            trig = "period"
            t0 = reset_t = last_t
        advance(t)

        is_ft = "free throw" in low
        if pending is not None and typ != "Substitution" and not (is_ft and tid == opp[pending[0]]):
            start_poss(pending[0], pending[1], "make")
            pending = None

        if typ == "Substitution" and tid in on and actor:
            if "subbing in" in low or " enters" in low:
                on[tid].add(actor)
                last_act[actor] = t
                if len(on[tid]) > 5:
                    pass  # an out usually follows at the same timestamp
            elif "subbing out" in low or " goes to the bench" in low:
                on[tid].discard(actor)
            continue
        if tid not in on:
            continue
        if actor and pinfo.get(actor) == tid:
            touch(actor, tid, t)
            if len(on[tid]) > 5:
                drop = min((q for q in on[tid] if q != actor), key=lambda q: last_act.get(q, -1))
                on[tid].discard(drop)

        if p.get("shootingPlay") and not is_ft:
            n_shots += 1
            if off != tid:
                n_sync += 1
                start_poss(tid, t, "unknown")
            s = shot_info(p)
            made = bool(p.get("scoringPlay"))
            since_start = t - t0
            since_reset = t - reset_t
            timed = trig not in ("unknown", "period") or since_start < 40
            R, Pp = T[tid], P[actor] if actor else defaultdict(float)
            z = s["zone"]
            for D in (R, Pp):
                D["fga"] += 1
                D[f"{z}_a"] += 1
                D[f"{s['kind']}_a"] += 1
                if made:
                    D["fgm"] += 1
                    D[f"{z}_m"] += 1
                    D[f"{s['kind']}_m"] += 1
                    D[f"{z}_ast" if s["assisted"] else f"{z}_unast"] += 1
                for f in ("pullup", "stepback", "turnaround", "driving", "floater", "alleyoop"):
                    if s[f]:
                        D[f"{f}_a"] += 1
                        D[f"{f}_m"] += made
                if s["dist"] is not None and not s["three"]:
                    D["two_dist_sum"] += s["dist"]
                    D["two_dist_n"] += 1
            if t - last_orb_t[tid] <= 4 and z == "rim":
                R["putback_a"] += 1
                R["putback_m"] += made
            if trig not in ("unknown",) and timed:
                R["timed_fga"] += 1
                if since_start <= 10 and trig in ("steal", "drb", "deadball", "make"):
                    R["trans_a"] += 1
                    R[f"trans_{trig}_a"] += 1
                    R["trans_pts"] += p.get("scoreValue", 0) if made else 0
                    R["trans_m"] += made
                    if s["zone"] == "rim":
                        R["trans_rim_a"] += 1
                if since_reset >= shot_clock - 6:
                    R["late_a"] += 1
                    R["late_m"] += made
                R["shot_t_sum"] += since_start
            if made:
                credit_points(tid, p.get("scoreValue", 2))
                if s["assisted"] and len(parts) > 1 and parts[1]:
                    a = parts[1]
                    touch(a, tid, t)
                    P[a]["ast_given"] += 1
                    P[a][f"ast_given_{z}"] += 1
                pending = (opp[tid], t)
            continue

        if is_ft:
            made = bool(p.get("scoringPlay"))
            if "technical" in low or "flagrant" in low:
                if made:
                    credit_points(tid, 1)
                T[tid]["tech_fta"] += 1
                continue
            if pending is not None and pending[0] == opp[tid]:
                pending = None  # and-one: same possession continues through the FT
            if off != tid:
                off = tid  # FTs belong to the fouled team; not a new possession
            T[tid]["ft_a"] += 1
            T[tid]["ft_m"] += made
            m = FT_RE.search(low)
            last = (m is None) or (m.group(1) == m.group(2))
            if m and m.group(1) == "1":
                T[tid]["ft_trips"] += 1
                if m.group(2) == "1":
                    T[tid]["and1"] += 1
                if t - t0 <= 10 and trig in ("steal", "drb", "deadball", "make"):
                    T[tid]["trans_ft_trips"] += 1
            if made:
                credit_points(tid, 1)
                T[tid]["trans_pts"] += 1 if (t - t0 <= 12 and trig in ("steal", "drb", "deadball", "make")) else 0
            if last:
                pending = (opp[tid], t) if made else None
            continue

        if "Offensive Rebound" in typ:
            T[tid]["orb_pbp"] += 1
            last_orb_t[tid] = t
            if off != tid:
                start_poss(tid, t, "unknown")
            reset_t = t
            continue
        if "Defensive Rebound" in typ:
            T[tid]["drb_pbp"] += 1
            if off != tid:
                start_poss(tid, t, "drb")
            continue
        if "Dead Ball Rebound" in typ or ("rebound" in low and "team" in low and "Rebound" in typ):
            if off is not None and off != tid:
                start_poss(tid, t, "deadball")
            continue
        if "Turnover" in typ or "turnover" in low:
            nxt = plays[i + 1] if i + 1 < len(plays) else {}
            live = "Steal" in nxt.get("type", {}).get("text", "") or "steal" in low
            T[tid]["tov_pbp"] += 1
            T[tid]["tov_live" if live else "tov_dead"] += 1
            if "shot clock" in low:
                T[tid]["tov_shotclock"] += 1
            if "travel" in low:
                T[tid]["tov_travel"] += 1
            if "offensive foul" in low or "charge" in low:
                T[tid]["tov_offfoul"] += 1
            if "backcourt" in low or "10-second" in low or "10 second" in low or "5-second" in low:
                T[tid]["tov_press"] += 1
            if t - t0 <= 8 and off == tid:
                T[tid]["tov_early"] += 1
            start_poss(opp[tid], t, "steal" if live else "deadball")
            continue
        if "Steal" in typ:
            T[tid]["stl_pbp"] += 1
            continue
        if "Block" in typ:
            T[tid]["blk_pbp"] += 1
            continue
        if "Foul" in typ or "foul" in low:
            T[tid]["fouls_pbp"] += 1
            if off is not None and tid != off:
                T[tid]["def_fouls"] += 1
                if t - t0 <= 8:
                    T[tid]["def_fouls_early"] += 1
            if "offensive" in low:
                T[tid]["off_fouls"] += 1
            continue
        if "TimeOut" in typ.replace(" ", "") or "timeout" in low:
            if "official" not in low and "tv" not in low:
                T[tid]["timeouts"] += 1
            continue

    if pending is not None:
        start_poss(pending[0], pending[1], "make")
    end_t = sum(PERIOD_LEN(q) for q in range(1, cur_period + 1))
    advance(end_t)
    if off is not None:
        poss_lengths[off].append(end_t - t0)
    for tid in (hid, aid):
        pl = [x for x in poss_lengths[tid] if 0 < x < 60]
        T[tid]["poss_len_sum"] = sum(pl)
        T[tid]["poss_len_n"] = len(pl)
    lineups = []
    for (tid, k), d in lineup_acc.items():
        if d["sec"] >= 30:
            lineups.append(dict(game_id=game["game_id"], season=game["season"], team_id=tid,
                                lineup="|".join(k), **d))
    meta = dict(pbp_shots=n_shots, pbp_resync=n_sync)
    return T, P, lineups, meta

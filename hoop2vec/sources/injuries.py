"""Current injury information -> player availability probabilities.

Sources (in priority order):
1. ``overrides/injuries.yaml`` — hand-maintained (team, player, status/availability, until)
2. Men's college: Rotowire's public injury-report JSON feed and the Covers.com injury page.
   NBA / WNBA: ESPN's public injury report. Women's college: no free feed (overrides + missed games only).
3. Heuristic from recent missed games (applied later in players.py when nothing else known)
"""
from __future__ import annotations

import datetime as dt
import json
import re
import unicodedata

import pandas as pd
import yaml

from ..config import OVERRIDES, RAW, today
from ..http import get

ROTOWIRE = "https://www.rotowire.com/cbasketball/tables/injury-report.php?team=ALL&pos=ALL"
STATUS = {"out": 0.0, "out for season": 0.0, "suspended": 0.0, "doubtful": 0.2, "game time decision": 0.5,
          "questionable": 0.5, "day-to-day": 0.6, "probable": 0.9, "available": 1.0, "active": 1.0}


def norm_name(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    s = s.lower().replace(".", "").replace("'", "").replace("-", " ")
    s = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", "", s)
    return re.sub(r"\s+", " ", s).strip()


def norm_team(s: str) -> str:
    s = norm_name(s).replace("&", "and")
    s = re.sub(r"\bstate\b", "st", s)
    s = re.sub(r"\bsaint\b", "st", s)
    s = re.sub(r"\buniversity\b|\bof\b", "", s)
    return re.sub(r"\s+", " ", s).strip()


def team_matcher(meta: pd.DataFrame):
    keys = {}
    for r in meta.itertuples():
        for v in (r.team, getattr(r, "location", None), getattr(r, "short", None), getattr(r, "name", None)):
            if isinstance(v, str) and v:
                keys.setdefault(norm_team(v), r.team)
    return lambda name: keys.get(norm_team(name))


def rotowire() -> pd.DataFrame:
    try:
        data = get(ROTOWIRE, timeout=30).json()
    except Exception as e:
        print("rotowire injuries unavailable:", e)
        return pd.DataFrame(columns=["player", "team", "status", "injury"])
    (RAW / "injuries").mkdir(parents=True, exist_ok=True)
    (RAW / "injuries" / f"rotowire_{today():%Y%m%d}.json").write_text(json.dumps(data))
    df = pd.DataFrame(data)
    return df[["player", "team", "status", "injury"]] if not df.empty else df


COVERS = "https://www.covers.com/sport/basketball/ncaab/injuries"


def covers() -> pd.DataFrame:
    """Covers.com injury page: one header table per team followed by a player table."""
    from bs4 import BeautifulSoup

    try:
        html = get(COVERS, timeout=40).text
    except Exception as e:
        print("covers injuries unavailable:", e)
        return pd.DataFrame(columns=["player", "team", "status", "injury"])
    soup = BeautifulSoup(html, "lxml")
    rows = []
    for cnt in soup.select("td.covers-CoversSeasonInjuries-injuryCountColumn"):
        if cnt.get_text(strip=True) in ("(0)", ""):
            continue
        hdr = cnt.find_parent("table")
        name_el = hdr.select_one(".covers-CoversMatchups-teamName a")
        if name_el is None:
            continue
        team = name_el.contents[0].strip() if name_el.contents else name_el.get_text(strip=True)
        body = hdr.find_next("table")
        if body is None:
            continue
        for tr in body.select("tbody tr"):
            tds = tr.find_all("td")
            link = tr.select_one("a.player-link")
            if link is None or len(tds) < 3:
                continue
            slug = link.get("href", "").rstrip("/").split("/")[-1]
            player = " ".join(w.capitalize() for w in slug.split("-")) if slug else link.get_text(" ", strip=True)
            b = tds[2].find("b")
            st = (b.get_text(" ", strip=True) if b else tds[2].get_text(" ", strip=True))
            status, _, injury = st.partition(" - ")
            rows.append({"player": player, "team": team, "status": status.strip(), "injury": injury.strip()})
    return pd.DataFrame(rows, columns=["player", "team", "status", "injury"])


def espn() -> pd.DataFrame:
    """ESPN's public injury report (NBA and WNBA; free, updated through the day). Team names come back as our team
    keys (ESPN team id -> team directory), so they always match."""
    from ..config import ESPN_BASE
    try:
        js = get(f"{ESPN_BASE}/injuries", timeout=30).json()
        from ..espn_data import team_directory
        d = team_directory()
        id2team = dict(zip(d["espn_id"].astype(str), d["team"]))
    except Exception as e:
        print("  espn injuries:", e)
        return pd.DataFrame(columns=["player", "team", "status", "injury"])
    rows = []
    for t in js.get("injuries", []):
        team = id2team.get(str(t.get("id")), t.get("displayName"))
        for i in t.get("injuries", []):
            rows.append({"player": (i.get("athlete") or {}).get("displayName"), "team": team,
                         "status": i.get("status") or "", "injury": i.get("shortComment") or ""})
    return pd.DataFrame(rows, columns=["player", "team", "status", "injury"]).dropna(subset=["player"])


def manual() -> pd.DataFrame:
    p = OVERRIDES / "injuries.yaml"
    if not p.exists():
        return pd.DataFrame(columns=["player", "team", "status", "avail"])
    items = (yaml.safe_load(p.read_text()) or {}).get("injuries") or []
    rows = []
    for it in items:
        until = it.get("until")
        if until and pd.Timestamp(until).date() < today():
            continue
        rows.append({"player": it["player"], "team": it["team"], "status": it.get("status", "out"),
                     "avail": it.get("availability"), "injury": it.get("note", "")})
    return pd.DataFrame(rows)


def availability(meta: pd.DataFrame, players: pd.DataFrame):
    """Return ({(team, pid): p_available}, report DataFrame).

    ``players``: current-season roster (team, pid, player)."""
    match_team = team_matcher(meta)
    roster = players.assign(nk=players["player"].map(norm_name))
    idx = {(t, n): p for t, n, p in zip(roster["team"], roster["nk"], roster["pid"])}
    roster["last"] = roster["nk"].str.split().str[-1]
    lastc = roster.groupby(["team", "last"])["pid"].agg(list)
    by_last = {k: v[0] for k, v in lastc.items() if len(v) == 1}
    out, rep = {}, []
    from ..config import LEAGUE
    feeds = {"womens": (("manual", manual()),),
             "nba": (("espn", espn()), ("manual", manual())), "wnba": (("espn", espn()), ("manual", manual()))}.get(
        LEAGUE, (("covers", covers()), ("rotowire", rotowire()), ("manual", manual())))
    for src, df in feeds:
        for r in df.itertuples():
            team = match_team(r.team) or (r.team if r.team in set(roster["team"]) else None)
            if team is None:
                continue
            nk = norm_name(r.player)
            pid = idx.get((team, nk))
            if pid is None and nk:
                pid = by_last.get((team, nk.split()[-1]))
            st = str(r.status).strip().lower()
            a = getattr(r, "avail", None)
            a = float(a) if a is not None and not pd.isna(a) else STATUS.get(st, 0.5 if "question" in st else 0.0)
            rep.append({"team": team, "player": r.player, "pid": pid, "status": r.status,
                        "injury": getattr(r, "injury", ""), "avail": a, "source": src})
            if pid is not None:
                out[(team, int(pid))] = a  # manual rows come last and win
    report = pd.DataFrame(rep)
    archive(report)
    return out, report


def archive(report: pd.DataFrame) -> None:
    """Keep each day's injury report in history/<league>/injuries_<season>.parquet, which the daily job commits.
    Injury feeds are snapshots that are gone the next day; a season of them is what it takes to learn what a
    "questionable" tag is worth and to backtest with real injury news. One snapshot per date (the latest run wins)."""
    from ..config import HIST, current_season
    if report is None or report.empty:
        return
    try:
        f = HIST / f"injuries_{current_season()}.parquet"
        snap = report.assign(date=today().isoformat(),
                             fetched=dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"))
        snap["pid"] = pd.to_numeric(snap["pid"], errors="coerce").astype("Int64")
        for c in ("team", "player", "status", "injury", "source"):
            snap[c] = snap[c].astype("string")
        old = pd.read_parquet(f) if f.exists() else None
        if old is not None:
            old = old[old["date"] != snap["date"].iloc[0]]
            snap = pd.concat([old, snap], ignore_index=True)
        HIST.mkdir(parents=True, exist_ok=True)
        snap.to_parquet(f, index=False, compression="zstd", compression_level=19)
    except Exception as e:  # the archive must never stop a refresh
        print("  injury archive:", e)

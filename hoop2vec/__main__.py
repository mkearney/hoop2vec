"""CLI: python -m hoop2vec [daily|live|train|backtest|all|publish|offline|history|ingest SEASONS|site] [--mens|--womens|--nba|--wnba]

--mens (default) / --womens picks the league: data/<league>, artifacts/<league>, docs/<league>.

daily     refresh the current season -> ratings, identities, predictions, bracket, site JSON
train     same + retrain the matchup networks and the linear game model
backtest  same + rerun the season-ahead backtest (re-chooses the blend weight)
all       train + backtest + publish
publish   re-export the site from cached features (fast)
offline   like daily, but without any network refresh (uses local caches)
live      daily as games finish during the day: only new games, completed seasons from the cache, no prospects;
          keeps the league's site data under live/ for the Pages deploy (see hoop2vec/live.py)
history   after a season ends: refresh the committed history/ bundle
ingest    play-by-play ingest for seasons, e.g. `ingest 2015-2026` (needed to refit identities)
shots     one-time backfill of shot locations for seasons ingested before they were kept, e.g. `shots 2008-2026`
          (player shot charts; the daily job does the current season by itself). Then `publish` to re-export.
style     refit the team-identity model from the league's play-by-play seasons (`--rename` re-derives names)
tune      tune the dynamic (Kalman) rating parameters for the league (checked on held-out recent seasons)
site      rewrite the static page shells (docs/index.html chooser + docs/<league>/index.html)
"""
import os
import sys

if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    for lg in ("mens", "womens", "nba", "wnba"):
        if f"--{lg}" in sys.argv:
            os.environ["HOOP2VEC_LEAGUE"] = lg
    sys.argv = [sys.argv[0]] + args
    mode = sys.argv[1] if len(sys.argv) > 1 else "daily"
    if mode == "history":
        from .history import export
        export()
        raise SystemExit(0)
    if mode == "style":
        from .style.fit import run as fit_style
        fit_style(rename="--rename" in flags)
        raise SystemExit(0)
    if mode == "tune":
        from .ratings.tune import tune
        print(tune())
        raise SystemExit(0)
    if mode == "site":
        from .site import write
        write()
        raise SystemExit(0)
    if mode in ("ingest", "shots"):
        from .ingest import ingest_season
        arg = sys.argv[2] if len(sys.argv) > 2 else ""
        a, _, b = arg.partition("-")
        for s in range(int(a), int(b or a) + 1):
            ingest_season(s, shots=True) if mode == "shots" else ingest_season(s)
        raise SystemExit(0)
    if mode == "live":
        from .live import run as live_run
        live_run()
        raise SystemExit(0)
    if mode not in ("daily", "train", "backtest", "all", "publish", "offline"):
        raise SystemExit(__doc__)
    from .pipeline import run
    run(mode)

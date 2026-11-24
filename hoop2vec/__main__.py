"""CLI: python -m hoop2vec [daily|train|backtest|all|publish|offline|history|ingest SEASONS]

daily     refresh the current season -> ratings, identities, predictions, bracket, site JSON
train     same + retrain the matchup networks and the linear game model
backtest  same + rerun the season-ahead backtest (re-chooses the blend weight)
all       train + backtest + publish
publish   re-export the site from cached features (fast)
offline   like daily, but without any network refresh (uses local caches)
history   after a season ends: refresh the committed history/ bundle
ingest    play-by-play ingest for seasons, e.g. `ingest 2015-2026` (needed to refit identities)
"""
import sys

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "daily"
    if mode == "history":
        from .history import export
        export()
        raise SystemExit(0)
    if mode == "ingest":
        from .ingest import ingest_season
        arg = sys.argv[2] if len(sys.argv) > 2 else ""
        a, _, b = arg.partition("-")
        for s in range(int(a), int(b or a) + 1):
            ingest_season(s)
        raise SystemExit(0)
    if mode not in ("daily", "train", "backtest", "all", "publish", "offline"):
        raise SystemExit(__doc__)
    from .pipeline import run
    run(mode)

# hoop2vec

Daily men's college basketball **power ratings**, **game forecasts**, data-driven **team identities** and the
**2027 NCAA bracket**, built only from free public data. Every number on the site is computed point-in-time
and checked for look-ahead leakage against closing betting lines.

**Site:** https://mkearney.github.io/hoop2vec/ (GitHub Pages, served from `docs/`)

hoop2vec merges two earlier projects. Where they overlapped, the better piece was kept, or both were combined:

| Piece | From | Notes |
|---|---|---|
| Data layer: Torvik games and box scores since 2008, polite client, committed `history/`, ESPN fallback | mmai | |
| ESPN play-by-play ingest (possessions, shot zones, lineups) + closing lines | mmkm | Lines are a benchmark only |
| Adjusted efficiency (recency-weighted ridge, re-solved daily) + Elo | mmai | |
| Dynamic Kalman rating (offense/defense/tempo with uncertainty, roster-continuity carryover) | mmkm | Also used to de-leak player values |
| Player values, roster strength, injuries/availability | mmai | Two leaks fixed (below) |
| Matchup neural network (antisymmetric, team embeddings, σ head) | mmai | |
| Linear game model (dynamic + snapshot ratings, travel, rest, availability) | mmkm | |
| **Final game model** | both | Margin blend of the two, weight chosen by the backtest |
| Team identities: factor analysis + archetypes + identity cards | mmkm | Descriptive; style adds ~nothing to March predictions |
| Bracket: exact advancement odds, 76-team format, pods, actual bracket from ESPN | mmai + mmkm | Pool-optimal and most-likely picks with expected points |
| NBA prospects: draft model + NBA success model | new | Torvik player tables (incl. draft pick, birthdate) + Basketball-Reference careers |
| Front end | both | Ratings, Players, Prospects, team pages, Matchup Lab, Games, Bracket, Styles, Method |

## What changed in the merge

* **Leakage found and removed.** mmai's tournament backtest beat ESPN's betting lines by 0.021 log loss (0.529 vs
  0.550 on 602 tournament games), which an honest model can't do. The cause is Torvik's per-game player
  plus/minus. At team level it's almost exactly (R² = 0.998) half the game's raw box-score margin plus about half the
  team's **own end-of-season** rating, plus an opponent adjustment that also uses **end-of-season** ratings.
  Summed over a roster, those values told the model in November how good a team (and its opponents) would be in
  March, so a one-term roster rating beat the line in every season tested. hoop2vec re-bases every player-game
  value on **pre-game Kalman ratings** (after the fix, end-of-season ratings get ≈0 weight). It also replaces the
  preseason prior's use of full-season minute shares with a typical rotation curve assigned by prior value. See
  `hoop2vec/players.py::deleak_bpm`, `artifacts/leak_check.json` and the site's Method page. mmai reported 0.526 on
  2012–2026 tournament games; the same network, leak-free and season-ahead, scores 0.549.
* **Duplicate games removed.** Torvik game ids repeat across seasons. A join on the id alone had duplicated 13,908
  rows of mmai's training table and corrupted its form feature.
* **One backtest for every model.** Season-ahead (train on earlier seasons only), the same games for every
  model, and ESPN's pre-game line as a yardstick wherever it exists (`artifacts/backtest.json`).

## Ratings and players

* **Power**: points per 100 possessions better than an average D-I team on a neutral floor (0 = average), from the
  final game model's round-robin against top-100-calibre opposition. **SOS** is the average Power of the opponents
  faced, so 0 is an average D-I schedule.
* **Players** (`docs/data/players.json`): every D-I player this season. **Value** is the de-leaked, prior-shrunk box
  plus/minus (points per 100 possessions vs an average D-I player). **Impact** = minutes share × (value −
  replacement), i.e. what he adds to his team's rating. Each player also gets availability, ESPN per-game box stats
  and Torvik's advanced rates. The site defaults to rotation players (3+ games, 15+ mpg) and can show everyone.

## NBA prospects

Two models on every D-I player-season since 2008 (`hoop2vec/prospects.py`, `docs/data/prospects.json`):

* **Draft model:** gradient-boosted classifiers give P(picked in the top 5 / 14 / 30 / 60 this June). The college
  board orders players by them. Labels come from barttorvik.com's draft-pick field, counted on a player's final
  college season.
* **NBA success model:** regularized logistic and Poisson models give P(8+ Win Shares in the first 4 NBA seasons),
  P(20+, a "star start") and expected 4-year Win Shares. Outcomes come from basketball-reference.com season
  tables (one page per season, allowed by robots.txt and fetched slowly), matched to college players by name,
  timing and age. Undrafted players and players who never reach the NBA count too.
* **Features:** age on draft day, height, class, recruiting, team and conference strength, role, and about 30
  opponent-aware rates (BPM, PORPAG, usage, shooting, rim and 3-point profile, assist, turnover, rebound, block and
  steal rates). The current season is shrunk toward last season and the population, the same way in training.
  Each prospect also gets three statistical comps.

Forward-chained backtest by draft year (each draft or class predicted by a model trained only on earlier ones):

| | Model | Recruiting + BPM + age baseline | Actual draft slot |
|---|---|---|---|
| Who gets drafted, 2012–2026 (AUC) | **0.988** | 0.969 | – |
| Actual draftees in the model's top 30 (avg per draft) | **20.4** | 14.7 | – |
| Draft order of picked players (Spearman) | **0.53** | 0.48 | – |
| NBA success, all players 2012–2022 (log loss) | **0.0301** | 0.0322 | – |
| NBA success, drafted players (AUC) | 0.741 | 0.738 | **0.773** |

NBA teams' draft position remains the better predictor of NBA success (they see workouts, medicals and context),
and adding the model to the slot didn't beat the slot alone. College players only: international and G League
prospects aren't modeled.

## Backtest

Season-ahead, 2012–2026: for each test season every model is trained only on earlier seasons, then predicts every
game from features as of the day before, with availability knowable before tip-off. Log loss, lower is better:

| Model | All games (81,758) | NCAA tournament (936) | Sportsbook lines, 2025-26 (5,499) |
|---|---|---|---|
| **hoop2vec blend** (70% network + 30% linear, live model) | **0.5176** | 0.5464 | 0.5472 |
| Matchup network (all features) | 0.5182 | 0.5490 | 0.5484 |
| Matchup network (mmai's features) | 0.5183 | 0.5495 | 0.5481 |
| Linear game model (mmkm) | 0.5217 | **0.5445** | 0.5492 |
| Adjusted efficiency (AdjEM) | 0.5246 | 0.5452 | 0.5493 |
| Dynamic (Kalman) rating | 0.5259 | 0.5461 | 0.5527 |
| Elo | 0.5382 | 0.5660 | 0.5676 |
| DraftKings line (benchmark only) | – | – | **0.5422** |

The blend beats the linear model on all games by 0.0040 log loss (95% CI 0.0035–0.0047) and was chosen on that.
In each season of the table, its weight comes only from earlier seasons. On the 936 tournament games every
reasonable model is within noise of the others; mmkm's research found the same for style effects. A real
sportsbook is still better than the model during the regular season and level with it in March, as you'd expect
from an honest public-data model. ESPN's older "consensus" lines (2015–2023) are soft, especially in November:
the blend beats them on 41,280 games (0.5258 vs 0.5331), so they are not used as evidence of skill.

## Usage

```bash
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt

python -m hoop2vec daily      # refresh current season -> ratings, identities, predictions, bracket, site JSON
python -m hoop2vec train      # same + retrain the matchup networks and the linear game model
python -m hoop2vec backtest   # same + season-ahead backtest of every model (re-chooses the blend weight)
python -m hoop2vec all        # train + backtest + publish
python -m hoop2vec publish    # re-export the site from cached features (fast)
python -m hoop2vec history    # after a season ends: refresh the committed history/ bundle
python -m hoop2vec ingest 2015-2026   # play-by-play for past seasons (only needed to refit identities)
```

**Polite data access.** Requests identify themselves (`hoop2vec-research/1.0`). Calls to barttorvik.com are spaced at
least 10 seconds apart, as its robots.txt asks. A daily run never downloads Torvik's player-game json: this season's
player games come from a frozen Torvik snapshot plus ESPN box scores. If Torvik refuses a request (it has blocked
GitHub's runners), the snapshot is used and newer games are filled in from ESPN.

To preview locally: `cd docs && python -m http.server`.

### Manual controls

* `overrides/injuries.yaml`: force a player's availability (e.g. a star ruled out).
* `overrides/bracket_2027.yaml`: tournament sites, Final Four pairings, or a hand-entered bracket.
* `artifacts/style/style_names.json`: names and descriptions of style dimensions and archetypes.

### Automation

* `.github/workflows/daily.yml`: every morning, November through April.
* `.github/workflows/retrain.yml`: monthly retrain + backtest, or on demand.

Enable Pages once: **Settings → Pages → Deploy from a branch → `main` / `/docs`**.

## Layout

```
hoop2vec/            python package
  sources/           torvik.py, espn.py, pbp.py (play-by-play parser), injuries.py
  games.py           games table, Torvik<->ESPN crosswalk, venues, NCAA rounds/seeds
  espn_fallback.py   current season from ESPN when Torvik is unreachable
  ingest.py          ESPN play-by-play ingest (box score, PBP counts, lineups, closing line)
  ratings/           efficiency.py (ridge snapshots), kalman.py (dynamic), elo.py
  players.py         player values (de-leaked), roster strength, availability
  features.py        leak-free per-game modelling table
  model/             net.py + train.py (matchup network), linear.py (linear game model), backtest.py
  style/             play-by-play features, EB adjustment, roster, factor model, identity cards
  predict.py         live team state, blended predictor, Power ratings, upcoming games
  bracket.py         projected/actual bracket, exact probabilities, picks
  pipeline.py        end-to-end + site JSON
history/             compact committed copy of completed seasons (games, players, venues, closing lines)
artifacts/           trained models, blend weight, crosswalk, team metadata, style model, backtests
docs/                static site (index.html, assets/, data/)
```

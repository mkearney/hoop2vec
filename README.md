# hoop2vec

Daily **power ratings**, **game forecasts**, **player values** and data-driven **team identities** for men's and
women's college basketball, the **NBA** and the **WNBA**, plus **pro prospects** for the college players, the
**2027 NCAA brackets** and **NBA / WNBA playoff odds**, built only from free public data. Every number on the site is computed point-in-time and checked for look-ahead leakage against closing
betting lines.

**Site:** https://mkearney.github.io/hoop2vec/ (GitHub Pages, served from `docs/`). The home page picks a league:
`docs/mens/`, `docs/womens/`, `docs/nba/` and `docs/wnba/` run the same app (`docs/assets/`) on their own data
(`docs/<league>/data/`).

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
* **Players** (`docs/<league>/data/players.json`): every D-I player this season. **Value** is the de-leaked,
  prior-shrunk box plus/minus (points per 100 possessions vs an average D-I player). **Impact** = minutes share ×
  (value − replacement), i.e. what the player adds to the team's rating. Each player also gets availability, ESPN per-game box stats
  and Torvik's advanced rates. The site defaults to rotation players (3+ games, 15+ mpg) and can show everyone.

## NBA prospects

Two models on every D-I player-season since 2008 (`hoop2vec/prospects.py`, `docs/mens/data/prospects.json`):

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

**NBA comps** (`hoop2vec/comps.py`): a translation model (multi-output ridge regression) learns how a college profile
turns into an NBA profile over a player's first four NBA seasons: usage, true shooting, 3-point and free-throw
rate, rebound/assist/steal/block/turnover rates and BPM. Three outputs per prospect:

* **College comps:** past college players at the same stage (same class, age within 1.5 years) with the closest
  *projected* NBA profile plus height, shown with what they actually became (pick, NBA line, Win Shares).
* **Plays like:** NBA players, international ones included, whose *actual* NBA profile is closest to the
  prospect's projected one.
* **Comp hit rate:** of the 25 most similar finished college careers, the share that reached the NBA and the share
  that became rotation players.

Validated out-of-sample by class: comps chosen this way predict a player's real NBA profile better than comps
chosen on raw college stats (average R² 0.40 vs 0.35). Rebounding, blocks, assists and 3-point volume translate
well (R² 0.6–0.76); efficiency and overall impact much less (0.2–0.3).

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

## Women's basketball

Same app, same models, rebuilt on what is free for the women's game (`python -m hoop2vec <mode> --womens`;
`hoop2vec/espn_data.py`). There is no women's barttorvik.com, so everything comes from ESPN plus
Basketball-Reference:

| | Men's | Women's |
|---|---|---|
| Games, box scores | Torvik since 2008 (+ ESPN) | ESPN since 2019 (four 10-minute quarters handled in the PBP parser) |
| Play-by-play (identities) | ESPN since 2015 | ESPN since 2020 |
| Conferences | Torvik | ESPN standings |
| Player values | Torvik game BPM, de-leaked | Own box plus/minus (below) |
| Injuries | Covers + Rotowire + overrides | Recent missed games + `overrides/womens/injuries.yaml` |
| Pro data | NBA seasons + Torvik draft picks | WNBA seasons + WNBA drafts (Basketball-Reference) |
| Bracket | 76 teams, neutral pods | 76 teams; first/second rounds on the top-16 seeds' campuses; regionals Philadelphia + Las Vegas; Final Four Columbus |

* **Player values.** Each team-game's opponent-adjusted offensive and defensive efficiency (vs the opponent's
  *pre-game* Kalman rating, so nothing leaks) is regressed on the team's minute-weighted per-40 box-score rates;
  a player's value is her share of that fit plus the team residual, in the same units as Torvik's BPM. Team-level
  regressions over-credit whoever grabs the rebound or makes the interior shot (raw correlation of value with
  height 0.59, vs 0.27 for Torvik's men's BPM), so credit is shifted by height *within each team-game* (team totals
  unchanged), calibrated so the offensive/defensive height correlations match Torvik's.
* **Identities.** A women's style model fitted on women's play-by-play (`python -m hoop2vec style --womens`); the
  men's hand-written names were carried over where the fitted dimensions match (loading cosine 0.80–0.99) and the
  archetypes were renamed by hand from their centroids (`artifacts/womens/style/style_names.json`).
* **WNBA prospects.** The WNBA draft has three rounds (36 picks) in April and the rookie season starts weeks later,
  so the draft tiers are top 4 / 12 / 24 / drafted, and success is 2.5+ Win Shares in the first three WNBA seasons
  (about the top 15% of picks; a "star start" is 5+). Drafts are linked to college players by name, timing and
  school. No free birth dates or recruiting ranks exist for women's players, so class and height stand in.
  Backtest (draft years 2021–2026, success classes 2021–2024): who gets drafted AUC 0.989 (baseline 0.985); draft
  order of picked players Spearman 0.49 (0.35); success among drafted players AUC 0.84, below draft slot (0.87).
  Women's comps translate to a WNBA profile with total rebound rate and WS/40 in place of DRB% and BPM.

Women's season-ahead backtest, 2022–2026 (trained on 2020 onward; log loss):

| Model | All games (26,762) | NCAA tournament (332) | Sportsbook lines, 2025-26 (811) |
|---|---|---|---|
| **hoop2vec blend** (80% network + 20% linear, live model) | **0.4614** | 0.3932 | 0.3896 |
| Matchup network (all features) | 0.4616 | 0.3956 | **0.3894** |
| Linear game model | 0.4653 | 0.3934 | 0.3933 |
| Adjusted efficiency (AdjEM) | 0.4704 | 0.4050 | 0.3932 |
| Dynamic (Kalman) rating | 0.4706 | **0.3924** | 0.3948 |
| Elo | 0.4928 | 0.4476 | 0.4169 |
| Sportsbook line (benchmark only) | – | – | 0.3956 |

The blend beats the linear model on all games by 0.0039 (95% CI 0.0031–0.0047). It also edges the few women's
sportsbook lines ESPN carries. Women's markets are thinner, so that is plausible, but it's the symptom that exposed
the men's leak, so it was checked the same way: a one-term model on the roster rating alone scores 0.404 on those
games, worse than the line (0.396), and the Kalman rating alone is level with it.

## NBA and WNBA

Same app and models, minus the Prospects tab, with the bracket replaced by a playoff model
(`python -m hoop2vec <mode> --nba` / `--wnba`; data from `hoop2vec/espn_data.py`, playoffs in `hoop2vec/playoffs.py`).
Everything comes from ESPN's public API: every game since 2008 with box scores and play-by-play, standings and
official seeds, and ESPN's injury report. Preseason and All-Star games are dropped. Teams are keyed by nickname
(Lakers, Clippers, Liberty), and relocated or folded franchises (Connecticut Sun, Sacramento Monarchs) are kept.

**Playoffs.** College ends in one bracket; the pros play long seasons into seeded best-of-N series, so the forecast
works differently:

* The rest of the regular season is played out 10,000 times, each game priced by the game model at its arena. Each
  simulated season also shifts every team's strength by a random amount, sized from history: how far a team's
  rating moved between this point of a season and its end (wide in November, small in April). Games the NBA hasn't
  scheduled yet (each team's last two, set after the Cup group stage) are filled in as neutral-site games.
* Ties: head-to-head among the tied teams, then conference record (NBA), then a coin flip. The official rules have
  a few more steps that rarely matter; once the regular season ends, the official seeds are used.
* NBA: play-in (7 hosts 8, 9 hosts 10, loser of 7-8 hosts winner of 9-10), conference brackets, best-of-7 with a
  2-2-1-1-1 home pattern, home court to the better record. WNBA: top 8 overall, best-of-3 (1-1-1), best-of-5
  semifinals (2-2-1), best-of-7 Finals. Formats live in `overrides/<league>/playoffs.yaml`.
* Each series is solved exactly from two numbers, the home-court team's chance to win at home and on the road,
  through its home pattern, from any series score. Games already played are locked in, so the same code covers the
  regular season, the playoffs in progress and a finished season (shown with the odds the model had before round 1).
* Backtest: every past series, predicted before game 1 by the season-ahead models (trained on earlier seasons only):

| Series, log loss | NBA (224 series, 2012–2026) | WNBA (97 series, 2012–2025) |
|---|---|---|
| Home-court team wins at its historical rate | 0.598 | 0.591 |
| hoop2vec blend | **0.566** | 0.560 |
| Linear game model | 0.573 | 0.568 |
| Adjusted efficiency | 0.578 | **0.555** |
| Elo | 0.581 | 0.557 |

**Game model.** Season-ahead backtest, log loss:

| | NBA, all games (19,186) | NBA playoffs (1,258) | NBA, DraftKings lines (1,071) | WNBA, all games (3,203) | WNBA playoffs (260) |
|---|---|---|---|---|---|
| **hoop2vec blend** | **0.6096** | 0.6347 | 0.5979 | 0.6133 | 0.6251 |
| Matchup network | 0.6101 | 0.6382 | 0.5981 | 0.6187 | 0.6258 |
| Linear game model | 0.6136 | **0.6343** | 0.6020 | 0.6132 | 0.6245 |
| Adjusted efficiency | 0.6147 | 0.6348 | 0.6033 | **0.6126** | 0.6225 |
| Elo | 0.6174 | 0.6444 | 0.6082 | 0.6190 | **0.6217** |
| Sportsbook line (benchmark) | – | – | **0.5812** | – | – |

Pro games are much closer than college games, so every number sits near 0.6. In the NBA the blend beats the linear
model by 0.0040 (95% CI 0.0028–0.0052) and the network carries 70% of it; the sportsbook beats everything by about
0.017, as a sharp market should. With 15 small WNBA seasons the models are within noise of each other and the
backtest puts 70% on the linear model. Switching player values to RAPM improved NBA games (0.6117 to 0.6096) and
WNBA games (0.6160 to 0.6133); NBA series got slightly worse (0.558 to 0.566, within noise for 224 series). The Kalman settings were re-tuned on each league (`python -m hoop2vec tune`)
and kept only if they did better on the three most recent seasons; the college defaults held up in both.

**Players.** The team-level box plus/minus the women's college site uses has the old PER bias in the pros:
efficient low-usage centers (Robert Williams III, Mitchell Robinson, Luke Kornet) came out as the NBA's best
players, because a team's rebounds and blocks were credited to whoever recorded them. The pro sites measure impact
from lineups instead (`hoop2vec/rapm.py`): **RAPM**, a ridge regression of every lineup stint's opponent-adjusted
offense and defense on the five players on the floor, over rolling three-season windows. A box-score model is then
fitted to RAPM (the way BPM is built) and applied to each game line, with a team adjustment so the team's
minute-weighted total matches its opponent-adjusted result. The NBA's top players by value are now Wembanyama,
Curry, Gilgeous-Alexander, Doncic and Jokic; those three centers rank 88th, 146th and 172nd. The box model explains
49% of offensive RAPM and 15% of defensive (defense is mostly invisible in a box score). It also improved the game
models (below).

**Availability.** Before a game, players' chances of playing come from recent missed games. College players who miss
a game are usually hurt; pros rest. Measured on 2008-2026 regulars: after missing 1, 2 or 3+ games an NBA player
plays the next one 55%, 36% and 12% of the time, but in the first playoff game 91%, 84% and 76% (end-of-season
rest). Those league-specific rates replace the college ones, so a rested star no longer reads as out for a series.

**Styles.** Identity models are fitted on each league's play-by-play (570 NBA and 220 WNBA team-seasons), with fewer
archetypes than college (6/5/5 and 4/4/4) and names carried over from the men's model where the dimensions match,
then edited by hand.

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
python -m hoop2vec style      # refit the team-identity model from the play-by-play seasons
python -m hoop2vec site       # rewrite the page shells (docs/index.html chooser + docs/<league>/index.html)

python -m hoop2vec daily --womens     # any mode, for another league: --womens, --nba, --wnba (default: --mens)
python -m hoop2vec tune --nba         # re-tune the dynamic rating for a league (kept only if it helps)
```

Each league keeps its own `data/<league>/`, `artifacts/<league>/`, `history/<league>/`, `overrides/<league>/` and
`docs/<league>/`. A fresh women's data cache ingests the completed ESPN seasons once (about 40 minutes), and then
only the current season.

**Polite data access.** Requests identify themselves (`hoop2vec-research/1.0`). Calls to barttorvik.com are spaced at
least 10 seconds apart, as its robots.txt asks. A daily run never downloads Torvik's player-game json: this season's
player games come from a frozen Torvik snapshot plus ESPN box scores. If Torvik refuses a request (it has blocked
GitHub's runners), the snapshot is used and newer games are filled in from ESPN.

To preview locally: `cd docs && python -m http.server`.

### Manual controls

* `overrides/<league>/injuries.yaml`: force a player's availability (e.g. a star ruled out).
* `overrides/<league>/bracket_2027.yaml`: tournament sites, Final Four pairings, or a hand-entered bracket.
* `artifacts/<league>/style/style_names.json`: names and descriptions of style dimensions and archetypes.

### Automation

* `.github/workflows/daily.yml`: every morning, all four leagues in turn (a league out of season has nothing new).
* `.github/workflows/retrain.yml`: monthly retrain + backtest for every league, or on demand.

Enable Pages once: **Settings → Pages → Deploy from a branch → `main` / `/docs`**.

## Layout

```
hoop2vec/            python package
  config.py          league switch (HOOP2VEC_LEAGUE / --womens / --nba / --wnba): paths, calendar, game length
  sources/           torvik.py, espn.py, pbp.py (play-by-play parser), injuries.py, bbref.py (NBA + WNBA)
  espn_data.py       games, standings, player values (and the women's college player table) from ESPN
  playoffs.py        NBA / WNBA: season simulation, tiebreakers, play-in, best-of-N series, series backtest
  games.py           games table, Torvik<->ESPN crosswalk, venues, NCAA rounds/seeds
  espn_fallback.py   current season from ESPN when Torvik is unreachable
  ingest.py          ESPN play-by-play ingest (box score, PBP counts, lineups, closing line)
  ratings/           efficiency.py (ridge snapshots), kalman.py (dynamic), elo.py
  players.py         player values (de-leaked), roster strength, availability
  features.py        leak-free per-game modelling table
  model/             net.py + train.py (matchup network), linear.py (linear game model), backtest.py
  style/             play-by-play features, EB adjustment, roster, factor model, identity cards, fit.py
  prospects.py       draft + pro success models;  comps.py: college and pro comparisons
  site.py            page shells: league chooser + one page per league
  predict.py         live team state, blended predictor, Power ratings, upcoming games
  bracket.py         projected/actual bracket, exact probabilities, picks
  pipeline.py        end-to-end + site JSON
history/<league>/    compact committed copy of completed seasons (games, players, venues, lines, pro careers)
artifacts/<league>/  trained models, blend weight, crosswalk, team metadata, style model, backtests
overrides/<league>/  hand-edited injuries and bracket
docs/                static site: index.html (choose a league), assets/ (shared app), mens/ womens/ nba/ wnba/
```

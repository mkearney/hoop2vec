# hoop2vec

Power ratings, game forecasts, player values, and team style identities for men's and women's college basketball,
the NBA, and the WNBA. The college sites add pro prospect boards and 2027 NCAA bracket odds. The pro sites add
playoff odds. Everything is built from free public data, computed point-in-time, and graded on seasons the models
never saw, with closing betting lines as the yardstick. Ratings update within minutes of each final and are rebuilt
from scratch every morning.

**Site:** https://hoop2vec.net/ (GitHub Pages, served from `docs/`). The home page picks a league:
`docs/mens/`, `docs/womens/`, `docs/nba/`, and `docs/wnba/` run the same app (`docs/assets/`) on their own data
(`docs/<league>/data/`). Every team also gets a static page (`docs/<league>/team/<slug>/`) that search engines can
index.

## What's on the site

* **Ratings:** every team's Power (points per 100 possessions vs an average team), offense, defense, tempo,
  roster strength, injury impact, strength of schedule, and style archetype.
* **Games:** win probabilities and predicted margins for the coming week, and this season's results next to the
  forecast the model made the day before.
* **Teams and players:** rating history, roster values, and schedule for every team; career lines and shot charts
  for every player.
* **Matchup Lab:** any two teams on any floor, with the forecast broken into its parts.
* **Bracket / Playoffs:** exact NCAA advancement odds with pool picks, or simulated NBA and WNBA seasons, play-ins,
  and series.
* **Prospects:** draft and pro-success odds for college players, with college and pro comps.
* **Styles and Method:** what each team's style dimensions mean, how every model works, and the backtests.

## How it compares

✅ free · 💲 paid · ❌ not offered (as of November 2026)

| Site | Men's D-I | Women's D-I | NBA | WNBA | Team ratings | Game predictions | Player metrics | Bracket / playoff odds | Open source |
|---|---|---|---|---|---|---|---|---|---|
| **hoop2vec** | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| [KenPom](https://kenpom.com) | 💲 | ❌ | ❌ | ❌ | ✅ | 💲 | 💲 | ❌ | ❌ |
| [Bart Torvik](https://barttorvik.com) | ✅ | ✅ | ❌ | ❌ | ✅ | ✅ | ✅ | ✅ | ❌ |
| [EvanMiya](https://evanmiya.com) | ✅ | ❌ | ❌ | ❌ | ✅ | 💲 | ✅ | ✅ | ❌ |
| [Haslametrics](https://haslametrics.com) | ✅ | ❌ | ❌ | ❌ | ✅ | ✅ | ❌ | ✅ | ❌ |
| [ESPN BPI](https://www.espn.com/mens-college-basketball/bpi) | ✅ | ❌ | ❌ | ❌ | ✅ | ✅ | ❌ | ✅ | ❌ |
| [Her Hoop Stats](https://herhoopstats.com) | ❌ | ✅ | ❌ | ✅ | 💲 | 💲 | ✅ | ✅ | ❌ |
| [Dunks & Threes](https://dunksandthrees.com) | ❌ | ❌ | 💲 | ❌ | 💲 | 💲 | 💲 | 💲 | ❌ |

A checkmark doesn't mean equal quality. KenPom and Torvik have years of trust that hoop2vec hasn't earned. One head
to head exists: barttorvik.com keeps T-Rank's own pre-game win probability for every game since 2015, written before
tip-off and never changed afterward, and on those 65,392 games the hoop2vec backtest scores better in every season
(log loss 0.5207 vs 0.5275). KenPom's predictions aren't free to check. EvanMiya's college player ratings and Dunks &
Threes' NBA ones are more sophisticated than hoop2vec's. A sharp sportsbook still beats hoop2vec during the regular
season.

## Ratings and players

* **Power**: points per 100 possessions better than an average D-I team on a neutral floor (0 = average), from the
  final game model's round-robin against top-100-calibre opposition. **SOS** is the average Power of the opponents
  faced, so 0 is an average D-I schedule.
* **Players** (`docs/<league>/data/players.json`): every D-I player this season. **Value** is the point-in-time,
  prior-shrunk box plus/minus (points per 100 possessions vs an average D-I player). **Impact** = minutes share ×
  (value − replacement), i.e. what the player adds to the team's rating. Each player also gets availability, ESPN per-game box stats
  and Torvik's advanced rates. The site defaults to rotation players (3+ games, 15+ mpg) and can show everyone.
* **Player pages** (`#/player/<team>/<pid>`, `hoop2vec/careers.py`): every player in the Players table gets a career
  table and a shot chart. The data is one file per team (`docs/<league>/data/players/<team>.json`), so a page loads only
  its own team's file. Career lines come from Torvik's player-season tables for men's college (every D-I season since
  2008) and from ESPN box scores for everyone else (women's college since 2019, NBA and WNBA since 2008, playoffs
  included). Shot zones come from ESPN play-by-play, compared with the league average from the same zone that season
  (`shots_league.json`). ESPN's tagging changes over time, so each season uses the finest zones its data allows: rim /
  short mid / long mid / corner 3 / above-the-break 3 when ESPN gave court locations (every pro season since 2008 except the 2020 WNBA bubble; college from
  2025–26), rim / short / long / 3 with only shot distances, and rim / other 2s / 3 with neither (college before
  2025–26). The ingest now also keeps each located shot (ESPN's x, y in feet), so the chart can show exact spots
  for up to the last 5 seasons. The daily job fills in the current season by itself. Past seasons can be backfilled
  with `python -m hoop2vec shots <seasons>` or the `backfill-shots` workflow.

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
  opponent-aware rates (BPM, PORPAG, usage, shooting, rim and 3-point profile, assist, turnover, rebound, block, and
  steal rates). The current season is shrunk toward last season and the population, the same way in training.
  Each prospect also gets three statistical comps.

**NBA comps** (`hoop2vec/comps.py`): a translation model (multi-output ridge regression) learns how a college profile
turns into an NBA profile over a player's first four NBA seasons: usage, true shooting, 3-point and free-throw
rate, rebound/assist/steal/block/turnover rates, and BPM. Three outputs per prospect:

* **College comps:** past college players at the same stage (same class, age within 1.5 years) with the closest
  *projected* NBA profile plus height, shown with what they actually became (pick, NBA line, Win Shares).
* **Plays like:** NBA players, international ones included, whose *actual* NBA profile is closest to the
  prospect's projected one.
* **Comp hit rate:** of the 25 most similar finished college careers, the share that reached the NBA and the share
  that became rotation players.

Validated out-of-sample by class: comps chosen this way predict a player's real NBA profile better than comps
chosen on raw college stats (average R² 0.40 vs 0.35). Rebounding, blocks, assists, and 3-point volume translate
well (R² 0.6–0.76); efficiency and overall impact much less (0.2–0.3).

Forward-chained backtest by draft year (each draft or class predicted by a model trained only on earlier ones):

| | Model | Recruiting + BPM + age baseline | Actual draft slot |
|---|---|---|---|
| Who gets drafted, 2012–2026 (AUC) | **0.988** | 0.969 | – |
| Actual draftees in the model's top 30 (avg per draft) | **20.4** | 14.7 | – |
| Draft order of picked players (Spearman) | **0.53** | 0.48 | – |
| NBA success, all players 2012–2022 (log loss) | **0.0301** | 0.0322 | – |
| NBA success, drafted players (AUC) | 0.741 | 0.738 | **0.773** |

NBA teams' draft position remains the better predictor of NBA success (they see workouts, medicals, and context),
and adding the model to the slot didn't beat the slot alone. College players only: international and G League
prospects aren't modeled.

## Backtest

Season-ahead, 2012–2026: for each test season every model is trained only on earlier seasons, then predicts every
game from features as of the day before, with availability knowable before tip-off. Log loss, lower is better:

| Model | All games (81,758) | NCAA tournament (936) | Sportsbook lines, 2025-26 (5,499) | T-Rank games, 2015–2026 (65,392) |
|---|---|---|---|---|
| **hoop2vec blend** (70% network + 30% linear, live model) | **0.5165** | 0.5466 | 0.5449 | **0.5207** |
| Matchup network (all features) | 0.5169 | 0.5487 | 0.5452 | 0.5211 |
| Matchup network (core features) | 0.5171 | 0.5489 | 0.5450 | 0.5213 |
| Linear game model | 0.5190 | 0.5450 | 0.5475 | 0.5235 |
| Dynamic (Kalman) rating | 0.5203 | 0.5453 | 0.5487 | 0.5244 |
| Adjusted efficiency (AdjEM) | 0.5240 | **0.5446** | 0.5492 | 0.5287 |
| Elo | 0.5338 | 0.5588 | 0.5628 | 0.5377 |
| DraftKings line (benchmark only) | – | – | **0.5422** | – |
| T-Rank pre-game probability (benchmark only) | – | – | – | 0.5275 |

The blend beats the linear model on all games by 0.0026 log loss (95% CI 0.0021–0.0031) and was chosen on that.
In each season of the table, its weight comes only from earlier seasons. It picks the winner 73.5% of the time. On the
936 tournament games every reasonable model is within noise of the others, and play-by-play style factors add
nothing measurable. T-Rank's stored pre-game probabilities lose to the blend in all 12 seasons they cover, by
0.004 to 0.010. A real sportsbook is still better than the model during the regular season and level with it in
March, as you'd expect from an honest public-data model. ESPN's older "consensus" lines (2015–2023) are soft,
especially in November: the blend beats them on 41,280 games (0.5250 vs 0.5330), so they are not used as
evidence of skill.

The November 2026 changes (`research/accuracy_plan.md`, every experiment logged in `research/experiments.jsonl`)
moved the blend from 0.5176 to 0.5165, accuracy from 73.47% to 73.53%, and cut the DraftKings gap from 0.0050 to
0.0027. Most of it came from the dynamic rating, which went from 0.5259 to 0.5203. It now starts each season from the
roster-based preseason prior instead of the league average, ignores a third of each opponent's 3-point luck, and was
re-tuned with both in place. Smaller pieces: returning players' values are shrunk less toward replacement (as if 125
extra minutes, not 250), a missing player now costs more (replacement value -4, not -2), the efficiency ratings keep
more weight on early-season games, and Elo was re-tuned (it now ignores home court in its updates, since a home team
in a college mismatch is usually the stronger one). The backtest also got stricter: every fitted piece (preseason
prior coefficients, class progression, the recruiting slope) now uses only seasons before the one it predicts, and
both game models train on the same pre-game availability estimate they are tested on. A wider network, longer
training, more dropout, closing lines as a training target, and sharper player priors for recruits and transfers
were all tried and left out because they didn't help.

## Women's basketball

Same app, same models, rebuilt on what is free for the women's game (`python -m hoop2vec <mode> --womens`;
`hoop2vec/espn_data.py`). Everything comes from ESPN plus Basketball-Reference (Torvik's newer women's site isn't
used):

| | Men's | Women's |
|---|---|---|
| Games, box scores | Torvik since 2008 (+ ESPN) | ESPN since 2019 (four 10-minute quarters handled in the PBP parser) |
| Play-by-play (identities) | ESPN since 2015 | ESPN since 2020 |
| Conferences | Torvik | ESPN standings |
| Player values | Torvik game BPM, re-based on pre-game ratings | Own box plus/minus (below) |
| Injuries | Covers + Rotowire + overrides | Recent missed games + `overrides/womens/injuries.yaml` |
| Pro data | NBA seasons + Torvik draft picks | WNBA seasons + WNBA drafts (Basketball-Reference) |
| Bracket | 76 teams, neutral pods | 76 teams; first/second rounds on the top-16 seeds' campuses; regionals Philadelphia + Las Vegas; Final Four Columbus |

* **Player values.** Each team-game's opponent-adjusted offensive and defensive efficiency (vs the opponent's
  *pre-game* Kalman rating, so it uses only what was known before tip-off) is regressed on the team's minute-weighted per-40 box-score rates;
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
| **hoop2vec blend** (50% network + 50% linear, live model) | **0.4592** | **0.3950** | **0.3890** |
| Matchup network (all features) | 0.4606 | 0.3971 | 0.3892 |
| Linear game model | 0.4612 | 0.3955 | 0.3911 |
| Adjusted efficiency (AdjEM) | 0.4700 | 0.4015 | 0.3938 |
| Dynamic (Kalman) rating | 0.4625 | 0.3979 | 0.3909 |
| Elo | 0.4831 | 0.4322 | 0.4170 |
| Sportsbook line (benchmark only) | – | – | 0.3956 |

The blend beats the linear model on all games by 0.0020 (95% CI 0.0013–0.0028). It also edges the few women's
sportsbook lines ESPN carries. Women's markets are thinner, so that is plausible. As a sanity check, a one-term
model on the roster rating alone scores 0.404 on those games, worse than the line (0.396).

The November 2026 changes helped the women's model more than any other league's: blend 0.4614 to 0.4592, linear
model 0.4653 to 0.4612, and dynamic rating 0.4706 to 0.4625. The women's rating had been running on settings tuned
for the men's game. Re-tuned on women's seasons, with each season starting from the roster prior and part of each
opponent's 3-point luck ignored, it now edges the sportsbook line by itself (0.391), and the linear model is good
enough that the blend splits evenly between it and the network. The women's site also shares the men's smaller
changes (returning players shrunk less, a steeper cost for missing players, more weight on early games in the
efficiency ratings, re-tuned Elo) and uses availability rates measured on women's games.

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
  regular season, the playoffs in progress, and a finished season (shown with the odds the model had before round 1).
* Backtest: every past series, predicted before game 1 by the season-ahead models (trained on earlier seasons only):

| Series, log loss | NBA (224 series, 2012–2026) | WNBA (97 series, 2012–2025) |
|---|---|---|
| Home-court team wins at its historical rate | 0.598 | 0.591 |
| hoop2vec blend | **0.556** | 0.563 |
| Linear game model | 0.562 | 0.568 |
| Adjusted efficiency | 0.562 | **0.553** |
| Elo | 0.581 | 0.557 |

**Game model.** Season-ahead backtest, log loss:

| | NBA, all games (19,186) | NBA playoffs (1,258) | NBA, DraftKings lines (1,071) | WNBA, all games (3,203) | WNBA playoffs (260) |
|---|---|---|---|---|---|
| **hoop2vec blend** | **0.6092** | 0.6338 | 0.5965 | 0.6141 | 0.6250 |
| Matchup network | 0.6093 | 0.6359 | **0.5964** | 0.6190 | 0.6256 |
| Linear game model | 0.6136 | 0.6333 | 0.6019 | **0.6130** | 0.6231 |
| Adjusted efficiency | 0.6147 | **0.6332** | 0.6034 | 0.6137 | **0.6203** |
| Elo | 0.6174 | 0.6444 | 0.6082 | 0.6190 | 0.6217 |
| Sportsbook line (benchmark) | – | – | **0.5812** | – | – |

Pro games are much closer than college games, so every number sits near 0.6. In the NBA the blend beats the linear
model by 0.0044 (95% CI 0.0032–0.0057) and the network carries 80% of it; the sportsbook beats everything by about
0.015, as a sharp market should. With 15 small WNBA seasons the models are within noise of each other and the
backtest puts 80% on the linear model. Switching player values to RAPM improved NBA games (0.6117 to 0.6096) and
WNBA games (0.6160 to 0.6133). The November 2026 changes moved NBA games from 0.6096 to 0.6092 and NBA series from
0.566 to 0.556; the WNBA's linear model improved slightly (0.6132 to 0.6130) while its blend sits at 0.6141, within
noise of the 0.6133 before. The dynamic rating now starts each season from the roster prior and ignores part of
each opponent's 3-point luck in both leagues. Its settings were re-tuned with those on (`python -m hoop2vec tune`)
and kept only if they did better on the three most recent seasons: the WNBA's were kept, while the NBA's tuning
overfit, so the NBA uses the college defaults plus the two changes.

**Players.** The team-level box plus/minus the women's college site uses has the old PER bias in the pros:
efficient low-usage centers (Robert Williams III, Mitchell Robinson, Luke Kornet) came out as the NBA's best
players, because a team's rebounds and blocks were credited to whoever recorded them. The pro sites measure impact
from lineups instead (`hoop2vec/rapm.py`): **RAPM**, a ridge regression of every lineup stint's opponent-adjusted
offense and defense on the five players on the floor, over rolling three-season windows. A box-score model is then
fitted to RAPM (the way BPM is built) and applied to each game line, with a team adjustment so the team's
minute-weighted total matches its opponent-adjusted result. The NBA's top players by value are now Wembanyama,
Curry, Gilgeous-Alexander, Doncic, and Jokic; those three centers rank 88th, 146th, and 172nd. The box model explains
49% of offensive RAPM and 15% of defensive (defense is mostly invisible in a box score). It also improved the game
models (below).

**Availability.** Before a game, players' chances of playing come from recent missed games. College players who miss
a game are usually hurt; pros rest. Measured on 2008-2026 regulars: after missing 1, 2, or 3+ games an NBA player
plays the next one 55%, 36%, and 12% of the time, but in the first playoff game 91%, 84%, and 76% (end-of-season
rest). A men's college regular plays the next game 52% of the time after missing one, 35% after two, then 24%, 18%,
and 13% after three to five, and 5% after six or more. Each league uses its own rates, so a rested star no longer
reads as out for a series. Every day's injury report is also saved (`history/<league>/injuries_<season>.parquet`). A report
dated the day of a game replaces the missed-games estimate for that game, in training and backtests too, and what
each status means ("questionable", "doubtful") is measured from the archive once it has enough cases.

**Styles.** Identity models are fitted on each league's play-by-play (570 NBA and 220 WNBA team-seasons), with fewer
archetypes than college (6/5/5 and 4/4/4) and names carried over from the men's model where the dimensions match,
then edited by hand.

## Usage

```bash
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt

python -m hoop2vec daily      # refresh current season -> ratings, identities, predictions, bracket, site JSON
python -m hoop2vec live       # same, as games finish: only new games, past seasons from the cache, no prospects
python -m hoop2vec train      # same + retrain the matchup networks and the linear game model
python -m hoop2vec backtest   # same + season-ahead backtest of every model (re-chooses the blend weight)
python -m hoop2vec all        # train + backtest + publish
python -m hoop2vec publish    # re-export the site from cached features (fast)
python -m hoop2vec history    # after a season ends: refresh the committed history/ bundle
python -m hoop2vec ingest 2015-2026   # play-by-play for past seasons (only needed to refit identities)
python -m hoop2vec shots 2022-2026    # one-time backfill of shot locations for past seasons (player shot charts)
python -m hoop2vec style      # refit the team-identity model from the play-by-play seasons
python -m hoop2vec site       # rewrite the page shells (docs/index.html chooser + docs/<league>/index.html), team pages

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

**Site data lives on its own branch.** Everything under `docs/<league>/data/` changes several times a day, so it isn't
committed to `main` (it grew the repository by megabytes a day). It lives on the `site-data` branch, which always holds
a single commit: each publish replaces it, so its history never grows. Page shells, share images, team pages, and
crawl files are rebuilt from that data at every deploy and aren't committed anywhere.

```bash
scripts/site_data.sh pull                           # put the published data into docs/<league>/data/
python -m hoop2vec site                             # page shells and team pages for that data
scripts/site_data.sh push "Retrain (mens)" mens     # publish docs/mens/data/ (only the leagues named)
```

A push by hand starts a Pages deploy. To preview locally: `scripts/site_data.sh pull`, `python -m hoop2vec site`, then
`cd docs && python -m http.server`. To check every view in a browser: `node tests/smoke/smoke.mjs docs` (needs
Playwright).

### Manual controls

* `overrides/<league>/injuries.yaml`: force a player's availability (e.g. a star ruled out).
* `overrides/<league>/bracket_2027.yaml`: tournament sites, Final Four pairings, or a hand-entered bracket.
* `artifacts/<league>/style/style_names.json`: names and descriptions of style dimensions and archetypes.

### Automation

* `.github/workflows/daily.yml`: every morning, all four leagues in turn (a league out of season has nothing new).
  The published record of the site (`site-data` branch); model artifacts and `history/` go to `main`.
* `.github/workflows/live.yml`: every 10 minutes, updates leagues as their games finish (below).
* `.github/workflows/retrain.yml`: monthly retrain + backtest for every league, or on demand.
* `.github/workflows/backfill-shots.yml`: on demand, backfill past seasons' shot locations for one league and re-publish.
* `.github/workflows/pages.yml`: deploys the app from `main` with the data from `site-data`. The other workflows
  call it after they publish, and it also runs when a push to `main` or `site-data` changes `docs/`.
* `.github/workflows/smoke.yml`: opens every league in Chromium and walks the main views (`tests/smoke/smoke.mjs`),
  when the app changes and after each daily refresh.

Enable Pages once: **Settings → Pages → Source → GitHub Actions**.

**Live updates** (`hoop2vec/live.py`). A check job reads ESPN's scoreboard for yesterday and today in every league in
season. It needs only the standard library, so nothing is installed. If no game has finished since the last live
publish, the run ends there, after a few seconds. Otherwise each league with new finals gets
`python -m hoop2vec live --<league>`. That's the daily pipeline with only the new games fetched (men's college:
ESPN on top of the Torvik snapshot, never barttorvik.com), completed seasons reused from the data cache, no
training, and no prospect boards. It takes about 1–2 minutes plus setup. Live data never goes into git. Each
league's latest site data and the list of finals it includes live in the Actions cache (`live/<league>/`), and the
Pages deploy puts them over the published data wherever they're newer. The next morning's daily run is newer
again and takes over. Run `live.yml` by hand with **force** to refresh every league in season right away.

Completed seasons are reused exactly, not approximated: the slow per-season steps (rating snapshots, roster
features) are stored with a hash of their inputs (`hoop2vec/memo.py`, and the input keys in `snapshots.parquet`'s
metadata), so a change to any input recomputes that season. Full daily runs get faster too.

## Layout

```
hoop2vec/            python package
  config.py          league switch (HOOP2VEC_LEAGUE / --womens / --nba / --wnba): paths, calendar, game length
  sources/           torvik.py, espn.py, pbp.py (play-by-play parser), injuries.py, bbref.py (NBA + WNBA)
  espn_data.py       games, standings, player values (and the women's college player table) from ESPN
  playoffs.py        NBA / WNBA: season simulation, tiebreakers, play-in, best-of-N series, series backtest
  games.py           games table, Torvik<->ESPN crosswalk, venues, NCAA rounds/seeds
  espn_fallback.py   current season from ESPN when Torvik is unreachable
  ingest.py          ESPN play-by-play ingest (box score, PBP counts, lineups, shot locations, closing line)
  careers.py         player pages: career lines, shot zones, and shot locations per team
  ratings/           efficiency.py (ridge snapshots), kalman.py (dynamic), elo.py
  players.py         player values (point-in-time), roster strength, availability
  features.py        point-in-time per-game modelling table
  model/             net.py + train.py (matchup network), linear.py (linear game model), backtest.py
  style/             play-by-play features, EB adjustment, roster, factor model, identity cards, fit.py
  prospects.py       draft + pro success models;  comps.py: college and pro comparisons
  site.py            page shells: league chooser + one page per league
  predict.py         live team state, blended predictor, Power ratings, upcoming games
  bracket.py         projected/actual bracket, exact probabilities, picks
  pipeline.py        end-to-end + site JSON
  live.py            live updates as games finish: scoreboard check, live refresh, overlay for the Pages deploy
  memo.py            completed seasons' slow steps reused across runs (keyed by a hash of their inputs)
history/<league>/    compact committed copy of completed seasons (games, players, venues, lines, pro careers)
artifacts/<league>/  trained models, blend weight, crosswalk, team metadata, style model, backtests
overrides/<league>/  hand-edited injuries and bracket
docs/                static site: index.html (choose a league), assets/ (shared app), mens/ womens/ nba/ wnba/
scripts/site_data.sh publish and fetch the site data (site-data branch)
tests/smoke/         browser smoke test of every league's views
```

## Origin

I've built college basketball models for more than a decade. hoop2vec is where that work landed: a final push with
Claude Code scaled the ideas to four leagues and put a real front end on them.

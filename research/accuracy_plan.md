# Plan: sharper game forecasts

Written 2026-11-28. Men's college first, but every change below is shared code, so the women's, NBA, and WNBA
models get each one too, retested on their own backtests. The last section says how each change carries over.

The numbers here come from a scratch rebuild: men's features rebuilt from `history/` (2008 to 2026, no network),
then the season-ahead backtest rerun the way `stage_backtest` does it. It reproduces the published result (0.5176
log loss, 73.45 percent picked correctly), so the experiments below start from the same place as the live model.

## Where the model stands

The live model is a 70/30 blend of the matchup network and the linear game model. Three yardsticks put its
0.5176 in context.

| Benchmark | Games | hoop2vec blend | Benchmark |
|---|---|---|---|
| DraftKings closing lines, 2025-26 | 5,499 | 0.5472 | 0.5422 |
| ESPN consensus lines, 2015 to 2023 | 41,280 | 0.5258 | 0.5330 |
| Torvik's stored pre-game win probability, 2015 to 2026 | 65,392 | 0.5219 | 0.5275 |

The last row is new. `games.parquet` already carries `torvik_p_a`, T-Rank's win probability from the super_sked
table. The blend beats it in all 12 seasons, by 0.003 to 0.009, even after rescaling Torvik's numbers with the
best single factor fit in hindsight. The README says hoop2vec has never been tested head to head with Torvik. That
needs one check first: that `torvik_p_a` is frozen before tip-off. If Torvik recomputes it later with end-of-season
ratings, his numbers had hindsight and still lost, so the comparison is conservative either way.

That leaves the sharp market as the real target. The gap to DraftKings is 0.0050 log loss, with a standard error
of 0.0018.

## What didn't help

Before proposing anything I tried the standard statistical fixes. None of them moved the number.

| Change, scored on the 2012 to 2026 backtest | Log loss | vs now |
|---|---|---|
| Current blend | 0.5176 | |
| Temperature-scale the win probability (scale fit on earlier seasons only) | 0.5177 | +0.0001 |
| Stack four base models (network, linear, Kalman, efficiency), separately by phase of season | 0.5176 | 0.0000 |
| Blend weight chosen by phase of season | 0.5174 | -0.0002 |
| Linear model, recency-weighted training seasons (half-life 3, 5, or 8 seasons) | 0.5217 to 0.5218 | 0.0000 |
| Linear model, trained on pre-game instead of realized availability | 0.5217 | 0.0000 |
| Kalman rating, discount half or more of opponent 3-point luck, plus free-throw luck | worse by 0.0005 to 0.0022 | |

Calibration is already close to perfect. The linear model's favorites priced at 92.5 percent won 92.6 percent of
the time. Those priced at 65 percent won 64.2 percent. The blend's best temperature is 1.02, which is the same as
no change. The model isn't badly assembled. It's short on information.

## Where the market's edge is

The DraftKings gap isn't in the lopsided games. On lines of 7 to 20 points, the blend and the market are within
0.002 of each other. The gap is in close games: 0.010 on lines of 3 points or less and 0.006 on lines of 3 to 7. By month it's
largest in December and January (about 0.007) and small in November and March (about 0.002). One season of lines
is too few to pin down the monthly numbers. The shape is still consistent with one story: the market sorts toss-ups
better because it knows more about who is good right now and who is playing tonight.

There's a ceiling on the second part. Give the linear model perfect knowledge of who actually played and its log
loss drops from 0.5217 to 0.5203 (with no availability at all it's 0.5221). That ceiling is generous, because
"actually played" includes walk-ons who only appear in blowouts. The recent-missed-games heuristic captures 0.0004
of the 0.0018. So real injury news is worth up to about 0.0014 in men's college with the current player values,
roughly a quarter of the DraftKings gap. In the pros it should be worth more, since stars carry more of a team and
healthy players sit out.

## Critique

These are ordered by how much I expect each to matter.

1. The Kalman rating ignores the roster at the start of a season. It's the strongest single input to both models,
   and between seasons it shrinks every team toward average by an amount set by returning minutes. A team that
   loses its starters but adds a top recruiting class and three good transfers gets pulled toward zero, same as one
   that adds nobody. The efficiency ratings already start from a roster-based prior (`make_prior`). Shrinking the
   Kalman toward that same prior instead of toward zero improved the Kalman's own log loss by 0.0011 overall and by
   0.0031 in November.

2. Injury information is fetched every day and thrown away. The betting lines are archived in `history/` by the
   daily job, but the Rotowire and Covers feeds land in `data/raw/`, which isn't committed. So the backtest can only use the missed-games heuristic, and the models can never learn
   what a "questionable" tag is worth. Nothing in this plan pays off later and costs so little to start now.

3. Training sees who actually played, testing sees a guess. `avail_delta` in training is built from realized
   participation in the game being predicted, including garbage-time walk-ons, which is a small piece of the
   outcome inside a feature. The backtest then feeds the pre-game heuristic into the coefficient learned on that.
   Today it costs nothing measurable: the linear coefficient drops from 0.70 to 0.50 when trained honestly and the
   log loss doesn't change. It will matter once real injury probabilities arrive, and the live site already feeds
   injury-report probabilities into a coefficient fit on certainties.

4. Most constants were inherited from mmai and mmkm and never tuned. The efficiency ratings use a 45-day
   half-life, a 0.40 weight floor, a 5-game prior, and no cap on blowouts (the Kalman has one). The player layer
   shrinks last season's value with 250 minutes, trusts in-season box scores at 300 minutes, and values a
   replacement at -2. The college availability rates (0.65, 0.30, and 0.05 after missing 1, 2, and 3+ games) look
   like guesses, while the pro rates were measured. The network's width, epochs, weight decay, and sample weights
   were set once. Only the Kalman was ever tuned.

5. The backtest is slightly optimistic, and a run of experiments would overfit it. Several fitted pieces use
   seasons after the test season. `fit_priors` pools every completed season, including the test season's own final
   ratings as targets. `player_priors` fits class progression and the recruiting slope on every season. The minutes
   curve is fit on 2009 to 2026. The Kalman was tuned on 2010 to 2023, which is in-sample for 12 of the 15 test
   seasons. The network's season means use the whole test season. Each leak is small. Together they flatter the
   backtest, and they sit in exactly the parts (priors, Kalman) that the changes above touch. There's also no
   locked holdout: the blend weight and every past choice were made on the same 15 seasons that report the score.

6. The yardstick that matters most is too small. One season of DraftKings lines has a standard error of 0.0018
   against the market, so it can't see a gain smaller than about 0.0035. The full backtest can: paired differences
   between two models have a standard error of about 0.0003, so it detects 0.0006. Changes should be judged on the
   full backtest, with the market and Torvik as sanity checks.

7. The preseason prior is thin for high-turnover teams. In November and December, the linear model beats the
   consensus line by 0.008 to 0.021 in three of four roster-continuity quartiles. In the quartile where one team
   returned less than 37 percent of its minutes, it only matches the line. Transfers carry last season's value with
   no adjustment for moving up or down a level, coaching changes aren't used, and recruiting enters as a straight
   line in rank.

8. Home court is one number. The network learns a single home edge, and the linear model splits it between site
   and travel distance. In 2021, with empty arenas, home teams did 0.97 points worse than predicted. Other seasons
   sit within 0.5 points. There's no altitude term. Small upside.

9. The closing lines are only used for grading. The backtest seasons have lines for 46,779 games. A line is a
   lower-noise estimate of the expected margin than the final score. Used as a second training target, never as an
   input, it could steady the network's estimates. Untested.

## The plan

### Phase 0: a clean test bench (about two days)

1. Make every fitted piece season-ahead: `fit_priors`, the progression and recruiting fits in `player_priors`, the
   minutes curve, Kalman tuning (on seasons before the first test season, or nested), and season means to date.
2. Lock a holdout. Develop on 2012 to 2024. Score 2025 and 2026 only when a batch of changes is ready. The live
   2026-27 season is the final exam.
3. Build a fast experiment harness: features cached per variant, the backtest as one command, and every result
   appended to a log with its paired confidence interval against the current model. The scratch rebuild took about
   2 minutes for features and 4 minutes for 15 seasons of networks on 4 CPUs, so an experiment fits in 10 minutes.
4. Add Torvik's pre-game probability as a men's benchmark, once it's confirmed to be frozen before tip-off.
5. Keep the daily line archive running. It already holds 1,137 men's 2026-27 lines, nearly all DraftKings, so by
   April there will be a second season of book lines to grade against.

The backtest number may get a little worse after this phase. That's expected.

### Phase 1: changes with evidence behind them (about a week)

1. Start each Kalman season from the roster prior: shrink last season's rating toward `make_prior`'s `eff_o` and
   `eff_d` instead of toward zero. The prior depends on de-leaked player values, which depend on the Kalman, so this
   takes two passes. Then retune the carryover and season-start variance with the prior in place. Measured: -0.0011
   for the Kalman alone. The blend will gain less, since the network already sees roster strength.
2. Discount a quarter of opponent 3-point luck in the Kalman's observations, then tune the fraction. Measured:
   -0.0005 for the Kalman alone. Bigger discounts, and any free-throw discount, made it worse.
3. Train both game models on the pre-game availability view, in the backtest and in `stage_train`.
4. Measure the college availability rates from 2008 to 2026 the way the pro rates were measured, and split them by
   minutes share and length of absence.
5. Choose the blend weight by phase of season. The most recent fit puts the network at 1.0 in November, 0.8 in
   December, 0.6 in conference play, and 0.5 in the postseason. Measured: -0.0002.

Expected: -0.0005 to -0.0015 on the blend.

### Phase 2: tune what was inherited (one to two weeks)

Search one block at a time on the development seasons, so each gain has a clear source.

1. Efficiency ratings: half-life, weight floor, prior weight, and a blowout cap like the Kalman's.
2. Player layer: prior shrinkage, in-season trust, replacement value, and the newcomer and transfer priors.
3. Network: epochs with early stopping on a held-out season, width, weight decay, the postseason and early-game
   sample weights, and ensemble size (3 in the backtest, 5 live; try 10).
4. Kalman: retune everything jointly with Phase 1's changes.
5. Elo: K, home edge, and carryover. It doesn't enter the blend, but it's a network input.

Expected: -0.0005 to -0.0015.

### Phase 3: new information (pays off over a season)

1. Archive injury reports daily, starting now: Rotowire and Covers for men's, ESPN for the NBA and WNBA, and the
   overrides file for women's, saved under `history/<league>/injuries/`. After one season, fit an availability model
   on reported status and backtest that season with real information.
2. Better preseason priors for high-turnover teams: a level adjustment for transfers (from how past transfers'
   values changed when they moved between conference tiers), coaching changes, and a curved recruiting term. Judge
   it on November and December games.
3. Closing lines as a second training target: the network predicts both the margin and the line where one exists.
   In the backtest only training seasons' lines are used. Weight the soft consensus lines down in November.
4. Home edge by era (at least a 2021 adjustment) and venue altitude. Test in the linear model first.

Expected: -0.001 to -0.002, mostly from injuries and priors.

### Phase 4: the big structural bet

College player values from lineups. The pro sites already do this (`rapm.py`), and ESPN play-by-play has college
lineups since 2015. A lineup RAPM with a box-score prior would sharpen roster strength, the cost of a missing
player, and the preseason prior all at once. It's the most work and the least certain payoff, so it waits until
Phase 3 shows what availability is worth with real injury data.

## Targets

The realistic goal is to take all-games log loss from 0.5176 to about 0.514 and to halve the DraftKings gap, from
0.005 to about 0.0025, on 2026-27 lines. Accuracy moves slowly. The blend's 0.004 edge over the linear model bought
0.2 points of accuracy (73.2 to 73.45 percent), so a 0.003 to 0.004 gain means about 73.6 to 73.7 percent. A change
ships when its paired 95 percent interval on the development seasons excludes zero and the holdout doesn't
disagree.

## The other three leagues

Everything in Phases 0 to 2 lives in shared code (`features.py`, `players.py`, `ratings/`, `model/`), so it reaches
every league by default. Each league gets its own backtest before a change is kept, the way the Kalman tuning
already works.

| Change | Women's | NBA | WNBA |
|---|---|---|---|
| Season-ahead fits and a locked holdout | Same leaks exist | Same leaks exist | Same; with 15 short seasons, hold out only the last two |
| Kalman season start from the roster prior | Yes; the prior leans on last season's values, with no recruiting ranks | Likely the biggest winner: rosters turn over through trades and free agency, and the Kalman only sees returning minutes | Same as the NBA |
| Opponent 3-point luck discount | Test | Test | Test |
| Train on pre-game availability | Yes | Yes | Yes |
| Measured availability rates | Uses the men's guesses; measure them | Already measured | Already measured |
| Blend weight by phase | Yes | Early season, late season, playoffs | Same as the NBA |
| Tune inherited constants | Runs on default Kalman settings | Runs on default Kalman settings | Runs on default Kalman settings |
| Daily injury archive | No free feed; overrides only | High value: ESPN's report covers rest and injuries | High value |
| Preseason prior upgrades | No free recruiting ranks; class and height stand in | Trades and free agency instead of transfers | Same as the NBA |
| Closing lines as a training target | 970 lines, all since 2025-26 | 15,487 lines since 2013, mostly consensus | 352 lines |
| Home edge by era and altitude | Small | Denver and Utah | Small |
| Lineup-based player values | Would help | Already built | Already built |

## Progress, 2026-11-28

Every number here comes from `research/experiment.py` (log in `research/experiments.jsonl`). Dev seasons are 2012 to
2024 and the holdout is 2025 and 2026 for men's; for the other leagues the last two seasons are the holdout. Each
change is compared with the run before it, on the same games.

### Men's

| Step | Kalman | Linear | Blend (dev) | Blend (holdout) | Kept |
|---|---|---|---|---|---|
| Old code | 0.5259 | 0.5217 | 0.5175 | 0.5188 | |
| Phase 0: season-ahead fits, date-known centering, pre-game availability in training | 0.5259 | 0.5219 | 0.5174 | 0.5181 | Yes |
| Phase 0, but trained on realized availability | 0.5259 | 0.5219 | 0.5175 | 0.5189 | No |
| Kalman season start from the roster prior | 0.5250 | 0.5215 | 0.5174 | 0.5180 | Yes |
| Plus a 25 percent opponent 3-point luck discount | 0.5242 | 0.5210 | 0.5173 | 0.5179 | Yes |
| Measured availability rates | 0.5242 | 0.5210 | 0.5173 | 0.5178 | Yes (no effect, but measured) |
| Kalman settings re-tuned with both on | 0.5204 | 0.5194 | 0.5169 | 0.5174 | Yes |
| Network also trained toward past closing lines | 0.5204 | 0.5194 | 0.5173 | 0.5184 | No |
| Blend weight by phase of season | | | 0.5173 | 0.5179 | No (gain of 0.00005, within noise) |

The Kalman rating improved the most, from 0.5259 to 0.5204, and now beats adjusted efficiency (0.5246) instead of
trailing it. The linear model went from 0.5217 to 0.5194. The blend moved much less, from 0.5177 to 0.5170, because
the network already used the roster information the Kalman was missing. The Kalman tuning was fit on 2010 to 2023,
so its dev-season gain is partly in-sample; the holdout gain (-0.0004) is not.

The re-tuned settings carry almost nothing of last season over by itself (`rho0` 0.003, `rho1` 0.30) and lean on the
roster prior instead (weight 1.15), with a third of opponent 3-point luck discounted.

Two bugs turned up along the way. The cache of completed seasons (`memo.py`) was keyed on a step's inputs but not
its settings, so a change to the availability rates would have kept the old rates for every past season, in
experiments and in the daily job. The keys now include the settings. And with season-ahead fits, a pro league's first
seasons have too few teams for the full prior formula; `make_prior` now falls back to last season's rating alone.

### Other leagues

| League, change | Kalman (dev) | Blend (dev) | Blend (holdout) | Kept |
|---|---|---|---|---|
| Women's, Phase 0 | 0.0000 | +0.0002 | +0.0005 | Yes (honest cost of the leak fixes) |
| Women's, roster prior + 25 percent luck discount | -0.0041 | -0.0012 | +0.0003 | Yes |
| WNBA, Phase 0 | 0.0000 | +0.0016 | -0.0004 | Yes (as above; 12 teams a season leave early priors thin) |
| WNBA, Phase 0 but realized availability | | -0.0009 | +0.0004 | No (inconclusive) |
| Women's, Kalman settings tuned with both on | -0.0074 | -0.0026 | -0.0017 | Yes |
| WNBA, roster prior + luck, settings tuned | -0.0016 | -0.0007 | -0.0009 | Yes |
| NBA, Phase 0 | 0.0000 | -0.0004 | +0.0005 | Yes (the network gained 0.0009 on dev seasons) |
| NBA, roster prior + 25 percent luck discount | -0.0014 | -0.0001 | -0.0001 | Yes |
| NBA, settings re-tuned with both on | | | | No (worse on the tuner's own held-out seasons) |

Women's and NBA deltas are against Phase 0 for that league; the tuned rows are against the step before.

### What's live

Men's was retrained and re-backtested with everything kept above (commit "Retrain + backtest (mens) with the new
dynamic rating"). The production backtest: blend 0.5170 on all 81,758 games (was 0.5176), accuracy 73.49 percent
(was 73.47), DraftKings gap 0.0032 on 5,499 games (was 0.0050), and 0.5212 vs T-Rank's 0.5275 on 65,392 games,
better in all 12 seasons. The other three leagues were retrained and re-backtested the same day:

| League | Blend before | Blend now | Linear before | Linear now | Kalman before | Kalman now | Network weight |
|---|---|---|---|---|---|---|---|
| Men's (81,758 games) | 0.5176 | 0.5170 | 0.5217 | 0.5194 | 0.5259 | 0.5204 | 70% (same) |
| Women's (26,762) | 0.4614 | 0.4596 | 0.4653 | 0.4614 | 0.4706 | 0.4624 | 50% (was 80%) |
| NBA (19,186) | 0.6096 | 0.6092 | 0.6136 | 0.6136 | 0.6227 | 0.6218 | 80% (was 70%) |
| WNBA (3,203) | 0.6133 | 0.6141 | 0.6132 | 0.6130 | 0.6163 | 0.6141 | 20% (was 30%) |

NBA playoff series went from 0.566 to 0.556 (224 series). The WNBA blend is within noise of where it was; most of
the change there is the honest cost of fitting priors only on earlier seasons in a 12-team league.

### Next

The biggest gains came from the dynamic rating, not the network. The network barely moved, and the two network
experiments that finished (training toward closing lines, a blend weight by phase) didn't help. Still open from
Phase 2: network width, epochs, and dropout (a wider-network run and a 24-epoch run were started and stopped for time),
and the efficiency-rating settings (half-life, weight floor, prior weight). Phase 3's injury archive is running; after
a season it can be used to fit an availability model on reported status.

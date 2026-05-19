# nighttrade — strategy plan & measured results

How the strategy is being made honest, in five research-backed phases. Every
phase is proven in the research lab (`trading-bot research`) — out-of-sample,
on real history — *before* it is wired into the live observer.

> Backtests are optimistic. "No edge" is the default, expected result. The
> goal of this work is to find out *whether* the strategy works, cheaply, on
> paper — not to make it look good.

## The four problems

A live run of the baseline strategy revealed four failure modes:

1. **No predictive edge** — direction accuracy ≈ 50%, a coin flip.
2. **Wrong-regime trading** — it traded most in the market regime where it
   was measurably weakest.
3. **Overconfidence** — stated confidence ran far above realized accuracy.
4. **Stops too tight** — direction was often right, but trades were stopped
   out by routine noise before they could pay.

## The five-phase fix

| Phase | Fix | Problem |
|---|---|---|
| **0** | Measurement harness — backtest + purged walk-forward on real history | (baseline) |
| **1** | ATR-width stops + triple-barrier time stop | #4 |
| **2** | Regime gate — block trades in regimes below break-even win rate | #2 |
| **3** | Isotonic confidence calibration — gate on calibrated probability | #3 |
| **4** | Meta-labelling — a secondary model scores P(target before stop) | #1 |
| **5** | Wire all four gates into the live decision path + dashboard | — |

## Measured results — before / after

Research lab, 12 liquid large caps, 3 years of real daily history, purged
walk-forward (purge = label horizon).

| Phase | Mean WF accuracy | Overfit gap | Mean win rate | Trades | Verdict |
|---|---|---|---|---|---|
| **0 — baseline** | **50.2%** | **+49.2%** | **43.8%** | 204 | **OVERFIT** |
| **1 — ATR stops + time stop** | exit-logic change — see §Phase 1 | | | | **TIME STOP ADDED; sweep overfit** |
| **2 — regime gate** | adaptive filter — see §Phase 2 | | | | **GATE LIVE** |
| **3 — calibration** | adaptive filter — see §Phase 3 | | | | **GATE LIVE** |
| **4 — meta-labelling** | precision 13.9% vs 12.2% base — see §Phase 4 | | | 2,245 OOS | **+1.7 pts — NO EDGE** |
| **5 — gates live + dashboard** | wiring, not a measurement — see §Phase 5 | | | | **GATES LIVE & VISIBLE** |

### Phase 0 — baseline (recorded)

The harness confirms problem #1 directly: **walk-forward accuracy 50.2%** —
no edge. The **+49.2% overfit gap** (train ≈ 99%, test ≈ 50%) shows the model
memorizes the training window and does not generalize. The headline **+13.8%
backtest "return" is a mirage** — it is the bull-market drift of megacaps over
three years, not strategy skill; the 43.8% win rate and coin-flip walk-forward
accuracy are the truth. This is the number every later phase must beat,
out-of-sample, before going live.

### Phase 1 — ATR stops + triple-barrier time stop (recorded)

Two changes, both run through the lab before adoption:

* **Triple-barrier time stop** — a position is now force-closed after
  `risk.time_stop_bars` bars even if neither stop nor target is hit (de
  Prado's third barrier). It is a structural rule, not a tuned parameter, so
  it is adopted directly. Stops/targets already sit at ATR-based volatility
  multiples, and position size already shrinks as the stop widens to hold
  risk-per-trade constant.

* **ATR stop/target sweep** — `trading-bot research --sweep` grids the stop
  multiplier × reward:risk, optimizes in-sample, validates out-of-sample.
  Result (12 stocks, 3y): the in-sample optimum (1.0×ATR, 1:1) shows a **94%
  win rate over 999 trades, +33.7%** — implausible, and the harness flags it.
  **Out-of-sample it earns only +9.1%** (vs +2.5% for the current config) —
  it beats the baseline OOS, but the +33.7%→+9.1% collapse marks it as
  overfit to the in-sample window.

  **Decision: the default multipliers are left unchanged.** A drift-dominated
  backtest gave no *trustworthy* reason to prefer any setting — adopting the
  in-sample winner would violate this plan's own honesty rule. The sweep is
  kept as a harness tool, re-run as more data accumulates. The concrete,
  proven Phase 1 change is the time stop.

### Phase 2 — regime gate (recorded)

Problem #2 was that the strategy traded *most* in the regime where it was
*weakest*. The regime gate (`gates/regime.py`) fixes that structurally:

* The break-even win rate for a reward:risk ratio R is `1 / (1 + R)`.
* Each cycle, the observer measures its own direction accuracy per market
  regime (from its evaluated prediction history) and **blocks new entries in
  any regime whose accuracy is below break-even**.
* A regime with fewer than `gates.regime_min_samples` (20) evaluated
  predictions is **allowed through** — the gate gathers evidence before it
  judges, so it self-corrects as data accumulates and cannot lock the bot out
  on a cold start.

It is wired into the live observer's entry decision; every block is recorded
to the `gate_events` table (for the Phase 5 dashboard panel). It is an
adaptive, evidence-driven filter — its correctness is the logic (unit-tested:
break-even maths, low-sample passthrough, block/allow), not a single backtest
number, because it depends on accumulated *live* measured accuracy.

### Phase 3 — confidence calibration gate (recorded)

Problem #3 was overconfidence — stated confidence ran well above realized
accuracy. `gates/calibration.py`:

* A `ConfidenceCalibrator` fits an **isotonic regression** from the bot's
  (stated confidence → was-correct) history onto the empirically-true win
  probability. The observer refits it every cycle from evaluated outcomes.
* The `CalibrationGate` admits an entry only when its **calibrated**
  probability clears `gates.calibration_floor` (0.50) — never the raw,
  optimistic confidence.
* Below `gates.calibration_min_samples` (40) evaluated predictions the
  calibrator is the **identity map** — it does not overrule the raw signal
  before it has the evidence to.

Wired into the live observer's entry decision alongside the regime gate;
blocks are recorded to `gate_events`. Unit-tested: identity below the sample
floor, isotonic shrinking of overconfidence, block-below-floor.

### Phase 4 — meta-labelling (recorded)

The brief's "big one" for problem #1. `labels/triple_barrier.py` +
`gates/meta.py`:

* **Triple-barrier labelling** — every historical bar is labelled 1 if a long
  entered there would hit the ATR-target before the ATR-stop (within the time
  barrier), else 0.
* A **gradient-boosting meta-model** is trained on those labels to predict
  P(target before stop) from the platform's technical/market features.
* The **`MetaGate`** admits an entry only when that probability clears
  `gates.meta_min_probability` — the primary model proposes direction, the
  meta-model vetoes the low-precision ones. The observer retrains it on
  triple-barrier-labelled history during market-closed downtime.

**Measured (12 stocks, 3y, out-of-sample):**

* The triple-barrier **base "target-first" rate is only 12.2%** — with the
  current 5:1 reward:risk geometry a 5-ATR target is genuinely hard to reach.
* This exposed a config bug: a naive 55% probability floor is *unreachable*
  at a 12% base rate — it would veto 100% of trades. The floor was corrected
  to **0.20** (above the base rate, not a naive 50%+).
* At that floor the meta-model accepts 9.3% of trades; **they win 13.9% vs
  the 12.2% base — a +1.7-point lift. The lab's verdict: NO EDGE.** A model
  that always says "no" scores 87.8% "accuracy" here purely from the skewed
  base rate — the harness is not fooled by it.

The meta-labelling machinery is built, wired and honest — and it reports, on
real out-of-sample data, that **the strategy still has no proven edge**. That
is the correct, valuable Phase 4 result: the platform's job is to find that
out cheaply, on paper, and it has.

### Phase 5 — gates live + dashboard (recorded)

The final phase carries no new measurement — it makes the previous four
visible and verifiable in the running system.

* **Wired into the live path.** The observer's entry decision runs a
  `(regime, calibration, meta)` check tuple every cycle; the first gate to
  object blocks the entry. The time stop (Phase 1) is enforced in the
  backtest/exit engine. Every decision — a block, or an entry that *cleared*
  all three gates — is written to the `gate_events` table.
* **Surfaced on the dashboard.** A new **Strategy Gates** tab
  (`/api/gates`) shows each gate's phase, its configured threshold, its
  all-time block count, and a live feed of the most recent allowed/blocked
  decisions. A gate with zero blocks reads as *gathering evidence*, not idle
  — consistent with the low-sample passthrough built into Phases 2–3.
* **Honesty preserved.** The panel states plainly that gates only ever
  *remove* trades — they never invent one — so a quiet gate is not a broken
  one. `/api/health` continues to report `real_trading=false`,
  `paper_only=true`.

The five-phase methodology is now complete and observable end-to-end: the
research lab proves each phase out-of-sample before it ships, the gates run
live in the observer, and the dashboard shows exactly what they did and why.

## Reference techniques

de Prado, *Advances in Financial Machine Learning* (triple-barrier labelling,
meta-labelling, purged cross-validation); ATR-based volatility stops;
scikit-learn probability calibration; regime filtering. Studied, not copied.

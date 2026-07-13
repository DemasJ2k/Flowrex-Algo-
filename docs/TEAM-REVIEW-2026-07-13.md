# Flowrex Algo — Five-Agent Team Review (2026-07-13)

The owner asked for a full repository review by a team of five agents with
different personalities, ahead of a reorganisation ("Fable reorganise").
Owner's own diagnosis: M5 AI trading produced duplicate/dumb trades, too many
components and strategies were bolted on, no clear strategy, return targets
too greedy. Proposed new direction: H1/H4/D1 timeframes, US30 + XAUUSD,
supply/demand zones, swing highs/lows, candlestick patterns, sensible
lookback.

## The team

| Agent | Role | Focus |
|---|---|---|
| **Atlas** | Systems Architect | Structure, what earns its keep |
| **Vega** | Quant Skeptic | ML validity, leakage, sample size |
| **Rex** | Risk Manager | Execution safety, prop-firm rules |
| **Iris** | Product Simplifier | Scope, feature surface, the cut |
| **Forge** | Pragmatic Engineer | Tests, debt, maintenance load |

---

## Atlas — Systems Architecture

~42k lines of backend Python, 6 agent types, 17 ML modules, 9 training
scripts, 5 broker adapters. The house is well-built; the problem is wings
kept getting added instead of rooms.

**KEEP**
1. **Broker layer** — `backend/app/services/broker/` (`base.py` ABC,
   `manager.py`, `symbol_registry.py`). Clean adapter interface, hardened
   event-loop handling, user-scoped lookups. Timeframe-agnostic.
2. **RiskManager** — `backend/app/services/agent/risk_manager.py`. Tiered DD,
   anti-martingale, trailing-lock, prop-firm aware. Symbol/timeframe neutral.
3. **Engine skeleton + trade_monitor** — `engine.py`'s lifecycle wiring is
   25+ bug-fixes of accumulated wisdom. Keep the loop, gut the 6-way agent
   dispatch.
4. **Backtest + data layer** — `api/backtest.py`, Dukascopy delta-merge,
   `History Data/` (15 years). Fewer bars needed on H4 is a bonus.
5. **Platform shell** — auth, settings, Telegram, LLM supervisor, deploy
   scripts. Boring, done, don't touch.

**CUT**
1. Four of six agent types: `scalping_agent.py`, `expert_agent.py`,
   `flowrex_agent.py` (v1 — still imported by `engine.py:15`),
   `scout_agent.py`. Also `m5_signal_generator.py`, `ict_signal_generator.py`.
2. Three parallel feature stacks (`features_mtf.py` 157, `features_potential.py`
   85, `features_flowrex.py` 120) plus `features_tier1.py`, `smc_features.py`,
   `features_correlation.py` — overlapping swing/ATR/structure math computed
   independently.
3. Research ballast: `features_williams.py`, `features_quant.py`,
   `features_cot.py`, `meta_labeler.py` v1, 6+ of 9 `train_*.py` scripts.
4. Orphaned swing attempt: `scripts/train_swing.py` imports a **deleted**
   `features_swing` module; `swing_US30_H4_*.joblib` models still in
   `ml_models/`. The H4 swing pipeline was built once and abandoned —
   recover the design, not the corpse.
5. 24 deployed M5 joblibs — archive; the new direction invalidates them.

**Verdict on pivot:** ~55–60% of the codebase is reusable (brokers, risk,
engine skeleton, backtest, data, platform), ~35% dead weight, ~5% salvage
(the ICT primitives in `features_ict.py` — order blocks ARE supply/demand
zones — plus swing points and BOS/CHOCH). The pivot is architecturally
cheap: one agent class, one ~40-feature H4 module, one training script,
two symbols. Delete before you build.

Concern: every runtime signal path assumes M5 primary bars with H1/H4/D1 as
context only (`potential_agent.py:688`). The pivot inverts that — it is a
rewrite of the signal layer, not a config change.

---

## Vega — Quant / ML validity

**SOUND**
1. `model_utils.py:create_labels` — bidirectional triple-barrier-ish labels,
   no trend filter baked in.
2. Next-bar-open fills, SL-before-TP pessimism, gap slippage, ATR-warmup skip
   in `compute_backtest_metrics`.
3. Bounded-CVD fix (`features_potential.py:363-370`) — real bug, correctly
   fixed.
4. Features are causal — rolling trailing windows throughout, no `shift(-)`,
   no centered windows, no full-series normalization in features.
5. `purged_walk_forward_splits` and `check_train_test_divergence` exist in
   `model_utils.py` — de Prado done right…

**BROKEN**
1. …but **`train_potential.py` doesn't call them.** `get_wf_folds`
   (line ~115): `test_start = train_end`, zero embargo. Labels look 10 bars
   forward, so the last 10 training labels contain test-period prices.
   `train_flowrex.py` added the embargo; the pipeline behind every deployed
   "Grade A" did not.
2. **Evaluation-time overlay stack** (`compute_backtest_metrics:155-203`):
   trend filter + ATR gate + Donchian squeeze + hardcoded US30 session
   window, applied to predictions at grading time and iterated against the
   same OOS window across retrains. That is fitting the test set with extra
   steps. Same OOS (2026-01+) reused for every retrain gate = multiple
   comparisons, no correction.
3. **Optuna maximizes accuracy on a HOLD-dominated 3-class problem** —
   predicting HOLD everywhere scores well. Wrong objective.
4. Low-vol label forcing uses `np.percentile` over the full series including
   OOS (`train_potential.py:372`) — mild global-stat leak.
5. **Inference trims X positionally** to the model's feature count with the
   count check relaxed to a 60–160 range — silent feature misalignment; a
   live-divergence factory, not a compatibility shim.

**The math:** Sharpe 24.17 on 85 OOS trades is a measurement artifact.
Live already falsified the backtests (30% live WR vs 60–79% backtest,
5–14σ feature drift).

**Verdict on pivot: directionally right — respect the sample-size cliff.**
Per symbol per year: H1 ≈ 6,000 bars, H4 ≈ 1,500, D1 ≈ 260. Ten years of H4
≈ 15k autocorrelated bars → maybe 500–1,500 labeled events. Distinguishing a
55% WR from a coin flip at 95% confidence needs ~1,100–1,500 independent
trades. Therefore: ≤15–25 features (S/D zones and swing structure qualify;
**candlestick patterns are ~51% base-rate noise — cut them**), pool training
across both symbols, and seriously consider transparent rules + statistical
testing instead of GBMs with Optuna at that sample size. Live validation on
H4 takes quarters, not weeks — budget for that.

---

## Rex — Risk / execution safety

**SOLID**
1. Cooldown survives restarts — wall-clock, reloaded from DB
   (`potential_agent.py:244-253`).
2. New-bar gate: timestamp + OHLC-hash dedupe (`engine.py:367-394`) — no
   same-bar re-fire.
3. Broker-truth P&L: `_check_closed_trades` verifies Oanda state, never
   fabricates P&L (`engine.py:696-798`).
4. Bolt preset in `risk_manager.py` is honest math (0.35% risk, 3 trades/day,
   consistency cap, trail-lock at $50,100).
5. Oanda adapter maps symbols internally on every call path.

**DANGEROUS**
1. **Force-flat is fiction on Oanda.** Engine calls
   `adapter.close_position(broker_ticket)` with a numeric ticket; Oanda's
   `close_position` (`oanda.py:395-404`) requires `"INSTRUMENT:side"` →
   "Invalid position ID format" failure. The fallback
   `_reconcile_closed_trade_from_broker` (`engine.py:653-694`) has **no
   early-return when broker state == OPEN** — it marks the DB trade closed
   unconditionally. Net effect: max-hold, force-flat, and EOD-flat can leave
   a live, unmanaged position on the broker while the DB says flat.
2. **Account-level daily loss doesn't exist.** `_daily_pnl` is per-runner,
   in-memory, realized-only, wiped on every restart (`engine.py:399-403`) —
   and `deploy.sh` force-recreates the backend. Floating losses invisible;
   three agents = three independent budgets = 9% account exposure.
3. **Duplicate-trade vectors** (the "dumb trades"): `_active_direction` is
   in-memory (reset on restart with positions still open); set to None on any
   ORDER FAILED (`engine.py:975`) even when an earlier trade holds the lock;
   only blocks same-direction; default `max_positions=6` per agent; zero
   cross-agent dedupe — two agents on US30 don't know about each other.
4. **Hard stop never closes anything.** `should_close_all()` only blocks new
   entries (`flowrex_agent_v2.py:771`); nothing liquidates on daily
   hard-stop.
5. Symbol-mismatch pre-flight is a false-negative trap: both probes return
   empty on weekends/reconnects; `data/symbol_mappings.json` user overrides
   can silently clobber `US30→US30_USD`. Check that file on the droplet.

**Root cause of dumb trades:** in-memory state (`_active_direction`,
`_daily_pnl`, RiskManager counters) that evaporates on restart, plus the
ORDER-FAILED lock wipe, plus per-agent-only limits. State must live in the
DB and gate at account level.

**Verdict: NOT funded-ready.** Fix order: (1) Oanda ticket-close
(`/trades/{id}/close`) + never mark DB-closed while broker says OPEN;
(2) account-level equity-based daily stop, persisted; (3) hard-stop
liquidation actually wired; (4) DB-backed direction lock + cross-agent
symbol dedupe. **Strategy conflict:** H4/D1 swing holds overnight — an
instant FundedNext Bolt violation. Swing on Bolt is disqualified by rule.
Pick FTMO (swing allowed) or trade intraday for Bolt; the risk layer must
know which regime it's in.

---

## Iris — Product scope

15 routes, 14 API modules, ~113 endpoints, 5 brokers, 3 agent types — for a
goal of "swing trade two symbols."

**CORE:** `/agents` + `AgentWizard.tsx`, `/trading` (the page a trader lives
on), `/` dashboard, `/settings` broker connection, `api/agent.py` +
`api/broker.py`.

**PERIPHERY:** `/backtest` (1,432-line page, 39 useState hooks — a research
IDE bolted onto a trading app; serves the dev, not users); three
notification/AI surfaces (AI chat 755 lines, news 337, Telegram 409) — pick
one; five broker adapters with simultaneous multi-broker for one prop
account; `/models` retrain UI (model ops belongs in scripts, where it
already lives); PWA + GDPR + admin + beta codes — SaaS compliance theater.

**HALF-DONE:** symbol-mismatch bug breaks the core loop while periphery
ships; BTCUSD flowrex_v2 drift; NAS100 "DO NOT enable live"; regime features
only on Potential; Scout as a synthetic-pipeline hack; mobile overflow.

**The 30% cut:** this is **a personal trading tool wearing a SaaS costume**
— believe the 2GB droplet. Keep `/`, `/agents`, `/trading`, `/settings`
(broker + password), `/login`; one agent type, one broker, two symbols,
Telegram for alerts. Freeze the rest. A tool that reliably trades two
symbols beats a platform that almost does everything.

---

## Forge — Engineering quality

**SOLID:** 637 real tests across 59 files (more than docs claim) + honest
CI; pydantic settings done right; 8 disciplined alembic migrations; compose
memory limits/healthchecks/backups; 21 tests on `risk_manager.py`.

**DEBT:**
1. God-files: `api/backtest.py` 1,460 lines, `engine.py` 1,227, `api/ml.py`
   874, `flowrex_agent_v2.py` 821, `potential_agent.py` 728; frontend
   `backtest/page.tsx` 1,432, `settings/page.tsx` 1,126.
2. Copy-paste session classifier ×4 (`potential_agent.py:407`,
   `flowrex_agent_v2.py:370`, twice in `api/backtest.py:758,939`); direction
   gate + ATR SL/TP near-verbatim between agents. One drift and
   backtest ≠ live, silently.
3. 191 `except Exception` in app/, 21 silent `pass` handlers — one in the
   signal path (`potential_agent.py:395`); only ~10 `logger.error` calls.
4. Legacy agents still routed by `engine.py:77-87` + ensemble fallback shims.
5. 471MB of data in git; compose limits sum to ~2.6GB on a 2GB droplet
   (OOM lottery); `DEBUG=True` bypasses auth.

**TEST REALITY — inverted from risk:** feature math is drowning in tests
(tier1 33, calendar 21) while the order-lifecycle spine is nearly bare:
`engine.py` 4 tests, `trade_monitor.py` **zero**, broker_manager 7. The
least-tested code is exactly where the money-losing bugs live.

**LEVERAGE MOVES:** (1) one shared `filters.py` consumed by live agents AND
backtest sim — parity by construction; (2) delete scalping/expert/flowrex-v1
paths (~1,000 lines); (3) 20 tests on engine tick-loop + trade_monitor
before writing the swing agent.

---

## Synthesis — where all five agree

1. **Foundations are good.** Broker layer, RiskManager math, engine
   lifecycle, backtest data plumbing, auth/deploy. This was not wasted work.
2. **The strategy layer is the problem.** Too many agents, too many feature
   stacks, models whose validation was compromised (no embargo, OOS reuse,
   wrong Optuna objective). The live-vs-backtest divergence was the
   experiment that proved it.
3. **Execution/risk bugs come before any strategy work.** The broken
   force-flat path and the in-memory risk state are account-killers
   regardless of how good the new signals are.
4. **The pivot direction is right** — higher timeframes, fewer symbols,
   structural price features — with two corrections: candlestick patterns
   are noise (cut), and H4 sample size demands ≤15–25 features and probably
   rules-first rather than GBM-first.
5. **Scope must shrink to match one maintainer.** One agent type, one
   broker, two symbols, five pages.

## Owner's proposed direction — point-by-point verdict

| Proposal | Verdict |
|---|---|
| H1/H4/D1 timeframes | **Yes.** Slower decisions, fewer trades, less noise, execution costs matter less. But it's a signal-layer rewrite (M5 is currently primary everywhere) and live validation takes quarters. |
| US30 + XAUUSD focus | **Yes.** Both have 15 years of in-repo history. Note: their correlation is regime-dependent (often inverse in risk-off) — useful as a filter feature, not as "they move together." |
| Supply/demand zones on all 3 TFs | **Yes — already 80% built.** Order-block + FVG detection in `features_ict.py` IS supply/demand. Extract and re-point at H1/H4/D1. |
| Swing highs/lows | **Yes — exists.** Swing-point + BOS/CHOCH logic in `features_ict.py`/`smc_features.py`. Fractal-style N-bar pivots on each TF. |
| Candlestick patterns | **No (weak).** ~51% base rate alone. If used at all, only as an entry *trigger* inside a zone, never as a signal. Vega recommends cutting to save the feature budget. |
| Suitable lookback | Zones: last 200–300 bars per TF (~2 months H1, ~2 quarters H4, ~1 year D1). Swing structure: 50–100 bars. Training: pool all 15 years of H4 across both symbols. |
| "AI up to date like TradingView indicators" | TradingView indicators are deterministic formulas, not intelligence — Python computes the identical math on live bars (much of it already exists in-repo). The system doesn't need "up-to-date AI" for indicators; it needs the same formula applied to the latest bar, which is what feature modules do. Where up-to-dateness genuinely matters (news, macro regime), the existing Claude supervisor is the right tool — as a veto/context layer, not a signal generator. |

## Recommended plan (proposed, not started)

- **Phase 0 — Stop the bleeding (before anything else):** fix Oanda
  ticket-close + reconcile early-return; persist daily P&L and direction
  locks to DB; account-level equity-based daily stop; wire hard-stop
  liquidation; cross-agent symbol dedupe; verify
  `data/symbol_mappings.json` on the droplet (symbol-mismatch bug).
- **Phase 1 — The cut:** delete/freeze 4 agent types, legacy feature stacks,
  redundant training scripts, periphery pages; archive M5 joblibs; extract
  shared `filters.py`; add engine/trade_monitor tests.
- **Phase 2 — The swing system:** one agent, H1/H4/D1, US30 + XAUUSD,
  ~15–25 structural features (zones, swing structure, ATR context, session,
  D1 trend), rules-first with proper stats; ML only as an optional
  meta-filter later, trained with embargoed purged walk-forward and a
  profit-based objective.
- **Phase 3 — Validate slowly:** paper trade H4 for a full quarter before
  any funded attempt.

## Open questions for the owner

1. **Personal tool or product?** Beta testers + GDPR say product; the 2GB
   droplet and solo maintenance say personal tool. The 30% cut depends on
   this answer.
2. **Which prop firm?** H4/D1 swing violates FundedNext Bolt's no-overnight
   rule. FTMO-style (swing OK) vs Bolt (intraday only) changes the design.
3. **Rules-first or ML-first** for the new swing system? (Team recommends
   rules-first with statistical validation, ML later as a filter.)
4. **Keep the backtest research IDE** (the 1,432-line page) or demote it to
   scripts?

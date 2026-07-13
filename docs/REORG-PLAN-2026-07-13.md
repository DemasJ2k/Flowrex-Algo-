# Flowrex Reorganisation Plan (2026-07-13)

Follows from `docs/TEAM-REVIEW-2026-07-13.md` (five-agent review). This is
the execution plan with the owner's decisions locked in.

## Locked decisions (owner, 2026-07-13)

1. **Personal tool, not a product.** SaaS surface (beta codes, GDPR, PWA,
   admin, multi-broker) gets frozen, not maintained.
2. **Prop-firm-style strict rules, with overnight holds allowed as the
   one exception.** All trades are expected to hold overnight (H1/H4/D1
   swing). This rules out FundedNext Bolt (no-overnight rule) — target an
   FTMO-style program. Everything else stays strict: account-level daily
   stop, trailing DD, consistency-style discipline.
3. **Rules-first** for the new swing system. ML returns later, if ever,
   only as a meta-filter with embargoed purged walk-forward validation.
4. **Backtest research UI frozen.** The backtest backend stays; the new
   rules-based H4 backtester lives in `scripts/`. The 1,432-line
   `/backtest` page is not maintained further.

## Phase 0 — Stop the bleeding (risk/execution) — DONE 2026-07-13

All five account-killers from the review, fixed and tested
(`tests/test_engine_risk_fixes.py`, 13 tests):

1. **Trade-level broker close.** `OandaAdapter.close_trade(ticket)` added
   (`PUT /v3/accounts/{id}/trades/{ticket}/close`). Engine force-flat /
   max-hold / hard-stop paths now use `_close_broker_trade()`, which
   prefers trade-level close. Previously every ticket-based close on
   Oanda failed with "Invalid position ID format".
2. **Reconcile no longer fabricates closes.**
   `_reconcile_closed_trade_from_broker` returns early when the broker
   reports the trade OPEN (or when a non-Oanda broker still shows
   matching exposure). A DB row is marked closed only when the broker
   confirms it.
3. **Daily risk state from the DB, account-level.**
   `_compute_daily_stats()` sums today's realized P&L and opened trades
   across ALL of the user's agents on the broker; floating P&L from the
   broker account is added. Replaces per-agent in-memory counters that
   reset on every restart/deploy.
4. **Hard stop liquidates.** On breach of `max_daily_loss_pct` (default
   3%), the engine closes all open positions and skips evaluation until
   the next UTC day. Previously it only blocked new entries.
5. **Duplicate-trade guard in the DB.** One open trade per symbol per
   user, across all agents and both directions. Replaces the in-memory
   `_active_direction` lock (reset on restart, wiped on any order
   failure — the root cause of the live duplicate trades).

Still open from the review (Phase 0.5, before live money):
- Verify `data/symbol_mappings.json` on the droplet (symbol-mismatch bug
  false-negative trap).
- `DEBUG=True` auth bypass landmine; compose memory limits exceed the
  2GB droplet.

## Phase 1 — The cut — DONE 2026-07-13

Delete/freeze so one person can maintain what remains:

- **Agents:** delete `scalping_agent.py`, `expert_agent.py`,
  `flowrex_agent.py` (v1), `scout_agent.py`, `m5_signal_generator.py`,
  `ict_signal_generator.py`; collapse `engine.py` dispatch.
- **Features:** keep `features_potential.py` as the base; salvage
  order-block / FVG / swing-point / BOS-CHOCH primitives from
  `features_ict.py`; delete `features_williams.py`, `features_quant.py`,
  `features_cot.py`, `features_mtf.py`, `features_correlation.py`,
  `smc_features.py`, `features_tier1.py`, `meta_labeler.py` (v1).
- **Training:** delete all `train_*.py` except one new swing trainer;
  archive the 24 M5 joblibs.
- **Shared filters:** extract one `filters.py` (session/regime/direction
  gates) consumed by both live agent and backtest — parity by
  construction (kills the ×4 session-classifier copies).
- **Frontend:** keep `/`, `/agents`, `/trading`, `/settings`, `/login`;
  freeze `/backtest`, `/ai`, `/news`, `/models`, admin, PWA.
- **Brokers:** Oanda primary; others remain in-tree but unregistered.
- **Tests first:** ~20 tests on engine tick-loop + trade_monitor before
  the swing agent lands (trade_monitor currently has zero).

## Phase 2 — The swing system (rules-first)

- One agent: H1/H4/D1, US30 + XAUUSD.
- ~15–25 structural features max (Vega's sample-size cliff: H4 ≈ 1,500
  bars/yr/symbol): supply/demand zones (order blocks + FVG on all three
  TFs), swing highs/lows + BOS/CHOCH, ATR context, D1 trend, session.
  No candlestick patterns (base-rate noise).
- Zone lookbacks: 200–300 bars per TF; swing structure 50–100 bars.
- Transparent entry/exit rules; every rule statistically tested on
  15 years of pooled H4 across both symbols before it ships.
- New `scripts/backtest_swing.py` for validation (replaces backtest UI).

## Phase 3 — Validate slowly

- Paper trade a full quarter on H4 before any funded attempt.
- Weekly review via existing Telegram/supervisor reporting.

# Flowrex Algo — Beta Tester Guide

Welcome to the Flowrex Algo beta! This guide walks you through getting started.

---

## 1. Registration

Go to **https://flowrexalgo.com/register**

You'll need:
- **Invite code** — provided by the admin (single-use, 30-day expiry)
- **Email** — must be unique
- **Password** — minimum 12 characters with uppercase, lowercase, and a digit
- **Date of birth** — you must be 18+ to use the platform (financial trading requirement)
- **Terms acceptance** — check the box to accept the Terms of Service and Privacy Policy

## 2. Connect Your Broker

After registration, go to **Settings → Trading → Broker Connections**.

Currently supported:
- **Oanda** (recommended for paper trading) — [Create a practice account](https://www.oanda.com/register/)
- **Tradovate** (futures) — for ES, NQ, YM, GC
- **cTrader** — multi-asset
- **MT5** — MetaTrader 5

For Oanda:
1. Click **+ Add Connection** or **Connect** next to Oanda
2. Enter your Oanda API key and Account ID
3. Check "Practice" for paper trading
4. Click Connect — your balance should appear within 10 seconds

## 3. Create an Agent

Go to the **Agents** page → click **+ New Agent**.

**Step 1 — Setup:**
- **Agent Name:** e.g., "XAUUSD Flowrex"
- **Symbol:** XAUUSD, BTCUSD, US30, ES, NAS100
- **Agent Type:** `flowrex_v2` (120 features, 3-model ensemble) or `potential` (85 features, 2-model ensemble)
- **Broker:** Oanda (or whichever you connected)

**Step 2 — Risk:**
- **Risk per Trade:** Start with 0.10% for paper trading
- **Max Daily Loss:** 3-6%
- **Cooldown:** 3-10 bars (how long to wait between trades)

**Step 3 — Deploy:**
- Review your settings
- Click **Deploy Agent**
- The agent starts polling immediately (every 60 seconds)

## 4. Monitor Trades

On the **Agents** page, you'll see:
- **Status badge:** Running (green), Paused (amber), Stopped (grey)
- **P&L:** Total profit/loss since agent creation
- **Win rate:** Percentage of profitable trades
- **Logs:** Click the agent card to expand and see the live log stream

Common log messages:
- `Eval #N: no signal` — the model evaluated and found no trading opportunity
- `SIGNAL BUY XAUUSD conf=0.85` — a trading signal was generated
- `OPENED BUY XAUUSD @ 4800.00` — trade was executed on the broker
- `CLOSED BUY XAUUSD | TP_HIT` — trade hit its take-profit target

## 5. Edit Config

Click the **gear icon** on any agent to open Edit Config:
- Change risk%, cooldown, sizing mode, max lot size
- Changes take effect immediately on the running agent (hot-reload)
- No need to stop/restart the agent

## 6. Export Your Data

Go to **Settings → Privacy & Data**:
- **Export My Data** — downloads a JSON file with all your profile, agents, trades, and logs
- **Delete My Account** — permanently removes all your data (requires password confirmation)

## 7. Known Limitations

- **ES** — uses Dukascopy S&P 500 CFD data, not futures. Pip values may differ slightly from live ES futures.
- **MT5** — recently fixed (was crashing on order placement). Functional but less tested than Oanda.
- **AI Supervisor** — requires your own Anthropic API key. Configure in Settings → AI Supervisor.
- **Mobile** — the UI works on mobile but is optimized for desktop.

## 8. Getting Help

- **Feedback:** Settings → Feedback tab — submit bugs or feature requests
- **Access requests:** If you know someone who wants to join, they can request access at the login page

---

_Last updated: 2026-04-16 (Beta v1.0)_

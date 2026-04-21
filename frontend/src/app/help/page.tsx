"use client";

import { useState } from "react";
import api from "@/lib/api";
import { toast } from "sonner";
import { getErrorMessage } from "@/lib/errors";
import Glass from "@/components/ui/Glass";
import Tabs from "@/components/ui/Tabs";
import {
  LifeBuoy, BookOpen, Landmark, Server, MessageSquare, ExternalLink,
  Clock, Loader2, Brain, Settings2, TestTube,
} from "lucide-react";

type BrokerGuide = {
  name: string;
  tagline: string;
  platform: string;
  demo: string;
  setup: string[];
  signupUrl: string;
};

const BROKER_GUIDES: BrokerGuide[] = [
  {
    name: "Oanda",
    tagline: "FX + metals + indices CFDs. Free demo in ~2 minutes.",
    platform: "REST API v20",
    demo: "Yes — instant practice account.",
    setup: [
      "Create a demo (fxTrade Practice) account at oanda.com.",
      "In the Oanda web dashboard → Manage API Access → Generate a personal access token.",
      "Note your Account ID (shown on the same page).",
      "In Flowrex → Settings → Broker Connections → Add Connection → Oanda.",
      "Paste the API token + Account ID. Flowrex verifies the connection live.",
    ],
    signupUrl: "https://www.oanda.com/apply/",
  },
  {
    name: "cTrader",
    tagline: "OAuth-based; works with any cTrader-licensed broker (Pepperstone, IC Markets, FXPro, etc.).",
    platform: "Open API 2.0",
    demo: "Yes — most cTrader brokers offer free demo accounts.",
    setup: [
      "Sign up with a cTrader broker and open a demo account.",
      "In Flowrex → Settings → Broker Connections → Add Connection → cTrader.",
      "You will be redirected to the cTrader login to authorize Flowrex.",
      "After approving, your account(s) appear in Flowrex's broker list.",
    ],
    signupUrl: "https://ctrader.com/",
  },
  {
    name: "MetaTrader 5",
    tagline: "Windows-only terminal. Pairs well with prop firms.",
    platform: "MetaTrader5 Python package (Windows COM)",
    demo: "Yes — via your MT5 broker.",
    setup: [
      "Install MetaTrader 5 from your broker on a Windows PC (or Windows VPS).",
      "Log in to your demo or live account inside MT5.",
      "Run Flowrex on the same Windows machine — the MT5 adapter attaches to the running terminal.",
      "If you're on Mac/Linux, run a Windows VPS or use one of the other brokers. MT5's Python package does not work outside Windows.",
    ],
    signupUrl: "https://www.metatrader5.com/",
  },
  {
    name: "Tradovate",
    tagline: "US futures (ES, NQ, GC, CL, micro contracts). Popular with prop firms.",
    platform: "REST API + WebSocket (OAuth2)",
    demo: "Yes — 14-day demo account.",
    setup: [
      "Sign up at tradovate.com and start the free 14-day demo.",
      "From the Tradovate web app → Settings → API Access → create an Application.",
      "Copy the credentials (client_id, secret) into Flowrex → Settings → Broker Connections → Add → Tradovate.",
      "Pick Live or Demo environment.",
    ],
    signupUrl: "https://www.tradovate.com/",
  },
  {
    name: "Interactive Brokers",
    tagline: "Global markets, tight spreads, professional-grade reporting.",
    platform: "Client Portal Web API (REST) — cloud-friendly",
    demo: "Yes — free IBKR paper account.",
    setup: [
      "Open a paper trading account at interactivebrokers.com.",
      "In Client Portal → API → enable the Web API; generate a consumer key.",
      "In Flowrex → Settings → Broker Connections → Add Connection → Interactive Brokers.",
      "Paste consumer key + account ID. Flowrex uses the REST API — no local Gateway install required.",
    ],
    signupUrl: "https://www.interactivebrokers.com/en/trading/free-trial.php",
  },
];

type PropFirm = {
  name: string;
  platform: string;
  minChallenge: string;
  profitSplit: string;
  status: "supported" | "compatible" | "incompatible";
  lastVerified: string;
  url: string;
  notes?: string;
};

const PROP_FIRMS: PropFirm[] = [
  {
    name: "FTMO",
    platform: "MT5 / cTrader / DXtrade",
    minChallenge: "$10k",
    profitSplit: "80% → 90%",
    status: "supported",
    lastVerified: "2026-04-18",
    url: "https://ftmo.com/",
  },
  {
    name: "FundedNext",
    platform: "MT5 / MT4",
    minChallenge: "$6k",
    profitSplit: "80% → 95%",
    status: "supported",
    lastVerified: "2026-04-18",
    url: "https://fundednext.com/",
  },
  {
    name: "The 5%ers",
    platform: "MT5 / cTrader",
    minChallenge: "$6k",
    profitSplit: "75% → 100%",
    status: "supported",
    lastVerified: "2026-04-18",
    url: "https://the5ers.com/",
  },
  {
    name: "Topstep",
    platform: "Tradovate",
    minChallenge: "$50k (futures)",
    profitSplit: "100% up to 1st $10k",
    status: "supported",
    lastVerified: "2026-04-18",
    url: "https://www.topstep.com/",
  },
  {
    name: "Apex Trader Funding",
    platform: "Tradovate",
    minChallenge: "$25k (futures)",
    profitSplit: "100% up to 1st $25k",
    status: "supported",
    lastVerified: "2026-04-18",
    url: "https://apextraderfunding.com/",
  },
  {
    name: "E8 Markets",
    platform: "cTrader / MT5",
    minChallenge: "$25k",
    profitSplit: "80% → 90%",
    status: "supported",
    lastVerified: "2026-04-18",
    url: "https://e8markets.com/",
  },
  {
    name: "Funded Trading Plus",
    platform: "MT5",
    minChallenge: "$5k",
    profitSplit: "80% → 90%",
    status: "compatible",
    lastVerified: "2026-04-18",
    url: "https://fundedtradingplus.com/",
    notes: "EA/automation allowed — confirm with support for your plan.",
  },
  {
    name: "My Forex Funds",
    platform: "—",
    minChallenge: "—",
    profitSplit: "—",
    status: "incompatible",
    lastVerified: "2026-04-18",
    url: "https://myforexfunds.com/",
    notes: "Company ceased operations in 2024.",
  },
  {
    name: "SurgeTrader",
    platform: "—",
    minChallenge: "—",
    profitSplit: "—",
    status: "incompatible",
    lastVerified: "2026-04-18",
    url: "https://surgetrader.com/",
    notes: "Closed for new accounts.",
  },
];

type AgentStrategy = {
  id: string;
  name: string;
  summary: string;
  features: string;
  bestFor: string[];
  pros: string[];
  cons: string[];
};

const AGENT_STRATEGIES: AgentStrategy[] = [
  {
    id: "flowrex_v2",
    name: "Flowrex Agent v2",
    summary: "Largest feature footprint + 3-model ensemble majority-vote. Built for conviction signals — fires fewer trades with stronger setups.",
    features: "120 curated features across M5/M15/H1/H4/D1 · XGBoost + LightGBM + CatBoost majority-vote",
    bestFor: ["Swing + intraday trend continuations", "Indices and FX majors during London/NY", "Users who want fewer, higher-quality signals"],
    pros: [
      "Ensemble drastically cuts false positives vs a single model.",
      "4-layer MTF means signals only fire when HTF agrees with M5 direction.",
      "Curated feature set is regime-aware (ICT, Williams, Quant modules).",
    ],
    cons: [
      "Slower to react — will often miss the first leg of a fast move.",
      "Slightly higher compute per tick (three models run per bar).",
      "All three models must be deployed; if one symbol is missing a CatBoost, the ensemble gracefully falls back to the remaining two.",
    ],
  },
  {
    id: "potential",
    name: "Potential Agent v2",
    summary: "Leaner institutional-feature model. More trades, faster entries, tighter stops.",
    features: "85 ATR-normalized institutional features (VWAP, anchored VWAPs, ORB, ADX) · XGBoost + LightGBM",
    bestFor: ["Scalp-to-swing hybrid on liquid symbols", "Users testing new symbols quickly (faster retrain)", "Prop-firm challenges that reward trade frequency"],
    pros: [
      "Walk-forward validated with a stamped OOS boundary — backtest honesty is built in.",
      "Per-symbol TP/SL/confidence tuned via `symbol_config.py`.",
      "Smaller feature set means faster retraining cycles.",
    ],
    cons: [
      "Lone models can overfit to a regime; Flowrex v2's ensemble is safer against regime drift.",
      "Tighter stops on choppy symbols (NAS100/US30) historically got chopped — we widened them Apr 2026 but monitor carefully.",
      "No built-in lookback entry (see Scout).",
    ],
  },
  {
    id: "scout",
    name: "Scout Agent",
    summary: "Potential's models + a lookback/pullback/BOS entry state machine. Waits for structural confirmation before entering.",
    features: "Reuses deployed Potential joblibs · 40-bar lookback window · entry state machine (pullback / break-of-structure / instant-confidence)",
    bestFor: ["Users who want fewer whipsaws and better entry prices", "Trend-day fades after a pullback", "When live-testing a new symbol you don't fully trust yet"],
    pros: [
      "Pullback entries hit cleaner risk:reward than market-on-signal.",
      "Instant-entry shortcut (conf ≥ 0.85) means you don't miss the rare high-conviction signals.",
      "Dedupe filter stops the model from stacking same-direction trades in a chop zone.",
    ],
    cons: [
      "Will miss signals where no pullback or BOS arrives within the pending window.",
      "Live behaviour depends on market volatility — in calm regimes, more pendings expire.",
      "Uses Potential's models, so if Potential is Grade F on a symbol, Scout inherits that.",
    ],
  },
];

type ConfigRef = {
  key: string;
  label: string;
  appearsIn: string;
  what: string;
  why: string;
  defaultValue: string;
};

const CONFIG_GLOSSARY: ConfigRef[] = [
  {
    key: "risk_per_trade",
    label: "Risk per trade (%)",
    appearsIn: "Wizard · Edit Config · Settings → Trading",
    what: "Fraction of account balance to risk on each trade. Sets position size via stop distance.",
    why: "Keeps drawdowns predictable — at 0.5% per trade, ten losses in a row = ~5% account drawdown. Prop-firm challenges typically want 0.5–0.75% max.",
    defaultValue: "0.5%",
  },
  {
    key: "max_daily_loss_pct",
    label: "Max daily loss (%)",
    appearsIn: "Wizard · Edit Config · Settings → Trading",
    what: "Hard stop. Once the day's P&L drops below -(max_daily_loss_pct × balance), the agent pauses until UTC midnight.",
    why: "Prevents death-spiral trading. FTMO-style challenges use 4–5%; we default to a cautious 3%.",
    defaultValue: "3% (4% when Prop Firm mode is on)",
  },
  {
    key: "cooldown_bars",
    label: "Cooldown (bars)",
    appearsIn: "Wizard · Edit Config",
    what: "After each entry, the agent waits this many M5 bars before considering another signal on the same symbol.",
    why: "Stops the agent from re-entering a trade it just exited (common at reversal candles). 3 bars = 15 minutes.",
    defaultValue: "3",
  },
  {
    key: "session_filter",
    label: "Session filter",
    appearsIn: "Wizard · Edit Config · Settings · Backtest sandbox",
    what: "Restricts trading to selected UTC session buckets: Asian, London, NY Open, NY Close, Off Hours.",
    why: "Liquidity matters. NAS100/US30 chop during Asia; forex moves during London/NY. Blocking low-edge sessions lifts hit-rate without changing the model.",
    defaultValue: "London + NY Open + NY Close",
  },
  {
    key: "regime_filter",
    label: "Regime filter",
    appearsIn: "Wizard · Edit Config · Settings · Backtest sandbox",
    what: "Classifies the current market using a rule tree (ATR percentile → ADX → EMA50 slope). Hard-skips trades when the regime is not in the allowed list.",
    why: "Mean-reverting models bleed in trending regimes and vice-versa. Instead of retraining per regime, block the trades that were already doomed.",
    defaultValue: "all four regimes allowed",
  },
  {
    key: "news_filter_enabled",
    label: "News filter",
    appearsIn: "Wizard · Edit Config · Settings",
    what: "Skips entries 30 minutes before high-impact economic releases (CPI, FOMC, NFP).",
    why: "Models aren't trained on news-driven spikes. Skipping keeps your expectancy stable through calendar events.",
    defaultValue: "On",
  },
  {
    key: "use_correlations",
    label: "Symbol correlations",
    appearsIn: "Wizard · Edit Config · Settings · Backtest sandbox",
    what: "Include cross-symbol features (e.g. BTC↔US30, XAU↔BTC). Off zero-masks those columns before inference.",
    why: "These features capture spillover flow (risk-on/risk-off). Keep on by default; turn off to debug drift or test signal robustness.",
    defaultValue: "On",
  },
  {
    key: "allow_buy / allow_sell",
    label: "Direction gate",
    appearsIn: "Wizard · Edit Config",
    what: "Enable long-only, short-only, or both. Disabling one side discards signals in that direction.",
    why: "When analytics show a strong directional bias for a symbol-timeframe (e.g. XAUUSD longs win more than shorts), restrict to the winning side.",
    defaultValue: "Both enabled",
  },
  {
    key: "prop_firm_enabled",
    label: "Prop Firm mode",
    appearsIn: "Wizard · Edit Config",
    what: "Activates FTMO-style tiered drawdown gates: yellow (-1.5% size↓), red (-2.5% pause), hard (-3% close all).",
    why: "Prop firms kill accounts on hard DD. This keeps you inside their rules automatically.",
    defaultValue: "Off",
  },
  {
    key: "lookback_bars",
    label: "Scout: Lookback bars",
    appearsIn: "Wizard (scout only) · Edit Config (scout only)",
    what: "Number of historical bars Scout scans for break-of-structure (BOS) reference highs/lows.",
    why: "Longer lookback = more conservative BOS entries. 40 bars = ~3h 20m on M5.",
    defaultValue: "40",
  },
  {
    key: "instant_entry_confidence",
    label: "Scout: Instant-entry confidence",
    appearsIn: "Wizard (scout only) · Edit Config (scout only)",
    what: "If model confidence exceeds this threshold, Scout enters immediately instead of waiting for pullback/BOS.",
    why: "High-confidence signals rarely retrace. 0.85 is a good balance; lower → fewer waits, higher → more patience.",
    defaultValue: "0.85",
  },
  {
    key: "max_pending_bars",
    label: "Scout: Max pending bars",
    appearsIn: "Wizard (scout only) · Edit Config (scout only)",
    what: "Pending signals that don't trigger within this many bars are discarded.",
    why: "Stale pendings misrepresent current conditions. 10 bars = 50 minutes on M5.",
    defaultValue: "10",
  },
  {
    key: "pullback_atr_fraction",
    label: "Scout: Pullback (× ATR)",
    appearsIn: "Wizard (scout only) · Edit Config (scout only)",
    what: "How far price must retrace (in ATR multiples) before a pullback entry can trigger.",
    why: "Too small = you enter at the signal bar (defeats Scout's purpose). Too big = you rarely enter. 0.5× ATR is the sweet spot most symbols.",
    defaultValue: "0.50",
  },
  {
    key: "dedupe_window_bars",
    label: "Scout: Dedupe window",
    appearsIn: "Wizard (scout only) · Edit Config (scout only)",
    what: "If the last closed trade was the same direction within this many bars, Scout skips the next pending.",
    why: "Stops the model from stacking multiple longs (or shorts) in a chop zone when the same setup keeps re-emitting.",
    defaultValue: "20",
  },
  {
    key: "ADX",
    label: "ADX (glossary)",
    appearsIn: "Regime classifier only",
    what: "Average Directional Index. Measures trend strength, not direction. 0–20 = no trend, 20–40 = moderate, 40+ = strong trend.",
    why: "We use ADX < 20 as the 'ranging' regime signal. Models trained on trending data bleed when ADX < 20, so the regime filter blocks those bars.",
    defaultValue: "—",
  },
  {
    key: "ATR",
    label: "ATR (glossary)",
    appearsIn: "Stop/TP sizing · Regime classifier · Feature engineering",
    what: "Average True Range. Measures volatility in price units.",
    why: "Stops placed at 0.8× ATR are a stable way to 'scale' loss tolerance with market conditions — tighter in calm markets, wider in wild ones.",
    defaultValue: "—",
  },
];

const FAQ: { q: string; a: string }[] = [
  {
    q: "Do I need a VPS for MT5?",
    a: "Technically no — you can run MT5 + Flowrex on a Windows PC that stays on 24/7. But MT5's Python package is Windows-only, and trading bots need uptime, so most users rent a small Windows VPS (~$10–$30/mo) to avoid missed fills. Other brokers (Oanda, cTrader, Tradovate, Interactive Brokers) don't have this requirement.",
  },
  {
    q: "Why are my reports in UTC?",
    a: "Flowrex stores your timezone in Settings → Preferences. The first time you log in, we auto-detect it from your browser and ask you to confirm. If it's wrong, change it in Settings — all future reports, dashboards, and trade history switch to that zone.",
  },
  {
    q: "Why did my AI supervisor say the market was broken?",
    a: "It shouldn't. If you see a hallucinated 'system failure' message, make sure you're on the latest build — we fixed the supervisor prompt so it knows forex/futures close on weekends while crypto trades 24/7. If you still see odd reports, submit feedback with a screenshot.",
  },
  {
    q: "How often do the AI reports fire?",
    a: "Configurable per user in AI Supervisor → Settings → Monitoring. Presets: Off, 1h, 4h, 12h, Daily. You can also set quiet hours (e.g. 22:00–07:00) and choose to skip sends when markets are closed or when nothing has changed.",
  },
  {
    q: "Can I connect more than one broker?",
    a: "Yes. From Settings → Broker Connections click 'Add Connection' for each broker. Each agent still targets one specific broker — that stays 1:1 — but you can run Oanda agents alongside Tradovate agents, etc.",
  },
  {
    q: "When does retraining happen?",
    a: "Automatically on the 1st of each month for the three core symbols (US30, BTCUSD, XAUUSD). You can also trigger a retrain manually from the Models page.",
  },
  {
    q: "Is there a downloadable app?",
    a: "Flowrex is a progressive web app — on Chrome, Safari, or Edge you'll see an 'Install app' prompt that adds a standalone window / home-screen icon. A dedicated desktop companion for MT5 users is on the roadmap.",
  },
];

function StatusPill({ status }: { status: PropFirm["status"] }) {
  const map = {
    supported: { label: "Supported", color: "text-emerald-400 border-emerald-500/40" },
    compatible: { label: "Compatible", color: "text-blue-400 border-blue-500/40" },
    incompatible: { label: "N/A", color: "text-gray-400 border-gray-500/40" },
  } as const;
  const s = map[status];
  return (
    <span className={`inline-block text-[10px] font-medium px-2 py-0.5 rounded-full border ${s.color}`}>
      {s.label}
    </span>
  );
}

export default function HelpPage() {
  const [feedbackType, setFeedbackType] = useState("bug");
  const [feedbackMsg, setFeedbackMsg] = useState("");
  const [prefilledFirm, setPrefilledFirm] = useState<string | null>(null);
  const [sending, setSending] = useState(false);

  const submitFeedback = async () => {
    setSending(true);
    try {
      const prefix = prefilledFirm ? `[prop-firm: ${prefilledFirm}] ` : "";
      await api.post("/api/feedback", {
        feedback_type: feedbackType,
        message: prefix + feedbackMsg,
      });
      toast.success("Feedback submitted — thank you!");
      setFeedbackMsg("");
      setPrefilledFirm(null);
    } catch (e) {
      toast.error(getErrorMessage(e));
    } finally {
      setSending(false);
    }
  };

  const reportPropFirm = (firm: PropFirm) => {
    setPrefilledFirm(firm.name);
    setFeedbackType("provider_request");
    setFeedbackMsg(`Update for ${firm.name}: `);
    const el = document.getElementById("feedback-form");
    if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  return (
    <div className="space-y-4 max-w-5xl">
      <div className="flex items-center gap-2">
        <LifeBuoy size={20} className="text-violet-400" />
        <h1 className="text-2xl md:text-3xl font-semibold tracking-tight text-gradient">Help & Support</h1>
      </div>

      <Tabs tabs={[
        {
          label: "Quick Start",
          content: (
            <div className="space-y-4">
              <Glass padding="md">
                <h3 className="text-sm font-medium mb-2 flex items-center gap-2">
                  <BookOpen size={14} /> Getting Started
                </h3>
                <ol className="space-y-2 text-sm list-decimal list-inside" style={{ color: "var(--foreground)" }}>
                  <li>Pick a broker in the Broker Setup tab and follow its checklist.</li>
                  <li>Connect it in Settings → Broker Connections.</li>
                  <li>Create your first agent: Agents → New Agent. Start on a demo account.</li>
                  <li>(Optional) Enable the AI Supervisor on the AI page for Telegram status reports.</li>
                  <li>Watch the Dashboard / Trading page — you can pause or close any agent live.</li>
                </ol>
                <p className="text-xs mt-3" style={{ color: "var(--muted)" }}>
                  Need more detail? See the full{" "}
                  <a href="/docs/USER-GUIDE.txt" className="text-violet-400 hover:underline">
                    user guide
                  </a>.
                </p>
              </Glass>
            </div>
          ),
        },
        {
          label: "Agent Guide",
          content: (
            <div className="space-y-4">
              <Glass padding="md">
                <h3 className="text-sm font-medium mb-2 flex items-center gap-2">
                  <Brain size={14} className="text-violet-400" /> Agent Strategies
                </h3>
                <p className="text-[11px] mb-3" style={{ color: "var(--muted)" }}>
                  Three agent types ship with Flowrex. They use different feature engineering, different
                  models, and different entry logic. Pick based on how you trade, not which is newest.
                </p>
                <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                  {AGENT_STRATEGIES.map((a) => (
                    <div key={a.id} className="p-3 rounded-lg border" style={{ borderColor: "var(--border)", background: "rgba(255,255,255,0.02)" }}>
                      <h4 className="text-sm font-semibold mb-1">{a.name}</h4>
                      <p className="text-[11px] mb-2" style={{ color: "var(--muted)" }}>{a.summary}</p>
                      <div className="text-[10px] mb-2" style={{ color: "var(--muted)" }}>
                        <span style={{ color: "var(--foreground)" }}>Under the hood:</span> {a.features}
                      </div>
                      <div className="mb-2">
                        <p className="text-[11px] font-medium mb-1">Best for</p>
                        <ul className="text-[11px] space-y-0.5 list-disc list-inside" style={{ color: "var(--muted)" }}>
                          {a.bestFor.map((b, i) => <li key={i}>{b}</li>)}
                        </ul>
                      </div>
                      <div className="mb-2">
                        <p className="text-[11px] font-medium mb-1 text-emerald-400">Pros</p>
                        <ul className="text-[11px] space-y-0.5 list-disc list-inside" style={{ color: "var(--muted)" }}>
                          {a.pros.map((p, i) => <li key={i}>{p}</li>)}
                        </ul>
                      </div>
                      <div>
                        <p className="text-[11px] font-medium mb-1 text-amber-400">Cons</p>
                        <ul className="text-[11px] space-y-0.5 list-disc list-inside" style={{ color: "var(--muted)" }}>
                          {a.cons.map((c, i) => <li key={i}>{c}</li>)}
                        </ul>
                      </div>
                    </div>
                  ))}
                </div>
              </Glass>

              <Glass padding="md">
                <h3 className="text-sm font-medium mb-2 flex items-center gap-2">
                  <TestTube size={14} className="text-violet-400" /> Paper vs Live
                </h3>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-xs">
                  <div className="p-3 rounded-lg border" style={{ borderColor: "var(--border)" }}>
                    <h4 className="text-sm font-semibold mb-1 text-blue-400">Paper mode</h4>
                    <ul className="space-y-1 list-disc list-inside" style={{ color: "var(--muted)" }}>
                      <li>Uses your broker's demo endpoint (Oanda Practice, IBKR Paper, Tradovate Demo).</li>
                      <li>Real market data, simulated execution.</li>
                      <li>Best for: validating a config over 2–4 weeks before going live, testing new symbols.</li>
                      <li>Metrics ARE representative — spread/slippage is modelled from symbol_config.</li>
                    </ul>
                  </div>
                  <div className="p-3 rounded-lg border" style={{ borderColor: "var(--border)" }}>
                    <h4 className="text-sm font-semibold mb-1 text-emerald-400">Live mode</h4>
                    <ul className="space-y-1 list-disc list-inside" style={{ color: "var(--muted)" }}>
                      <li>Real money against your live broker account.</li>
                      <li>Switch only AFTER paper mode shows consistent positive expectancy for 2+ weeks.</li>
                      <li>Start with Conservative risk (0.25%) even if paper was 1%.</li>
                      <li>Prop Firm mode highly recommended on challenge accounts.</li>
                    </ul>
                  </div>
                </div>
              </Glass>

              <Glass padding="md">
                <h3 className="text-sm font-medium mb-2 flex items-center gap-2">
                  <Settings2 size={14} className="text-violet-400" /> Config glossary
                </h3>
                <p className="text-[11px] mb-3" style={{ color: "var(--muted)" }}>
                  Every filter / knob you see in the Agent Wizard, Edit Config modal, and Settings → Trading tab.
                  Scout-specific knobs only appear when the agent type is Scout.
                </p>
                <div className="space-y-2">
                  {CONFIG_GLOSSARY.map((c) => (
                    <details key={c.key} className="p-2 rounded-lg border" style={{ borderColor: "var(--border)" }}>
                      <summary className="cursor-pointer text-sm font-medium flex items-center justify-between">
                        <span>{c.label}</span>
                        <span className="text-[10px]" style={{ color: "var(--muted)" }}>default: {c.defaultValue}</span>
                      </summary>
                      <div className="mt-2 text-[11px] space-y-1.5" style={{ color: "var(--muted)" }}>
                        <p><span style={{ color: "var(--foreground)" }}>What:</span> {c.what}</p>
                        <p><span style={{ color: "var(--foreground)" }}>Why:</span> {c.why}</p>
                        <p><span style={{ color: "var(--foreground)" }}>Appears in:</span> {c.appearsIn}</p>
                      </div>
                    </details>
                  ))}
                </div>
              </Glass>

              <Glass padding="md">
                <h3 className="text-sm font-medium mb-2">Edit agent config (post-create)</h3>
                <p className="text-[11px] mb-2" style={{ color: "var(--muted)" }}>
                  Every agent exposes its full config for live editing. From the Agents page, hover a card and
                  click the gear icon to open <span style={{ color: "var(--foreground)" }}>Edit Config</span>.
                  Changes apply on the next tick — no agent restart needed.
                </p>
                <ul className="text-[11px] space-y-1 list-disc list-inside" style={{ color: "var(--muted)" }}>
                  <li>Name, Mode (paper/live), Sizing mode, Risk %, Daily loss, Cooldown.</li>
                  <li>Direction gate (buy/sell).</li>
                  <li>Session filter + allowed sessions multi-select.</li>
                  <li>Regime filter + allowed regimes multi-select.</li>
                  <li>News filter · Symbol correlations.</li>
                  <li>Prop Firm mode.</li>
                  <li>Scout tuning (5 knobs) — only visible when the agent is a Scout.</li>
                </ul>
                <p className="text-[11px] mt-2" style={{ color: "var(--muted)" }}>
                  Use the <span style={{ color: "var(--foreground)" }}>Filter sandbox</span> on the Backtest page
                  to A/B test these settings before you change them on a live agent.
                </p>
              </Glass>
            </div>
          ),
        },
        {
          label: "Broker Setup",
          content: (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              {BROKER_GUIDES.map((b) => (
                <Glass key={b.name} padding="md">
                  <div className="flex items-start justify-between mb-2 gap-2">
                    <div>
                      <h3 className="text-sm font-semibold flex items-center gap-2">
                        <Server size={14} /> {b.name}
                      </h3>
                      <p className="text-[11px] mt-0.5" style={{ color: "var(--muted)" }}>{b.tagline}</p>
                    </div>
                    <a href={b.signupUrl} target="_blank" rel="noreferrer"
                      className="text-[11px] text-violet-400 hover:underline flex items-center gap-1 flex-shrink-0">
                      Sign up <ExternalLink size={10} />
                    </a>
                  </div>
                  <div className="text-[11px] space-y-1 mb-3" style={{ color: "var(--muted)" }}>
                    <div>Platform: <span style={{ color: "var(--foreground)" }}>{b.platform}</span></div>
                    <div>Demo available: <span style={{ color: "var(--foreground)" }}>{b.demo}</span></div>
                  </div>
                  <ol className="space-y-1.5 text-[12px] list-decimal list-inside">
                    {b.setup.map((step, i) => <li key={i}>{step}</li>)}
                  </ol>
                </Glass>
              ))}
            </div>
          ),
        },
        {
          label: "Prop Firms",
          content: (
            <div className="space-y-3">
              <Glass padding="md">
                <div className="flex items-start gap-2 mb-3">
                  <Landmark size={16} className="text-violet-400 mt-0.5" />
                  <div className="flex-1">
                    <h3 className="text-sm font-medium">Prop firm compatibility</h3>
                    <p className="text-[11px] mt-0.5" style={{ color: "var(--muted)" }}>
                      Prop firm policies change often. If you spot something out of date, click
                      "Report update" on the row — it pre-fills the feedback form for you.
                    </p>
                  </div>
                </div>
                <div className="overflow-x-auto">
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="text-left" style={{ color: "var(--muted)", borderBottom: "1px solid var(--border)" }}>
                        <th className="py-2 pr-3 font-medium">Firm</th>
                        <th className="py-2 pr-3 font-medium">Platform</th>
                        <th className="py-2 pr-3 font-medium">Min</th>
                        <th className="py-2 pr-3 font-medium">Split</th>
                        <th className="py-2 pr-3 font-medium">Status</th>
                        <th className="py-2 pr-3 font-medium">Verified</th>
                        <th className="py-2 pr-3 font-medium"></th>
                      </tr>
                    </thead>
                    <tbody>
                      {PROP_FIRMS.map((f) => (
                        <tr key={f.name} style={{ borderBottom: "1px solid var(--border)" }}>
                          <td className="py-2 pr-3">
                            <a href={f.url} target="_blank" rel="noreferrer" className="font-medium hover:text-violet-400">
                              {f.name}
                            </a>
                            {f.notes && (
                              <div className="text-[10px] mt-0.5" style={{ color: "var(--muted)" }}>{f.notes}</div>
                            )}
                          </td>
                          <td className="py-2 pr-3" style={{ color: "var(--muted)" }}>{f.platform}</td>
                          <td className="py-2 pr-3">{f.minChallenge}</td>
                          <td className="py-2 pr-3">{f.profitSplit}</td>
                          <td className="py-2 pr-3"><StatusPill status={f.status} /></td>
                          <td className="py-2 pr-3 whitespace-nowrap" style={{ color: "var(--muted)" }}>
                            <Clock size={10} className="inline mr-1" />{f.lastVerified}
                          </td>
                          <td className="py-2 pr-3">
                            <button onClick={() => reportPropFirm(f)}
                              className="text-[10px] text-blue-400 hover:underline">
                              Report update
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Glass>
            </div>
          ),
        },
        {
          label: "FAQ",
          content: (
            <div className="space-y-2">
              {FAQ.map((item, i) => (
                <Glass key={i} padding="md">
                  <h3 className="text-sm font-medium mb-1">{item.q}</h3>
                  <p className="text-xs leading-relaxed" style={{ color: "var(--muted)" }}>{item.a}</p>
                </Glass>
              ))}
            </div>
          ),
        },
        {
          label: "Contact & Feedback",
          content: (
            <div className="space-y-4">
              <Glass padding="md" id="feedback-form">
                <h3 className="text-sm font-medium mb-3 flex items-center gap-2">
                  <MessageSquare size={14} /> Submit Feedback
                </h3>
                {prefilledFirm && (
                  <div className="text-[11px] mb-2 px-2 py-1 rounded"
                    style={{ background: "var(--background)", color: "var(--muted)" }}>
                    Reporting an update for <span style={{ color: "var(--foreground)" }}>{prefilledFirm}</span>.{" "}
                    <button onClick={() => setPrefilledFirm(null)} className="text-blue-400 hover:underline">clear</button>
                  </div>
                )}
                <div className="space-y-3">
                  <div>
                    <label className="block text-xs mb-1" style={{ color: "var(--muted)" }}>Type</label>
                    <select value={feedbackType} onChange={(e) => setFeedbackType(e.target.value)}
                      className="w-full px-3 py-2 text-sm rounded-lg border bg-transparent"
                      style={{ borderColor: "var(--border)", background: "var(--card)" }}>
                      <option value="bug">Bug report</option>
                      <option value="feature">Feature request</option>
                      <option value="provider_request">Broker / prop-firm update</option>
                      <option value="other">Other</option>
                    </select>
                  </div>
                  <div>
                    <label className="block text-xs mb-1" style={{ color: "var(--muted)" }}>Message</label>
                    <textarea value={feedbackMsg} onChange={(e) => setFeedbackMsg(e.target.value)} rows={4}
                      className="w-full px-3 py-2 text-sm rounded-lg border bg-transparent outline-none focus:border-blue-500 resize-none"
                      style={{ borderColor: "var(--border)" }}
                      placeholder="Describe the issue or the update…" />
                  </div>
                  <button
                    disabled={!feedbackMsg.trim() || sending}
                    onClick={submitFeedback}
                    className="px-4 py-2 text-sm font-medium rounded-lg bg-blue-600 hover:bg-blue-500 text-white disabled:opacity-50 flex items-center gap-2">
                    {sending && <Loader2 size={14} className="animate-spin" />}
                    Submit Feedback
                  </button>
                </div>
              </Glass>

              <Glass padding="md">
                <h3 className="text-sm font-medium mb-2">Reach the team</h3>
                <ul className="text-xs space-y-1.5" style={{ color: "var(--muted)" }}>
                  <li>Admin: <span style={{ color: "var(--foreground)" }}>support@flowrexalgo.com</span></li>
                  <li>Status page: <a href="https://flowrexalgo.com/status" className="text-violet-400 hover:underline">flowrexalgo.com/status</a></li>
                  <li>GitHub: <a href="https://github.com/anthropics/claude-code/issues" className="text-violet-400 hover:underline">report a bug</a></li>
                </ul>
              </Glass>
            </div>
          ),
        },
      ]} />
    </div>
  );
}

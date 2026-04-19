"use client";

import { useState } from "react";
import api from "@/lib/api";
import { toast } from "sonner";
import { getErrorMessage } from "@/lib/errors";
import Glass from "@/components/ui/Glass";
import Tabs from "@/components/ui/Tabs";
import {
  LifeBuoy, BookOpen, Landmark, Server, MessageSquare, ExternalLink,
  Clock, Loader2,
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

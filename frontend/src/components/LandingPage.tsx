"use client";

import { useState, useEffect, useRef } from "react";
import Link from "next/link";
import { Bot, LineChart, Shield, Zap, ChevronRight, BarChart3, Globe, Brain, ArrowRight } from "lucide-react";
import RequestAccessModal from "./RequestAccessModal";

function useInView(ref: React.RefObject<HTMLElement | null>) {
  const [inView, setInView] = useState(false);
  useEffect(() => {
    if (!ref.current) return;
    const obs = new IntersectionObserver(([e]) => { if (e.isIntersecting) setInView(true); }, { threshold: 0.15 });
    obs.observe(ref.current);
    return () => obs.disconnect();
  }, [ref]);
  return inView;
}

function FadeIn({ children, delay = 0, className = "" }: { children: React.ReactNode; delay?: number; className?: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const inView = useInView(ref);
  return (
    <div
      ref={ref}
      className={className}
      style={{
        opacity: inView ? 1 : 0,
        transform: inView ? "translateY(0)" : "translateY(24px)",
        transition: `opacity 0.6s ease ${delay}s, transform 0.6s ease ${delay}s`,
      }}
    >
      {children}
    </div>
  );
}

const FEATURES = [
  {
    icon: Brain,
    title: "ML-Powered Agents",
    desc: "Institutional-grade models trained on 500k+ bars with walk-forward validation. Grade A performance.",
    color: "#8b5cf6",
  },
  {
    icon: Globe,
    title: "Multi-Broker",
    desc: "Connect Oanda, cTrader, or MT5. Run agents on multiple brokers simultaneously.",
    color: "#3b82f6",
  },
  {
    icon: BarChart3,
    title: "Real-Time Monitoring",
    desc: "Live equity curves, trade logs, and performance metrics. WebSocket-powered dashboard.",
    color: "#22c55e",
  },
  {
    icon: Shield,
    title: "Risk Management",
    desc: "ATR-based position sizing, daily loss limits, and drawdown protection built into every agent.",
    color: "#f59e0b",
  },
];

const STEPS = [
  { num: "01", title: "Connect", desc: "Link your broker and market data provider in settings." },
  { num: "02", title: "Deploy", desc: "Choose an agent strategy, select your symbol, and go live." },
  { num: "03", title: "Monitor", desc: "Watch your agents trade in real-time from the dashboard." },
];

export default function LandingPage() {
  const [showRequestModal, setShowRequestModal] = useState(false);

  return (
    <div className="min-h-screen" style={{ background: "var(--background)" }}>
      {/* Nav */}
      <nav className="fixed top-0 left-0 right-0 z-50 backdrop-blur-xl border-b" style={{ background: "rgba(10,11,15,0.85)", borderColor: "var(--border)" }}>
        <div className="max-w-6xl mx-auto px-6 h-16 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <img src="/logo-icon.png" alt="FlowrexAlgo" className="w-8 h-8 rounded-lg object-contain" />
            <span className="text-lg font-semibold">FlowrexAlgo</span>
          </div>
          <div className="flex items-center gap-3">
            <Link href="/login" className="px-4 py-2 text-sm font-medium rounded-lg hover:bg-white/5 transition-colors" style={{ color: "var(--muted)" }}>
              Log in
            </Link>
            <Link href="/register" className="px-4 py-2 text-sm font-medium rounded-lg text-white transition-all hover:scale-105" style={{ background: "linear-gradient(135deg, #8b5cf6, #3b82f6)" }}>
              Get Started
            </Link>
          </div>
        </div>
      </nav>

      {/* Hero */}
      <section className="relative pt-32 pb-20 px-6 overflow-hidden">
        {/* Glow effects */}
        <div className="absolute top-20 left-1/2 -translate-x-1/2 w-[600px] h-[400px] rounded-full opacity-20 blur-[120px]" style={{ background: "linear-gradient(135deg, #8b5cf6, #3b82f6)" }} />
        <div className="absolute top-40 right-1/4 w-[300px] h-[300px] rounded-full opacity-10 blur-[100px]" style={{ background: "#22c55e" }} />

        <div className="max-w-4xl mx-auto text-center relative z-10">
          <FadeIn>
            <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full text-xs font-medium mb-6 border" style={{ borderColor: "rgba(139,92,246,0.3)", background: "rgba(139,92,246,0.1)", color: "#a78bfa" }}>
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
              Autonomous Trading Platform
            </div>
          </FadeIn>

          <FadeIn delay={0.1}>
            <h1 className="text-4xl sm:text-5xl md:text-6xl font-bold leading-tight mb-6">
              Trade Smarter with{" "}
              <span className="bg-clip-text text-transparent" style={{ backgroundImage: "linear-gradient(135deg, #8b5cf6, #3b82f6, #22c55e)" }}>
                ML-Powered
              </span>{" "}
              Agents
            </h1>
          </FadeIn>

          <FadeIn delay={0.2}>
            <p className="text-lg md:text-xl max-w-2xl mx-auto mb-8" style={{ color: "var(--muted)" }}>
              Deploy institutional-grade trading algorithms on US30, BTCUSD, and XAUUSD.
              Walk-forward validated. Real-time monitoring. Multi-broker execution.
            </p>
          </FadeIn>

          <FadeIn delay={0.3}>
            <div className="flex flex-col sm:flex-row items-center justify-center gap-3">
              <Link
                href="/register"
                className="group px-6 py-3 text-sm font-semibold rounded-xl text-white flex items-center gap-2 transition-all hover:scale-105 hover:shadow-lg"
                style={{
                  background: "linear-gradient(135deg, #8b5cf6, #3b82f6)",
                  boxShadow: "0 0 30px rgba(139,92,246,0.3)",
                }}
              >
                Get Started
                <ArrowRight size={16} className="group-hover:translate-x-1 transition-transform" />
              </Link>
              <button
                onClick={() => setShowRequestModal(true)}
                className="px-6 py-3 text-sm font-medium rounded-xl border transition-all hover:bg-white/5"
                style={{ borderColor: "var(--border)", color: "var(--foreground)" }}
              >
                Request Access
              </button>
            </div>
          </FadeIn>

          {/* Stats bar */}
          <FadeIn delay={0.4}>
            <div className="flex items-center justify-center gap-8 mt-12 flex-wrap">
              {[
                { label: "Win Rate", value: "62.2%" },
                { label: "Sharpe Ratio", value: "4.96" },
                { label: "Max Drawdown", value: "0.8%" },
                { label: "Grade", value: "A" },
              ].map((s) => (
                <div key={s.label} className="text-center">
                  <div className="text-xl font-bold" style={{ color: "#a78bfa" }}>{s.value}</div>
                  <div className="text-xs" style={{ color: "var(--muted)" }}>{s.label}</div>
                </div>
              ))}
            </div>
          </FadeIn>
        </div>
      </section>

      {/* Features */}
      <section className="py-20 px-6">
        <div className="max-w-5xl mx-auto">
          <FadeIn>
            <h2 className="text-2xl md:text-3xl font-bold text-center mb-12">
              Built for Serious Traders
            </h2>
          </FadeIn>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-5">
            {FEATURES.map((f, i) => (
              <FadeIn key={f.title} delay={i * 0.1}>
                <div
                  className="p-6 rounded-xl border transition-all hover:scale-[1.02] hover:border-opacity-50 group cursor-default"
                  style={{
                    background: "var(--card)",
                    borderColor: "var(--border)",
                  }}
                >
                  <div
                    className="w-10 h-10 rounded-lg flex items-center justify-center mb-4"
                    style={{ background: `${f.color}15` }}
                  >
                    <f.icon size={20} style={{ color: f.color }} />
                  </div>
                  <h3 className="text-base font-semibold mb-2">{f.title}</h3>
                  <p className="text-sm leading-relaxed" style={{ color: "var(--muted)" }}>{f.desc}</p>
                </div>
              </FadeIn>
            ))}
          </div>
        </div>
      </section>

      {/* How it works */}
      <section className="py-20 px-6">
        <div className="max-w-4xl mx-auto">
          <FadeIn>
            <h2 className="text-2xl md:text-3xl font-bold text-center mb-12">
              How It Works
            </h2>
          </FadeIn>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
            {STEPS.map((s, i) => (
              <FadeIn key={s.num} delay={i * 0.15}>
                <div className="text-center">
                  <div
                    className="w-12 h-12 rounded-full flex items-center justify-center mx-auto mb-4 text-sm font-bold"
                    style={{ background: "linear-gradient(135deg, #8b5cf6, #3b82f6)", color: "white" }}
                  >
                    {s.num}
                  </div>
                  <h3 className="text-base font-semibold mb-2">{s.title}</h3>
                  <p className="text-sm" style={{ color: "var(--muted)" }}>{s.desc}</p>
                </div>
              </FadeIn>
            ))}
          </div>
        </div>
      </section>

      {/* CTA */}
      <section className="py-20 px-6">
        <FadeIn>
          <div
            className="max-w-3xl mx-auto rounded-2xl p-8 md:p-12 text-center border relative overflow-hidden"
            style={{ background: "var(--card)", borderColor: "var(--border)" }}
          >
            <div className="absolute inset-0 opacity-10" style={{ background: "linear-gradient(135deg, #8b5cf6, #3b82f6)" }} />
            <div className="relative z-10">
              <h2 className="text-2xl md:text-3xl font-bold mb-4">Ready to Start?</h2>
              <p className="text-sm mb-6" style={{ color: "var(--muted)" }}>
                Join the platform. Deploy your first agent in minutes.
              </p>
              <div className="flex flex-col sm:flex-row items-center justify-center gap-3">
                <Link
                  href="/register"
                  className="px-6 py-3 text-sm font-semibold rounded-xl text-white transition-all hover:scale-105"
                  style={{ background: "linear-gradient(135deg, #8b5cf6, #3b82f6)" }}
                >
                  Get Started Free
                </Link>
                <button
                  onClick={() => setShowRequestModal(true)}
                  className="px-6 py-3 text-sm font-medium rounded-xl border transition-all hover:bg-white/5"
                  style={{ borderColor: "var(--border)" }}
                >
                  Request Access
                </button>
              </div>
            </div>
          </div>
        </FadeIn>
      </section>

      {/* Footer */}
      <footer className="py-8 px-6 border-t" style={{ borderColor: "var(--border)" }}>
        <div className="max-w-5xl mx-auto flex flex-col sm:flex-row items-center justify-between gap-4">
          <div className="flex items-center gap-2">
            <img src="/logo-icon.png" alt="FlowrexAlgo" className="w-6 h-6 object-contain" />
            <span className="text-sm font-medium">FlowrexAlgo</span>
          </div>
          <p className="text-xs" style={{ color: "var(--muted)" }}>
            &copy; {new Date().getFullYear()} FlowrexAlgo. All rights reserved.
          </p>
        </div>
      </footer>

      <RequestAccessModal open={showRequestModal} onClose={() => setShowRequestModal(false)} />
    </div>
  );
}

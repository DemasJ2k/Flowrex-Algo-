export default function PrivacyPage() {
  return (
    <div className="max-w-3xl mx-auto py-12 px-4 space-y-8">
      <div>
        <h1 className="text-3xl font-bold bg-gradient-to-r from-violet-400 to-blue-400 bg-clip-text text-transparent">
          Privacy Policy
        </h1>
        <p className="text-sm mt-2" style={{ color: "var(--muted)" }}>Last updated: April 2026</p>
      </div>

      <section className="space-y-3">
        <h2 className="text-lg font-semibold">1. Information We Collect</h2>
        <p className="text-sm leading-relaxed" style={{ color: "var(--muted)" }}>
          We collect information you provide directly to us, including your email address, password (stored as a secure hash), and broker API credentials (stored encrypted). We also collect usage data such as trade logs, agent configurations, and chat messages with the AI Supervisor.
        </p>
      </section>

      <section className="space-y-3">
        <h2 className="text-lg font-semibold">2. How We Use Your Information</h2>
        <p className="text-sm leading-relaxed" style={{ color: "var(--muted)" }}>
          We use the information we collect to provide, maintain, and improve the Service, to process trades on your behalf via connected brokers, to send you notifications (including via Telegram if configured), and to respond to your inquiries.
        </p>
      </section>

      <section className="space-y-3">
        <h2 className="text-lg font-semibold">3. Data Security</h2>
        <p className="text-sm leading-relaxed" style={{ color: "var(--muted)" }}>
          We implement industry-standard security measures including encrypted storage for sensitive credentials, bcrypt password hashing, HTTPS-only communication, and rate limiting. However, no method of transmission over the Internet is 100% secure.
        </p>
      </section>

      <section className="space-y-3">
        <h2 className="text-lg font-semibold">4. Data Retention</h2>
        <p className="text-sm leading-relaxed" style={{ color: "var(--muted)" }}>
          We retain your account data for as long as your account is active. Trade history and logs are retained for up to 2 years. You may request deletion of your account and associated data at any time.
        </p>
      </section>

      <section className="space-y-3">
        <h2 className="text-lg font-semibold">5. Your Rights (GDPR)</h2>
        <p className="text-sm leading-relaxed" style={{ color: "var(--muted)" }}>
          If you are located in the European Economic Area, you have the right to access, correct, or delete your personal data, the right to data portability, and the right to object to processing. To exercise these rights, contact us or use the account deletion feature in Settings.
        </p>
      </section>

      <section className="space-y-3">
        <h2 className="text-lg font-semibold">6. Third-Party Services</h2>
        <p className="text-sm leading-relaxed" style={{ color: "var(--muted)" }}>
          The Service integrates with third-party broker APIs (Oanda, cTrader, Tradovate, MT5) and the Anthropic API for AI features. Data shared with these providers is governed by their respective privacy policies. We do not sell your personal data to third parties.
        </p>
      </section>

      <section className="space-y-3">
        <h2 className="text-lg font-semibold">7. Cookies</h2>
        <p className="text-sm leading-relaxed" style={{ color: "var(--muted)" }}>
          We use authentication tokens stored in your browser's local storage to keep you logged in. We do not use tracking or advertising cookies.
        </p>
      </section>

      <section className="space-y-3">
        <h2 className="text-lg font-semibold">8. Changes to This Policy</h2>
        <p className="text-sm leading-relaxed" style={{ color: "var(--muted)" }}>
          We may update this Privacy Policy from time to time. We will notify you of significant changes by posting the new policy on this page with an updated effective date.
        </p>
      </section>

      <section className="space-y-3">
        <h2 className="text-lg font-semibold">9. Contact</h2>
        <p className="text-sm leading-relaxed" style={{ color: "var(--muted)" }}>
          For privacy-related questions or data requests, contact us at{" "}
          <a href="mailto:Flowrexflex@gmail.com" className="text-violet-400 hover:underline">
            Flowrexflex@gmail.com
          </a>.
        </p>
      </section>
    </div>
  );
}

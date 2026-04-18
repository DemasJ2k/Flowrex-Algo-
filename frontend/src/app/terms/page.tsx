export default function TermsPage() {
  return (
    <div className="max-w-3xl mx-auto py-12 px-4 space-y-8">
      <div>
        <h1 className="text-3xl font-bold bg-gradient-to-r from-violet-400 to-blue-400 bg-clip-text text-transparent">
          Terms of Service
        </h1>
        <p className="text-sm mt-2" style={{ color: "var(--muted)" }}>Last updated: April 2026</p>
      </div>

      <section className="space-y-3">
        <h2 className="text-lg font-semibold">1. Acceptance of Terms</h2>
        <p className="text-sm leading-relaxed" style={{ color: "var(--muted)" }}>
          By accessing or using Flowrex Algo ("the Service"), you agree to be bound by these Terms of Service. If you do not agree, you may not use the Service.
        </p>
      </section>

      <section className="space-y-3">
        <h2 className="text-lg font-semibold">2. Description of Service</h2>
        <p className="text-sm leading-relaxed" style={{ color: "var(--muted)" }}>
          Flowrex Algo is an algorithmic trading platform that provides automated trading agents, machine learning models, and market analysis tools. The Service is intended for informational and educational purposes only and does not constitute financial advice.
        </p>
      </section>

      <section className="space-y-3">
        <h2 className="text-lg font-semibold">3. Risk Disclaimer</h2>
        <p className="text-sm leading-relaxed" style={{ color: "var(--muted)" }}>
          Trading financial instruments involves substantial risk of loss and is not suitable for every investor. Past performance of any trading system or methodology is not necessarily indicative of future results. You acknowledge that you can lose some or all of your investment and agree that Flowrex Algo bears no responsibility for any trading losses.
        </p>
      </section>

      <section className="space-y-3">
        <h2 className="text-lg font-semibold">4. User Responsibilities</h2>
        <p className="text-sm leading-relaxed" style={{ color: "var(--muted)" }}>
          You are solely responsible for all activity under your account and for ensuring that your use of the Service complies with all applicable laws and regulations. You must keep your credentials secure and notify us immediately of any unauthorized use.
        </p>
      </section>

      <section className="space-y-3">
        <h2 className="text-lg font-semibold">5. Intellectual Property</h2>
        <p className="text-sm leading-relaxed" style={{ color: "var(--muted)" }}>
          All content, features, and functionality of the Service are and will remain the exclusive property of Flowrex Algo. You may not reproduce, distribute, or create derivative works without express written permission.
        </p>
      </section>

      <section className="space-y-3">
        <h2 className="text-lg font-semibold">6. Limitation of Liability</h2>
        <p className="text-sm leading-relaxed" style={{ color: "var(--muted)" }}>
          To the maximum extent permitted by law, Flowrex Algo shall not be liable for any indirect, incidental, special, consequential, or punitive damages, including but not limited to loss of profits, data, or goodwill, arising out of or in connection with your use of the Service.
        </p>
      </section>

      <section className="space-y-3">
        <h2 className="text-lg font-semibold">7. Modifications</h2>
        <p className="text-sm leading-relaxed" style={{ color: "var(--muted)" }}>
          We reserve the right to modify these Terms at any time. Continued use of the Service after changes constitutes acceptance of the updated Terms.
        </p>
      </section>

      <section className="space-y-3">
        <h2 className="text-lg font-semibold">8. Contact</h2>
        <p className="text-sm leading-relaxed" style={{ color: "var(--muted)" }}>
          For questions about these Terms, please contact us at{" "}
          <a href="mailto:Flowrexflex@gmail.com" className="text-violet-400 hover:underline">
            Flowrexflex@gmail.com
          </a>.
        </p>
      </section>
    </div>
  );
}

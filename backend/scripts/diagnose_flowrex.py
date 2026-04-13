"""
Diagnostic script for Flowrex v2 models.

Reads all flowrex_*.joblib files and outputs:
  - Per-symbol walk-forward fold metrics
  - Consistency check (fold-over-fold degradation)
  - Trade count per fold
  - Confidence distribution (if SHAP succeeded)
  - Top 20 feature importance per symbol
  - Comparison with Potential Agent v2 (if available)
  - Recommended action per symbol

Usage:
    cd backend
    python -m scripts.diagnose_flowrex
"""
import os
import sys
import glob
import joblib
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

MODEL_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "ml_models")
SYMBOLS = ["US30", "BTCUSD", "XAUUSD", "ES", "NAS100"]
MODEL_TYPES = ["xgboost", "lightgbm", "catboost"]


def _fmt_grade(g):
    colors = {"A": "\033[92m", "B": "\033[96m", "C": "\033[93m", "D": "\033[93m", "F": "\033[91m"}
    reset = "\033[0m"
    return f"{colors.get(g, '')}{g}{reset}"


def _load_model(symbol, mtype):
    path = os.path.join(MODEL_DIR, f"flowrex_{symbol}_M5_{mtype}.joblib")
    if not os.path.exists(path):
        return None
    try:
        return joblib.load(path)
    except Exception as e:
        print(f"  [ERROR] Failed to load {path}: {e}")
        return None


def _analyze_symbol(symbol):
    print(f"\n{'=' * 85}")
    print(f"  {symbol}")
    print(f"{'=' * 85}")

    models = {}
    for mt in MODEL_TYPES:
        data = _load_model(symbol, mt)
        if data:
            models[mt] = data

    if not models:
        print("  No Flowrex v2 models found.")
        return

    first_model = next(iter(models.values()))
    print(f"  Pipeline: {first_model.get('pipeline_version', '?')}")
    print(f"  Trained:  {first_model.get('trained_at', '?')[:19]}")
    print(f"  Features: {len(first_model.get('feature_names', []))}")
    print(f"  OOS start: {first_model.get('oos_start', '?')}")

    # Walk-forward fold metrics
    print(f"\n  Walk-Forward Metrics (per fold):")
    print(f"  {'-' * 83}")
    print(f"  {'Fold':<6} {'Model':<12} {'Grade':<6} {'Sharpe':<9} {'WR':<9} {'DD':<9} {'Return':<10} {'Trades':<8}")
    print(f"  {'-' * 83}")

    all_wf = {}
    for mt, data in models.items():
        for r in data.get("wf_results", []):
            fold = r.get("fold", 0)
            if fold not in all_wf:
                all_wf[fold] = {}
            all_wf[fold][mt] = r

    for fold in sorted(all_wf.keys()):
        for mt in MODEL_TYPES:
            if mt in all_wf[fold]:
                r = all_wf[fold][mt]
                grade = _fmt_grade(r.get("grade", "?"))
                sharpe = r.get("sharpe", 0)
                wr = r.get("win_rate", 0)
                dd = r.get("max_drawdown", 0)
                ret = r.get("total_return", 0)
                trades = r.get("total_trades", 0)
                print(f"  {fold:<6} {mt:<12} {grade:<15} {sharpe:<8.2f} {wr:<7.1f}%  {dd:<7.1f}%  {ret:<8.1f}%  {trades:<8}")
        print(f"  {'-' * 83}")

    # Consistency check — sharpe degradation across folds
    print(f"\n  Sharpe Degradation (per model):")
    for mt, data in models.items():
        sharpes = [r.get("sharpe", 0) for r in sorted(data.get("wf_results", []), key=lambda x: x.get("fold", 0))]
        if len(sharpes) >= 2:
            trend = "↓" if sharpes[-1] < sharpes[0] else "↑"
            degradation = (sharpes[-1] - sharpes[0]) if sharpes[0] != 0 else 0
            sharpe_str = " → ".join(f"{s:.2f}" for s in sharpes)
            print(f"    {mt:<12} {sharpe_str}  {trend} ({degradation:+.2f})")

    # OOS metrics
    print(f"\n  True OOS (2026-01-01 → present):")
    print(f"  {'-' * 83}")
    for mt, data in models.items():
        m = data.get("oos_metrics", {})
        grade = _fmt_grade(data.get("grade", "?"))
        print(f"  {mt:<12} {grade:<15} "
              f"Sharpe={m.get('sharpe', 0):<7.2f} "
              f"WR={m.get('win_rate', 0):<6.1f}% "
              f"DD={m.get('max_drawdown', 0):<6.1f}% "
              f"Ret={m.get('total_return', 0):<7.1f}% "
              f"Trades={m.get('total_trades', 0):<6} "
              f"PF={m.get('profit_factor', 0):<5.2f}")

    # Feature importance (if available from tree_feature_importances)
    print(f"\n  Top 20 Feature Importances (XGBoost via gain):")
    xgb = models.get("xgboost")
    if xgb and hasattr(xgb.get("model"), "feature_importances_"):
        names = xgb.get("feature_names", [])
        imps = xgb["model"].feature_importances_
        if len(names) == len(imps):
            ranked = sorted(zip(names, imps), key=lambda x: -x[1])
            total = sum(imps) or 1
            for i, (name, imp) in enumerate(ranked[:20], 1):
                pct = imp / total * 100
                bar = "█" * int(pct * 1.5)
                print(f"    {i:2d}. {name:<35} {pct:5.2f}%  {bar}")

    # Feature group breakdown
    if xgb and hasattr(xgb.get("model"), "feature_importances_"):
        names = xgb.get("feature_names", [])
        imps = xgb["model"].feature_importances_
        groups = {
            "VWAP/VP":    lambda n: any(x in n for x in ["vwap", "poc", "vah", "val", "value_area"]),
            "ADX/EMA":    lambda n: any(x in n for x in ["adx", "_di", "ema_"]),
            "RSI/MACD":   lambda n: any(x in n for x in ["rsi", "macd"]),
            "ORB":        lambda n: "orb" in n,
            "ICT/SMC":    lambda n: n.startswith("fx_ict_"),
            "Williams":   lambda n: n.startswith("fx_lw_"),
            "Donchian":   lambda n: "donch" in n or "zscore" in n or "tsmom" in n or "hurst" in n or "dist_prev" in n,
            "HTF":        lambda n: any(x in n for x in ["h1_", "h4_", "d1_", "htf_"]),
            "Session":    lambda n: any(x in n for x in ["hour_", "dow_", "session", "overlap", "minutes_since", "pre_close"]),
            "Microstr":   lambda n: any(x in n for x in ["spread", "vol_ratio", "body_ratio", "wick", "absorption", "cvd", "delta_div", "relative_vol"]),
            "Volatility": lambda n: any(x in n for x in ["bb_", "atr_14_50"]),
        }
        total = sum(imps) or 1
        print(f"\n  Feature Group Breakdown:")
        print(f"  {'-' * 50}")
        assigned = set()
        for gname, matcher in groups.items():
            group_imp = 0
            count = 0
            for n, imp in zip(names, imps):
                if n not in assigned and matcher(n):
                    group_imp += imp
                    count += 1
                    assigned.add(n)
            pct = group_imp / total * 100
            bar = "█" * int(pct * 0.8)
            print(f"    {gname:<12} {pct:5.1f}%  ({count:2d} features)  {bar}")

    # Recommendation
    print(f"\n  Recommendation:")
    oos_grade = first_model.get("grade", "F")
    avg_sharpe = np.mean([d.get("oos_metrics", {}).get("sharpe", 0) for d in models.values()])
    total_trades = np.mean([d.get("oos_metrics", {}).get("total_trades", 0) for d in models.values()])
    wr = np.mean([d.get("oos_metrics", {}).get("win_rate", 0) for d in models.values()])

    if avg_sharpe > 2.0 and total_trades > 500 and wr > 55:
        rec = "✅ DEPLOY — large sample, solid edge, ready for paper trading"
    elif avg_sharpe > 2.0 and total_trades < 200:
        rec = "⚠️ VALIDATE — Sharpe is high but trade count too low (statistical noise)"
    elif avg_sharpe > 1.0 and total_trades > 300:
        rec = "🟡 WATCH — moderate edge, usable on paper with close monitoring"
    elif avg_sharpe < 0:
        rec = "❌ REJECT — losing money on OOS; needs feature/label investigation"
    else:
        rec = "🟠 INVESTIGATE — borderline metrics, needs diagnostic"

    print(f"    {rec}")
    print(f"    Avg Sharpe: {avg_sharpe:.2f}  |  Avg Trades: {total_trades:.0f}  |  Avg WR: {wr:.1f}%")


def _compare_with_potential():
    print(f"\n{'=' * 85}")
    print(f"  Comparison: Potential Agent v2 vs Flowrex v2 (OOS metrics)")
    print(f"{'=' * 85}")
    print(f"  {'Symbol':<10} {'Potential':<35} {'Flowrex v2':<35}")
    print(f"  {'-' * 80}")
    for sym in SYMBOLS:
        pot_file = None
        for mt in ["lightgbm", "xgboost"]:
            p = os.path.join(MODEL_DIR, f"potential_{sym}_M5_{mt}.joblib")
            if os.path.exists(p):
                pot_file = p
                break

        flow_file = None
        for mt in ["xgboost", "lightgbm", "catboost"]:
            p = os.path.join(MODEL_DIR, f"flowrex_{sym}_M5_{mt}.joblib")
            if os.path.exists(p):
                flow_file = p
                break

        pot_str = "—"
        flow_str = "—"

        if pot_file:
            try:
                d = joblib.load(pot_file)
                m = d.get("oos_metrics", {})
                g = d.get("grade", "?")
                pot_str = f"{g} • Sharpe {m.get('sharpe', 0):.2f} • {m.get('total_trades', 0)} trades"
            except Exception:
                pass

        if flow_file:
            try:
                d = joblib.load(flow_file)
                m = d.get("oos_metrics", {})
                g = d.get("grade", "?")
                flow_str = f"{g} • Sharpe {m.get('sharpe', 0):.2f} • {m.get('total_trades', 0)} trades"
            except Exception:
                pass

        print(f"  {sym:<10} {pot_str:<35} {flow_str:<35}")


def main():
    print("\n" + "=" * 85)
    print("  FLOWREX v2 DIAGNOSTIC REPORT")
    print("=" * 85)

    for sym in SYMBOLS:
        _analyze_symbol(sym)

    _compare_with_potential()

    print(f"\n{'=' * 85}")
    print("  End of report")
    print(f"{'=' * 85}\n")


if __name__ == "__main__":
    main()

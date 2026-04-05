"""
PPO RL Trade Manager Training Pipeline
=======================================
Trains a PPO agent to decide SKIP/SMALL/NORMAL/AGGRESSIVE for each trade signal.

Replays historical signal events from trained tree models.
The RL agent learns optimal trade sizing and filtering.

Usage:
  python -m scripts.train_rl_manager --symbol US30
  python -m scripts.train_rl_manager --symbol US30 --timesteps 200000
"""
import os
import sys
import warnings
import argparse
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
warnings.filterwarnings("ignore")

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import gymnasium as gym
from gymnasium import spaces

DATA_DIR  = os.path.join(os.path.dirname(__file__), "..", "data")
MODEL_DIR = os.path.join(DATA_DIR, "ml_models")

# Action configs matching rl_trade_manager.py
ACTION_CONFIGS = {
    0: {"name": "SKIP",       "lot_mult": 0.0, "sl_atr": 0.0, "tp_atr": 0.0},
    1: {"name": "SMALL",      "lot_mult": 0.5, "sl_atr": 0.8, "tp_atr": 1.5},
    2: {"name": "NORMAL",     "lot_mult": 1.0, "sl_atr": 1.2, "tp_atr": 2.5},
    3: {"name": "AGGRESSIVE", "lot_mult": 1.5, "sl_atr": 1.5, "tp_atr": 4.0},
}


class TradeReplayEnv(gym.Env):
    """
    Gymnasium environment that replays historical signal events.
    At each step, the RL agent decides how to size/filter a trade signal.
    """
    metadata = {"render_modes": []}

    def __init__(
        self,
        signal_events: list[dict],
        closes: np.ndarray,
        highs: np.ndarray,
        lows: np.ndarray,
        opens: np.ndarray,
        atr_values: np.ndarray,
        hold_bars: int = 10,
        cost_bps: float = 1.5,
    ):
        super().__init__()
        self.signal_events = signal_events
        self.closes = closes
        self.highs = highs
        self.lows = lows
        self.opens = opens
        self.atr_values = atr_values
        self.hold_bars = hold_bars
        self.cost_bps = cost_bps

        self.observation_space = spaces.Box(low=-5.0, high=5.0, shape=(20,), dtype=np.float32)
        self.action_space = spaces.Discrete(4)

        self._idx = 0
        self._recent_results = []

    def reset(self, seed=None, options=None):
        super().reset(seed=seed)
        self._idx = 0
        self._recent_results = []
        if len(self.signal_events) == 0:
            return np.zeros(20, dtype=np.float32), {}
        return self.signal_events[0]["obs"].astype(np.float32), {}

    def step(self, action: int):
        if self._idx >= len(self.signal_events):
            return np.zeros(20, dtype=np.float32), 0.0, True, False, {}

        event = self.signal_events[self._idx]
        bar_idx = event["bar_idx"]
        direction = event["direction"]  # +1 buy, -1 sell

        cfg = ACTION_CONFIGS[action]
        reward = 0.0

        if action == 0:
            # SKIP — tiny penalty to prevent never-trade policy
            reward = -0.001
        elif bar_idx + 1 < len(self.closes) and bar_idx + self.hold_bars < len(self.closes):
            # Simulate the trade
            entry = self.opens[bar_idx + 1] if bar_idx + 1 < len(self.opens) else self.closes[bar_idx]
            atr = max(self.atr_values[bar_idx], 1e-10)
            tp_dist = atr * cfg["tp_atr"]
            sl_dist = atr * cfg["sl_atr"]

            is_long = direction == 1
            tp_price = entry + tp_dist if is_long else entry - tp_dist
            sl_price = entry - sl_dist if is_long else entry + sl_dist

            # Simulate forward bars
            exit_price = self.closes[min(bar_idx + self.hold_bars, len(self.closes) - 1)]
            exit_reason = "timeout"
            bars_held = self.hold_bars
            max_adverse = 0.0

            for j in range(bar_idx + 1, min(bar_idx + self.hold_bars + 1, len(self.closes))):
                hi = self.highs[j]
                lo = self.lows[j]

                # Track max adverse excursion
                if is_long:
                    adverse = (entry - lo) / entry
                else:
                    adverse = (hi - entry) / entry
                max_adverse = max(max_adverse, adverse)

                # Check SL (pessimistic: SL before TP if both)
                sl_hit = (lo <= sl_price) if is_long else (hi >= sl_price)
                tp_hit = (hi >= tp_price) if is_long else (lo <= tp_price)

                if sl_hit and tp_hit:
                    exit_price = sl_price  # pessimistic
                    exit_reason = "sl"
                    bars_held = j - bar_idx
                    break
                elif sl_hit:
                    exit_price = sl_price
                    exit_reason = "sl"
                    bars_held = j - bar_idx
                    break
                elif tp_hit:
                    exit_price = tp_price
                    exit_reason = "tp"
                    bars_held = j - bar_idx
                    break

            # Calculate PnL
            if is_long:
                pnl_pct = (exit_price - entry) / entry
            else:
                pnl_pct = (entry - exit_price) / entry

            cost = self.cost_bps / 10000.0
            pnl_pct -= cost

            # Scale by lot multiplier
            pnl_pct *= cfg["lot_mult"]

            # Reward: risk-adjusted PnL
            reward = pnl_pct
            reward -= 0.01 * max_adverse      # penalty for drawdown during trade
            reward -= 0.0001 * bars_held       # small time penalty
            if pnl_pct > 0 and abs(pnl_pct) > 2 * max_adverse:
                reward += 0.02                  # bonus for good risk/reward

            self._recent_results.append(pnl_pct)

        # Advance to next signal
        self._idx += 1
        terminated = self._idx >= len(self.signal_events)

        if terminated:
            obs = np.zeros(20, dtype=np.float32)
        else:
            obs = self.signal_events[self._idx]["obs"].astype(np.float32)

        return obs, float(reward), terminated, False, {"action": action, "reward": reward}


def generate_signal_events(
    symbol: str,
    X_all: np.ndarray,
    y_all: np.ndarray,
    feature_names: list[str],
    closes: np.ndarray,
    timestamps: np.ndarray,
) -> list[dict]:
    """
    Generate signal events by running tree models on historical data.
    Returns list of dicts with bar_idx, direction, obs (20-dim).
    """
    import joblib
    from app.services.ml.rl_trade_manager import build_rl_observation

    events = []
    hours = ((timestamps.astype(np.int64) % 86400) // 3600).astype(np.int32)

    # Load tree models for signal generation
    for mtype in ["xgboost", "lightgbm"]:
        path = os.path.join(MODEL_DIR, f"scalping_{symbol}_M5_{mtype}.joblib")
        if not os.path.exists(path):
            continue
        data = joblib.load(path)
        model = data.get("model")
        if model is None:
            continue

        try:
            preds = model.predict(X_all)
        except Exception:
            continue

        for i in range(len(preds)):
            if preds[i] in (0, 2):  # sell or buy
                direction = 1 if preds[i] == 2 else -1
                proba = model.predict_proba(X_all[i:i+1])[0]
                confidence = float(proba[preds[i]])

                if confidence < 0.50:
                    continue

                obs = build_rl_observation(
                    signal_direction=direction,
                    signal_confidence=confidence,
                    feature_vector=X_all[i],
                    feature_names=feature_names,
                    regime_id=0,
                    regime_confidence=0.5,
                    hour_utc=int(hours[i]),
                    recent_trade_results=[],
                )
                events.append({
                    "bar_idx": i,
                    "direction": direction,
                    "confidence": confidence,
                    "model": mtype,
                    "obs": obs,
                })

    # Sort by bar index, deduplicate (keep highest confidence per bar)
    events.sort(key=lambda e: (e["bar_idx"], -e["confidence"]))
    seen_bars = set()
    unique_events = []
    for e in events:
        if e["bar_idx"] not in seen_bars:
            seen_bars.add(e["bar_idx"])
            unique_events.append(e)

    return unique_events


def train_rl_manager(
    symbol: str,
    total_timesteps: int = 500_000,
    learning_rate: float = 3e-4,
):
    """Train PPO agent on historical signal replay."""
    from stable_baselines3 import PPO
    from stable_baselines3.common.vec_env import DummyVecEnv
    from app.services.ml.features_mtf import compute_expert_features
    from app.services.ml.symbol_config import get_symbol_config
    from scripts.model_utils import create_labels
    from scripts.train_walkforward import load_ohlcv, load_peer_m5

    cfg = get_symbol_config(symbol)
    cost_bps = cfg.get("cost_bps", 5.0) + cfg.get("slippage_bps", 1.0)
    hold_bars = cfg.get("hold_bars", cfg.get("label_forward_bars", 10))

    print(f"\n{'='*65}")
    print(f"  RL TRADE MANAGER TRAINING: {symbol}")
    print(f"  Timesteps: {total_timesteps:,} | Cost: {cost_bps:.1f}bps")
    print(f"{'='*65}")

    # Load data + features
    print("  Loading data...", flush=True)
    m5, m15, h1, h4, d1 = load_ohlcv(symbol)
    peer_m5 = load_peer_m5(symbol)

    print("  Computing features...", flush=True)
    feature_names, X_all = compute_expert_features(
        m5, h1, h4, d1, symbol=symbol, include_external=True,
        other_m5=peer_m5 if peer_m5 else None, m15_bars=m15,
    )
    print(f"  Features: {len(feature_names)}")

    closes = m5["close"].values
    opens = m5["open"].values
    highs = m5["high"].values
    lows = m5["low"].values
    timestamps = m5["time"].values.astype(np.int64)
    atr_idx = feature_names.index("atr_14") if "atr_14" in feature_names else None
    atr_vals = X_all[:, atr_idx] if atr_idx is not None else np.ones(len(closes))
    y_all = create_labels(closes, atr_vals, config=cfg)

    # Generate signal events from tree models
    print("  Generating signal events from tree models...", flush=True)
    events = generate_signal_events(symbol, X_all, y_all, feature_names, closes, timestamps)
    print(f"  Signal events: {len(events):,}")

    if len(events) < 100:
        print("  ERROR: Too few signal events for RL training")
        return

    # Create environment
    env = TradeReplayEnv(
        events, closes, highs, lows, opens, atr_vals,
        hold_bars=hold_bars, cost_bps=cost_bps,
    )

    # Wrap for SB3
    def make_env():
        return TradeReplayEnv(
            events, closes, highs, lows, opens, atr_vals,
            hold_bars=hold_bars, cost_bps=cost_bps,
        )

    vec_env = DummyVecEnv([make_env])

    # Train PPO
    print(f"  Training PPO ({total_timesteps:,} timesteps)...", flush=True)
    model = PPO(
        "MlpPolicy",
        vec_env,
        learning_rate=learning_rate,
        n_steps=min(2048, len(events)),
        batch_size=64,
        n_epochs=10,
        gamma=0.99,
        clip_range=0.2,
        verbose=1,
    )
    model.learn(total_timesteps=total_timesteps)

    # Save
    save_path = os.path.join(MODEL_DIR, f"rl_trade_manager_{symbol}")
    model.save(save_path)
    print(f"  Saved: rl_trade_manager_{symbol}.zip")

    # Quick evaluation
    obs, _ = env.reset()
    total_reward = 0
    action_counts = {0: 0, 1: 0, 2: 0, 3: 0}
    done = False
    while not done:
        action, _ = model.predict(obs, deterministic=True)
        obs, reward, done, _, info = env.step(int(action))
        total_reward += reward
        action_counts[int(action)] += 1

    print(f"\n  Evaluation:")
    print(f"    Total reward: {total_reward:.4f}")
    print(f"    Actions: SKIP={action_counts[0]}, SMALL={action_counts[1]}, "
          f"NORMAL={action_counts[2]}, AGGRESSIVE={action_counts[3]}")

    skip_pct = action_counts[0] / max(sum(action_counts.values()), 1) * 100
    print(f"    Skip rate: {skip_pct:.1f}%")

    print(f"\n{'='*65}")
    print(f"  RL training complete for {symbol}")
    print(f"{'='*65}\n")


# ── CLI ──────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Train PPO RL trade manager")
    parser.add_argument("--symbol", type=str, required=True, help="Symbol (e.g. US30)")
    parser.add_argument("--timesteps", type=int, default=500_000, help="Total training timesteps")
    parser.add_argument("--lr", type=float, default=3e-4, help="Learning rate")
    args = parser.parse_args()

    train_rl_manager(args.symbol.upper(), args.timesteps, args.lr)


if __name__ == "__main__":
    main()

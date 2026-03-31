"""
Quick live connection test for brokers.
Run: python scripts/test_live_connection.py
"""
import os
import sys
import asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv

load_dotenv()


async def test_oanda():
    from app.services.broker.oanda import OandaAdapter

    api_key = os.getenv("OANDA_API_KEY", "")
    account_id = os.getenv("OANDA_ACCOUNT_ID", "")
    practice = os.getenv("OANDA_PRACTICE", "true").lower() == "true"

    if not api_key or api_key.startswith("your-"):
        print("[OANDA] Skipped — no credentials in .env")
        return

    adapter = OandaAdapter()
    print("[OANDA] Connecting...")

    try:
        await adapter.connect({"api_key": api_key, "account_id": account_id, "practice": practice})
        print("[OANDA] Connected!")

        info = await adapter.get_account_info()
        print(f"  Balance:  {info.balance} {info.currency}")
        print(f"  Equity:   {info.equity}")
        print(f"  P&L:      {info.unrealized_pnl}")

        positions = await adapter.get_positions()
        print(f"  Open positions: {len(positions)}")

        candles = await adapter.get_candles("XAUUSD", "M5", 5)
        print(f"  Last 5 XAUUSD M5 candles:")
        for c in candles:
            print(f"    {c.time} | O:{c.open} H:{c.high} L:{c.low} C:{c.close} V:{c.volume}")

        tick = await adapter.get_tick("XAUUSD")
        print(f"  XAUUSD tick: bid={tick.bid} ask={tick.ask}")

        symbols = await adapter.get_symbols()
        print(f"  Available symbols: {len(symbols)}")

        await adapter.disconnect()
        print("[OANDA] Disconnected. All good!")

    except Exception as e:
        print(f"[OANDA] Error: {e}")
        await adapter.disconnect()


async def test_mt5():
    from app.services.broker.mt5 import MT5Adapter, MT5_AVAILABLE

    if not MT5_AVAILABLE:
        print("[MT5] Skipped — MetaTrader5 package not installed")
        return

    login = os.getenv("MT5_LOGIN", "")
    password = os.getenv("MT5_PASSWORD", "")
    server = os.getenv("MT5_SERVER", "").strip()
    path = os.getenv("MT5_PATH", "")

    if not login or not password:
        print("[MT5] Skipped — no credentials in .env")
        return

    adapter = MT5Adapter()
    print("[MT5] Connecting...")

    creds = {"password": password, "server": server}
    # MT5 login must be numeric
    try:
        creds["login"] = int(login)
    except ValueError:
        # login might be the password and password might be the login
        # Try swapping if login isn't numeric
        try:
            creds["login"] = int(password)
            creds["password"] = login
        except ValueError:
            print(f"[MT5] Error: Cannot determine numeric login from MT5_LOGIN={login} / MT5_PASSWORD={password}")
            print("  MT5 login must be a number. Check your .env values.")
            return

    if path:
        creds["path"] = path

    try:
        await adapter.connect(creds)
        print("[MT5] Connected!")

        info = await adapter.get_account_info()
        print(f"  Balance:  {info.balance} {info.currency}")
        print(f"  Equity:   {info.equity}")
        print(f"  P&L:      {info.unrealized_pnl}")

        positions = await adapter.get_positions()
        print(f"  Open positions: {len(positions)}")

        # Try fetching candles for a common symbol
        for sym in ["XAUUSD", "XAUUSDm", "GOLD"]:
            try:
                candles = await adapter.get_candles(sym, "M5", 5)
                if candles:
                    print(f"  Last 5 {sym} M5 candles:")
                    for c in candles:
                        print(f"    {c.time} | O:{c.open} H:{c.high} L:{c.low} C:{c.close} V:{c.volume}")
                    break
            except Exception:
                continue
        else:
            print("  Could not fetch candles for XAUUSD/XAUUSDm/GOLD")

        symbols = await adapter.get_symbols()
        print(f"  Available symbols: {len(symbols)}")
        if symbols:
            sample = [s.name for s in symbols[:10]]
            print(f"  First 10: {sample}")

        await adapter.disconnect()
        print("[MT5] Disconnected. All good!")

    except Exception as e:
        print(f"[MT5] Error: {e}")
        try:
            await adapter.disconnect()
        except Exception:
            pass


async def main():
    print("=" * 50)
    print("Flowrex Algo — Live Broker Connection Test")
    print("=" * 50)
    print()
    await test_oanda()
    print()
    await test_mt5()


if __name__ == "__main__":
    asyncio.run(main())

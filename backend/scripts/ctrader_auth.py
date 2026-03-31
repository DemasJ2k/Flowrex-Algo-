"""
cTrader OAuth2 helper — gets your access token.

Run: python scripts/ctrader_auth.py
"""
import os
import sys
import webbrowser
from urllib.parse import urlencode

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv

load_dotenv()

CLIENT_ID = os.getenv("CTRADER_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("CTRADER_CLIENT_SECRET", "")
REDIRECT_URI = "https://openapi.ctrader.com/apps/auth"  # default cTrader redirect

AUTH_URL = "https://openapi.ctrader.com/apps/auth"
TOKEN_URL = "https://openapi.ctrader.com/apps/token"


def main():
    if not CLIENT_ID or not CLIENT_SECRET:
        print("ERROR: Set CTRADER_CLIENT_ID and CTRADER_CLIENT_SECRET in .env first")
        return

    # Step 1: Open browser for authorization
    params = urlencode({
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": "trading",
    })
    auth_url = f"https://openapi.ctrader.com/apps/auth?{params}"

    print("=" * 60)
    print("cTrader OAuth2 Authorization")
    print("=" * 60)
    print()
    print("Opening browser for authorization...")
    print(f"If it doesn't open, visit this URL manually:")
    print()
    print(auth_url)
    print()

    webbrowser.open(auth_url)

    print("After logging in and authorizing, you'll be redirected to a URL like:")
    print(f"  {REDIRECT_URI}?code=SOME_CODE_HERE")
    print()
    code = input("Paste the 'code' value from the redirect URL: ").strip()

    if not code:
        print("No code provided. Aborting.")
        return

    # Step 2: Exchange code for tokens
    import httpx

    resp = httpx.post(TOKEN_URL, data={
        "grant_type": "authorization_code",
        "code": code,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "redirect_uri": REDIRECT_URI,
    })

    if resp.status_code != 200:
        print(f"ERROR: Token exchange failed ({resp.status_code})")
        print(resp.text)
        return

    data = resp.json()
    access_token = data.get("accessToken", "")
    refresh_token = data.get("refreshToken", "")

    print()
    print("=" * 60)
    print("SUCCESS! Update your .env file with these values:")
    print("=" * 60)
    print()
    print(f"CTRADER_ACCESS_TOKEN={access_token}")
    print()
    if refresh_token:
        print(f"# Save this too — use it to get new access tokens when they expire:")
        print(f"CTRADER_REFRESH_TOKEN={refresh_token}")
    print()
    print("Done! You can now connect to cTrader through Flowrex Algo.")


if __name__ == "__main__":
    main()

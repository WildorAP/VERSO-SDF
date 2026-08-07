"""
SEP-10 end-to-end test — simulates a wallet auth flow against local Polaris.

Usage (with runserver running):
    python scripts/test_sep10.py
    python scripts/test_sep10.py --base-url http://127.0.0.1:8000
"""

import argparse
import sys

import requests
from stellar_sdk import Keypair, TransactionEnvelope


def main() -> int:
    parser = argparse.ArgumentParser(description="Test SEP-10 auth against VERSO Anchor")
    parser.add_argument(
        "--base-url",
        default="http://localhost:8000",
        help="Anchor base URL (default: http://localhost:8000)",
    )
    parser.add_argument(
        "--home-domain",
        default="localhost",
        help="home_domain query param (must be in SEP10_HOME_DOMAINS)",
    )
    args = parser.parse_args()
    base = args.base_url.rstrip("/")

    print("=" * 50)
    print("SEP-10 Test — VERSO Anchor")
    print("=" * 50)

    # Step 0: endpoint alive
    print("\n[1/4] Checking /auth endpoint...")
    r = requests.get(f"{base}/auth", timeout=10)
    if r.status_code != 400 or "account" not in r.text.lower():
        print(f"  FAIL — unexpected response: {r.status_code} {r.text[:200]}")
        return 1
    print("  OK — endpoint responds")

    # Client wallet (simulates user)
    client = Keypair.random()
    print(f"\n[2/4] GET challenge for client {client.public_key[:8]}...")
    r = requests.get(
        f"{base}/auth",
        params={"account": client.public_key, "home_domain": args.home_domain},
        timeout=15,
    )
    if not r.ok:
        print(f"  FAIL — {r.status_code}: {r.text}")
        print("\n  Tip: fund the anchor account (SIGNING_KEY in stellar.toml) via Friendbot testnet.")
        return 1

    data = r.json()
    if "transaction" not in data:
        print(f"  FAIL — no transaction in response: {data}")
        return 1
    print("  OK — challenge received")
    print(f"  network: {data.get('network_passphrase', '?')[:40]}...")

    # Sign challenge
    print("\n[3/4] Signing challenge with client keypair...")
    envelope = TransactionEnvelope.from_xdr(
        data["transaction"], data["network_passphrase"]
    )
    envelope.sign(client)
    signed_xdr = envelope.to_xdr()
    print("  OK — signed")

    # POST for JWT
    print("\n[4/4] POST signed transaction -> JWT...")
    r = requests.post(
        f"{base}/auth",
        json={"transaction": signed_xdr},
        timeout=15,
    )
    if not r.ok:
        print(f"  FAIL — {r.status_code}: {r.text}")
        return 1

    token = r.json().get("token")
    if not token:
        print(f"  FAIL — no token: {r.json()}")
        return 1

    print(f"  OK — JWT received ({len(token)} chars)")
    print(f"  token preview: {token[:60]}...")

    print("\n" + "=" * 50)
    print("PASS — SEP-10 auth loop complete")
    print("=" * 50)
    return 0


if __name__ == "__main__":
    sys.exit(main())

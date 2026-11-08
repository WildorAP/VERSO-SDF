"""
SEP-1 stellar.toml content for VERSO Anchor.

Polaris auto-generates endpoint URLs (WEB_AUTH_ENDPOINT, etc.)
from ACTIVE_SEPS and HOST_URL in .env. It builds them with os.path.join, which writes
a backslash on Windows, so the ones VERSO serves are rebuilt here.
"""

import os

from polaris import settings as polaris_settings

from verso_integrations.turnkey_signer import turnkey_sign_with

USDC_ISSUER_MAINNET = "GA5ZSEJYB37JRC5AVCIA5MOP4RHTM335X2KGX3IHOJAPP5RE34K4KZVN"
USDC_ISSUER_TESTNET = "GBBD47IF6LWK7P7MDEVSCWR7DPUWV3NY3DTQEVFL4NAT4AQH3ZLLFLA5"
TESTNET_PASSPHRASE = "Test SDF Network ; September 2015"
SEP_ENDPOINTS = (
    ("sep-10", "WEB_AUTH_ENDPOINT", "auth"),
    ("sep-24", "TRANSFER_SERVER_SEP0024", "sep24"),
    ("sep-38", "ANCHOR_QUOTE_SERVER", "sep38"),
)


def _endpoint_urls() -> dict:
    host = polaris_settings.HOST_URL.rstrip("/")
    return {
        key: f"{host}/{path}"
        for sep, key, path in SEP_ENDPOINTS
        if sep in polaris_settings.ACTIVE_SEPS
    }


def _usdc_issuer() -> str:
    passphrase = os.environ.get("STELLAR_NETWORK_PASSPHRASE", TESTNET_PASSPHRASE)
    if passphrase == TESTNET_PASSPHRASE:
        return USDC_ISSUER_TESTNET
    return USDC_ISSUER_MAINNET


def _currency_status() -> str:
    passphrase = os.environ.get("STELLAR_NETWORK_PASSPHRASE", TESTNET_PASSPHRASE)
    if passphrase == TESTNET_PASSPHRASE:
        return "test"
    return "live"


def _usdc_currency(anchor_asset: str, description: str, redemption: str) -> dict:
    return {
        "code": "USDC",
        "issuer": _usdc_issuer(),
        "status": _currency_status(),
        "display_decimals": 7,
        "name": "USD Coin",
        "desc": description,
        "is_asset_anchored": True,
        "anchor_asset_type": "fiat",
        "anchor_asset": anchor_asset,
        "redemption_instructions": redemption,
    }


def return_toml_contents(request, *args, **kwargs):
    # ACCOUNTS lists only the Turnkey hot wallet, the one account that holds and moves USDC.
    # The SEP-10 signing account is published as SIGNING_KEY (Polaris adds it); it holds no
    # funds and does not exist on-chain, so it is not listed here. ACCOUNTS is always set, so
    # Polaris' default (each asset's distribution account) is never published instead.
    turnkey_wallet = turnkey_sign_with()
    anchor_accounts = [turnkey_wallet] if turnkey_wallet else []

    return {
        **_endpoint_urls(),
        # Polaris adds TRANSFER_SERVER (the SEP-6 field in SEP-1) whenever SEP-24 is active.
        # SEP-6 is not implemented, so drop it (None keys are omitted from the TOML) to stop
        # wallets from offering a SEP-6 flow that returns 404. SEP-24 uses TRANSFER_SERVER_SEP0024.
        "TRANSFER_SERVER": None,
        "ACCOUNTS": anchor_accounts,
        "CURRENCIES": [
            _usdc_currency(
                "PEN",
                "USDC on-ramp/off-ramp via soles peruanos (CCI/CCE) PSAV Peru",
                "Withdraw USDC via SEP-24; receive PEN by bank transfer (CCI/CCE) to any Peruvian bank.",
            ),
            _usdc_currency(
                "USD",
                "USDC on-ramp/off-ramp vía dólares en cuentas peruanas (CCI/CCE) — PSAV Perú",
                "Withdraw USDC via SEP-24; receive USD by CCI/CCE transfer to your Peruvian bank account.",
            ),
        ],
        "DOCUMENTATION": {
            "ORG_NAME": "VERSO PERU",
            "ORG_DBA": "VERSO",
            "ORG_URL": "https://versotek.io",
            "ORG_LOGO": "https://versotek.io/static/logo.png",
            "ORG_DESCRIPTION": (
                "VERSO es un settlment layer para STABLECOIN en moneda local peruana PEN/USD"
            ),
            "ORG_OFFICIAL_EMAIL": "tecnologia@versotek.io",
            "ORG_SUPPORT_EMAIL": "operaciones@versotek.io",
            "ORG_PHYSICAL_ADDRESS": "Lima, Peru",
            "ORG_PHONE_NUMBER": "+51 963597658",
        },
    }

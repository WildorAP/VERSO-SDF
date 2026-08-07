"""
SEP-1 stellar.toml content for VERSO Anchor.

Polaris auto-generates endpoint URLs (WEB_AUTH_ENDPOINT, etc.)
from ACTIVE_SEPS and HOST_URL in .env.
"""

import os

from stellar_sdk import Keypair

USDC_ISSUER_MAINNET = "GA5ZSEJYB37JRC5AVCIA5MOP4RHTM335X2KGX3IHOJAPP5RE34K4KZVN"
USDC_ISSUER_TESTNET = "GBBD47IF6LWK7P7MDEVSCWR7DPUWV3NY3DTQEVFL4NAT4AQH3ZLLFLA5"
TESTNET_PASSPHRASE = "Test SDF Network ; September 2015"


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
    anchor_accounts = []
    signing_seed = os.environ.get("SIGNING_SEED")
    if signing_seed:
        anchor_accounts.append(Keypair.from_secret(signing_seed).public_key)

    return {
        "ACCOUNTS": anchor_accounts,
        "CURRENCIES": [
            _usdc_currency(
                "PEN",
                "USDC on-ramp/off-ramp via soles peruanos (CCI/CCE) PSAV Peru",
                "Withdraw USDC via SEP-24; receive PEN by bank transfer (CCI/CCE) to any Peruvian bank.",
            ),
            _usdc_currency(
                "USD",
                "USDC on-ramp/off-ramp via CCI/CCE — PSAV Peru",
                "Withdraw USDC via SEP-24; receive USD by wire to your verified USD bank account.",
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
            "ORG_OFFICIAL_EMAIL": "versotecnologia@versotek.io",
            "ORG_SUPPORT_EMAIL": "soporte@versotek.io",
            "ORG_PHYSICAL_ADDRESS": "Lima, Peru",
            "ORG_PHONE_NUMBER": "+51 ",
        },
    }

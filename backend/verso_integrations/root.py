"""
Root landing / health check for anchor.versotek.io.

Replaces the default Django 404 on `/` with a small JSON manifest:
service status, active SEPs, tranche roadmap, and key endpoint URLs.
"""

import os

from django.http import JsonResponse
from stellar_sdk import Keypair

from verso_integrations.sep1 import TESTNET_PASSPHRASE


def _network() -> str:
    passphrase = os.environ.get("STELLAR_NETWORK_PASSPHRASE", TESTNET_PASSPHRASE)
    return "testnet" if passphrase == TESTNET_PASSPHRASE else "mainnet"


def _anchor_account() -> str | None:
    signing_seed = os.environ.get("SIGNING_SEED", "").strip()
    if not signing_seed:
        return None
    try:
        return Keypair.from_secret(signing_seed).public_key
    except Exception:
        return None


def _active_seps() -> list[str]:
    raw = os.environ.get("ACTIVE_SEPS", "sep-1,sep-10")
    return [sep.strip() for sep in raw.split(",") if sep.strip()]


def root_view(request):
    host = os.environ.get("HOST_URL", "http://localhost:8000").rstrip("/")
    network = _network()
    explorer = (
        "https://stellar.expert/explorer/testnet"
        if network == "testnet"
        else "https://stellar.expert/explorer/public"
    )

    return JsonResponse(
        {
            "status": "ok",
            "service": "verso-anchor",
            "network": network,
            "home_domain": host.removeprefix("https://").removeprefix("http://"),
            "anchor_account": _anchor_account(),
            "active_seps": _active_seps(),
            "tranches": {
                "T1": {
                    "seps": ["sep-1", "sep-10"],
                    "status": "live",
                    "scope": "stellar.toml, wallet auth, depósito simulado (admin)",
                },
                "T2": {
                    "seps": ["sep-24", "sep-38"],
                    "status": "planned",
                    "scope": "webview on-ramp, cotizaciones PEN/USDC, KYC (DIDIT)",
                },
                "T3": {
                    "seps": ["mainnet"],
                    "status": "planned",
                    "scope": "producción mainnet",
                },
            },
            "paths": {
                "stellar_toml": "/.well-known/stellar.toml",
                "sep10_auth": "/auth",
                "admin": "/admin",
            },
            "endpoints": {
                "stellar_toml": f"{host}/.well-known/stellar.toml",
                "sep10_auth": f"{host}/auth",
                "admin": f"{host}/admin",
            },
            "links": {
                "org": "https://versotek.io",
                "polaris_docs": "https://django-polaris.readthedocs.io/en/stable/",
                "stellar_expert": explorer,
            },
        }
    )

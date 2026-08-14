"""
Root landing / health check for anchor.versotek.io.

HTML landing for human reviewers (SDF); JSON via ?format=json or Accept: application/json.
"""

import os

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render
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


def build_root_payload() -> dict:
    host = os.environ.get("HOST_URL", "http://localhost:8000").rstrip("/")
    network = _network()
    anchor_account = _anchor_account()
    explorer = (
        "https://stellar.expert/explorer/testnet"
        if network == "testnet"
        else "https://stellar.expert/explorer/public"
    )
    account_explorer = (
        f"{explorer}/account/{anchor_account}" if anchor_account else explorer
    )

    return {
        "status": "ok",
        "service": "verso-anchor",
        "network": network,
        "home_domain": host.removeprefix("https://").removeprefix("http://"),
        "anchor_account": anchor_account,
        "account_explorer": account_explorer,
        "active_seps": _active_seps(),
        "tranches": [
            {
                "id": "T1",
                "seps": ["SEP-1", "SEP-10"],
                "status": "live",
                "status_label": "En testnet",
                "scope": "stellar.toml, wallet auth, depósito simulado (admin)",
            },
            {
                "id": "T2",
                "seps": ["SEP-24", "SEP-38"],
                "status": "planned",
                "status_label": "Planificado",
                "scope": "webview on-ramp, cotizaciones PEN/USDC, KYC (DIDIT)",
            },
            {
                "id": "T3",
                "seps": ["Mainnet"],
                "status": "planned",
                "status_label": "Planificado",
                "scope": "producción mainnet",
            },
        ],
        "endpoints": [
            {
                "label": "stellar.toml",
                "sep": "SEP-1",
                "path": "/.well-known/stellar.toml",
                "url": f"{host}/.well-known/stellar.toml",
            },
            {
                "label": "Wallet auth",
                "sep": "SEP-10",
                "path": "/auth",
                "url": f"{host}/auth",
            },
            {
                "label": "Admin (operador)",
                "sep": None,
                "path": "/admin",
                "url": f"{host}/admin",
            },
        ],
        "links": {
            "org": "https://versotek.io",
            "polaris_docs": "https://django-polaris.readthedocs.io/en/stable/",
            "stellar_expert": explorer,
        },
    }


def _wants_json(request: HttpRequest) -> bool:
    if request.GET.get("format") == "json":
        return True
    accept = request.META.get("HTTP_ACCEPT", "")
    if not accept or accept.strip() == "*/*":
        return False
    return "application/json" in accept and "text/html" not in accept


def root_view(request: HttpRequest) -> HttpResponse:
    payload = build_root_payload()
    if _wants_json(request):
        return JsonResponse(payload)
    return render(request, "verso_integrations/root.html", payload)

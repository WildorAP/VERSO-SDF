"""
Bridge to BASE_DE_CLIENTES for KYC lookup by Stellar public key.
"""

from __future__ import annotations

import requests
from django.conf import settings


class KycBridgeError(Exception):
    """VERSO Core client lookup error."""


def fetch_client_by_stellar_key(stellar_public_key: str) -> dict | None:
    """
    Return VERSO Core JSON for the client linked to ``stellar_public_key``.

    Returns None when the client does not exist (HTTP 404).
    """
    if not settings.VERSO_CORE_API_KEY:
        raise KycBridgeError("VERSO_CORE_API_KEY is not configured.")

    url = (
        f"{settings.VERSO_CORE_API_URL.rstrip('/')}"
        f"/internal/clients/by-stellar-key/{stellar_public_key}"
    )
    try:
        response = requests.get(
            url,
            headers={"Authorization": f"Bearer {settings.VERSO_CORE_API_KEY}"},
            timeout=10,
        )
    except requests.RequestException as exc:
        raise KycBridgeError(f"Failed to fetch client from VERSO Core: {exc}") from exc

    if response.status_code == 404:
        return None
    if response.status_code != 200:
        raise KycBridgeError(
            f"Unexpected VERSO Core response ({response.status_code}) for client lookup."
        )

    payload = response.json()
    if not isinstance(payload, dict):
        raise KycBridgeError("VERSO Core returned a non-object client payload.")
    return payload

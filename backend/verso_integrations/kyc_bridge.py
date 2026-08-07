"""
Bridge to BASE_DE_CLIENTES for KYC lookup by Stellar public key.

Used by SEP-10 post-auth hooks (T1) and SEP-24 onboarding (T2).
"""

from django.conf import settings
import requests


def get_client_by_stellar_key(stellar_public_key: str) -> dict | None:
    """Return client KYC record or None if not found."""
    if not settings.VERSO_CORE_API_KEY:
        return None

    url = f"{settings.VERSO_CORE_API_URL}/internal/clients/by-stellar-key/{stellar_public_key}"
    response = requests.get(
        url,
        headers={"Authorization": f"Bearer {settings.VERSO_CORE_API_KEY}"},
        timeout=10,
    )
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return response.json()

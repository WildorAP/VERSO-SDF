"""
PEN/USDC rate provider for SEP-38 (Tranche 2).
"""

from django.conf import settings
import requests


def get_pen_usdc_rate() -> dict:
    """Fetch live PEN/USDC rate from BASE_DE_CLIENTES pricing engine."""
    url = f"{settings.VERSO_CORE_API_URL}/internal/rates/pen-usdc"
    response = requests.get(
        url,
        headers={"Authorization": f"Bearer {settings.VERSO_CORE_API_KEY}"},
        timeout=5,
    )
    response.raise_for_status()
    return response.json()

"""
Fiat/USDC rate provider for SEP-38 and SEP-24 (Tranche 2).

VERSO Core contract (same shape for each pair):

    GET {VERSO_CORE_API_URL}/internal/rates/pen-usdc
    GET {VERSO_CORE_API_URL}/internal/rates/usd-usdc

    {
        "tipo_cambio": "3.7500",           // required — fiat units per 1 USDC
        "updated_at": "2026-09-07T12:00:00Z",  // optional, ISO 8601
        "source": "pricing_engine"         // optional
    }

PEN: tipo_cambio = PEN per 1 USDC.
USD: tipo_cambio = USD per 1 USDC (typically ~1.0000).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.utils.dateparse import parse_datetime
import requests


class RatesError(Exception):
    """VERSO Core rates API or response contract error."""


@dataclass(frozen=True)
class FiatUsdcRate:
    """Normalized fiat/USDC exchange rate from VERSO Core."""

    tipo_cambio: Decimal
    updated_at: datetime | None = None
    source: str | None = None

    @property
    def tipo_cambio_fiat_per_usdc(self) -> Decimal:
        return self.tipo_cambio


# Backward-compatible alias used in deposit flow (PEN).
PenUsdcRate = FiatUsdcRate
UsdUsdcRate = FiatUsdcRate


def parse_fiat_usdc_response(payload: dict) -> FiatUsdcRate:
    """Validate and normalize the VERSO Core JSON body."""
    if not isinstance(payload, dict):
        raise RatesError("Response must be a JSON object.")

    raw_tipo = payload.get("tipo_cambio")
    if raw_tipo is None:
        raise RatesError('Missing required field "tipo_cambio".')

    try:
        tipo_cambio = Decimal(str(raw_tipo))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise RatesError(f'Invalid "tipo_cambio" value: {raw_tipo!r}.') from exc

    if tipo_cambio <= Decimal("0"):
        raise RatesError('"tipo_cambio" must be greater than zero.')

    updated_at = None
    raw_updated = payload.get("updated_at")
    if raw_updated is not None:
        if not isinstance(raw_updated, str):
            raise RatesError('"updated_at" must be an ISO 8601 string.')
        updated_at = parse_datetime(raw_updated)
        if updated_at is None:
            raise RatesError(f'Invalid "updated_at" value: {raw_updated!r}.')

    source = payload.get("source")
    if source is not None and not isinstance(source, str):
        raise RatesError('"source" must be a string.')

    return FiatUsdcRate(
        tipo_cambio=tipo_cambio,
        updated_at=updated_at,
        source=source,
    )


parse_pen_usdc_response = parse_fiat_usdc_response
parse_usd_usdc_response = parse_fiat_usdc_response


def _fetch_fiat_usdc_rate(pair_slug: str) -> FiatUsdcRate:
    if not settings.VERSO_CORE_API_KEY:
        raise RatesError("VERSO_CORE_API_KEY is not configured.")

    url = f"{settings.VERSO_CORE_API_URL.rstrip('/')}/internal/rates/{pair_slug}"
    try:
        response = requests.get(
            url,
            headers={"Authorization": f"Bearer {settings.VERSO_CORE_API_KEY}"},
            timeout=5,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise RatesError(f"Failed to fetch rate from VERSO Core: {exc}") from exc

    try:
        payload = response.json()
    except ValueError as exc:
        raise RatesError("VERSO Core returned non-JSON response.") from exc

    return parse_fiat_usdc_response(payload)


def get_pen_usdc_rate() -> FiatUsdcRate:
    """Fetch live PEN/USDC rate from BASE_DE_CLIENTES pricing engine."""
    return _fetch_fiat_usdc_rate("pen-usdc")


def get_usd_usdc_rate() -> FiatUsdcRate:
    """Fetch live USD/USDC rate from BASE_DE_CLIENTES pricing engine."""
    return _fetch_fiat_usdc_rate("usd-usdc")


def get_tipo_cambio() -> Decimal:
    """PEN per 1 USDC — convenience for PEN on-ramp integrations."""
    return get_pen_usdc_rate().tipo_cambio

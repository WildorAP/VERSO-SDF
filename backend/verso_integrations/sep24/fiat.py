"""Shared fiat on-ramp helpers for SEP-24 (PEN and USD) — Perú only, CCI/CCE."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from verso_integrations.polaris_setup import (
    DELIVERY_PEN_SELL,
    pen_asset_identification,
    usd_asset_identification,
)
from verso_integrations.rates import (
    FiatUsdcRate,
    get_pen_usdc_rate,
    get_usd_usdc_rate,
)

FIAT_PEN = "PEN"
FIAT_USD = "USD"
SUPPORTED_FIAT_CURRENCIES = frozenset({FIAT_PEN, FIAT_USD})


@dataclass(frozen=True)
class FiatOnRampConfig:
    code: str
    symbol: str
    pair_label: str
    delivery_method: str
    asset_identification: str
    min_amount: str
    rate_fetcher: Callable[[], FiatUsdcRate]
    amount_label: str
    amount_help: str
    verifying_message: str


def normalize_fiat_currency(value: str | None) -> str:
    if not value:
        return FIAT_PEN
    normalized = value.strip().upper()
    if normalized in SUPPORTED_FIAT_CURRENCIES:
        return normalized
    if normalized.endswith(":PEN") or normalized == "ISO4217:PEN":
        return FIAT_PEN
    if normalized.endswith(":USD") or normalized == "ISO4217:USD":
        return FIAT_USD
    return FIAT_PEN


def fiat_config(currency: str) -> FiatOnRampConfig:
    code = normalize_fiat_currency(currency)
    if code == FIAT_USD:
        return FiatOnRampConfig(
            code=FIAT_USD,
            symbol="$",
            pair_label="USD → USDC",
            delivery_method=DELIVERY_PEN_SELL,
            asset_identification=usd_asset_identification(),
            min_amount="10",
            rate_fetcher=get_usd_usdc_rate,
            amount_label="Monto en dólares (USD)",
            amount_help=(
                "Ingresa el monto en USD que transferirás por CCI/CCE "
                "a nuestra cuenta en dólares (banco peruano)."
            ),
            verifying_message="Verificando depósito en dólares…",
        )
    return FiatOnRampConfig(
        code=FIAT_PEN,
        symbol="S/",
        pair_label="PEN → USDC",
        delivery_method=DELIVERY_PEN_SELL,
        asset_identification=pen_asset_identification(),
        min_amount="1",
        rate_fetcher=get_pen_usdc_rate,
        amount_label="Monto en soles (PEN)",
        amount_help="Ingresa el monto en soles que transferirás por CCI/CCE a nuestra cuenta PEN.",
        verifying_message="Verificando depósito en soles…",
    )

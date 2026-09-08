"""
SEP-38 QuoteIntegration for VERSO — PEN/USDC and USD/USDC via VERSO Core rates.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import List, Optional, Union

from django.conf import settings
from polaris.integrations import QuoteIntegration
from polaris.models import Asset, DeliveryMethod, OffChainAsset, Quote
from polaris.sep10.token import SEP10Token
from rest_framework.request import Request

from verso_integrations.polaris_setup import (
    pen_asset_identification,
    usd_asset_identification,
    usdc_asset_identification,
)
from verso_integrations.rates import RatesError, get_pen_usdc_rate, get_usd_usdc_rate


def _quantize_price(price: Decimal, sell_asset: Union[Asset, OffChainAsset]) -> Decimal:
    quantizer = Decimal("1").scaleb(-sell_asset.significant_decimals)
    return price.quantize(quantizer)


def price_for_pair(
    sell_asset: Union[Asset, OffChainAsset],
    buy_asset: Union[Asset, OffChainAsset],
    tipo_cambio: Decimal,
) -> Decimal:
    """
    SEP-38 price: value of 1 unit of buy_asset in terms of sell_asset.

    tipo_cambio is fiat units per 1 USDC (PEN or USD from VERSO Core).
    """
    sell_id = sell_asset.asset_identification_format
    buy_id = buy_asset.asset_identification_format
    usdc_id = usdc_asset_identification()

    if sell_id == usdc_id:
        price = Decimal("1") / tipo_cambio
    elif buy_id == usdc_id:
        price = tipo_cambio
    else:
        raise ValueError("unsupported asset pair for VERSO fiat/USDC")

    return _quantize_price(price, sell_asset)


def tipo_cambio_for_assets(
    sell_asset: Union[Asset, OffChainAsset],
    buy_asset: Union[Asset, OffChainAsset],
) -> Decimal:
    """Fetch the live tipo_cambio for a supported fiat/USDC pair."""
    return tipo_cambio_for_asset_ids(
        sell_asset.asset_identification_format,
        buy_asset.asset_identification_format,
    )


def tipo_cambio_for_asset_ids(sell_id: str, buy_id: str) -> Decimal:
    pen_id = pen_asset_identification()
    usd_id = usd_asset_identification()
    usdc_id = usdc_asset_identification()

    pair = {sell_id, buy_id}
    if pair == {pen_id, usdc_id}:
        return get_pen_usdc_rate().tipo_cambio
    if pair == {usd_id, usdc_id}:
        return get_usd_usdc_rate().tipo_cambio
    raise ValueError("unsupported asset pair for VERSO fiat/USDC")


def quote_expires_at(quote: Quote) -> datetime:
    """Default firm-quote TTL, honoring client expire_after when within policy."""
    now = datetime.now(timezone.utc)
    max_ttl = getattr(settings, "VERSO_QUOTE_TTL_SECONDS", 900)
    default_expiry = now + timedelta(seconds=max_ttl)

    if quote.requested_expire_after:
        if quote.requested_expire_after > default_expiry:
            raise ValueError("the requested expiration cannot be provided")
        if quote.requested_expire_after <= now:
            raise ValueError("the requested expiration cannot be provided")
        return quote.requested_expire_after

    return default_expiry


class VersoQuoteIntegration(QuoteIntegration):
    """Live PEN/USDC and USD/USDC quotes sourced from VERSO Core (rates.py)."""

    def _live_tipo_cambio(
        self,
        sell_asset: Union[Asset, OffChainAsset],
        buy_asset: Union[Asset, OffChainAsset],
    ) -> Decimal:
        try:
            return tipo_cambio_for_assets(sell_asset, buy_asset)
        except (RatesError, ValueError) as exc:
            if isinstance(exc, RatesError):
                raise RuntimeError("unable to fetch price") from exc
            raise

    def get_price(
        self,
        token: SEP10Token,
        request: Request,
        sell_asset: Union[Asset, OffChainAsset],
        buy_asset: Union[Asset, OffChainAsset],
        buy_amount: Optional[Decimal] = None,
        sell_amount: Optional[Decimal] = None,
        sell_delivery_method: Optional[DeliveryMethod] = None,
        buy_delivery_method: Optional[DeliveryMethod] = None,
        country_code: Optional[str] = None,
        *args,
        **kwargs,
    ) -> Decimal:
        tipo_cambio = self._live_tipo_cambio(sell_asset, buy_asset)
        return price_for_pair(sell_asset, buy_asset, tipo_cambio)

    def get_prices(
        self,
        token: SEP10Token,
        request: Request,
        sell_asset: Union[Asset, OffChainAsset],
        sell_amount: Decimal,
        buy_assets: List[Union[Asset, OffChainAsset]],
        sell_delivery_method: Optional[DeliveryMethod] = None,
        buy_delivery_method: Optional[DeliveryMethod] = None,
        country_code: Optional[str] = None,
        *args,
        **kwargs,
    ) -> List[Decimal]:
        return [
            price_for_pair(
                sell_asset,
                buy_asset,
                self._live_tipo_cambio(sell_asset, buy_asset),
            )
            for buy_asset in buy_assets
        ]

    def post_quote(
        self, token: SEP10Token, request: Request, quote: Quote, *args, **kwargs
    ) -> Quote:
        sell_asset = _asset_from_quote_id(quote.sell_asset)
        buy_asset = _asset_from_quote_id(quote.buy_asset)
        tipo_cambio = self._live_tipo_cambio(sell_asset, buy_asset)
        quote.price = price_for_pair(sell_asset, buy_asset, tipo_cambio)
        quote.expires_at = quote_expires_at(quote)
        return quote


def _asset_from_quote_id(asset_id: str) -> Union[Asset, OffChainAsset]:
    """Resolve quote asset string to model for decimal precision."""
    if asset_id.startswith("stellar:"):
        _prefix, code, issuer = asset_id.split(":", 2)
        return Asset.objects.get(code=code, issuer=issuer)
    scheme, identifier = asset_id.split(":", 1)
    return OffChainAsset.objects.get(scheme=scheme, identifier=identifier)

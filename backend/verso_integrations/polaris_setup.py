"""
Idempotent Polaris admin seed data for T2 (SEP-24 / SEP-38).

Run: python manage.py seed_polaris_t2
"""

from __future__ import annotations

import os

from polaris.models import Asset, DeliveryMethod, ExchangePair, OffChainAsset

from verso_integrations.sep1 import TESTNET_PASSPHRASE, USDC_ISSUER_MAINNET, USDC_ISSUER_TESTNET

PEN_SCHEME = "iso4217"
PEN_IDENTIFIER = "PEN"
PEN_COUNTRY_CODES = "PER"

USD_SCHEME = "iso4217"
USD_IDENTIFIER = "USD"
USD_COUNTRY_CODES = "USA,PER"

DELIVERY_PEN_SELL = "bank_transfer_cci_cce"
DELIVERY_PEN_BUY = "bank_transfer_cci_cce"
DELIVERY_USD_SELL = "wire_usd"
DELIVERY_USD_BUY = "wire_usd"


def usdc_issuer() -> str:
    passphrase = os.environ.get("STELLAR_NETWORK_PASSPHRASE", TESTNET_PASSPHRASE)
    if passphrase == TESTNET_PASSPHRASE:
        return USDC_ISSUER_TESTNET
    return USDC_ISSUER_MAINNET


def usdc_asset_identification() -> str:
    return f"stellar:USDC:{usdc_issuer()}"


def pen_asset_identification() -> str:
    return f"{PEN_SCHEME}:{PEN_IDENTIFIER}"


def usd_asset_identification() -> str:
    return f"{USD_SCHEME}:{USD_IDENTIFIER}"


def seed_polaris_t2(*, distribution_seed: str | None = None) -> dict[str, str]:
    """
    Create or update Polaris models required for SEP-24/SEP-38.

    On-ramps: PEN→USDC and USD→USDC.
    Off-ramps: USDC→PEN and USDC→USD.

    distribution_seed defaults to SIGNING_SEED (anchor payout account on testnet).
    Returns a summary of created/updated object labels.
    """
    distribution_seed = (distribution_seed or os.environ.get("SIGNING_SEED", "")).strip()
    issuer = usdc_issuer()
    usdc_id = usdc_asset_identification()
    pen_id = pen_asset_identification()
    usd_id = usd_asset_identification()

    asset, asset_created = Asset.objects.update_or_create(
        code="USDC",
        issuer=issuer,
        defaults={
            "significant_decimals": 7,
            "deposit_enabled": True,
            "withdrawal_enabled": True,
            "sep24_enabled": True,
            "sep38_enabled": True,
            "symbol": "USDC",
            "distribution_seed": distribution_seed or None,
        },
    )

    pen_sell_method, _ = DeliveryMethod.objects.update_or_create(
        name=DELIVERY_PEN_SELL,
        type=DeliveryMethod.TYPE.sell,
        defaults={
            "description": "Transferencia bancaria PEN vía CCI/CCE (Perú).",
        },
    )
    pen_buy_method, _ = DeliveryMethod.objects.update_or_create(
        name=DELIVERY_PEN_BUY,
        type=DeliveryMethod.TYPE.buy,
        defaults={
            "description": "Recepción de PEN en cuenta bancaria peruana vía CCI/CCE.",
        },
    )
    usd_sell_method, _ = DeliveryMethod.objects.update_or_create(
        name=DELIVERY_USD_SELL,
        type=DeliveryMethod.TYPE.sell,
        defaults={
            "description": "Transferencia USD (wire) a cuenta VERSO verificada.",
        },
    )
    usd_buy_method, _ = DeliveryMethod.objects.update_or_create(
        name=DELIVERY_USD_BUY,
        type=DeliveryMethod.TYPE.buy,
        defaults={
            "description": "Recepción de USD en cuenta bancaria verificada (wire).",
        },
    )

    pen_asset, pen_created = OffChainAsset.objects.update_or_create(
        scheme=PEN_SCHEME,
        identifier=PEN_IDENTIFIER,
        defaults={
            "significant_decimals": 2,
            "country_codes": PEN_COUNTRY_CODES,
            "symbol": "S/",
        },
    )
    pen_asset.delivery_methods.set([pen_sell_method, pen_buy_method])

    usd_asset, usd_created = OffChainAsset.objects.update_or_create(
        scheme=USD_SCHEME,
        identifier=USD_IDENTIFIER,
        defaults={
            "significant_decimals": 2,
            "country_codes": USD_COUNTRY_CODES,
            "symbol": "$",
        },
    )
    usd_asset.delivery_methods.set([usd_sell_method, usd_buy_method])

    pen_to_usdc, pen_on_created = ExchangePair.objects.update_or_create(
        sell_asset=pen_id,
        buy_asset=usdc_id,
    )
    usdc_to_pen, pen_off_created = ExchangePair.objects.update_or_create(
        sell_asset=usdc_id,
        buy_asset=pen_id,
    )
    usd_to_usdc, usd_on_created = ExchangePair.objects.update_or_create(
        sell_asset=usd_id,
        buy_asset=usdc_id,
    )
    usdc_to_usd, usd_off_created = ExchangePair.objects.update_or_create(
        sell_asset=usdc_id,
        buy_asset=usd_id,
    )

    return {
        "usdc_asset": "created" if asset_created else "updated",
        "pen_offchain_asset": "created" if pen_created else "updated",
        "usd_offchain_asset": "created" if usd_created else "updated",
        "exchange_pair_pen_usdc": "created" if pen_on_created else "exists",
        "exchange_pair_usdc_pen": "created" if pen_off_created else "exists",
        "exchange_pair_usd_usdc": "created" if usd_on_created else "exists",
        "exchange_pair_usdc_usd": "created" if usd_off_created else "exists",
        "usdc_issuer": issuer,
        "pen_to_usdc": f"{pen_to_usdc.sell_asset} → {pen_to_usdc.buy_asset}",
        "usd_to_usdc": f"{usd_to_usdc.sell_asset} → {usd_to_usdc.buy_asset}",
    }

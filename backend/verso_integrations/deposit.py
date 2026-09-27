"""
Deposit helpers for SEP-24 on-ramp (PEN / USD → USDC).

Solo transferencias vía CCI/CCE en bancos peruanos. Cada moneda tiene su propio CCI.
"""

from decimal import Decimal

from django.conf import settings

from verso_integrations.sep24.fiat import FIAT_PEN, FIAT_USD, fiat_config, normalize_fiat_currency

USDC_DECIMALS = Decimal("0.0000001")


def compute_amount_usdc(amount_fiat: Decimal, tipo_cambio: Decimal) -> Decimal:
    """Convert fiat units to USDC using tipo de cambio (fiat per 1 USDC)."""
    return (amount_fiat / tipo_cambio).quantize(USDC_DECIMALS)


def _cci_settings_for_currency(fiat_currency: str) -> tuple[str, str, str]:
    """Return bank name, CCI number and holder for the given fiat currency."""
    currency = normalize_fiat_currency(fiat_currency)
    if currency == FIAT_USD:
        return (
            settings.VERSO_USD_CCI_BANK_NAME,
            settings.VERSO_USD_CCI_ACCOUNT_NUMBER,
            settings.VERSO_USD_CCI_ACCOUNT_HOLDER,
        )
    return (
        settings.VERSO_CCI_BANK_NAME,
        settings.VERSO_CCI_ACCOUNT_NUMBER,
        settings.VERSO_CCI_ACCOUNT_HOLDER,
    )


def get_cci_deposit_instructions(
    amount_fiat: float,
    client_id: str,
    *,
    tipo_cambio: float,
    amount_usdc: float,
    fiat_currency: str,
) -> dict:
    """Return CCI/CCE transfer instructions for PEN or USD (cuentas distintas)."""
    config = fiat_config(fiat_currency)
    bank_name, account_number, account_holder = _cci_settings_for_currency(config.code)
    return {
        "fiat_currency": config.code,
        "bank_name": bank_name,
        "account_number": account_number,
        "account_holder": account_holder,
        "reference": f"TXN-{client_id}",
        "amount_fiat": amount_fiat,
        "amount_pen": amount_fiat,
        "tipo_cambio_fiat_per_usdc": tipo_cambio,
        "tipo_cambio_pen_per_usdc": tipo_cambio,
        "amount_usdc": amount_usdc,
        "instructions": (
            f"Transfiere {config.code} vía CCI/CCE desde un banco peruano "
            f"a la cuenta indicada."
        ),
    }


def get_deposit_instructions(
    fiat_currency: str,
    amount_fiat: float,
    client_id: str,
    *,
    tipo_cambio: float,
    amount_usdc: float,
) -> dict:
    return get_cci_deposit_instructions(
        amount_fiat,
        client_id,
        tipo_cambio=tipo_cambio,
        amount_usdc=amount_usdc,
        fiat_currency=fiat_currency,
    )

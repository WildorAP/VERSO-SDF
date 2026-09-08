"""
Deposit helpers for SEP-24 on-ramp (PEN → USDC).

VersoDepositIntegration lives in verso_integrations.sep24.integration.
"""

from decimal import Decimal

from django.conf import settings

USDC_DECIMALS = Decimal("0.0000001")


def compute_amount_usdc(amount_pen: Decimal, tipo_cambio: Decimal) -> Decimal:
    """Convert PEN to USDC using tipo de cambio (PEN per 1 USDC)."""
    return (amount_pen / tipo_cambio).quantize(USDC_DECIMALS)


def get_cci_deposit_instructions(
    amount_pen: float,
    client_id: str,
    *,
    tipo_cambio: float,
    amount_usdc: float,
) -> dict:
    """Return CCI/CCE bank transfer instructions for the user."""
    bank_name = getattr(settings, "VERSO_CCI_BANK_NAME", "BCP")
    account_number = getattr(settings, "VERSO_CCI_ACCOUNT_NUMBER", "XXXXXXXX")
    return {
        "bank_name": bank_name,
        "account_number": account_number,
        "reference": f"TXN-{client_id}",
        "amount_pen": amount_pen,
        "tipo_cambio_pen_per_usdc": tipo_cambio,
        "amount_usdc": amount_usdc,
        "instructions": "Transfer via CCI/CCE from any Peruvian bank.",
    }

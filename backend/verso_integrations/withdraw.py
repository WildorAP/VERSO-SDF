"""
Withdrawal helpers for SEP-24 off-ramp (USDC → PEN / USD).

Payouts via CCI/CCE to the client's Peruvian bank account.
"""

from decimal import Decimal

from verso_integrations.sep24.fiat import normalize_fiat_currency

FIAT_DECIMALS = Decimal("0.01")


def compute_amount_fiat(amount_usdc: Decimal, rate_compra: Decimal) -> Decimal:
    """Convert USDC to fiat using VERSO rate_compra (fiat per 1 USDC)."""
    return (amount_usdc * rate_compra).quantize(FIAT_DECIMALS)


def build_payout_bank_details(
    *,
    bank_name: str,
    account_number: str,
    account_holder: str,
    fiat_currency: str,
) -> dict:
    """Normalize payout CCI details stored on Sep24WithdrawMeta."""
    currency = normalize_fiat_currency(fiat_currency)
    return {
        "fiat_currency": currency,
        "bank_name": bank_name.strip(),
        "account_number": account_number.strip(),
        "account_holder": account_holder.strip(),
        "transfer_kind": "cci",
    }

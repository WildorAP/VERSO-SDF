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


def finalize_sep24_withdrawal(meta) -> bool:
    """
    Mark a SEP-24 withdrawal Transaction completed after fiat payout.

    Called from admin ``mark_fiat_sent`` and ``poll_outgoing_transactions``.
    Returns True when the transaction was moved to ``completed``.
    """
    from django.utils import timezone
    from polaris.models import Transaction

    tx = meta.transaction
    if tx.status == Transaction.STATUS.completed:
        return False

    amount_usdc = tx.amount_in or meta.amount_usdc
    if amount_usdc and amount_usdc != meta.amount_usdc:
        meta.amount_usdc = amount_usdc
        meta.amount_pen = compute_amount_fiat(amount_usdc, meta.tipo_cambio)
        meta.save(update_fields=["amount_usdc", "amount_pen", "updated_at"])

    tx.amount_in = amount_usdc or meta.amount_usdc
    tx.amount_out = meta.amount_pen
    tx.amount_fee = Decimal("0")
    tx.fee_asset = meta.buy_asset
    tx.pending_execution_attempt = False
    tx.status = Transaction.STATUS.completed
    tx.completed_at = timezone.now()
    tx.save(
        update_fields=[
            "amount_in",
            "amount_out",
            "amount_fee",
            "fee_asset",
            "pending_execution_attempt",
            "status",
            "completed_at",
        ]
    )
    return True


def build_payout_bank_details(
    *,
    bank_name: str,
    account_number: str,
    account_holder: str,
    fiat_currency: str,
    origen_fondos: str,
    origen_fondos_otro: str = "",
) -> dict:
    """Normalize payout CCI details stored on Sep24WithdrawMeta."""
    currency = normalize_fiat_currency(fiat_currency)
    details = {
        "fiat_currency": currency,
        "bank_name": bank_name.strip(),
        "account_number": account_number.strip(),
        "account_holder": account_holder.strip(),
        "origen_fondos": origen_fondos.strip(),
        "transfer_kind": "cci",
    }
    if origen_fondos == "OTRO":
        details["origen_fondos_otro"] = origen_fondos_otro.strip()
    return details

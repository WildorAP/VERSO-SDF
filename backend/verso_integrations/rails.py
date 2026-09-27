"""
Polaris RailsIntegration — fiat on-ramp deposits and fiat off-ramp withdrawals.
"""

from __future__ import annotations

from decimal import Decimal

from polaris.integrations import RailsIntegration
from polaris.models import Transaction

from verso_integrations.models import Sep24DepositMeta, Sep24WithdrawMeta
from verso_integrations.withdraw import compute_amount_fiat


class VersoRailsIntegration(RailsIntegration):
    """
    Deposits: return SEP-24 transactions once VERSO confirms fiat received.
    Withdrawals: initiate fiat payout after USDC is detected on-chain.
    """

    def poll_pending_deposits(self, pending_deposits, *args, **kwargs):
        ready: list[Transaction] = []

        for transaction in pending_deposits:
            try:
                meta = transaction.verso_deposit_meta
            except Sep24DepositMeta.DoesNotExist:
                continue

            if meta.fiat_confirmed_at is None:
                continue

            transaction.amount_in = meta.amount_pen
            transaction.amount_expected = meta.amount_pen
            transaction.amount_out = meta.amount_usdc
            transaction.amount_fee = Decimal("0")
            transaction.fee_asset = meta.sell_asset
            transaction.save(
                update_fields=[
                    "amount_in",
                    "amount_expected",
                    "amount_out",
                    "amount_fee",
                    "fee_asset",
                ]
            )
            ready.append(transaction)

        return ready

    def execute_outgoing_transaction(self, transaction: Transaction, *args, **kwargs):
        try:
            meta = transaction.verso_withdraw_meta
        except Sep24WithdrawMeta.DoesNotExist:
            transaction.status = Transaction.STATUS.error
            transaction.message = "Missing withdrawal metadata."
            transaction.save(update_fields=["status", "message"])
            return

        amount_usdc = transaction.amount_in or meta.amount_usdc
        if amount_usdc != meta.amount_usdc:
            amount_fiat = compute_amount_fiat(amount_usdc, meta.tipo_cambio)
            meta.amount_usdc = amount_usdc
            meta.amount_pen = amount_fiat
            meta.save(update_fields=["amount_usdc", "amount_pen", "updated_at"])

        transaction.amount_out = meta.amount_pen
        transaction.amount_fee = Decimal("0")
        transaction.fee_asset = meta.buy_asset
        transaction.save(update_fields=["amount_out", "amount_fee", "fee_asset"])

        if meta.fiat_sent_at:
            transaction.status = Transaction.STATUS.completed
        else:
            transaction.status = Transaction.STATUS.pending_external
        transaction.save(update_fields=["status"])

    def poll_outgoing_transactions(self, transactions, *args, **kwargs):
        complete: list[Transaction] = []
        for transaction in transactions:
            try:
                meta = transaction.verso_withdraw_meta
            except Sep24WithdrawMeta.DoesNotExist:
                continue
            if meta.fiat_sent_at is not None:
                transaction.amount_out = meta.amount_pen
                transaction.amount_fee = Decimal("0")
                transaction.fee_asset = meta.buy_asset
                transaction.save(
                    update_fields=["amount_out", "amount_fee", "fee_asset"]
                )
                complete.append(transaction)
        return complete

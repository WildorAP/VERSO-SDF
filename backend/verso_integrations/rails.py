"""
Polaris RailsIntegration — detect off-chain PEN received for SEP-24 deposits.
"""

from __future__ import annotations

from decimal import Decimal

from polaris.integrations import RailsIntegration
from polaris.models import Transaction

from verso_integrations.models import Sep24DepositMeta


class VersoRailsIntegration(RailsIntegration):
    """
    Return SEP-24 deposit transactions once VERSO confirms PEN (mock/admin for MVP).
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

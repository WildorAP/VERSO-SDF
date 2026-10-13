"""
Polaris custody integration backed by Turnkey (T3 — hot wallet custody).

Polaris decides *when* to pay a deposit; ``CustodyIntegration`` decides *who signs*.
The default ``SelfCustodyIntegration`` signs with ``Asset.distribution_seed`` held by
this server. ``VersoCustodyIntegration`` instead builds the payment with the Turnkey
wallet (``TURNKEY_SIGN_WITH``) as source and asks Turnkey for the signature, so the
hot wallet's private key never exists on VERSO infrastructure.

SEP-10 challenges are still signed with ``SIGNING_SEED`` (Polaris has no extension
point there); that account must hold no funds.

Safety rules:
- Payouts above ``MAX_PAYOUT_USDC`` are not signed: the transaction is saved as
  ``blocked`` for operator review (Turnkey cannot parse Stellar amounts, so the
  limit lives here).
- The signed envelope is saved before submission. Retries resubmit that same
  envelope (same sequence number), and if its hash is already on the ledger it is
  returned instead of paying again. A new envelope is only built once the previous
  one has expired without landing.
"""

from __future__ import annotations

import logging
import time
from decimal import Decimal, InvalidOperation
from typing import Callable

from django.conf import settings as django_settings
from polaris import settings as polaris_settings
from polaris.exceptions import (
    TransactionSubmissionBlocked,
    TransactionSubmissionFailed,
    TransactionSubmissionPending,
)
from polaris.integrations import CustodyIntegration
from polaris.models import Asset, Transaction
from polaris.utils import make_memo, memo_hex_to_base64
from stellar_sdk import Asset as StellarAsset
from stellar_sdk import Claimant, Server, TransactionBuilder, TransactionEnvelope
from stellar_sdk.exceptions import BadRequestError, BadResponseError, ConnectionError, NotFoundError

from verso_integrations.turnkey_signer import (
    TurnkeyConfigError,
    TurnkeyRejectedError,
    TurnkeySigner,
    TurnkeyUnavailableError,
    turnkey_sign_with,
)

logger = logging.getLogger(__name__)

PAYOUT_TX_TIMEOUT_SECONDS = 300
# Seconds past an envelope's max_time before it is treated as unable to land.
EXPIRY_MARGIN_SECONDS = 30
# Polaris retries TransactionSubmissionPending immediately; pause so we don't hammer Turnkey/Horizon.
PENDING_BACKOFF_SECONDS = 5


def max_payout_usdc() -> Decimal | None:
    raw = str(getattr(django_settings, "MAX_PAYOUT_USDC", "") or "").strip()
    if not raw:
        return None
    try:
        limit = Decimal(raw)
    except InvalidOperation:
        raise TransactionSubmissionBlocked(f"MAX_PAYOUT_USDC is not a valid number: {raw!r}")
    return limit if limit > 0 else None


def deposit_payment_amount(transaction: Transaction) -> Decimal:
    """Same amount Polaris' create_deposit_envelope would send."""
    if transaction.amount_out:
        return Decimal(transaction.amount_out)
    if transaction.quote:
        raise TransactionSubmissionFailed(
            f"transaction {transaction.id} uses a quote but does not have amount_out assigned"
        )
    return round(
        Decimal(transaction.amount_in) - Decimal(transaction.amount_fee),
        transaction.asset.significant_decimals,
    )


def _backoff_then_pending(message: str) -> TransactionSubmissionPending:
    time.sleep(PENDING_BACKOFF_SECONDS)
    return TransactionSubmissionPending(message)


class VersoCustodyIntegration(CustodyIntegration):
    def __init__(self, signer_factory: Callable[[], TurnkeySigner] = TurnkeySigner):
        self._signer_factory = signer_factory
        self._signer: TurnkeySigner | None = None

    @property
    def claimable_balances_supported(self) -> bool:
        return True

    @property
    def account_creation_supported(self) -> bool:
        return True

    def get_distribution_account(self, asset: Asset) -> str:
        return turnkey_sign_with()

    def get_receiving_account_and_memo(self, request, transaction: Transaction):
        """Withdrawals are received on the Turnkey wallet, matched by a hash memo of the transaction ID."""
        padded_hex_memo = "0" * (64 - len(transaction.id.hex)) + transaction.id.hex
        return turnkey_sign_with(), memo_hex_to_base64(padded_hex_memo)

    def submit_deposit_transaction(self, transaction: Transaction, has_trustline: bool = True) -> str:
        amount = deposit_payment_amount(transaction)
        limit = max_payout_usdc()
        if limit is not None and amount > limit:
            raise TransactionSubmissionBlocked(
                f"payout of {amount} {transaction.asset.code} exceeds MAX_PAYOUT_USDC ({limit}); "
                "operator review required"
            )
        asset = StellarAsset(code=transaction.asset.code, issuer=transaction.asset.issuer)

        def add_operation(builder: TransactionBuilder) -> None:
            if has_trustline:
                builder.append_payment_op(destination=transaction.to_address, asset=asset, amount=str(amount))
            else:
                builder.append_create_claimable_balance_op(
                    claimants=[Claimant(destination=transaction.to_address)],
                    asset=asset,
                    amount=str(amount),
                )

        return self._submit(transaction, add_operation)

    def create_destination_account(self, transaction: Transaction) -> str:
        def add_operation(builder: TransactionBuilder) -> None:
            builder.append_create_account_op(
                destination=transaction.to_address,
                starting_balance=polaris_settings.ACCOUNT_STARTING_BALANCE,
            )

        return self._submit(transaction, add_operation)

    # --- internals -------------------------------------------------------------------

    def _get_signer(self) -> TurnkeySigner:
        if self._signer is None:
            self._signer = self._signer_factory()
        return self._signer

    def _submit(self, transaction: Transaction, add_operation: Callable[[TransactionBuilder], None]) -> str:
        with Server(horizon_url=polaris_settings.HORIZON_URI) as server:
            envelope = self._previous_envelope(transaction, server)
            if isinstance(envelope, str):
                return envelope  # already on the ledger: never pay twice
            if envelope is None:
                envelope = self._build_and_sign(transaction, server, add_operation)

            try:
                response = server.submit_transaction(envelope)
            except (BadResponseError, ConnectionError) as exc:
                if isinstance(exc, BadResponseError):
                    message = f"BadResponseError ({exc.status}): {exc.message}"
                else:
                    message = str(exc)
                raise _backoff_then_pending(message)
            except BadRequestError as exc:
                if exc.status != 400:
                    raise TransactionSubmissionFailed(f"unexpected status code: {exc.status}")
                tx_error_code = (exc.extras or {}).get("result_codes", {}).get("transaction")
                message = f"BadRequestError ({exc.status}): {exc.message} [{tx_error_code}]"
                if tx_error_code == "tx_insufficient_fee":
                    raise _backoff_then_pending(message)
                raise TransactionSubmissionFailed(message)
            return response["hash"]

    def _previous_envelope(self, transaction: Transaction, server: Server):
        """
        Returns the hash if the saved envelope already landed, the envelope to resubmit if it
        can still land, or None when a new envelope must be built.
        """
        if not transaction.envelope_xdr:
            return None
        envelope = TransactionEnvelope.from_xdr(
            transaction.envelope_xdr, polaris_settings.STELLAR_NETWORK_PASSPHRASE
        )
        tx_hash = envelope.hash_hex()
        try:
            server.transactions().transaction(tx_hash).call()
        except NotFoundError:
            pass
        except (BadResponseError, ConnectionError) as exc:
            raise _backoff_then_pending(f"could not check previous envelope {tx_hash}: {exc}")
        else:
            logger.info("transaction %s: envelope %s already on ledger", transaction.id, tx_hash)
            return tx_hash

        time_bounds = envelope.transaction.preconditions.time_bounds if envelope.transaction.preconditions else None
        max_time = time_bounds.max_time if time_bounds else 0
        if max_time and time.time() > max_time + EXPIRY_MARGIN_SECONDS:
            logger.info("transaction %s: envelope %s expired without landing; rebuilding", transaction.id, tx_hash)
            return None
        return envelope

    def _build_and_sign(
        self,
        transaction: Transaction,
        server: Server,
        add_operation: Callable[[TransactionBuilder], None],
    ) -> TransactionEnvelope:
        source = turnkey_sign_with()
        try:
            source_account = server.load_account(source)
            base_fee = polaris_settings.MAX_TRANSACTION_FEE_STROOPS or server.fetch_base_fee()
        except NotFoundError:
            raise TransactionSubmissionBlocked(f"Turnkey hot wallet {source} does not exist on this network")
        except (BadResponseError, ConnectionError) as exc:
            raise _backoff_then_pending(f"could not load hot wallet {source}: {exc}")

        builder = TransactionBuilder(
            source_account=source_account,
            network_passphrase=polaris_settings.STELLAR_NETWORK_PASSPHRASE,
            base_fee=base_fee,
        )
        add_operation(builder)
        if transaction.memo:
            builder.add_memo(make_memo(transaction.memo, transaction.memo_type))
        envelope = builder.set_timeout(PAYOUT_TX_TIMEOUT_SECONDS).build()

        try:
            self._get_signer().sign_envelope(envelope)
        except TurnkeyUnavailableError as exc:
            raise _backoff_then_pending(str(exc))
        except (TurnkeyConfigError, TurnkeyRejectedError) as exc:
            raise TransactionSubmissionBlocked(str(exc))

        # Persist before submitting so a retry resubmits this exact envelope instead of signing a new payment.
        transaction.envelope_xdr = envelope.to_xdr()
        transaction.save()
        return envelope

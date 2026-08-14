"""
On-chain USDC disbursement for simulated fiat deposits (testnet / mainnet).
"""

import os
from decimal import Decimal

from stellar_sdk import Asset, Keypair, Server, TransactionBuilder
from stellar_sdk.exceptions import BaseRequestError, NotFoundError

from verso_integrations.sep1 import (
    TESTNET_PASSPHRASE,
    USDC_ISSUER_MAINNET,
    USDC_ISSUER_TESTNET,
)

USDC_CODE = "USDC"
HORIZON_TESTNET = "https://horizon-testnet.stellar.org"
HORIZON_MAINNET = "https://horizon.stellar.org"


class StellarPayoutError(Exception):
    pass


def _network_passphrase() -> str:
    return os.environ.get("STELLAR_NETWORK_PASSPHRASE", TESTNET_PASSPHRASE)


def _horizon_url() -> str:
    if _network_passphrase() == TESTNET_PASSPHRASE:
        return HORIZON_TESTNET
    return HORIZON_MAINNET


def _usdc_issuer() -> str:
    if _network_passphrase() == TESTNET_PASSPHRASE:
        return USDC_ISSUER_TESTNET
    return USDC_ISSUER_MAINNET


def _signing_keypair() -> Keypair:
    seed = os.environ.get("SIGNING_SEED", "").strip()
    if not seed:
        raise StellarPayoutError("SIGNING_SEED is not configured.")
    try:
        return Keypair.from_secret(seed)
    except Exception as exc:
        raise StellarPayoutError("SIGNING_SEED is invalid.") from exc


def disburse_usdc(destination_account: str, amount_usdc: Decimal) -> str:
    """
    Send USDC from the anchor signing account to the client's Stellar account.
    Returns the transaction hash on success.
    """
    if amount_usdc <= Decimal("0"):
        raise StellarPayoutError("amount_usdc must be greater than zero.")

    source = _signing_keypair()
    server = Server(_horizon_url())
    usdc = Asset(USDC_CODE, _usdc_issuer())

    try:
        source_account = server.load_account(source.public_key)
    except NotFoundError as exc:
        raise StellarPayoutError(
            f"Anchor account {source.public_key} not found on the network."
        ) from exc

    try:
        transaction = (
            TransactionBuilder(
                source_account=source_account,
                network_passphrase=_network_passphrase(),
                base_fee=server.fetch_base_fee(),
            )
            .append_payment_op(
                destination=destination_account,
                amount=str(amount_usdc),
                asset=usdc,
            )
            .set_timeout(30)
            .build()
        )
        transaction.sign(source)
        response = server.submit_transaction(transaction)
    except BaseRequestError as exc:
        raise StellarPayoutError(f"Stellar network rejected payment: {exc}") from exc
    except Exception as exc:
        raise StellarPayoutError(f"Failed to submit USDC payment: {exc}") from exc

    tx_hash = response.get("hash")
    if not tx_hash:
        raise StellarPayoutError("Payment submitted but no transaction hash was returned.")
    return tx_hash

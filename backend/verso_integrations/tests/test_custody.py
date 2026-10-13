"""
T3 custody: VersoCustodyIntegration + TurnkeySigner with a simulated Turnkey.

The fake Turnkey client signs with a local keypair standing in for the key held in
Turnkey, so no network calls or real credentials are involved.
"""

import json
import time
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import polaris.integrations
from django.apps import apps
from django.test import TestCase, override_settings
from polaris import settings as polaris_settings
from polaris.exceptions import (
    TransactionSubmissionBlocked,
    TransactionSubmissionFailed,
    TransactionSubmissionPending,
)
from polaris.integrations import SelfCustodyIntegration
from polaris.models import Asset, Transaction
from stellar_sdk import Account, Keypair, TransactionBuilder, TransactionEnvelope
from stellar_sdk.client.response import Response
from stellar_sdk.exceptions import BadRequestError, NotFoundError
from stellar_sdk.operation import CreateAccount, CreateClaimableBalance, Payment
from turnkey_sdk_types.errors import TurnkeyNetworkError

from reconciliation.config import hot_wallet
from verso_integrations.custody import VersoCustodyIntegration
from verso_integrations.sep1 import USDC_ISSUER_TESTNET
from verso_integrations.turnkey_signer import (
    TurnkeyConfig,
    TurnkeyConfigError,
    TurnkeyRejectedError,
    TurnkeySigner,
    TurnkeyUnavailableError,
)

WALLET = Keypair.random()  # stands in for the key that lives inside Turnkey
CLIENT = Keypair.random()


class FakeTurnkeyClient:
    """Mimics TurnkeyClient.sign_raw_payload: Ed25519 over the raw hash, returned as r/s hex."""

    def __init__(self, keypair=WALLET, status="ACTIVITY_STATUS_COMPLETED", error=None):
        self.keypair = keypair
        self.status = status
        self.error = error
        self.calls = []

    def sign_raw_payload(self, body):
        self.calls.append(body)
        if self.error:
            raise self.error
        assert body.encoding.value == "PAYLOAD_ENCODING_HEXADECIMAL"
        assert body.hashFunction.value == "HASH_FUNCTION_NOT_APPLICABLE"
        signature = self.keypair.sign(bytes.fromhex(body.payload))
        return SimpleNamespace(
            activity=SimpleNamespace(status=self.status),
            r=signature[:32].hex(),
            s=signature[32:].hex(),
        )


def make_signer(client=None) -> TurnkeySigner:
    config = TurnkeyConfig(
        sign_with=WALLET.public_key,
        organization_id="org-test",
        api_public_key="02" + "a" * 64,
        api_private_key="b" * 64,
    )
    return TurnkeySigner(config=config, client=client or FakeTurnkeyClient())


def horizon_error(cls, status, tx_code=None):
    body = {"status": status, "title": "error"}
    if tx_code:
        body["extras"] = {"result_codes": {"transaction": tx_code}}
    return cls(Response(status_code=status, text=json.dumps(body), headers={}, url="https://horizon"))


def mock_horizon(server_cls, *, submit_hash="ab" * 32, on_ledger=False):
    server = MagicMock()
    server_cls.return_value.__enter__.return_value = server
    server.load_account.side_effect = lambda account_id: Account(account_id, 1000)
    server.fetch_base_fee.return_value = 100
    server.submit_transaction.return_value = {"hash": submit_hash}
    if on_ledger:
        server.transactions.return_value.transaction.return_value.call.return_value = {"successful": True}
    else:
        server.transactions.return_value.transaction.return_value.call.side_effect = horizon_error(
            NotFoundError, 404
        )
    return server


@override_settings(TURNKEY_SIGN_WITH=WALLET.public_key, MAX_PAYOUT_USDC="1000")
@patch("verso_integrations.custody.PENDING_BACKOFF_SECONDS", 0)
@patch("verso_integrations.custody.Server")
class VersoCustodyIntegrationTests(TestCase):
    def setUp(self):
        self.asset = Asset.objects.create(code="USDC", issuer=USDC_ISSUER_TESTNET, significant_decimals=7)
        self.transaction = Transaction.objects.create(
            stellar_account=CLIENT.public_key,
            to_address=CLIENT.public_key,
            asset=self.asset,
            kind=Transaction.KIND.deposit,
            status=Transaction.STATUS.pending_anchor,
            protocol=Transaction.PROTOCOL.sep24,
            amount_in=Decimal("100"),
            amount_fee=Decimal("0"),
            amount_out=Decimal("26.5"),
        )
        self.turnkey = FakeTurnkeyClient()
        self.custody = VersoCustodyIntegration(signer_factory=lambda: make_signer(self.turnkey))

    def submitted_envelope(self, server) -> TransactionEnvelope:
        return server.submit_transaction.call_args.args[0]

    def test_accounts_are_the_turnkey_wallet(self, server_cls):
        self.assertEqual(self.custody.get_distribution_account(self.asset), WALLET.public_key)
        account, memo = self.custody.get_receiving_account_and_memo(None, self.transaction)
        self.assertEqual(account, WALLET.public_key)
        self.assertTrue(memo)

    def test_deposit_payment_is_signed_by_turnkey(self, server_cls):
        server = mock_horizon(server_cls)

        tx_hash = self.custody.submit_deposit_transaction(self.transaction, has_trustline=True)

        self.assertEqual(tx_hash, "ab" * 32)
        envelope = self.submitted_envelope(server)
        self.assertEqual(envelope.transaction.source.account_id, WALLET.public_key)
        op = envelope.transaction.operations[0]
        self.assertIsInstance(op, Payment)
        self.assertEqual(op.destination.account_id, CLIENT.public_key)
        self.assertEqual(Decimal(op.amount), Decimal("26.5"))
        self.assertEqual(len(envelope.signatures), 1)
        WALLET.verify(envelope.hash(), envelope.signatures[0].signature)
        self.transaction.refresh_from_db()
        self.assertEqual(self.transaction.envelope_xdr, envelope.to_xdr())

    def test_deposit_without_trustline_uses_claimable_balance(self, server_cls):
        server = mock_horizon(server_cls)

        self.custody.submit_deposit_transaction(self.transaction, has_trustline=False)

        self.assertIsInstance(self.submitted_envelope(server).transaction.operations[0], CreateClaimableBalance)

    def test_create_destination_account_is_signed_by_turnkey(self, server_cls):
        server = mock_horizon(server_cls)

        self.custody.create_destination_account(self.transaction)

        op = self.submitted_envelope(server).transaction.operations[0]
        self.assertIsInstance(op, CreateAccount)
        self.assertEqual(op.destination, CLIENT.public_key)

    def test_payout_above_limit_is_blocked_without_signing(self, server_cls):
        server = mock_horizon(server_cls)
        self.transaction.amount_out = Decimal("1000.01")

        with self.assertRaises(TransactionSubmissionBlocked):
            self.custody.submit_deposit_transaction(self.transaction)

        self.assertEqual(self.turnkey.calls, [])
        server.submit_transaction.assert_not_called()

    @override_settings(MAX_PAYOUT_USDC="")
    def test_empty_limit_disables_the_cap(self, server_cls):
        mock_horizon(server_cls)
        self.transaction.amount_out = Decimal("5000")

        self.custody.submit_deposit_transaction(self.transaction)

        self.assertEqual(len(self.turnkey.calls), 1)

    def test_turnkey_unreachable_is_retried_without_saving_an_envelope(self, server_cls):
        server = mock_horizon(server_cls)
        self.turnkey.error = TurnkeyNetworkError("timeout", status_code=None)

        with self.assertRaises(TransactionSubmissionPending):
            self.custody.submit_deposit_transaction(self.transaction)

        server.submit_transaction.assert_not_called()
        self.transaction.refresh_from_db()
        self.assertIsNone(self.transaction.envelope_xdr)

    def test_turnkey_refusal_blocks_the_transaction(self, server_cls):
        server = mock_horizon(server_cls)
        self.turnkey.error = TurnkeyNetworkError("policy denied", status_code=403)

        with self.assertRaises(TransactionSubmissionBlocked):
            self.custody.submit_deposit_transaction(self.transaction)

        server.submit_transaction.assert_not_called()

    def test_signature_from_another_key_is_never_submitted(self, server_cls):
        server = mock_horizon(server_cls)
        self.turnkey.keypair = Keypair.random()

        with self.assertRaises(TransactionSubmissionBlocked):
            self.custody.submit_deposit_transaction(self.transaction)

        server.submit_transaction.assert_not_called()

    @override_settings(TURNKEY_API_PRIVATE_KEY="")
    def test_missing_credentials_block_the_transaction(self, server_cls):
        mock_horizon(server_cls)
        custody = VersoCustodyIntegration()  # real TurnkeySigner reading settings

        with self.assertRaises(TransactionSubmissionBlocked):
            custody.submit_deposit_transaction(self.transaction)

    def test_retry_returns_hash_when_previous_envelope_already_landed(self, server_cls):
        mock_horizon(server_cls)
        self.custody.submit_deposit_transaction(self.transaction)
        saved = TransactionEnvelope.from_xdr(
            self.transaction.envelope_xdr, polaris_settings.STELLAR_NETWORK_PASSPHRASE
        )

        server = mock_horizon(server_cls, on_ledger=True)
        tx_hash = self.custody.submit_deposit_transaction(self.transaction)

        self.assertEqual(tx_hash, saved.hash_hex())
        server.submit_transaction.assert_not_called()
        self.assertEqual(len(self.turnkey.calls), 1)  # no second payment signed

    def test_retry_resubmits_the_same_envelope_while_it_can_still_land(self, server_cls):
        mock_horizon(server_cls)
        self.custody.submit_deposit_transaction(self.transaction)
        first_xdr = self.transaction.envelope_xdr

        server = mock_horizon(server_cls)
        self.custody.submit_deposit_transaction(self.transaction)

        self.assertEqual(self.submitted_envelope(server).to_xdr(), first_xdr)
        self.assertEqual(len(self.turnkey.calls), 1)

    def test_expired_envelope_that_never_landed_is_rebuilt(self, server_cls):
        expired = (
            TransactionBuilder(
                source_account=Account(WALLET.public_key, 5),
                network_passphrase=polaris_settings.STELLAR_NETWORK_PASSPHRASE,
                base_fee=100,
            )
            .append_payment_op(destination=CLIENT.public_key, asset=_usdc(), amount="26.5")
            .add_time_bounds(0, int(time.time()) - 3600)
            .build()
        )
        self.transaction.envelope_xdr = expired.to_xdr()
        self.transaction.save()
        server = mock_horizon(server_cls)

        self.custody.submit_deposit_transaction(self.transaction)

        self.assertNotEqual(self.submitted_envelope(server).to_xdr(), expired.to_xdr())
        self.assertEqual(len(self.turnkey.calls), 1)

    def test_network_rejection_fails_the_transaction(self, server_cls):
        server = mock_horizon(server_cls)
        server.submit_transaction.side_effect = horizon_error(BadRequestError, 400, "tx_bad_seq")

        with self.assertRaises(TransactionSubmissionFailed):
            self.custody.submit_deposit_transaction(self.transaction)

    def test_surge_pricing_is_retried(self, server_cls):
        server = mock_horizon(server_cls)
        server.submit_transaction.side_effect = horizon_error(BadRequestError, 400, "tx_insufficient_fee")

        with self.assertRaises(TransactionSubmissionPending):
            self.custody.submit_deposit_transaction(self.transaction)


def _usdc():
    from stellar_sdk import Asset as StellarAsset

    return StellarAsset("USDC", USDC_ISSUER_TESTNET)


class TurnkeySignerTests(TestCase):
    def test_signature_is_verified_against_the_wallet(self):
        tx_hash = bytes(range(32))
        signature = make_signer().sign_hash(tx_hash)
        WALLET.verify(tx_hash, signature)

    def test_server_errors_are_transient(self):
        client = FakeTurnkeyClient(error=TurnkeyNetworkError("down", status_code=503))
        with self.assertRaises(TurnkeyUnavailableError):
            make_signer(client).sign_hash(bytes(32))

    def test_consensus_needed_is_a_rejection(self):
        client = FakeTurnkeyClient(status="ACTIVITY_STATUS_CONSENSUS_NEEDED")
        with self.assertRaises(TurnkeyRejectedError):
            make_signer(client).sign_hash(bytes(32))

    def test_unfinished_activity_is_transient(self):
        client = FakeTurnkeyClient(status="ACTIVITY_STATUS_PENDING")
        with self.assertRaises(TurnkeyUnavailableError):
            make_signer(client).sign_hash(bytes(32))

    @override_settings(TURNKEY_SIGN_WITH=WALLET.public_key, TURNKEY_ORGANIZATION_ID="", TURNKEY_API_PUBLIC_KEY="", TURNKEY_API_PRIVATE_KEY="")
    def test_config_reports_missing_variables(self):
        with self.assertRaisesMessage(TurnkeyConfigError, "TURNKEY_API_PRIVATE_KEY"):
            TurnkeyConfig.from_settings()

    @override_settings(TURNKEY_SIGN_WITH="not-a-key", TURNKEY_ORGANIZATION_ID="o", TURNKEY_API_PUBLIC_KEY="p", TURNKEY_API_PRIVATE_KEY="k")
    def test_config_rejects_invalid_wallet_address(self):
        with self.assertRaises(TurnkeyConfigError):
            TurnkeyConfig.from_settings()


class CustodyRegistrationTests(TestCase):
    def tearDown(self):
        polaris.integrations.registered_custody_integration = SelfCustodyIntegration()

    @override_settings(TURNKEY_SIGN_WITH=WALLET.public_key)
    def test_turnkey_custody_is_registered_when_wallet_is_configured(self):
        apps.get_app_config("verso_integrations").ready()
        self.assertIsInstance(polaris.integrations.registered_custody_integration, VersoCustodyIntegration)

    @override_settings(TURNKEY_SIGN_WITH="")
    def test_self_custody_stays_when_turnkey_is_not_configured(self):
        polaris.integrations.registered_custody_integration = SelfCustodyIntegration()
        apps.get_app_config("verso_integrations").ready()
        self.assertIsInstance(polaris.integrations.registered_custody_integration, SelfCustodyIntegration)


class ReconciliationHotWalletTests(TestCase):
    @override_settings(RECON_HOT_WALLET="", TURNKEY_SIGN_WITH=WALLET.public_key)
    def test_reconciliation_watches_the_turnkey_wallet(self):
        self.assertEqual(hot_wallet(), WALLET.public_key)

    @override_settings(RECON_HOT_WALLET=CLIENT.public_key, TURNKEY_SIGN_WITH=WALLET.public_key)
    def test_explicit_recon_hot_wallet_wins(self):
        self.assertEqual(hot_wallet(), CLIENT.public_key)

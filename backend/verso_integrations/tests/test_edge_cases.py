"""
Tranche 3 / Deliverable 4 — edge-case suite.

Groups required by the deliverable: invalid SEP-10 signatures, malformed payloads, duplicate IDs and
webhook replay. Each test states the attack or mistake it simulates and the expected safe outcome.
Related coverage elsewhere: payout idempotency in ``test_custody.py`` and idempotent event ingest in
``reconciliation/tests/test_sync.py``.
"""

import os
import time
import uuid
from decimal import Decimal
from unittest.mock import MagicMock, patch
from urllib.parse import urlparse

import jwt
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.utils import timezone
from polaris import settings as polaris_settings
from polaris.models import Asset, Transaction
from stellar_sdk import Keypair, Network, TransactionEnvelope
from stellar_sdk.client.response import Response as StellarResponse
from stellar_sdk.exceptions import NotFoundError
from stellar_sdk.sep.stellar_web_authentication import build_challenge_transaction

from reconciliation.matching import match_pending
from reconciliation.models import Discrepancy, LedgerEntry
from reconciliation.tests.factories import memo_hash_for_uuid
from verso_integrations.models import Sep24DepositMeta, Sep24WithdrawMeta
from verso_integrations.polaris_setup import (
    pen_asset_identification,
    seed_polaris_t2,
    usdc_asset_identification,
)
from verso_integrations.sep24.forms import (
    BankTransferReceiptForm,
    FiatDepositForm,
    PayoutBankForm,
    UsdcWithdrawForm,
)

STATIC_STORAGE = {
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}
}


def _account_not_found(*_args, **_kwargs):
    raise NotFoundError(StellarResponse(404, "{}", {}, ""))


def _horizon_without_accounts() -> MagicMock:
    """Client accounts used in tests do not exist on-chain: Polaris then verifies the master key."""
    horizon = MagicMock()
    horizon.load_account.side_effect = _account_not_found
    return horizon


def _web_auth_domain() -> str:
    return urlparse(polaris_settings.HOST_URL).netloc


def _home_domain() -> str:
    return polaris_settings.SEP10_HOME_DOMAINS[0]


def _sep10_token(account: str, *, key: str | None = None, expires_in: int = 3600) -> str:
    now = int(time.time())
    payload = {
        "iss": os.path.join(polaris_settings.HOST_URL, "auth"),
        "sub": account,
        "iat": now,
        "exp": now + expires_in,
        "jti": uuid.uuid4().hex,
        "client_domain": None,
    }
    return jwt.encode(payload, key or polaris_settings.SERVER_JWT_KEY, algorithm="HS256")


def _usdc_transaction(kind: str, account: str, **kwargs) -> Transaction:
    defaults = {
        "stellar_account": account,
        "asset": Asset.objects.get(code="USDC"),
        "kind": kind,
        "status": Transaction.STATUS.pending_user_transfer_start,
        "protocol": Transaction.PROTOCOL.sep24,
    }
    defaults.update(kwargs)
    return Transaction.objects.create(**defaults)


# ---------------------------------------------------------------------------------------------
# 1. Invalid SEP-10 signatures
# ---------------------------------------------------------------------------------------------


@patch("polaris.sep10.views.settings.HORIZON_SERVER", new_callable=_horizon_without_accounts)
class InvalidSep10SignatureTests(TestCase):
    def setUp(self):
        self.client_kp = Keypair.random()

    def _challenge_from_server(self) -> str:
        response = self.client.get(f"/auth?account={self.client_kp.public_key}")
        self.assertEqual(response.status_code, 200)
        return response.json()["transaction"]

    def _sign(self, envelope_xdr: str, *signers: Keypair, passphrase: str | None = None) -> str:
        envelope = TransactionEnvelope.from_xdr(
            envelope_xdr, passphrase or polaris_settings.STELLAR_NETWORK_PASSPHRASE
        )
        for signer in signers:
            envelope.sign(signer)
        return envelope.to_xdr()

    def _post(self, envelope_xdr: str):
        return self.client.post(
            "/auth", data={"transaction": envelope_xdr}, content_type="application/json"
        )

    def test_challenge_signed_by_the_client_returns_a_token(self, _horizon):
        """Control case: the legitimate flow still works."""
        response = self._post(self._sign(self._challenge_from_server(), self.client_kp))
        self.assertEqual(response.status_code, 200)
        token = jwt.decode(
            response.json()["token"], polaris_settings.SERVER_JWT_KEY, algorithms=["HS256"]
        )
        self.assertEqual(token["sub"], self.client_kp.public_key)

    def test_challenge_signed_by_another_account_is_rejected(self, _horizon):
        """An attacker signs the victim's challenge with their own key."""
        response = self._post(self._sign(self._challenge_from_server(), Keypair.random()))
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("token", response.json())

    def test_challenge_without_client_signature_is_rejected(self, _horizon):
        response = self._post(self._challenge_from_server())
        self.assertEqual(response.status_code, 400)

    def test_challenge_with_an_extra_signature_is_rejected(self, _horizon):
        """For a new account only the master key may sign; extra signers are refused."""
        signed = self._sign(self._challenge_from_server(), self.client_kp, Keypair.random())
        self.assertEqual(self._post(signed).status_code, 400)

    def test_challenge_forged_with_another_server_key_is_rejected(self, _horizon):
        """A challenge not issued by the anchor (wrong server signature) never yields a token."""
        forged = build_challenge_transaction(
            server_secret=Keypair.random().secret,
            client_account_id=self.client_kp.public_key,
            home_domain=_home_domain(),
            web_auth_domain=_web_auth_domain(),
            network_passphrase=polaris_settings.STELLAR_NETWORK_PASSPHRASE,
            timeout=900,
        )
        self.assertEqual(self._post(self._sign(forged, self.client_kp)).status_code, 400)

    def test_expired_challenge_is_rejected(self, _horizon):
        with patch(
            "stellar_sdk.sep.stellar_web_authentication.time.time",
            return_value=time.time() - 3600,
        ):
            expired = build_challenge_transaction(
                server_secret=polaris_settings.SIGNING_SEED,
                client_account_id=self.client_kp.public_key,
                home_domain=_home_domain(),
                web_auth_domain=_web_auth_domain(),
                network_passphrase=polaris_settings.STELLAR_NETWORK_PASSPHRASE,
                timeout=900,
            )
        self.assertEqual(self._post(self._sign(expired, self.client_kp)).status_code, 400)

    def test_challenge_for_another_network_is_rejected(self, _horizon):
        """A challenge built and signed for a different network passphrase does not verify here."""
        other_network = (
            Network.PUBLIC_NETWORK_PASSPHRASE
            if polaris_settings.STELLAR_NETWORK_PASSPHRASE != Network.PUBLIC_NETWORK_PASSPHRASE
            else Network.TESTNET_NETWORK_PASSPHRASE
        )
        challenge = build_challenge_transaction(
            server_secret=polaris_settings.SIGNING_SEED,
            client_account_id=self.client_kp.public_key,
            home_domain=_home_domain(),
            web_auth_domain=_web_auth_domain(),
            network_passphrase=other_network,
            timeout=900,
        )
        signed = self._sign(challenge, self.client_kp, passphrase=other_network)
        self.assertEqual(self._post(signed).status_code, 400)

    def test_challenge_for_another_home_domain_is_rejected(self, _horizon):
        challenge = build_challenge_transaction(
            server_secret=polaris_settings.SIGNING_SEED,
            client_account_id=self.client_kp.public_key,
            home_domain="evil.example",
            web_auth_domain=_web_auth_domain(),
            network_passphrase=polaris_settings.STELLAR_NETWORK_PASSPHRASE,
            timeout=900,
        )
        self.assertEqual(self._post(self._sign(challenge, self.client_kp)).status_code, 400)

    def test_challenge_request_for_invalid_account_is_rejected(self, _horizon):
        response = self.client.get("/auth?account=GNOTAVALIDSTELLARACCOUNT")
        self.assertEqual(response.status_code, 400)

    def test_challenge_request_for_unknown_home_domain_is_rejected(self, _horizon):
        response = self.client.get(
            f"/auth?account={self.client_kp.public_key}&home_domain=evil.example"
        )
        self.assertEqual(response.status_code, 400)


class Sep10TokenTests(TestCase):
    """A SEP-10 token only opens the account it was issued for."""

    def setUp(self):
        seed_polaris_t2(distribution_seed=Keypair.random().secret)
        self.owner = Keypair.random().public_key
        self.transaction = _usdc_transaction(Transaction.KIND.deposit, self.owner)

    def _get(self, token: str | None):
        headers = {"HTTP_AUTHORIZATION": f"Bearer {token}"} if token else {}
        return self.client.get(f"/sep24/transaction?id={self.transaction.id}", **headers)

    def test_owner_token_can_read_its_transaction(self):
        response = self._get(_sep10_token(self.owner))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["transaction"]["id"], str(self.transaction.id))

    def test_missing_token_is_rejected(self):
        self.assertEqual(self._get(None).status_code, 403)

    def test_token_signed_with_another_key_is_rejected(self):
        self.assertEqual(self._get(_sep10_token(self.owner, key="x" * 40)).status_code, 403)

    def test_expired_token_is_rejected(self):
        self.assertEqual(self._get(_sep10_token(self.owner, expires_in=-60)).status_code, 403)

    def test_token_of_another_account_cannot_read_the_transaction(self):
        response = self._get(_sep10_token(Keypair.random().public_key))
        self.assertEqual(response.status_code, 404)


# ---------------------------------------------------------------------------------------------
# 2. Malformed payloads
# ---------------------------------------------------------------------------------------------


class MalformedAmountFormTests(TestCase):
    """Amounts typed in the SEP-24 webview."""

    def test_deposit_amount_rejects_invalid_values(self):
        for value in ("-10", "0", "abc", "10.123", "", "1e309", "NaN"):
            with self.subTest(value=value):
                self.assertFalse(FiatDepositForm({"amount_fiat": value}).is_valid())

    def test_deposit_amount_below_currency_minimum_is_rejected(self):
        self.assertFalse(FiatDepositForm({"amount_fiat": "0.99"}, fiat_currency="PEN").is_valid())

    def test_withdraw_amount_rejects_invalid_values(self):
        for value in ("-1", "0", "abc", "1.12345678", "", "NaN"):
            with self.subTest(value=value):
                self.assertFalse(UsdcWithdrawForm({"amount_usdc": value}).is_valid())

    def test_payout_bank_form_requires_every_field(self):
        form = PayoutBankForm({"bank_name": "", "account_number": "", "account_holder": ""})
        self.assertFalse(form.is_valid())
        for field in ("bank_name", "account_number", "account_holder", "origen_fondos"):
            self.assertIn(field, form.errors)

    def test_payout_bank_form_rejects_unknown_source_of_funds(self):
        form = PayoutBankForm(
            {
                "bank_name": "BCP",
                "account_number": "00219300123456789012",
                "account_holder": "Cliente",
                "origen_fondos": "NOT_A_CHOICE",
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("origen_fondos", form.errors)


class MalformedReceiptTests(TestCase):
    """Bank receipts uploaded for on-ramp deposits."""

    def _form(self, upload):
        return BankTransferReceiptForm({}, {"receipt": upload})

    def test_executable_or_text_file_is_rejected(self):
        for name, content_type in (
            ("receipt.exe", "application/x-msdownload"),
            ("receipt.html", "text/html"),
            ("receipt.txt", "text/plain"),
        ):
            with self.subTest(name=name):
                upload = SimpleUploadedFile(name, b"payload", content_type=content_type)
                self.assertFalse(self._form(upload).is_valid())

    def test_file_over_10_mb_is_rejected(self):
        upload = SimpleUploadedFile(
            "receipt.png", b"0" * (10 * 1024 * 1024 + 1), content_type="image/png"
        )
        self.assertFalse(self._form(upload).is_valid())

    def test_missing_file_is_rejected(self):
        self.assertFalse(BankTransferReceiptForm({}, {}).is_valid())

    def test_image_and_pdf_are_accepted(self):
        for name, content_type in (("r.png", "image/png"), ("r.pdf", "application/pdf")):
            with self.subTest(name=name):
                upload = SimpleUploadedFile(name, b"data", content_type=content_type)
                self.assertTrue(self._form(upload).is_valid())


class MalformedSep24RequestTests(TestCase):
    def setUp(self):
        seed_polaris_t2(distribution_seed=Keypair.random().secret)
        self.account = Keypair.random().public_key
        self.auth = {"HTTP_AUTHORIZATION": f"Bearer {_sep10_token(self.account)}"}

    def _interactive(self, kind: str, data: dict):
        return self.client.post(
            f"/sep24/transactions/{kind}/interactive", data=data, **self.auth
        )

    def test_unknown_asset_is_rejected(self):
        for kind in ("deposit", "withdraw"):
            with self.subTest(kind=kind):
                response = self._interactive(kind, {"asset_code": "FAKE"})
                self.assertEqual(response.status_code, 400)

    def test_invalid_amount_is_rejected(self):
        issuer = Asset.objects.get(code="USDC").issuer
        for amount in ("abc", "-5"):
            with self.subTest(amount=amount):
                response = self._interactive(
                    "deposit", {"asset_code": "USDC", "asset_issuer": issuer, "amount": amount}
                )
                self.assertEqual(response.status_code, 400)

    def test_transaction_lookup_with_non_uuid_id_is_not_a_server_error(self):
        response = self.client.get("/sep24/transaction?id=not-a-uuid", **self.auth)
        self.assertIn(response.status_code, (400, 404))


@override_settings(STORAGES=STATIC_STORAGE)
class MalformedTransactionIdTests(TestCase):
    """Webview endpoints that receive a transaction ID in the query string."""

    def test_non_uuid_ids_never_cause_a_server_error(self):
        urls = (
            "/sep24/transaction/poll/?id=not-a-uuid",
            "/sep24/onboarding/?transaction_id=not-a-uuid",
            "/sep24/kyc/start/?transaction_id=not-a-uuid",
            "/sep24/kyc/poll/?transaction_id=not-a-uuid",
            "/sep24/kyc/callback/?transaction_id=not-a-uuid",
            "/sep24/onboarding/switch/?transaction_id=not-a-uuid&mode=register",
        )
        for url in urls:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertIn(response.status_code, (400, 404))

    def test_unknown_transaction_id_returns_404(self):
        response = self.client.get(f"/sep24/transaction/poll/?id={uuid.uuid4()}")
        self.assertEqual(response.status_code, 404)


class MalformedSep38RequestTests(TestCase):
    def setUp(self):
        seed_polaris_t2(distribution_seed=Keypair.random().secret)
        self.usdc = usdc_asset_identification()
        self.pen = pen_asset_identification()
        self.auth = {"HTTP_AUTHORIZATION": f"Bearer {_sep10_token(Keypair.random().public_key)}"}

    def _price(self, **params):
        return self.client.get("/sep38/price", data=params, **self.auth)

    def test_price_with_unknown_asset_is_rejected(self):
        response = self._price(
            sell_asset="iso4217:XXX", buy_asset=self.usdc, sell_amount="10",
            context="sep6",
        )
        self.assertEqual(response.status_code, 400)

    def test_price_with_non_numeric_amount_is_rejected(self):
        response = self._price(
            sell_asset=self.pen, buy_asset=self.usdc, sell_amount="ten", context="sep6"
        )
        self.assertEqual(response.status_code, 400)

    def test_price_with_both_amounts_is_rejected(self):
        response = self._price(
            sell_asset=self.pen, buy_asset=self.usdc, sell_amount="10", buy_amount="3",
            context="sep6",
        )
        self.assertEqual(response.status_code, 400)

    def test_quote_with_malformed_json_is_rejected(self):
        response = self.client.post(
            "/sep38/quote", data="{not json", content_type="application/json", **self.auth
        )
        self.assertEqual(response.status_code, 400)

    def test_quote_without_token_is_rejected(self):
        response = self.client.post(
            "/sep38/quote",
            data={"sell_asset": self.pen, "buy_asset": self.usdc, "sell_amount": "10"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)


# ---------------------------------------------------------------------------------------------
# 3. Duplicate IDs
# ---------------------------------------------------------------------------------------------


class DuplicateOperatorActionTests(TestCase):
    """Operators clicking the same admin action twice must never move money twice."""

    def setUp(self):
        seed_polaris_t2(distribution_seed=Keypair.random().secret)
        self.admin = get_user_model().objects.create_superuser("ops", "ops@example.com", "pw")
        self.client.force_login(self.admin)
        self.account = Keypair.random().public_key

    def _run_action(self, model: str, action: str, pk: int):
        return self.client.post(
            f"/admin/verso_integrations/{model}/",
            {"action": action, "_selected_action": [pk]},
        )

    def test_mark_fiat_received_twice_keeps_the_first_confirmation(self):
        tx = _usdc_transaction(Transaction.KIND.deposit, self.account)
        meta = Sep24DepositMeta.objects.create(
            transaction=tx,
            amount_pen=Decimal("10.00"),
            tipo_cambio=Decimal("3.4500"),
            amount_usdc=Decimal("2.8985507"),
            sell_asset=pen_asset_identification(),
            buy_asset=usdc_asset_identification(),
        )
        self._run_action("sep24depositmeta", "mark_fiat_received", meta.pk)
        meta.refresh_from_db()
        first = meta.fiat_confirmed_at
        self.assertIsNotNone(first)

        self._run_action("sep24depositmeta", "mark_fiat_received", meta.pk)
        meta.refresh_from_db()
        self.assertEqual(meta.fiat_confirmed_at, first)

    def _withdrawal(self, **tx_kwargs) -> Sep24WithdrawMeta:
        tx = _usdc_transaction(
            Transaction.KIND.withdrawal,
            self.account,
            amount_in=Decimal("3.0000000"),
            status=Transaction.STATUS.pending_anchor,
            **tx_kwargs,
        )
        return Sep24WithdrawMeta.objects.create(
            transaction=tx,
            amount_usdc=Decimal("3.0000000"),
            amount_pen=Decimal("10.06"),
            tipo_cambio=Decimal("3.3550"),
            sell_asset=usdc_asset_identification(),
            buy_asset=pen_asset_identification(),
            payout_confirmed_at=timezone.now(),
        )

    def test_mark_fiat_sent_twice_completes_once(self):
        meta = self._withdrawal(stellar_transaction_id="a" * 64)
        self._run_action("sep24withdrawmeta", "mark_fiat_sent", meta.pk)
        meta.refresh_from_db()
        meta.transaction.refresh_from_db()
        first_sent, first_completed = meta.fiat_sent_at, meta.transaction.completed_at
        self.assertEqual(meta.transaction.status, Transaction.STATUS.completed)

        self._run_action("sep24withdrawmeta", "mark_fiat_sent", meta.pk)
        meta.refresh_from_db()
        meta.transaction.refresh_from_db()
        self.assertEqual(meta.fiat_sent_at, first_sent)
        self.assertEqual(meta.transaction.completed_at, first_completed)

    def test_mark_fiat_sent_is_refused_without_onchain_usdc(self):
        """Fiat is never paid for a withdrawal whose USDC was not detected on-chain."""
        meta = self._withdrawal()
        self._run_action("sep24withdrawmeta", "mark_fiat_sent", meta.pk)
        meta.refresh_from_db()
        meta.transaction.refresh_from_db()
        self.assertIsNone(meta.fiat_sent_at)
        self.assertNotEqual(meta.transaction.status, Transaction.STATUS.completed)


class DuplicateWebviewSubmissionTests(TestCase):
    """The same webview form posted twice must not create a second order."""

    def setUp(self):
        seed_polaris_t2(distribution_seed=Keypair.random().secret)

    @override_settings(VERSO_MOCK_KYC="approved", VERSO_MOCK_AUTO_CONFIRM_FIAT=False)
    def test_receipt_declared_twice_keeps_one_deposit(self):
        from verso_integrations.sep24.forms import TransferAlreadyDeclaredForm
        from verso_integrations.sep24.integration import VersoDepositIntegration

        tx = _usdc_transaction(Transaction.KIND.deposit, Keypair.random().public_key)
        Sep24DepositMeta.objects.create(
            transaction=tx,
            amount_pen=Decimal("10.00"),
            tipo_cambio=Decimal("3.4500"),
            amount_usdc=Decimal("2.8985507"),
            sell_asset=pen_asset_identification(),
            buy_asset=usdc_asset_identification(),
            transfer_declared_at=timezone.now(),
        )
        request = MagicMock()
        request.GET = {}
        form = VersoDepositIntegration().form_for_transaction(request, tx, post_data={"x": "1"})
        self.assertIsInstance(form, TransferAlreadyDeclaredForm)
        self.assertEqual(Sep24DepositMeta.objects.filter(transaction=tx).count(), 1)


class DuplicateWithdrawalPaymentTests(TestCase):
    """
    A client pays the same withdrawal twice (same memo). Polaris only takes the first payment;
    reconciliation must not attach the second one silently to the same order.
    """

    HOT = "GBTV5QYBPGHGT2SVUHCFRRKFFWUWHOEPKH7QAXGTJHFGFYZRIE24UOPB"
    CLIENT = "GCXGLWL7GEPUDCCZABQVLHTZLDWWXPTURGXODJ6JF6BVJSO4KWU45IFG"

    def setUp(self):
        seed_polaris_t2()
        tx_id = uuid.uuid4()
        self.withdrawal = _usdc_transaction(
            Transaction.KIND.withdrawal,
            self.CLIENT,
            id=tx_id,
            memo=memo_hash_for_uuid(tx_id),
            memo_type=Transaction.MEMO_TYPES.hash,
            amount_in=Decimal("3.0000000"),
            status=Transaction.STATUS.pending_anchor,
            stellar_transaction_id="first" + "0" * 59,
        )

    def _inbound(self, *, event_id: str, tx_hash: str, closed_minutes_ago: int = 0) -> LedgerEntry:
        return LedgerEntry.objects.create(
            stellar_tx_hash=tx_hash,
            amount_usdc=Decimal("3.0000000"),
            direction=LedgerEntry.Direction.INBOUND,
            event_id=event_id,
            event_type=LedgerEntry.EventType.TRANSFER,
            ledger=100,
            ledger_closed_at=timezone.now() - timezone.timedelta(minutes=closed_minutes_ago),
            from_address=self.CLIENT,
            to_address=self.HOT,
            counterparty=self.CLIENT,
            memo_raw=self.withdrawal.memo,
            memo_type="hash",
        )

    @patch("reconciliation.matching.fetch_client_by_stellar_key", return_value=None)
    def test_second_payment_with_the_same_memo_is_flagged(self, _kyc):
        first = self._inbound(event_id="evt-first", tx_hash=self.withdrawal.stellar_transaction_id)
        second = self._inbound(event_id="evt-second", tx_hash="second" + "0" * 58, closed_minutes_ago=60)

        with self.settings(RECON_MATCH_GRACE_SECONDS=0):
            match_pending()

        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.match_status, LedgerEntry.MatchStatus.MATCHED)
        self.assertEqual(first.polaris_transaction_id, self.withdrawal.id)
        self.assertEqual(second.match_status, LedgerEntry.MatchStatus.UNMATCHED)
        self.assertIsNone(second.polaris_transaction_id)
        self.assertTrue(
            Discrepancy.objects.filter(
                kind=Discrepancy.Kind.UNMATCHED_INBOUND, ledger_entry=second, resolved_at__isnull=True
            ).exists()
        )

    @patch("reconciliation.matching.fetch_client_by_stellar_key", return_value=None)
    def test_payment_seen_before_polaris_records_it_still_matches(self, _kyc):
        """Reconciliation may ingest the payment before the withdrawal worker; that is not a duplicate."""
        self.withdrawal.stellar_transaction_id = None
        self.withdrawal.save()
        entry = self._inbound(event_id="evt-early", tx_hash="early" + "0" * 59)
        match_pending()
        entry.refresh_from_db()
        self.assertEqual(entry.match_status, LedgerEntry.MatchStatus.MATCHED)
        self.assertEqual(entry.polaris_transaction_id, self.withdrawal.id)


@patch.object(polaris_settings, "LOCAL_MODE", False)
class TransactionPollAccessTests(TestCase):
    """
    ``/sep24/transaction/poll/`` returns the same data as ``GET /sep24/transaction``; knowing a
    transaction ID must not be enough to read it.
    """

    def setUp(self):
        seed_polaris_t2(distribution_seed=Keypair.random().secret)
        self.account = Keypair.random().public_key
        self.tx = _usdc_transaction(Transaction.KIND.deposit, self.account)
        self.url = f"/sep24/transaction/poll/?id={self.tx.id}"

    def _client_with_session(self, *, transactions: list[str], account: str) -> Client:
        client = Client()
        session = client.session
        session["transactions"] = transactions
        session["account"] = account
        session.save()
        return client

    def test_session_that_opened_the_transaction_can_poll(self):
        client = self._client_with_session(transactions=[str(self.tx.id)], account=self.account)
        response = client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], self.tx.status)

    def test_request_without_session_is_refused(self):
        response = Client().get(self.url)
        self.assertEqual(response.status_code, 403)
        self.assertNotIn("transaction", response.json())

    def test_session_of_another_transaction_is_refused(self):
        other = _usdc_transaction(Transaction.KIND.deposit, self.account)
        client = self._client_with_session(transactions=[str(other.id)], account=self.account)
        self.assertEqual(client.get(self.url).status_code, 403)

    def test_session_of_another_account_is_refused(self):
        client = self._client_with_session(
            transactions=[str(self.tx.id)], account=Keypair.random().public_key
        )
        self.assertEqual(client.get(self.url).status_code, 403)

    def test_unknown_id_is_refused_without_revealing_existence(self):
        response = Client().get(f"/sep24/transaction/poll/?id={uuid.uuid4()}")
        self.assertEqual(response.status_code, 403)


# ---------------------------------------------------------------------------------------------
# 4. Webhook replay
# ---------------------------------------------------------------------------------------------


@override_settings(LOCAL_MODE=True, VERSO_CORE_API_KEY="test-key", STORAGES=STATIC_STORAGE)
class KycCallbackReplayTests(TestCase):
    """
    The DIDIT return URL (``/sep24/kyc/callback/``) is a browser redirect anyone can replay or edit.
    It must never approve KYC by itself: approval only comes from VERSO Core for the session's user.
    """

    def setUp(self):
        seed_polaris_t2(distribution_seed=Keypair.random().secret)
        self.tx = _usdc_transaction(Transaction.KIND.deposit, Keypair.random().public_key)
        self.other_tx = _usdc_transaction(Transaction.KIND.deposit, Keypair.random().public_key)

    def _start_session(self, client: Client, tx: Transaction) -> None:
        session = client.session
        prefix = f"sep24:{tx.id}"
        session[f"{prefix}:verso_user_id"] = 42
        session[f"{prefix}:verso_email"] = "user@example.com"
        session[f"{prefix}:email_verified"] = True
        session[f"{prefix}:profile_completed"] = True
        session.save()

    def _approved(self, client: Client, tx: Transaction) -> bool:
        return bool(client.session.get(f"sep24:{tx.id}:kyc_approved"))

    def _status(self, completed: bool):
        from verso_integrations.core_client import UserStatusResult

        return UserStatusResult(user_id=42, email="user@example.com", kyc_completed=completed)

    def test_replayed_callback_without_session_is_rejected(self):
        response = Client().get(
            f"/sep24/kyc/callback/?transaction_id={self.tx.id}&status=Approved"
        )
        self.assertEqual(response.status_code, 400)

    @patch("verso_integrations.sep24.kyc_views.sync_kyc")
    @patch("verso_integrations.sep24.kyc_views.user_status")
    def test_forged_status_parameter_does_not_approve_kyc(self, mock_status, mock_sync):
        """``?status=Approved`` is ignored: Core still says pending, so KYC stays pending."""
        mock_sync.return_value = self._status(False)
        mock_status.return_value = self._status(False)
        client = Client()
        self._start_session(client, self.tx)

        response = client.get(
            f"/sep24/kyc/callback/?transaction_id={self.tx.id}&status=Approved&verificationSessionId=fake"
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(self._approved(client, self.tx))

    @patch("verso_integrations.sep24.kyc_views.sync_kyc")
    @patch("verso_integrations.sep24.kyc_views.user_status")
    def test_callback_for_another_transaction_is_rejected(self, mock_status, mock_sync):
        """A session started for one transaction cannot approve a different one."""
        mock_sync.return_value = self._status(True)
        mock_status.return_value = self._status(True)
        client = Client()
        self._start_session(client, self.tx)

        response = client.get(f"/sep24/kyc/callback/?transaction_id={self.other_tx.id}")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(self._approved(client, self.other_tx))

    @patch("verso_integrations.sep24.kyc_views.sync_kyc")
    @patch("verso_integrations.sep24.kyc_views.user_status")
    def test_replaying_an_approved_callback_is_harmless(self, mock_status, mock_sync):
        """Replaying a valid callback re-checks Core and leaves the same single approval."""
        mock_sync.return_value = self._status(True)
        mock_status.return_value = self._status(True)
        client = Client()
        self._start_session(client, self.tx)

        url = f"/sep24/kyc/callback/?transaction_id={self.tx.id}"
        first = client.get(url)
        second = client.get(url)
        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 302)
        self.assertTrue(self._approved(client, self.tx))
        self.assertEqual(mock_sync.call_count, 2)
        self.assertFalse(self._approved(client, self.other_tx))

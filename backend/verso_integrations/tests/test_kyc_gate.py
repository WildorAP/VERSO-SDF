from decimal import Decimal
from unittest.mock import patch

from django.contrib.sessions.backends.db import SessionStore
from django.test import Client, RequestFactory, TestCase, override_settings
from polaris.models import Asset, Transaction
from stellar_sdk import Keypair

from verso_integrations.core_client import CoreClientError, UserStatusResult
from verso_integrations.models import Sep24DepositMeta, Sep24WithdrawMeta
from verso_integrations.polaris_setup import (
    pen_asset_identification,
    seed_polaris_t2,
    usdc_asset_identification,
)
from verso_integrations.sep24.kyc_gate import (
    KYC_APPROVED,
    KYC_NOT_FOUND,
    KYC_PENDING,
    KYC_REJECTED,
    find_open_operation,
    get_pending_verso_user,
    lookup_client_kyc,
    mark_email_verified,
    mark_profile_completed,
    mark_session_kyc_approved,
    mark_wants_register,
    onboarding_step,
    parse_client_payload,
    set_pending_verso_user,
)


def _deposit_transaction(stellar_account: str) -> Transaction:
    asset = Asset.objects.get(code="USDC")
    return Transaction.objects.create(
        stellar_account=stellar_account,
        asset=asset,
        kind=Transaction.KIND.deposit,
        status=Transaction.STATUS.pending_user,
        protocol=Transaction.PROTOCOL.sep24,
    )


class ParseClientPayloadTests(TestCase):
    def test_reads_kyc_status_and_email(self):
        status = parse_client_payload(
            {
                "client_id": 42,
                "kyc_status": "approved",
                "email": "user@example.com",
                "didit_url": "https://didit.example/start",
            }
        )
        self.assertEqual(status.status, KYC_APPROVED)
        self.assertEqual(status.client_id, 42)
        self.assertEqual(status.email, "user@example.com")
        self.assertEqual(status.didit_url, "https://didit.example/start")


@override_settings(VERSO_MOCK_KYC="")
class LookupClientKycTests(TestCase):
    @override_settings(VERSO_MOCK_KYC="approved")
    def test_mock_setting_overrides_core(self):
        status = lookup_client_kyc("GMOCK")
        self.assertEqual(status.status, KYC_APPROVED)

    @patch("verso_integrations.sep24.kyc_gate.user_status")
    def test_core_not_completed(self, mock_status):
        mock_status.return_value = type(
            "Status",
            (),
            {"user_id": 1, "email": "user@example.com", "kyc_completed": False},
        )()
        request = RequestFactory().get("/")
        request.session = SessionStore()
        set_pending_verso_user(
            request,
            42,
            user_id=1,
            email="user@example.com",
        )
        status = lookup_client_kyc("GNOTFOUND", request=request, transaction_id=42)
        self.assertEqual(status.status, KYC_NOT_FOUND)

    @patch("verso_integrations.sep24.kyc_gate.user_status")
    def test_core_completed_marks_session(self, mock_status):
        mock_status.return_value = type(
            "Status",
            (),
            {"user_id": 1, "email": "user@example.com", "kyc_completed": True},
        )()
        request = RequestFactory().get("/")
        request.session = SessionStore()
        set_pending_verso_user(
            request,
            42,
            user_id=1,
            email="user@example.com",
        )
        status = lookup_client_kyc("GTEST", request=request, transaction_id=42)
        self.assertEqual(status.status, KYC_APPROVED)

    def test_session_override_wins(self):
        request = RequestFactory().get("/")
        request.session = SessionStore()
        mark_session_kyc_approved(request, 99)
        status = lookup_client_kyc("GPENDING", request=request, transaction_id=99)
        self.assertEqual(status.status, KYC_APPROVED)


class OnboardingStepTests(TestCase):
    def setUp(self):
        seed_polaris_t2(distribution_seed=Keypair.random().secret)
        self.request = RequestFactory().get("/")
        self.request.session = SessionStore()
        self.account = Keypair.random().public_key
        self.transaction = _deposit_transaction(self.account)

    @override_settings(VERSO_MOCK_KYC="approved")
    def test_approved_goes_to_deposit(self):
        self.assertEqual(onboarding_step(self.request, self.transaction), "deposit")

    @override_settings(VERSO_MOCK_KYC="not_found")
    def test_not_found_starts_login(self):
        self.assertEqual(onboarding_step(self.request, self.transaction), "login")

    @override_settings(VERSO_MOCK_KYC="not_found")
    def test_not_found_register_when_requested(self):
        mark_wants_register(self.request, self.transaction.id)
        self.assertEqual(onboarding_step(self.request, self.transaction), "register")

    @override_settings(VERSO_MOCK_KYC="not_found")
    def test_after_register_goes_to_verify_email(self):
        set_pending_verso_user(
            self.request,
            self.transaction.id,
            user_id=7,
            email="user@example.com",
        )
        self.assertEqual(onboarding_step(self.request, self.transaction), "verify_email")

    @override_settings(VERSO_MOCK_KYC="not_found")
    def test_after_verify_goes_to_profile(self):
        set_pending_verso_user(
            self.request,
            self.transaction.id,
            user_id=7,
            email="user@example.com",
        )
        mark_email_verified(self.request, self.transaction.id)
        self.assertEqual(onboarding_step(self.request, self.transaction), "profile")

    @override_settings(VERSO_MOCK_KYC="not_found")
    def test_after_profile_goes_to_didit(self):
        set_pending_verso_user(
            self.request,
            self.transaction.id,
            user_id=7,
            email="user@example.com",
        )
        mark_email_verified(self.request, self.transaction.id)
        mark_profile_completed(self.request, self.transaction.id)
        self.assertEqual(onboarding_step(self.request, self.transaction), "didit")

    @override_settings(VERSO_MOCK_KYC="pending")
    def test_pending_blocks(self):
        self.assertEqual(onboarding_step(self.request, self.transaction), "pending")

    @override_settings(VERSO_MOCK_KYC="rejected")
    def test_rejected_blocks(self):
        self.assertEqual(onboarding_step(self.request, self.transaction), "rejected")


def _wallet_status(account: str, **overrides) -> UserStatusResult:
    values = {
        "user_id": 7,
        "email": "user@example.com",
        "active": True,
        "email_verified": True,
        "kyc_completed": True,
        "profile_completed": True,
        "stellar_public_key": account,
        "stellar_linked": True,
    }
    values.update(overrides)
    return UserStatusResult(**values)


@override_settings(VERSO_MOCK_KYC="")
@patch("verso_integrations.sep24.kyc_gate.user_status")
class LinkedWalletSkipsLoginTests(TestCase):
    """A wallet already linked to a KYC-approved VERSO user goes straight to the amount step."""

    def setUp(self):
        seed_polaris_t2(distribution_seed=Keypair.random().secret)
        self.request = RequestFactory().get("/")
        self.request.session = SessionStore()
        self.account = Keypair.random().public_key
        self.transaction = _deposit_transaction(self.account)

    def test_linked_wallet_with_kyc_goes_to_amount(self, mock_status):
        mock_status.return_value = _wallet_status(self.account)
        self.assertEqual(onboarding_step(self.request, self.transaction), "deposit")
        mock_status.assert_called_once_with(stellar_public_key=self.account)
        self.assertEqual(
            get_pending_verso_user(self.request, self.transaction.id),
            (7, "user@example.com"),
        )

    def test_wallet_without_kyc_still_logs_in(self, mock_status):
        mock_status.return_value = _wallet_status(self.account, kyc_completed=False)
        self.assertEqual(onboarding_step(self.request, self.transaction), "login")

    def test_inactive_user_still_logs_in(self, mock_status):
        mock_status.return_value = _wallet_status(self.account, active=False)
        self.assertEqual(onboarding_step(self.request, self.transaction), "login")

    def test_user_linked_to_another_wallet_still_logs_in(self, mock_status):
        mock_status.return_value = _wallet_status(Keypair.random().public_key)
        self.assertEqual(onboarding_step(self.request, self.transaction), "login")

    def test_unlinked_wallet_or_core_error_still_logs_in(self, mock_status):
        mock_status.side_effect = CoreClientError("That user is not registered in VERSO.")
        self.assertEqual(onboarding_step(self.request, self.transaction), "login")


@override_settings(VERSO_MOCK_KYC="approved")
class OpenOperationTests(TestCase):
    """A wallet cannot start a new operation while a previous one is still in progress."""

    def setUp(self):
        seed_polaris_t2(distribution_seed=Keypair.random().secret)
        self.request = RequestFactory().get("/")
        self.request.session = SessionStore()
        self.account = Keypair.random().public_key

    def _previous_deposit(self, status, with_meta=True):
        tx = _deposit_transaction(self.account)
        tx.status = status
        tx.save(update_fields=["status"])
        if with_meta:
            Sep24DepositMeta.objects.create(
                transaction=tx,
                amount_pen=Decimal("10.00"),
                tipo_cambio=Decimal("3.4500"),
                amount_usdc=Decimal("2.8985507"),
                sell_asset=pen_asset_identification(),
                buy_asset=usdc_asset_identification(),
            )
        return tx

    def _previous_withdrawal(self, status):
        tx = _deposit_transaction(self.account)
        tx.kind = Transaction.KIND.withdrawal
        tx.status = status
        tx.save(update_fields=["kind", "status"])
        Sep24WithdrawMeta.objects.create(
            transaction=tx,
            fiat_currency="PEN",
            amount_usdc=Decimal("5.0000000"),
            amount_pen=Decimal("18.50"),
            tipo_cambio=Decimal("3.7000"),
            sell_asset=usdc_asset_identification(),
            buy_asset=pen_asset_identification(),
        )
        return tx

    def test_unfinished_deposit_blocks_a_new_operation(self):
        previous = self._previous_deposit(Transaction.STATUS.pending_user_transfer_start)
        new = _deposit_transaction(self.account)
        self.assertEqual(find_open_operation(new), previous)
        self.assertEqual(onboarding_step(self.request, new), "open_operation")

    def test_unfinished_withdrawal_blocks_a_new_deposit(self):
        self._previous_withdrawal(Transaction.STATUS.pending_external)
        self.assertEqual(
            onboarding_step(self.request, _deposit_transaction(self.account)), "open_operation"
        )

    def test_completed_or_error_does_not_block(self):
        self._previous_deposit(Transaction.STATUS.completed)
        self._previous_deposit(Transaction.STATUS.error)
        self._previous_withdrawal(Transaction.STATUS.completed)
        self.assertEqual(onboarding_step(self.request, _deposit_transaction(self.account)), "deposit")

    def test_abandoned_webview_without_amount_does_not_block(self):
        self._previous_deposit(Transaction.STATUS.incomplete, with_meta=False)
        self.assertEqual(onboarding_step(self.request, _deposit_transaction(self.account)), "deposit")

    def test_other_wallets_do_not_block(self):
        other = Keypair.random().public_key
        tx = _deposit_transaction(other)
        Sep24DepositMeta.objects.create(
            transaction=tx,
            amount_pen=Decimal("10.00"),
            tipo_cambio=Decimal("3.4500"),
            amount_usdc=Decimal("2.8985507"),
            sell_asset=pen_asset_identification(),
            buy_asset=usdc_asset_identification(),
        )
        self.assertEqual(onboarding_step(self.request, _deposit_transaction(self.account)), "deposit")

    def test_onboarding_page_explains_the_block(self):
        self._previous_deposit(Transaction.STATUS.pending_user_transfer_start)
        new = _deposit_transaction(self.account)
        response = Client().get(f"/sep24/onboarding/?transaction_id={new.id}")
        self.assertContains(response, "Tienes una operación en curso")


@override_settings(
    STORAGES={"staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}}
)
class NewOperationButtonTests(TestCase):
    """"Hacer otra operación" is offered only once the operation has finished (completed or error)."""

    def _render(self, template, status):
        from django.template.loader import render_to_string

        return render_to_string(template, {"show_rail": True, "transaction_status": status})

    def test_button_visible_only_when_finished(self):
        for template, operation in (
            ("sep24/onboarding/deposit_waiting.html", "deposit"),
            ("sep24/onboarding/withdraw_waiting.html", "withdraw"),
        ):
            for status, hidden in (("completed", False), ("error", False), ("pending_anchor", True)):
                with self.subTest(template=template, status=status):
                    html = self._render(template, status)
                    self.assertIn('id="new-operation-btn"', html)
                    self.assertIn(f'data-operation="{operation}"', html)
                    self.assertEqual('class="is-hidden"' in html, hidden)


@override_settings(
    ROOT_URLCONF="config.urls",
    STORAGES={"staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}},
)
class WithdrawMoreInfoScreenTests(TestCase):
    """Main popup screen of a withdrawal: wallet payment button while unpaid, new operation at the end."""

    def setUp(self):
        from django.utils import timezone

        seed_polaris_t2(distribution_seed=Keypair.random().secret)
        self.transaction = Transaction.objects.create(
            stellar_account=Keypair.random().public_key,
            asset=Asset.objects.get(code="USDC"),
            kind=Transaction.KIND.withdrawal,
            status=Transaction.STATUS.pending_user_transfer_start,
            protocol=Transaction.PROTOCOL.sep24,
            amount_in=Decimal("5"),
            receiving_anchor_account=Keypair.random().public_key,
            memo="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
            memo_type=Transaction.MEMO_TYPES.hash,
        )
        Sep24WithdrawMeta.objects.create(
            transaction=self.transaction,
            fiat_currency="PEN",
            amount_usdc=Decimal("5.0000000"),
            amount_pen=Decimal("18.50"),
            tipo_cambio=Decimal("3.7000"),
            sell_asset=usdc_asset_identification(),
            buy_asset=pen_asset_identification(),
            payout_confirmed_at=timezone.now(),
        )

    def _page(self, status, message=None):
        Transaction.objects.filter(pk=self.transaction.pk).update(status=status, status_message=message)
        response = Client().get("/sep24/transaction/more_info", {"id": str(self.transaction.id)})
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_unpaid_withdrawal_has_the_wallet_payment_button_hidden_until_the_opener_answers(self):
        html = self._page(Transaction.STATUS.pending_user_transfer_start)
        self.assertIn('id="wallet-pay-btn"', html)
        self.assertIn(
            f'id="wallet-pay" class="is-hidden" data-transaction-id="{self.transaction.id}"', html
        )
        self.assertNotIn('id="new-operation-btn"', html)
        self.assertGreater(html.index('id="wallet-pay-btn"'), html.rindex('class="field-value"'))
        account = self.transaction.receiving_anchor_account
        self.assertIn(f'data-copy-value="{account}"', html)
        self.assertIn(f"{account[:6]}…{account[-4:]}", html)

    def test_finished_withdrawal_offers_a_new_operation(self):
        for status in (Transaction.STATUS.completed, Transaction.STATUS.error):
            with self.subTest(status=status):
                html = self._page(status)
                self.assertIn('id="new-operation-btn"', html)
                self.assertIn('data-operation="withdraw"', html)
                self.assertNotIn('id="wallet-pay-btn"', html)
                self.assertGreater(html.index('id="new-operation-btn"'), html.rindex('class="field-value"'))

    def test_error_hides_the_internal_message_and_does_not_ask_for_usdc(self):
        html = self._page(Transaction.STATUS.error, message="detalle interno del error")
        self.assertNotIn("detalle interno del error", html)
        self.assertIn("Hubo un problema con tu retiro", html)
        self.assertNotIn("Envía USDC on-chain", html)

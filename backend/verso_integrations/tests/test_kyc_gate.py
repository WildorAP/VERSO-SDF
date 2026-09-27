from unittest.mock import patch

from django.contrib.sessions.backends.db import SessionStore
from django.test import Client, RequestFactory, TestCase, override_settings
from polaris.models import Asset, Transaction
from stellar_sdk import Keypair

from verso_integrations.polaris_setup import seed_polaris_t2
from verso_integrations.sep24.kyc_gate import (
    KYC_APPROVED,
    KYC_NOT_FOUND,
    KYC_PENDING,
    KYC_REJECTED,
    lookup_client_kyc,
    mark_email_verified,
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
    def test_after_verify_goes_to_didit(self):
        set_pending_verso_user(
            self.request,
            self.transaction.id,
            user_id=7,
            email="user@example.com",
        )
        mark_email_verified(self.request, self.transaction.id)
        self.assertEqual(onboarding_step(self.request, self.transaction), "didit")

    @override_settings(VERSO_MOCK_KYC="pending")
    def test_pending_blocks(self):
        self.assertEqual(onboarding_step(self.request, self.transaction), "pending")

    @override_settings(VERSO_MOCK_KYC="rejected")
    def test_rejected_blocks(self):
        self.assertEqual(onboarding_step(self.request, self.transaction), "rejected")

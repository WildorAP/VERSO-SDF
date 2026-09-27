from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from stellar_sdk import Keypair

from verso_integrations.deposit import compute_amount_usdc, get_cci_deposit_instructions
from verso_integrations.models import FiatDeposit


class CciDepositInstructionsTests(TestCase):
    @override_settings(
        VERSO_CCI_BANK_NAME="BCP",
        VERSO_CCI_ACCOUNT_NUMBER="002-PEN-CCI",
        VERSO_CCI_ACCOUNT_HOLDER="VERSO PEN",
        VERSO_USD_CCI_BANK_NAME="Interbank",
        VERSO_USD_CCI_ACCOUNT_NUMBER="003-USD-CCI",
        VERSO_USD_CCI_ACCOUNT_HOLDER="VERSO USD",
    )
    def test_pen_and_usd_use_separate_cci_accounts(self):
        pen = get_cci_deposit_instructions(
            100.0,
            "abc",
            tipo_cambio=3.75,
            amount_usdc=26.6666667,
            fiat_currency="PEN",
        )
        usd = get_cci_deposit_instructions(
            100.0,
            "abc",
            tipo_cambio=1.01,
            amount_usdc=99.009901,
            fiat_currency="USD",
        )
        self.assertEqual(pen["account_number"], "002-PEN-CCI")
        self.assertEqual(usd["account_number"], "003-USD-CCI")
        self.assertEqual(pen["fiat_currency"], "PEN")
        self.assertEqual(usd["fiat_currency"], "USD")
        self.assertNotIn("swift_code", pen)
        self.assertNotIn("routing_number", usd)


class FiatDepositModelTests(TestCase):
    def setUp(self):
        self.account = Keypair.random().public_key

    def test_create_deposit_defaults_to_pending(self):
        deposit = FiatDeposit.objects.create(
            stellar_account=self.account,
            amount_pen=Decimal("100.00"),
            tipo_cambio=Decimal("4.0000"),
        )
        self.assertEqual(deposit.status, FiatDeposit.Status.PENDING)
        self.assertEqual(deposit.stellar_tx_hash, "")
        self.assertEqual(deposit.amount_usdc, Decimal("25.0000000"))

    def test_amount_usdc_computed_from_pen_and_tipo_cambio(self):
        self.assertEqual(
            compute_amount_usdc(Decimal("100.00"), Decimal("3.7500")),
            Decimal("26.6666667"),
        )

    def test_invalid_stellar_account_rejected(self):
        deposit = FiatDeposit(
            stellar_account="not-a-valid-key",
            amount_pen=Decimal("10"),
            tipo_cambio=Decimal("3.75"),
        )
        with self.assertRaises(Exception):
            deposit.full_clean()


class FiatDepositAdminTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.admin_user = user_model.objects.create_superuser(
            username="admin",
            email="admin@test.local",
            password="testpass123",
        )
        self.client.login(username="admin", password="testpass123")
        self.account = Keypair.random().public_key
        self.deposit = FiatDeposit.objects.create(
            stellar_account=self.account,
            amount_pen=Decimal("50.00"),
            tipo_cambio=Decimal("4.0000"),
            bank_instructions={"bank_name": "BCP", "reference": "TXN-1"},
        )

    def test_mark_fiat_received_action(self):
        url = reverse("admin:verso_integrations_fiatdeposit_changelist")
        response = self.client.post(
            url,
            {
                "action": "mark_fiat_received",
                "_selected_action": [str(self.deposit.pk)],
            },
        )
        self.assertEqual(response.status_code, 302)
        self.deposit.refresh_from_db()
        self.assertEqual(self.deposit.status, FiatDeposit.Status.FIAT_CONFIRMED)

    @patch("verso_integrations.admin.send_usdc_on_chain", return_value="abc123hash")
    def test_disburse_usdc_action(self, mock_disburse):
        self.deposit.status = FiatDeposit.Status.FIAT_CONFIRMED
        self.deposit.save(update_fields=["status"])

        url = reverse("admin:verso_integrations_fiatdeposit_changelist")
        response = self.client.post(
            url,
            {
                "action": "disburse_usdc",
                "_selected_action": [str(self.deposit.pk)],
            },
        )
        self.assertEqual(response.status_code, 302)
        mock_disburse.assert_called_once_with(self.account, Decimal("12.5000000"))

        self.deposit.refresh_from_db()
        self.assertEqual(self.deposit.status, FiatDeposit.Status.DISBURSED)
        self.assertEqual(self.deposit.stellar_tx_hash, "abc123hash")
        self.assertIsNotNone(self.deposit.disbursed_at)

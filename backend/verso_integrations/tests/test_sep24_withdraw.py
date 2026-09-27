from decimal import Decimal
from io import BytesIO
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings
from polaris.models import Asset, DeliveryMethod, Quote, Transaction
from polaris.templates import Template
from stellar_sdk import Keypair

from verso_integrations.models import Sep24WithdrawMeta
from verso_integrations.polaris_setup import (
    pen_asset_identification,
    seed_polaris_t2,
    usd_asset_identification,
    usdc_asset_identification,
)
from verso_integrations.rails import VersoRailsIntegration
from verso_integrations.rates import FiatUsdcRate
from verso_integrations.sep24.forms import PayoutBankForm, UsdcWithdrawForm
from verso_integrations.sep24.withdraw_integration import VersoWithdrawIntegration
from verso_integrations.withdraw import compute_amount_fiat


def _create_withdraw_transaction(stellar_account: str) -> Transaction:
    asset = Asset.objects.get(code="USDC")
    return Transaction.objects.create(
        stellar_account=stellar_account,
        asset=asset,
        kind=Transaction.KIND.withdrawal,
        status=Transaction.STATUS.pending_user,
        protocol=Transaction.PROTOCOL.sep24,
    )


class ComputeAmountFiatTests(TestCase):
    def test_usdc_to_pen(self):
        amount = compute_amount_fiat(Decimal("10"), Decimal("3.7000"))
        self.assertEqual(amount, Decimal("37.00"))


@override_settings(VERSO_MOCK_KYC="approved")
class VersoWithdrawIntegrationTests(TestCase):
    def setUp(self):
        seed_polaris_t2(distribution_seed=Keypair.random().secret)
        self.integration = VersoWithdrawIntegration()
        self.account = Keypair.random().public_key
        self.transaction = _create_withdraw_transaction(self.account)
        self.request = MagicMock()
        self.request.GET = {}

    def test_form_for_transaction_returns_usdc_form(self):
        form = self.integration.form_for_transaction(self.request, self.transaction)
        self.assertIsInstance(form, UsdcWithdrawForm)

    @patch("verso_integrations.sep24.fiat.get_pen_usdc_rate")
    def test_after_usdc_form_creates_meta_and_quote(self, mock_rate):
        mock_rate.return_value = FiatUsdcRate(
            rate_venta=Decimal("3.7500"), rate_compra=Decimal("3.7000")
        )
        form = UsdcWithdrawForm({"amount_usdc": "10.0000000"}, fiat_currency="PEN")
        self.assertTrue(form.is_valid())

        self.integration.after_form_validation(self.request, form, self.transaction)

        meta = Sep24WithdrawMeta.objects.get(transaction=self.transaction)
        self.assertEqual(meta.amount_usdc, Decimal("10.0000000"))
        self.assertEqual(meta.amount_pen, Decimal("37.00"))
        self.assertEqual(meta.tipo_cambio, Decimal("3.7000"))
        self.assertEqual(meta.fiat_currency, "PEN")

        self.transaction.refresh_from_db()
        self.assertEqual(self.transaction.amount_in, Decimal("10.0000000"))
        self.assertEqual(self.transaction.amount_out, Decimal("37.00"))
        self.assertIsNotNone(self.transaction.quote_id)

    @patch("verso_integrations.sep24.fiat.get_pen_usdc_rate")
    def test_form_for_transaction_returns_payout_form_after_amount(self, mock_rate):
        mock_rate.return_value = FiatUsdcRate(
            rate_venta=Decimal("3.7500"), rate_compra=Decimal("3.7000")
        )
        form = UsdcWithdrawForm({"amount_usdc": "5.0000000"}, fiat_currency="PEN")
        self.assertTrue(form.is_valid())
        self.integration.after_form_validation(self.request, form, self.transaction)

        next_form = self.integration.form_for_transaction(self.request, self.transaction)
        self.assertIsInstance(next_form, PayoutBankForm)

    @patch("verso_integrations.sep24.fiat.get_usd_usdc_rate")
    def test_usd_withdraw_uses_usd_rate(self, mock_rate):
        mock_rate.return_value = FiatUsdcRate(
            rate_venta=Decimal("1.0010"), rate_compra=Decimal("0.9990")
        )
        self.request.GET = {"destination_asset": usd_asset_identification()}
        form = UsdcWithdrawForm({"amount_usdc": "100.0000000"}, fiat_currency="USD")
        self.assertTrue(form.is_valid())
        self.integration.after_form_validation(self.request, form, self.transaction)
        meta = Sep24WithdrawMeta.objects.get(transaction=self.transaction)
        self.assertEqual(meta.fiat_currency, "USD")
        self.assertEqual(meta.amount_pen, Decimal("99.90"))

    @patch("verso_integrations.sep24.fiat.get_pen_usdc_rate")
    def test_payout_form_completes_meta(self, mock_rate):
        mock_rate.return_value = FiatUsdcRate(
            rate_venta=Decimal("3.7500"), rate_compra=Decimal("3.7000")
        )
        amount_form = UsdcWithdrawForm({"amount_usdc": "2.0000000"}, fiat_currency="PEN")
        self.assertTrue(amount_form.is_valid())
        self.integration.after_form_validation(self.request, amount_form, self.transaction)

        payout_form = PayoutBankForm(
            {
                "bank_name": "BCP",
                "account_number": "0021001234567890123456",
                "account_holder": "Juan Pérez",
                "origen_fondos": "TRADING",
            }
        )
        self.assertTrue(payout_form.is_valid())
        self.integration.after_form_validation(self.request, payout_form, self.transaction)

        meta = Sep24WithdrawMeta.objects.get(transaction=self.transaction)
        self.assertEqual(meta.payout_bank_details["bank_name"], "BCP")
        self.assertEqual(meta.payout_bank_details["origen_fondos"], "TRADING")
        self.assertIsNotNone(meta.payout_confirmed_at)
        self.assertIsNone(
            self.integration.form_for_transaction(self.request, self.transaction)
        )

    def test_payout_form_requires_origen_fondos_otro_when_otro(self):
        form = PayoutBankForm(
            {
                "bank_name": "BCP",
                "account_number": "0021001234567890123456",
                "account_holder": "Juan Pérez",
                "origen_fondos": "OTRO",
            }
        )
        self.assertFalse(form.is_valid())


@override_settings(VERSO_MOCK_KYC="approved")
class VersoWithdrawRailsTests(TestCase):
    def setUp(self):
        seed_polaris_t2(distribution_seed=Keypair.random().secret)
        self.rails = VersoRailsIntegration()
        self.transaction = _create_withdraw_transaction(Keypair.random().public_key)
        self.meta = Sep24WithdrawMeta.objects.create(
            transaction=self.transaction,
            fiat_currency="PEN",
            amount_usdc=Decimal("10.0000000"),
            amount_pen=Decimal("37.00"),
            tipo_cambio=Decimal("3.7000"),
            sell_asset=usdc_asset_identification(),
            buy_asset=pen_asset_identification(),
            payout_bank_details={"account_number": "0021001234567890123456"},
        )

    def test_execute_outgoing_sets_pending_external(self):
        from django.utils import timezone

        self.meta.payout_confirmed_at = timezone.now()
        self.meta.save()
        self.transaction.amount_in = Decimal("10.0000000")
        self.transaction.status = Transaction.STATUS.pending_anchor
        self.transaction.save()

        self.rails.execute_outgoing_transaction(self.transaction)
        self.transaction.refresh_from_db()
        self.assertEqual(
            self.transaction.status, Transaction.STATUS.pending_external
        )
        self.assertEqual(self.transaction.amount_out, Decimal("37.00"))

    def test_poll_outgoing_completes_when_fiat_sent(self):
        from django.utils import timezone

        self.meta.fiat_sent_at = timezone.now()
        self.meta.save()
        self.transaction.status = Transaction.STATUS.pending_external
        self.transaction.save()

        complete = self.rails.poll_outgoing_transactions(
            Transaction.objects.filter(id=self.transaction.id)
        )
        self.assertEqual(len(complete), 1)
        self.transaction.refresh_from_db()
        self.assertEqual(self.transaction.status, Transaction.STATUS.completed)
        self.assertIsNotNone(self.transaction.completed_at)

    def test_mark_fiat_sent_completes_transaction(self):
        from django.utils import timezone

        self.meta.payout_confirmed_at = timezone.now()
        self.meta.save()
        self.transaction.amount_in = Decimal("10.0000000")
        self.transaction.status = Transaction.STATUS.pending_external
        self.transaction.save()

        self.meta.mark_fiat_sent()
        self.transaction.refresh_from_db()
        self.assertEqual(self.transaction.status, Transaction.STATUS.completed)
        self.assertIsNotNone(self.meta.fiat_sent_at)

from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings
from polaris.models import Asset, DeliveryMethod, Quote, Transaction
from polaris.templates import Template
from stellar_sdk import Keypair

from verso_integrations.models import Sep24DepositMeta
from verso_integrations.polaris_setup import (
    pen_asset_identification,
    seed_polaris_t2,
    usdc_asset_identification,
)
from verso_integrations.rails import VersoRailsIntegration
from verso_integrations.rates import FiatUsdcRate
from verso_integrations.sep24.forms import PenDepositForm
from verso_integrations.sep24.integration import VersoDepositIntegration


def _create_deposit_transaction(stellar_account: str) -> Transaction:
    asset = Asset.objects.get(code="USDC")
    return Transaction.objects.create(
        stellar_account=stellar_account,
        asset=asset,
        kind=Transaction.KIND.deposit,
        status=Transaction.STATUS.pending_user,
        protocol=Transaction.PROTOCOL.sep24,
    )


class PenDepositFormTests(TestCase):
    def test_valid_amount(self):
        form = PenDepositForm({"amount_pen": "150.50"})
        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data["amount_pen"], Decimal("150.50"))

    def test_rejects_amount_below_minimum(self):
        form = PenDepositForm({"amount_pen": "0.50"})
        self.assertFalse(form.is_valid())


@override_settings(VERSO_MOCK_AUTO_CONFIRM_FIAT=False)
class VersoDepositIntegrationTests(TestCase):
    def setUp(self):
        seed_polaris_t2(distribution_seed=Keypair.random().secret)
        self.integration = VersoDepositIntegration()
        self.account = Keypair.random().public_key
        self.transaction = _create_deposit_transaction(self.account)
        self.request = MagicMock()

    def test_form_for_transaction_returns_pen_form(self):
        form = self.integration.form_for_transaction(
            self.request,
            self.transaction,
        )
        self.assertIsInstance(form, PenDepositForm)

    def test_form_for_transaction_returns_none_after_meta_exists(self):
        Sep24DepositMeta.objects.create(
            transaction=self.transaction,
            amount_pen=Decimal("100.00"),
            tipo_cambio=Decimal("3.7500"),
            amount_usdc=Decimal("26.6666667"),
            sell_asset=pen_asset_identification(),
            buy_asset=usdc_asset_identification(),
        )
        form = self.integration.form_for_transaction(
            self.request,
            self.transaction,
        )
        self.assertIsNone(form)

    @patch("verso_integrations.sep24.integration.get_pen_usdc_rate")
    def test_after_form_validation_creates_quote_and_meta(self, mock_rate):
        mock_rate.return_value = FiatUsdcRate(tipo_cambio=Decimal("3.7500"))
        form = PenDepositForm({"amount_pen": "100.00"})
        self.assertTrue(form.is_valid())

        self.integration.after_form_validation(
            self.request,
            form,
            self.transaction,
        )

        self.transaction.refresh_from_db()
        meta = Sep24DepositMeta.objects.get(transaction=self.transaction)
        self.assertEqual(meta.amount_pen, Decimal("100.00"))
        self.assertEqual(meta.amount_usdc, Decimal("26.6666667"))
        self.assertEqual(meta.tipo_cambio, Decimal("3.7500"))
        self.assertIn("reference", meta.bank_instructions)
        self.assertIsNone(meta.fiat_confirmed_at)

        self.assertIsNotNone(self.transaction.quote_id)
        quote = Quote.objects.get(pk=self.transaction.quote_id)
        self.assertEqual(quote.sell_asset, pen_asset_identification())
        self.assertEqual(quote.buy_asset, usdc_asset_identification())
        self.assertEqual(quote.sell_amount, Decimal("100.00"))
        self.assertEqual(quote.buy_amount, Decimal("26.6666667"))
        self.assertEqual(
            quote.sell_delivery_method.name,
            DeliveryMethod.objects.get(
                name="bank_transfer_cci_cce",
                type=DeliveryMethod.TYPE.sell,
            ).name,
        )
        self.assertEqual(
            self.transaction.status,
            Transaction.STATUS.pending_user_transfer_start,
        )

    @patch("verso_integrations.sep24.integration.get_pen_usdc_rate")
    def test_content_for_template_more_info_includes_bank_guidance(self, mock_rate):
        mock_rate.return_value = FiatUsdcRate(tipo_cambio=Decimal("4.0000"))
        form = PenDepositForm({"amount_pen": "50.00"})
        self.assertTrue(form.is_valid())
        self.integration.after_form_validation(self.request, form, self.transaction)

        content = self.integration.content_for_template(
            self.request,
            Template.MORE_INFO,
            transaction=self.transaction,
        )
        self.assertIn("guidance", content)
        self.assertIn("bank_instructions", content)
        self.assertIn("50.0", content["guidance"])

    def test_content_for_template_deposit_returns_none_without_form(self):
        content = self.integration.content_for_template(
            self.request,
            Template.DEPOSIT,
            form=None,
            transaction=self.transaction,
        )
        self.assertIsNone(content)


@override_settings(VERSO_MOCK_AUTO_CONFIRM_FIAT=True)
class VersoDepositIntegrationAutoConfirmTests(TestCase):
    def setUp(self):
        seed_polaris_t2(distribution_seed=Keypair.random().secret)
        self.integration = VersoDepositIntegration()
        self.transaction = _create_deposit_transaction(Keypair.random().public_key)

    @patch("verso_integrations.sep24.integration.get_pen_usdc_rate")
    def test_auto_confirm_sets_fiat_confirmed_at(self, mock_rate):
        mock_rate.return_value = FiatUsdcRate(tipo_cambio=Decimal("3.7500"))
        form = PenDepositForm({"amount_pen": "10.00"})
        self.assertTrue(form.is_valid())
        self.integration.after_form_validation(MagicMock(), form, self.transaction)

        meta = Sep24DepositMeta.objects.get(transaction=self.transaction)
        self.assertIsNotNone(meta.fiat_confirmed_at)


class VersoRailsIntegrationTests(TestCase):
    def setUp(self):
        seed_polaris_t2(distribution_seed=Keypair.random().secret)
        self.rails = VersoRailsIntegration()
        self.transaction = _create_deposit_transaction(Keypair.random().public_key)

    def test_poll_pending_deposits_skips_unconfirmed_meta(self):
        Sep24DepositMeta.objects.create(
            transaction=self.transaction,
            amount_pen=Decimal("100.00"),
            tipo_cambio=Decimal("3.7500"),
            amount_usdc=Decimal("26.6666667"),
            sell_asset=pen_asset_identification(),
            buy_asset=usdc_asset_identification(),
        )
        ready = self.rails.poll_pending_deposits([self.transaction])
        self.assertEqual(ready, [])

    def test_poll_pending_deposits_returns_confirmed_transaction(self):
        meta = Sep24DepositMeta.objects.create(
            transaction=self.transaction,
            amount_pen=Decimal("100.00"),
            tipo_cambio=Decimal("3.7500"),
            amount_usdc=Decimal("26.6666667"),
            sell_asset=pen_asset_identification(),
            buy_asset=usdc_asset_identification(),
        )
        meta.mark_fiat_confirmed()

        ready = self.rails.poll_pending_deposits([self.transaction])
        self.assertEqual(len(ready), 1)
        self.assertEqual(ready[0].pk, self.transaction.pk)
        self.assertEqual(ready[0].amount_in, Decimal("100.00"))
        self.assertEqual(ready[0].amount_out, Decimal("26.6666667"))

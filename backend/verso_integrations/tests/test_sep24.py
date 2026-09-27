from decimal import Decimal
from io import BytesIO
from unittest.mock import MagicMock, patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from polaris.models import Asset, DeliveryMethod, Quote, Transaction
from polaris.templates import Template
from stellar_sdk import Keypair

from verso_integrations.models import Sep24DepositMeta
from verso_integrations.polaris_setup import (
    pen_asset_identification,
    seed_polaris_t2,
    usd_asset_identification,
    usdc_asset_identification,
)
from verso_integrations.rails import VersoRailsIntegration
from verso_integrations.rates import FiatUsdcRate
from verso_integrations.sep24.forms import BankTransferReceiptForm, FiatDepositForm, PenDepositForm
from verso_integrations.sep24.integration import VersoDepositIntegration
from verso_integrations.sep24.onboarding_forms import VersoLoginForm, VersoRegisterForm


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
        self.assertEqual(form.cleaned_data["amount_fiat"], Decimal("150.50"))

    def test_rejects_amount_below_minimum(self):
        form = PenDepositForm({"amount_pen": "0.50"})
        self.assertFalse(form.is_valid())


@override_settings(VERSO_MOCK_AUTO_CONFIRM_FIAT=False, VERSO_MOCK_KYC="approved")
class VersoDepositIntegrationTests(TestCase):
    def setUp(self):
        seed_polaris_t2(distribution_seed=Keypair.random().secret)
        self.integration = VersoDepositIntegration()
        self.account = Keypair.random().public_key
        self.transaction = _create_deposit_transaction(self.account)
        self.request = MagicMock()
        self.request.GET = {}

    def test_form_for_transaction_returns_fiat_form(self):
        form = self.integration.form_for_transaction(
            self.request,
            self.transaction,
        )
        self.assertIsInstance(form, FiatDepositForm)

    def test_form_for_transaction_returns_receipt_form_after_amount_step(self):
        Sep24DepositMeta.objects.create(
            transaction=self.transaction,
            amount_pen=Decimal("100.00"),
            tipo_cambio=Decimal("3.7500"),
            amount_usdc=Decimal("26.6666667"),
            sell_asset=pen_asset_identification(),
            buy_asset=usdc_asset_identification(),
            bank_instructions={"account_number": "CCI-123", "reference": "TXN-1"},
        )
        form = self.integration.form_for_transaction(
            self.request,
            self.transaction,
        )
        self.assertIsInstance(form, BankTransferReceiptForm)

    def test_form_for_transaction_returns_none_after_transfer_confirmed(self):
        meta = Sep24DepositMeta.objects.create(
            transaction=self.transaction,
            amount_pen=Decimal("100.00"),
            tipo_cambio=Decimal("3.7500"),
            amount_usdc=Decimal("26.6666667"),
            sell_asset=pen_asset_identification(),
            buy_asset=usdc_asset_identification(),
        )
        from django.utils import timezone

        meta.transfer_declared_at = timezone.now()
        meta.save(update_fields=["transfer_declared_at"])
        form = self.integration.form_for_transaction(
            self.request,
            self.transaction,
        )
        self.assertIsNone(form)

    @patch("verso_integrations.sep24.fiat.get_pen_usdc_rate")
    def test_after_form_validation_creates_quote_and_meta(self, mock_rate):
        mock_rate.return_value = FiatUsdcRate(
            rate_venta=Decimal("3.7500"), rate_compra=Decimal("3.7000")
        )
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

    @patch("verso_integrations.sep24.fiat.get_pen_usdc_rate")
    def test_content_for_template_more_info_includes_bank_guidance(self, mock_rate):
        mock_rate.return_value = FiatUsdcRate(
            rate_venta=Decimal("4.0000"), rate_compra=Decimal("3.9500")
        )
        form = PenDepositForm({"amount_pen": "50.00"})
        self.assertTrue(form.is_valid())
        self.integration.after_form_validation(self.request, form, self.transaction)
        meta = Sep24DepositMeta.objects.get(transaction=self.transaction)
        from django.utils import timezone

        meta.transfer_declared_at = timezone.now()
        meta.save(update_fields=["transfer_declared_at"])

        content = self.integration.content_for_template(
            self.request,
            Template.MORE_INFO,
            transaction=self.transaction,
        )
        self.assertEqual(content["template_name"], "sep24/onboarding/deposit_waiting.html")
        self.assertIn("bank_instructions", content)
        self.assertIn("poll_url", content)
        self.assertIn("50.0", str(content["amount_pen"]))

    def test_content_for_template_deposit_returns_none_without_form(self):
        content = self.integration.content_for_template(
            self.request,
            Template.DEPOSIT,
            form=None,
            transaction=self.transaction,
        )
        self.assertIsNone(content)

    def test_content_for_template_deposit_waiting_after_transfer_without_form(self):
        from django.utils import timezone

        Sep24DepositMeta.objects.create(
            transaction=self.transaction,
            amount_pen=Decimal("100.00"),
            tipo_cambio=Decimal("3.7500"),
            amount_usdc=Decimal("26.6666667"),
            sell_asset=pen_asset_identification(),
            buy_asset=usdc_asset_identification(),
            bank_instructions={"account_number": "CCI-123", "reference": "TXN-1"},
            transfer_declared_at=timezone.now(),
        )
        content = self.integration.content_for_template(
            self.request,
            Template.DEPOSIT,
            form=None,
            transaction=self.transaction,
        )
        self.assertEqual(content["template_name"], "sep24/onboarding/deposit_waiting.html")

    def test_form_for_transaction_accepts_duplicate_post_after_transfer(self):
        from django.utils import timezone

        Sep24DepositMeta.objects.create(
            transaction=self.transaction,
            amount_pen=Decimal("100.00"),
            tipo_cambio=Decimal("3.7500"),
            amount_usdc=Decimal("26.6666667"),
            sell_asset=pen_asset_identification(),
            buy_asset=usdc_asset_identification(),
            transfer_declared_at=timezone.now(),
        )
        from verso_integrations.sep24.forms import TransferAlreadyDeclaredForm

        form = self.integration.form_for_transaction(
            self.request,
            self.transaction,
            post_data={"acknowledge": ""},
        )
        self.assertIsInstance(form, TransferAlreadyDeclaredForm)

    @patch("verso_integrations.sep24.fiat.get_pen_usdc_rate")
    def test_content_for_template_deposit_includes_rate_and_custom_template(self, mock_rate):
        mock_rate.return_value = FiatUsdcRate(
            rate_venta=Decimal("3.8100"), rate_compra=Decimal("3.7900")
        )
        form = FiatDepositForm()
        content = self.integration.content_for_template(
            self.request,
            Template.DEPOSIT,
            form=form,
            transaction=self.transaction,
        )
        self.assertEqual(content["template_name"], "sep24/onboarding/deposit_amount.html")
        self.assertEqual(content["rate_venta"], "3.8100")
        self.assertEqual(content["rate_venta_display"], "3.8100")
        self.assertEqual(content["fiat_currency"], "PEN")
        self.assertFalse(content["show_timeline"])
        self.assertTrue(content["show_rail"])
        self.assertFalse(content["rate_unavailable"])

    @override_settings(
        STORAGES={
            "default": {
                "BACKEND": "django.core.files.storage.FileSystemStorage",
            },
            "staticfiles": {
                "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
            },
        }
    )
    @patch("verso_integrations.sep24.fiat.get_pen_usdc_rate")
    def test_deposit_amount_template_renders_quote_panel(self, mock_rate):
        from django.template.loader import render_to_string

        mock_rate.return_value = FiatUsdcRate(
            rate_venta=Decimal("3.7500"), rate_compra=Decimal("3.7000")
        )
        form = FiatDepositForm()
        content = self.integration.content_for_template(
            self.request,
            Template.DEPOSIT,
            form=form,
            transaction=self.transaction,
        )
        html = render_to_string(
            content["template_name"],
            {
                **content,
                "form": form,
                "post_url": "/sep24/transactions/deposit/webapp/submit/?transaction_id=tx1",
            },
        )
        self.assertIn("Tipo de cambio vigente", html)
        self.assertIn("S/ 3.7500 por 1 USDC", html)
        self.assertIn("Enviarás", html)
        self.assertIn("Recibirás", html)
        self.assertIn("Enviar operación", html)
        self.assertIn("deposit_quote.js", html)
        self.assertIn("Soles (PEN)", html)
        self.assertIn("Dólares (USD)", html)

    @patch("verso_integrations.sep24.fiat.get_usd_usdc_rate")
    def test_usd_deposit_creates_cci_meta(self, mock_rate):
        mock_rate.return_value = FiatUsdcRate(
            rate_venta=Decimal("1.0100"), rate_compra=Decimal("0.9900")
        )
        self.request.GET = {"source_asset": "iso4217:USD"}
        form = FiatDepositForm({"amount_fiat": "100.00"}, fiat_currency="USD")
        self.assertTrue(form.is_valid())
        self.integration.after_form_validation(self.request, form, self.transaction)

        meta = Sep24DepositMeta.objects.get(transaction=self.transaction)
        self.assertEqual(meta.fiat_currency, "USD")
        self.assertEqual(meta.amount_pen, Decimal("100.00"))
        self.assertEqual(meta.sell_asset, usd_asset_identification())
        self.assertEqual(meta.bank_instructions["fiat_currency"], "USD")
        self.assertNotIn("routing_number", meta.bank_instructions)
        self.assertNotIn("swift_code", meta.bank_instructions)

    @override_settings(
        STORAGES={
            "default": {
                "BACKEND": "django.core.files.storage.FileSystemStorage",
            },
            "staticfiles": {
                "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
            },
        }
    )
    @patch("verso_integrations.sep24.fiat.get_usd_usdc_rate")
    def test_usd_transfer_template_renders_cci_fields(self, mock_rate):
        from django.template.loader import render_to_string

        mock_rate.return_value = FiatUsdcRate(
            rate_venta=Decimal("1.0100"), rate_compra=Decimal("0.9900")
        )
        self.request.GET = {"source_asset": "iso4217:USD"}
        form = FiatDepositForm({"amount_fiat": "100.00"}, fiat_currency="USD")
        self.assertTrue(form.is_valid())
        self.integration.after_form_validation(self.request, form, self.transaction)
        meta = Sep24DepositMeta.objects.get(transaction=self.transaction)
        receipt_form = BankTransferReceiptForm()
        content = self.integration.content_for_template(
            self.request,
            Template.DEPOSIT,
            form=receipt_form,
            transaction=self.transaction,
        )
        html = render_to_string(
            content["template_name"],
            {
                **content,
                "form": receipt_form,
                "post_url": "/sep24/transactions/deposit/webapp/submit/",
            },
        )
        self.assertIn("Copiar CCI", html)
        self.assertIn("Copiar referencia", html)
        self.assertIn("CCI/CCE", html)

    def test_bank_transfer_receipt_form_requires_file(self):
        form = BankTransferReceiptForm({})
        self.assertFalse(form.is_valid())
        self.assertIn("receipt", form.errors)

    @patch("verso_integrations.sep24.fiat.get_pen_usdc_rate")
    def test_bank_transfer_confirmation_marks_declared(self, mock_rate):
        mock_rate.return_value = FiatUsdcRate(
            rate_venta=Decimal("3.7500"), rate_compra=Decimal("3.7000")
        )
        form = PenDepositForm({"amount_pen": "25.00"})
        self.assertTrue(form.is_valid())
        self.integration.after_form_validation(self.request, form, self.transaction)

        receipt = SimpleUploadedFile(
            "voucher.pdf",
            b"%PDF-1.4 test receipt",
            content_type="application/pdf",
        )
        receipt_form = BankTransferReceiptForm({}, {"receipt": receipt})
        self.assertTrue(receipt_form.is_valid())
        self.integration.after_form_validation(self.request, receipt_form, self.transaction)

        meta = Sep24DepositMeta.objects.get(transaction=self.transaction)
        self.assertIsNotNone(meta.transfer_declared_at)
        self.assertTrue(meta.transfer_receipt)


@override_settings(VERSO_MOCK_KYC="not_found")
class VersoDepositOnboardingGateTests(TestCase):
    def setUp(self):
        seed_polaris_t2(distribution_seed=Keypair.random().secret)
        self.integration = VersoDepositIntegration()
        self.request = MagicMock()
        self.request.session = {}
        self.transaction = _create_deposit_transaction(Keypair.random().public_key)

    def test_form_for_transaction_returns_login_when_not_found(self):
        form = self.integration.form_for_transaction(
            self.request,
            self.transaction,
        )
        self.assertIsInstance(form, VersoLoginForm)

    @override_settings(VERSO_MOCK_KYC="not_found")
    def test_form_for_transaction_returns_register_when_requested(self):
        from verso_integrations.sep24.kyc_gate import mark_wants_register

        mark_wants_register(self.request, self.transaction.id)
        form = self.integration.form_for_transaction(
            self.request,
            self.transaction,
        )
        self.assertIsInstance(form, VersoRegisterForm)


@override_settings(VERSO_MOCK_AUTO_CONFIRM_FIAT=True, VERSO_MOCK_KYC="approved")
class VersoDepositIntegrationAutoConfirmTests(TestCase):
    def setUp(self):
        seed_polaris_t2(distribution_seed=Keypair.random().secret)
        self.integration = VersoDepositIntegration()
        self.transaction = _create_deposit_transaction(Keypair.random().public_key)

    @patch("verso_integrations.sep24.fiat.get_pen_usdc_rate")
    def test_auto_confirm_sets_fiat_confirmed_at(self, mock_rate):
        mock_rate.return_value = FiatUsdcRate(
            rate_venta=Decimal("3.7500"), rate_compra=Decimal("3.7000")
        )
        form = PenDepositForm({"amount_pen": "10.00"})
        self.assertTrue(form.is_valid())
        request = MagicMock()
        request.GET = {}
        self.integration.after_form_validation(request, form, self.transaction)

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

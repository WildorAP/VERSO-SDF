from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.test import Client, TestCase, override_settings
from django.urls import reverse
from polaris.models import Asset, Transaction
from polaris.templates import Template
from stellar_sdk import Keypair

from verso_integrations.models import Sep24DepositMeta
from verso_integrations.polaris_setup import seed_polaris_t2
from verso_integrations.sep24.integration import VersoDepositIntegration


def _create_deposit_transaction(stellar_account: str) -> Transaction:
    asset = Asset.objects.get(code="USDC")
    return Transaction.objects.create(
        stellar_account=stellar_account,
        asset=asset,
        kind=Transaction.KIND.deposit,
        status=Transaction.STATUS.pending_user_transfer_start,
        protocol=Transaction.PROTOCOL.sep24,
        amount_in=Decimal("12.00"),
        amount_out=Decimal("3.5555556"),
    )


@override_settings(VERSO_MOCK_KYC="approved")
class TransactionPollViewTests(TestCase):
    def setUp(self):
        seed_polaris_t2(distribution_seed=Keypair.random().secret)
        self.client = Client()
        self.transaction = _create_deposit_transaction(Keypair.random().public_key)

    def test_transaction_poll_returns_live_status(self):
        response = self.client.get(
            reverse("sep24_transaction_poll"),
            {"id": str(self.transaction.id)},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "pending_user_transfer_start")
        self.assertFalse(payload["terminal"])
        self.assertEqual(payload["transaction"]["id"], str(self.transaction.id))

    def test_transaction_poll_marks_completed_as_terminal(self):
        self.transaction.status = Transaction.STATUS.completed
        self.transaction.save(update_fields=["status"])

        response = self.client.get(
            reverse("sep24_transaction_poll"),
            {"id": str(self.transaction.id)},
        )
        payload = response.json()
        self.assertEqual(payload["status"], "completed")
        self.assertTrue(payload["terminal"])

    def test_transaction_poll_requires_id(self):
        response = self.client.get(reverse("sep24_transaction_poll"))
        self.assertEqual(response.status_code, 400)


@override_settings(
    VERSO_MOCK_KYC="approved",
    STORAGES={
        "default": {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
        },
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
        }
    },
)
class MoreInfoAutoRefreshTests(TestCase):
    def setUp(self):
        seed_polaris_t2(distribution_seed=Keypair.random().secret)
        self.client = Client()
        self.integration = VersoDepositIntegration()
        self.request = MagicMock()
        self.request.build_absolute_uri.side_effect = lambda path: f"http://testserver{path}"
        self.transaction = _create_deposit_transaction(Keypair.random().public_key)

    @patch("verso_integrations.sep24.integration.get_pen_usdc_rate")
    def test_more_info_context_includes_poll_url_and_template(self, mock_rate):
        from verso_integrations.rates import FiatUsdcRate
        from verso_integrations.sep24.forms import PenDepositForm

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
        self.assertIn("/sep24/transaction/poll/", content["poll_url"])
        self.assertIn(str(self.transaction.id), content["poll_url"])
        self.assertTrue(content["poll_enabled"])
        self.assertEqual(content["transaction_status"], "pending_user_transfer_start")

    @patch("verso_integrations.sep24.integration.get_pen_usdc_rate")
    def test_deposit_waiting_skips_poll_when_completed(self, mock_rate):
        from verso_integrations.rates import FiatUsdcRate
        from verso_integrations.sep24.forms import PenDepositForm

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
        self.transaction.status = Transaction.STATUS.completed
        self.transaction.stellar_transaction_id = "abc123stellarhash"
        self.transaction.save(update_fields=["status", "stellar_transaction_id"])

        content = self.integration.content_for_template(
            self.request,
            Template.MORE_INFO,
            transaction=self.transaction,
        )
        self.assertFalse(content["poll_enabled"])
        self.assertEqual(content["transaction_status"], "completed")
        self.assertEqual(content["status_message"], "Operación finalizada")
        self.assertEqual(content["stellar_transaction_id"], "abc123stellarhash")

    @patch("verso_integrations.sep24.integration.get_pen_usdc_rate")
    def test_deposit_waiting_completed_template_has_no_poll_script(self, mock_rate):
        from django.template.loader import render_to_string
        from verso_integrations.rates import FiatUsdcRate
        from verso_integrations.sep24.forms import PenDepositForm

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
        self.transaction.status = Transaction.STATUS.completed
        self.transaction.save(update_fields=["status"])

        content = self.integration.content_for_template(
            self.request,
            Template.MORE_INFO,
            transaction=self.transaction,
        )
        html = render_to_string(content["template_name"], content)
        self.assertIn("Operación finalizada", html)
        self.assertNotIn("deposit_wait_poll.js", html)
        self.assertNotIn("deposit-wait-poll-root", html)

    @patch("verso_integrations.sep24.integration.get_pen_usdc_rate")
    def test_transfer_template_renders_cci_copy_fields(self, mock_rate):
        from django.template.loader import render_to_string
        from verso_integrations.models import Sep24DepositMeta
        from verso_integrations.polaris_setup import pen_asset_identification, usdc_asset_identification
        from verso_integrations.rates import FiatUsdcRate
        from verso_integrations.sep24.forms import BankTransferReceiptForm

        mock_rate.return_value = FiatUsdcRate(
            rate_venta=Decimal("3.7500"), rate_compra=Decimal("3.7000")
        )
        Sep24DepositMeta.objects.create(
            transaction=self.transaction,
            amount_pen=Decimal("50.00"),
            tipo_cambio=Decimal("3.7500"),
            amount_usdc=Decimal("13.3333333"),
            sell_asset=pen_asset_identification(),
            buy_asset=usdc_asset_identification(),
            bank_instructions={
                "bank_name": "BCP",
                "account_holder": "VERSO PERU",
                "account_number": "00212345678901234567890",
                "reference": f"TXN-{self.transaction.id}",
            },
        )
        form = BankTransferReceiptForm()
        content = self.integration.content_for_template(
            self.request,
            Template.DEPOSIT,
            form=form,
            transaction=self.transaction,
        )
        html = render_to_string(
            content["template_name"],
            {**content, "form": form, "post_url": "/sep24/transactions/deposit/webapp/submit/"},
        )
        self.assertIn("00212345678901234567890", html)
        self.assertIn("Copiar CCI", html)
        self.assertIn("Confirmar transferencia", html)

    def test_more_info_page_includes_poll_script_for_pending_transaction(self):
        response = self.client.get(
            "/sep24/transaction/more_info",
            {"id": str(self.transaction.id)},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "verso-tx-poll-root")
        self.assertContains(response, "more_info_poll.js")
        self.assertContains(response, reverse("sep24_transaction_poll"))

    def test_more_info_page_skips_poll_for_completed_transaction(self):
        self.transaction.status = Transaction.STATUS.completed
        self.transaction.save(update_fields=["status"])

        response = self.client.get(
            "/sep24/transaction/more_info",
            {"id": str(self.transaction.id)},
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "verso-tx-poll-root")

from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.test import Client, TestCase, override_settings
from django.urls import reverse
from polaris.models import Asset, Transaction
from polaris.templates import Template
from stellar_sdk import Keypair

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

        content = self.integration.content_for_template(
            self.request,
            Template.MORE_INFO,
            transaction=self.transaction,
        )
        self.assertEqual(content["template_name"], "polaris/more_info_verso.html")
        self.assertIn("/sep24/transaction/poll/", content["poll_url"])
        self.assertIn(str(self.transaction.id), content["poll_url"])

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

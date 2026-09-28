from django.test import RequestFactory, TestCase, override_settings
from polaris.models import Asset, Transaction
from stellar_sdk import Keypair

from verso_integrations.polaris_setup import seed_polaris_t2
from verso_integrations.sep24.wallet_callbacks import (
    append_wallet_callbacks_to_url,
    persist_sep24_wallet_callbacks,
    sep24_wallet_callback_query,
)


class WalletCallbackPersistenceTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.transaction_id = "a220ce50-f7d3-4f51-9717-cbb8bda56f0b"

    def test_persist_and_restore_callbacks_from_session(self):
        request = self.factory.get(
            "/sep24/transactions/withdraw/webapp",
            {
                "callback": "postMessage",
                "on_change_callback": "postMessage",
                "transaction_id": self.transaction_id,
            },
        )
        request.session = self.client.session
        persist_sep24_wallet_callbacks(request, self.transaction_id)

        follow_up = self.factory.get("/sep24/onboarding/")
        follow_up.session = request.session
        params = sep24_wallet_callback_query(follow_up, self.transaction_id)
        self.assertEqual(params["callback"], "postMessage")
        self.assertEqual(params["on_change_callback"], "postMessage")

    def test_append_wallet_callbacks_to_url(self):
        request = self.factory.get("/sep24/onboarding/")
        request.session = self.client.session
        request.session[f"sep24_wallet_callback:{self.transaction_id}"] = "postMessage"
        request.session.modified = True

        query = append_wallet_callbacks_to_url(
            request,
            self.transaction_id,
            {"transaction_id": self.transaction_id},
        )
        self.assertIn("callback=postMessage", query)
        self.assertIn(f"transaction_id={self.transaction_id}", query)


@override_settings(VERSO_MOCK_KYC="approved")
class WalletCallbackTransactionSyncTests(TestCase):
    def setUp(self):
        seed_polaris_t2(distribution_seed=Keypair.random().secret)
        asset = Asset.objects.get(code="USDC")
        self.transaction = Transaction.objects.create(
            stellar_account=Keypair.random().public_key,
            asset=asset,
            kind=Transaction.KIND.withdrawal,
            status=Transaction.STATUS.incomplete,
            protocol=Transaction.PROTOCOL.sep24,
        )

    def test_persist_saves_on_change_callback_on_transaction(self):
        request = RequestFactory().get(
            "/sep24/transactions/withdraw/webapp",
            {
                "callback": "postMessage",
                "on_change_callback": "postMessage",
                "transaction_id": str(self.transaction.id),
            },
        )
        request.session = self.client.session
        persist_sep24_wallet_callbacks(request, str(self.transaction.id))

        self.transaction.refresh_from_db()
        self.assertEqual(self.transaction.on_change_callback, "postMessage")

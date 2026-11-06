from unittest.mock import patch

from django.test import Client, RequestFactory, TestCase, override_settings
from polaris.models import Asset, Transaction
from stellar_sdk import Keypair

from verso_integrations.polaris_setup import seed_polaris_t2
from verso_integrations.sep24.wallet_callbacks import (
    append_wallet_callbacks_to_url,
    is_safe_wallet_callback,
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


@override_settings(
    ROOT_URLCONF="config.urls",
    STORAGES={"staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}},
)
class MoreInfoCallbackSafetyTests(TestCase):
    """more_info is reachable with only the transaction ID: its query must not reach the script or the database."""

    UNSAFE_CALLBACK = "https://example.com/x'});window.marker=1;({a:'"

    def setUp(self):
        seed_polaris_t2(distribution_seed=Keypair.random().secret)
        self.transaction = Transaction.objects.create(
            stellar_account=Keypair.random().public_key,
            asset=Asset.objects.get(code="USDC"),
            kind=Transaction.KIND.deposit,
            status=Transaction.STATUS.pending_user_transfer_start,
            protocol=Transaction.PROTOCOL.sep24,
        )

    def _get(self, **params):
        response = Client().get(
            "/sep24/transaction/more_info", {"id": str(self.transaction.id), **params}
        )
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_unsafe_callback_never_reaches_the_page(self):
        for value in (self.UNSAFE_CALLBACK, "</script><b>x</b>", "x'", "javascript:alert"):
            with self.subTest(value=value):
                html = self._get(callback=value)
                self.assertNotIn(value, html)
                self.assertIn("callback: ''", html)

    def test_any_unsafe_repeated_callback_drops_the_param(self):
        response = Client().get(
            f"/sep24/transaction/more_info?id={self.transaction.id}"
            "&callback=postMessage&callback=x%27"
        )
        self.assertNotIn("callback: 'postMessage'", response.content.decode())

    def test_safe_callbacks_are_kept(self):
        self.assertIn("callback: 'postMessage'", self._get(callback="postMessage"))
        url = "https://wallet.example.com/cb?a=1"
        self.assertIn(f"callback: '{url}'", self._get(callback=url))

    @patch("polaris.settings.LOCAL_MODE", False)
    def test_plain_http_callback_is_rejected_outside_local_mode(self):
        self.assertFalse(is_safe_wallet_callback("http://wallet.example.com/cb"))
        self.assertTrue(is_safe_wallet_callback("https://wallet.example.com/cb"))

    def test_query_cannot_set_a_callback_url_on_the_transaction(self):
        self._get(on_change_callback="https://attacker.example.com/hook")
        self.transaction.refresh_from_db()
        self.assertFalse(self.transaction.on_change_callback)

    def test_query_cannot_replace_the_callback_of_the_transaction(self):
        original = "https://wallet.example.com/hook"
        Transaction.objects.filter(pk=self.transaction.pk).update(on_change_callback=original)
        for params in (
            {"on_change_callback": "https://attacker.example.com/hook"},
            {"on_change_callback": "postMessage"},
            {"callback": "postMessage"},
        ):
            with self.subTest(params=params):
                self._get(**params)
                self.transaction.refresh_from_db()
                self.assertEqual(self.transaction.on_change_callback, original)

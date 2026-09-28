from decimal import Decimal
from urllib.parse import parse_qs, urlparse

from django.contrib.sessions.middleware import SessionMiddleware
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from polaris.models import Asset, Transaction
from stellar_sdk import Keypair

from verso_integrations.models import Sep24WithdrawMeta
from verso_integrations.polaris_setup import (
    pen_asset_identification,
    seed_polaris_t2,
    usdc_asset_identification,
)
from verso_integrations.sep24.onboarding_middleware import Sep24OnboardingMiddleware


def _noop(get_response):
    return get_response


def _request_with_session(method, path, data=None, session=None):
    factory = RequestFactory()
    request = getattr(factory, method.lower())(path, data or {})
    middleware = SessionMiddleware(_noop)
    middleware.process_request(request)
    if session is not None:
        request.session = session
        if hasattr(request.session, "save"):
            request.session.save = lambda *args, **kwargs: None
    return request


@override_settings(VERSO_MOCK_KYC="approved")
class WithdrawMiddlewareCallbackTests(TestCase):
    def setUp(self):
        seed_polaris_t2(distribution_seed=Keypair.random().secret)
        self.client = Client()
        self.asset = Asset.objects.get(code="USDC")
        self.transaction = Transaction.objects.create(
            stellar_account=Keypair.random().public_key,
            asset=self.asset,
            kind=Transaction.KIND.withdrawal,
            status=Transaction.STATUS.pending_user_transfer_start,
            protocol=Transaction.PROTOCOL.sep24,
            receiving_anchor_account="GANCHOR123",
            memo="memo123",
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

    def test_middleware_redirects_more_info_with_persisted_callback(self):
        session = self.client.session
        session[f"sep24_wallet_callback:{self.transaction.id}"] = "postMessage"
        session[f"sep24_wallet_on_change:{self.transaction.id}"] = "postMessage"
        session.save()

        request = _request_with_session(
            "GET",
            "/sep24/transactions/withdraw/webapp?"
            f"transaction_id={self.transaction.id}&asset_code=USDC",
            session=self.client.session,
        )
        response = Sep24OnboardingMiddleware(_noop)(request)

        self.assertEqual(response.status_code, 302)
        location = response["Location"]
        self.assertIn(reverse("more_info"), location)
        query = parse_qs(urlparse(location).query)
        self.assertEqual(query["initialLoad"], ["true"])
        self.assertEqual(query["callback"], ["postMessage"])
        self.assertEqual(query["on_change_callback"], ["postMessage"])


@override_settings(
    VERSO_MOCK_KYC="approved",
    STORAGES={
        "default": {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
        },
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
        },
    },
)
class WithdrawMoreInfoCallbackTests(TestCase):
    def setUp(self):
        seed_polaris_t2(distribution_seed=Keypair.random().secret)
        self.client = Client()
        asset = Asset.objects.get(code="USDC")
        self.transaction = Transaction.objects.create(
            stellar_account=Keypair.random().public_key,
            asset=asset,
            kind=Transaction.KIND.withdrawal,
            status=Transaction.STATUS.pending_user_transfer_start,
            protocol=Transaction.PROTOCOL.sep24,
            receiving_anchor_account="GANCHOR123",
            memo="memo123",
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

    def test_more_info_page_includes_callback_script_params(self):
        response = self.client.get(
            reverse("more_info"),
            {
                "id": str(self.transaction.id),
                "initialLoad": "true",
                "callback": "postMessage",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "polaris/scripts/callback.js")
        self.assertContains(response, "postMessage")

    def test_verso_more_info_injects_postmessage_when_only_initial_load(self):
        response = self.client.get(
            reverse("more_info"),
            {
                "id": str(self.transaction.id),
                "initialLoad": "true",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "callback.js")
        self.assertContains(response, "postMessage")
        self.transaction.refresh_from_db()
        self.assertEqual(self.transaction.on_change_callback, "postMessage")

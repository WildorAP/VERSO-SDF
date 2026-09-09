from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest import skipUnless
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, TestCase, override_settings
from polaris import settings as polaris_settings
from polaris.models import Quote
from stellar_sdk import Keypair

from verso_integrations.polaris_setup import (
    pen_asset_identification,
    seed_polaris_t2,
    usd_asset_identification,
    usdc_asset_identification,
)
from verso_integrations.rates import FiatUsdcRate, RatesError
from verso_integrations.sep38 import VersoQuoteIntegration, price_for_pair, quote_expires_at


class FakeAsset:
    def __init__(self, asset_id: str, significant_decimals: int):
        self.asset_identification_format = asset_id
        self.significant_decimals = significant_decimals


class PriceForPairTests(SimpleTestCase):
    def setUp(self):
        self.pen = FakeAsset(pen_asset_identification(), 2)
        self.usd = FakeAsset(usd_asset_identification(), 2)
        self.usdc = FakeAsset(usdc_asset_identification(), 7)
        # rate_venta y rate_compra deliberadamente distintos, para que el test
        # confirme que se usa el lado correcto (no solo "algún número").
        self.pen_rate = FiatUsdcRate(rate_venta=Decimal("3.7500"), rate_compra=Decimal("3.7000"))
        self.usd_rate = FiatUsdcRate(rate_venta=Decimal("1.0010"), rate_compra=Decimal("0.9990"))

    def test_pen_to_usdc_uses_rate_venta(self):
        # Deposito (on-ramp): VERSO vende USDC -> usa rate_venta.
        price = price_for_pair(self.pen, self.usdc, self.pen_rate)
        self.assertEqual(price, Decimal("3.75"))

    def test_usdc_to_pen_uses_inverted_rate_compra(self):
        # Retiro (off-ramp): VERSO compra USDC -> usa rate_compra (invertido).
        price = price_for_pair(self.usdc, self.pen, self.pen_rate)
        self.assertEqual(price, Decimal("0.2702703"))

    def test_usd_to_usdc_uses_rate_venta(self):
        price = price_for_pair(self.usd, self.usdc, self.usd_rate)
        self.assertEqual(price, Decimal("1.00"))

    def test_usdc_to_usd_uses_inverted_rate_compra(self):
        price = price_for_pair(self.usdc, self.usd, self.usd_rate)
        self.assertEqual(price, Decimal("1.0010010"))

    def test_unsupported_pair_raises(self):
        other = FakeAsset("iso4217:EUR", 2)
        with self.assertRaises(ValueError):
            price_for_pair(self.pen, other, self.pen_rate)


@override_settings(VERSO_QUOTE_TTL_SECONDS=600)
class QuoteExpiresAtTests(SimpleTestCase):
    def test_default_ttl_from_now(self):
        quote = Quote(requested_expire_after=None)
        before = datetime.now(timezone.utc)
        expires = quote_expires_at(quote)
        after = datetime.now(timezone.utc) + timedelta(seconds=601)
        self.assertGreaterEqual(expires, before + timedelta(seconds=599))
        self.assertLessEqual(expires, after)

    def test_honors_client_expire_after_within_policy(self):
        requested = datetime.now(timezone.utc) + timedelta(minutes=5)
        quote = Quote(requested_expire_after=requested)
        self.assertEqual(quote_expires_at(quote), requested)

    def test_rejects_expire_after_beyond_policy(self):
        requested = datetime.now(timezone.utc) + timedelta(hours=2)
        quote = Quote(requested_expire_after=requested)
        with self.assertRaises(ValueError):
            quote_expires_at(quote)


class VersoQuoteIntegrationTests(TestCase):
    def setUp(self):
        seed_polaris_t2(distribution_seed=Keypair.random().secret)
        self.integration = VersoQuoteIntegration()
        self.pen = FakeAsset(pen_asset_identification(), 2)
        self.usd = FakeAsset(usd_asset_identification(), 2)
        self.usdc = FakeAsset(usdc_asset_identification(), 7)

    @patch("verso_integrations.sep38.get_pen_usdc_rate")
    def test_get_price_fetches_live_pen_rate(self, mock_pen_rate):
        mock_pen_rate.return_value = FiatUsdcRate(
            rate_venta=Decimal("3.8000"), rate_compra=Decimal("3.7500")
        )

        price = self.integration.get_price(
            token=MagicMock(),
            request=MagicMock(),
            sell_asset=self.pen,
            buy_asset=self.usdc,
        )

        self.assertEqual(price, Decimal("3.80"))
        mock_pen_rate.assert_called_once()

    @patch("verso_integrations.sep38.get_usd_usdc_rate")
    def test_get_price_fetches_live_usd_rate(self, mock_usd_rate):
        mock_usd_rate.return_value = FiatUsdcRate(
            rate_venta=Decimal("1.0010"), rate_compra=Decimal("0.9990")
        )

        price = self.integration.get_price(
            token=MagicMock(),
            request=MagicMock(),
            sell_asset=self.usd,
            buy_asset=self.usdc,
        )

        self.assertEqual(price, Decimal("1.00"))
        mock_usd_rate.assert_called_once()

    @patch("verso_integrations.sep38.get_pen_usdc_rate")
    def test_get_price_maps_rates_error_to_runtime_error(self, mock_rate):
        mock_rate.side_effect = RatesError("core unavailable")
        with self.assertRaises(RuntimeError):
            self.integration.get_price(
                token=MagicMock(),
                request=MagicMock(),
                sell_asset=self.pen,
                buy_asset=self.usdc,
            )

    @patch("verso_integrations.sep38.get_pen_usdc_rate")
    def test_post_quote_sets_price_and_expiration(self, mock_rate):
        from polaris.models import DeliveryMethod

        mock_rate.return_value = FiatUsdcRate(
            rate_venta=Decimal("3.7500"), rate_compra=Decimal("3.7000")
        )
        sell_method = DeliveryMethod.objects.get(
            name="bank_transfer_cci_cce",
            type=DeliveryMethod.TYPE.sell,
        )
        quote = Quote(
            sell_asset=pen_asset_identification(),
            buy_asset=usdc_asset_identification(),
            sell_amount=Decimal("100.00"),
            sell_delivery_method=sell_method,
        )

        result = self.integration.post_quote(
            token=MagicMock(),
            request=MagicMock(),
            quote=quote,
        )

        self.assertEqual(result.price, Decimal("3.75"))
        self.assertIsNotNone(result.expires_at)
        self.assertGreater(result.expires_at, datetime.now(timezone.utc))


@skipUnless("sep-38" in polaris_settings.ACTIVE_SEPS, "sep-38 not in ACTIVE_SEPS")
class Sep38HttpTests(TestCase):
    def test_price_endpoint_requires_jwt(self):
        response = self.client.get(
            "/sep38/price",
            {
                "sell_asset": pen_asset_identification(),
                "buy_asset": usdc_asset_identification(),
                "sell_amount": "100",
                "sell_delivery_method": "bank_transfer_cci_cce",
            },
        )
        self.assertEqual(response.status_code, 403)


@override_settings(ROOT_URLCONF="config.urls")
@patch.dict("os.environ", {"HOST_URL": "http://localhost:8000"})
@patch("polaris.settings.ACTIVE_SEPS", ["sep-1", "sep-10", "sep-38"])
class Sep38TomlTests(TestCase):
    def test_stellar_toml_includes_anchor_quote_server(self):
        response = self.client.get("/.well-known/stellar.toml")
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("ANCHOR_QUOTE_SERVER", content)
        self.assertIn("sep38", content)
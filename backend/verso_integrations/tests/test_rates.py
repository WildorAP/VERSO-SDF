from decimal import Decimal
from unittest.mock import MagicMock, patch

import requests
from django.test import SimpleTestCase, TestCase, override_settings

from verso_integrations.polaris_setup import (
    pen_asset_identification,
    seed_polaris_t2,
    usd_asset_identification,
    usdc_asset_identification,
    usdc_issuer,
)
from verso_integrations.rates import (
    FiatUsdcRate,
    PenUsdcRate,
    RatesError,
    get_pen_usdc_rate,
    get_usd_usdc_rate,
    parse_fiat_usdc_response,
    parse_pen_usdc_response,
)
from verso_integrations.sep1 import USDC_ISSUER_TESTNET


class ParsePenUsdcResponseTests(SimpleTestCase):
    def test_parses_required_fields(self):
        rate = parse_pen_usdc_response({"rate_venta": "3.5000", "rate_compra": "3.4000"})
        self.assertEqual(rate.rate_venta, Decimal("3.5000"))
        self.assertEqual(rate.rate_compra, Decimal("3.4000"))
        self.assertIsInstance(rate, FiatUsdcRate)
        self.assertIsNone(rate.updated_at)
        self.assertIsNone(rate.source)

    def test_parses_optional_fields(self):
        rate = parse_pen_usdc_response(
            {
                "rate_venta": 3.5,
                "rate_compra": 3.4,
                "updated_at": "2026-09-07T17:00:00Z",
                "source": "platea_exchangerate",
            }
        )
        self.assertEqual(rate.rate_venta, Decimal("3.5"))
        self.assertEqual(rate.rate_compra, Decimal("3.4"))
        self.assertEqual(rate.source, "platea_exchangerate")
        self.assertIsNotNone(rate.updated_at)

    def test_rejects_missing_rate_venta(self):
        with self.assertRaises(RatesError):
            parse_pen_usdc_response({"rate_compra": "3.4000"})

    def test_rejects_missing_rate_compra(self):
        with self.assertRaises(RatesError):
            parse_pen_usdc_response({"rate_venta": "3.5000"})

    def test_rejects_non_positive_rate_venta(self):
        with self.assertRaises(RatesError):
            parse_pen_usdc_response({"rate_venta": "0", "rate_compra": "3.4000"})

    def test_rejects_non_positive_rate_compra(self):
        with self.assertRaises(RatesError):
            parse_pen_usdc_response({"rate_venta": "3.5000", "rate_compra": "0"})

    def test_rejects_invalid_updated_at(self):
        with self.assertRaises(RatesError):
            parse_pen_usdc_response(
                {"rate_venta": "3.5000", "rate_compra": "3.4000", "updated_at": "not-a-date"}
            )


@override_settings(
    VERSO_CORE_API_URL="http://core.test",
    VERSO_CORE_API_KEY="test-key",
)
class GetPenUsdcRateTests(SimpleTestCase):
    @patch("verso_integrations.rates.requests.get")
    def test_fetches_and_parses_core_response(self, mock_get):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "rate_venta": "3.8100",
            "rate_compra": "3.7900",
            "updated_at": "2026-09-07T17:00:00Z",
        }
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        rate = get_pen_usdc_rate()

        self.assertIsInstance(rate, PenUsdcRate)
        self.assertEqual(rate.rate_venta, Decimal("3.8100"))
        self.assertEqual(rate.rate_compra, Decimal("3.7900"))
        mock_get.assert_called_once_with(
            "http://core.test/internal/rates/pen-usdc",
            headers={"Authorization": "Bearer test-key"},
            timeout=5,
        )

    @override_settings(VERSO_CORE_API_KEY="")
    def test_requires_api_key(self):
        with self.assertRaises(RatesError):
            get_pen_usdc_rate()

    @patch("verso_integrations.rates.requests.get")
    def test_wraps_http_errors(self, mock_get):
        mock_get.side_effect = requests.exceptions.ConnectionError("network down")
        with self.assertRaises(RatesError):
            get_pen_usdc_rate()


@override_settings(
    VERSO_CORE_API_URL="http://core.test",
    VERSO_CORE_API_KEY="test-key",
)
class GetUsdUsdcRateTests(SimpleTestCase):
    @patch("verso_integrations.rates.requests.get")
    def test_fetches_usd_usdc_from_core(self, mock_get):
        mock_response = MagicMock()
        mock_response.json.return_value = {"rate_venta": "1.0050", "rate_compra": "0.9950"}
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        rate = get_usd_usdc_rate()

        self.assertEqual(rate.rate_venta, Decimal("1.0050"))
        self.assertEqual(rate.rate_compra, Decimal("0.9950"))
        mock_get.assert_called_once_with(
            "http://core.test/internal/rates/usd-usdc",
            headers={"Authorization": "Bearer test-key"},
            timeout=5,
        )


class SeedPolarisT2Tests(TestCase):
    def test_seed_is_idempotent(self):
        from stellar_sdk import Keypair

        distribution_seed = Keypair.random().secret
        summary_first = seed_polaris_t2(distribution_seed=distribution_seed)
        summary_second = seed_polaris_t2(distribution_seed=distribution_seed)

        self.assertEqual(summary_first["usdc_asset"], "created")
        self.assertEqual(summary_second["usdc_asset"], "updated")

        from polaris.models import Asset, ExchangePair, OffChainAsset

        self.assertEqual(Asset.objects.filter(code="USDC", issuer=usdc_issuer()).count(), 1)
        self.assertEqual(OffChainAsset.objects.filter(scheme="iso4217", identifier="PEN").count(), 1)
        self.assertEqual(OffChainAsset.objects.filter(scheme="iso4217", identifier="USD").count(), 1)
        self.assertEqual(ExchangePair.objects.count(), 4)

        asset = Asset.objects.get(code="USDC", issuer=usdc_issuer())
        self.assertTrue(asset.sep24_enabled)
        self.assertTrue(asset.sep38_enabled)

    @patch.dict("os.environ", {"STELLAR_NETWORK_PASSPHRASE": "Test SDF Network ; September 2015"})
    def test_asset_identification_helpers(self):
        self.assertEqual(usdc_issuer(), USDC_ISSUER_TESTNET)
        self.assertEqual(usdc_asset_identification(), f"stellar:USDC:{USDC_ISSUER_TESTNET}")
        self.assertEqual(pen_asset_identification(), "iso4217:PEN")
        self.assertEqual(usd_asset_identification(), "iso4217:USD")
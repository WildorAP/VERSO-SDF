from decimal import Decimal

from django.test import TestCase
from stellar_sdk import Keypair

from verso_integrations.management.commands.validate_quotes import check_quote
from verso_integrations.polaris_setup import (
    pen_asset_identification,
    seed_polaris_t2,
    usdc_asset_identification,
)
from verso_integrations.rates import FiatUsdcRate


class CheckQuoteTests(TestCase):
    def setUp(self):
        seed_polaris_t2(distribution_seed=Keypair.random().secret)
        self.pen = pen_asset_identification()
        self.usdc = usdc_asset_identification()
        self.rate = FiatUsdcRate(rate_venta=Decimal("3.8000"), rate_compra=Decimal("3.7500"))

    def _quote(self, sell, buy, sell_amount, buy_amount, price):
        return {
            "sell_asset": sell,
            "buy_asset": buy,
            "sell_amount": sell_amount,
            "buy_amount": buy_amount,
            "price": price,
        }

    def test_on_ramp_quote_matches_core_rate(self):
        quote = self._quote(self.pen, self.usdc, "100.00", "26.3157895", "3.80")
        match, expected_price, expected_buy, detail = check_quote(quote, [self.rate])
        self.assertTrue(match, detail)
        self.assertEqual(expected_price, Decimal("3.80"))
        self.assertEqual(expected_buy, Decimal("26.3157895"))

    def test_off_ramp_quote_matches_core_rate(self):
        quote = self._quote(self.usdc, self.pen, "10.0000000", "37.50", "0.2666667")
        match, _, _, detail = check_quote(quote, [self.rate])
        self.assertTrue(match, detail)

    def test_four_decimal_core_rate_is_not_rounded_to_cents(self):
        rate = FiatUsdcRate(rate_venta=Decimal("3.3750"), rate_compra=Decimal("3.3000"))
        exact = self._quote(self.pen, self.usdc, "100.0000", "29.6296296", "3.3750")
        rounded = self._quote(self.pen, self.usdc, "100.0000", "29.5857988", "3.38")
        self.assertTrue(check_quote(exact, [rate])[0])
        self.assertFalse(check_quote(rounded, [rate])[0])

    def test_price_different_from_core_is_reported(self):
        quote = self._quote(self.pen, self.usdc, "100.00", "25.6410256", "3.90")
        match, _, _, detail = check_quote(quote, [self.rate])
        self.assertFalse(match)
        self.assertIn("price", detail)

    def test_matches_when_rate_moved_between_before_and_after(self):
        moved = FiatUsdcRate(rate_venta=Decimal("3.9000"), rate_compra=Decimal("3.8500"))
        quote = self._quote(self.pen, self.usdc, "100.00", "25.6410256", "3.90")
        match, _, _, detail = check_quote(quote, [self.rate, moved])
        self.assertTrue(match, detail)

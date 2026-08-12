from unittest.mock import patch

from django.test import TestCase

from verso_integrations.sep1 import (
    return_toml_contents,
    USDC_ISSUER_TESTNET,
    USDC_ISSUER_MAINNET,
)


class Sep1TomlTests(TestCase):
    def test_toml_includes_pen_and_usd_usdc_currencies_on_testnet(self):
        toml_data = return_toml_contents(None)

        currencies = toml_data["CURRENCIES"]
        self.assertEqual(len(currencies), 2)

        codes = {c["code"] for c in currencies}
        self.assertEqual(codes, {"USDC"})

        issuers = {c["issuer"] for c in currencies}
        self.assertEqual(issuers, {USDC_ISSUER_TESTNET})

        anchor_assets = {c["anchor_asset"] for c in currencies}
        self.assertEqual(anchor_assets, {"PEN", "USD"})

    @patch.dict(
        "os.environ",
        {"STELLAR_NETWORK_PASSPHRASE": "Public Global Stellar Network ; September 2015"},
    )
    def test_toml_uses_mainnet_issuer_when_passphrase_is_mainnet(self):
        toml_data = return_toml_contents(None)

        currencies = toml_data["CURRENCIES"]
        issuers = {c["issuer"] for c in currencies}
        self.assertEqual(issuers, {USDC_ISSUER_MAINNET})

        statuses = {c["status"] for c in currencies}
        self.assertEqual(statuses, {"live"})

    @patch.dict("os.environ", {"SIGNING_SEED": ""})
    def test_toml_accounts_empty_when_signing_seed_is_blank(self):
        toml_data = return_toml_contents(None)
        self.assertEqual(toml_data["ACCOUNTS"], [])

    @patch.dict("os.environ", {"SIGNING_SEED": "not-a-valid-stellar-secret-key"})
    def test_toml_accounts_empty_when_signing_seed_is_invalid(self):
        toml_data = return_toml_contents(None)
        self.assertEqual(toml_data["ACCOUNTS"], [])
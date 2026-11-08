import ntpath
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase, override_settings
from stellar_sdk import Keypair

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

    @override_settings(TURNKEY_SIGN_WITH="")
    def test_toml_accounts_empty_without_turnkey_wallet(self):
        toml_data = return_toml_contents(None)
        self.assertEqual(toml_data["ACCOUNTS"], [])

    def test_toml_accounts_list_only_the_turnkey_wallet(self):
        signing = Keypair.random()
        wallet = Keypair.random().public_key
        with patch.dict("os.environ", {"SIGNING_SEED": signing.secret}), override_settings(
            TURNKEY_SIGN_WITH=wallet
        ):
            toml_data = return_toml_contents(None)
        self.assertEqual(toml_data["ACCOUNTS"], [wallet])
        self.assertNotIn(signing.public_key, toml_data["ACCOUNTS"])


@override_settings(ROOT_URLCONF="config.urls")
@patch("polaris.settings.ACTIVE_SEPS", ["sep-1", "sep-10", "sep-24", "sep-38"])
class Sep1TomlHttpTests(TestCase):
    def test_stellar_toml_advertises_sep24_but_not_sep6(self):
        response = self.client.get("/.well-known/stellar.toml")
        self.assertEqual(response.status_code, 200)
        lines = [line.split("=")[0].strip() for line in response.content.decode().splitlines()]
        self.assertIn("TRANSFER_SERVER_SEP0024", lines)
        self.assertNotIn("TRANSFER_SERVER", lines)

    def test_stellar_toml_keeps_signing_key_out_of_accounts(self):
        """Wallets verify SEP-10 challenges against SIGNING_KEY, so it stays even outside ACCOUNTS."""
        wallet = Keypair.random().public_key
        with override_settings(TURNKEY_SIGN_WITH=wallet):
            response = self.client.get("/.well-known/stellar.toml")
        self.assertEqual(response.status_code, 200)
        lines = response.content.decode().splitlines()
        signing_key = next(line for line in lines if line.startswith("SIGNING_KEY"))
        signing_key = signing_key.split("=", 1)[1].strip().strip('"')
        accounts = next(line for line in lines if line.startswith("ACCOUNTS"))
        self.assertTrue(signing_key.startswith("G"))
        self.assertIn(wallet, accounts)
        self.assertNotIn(signing_key, accounts)

    @patch("polaris.settings.HOST_URL", "http://localhost:8000")
    @patch("polaris.sep1.views.os", SimpleNamespace(path=ntpath))
    def test_stellar_toml_urls_use_forward_slashes_on_windows(self):
        response = self.client.get("/.well-known/stellar.toml")
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn('TRANSFER_SERVER_SEP0024 = "http://localhost:8000/sep24"', content)
        self.assertIn('WEB_AUTH_ENDPOINT = "http://localhost:8000/auth"', content)
        self.assertIn('ANCHOR_QUOTE_SERVER = "http://localhost:8000/sep38"', content)
        self.assertNotIn("\\", content)
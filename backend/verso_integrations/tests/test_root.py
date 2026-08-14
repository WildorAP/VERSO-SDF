import json
from unittest.mock import patch

from django.test import TestCase, override_settings
from stellar_sdk import Keypair


@override_settings(ROOT_URLCONF="config.urls")
class RootViewTests(TestCase):
    def test_root_returns_health_json(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/json")

        data = json.loads(response.content)
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["service"], "verso-anchor")
        self.assertEqual(data["network"], "testnet")
        self.assertIn("T1", data["tranches"])
        self.assertEqual(data["tranches"]["T1"]["status"], "live")
        self.assertEqual(data["tranches"]["T2"]["status"], "planned")
        self.assertIn("stellar_toml", data["paths"])
        self.assertIn("stellar_toml", data["endpoints"])

    def test_root_includes_anchor_account_and_full_urls(self):
        keypair = Keypair.random()
        with patch.dict(
            "os.environ",
            {
                "HOST_URL": "https://anchor.versotek.io",
                "SIGNING_SEED": keypair.secret,
            },
        ):
            response = self.client.get("/")
        data = json.loads(response.content)

        self.assertEqual(data["anchor_account"], keypair.public_key)
        self.assertEqual(
            data["endpoints"]["stellar_toml"],
            "https://anchor.versotek.io/.well-known/stellar.toml",
        )

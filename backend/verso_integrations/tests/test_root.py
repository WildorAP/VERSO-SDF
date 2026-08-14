import json
from unittest.mock import patch

from django.test import TestCase, override_settings
from stellar_sdk import Keypair


@override_settings(ROOT_URLCONF="config.urls")
class RootViewTests(TestCase):
    def test_root_returns_html_by_default(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response["Content-Type"])
        self.assertContains(response, "Stellar Anchor")
        self.assertContains(response, "Qué viene en cada tranche")
        self.assertContains(response, "T1")

    def test_root_returns_health_json_with_format_param(self):
        response = self.client.get("/?format=json")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/json")

        data = json.loads(response.content)
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["service"], "verso-anchor")
        self.assertEqual(data["network"], "testnet")
        self.assertEqual(len(data["tranches"]), 3)
        self.assertEqual(data["tranches"][0]["id"], "T1")
        self.assertEqual(data["tranches"][0]["status"], "live")
        self.assertEqual(data["tranches"][1]["status"], "planned")
        self.assertEqual(len(data["endpoints"]), 3)

    def test_root_returns_json_when_accept_header_requests_it(self):
        response = self.client.get("/", HTTP_ACCEPT="application/json")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/json")
        data = json.loads(response.content)
        self.assertEqual(data["status"], "ok")

    def test_root_includes_anchor_account_and_full_urls(self):
        keypair = Keypair.random()
        with patch.dict(
            "os.environ",
            {
                "HOST_URL": "https://anchor.versotek.io",
                "SIGNING_SEED": keypair.secret,
            },
        ):
            response = self.client.get("/?format=json")
        data = json.loads(response.content)

        self.assertEqual(data["anchor_account"], keypair.public_key)
        self.assertEqual(
            data["endpoints"][0]["url"],
            "https://anchor.versotek.io/.well-known/stellar.toml",
        )

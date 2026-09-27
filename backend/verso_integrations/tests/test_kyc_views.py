from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import jwt
from django.test import Client, TestCase, override_settings
from polaris import settings as polaris_settings
from polaris.models import Asset, Transaction
from stellar_sdk import Keypair

from verso_integrations.core_client import KycHandoffResult, UserStatusResult
from verso_integrations.polaris_setup import seed_polaris_t2


def _deposit_transaction(stellar_account: str) -> Transaction:
    asset = Asset.objects.get(code="USDC")
    return Transaction.objects.create(
        stellar_account=stellar_account,
        asset=asset,
        kind=Transaction.KIND.deposit,
        status=Transaction.STATUS.pending_user,
        protocol=Transaction.PROTOCOL.sep24,
    )


@override_settings(
    LOCAL_MODE=True,
    VERSO_CORE_API_KEY="test-key",
    STORAGES={
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
        }
    },
)
class KycViewTests(TestCase):
    def setUp(self):
        seed_polaris_t2(distribution_seed=Keypair.random().secret)
        self.client = Client()
        self.transaction = _deposit_transaction(Keypair.random().public_key)
        prefix = f"sep24:{self.transaction.id}"
        session = self.client.session
        session[f"{prefix}:verso_user_id"] = 42
        session[f"{prefix}:verso_email"] = "user@example.com"
        session[f"{prefix}:email_verified"] = True
        session[f"{prefix}:profile_completed"] = True
        session.save()

    @patch("verso_integrations.sep24.onboarding_views.prepare_kyc_step")
    def test_onboarding_renders_didit_embed(self, mock_prepare):
        from verso_integrations.sep24.kyc_views import KycStepResult

        mock_prepare.return_value = KycStepResult(
            embed_url="https://verification.didit.me/v/session-abc",
            callback_url="http://testserver/sep24/kyc/callback/?transaction_id=tx1",
            fallback_url="http://testserver/sep24/kyc/start/?transaction_id=tx1",
        )
        response = self.client.get(
            f"/sep24/onboarding/?transaction_id={self.transaction.id}"
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Verificación de identidad")
        self.assertContains(response, 'id="didit-embed"')
        self.assertContains(response, "https://verification.didit.me/v/session-abc")
        self.assertContains(response, "Abrir en pantalla completa")
        mock_prepare.assert_called_once()

    def test_onboarding_renders_profile_wizard_before_didit(self):
        session = self.client.session
        prefix = f"sep24:{self.transaction.id}"
        session.pop(f"{prefix}:profile_completed", None)
        session.save()
        response = self.client.get(
            f"/sep24/onboarding/?transaction_id={self.transaction.id}"
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Completa tu perfil")
        self.assertContains(response, "profile-wizard-form")

    @patch("verso_integrations.sep24.kyc_views.kyc_handoff")
    def test_kyc_start_redirects_to_didit(self, mock_handoff):
        mock_handoff.return_value = KycHandoffResult(
            kyc_url="https://didit.example/start",
            kyc_completed=False,
        )
        response = self.client.get(
            f"/sep24/kyc/start/?transaction_id={self.transaction.id}"
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "https://didit.example/start")
        mock_handoff.assert_called_once()

    @patch("verso_integrations.sep24.kyc_views.kyc_handoff")
    def test_kyc_start_local_mock_marks_session_and_returns_webapp(self, mock_handoff):
        def _echo_return_url(**kwargs):
            return KycHandoffResult(
                kyc_url=kwargs["return_url"],
                kyc_completed=False,
            )

        mock_handoff.side_effect = _echo_return_url

        response = self.client.get(
            f"/sep24/kyc/start/?transaction_id={self.transaction.id}"
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/sep24/transactions/deposit/webapp", response["Location"])

        session_key = f"sep24:{self.transaction.id}:kyc_approved"
        self.assertTrue(self.client.session.get(session_key))

    @patch("verso_integrations.sep24.kyc_views._refresh_kyc_status")
    def test_kyc_poll_returns_redirect_when_core_approves(self, mock_refresh):
        mock_refresh.return_value = UserStatusResult(
            user_id=42,
            email="user@example.com",
            kyc_completed=True,
        )
        response = self.client.get(
            f"/sep24/kyc/poll/?transaction_id={self.transaction.id}"
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "approved")
        self.assertIn("/sep24/transactions/deposit/webapp", payload["redirect"])

    def test_kyc_poll_pending_when_kyc_incomplete(self):
        with patch(
            "verso_integrations.sep24.kyc_views._refresh_kyc_status",
            return_value=UserStatusResult(
                user_id=42,
                email="user@example.com",
                kyc_completed=False,
            ),
        ):
            response = self.client.get(
                f"/sep24/kyc/poll/?transaction_id={self.transaction.id}"
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "pending")

    @patch("verso_integrations.sep24.kyc_views._refresh_kyc_status")
    def test_kyc_callback_marks_kyc_when_core_confirms(self, mock_refresh):
        mock_refresh.return_value = UserStatusResult(
            user_id=42,
            email="user@example.com",
            kyc_completed=True,
        )
        response = self.client.get(
            f"/sep24/kyc/callback/?transaction_id={self.transaction.id}"
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/sep24/transactions/deposit/webapp", response["Location"])
        session_key = f"sep24:{self.transaction.id}:kyc_approved"
        self.assertTrue(self.client.session.get(session_key))

    @patch("verso_integrations.sep24.kyc_views._refresh_kyc_status")
    def test_kyc_callback_webapp_token_uses_interactive_issuer(self, mock_refresh):
        mock_refresh.return_value = UserStatusResult(
            user_id=42,
            email="user@example.com",
            kyc_completed=True,
        )
        response = self.client.get(
            f"/sep24/kyc/callback/?transaction_id={self.transaction.id}"
        )
        redirect_url = response["Location"]
        token = parse_qs(urlparse(redirect_url).query)["token"][0]
        payload = jwt.decode(
            token,
            polaris_settings.SERVER_JWT_KEY,
            algorithms=["HS256"],
        )
        self.assertEqual(
            payload["iss"],
            "http://testserver/sep24/transactions/deposit/interactive",
        )
        self.assertEqual(payload["jti"], str(self.transaction.id))

        webapp_response = self.client.get(redirect_url)
        self.assertNotEqual(webapp_response.status_code, 403)

    def test_kyc_start_requires_onboarding_session(self):
        client = Client()
        response = client.get(
            f"/sep24/kyc/start/?transaction_id={self.transaction.id}"
        )
        self.assertEqual(response.status_code, 400)

    def test_kyc_callback_shows_wait_page_when_pending(self):
        with patch(
            "verso_integrations.sep24.kyc_views._refresh_kyc_status",
            return_value=UserStatusResult(
                user_id=42,
                email="user@example.com",
                kyc_completed=False,
            ),
        ):
            response = self.client.get(
                f"/sep24/kyc/callback/?transaction_id={self.transaction.id}"
            )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Procesando verificación")

    def test_kyc_start_requires_transaction_id(self):
        response = self.client.get("/sep24/kyc/start/")
        self.assertEqual(response.status_code, 400)

    def test_onboarding_switch_to_register(self):
        response = self.client.get(
            f"/sep24/onboarding/switch/?transaction_id={self.transaction.id}&mode=register"
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/sep24/onboarding/", response["Location"])
        session_key = f"sep24:{self.transaction.id}:wants_register"
        self.assertTrue(self.client.session.get(session_key))

    def test_onboarding_switch_to_login(self):
        session = self.client.session
        session[f"sep24:{self.transaction.id}:wants_register"] = True
        session.save()
        response = self.client.get(
            f"/sep24/onboarding/switch/?transaction_id={self.transaction.id}&mode=login"
        )
        self.assertEqual(response.status_code, 302)
        session_key = f"sep24:{self.transaction.id}:wants_register"
        self.assertIsNone(self.client.session.get(session_key))

from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from verso_integrations.accounts.models import AnchorProfile


def _mock_response(status_code=200, json_data=None):
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = json_data or {}
    return response


class RegisterViewTests(TestCase):
    def test_get_renders_form_with_step_one(self):
        response = self.client.get(reverse("accounts:register"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "accounts/register.html")
        self.assertEqual(response.context["step"], 1)
        self.assertTrue(response.context["show_rail"])

    def test_post_without_email_or_password_shows_error(self):
        response = self.client.post(reverse("accounts:register"), data={"email": ""})
        self.assertEqual(response.status_code, 200)
        self.assertIn("obligatorios", response.context["error"])

    @patch("verso_integrations.accounts.views.requests.post")
    def test_post_success_stores_session_and_redirects_to_verify(self, mock_post):
        mock_post.return_value = _mock_response(
            201, {"user_id": 42, "email": "nueva@correo.com"}
        )
        response = self.client.post(
            reverse("accounts:register"),
            data={"email": "nueva@correo.com", "password": "unaClaveSegura1"},
        )
        self.assertRedirects(response, reverse("accounts:verify_email"))
        self.assertEqual(self.client.session["pending_verso_user_id"], 42)
        self.assertEqual(self.client.session["pending_verso_email"], "nueva@correo.com")

    @patch("verso_integrations.accounts.views.requests.post")
    def test_post_duplicate_email_shows_error(self, mock_post):
        mock_post.return_value = _mock_response(409)
        response = self.client.post(
            reverse("accounts:register"),
            data={"email": "ya@existe.com", "password": "unaClaveSegura1"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("ya está registrado", response.context["error"])

    @patch("verso_integrations.accounts.views.requests.post")
    def test_post_core_failure_shows_generic_error(self, mock_post):
        mock_post.return_value = _mock_response(500)
        response = self.client.post(
            reverse("accounts:register"),
            data={"email": "algo@correo.com", "password": "unaClaveSegura1"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("No se pudo completar", response.context["error"])


class VerifyEmailViewTests(TestCase):
    def test_get_without_pending_registration_redirects_to_register(self):
        response = self.client.get(reverse("accounts:verify_email"))
        self.assertRedirects(response, reverse("accounts:register"))

    def _start_pending_session(self):
        session = self.client.session
        session["pending_verso_user_id"] = 7
        session["pending_verso_email"] = "pendiente@correo.com"
        session.save()

    def test_get_with_pending_registration_shows_step_two(self):
        self._start_pending_session()
        response = self.client.get(reverse("accounts:verify_email"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["step"], 2)
        self.assertEqual(response.context["email"], "pendiente@correo.com")

    @patch("verso_integrations.accounts.views.requests.post")
    def test_post_invalid_code_shows_error(self, mock_post):
        self._start_pending_session()
        mock_post.return_value = _mock_response(400)
        response = self.client.post(reverse("accounts:verify_email"), data={"codigo": "000000"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("inválido", response.context["error"])

    @patch("verso_integrations.accounts.views.requests.post")
    def test_post_valid_code_clears_session_and_redirects_to_login(self, mock_post):
        self._start_pending_session()
        mock_post.return_value = _mock_response(200)
        response = self.client.post(reverse("accounts:verify_email"), data={"codigo": "123456"})
        self.assertRedirects(
            response, reverse("accounts:login"), fetch_redirect_response=False
        )
        self.assertNotIn("pending_verso_user_id", self.client.session)
        self.assertNotIn("pending_verso_email", self.client.session)


@override_settings(
    VERSO_OAUTH_CLIENT_ID="anchor-client",
    VERSO_OAUTH_AUTHORIZE_URL="https://verso.test/oauth/authorize/",
    VERSO_OAUTH_TOKEN_URL="https://verso.test/oauth/token/",
    VERSO_OAUTH_USERINFO_URL="https://verso.test/oauth/userinfo/",
    VERSO_OAUTH_REDIRECT_URI="https://anchor.test/oauth/callback/",
)
class LoginStartTests(TestCase):
    def test_redirects_to_verso_authorize_with_pkce_params(self):
        response = self.client.get(reverse("accounts:login"))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith("https://verso.test/oauth/authorize/"))
        self.assertIn("code_challenge=", response.url)
        self.assertIn("client_id=anchor-client", response.url)
        self.assertIn("verso_oauth_state", self.client.session)
        self.assertIn("verso_oauth_code_verifier", self.client.session)


@override_settings(
    VERSO_OAUTH_TOKEN_URL="https://verso.test/oauth/token/",
    VERSO_OAUTH_USERINFO_URL="https://verso.test/oauth/userinfo/",
    VERSO_OAUTH_CLIENT_ID="anchor-client",
    VERSO_OAUTH_CLIENT_SECRET="anchor-secret",
    VERSO_OAUTH_REDIRECT_URI="https://anchor.test/oauth/callback/",
)
class OauthCallbackTests(TestCase):
    def test_error_param_renders_login_error_with_simple_layout(self):
        response = self.client.get(reverse("oauth_callback"), {"error": "access_denied"})
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "accounts/login_error.html")
        self.assertFalse(response.context.get("show_rail"))

    def test_state_mismatch_renders_login_error(self):
        session = self.client.session
        session["verso_oauth_state"] = "expected-state"
        session.save()
        response = self.client.get(
            reverse("oauth_callback"), {"code": "abc", "state": "wrong-state"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "accounts/login_error.html")

    def test_missing_code_renders_login_error(self):
        session = self.client.session
        session["verso_oauth_state"] = "expected-state"
        session.save()
        response = self.client.get(reverse("oauth_callback"), {"state": "expected-state"})
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "accounts/login_error.html")

    @patch("verso_integrations.accounts.views.requests.get")
    @patch("verso_integrations.accounts.views.requests.post")
    def test_successful_callback_creates_profile_and_logs_in(self, mock_post, mock_get):
        session = self.client.session
        session["verso_oauth_state"] = "expected-state"
        session["verso_oauth_code_verifier"] = "verifier"
        session.save()

        mock_post.return_value = _mock_response(200, {"access_token": "tok-123"})
        mock_get.return_value = _mock_response(
            200,
            {"id": 99, "email": "usuario@correo.com", "stellar_public_key": None},
        )

        response = self.client.get(
            reverse("oauth_callback"), {"code": "abc", "state": "expected-state"}
        )
        self.assertRedirects(response, reverse("dashboard"))
        profile = AnchorProfile.objects.get(verso_user_id=99)
        self.assertEqual(profile.user.username, "verso_99")
        self.assertFalse(profile.user.has_usable_password())


class DashboardViewTests(TestCase):
    def test_requires_login(self):
        response = self.client.get(reverse("dashboard"))
        self.assertRedirects(
            response,
            f"{reverse('accounts:login')}?next={reverse('dashboard')}",
            fetch_redirect_response=False,
        )

    def test_shows_username_and_wallet_with_simple_layout(self):
        user = User.objects.create_user(username="verso_5")
        AnchorProfile.objects.create(
            user=user, verso_user_id=5, stellar_public_key="GABCDEF1234567890"
        )
        self.client.force_login(user)
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["show_rail"])
        self.assertEqual(response.context["stellar_public_key"], "GABCDEF1234567890")


class LinkStellarTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="verso_8")
        AnchorProfile.objects.create(user=self.user, verso_user_id=8, stellar_public_key=None)
        self.client.force_login(self.user)

    def test_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("accounts:link_stellar_start"))
        self.assertEqual(response.status_code, 302)

    def test_get_shows_step_three(self):
        response = self.client.get(reverse("accounts:link_stellar_start"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["step"], 3)

    def test_post_without_public_key_shows_error(self):
        response = self.client.post(reverse("accounts:link_stellar_start"), data={})
        self.assertEqual(response.status_code, 200)
        self.assertIn("Ingresa una llave", response.context["error"])

    @patch("verso_integrations.accounts.views.requests.get")
    def test_post_with_valid_key_shows_challenge_to_sign(self, mock_get):
        mock_get.return_value = _mock_response(
            200, {"transaction": "AAAA...", "network_passphrase": "Test SDF Network ; September 2015"}
        )
        response = self.client.post(
            reverse("accounts:link_stellar_start"),
            data={"public_key": "GABCDEF1234567890"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "accounts/link_stellar_sign.html")
        self.assertEqual(response.context["challenge_xdr"], "AAAA...")
        self.assertEqual(
            self.client.session["pending_stellar_public_key"], "GABCDEF1234567890"
        )

    @patch("verso_integrations.accounts.views.requests.get")
    def test_post_when_challenge_endpoint_fails_shows_error(self, mock_get):
        mock_get.return_value = _mock_response(500)
        response = self.client.post(
            reverse("accounts:link_stellar_start"),
            data={"public_key": "GABCDEF1234567890"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("No se pudo generar el reto", response.context["error"])

    def test_submit_without_pending_key_redirects_to_start(self):
        response = self.client.post(reverse("accounts:link_stellar_submit"), data={})
        self.assertRedirects(response, reverse("accounts:link_stellar_start"))

    def _start_pending_signature(self, public_key="GABCDEF1234567890"):
        session = self.client.session
        session["pending_stellar_public_key"] = public_key
        session.save()

    @patch("verso_integrations.accounts.views.requests.post")
    def test_submit_with_invalid_signature_shows_error(self, mock_post):
        self._start_pending_signature()
        mock_post.return_value = _mock_response(400)
        response = self.client.post(
            reverse("accounts:link_stellar_submit"), data={"signed_xdr": "bad-xdr"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("no pudo validarse", response.context["error"])

    @patch("verso_integrations.accounts.views.requests.post")
    def test_submit_success_updates_profile_and_redirects_to_dashboard(self, mock_post):
        self._start_pending_signature()

        def side_effect(url, **kwargs):
            if url.endswith("/auth"):
                return _mock_response(200)
            return _mock_response(200)

        mock_post.side_effect = side_effect

        response = self.client.post(
            reverse("accounts:link_stellar_submit"), data={"signed_xdr": "good-xdr"}
        )
        self.assertRedirects(response, reverse("dashboard"))
        self.user.anchor_profile.refresh_from_db()
        self.assertEqual(
            self.user.anchor_profile.stellar_public_key, "GABCDEF1234567890"
        )
        self.assertNotIn("pending_stellar_public_key", self.client.session)

    @patch("verso_integrations.accounts.views.requests.post")
    def test_submit_when_key_already_linked_elsewhere_shows_error(self, mock_post):
        self._start_pending_signature()

        def side_effect(url, **kwargs):
            if url.endswith("/auth"):
                return _mock_response(200)
            return _mock_response(409)

        mock_post.side_effect = side_effect

        response = self.client.post(
            reverse("accounts:link_stellar_submit"), data={"signed_xdr": "good-xdr"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("ya está vinculada", response.context["error"])
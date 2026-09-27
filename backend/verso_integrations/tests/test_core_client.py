from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings

from verso_integrations.core_client import (
    CoreClientError,
    KycHandoffResult,
    kyc_handoff,
    login_user,
    register_user,
    user_status,
    verify_email,
)


@override_settings(
    VERSO_CORE_API_URL="https://core.example",
    VERSO_CORE_API_KEY="test-key",
)
class CoreClientTests(TestCase):
    @patch("verso_integrations.core_client.requests.post")
    def test_login_user_success(self, mock_post):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "user_id": 9,
                "email": "a@b.com",
                "registered": True,
                "active": True,
                "email_verified": True,
                "kyc_completed": False,
                "stellar_linked": False,
            },
        )
        result = login_user(email="a@b.com", password="secret")
        self.assertEqual(result.user_id, 9)
        self.assertTrue(result.email_verified)

    @patch("verso_integrations.core_client.requests.post")
    def test_login_user_invalid_credentials(self, mock_post):
        mock_post.return_value = MagicMock(status_code=401)
        with self.assertRaises(CoreClientError):
            login_user(email="a@b.com", password="wrong")

    @patch("verso_integrations.core_client.requests.get")
    def test_user_status_success(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "registered": True,
                "user_id": 3,
                "email": "a@b.com",
                "email_verified": True,
                "kyc_completed": True,
                "stellar_linked": True,
                "stellar_public_key": "GTEST",
            },
        )
        result = user_status(email="a@b.com")
        self.assertEqual(result.user_id, 3)
        self.assertTrue(result.kyc_completed)

    @patch("verso_integrations.core_client.requests.post")
    def test_register_user_success(self, mock_post):
        mock_post.return_value = MagicMock(status_code=201, json=lambda: {"user_id": 5, "email": "a@b.com"})

        result = register_user(
            email="a@b.com",
            password="secret",
            nombre="Ana",
            apellidos="Perez",
            stellar_public_key="GTEST",
        )
        self.assertEqual(result.user_id, 5)
        self.assertEqual(result.email, "a@b.com")
        mock_post.assert_called_once()
        headers = mock_post.call_args.kwargs["headers"]
        self.assertEqual(headers["Authorization"], "Bearer test-key")

    @patch("verso_integrations.core_client.requests.post")
    def test_register_user_conflict(self, mock_post):
        mock_post.return_value = MagicMock(status_code=409)
        with self.assertRaises(CoreClientError):
            register_user(
                email="a@b.com",
                password="secret",
                nombre="Ana",
                apellidos="Perez",
                stellar_public_key="GTEST",
            )

    @patch("verso_integrations.core_client.requests.post")
    def test_verify_email_invalid_code(self, mock_post):
        mock_post.return_value = MagicMock(status_code=400)
        with self.assertRaises(CoreClientError):
            verify_email(user_id=1, codigo="000000")

    @patch("verso_integrations.core_client.requests.post")
    @override_settings(LOCAL_MODE=True)
    def test_kyc_handoff_local_fallback_on_404(self, mock_post):
        mock_post.return_value = MagicMock(status_code=404)
        result = kyc_handoff(user_id=7, return_url="https://anchor/callback")
        self.assertEqual(result.kyc_url, "https://anchor/callback")
        self.assertFalse(result.kyc_completed)

    @patch("verso_integrations.core_client.requests.post")
    def test_kyc_handoff_returns_url_from_payload(self, mock_post):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "kyc_completed": False,
                "kyc_url": "https://didit.example/onboard",
            },
        )
        result = kyc_handoff(user_id=7, return_url="https://anchor/callback")
        self.assertEqual(result.kyc_url, "https://didit.example/onboard")
        self.assertFalse(result.kyc_completed)
        mock_post.assert_called_once_with(
            "https://core.example/internal/kyc/handoff/",
            headers={"Authorization": "Bearer test-key"},
            json={"user_id": 7, "return_url": "https://anchor/callback"},
            timeout=15,
        )

    @patch("verso_integrations.core_client.requests.post")
    def test_kyc_handoff_already_completed(self, mock_post):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "kyc_completed": True,
                "kyc_url": "https://anchor/callback",
            },
        )
        result = kyc_handoff(user_id=7, return_url="https://anchor/callback")
        self.assertTrue(result.kyc_completed)

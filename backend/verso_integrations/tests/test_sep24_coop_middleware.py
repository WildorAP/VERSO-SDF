from django.test import TestCase, override_settings


@override_settings(
    DEBUG=False,
    SECURE_CROSS_ORIGIN_OPENER_POLICY="same-origin",
)
class Sep24CoopMiddlewareTests(TestCase):
    def test_sep24_info_omits_coop_header(self):
        response = self.client.get("/sep24/info")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Cross-Origin-Opener-Policy", response.headers)

    def test_admin_login_keeps_coop_header(self):
        response = self.client.get("/admin/login/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.headers.get("Cross-Origin-Opener-Policy"),
            "same-origin",
        )

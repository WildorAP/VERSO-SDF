from django.test import TestCase


class Sep10AuthTests(TestCase):
    def test_post_without_transaction_field_returns_400(self):
        response = self.client.post("/auth", data={}, content_type="application/json")
        self.assertEqual(response.status_code, 400)

    def test_post_with_non_string_transaction_returns_400(self):
        response = self.client.post(
            "/auth", data={"transaction": 12345}, content_type="application/json"
        )
        self.assertEqual(response.status_code, 400)

    def test_post_with_malformed_xdr_returns_400_not_500(self):
        response = self.client.post(
            "/auth",
            data={"transaction": "this-is-not-a-valid-xdr-string"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
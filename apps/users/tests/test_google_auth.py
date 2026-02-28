from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse


User = get_user_model()


@override_settings(
    GOOGLE_OAUTH_CLIENT_ID="google-client-id",
    GOOGLE_OAUTH_CLIENT_SECRET="google-client-secret",
)
class GoogleAuthTests(TestCase):
    def test_google_start_redirects_and_persists_state(self):
        response = self.client.get(reverse("google_auth_start"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("accounts.google.com/o/oauth2/v2/auth", response.url)
        self.assertIn("client_id=google-client-id", response.url)

        session = self.client.session
        self.assertTrue(session.get("google_oauth_state"))

    @patch("apps.users.views.requests.get")
    @patch("apps.users.views.requests.post")
    def test_google_callback_creates_new_user(self, mock_post, mock_get):
        token_response = Mock()
        token_response.raise_for_status.return_value = None
        token_response.json.return_value = {"access_token": "token-123"}
        mock_post.return_value = token_response

        userinfo_response = Mock()
        userinfo_response.raise_for_status.return_value = None
        userinfo_response.json.return_value = {
            "email": "newuser@example.com",
            "email_verified": True,
            "given_name": "New",
            "family_name": "User",
        }
        mock_get.return_value = userinfo_response

        session = self.client.session
        session["google_oauth_state"] = "state-abc"
        session.save()

        response = self.client.get(
            reverse("google_auth_callback"),
            {"code": "auth-code", "state": "state-abc"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("home"))
        self.assertTrue(User.objects.filter(email="newuser@example.com").exists())
        self.assertIn("_auth_user_id", self.client.session)

    @patch("apps.users.views.requests.get")
    @patch("apps.users.views.requests.post")
    def test_google_callback_logs_in_existing_user(self, mock_post, mock_get):
        user = User.objects.create_user(
            username="existing",
            email="existing@example.com",
            password="x",
        )

        token_response = Mock()
        token_response.raise_for_status.return_value = None
        token_response.json.return_value = {"access_token": "token-123"}
        mock_post.return_value = token_response

        userinfo_response = Mock()
        userinfo_response.raise_for_status.return_value = None
        userinfo_response.json.return_value = {
            "email": "existing@example.com",
            "email_verified": True,
        }
        mock_get.return_value = userinfo_response

        session = self.client.session
        session["google_oauth_state"] = "state-existing"
        session["google_oauth_next"] = "/predictions/"
        session.save()

        response = self.client.get(
            reverse("google_auth_callback"),
            {"code": "auth-code", "state": "state-existing"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/predictions/")
        self.assertEqual(User.objects.filter(email="existing@example.com").count(), 1)
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.id)



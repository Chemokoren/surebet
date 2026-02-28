import re
import secrets
from urllib.parse import urlencode

import requests
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.generic import View

User = get_user_model()

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"
GOOGLE_SCOPES = ["openid", "email", "profile"]


def _safe_next_url(request, next_url):
    if not next_url:
        return ""
    if url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return next_url
    return ""


def _generate_unique_username(email):
    local_part = email.split("@")[0].lower()
    slug = re.sub(r"[^a-z0-9_]+", "", local_part).strip("_")
    base = slug[:20] or "user"
    candidate = base
    suffix = 1
    while User.objects.filter(username=candidate).exists():
        candidate = f"{base[:14]}_{suffix}"
        suffix += 1
    return candidate


class GoogleAuthStartView(View):
    def get(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return HttpResponseRedirect(reverse("home"))

        client_id = getattr(settings, "GOOGLE_OAUTH_CLIENT_ID", "")
        if not client_id:
            messages.error(
                request,
                "Google login is not configured yet. Please use email/password login.",
            )
            return HttpResponseRedirect(reverse("login"))

        state = secrets.token_urlsafe(24)
        request.session["google_oauth_state"] = state

        next_url = _safe_next_url(request, request.GET.get("next", ""))
        if next_url:
            request.session["google_oauth_next"] = next_url

        redirect_uri = request.build_absolute_uri(reverse("google_auth_callback"))
        query_params = urlencode(
            {
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": " ".join(GOOGLE_SCOPES),
                "state": state,
                "prompt": "select_account",
                "access_type": "online",
            }
        )
        return HttpResponseRedirect(f"{GOOGLE_AUTH_URL}?{query_params}")


class GoogleAuthCallbackView(View):
    def get(self, request, *args, **kwargs):
        expected_state = request.session.pop("google_oauth_state", "")
        returned_state = request.GET.get("state", "")
        if not expected_state or expected_state != returned_state:
            messages.error(request, "Google login failed. Please try again.")
            return HttpResponseRedirect(reverse("login"))

        if request.GET.get("error"):
            messages.error(request, "Google login was cancelled.")
            return HttpResponseRedirect(reverse("login"))

        code = request.GET.get("code", "")
        if not code:
            messages.error(request, "Google login failed to return an authorization code.")
            return HttpResponseRedirect(reverse("login"))

        client_id = getattr(settings, "GOOGLE_OAUTH_CLIENT_ID", "")
        client_secret = getattr(settings, "GOOGLE_OAUTH_CLIENT_SECRET", "")
        if not client_id or not client_secret:
            messages.error(
                request,
                "Google login is not configured yet. Please use email/password login.",
            )
            return HttpResponseRedirect(reverse("login"))

        redirect_uri = request.build_absolute_uri(reverse("google_auth_callback"))
        try:
            token_response = requests.post(
                GOOGLE_TOKEN_URL,
                data={
                    "code": code,
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code",
                },
                timeout=10,
            )
            token_response.raise_for_status()
            access_token = token_response.json().get("access_token", "")
            if not access_token:
                raise ValueError("Missing access token")

            userinfo_response = requests.get(
                GOOGLE_USERINFO_URL,
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=10,
            )
            userinfo_response.raise_for_status()
            userinfo = userinfo_response.json()
        except (requests.RequestException, ValueError):
            messages.error(request, "Could not verify your Google account. Please try again.")
            return HttpResponseRedirect(reverse("login"))

        email = (userinfo.get("email") or "").strip().lower()
        email_verified = userinfo.get("email_verified", False)
        if not email or not email_verified:
            messages.error(request, "Your Google account must have a verified email.")
            return HttpResponseRedirect(reverse("login"))

        user = User.objects.filter(email__iexact=email).first()
        created = False
        if user is None:
            user = User.objects.create(
                username=_generate_unique_username(email),
                email=email,
                first_name=userinfo.get("given_name", "")[:150],
                last_name=userinfo.get("family_name", "")[:150],
            )
            user.set_unusable_password()
            user.save(update_fields=["password"])
            created = True

        login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        if created:
            messages.success(request, "Your account was created with Google successfully.")
        else:
            messages.success(request, "You are now signed in with Google.")

        next_url = _safe_next_url(request, request.session.pop("google_oauth_next", ""))
        if next_url:
            return HttpResponseRedirect(next_url)
        return HttpResponseRedirect(reverse("home"))

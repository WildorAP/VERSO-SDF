"""
OAuth2 client flow: the Anchor never sees a VERSO password. It only
redirects the browser to VERSO's login page, then exchanges a one-time
code for an access token server-to-server, and asks VERSO who owns it.
"""
import json
import secrets

import requests
from django.conf import settings
from django.contrib.auth import login as django_login
from django.contrib.auth import logout as django_logout
from django.contrib.auth.models import User
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods
from django.contrib.auth.decorators import login_required
import base64
import hashlib


from .models import AnchorProfile

OAUTH_STATE_SESSION_KEY = "verso_oauth_state"
PKCE_VERIFIER_SESSION_KEY = "verso_oauth_code_verifier"


def _generate_pkce_pair():
    verifier = secrets.token_urlsafe(64)[:128]
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


def login_start(request):
    state = secrets.token_urlsafe(24)
    code_verifier, code_challenge = _generate_pkce_pair()
    request.session[OAUTH_STATE_SESSION_KEY] = state
    request.session[PKCE_VERIFIER_SESSION_KEY] = code_verifier

    params = {
        "response_type": "code",
        "client_id": settings.VERSO_OAUTH_CLIENT_ID,
        "redirect_uri": settings.VERSO_OAUTH_REDIRECT_URI,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    query = "&".join(f"{k}={requests.utils.quote(str(v))}" for k, v in params.items())
    return redirect(f"{settings.VERSO_OAUTH_AUTHORIZE_URL}?{query}")


def oauth_callback(request):
    error = request.GET.get("error")
    if error:
        return render(
            request,
            "accounts/login_error.html",
            {"error": f"VERSO rechazó el inicio de sesión: {error}"},
        )

    returned_state = request.GET.get("state")
    expected_state = request.session.pop(OAUTH_STATE_SESSION_KEY, None)
    if not expected_state or returned_state != expected_state:
        return render(
            request,
            "accounts/login_error.html",
            {"error": "Estado de sesión inválido. Por favor, intenta iniciar sesión de nuevo."},
        )

    code = request.GET.get("code")
    if not code:
        return render(
            request, "accounts/login_error.html", {"error": "VERSO no envió un código de autorización."}
        )

    code_verifier = request.session.pop(PKCE_VERIFIER_SESSION_KEY, None)
    token_response = requests.post(
        settings.VERSO_OAUTH_TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": settings.VERSO_OAUTH_REDIRECT_URI,
            "client_id": settings.VERSO_OAUTH_CLIENT_ID,
            "client_secret": settings.VERSO_OAUTH_CLIENT_SECRET,
            "code_verifier": code_verifier,
        },
        timeout=10,
    )

    if token_response.status_code != 200:
        return render(
            request,
            "accounts/login_error.html",
            {"error": "No se pudo validar la sesión con VERSO. Intenta de nuevo."},
        )

    access_token = token_response.json().get("access_token")

    userinfo_response = requests.get(
        settings.VERSO_OAUTH_USERINFO_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10,
    )
    if userinfo_response.status_code != 200:
        return render(
            request,
            "accounts/login_error.html",
            {"error": "No se pudo obtener tu información de VERSO."},
        )

    verso_user = userinfo_response.json()

    profile, _created = AnchorProfile.objects.select_related("user").get_or_create(
        verso_user_id=verso_user["id"],
        defaults={
            "user": _local_user_for(verso_user),
            "stellar_public_key": verso_user.get("stellar_public_key"),
        },
    )
    if profile.stellar_public_key != verso_user.get("stellar_public_key"):
        profile.stellar_public_key = verso_user.get("stellar_public_key")
        profile.save(update_fields=["stellar_public_key", "updated_at"])

    django_login(request, profile.user, backend="django.contrib.auth.backends.ModelBackend")
    return redirect("dashboard")


def _local_user_for(verso_user: dict) -> User:
    """Create (once) a local, passwordless mirror user for this VERSO account."""
    local_username = f"verso_{verso_user['id']}"
    user, _created = User.objects.get_or_create(
        username=local_username,
        defaults={"email": verso_user.get("email", "")},
    )
    if not user.has_usable_password():
        user.set_unusable_password()
        user.save(update_fields=["password"])
    return user


@require_http_methods(["GET", "POST"])
def register(request):
    if request.method == "GET":
        return render(request, "accounts/register.html")

    email = request.POST.get("email", "").strip()
    password = request.POST.get("password", "")
    nombre = request.POST.get("nombre", "").strip()
    apellidos = request.POST.get("apellidos", "").strip()

    if not email or not password:
        return render(
            request,
            "accounts/register.html",
            {"error": "Email y contraseña son obligatorios."},
        )

    response = requests.post(
        f"{settings.VERSO_CORE_API_URL}/internal/users/register/",
        headers={"Authorization": f"Bearer {settings.VERSO_CORE_API_KEY}"},
        json={"email": email, "password": password, "nombre": nombre, "apellidos": apellidos},
        timeout=10,
    )

    if response.status_code == 409:
        return render(
            request,
            "accounts/register.html",
            {"error": "Ese email ya está registrado en VERSO."},
        )
    if response.status_code != 201:
        return render(
            request,
            "accounts/register.html",
            {"error": "No se pudo completar el registro. Intenta de nuevo más tarde."},
        )

    data = response.json()
    request.session["pending_verso_user_id"] = data["user_id"]
    request.session["pending_verso_email"] = data["email"]
    return redirect("accounts:verify_email")


@require_http_methods(["GET", "POST"])
def verify_email(request):
    user_id = request.session.get("pending_verso_user_id")
    email = request.session.get("pending_verso_email")

    if not user_id:
        return redirect("accounts:register")

    if request.method == "GET":
        return render(request, "accounts/verify_email.html", {"email": email})

    codigo = request.POST.get("codigo", "").strip()

    response = requests.post(
        f"{settings.VERSO_CORE_API_URL}/internal/users/verify-email/",
        headers={"Authorization": f"Bearer {settings.VERSO_CORE_API_KEY}"},
        json={"user_id": user_id, "codigo": codigo},
        timeout=10,
    )

    if response.status_code != 200:
        return render(
            request,
            "accounts/verify_email.html",
            {"email": email, "error": "Código inválido o expirado."},
        )

    request.session.pop("pending_verso_user_id", None)
    request.session.pop("pending_verso_email", None)
    return redirect("accounts:login")

def logout_view(request):
    django_logout(request)
    return redirect("principal_anchor")

@login_required(login_url="accounts:login")
def dashboard(request):
    profile = getattr(request.user, "anchor_profile", None)
    return render(
        request,
        "accounts/dashboard.html",
        {
            "username": request.user.username,
            "stellar_public_key": profile.stellar_public_key if profile else None,
        },
    )
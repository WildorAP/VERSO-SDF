"""
HTTP client for VERSO Core internal APIs (register, verify email, DIDIT).

Reuses the contract exercised in feature/oauth2-anchor-sso accounts/views.py.
"""

from __future__ import annotations

from dataclasses import dataclass

import requests
from django.conf import settings


class CoreClientError(Exception):
    """VERSO Core API error."""


@dataclass(frozen=True)
class RegisterResult:
    user_id: int
    email: str


@dataclass(frozen=True)
class UserStatusResult:
    user_id: int
    email: str
    registered: bool = True
    active: bool = True
    email_verified: bool = False
    kyc_completed: bool = False
    stellar_public_key: str | None = None
    stellar_linked: bool = False


@dataclass(frozen=True)
class KycHandoffResult:
    kyc_url: str | None
    kyc_completed: bool = False


def _parse_user_status(payload: dict) -> UserStatusResult:
    stellar_key = payload.get("stellar_public_key")
    return UserStatusResult(
        user_id=int(payload["user_id"]),
        email=str(payload["email"]),
        registered=bool(payload.get("registered", True)),
        active=bool(payload.get("active", True)),
        email_verified=bool(payload.get("email_verified")),
        kyc_completed=bool(payload.get("kyc_completed")),
        stellar_public_key=str(stellar_key) if stellar_key else None,
        stellar_linked=bool(payload.get("stellar_linked")),
    )


def _internal_headers() -> dict[str, str]:
    if not settings.VERSO_CORE_API_KEY:
        raise CoreClientError("VERSO_CORE_API_KEY is not configured.")
    return {"Authorization": f"Bearer {settings.VERSO_CORE_API_KEY}"}


def _base_url() -> str:
    return settings.VERSO_CORE_API_URL.rstrip("/")


def register_user(
    *,
    email: str,
    password: str,
    nombre: str,
    apellidos: str,
    stellar_public_key: str,
) -> RegisterResult:
    try:
        response = requests.post(
            f"{_base_url()}/internal/users/register/",
            headers=_internal_headers(),
            json={
                "email": email,
                "password": password,
                "nombre": nombre,
                "apellidos": apellidos,
                "stellar_public_key": stellar_public_key,
            },
            timeout=10,
        )
    except requests.RequestException as exc:
        raise CoreClientError(f"Failed to register user: {exc}") from exc

    if response.status_code == 409:
        try:
            detail = response.json().get("error", "")
        except ValueError:
            detail = ""
        raise CoreClientError(
            detail or "That email is already registered in VERSO."
        )
    if response.status_code != 201:
        try:
            detail = response.json().get("error", "")
        except ValueError:
            detail = ""
        raise CoreClientError(
            detail or f"Could not complete registration with VERSO Core (HTTP {response.status_code})."
        )

    payload = response.json()
    return RegisterResult(user_id=int(payload["user_id"]), email=str(payload["email"]))


def user_status(*, email: str) -> UserStatusResult:
    try:
        response = requests.get(
            f"{_base_url()}/internal/users/status/",
            headers=_internal_headers(),
            params={"email": email.strip().lower()},
            timeout=10,
        )
    except requests.RequestException as exc:
        raise CoreClientError(f"Failed to look up user: {exc}") from exc

    if response.status_code != 200:
        raise CoreClientError(
            f"Could not look up user in VERSO Core (HTTP {response.status_code})."
        )

    payload = response.json()
    if not payload.get("registered"):
        raise CoreClientError("That email is not registered in VERSO.")
    return _parse_user_status(payload)


def login_user(*, email: str, password: str) -> UserStatusResult:
    try:
        response = requests.post(
            f"{_base_url()}/internal/users/login/",
            headers=_internal_headers(),
            json={"email": email.strip().lower(), "password": password},
            timeout=10,
        )
    except requests.RequestException as exc:
        raise CoreClientError(f"Failed to log in: {exc}") from exc

    if response.status_code == 401:
        raise CoreClientError("Correo o contraseña incorrectos.")
    if response.status_code != 200:
        try:
            detail = response.json().get("error", "")
        except ValueError:
            detail = ""
        raise CoreClientError(
            detail or f"Could not log in with VERSO Core (HTTP {response.status_code})."
        )

    return _parse_user_status(response.json())


def link_stellar_key(*, user_id: int, stellar_public_key: str) -> None:
    try:
        response = requests.post(
            f"{_base_url()}/internal/users/link-stellar-key/",
            headers=_internal_headers(),
            json={"user_id": user_id, "stellar_public_key": stellar_public_key},
            timeout=10,
        )
    except requests.RequestException as exc:
        raise CoreClientError(f"Failed to link Stellar key: {exc}") from exc

    if response.status_code == 409:
        raise CoreClientError(
            "Esta cuenta VERSO ya está vinculada a otra wallet Stellar."
        )
    if response.status_code != 200:
        raise CoreClientError("Could not link your Stellar wallet to VERSO.")


def verify_email(*, user_id: int, codigo: str) -> None:
    try:
        response = requests.post(
            f"{_base_url()}/internal/users/verify-email/",
            headers=_internal_headers(),
            json={"user_id": user_id, "codigo": codigo},
            timeout=10,
        )
    except requests.RequestException as exc:
        raise CoreClientError(f"Failed to verify email: {exc}") from exc

    if response.status_code != 200:
        raise CoreClientError("Invalid or expired verification code.")


def kyc_handoff(*, user_id: int, return_url: str) -> KycHandoffResult:
    """
    Ask VERSO Core to start DIDIT for ``user_id``.

    VERSO creates the DIDIT session and returns ``kyc_url`` for browser redirect.
    When ``kyc_completed`` is already true, ``kyc_url`` is the anchor ``return_url``.
    """
    try:
        response = requests.post(
            f"{_base_url()}/internal/kyc/handoff/",
            headers=_internal_headers(),
            json={"user_id": user_id, "return_url": return_url},
            timeout=15,
        )
    except requests.RequestException as exc:
        if settings.LOCAL_MODE:
            return KycHandoffResult(kyc_url=return_url, kyc_completed=False)
        raise CoreClientError(f"Failed to start KYC handoff: {exc}") from exc

    if response.status_code in {404, 501} and settings.LOCAL_MODE:
        return KycHandoffResult(kyc_url=return_url, kyc_completed=False)
    if response.status_code != 200:
        try:
            detail = response.json().get("error", "")
        except ValueError:
            detail = ""
        raise CoreClientError(
            detail or "Could not start KYC verification with VERSO Core."
        )

    payload = response.json()
    kyc_completed = bool(payload.get("kyc_completed"))
    kyc_url = payload.get("kyc_url") or payload.get("didit_url") or payload.get("url")
    if kyc_completed:
        return KycHandoffResult(
            kyc_url=str(kyc_url) if kyc_url else return_url,
            kyc_completed=True,
        )
    if not kyc_url:
        raise CoreClientError("VERSO Core did not return a KYC URL.")
    return KycHandoffResult(kyc_url=str(kyc_url), kyc_completed=False)

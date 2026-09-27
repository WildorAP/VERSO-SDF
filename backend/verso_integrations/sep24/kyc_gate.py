"""
SEP-24 onboarding gate: resolve VERSO client / KYC status for a Stellar account.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings

from verso_integrations.core_client import CoreClientError, user_status

KYC_APPROVED = "approved"
KYC_PENDING = "pending"
KYC_REJECTED = "rejected"
KYC_NOT_FOUND = "not_found"


@dataclass(frozen=True)
class ClientKycStatus:
    status: str
    client_id: int | None = None
    email: str | None = None
    didit_url: str | None = None


def _session_prefix(transaction_id) -> str:
    return f"sep24:{transaction_id}"


def session_get(request, transaction_id, suffix: str):
    return request.session.get(f"{_session_prefix(transaction_id)}:{suffix}")


def session_set(request, transaction_id, suffix: str, value) -> None:
    request.session[f"{_session_prefix(transaction_id)}:{suffix}"] = value
    if hasattr(request.session, "modified"):
        request.session.modified = True


def session_pop(request, transaction_id, suffix: str):
    key = f"{_session_prefix(transaction_id)}:{suffix}"
    value = request.session.pop(key, None)
    if hasattr(request.session, "modified"):
        request.session.modified = True
    return value


def mark_session_kyc_approved(request, transaction_id) -> None:
    session_set(request, transaction_id, "kyc_approved", True)


def is_session_kyc_approved(request, transaction_id) -> bool:
    return bool(session_get(request, transaction_id, "kyc_approved"))


def set_pending_verso_user(request, transaction_id, *, user_id: int, email: str) -> None:
    session_set(request, transaction_id, "verso_user_id", user_id)
    session_set(request, transaction_id, "verso_email", email)


def get_pending_verso_user(request, transaction_id) -> tuple[int | None, str | None]:
    user_id = session_get(request, transaction_id, "verso_user_id")
    email = session_get(request, transaction_id, "verso_email")
    return user_id, email


def mark_email_verified(request, transaction_id) -> None:
    session_set(request, transaction_id, "email_verified", True)


def is_email_verified(request, transaction_id) -> bool:
    return bool(session_get(request, transaction_id, "email_verified"))


def wants_register(request, transaction_id) -> bool:
    return bool(session_get(request, transaction_id, "wants_register"))


def mark_wants_register(request, transaction_id) -> None:
    session_set(request, transaction_id, "wants_register", True)
    mark_onboarding_register_flow(request, transaction_id)


def clear_wants_register(request, transaction_id) -> None:
    session_pop(request, transaction_id, "wants_register")
    mark_onboarding_login_flow(request, transaction_id)


def mark_onboarding_register_flow(request, transaction_id) -> None:
    session_set(request, transaction_id, "onboarding_flow", "register")


def mark_onboarding_login_flow(request, transaction_id) -> None:
    session_set(request, transaction_id, "onboarding_flow", "login")


def is_register_onboarding_flow(request, transaction_id) -> bool:
    return session_get(request, transaction_id, "onboarding_flow") == "register"


def apply_verso_user_session(request, transaction_id, status) -> None:
    """Persist Core user status in the SEP-24 session after login/register."""
    set_pending_verso_user(
        request,
        transaction_id,
        user_id=status.user_id,
        email=status.email,
    )
    if status.email_verified:
        mark_email_verified(request, transaction_id)
    if status.kyc_completed:
        mark_session_kyc_approved(request, transaction_id)


def ensure_stellar_wallet_linked(transaction, status) -> None:
    """Link the SEP-24 wallet to the authenticated VERSO user when needed."""
    from verso_integrations.core_client import CoreClientError, link_stellar_key

    if (
        status.stellar_linked
        and status.stellar_public_key
        and status.stellar_public_key != transaction.stellar_account
    ):
        raise CoreClientError(
            "Esta cuenta VERSO ya está vinculada a otra wallet Stellar."
        )
    if not status.stellar_linked:
        link_stellar_key(
            user_id=status.user_id,
            stellar_public_key=transaction.stellar_account,
        )


def parse_client_payload(payload: dict) -> ClientKycStatus:
    raw_status = (
        payload.get("kyc_status")
        or payload.get("status")
        or KYC_APPROVED
    )
    status = str(raw_status).lower()
    if status not in {KYC_APPROVED, KYC_PENDING, KYC_REJECTED, KYC_NOT_FOUND}:
        status = KYC_PENDING

    client_id = payload.get("client_id") or payload.get("id")
    if client_id is not None:
        client_id = int(client_id)

    return ClientKycStatus(
        status=status,
        client_id=client_id,
        email=payload.get("email"),
        didit_url=payload.get("didit_url") or payload.get("onboarding_url"),
    )


def sync_email_verified_from_core(request, transaction_id) -> bool:
    """Align session with VERSO when email was verified but the webview session was not updated."""
    if is_email_verified(request, transaction_id):
        return True
    _user_id, email = get_pending_verso_user(request, transaction_id)
    if not email:
        return False
    try:
        status = user_status(email=email)
    except CoreClientError:
        return False
    if status.email_verified:
        mark_email_verified(request, transaction_id)
        return True
    return False


def sync_kyc_from_core(request, transaction_id) -> ClientKycStatus | None:
    """Refresh KYC flags from ``GET /internal/users/status/`` when email is known."""
    _user_id, email = get_pending_verso_user(request, transaction_id)
    if not email:
        return None
    try:
        status = user_status(email=email)
    except CoreClientError:
        return None
    if status.kyc_completed:
        mark_session_kyc_approved(request, transaction_id)
        return ClientKycStatus(
            status=KYC_APPROVED,
            client_id=status.user_id,
            email=status.email,
        )
    return ClientKycStatus(
        status=KYC_NOT_FOUND,
        client_id=status.user_id,
        email=status.email,
    )


def lookup_client_kyc(stellar_public_key: str, *, request=None, transaction_id=None) -> ClientKycStatus:
    """
    Resolve KYC status for the SEP-24 session.

    ``VERSO_MOCK_KYC`` forces a status in tests/dev. Session flag ``kyc_approved``
    overrides lookup after DIDIT completes. Otherwise we ask VERSO Core via the
    authenticated user's email stored in the SEP-24 session.
    """
    if request is not None and transaction_id is not None:
        if is_session_kyc_approved(request, transaction_id):
            return ClientKycStatus(status=KYC_APPROVED)

    mock = getattr(settings, "VERSO_MOCK_KYC", "").strip().lower()
    if mock in {KYC_APPROVED, KYC_PENDING, KYC_REJECTED, KYC_NOT_FOUND}:
        return ClientKycStatus(status=mock)

    if request is not None and transaction_id is not None:
        synced = sync_kyc_from_core(request, transaction_id)
        if synced is not None:
            return synced

    return ClientKycStatus(status=KYC_NOT_FOUND)


def onboarding_step(request, transaction) -> str:
    """
    Next onboarding step inside the SEP-24 webview.

    Returns one of: login | register | deposit | verify_email | didit | pending | rejected
    """
    kyc = lookup_client_kyc(
        transaction.stellar_account,
        request=request,
        transaction_id=transaction.id,
    )
    if kyc.status == KYC_APPROVED:
        return "deposit"
    if kyc.status == KYC_REJECTED:
        return "rejected"
    if kyc.status == KYC_PENDING:
        return "pending"

    sync_email_verified_from_core(request, transaction.id)

    user_id, _email = get_pending_verso_user(request, transaction.id)
    if user_id and is_email_verified(request, transaction.id):
        return "didit"
    if user_id:
        return "verify_email"
    if wants_register(request, transaction.id):
        return "register"
    return "login"

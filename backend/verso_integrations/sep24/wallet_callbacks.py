"""
Persist SEP-24 wallet callback query params across onboarding redirects.

Wallets (Lobstr, Demo Wallet, etc.) open the interactive webview with
``callback=postMessage`` and ``on_change_callback=postMessage``. Polaris
``more_info`` + ``callback.js`` only trigger auto-signing when those params
reach ``/sep24/transaction/more_info?initialLoad=true&callback=postMessage``.

Our onboarding middleware redirects to ``/sep24/onboarding/`` before Polaris
can read them; this module stores them in the session and re-appends them on
every redirect back to webapp or more_info.
"""

from __future__ import annotations

from urllib.parse import urlencode

from django.http import HttpRequest

CALLBACK_SESSION_PREFIX = "sep24_wallet_callback"
ON_CHANGE_SESSION_PREFIX = "sep24_wallet_on_change"


def _session_key(prefix: str, transaction_id: str) -> str:
    return f"{prefix}:{transaction_id}"


def persist_sep24_wallet_callbacks(request: HttpRequest, transaction_id: str) -> None:
    """Save callback params from the query string into the webview session."""
    callback = request.GET.get("callback")
    on_change = request.GET.get("on_change_callback")
    if callback:
        request.session[_session_key(CALLBACK_SESSION_PREFIX, transaction_id)] = callback
    if on_change:
        request.session[_session_key(ON_CHANGE_SESSION_PREFIX, transaction_id)] = on_change
        _sync_on_change_callback(transaction_id, on_change)
    if callback or on_change:
        request.session.modified = True


def _sync_on_change_callback(transaction_id: str, on_change: str) -> None:
    from polaris.models import Transaction

    try:
        transaction = Transaction.objects.get(id=transaction_id)
    except Transaction.DoesNotExist:
        return
    if transaction.on_change_callback != on_change:
        transaction.on_change_callback = on_change
        transaction.save(update_fields=["on_change_callback"])


def sep24_wallet_callback_query(request: HttpRequest, transaction_id: str) -> dict[str, str]:
    """Return callback params from the current request or session."""
    callback = request.GET.get("callback") or request.session.get(
        _session_key(CALLBACK_SESSION_PREFIX, transaction_id)
    )
    on_change = request.GET.get("on_change_callback") or request.session.get(
        _session_key(ON_CHANGE_SESSION_PREFIX, transaction_id)
    )
    params: dict[str, str] = {}
    if callback:
        params["callback"] = callback
    if on_change:
        params["on_change_callback"] = on_change
    return params


def append_wallet_callbacks_to_url(
    request: HttpRequest, transaction_id: str, base_query: dict[str, str]
) -> str:
    """Build a query string including persisted wallet callback params."""
    merged = {**base_query, **sep24_wallet_callback_query(request, transaction_id)}
    return urlencode(merged)

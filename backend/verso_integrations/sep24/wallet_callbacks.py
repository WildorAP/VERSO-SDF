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
from polaris.models import Transaction
from rest_framework.request import Request as DrfRequest

CALLBACK_SESSION_PREFIX = "sep24_wallet_callback"
ON_CHANGE_SESSION_PREFIX = "sep24_wallet_on_change"
POSTMESSAGE_CALLBACK = "postMessage"


def _session_key(prefix: str, transaction_id: str) -> str:
    return f"{prefix}:{transaction_id}"


def _infer_postmessage_callback(on_change: str | None) -> str | None:
    if on_change and on_change.lower() == "postmessage":
        return POSTMESSAGE_CALLBACK
    return None


def _sync_on_change_callback(transaction_id: str, on_change: str) -> None:
    try:
        transaction = Transaction.objects.get(id=transaction_id)
    except Transaction.DoesNotExist:
        return
    if transaction.on_change_callback != on_change:
        transaction.on_change_callback = on_change
        transaction.save(update_fields=["on_change_callback"])


def persist_sep24_wallet_callbacks(request: HttpRequest, transaction_id: str) -> None:
    """Save callback params from the query string into the webview session."""
    callback = request.GET.get("callback")
    on_change = request.GET.get("on_change_callback")
    if not callback:
        callback = _infer_postmessage_callback(on_change)
    if callback:
        request.session[_session_key(CALLBACK_SESSION_PREFIX, transaction_id)] = callback
        if callback.lower() == "postmessage":
            _sync_on_change_callback(transaction_id, POSTMESSAGE_CALLBACK)
    if on_change:
        request.session[_session_key(ON_CHANGE_SESSION_PREFIX, transaction_id)] = on_change
        _sync_on_change_callback(transaction_id, on_change)
    if callback or on_change:
        request.session.modified = True


def withdraw_awaiting_wallet_payment(transaction, meta) -> bool:
    if transaction.kind != Transaction.KIND.withdrawal:
        return False
    if meta is None or meta.payout_confirmed_at is None:
        return False
    if transaction.status in {
        Transaction.STATUS.completed,
        Transaction.STATUS.error,
    }:
        return False
    return not (transaction.stellar_transaction_id or "").strip()


def sep24_wallet_callback_query(
    request: HttpRequest,
    transaction_id: str,
    *,
    transaction=None,
    meta=None,
) -> dict[str, str]:
    """Return callback params from the request, session, transaction, or SEP-24 defaults."""
    callback = request.GET.get("callback") or request.session.get(
        _session_key(CALLBACK_SESSION_PREFIX, transaction_id)
    )
    on_change = request.GET.get("on_change_callback") or request.session.get(
        _session_key(ON_CHANGE_SESSION_PREFIX, transaction_id)
    )
    if transaction is not None:
        persisted_on_change = (transaction.on_change_callback or "").strip()
        if not on_change and persisted_on_change:
            on_change = persisted_on_change
        if not callback:
            callback = _infer_postmessage_callback(on_change or persisted_on_change)
        if meta is None:
            from verso_integrations.models import Sep24WithdrawMeta

            meta = Sep24WithdrawMeta.objects.filter(transaction=transaction).first()
        if withdraw_awaiting_wallet_payment(transaction, meta):
            callback = callback or POSTMESSAGE_CALLBACK
            on_change = on_change or persisted_on_change or POSTMESSAGE_CALLBACK
    params: dict[str, str] = {}
    if callback:
        params["callback"] = callback
    if on_change:
        params["on_change_callback"] = on_change
    return params


def inject_wallet_callbacks_into_query(
    request: DrfRequest | HttpRequest,
    transaction: Transaction,
) -> bool:
    """
    Mutate the underlying Django GET QueryDict so Polaris' more_info sees
    ``callback=postMessage``. Returns True when any param was added.
    """
    django_request = request._request if isinstance(request, DrfRequest) else request
    query = django_request.GET.copy()
    query.mutable = True
    changed = False

    current_callback = (query.get("callback") or "").lower()
    if current_callback == "success":
        return False

    if not query.get("callback"):
        query["callback"] = POSTMESSAGE_CALLBACK
        changed = True
        _sync_on_change_callback(str(transaction.id), POSTMESSAGE_CALLBACK)
        if not transaction.on_change_callback:
            transaction.on_change_callback = POSTMESSAGE_CALLBACK

    if not query.get("on_change_callback"):
        on_change = transaction.on_change_callback or POSTMESSAGE_CALLBACK
        query["on_change_callback"] = on_change
        changed = True
        if not transaction.on_change_callback:
            _sync_on_change_callback(str(transaction.id), on_change)
            transaction.on_change_callback = on_change

    if not query.get("initialLoad"):
        query["initialLoad"] = "true"
        changed = True

    if changed:
        django_request.GET = query
    return changed


def append_wallet_callbacks_to_url(
    request: HttpRequest, transaction_id: str, base_query: dict[str, str]
) -> str:
    """Build a query string including persisted wallet callback params."""
    merged = {**base_query, **sep24_wallet_callback_query(request, transaction_id)}
    return urlencode(merged)

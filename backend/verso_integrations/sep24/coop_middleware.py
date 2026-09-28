"""
SEP-24 popup compatibility — disable COOP on interactive wallet webviews.

Django's SecurityMiddleware sets ``Cross-Origin-Opener-Policy: same-origin`` in
production. Demo Wallet / Lobstr open ``/sep24/`` in a cross-origin popup and
poll ``popup.closed`` while waiting for ``pending_user_transfer_start``. With
COOP ``same-origin``, the browser severs the opener relationship and reports
the popup as closed, so the wallet never submits the on-chain USDC payment.
"""

from __future__ import annotations


class Sep24PopupCOOPMiddleware:
    """Strip COOP from SEP-24 responses so wallet popups stay connected to the opener."""

    COOP_HEADER = "Cross-Origin-Opener-Policy"

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if request.path.startswith("/sep24/"):
            response.headers.pop(self.COOP_HEADER, None)
        return response

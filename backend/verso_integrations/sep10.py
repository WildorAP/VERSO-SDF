"""
SEP-10 auth view with broader invalid-transaction handling.

Polaris 2.6 only catches InvalidSep10ChallengeError/TypeError when parsing POST
transaction XDR. Malformed base64 can raise ValueError/EOFError/AttributeError
before that and surface as HTTP 500; the Anchor Validator expects 400.
"""

import binascii

from django.utils.translation import gettext
from rest_framework.response import Response

from polaris.sep10.views import SEP10Auth
from polaris.utils import render_error_response


class VersoSEP10Auth(SEP10Auth):
    def post(self, request, *_args, **_kwargs) -> Response:
        envelope_xdr = request.data.get("transaction")
        if not envelope_xdr:
            return render_error_response(gettext("'transaction' is required"))
        if not isinstance(envelope_xdr, str):
            return render_error_response(
                gettext("error while validating challenge: invalid transaction")
            )
        client_domain, error_response = self._validate_challenge_xdr(envelope_xdr)
        if error_response:
            return error_response
        return Response({"token": self._generate_jwt(envelope_xdr, client_domain)})

    @staticmethod
    def _validate_challenge_xdr(envelope_xdr: str):
        try:
            return SEP10Auth._validate_challenge_xdr(envelope_xdr)
        except (ValueError, EOFError, TypeError, AttributeError, binascii.Error) as exc:
            message = gettext("error while validating challenge: %s") % str(exc)
            return None, render_error_response(message)

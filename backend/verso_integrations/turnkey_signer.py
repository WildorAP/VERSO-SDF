"""
Turnkey signer for Stellar transactions (T3 — hot wallet custody).

The hot wallet's Ed25519 private key lives inside Turnkey and never reaches this
server. We send Turnkey the transaction hash (``sign_raw_payload``), receive the
64-byte signature (r || s), verify it locally against the wallet's public key and
only then attach it to the envelope.

Configuration (Django settings / environment):

    TURNKEY_SIGN_WITH         G... address of the Turnkey Stellar wallet (public)
    TURNKEY_ORGANIZATION_ID   Turnkey organization ID (public identifier)
    TURNKEY_API_PUBLIC_KEY    API key of the non-root service user ``verso-anchor``
    TURNKEY_API_PRIVATE_KEY   secret — only on the Railway service that signs (DEPOSITO)
    TURNKEY_API_BASE_URL      optional, defaults to https://api.turnkey.com

The service user must be non-root: root users bypass Turnkey policies.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from django.conf import settings
from stellar_sdk import Keypair, TransactionEnvelope
from stellar_sdk.decorated_signature import DecoratedSignature
from stellar_sdk.exceptions import BadSignatureError

logger = logging.getLogger(__name__)

DEFAULT_TURNKEY_API_BASE_URL = "https://api.turnkey.com"

ACTIVITY_COMPLETED = "ACTIVITY_STATUS_COMPLETED"
ACTIVITY_CONSENSUS_NEEDED = "ACTIVITY_STATUS_CONSENSUS_NEEDED"
ACTIVITY_REJECTED_STATUSES = ("ACTIVITY_STATUS_FAILED", "ACTIVITY_STATUS_REJECTED")


class TurnkeySigningError(Exception):
    """Base class for Turnkey signing failures."""


class TurnkeyConfigError(TurnkeySigningError):
    """Turnkey credentials are missing or invalid on this service."""


class TurnkeyUnavailableError(TurnkeySigningError):
    """Transient failure (network, 5xx, activity still processing). Safe to retry."""


class TurnkeyRejectedError(TurnkeySigningError):
    """Turnkey refused to sign, or returned a signature that does not verify. Needs an operator."""


def turnkey_sign_with() -> str:
    return (getattr(settings, "TURNKEY_SIGN_WITH", "") or "").strip()


def turnkey_enabled() -> bool:
    """Custody goes through Turnkey when the hot wallet address is configured."""
    return bool(turnkey_sign_with())


@dataclass(frozen=True)
class TurnkeyConfig:
    sign_with: str
    organization_id: str
    api_public_key: str
    api_private_key: str
    base_url: str = DEFAULT_TURNKEY_API_BASE_URL

    @classmethod
    def from_settings(cls) -> "TurnkeyConfig":
        values = {
            "TURNKEY_SIGN_WITH": turnkey_sign_with(),
            "TURNKEY_ORGANIZATION_ID": (getattr(settings, "TURNKEY_ORGANIZATION_ID", "") or "").strip(),
            "TURNKEY_API_PUBLIC_KEY": (getattr(settings, "TURNKEY_API_PUBLIC_KEY", "") or "").strip(),
            "TURNKEY_API_PRIVATE_KEY": (getattr(settings, "TURNKEY_API_PRIVATE_KEY", "") or "").strip(),
        }
        missing = [name for name, value in values.items() if not value]
        if missing:
            raise TurnkeyConfigError(f"Turnkey is not configured on this service: missing {', '.join(missing)}.")
        try:
            Keypair.from_public_key(values["TURNKEY_SIGN_WITH"])
        except Exception as exc:
            raise TurnkeyConfigError("TURNKEY_SIGN_WITH is not a valid Stellar public key.") from exc
        return cls(
            sign_with=values["TURNKEY_SIGN_WITH"],
            organization_id=values["TURNKEY_ORGANIZATION_ID"],
            api_public_key=values["TURNKEY_API_PUBLIC_KEY"],
            api_private_key=values["TURNKEY_API_PRIVATE_KEY"],
            base_url=(getattr(settings, "TURNKEY_API_BASE_URL", "") or DEFAULT_TURNKEY_API_BASE_URL).strip(),
        )


class TurnkeySigner:
    """Signs Stellar transaction envelopes with the Turnkey hot wallet."""

    def __init__(self, config: TurnkeyConfig | None = None, client=None):
        self.config = config or TurnkeyConfig.from_settings()
        self._client = client

    @property
    def public_key(self) -> str:
        return self.config.sign_with

    def _get_client(self):
        if self._client is None:
            from turnkey_api_key_stamper import ApiKeyStamper, ApiKeyStamperConfig
            from turnkey_http import TurnkeyClient

            self._client = TurnkeyClient(
                base_url=self.config.base_url,
                stamper=ApiKeyStamper(
                    ApiKeyStamperConfig(
                        api_public_key=self.config.api_public_key,
                        api_private_key=self.config.api_private_key,
                    )
                ),
                organization_id=self.config.organization_id,
            )
        return self._client

    def sign_hash(self, tx_hash: bytes) -> bytes:
        """Ask Turnkey to sign a 32-byte transaction hash. Returns the verified 64-byte signature."""
        from turnkey_sdk_types.errors import TurnkeyError, TurnkeyNetworkError
        from turnkey_sdk_types.generated.types import SignRawPayloadBody

        body = SignRawPayloadBody(
            signWith=self.config.sign_with,
            payload=tx_hash.hex(),
            encoding="PAYLOAD_ENCODING_HEXADECIMAL",
            hashFunction="HASH_FUNCTION_NOT_APPLICABLE",  # Ed25519 signs the raw hash
        )
        try:
            result = self._get_client().sign_raw_payload(body)
        except TurnkeyNetworkError as exc:
            status_code = getattr(exc, "status_code", None)
            if status_code is not None and 400 <= status_code < 500:
                raise TurnkeyRejectedError(f"Turnkey refused the request ({status_code}): {exc}") from exc
            raise TurnkeyUnavailableError(f"Turnkey is unreachable: {exc}") from exc
        except TurnkeyError as exc:
            raise TurnkeyUnavailableError(f"Turnkey error: {exc}") from exc

        status = getattr(getattr(result, "activity", None), "status", None)
        status = getattr(status, "value", status)
        if status == ACTIVITY_CONSENSUS_NEEDED:
            raise TurnkeyRejectedError("Turnkey activity needs consensus approval before signing.")
        if status in ACTIVITY_REJECTED_STATUSES:
            raise TurnkeyRejectedError(f"Turnkey did not sign the payload (activity status {status}).")
        if status != ACTIVITY_COMPLETED or not result.r or not result.s:
            raise TurnkeyUnavailableError(f"Turnkey activity did not complete (status {status}).")

        try:
            signature = bytes.fromhex(result.r + result.s)
        except ValueError as exc:
            raise TurnkeyRejectedError("Turnkey returned a malformed signature.") from exc

        try:
            Keypair.from_public_key(self.config.sign_with).verify(tx_hash, signature)
        except BadSignatureError as exc:
            raise TurnkeyRejectedError(
                "Turnkey signature does not verify against TURNKEY_SIGN_WITH; refusing to submit."
            ) from exc
        return signature

    def sign_envelope(self, envelope: TransactionEnvelope) -> TransactionEnvelope:
        """Append the hot wallet signature to ``envelope`` (in place) and return it."""
        signature = self.sign_hash(envelope.hash())
        keypair = Keypair.from_public_key(self.config.sign_with)
        envelope.signatures.append(DecoratedSignature(keypair.signature_hint(), signature))
        logger.info("Turnkey signed transaction %s", envelope.hash_hex())
        return envelope

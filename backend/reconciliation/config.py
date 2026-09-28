"""Reconciliation settings loaded from Django settings / environment."""

from __future__ import annotations

import os

from django.conf import settings
from stellar_sdk import Asset, Keypair

from verso_integrations.sep1 import TESTNET_PASSPHRASE, USDC_ISSUER_MAINNET, USDC_ISSUER_TESTNET


def network_passphrase() -> str:
    return os.environ.get("STELLAR_NETWORK_PASSPHRASE", TESTNET_PASSPHRASE)


def default_rpc_url() -> str:
    if network_passphrase() == TESTNET_PASSPHRASE:
        return "https://soroban-testnet.stellar.org"
    return ""


def stellar_rpc_url() -> str:
    url = getattr(settings, "STELLAR_RPC_URL", "") or os.environ.get("STELLAR_RPC_URL", "")
    url = (url or default_rpc_url()).strip()
    if not url:
        raise RuntimeError(
            "STELLAR_RPC_URL is required when not on Stellar testnet (set STELLAR_NETWORK_PASSPHRASE)."
        )
    return url


def hot_wallet() -> str:
    explicit = getattr(settings, "RECON_HOT_WALLET", "") or os.environ.get("RECON_HOT_WALLET", "")
    if explicit.strip():
        return explicit.strip()
    seed = os.environ.get("SIGNING_SEED", "").strip()
    if not seed:
        raise RuntimeError("RECON_HOT_WALLET or SIGNING_SEED must be configured.")
    return Keypair.from_secret(seed).public_key


def usdc_asset() -> Asset:
    if network_passphrase() == TESTNET_PASSPHRASE:
        return Asset("USDC", USDC_ISSUER_TESTNET)
    return Asset("USDC", USDC_ISSUER_MAINNET)


def usdc_contract_id() -> str:
    return usdc_asset().contract_id(network_passphrase())


def poll_seconds() -> int:
    return int(getattr(settings, "RECON_POLL_SECONDS", 5))


def reconcile_interval_seconds() -> int:
    return int(getattr(settings, "RECON_INTERVAL_SECONDS", 60))


def match_grace_seconds() -> int:
    return int(getattr(settings, "RECON_MATCH_GRACE_SECONDS", 600))


def max_stale_seconds() -> int:
    return int(getattr(settings, "RECON_MAX_STALE_SECONDS", 300))


def alert_backend() -> str:
    return getattr(settings, "RECON_ALERT_BACKEND", "log").strip().lower()


def cloudwatch_namespace() -> str:
    return getattr(settings, "RECON_CLOUDWATCH_NAMESPACE", "VERSO/AnchorReconciliation")


def recon_environment() -> str:
    return getattr(settings, "RECON_ENV", "testnet")


def sns_topic_arn() -> str:
    return getattr(settings, "RECON_SNS_TOPIC_ARN", "")


STROOPS = 10**7

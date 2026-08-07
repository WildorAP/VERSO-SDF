"""
Withdrawal integration stubs for SEP-24 off-ramp (Tranche 2).

Implement WithdrawalIntegration subclass and register in apps.py.
"""


def get_hot_wallet_address() -> str:
    """Return VERSO Stellar hot wallet public key."""
    # T2: read from env or KMS-backed config
    return "G_REPLACE_WITH_HOT_WALLET_PUBLIC_KEY"

"""
Deposit integration stubs for SEP-24 on-ramp (Tranche 2).

Implement DepositIntegration subclass and register in apps.py.
"""

# T2: from polaris.integrations import DepositIntegration


def get_cci_deposit_instructions(amount_pen: float, client_id: str) -> dict:
    """Return CCI/CCE bank transfer instructions for the user."""
    return {
        "bank_name": "BCP",
        "account_number": "XXXXXXXX",
        "reference": f"TXN-{client_id}",
        "amount_pen": amount_pen,
        "instructions": "Transfer via CCI/CCE from any Peruvian bank.",
    }

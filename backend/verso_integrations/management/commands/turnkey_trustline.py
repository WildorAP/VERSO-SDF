"""
T3: create the USDC trustline of the Turnkey hot wallet, signed by Turnkey.

The network (testnet / mainnet) and USDC issuer follow STELLAR_NETWORK_PASSPHRASE and
HORIZON_URI, like the rest of the anchor. Requires the TURNKEY_* variables of the
non-root service user (see verso_integrations/turnkey_signer.py).

    python manage.py turnkey_trustline --check   # only report the wallet status
    python manage.py turnkey_trustline           # sign with Turnkey and submit

Run it once per wallet; the trustline is permanent.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from polaris import settings as polaris_settings
from stellar_sdk import Asset, Server, TransactionBuilder
from stellar_sdk.exceptions import BaseRequestError, NotFoundError

from verso_integrations.polaris_setup import usdc_issuer
from verso_integrations.root import stellar_expert_tx_url
from verso_integrations.turnkey_signer import TurnkeySigner, TurnkeySigningError, turnkey_sign_with


class Command(BaseCommand):
    help = "Create the USDC trustline of the Turnkey hot wallet (signed by Turnkey)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--check",
            action="store_true",
            help="Only show the wallet balances and whether the trustline exists; sign nothing.",
        )

    def handle(self, *args, **options):
        wallet = turnkey_sign_with()
        if not wallet:
            raise CommandError("TURNKEY_SIGN_WITH is not configured.")
        usdc = Asset("USDC", usdc_issuer())
        server = Server(polaris_settings.HORIZON_URI)

        self.stdout.write(f"Network: {polaris_settings.STELLAR_NETWORK_PASSPHRASE}")
        self.stdout.write(f"Horizon: {polaris_settings.HORIZON_URI}")
        self.stdout.write(f"Wallet:  {wallet}")
        self.stdout.write(f"USDC:    {usdc.issuer}")

        try:
            account = server.accounts().account_id(wallet).call()
        except NotFoundError:
            raise CommandError("The wallet does not exist on this network yet: fund it with XLM first.")

        for balance in account["balances"]:
            code = balance.get("asset_code", "XLM")
            self.stdout.write(f"  {code}: {balance['balance']}")
        has_trustline = any(
            b.get("asset_code") == usdc.code and b.get("asset_issuer") == usdc.issuer for b in account["balances"]
        )
        if has_trustline:
            self.stdout.write(self.style.SUCCESS("USDC trustline already exists. Nothing to do."))
            return
        if options["check"]:
            self.stdout.write(self.style.WARNING("USDC trustline missing (--check: nothing signed)."))
            return

        try:
            signer = TurnkeySigner()
            envelope = (
                TransactionBuilder(
                    source_account=server.load_account(wallet),
                    network_passphrase=polaris_settings.STELLAR_NETWORK_PASSPHRASE,
                    base_fee=server.fetch_base_fee(),
                )
                .append_change_trust_op(asset=usdc)
                .set_timeout(180)
                .build()
            )
            signer.sign_envelope(envelope)
            self.stdout.write("Turnkey signature verified locally.")
            response = server.submit_transaction(envelope)
        except TurnkeySigningError as exc:
            raise CommandError(f"Turnkey did not sign: {exc}")
        except BaseRequestError as exc:
            raise CommandError(f"Stellar network rejected the transaction: {exc}")

        self.stdout.write(self.style.SUCCESS(f"Trustline created. Hash: {response['hash']}"))
        self.stdout.write(stellar_expert_tx_url(response["hash"]))

"""Build fake RPC EventInfo payloads for tests."""

from __future__ import annotations

import base64
import uuid
from datetime import datetime
from datetime import timezone as dt_timezone
from decimal import Decimal

from django.utils import timezone
from stellar_sdk import scval
from stellar_sdk import xdr as stellar_xdr
from reconciliation.models import SyncState
from stellar_sdk.soroban_rpc import EventInfo


def create_sync_state(**kwargs) -> SyncState:
    defaults = {
        "network_passphrase": "Test SDF Network ; September 2015",
        "hot_wallet": "GBTV5QYBPGHGT2SVUHCFRRKFFWUWHOEPKH7QAXGTJHFGFYZRIE24UOPB",
        "asset_contract_id": "CBIELTK6YBZJU5UP2WWQEUCYKLPU6AUNZ2BQ4WWFEIE3USCIHMXQDAMA",
        "start_ledger": 100,
        "cursor": "",
        "last_processed_ledger": 99,
        "opening_balance": Decimal("100.0000000"),
        "observation_started_at": timezone.now(),
        "last_success_at": timezone.now(),
    }
    defaults.update(kwargs)
    return SyncState.objects.create(**defaults)


def _topic(value) -> str:
    if isinstance(value, str) and value in ("transfer", "mint", "burn", "clawback"):
        return scval.to_symbol(value).to_xdr()
    if isinstance(value, str) and (
        value.startswith("G") or value.startswith("C") or value.startswith("M")
    ):
        return scval.to_address(value).to_xdr()
    return scval.to_string(value).to_xdr()


def transfer_event(
    *,
    event_id: str = "evt-1",
    tx_hash: str = "abc123" * 8,
    ledger: int = 100,
    from_account: str,
    to_account: str,
    amount_stroops: int = 100_000_000,
    muxed_bytes: bytes | None = None,
    asset_label: str = "USDC:GBBD47IF6LWK7P7MDEVSCWR7DPUWV3NY3DTQEVFL4NAT4AQH3ZLLFLA5",
) -> EventInfo:
    if muxed_bytes is not None:
        value_xdr = scval.to_map(
            {
                scval.to_symbol("amount"): scval.to_int128(amount_stroops),
                scval.to_symbol("to_muxed_id"): scval.to_bytes(muxed_bytes),
            }
        ).to_xdr()
    else:
        value_xdr = scval.to_int128(amount_stroops).to_xdr()

    return EventInfo(
        type="contract",
        ledger=ledger,
        ledgerClosedAt=datetime(2026, 9, 28, 12, 0, tzinfo=dt_timezone.utc),
        contractId="CBIELTK6YBZJU5UP2WWQEUCYKLPU6AUNZ2BQ4WWFEIE3USCIHMXQDAMA",
        id=event_id,
        pagingToken=event_id,
        topic=[
            _topic("transfer"),
            _topic(from_account),
            _topic(to_account),
            _topic(asset_label),
        ],
        value=value_xdr,
        inSuccessfulContractCall=True,
        txHash=tx_hash,
    )


def memo_hash_for_uuid(transaction_uuid: uuid.UUID) -> str:
    padded = transaction_uuid.hex.zfill(64)
    raw = bytes.fromhex(padded)
    return base64.b64encode(raw).decode("ascii")

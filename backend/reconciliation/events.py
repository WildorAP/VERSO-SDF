"""Decode CAP-67 USDC contract events from Stellar RPC."""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from stellar_sdk import scval
from stellar_sdk import xdr as stellar_xdr
from stellar_sdk.soroban_rpc import EventFilter, EventFilterType, EventInfo

from reconciliation.config import STROOPS

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class UsdcMovement:
    event_id: str
    ledger: int
    ledger_closed_at: datetime
    tx_hash: str
    event_type: str
    from_address: str
    to_address: str
    amount: Decimal
    memo_raw: str
    memo_type: str
    raw: dict[str, Any]


def _topic_native(topic_b64: str) -> Any:
    return scval.to_native(stellar_xdr.SCVal.from_xdr(topic_b64))


def _address_string(value: Any) -> str:
    if hasattr(value, "address"):
        return str(value.address)
    if isinstance(value, str):
        return value
    return str(value)


def _normalize_muxed_memo(raw: Any) -> tuple[str, str]:
    if isinstance(raw, bytes):
        return base64.b64encode(raw).decode("ascii"), "hash"
    if isinstance(raw, int):
        return str(raw), "id"
    if isinstance(raw, str):
        return raw, "text"
    return str(raw), ""


def _amount_from_value(native: Any) -> tuple[Decimal, str, str]:
    memo_raw = ""
    memo_type = ""
    if isinstance(native, dict):
        amount_raw = native.get("amount", 0)
        amount = Decimal(int(amount_raw)) / Decimal(STROOPS)
        if "to_muxed_id" in native:
            memo_raw, memo_type = _normalize_muxed_memo(native["to_muxed_id"])
        return amount, memo_raw, memo_type
    if isinstance(native, int):
        return Decimal(native) / Decimal(STROOPS), "", ""
    raise ValueError(f"Unsupported event value type: {type(native)!r}")


def _topic_row(*parts: str) -> list[str]:
    row: list[str] = []
    for part in parts:
        if part == "*":
            row.append("*")
        elif part.startswith("G") or part.startswith("C") or part.startswith("M"):
            row.append(scval.to_address(part).to_xdr())
        else:
            row.append(scval.to_symbol(part).to_xdr())
    return row


def build_event_filters(contract_id: str, hot_wallet: str) -> list[EventFilter]:
    """
    Hot-wallet-scoped filters only (max 5 per RPC request).

    Do not add a contract-wide filter — it would ingest every USDC movement on testnet.
    """
    return [
        EventFilter(
            event_type=EventFilterType.CONTRACT,
            contract_ids=[contract_id],
            topics=[_topic_row("transfer", "*", hot_wallet, "*")],
        ),
        EventFilter(
            event_type=EventFilterType.CONTRACT,
            contract_ids=[contract_id],
            topics=[_topic_row("transfer", hot_wallet, "*", "*")],
        ),
        EventFilter(
            event_type=EventFilterType.CONTRACT,
            contract_ids=[contract_id],
            topics=[_topic_row("mint", hot_wallet, "*")],
        ),
        EventFilter(
            event_type=EventFilterType.CONTRACT,
            contract_ids=[contract_id],
            topics=[_topic_row("burn", hot_wallet, "*")],
        ),
        EventFilter(
            event_type=EventFilterType.CONTRACT,
            contract_ids=[contract_id],
            topics=[_topic_row("clawback", hot_wallet, "*")],
        ),
    ]


def decode_event(event: EventInfo, hot_wallet: str) -> UsdcMovement | None:
    if not event.in_successful_contract_call:
        return None
    if not event.topic:
        return None

    try:
        symbol = _topic_native(event.topic[0])
    except Exception:
        return None

    event_type = str(symbol).lower()
    from_address = ""
    to_address = ""

    if event_type == "transfer" and len(event.topic) >= 3:
        try:
            from_address = _address_string(_topic_native(event.topic[1]))
            to_address = _address_string(_topic_native(event.topic[2]))
        except Exception:
            return None
    elif event_type == "mint" and len(event.topic) >= 2:
        try:
            to_address = _address_string(_topic_native(event.topic[1]))
        except Exception:
            return None
    elif event_type in ("burn", "clawback") and len(event.topic) >= 2:
        try:
            from_address = _address_string(_topic_native(event.topic[1]))
        except Exception:
            return None
    else:
        return None

    if from_address == hot_wallet and to_address == hot_wallet:
        logger.warning("Ignoring self-transfer event %s", event.id)
        return None

    if hot_wallet not in (from_address, to_address):
        return None

    try:
        value_native = scval.to_native(stellar_xdr.SCVal.from_xdr(event.value))
        amount, memo_raw, memo_type = _amount_from_value(value_native)
    except Exception as exc:
        logger.warning("Failed to decode event value for %s: %s", event.id, exc)
        return None

    if amount <= Decimal("0"):
        return None

    closed_at = event.ledger_close_at
    if closed_at.tzinfo is None:
        closed_at = closed_at.replace(tzinfo=timezone.utc)

    return UsdcMovement(
        event_id=event.id,
        ledger=event.ledger,
        ledger_closed_at=closed_at,
        tx_hash=event.transaction_hash,
        event_type=event_type,
        from_address=from_address,
        to_address=to_address,
        amount=amount,
        memo_raw=memo_raw,
        memo_type=memo_type,
        raw=event.model_dump(by_alias=True, mode="json"),
    )

"""Thin wrapper around Stellar RPC (SorobanServer + raw getEvents)."""

from __future__ import annotations

import logging
import time
from decimal import Decimal
from typing import Sequence

import requests
from stellar_sdk import Asset, Keypair, SorobanServer
from stellar_sdk import xdr as stellar_xdr
from stellar_sdk.exceptions import SorobanRpcErrorResponse
from stellar_sdk.soroban_rpc import EventFilter, GetEventsResponse
from stellar_sdk.xdr import LedgerEntryData, LedgerEntryType, LedgerKey, LedgerKeyTrustLine

from reconciliation.config import STROOPS

logger = logging.getLogger(__name__)


class RpcClientError(Exception):
    """RPC client failure."""


class RpcClient:
    def __init__(self, url: str):
        self.url = url.rstrip("/")
        self.server = SorobanServer(self.url)

    def latest_ledger(self) -> int:
        return self._with_retries(lambda: self.server.get_latest_ledger().sequence)

    def get_events(
        self,
        *,
        start_ledger: int | None,
        cursor: str | None,
        filters: Sequence[EventFilter],
        limit: int = 200,
    ) -> GetEventsResponse:
        if start_ledger is not None and cursor:
            raise ValueError("start_ledger and cursor cannot both be set")

        def _call() -> GetEventsResponse:
            pagination: dict = {"limit": limit}
            if cursor:
                pagination["cursor"] = cursor
            params: dict = {
                "filters": [
                    f.model_dump(by_alias=True, exclude_none=True, mode="json")
                    for f in filters
                ],
                "pagination": pagination,
            }
            if start_ledger is not None:
                params["startLedger"] = start_ledger
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getEvents",
                "params": params,
            }
            response = requests.post(self.url, json=payload, timeout=60)
            response.raise_for_status()
            body = response.json()
            if body.get("error"):
                raise RpcClientError(str(body["error"]))
            result = body["result"]
            for event in result.get("events", []):
                event.setdefault("pagingToken", event.get("id", ""))
            return GetEventsResponse.model_validate(result)

        return self._with_retries(_call)

    def usdc_balance(self, account: str, asset: Asset) -> tuple[Decimal, int]:
        def _call() -> tuple[Decimal, int]:
            trust_line = LedgerKeyTrustLine(
                account_id=Keypair.from_public_key(account).xdr_account_id(),
                asset=asset.to_trust_line_asset_xdr_object(),
            )
            key = LedgerKey(
                type=LedgerEntryType.TRUSTLINE,
                trust_line=trust_line,
            )
            try:
                response = self.server.get_ledger_entries([key])
            except SorobanRpcErrorResponse as exc:
                raise RpcClientError(str(exc)) from exc
            if not response.entries:
                raise RpcClientError(f"Hot wallet {account} has no USDC trustline.")
            data = LedgerEntryData.from_xdr(response.entries[0].xdr)
            balance = Decimal(data.trust_line.balance.int64) / Decimal(STROOPS)
            return balance, response.latest_ledger

        return self._with_retries(_call)

    def _with_retries(self, fn, attempts: int = 3):
        delay = 1.0
        last_exc: Exception | None = None
        for attempt in range(attempts):
            try:
                return fn()
            except (requests.RequestException, SorobanRpcErrorResponse, RpcClientError) as exc:
                last_exc = exc
                logger.warning("RPC attempt %s failed: %s", attempt + 1, exc)
                if attempt + 1 < attempts:
                    time.sleep(delay)
                    delay *= 2
        raise RpcClientError(str(last_exc)) from last_exc

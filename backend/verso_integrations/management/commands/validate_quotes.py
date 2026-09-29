"""
D2 evidence: request N consecutive firm SEP-38 quotes over HTTP and check each one
against VERSO Core's live rate (the pricing engine).

Run on Railway Shell (VERSO-SDF) or locally with VERSO Core reachable:

    python manage.py validate_quotes --count 10 --out /tmp/d2_quotes

Authenticates with SEP-10 using a throwaway keypair (Polaris accepts unfunded accounts
signed by their master key), so no client secret is needed.
"""

from __future__ import annotations

import csv
import json
import os
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import requests
from django.core.management.base import BaseCommand, CommandError
from django.utils.dateparse import parse_datetime
from stellar_sdk import Keypair, TransactionEnvelope

from verso_integrations.polaris_setup import pen_asset_identification, usdc_asset_identification
from verso_integrations.rates import FiatUsdcRate, RatesError, get_pen_usdc_rate
from verso_integrations.sep38 import _asset_from_quote_id, price_for_pair

HTTP_TIMEOUT = 15


@dataclass
class QuoteCheck:
    n: int
    direction: str
    quote_id: str
    requested_at: str
    expires_at: str
    ttl_seconds: int
    sell_asset: str
    sell_amount: str
    buy_asset: str
    buy_amount: str
    price: str
    core_rate_venta: str
    core_rate_compra: str
    expected_price: str
    expected_buy_amount: str
    fee: str
    match: bool
    detail: str


def sep10_token(base_url: str, keypair: Keypair) -> str:
    challenge = requests.get(
        f"{base_url}/auth", params={"account": keypair.public_key}, timeout=HTTP_TIMEOUT
    )
    challenge.raise_for_status()
    data = challenge.json()
    envelope = TransactionEnvelope.from_xdr(data["transaction"], data["network_passphrase"])
    envelope.sign(keypair)
    response = requests.post(
        f"{base_url}/auth", json={"transaction": envelope.to_xdr()}, timeout=HTTP_TIMEOUT
    )
    response.raise_for_status()
    return response.json()["token"]


def request_firm_quote(
    base_url: str, token: str, sell_asset: str, buy_asset: str, sell_amount: Decimal
) -> dict:
    response = requests.post(
        f"{base_url}/sep38/quote",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "sell_asset": sell_asset,
            "buy_asset": buy_asset,
            "sell_amount": str(sell_amount),
        },
        timeout=HTTP_TIMEOUT,
    )
    if response.status_code != 201:
        raise CommandError(f"POST /sep38/quote -> {response.status_code}: {response.text[:300]}")
    return response.json()


def check_quote(quote: dict, rates: list[FiatUsdcRate]) -> tuple[bool, Decimal, Decimal, str]:
    """
    A quote matches when its price equals the price computed from VERSO Core's rate
    (fetched just before or just after the quote, in case the rate moved in between)
    and buy_amount = sell_amount / price.
    """
    sell = _asset_from_quote_id(quote["sell_asset"])
    buy = _asset_from_quote_id(quote["buy_asset"])
    price = Decimal(quote["price"])
    sell_amount = Decimal(quote["sell_amount"])
    buy_amount = Decimal(quote["buy_amount"])

    expected_price = price_for_pair(sell, buy, rates[0])
    for rate in rates:
        candidate = price_for_pair(sell, buy, rate)
        if candidate == price:
            expected_price = candidate
            break

    quantizer = Decimal("1").scaleb(-buy.significant_decimals)
    expected_buy = (sell_amount / expected_price).quantize(quantizer)

    problems = []
    if price != expected_price:
        problems.append(f"price {price} != expected {expected_price}")
    if buy_amount != expected_buy:
        problems.append(f"buy_amount {buy_amount} != expected {expected_buy}")
    return not problems, expected_price, expected_buy, "; ".join(problems) or "ok"


class Command(BaseCommand):
    help = "Request N consecutive SEP-38 firm quotes and validate them against VERSO Core rates (D2 evidence)."

    def add_arguments(self, parser):
        parser.add_argument("--count", type=int, default=10)
        parser.add_argument("--sell-amount-pen", default="100", help="PEN sold in on-ramp quotes")
        parser.add_argument("--sell-amount-usdc", default="10", help="USDC sold in off-ramp quotes")
        parser.add_argument(
            "--direction",
            choices=["on", "off", "both"],
            default="on",
            help="on = PEN->USDC, off = USDC->PEN, both = alternate",
        )
        parser.add_argument("--interval", type=float, default=2.0, help="Seconds between quotes")
        parser.add_argument("--base-url", default=None, help="Defaults to HOST_URL")
        parser.add_argument("--out", default=None, help="Directory for quotes.json / quotes.csv")

    def handle(self, *args, **options):
        base_url = (options["base_url"] or os.environ.get("HOST_URL", "")).rstrip("/")
        if not base_url:
            raise CommandError("Set HOST_URL or pass --base-url.")

        pen_id = pen_asset_identification()
        usdc_id = usdc_asset_identification()
        pairs = {
            "on": (pen_id, usdc_id, Decimal(options["sell_amount_pen"])),
            "off": (usdc_id, pen_id, Decimal(options["sell_amount_usdc"])),
        }

        token = sep10_token(base_url, Keypair.random())
        self.stdout.write(f"SEP-10 OK against {base_url}")

        checks: list[QuoteCheck] = []
        for n in range(1, options["count"] + 1):
            direction = options["direction"]
            if direction == "both":
                direction = "on" if n % 2 else "off"
            sell_id, buy_id, sell_amount = pairs[direction]

            try:
                rate_before = get_pen_usdc_rate()
                requested_at = datetime.now(timezone.utc)
                quote = request_firm_quote(base_url, token, sell_id, buy_id, sell_amount)
                rate_after = get_pen_usdc_rate()
            except RatesError as exc:
                raise CommandError(f"VERSO Core rate unavailable: {exc}") from exc

            match, expected_price, expected_buy, detail = check_quote(quote, [rate_before, rate_after])
            expires_at = parse_datetime(quote["expires_at"])
            checks.append(
                QuoteCheck(
                    n=n,
                    direction=direction,
                    quote_id=quote["id"],
                    requested_at=requested_at.isoformat(),
                    expires_at=quote["expires_at"],
                    ttl_seconds=round((expires_at - requested_at).total_seconds()),
                    sell_asset=quote["sell_asset"],
                    sell_amount=quote["sell_amount"],
                    buy_asset=quote["buy_asset"],
                    buy_amount=quote["buy_amount"],
                    price=quote["price"],
                    core_rate_venta=str(rate_after.rate_venta),
                    core_rate_compra=str(rate_after.rate_compra),
                    expected_price=str(expected_price),
                    expected_buy_amount=str(expected_buy),
                    fee="0 (commission embedded in rate spread)",
                    match=match,
                    detail=detail,
                )
            )
            style = self.style.SUCCESS if match else self.style.ERROR
            self.stdout.write(
                style(
                    f"#{n:>2} {direction:<3} price={quote['price']:<12} "
                    f"sell={quote['sell_amount']} buy={quote['buy_amount']} "
                    f"ttl={checks[-1].ttl_seconds}s -> {detail}"
                )
            )
            if n < options["count"]:
                time.sleep(options["interval"])

        matched = sum(c.match for c in checks)
        summary = {
            "base_url": base_url,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "count": len(checks),
            "matched": matched,
            "all_match": matched == len(checks),
        }

        if options["out"]:
            out = Path(options["out"])
            out.mkdir(parents=True, exist_ok=True)
            rows = [asdict(c) for c in checks]
            (out / "quotes.json").write_text(
                json.dumps({"summary": summary, "quotes": rows}, indent=2), encoding="utf-8"
            )
            with (out / "quotes.csv").open("w", newline="", encoding="utf-8") as fh:
                writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
                writer.writeheader()
                writer.writerows(rows)
            self.stdout.write(f"Evidence written to {out}")

        if not summary["all_match"]:
            raise CommandError(f"{len(checks) - matched} of {len(checks)} quotes did not match VERSO Core.")
        self.stdout.write(self.style.SUCCESS(f"{matched}/{len(checks)} quotes match VERSO Core rates."))

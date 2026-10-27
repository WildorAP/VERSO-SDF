# Callback reference — VERSO Stellar Anchor

Reference of every integration point between django-polaris and VERSO's code, plus the external interfaces the anchor
depends on. The SCF proposal described these as "Anchor Platform callbacks"; with Polaris they are **integration
classes** that Polaris calls in-process (see README → *Architecture note*, point 1).
Back to the [README](../README.md) · Related: [Implementation guide](IMPLEMENTATION_GUIDE.md) ·
[Architecture](ARCHITECTURE.md) · [Operator runbook](RUNBOOK.md).

## Summary

| Anchor Platform concept | Polaris hook | VERSO implementation | Called by |
| --- | --- | --- | --- |
| SEP-1 TOML customization | `toml` | `sep1.return_toml_contents` | Web, on `GET /.well-known/stellar.toml` |
| SEP-24 interactive deposit | `deposit` | `VersoDepositIntegration` | Web, on every webview request of a deposit |
| SEP-24 interactive withdrawal | `withdrawal` | `VersoWithdrawIntegration` | Web, on every webview request of a withdrawal |
| `/callbacks/rate` | `quote` | `VersoQuoteIntegration` | Web, on SEP-38 requests |
| Payment rails / event handling | `rails` | `VersoRailsIntegration` | Deposit and withdrawal workers |
| Custody / signing | `custody` | `VersoCustodyIntegration` | Deposit worker, and web for withdrawal instructions |
| Wallet status callbacks | Polaris `on_change_callback` / `postMessage` | `sep24/wallet_callbacks.py` | Web |

All hooks are registered in `verso_integrations/apps.py` (`register_integrations`).

---

## `toml` — `sep1.return_toml_contents(request)`

**Returns** a dict merged into the TOML that Polaris generates from the active SEPs.

| Key | Content |
| --- | --- |
| `ACCOUNTS` | Hot wallet (custody) and SEP-10 signing account |
| `CURRENCIES` | USDC anchored to PEN and to USD (`status` `live` / `test` by network) |
| `DOCUMENTATION` | Organization data |
| `TRANSFER_SERVER` | `None` — removes the SEP-6 field because SEP-6 is not offered |

Never raises: an invalid SEP-10 key in configuration is skipped so the TOML still renders.

---

## `deposit` — `VersoDepositIntegration`

File: `sep24/integration.py`. Polaris base class: `DepositIntegration`.

### `form_for_transaction(request, transaction, post_data=None, amount=None) → Form | None`

Returns the form for the current webview step, or `None` when the interactive part is finished (Polaris then shows
the transaction status page).

| Situation | Form returned |
| --- | --- |
| No VERSO session / KYC not approved | `VersoLoginForm`, `VersoRegisterForm`, `VersoVerifyEmailForm`, `VersoProfileForm` or `KycStatusForm` (DIDIT / pending / rejected), chosen by `kyc_gate.onboarding_step` |
| KYC approved, no amount yet | `FiatDepositForm` (amount in PEN or USD, minimum per currency in `sep24/fiat.py`) |
| Amount locked, transfer not declared | `BankTransferReceiptForm` (receipt upload) or `ChangeAmountForm` |
| Transfer declared or fiat confirmed | `None` (or `TransferAlreadyDeclaredForm` for a repeated POST) |

`amount` is the value the wallet passed to `/transactions/deposit/interactive`; it prefills the amount field.

### `after_form_validation(request, form, transaction)`

| Form | Effect |
| --- | --- |
| Login / register / verify email / profile | Updates the webview session; links the Stellar account to the VERSO user; refreshes KYC from Core |
| `FiatDepositForm` | Re-checks KYC; fetches `rate_venta`; creates an indicative `Quote` and `Sep24DepositMeta` with the CCI instructions; sets `amount_in`, `amount_expected`, `amount_out`, `amount_fee = 0`, `to_address`; status → `pending_user_transfer_start` |
| `BankTransferReceiptForm` | Stores the receipt and `transfer_declared_at` |
| `ChangeAmountForm` | Deletes the quote and metadata and returns to the amount step, prefilled with the previous amount |

Errors (KYC not approved, rate unavailable) are added to the form and shown in the webview; the transaction does not
advance.

### `content_for_template(request, template, form=None, transaction=None) → dict`

Supplies titles, guidance text, the live quote, CCI instructions and status messages for each template.

### `after_deposit(transaction)`

No-op.

---

## `withdrawal` — `VersoWithdrawIntegration`

File: `sep24/withdraw_integration.py`. Polaris base class: `WithdrawalIntegration`.

### `form_for_transaction(...)`

| Situation | Form returned |
| --- | --- |
| No VERSO session / KYC not approved | Same onboarding forms as deposits |
| KYC approved, no amount yet | `UsdcWithdrawForm` (USDC amount) |
| Amount locked, payout account not confirmed | `PayoutBankForm` or `ChangeAmountForm` |
| Payout account confirmed | `None` → Polaris shows the payment instructions (`more_info`) |

### `after_form_validation(...)`

| Form | Effect |
| --- | --- |
| `UsdcWithdrawForm` | Re-checks KYC; fetches `rate_compra`; creates an indicative `Quote` and `Sep24WithdrawMeta`; sets `amount_in`, `amount_expected`, `amount_out`, `amount_fee = 0` |
| `PayoutBankForm` | Stores bank, CCI, holder and declared source of funds; sets `payout_confirmed_at` |
| `ChangeAmountForm` | Returns to the amount step |

### `content_for_template(...)`

Adds the quote, payout details and, once the client must pay, keeps Polaris' `more_info` page with
`callback=postMessage`, so the wallet receives the hot wallet address, memo and amount to send.

---

## `quote` — `VersoQuoteIntegration`

File: `sep38.py`. Polaris base class: `QuoteIntegration`. Equivalent to Anchor Platform's `/callbacks/rate`.

| Method | Called for | Returns |
| --- | --- | --- |
| `get_price(token, request, sell_asset, buy_asset, sell_amount / buy_amount, …)` | `GET /sep38/price` | `Decimal` price |
| `get_prices(token, request, sell_asset, sell_amount, buy_assets, …)` | `GET /sep38/prices` | One price per buy asset |
| `post_quote(token, request, quote)` | `POST /sep38/quote` | The quote with `price` and `expires_at` set |

**Price rules** (`price_for_pair`): fiat → USDC uses `rate_venta`; USDC → fiat uses `1 / rate_compra`; the result is
quantized to the sell asset's decimals.

**Expiry** (`quote_expires_at`): now + `VERSO_QUOTE_TTL_SECONDS` (default 180). A requested `expire_after` later than
that, or in the past, raises `ValueError` → HTTP 400.

**Failure:** if the rate cannot be fetched, the method raises and Polaris answers with an error; no stale price is
returned.

### Rate source contract

`rates.py` reads one rate document per pair (`pen-usdc`, `usd-usdc`) from VERSO Core over an authenticated
server-to-server channel:

```json
{
  "rate_venta": "3.5000",
  "rate_compra": "3.4000",
  "updated_at": "2026-09-07T12:00:00Z",
  "source": "…"
}
```

| Field | Required | Meaning |
| --- | --- | --- |
| `rate_venta` | yes | Fiat per 1 USDC when VERSO **sells** USDC (on-ramp) |
| `rate_compra` | yes | Fiat per 1 USDC when VERSO **buys** USDC (off-ramp) |
| `updated_at` | no | ISO 8601 timestamp of the rate |
| `source` | no | Informational |

Both rates must be positive decimals; otherwise the response is rejected (`RatesError`).

---

## `rails` — `VersoRailsIntegration`

File: `rails.py`. Polaris base class: `RailsIntegration`. This is where off-chain (fiat) settlement meets Polaris.

### `poll_pending_deposits(pending_deposits) → list[Transaction]`

Called by the deposit worker (`process_pending_deposits --loop`). Returns the deposits whose fiat was confirmed by
operations (`Sep24DepositMeta.fiat_confirmed_at` set), after writing their final `amount_in`, `amount_out` and
`amount_fee = 0`. Polaris then pays them through the custody integration.

### `execute_outgoing_transaction(transaction)`

Called for a withdrawal once its USDC was received. Recomputes the fiat amount if the client sent a different USDC
amount (same locked rate), sets `amount_out` and `amount_fee = 0`, and moves the transaction to `pending_external`
until operations pay the fiat. If the fiat was already marked as sent, completes the transaction.

### `poll_outgoing_transactions(transactions) → list[Transaction]`

Returns the withdrawals whose fiat payout was recorded (`fiat_sent_at`), completing them.

### Operator actions (admin)

The rails are fed by two admin actions, which are the only way to move fiat-side state:

| Action | Model | Effect |
| --- | --- | --- |
| **Mark fiat received** | SEP-24 deposit metadata | Sets `fiat_confirmed_at`; the next `poll_pending_deposits` releases the USDC payout |
| **Mark fiat sent** | SEP-24 withdrawal metadata | Sets `fiat_sent_at` and completes the withdrawal. **Refused** if no on-chain USDC payment was detected for it |

---

## `custody` — `VersoCustodyIntegration`

File: `custody.py`. Polaris base class: `CustodyIntegration`. Active when a Turnkey hot wallet is configured.

| Method / property | Returns |
| --- | --- |
| `get_distribution_account(asset)` | The Turnkey hot wallet address |
| `get_receiving_account_and_memo(request, transaction)` | Hot wallet + **hash memo** (transaction ID, zero-padded to 32 bytes, base64) used to match withdrawals |
| `submit_deposit_transaction(transaction, has_trustline=True)` | Hash of the USDC payment (or claimable balance when the client has no trustline yet) |
| `create_destination_account(transaction)` | Hash of the account-creation transaction |
| `claimable_balances_supported`, `account_creation_supported` | `True` |

**Exceptions raised to Polaris:**

| Exception | When | Polaris behavior |
| --- | --- | --- |
| `TransactionSubmissionPending` | Horizon or Turnkey temporarily unavailable, insufficient fee | Retries (after a 5 s back-off) |
| `TransactionSubmissionBlocked` | Amount above `MAX_PAYOUT_USDC`, Turnkey refused, signature did not verify, hot wallet missing | Leaves the transaction blocked for an operator |
| `TransactionSubmissionFailed` | Horizon rejected the transaction permanently | Marks the transaction `error` |

**Idempotency:** the signed envelope is saved on the transaction before submission; a retry checks whether that hash
is already on the ledger and returns it instead of paying again.

---

## Wallet-facing callbacks

| Mechanism | Behavior |
| --- | --- |
| `GET /sep24/transaction?id=` | Standard SEP-24 status endpoint (SEP-10 JWT required). Includes `withdraw_anchor_account` and `withdraw_memo` for withdrawals awaiting payment |
| `on_change_callback` | Polaris POSTs the transaction to the wallet's URL on status changes when the wallet provides one (including after **Mark fiat sent**) |
| `postMessage` | The webview posts the transaction JSON to `window.opener` / `window.parent` when the client must act (withdrawal payment) and on completion |

`sep24/wallet_callbacks.py` keeps the wallet's `callback` and `on_change_callback` parameters in the webview session,
so they survive the login, KYC and amount steps.

---

## Identity (KYC) interface

The webview reads KYC status from VERSO Core for the logged-in VERSO user and, when needed, starts a DIDIT
verification session embedded in the webview. Possible statuses: `approved`, `pending`, `rejected`, `not_found`. Only
`approved` unlocks the amount step. The SEP-10 endpoint never consults KYC.

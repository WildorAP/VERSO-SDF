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
| Operator actions (fiat settlement, reject, hold, refund) | — (VERSO admin) | `operations.py`, `admin.py` | Web (admin) |
| Outbound: client history in VERSO | — | `history_sync.py`, `core_client.sync_anchor_transaction` | History sync worker |

All Polaris hooks are registered in `verso_integrations/apps.py` (`register_integrations`).

---

## `toml` — `sep1.return_toml_contents(request)`

**Returns** a dict merged into the TOML that Polaris generates from the active SEPs.

| Key | Content |
| --- | --- |
| `WEB_AUTH_ENDPOINT`, `TRANSFER_SERVER_SEP0024`, `ANCHOR_QUOTE_SERVER` | `HOST_URL` + `/auth`, `/sep24`, `/sep38`, for the SEPs in `ACTIVE_SEPS` (replaces Polaris' values, built with `os.path.join`) |
| `ACCOUNTS` | Only the hot wallet (custody). The SEP-10 key is published as `SIGNING_KEY` by Polaris; it holds no funds and is not listed here |
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
| Another operation of the same wallet is still open | `KycStatusForm` with *"Tienes una operación en curso"* (step `open_operation`); the transaction does not advance |
| No VERSO session / KYC not approved | `VersoLoginForm`, `VersoRegisterForm`, `VersoVerifyEmailForm`, `VersoProfileForm` or `KycStatusForm` (DIDIT / pending / rejected), chosen by `kyc_gate.onboarding_step`. Skipped when the wallet is linked to an active, KYC-approved VERSO user |
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

Supplies titles, guidance text, the live quote, CCI instructions and status messages for each template. A deposit
whose transfer was declared, whose fiat was confirmed or that ended in `error` always shows the waiting page. Status
messages come from `operations.customer_message` (rejection, hold) or fixed texts, never from Polaris' `message`.

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
`callback=postMessage`, so the wallet receives the hot wallet address, memo and amount to send. A withdrawal in
`error` (for example rejected) shows the same page with the customer notice and without payment instructions.

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

Called by the deposit worker (`process_pending_deposits`). Returns the deposits whose fiat was confirmed by
operations (`Sep24DepositMeta.fiat_confirmed_at` set) and that have **no active hold**, after writing their final
`amount_in`, `amount_out` and `amount_fee = 0`. Polaris then pays them through the custody integration.

### `execute_outgoing_transaction(transaction)`

Called for a withdrawal once its USDC was received. Recomputes the fiat amount if the client sent a different USDC
amount (same locked rate), sets `amount_out` and `amount_fee = 0`, and moves the transaction to `pending_external`
until operations pay the fiat. If the fiat was already marked as sent, completes the transaction.

### `poll_outgoing_transactions(transactions) → list[Transaction]`

Returns the withdrawals whose fiat payout was recorded (`fiat_sent_at`), completing them.

### Operator actions (admin)

The rails are fed by operator actions in the admin (`operations.py`), which are the only way to move fiat-side
state. Each operation shows only the actions allowed in its current stage (`operation_status.py`); every action is
applied in a database transaction, after locking the Polaris transaction row and checking the stage again.

| Action | Applies to | Effect | Refused when |
| --- | --- | --- | --- |
| **Confirm and send** (*Confirmar y enviar*) | Deposit, initiated or held | Sets `fiat_confirmed_at` and releases any hold; the next `poll_pending_deposits` releases the USDC payout | The client has not declared the transfer, or the status is not `pending_user_transfer_start` |
| **Mark paid** (*Marcar pagado*) | Withdrawal, USDC received or held | Sets `fiat_sent_at`, completes the withdrawal and releases any hold | No on-chain USDC payment was detected (`WithdrawalUsdcNotReceivedError`) |
| **Reject** (*Rechazar*) | Deposit or withdrawal not finished | Creates `OperationRejection` (reason, note, `funds_received`), releases holds, sets status `error` with `status_message = "Rejected by operator: <reason>"`, and calls the wallet callback (`maybe_make_callback`) | Deposit whose USDC already left; withdrawal already paid |
| **Hold** (*Retener*) | Deposit initiated; withdrawal initiated or USDC received | Creates an active `OperationHold` with a mandatory note. Polaris status unchanged; held deposits are not paid | An active hold already exists (one per transaction, database constraint) |
| **Register refund** (*Registrar devolución*) | Rejected with funds received | Records amount, reference, operator and time on the rejection, and sets `Transaction.refunded`. Moves no money | Amount ≤ 0, empty reference, or (withdrawals) a reference that is not a 64-character Stellar transaction hash |

`funds_received` is taken from the chain for withdrawals (`usdc_payment_received`) and, for deposits, from
`fiat_confirmed_at` or from the operator's answer (the anchor cannot see the bank). Rejection reasons:
`client_withdrew`, `amount_mismatch`, `third_party`, `compliance`, `other` (a note is mandatory for `other`).

**What the client sees** (`customer_message`): the same rejection text for every reason, *"Tu operación está en
revisión"* during a hold, and a generic error text otherwise. Reasons and notes are internal.

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
| `GET /sep24/transaction/poll/?id=` | Used by the webview's waiting pages. Only answers the browser session that opened the transaction (403 otherwise); non-UUID IDs answer 400. Returns `status`, the customer message and `completed_at` |
| `on_change_callback` | Polaris POSTs the transaction to the wallet's URL on status changes when the wallet provides one (including after **Mark paid** and **Reject**) |
| `postMessage` | The webview posts the transaction JSON to `window.opener` / `window.parent` when the client must act (withdrawal payment) and on completion |

`sep24/wallet_callbacks.py` keeps the wallet's `callback` and `on_change_callback` parameters in the webview session,
so they survive the login, KYC and amount steps. Safety rules:

- A callback value is kept only if it is `postMessage`, `success` or an `https` URL (`http` too in local mode) with a
  host, made only of the characters `A–Z a–z 0–9 - . _ ~ : / ? # @ ! $ & * + , = %`. Polaris writes `callback`
  unescaped inside a `<script>` block in `more_info`, so `drop_unsafe_wallet_callbacks` removes any other value from
  the request before Polaris renders the page.
- The webview pages are reachable with only a transaction ID, so they never store or replace a callback URL on the
  transaction; they can only set `on_change_callback = "postMessage"` on a transaction that has none. Callback URLs
  come only from the wallet's SEP-10-authenticated request, through Polaris.

### Messages between the webview and versotek.io

When the webview is opened as a popup from versotek.io, it exchanges these `postMessage` messages with its opener.
The webview only accepts messages whose `source` is its opener and whose `transaction_id` matches the page.

| Message | Direction | Meaning |
| --- | --- | --- |
| `verso:withdraw_pay_check` | webview → opener | "Can you sign this withdrawal's payment?" |
| `verso:withdraw_pay_ready` | opener → webview | Yes: the webview shows *Firmar y enviar pago* |
| `verso:pay_withdraw` | webview → opener | The client clicked: sign and submit the USDC payment with the memo and amount of the transaction |
| `verso:withdraw_pay_result` | opener → webview | `ok: true`, or `ok: false` with an `error` text shown to the client |
| `verso:new_operation` | webview → opener | Start a new SEP-24 operation (`operation`: `deposit` or `withdraw`); only the opener holds the SEP-10 token |

Wallets that do not answer `verso:withdraw_pay_check` pay on their own, as with any SEP-24 anchor.

---

## Identity (KYC) interface

The webview reads KYC status from VERSO Core for the logged-in VERSO user and, when needed, starts a DIDIT
verification session embedded in the webview. Possible statuses: `approved`, `pending`, `rejected`, `not_found`. Only
`approved` unlocks the amount step. The SEP-10 endpoint never consults KYC.

Core's user lookup accepts an email or a Stellar public key (`core_client.user_status`). The webview uses the
public-key lookup to skip the login only when Core reports the user as active, KYC-completed and linked to exactly
that wallet; on any error it falls back to the login.

---

## Outbound: history sync — `history_sync.sync_once`

Called in a loop by `sync_history_to_verso --loop`. For each SEP-24 transaction with VERSO metadata that is not
`incomplete`, it posts this document to VERSO Core over the authenticated server-to-server channel
(`core_client.sync_anchor_transaction`, 10 s timeout, redirects not followed):

| Field | Content |
| --- | --- |
| `anchor_transaction_id` | Polaris transaction ID (UUID); VERSO's key for idempotency |
| `kind` | `deposit` or `withdrawal` |
| `status` | Polaris status |
| `stellar_account` | Client wallet (SEP-10 account) |
| `fiat_currency`, `amount_fiat`, `amount_usdc`, `rate` | Values locked at the amount step |
| `started_at`, `completed_at` | ISO 8601; `completed_at` may be `null` |
| `stellar_transaction_id` | On-chain hash, or empty |
| `origen_fondos`, `origen_fondos_otro` | Withdrawals only: declared source of funds |

A document is sent only when its SHA-256 fingerprint differs from the last one VERSO accepted. Responses:

| Response | Result stored in `VersoHistorySync` | Next attempt |
| --- | --- | --- |
| 200 / 201 | `synced` (keeps VERSO's `id` if returned) | Only when the operation changes |
| 422 `stellar_account_not_linked` | `not_linked` | 1 hour, or as soon as the operation changes |
| 400 / 409 | `rejected` | 1 hour, or as soon as the operation changes |
| 3xx, 404, 5xx, 401, network error | `retrying` | Back-off 30 s, 60 s, 2 min, 5 min, 10 min, 15 min |

Errors are recorded per operation and never stop the sweep. The admin (*sincronización con VERSO*) is read-only,
with a *retry now* action that clears the fingerprint so the next sweep resends.

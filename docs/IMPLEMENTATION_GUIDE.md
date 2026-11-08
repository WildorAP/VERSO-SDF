# Implementation guide — SEP-1, SEP-10, SEP-24 and SEP-38

How VERSO implements each Stellar Ecosystem Proposal on top of **django-polaris**, with the decisions taken along the
way. It is written for wallet developers integrating with VERSO and for teams building a similar fiat anchor.
Back to the [README](../README.md) · Related: [Architecture](ARCHITECTURE.md) ·
[Callback reference](CALLBACKS.md) · [Operator runbook](RUNBOOK.md).

## 1. Stack and how Polaris is extended

django-polaris provides the SEP endpoints, the `Transaction` / `Quote` models and the background workers. An anchor
customizes it by registering **integration classes**. VERSO registers them in `verso_integrations/apps.py`:

| Polaris hook | VERSO class / function | File |
| --- | --- | --- |
| `toml` | `return_toml_contents` | `verso_integrations/sep1.py` |
| `deposit` | `VersoDepositIntegration` | `verso_integrations/sep24/integration.py` |
| `withdrawal` | `VersoWithdrawIntegration` | `verso_integrations/sep24/withdraw_integration.py` |
| `rails` | `VersoRailsIntegration` | `verso_integrations/rails.py` |
| `quote` | `VersoQuoteIntegration` | `verso_integrations/sep38.py` |
| `custody` | `VersoCustodyIntegration` (when a Turnkey wallet is configured) | `verso_integrations/custody.py` |

A few Polaris views are overridden in `config/urls.py` (SEP-10 error handling, `stellar.toml` charset, SEP-24
`more_info`). Every hook is described in the [callback reference](CALLBACKS.md).

VERSO's own code lives in two Django apps:

| App / module | Responsibility |
| --- | --- |
| `verso_integrations/` | Polaris integrations, the SEP-24 webview (`sep24/`), VERSO Core client (`core_client.py`), live rates (`rates.py`), custody (`custody.py`, `turnkey_signer.py`), operator actions (`operations.py`, `operation_status.py`, `admin.py`) and history sync (`history_sync.py`) |
| `reconciliation/` | D3: RPC event ingest, matching, balance reconciliation, CloudWatch metrics and reports |

Long-running processes are Django management commands: Polaris' `process_pending_deposits` and
`watch_transactions`, and VERSO's `reconciliation_worker --loop` and `sync_history_to_verso --loop`.

**Assets.** `python manage.py seed_polaris_t2` creates, idempotently:

- the on-chain asset **USDC** (Circle; issuer chosen from the network passphrase), enabled for SEP-24 and SEP-38;
- the off-chain assets **`iso4217:PEN`** and **`iso4217:USD`**, with 4 significant decimals (the precision of VERSO's
  rates);
- the delivery method `bank_transfer_cci_cce` (sell and buy);
- four exchange pairs: PEN→USDC, USDC→PEN, USD→USDC, USDC→USD.

## 2. SEP-1 — `stellar.toml`

Endpoint: `GET /.well-known/stellar.toml` ([mainnet](https://anchor.versotek.io/.well-known/stellar.toml)).

Polaris writes the endpoint fields from the active SEPs (`WEB_AUTH_ENDPOINT`, `SIGNING_KEY`,
`TRANSFER_SERVER_SEP0024`, `ANCHOR_QUOTE_SERVER`, `NETWORK_PASSPHRASE`). `sep1.py` adds the rest:

| Field | Value |
| --- | --- |
| `ACCOUNTS` | Only the Turnkey hot wallet, the account that holds and moves USDC. The SEP-10 key appears as `SIGNING_KEY` (written by Polaris); it holds no funds and does not exist on-chain, so it is not listed |
| `CURRENCIES` | USDC twice: one entry anchored to PEN and one to USD, each with `anchor_asset_type = "fiat"`, `status = "live"` on mainnet (`"test"` on testnet) and redemption instructions |
| `DOCUMENTATION` | Organization name, URL, logo, contact emails and address |
| `TRANSFER_SERVER` | **Removed on purpose** (see below) |

**SEP-6 is not offered.** Polaris writes `TRANSFER_SERVER` (the SEP-6 field) whenever SEP-24 is active, which made
some wallets show a SEP-6 option that returned 404. `sep1.py` sets it to `None` so the TOML encoder omits it;
`test_sep1.py` checks that the published file contains `TRANSFER_SERVER_SEP0024` and not `TRANSFER_SERVER`.

`toml_view.py` serves the file with an explicit UTF-8 charset (the organization data contains accented characters).

**Endpoint URLs are rebuilt.** Polaris builds `WEB_AUTH_ENDPOINT`, `TRANSFER_SERVER_SEP0024` and
`ANCHOR_QUOTE_SERVER` with `os.path.join`, which writes a backslash when the server runs on Windows. `sep1.py`
rebuilds them as `HOST_URL` + `/auth`, `/sep24`, `/sep38`, only for the SEPs listed in `ACTIVE_SEPS`.

**Verification:** the SDF anchor test suite (`@stellar/anchor-tests`) passes **SEP-1 5/5** against mainnet.

## 3. SEP-10 — Web authentication

Endpoint: `GET /auth?account=G…` (challenge) and `POST /auth` (signed challenge → JWT).

- **Standard Polaris flow.** The challenge is a sequence-0 transaction with `manage_data` operations for the home
  domain and a random nonce, signed by the anchor's SEP-10 key. The client signs it and posts it back; Polaris
  verifies the signatures (including accounts with multiple signers and accounts that do not exist yet, which are
  verified against their master key) and returns a JWT whose `sub` is the client account.
- **Home domain:** `anchor.versotek.io`.
- **Hardening (`sep10.py`).** Polaris 2.6 only catches `InvalidSep10ChallengeError` / `TypeError` while parsing the
  posted XDR, so malformed base64 could surface as HTTP 500. `VersoSEP10Auth` also catches `ValueError`, `EOFError`,
  `AttributeError` and `binascii.Error` and answers **400** as the protocol expects. Covered by `test_sep10.py`.
- **No compliance logic in SEP-10.** SEP-10 only proves account ownership. KYC is enforced inside the SEP-24 webview
  (section 4.2), never by refusing a JWT.
- **The SEP-10 key holds no funds.** It signs challenges only; payouts are signed by the custody integration
  (section 6). The key can therefore be rotated by changing configuration and redeploying: the `SIGNING_KEY` in
  `stellar.toml` updates automatically and wallets fetch it on every login.

**Verification:** `@stellar/anchor-tests` passes **SEP-10 14/17** on mainnet. The three remaining tests (*Account
Signer Support*) create and fund accounts with extra signers, which the suite can only do on testnet; they pass there.
A reproducible CLI walkthrough is in the README (*Tranche 1, Deliverable 2*).

## 4. SEP-24 — Interactive deposit and withdrawal

Endpoints (Polaris): `GET /sep24/info`, `POST /sep24/transactions/deposit/interactive`,
`POST /sep24/transactions/withdraw/interactive`, `GET /sep24/transaction`, `GET /sep24/transactions`.

### 4.1 Interactive flow

The wallet authenticates with SEP-10, opens the interactive URL returned by Polaris and polls
`GET /sep24/transaction` (or listens for `postMessage`) until the transaction reaches a terminal status. The
webview is rendered by VERSO with Django templates and vanilla JS (`templates/sep24/`, `static/sep24/`).

Polaris drives the webview by asking the integration class for **the form of the current step**
(`form_for_transaction`) and calling **`after_form_validation`** when that form is submitted. VERSO uses that loop
as a small state machine:

```
(another operation of this wallet still open) ─► open_operation (stop)

(wallet linked to an approved VERSO account) ───────────────┐
                                                            ▼
login ─┬─► verify_email ─► profile ─► didit ─► (pending | rejected)
       └─► (approved KYC) ─────────────────────────────► amount ─► (deposit) receipt upload
                                                                └► (withdraw) payout bank details
```

The step is computed by `kyc_gate.onboarding_step` on every request, so reloading the webview or opening it again
always resumes at the right place.

### 4.2 KYC gate

Implemented in `sep24/kyc_gate.py` and `sep24/kyc_views.py`.

1. **Account.** The client logs in with their VERSO account or registers inside the webview (with email
   verification). The Stellar account that authenticated with SEP-10 is linked to that VERSO user.
2. **KYC status** is read from **VERSO Core** (`approved`, `pending`, `rejected`, `not_found`). A client who is
   already verified in VERSO goes straight to the amount step: **KYC is not repeated**.
3. **DIDIT.** Clients without approved KYC complete identity verification with DIDIT, embedded in the webview; the
   webview polls until Core reports the new status.
4. **Enforced twice.** The amount step is only offered with approved KYC, and the amount handler checks the status
   again before creating the quote, so a crafted POST cannot skip the gate.
5. **Linked wallets skip the login.** If Core reports that the SEP-10 wallet is linked to an **active**,
   **KYC-approved** VERSO user with exactly that public key (`sync_kyc_from_linked_wallet`), the webview session is
   opened for that user without asking for the password again. The SEP-10 signature proves control of the wallet,
   and the link itself was created through a password login. Any other case (unknown wallet, inactive user, KYC not
   completed, Core unreachable) falls back to the normal login.
6. **One open operation per wallet.** `find_open_operation` looks for another SEP-24 transaction of the same wallet
   that reached the amount step (it has VERSO metadata) and is not in a terminal status. If there is one, the webview
   shows *"Tienes una operación en curso"* and stops. Webviews abandoned before entering an amount never block.

### 4.3 Deposit (on-ramp: PEN/USD → USDC)

`VersoDepositIntegration` — `sep24/integration.py`.

| Step | What happens | Transaction status |
| --- | --- | --- |
| 1. Currency | The wallet or the client picks PEN or USD | `incomplete` |
| 2. Amount | Client enters the fiat amount. The anchor fetches the live `rate_venta` from Core, computes `amount_out = amount_in / rate_venta` (7 decimals), creates an indicative SEP-38 `Quote`, stores `Sep24DepositMeta` and sets `amount_in`, `amount_out`, `amount_fee = 0` | `pending_user_transfer_start` |
| 3. Instructions | The webview shows the VERSO CCI for that currency, the account holder, the reference `TXN-<id>`, the rate and the USDC to receive. **"Change amount"** discards the quote and returns to step 2 (only before the transfer is declared) | `pending_user_transfer_start` |
| 4. Receipt | Client transfers by CCI/CCE and uploads the bank receipt (image or PDF, max 10 MB) | `pending_user_transfer_start` |
| 5. Confirmation | Operations verify the credit in VERSO's bank account and apply **Confirm and send** (*Confirmar y enviar*). It is refused if the client has not declared the transfer or the transaction is no longer `pending_user_transfer_start` | — |
| 6. Payout | Polaris `process_pending_deposits` asks `VersoRailsIntegration.poll_pending_deposits` for ready deposits (confirmed and **not on hold**), then the custody integration pays the USDC | `pending_anchor` → `pending_stellar` → `completed` |

If the client account has no USDC trustline, Polaris waits for it or, when the wallet supports claimable balances,
the payout is sent as a claimable balance.

### 4.4 Withdrawal (off-ramp: USDC → PEN/USD)

`VersoWithdrawIntegration` — `sep24/withdraw_integration.py`.

| Step | What happens | Transaction status |
| --- | --- | --- |
| 1. Amount | Client enters the USDC amount. The anchor fetches the live `rate_compra`, computes `amount_out = amount_in × rate_compra` (2 decimals), creates an indicative quote and stores `Sep24WithdrawMeta` | `incomplete` |
| 2. Payout account | Client enters bank, CCI and account holder, plus the declared source of funds (PSAV requirement). "Change amount" is available until this step is confirmed | `pending_user_transfer_start` |
| 3. USDC payment | The webview and `GET /sep24/transaction` return `withdraw_anchor_account` (the hot wallet) and a **hash memo** derived from the transaction ID. The wallet sends the USDC with that memo; the webview notifies the opener with `postMessage` | `pending_user_transfer_start` |
| 4. Detection | Polaris `watch_transactions` matches the incoming payment by memo and records `stellar_transaction_id` | `pending_anchor` |
| 5. Awaiting fiat payout | `VersoRailsIntegration.execute_outgoing_transaction` recomputes the fiat amount if the client sent a different USDC amount and waits for operations | `pending_external` |
| 6. Fiat payout | Operations transfer the fiat by CCI/CCE and apply **Mark paid** (*Marcar pagado*) | `completed` |

**Safety rule:** a withdrawal **cannot be completed unless its USDC payment was detected on-chain**
(`WithdrawalUsdcNotReceivedError` in `withdraw.py`), so operations can never pay fiat for USDC that never arrived. If
the client sends a different amount than quoted, the fiat amount is recomputed with the locked rate.

### 4.5 Fees

VERSO's commission is the spread between `rate_venta` and `rate_compra`. SEP-24 transactions always report
`amount_fee = 0`, and `amount_out` is exactly the converted amount, so the quote the wallet shows is the total cost.
Network fees (XLM) for payouts are paid by the hot wallet.

### 4.6 Popup wallets

Wallets that open the webview as a popup (Stellar Demo Wallet style, or the ANCHOR button on versotek.io) need the
popup to stay connected to its opener:

- `sep24/coop_middleware.py` removes the `Cross-Origin-Opener-Policy` header on `/sep24/` responses so
  `window.opener` survives;
- `sep24/wallet_callbacks.py` keeps the wallet's `callback` / `on_change_callback` parameters through every webview
  step, and `more_info` always uses `callback=postMessage` for withdrawals, so the wallet learns the memo and amount it
  must send.
- **Callback values are filtered.** Polaris writes `callback` unescaped inside a `<script>` block of `more_info`, so
  only `postMessage`, Polaris' `success` marker or an `https` URL (also `http` in local mode) made of a strict set of
  characters is kept; any other value is removed from the request before Polaris renders the page
  (`drop_unsafe_wallet_callbacks`). The webview pages, reachable with only a transaction ID, never store or replace
  a callback URL on the transaction: they can only mark a transaction without callback as `postMessage`.
- **Sign and pay from the opener.** On the withdrawal instructions, `withdraw_wallet_pay.js` asks the opener whether
  it can pay (`verso:withdraw_pay_check`). versotek.io answers `verso:withdraw_pay_ready`, the webview shows *Firmar y
  enviar pago*, and the click sends `verso:pay_withdraw`; the opener signs the payment with the connected wallet and
  replies `verso:withdraw_pay_result`. Messages are only accepted from the opener and for the same transaction ID.
  Wallets that do not answer keep paying on their own, and the button stays hidden.
- **Another operation.** When the transaction finishes, *Hacer otra operación* (`new_operation.js`) posts
  `verso:new_operation` to the opener, which holds the SEP-10 token needed to start a new SEP-24 transaction.

### 4.7 Operator actions and what the client sees

Polaris has no "rejected" or "held" status. VERSO adds them on top of Polaris without changing its state machine:

- `operation_status.py` derives a read-only **stage** for each operation (initiated, received, sending, completed,
  error, held, rejected, rejected-refund-pending, refunded) from the Polaris status, the VERSO metadata and two
  models: `OperationRejection` (one per transaction) and `OperationHold` (several, at most one active, enforced by a
  database constraint). The same rule is written as a function (admin column) and as a query filter (admin list
  filter); tests keep both in sync.
- `operations.py` defines the actions allowed per stage — **Confirm and send**, **Mark paid**, **Reject**, **Hold**,
  **Register refund** — and applies each one inside a database transaction, after re-reading the operation under a
  row lock and checking again that the action is still allowed.
- **Reject** sets the Polaris status to `error`, with `status_message = "Rejected by operator: <reason>"`, and calls
  the wallet callback. It is refused for a deposit whose USDC already left and for a withdrawal already paid.
  `funds_received` is taken from the chain for withdrawals and from the operator for unconfirmed deposits (the anchor
  cannot see the bank). With funds received, the operation waits for **Register refund**, which only records the
  amount and reference (a Stellar hash for USDC refunds) and sets Polaris' `refunded` flag.
- **Hold** leaves the Polaris status unchanged; `VersoRailsIntegration.poll_pending_deposits` skips held deposits, so
  no USDC leaves while the hold is active.
- The client only sees fixed texts (`customer_message`): one rejection text for every reason, *"Tu operación está
  en revisión"* during a hold, and a generic error text. Polaris' `message` (written for operators) is never shown in
  the webview or in the poll endpoint.

## 5. SEP-38 — Quotes

Endpoints (Polaris): `GET /sep38/info`, `GET /sep38/prices`, `GET /sep38/price`, `POST /sep38/quote`,
`GET /sep38/quote/:id`.

`VersoQuoteIntegration` (`sep38.py`) fetches the **live rate from VERSO Core on every request** (`rates.py`); the
anchor never caches or adjusts it.

| Direction | Core rate | SEP-38 `price` |
| --- | --- | --- |
| PEN/USD → USDC (on-ramp) | `rate_venta` (fiat per 1 USDC) | `rate_venta` |
| USDC → PEN/USD (off-ramp) | `rate_compra` (fiat per 1 USDC) | `1 / rate_compra` |

- Prices are quantized to the sell asset's decimals (4 for PEN/USD, 7 for USDC), so the exact Core rate is published.
- **Firm quotes expire after 180 s** by default (`VERSO_QUOTE_TTL_SECONDS`). A wallet may request a shorter
  `expire_after`; a longer one is rejected with 400.
- If Core is unreachable, the endpoint returns an error instead of a stale price.

**How SEP-38 relates to SEP-24.** SEP-24 does not carry a SEP-38 `quote_id`. The webview fixes the price when the
client confirms the amount, using the same Core rate and the same formula, and records it as an indicative `Quote`
linked to the transaction. Wallets that call SEP-38 directly therefore see the same price the webview will apply.
`python manage.py validate_quotes` checks consecutive firm quotes against Core.

## 6. Custody — paying USDC without a key on the server

`VersoCustodyIntegration` (`custody.py`) implements Polaris' `CustodyIntegration`. Polaris decides *when* to pay;
the custody integration decides *who signs*.

1. Build the payment (or a claimable balance, or account creation) with the **Turnkey hot wallet as source** and a
   300 s time bound.
2. Refuse to sign if the amount exceeds `MAX_PAYOUT_USDC`: the transaction is left **blocked** for operator review.
3. Send the transaction hash to Turnkey (`turnkey_signer.py`), receive the Ed25519 signature and **verify it locally**
   against the hot wallet's public key before attaching it.
4. **Persist the signed envelope before submitting.** A retry resubmits the same envelope; if its hash is already on
   the ledger, that hash is returned instead of paying again. A new envelope is only built once the old one has
   expired without landing. This makes payouts idempotent across crashes, timeouts and restarts.
5. Transient failures (Horizon or Turnkey unavailable, insufficient fee) are retried with back-off; permanent ones
   (Turnkey refusal, invalid signature, missing hot wallet) block the transaction for an operator.

Withdrawals are received on the same hot wallet; `get_receiving_account_and_memo` returns the wallet and the hash memo
used for matching.

Covered by `test_custody.py` (signing, payout cap, outages, refusals, retries without double payment).

## 7. Reconciliation

Not a SEP, but part of operating an anchor safely. The `reconciliation` app ingests every USDC movement of the hot
wallet from Stellar RPC, matches it to its SEP-24 transaction (by Stellar transaction hash or memo, then checks the amount) and compares the internal
ledger with the on-chain balance every ~60 s. Discrepancy types: `balance_mismatch`, `unmatched_inbound`,
`unmatched_outbound`, `amount_mismatch`, `missing_onchain`, `worker_stale`. Operator handling is in the
[runbook](RUNBOOK.md#6-reconciliation-discrepancies).

## 8. History sync with VERSO

Not a SEP either: it lets a client see their anchor operations in their VERSO history. `history_sync.sync_once`,
run by `python manage.py sync_history_to_verso --loop` (its own service, one replica), sweeps the SEP-24
transactions every `VERSO_HISTORY_SYNC_INTERVAL_SECONDS` (default 15):

1. It takes every SEP-24 transaction with VERSO metadata (it reached the amount step) that is not `incomplete`.
2. It builds the operation's current state — `anchor_transaction_id`, `kind`, `status`, `stellar_account`,
   `fiat_currency`, `amount_fiat`, `amount_usdc`, `rate`, `started_at`, `completed_at`, `stellar_transaction_id` and,
   for withdrawals, the declared source of funds — and its SHA-256 fingerprint.
3. If the fingerprint equals the last one VERSO accepted, nothing is sent. Otherwise it is posted to VERSO Core,
   whose endpoint is idempotent per `anchor_transaction_id`.

The result is stored in `VersoHistorySync` (one row per transaction): *synced*, *retrying* (temporary error, back-off
30 s → 15 min), *not linked* (the wallet belongs to no VERSO user) or *rejected* (VERSO refused the data); the last two
are retried hourly, or at once if the operation changes. A sweep never stops on a VERSO error.

A sweep is used instead of Django signals because Polaris sometimes changes statuses with `QuerySet.update()`, which
fires no signals. The module only reads Polaris and VERSO tables and writes only `VersoHistorySync`, so a failure in
VERSO can never affect payouts, withdrawals or reconciliation. `--dry-run` prints the payloads without sending.

## 9. Testing

Requires **Python 3.12**. From a fresh clone (PowerShell; on Linux/macOS use `source venv/bin/activate` and `cp`):

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt

cd backend
Copy-Item .env.example .env
# SIGNING_SEED must be a valid Stellar secret key; generate a throwaway one for tests:
python -c "from stellar_sdk import Keypair; print(Keypair.random().secret)"
# paste the printed S... value as SIGNING_SEED in backend/.env

python manage.py test verso_integrations reconciliation
```

No PostgreSQL or Redis is needed locally (SQLite by default). GitHub Actions runs the suite on every push against
PostgreSQL and Redis, after `collectstatic` (with `DEBUG=False` the admin and webview templates need WhiteNoise's
static manifest, as in production). **340 tests** (305 in `verso_integrations`, 35 in `reconciliation`):

| Area | Tests |
| --- | --- |
| SEP-1 / SEP-10 | `test_sep1.py`, `test_sep10.py`, `test_root.py` |
| SEP-24 | `test_sep24.py`, `test_sep24_withdraw.py`, `test_deposit_flow.py`, `test_kyc_gate.py`, `test_kyc_views.py`, `test_transaction_poll.py`, `test_wallet_callbacks.py`, `test_withdraw_wallet_callbacks.py`, `test_sep24_coop_middleware.py` |
| SEP-38 | `test_sep38.py`, `test_rates.py`, `test_validate_quotes.py` |
| Custody | `test_custody.py` |
| Operator actions (stages, reject, hold, refund) | `test_operations.py`, `test_operation_status.py` |
| VERSO Core client and history sync | `test_core_client.py`, `test_history_sync.py` |
| Edge cases (invalid SEP-10 signatures, malformed payloads, duplicate IDs, webhook replay) | `test_edge_cases.py` (51) |
| Reconciliation | `reconciliation/tests/` (events, sync, matching, reconcile, alerts, report, bootstrap, admin) |

End-to-end checks against a live deployment: `@stellar/anchor-tests` (SEP-1, SEP-10), the Stellar Demo Wallet on
testnet and the ANCHOR button on versotek.io on mainnet.

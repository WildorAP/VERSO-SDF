# Operator runbook — VERSO Stellar Anchor

Procedures for VERSO's operations team to settle SEP-24 transactions on mainnet and handle exceptions. Fiat
settlement is **semi-automated by design**: the anchor automates quotes, instructions, on-chain payouts and on-chain
detection, while a person confirms every fiat movement (PSAV obligations).
Back to the [README](../README.md) · Related: [Architecture](ARCHITECTURE.md) ·
[Implementation guide](IMPLEMENTATION_GUIDE.md) · [Callback reference](CALLBACKS.md).

> This public version describes *what* to do and *why*. Access details (consoles, credentials, contacts) are kept in
> VERSO's internal operations documentation.

**Service target:** average settlement **≤ 30 minutes** from the moment the client's transfer (fiat or USDC) arrives.

## 1. Where to look

| Need | Where |
| --- | --- |
| Deposits waiting for fiat | Admin → **SEP-24 deposits (fiat on-ramp)** — filter *fiat confirmed: no*, *transfer declared: yes* |
| Withdrawals waiting for fiat payout | Admin → **SEP-24 withdrawals (fiat off-ramp)** — filter *fiat sent: no* |
| Status of any transaction | Admin → **Polaris → Transactions** (status, amounts, memo, Stellar transaction id) |
| Reconciliation health | Admin → **Reconciliation** (runs, ledger entries, discrepancies) |
| On-chain proof | [Stellar Expert — hot wallet](https://stellar.expert/explorer/public/account/GCYWYXFOWGOXOLUI6ZXBSMMRRK4KJUUVHBBSPMM6XAEOAX2YU5KZNJMM) |
| Incoming fiat | VERSO's operating bank account (PEN and USD CCI accounts) |

**Never copy a Stellar address from a transaction history.** Address-poisoning attacks send dust from addresses that
share the first and last characters of a real client. Always take addresses from the admin or from VERSO's own list
of treasury accounts.

## 2. Daily checks (start of shift)

1. **Reconciliation:** the latest runs are `ok` with `delta = 0`, and there are no open discrepancies.
2. **Workers alive:** the latest reconciliation run is less than a few minutes old; no deposit confirmed more than
   ~5 minutes ago is still unpaid; no withdrawal shows a client payment on Stellar Expert while still waiting in the
   admin. See [section 6](#6-worker-health).
3. **Hot wallet float:** enough USDC for the day's expected on-ramps and enough XLM for fees. Top up or sweep per
   [section 7](#7-treasury-hot-wallet-and-treasury-wallet).
4. **Alarm inbox:** any alarm email since the last shift is understood and closed.

## 3. On-ramp (client pays fiat, receives USDC)

Client flow: wallet → VERSO webview → amount and quote → CCI instructions with reference `TXN-<transaction id>` →
bank transfer → receipt upload.

**Operator steps**

1. Open the deposit in the admin. Note the currency, `amount` (fiat expected), reference and the uploaded receipt.
2. In VERSO's bank account, find the matching credit. Check:
   - **amount** equals the expected fiat amount;
   - **currency account** is the right one (PEN credit to the PEN account, USD to the USD account);
   - the **sender** is consistent with the client, as required by VERSO's compliance procedures;
   - the credit is **settled**, not just announced.
3. Select the deposit and run **Mark fiat received**.
4. Within about a minute the deposit worker pays the USDC. Confirm that the transaction reaches `completed` and has a
   Stellar transaction id, and that the payment is visible on Stellar Expert.

**Exceptions**

| Situation | Action |
| --- | --- |
| Credit amount differs from the quote | Do not confirm. Contact the client: either they complete the difference, or the transfer is refunded and they start a new deposit with the right amount |
| Credit received but no receipt uploaded | Confirm only if the credit can be matched unambiguously (amount + reference + client); otherwise ask the client for the receipt |
| Credit received but no matching transaction | Do not create one by hand. Identify the sender and refund, or ask the client to start a deposit for that amount |
| Sender does not match the client | Do not confirm; escalate to compliance |
| Confirmed, but not paid after ~5 minutes | Check the transaction status: `pending_anchor` with a message → see the next rows; nothing happening → deposit worker down ([section 6](#6-worker-health)) |
| Transaction **blocked**: amount above the payout limit | Expected protection. Verify the operation with compliance; release it only through an authorized change of the limit, then let the worker retry |
| Transaction **blocked**: custody refused or signature invalid | Stop. Treat as a possible custody incident ([section 8](#8-security-incidents)) |
| Payout retried repeatedly (network or custody temporarily unavailable) | No action needed; payouts are idempotent and the worker retries. Escalate if it lasts more than 15 minutes |
| Client has no USDC trustline | If the client's wallet supports claimable balances, the payout is sent as a **claimable balance**: tell the client to claim it from their wallet. Otherwise the transaction waits in `pending_trust`: ask the client to add the USDC trustline, and the deposit worker pays as soon as it exists |

## 4. Off-ramp (client sends USDC, receives fiat)

Client flow: wallet → VERSO webview → USDC amount and quote → payout CCI and source of funds → the wallet sends USDC
to the hot wallet **with the memo shown** → waiting for fiat.

**Operator steps**

1. Wait until the withdrawal shows its **USDC payment detected** (Stellar transaction id present; status
   `pending_anchor` or `pending_external`). Do not pay fiat before that.
2. Open the withdrawal metadata. Check the **USDC amount received** and the **fiat amount** (recomputed with the
   locked rate if the client sent a different USDC amount).
3. Check the payout account: bank, CCI, holder name consistent with the client, and the declared source of funds.
4. Transfer the fiat by CCI/CCE from the matching currency account. Keep the bank confirmation.
5. Select the withdrawal and run **Mark fiat sent**. The transaction becomes `completed` and the wallet is notified.

**Exceptions**

| Situation | Action |
| --- | --- |
| **Mark fiat sent** is refused ("no on-chain USDC payment registered") | Correct protection: do not pay. If Stellar Expert shows the client's payment with the right memo, the withdrawal worker is not processing ([section 6](#6-worker-health)) |
| Client sent USDC **without memo or with a wrong memo** | It cannot be matched to the withdrawal (the anchor only matches by memo) and appears as an `unmatched_inbound` discrepancy. Identify the client from the sending account and the amount, confirm with them, and **refund the USDC to the same sending account**. The withdrawal cannot be completed with that payment: *Mark fiat sent* requires a payment matched on-chain. If the client still wants to cash out, they send the USDC again with the memo shown. Resolve the discrepancy with a note that includes the refund transaction hash |
| Client sent a different USDC amount than quoted | Pay the recomputed fiat amount shown in the admin (same locked rate) |
| Payout CCI rejected by the bank | Contact the client for a correct account; pay again; then mark as sent |
| Withdrawal abandoned before the client paid | No action; it stays open without any funds moved |

## 5. Reconciliation discrepancies

The reconciliation worker compares the hot wallet's on-chain USDC balance with the internal ledger every ~60 s and
opens a discrepancy for anything it cannot explain. Alarms notify operations by email.

| Kind | Meaning | Typical cause | Resolution |
| --- | --- | --- | --- |
| `balance_mismatch` | On-chain balance ≠ internal ledger | A movement not yet ingested, or a movement outside SEP-24 | Wait one cycle; if it persists, find the movement on Stellar Expert and classify it |
| `unmatched_inbound` | USDC arrived without a matching transaction | Treasury top-up, a withdrawal sent without memo or with a wrong memo, or a second payment with the memo of a withdrawal that was already paid | Top-up → resolve as `funding`; client payment → handle as in [section 4](#4-off-ramp-client-sends-usdc-receives-fiat) |
| `unmatched_outbound` | USDC left without a matching transaction | Sweep to the treasury wallet or a refund | Verify it was authorized → resolve as `manual_transfer`. **Not authorized → security incident** |
| `amount_mismatch` | Matched, but the amount differs from the transaction | Client sent a different amount | Check that the off-ramp was settled with the recomputed amount → `fixed` |
| `missing_onchain` | A completed transaction has no on-chain movement | Ingest delay, or data inconsistency | Wait one cycle; then investigate before closing |
| `worker_stale` | Event ingest fell behind or lost continuity | Worker or RPC provider down | Restore the worker ([section 6](#6-worker-health)); confirm the balance check returns to `delta = 0` |

Always write in the discrepancy **why** it was resolved; these notes are part of the audit trail. Use
`false_positive` only after confirming nothing moved.

**Reports.** `python manage.py reconciliation_report --days N` (or the report link in the reconciliation admin)
exports runs, ledger entries and discrepancies for evidence and audits.

## 6. Worker health

The three background workers can be restarted at any time without losing or duplicating work: the deposit worker
uses a lock and saves each signed payment before submitting it; the withdrawal worker resumes from its stored stream
cursor; the reconciliation worker resumes from its stored RPC cursor.

| Symptom | Likely worker | Action |
| --- | --- | --- |
| Deposits confirmed but not paid | Deposit worker | Check its logs for errors; restart |
| Client paid USDC (visible on Stellar Expert) but the withdrawal still waits for payment | Withdrawal worker | Check its logs; restart. After the restart it picks up the payment and the withdrawal advances |
| Heartbeat or ledger-lag alarm | Reconciliation worker | Check its logs and the RPC provider status; restart |

A worker can look "running" in the hosting dashboard while its loop has stopped. **Trust the symptoms above and the
logs, not the dashboard indicator.** Every worker must have its restart policy set to restart on **any** exit, not
only on failure: a stream that ends cleanly exits with code 0.

## 7. Treasury: hot wallet and treasury wallet

- The **hot wallet** (Turnkey) holds only the operating float. The bulk of funds stays in VERSO's **treasury
  wallet**, a separate account that is not connected to the anchor or its servers.
- **Top-up (treasury → hot):** send USDC from the treasury wallet to the hot wallet address taken from the admin,
  then resolve the resulting `unmatched_inbound` as `funding`, with a note.
- **Sweep (hot → treasury):** when the hot wallet holds more than the float, move the excess to the treasury wallet.
  This is a manual treasury operation outside the anchor's code (the anchor has no sweep command): the payment must
  be signed with the hot wallet's key in Turnkey, the only place where that key exists. Resolve the resulting
  `unmatched_outbound` as `manual_transfer`, with a note. Keeping top-ups small avoids most sweeps.
- Keep enough **XLM** in the hot wallet for network fees and, if offered, account creation for new clients.

## 8. Security incidents

| Incident | Immediate action | Follow-up |
| --- | --- | --- |
| Unexpected outflow from the hot wallet | **Revoke the custody API credential in Turnkey** — payouts stop at once (they become blocked) | Move remaining funds to the treasury wallet, investigate the activity log in Turnkey, issue a new credential |
| SEP-10 signing key exposed | Generate a new key, replace it in every service, redeploy | Verify that `stellar.toml` shows the new `SIGNING_KEY` and that a wallet can log in. The key holds no funds, so no on-chain action is needed; completed operations are unaffected |
| Any other secret exposed (API keys, database, Core credential) | Rotate it at the source, update the services, redeploy | Record the incident and the rotation |
| Dust from look-alike addresses | Ignore the dust; never reuse those addresses | Remind the team to take addresses from the admin only |

Secrets are never printed in consoles, logs, chat or documents. Diagnostics that touch configuration show only
whether a value is set.

## 9. Releases

1. Merge to `main` → the **testnet** environment deploys automatically and CI runs the test suite.
2. Test the change on testnet (deposit and withdrawal through a wallet).
3. Deploy **mainnet manually** (mainnet auto-deploy is off).
4. Smoke test on mainnet: `stellar.toml`, SEP-10 challenge, `/sep24/info`, `/sep38/info`, one webview opening.
5. Watch reconciliation and the workers for the next 15 minutes.

## 10. Records

For each operation keep: the bank receipt or bank confirmation, the Stellar transaction hash, and the admin
timestamps (created, fiat confirmed or sent, completed). Settlement time per operation is
`completed − client transfer arrival`. These records support the monthly reconciliation report and compliance
reviews.

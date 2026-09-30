# VERSO Stellar Anchor

VERSO's Stellar anchor (PSAV Peru) implemented with **Django + Polaris**.

Repository kept separate from the VERSO core (`BASE_DE_CLIENTES`, [versotek.io](https://versotek.io)).

**Production (testnet):** https://anchor.versotek.io

## Roadmap

| Tranche | SEPs           | Status                                                                                                                                                                                                                                                                   |
| ------- | -------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **T1**  | SEP-1, SEP-10  | Complete on testnet (`anchor.versotek.io`) — 3 deliverables verified, see details below                                                                                                                                                                                  |
| **T2**  | SEP-24, SEP-38 | **In testnet** — SEP-24 on-ramp + off-ramp webview (VERSO login, KYC gate), SEP-38 quotes, D3 USDC reconciliation. **Workers on Railway** (DEPOSITO, RETIRO, RECONCILIATION). Phase 1 manual fiat settlement; **Evidence:** [`docs/T2_EVIDENCE.md`](docs/T2_EVIDENCE.md) |
| **T3**  | Mainnet        | Pending                                                                                                                                                                                                                                                                  |

## Deliverable status — Tranche 1 (SCF #44)

### Deliverable 1 — SEP-1: Anchor Platform live on testnet + stellar.toml published

**Covered.** The `stellar.toml` file is published and discoverable by SEP-compatible wallets on testnet.

- Public endpoint: [anchor.versotek.io/.well-known/stellar.toml](https://anchor.versotek.io/.well-known/stellar.toml), served by `verso_integrations/sep1.py` (dynamic content: accounts, USDC/PEN/USD currencies, documentation) via `toml_view.py` (UTF-8 charset enforced).
- Discoverability verified with the [Stellar Demo Wallet](https://demo-wallet.stellar.org): after adding the USDC asset with home domain `anchor.versotek.io`, the wallet resolved the `stellar.toml` correctly, recognized the asset and allowed operating it (balance visible, trustline active).
- Status landing page at [anchor.versotek.io/](https://anchor.versotek.io/) (HTML for reviewers; JSON manifest at `?format=json`), implemented in `root.py` + `templates/verso_integrations/root.html`.
- Service health: confirmed operational on Railway (automatic deploy from `main`).

See "Architecture note" below for the documented deviations (Polaris instead of Anchor Platform Docker; signing seed management without AWS KMS; KYC deferred to T2).

### Deliverable 2 — SEP-10: Wallet authentication connected to VERSO's compliance system

**Covered in its authentication component.** The SEP-10 authentication flow was verified end-to-end against the live testnet deployment using the Stellar CLI, following the procedure officially documented by Stellar ([developers.stellar.org — Testing Your Configuration](https://developers.stellar.org/docs/tools/cli)).

#### Reproducing the verification

Anyone can reproduce this against the live anchor with their own testnet account. Commands below are PowerShell; the equivalent works on any shell with `curl` and `jq`.

**Prerequisites** — verify both tools are available:

```powershell
stellar --version
jq --version
```

**Step 1 — Define the client account.** Any funded testnet account works:

```powershell
$ACCOUNT_ID = "GCXGLWL7GEPUDCCZABQVLHTZLDWWXPTURGXODJ6JF6BVJSO4KWU45IFG"
```

**Step 2 — Define the secret key for that account.** Alternatively, resolve it from a Stellar CLI identity (`stellar keys secret <name>`) so the secret never appears on screen:

```powershell
$SECRET_SEED = "S..."
```

**Step 3 — Request the challenge.** The anchor returns an unsigned transaction plus the network passphrase:

```powershell
$CHALLENGE_RESPONSE = curl.exe -s "https://anchor.versotek.io/auth?account=$ACCOUNT_ID"
$CHALLENGE_RESPONSE
```

Expected: `{"transaction":"AAAAAg...","network_passphrase":"Test SDF Network ; September 2015"}`

**Step 4 — Extract the challenge XDR:**

```powershell
$CHALLENGE_XDR = $CHALLENGE_RESPONSE | jq -r '.transaction'
$CHALLENGE_XDR
```

Expected: a base64 string starting with `AAAAAg...`. The challenge is a `sequence = 0` transaction (never submitted to the network) carrying `manage_data` operations with the home domain and a random nonce. It can be inspected with `stellar xdr decode --type TransactionEnvelope --output json-formatted`, or in [Stellar Lab → View XDR](https://lab.stellar.org).

**Step 5 — Sign the challenge with the client wallet:**

```powershell
$SIGNED_CHALLENGE_XDR = ($CHALLENGE_XDR | stellar tx sign --sign-with-key $SECRET_SEED --network testnet 2>&1) | Select-Object -Last 1
$SIGNED_CHALLENGE_XDR
```

Expected: a longer XDR than step 4 — it now carries the client signature.

**Step 6 — Build the request body.** `-Encoding ascii` is required: PowerShell's `utf8` writes a BOM that breaks JSON parsing server-side:

```powershell
$body = @{ transaction = $SIGNED_CHALLENGE_XDR } | ConvertTo-Json -Compress
Set-Content -Path "$env:TEMP\sep10_body.json" -Value $body -Encoding ascii -NoNewline
```

**Step 7 — Submit the signature and receive the JWT:**

```powershell
curl.exe -X POST "https://anchor.versotek.io/auth" -H "Content-Type: application/json" -d "@$env:TEMP\sep10_body.json"
```

> Challenges have a short validity window. If steps 3–7 take too long, the server rejects the challenge as expired — simply restart from step 3.

#### Result

A valid JWT, issued by the VERSO anchor for the account that signed the challenge:

```json
{
  "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJodHRwczovL2FuY2hvci52ZXJzb3Rlay5pby9hdXRoIiwic3ViIjoiR0NYR0xXTDdHRVBVRENDWkFCUVZMSFRaTERXV1hQVFVSR1hPREo2SkY2QlZKU080S1dVNDVJRkciLCJpYXQiOjE3ODY2Njc0NTMsImV4cCI6MTc4Njc1Mzg1MywianRpIjoiMDYxZTUxNDYzNjZiMjRmYjM1OTMxNmZkYmNmNThmMGRiMTFkNjJhNjFlNGNlYzBjMDI3ZjY0Y2ZmNDgxODViMiIsImNsaWVudF9kb21haW4iOm51bGx9..."
}
```

The token payload can be decoded to verify its claims:

```powershell
$AUTH_RESPONSE = curl.exe -s -X POST "https://anchor.versotek.io/auth" -H "Content-Type: application/json" -d "@$env:TEMP\sep10_body.json"
$TOKEN = $AUTH_RESPONSE | jq -r '.token'
$p = $TOKEN.Split('.')[1].Replace('-','+').Replace('_','/'); switch ($p.Length % 4) { 2 { $p += '==' } 3 { $p += '=' } }; [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($p))
```

The decoded payload confirms `iss: https://anchor.versotek.io/auth` and a `sub` matching the signing account, with correct issued-at/expiration timestamps — validating the full cycle: challenge request → signature by the client wallet → signature verification by the backend → JWT issuance.

### Deliverable 3 — First end-to-end simulated deposit on testnet

**Implemented (simulation via Django Admin).** `FiatDeposit` model, Django Admin actions and on-chain USDC payout signed with `SIGNING_SEED`.

Operator flow:

1. Admin → **Simulated fiat deposits** → **Add** (client `G...` account, PEN amount and **exchange rate**; USDC is computed automatically).
2. Review `bank_instructions` (simulated CCI/CCE details).
3. Action **Mark fiat as received** (`pending` → `fiat_confirmed`).
4. Action **Disburse USDC on-chain** → moves through `disbursing`, sends testnet USDC to the client wallet and ends in `disbursed` with `stellar_tx_hash`.
5. Verify on [Stellar Expert testnet](https://stellar.expert/explorer/testnet).

**Deposit states** (`FiatDeposit.status`):

| State            | Meaning                                                   |
| ---------------- | --------------------------------------------------------- |
| `pending`        | Created; awaiting simulated PEN transfer                  |
| `fiat_confirmed` | Operator confirmed fiat receipt; ready to disburse        |
| `disbursing`     | On-chain payment in flight (row lock held)                |
| `disbursed`      | USDC sent; `stellar_tx_hash` and `disbursed_at` persisted |

If the Stellar payment fails, the deposit reverts to `fiat_confirmed` and the error is stored in `status_message` — the operator can retry **Disburse USDC on-chain** without creating a new deposit.

Testnet requirements: the anchor account (`SIGNING_SEED`) must hold an active USDC trustline and sufficient balance.

**Verified in production (Railway).** Full cycle executed against `anchor.versotek.io/admin`, using the same client account as the SEP-10 test (Deliverable 2):

| Field                    | Value                                                              |
| ------------------------ | ------------------------------------------------------------------ |
| Stellar account (client) | `GCXGLWL7GEPUDCCZABQVLHTZLDWWXPTURGXODJ6JF6BVJSO4KWU45IFG`         |
| Amount PEN (simulated)   | 10.00                                                              |
| Exchange rate            | 3.4000                                                             |
| Amount USDC (computed)   | 2.9411765                                                          |
| Final status             | `disbursed` (`USDC disbursed` in admin)                            |
| Stellar tx hash          | `8d664b23e57faff63b957bfd88279862b73d9c5919eb796783735c02abb7c050` |

Transaction confirmed on-chain on [Stellar Expert (testnet)](https://stellar.expert/explorer/testnet/tx/8d664b23e57faff63b957bfd88279862b73d9c5919eb796783735c02abb7c050): status `Successful`, ledger `4130191`, `GBTV5Q…24UOPB sent 2.9411765 USDC to GCXG…5IFG` — the anchor hot wallet (`SIGNING_SEED`) transferring real testnet USDC to the client wallet, triggered by the operator's manual confirmation in the Admin.

This validates the full cycle: deposit request → simulated bank instructions → manual operator confirmation (simulated PEN receipt) → automatic on-chain USDC disbursement → final state tracked — meeting the deliverable's measurement criteria ("all transaction states tracked", "on-chain USDC disbursement confirmed on Stellar testnet").

**Not implemented in T1, deferred to T2:** the "KYC check passes" measurement criterion from the original deliverable text is not covered — the flow allows creating, confirming and disbursing a deposit without any KYC check in between. See point 3 of the "Architecture note".

## Pre-audit hardening (simulated deposit)

Improvements merged into `main` before external review (branch `hardening/pre-audit-fixes`):

| Change              | File                          | Detail                                                                                       |
| ------------------- | ----------------------------- | -------------------------------------------------------------------------------------------- |
| Concurrency locking | `admin.py`                    | `select_for_update` prevents double disbursement if two operators trigger the action at once |
| `disbursing` state  | `models.py`, migration `0003` | Marks the deposit while the Stellar transaction is in flight                                 |
| Safe retry          | `admin.py`                    | Network failure → reverts to `fiat_confirmed` + `status_message`; never left inconsistent    |
| Stellar timeout     | `stellar_payout.py`           | Transaction built with `set_timeout(180)` (previously 30 s)                                  |
| Admin session       | `settings.py`                 | `SESSION_COOKIE_AGE = 600` (10 min of inactivity)                                            |

Automated coverage: `test_deposit_concurrency.py`, `test_stellar_payout.py`.

## Architecture note: deviations from the proposal (SCF #44)

The original proposal describes using the **SDF Anchor Platform** (Java/Kotlin service distributed as a Docker image) with **AWS KMS** for transaction signing. The current implementation differs in the respects documented below for SDF's awareness (points 1–3 since Tranche 1; point 4 added in Tranche 2).

**1. Anchor Platform → django-polaris.** We use [django-polaris](https://django-polaris.readthedocs.io/en/stable/), the Python reference implementation officially maintained by SDF, integrated directly into VERSO's Django backend, instead of the Anchor Platform service deployed as a standalone container. Both alternatives are official SDF solutions and implement the same SEPs with the same level of conformance. This choice avoids operating two services on different runtimes (Python and JVM) and consolidates the deployment into a single process. As a consequence, the repository does not include an application `Dockerfile` or an "Anchor Platform" container; `docker-compose.yml` in this repo is for **local development** (Postgres + Redis). In **production (Railway)** the database is **PostgreSQL** managed by Railway, linked to the web service via `DATABASE_URL`.

**2. AWS KMS: not implemented.** Stellar transaction signing uses the Ed25519 scheme, an algorithm not supported by the AWS KMS `Sign` API (limited to RSA and ECDSA over NIST curves). Additionally, django-polaris does not expose an extension point to delegate signing to an external service: the signing seed is loaded into memory at process startup and used directly through `stellar_sdk`.

Consequently, `SIGNING_SEED` is currently managed as an environment variable on Railway, without an additional custody layer such as KMS or an HSM. This is a deliberate and temporary decision for this delivery: the environment is testnet, with no real funds at risk, and Railway encrypts environment variables at rest. Before operating on mainnet, this secret management will be migrated to a more robust custody scheme (for example AWS Secrets Manager with IAM-restricted access and CloudTrail auditing, and/or a custody provider with native Ed25519 support such as Turnkey or Fireblocks).

**3. KYC verification: not implemented in T1, deferred to T2 (not yet relocated in code).** The original internal proposal called for verifying the client's KYC status and issuing the JWT conditionally inside the SEP-10 authentication endpoint itself. This approach is corrected because it is inconsistent with the protocol's separation of concerns: SEP-10 exclusively certifies ownership of the Stellar account (signature verification) and must not depend on, nor expose, the client's compliance status. The SEP-10 endpoint in T1 issues the JWT solely on the basis of cryptographic signature verification, per the standard.

The correct place in the protocol for the KYC check and the DIDIT onboarding redirect is the SEP-24 interactive webview (Tranche 2), not T1's simulated deposit flow without a webview. Tranche 2 added the KYC gate inside the webview (`sep24/kyc_gate.py`, `sep24/kyc_views.py`: VERSO login/register, email verification, DIDIT redirect), so deposits and withdrawals now require an approved KYC status from VERSO Core.

**4. D3 reconciliation: Horizon Streaming API → Stellar RPC `getEvents` (Tranche 2).** The proposal described a service listening to the hot wallet through the **Horizon Streaming API**. The reconciliation service (`backend/reconciliation/`) is built on **Stellar RPC** instead, for these reasons:

- **Horizon is being phased out.** Stellar's official documentation positions Stellar RPC as the API for new integrations, with Horizon on a deprecation path. Building a new, long-lived service on Horizon would mean rewriting it before or shortly after mainnet.
- **Protocol 23 (CAP-67) makes RPC sufficient for classic assets.** With unified events, classic USDC payments also emit `transfer` / `mint` / `burn` / `clawback` events from the asset's Stellar Asset Contract (SAC). `getEvents` filters on the USDC SAC with the hot wallet in the event topics (`reconciliation/events.py`) capture every USDC movement in or out of the wallet, without parsing Horizon operation types one by one. Verified on testnet on 2026-09-28: a 10 USDC SEP-24 withdrawal (tx `c6efa31f…`) appeared as a `transfer` event carrying the amount and the memo.
- **Balance check at a known ledger.** The on-chain side of the reconciliation reads the USDC trustline with `getLedgerEntries`, which returns the ledger sequence it reflects. The internal ledger is compared at that same ledger, so a run never compares a balance against a partial set of events.
- **Resilience with a persisted cursor.** Horizon streaming uses a long-lived HTTP connection that can drop silently. The worker instead polls `getEvents` every ~5 seconds (one Stellar ledger closes every ~5–6 s, so latency is at most about one ledger) and stores the cursor in PostgreSQL (`SyncState`). After a restart or a network failure it resumes exactly where it stopped, with no events lost or duplicated (`LedgerEntry.event_id` is unique).
- **Same requirement, same output.** Real-time capture, PostgreSQL storage with the requested schema, balance reconciliation and CloudWatch alerting are unchanged; only the data source differs.

**Operational note:** Stellar RPC keeps a limited event history (a few days on public providers). If the worker were down longer than that window, events could not be backfilled from RPC. Two safeguards cover this: the CloudWatch `verso-recon-heartbeat` alarm treats missing heartbeats as breaching, so it fires after ~5 minutes without the worker, long before the retention window ends; and if the cursor ever falls outside the retention window, the worker records a critical `worker_stale` discrepancy (`reconciliation/sync.py`) instead of silently skipping events. The balance check would also expose any missed movement as a `balance_mismatch`. The Polaris SEP-24 withdrawal watcher (`watch_transactions`, service **RETIRO**) still uses Horizon, because that is how django-polaris is built; this affects only transaction status detection, not reconciliation.

**Where Horizon is still used, and why.** django-polaris 2.x only supports Horizon: `watch_transactions` (service **RETIRO**, detects inbound USDC for withdrawals), `process_pending_deposits` (service **DEPOSITO**, submits USDC payouts) and SEP-10 account loading all go through Polaris' `HORIZON_URI`. This is a limitation of the library, not a design choice; replacing it would mean forking Polaris or rewriting its workers, which no Tranche 2 deliverable requires. VERSO's own code follows a single rule: the reconciliation service uses Stellar RPC, and the T1 admin payout (`stellar_payout.py`) reuses Polaris' `HORIZON_URI` so every component always targets the same network. Because Horizon is being phased out, the Polaris Horizon dependency will be reviewed before mainnet (T3). Full design: `docs/D3_RECONCILIATION_PLAN.md` §0 and §3 (Spanish).

## Deliverable status — Tranche 2 (SCF #44)

Full evidence (transaction ids, Stellar hashes, quote ids, CloudWatch and admin screenshots): **[`docs/T2_EVIDENCE.md`](docs/T2_EVIDENCE.md)**.

| #   | Deliverable                                       | Status                                                                                                     |
| --- | ------------------------------------------------- | ---------------------------------------------------------------------------------------------------------- |
| 1   | SEP-24 on-ramp and off-ramp on testnet            | **Covered** — 17 completed (12 on-ramp + 5 off-ramp), 11 in PEN, 2 client accounts                         |
| 2   | SEP-38 quotes from VERSO's live pricing engine    | **Covered** — 10/10 consecutive firm quotes match VERSO Core; quote shown in a wallet flow                 |
| 3   | Real-time USDC reconciliation + CloudWatch alerts | **Covered on testnet** — 100% of runs OK, 0 discrepancies, alerts tested; 14-day window: planned deviation |

### Implementation overview

| Component                                                                 | Status                                   |
| ------------------------------------------------------------------------- | ---------------------------------------- |
| SEP-24 webview on-ramp **PEN/USD → USDC** and off-ramp **USDC → PEN/USD** | Done                                     |
| VERSO login/register, email verification, DIDIT KYC gate in the webview   | Done                                     |
| SEP-38 quotes (`rate_venta` / `rate_compra` from VERSO Core)              | Done                                     |
| `process_pending_deposits` → USDC on-chain (Polaris)                      | Done (Railway worker **DEPOSITO**)       |
| `watch_transactions` → SEP-24 off-ramp detection (Polaris / Horizon)      | Done (Railway worker **RETIRO**)         |
| D3 reconciliation worker (RPC sync + balance check + CloudWatch)          | Done (Railway worker **RECONCILIATION**) |
| CI: Postgres + Redis, 181 tests                                           | Done                                     |

**Phase 1 fiat settlement:** PEN/USD receipt (on-ramp) and payout (off-ramp) are confirmed manually by VERSO operations in the admin (**Mark PEN received** / **Mark fiat sent**). eposit, withdrawal and reconciliation **workers run on Railway** (see **Production workers** below).

#### Architecture: Django + Polaris (continuation of the T1 decision)

Tranche 2 keeps the stack chosen in Tranche 1: **django-polaris integrated into VERSO's Django backend** instead of the SDF Anchor Platform container (see _Architecture note_, point 1). The T2 deliverable texts were written against Anchor Platform, so this table maps each proposed component to its implementation:

| Proposal (Anchor Platform)                                      | Implementation (Django + Polaris)                                                                                                                                                                                                                    |
| --------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Anchor Platform SEP-24 server + VERSO backend callbacks         | Polaris SEP-24 endpoints (`/sep24/...`) + VERSO integration classes: `VersoDepositIntegration` (`sep24/integration.py`), `VersoWithdrawIntegration` (`sep24/withdraw_integration.py`), `VersoRailsIntegration` (`rails.py`), registered in `apps.py` |
| Anchor Platform SEP-38 endpoint calling VERSO `/callbacks/rate` | Polaris SEP-38 endpoints (`/sep38/info`, `/price`, `/prices`, `/quote`) + `VersoQuoteIntegration` (`sep38.py`), which fetches the live rate from VERSO Core `GET /internal/rates/pen-usdc/` (`rates.py`) on every request                            |
| SEP-24 interactive webview in React 18 + Vite + TypeScript      | Django templates + vanilla JS (`templates/sep24/onboarding/`, `static/sep24/`), served by the same backend under VERSO branding                                                                                                                      |
| Anchor Platform event/status handling                           | Polaris workers on Railway: `process_pending_deposits` (DEPOSITO), `watch_transactions` (RETIRO) — see **Production workers**                                                                                                                        |

### Deliverable 1 — SEP-24: on-ramp and off-ramp on testnet

**Covered.** Criterion: _10+ testnet on-ramp and off-ramp transactions completed._ Result: **17 completed** transactions (12 on-ramp, 5 off-ramp; 11 in PEN, 6 in USD) from 2 client accounts, each with a successful on-chain USDC payment. Full list in `docs/T2_EVIDENCE.md` → Deliverable 1.

- **On-ramp (PEN/USD → USDC):** wallet SEP-10 → SEP-24 interactive webview (VERSO login + KYC) → CCI/CCE instructions → operations confirm fiat receipt (**Mark PEN received**) → `process_pending_deposits` (Railway **DEPOSITO**) sends USDC on-chain → `completed`.
- **Off-ramp (USDC → PEN/USD):** wallet SEP-10 → SEP-24 webview (VERSO login + KYC, payout CCI) → client sends USDC with the Polaris `hash` memo → `watch_transactions` (Railway **RETIRO**) detects it → operations send fiat (**Mark fiat sent**) → `completed`.
- **KYC gate (pending item from T1 Deliverable 3):** the webview requires a VERSO account (login/register, email verification) and an **approved KYC status** from VERSO Core, with DIDIT onboarding when needed (`sep24/kyc_gate.py`, `sep24/kyc_views.py`). Deposits and withdrawals cannot start without it. See _Architecture note_, point 3.
- **Wallet compatibility:** discovered through `stellar.toml` (`TRANSFER_SERVER_SEP0024`); tested with the Stellar Demo Wallet. See **Stellar wallets** below.

### Deliverable 2 — SEP-38: quotes from VERSO's live pricing engine

**Covered.** Criteria: _SEP-38 endpoint returns valid quotes with live rate, fee and final USDC amount; 10 consecutive quotes validated against VERSO's pricing engine; quotes displayed in a SEP-38 compatible wallet before confirmation._

- **10/10 consecutive firm quotes** (`POST /sep38/quote`, alternating PEN→USDC / USDC→PEN) match VERSO Core's live rate (`validate_quotes`, run in production on 2026-09-29).
- **Quote in the wallet before confirmation:** Stellar Demo Wallet + VERSO webview, on-ramp and off-ramp (screenshots in `docs/T2_EVIDENCE.md` → Deliverable 2).
- **Deviation:** firm quotes expire after **180 s** instead of 30 s (see _Quote expiration window_ below).

#### Pricing and fee model

**VERSO's commission is embedded in the exchange rate (spread), not charged as a separate fee.** VERSO's pricing engine (VERSO Core) publishes two live rates per pair, already including VERSO's margin:

| Rate          | Direction                           | Used for              |
| ------------- | ----------------------------------- | --------------------- |
| `rate_venta`  | VERSO **sells** USDC to the client  | On-ramp (PEN → USDC)  |
| `rate_compra` | VERSO **buys** USDC from the client | Off-ramp (USDC → PEN) |

The difference between `rate_venta` and `rate_compra` (the spread) is VERSO's commission. Rates are refreshed by VERSO Core and fetched live by the anchor on every quote (`rates.py` → `GET /internal/rates/pen-usdc/`); the anchor never caches or adjusts them.

Consequences for the SEP responses:

- **SEP-38 `price` is all-in.** For PEN → USDC, `price = rate_venta` (PEN per 1 USDC); for USDC → PEN, `price = 1 / rate_compra` (`sep38.py:price_for_pair`). The price returned to the wallet already reflects VERSO's commission.
- **Price precision: 4 decimals.** PEN and USD are configured with `significant_decimals = 4` (`polaris_setup.py:FIAT_SIGNIFICANT_DECIMALS`, migration `0009`), matching VERSO Core's rate precision. SEP-38 rounds `price` to the sell asset's decimals, so with the former 2 decimals a Core rate of `3.3750` was quoted as `3.38` (≈0.13% off the webview amount) and `1.0010` as `1.00` (hiding the USD spread). Quotes now carry the exact Core rate.
- **Final USDC amount** = `amount_pen / rate_venta` (on-ramp); **final PEN amount** = `amount_usdc × rate_compra` (off-ramp). There are no hidden deductions after the quote.
- **Explicit fee is zero.** SEP-24 transactions record `amount_fee = 0` (`sep24/integration.py`, `sep24/withdraw_integration.py`, `rails.py`), and `amount_in − amount_fee` equals the converted amount exactly. A wallet displaying the quote therefore shows the total cost the client pays.
- Network fees (XLM) for the on-chain USDC payment are paid by VERSO's hot wallet and are not passed on to the client.

Example (on-ramp, illustrative): VERSO Core returns `rate_venta = 3.3750`. The client deposits 10.00 PEN and receives 10.00 / 3.3750 = **2.9629630 USDC**; `amount_fee = 0`, and VERSO's commission is the margin already contained in 3.3750.

**How to verify:** request `GET /sep38/price?sell_asset=iso4217:PEN&buy_asset=stellar:USDC:<issuer>&sell_amount=10` and compare `price` with `rate_venta` from VERSO Core `/internal/rates/pen-usdc/` at the same moment. They must match (quantized to the asset's decimals).

#### How quotes reach the wallet (SEP-24 + SEP-38)

VERSO's on/off-ramp runs on **SEP-24**, where the wallet hands the user to the anchor's interactive webview instead of building the transaction itself. The quote is therefore shown **inside the wallet flow, before the client confirms**, in two places:

1. **VERSO webview** (opened by the wallet): the amount screen shows the live rate from VERSO Core, the amount the client sends and the final amount they receive — e.g. S/ 20.00 → 5.7142857 USDC at S/ 3.5000 / USDC — before _Confirmar transferencia_ / _Confirmar cuenta de destino_. The price is fixed at that moment from the same `rate_venta` / `rate_compra` that SEP-38 uses.
2. **Wallet UI**: the wallet polls `GET /sep24/transaction` and displays `amount_in`, `amount_out` and `amount_fee` (with their assets) while the webview is waiting for confirmation.

The **SEP-38 quote server** (`ANCHOR_QUOTE_SERVER` in `stellar.toml`) exposes the same prices for wallets that request quotes directly (`/sep38/info`, `/price`, `/prices`, `/quote`); `validate_quotes` below shows they match VERSO Core. In SEP-24 the wallet does not send a SEP-38 `quote_id`: the flow that consumes SEP-38 quotes natively is SEP-6/SEP-31, which VERSO does not offer (see _Protocols advertised in `stellar.toml`_).

Evidence (Stellar Demo Wallet, testnet): `docs/evidence/t2/d2_deposit_pen_quote.png` and `docs/evidence/t2/d2_withdraw_pen_quote.png`, described in `docs/T2_EVIDENCE.md` → Deliverable 2.

#### Quote validation against VERSO's pricing engine

`validate_quotes` requests consecutive **firm** SEP-38 quotes over HTTP, exactly as a wallet would, and checks each one against the live rate from VERSO Core:

```powershell
cd backend
python manage.py validate_quotes --count 10 --out /tmp/d2_quotes
```

For each quote it:

1. Authenticates with **SEP-10** using a throwaway keypair (Polaris accepts unfunded accounts signed by their master key, so no client secret is needed).
2. Reads VERSO Core's rate (`/internal/rates/pen-usdc/`) **right before and right after** calling `POST /sep38/quote`, so a rate change in between is not reported as a mismatch.
3. Checks that `price` equals the price computed from the Core rate (`sep38.py:price_for_pair`) and that `buy_amount = sell_amount / price`.
4. Records `expires_at` and the resulting window (≈180 s) and the fee (`0`, commission embedded in the spread).

Options: `--direction on|off|both` (PEN→USDC, USDC→PEN, or alternating), `--sell-amount-pen 100`, `--sell-amount-usdc 10`, `--interval 2` (seconds between quotes), `--base-url` (defaults to `HOST_URL`). With `--out`, it writes `quotes.json` (summary + every quote) and `quotes.csv`. The command exits with an error if any quote does not match.

Run it from the **Railway Shell** on VERSO-SDF (it needs `VERSO_CORE_API_KEY` to read Core's rate), then download the files for the SCF report.

#### Quote expiration window: 3 minutes (deviation from 30 s)

Firm quotes (`POST /sep38/quote`) expire **180 seconds** after creation (`VERSO_QUOTE_TTL_SECONDS=180`, applied in `sep38.py:quote_expires_at`). The original deliverable text specifies a 30-second window; we extended it to 3 minutes:

- **30 seconds is too short for a real user.** Between receiving the quote and confirming, the client has to review the amount in the wallet, open the SEP-24 webview and, when needed, sign in to VERSO. A 30-second window would often expire before confirmation and force the user to re-quote.
- **Rate risk stays bounded.** VERSO's commission is embedded in the spread between `rate_venta` and `rate_compra` (see _Pricing and fee model_), which gives margin against PEN/USD movement within a 3-minute window, and every new quote fetches the live rate again from VERSO Core.
- **Clients can request less.** A wallet can send `expire_after` to ask for a shorter window; requests beyond 180 seconds are rejected with `400` ("the requested expiration cannot be provided").
- **Configurable without code changes.** The window is an environment variable, so it can be tuned per environment (testnet/mainnet) from Railway.

**Configuration:** the default of 180 seconds lives in code (`config/settings.py`), so **no `.env` entry is required** — neither locally (`backend/.env`) nor on Railway. Set `VERSO_QUOTE_TTL_SECONDS` only to override it. If the variable exists on Railway (service **VERSO-SDF**), its value takes precedence over the code default: make sure it is `180` or remove it. To verify, create a firm quote with `POST /sep38/quote` (SEP-10 JWT required) and check that `expires_at` is about 3 minutes after the request time.

The SEP-24 webview is not affected: deposits and withdrawals lock the price when the user confirms the amount in the webview, using the live rate at that moment.

### Deliverable 3 — Real-time USDC reconciliation and CloudWatch alerts

**Covered on testnet.** Criteria: _(1) every USDC movement on the testnet hot wallet captured in real time and reconciled with the internal ledger; (2) 14-day monitoring period with no unresolved discrepancies; (3) CloudWatch alerts configured and tested._

1. **Real-time capture and reconciliation:** the **RECONCILIATION** worker ingests every USDC movement of the hot wallet through Stellar RPC `getEvents` (every ~5 s), stores it in PostgreSQL (`LedgerEntry`, with the schema requested in the proposal), matches it to its SEP-24 transaction and compares on-chain vs internal balance every ~60 s. Since bootstrap: **100% of runs OK, balances equal, every movement matched**. Horizon → RPC change: see _Architecture note_, point 4.
2. **Monitoring with no unresolved discrepancies:** **0 discrepancies** since bootstrap (2026-09-28). The formal 14-day window is a **planned deviation**, moved to mainnet (see below).
3. **CloudWatch alerts:** 4 alarms + SNS email, tested on 2026-09-29 with a synthetic critical discrepancy (OK → ALARM → OK).

Evidence: `docs/T2_EVIDENCE.md` → Deliverable 3. Design: `docs/D3_RECONCILIATION_PLAN.md` (Spanish).

#### Reconciliation worker

On-chain USDC movements on the anchor hot wallet are ingested via **Stellar RPC `getEvents`** (CAP-67 SAC events), stored in PostgreSQL, and reconciled against the USDC trustline balance (`getLedgerEntries`). The **RECONCILIATION** Railway service runs this loop; see **Production workers (Railway)** below.

**One-time bootstrap** (Railway **Shell** on VERSO-SDF after web deploy + migrate—not `railway run` from Windows unless you override `DATABASE_URL` with the public Postgres URL):

```powershell
cd backend
python manage.py reconciliation_bootstrap
```

Expected output includes `Bootstrap OK — hot=… opening_balance=… start_ledger=…`. Verify in admin: `/admin/reconciliation/syncstate/1/` (`observation_started_at`, `opening_balance`). Do **not** put bootstrap in the web start command permanently—the process exits and would take the site down.

**Worker** (Railway service **RECONCILIATION**; same env as web):

```powershell
cd backend && python manage.py reconciliation_worker --loop
```

#### Testnet operational status

Reconciliation has been **running continuously in production** since the first worker deploy after bootstrap:

| Milestone                                                          | When                                                           | Where to verify                                                                                  |
| ------------------------------------------------------------------ | -------------------------------------------------------------- | ------------------------------------------------------------------------------------------------ |
| Bootstrap (`SyncState`, opening balance, `observation_started_at`) | **2026-09-28** (~14:17 UTC-5)                                  | `/admin/reconciliation/syncstate/1/`                                                             |
| First `ReconciliationRun`                                          | Same day, immediately after **RECONCILIATION** service started | `/admin/reconciliation/reconciliationrun/` (oldest row)                                          |
| Steady state                                                       | Every **~60 s** since then                                     | Latest runs: `status=ok`, `delta=0`, `ledger_lag=0`, `open_discrepancies=0`, `unmatched_count=0` |

Each run compares **on-chain USDC trustline balance** (RPC `getLedgerEntries`) with **internal ledger**
(`opening_balance` + sum of `LedgerEntry` movements). A long streak of `ok` / `delta=0` rows (thousands of runs; admin
paginates 100 per page) demonstrates that the reconcile loop is stable—not a one-off test.

**Sync ingest** (RPC `getEvents` every ~5 s) updates `SyncState.last_success_at`; **reconcile** (every ~60 s) creates
the rows shown in admin. Both run inside `reconciliation_worker --loop`.

#### Planned deviation — 14-day monitoring window

The original D3 completion text asks for a **14-day monitoring period with no unresolved discrepancies on testnet**.
VERSO documents this **intentional deviation**:

| Grant text                                             | VERSO approach                                                                                                                     |
| ------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------- |
| Wait 14 calendar days on **testnet** before closing D3 | On **testnet**: prove **continuous** reconciliation from bootstrap forward (run history + optional report `--days N`).             |
| —                                                      | Run the formal **14-day production monitoring window on mainnet** after go-live (real fiat, real client volume, operational risk). |

**Why:** testnet uses simulated fiat settlement and low traffic; holding the calendar for 14 days on testnet does not
add operational signal once thousands of consecutive `ReconciliationRun` rows show `delta=0`. The same worker, alerts,
and report code apply on mainnet—only environment variables change (`STELLAR_NETWORK_PASSPHRASE`, **`HORIZON_URI`**, `STELLAR_RPC_URL`,
`RECON_ENV`, issuers). See `docs/D3_RECONCILIATION_PLAN.md` §0 for the full rationale (Spanish).

**SCF evidence on testnet today:** admin screenshots or CSV of reconciliation runs; export via
`/admin/reconciliation/report/?days=N` or `reconciliation_report --days N --out …` covering elapsed time since
`observation_started_at`, with `no_unresolved_discrepancies: true`. CloudWatch test alert remains a separate checklist item.

**Report** (evidence export; use `--days` = elapsed days since bootstrap on testnet, or 14 on mainnet):

```powershell
python manage.py reconciliation_report --days 14 --out /tmp/d3_report
```

Admin ZIP download: `/admin/reconciliation/report/?days=14`

See `docs/D3_RECONCILIATION_PLAN.md` for CloudWatch alarms, matching rules, and acceptance criteria.

#### CloudWatch alerts — setup

Configured on 2026-09-29 (AWS `us-east-1`); test evidence in `docs/T2_EVIDENCE.md` → Deliverable 3.

1. **IAM:** dedicated user `verso-anchor-recon` (no console access) with a least-privilege policy — `cloudwatch:PutMetricData`, `cloudwatch:PutMetricAlarm`, `cloudwatch:DescribeAlarms`, and `sns:Publish`, `sns:CreateTopic`, `sns:Subscribe` restricted to `arn:aws:sns:*:*:verso-anchor-recon-*`. Access key use case: _application running outside AWS_.
2. **Railway — RECONCILIATION service only** (not Shared Variables; only this worker and the alert commands use AWS): `RECON_ALERT_BACKEND=cloudwatch`, `RECON_ENV=testnet`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_DEFAULT_REGION` (read by boto3) and `AWS_REGION`. Deploy and wait until CloudWatch → Metrics shows namespace `VERSO/AnchorReconciliation` — creating the alarms before data exists makes the heartbeat alarm fire.
3. **Alarms + SNS topic** (`railway ssh --service RECONCILIATION`): `python manage.py setup_cloudwatch_alarms --create-topic --email <ops email>` → prints the topic ARN and creates `verso-recon-open-discrepancies`, `verso-recon-balance-delta`, `verso-recon-heartbeat`, `verso-recon-ledger-lag`. Confirm the subscription email from AWS.
4. **Railway:** add `RECON_SNS_TOPIC_ARN=<printed ARN>` on RECONCILIATION and deploy.
5. **Test:** `python manage.py reconciliation_test_alert --resolve` → SNS email, `verso-recon-open-discrepancies` goes to ALARM for about a minute, then back to OK on the next worker run (test discrepancies are excluded from the metric).

Approximate cost: 5 custom metrics + 4 alarms + one `PutMetricData` per minute — a few USD per month.

## Stellar wallets (Lobstr, Freighter, Demo Wallet, …)

This anchor is designed for the **standard Stellar on-ramp flow**, not as a standalone website. Compatible wallets (Lobstr, Freighter, xBull, Solar, [Stellar Demo Wallet](https://demo-wallet.stellar.org), etc.) integrate via **SEP-10** and **SEP-24** when the anchor is listed in `stellar.toml` with a **public** `HOST_URL`.

**Protocols advertised in `stellar.toml`:** `TRANSFER_SERVER_SEP0024` (SEP-24), `ANCHOR_QUOTE_SERVER` (SEP-38) and `WEB_AUTH_ENDPOINT` (SEP-10). **SEP-6 is not offered.** django-polaris writes `TRANSFER_SERVER` (the SEP-6 field in SEP-1) automatically whenever SEP-24 is active, which made wallets such as the Stellar Demo Wallet show a SEP-6 option that returned 404. `sep1.py` removes it (`"TRANSFER_SERVER": None`; the TOML encoder omits `None` keys), and `test_sep1.py` checks that the published TOML contains `TRANSFER_SERVER_SEP0024` but not `TRANSFER_SERVER`. If SEP-6 is implemented in the future, publish `TRANSFER_SERVER` again pointing to `/sep6`.

### What the wallet does

1. **SEP-10** — User signs a challenge with their Stellar account (`G...`); anchor returns a JWT.
2. **SEP-24 interactive deposit** — Wallet calls `POST /sep24/transactions/deposit/interactive` with the JWT and opens the returned **`url`** in an **in-app webview**.
3. **Completion** — Wallet polls or refreshes transaction status; when the anchor marks the deposit complete, USDC appears in the user's Stellar account.

The wallet does **not** implement VERSO login, KYC, or bank transfer UI — it only embeds the anchor's webview.

### What runs inside the webview (anchor-hosted)

| Step | Screen                                                      | Backend                                     |
| ---- | ----------------------------------------------------------- | ------------------------------------------- |
| 1    | **Iniciar sesión** VERSO (email + password)                 | `POST /internal/users/login/` on VERSO Core |
| 2    | _Optional_ **Crear cuenta** (link “¿Aún no tienes cuenta?”) | `POST /internal/users/register/`            |
| 3    | Verificar email (código)                                    | `POST /internal/users/verify-email/`        |
| 4    | KYC / DIDIT (redirect)                                      | VERSO web + callback to anchor              |
| 5    | Monto PEN → USDC                                            | `GET /internal/rates/pen-usdc/`             |
| 6    | Instrucciones CCI/CCE                                       | Anchor only                                 |
| 7    | Estado `completed` + USDC on-chain                          | `process_pending_deposits` worker           |

The Stellar account used in SEP-10 is the account that receives USDC. After login, Core links that wallet via `POST /internal/users/link-stellar-key/`.

### What wallets never call directly

| Component                         | Visible to wallet?                                                              |
| --------------------------------- | ------------------------------------------------------------------------------- |
| VERSO Core (`VERSO_CORE_API_URL`) | No — anchor backend only                                                        |
| Django Admin “Mark PEN received”  | No — operations-only; Phase 1 manual settlement (bank webhook planned)          |
| `process_pending_deposits`        | No — anchor worker                                                              |
| SEP-38 `/sep38/quote`             | Optional — wallets may quote before deposit; SEP-24 does not consume `quote_id` |

### Local vs production

| Environment                    | Wallet can connect?                                                                                                             |
| ------------------------------ | ------------------------------------------------------------------------------------------------------------------------------- |
| `localhost:8000`               | **No** — mobile/desktop wallets cannot reach your machine. Use the Python script below (simulates wallet SEP-10 + interactive). |
| `anchor.versotek.io` (testnet) | **Yes** — if the wallet supports SEP-24 USDC deposits and discovers the anchor via `stellar.toml`.                              |

**SEP-38** is complementary: wallets can fetch indicative/firm quotes before operating; the **firm price for the on-ramp** is set when the user confirms the PEN amount in the SEP-24 webview, not from a SEP-38 `quote_id`.

### Local E2E — SEP-24 PEN → USDC (full stack)

Prerequisites:

- `backend/.env` from `.env.example` (`ACTIVE_SEPS` includes `sep-24`, `REDIS_URL`, `LOCAL_MODE=1`).
- Anchor **`SIGNING_SEED`** funded with **XLM + USDC testnet** (Circle faucet or transfer).
- **VERSO Core** (DJANGO_RAIL) on `:8001` with `ANCHOR_SHARED_SECRET` = anchor `VERSO_CORE_API_KEY`.
- Testnet client account (`G...` + secret) for SEP-10.

**Anchor `.env` (local):**

```env
HOST_URL=http://localhost:8000
VERSO_CORE_API_URL=http://127.0.0.1:8001
VERSO_CORE_API_KEY=<same as ANCHOR_SHARED_SECRET in VERSO>
REDIS_URL=redis://localhost:6379/0
```

**VERSO `.env` (DJANGO_RAIL):**

```env
ANCHOR_SHARED_SECRET=<same value>
ANCHOR_KYC_RETURN_URL=http://localhost:8000/sep24/kyc/callback/
```

**Five processes** (all must stay running during a test):

| #   | Service        | Command                                     | Folder         |
| --- | -------------- | ------------------------------------------- | -------------- |
| 1   | Redis          | `docker compose up -d`                      | `ANCHOR/`      |
| 2   | Anchor API     | `python manage.py runserver 8000`           | `backend/`     |
| 3   | Deposit worker | `python manage.py process_pending_deposits` | `backend/`     |
| 4   | VERSO Core     | `python manage.py runserver 8001`           | `DJANGO_RAIL/` |
| 5   | Webview entry  | SEP-10 + interactive script (below)         | `backend/`     |

Setup once per environment:

```powershell
cd backend
python manage.py migrate
python manage.py seed_polaris_t2
```

**Generate webview URL** (simulates what Lobstr/Freighter do — run in **PowerShell**):

```powershell
cd backend
..\venv\Scripts\Activate.ps1
python -c @"
import requests
from stellar_sdk import Keypair, TransactionEnvelope

CLIENT = 'G...'   # your testnet public key
SECRET = 'S...'   # matching secret
BASE = 'http://localhost:8000'

kp = Keypair.from_secret(SECRET)
r = requests.get(f'{BASE}/auth', params={'account': CLIENT, 'home_domain': 'localhost'}, timeout=15)
r.raise_for_status()
data = r.json()
env = TransactionEnvelope.from_xdr(data['transaction'], data['network_passphrase'])
env.sign(kp)
token = requests.post(f'{BASE}/auth', json={'transaction': env.to_xdr()}, timeout=15).json()['token']
dep = requests.post(
    f'{BASE}/sep24/transactions/deposit/interactive',
    headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
    json={'asset_code': 'USDC', 'account': CLIENT},
    timeout=15,
)
dep.raise_for_status()
body = dep.json()
print('TX_ID:', body['id'])
print('WEBAPP_URL:', body['url'])
"@
```

Open **`WEBAPP_URL`** in the browser.

**Operator flow after webview:**

1. Login (or “Crear cuenta” → register → verify email → KYC).
2. Enter PEN amount → CCI instructions (`more_info`).
3. Admin → **VERSO INTEGRATIONS → SEP-24 deposits (PEN on-ramp)** → action **Mark PEN received** (not “Django Polaris → Transactions”).
4. With `process_pending_deposits` running, status becomes **`completed`** and USDC is sent on-chain (~10 s).
5. Verify on [Stellar Expert testnet](https://stellar.expert/explorer/testnet).

**Local dev shortcuts** (in `backend/.env`):

| Variable                                     | Effect                                                       |
| -------------------------------------------- | ------------------------------------------------------------ |
| `VERSO_MOCK_KYC=approved`                    | Skip KYC gate; go straight to PEN amount (restart runserver) |
| `VERSO_MOCK_KYC_AUTO_APPROVE_AFTER_VERIFY=1` | Auto-approve KYC after email verify                          |
| `VERSO_MOCK_AUTO_CONFIRM_FIAT=1`             | Skip admin “Mark PEN received” (never use in production)     |

**Email verification in local Core:** SMTP may fail; read the code from VERSO shell:

```powershell
cd "path\to\DJANGO_RAIL"
python manage.py shell -c "from django.contrib.auth.models import User; u=User.objects.get(email__iexact='you@example.com'); print(u.perfil.codigo_verificacion_email)"
```

**Mock rates only** (without VERSO Core): run a stub on `:9000` returning `{"rate_venta":"3.5000","rate_compra":"3.4000"}` and set `VERSO_CORE_API_URL=http://localhost:9000`. For the full onboarding flow (login, KYC), use real Core on `:8001`.

## Repository structure

The Git repository lives at the **`ANCHOR/` root**. Django sits under `backend/`; Railway and the build use the root (`railpack.json` runs `cd backend`).

```
ANCHOR/
├── .github/workflows/
│   └── backend-tests.yml       # CI: Postgres + Redis; all SEPs + reconciliation; 181 tests
├── docs/
│   ├── D3_RECONCILIATION_PLAN.md  # D3 design: RPC getEvents, matching, CloudWatch
│   ├── T2_EVIDENCE.md          # Tranche 2 deliverable evidence (D1–D3)
│   └── evidence/t2/            # Screenshots and exported reports for T2
├── backend/
│   ├── manage.py
│   ├── config/                 # settings, urls, wsgi
│   ├── templates/
│   │   ├── verso_integrations/
│   │   │   └── root.html       # HTML landing page at /
│   │   └── sep24/onboarding/   # SEP-24 webview screens (deposit + withdraw)
│   ├── .env.example            # Environment variable template
│   ├── reconciliation/         # D3: USDC ledger sync + balance reconciliation
│   │   ├── models.py           # SyncState, LedgerEntry, ReconciliationRun, Discrepancy
│   │   ├── rpc_client.py       # Stellar RPC getEvents / getLedgerEntries
│   │   ├── sync.py             # Event ingest with persisted cursor
│   │   ├── matching.py         # Match on-chain events to SEP-24 transactions
│   │   ├── reconcile.py        # On-chain vs internal balance check
│   │   ├── alerts.py           # Log / CloudWatch + SNS alert backends
│   │   ├── report.py           # 14-day evidence report
│   │   ├── management/commands/  # bootstrap, worker, report, CloudWatch setup, test alert
│   │   └── tests/              # 35 tests
│   └── verso_integrations/
│       ├── apps.py             # Polaris registration (toml, quote, deposit, withdrawal, rails)
│       ├── admin.py            # FiatDeposit, Sep24DepositMeta, Sep24WithdrawMeta admin
│       ├── models.py           # FiatDeposit, Sep24DepositMeta, Sep24WithdrawMeta
│       ├── migrations/         # 0001–0009
│       ├── root.py             # Landing / health check at /
│       ├── sep1.py             # Dynamic stellar.toml content
│       ├── sep10.py            # SEP-10 (400 errors on invalid XDR)
│       ├── sep38.py            # SEP-38 quote integration
│       ├── sep24/              # SEP-24 webview (on-ramp, off-ramp, onboarding/KYC)
│       │   ├── integration.py  # DepositIntegration, login/register gate
│       │   ├── withdraw_integration.py  # WithdrawIntegration (USDC → PEN/USD)
│       │   ├── kyc_gate.py     # Session onboarding steps
│       │   ├── kyc_views.py    # DIDIT redirect + login/register switch
│       │   └── onboarding_forms.py
│       ├── core_client.py      # VERSO Core HTTP (login, register, verify, link key)
│       ├── rails.py            # RailsIntegration (fiat confirmation, outgoing withdrawals)
│       ├── rates.py            # VERSO Core rate_venta / rate_compra
│       ├── deposit.py          # CCI helpers + USDC amount computation
│       ├── polaris_setup.py    # seed_polaris_t2 data
│       ├── kyc_bridge.py       # Core client lookup by Stellar key (used by reconciliation)
│       ├── stellar_payout.py   # On-chain USDC (T1 admin simulation)
│       ├── withdraw.py         # Off-ramp helpers (fiat amount, finalize withdrawal)
│       └── tests/              # 146 tests (see Tests section)
├── docker-compose.yml          # Postgres + Redis (optional locally; see Database)
├── requirements.txt            # Dependencies (root; used by CI and Railway)
├── runtime.txt                 # Python 3.12
├── railpack.json               # Build and start on Railway
└── Procfile                    # Startup fallback
```

## Requirements

- Python **3.12** (see `runtime.txt`)
- Git

## Local development

```powershell
cd ANCHOR
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt

Copy-Item backend\.env.example backend\.env
# Edit backend\.env: SIGNING_SEED, SERVER_JWT_KEY, etc.

cd backend
python manage.py migrate
python manage.py seed_polaris_t2
python manage.py runserver 8000
```

For SEP-24 local E2E, run **five processes** (Redis, Anchor `:8000`, `process_pending_deposits`, VERSO Core `:8001`, webview script). See **Stellar wallets** and **Local E2E** above.

### VERSO Core internal API (anchor ↔ BASE_DE_CLIENTES)

All calls use `Authorization: Bearer {VERSO_CORE_API_KEY}` (same secret as `ANCHOR_SHARED_SECRET` in Core).

| Method | Path                                | Purpose                               |
| ------ | ----------------------------------- | ------------------------------------- |
| GET    | `/internal/users/status/?email=`    | Lookup user by email                  |
| POST   | `/internal/users/login/`            | Authenticate for SEP-24 webview       |
| POST   | `/internal/users/register/`         | Create account from webview           |
| POST   | `/internal/users/verify-email/`     | Confirm email code                    |
| POST   | `/internal/users/link-stellar-key/` | Bind `G...` to VERSO user after login |
| GET    | `/internal/rates/pen-usdc/`         | Live TC for SEP-38 / SEP-24           |

Implemented in VERSO repo `usuarios/internal_views.py`; consumed by anchor `verso_integrations/core_client.py`.

## Verifying SEP-1 and SEP-10

### Production

| Check                   | URL                                                        |
| ----------------------- | ---------------------------------------------------------- |
| Health / landing        | https://anchor.versotek.io/ (HTML; JSON at `?format=json`) |
| stellar.toml (SEP-1)    | https://anchor.versotek.io/.well-known/stellar.toml        |
| SEP-10 auth (challenge) | https://anchor.versotek.io/auth?account=G...               |
| Admin                   | https://anchor.versotek.io/admin                           |

### Local

| Check                   | URL                                                   |
| ----------------------- | ----------------------------------------------------- |
| Health / landing        | http://localhost:8000/ (HTML; JSON at `?format=json`) |
| stellar.toml (SEP-1)    | http://localhost:8000/.well-known/stellar.toml        |
| SEP-10 auth (challenge) | http://localhost:8000/auth?account=G...               |
| Admin                   | http://localhost:8000/admin                           |
| SEP-38 quotes           | http://localhost:8000/sep38 (JWT required)            |
| SEP-24 info             | http://localhost:8000/sep24/info                      |

**SEP-10:** a **GET** with `?account=G...` returns the challenge. The **POST** requires JSON `{"transaction": "<signed XDR>"}`; the Django REST Framework browsable UI is not a substitute for a wallet.

External validation:

- Production TOML: [anchor.versotek.io/.well-known/stellar.toml](https://anchor.versotek.io/.well-known/stellar.toml)
- [Stellar Laboratory](https://laboratory.stellar.org/#account-creator?network=test)

## Tests

```powershell
cd backend
python manage.py test verso_integrations reconciliation
```

**181 tests** across 26 files (146 in `verso_integrations`, 35 in `reconciliation`). Main files:

| File                          | Covers                                                                      |
| ----------------------------- | --------------------------------------------------------------------------- |
| `test_sep1.py`                | `stellar.toml` content, testnet/mainnet issuers, no SEP-6 `TRANSFER_SERVER` |
| `test_sep10.py`               | 400 errors on POST `/auth` with invalid XDR                                 |
| `test_deposit_flow.py`        | `FiatDeposit` model, CCI, USDC computation                                  |
| `test_root.py`                | Landing `/` (HTML and `?format=json`)                                       |
| `test_deposit_concurrency.py` | Double disburse does not pay twice (Postgres)                               |
| `test_stellar_payout.py`      | `disburse_usdc` errors (seed, network, amount)                              |
| `test_rates.py`               | VERSO Core `rate_venta` / `rate_compra` parsing                             |
| `test_sep38.py`               | SEP-38 pricing, quotes, TOML quote server                                   |
| `test_sep24.py`               | SEP-24 form, integration, rails poll                                        |
| `test_core_client.py`         | Core login, register, verify, link key                                      |
| `test_kyc_gate.py`            | Onboarding steps (login → register → deposit)                               |
| `test_kyc_views.py`           | DIDIT redirect, onboarding mode switch                                      |
| `test_sep24_withdraw.py`      | SEP-24 off-ramp form, integration, finalize                                 |
| `test_transaction_poll.py`    | Webview status polling                                                      |
| `reconciliation/tests/`       | Event parsing, sync, matching, reconcile, alerts, report                    |

On every **push** and **pull request**, GitHub Actions runs the same tests against **PostgreSQL + Redis** (`.github/workflows/backend-tests.yml`), including the `reconciliation` app (D3).

## Production workers (Railway)

Production on `anchor.versotek.io` is **not** a single process. The web service serves HTTP (SEP-1/10/24/38, admin, webview); **long-running Polaris and reconciliation loops** run in separate Railway services that share the same repo, branch, Postgres (`DATABASE_URL`), Redis (`REDIS_URL`), and environment variables.

```
                    ┌─────────────────┐
                    │    VERSO-SDF    │  HTTPS — gunicorn (railpack.json)
                    │ anchor.versotek │
                    └────────┬────────┘
                             │
         ┌───────────────────┼───────────────────┐
         ▼                   ▼                   ▼
  ┌─────────────┐    ┌─────────────┐    ┌──────────────────┐
  │  DEPOSITO   │    │   RETIRO    │    │ RECONCILIATION   │
  │ on-ramp USDC│    │ off-ramp    │    │ D3 ledger sync   │
  └──────┬──────┘    └──────┬──────┘    └────────┬─────────┘
         │                  │                     │
         └──────────────────┴─────────────────────┘
                            │
                    ┌───────┴───────┐
                    │ Postgres Redis│
                    └───────────────┘
```

| Railway service    | Role                                                                    | Start command (override in Railway **Settings → Deploy**)                                                                                                            | When it runs                                                                                                   |
| ------------------ | ----------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------- |
| **VERSO-SDF**      | Web + admin + SEP APIs                                                  | `cd backend && python manage.py migrate --noinput && gunicorn config.wsgi:application --bind 0.0.0.0:$PORT --workers 2 --timeout 120` (default from `railpack.json`) | Always; only service with a public URL                                                                         |
| **DEPOSITO**       | SEP-24 **on-ramp**: disburse USDC after fiat confirmed                  | `cd backend && python manage.py process_pending_deposits --loop`                                                                                                     | After ops mark PEN received (admin mock today; bank webhook TBD)                                               |
| **RETIRO**         | SEP-24 **off-ramp**: detect inbound USDC, execute outbound, poll status | `cd backend && python manage.py watch_transactions` (Horizon stream; no `--loop` flag)                                                                               | Always — Restart Policy **Always** (the stream can end with exit code 0, which "On Failure" would not restart) |
| **RECONCILIATION** | **D3**: RPC `getEvents` ingest + balance reconcile                      | `cd backend && python manage.py reconciliation_worker --loop`                                                                                                        | Always (after one-time bootstrap)                                                                              |

**RETIRO and Polaris:** some deployments also run `execute_outgoing_transactions --loop` and `poll_outgoing_transactions --loop` (same repo, same env). If off-ramp stalls after USDC is received, confirm those commands are running—either as additional Railway services or combined in the RETIRO start command.

**Shared configuration:** attach the same **Shared Variables** (or copy from VERSO-SDF) to every worker: at minimum `DATABASE_URL`, `DJANGO_SECRET_KEY`, `SIGNING_SEED`, `STELLAR_NETWORK_PASSPHRASE`, `HOST_URL`, `REDIS_URL`, `VERSO_CORE_API_*`, and for D3 `STELLAR_RPC_URL`, `RECON_*`, optional AWS vars. Workers do **not** need a public port.

**Migrations:** only **VERSO-SDF** runs `migrate` on deploy (`railpack.json`). Worker services must not run `migrate` on every start; they assume the web deploy has already applied migrations.

### Production configuration checklist (verified on Railway, 2026-09-29)

**Restart policy — `Always` on every worker.** Railway service → **Settings → Deploy → Restart Policy**.

| Service            | Restart Policy                 | Why                                                                                                                                                                                                                                                                          |
| ------------------ | ------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **DEPOSITO**       | **Always**                     | `--loop` worker that must run permanently                                                                                                                                                                                                                                    |
| **RETIRO**         | **Always**                     | `watch_transactions` keeps a Horizon stream open; when Horizon closes the stream the command ends with **exit code 0**, which _On Failure_ treats as a normal exit and does not restart. _On Failure_ also stops after its retry limit (default 10) during a Horizon outage. |
| **RECONCILIATION** | **Always**                     | D3 evidence depends on uninterrupted runs; the CloudWatch heartbeat alarm fires if it stops                                                                                                                                                                                  |
| **VERSO-SDF**      | On Failure (default) is enough | gunicorn does not exit on its own                                                                                                                                                                                                                                            |

Restarting is safe: `watch_transactions` resumes from the last stored `paging_token`, `process_pending_deposits` tracks each payout's `submission_status` and uses a process lock (`PROCESS_PENDING_DEPOSITS_LOCK`), and Polaris recovers in-flight submissions after a restart, and the reconciliation worker resumes from its persisted RPC cursor (`SyncState`).

**No mocks in production.** Test shortcuts must be off so testnet transactions exercise the real flow (manual fiat confirmation, real KYC from VERSO Core, on-chain USDC):

| Variable                                    | Production value            | Effect                                                                  |
| ------------------------------------------- | --------------------------- | ----------------------------------------------------------------------- |
| `LOCAL_MODE`                                | `0`                         | Also controls the default of `VERSO_MOCK_KYC_AUTO_APPROVE_AFTER_VERIFY` |
| `VERSO_MOCK_KYC`                            | `""` (empty)                | KYC status is looked up in VERSO Core                                   |
| `VERSO_MOCK_KYC_AUTO_APPROVE_AFTER_VERIFY`  | `0`                         | DIDIT KYC is required after email verification                          |
| `VERSO_MOCK_AUTO_CONFIRM_FIAT`              | `0`                         | PEN receipt is confirmed by operations in the admin                     |
| `VERSO_MOCK_AUTO_SEND_FIAT`                 | unset (defaults to `False`) | PEN payout is confirmed by operations in the admin                      |
| `VERSO_MOCK_COMPLETE_WITHDRAW_WITHOUT_USDC` | unset (defaults to `False`) | A withdrawal cannot complete without on-chain USDC                      |

**Network.** `HORIZON_URI=https://horizon-testnet.stellar.org` is set explicitly on Railway (used by Polaris workers and `stellar_payout.py`); see **Mainnet switch (T3)** for the values that change at cutover.

**One-off commands (bootstrap, CloudWatch, reports):** run inside the **Railway dashboard Shell** on VERSO-SDF (or any service in the same project)—not `railway run` from a Windows PC, because `DATABASE_URL` uses `postgres.railway.internal`, which is unreachable outside Railway's network. Alternative: set `DATABASE_URL` to Postgres **public** URL locally for a single command (see **Database → Railway** below).

### Local development (same commands, more terminals)

| #   | Process       | Command                                                               |
| --- | ------------- | --------------------------------------------------------------------- |
| 1   | Redis         | `docker compose up -d`                                                |
| 2   | Web           | `python manage.py runserver 8000`                                     |
| 3   | Deposits      | `python manage.py process_pending_deposits --loop`                    |
| 4   | Withdrawals   | `python manage.py watch_transactions` (+ optional execute/poll loops) |
| 5   | D3 (optional) | `reconciliation_bootstrap` once, then `reconciliation_worker --loop`  |
| 6   | VERSO Core    | `:8001` when testing onboarding                                       |

See **Local E2E** above for the full five-process SEP-24 walkthrough.

## Database

| Environment              | Engine           | Configuration                                                                           |
| ------------------------ | ---------------- | --------------------------------------------------------------------------------------- |
| **Local**                | SQLite (default) | Leave `DATABASE_URL` undefined in `backend/.env` → `backend/db.sqlite3`                 |
| **Local with Postgres**  | PostgreSQL       | `docker compose up -d` + `DATABASE_URL` in `.env` (see below)                           |
| **Production (Railway)** | PostgreSQL       | Railway Postgres service + `DATABASE_URL=${{Postgres.DATABASE_URL}}` on the web service |

On **Railway** the data persists (admin, `FiatDeposit`, Polaris tables). The deploy's `migrate` step (`railpack.json`) applies migrations against Postgres.

### Local with SQLite (default)

```powershell
cd backend
python manage.py migrate
```

### Local with Postgres (optional, `docker-compose.yml`)

```powershell
docker compose up -d
```

In `backend/.env`:

```
DATABASE_URL=""
```

### Railway — PostgreSQL

1. Add a **PostgreSQL** service to the project (CLI: `railway add -d postgres`, or the dashboard).
2. On the **web** service, set the variable:

```
DATABASE_URL=${{Postgres.DATABASE_URL}}
```

(`Postgres` = the name of the database service in your project.)

3. Redeploy → `migrate` creates/updates the tables in Postgres.

**Creating a superuser in production:** from your machine, with the venv activated and the **public URL** of Postgres (not `postgres.railway.internal`):

```powershell
.\venv\Scripts\Activate.ps1
cd backend
$env:DATABASE_URL = "<Railway DATABASE_PUBLIC_URL>"
$env:DJANGO_SECRET_KEY = "temporary"
python manage.py createsuperuser
```

`railway run` injects the **internal** URL; it only works inside the Railway network, not from Windows.

## Production and deployment

- **Domain:** `anchor.versotek.io`
- **Platform:** Railway connected to this repo; **pushing to `main`** triggers an automatic deploy.
- **Build:** `railpack.json` → `pip install`, `collectstatic`, `migrate`, `gunicorn`.
- **Database:** PostgreSQL on Railway (`DATABASE_URL` referenced from the Postgres service).
- **Variables:** set them in the Railway dashboard (never in Git). Reference in `backend/.env.example`.

Key production values (Railway dashboard; Polaris reads them as environment variables):

```
DJANGO_SECRET_KEY=<secret>
DEBUG=False
ALLOWED_HOSTS=anchor.versotek.io,.up.railway.app
CSRF_TRUSTED_ORIGINS=https://anchor.versotek.io
DATABASE_URL=${{Postgres.DATABASE_URL}}

# T2 on anchor.versotek.io (SEP-24 + SEP-38 + workers):
ACTIVE_SEPS=sep-1,sep-10,sep-38,sep-24
REDIS_URL=${{Redis.REDIS_URL}}
VERSO_CORE_API_URL=https://...
VERSO_CORE_API_KEY=<secret>
VERSO_CCI_BANK_NAME=BCP
VERSO_CCI_ACCOUNT_NUMBER=<cci>
# Optional — SEP-38 firm quote window; code default is 180 (3 min). Omit unless overriding.
# VERSO_QUOTE_TTL_SECONDS=180

# Workers: separate Railway services DEPOSITO, RETIRO, RECONCILIATION
# (see "Production workers (Railway)" — not part of the web start command)

# D3 reconciliation (RECONCILIATION service + one-time bootstrap):
# STELLAR_RPC_URL=https://soroban-testnet.stellar.org
# Set on the RECONCILIATION service only (not Shared Variables) — see Tranche 2 → Deliverable 3 → "CloudWatch alerts — setup":
# RECON_ALERT_BACKEND=cloudwatch
# RECON_ENV=testnet
# RECON_SNS_TOPIC_ARN=arn:aws:sns:us-east-1:<account>:verso-anchor-recon-testnet
# AWS_DEFAULT_REGION=us-east-1
# AWS_REGION=us-east-1
# AWS_ACCESS_KEY_ID=<IAM user verso-anchor-recon>
# AWS_SECRET_ACCESS_KEY=<IAM user verso-anchor-recon>

HOST_URL=https://anchor.versotek.io
LOCAL_MODE=0
ENABLE_SEP_0023=1
SIGNING_SEED=<testnet-seed>
SERVER_JWT_KEY=<jwt-secret>
SEP10_HOME_DOMAINS=versotek.io,anchor.versotek.io
STELLAR_NETWORK_PASSPHRASE=Test SDF Network ; September 2015
```

The Stellar account behind `SIGNING_SEED` must have **home domain** `anchor.versotek.io` on testnet.

## Mainnet switch (T3) — environment checklist

Moving from testnet to mainnet is **not** a single variable. django-polaris reads `STELLAR_NETWORK_PASSPHRASE` and **`HORIZON_URI` independently**. If you set mainnet passphrase but omit `HORIZON_URI`, Polaris keeps using **`https://horizon-testnet.stellar.org`** (its default in `polaris/settings.py`) while transactions would be signed for mainnet—a dangerous mismatch.

`HORIZON_URI` is **not** defined in `backend/config/settings.py`; Polaris loads it from the environment via `env_or_settings("HORIZON_URI")`. It must appear in Railway Shared Variables and in `.env.example` documentation.

| Variable                                            | Testnet (today)                             | Mainnet (T3)                                          |
| --------------------------------------------------- | ------------------------------------------- | ----------------------------------------------------- |
| `STELLAR_NETWORK_PASSPHRASE`                        | `Test SDF Network ; September 2015`         | `Public Global Stellar Network ; September 2015`      |
| **`HORIZON_URI`**                                   | `https://horizon-testnet.stellar.org`       | **`https://horizon.stellar.org`**                     |
| `STELLAR_RPC_URL` (D3 reconciliation)               | `https://soroban-testnet.stellar.org`       | Mainnet Soroban RPC URL (**required**; no default)    |
| `SIGNING_SEED`                                      | Testnet distribution key                    | **Mainnet** key (new seed; KMS/HSM before real funds) |
| `RECON_ENV`                                         | `testnet`                                   | `mainnet` (CloudWatch dimension)                      |
| `HOST_URL` / `ALLOWED_HOSTS` / `SEP10_HOME_DOMAINS` | Production testnet domain                   | Production mainnet domain                             |
| Polaris assets                                      | `seed_polaris_t2` (testnet USDC issuer)     | Re-seed or migrate assets for mainnet issuer          |
| `verso_integrations/stellar_payout.py`              | Reads Polaris `HORIZON_URI` (single source) | Nothing extra — follows `HORIZON_URI` automatically   |

Apply the same values on **VERSO-SDF**, **DEPOSITO**, **RETIRO**, and **RECONCILIATION** (all workers use Polaris and/or RPC). After mainnet cutover, run `reconciliation_bootstrap --force` on a fresh observation window (D3).

## Git and branching

```powershell
git checkout main
git pull origin main
```

Recommended flow: feature branch → **pull request** → merge to `main` → deploy on Railway.

## Polaris documentation

- https://django-polaris.readthedocs.io/en/stable/

## License

Proprietary — VERSO / versotek.io

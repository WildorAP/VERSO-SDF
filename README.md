# VERSO Stellar Anchor

VERSO's Stellar anchor (PSAV Peru) implemented with **Django + Polaris**.

Repository kept separate from the VERSO core (`BASE_DE_CLIENTES`, [versotek.io](https://versotek.io)).

**Production (testnet):** https://anchor.versotek.io

## Roadmap

| Tranche | SEPs           | Status                                                                                  |
| ------- | -------------- | --------------------------------------------------------------------------------------- |
| **T1**  | SEP-1, SEP-10  | Complete on testnet (`anchor.versotek.io`) — 3 deliverables verified, see details below |
| **T2**  | SEP-24, SEP-38 | **Etapa 3 MVP complete** (on-ramp webview + SEP-38). **Etapa 4 in progress** (VERSO login/register in webview, KYC gate). Production deploy + bank webhook pending |
| **T3**  | Mainnet        | Pending                                                                                 |

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

The original proposal describes using the **SDF Anchor Platform** (Java/Kotlin service distributed as a Docker image) with **AWS KMS** for transaction signing. The current implementation differs in three respects, documented below for SDF's awareness.

**1. Anchor Platform → django-polaris.** We use [django-polaris](https://django-polaris.readthedocs.io/en/stable/), the Python reference implementation officially maintained by SDF, integrated directly into VERSO's Django backend, instead of the Anchor Platform service deployed as a standalone container. Both alternatives are official SDF solutions and implement the same SEPs with the same level of conformance. This choice avoids operating two services on different runtimes (Python and JVM) and consolidates the deployment into a single process. As a consequence, the repository does not include an application `Dockerfile` or an "Anchor Platform" container; `docker-compose.yml` in this repo is for **local development** (Postgres + Redis). In **production (Railway)** the database is **PostgreSQL** managed by Railway, linked to the web service via `DATABASE_URL`.

**2. AWS KMS: not implemented.** Stellar transaction signing uses the Ed25519 scheme, an algorithm not supported by the AWS KMS `Sign` API (limited to RSA and ECDSA over NIST curves). Additionally, django-polaris does not expose an extension point to delegate signing to an external service: the signing seed is loaded into memory at process startup and used directly through `stellar_sdk`.

Consequently, `SIGNING_SEED` is currently managed as an environment variable on Railway, without an additional custody layer such as KMS or an HSM. This is a deliberate and temporary decision for this delivery: the environment is testnet, with no real funds at risk, and Railway encrypts environment variables at rest. Before operating on mainnet, this secret management will be migrated to a more robust custody scheme (for example AWS Secrets Manager with IAM-restricted access and CloudTrail auditing, and/or a custody provider with native Ed25519 support such as Turnkey or Fireblocks).

**3. KYC verification: not implemented in T1, deferred to T2 (not yet relocated in code).** The original internal proposal called for verifying the client's KYC status and issuing the JWT conditionally inside the SEP-10 authentication endpoint itself. This approach is corrected because it is inconsistent with the protocol's separation of concerns: SEP-10 exclusively certifies ownership of the Stellar account (signature verification) and must not depend on, nor expose, the client's compliance status. The SEP-10 endpoint in T1 issues the JWT solely on the basis of cryptographic signature verification, per the standard.

The correct place in the protocol for the KYC check and the DIDIT onboarding redirect is the SEP-24 interactive webview (Tranche 2), not T1's simulated deposit flow without a webview. **Etapa 3** shipped the SEP-24 webview and on-ramp without KYC; **`kyc_bridge.py` remains unwired** and is scheduled for **Etapa 4**.

## Tranche 2 — Etapa 3 complete (SEP-24 MVP + SEP-38)

**Scope delivered in code (testnet/local):**

| Component | Status |
| --------- | ------ |
| SEP-38 quotes (`rate_venta` / `rate_compra` from VERSO Core) | Done |
| SEP-24 webview on-ramp **PEN → USDC** | Done |
| `Sep24DepositMeta` + admin **Mark PEN received** (mock fiat) | Done |
| `process_pending_deposits` → USDC on-chain (Polaris) | Done (manual worker locally) |
| CI: Postgres + Redis, 88 tests | Done |

**Etapa 4 (in progress):** VERSO login/register inside the SEP-24 webview, email verification, DIDIT redirect, session KYC gate. **Still mock/TBD:** bank webhook for real PEN confirmation, Railway worker for `process_pending_deposits`, USD on-ramp webview, off-ramp.

## Stellar wallets (Lobstr, Freighter, Demo Wallet, …)

This anchor is designed for the **standard Stellar on-ramp flow**, not as a standalone website. Compatible wallets (Lobstr, Freighter, xBull, Solar, [Stellar Demo Wallet](https://demo-wallet.stellar.org), etc.) integrate via **SEP-10** and **SEP-24** when the anchor is listed in `stellar.toml` with a **public** `HOST_URL`.

### What the wallet does

1. **SEP-10** — User signs a challenge with their Stellar account (`G...`); anchor returns a JWT.
2. **SEP-24 interactive deposit** — Wallet calls `POST /sep24/transactions/deposit/interactive` with the JWT and opens the returned **`url`** in an **in-app webview**.
3. **Completion** — Wallet polls or refreshes transaction status; when the anchor marks the deposit complete, USDC appears in the user's Stellar account.

The wallet does **not** implement VERSO login, KYC, or bank transfer UI — it only embeds the anchor's webview.

### What runs inside the webview (anchor-hosted)

| Step | Screen | Backend |
| ---- | ------ | ------- |
| 1 | **Iniciar sesión** VERSO (email + password) | `POST /internal/users/login/` on VERSO Core |
| 2 | *Optional* **Crear cuenta** (link “¿Aún no tienes cuenta?”) | `POST /internal/users/register/` |
| 3 | Verificar email (código) | `POST /internal/users/verify-email/` |
| 4 | KYC / DIDIT (redirect) | VERSO web + callback to anchor |
| 5 | Monto PEN → USDC | `GET /internal/rates/pen-usdc/` |
| 6 | Instrucciones CCI/CCE | Anchor only |
| 7 | Estado `completed` + USDC on-chain | `process_pending_deposits` worker |

The Stellar account used in SEP-10 is the account that receives USDC. After login, Core links that wallet via `POST /internal/users/link-stellar-key/`.

### What wallets never call directly

| Component | Visible to wallet? |
| --------- | ------------------ |
| VERSO Core (`VERSO_CORE_API_URL`) | No — anchor backend only |
| Django Admin “Mark PEN received” | No — local mock; production uses bank webhook |
| `process_pending_deposits` | No — anchor worker |
| SEP-38 `/sep38/quote` | Optional — wallets may quote before deposit; SEP-24 does not consume `quote_id` |

### Local vs production

| Environment | Wallet can connect? |
| ----------- | ------------------- |
| `localhost:8000` | **No** — mobile/desktop wallets cannot reach your machine. Use the Python script below (simulates wallet SEP-10 + interactive). |
| `anchor.versotek.io` (testnet) | **Yes** — if the wallet supports SEP-24 USDC deposits and discovers the anchor via `stellar.toml`. |

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

| # | Service | Command | Folder |
| - | ------- | ------- | ------ |
| 1 | Redis | `docker compose up -d` | `ANCHOR/` |
| 2 | Anchor API | `python manage.py runserver 8000` | `backend/` |
| 3 | Deposit worker | `python manage.py process_pending_deposits` | `backend/` |
| 4 | VERSO Core | `python manage.py runserver 8001` | `DJANGO_RAIL/` |
| 5 | Webview entry | SEP-10 + interactive script (below) | `backend/` |

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

| Variable | Effect |
| -------- | ------ |
| `VERSO_MOCK_KYC=approved` | Skip KYC gate; go straight to PEN amount (restart runserver) |
| `VERSO_MOCK_KYC_AUTO_APPROVE_AFTER_VERIFY=1` | Auto-approve KYC after email verify |
| `VERSO_MOCK_AUTO_CONFIRM_FIAT=1` | Skip admin “Mark PEN received” (never use in production) |

**Email verification in local Core:** SMTP may fail; read the code from VERSO shell:

```powershell
cd "path\to\DJANGO_RAIL"
python manage.py shell -c "from django.contrib.auth.models import User; u=User.objects.get(email__iexact='you@example.com'); print(u.perfil.codigo_verificacion_email)"
```

**Mock rates only** (without VERSO Core): run a stub on `:9000` returning `{"rate_venta":"3.5000","rate_compra":"3.4000"}` and set `VERSO_CORE_API_URL=http://localhost:9000`. For full Etapa 4 onboarding, use real Core on `:8001`.

## Repository structure

The Git repository lives at the **`ANCHOR/` root**. Django sits under `backend/`; Railway and the build use the root (`railpack.json` runs `cd backend`).

```
ANCHOR/
├── .github/workflows/
│   └── backend-tests.yml       # CI: Postgres + Redis; all SEPs; 88 tests
├── backend/
│   ├── manage.py
│   ├── config/                 # settings, urls, wsgi
│   ├── templates/
│   │   └── verso_integrations/
│   │       └── root.html       # HTML landing page at /
│   ├── .env.example            # Environment variable template
│   └── verso_integrations/
│       ├── apps.py             # Polaris registration (toml, quote, deposit, rails)
│       ├── admin.py            # FiatDeposit + Sep24DepositMeta admin
│       ├── models.py           # FiatDeposit, Sep24DepositMeta
│       ├── migrations/         # 0001–0005
│       ├── root.py             # Landing / health check at /
│       ├── sep1.py             # Dynamic stellar.toml content
│       ├── sep10.py            # SEP-10 (400 errors on invalid XDR)
│       ├── sep38.py            # SEP-38 quote integration
│       ├── sep24/              # SEP-24 webview (PEN on-ramp + Etapa 4 onboarding)
│       │   ├── integration.py  # DepositIntegration, login/register gate
│       │   ├── kyc_gate.py     # Session onboarding steps
│       │   ├── kyc_views.py    # DIDIT redirect + login/register switch
│       │   └── onboarding_forms.py
│       ├── core_client.py      # VERSO Core HTTP (login, register, verify, link key)
│       ├── rails.py            # RailsIntegration (mock PEN confirmation)
│       ├── rates.py            # VERSO Core rate_venta / rate_compra
│       ├── deposit.py          # CCI helpers + USDC amount computation
│       ├── polaris_setup.py    # seed_polaris_t2 data
│       ├── kyc_bridge.py       # Core KYC client (Etapa 4)
│       ├── stellar_payout.py   # On-chain USDC (T1 admin simulation)
│       ├── withdraw.py         # Off-ramp stub (Etapa 5)
│       └── tests/              # 88 tests (see Tests section)
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

For SEP-24 local E2E, run **five processes** (Redis, Anchor `:8000`, `process_pending_deposits`, VERSO Core `:8001`, webview script). See **Stellar wallets** and **Local E2E** under Tranche 2 above.

### VERSO Core internal API (anchor ↔ BASE_DE_CLIENTES)

All calls use `Authorization: Bearer {VERSO_CORE_API_KEY}` (same secret as `ANCHOR_SHARED_SECRET` in Core).

| Method | Path | Purpose |
| ------ | ---- | ------- |
| GET | `/internal/users/status/?email=` | Lookup user by email |
| POST | `/internal/users/login/` | Authenticate for SEP-24 webview |
| POST | `/internal/users/register/` | Create account from webview |
| POST | `/internal/users/verify-email/` | Confirm email code |
| POST | `/internal/users/link-stellar-key/` | Bind `G...` to VERSO user after login |
| GET | `/internal/rates/pen-usdc/` | Live TC for SEP-38 / SEP-24 |

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
python manage.py test verso_integrations
```

**61 tests** across 9 files → **88 tests** after Etapa 4 onboarding (login, KYC gate, Core client):

| File                          | Covers                                          |
| ----------------------------- | ----------------------------------------------- |
| `test_sep1.py`                | `stellar.toml` content, testnet/mainnet issuers |
| `test_sep10.py`               | 400 errors on POST `/auth` with invalid XDR     |
| `test_deposit_flow.py`        | `FiatDeposit` model, CCI, USDC computation      |
| `test_root.py`                | Landing `/` (HTML and `?format=json`)           |
| `test_deposit_concurrency.py` | Double disburse does not pay twice (Postgres)    |
| `test_stellar_payout.py`      | `disburse_usdc` errors (seed, network, amount)  |
| `test_rates.py`               | VERSO Core `rate_venta` / `rate_compra` parsing |
| `test_sep38.py`               | SEP-38 pricing, quotes, TOML quote server       |
| `test_sep24.py`               | SEP-24 form, integration, rails poll            |
| `test_core_client.py`         | Core login, register, verify, link key          |
| `test_kyc_gate.py`            | Onboarding steps (login → register → deposit)   |
| `test_kyc_views.py`           | DIDIT redirect, onboarding mode switch          |

On every **push** and **pull request**, GitHub Actions runs the same tests against **PostgreSQL + Redis** (`.github/workflows/backend-tests.yml`).

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

# T1 live today on anchor.versotek.io:
ACTIVE_SEPS=sep-1,sep-10

# T2 staging (Etapa 3+ — enable when deploying SEP-38/SEP-24):
# ACTIVE_SEPS=sep-1,sep-10,sep-38,sep-24
# REDIS_URL=${{Redis.REDIS_URL}}
# VERSO_CORE_API_URL=https://...
# VERSO_CORE_API_KEY=<secret>
# VERSO_CCI_BANK_NAME=BCP
# VERSO_CCI_ACCOUNT_NUMBER=<cci>
# Plus a Railway worker/cron: python manage.py process_pending_deposits

HOST_URL=https://anchor.versotek.io
LOCAL_MODE=0
ENABLE_SEP_0023=1
SIGNING_SEED=<testnet-seed>
SERVER_JWT_KEY=<jwt-secret>
SEP10_HOME_DOMAINS=versotek.io,anchor.versotek.io
STELLAR_NETWORK_PASSPHRASE=Test SDF Network ; September 2015
```

The Stellar account behind `SIGNING_SEED` must have **home domain** `anchor.versotek.io` on testnet.

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

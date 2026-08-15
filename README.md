# VERSO Stellar Anchor

VERSO's Stellar anchor (PSAV Peru) implemented with **Django + Polaris**.

Repository kept separate from the VERSO core (`BASE_DE_CLIENTES`, [versotek.io](https://versotek.io)).

**Production (testnet):** https://anchor.versotek.io

## Roadmap

| Tranche | SEPs           | Status                                                                                  |
| ------- | -------------- | --------------------------------------------------------------------------------------- |
| **T1**  | SEP-1, SEP-10  | Complete on testnet (`anchor.versotek.io`) — 3 deliverables verified, see details below |
| **T2**  | SEP-24, SEP-38 | Pending                                                                                 |
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

**Covered in its authentication component.** The SEP-10 authentication flow was verified end-to-end against the live testnet deployment using the Stellar CLI, following the procedure officially documented by Stellar ([developers.stellar.org — Testing Your Configuration](https://developers.stellar.org/docs/tools/cli)):

```powershell
# 1. Request the challenge
$CHALLENGE_RESPONSE = curl.exe -s "https://anchor.versotek.io/auth?account=$ACCOUNT_ID"
$CHALLENGE_XDR = $CHALLENGE_RESPONSE | jq -r '.transaction'

# 2. Sign the challenge with the client wallet (testnet)
$SIGNED_CHALLENGE_XDR = ($CHALLENGE_XDR | stellar tx sign --sign-with-key $SECRET_SEED --network testnet 2>&1) | Select-Object -Last 1

# 3. Submit the signature and receive the JWT
$body = @{ transaction = $SIGNED_CHALLENGE_XDR } | ConvertTo-Json -Compress
Set-Content -Path "$env:TEMP\sep10_body.json" -Value $body -Encoding ascii -NoNewline
curl.exe -X POST "https://anchor.versotek.io/auth" -H "Content-Type: application/json" -d "@$env:TEMP\sep10_body.json"
```

Result obtained — a valid JWT, issued by the VERSO anchor for the account that signed the challenge:

```json
{
  "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJodHRwczovL2FuY2hvci52ZXJzb3Rlay5pby9hdXRoIiwic3ViIjoiR0NYR0xXTDdHRVBVRENDWkFCUVZMSFRaTERXV1hQVFVSR1hPREo2SkY2QlZKU080S1dVNDVJRkciLCJpYXQiOjE3ODY2Njc0NTMsImV4cCI6MTc4Njc1Mzg1MywianRpIjoiMDYxZTUxNDYzNjZiMjRmYjM1OTMxNmZkYmNmNThmMGRiMTFkNjJhNjFlNGNlYzBjMDI3ZjY0Y2ZmNDgxODViMiIsImNsaWVudF9kb21haW4iOm51bGx9..."
}
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

The correct place in the protocol for the KYC check and the DIDIT onboarding redirect is the SEP-24 interactive webview (Tranche 2), not T1's simulated deposit flow without a webview. For that reason we deliberately chose **not** to build a minimal version of that check in T1 (for example, a simple gate in the operator panel) — that version would be discarded as soon as SEP-24 is implemented in T2, where the KYC check and DIDIT onboarding live naturally integrated into the webview. `kyc_bridge.py` exists in the repo as a functional HTTP client against VERSO's compliance system, ready to be wired up in T2, but **it has no caller in T1**: neither SEP-10 nor the simulated deposit flow invokes it. Consequently, the "KYC check passes" measurement criterion from the original text of Deliverables 2 and 3 is explicitly deferred to T2.

## Repository structure

The Git repository lives at the **`ANCHOR/` root**. Django sits under `backend/`; Railway and the build use the root (`railpack.json` runs `cd backend`).

```
ANCHOR/
├── .github/workflows/
│   └── backend-tests.yml       # CI: runs all of verso_integrations (SEP-1, SEP-10, deposit) on push and PR
├── backend/
│   ├── manage.py
│   ├── config/                 # settings, urls, wsgi
│   ├── templates/
│   │   └── verso_integrations/
│   │       └── root.html       # HTML landing page at /
│   ├── .env.example            # Environment variable template
│   └── verso_integrations/
│       ├── apps.py             # Polaris registration (toml)
│       ├── admin.py            # FiatDeposit admin + deposit actions
│       ├── models.py           # FiatDeposit + states
│       ├── migrations/         # 0001–0003 (incl. disbursing state)
│       ├── root.py             # Landing / health check at /
│       ├── sep1.py             # Dynamic stellar.toml content
│       ├── sep10.py            # SEP-10 (400 errors on invalid XDR)
│       ├── toml_view.py        # stellar.toml with UTF-8 charset
│       ├── kyc_bridge.py       # HTTP client to the core (prepared; no SEP-10 hook yet)
│       ├── deposit.py          # CCI + USDC computation (D3); SEP-24 integration in T2
│       ├── stellar_payout.py   # On-chain USDC payout (admin simulation)
│       ├── withdraw.py         # Off-ramp stub (T2)
│       ├── rates.py            # PEN/USDC quotes stub (T2)
│       ├── static/polaris/     # Static TOML files (local / prod)
│       └── tests/              # 6 files, 23 tests (see Tests section)
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
python manage.py runserver 8000
```

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

**SEP-10:** a **GET** with `?account=G...` returns the challenge. The **POST** requires JSON `{"transaction": "<signed XDR>"}`; the Django REST Framework browsable UI is not a substitute for a wallet.

External validation:

- Production TOML: [anchor.versotek.io/.well-known/stellar.toml](https://anchor.versotek.io/.well-known/stellar.toml)
- [Stellar Laboratory](https://laboratory.stellar.org/#account-creator?network=test)

## Tests

```powershell
cd backend
python manage.py test verso_integrations
```

**23 tests** across 6 files:

| File                          | Covers                                          |
| ----------------------------- | ----------------------------------------------- |
| `test_sep1.py`                | `stellar.toml` content, testnet/mainnet issuers |
| `test_sep10.py`               | 400 errors on POST `/auth` with invalid XDR     |
| `test_deposit_flow.py`        | `FiatDeposit` model, CCI, USDC computation      |
| `test_root.py`                | Landing `/` (HTML and `?format=json`)           |
| `test_deposit_concurrency.py` | Double disburse does not pay twice              |
| `test_stellar_payout.py`      | `disburse_usdc` errors (seed, network, amount)  |

On every **push** and **pull request**, GitHub Actions runs the same tests (`.github/workflows/backend-tests.yml`).

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

ACTIVE_SEPS=sep-1,sep-10
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

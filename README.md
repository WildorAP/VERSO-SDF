# VERSO Stellar Anchor

Anchor Stellar de VERSO (PSAV Perú) implementado con **Django + Polaris**.

Repositorio separado del core VERSO (`BASE_DE_CLIENTES`, [versotek.io](https://versotek.io)).

**Producción (testnet):** https://anchor.versotek.io

## Roadmap

| Tranche | SEPs                       | Estado                                                                              |
| ------- | -------------------------- | ----------------------------------------------------------------------------------- |
| **T1**  | SEP-1, SEP-10              | Desplegado en testnet (`anchor.versotek.io`); validación Anchor Directory pendiente |
| **T2**  | SEP-24, SEP-38             | Pendiente                                                                           |
| **T3**  | Mainnet + Anchor Directory | Pendiente                                                                           |

## Estructura del repo

El repositorio Git vive en la **raíz `ANCHOR/`**. Django está en `backend/`; Railway y el build usan la raíz (`railpack.json` hace `cd backend`).

```
ANCHOR/
├── .github/workflows/
│   └── backend-tests.yml       # CI: tests SEP-1 / SEP-10 en push y PR
├── backend/
│   ├── manage.py
│   ├── config/                 # settings, urls, wsgi
│   ├── .env.example            # Plantilla de variables (copiar a .env)
│   └── verso_integrations/
│       ├── sep1.py             # Contenido dinámico de stellar.toml
│       ├── sep10.py            # SEP-10 (errores 400 en XDR inválido)
│       ├── toml_view.py        # stellar.toml con charset UTF-8
│       ├── kyc_bridge.py       # Lookup KYC por pubkey (core VERSO)
│       ├── deposit.py          # On-ramp (T2)
│       ├── withdraw.py         # Off-ramp (T2)
│       ├── rates.py            # Cotizaciones PEN/USDC (T2)
│       ├── static/polaris/     # TOML estáticos (local / prod)
│       └── tests/              # test_sep1.py, test_sep10.py
├── docker-compose.yml          # Postgres + Redis (reservado para T2; no requerido en T1)
├── requirements.txt            # Dependencias (raíz; usado por CI y Railway)
├── runtime.txt                 # Python 3.12
├── railpack.json               # Build y start en Railway
└── Procfile                    # Fallback de arranque
```

No commitear `backend/.env` ni `venv/`.

## Requisitos

- Python **3.12** (ver `runtime.txt`)
- Git

## Desarrollo local

```powershell
cd ANCHOR
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt

Copy-Item backend\.env.example backend\.env
# Editar backend\.env: SIGNING_SEED, SERVER_JWT_KEY, etc.

cd backend
python manage.py migrate
python manage.py runserver 8000
```

Generar claves testnet y JWT:

```powershell
python -c "from stellar_sdk import Keypair; import secrets; print('SIGNING_SEED=' + Keypair.random().secret); print('SERVER_JWT_KEY=' + secrets.token_urlsafe(32))"
```

> Si el puerto **8000** está ocupado por otro proyecto Django, usa otro puerto (`runserver 8001`) y actualiza `HOST_URL` en `backend/.env`.

## Verificar SEP-1 y SEP-10

### Producción

| Check                   | URL                                                 |
| ----------------------- | --------------------------------------------------- |
| stellar.toml (SEP-1)    | https://anchor.versotek.io/.well-known/stellar.toml |
| SEP-10 auth (challenge) | https://anchor.versotek.io/auth?account=G...        |
| Admin                   | https://anchor.versotek.io/admin                    |

### Local

| Check                   | URL                                            |
| ----------------------- | ---------------------------------------------- |
| stellar.toml (SEP-1)    | http://localhost:8000/.well-known/stellar.toml |
| SEP-10 auth (challenge) | http://localhost:8000/auth?account=G...        |
| Admin                   | http://localhost:8000/admin                    |

**SEP-10:** el **GET** con `?account=G...` devuelve el challenge. El **POST** exige JSON `{"transaction": "<XDR firmado>"}`; la UI de Django REST Framework en el navegador no sustituye una wallet.

Validación externa:

- TOML en producción: [anchor.versotek.io/.well-known/stellar.toml](https://anchor.versotek.io/.well-known/stellar.toml)
- [Stellar Laboratory](https://laboratory.stellar.org/#account-creator?network=test)
- [Anchor Validator](https://anchor-tests.stellar.org) (testnet; home domain `anchor.versotek.io`)

## Tests

```powershell
cd backend
python manage.py test verso_integrations
```

En cada **push** y **pull request**, GitHub Actions ejecuta los mismos tests (`.github/workflows/backend-tests.yml`).

## Base de datos (T1)

**Tranche 1 no usa PostgreSQL.** Sin `DATABASE_URL`, Django usa **SQLite** (`backend/db.sqlite3`). Eso basta para SEP-1 y SEP-10 en testnet.

- **Local:** no configures `DATABASE_URL` en `backend/.env` (dejar vacío).
- **Producción (Railway):** no añadir Postgres ni `DATABASE_URL` en esta entrega.
- `docker-compose.yml` queda para **T2** (SEP-24/38, sesiones Redis, etc.).

`python manage.py migrate` sigue siendo necesario (tablas de Django/Polaris en SQLite).

## Producción y deploy

- **Dominio:** `anchor.versotek.io`
- **Plataforma:** Railway conectado a este repo; **push a `main`** dispara deploy automático.
- **Build:** `railpack.json` → `pip install`, `collectstatic`, `migrate`, `gunicorn`.
- **Variables:** definir en el dashboard de Railway (nunca en Git). Referencia en `backend/.env.example`.
- **Sin `DATABASE_URL`** en T1 (SQLite en el contenedor; suficiente para SEP-1/SEP-10).

Valores clave en producción:

```
HOST_URL=https://anchor.versotek.io
LOCAL_MODE=0
DEBUG=False
ALLOWED_HOSTS=anchor.versotek.io,.up.railway.app
```

La cuenta Stellar de `SIGNING_SEED` debe tener **home domain** `anchor.versotek.io` en testnet.

## Git y ramas

```powershell
git checkout main
git pull origin main
```

Flujo recomendado: rama feature → **pull request** → merge a `main` → deploy en Railway.

## Documentación Polaris

- https://django-polaris.readthedocs.io/en/stable/

## Licencia

Propietario — VERSO / versotek.io

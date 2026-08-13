# VERSO Stellar Anchor

Anchor Stellar de VERSO (PSAV Perú) implementado con **Django + Polaris**.

Repositorio separado del core VERSO (`BASE_DE_CLIENTES`, `versotek.io`). Se conecta por API interna.

## Roadmap

| Tranche | SEPs | Estado |
|---------|------|--------|
| **T1** | SEP-1, SEP-10 | En progreso |
| **T2** | SEP-24, SEP-38 | Pendiente |
| **T3** | Mainnet + Anchor Directory | Pendiente |

## Estructura

```
ANCHOR/
├── venv/                       # Virtual environment (no commitear)
├── backend/
│   ├── manage.py
│   ├── config/                 # Settings Django
│   ├── verso_integrations/     # Lógica de negocio VERSO
│   │   ├── sep1.py             # Contenido stellar.toml
│   │   ├── kyc_bridge.py       # Lookup KYC por Stellar pubkey
│   │   ├── deposit.py          # On-ramp (T2)
│   │   ├── withdraw.py         # Off-ramp (T2)
│   │   └── rates.py            # Cotizaciones PEN/USDC (T2)
│   └── .env                    # Variables Polaris (copiar de .env.example)
├── docker-compose.yml          # Postgres + Redis
└── requirements.txt
```

## Requisitos

- Python 3.11+
- (Opcional) Docker para Postgres y Redis

## Git y GitHub

El repositorio Git está en la **raíz `ANCHOR/`** (no solo en `backend/`).

```powershell
cd ANCHOR
git status
git add .
git commit -m "mensaje"
git push
```

No commitear `backend/.env` (secretos). Usar `backend/.env.example` como plantilla.

```powershell
# 1. Activar entorno virtual
cd ANCHOR
.\venv\Scripts\Activate.ps1

# 2. Configurar variables de entorno
Copy-Item backend\.env.example backend\.env

# 3. Generar claves Stellar testnet y JWT
python -c "from stellar_sdk import Keypair; import secrets; print('SIGNING_SEED=' + Keypair.random().secret); print('SERVER_JWT_KEY=' + secrets.token_urlsafe(32))"
# Pegar los valores en backend\.env

# 4. Migraciones
cd backend
python manage.py migrate

# 5. Servidor de desarrollo
python manage.py runserver
```

## Verificar SEP-1 y SEP-10

### Producción (testnet — `anchor.versotek.io`)

| Check | URL |
|-------|-----|
| stellar.toml (SEP-1) | https://anchor.versotek.io/.well-known/stellar.toml |
| SEP-10 auth | https://anchor.versotek.io/auth?account=G... |
| Admin | https://anchor.versotek.io/admin |

### Local (`python manage.py runserver`)

| Check | URL |
|-------|-----|
| stellar.toml (SEP-1) | http://localhost:8000/.well-known/stellar.toml |
| SEP-10 auth | http://localhost:8000/auth?account=G... |
| Admin | http://localhost:8000/admin |

Validar `stellar.toml` en [Stellar Laboratory](https://laboratory.stellar.org/#account-creator?network=test) o contra el endpoint de producción: [anchor.versotek.io/.well-known/stellar.toml](https://anchor.versotek.io/.well-known/stellar.toml).

## Postgres (local)

```powershell
docker compose up -d
```

En `backend/.env`:

```
DATABASE_URL=postgres://verso:verso@localhost:5432/verso_anchor
```

## Deploy en Railway

Guía completa: [RAILWAY.md](RAILWAY.md)

Archivos incluidos: `railpack.json`, `Procfile`, `runtime.txt`

Resumen:
1. Conectar repo GitHub en Railway
2. Añadir PostgreSQL y referenciar `DATABASE_URL`
3. Configurar variables (ver `backend/.env.example` y `RAILWAY.md`)
4. Dominio custom: `anchor.versotek.io`
5. `HOST_URL=https://anchor.versotek.io`, `LOCAL_MODE=0`, `DEBUG=False`

## Documentación Polaris

- https://django-polaris.readthedocs.io/en/stable/

## Licencia

Propietario — VERSO / versotek.io

# Deploy en Railway — VERSO Stellar Anchor

## Archivos de deploy incluidos

| Archivo | Función |
|---------|---------|
| `requirements.txt` | Dependencias Python |
| `runtime.txt` | Versión Python 3.12 |
| `railway.toml` | Build, migrate, start, healthcheck |
| `Procfile` | Comando web (fallback) |
| `nixpacks.toml` | Config Nixpacks |

## 1. Crear proyecto en Railway

1. [railway.app](https://railway.app) → **New Project** → **Deploy from GitHub**
2. Conectar repo `verso-stellar-anchor` (carpeta raíz `ANCHOR/`)
3. **No** cambiar root directory — el build usa `backend/` vía comandos en `railway.toml`

## 2. Añadir PostgreSQL

1. En el proyecto → **+ New** → **Database** → **PostgreSQL**
2. Railway crea `DATABASE_URL` automáticamente en el servicio web (referencia la variable)

En el servicio **web** → Variables → referenciar:

```
DATABASE_URL = ${{Postgres.DATABASE_URL}}
```

## 3. Variables de entorno (servicio web)

Copiar en Railway → **Variables** (no subir `.env` a Git):

### Django

```
DJANGO_SECRET_KEY=<generar-secreto-largo>
DEBUG=False
ALLOWED_HOSTS=anchor.versotek.io,.up.railway.app
CSRF_TRUSTED_ORIGINS=https://anchor.versotek.io
```

### Polaris (SEP-1 + SEP-10 testnet)

```
ACTIVE_SEPS=sep-1,sep-10
HOST_URL=https://anchor.versotek.io
LOCAL_MODE=0
ENABLE_SEP_0023=1
SIGNING_SEED=S...testnet...
SERVER_JWT_KEY=...
SEP10_HOME_DOMAINS=versotek.io,anchor.versotek.io
STELLAR_NETWORK_PASSPHRASE=Test SDF Network ; September 2015
```

> `HOST_URL` debe ser la URL **HTTPS pública final** (custom domain o `*.up.railway.app`).

### VERSO Core (opcional T1)

```
VERSO_CORE_API_URL=https://...
VERSO_CORE_API_KEY=...
```

## 4. Dominio custom (recomendado)

1. Railway → servicio web → **Settings** → **Networking** → **Custom Domain**
2. Añadir `anchor.versotek.io`
3. CNAME en DNS hacia Railway
4. Actualizar `HOST_URL`, `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, `SEP10_HOME_DOMAINS`

## 5. Deploy

Push a `main` → Railway build automático:

```
pip install → collectstatic → migrate → gunicorn
```

Healthcheck: `GET /.well-known/stellar.toml`

## 6. Verificar post-deploy

```bash
curl https://anchor.versotek.io/.well-known/stellar.toml
curl "https://anchor.versotek.io/auth?account=G..."
```

Anchor Validator: https://anchor-tests.stellar.org

- HOME DOMAIN: `anchor.versotek.io`
- Network: Testnet
- SEP: SEP-1, SEP-10

## 7. Generar secretos (local)

```powershell
.\venv\Scripts\python -c "from stellar_sdk import Keypair; import secrets; print('SIGNING_SEED=' + Keypair.random().secret); print('SERVER_JWT_KEY=' + secrets.token_urlsafe(32)); print('DJANGO_SECRET_KEY=' + secrets.token_urlsafe(50))"
```

## Troubleshooting

| Problema | Solución |
|----------|----------|
| `No start command detected` (Railpack) | Usar `railpack.json` en la raíz o variable `RAILPACK_START_CMD` (ver abajo) |
| 502 / crash al arrancar | Revisar logs; falta `SIGNING_SEED` o `SERVER_JWT_KEY` |
| DisallowedHost | Añadir dominio a `ALLOWED_HOSTS` |
| TOML con URLs HTTP | `LOCAL_MODE=0` y `HOST_URL=https://...` |
| Migrate failed | Verificar `DATABASE_URL` referenciada desde Postgres |

### Railpack — start command manual (si falla el auto-detect)

Railway usa **Railpack** y busca `manage.py` en la raíz. Nuestro Django está en `backend/`.

Opción A — ya incluido: archivo `railpack.json` en la raíz del repo.

Opción B — variable en Railway → **Variables**:

```
RAILPACK_START_CMD=cd backend && python manage.py migrate --noinput && gunicorn config.wsgi:application --bind 0.0.0.0:$PORT --workers 2 --timeout 120
```

Opción C — Railway → servicio → **Settings** → **Deploy** → **Custom Start Command** (mismo comando que arriba).

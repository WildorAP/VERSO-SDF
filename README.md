# VERSO Stellar Anchor

Anchor Stellar de VERSO (PSAV Perú) implementado con **Django + Polaris**.

Repositorio separado del core VERSO (`BASE_DE_CLIENTES`, [versotek.io](https://versotek.io)). `kyc_bridge.py` está preparado para su uso en el flujo de depósito/retiro (T2), no en SEP-10 — ver "Nota de arquitectura".

**Producción (testnet):** https://anchor.versotek.io

## Roadmap

| Tranche | SEPs           | Estado                                                                                     |
| ------- | -------------- | ------------------------------------------------------------------------------------------ |
| **T1**  | SEP-1, SEP-10  | Completo en testnet (`anchor.versotek.io`) — 3 deliverables verificados, ver detalle abajo |
| **T2**  | SEP-24, SEP-38 | Pendiente                                                                                  |
| **T3**  | Mainnet        | Pendiente                                                                                  |

## Estado de entregables — Tranche 1 (SCF #44)

### Deliverable 1 — SEP-1: Anchor Platform live on testnet + stellar.toml published

**Cubierto.** El `stellar.toml` está publicado y es descubrible por wallets SEP-compatibles en testnet.

- Endpoint público: [anchor.versotek.io/.well-known/stellar.toml](https://anchor.versotek.io/.well-known/stellar.toml), servido por `verso_integrations/sep1.py` (contenido dinámico: cuentas, currencies USDC/PEN/USD, documentación) vía `toml_view.py` (charset UTF-8 forzado).
- Descubribilidad verificada con el [Stellar Demo Wallet](https://demo-wallet.stellar.org): al agregar el asset USDC con home domain `anchor.versotek.io`, la wallet resolvió el `stellar.toml` correctamente, reconoció el asset y permitió operarlo (balance visible, trustline activa).
- Landing de estado en [anchor.versotek.io/](https://anchor.versotek.io/) (HTML para revisores; manifest JSON en `?format=json`), implementada en `root.py` + `templates/verso_integrations/root.html`.
- Health del servicio: confirmado operativo en Railway (deploy automático desde `main`).

Ver "Nota de arquitectura" más abajo para las desviaciones documentadas respecto al texto original de la propuesta (Polaris en lugar de Anchor Platform Docker; gestión del signing seed sin AWS KMS; KYC diferido a T2).

### Deliverable 2 — SEP-10: Wallet authentication connected to VERSO's compliance system

**Cubierto en su componente de autenticación.** El flujo de autenticación SEP-10 fue verificado de punta a punta contra el deploy real en testnet, con el Stellar CLI, siguiendo el procedimiento oficial documentado por Stellar ([developers.stellar.org — Testing Your Configuration](https://developers.stellar.org/docs/tools/cli)):

```powershell
# 1. Solicitar el challenge
$CHALLENGE_RESPONSE = curl.exe -s "https://anchor.versotek.io/auth?account=$ACCOUNT_ID"
$CHALLENGE_XDR = $CHALLENGE_RESPONSE | jq -r '.transaction'

# 2. Firmar el challenge con la wallet del cliente (testnet)
$SIGNED_CHALLENGE_XDR = ($CHALLENGE_XDR | stellar tx sign --sign-with-key $SECRET_SEED --network testnet 2>&1) | Select-Object -Last 1

# 3. Enviar la firma y recibir el JWT
$body = @{ transaction = $SIGNED_CHALLENGE_XDR } | ConvertTo-Json -Compress
Set-Content -Path "$env:TEMP\sep10_body.json" -Value $body -Encoding ascii -NoNewline
curl.exe -X POST "https://anchor.versotek.io/auth" -H "Content-Type: application/json" -d "@$env:TEMP\sep10_body.json"
```

Resultado obtenido — un JWT válido, emitido por el anchor de VERSO para la cuenta que firmó el challenge:

```json
{
  "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJodHRwczovL2FuY2hvci52ZXJzb3Rlay5pby9hdXRoIiwic3ViIjoiR0NYR0xXTDdHRVBVRENDWkFCUVZMSFRaTERXV1hQVFVSR1hPREo2SkY2QlZKU080S1dVNDVJRkciLCJpYXQiOjE3ODY2Njc0NTMsImV4cCI6MTc4Njc1Mzg1MywianRpIjoiMDYxZTUxNDYzNjZiMjRmYjM1OTMxNmZkYmNmNThmMGRiMTFkNjJhNjFlNGNlYzBjMDI3ZjY0Y2ZmNDgxODViMiIsImNsaWVudF9kb21haW4iOm51bGx9..."
}
```

El payload decodificado confirma `iss: https://anchor.versotek.io/auth` y `sub` igual a la cuenta que firmó, con timestamps de emisión/expiración correctos — validando el ciclo completo: solicitud de challenge → firma por la wallet del cliente → verificación de firma por el backend → emisión de JWT.

**No implementado en T1, diferido a T2:** la verificación de estado KYC contra el sistema de compliance de VERSO y la redirección a onboarding de DIDIT. `kyc_bridge.py` existe como cliente HTTP funcional, pero no está conectado a ningún punto del código en T1 (ni a SEP-10, ni al flujo de depósito de Deliverable 3) — cero llamadas, cero tests. Ver punto 3 de "Nota de arquitectura" para la justificación de por qué este chequeo se implementará en el webview interactivo de SEP-24 (T2) en lugar de duplicarlo ahora con una versión mínima que luego habría que rehacer.

### Deliverable 3 — First end-to-end simulated deposit on testnet

**Implementado (simulación vía Admin).** Modelo `FiatDeposit`, acciones en Django Admin y pago USDC on-chain con `SIGNING_SEED`.

Flujo operador:

1. Admin → **Simulated fiat deposits** → **Add** (cuenta `G...`, monto PEN y **tipo de cambio**; USDC se calcula solo).
2. Revisar `bank_instructions` (CCI/CCE ficticio).
3. Acción **Mark fiat as received** (`pending` → `fiat_confirmed`).
4. Acción **Disburse USDC on-chain** → pasa por `disbursing`, envía USDC testnet a la wallet del cliente y termina en `disbursed` con `stellar_tx_hash`.
5. Verificar en [Stellar Expert testnet](https://stellar.expert/explorer/testnet).

**Estados del depósito** (`FiatDeposit.status`):

| Estado | Significado |
|--------|-------------|
| `pending` | Creado; esperando transferencia PEN simulada |
| `fiat_confirmed` | Operador confirmó recepción fiat; listo para desembolsar |
| `disbursing` | Pago on-chain en curso (bloqueo de fila activo) |
| `disbursed` | USDC enviado; `stellar_tx_hash` y `disbursed_at` guardados |

Si el pago Stellar falla, el depósito vuelve a `fiat_confirmed` y el error queda en `status_message` — el operador puede reintentar **Disburse USDC on-chain** sin crear un depósito nuevo.

Requisitos testnet: la cuenta del anchor (`SIGNING_SEED`) debe tener trustline USDC + saldo suficiente.

**Verificado en producción (Railway).** Ciclo completo ejecutado contra `anchor.versotek.io/admin`, con la misma cuenta de cliente usada en la prueba de SEP-10 (Deliverable 2):

| Campo                     | Valor                                                              |
| ------------------------- | ------------------------------------------------------------------ |
| Stellar account (cliente) | `GCXGLWL7GEPUDCCZABQVLHTZLDWWXPTURGXODJ6JF6BVJSO4KWU45IFG`         |
| Amount PEN (simulado)     | 10.00                                                              |
| Tipo de cambio            | 3.4000                                                             |
| Amount USDC (calculado)   | 2.9411765                                                          |
| Status final              | `disbursed` (`USDC disbursed` en admin)                            |
| Stellar tx hash           | `8d664b23e57faff63b957bfd88279862b73d9c5919eb796783735c02abb7c050` |

Transacción confirmada on-chain en [Stellar Expert (testnet)](https://stellar.expert/explorer/testnet/tx/8d664b23e57faff63b957bfd88279862b73d9c5919eb796783735c02abb7c050): status `Successful`, ledger `4130191`, `GBTV5Q…24UOPB sent 2.9411765 USDC to GCXG…5IFG` — la hot wallet del anchor (`SIGNING_SEED`) transfiriendo USDC real de testnet a la wallet del cliente, disparado por la confirmación manual del operador en el Admin.

Esto valida el ciclo completo: solicitud de depósito → instrucciones bancarias simuladas → confirmación manual del operador (recepción PEN simulada) → desembolso automático de USDC on-chain → estado final trackeado — cumpliendo el criterio de medición del deliverable ("all transaction states tracked", "on-chain USDC disbursement confirmed on Stellar testnet").

**No implementado en T1, diferido a T2:** el criterio de medición "KYC check passes" del texto original del deliverable no está cubierto — el flujo permite crear, confirmar y desembolsar un depósito sin ningún chequeo de KYC de por medio. Ver punto 3 de "Nota de arquitectura".

## Hardening pre-auditoría (depósito simulado)

Mejoras incorporadas en `main` antes de revisión externa (rama `hardening/pre-audit-fixes`):

| Cambio | Archivo | Detalle |
|--------|---------|---------|
| Bloqueo de concurrencia | `admin.py` | `select_for_update` evita doble desembolso si dos operadores disparan la acción a la vez |
| Estado `disbursing` | `models.py`, migración `0003` | Marca el depósito mientras la transacción Stellar está en vuelo |
| Reintento seguro | `admin.py` | Fallo de red → vuelve a `fiat_confirmed` + `status_message`; no queda en estado inconsistente |
| Timeout Stellar | `stellar_payout.py` | Transacción con `set_timeout(180)` (antes 30 s) |
| Sesión admin | `settings.py` | `SESSION_COOKIE_AGE = 600` (10 min de inactividad) |

Cobertura automatizada: `test_deposit_concurrency.py`, `test_stellar_payout.py`.

## Nota de arquitectura: desviaciones respecto a la propuesta (SCF #44)

La propuesta original describe el uso del **SDF Anchor Platform** (servicio Java/Kotlin distribuido como imagen Docker) con **AWS KMS** para la firma de transacciones. La implementación actual difiere en tres aspectos, documentados a continuación para conocimiento del SDF.

**1. Anchor Platform → django-polaris.** Se utiliza [django-polaris](https://django-polaris.readthedocs.io/en/stable/), la implementación de referencia en Python mantenida oficialmente por SDF, integrada directamente en el backend Django de VERSO, en lugar del servicio Anchor Platform desplegado como contenedor independiente. Ambas alternativas son soluciones oficiales de SDF e implementan los mismos SEPs con el mismo nivel de conformidad. Esta elección evita operar dos servicios en runtimes distintos (Python y JVM) y consolida el despliegue en un único proceso. Como consecuencia, el repositorio no incluye un `Dockerfile` de aplicación ni un contenedor "Anchor Platform"; `docker-compose.yml` en el repo es para **desarrollo local** (Postgres + Redis). En **producción (Railway)** la base de datos es **PostgreSQL** gestionado por Railway, enlazado al servicio web vía `DATABASE_URL`.

**2. AWS KMS: no implementado.** La firma de transacciones Stellar utiliza el esquema Ed25519, algoritmo no soportado por la API `Sign` de AWS KMS (limitada a RSA y ECDSA sobre curvas NIST). Adicionalmente, django-polaris no expone un punto de extensión para delegar la firma a un servicio externo: el signing seed se carga en memoria al iniciar el proceso y se utiliza directamente mediante `stellar_sdk`.

En consecuencia, `SIGNING_SEED` se gestiona actualmente como variable de entorno en Railway, sin una capa adicional de custodia tipo KMS o HSM. Esta es una decisión consciente y temporal para la presente entrega: el entorno es testnet, sin fondos reales en riesgo, y Railway cifra las variables de entorno en reposo. Antes de operar en mainnet, esta gestión del secreto se migrará a un esquema de custodia más robusto (por ejemplo AWS Secrets Manager con acceso restringido por IAM y auditoría vía CloudTrail, y/o un proveedor de custodia con soporte nativo para Ed25519 como Turnkey o Fireblocks).

**3. Verificación de KYC: no implementada en T1, diferida a T2 (no reubicada aún en código).** La propuesta interna original planteaba verificar el estado de KYC del cliente y emitir el JWT de forma condicional dentro del mismo endpoint de autenticación SEP-10. Este planteamiento se corrige por no ser consistente con la separación de responsabilidades del protocolo: SEP-10 certifica exclusivamente la posesión de la cuenta Stellar (verificación de firma) y no debe depender de, ni exponer, el estado de compliance del cliente. El endpoint SEP-10 en T1 emite el JWT únicamente en base a la verificación criptográfica de la firma, conforme al estándar.

El punto correcto del protocolo para el chequeo de KYC y la redirección a onboarding de DIDIT es el webview interactivo de SEP-24 (Tranche 2), no el flujo de depósito simulado sin webview de T1. Por eso se decidió **no** construir en T1 una versión mínima de ese chequeo (por ejemplo, un gate simple en el panel de operador) — esa versión sería descartable en cuanto se implemente SEP-24 en T2, donde el chequeo de KYC y el onboarding DIDIT viven naturalmente integrados al webview. `kyc_bridge.py` existe en el repo como cliente HTTP funcional contra el sistema de compliance de VERSO, listo para conectarse en T2, pero **en T1 no tiene ningún caller**: ni SEP-10 ni el flujo de depósito simulado lo invocan. En consecuencia, el criterio de medición "KYC check passes" del texto original de los Deliverables 2 y 3 queda explícitamente diferido a T2.

## Estructura del repo

El repositorio Git vive en la **raíz `ANCHOR/`**. Django está en `backend/`; Railway y el build usan la raíz (`railpack.json` hace `cd backend`).

```
ANCHOR/
├── .github/workflows/
│   └── backend-tests.yml       # CI: corre todo verso_integrations (SEP-1, SEP-10, depósito) en push y PR
├── backend/
│   ├── manage.py
│   ├── config/                 # settings, urls, wsgi
│   ├── templates/
│   │   └── verso_integrations/
│   │       └── root.html       # Landing HTML en /
│   ├── .env.example            # Plantilla de variables (copiar a .env)
│   └── verso_integrations/
│       ├── apps.py             # Registro Polaris (toml)
│       ├── admin.py            # Admin FiatDeposit + acciones de depósito
│       ├── models.py           # FiatDeposit + estados
│       ├── migrations/         # 0001–0003 (incl. estado disbursing)
│       ├── root.py             # Landing / health check en /
│       ├── sep1.py             # Contenido dinámico de stellar.toml
│       ├── sep10.py            # SEP-10 (errores 400 en XDR inválido)
│       ├── toml_view.py        # stellar.toml con charset UTF-8
│       ├── kyc_bridge.py       # Cliente HTTP al core (preparado; sin hook SEP-10 aún)
│       ├── deposit.py          # CCI + cálculo USDC (D3); integración SEP-24 en T2
│       ├── stellar_payout.py   # Envío USDC on-chain (simulación admin)
│       ├── withdraw.py         # Stub off-ramp (T2)
│       ├── rates.py            # Stub cotizaciones PEN/USDC (T2)
│       ├── static/polaris/     # TOML estáticos (local / prod)
│       └── tests/              # 6 archivos, 23 tests (ver sección Tests)
├── docker-compose.yml          # Postgres + Redis (opcional en local; ver Base de datos)
├── requirements.txt            # Dependencias (raíz; usado por CI y Railway)
├── runtime.txt                 # Python 3.12
├── railpack.json               # Build y start en Railway
└── Procfile                    # Fallback de arranque
```

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

## Verificar SEP-1 y SEP-10

### Producción

| Check                   | URL                                                 |
| ----------------------- | --------------------------------------------------- |
| Health / landing         | https://anchor.versotek.io/ (HTML; JSON en `?format=json`) |
| stellar.toml (SEP-1)    | https://anchor.versotek.io/.well-known/stellar.toml |
| SEP-10 auth (challenge) | https://anchor.versotek.io/auth?account=G...        |
| Admin                   | https://anchor.versotek.io/admin                    |

### Local

| Check                   | URL                                            |
| ----------------------- | ---------------------------------------------- |
| Health / landing         | http://localhost:8000/ (HTML; JSON en `?format=json`) |
| stellar.toml (SEP-1)    | http://localhost:8000/.well-known/stellar.toml |
| SEP-10 auth (challenge) | http://localhost:8000/auth?account=G...        |
| Admin                   | http://localhost:8000/admin                    |

**SEP-10:** el **GET** con `?account=G...` devuelve el challenge. El **POST** exige JSON `{"transaction": "<XDR firmado>"}`; la UI de Django REST Framework en el navegador no sustituye una wallet.

Validación externa:

- TOML en producción: [anchor.versotek.io/.well-known/stellar.toml](https://anchor.versotek.io/.well-known/stellar.toml)
- [Stellar Laboratory](https://laboratory.stellar.org/#account-creator?network=test)

## Tests

```powershell
cd backend
python manage.py test verso_integrations
```

**23 tests** en 6 archivos:

| Archivo | Cubre |
|---------|--------|
| `test_sep1.py` | Contenido `stellar.toml`, issuers testnet/mainnet |
| `test_sep10.py` | Errores 400 en POST `/auth` con XDR inválido |
| `test_deposit_flow.py` | Modelo `FiatDeposit`, CCI, cálculo USDC |
| `test_root.py` | Landing `/` (HTML y `?format=json`) |
| `test_deposit_concurrency.py` | Doble disburse no paga dos veces |
| `test_stellar_payout.py` | Errores de `disburse_usdc` (seed, red, monto) |

En cada **push** y **pull request**, GitHub Actions ejecuta los mismos tests (`.github/workflows/backend-tests.yml`).

## Base de datos

| Entorno | Motor | Configuración |
|---------|--------|----------------|
| **Local** | SQLite (por defecto) | No definir `DATABASE_URL` en `backend/.env` → `backend/db.sqlite3` |
| **Local con Postgres** | PostgreSQL | `docker compose up -d` + `DATABASE_URL` en `.env` (ver abajo) |
| **Producción (Railway)** | PostgreSQL | Servicio Postgres en Railway + `DATABASE_URL=${{Postgres.DATABASE_URL}}` en el servicio web |

En **Railway** los datos persisten (admin, `FiatDeposit`, tablas Polaris). El `migrate` del deploy (`railpack.json`) aplica migraciones sobre Postgres.

### Local con SQLite (por defecto)

```powershell
cd backend
python manage.py migrate
```

### Local con Postgres (opcional, `docker-compose.yml`)

```powershell
docker compose up -d
```

En `backend/.env`:

```
DATABASE_URL=postgres://verso:verso@localhost:5432/verso_anchor
```

### Railway — PostgreSQL

1. Añadir servicio **PostgreSQL** al proyecto (CLI: `railway add -d postgres`, o dashboard).
2. En el servicio **web**, variable:

```
DATABASE_URL=${{Postgres.DATABASE_URL}}
```

(`Postgres` = nombre del servicio de base de datos en tu proyecto.)

3. Redeploy → `migrate` crea/actualiza tablas en Postgres.

**Crear superuser en producción:** desde tu PC, con venv activado y la **URL pública** de Postgres (no `postgres.railway.internal`):

```powershell
.\venv\Scripts\Activate.ps1
cd backend
$env:DATABASE_URL = "<DATABASE_PUBLIC_URL de Railway>"
$env:DJANGO_SECRET_KEY = "temporal"
python manage.py createsuperuser
```

`railway run` inyecta la URL **interna**; solo funciona dentro de la red Railway, no desde Windows.

## Producción y deploy

- **Dominio:** `anchor.versotek.io`
- **Plataforma:** Railway conectado a este repo; **push a `main`** dispara deploy automático.
- **Build:** `railpack.json` → `pip install`, `collectstatic`, `migrate`, `gunicorn`.
- **Base de datos:** PostgreSQL en Railway (`DATABASE_URL` referenciada desde el servicio Postgres).
- **Variables:** definir en el dashboard de Railway (nunca en Git). Referencia en `backend/.env.example`.

Valores clave en producción (dashboard Railway; Polaris las lee como variables de entorno):

```
DJANGO_SECRET_KEY=<secreto>
DEBUG=False
ALLOWED_HOSTS=anchor.versotek.io,.up.railway.app
CSRF_TRUSTED_ORIGINS=https://anchor.versotek.io
DATABASE_URL=${{Postgres.DATABASE_URL}}

ACTIVE_SEPS=sep-1,sep-10
HOST_URL=https://anchor.versotek.io
LOCAL_MODE=0
ENABLE_SEP_0023=1
SIGNING_SEED=<seed-testnet>
SERVER_JWT_KEY=<secreto-jwt>
SEP10_HOME_DOMAINS=versotek.io,anchor.versotek.io
STELLAR_NETWORK_PASSPHRASE=Test SDF Network ; September 2015
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

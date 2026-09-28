# D3 — Reconciliación on-chain de USDC (Stellar RPC) — Plan de implementación

> Documento para el agente que implementa el código. Léelo completo antes de escribir código.
> Idioma del código, nombres y docstrings: **inglés** (igual que el resto del repo). Mensajes del admin: pueden ir en español.

## 0. Contexto y objetivo

Entregable T2 / Deliverable 3 (texto original de la propuesta):

> Service listening in real time to every USDC movement on VERSO's testnet hot wallet. Each event recorded in
> PostgreSQL and reconciled with on-chain balance. Any discrepancy fires a CloudWatch alert.
> Schema: `stellar_tx_hash, amount_usdc, amount_pen, amount_usd, direction, kyc_status, fiat_rail, fiat_status,
> anchor_callback_id, created_at, updated_at`.
>
> Completion: (1) every USDC movement on the testnet hot wallet captured in real time and reconciled with the
> internal ledger; (2) 14-day monitoring period with no unresolved discrepancies; (3) CloudWatch alerts configured
> and tested.

**Decisión de arquitectura:** la propuesta decía "Horizon Streaming API". Horizon está en fin de vida según la docs
oficial de Stellar, así que se implementa sobre **Stellar RPC `getEvents`** con eventos unificados CAP-67
(Protocol 23: los pagos clásicos también emiten eventos `transfer`/`mint`/`burn`/`clawback` desde el contrato SAC del
activo). RPC **no tiene streaming**: se hace polling cada ~5 s con un cursor persistido en Postgres. Validado el
2026-09-28 en `soroban-testnet.stellar.org`: el retiro de 10 USDC (tx `c6efa31f…`) aparece como evento `transfer` con
`value = {amount: 100000000, to_muxed_id: <memo hash de 32 bytes>}`.

### Stack existente (no cambiar)

- Django 5.1 + **django-polaris 2.x** (`polaris.models.Transaction`), Postgres en Railway, `stellar-sdk==12.3.0`.
- App existente: `backend/verso_integrations/` (modelos `Sep24DepositMeta`, `Sep24WithdrawMeta`, `FiatDeposit`).
- Hot wallet = cuenta de distribución del asset USDC = public key de `SIGNING_SEED` (testnet: `GBTV5QYB…`).
- USDC testnet issuer: `USDC_ISSUER_TESTNET` en `verso_integrations/sep1.py`; SAC testnet:
  `CBIELTK6YBZJU5UP2WWQEUCYKLPU6AUNZ2BQ4WWFEIE3USCIHMXQDAMA`. **No hardcodear**: calcular con
  `Asset("USDC", issuer).contract_id(network_passphrase)`.
- Red: `STELLAR_NETWORK_PASSPHRASE` (testnet por defecto). Todo debe funcionar en mainnet cambiando solo env vars.

### Reglas duras

1. **La app D3 es de solo lectura sobre Polaris.** Nunca modificar `polaris.Transaction` ni los modelos de
   `verso_integrations`. `watch_transactions`, `execute_outgoing_transactions` y `poll_outgoing_transactions` siguen
   funcionando igual.
2. No tocar `stellar_payout.py` ni el flujo SEP-24/SEP-38.
3. Idempotencia: reprocesar el mismo rango de eventos nunca duplica filas.
4. Montos siempre `Decimal` con 7 decimales (stroops / 10^7). Nunca `float`.
5. Sin dependencias nuevas salvo `boto3`.

---

## 1. Estructura de archivos

Nueva app Django `backend/reconciliation/`:

```
backend/reconciliation/
├── __init__.py
├── apps.py                     # ReconciliationConfig, name="reconciliation"
├── models.py                   # SyncState, LedgerEntry, ReconciliationRun, Discrepancy
├── admin.py                    # admin de los 4 modelos + acción "resolver" + descarga de reporte
├── config.py                   # lectura de settings/env con defaults (ver §6)
├── rpc_client.py               # wrapper fino sobre stellar_sdk.SorobanServer
├── events.py                   # decodificación de eventos SAC (topics/value XDR → dataclass)
├── sync.py                     # sync_once(): getEvents → LedgerEntry + avance de cursor
├── matching.py                 # enlace LedgerEntry ↔ polaris.Transaction / FiatDeposit + campos fiat/KYC
├── reconcile.py                # reconcile_once(): balance on-chain vs ledger interno + detección de discrepancias
├── alerts.py                   # backend "log" y "cloudwatch" (métricas + publish)
├── report.py                   # generación del reporte de N días (CSV + JSON)
├── management/
│   ├── __init__.py
│   └── commands/
│       ├── __init__.py
│       ├── reconciliation_worker.py     # proceso principal del servicio Railway (--loop)
│       ├── sync_rpc_events.py           # --once / --loop (debug)
│       ├── reconcile.py                 # --once (debug)
│       ├── reconciliation_bootstrap.py  # inicializa SyncState + saldo de apertura
│       ├── setup_cloudwatch_alarms.py   # crea/actualiza alarmas y SNS (idempotente)
│       ├── reconciliation_test_alert.py # dispara una discrepancia de prueba (evidencia D3)
│       └── reconciliation_report.py     # --days 14 --out <path>
├── migrations/
│   └── 0001_initial.py
└── tests/
    ├── __init__.py
    ├── factories.py            # builders de eventos XDR falsos con stellar_sdk.scval
    ├── test_events.py
    ├── test_sync.py
    ├── test_matching.py
    ├── test_reconcile.py
    ├── test_alerts.py
    └── test_report.py
```

Cambios fuera de la app:

- `backend/config/settings.py`: añadir `"reconciliation"` a `INSTALLED_APPS` (después de `"polaris"`) y las
  settings de §6.
- `requirements.txt`: añadir `boto3>=1.34`.
- `.github/workflows/backend-tests.yml`: `python manage.py test verso_integrations reconciliation`.
- `backend/.env.example`: documentar las variables nuevas (§6).
- `README.md`: sección corta "Reconciliation worker (D3)".

---

## 2. Modelos (`reconciliation/models.py`)

### 2.1 `SyncState` (singleton, pk=1)

| Campo | Tipo | Notas |
|---|---|---|
| `network_passphrase` | CharField(128) | Para detectar cambio de red; si no coincide con el env, el worker aborta. |
| `hot_wallet` | CharField(56) | |
| `asset_contract_id` | CharField(56) | SAC de USDC. |
| `start_ledger` | PositiveIntegerField | Ledger desde el que se empezó a observar. |
| `cursor` | CharField(64, blank) | `cursor` devuelto por `getEvents`. |
| `last_processed_ledger` | PositiveIntegerField | Último ledger **completamente** procesado. |
| `opening_balance` | Decimal(20,7) | Saldo on-chain al `start_ledger`. |
| `last_success_at` | DateTimeField(null) | Heartbeat del sync. |
| `last_error` | TextField(blank) | |
| `last_error_at` | DateTimeField(null) | |
| `updated_at` | auto_now | |

Método de clase `SyncState.get()` → `objects.get(pk=1)`; lanza error claro si no existe ("run
reconciliation_bootstrap first").

### 2.2 `LedgerEntry` (un movimiento de USDC on-chain)

Columnas **exigidas por la propuesta** (nombres exactos):

| Campo | Tipo | Cómo se llena |
|---|---|---|
| `stellar_tx_hash` | CharField(64), db_index | `event.transaction_hash` |
| `amount_usdc` | Decimal(20,7) | Monto del evento, siempre positivo. |
| `amount_pen` | Decimal(18,2), null | Monto fiat si la moneda fiat del meta es PEN. |
| `amount_usd` | Decimal(18,2), null | Monto fiat si la moneda fiat del meta es USD. |
| `direction` | CharField choices `inbound` / `outbound` | `to == hot` → inbound; `from == hot` → outbound. |
| `kyc_status` | CharField(32), default `"unknown"` | Ver §4.4. |
| `fiat_rail` | CharField(32), blank | `"cci_cce"` si está enlazado a SEP-24/FiatDeposit; vacío si no. |
| `fiat_status` | CharField choices | `pending`, `confirmed` (depósito: fiat recibido), `sent` (retiro: fiat pagado), `not_applicable`. |
| `anchor_callback_id` | CharField(64), blank, db_index | **ID de la `polaris.Transaction`** (UUID string). No usamos Anchor Platform; documentarlo en el docstring. Para `FiatDeposit` legacy: `"fiatdeposit:<pk>"`. |
| `created_at` | auto_now_add | |
| `updated_at` | auto_now | |

Columnas técnicas adicionales:

| Campo | Tipo | Notas |
|---|---|---|
| `event_id` | CharField(64), **unique** | `event.id` de RPC. Clave de idempotencia. |
| `event_type` | CharField choices `transfer`, `mint`, `burn`, `clawback` | |
| `ledger` | PositiveIntegerField, db_index | |
| `ledger_closed_at` | DateTimeField | `event.ledger_close_at` |
| `from_address` / `to_address` | CharField(69), blank | G…, C… o M… |
| `counterparty` | CharField(69), blank | La dirección que no es la hot wallet. |
| `memo_raw` | CharField(128), blank | `to_muxed_id` normalizado: hash → base64 (formato Polaris), id → str(int), text → str. |
| `memo_type` | CharField(8), blank | `hash` / `id` / `text` / `""`. |
| `polaris_transaction` | FK `polaris.Transaction`, null, `on_delete=SET_NULL`, `related_name="+"` | |
| `fiat_deposit` | FK `verso_integrations.FiatDeposit`, null, `SET_NULL`, `related_name="+"` | Depósitos legacy por admin. |
| `match_status` | CharField choices `pending`, `matched`, `unmatched`, `classified` | `classified` = operador lo marcó como fondeo/manual (ver `Discrepancy.resolve`). |
| `match_kind` | CharField(32), blank | `sep24_withdrawal`, `sep24_deposit`, `fiat_deposit`, `funding`, `manual`, `other`. |
| `raw` | JSONField | Evento completo (para auditoría). |

`Meta.ordering = ["ledger", "event_id"]`.
Property `signed_amount` → `+amount_usdc` si inbound, `-amount_usdc` si outbound.

### 2.3 `ReconciliationRun` (una ejecución del chequeo de saldo)

`run_at`, `ledger` (ledger al que corresponde el saldo), `onchain_balance`, `internal_balance`, `delta`
(`onchain - internal`), `events_total`, `unmatched_count`, `open_discrepancies`, `status` (`ok` / `mismatch` /
`skipped`), `detail` (TextField). Index en `run_at`.

### 2.4 `Discrepancy`

| Campo | Tipo |
|---|---|
| `kind` | choices: `balance_mismatch`, `unmatched_inbound`, `unmatched_outbound`, `amount_mismatch`, `missing_onchain`, `worker_stale`, `test` |
| `severity` | `critical` / `warning` |
| `ledger_entry` | FK LedgerEntry, null |
| `polaris_transaction` | FK polaris.Transaction, null, `related_name="+"` |
| `run` | FK ReconciliationRun, null |
| `dedupe_key` | CharField(128) — p. ej. `unmatched_inbound:<event_id>`; **unique entre las abiertas** (`UniqueConstraint(fields=["dedupe_key"], condition=Q(resolved_at__isnull=True))`) |
| `expected` / `actual` | Decimal(20,7), null |
| `message` | TextField |
| `detected_at` | auto_now_add |
| `alerted_at` | DateTimeField, null |
| `resolved_at` | DateTimeField, null |
| `resolved_by` | FK user, null |
| `resolution` | choices `funding`, `manual_transfer`, `fixed`, `false_positive`, `auto_cleared` |
| `resolution_note` | TextField, blank |

Método `resolve(user, resolution, note)`: obligatorio `note` no vacío salvo `auto_cleared`. Si hay `ledger_entry` y
la resolución es `funding` o `manual_transfer`, poner `ledger_entry.match_status = "classified"` y
`match_kind = resolution`.

---

## 3. Lectura de eventos (RPC)

### 3.1 `rpc_client.py`

```python
class RpcClient:
    def __init__(self, url: str): self.server = SorobanServer(url)
    def latest_ledger(self) -> int
    def get_events(self, *, start_ledger: int | None, cursor: str | None, filters, limit: int) -> GetEventsResponse
    def usdc_balance(self, account: str, asset: Asset) -> tuple[Decimal, int]   # (saldo, latest_ledger)
```

- `get_events`: pasar **`start_ledger` o `cursor`, nunca ambos** (RPC lo rechaza).
- `usdc_balance`: `get_ledger_entries([LedgerKey trustline])` construido con
  `stellar_xdr.LedgerKey(type=LedgerEntryType.TRUSTLINE, trust_line=LedgerKeyTrustLine(account_id=..., asset=asset.to_trust_line_asset_xdr_object()))`.
  Decodificar `LedgerEntryData.from_xdr(entry.xdr).trust_line.balance.int64` / 10^7. Si no hay entrada → error
  claro "hot wallet has no USDC trustline". Devolver también `response.latest_ledger`.
- Reintentos: 3 intentos con backoff (1 s, 2 s, 4 s) ante errores de red / 5xx; luego propagar.

### 3.2 Filtros (`events.py`)

Un `EventFilter(event_type=EventFilterType.CONTRACT, contract_ids=[SAC], topics=[...])` con estos patrones
(topics codificados como XDR base64 con `scval.to_symbol(...).to_xdr()` y `scval.to_address(hot).to_xdr()`;
comodín `"*"`):

| Movimiento | topics |
|---|---|
| transfer saliente | `[transfer, HOT, *, *]` |
| transfer entrante | `[transfer, *, HOT, *]` |
| mint (issuer → hot) | `[mint, HOT, *]` |
| burn (hot → issuer) | `[burn, HOT, *]` |
| clawback | `[clawback, HOT, *]` |

Límite de RPC: máx. 5 filtros por request y 5 patrones de topics por filtro. Si el SDK no acepta los 5 patrones en un
filtro, dividir en 2 filtros. **Verificar contra testnet real** con un test manual (`sync_rpc_events --once
--dry-run` que imprime los eventos) antes de dar por buena la forma de los topics.

Formato CAP-67 (confirmar con el evento real `c6efa31f…`):

- `transfer`: topics `[Symbol("transfer"), Address(from), Address(to), String(asset_sep11)]`.
- `mint`: `[Symbol("mint"), Address(to), String(asset)]`. `burn`/`clawback`: `[Symbol(...), Address(from), String(asset)]`.
- `value`: o `i128` (monto) o `Map{ "amount": i128, "to_muxed_id": u64 | Bytes(32) | String }`.

### 3.3 Decodificación

```python
@dataclass(frozen=True)
class UsdcMovement:
    event_id: str; ledger: int; ledger_closed_at: datetime; tx_hash: str
    event_type: str; from_address: str; to_address: str
    amount: Decimal                       # positivo, 7 decimales
    memo_raw: str; memo_type: str         # normalizado, ver abajo
    raw: dict

def decode_event(event: EventInfo, hot_wallet: str) -> UsdcMovement | None
```

- Usar `scval.from_xdr(...)` + `scval.to_native(...)`.
- Ignorar eventos con `in_successful_contract_call is False`.
- Ignorar eventos donde la hot wallet es from y to (self-transfer) — registrar como warning en log.
- Memo:
  - `Bytes` de 32 → `base64.b64encode(bytes).decode()` con `memo_type="hash"` — **es exactamente el formato que Polaris
    guarda en `Transaction.memo`** (ver `polaris.utils.memo_hex_to_base64`; Polaris genera el memo como el UUID de la
    transacción en hex, rellenado con ceros a 64 caracteres).
  - `u64` → `str(int)`, `memo_type="id"`. `String` → `memo_type="text"`.

---

## 4. Sync (`sync.py`) y matching (`matching.py`)

### 4.1 Bootstrap (`reconciliation_bootstrap`)

1. Si ya existe `SyncState`, abortar salvo `--force`.
2. `balance, latest = rpc.usdc_balance(hot, usdc)`.
3. Crear `SyncState(start_ledger=latest + 1, last_processed_ledger=latest, opening_balance=balance, cursor="")`.
4. Imprimir resumen. **El saldo de apertura corresponde al ledger `latest`; los eventos se leen desde `latest + 1`.**

Opción `--start-ledger N` (debe estar dentro de la retención de RPC, ~7 días) para importar historia reciente:
en ese caso `opening_balance = saldo_actual - suma_firmada(eventos entre N y latest)`, calculado tras un sync
completo en modo bootstrap. Si es complejo, dejarlo fuera; no es requisito del D3.

### 4.2 `sync_once() -> int` (número de eventos nuevos)

```
state = SyncState.get() (select_for_update dentro de transaction.atomic)
resp = rpc.get_events(cursor=state.cursor or None, start_ledger=None if state.cursor else state.start_ledger, ...)
for event in resp.events:
    mv = decode_event(event, hot)
    if mv: LedgerEntry.objects.get_or_create(event_id=mv.event_id, defaults=...)
state.cursor = resp.cursor
state.last_processed_ledger = max(state.last_processed_ledger, resp.latest_ledger si la página vino incompleta, si no el ledger del último evento)
state.last_success_at = now()
save (todo en la misma transacción atómica)
```

- Paginar: repetir mientras la página venga llena (`len(events) == limit`, limit=200).
- Si RPC responde que el cursor/start_ledger quedó fuera de la retención → **no** saltar en silencio: registrar
  `last_error`, crear `Discrepancy(kind="worker_stale", severity="critical")` con mensaje "gap: events lost between
  ledger X and Y; manual backfill needed (Hubble)", y reiniciar el cursor desde el ledger más antiguo disponible.
- Tras insertar, llamar a `matching.match_pending()`.
- Errores: capturar, guardar `last_error`/`last_error_at`, re-lanzar al loop (el loop decide dormir y reintentar).

### 4.3 `match_pending()`

Para cada `LedgerEntry(match_status="pending")`:

**Inbound (`to == hot`)**
1. Si `memo_type == "hash"`: buscar `Transaction.objects.filter(kind="withdrawal", memo=entry.memo_raw, memo_type="hash")`.
2. Si no, buscar por `stellar_transaction_id == stellar_tx_hash` (lo escribe `watch_transactions`).
3. Match → `match_kind="sep24_withdrawal"`, `polaris_transaction`, `anchor_callback_id=str(tx.id)`.
   - Si `tx.amount_in` existe y difiere de `amount_usdc` → `Discrepancy(amount_mismatch, warning)`.

**Outbound (`from == hot`)**
1. `Transaction.objects.filter(kind="deposit", stellar_transaction_id=hash)` → `sep24_deposit`.
2. Si no, `FiatDeposit.objects.filter(stellar_tx_hash=hash)` → `fiat_deposit`, `anchor_callback_id=f"fiatdeposit:{pk}"`.
3. Si el monto difiere de `tx.amount_out` / `fiat_deposit.amount_usdc` → `amount_mismatch`.

**Mint/burn/clawback:** `match_kind="other"`, quedan `unmatched` hasta que el operador los clasifique.

**Sin match:** mientras `now - ledger_closed_at < RECON_MATCH_GRACE_SECONDS` (default 600) queda `pending`
(Polaris/admin pueden escribir el hash después). Pasado el plazo → `match_status="unmatched"` y
`Discrepancy(unmatched_inbound|unmatched_outbound, warning, dedupe_key=f"{kind}:{event_id}")`.
Si luego aparece el match (p. ej. el admin disparó el pago tarde), en el siguiente ciclo `match_pending` también debe
revisar entradas `unmatched` y, si ahora enlazan, marcarlas `matched` y resolver la discrepancia con
`resolution="auto_cleared"`.

### 4.4 Campos fiat y KYC (`enrich(entry)`)

Se ejecuta al hacer match y en cada ciclo de reconcile para entradas cuyo `fiat_status` no es final:

- Retiro SEP-24 (`tx.verso_withdraw_meta`): `fiat_rail="cci_cce"`; `fiat_status="sent"` si `meta.fiat_sent_at`,
  si no `"pending"`; monto fiat = `meta.amount_pen` en `amount_pen` o `amount_usd` según `meta.fiat_currency`
  (**ojo:** el campo `amount_pen` del meta guarda el monto fiat en cualquiera de las dos monedas).
- Depósito SEP-24 (`tx.verso_deposit_meta`): `fiat_status="confirmed"` si `meta.fiat_confirmed_at`, si no
  `"pending"`; mismo criterio de moneda.
- `FiatDeposit`: siempre PEN; `fiat_status="confirmed"` si su status ≠ `pending`.
- Sin match: `fiat_status="not_applicable"`, `fiat_rail=""`.
- `kyc_status`: usar `verso_integrations.kyc_bridge.fetch_client_by_stellar_key(counterparty)`; leer el campo de
  estado KYC del JSON (revisar qué devuelve Core; si no está claro, usar la clave `kyc_status` y si no existe
  `"unknown"`). `None` → `"not_found"`. **Cualquier excepción → `"unknown"` y log warning; nunca romper el sync.**
  Cachear el resultado por cuenta 10 min (`django.core.cache`) para no martillar a Core.

---

## 5. Reconciliación (`reconcile.py`)

### 5.1 `reconcile_once() -> ReconciliationRun`

1. `balance, latest = rpc.usdc_balance(...)`.
2. Asegurar que el sync alcanzó `latest`: si `state.last_processed_ledger < latest`, correr `sync_once()` y volver a
   comparar. Si tras 3 intentos sigue atrasado → `status="skipped"` con detalle (no es discrepancia).
   Esto evita falsos positivos porque el saldo se lee en el ledger `latest`.
3. `internal = state.opening_balance + Σ signed_amount(LedgerEntry con ledger <= latest)`.
4. `delta = balance - internal`. Si `delta != 0` → `Discrepancy(balance_mismatch, critical,
   dedupe_key="balance_mismatch")` (una sola abierta a la vez; actualizar `expected/actual` si ya existe). Si
   `delta == 0` y había una abierta → resolverla con `auto_cleared`.
5. `missing_onchain`: `polaris.Transaction` con `status="completed"`, `stellar_transaction_id` no vacío,
   `completed_at >= fecha de start_ledger` y sin `LedgerEntry` con ese hash, pasado el grace → `Discrepancy(missing_onchain, critical)`.
6. `worker_stale`: si `now - state.last_success_at > RECON_MAX_STALE_SECONDS` (default 300) →
   `Discrepancy(worker_stale, critical, dedupe_key="worker_stale")`; auto-resolver cuando vuelva.
7. Guardar `ReconciliationRun`, llamar a `alerts.publish(run)`.

### 5.2 Qué significa "sin discrepancias no resueltas"

Una discrepancia está resuelta si `resolved_at` no es nulo. Los fondeos (p. ej. los 20 USDC desde `GAYF33NN…`) y las
transferencias manuales se resuelven desde el admin con `resolution="funding"` o `"manual_transfer"` y una nota. El
reporte de 14 días debe mostrar: 0 abiertas al final, y cada una con quién/cuándo/cómo se resolvió.

---

## 6. Configuración (`reconciliation/config.py` + settings)

| Variable | Default | Uso |
|---|---|---|
| `STELLAR_RPC_URL` | `https://soroban-testnet.stellar.org` si passphrase = testnet; **obligatoria** en mainnet (error al arrancar si falta) | |
| `RECON_HOT_WALLET` | public key de `SIGNING_SEED` | Cuenta observada. |
| `RECON_POLL_SECONDS` | `5` | Intervalo del sync. |
| `RECON_INTERVAL_SECONDS` | `60` | Intervalo de `reconcile_once`. |
| `RECON_MATCH_GRACE_SECONDS` | `600` | |
| `RECON_MAX_STALE_SECONDS` | `300` | |
| `RECON_ALERT_BACKEND` | `log` | `log` o `cloudwatch`. |
| `RECON_CLOUDWATCH_NAMESPACE` | `VERSO/AnchorReconciliation` | |
| `RECON_ENV` | `testnet` | Dimension `Environment` de las métricas. |
| `RECON_SNS_TOPIC_ARN` | vacío | Destino de las alarmas. |
| `AWS_REGION`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` | vacío | Leídas por boto3. |

Todo con `environ.Env` como el resto de `settings.py`.

---

## 7. Alertas (`alerts.py`) — CloudWatch

Interfaz:

```python
class AlertBackend(Protocol):
    def publish_run(self, run: ReconciliationRun, state: SyncState) -> None
    def notify_discrepancy(self, d: Discrepancy) -> None
```

- `LogBackend`: `logger.warning/error` estructurado. Default en local y CI.
- `CloudWatchBackend` (boto3 `cloudwatch` y `sns`):
  - `put_metric_data` en cada run, dimensión `Environment=RECON_ENV`:
    - `OpenDiscrepancies` (Count), `CriticalOpenDiscrepancies` (Count), `BalanceDeltaAbs` (None, abs(delta)),
      `LedgerLag` (Count, `latest - last_processed_ledger`), `Heartbeat` (Count, 1).
  - `notify_discrepancy`: al crear una discrepancia nueva, `sns.publish` con asunto
    `[VERSO anchor][testnet] <kind>` y cuerpo con monto, hash, enlace a stellar.expert (reusar
    `verso_integrations.root.stellar_expert_tx_url`) y enlace al admin. Setear `alerted_at`.
  - Errores de AWS nunca tumban el worker: log + seguir.
- `setup_cloudwatch_alarms` (idempotente, `put_metric_alarm`):
  1. `verso-recon-open-discrepancies`: `CriticalOpenDiscrepancies` Maximum > 0, periodo 60 s, 1 datapoint → SNS.
  2. `verso-recon-balance-delta`: `BalanceDeltaAbs` Maximum > 0, 60 s, 1 datapoint → SNS.
  3. `verso-recon-heartbeat`: `Heartbeat` SampleCount < 1 en 5 min, **`TreatMissingData=breaching`** → SNS.
     Detecta el worker caído aunque el worker no pueda avisar.
  4. `verso-recon-ledger-lag`: `LedgerLag` Maximum > 60 (≈5 min), 2 de 3 periodos → SNS.
  Crear el topic SNS si `--create-topic --email x@y` (la suscripción email requiere confirmación manual).
  Permisos IAM mínimos: `cloudwatch:PutMetricData`, `cloudwatch:PutMetricAlarm`, `cloudwatch:DescribeAlarms`,
  `sns:Publish`, `sns:CreateTopic`, `sns:Subscribe`. Incluir la policy JSON en el README.

### Prueba de alerta (evidencia requerida)

`reconciliation_test_alert`: crea `Discrepancy(kind="test", severity="critical", message="Synthetic test")`,
llama a `notify_discrepancy`, publica un run con `CriticalOpenDiscrepancies=1` y deja la discrepancia abierta.
Con `--resolve` la cierra como `false_positive`. Imprimir instrucciones: "captura del email SNS + estado ALARM en la
consola de CloudWatch". Las discrepancias `test` se excluyen del conteo del reporte de 14 días pero se listan aparte
como evidencia de prueba.

---

## 8. Worker (`reconciliation_worker`)

```
python manage.py reconciliation_worker --loop
```

- Al arrancar: validar config, `SyncState.get()`, verificar `network_passphrase`/`hot_wallet`/`asset_contract_id`
  coinciden con el env (si no → salir con código 1 y mensaje claro).
- Bucle: `sync_once()` cada `RECON_POLL_SECONDS`; `reconcile_once()` cada `RECON_INTERVAL_SECONDS`.
- Errores: log con traceback, backoff exponencial hasta 60 s, nunca salir del loop por errores de red.
- `SIGTERM`/`SIGINT`: terminar el ciclo actual y salir limpio (Railway redeploy).
- `close_old_connections()` de Django en cada iteración (proceso de larga duración con Postgres).
- Logging a stdout, una línea por ciclo con eventos nuevos, ledger, lag y delta.

Comandos de debug: `sync_rpc_events --once [--dry-run]`, `reconcile --once`.

### Despliegue en Railway

Servicio **nuevo** en el mismo proyecto y el mismo repo (no otro repo):

- Nombre: `reconciliation-worker`.
- Start command: `cd backend && python manage.py reconciliation_worker --loop`.
- **No** ejecuta `migrate` (lo hace el servicio web). Primer deploy: desplegar web → migra → luego correr
  una vez `python manage.py reconciliation_bootstrap` (Railway shell) → arrancar el worker.
- Variables: las mismas del web vía Shared Variables (`DATABASE_URL`, `SIGNING_SEED`,
  `STELLAR_NETWORK_PASSPHRASE`, `VERSO_CORE_API_*`, `DJANGO_SECRET_KEY`, …) + las de §6.
- Restart policy: On Failure.
- No requiere puerto público.

---

## 9. Admin (`reconciliation/admin.py`)

- `LedgerEntryAdmin`: list_display `ledger_closed_at, direction, amount_usdc, event_type, counterparty(short),
  match_status, match_kind, fiat_status, kyc_status, stellar_tx_hash(link a stellar.expert)`; filtros por
  direction/match_status/match_kind/fiat_status; búsqueda por hash, memo, counterparty, anchor_callback_id; solo lectura.
- `DiscrepancyAdmin`: filtros abierta/resuelta, kind, severity. Acciones: "Resolver como fondeo", "Resolver como
  transferencia manual", "Marcar corregida", "Falso positivo" → formulario intermedio que exige nota (usar
  `admin.helpers.ACTION_CHECKBOX_NAME` + template simple, o un `ModelForm` en la vista de cambio). Guarda
  `resolved_by=request.user`.
- `ReconciliationRunAdmin`: solo lectura, ordenado por `-run_at`, colores/emoji por status.
- `SyncStateAdmin`: solo lectura; mostrar lag y minutos desde `last_success_at`.
- Vista extra en el admin: `/admin/reconciliation/report/?days=14` → descarga el CSV de §10.

---

## 10. Reporte de 14 días (`report.py`, `reconciliation_report`)

`python manage.py reconciliation_report --days 14 --out /tmp/d3_report` genera:

1. `summary.json`: periodo (inicio/fin UTC), red, hot wallet, SAC, `start_ledger`, saldo inicial/final on-chain e
   interno, total de runs, runs `ok`/`mismatch`/`skipped`, % de runs ok, eventos totales por dirección y por
   `match_kind`, discrepancias detectadas/resueltas/abiertas por kind, tiempo medio de resolución, máximo
   `LedgerLag`, huecos de heartbeat > `RECON_MAX_STALE_SECONDS`, y el booleano
   **`no_unresolved_discrepancies`**.
2. `ledger_entries.csv`: todas las columnas del esquema de la propuesta (en ese orden) + `event_id, ledger,
   ledger_closed_at, match_kind, counterparty`.
3. `discrepancies.csv` y `runs.csv` (runs agregados por hora para no generar 20k filas: min/max delta, count).
4. `README.txt` breve explicando las columnas y el método (RPC `getEvents` + chequeo de saldo por
   `getLedgerEntries`).

---

## 11. Tests

Usar `unittest.mock` para `RpcClient` (nunca red en tests). `factories.py` construye `EventInfo` con
`scval.to_symbol/to_address/to_string/to_int128/to_map/to_bytes/to_uint64(...).to_xdr()`.

Casos mínimos:

- `test_events`: decodifica transfer con `i128`; con map + `to_muxed_id` bytes (memo = base64 igual al generado por
  Polaris para un UUID); con `to_muxed_id` u64; mint/burn; ignora `in_successful_contract_call=False`; montos exactos
  (100000000 → `Decimal("10.0000000")`).
- `test_sync`: idempotencia (mismo evento dos veces → 1 fila); paginación; cursor persistido; error de RPC deja
  `last_error` y no avanza cursor; start_ledger vs cursor nunca juntos.
- `test_matching`: retiro por memo; retiro por hash; depósito SEP-24 por hash; `FiatDeposit` por hash; sin match
  dentro del grace → `pending`; fuera → `unmatched` + discrepancia; match tardío → auto_cleared; amount_mismatch;
  enrich de fiat PEN vs USD; kyc_bridge que lanza → `"unknown"`.
- `test_reconcile`: saldo cuadra → run ok; delta ≠ 0 → una sola discrepancia critical abierta (dedupe); vuelve a
  cuadrar → auto_cleared; sync atrasado → reintenta y luego `skipped`; worker_stale.
- `test_alerts`: CloudWatchBackend con `botocore.stub.Stubber` (o mock) verifica métricas y publish; errores de AWS
  no propagan.
- `test_report`: `no_unresolved_discrepancies` true/false; discrepancias `test` excluidas del conteo.

Los tests corren en CI con Postgres (ver workflow). Que `python manage.py test verso_integrations reconciliation`
pase completo.

---

## 12. Orden de implementación (PRs pequeños)

1. **PR1 — Modelos + config + app registrada + migración + admin read-only.** Tests de modelos.
2. **PR2 — `rpc_client` + `events` + `sync` + `reconciliation_bootstrap` + `sync_rpc_events`.** Probar
   `--dry-run` contra testnet real y confirmar que aparece el evento de `c6efa31f…` (si sigue dentro de la retención) o
   uno nuevo.
3. **PR3 — `matching` + `enrich`.** Tests de matching.
4. **PR4 — `reconcile` + `reconciliation_worker` + alertas `log`.**
5. **PR5 — CloudWatch (`alerts.CloudWatchBackend`, `setup_cloudwatch_alarms`, `reconciliation_test_alert`) + boto3.**
6. **PR6 — Reporte + vista admin + README + `.env.example`.**
7. Deploy en Railway (§8), prueba de alerta, arrancar los 14 días.

## 13. Criterios de aceptación

- [ ] Un pago USDC entrante y uno saliente de la hot wallet en testnet aparecen en `LedgerEntry` en < 30 s.
- [ ] Un retiro SEP-24 queda `matched` con su `polaris.Transaction` por memo; un depósito SEP-24 por hash.
- [ ] `ReconciliationRun` cada 60 s con `delta = 0` en operación normal.
- [ ] Un pago sin memo a la hot wallet genera `unmatched_inbound` y alerta SNS; se resuelve desde el admin con nota.
- [ ] Parar el worker 5+ min dispara la alarma `verso-recon-heartbeat`.
- [ ] `reconciliation_test_alert` produce email SNS y alarma en estado ALARM (capturas guardadas).
- [ ] `reconciliation_report --days 14` produce los 4 archivos y `no_unresolved_discrepancies: true`.
- [ ] Cambiar `STELLAR_NETWORK_PASSPHRASE` + `STELLAR_RPC_URL` a mainnet no requiere cambios de código.
- [ ] `watch_transactions` y el resto de workers de Polaris siguen funcionando sin cambios.
- [ ] Tests en verde en CI.

## 14. Fuera de alcance (no hacer)

- Reemplazar `watch_transactions` de Polaris (se hará después, reutilizando este worker).
- Migrar `stellar_payout.py` a RPC.
- Seguimiento de XLM (fees) — solo USDC.
- Backfill histórico con Hubble.

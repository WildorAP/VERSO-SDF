"""
Historial unificado: el servicio SYNC envía a VERSO las operaciones SEP-24.

Se verifica que arme bien cada envío, que solo envíe lo que cambió (incluidos los
cambios que Polaris hace con QuerySet.update()), que maneje los errores con
reintentos y que nunca modifique las transacciones de Polaris.
"""
from __future__ import annotations

import io
import json
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import requests
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from polaris.models import Asset, Transaction
from stellar_sdk import Keypair

from verso_integrations.core_client import CoreClientError, SyncResponse, sync_anchor_transaction
from verso_integrations.history_sync import (
    NEEDS_ATTENTION_RETRY_SECONDS,
    TRANSIENT_BACKOFF_SECONDS,
    build_payload,
    payload_hash,
    sync_once,
)
from verso_integrations.models import Sep24DepositMeta, Sep24WithdrawMeta, VersoHistorySync
from verso_integrations.polaris_setup import (
    pen_asset_identification,
    seed_polaris_t2,
    usd_asset_identification,
    usdc_asset_identification,
)

TX_HASH = "ab" * 32


class FakeVerso:
    """Sustituto del endpoint de VERSO que registra lo recibido."""

    def __init__(self, *responses):
        self.responses = list(responses) or [SyncResponse(201, {"id": 1, "created": True})]
        self.received: list[dict] = []

    def __call__(self, payload: dict) -> SyncResponse:
        self.received.append(payload)
        response = self.responses[0] if len(self.responses) == 1 else self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class HistorySyncBase(TestCase):
    def setUp(self):
        seed_polaris_t2(distribution_seed=Keypair.random().secret)
        self.account = Keypair.random().public_key

    def _tx(self, kind, status=Transaction.STATUS.pending_user_transfer_start, **extra):
        return Transaction.objects.create(
            stellar_account=self.account,
            asset=Asset.objects.get(code="USDC"),
            kind=kind,
            status=status,
            protocol=Transaction.PROTOCOL.sep24,
            **extra,
        )

    def _deposit(self, **extra):
        tx = self._tx(Transaction.KIND.deposit, **extra)
        Sep24DepositMeta.objects.create(
            transaction=tx,
            fiat_currency="USD",
            amount_pen=Decimal("500.00"),
            tipo_cambio=Decimal("1.0070"),
            amount_usdc=Decimal("496.5243297"),
            sell_asset=usd_asset_identification(),
            buy_asset=usdc_asset_identification(),
        )
        return tx

    def _withdrawal(self, origen="SUELDO", **extra):
        tx = self._tx(Transaction.KIND.withdrawal, **extra)
        details = {"account_number": "00211234567890123456", "origen_fondos": origen}
        if origen == "OTRO":
            details["origen_fondos_otro"] = "Venta de auto"
        Sep24WithdrawMeta.objects.create(
            transaction=tx,
            fiat_currency="PEN",
            amount_usdc=Decimal("100.0000000"),
            amount_pen=Decimal("335.50"),
            tipo_cambio=Decimal("3.3550"),
            sell_asset=usdc_asset_identification(),
            buy_asset=pen_asset_identification(),
            payout_bank_details=details,
        )
        return tx


class BuildPayloadTests(HistorySyncBase):
    def test_deposito(self):
        tx = self._deposit(stellar_transaction_id=TX_HASH)
        payload = build_payload(tx)
        self.assertEqual(
            payload,
            {
                "anchor_transaction_id": str(tx.id),
                "kind": "deposit",
                "status": "pending_user_transfer_start",
                "stellar_account": self.account,
                "fiat_currency": "USD",
                "amount_fiat": "500.00",
                "amount_usdc": "496.5243297",
                "rate": "1.0070",
                "started_at": tx.started_at.isoformat(),
                "completed_at": None,
                "stellar_transaction_id": TX_HASH,
            },
        )

    def test_retiro_incluye_origen_de_fondos(self):
        payload = build_payload(self._withdrawal())
        self.assertEqual(payload["kind"], "withdrawal")
        self.assertEqual((payload["fiat_currency"], payload["amount_fiat"]), ("PEN", "335.50"))
        self.assertEqual(payload["origen_fondos"], "SUELDO")
        self.assertNotIn("origen_fondos_otro", payload)

    def test_retiro_con_origen_otro(self):
        payload = build_payload(self._withdrawal(origen="OTRO"))
        self.assertEqual((payload["origen_fondos"], payload["origen_fondos_otro"]), ("OTRO", "Venta de auto"))

    def test_completada_incluye_fecha_de_cierre(self):
        cierre = timezone.now()
        payload = build_payload(self._deposit(status=Transaction.STATUS.completed, completed_at=cierre))
        self.assertEqual(payload["completed_at"], cierre.isoformat())

    def test_sin_meta_no_se_envia(self):
        self.assertIsNone(build_payload(self._tx(Transaction.KIND.deposit)))

    def test_incomplete_no_se_envia(self):
        self.assertIsNone(build_payload(self._deposit(status=Transaction.STATUS.incomplete)))

    def test_huella_cambia_solo_si_cambia_el_contenido(self):
        payload = build_payload(self._deposit())
        self.assertEqual(payload_hash(payload), payload_hash(dict(reversed(list(payload.items())))))
        self.assertNotEqual(payload_hash(payload), payload_hash({**payload, "status": "completed"}))


class SyncOnceTests(HistorySyncBase):
    def test_primer_barrido_envia_todas_las_operaciones_con_monto(self):
        deposito, retiro = self._deposit(), self._withdrawal()
        self._tx(Transaction.KIND.deposit)  # sin meta: el cliente no ingresó monto
        self._deposit(status=Transaction.STATUS.incomplete)
        verso = FakeVerso()

        stats = sync_once(sender=verso)

        self.assertEqual(stats.sent, 2)
        self.assertEqual(
            {p["anchor_transaction_id"] for p in verso.received}, {str(deposito.id), str(retiro.id)}
        )
        estado = VersoHistorySync.objects.get(transaction=deposito)
        self.assertEqual(estado.result, VersoHistorySync.Result.SYNCED)
        self.assertEqual(estado.sent_status, "pending_user_transfer_start")
        self.assertEqual(estado.verso_transaccion_id, 1)

    def test_no_reenvia_si_nada_cambio(self):
        self._deposit()
        verso = FakeVerso()
        sync_once(sender=verso)
        stats = sync_once(sender=verso)
        self.assertEqual(len(verso.received), 1)
        self.assertEqual(stats.up_to_date, 1)
        self.assertEqual(stats.sent, 0)

    def test_reenvia_cambio_de_estado_hecho_con_queryset_update(self):
        """Polaris completa algunos retiros con QuerySet.update(), que no dispara señales."""
        tx = self._withdrawal()
        verso = FakeVerso()
        sync_once(sender=verso)

        Transaction.objects.filter(pk=tx.pk).update(
            status=Transaction.STATUS.completed, completed_at=timezone.now(), stellar_transaction_id=TX_HASH
        )
        sync_once(sender=verso)

        self.assertEqual(len(verso.received), 2)
        self.assertEqual(verso.received[-1]["status"], "completed")
        self.assertEqual(verso.received[-1]["stellar_transaction_id"], TX_HASH)
        self.assertEqual(VersoHistorySync.objects.get(transaction=tx).sent_status, "completed")

    def test_nunca_modifica_las_transacciones_de_polaris(self):
        tx = self._deposit()
        antes = Transaction.objects.filter(pk=tx.pk).values().get()
        sync_once(sender=FakeVerso())
        sync_once(sender=FakeVerso(CoreClientError("caído")))
        self.assertEqual(Transaction.objects.filter(pk=tx.pk).values().get(), antes)

    def test_dry_run_no_envia_ni_guarda(self):
        self._deposit()
        verso = FakeVerso()
        stats = sync_once(sender=verso, dry_run=True)
        self.assertEqual(len(stats.dry_run), 1)
        self.assertEqual(verso.received, [])
        self.assertFalse(VersoHistorySync.objects.exists())


class ErroresYReintentosTests(HistorySyncBase):
    def setUp(self):
        super().setUp()
        self.tx = self._deposit()
        self.ahora = timezone.now()

    def _estado(self):
        return VersoHistorySync.objects.get(transaction=self.tx)

    def test_verso_caido_reintenta_con_espera_creciente(self):
        verso = FakeVerso(CoreClientError("Connection refused"))
        sync_once(sender=verso, now=self.ahora)
        estado = self._estado()
        self.assertEqual(estado.result, VersoHistorySync.Result.RETRYING)
        self.assertEqual(estado.attempts, 1)
        self.assertEqual(estado.next_attempt_at, self.ahora + timedelta(seconds=TRANSIENT_BACKOFF_SECONDS[0]))

        # Antes de que venza la espera, no se reintenta.
        stats = sync_once(sender=verso, now=self.ahora + timedelta(seconds=5))
        self.assertEqual(stats.waiting, 1)
        self.assertEqual(len(verso.received), 1)

        # Vencida la espera, reintenta y la siguiente espera es mayor.
        segundo = self.ahora + timedelta(seconds=TRANSIENT_BACKOFF_SECONDS[0] + 1)
        sync_once(sender=verso, now=segundo)
        self.assertEqual(self._estado().attempts, 2)
        self.assertEqual(
            self._estado().next_attempt_at, segundo + timedelta(seconds=TRANSIENT_BACKOFF_SECONDS[1])
        )

    def test_se_recupera_cuando_verso_vuelve(self):
        verso = FakeVerso(CoreClientError("timeout"), SyncResponse(201, {"id": 9}))
        sync_once(sender=verso, now=self.ahora)
        sync_once(sender=verso, now=self.ahora + timedelta(hours=1))
        estado = self._estado()
        self.assertEqual(estado.result, VersoHistorySync.Result.SYNCED)
        self.assertEqual((estado.attempts, estado.last_error, estado.next_attempt_at), (0, "", None))

    def test_errores_temporales_de_http(self):
        for codigo in (500, 502, 503, 401, 404, 301, 429):
            with self.subTest(codigo=codigo):
                VersoHistorySync.objects.all().delete()
                sync_once(sender=FakeVerso(SyncResponse(codigo, {})), now=self.ahora)
                self.assertEqual(self._estado().result, VersoHistorySync.Result.RETRYING)

    def test_redireccion_explica_que_revisar(self):
        sync_once(sender=FakeVerso(SyncResponse(301, {})), now=self.ahora)
        self.assertIn("VERSO_CORE_API_URL", self._estado().last_error)

    def test_wallet_no_vinculada_espera_una_hora(self):
        respuesta = SyncResponse(422, {"code": "stellar_account_not_linked", "error": "no vinculada"})
        stats = sync_once(sender=FakeVerso(respuesta), now=self.ahora)
        self.assertEqual(stats.not_linked, 1)
        estado = self._estado()
        self.assertEqual(estado.result, VersoHistorySync.Result.NOT_LINKED)
        self.assertEqual(estado.next_attempt_at, self.ahora + timedelta(seconds=NEEDS_ATTENTION_RETRY_SECONDS))

    def test_rechazo_de_datos_espera_una_hora_y_queda_registrado(self):
        respuesta = SyncResponse(409, {"code": "kind_mismatch", "error": "tipo distinto"})
        stats = sync_once(sender=FakeVerso(respuesta), now=self.ahora)
        self.assertEqual(stats.rejected, 1)
        estado = self._estado()
        self.assertEqual(estado.result, VersoHistorySync.Result.REJECTED)
        self.assertIn("kind_mismatch", estado.last_error)
        self.assertEqual(estado.next_attempt_at, self.ahora + timedelta(seconds=NEEDS_ATTENTION_RETRY_SECONDS))

    def test_si_la_operacion_cambia_durante_la_espera_se_intenta_de_inmediato(self):
        verso = FakeVerso(SyncResponse(422, {"code": "stellar_account_not_linked"}), SyncResponse(200, {"id": 3}))
        sync_once(sender=verso, now=self.ahora)
        Transaction.objects.filter(pk=self.tx.pk).update(status=Transaction.STATUS.pending_anchor)
        sync_once(sender=verso, now=self.ahora + timedelta(seconds=10))
        self.assertEqual(len(verso.received), 2)
        self.assertEqual(self._estado().result, VersoHistorySync.Result.SYNCED)

    def test_un_error_no_detiene_el_resto_del_barrido(self):
        otro = self._withdrawal()
        verso = FakeVerso(CoreClientError("timeout"), SyncResponse(201, {"id": 2}))
        stats = sync_once(sender=verso, now=self.ahora)
        self.assertEqual((stats.retrying, stats.sent), (1, 1))
        self.assertEqual(
            VersoHistorySync.objects.get(transaction=otro).result, VersoHistorySync.Result.SYNCED
        )


@override_settings(VERSO_CORE_API_URL="https://www.versotek.io/", VERSO_CORE_API_KEY="secreto")
class SyncAnchorTransactionClientTests(TestCase):
    @patch("verso_integrations.core_client.requests.post")
    def test_envia_al_endpoint_con_secreto_y_sin_seguir_redirecciones(self, post):
        post.return_value.status_code = 201
        post.return_value.content = b'{"id": 5}'
        post.return_value.json.return_value = {"id": 5}
        respuesta = sync_anchor_transaction({"anchor_transaction_id": "x"})
        self.assertEqual(respuesta, SyncResponse(201, {"id": 5}))
        args, kwargs = post.call_args
        self.assertEqual(args[0], "https://www.versotek.io/internal/anchor/transactions/")
        self.assertEqual(kwargs["headers"], {"Authorization": "Bearer secreto"})
        self.assertFalse(kwargs["allow_redirects"])

    @patch("verso_integrations.core_client.requests.post", side_effect=requests.ConnectionError("down"))
    def test_error_de_red_lanza_core_client_error(self, _post):
        with self.assertRaises(CoreClientError):
            sync_anchor_transaction({})

    @override_settings(VERSO_CORE_API_KEY="")
    def test_sin_credenciales_lanza_core_client_error(self):
        with self.assertRaises(CoreClientError):
            sync_anchor_transaction({})

    @patch("verso_integrations.core_client.requests.post")
    def test_respuesta_no_json(self, post):
        post.return_value.status_code = 502
        post.return_value.content = b"<html>Bad gateway</html>"
        post.return_value.json.side_effect = ValueError
        self.assertEqual(sync_anchor_transaction({}), SyncResponse(502, {}))


class ComandoTests(HistorySyncBase):
    def test_dry_run_muestra_lo_que_enviaria(self):
        tx = self._deposit()
        salida = io.StringIO()
        with patch("verso_integrations.history_sync.sync_anchor_transaction") as sender:
            call_command("sync_history_to_verso", "--dry-run", stdout=salida)
        sender.assert_not_called()
        lineas = salida.getvalue().strip().splitlines()
        self.assertEqual(json.loads(lineas[0])["anchor_transaction_id"], str(tx.id))
        self.assertIn("1 operación(es) por enviar", lineas[-1])

    # close_old_connections() cerraría la conexión que TestCase mantiene dentro de su
    # transacción (en Postgres); en el servicio real sí debe ejecutarse en cada barrido.
    @patch("verso_integrations.management.commands.sync_history_to_verso.close_old_connections")
    def test_un_barrido_sin_loop(self, _close):
        self._deposit()
        salida = io.StringIO()
        with patch(
            "verso_integrations.history_sync.sync_anchor_transaction",
            return_value=SyncResponse(201, {"id": 1}),
        ):
            call_command("sync_history_to_verso", stdout=salida)
        self.assertIn("enviadas=1", salida.getvalue())
        self.assertEqual(VersoHistorySync.objects.get().result, VersoHistorySync.Result.SYNCED)

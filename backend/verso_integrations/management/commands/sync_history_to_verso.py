"""
Servicio SYNC: envía las operaciones SEP-24 del Anchor al historial de VERSO.

    python manage.py sync_history_to_verso --dry-run   # muestra qué se enviaría, sin enviar
    python manage.py sync_history_to_verso             # un barrido y termina
    python manage.py sync_history_to_verso --loop      # servicio continuo (Railway: SYNC)

Corre en un proceso propio para no afectar depósitos, retiros ni conciliación.
Debe tener una sola réplica.
"""
from __future__ import annotations

import json
import logging
import signal
import time

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import close_old_connections

from verso_integrations.history_sync import sync_once

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Envía las operaciones SEP-24 del Anchor al historial de VERSO."

    def add_arguments(self, parser):
        parser.add_argument("--loop", action="store_true", help="Repetir hasta recibir SIGTERM.")
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Mostrar las operaciones que se enviarían, sin enviarlas ni guardar nada.",
        )
        parser.add_argument(
            "--interval",
            type=int,
            default=None,
            help="Segundos entre barridos (por defecto VERSO_HISTORY_SYNC_INTERVAL_SECONDS).",
        )

    def handle(self, *args, **options):
        if options["dry_run"]:
            stats = sync_once(dry_run=True)
            for payload in stats.dry_run:
                self.stdout.write(json.dumps(payload, ensure_ascii=False))
            self.stdout.write(
                f"[dry-run] {len(stats.dry_run)} operación(es) por enviar; {stats.summary()}"
            )
            return

        interval = options["interval"] or settings.VERSO_HISTORY_SYNC_INTERVAL_SECONDS
        stop = False

        def _handle_signal(signum, frame):
            nonlocal stop
            stop = True
            logger.info("Señal %s recibida: se detiene al terminar el barrido actual.", signum)

        signal.signal(signal.SIGTERM, _handle_signal)
        signal.signal(signal.SIGINT, _handle_signal)

        while True:
            close_old_connections()
            try:
                stats = sync_once()
            except Exception as exc:
                logger.exception("Barrido de historial VERSO falló")
                self.stderr.write(f"Barrido de historial VERSO falló: {exc}")
            else:
                if stats.had_activity or not options["loop"]:
                    self.stdout.write(stats.summary())
            if not options["loop"] or stop:
                break
            for _ in range(interval):
                if stop:
                    break
                time.sleep(1)
            if stop:
                break

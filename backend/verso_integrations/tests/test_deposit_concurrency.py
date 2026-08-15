import threading
from decimal import Decimal
from unittest.mock import patch

from django.contrib import messages
from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.test import RequestFactory, TransactionTestCase
from django.contrib.messages.storage.fallback import FallbackStorage

from verso_integrations.admin import FiatDepositAdmin
from verso_integrations.models import FiatDeposit
from verso_integrations.stellar_payout import StellarPayoutError


def _request_with_messages():
    request = RequestFactory().post("/admin/verso_integrations/fiatdeposit/")
    request.session = {}
    request._messages = FallbackStorage(request)
    return request


class DisburseConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.deposit = FiatDeposit.objects.create(
            stellar_account="GBTV5QYBPGHGT2SVUHCFRRKFFWUWHOEPKH7QAXGTJHFGFYZRIE24UOPB",
            amount_pen=Decimal("100.00"),
            tipo_cambio=Decimal("4.00"),
            status=FiatDeposit.Status.FIAT_CONFIRMED,
        )
        self.admin = FiatDepositAdmin(FiatDeposit, AdminSite())

    @patch("verso_integrations.admin.send_usdc_on_chain")
    def test_concurrent_disburse_only_pays_once(self, mock_send):
        # Simula la latencia real de red: cada hilo tarda un poco en "responder".
        def slow_send(*args, **kwargs):
            import time
            time.sleep(0.5)
            return "FAKE-TX-HASH"

        mock_send.side_effect = slow_send

        results = []

        def run_disburse():
            request = _request_with_messages()
            queryset = FiatDeposit.objects.filter(pk=self.deposit.pk)
            self.admin.disburse_usdc(request, queryset)
            results.append(
                [m.message for m in messages.get_messages(request)]
            )

        thread_a = threading.Thread(target=run_disburse)
        thread_b = threading.Thread(target=run_disburse)

        thread_a.start()
        thread_b.start()
        thread_a.join()
        thread_b.join()

        # La llamada real a Stellar (mockeada) debe haberse hecho UNA sola vez.
        self.assertEqual(mock_send.call_count, 1)

        self.deposit.refresh_from_db()
        self.assertEqual(self.deposit.status, FiatDeposit.Status.DISBURSED)
        self.assertEqual(self.deposit.stellar_tx_hash, "FAKE-TX-HASH")
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from polaris.models import Asset, Transaction
from stellar_sdk import Keypair

from verso_integrations.models import Sep24DepositMeta, Sep24WithdrawMeta
from verso_integrations.operation_status import (
    DEPOSIT_STAGES,
    WITHDRAW_STAGES,
    Stage,
    deposit_stage,
    deposit_stage_q,
    withdraw_stage,
    withdraw_stage_q,
)
from verso_integrations.polaris_setup import (
    pen_asset_identification,
    seed_polaris_t2,
    usdc_asset_identification,
)

S = Transaction.STATUS

# (Polaris status, fiat confirmed?, expected stage)
DEPOSIT_CASES = [
    (S.incomplete, False, Stage.INITIATED),
    (S.pending_user_transfer_start, False, Stage.INITIATED),
    (S.pending_user_transfer_start, True, Stage.RECEIVED),
    (S.pending_anchor, True, Stage.RECEIVED),
    (S.pending_stellar, True, Stage.SENDING),
    (S.pending_trust, True, Stage.SENDING),
    (S.completed, True, Stage.COMPLETED),
    (S.error, False, Stage.ERROR),
    (S.error, True, Stage.ERROR),
    (S.too_small, False, Stage.ERROR),
]

# (Polaris status, fiat sent?, expected stage)
WITHDRAW_CASES = [
    (S.incomplete, False, Stage.INITIATED),
    (S.pending_user_transfer_start, False, Stage.INITIATED),
    (S.pending_anchor, False, Stage.RECEIVED_PENDING_PAYOUT),
    (S.pending_external, False, Stage.RECEIVED_PENDING_PAYOUT),
    (S.completed, True, Stage.COMPLETED),
    (S.error, False, Stage.ERROR),
    (S.no_market, False, Stage.ERROR),
]


class OperationStageTests(TestCase):
    def setUp(self):
        seed_polaris_t2(distribution_seed=Keypair.random().secret)
        self.asset = Asset.objects.get(code="USDC")

    def _deposit(self, status, confirmed):
        tx = Transaction.objects.create(
            stellar_account=Keypair.random().public_key,
            asset=self.asset,
            kind=Transaction.KIND.deposit,
            status=status,
            protocol=Transaction.PROTOCOL.sep24,
        )
        return Sep24DepositMeta.objects.create(
            transaction=tx,
            amount_pen=Decimal("10.00"),
            tipo_cambio=Decimal("3.4500"),
            amount_usdc=Decimal("2.8985507"),
            sell_asset=pen_asset_identification(),
            buy_asset=usdc_asset_identification(),
            fiat_confirmed_at=timezone.now() if confirmed else None,
        )

    def _withdraw(self, status, sent):
        tx = Transaction.objects.create(
            stellar_account=Keypair.random().public_key,
            asset=self.asset,
            kind=Transaction.KIND.withdrawal,
            status=status,
            protocol=Transaction.PROTOCOL.sep24,
        )
        return Sep24WithdrawMeta.objects.create(
            transaction=tx,
            fiat_currency="PEN",
            amount_usdc=Decimal("5.0000000"),
            amount_pen=Decimal("18.50"),
            tipo_cambio=Decimal("3.7000"),
            sell_asset=usdc_asset_identification(),
            buy_asset=pen_asset_identification(),
            fiat_sent_at=timezone.now() if sent else None,
        )

    def test_deposit_stage_and_filter_agree_for_every_case(self):
        metas = [(self._deposit(status, confirmed), expected) for status, confirmed, expected in DEPOSIT_CASES]
        for meta, expected in metas:
            with self.subTest(status=meta.transaction.status, confirmed=bool(meta.fiat_confirmed_at)):
                self.assertEqual(deposit_stage(meta), expected)
        for stage in DEPOSIT_STAGES:
            with self.subTest(filter=stage):
                filtered = set(Sep24DepositMeta.objects.filter(deposit_stage_q(stage)).values_list("pk", flat=True))
                self.assertEqual(filtered, {meta.pk for meta, expected in metas if expected == stage})

    def test_withdraw_stage_and_filter_agree_for_every_case(self):
        metas = [(self._withdraw(status, sent), expected) for status, sent, expected in WITHDRAW_CASES]
        for meta, expected in metas:
            with self.subTest(status=meta.transaction.status):
                self.assertEqual(withdraw_stage(meta), expected)
        for stage in WITHDRAW_STAGES:
            with self.subTest(filter=stage):
                filtered = set(Sep24WithdrawMeta.objects.filter(withdraw_stage_q(stage)).values_list("pk", flat=True))
                self.assertEqual(filtered, {meta.pk for meta, expected in metas if expected == stage})

    def test_every_filter_option_covers_each_operation_exactly_once(self):
        for status, confirmed, _ in DEPOSIT_CASES:
            self._deposit(status, confirmed)
        total = sum(Sep24DepositMeta.objects.filter(deposit_stage_q(s)).count() for s in DEPOSIT_STAGES)
        self.assertEqual(total, Sep24DepositMeta.objects.count())

    def test_admin_lists_show_stage_column_and_filter(self):
        self._deposit(S.pending_stellar, True)
        self._withdraw(S.pending_external, False)
        admin_user = get_user_model().objects.create_superuser("ops", "ops@example.com", "pw")
        self.client.force_login(admin_user)

        deposits = self.client.get("/admin/verso_integrations/sep24depositmeta/?estado=sending")
        self.assertEqual(deposits.status_code, 200)
        self.assertContains(deposits, "Enviando")

        withdrawals = self.client.get("/admin/verso_integrations/sep24withdrawmeta/?estado=received_pending_payout")
        self.assertEqual(withdrawals.status_code, 200)
        self.assertContains(withdrawals, "Pago recibido – falta transferir")

        empty = self.client.get("/admin/verso_integrations/sep24depositmeta/?estado=completed")
        self.assertContains(empty, "0 SEP-24")

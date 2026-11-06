from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from polaris.models import Asset, Transaction
from stellar_sdk import Keypair

from verso_integrations import operations
from verso_integrations.models import (
    OperationHold,
    OperationRejection,
    Sep24DepositMeta,
    Sep24WithdrawMeta,
)
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
from verso_integrations.rails import VersoRailsIntegration

S = Transaction.STATUS
HASH = "a" * 64


class OperationTestCase(TestCase):
    def setUp(self):
        seed_polaris_t2(distribution_seed=Keypair.random().secret)
        self.asset = Asset.objects.get(code="USDC")
        self.user = get_user_model().objects.create_superuser("ops", "ops@example.com", "pw")
        self.client.force_login(self.user)

    def _tx(self, kind, status, **kwargs):
        return Transaction.objects.create(
            stellar_account=Keypair.random().public_key,
            asset=self.asset,
            kind=kind,
            status=status,
            protocol=Transaction.PROTOCOL.sep24,
            **kwargs,
        )

    def _deposit(self, status=S.pending_user_transfer_start, declared=True, confirmed=False, **tx_kwargs):
        return Sep24DepositMeta.objects.create(
            transaction=self._tx(Transaction.KIND.deposit, status, **tx_kwargs),
            amount_pen=Decimal("10.00"),
            tipo_cambio=Decimal("3.4500"),
            amount_usdc=Decimal("2.8985507"),
            sell_asset=pen_asset_identification(),
            buy_asset=usdc_asset_identification(),
            transfer_declared_at=timezone.now() if declared else None,
            fiat_confirmed_at=timezone.now() if confirmed else None,
        )

    def _withdrawal(self, status=S.pending_anchor, paid_in=True, **tx_kwargs):
        return Sep24WithdrawMeta.objects.create(
            transaction=self._tx(
                Transaction.KIND.withdrawal,
                status,
                amount_in=Decimal("3"),
                stellar_transaction_id=HASH if paid_in else None,
                **tx_kwargs,
            ),
            fiat_currency="PEN",
            amount_usdc=Decimal("3.0000000"),
            amount_pen=Decimal("10.06"),
            tipo_cambio=Decimal("3.3550"),
            sell_asset=usdc_asset_identification(),
            buy_asset=pen_asset_identification(),
            payout_confirmed_at=timezone.now(),
        )


class ActionMatrixTests(OperationTestCase):
    """Each kind of operation offers only the actions of its stage."""

    def test_deposit_actions_by_stage(self):
        cases = (
            (self._deposit(), {"confirm", "reject", "hold"}),
            (self._deposit(confirmed=True), set()),
            (self._deposit(status=S.pending_stellar, confirmed=True), set()),
            (self._deposit(status=S.completed, confirmed=True), set()),
            (self._deposit(status=S.error), {"reject"}),
        )
        for meta, expected in cases:
            with self.subTest(stage=deposit_stage(meta)):
                self.assertEqual(set(operations.allowed_actions(meta)), expected)

    def test_withdrawal_actions_by_stage(self):
        cases = (
            (self._withdrawal(status=S.pending_user_transfer_start, paid_in=False), {"reject", "hold"}),
            (self._withdrawal(), {"pay", "reject", "hold"}),
            (self._withdrawal(status=S.completed), set()),
            (self._withdrawal(status=S.error), {"reject"}),
        )
        for meta, expected in cases:
            with self.subTest(stage=withdraw_stage(meta)):
                self.assertEqual(set(operations.allowed_actions(meta)), expected)

    def test_a_deposit_never_offers_pay_and_a_withdrawal_never_offers_confirm(self):
        for actions in operations.DEPOSIT_ACTIONS.values():
            self.assertNotIn(operations.PAY, actions)
        for actions in operations.WITHDRAW_ACTIONS.values():
            self.assertNotIn(operations.CONFIRM, actions)

    def test_action_not_allowed_in_the_stage_is_refused(self):
        with self.assertRaises(operations.OperationError):
            operations.hold(self._deposit(confirmed=True), self.user, note="x")
        with self.assertRaises(operations.OperationError):
            operations.reject(
                self._deposit(status=S.completed, confirmed=True), self.user, reason="other", note="x"
            )


class RejectTests(OperationTestCase):
    def test_deposit_without_funds_is_rejected_and_needs_no_refund(self):
        meta = self._deposit()
        operations.reject(meta, self.user, reason="client_withdrew")
        meta.refresh_from_db()
        self.assertEqual(meta.transaction.status, S.error)
        self.assertEqual(deposit_stage(meta), Stage.REJECTED)
        self.assertEqual(operations.allowed_actions(meta), ())

    def test_deposit_with_funds_stated_by_the_operator_waits_for_the_refund(self):
        meta = self._deposit()
        operations.reject(meta, self.user, reason="amount_mismatch", funds_received=True)
        meta.refresh_from_db()
        self.assertEqual(deposit_stage(meta), Stage.REJECTED_PENDING_REFUND)
        self.assertEqual(operations.allowed_actions(meta), (operations.REFUND,))

    def test_technical_error_after_confirmed_fiat_counts_as_funds_received(self):
        meta = self._deposit(status=S.error, confirmed=True)
        rejection = operations.reject(meta, self.user, reason="other", note="fallo técnico")
        self.assertTrue(rejection.funds_received)

    def test_deposit_whose_usdc_already_left_cannot_be_rejected(self):
        meta = self._deposit(status=S.error, stellar_transaction_id=HASH)
        with self.assertRaises(operations.OperationError):
            operations.reject(meta, self.user, reason="other", note="x")
        self.assertFalse(OperationRejection.objects.exists())

    def test_withdrawal_detects_the_funds_by_itself(self):
        unpaid = self._withdrawal(status=S.pending_user_transfer_start, paid_in=False)
        paid = self._withdrawal()
        self.assertFalse(operations.reject(unpaid, self.user, reason="other", note="x").funds_received)
        self.assertTrue(operations.reject(paid, self.user, reason="other", note="x").funds_received)

    def test_withdrawal_already_paid_by_bank_cannot_be_rejected(self):
        meta = self._withdrawal()
        Sep24WithdrawMeta.objects.filter(pk=meta.pk).update(fiat_sent_at=timezone.now())
        with self.assertRaises(operations.OperationError):
            operations.reject(meta, self.user, reason="other", note="x")

    def test_rejecting_closes_an_active_hold_and_frees_the_wallet_of_the_operation(self):
        meta = self._deposit()
        operations.hold(meta, self.user, note="revisar")
        operations.reject(meta, self.user, reason="compliance")
        self.assertFalse(OperationHold.objects.filter(released_at__isnull=True).exists())
        meta.refresh_from_db()
        self.assertEqual(meta.transaction.status, S.error)


class HoldTests(OperationTestCase):
    def test_hold_does_not_change_the_polaris_status(self):
        meta = self._withdrawal()
        operations.hold(meta, self.user, note="revisar")
        meta.refresh_from_db()
        self.assertEqual(meta.transaction.status, S.pending_anchor)
        self.assertEqual(withdraw_stage(meta), Stage.HELD)

    def test_hold_requires_a_reason(self):
        with self.assertRaises(operations.OperationError):
            operations.hold(self._deposit(), self.user, note="  ")

    def test_deposit_worker_skips_a_held_deposit(self):
        meta = self._deposit(confirmed=True)
        OperationHold.objects.create(transaction=meta.transaction, note="x", placed_by=self.user)
        pending = Transaction.objects.filter(pk=meta.transaction_id)
        self.assertEqual(VersoRailsIntegration().poll_pending_deposits(pending), [])
        OperationHold.objects.update(released_at=timezone.now())
        pending = Transaction.objects.filter(pk=meta.transaction_id)
        self.assertEqual(len(VersoRailsIntegration().poll_pending_deposits(pending)), 1)

    def test_confirming_a_held_deposit_releases_it_and_records_who(self):
        meta = self._deposit()
        hold = operations.hold(meta, self.user, note="revisar")
        operations.confirm_deposit(meta, self.user)
        hold.refresh_from_db()
        meta.refresh_from_db()
        self.assertIsNotNone(meta.fiat_confirmed_at)
        self.assertIsNotNone(hold.released_at)
        self.assertEqual(hold.released_by, self.user)

    def test_paying_a_held_withdrawal_releases_it(self):
        meta = self._withdrawal()
        hold = operations.hold(meta, self.user, note="revisar")
        operations.pay_withdrawal(meta, self.user)
        hold.refresh_from_db()
        meta.refresh_from_db()
        self.assertEqual(meta.transaction.status, S.completed)
        self.assertIsNotNone(hold.released_at)

    def test_failed_payment_keeps_the_hold(self):
        meta = self._withdrawal(status=S.pending_user_transfer_start, paid_in=False)
        hold = operations.hold(meta, self.user, note="revisar")
        with self.assertRaises(operations.OperationError):
            operations.pay_withdrawal(meta, self.user)
        hold.refresh_from_db()
        self.assertIsNone(hold.released_at)


class RefundTests(OperationTestCase):
    def _rejected(self, meta):
        operations.reject(meta, self.user, reason="other", note="x", funds_received=True)
        return meta

    def test_refund_of_a_deposit_is_recorded(self):
        meta = self._rejected(self._deposit())
        operations.register_refund(meta, self.user, amount=Decimal("10"), reference="OP-123")
        meta.refresh_from_db()
        meta.transaction.refresh_from_db()
        self.assertEqual(deposit_stage(meta), Stage.REFUNDED)
        self.assertTrue(meta.transaction.refunded)
        self.assertEqual(meta.transaction.status, S.error)

    def test_withdrawal_refund_needs_the_network_hash(self):
        meta = self._rejected(self._withdrawal())
        with self.assertRaises(operations.OperationError):
            operations.register_refund(meta, self.user, amount=Decimal("3"), reference="OP-123")
        operations.register_refund(meta, self.user, amount=Decimal("3"), reference=HASH)
        meta.refresh_from_db()
        self.assertEqual(withdraw_stage(meta), Stage.REFUNDED)

    def test_refund_cannot_be_registered_twice_or_without_funds(self):
        meta = self._rejected(self._deposit())
        operations.register_refund(meta, self.user, amount=Decimal("10"), reference="OP-1")
        with self.assertRaises(operations.OperationError):
            operations.register_refund(meta, self.user, amount=Decimal("10"), reference="OP-2")
        unfunded = self._deposit()
        operations.reject(unfunded, self.user, reason="client_withdrew")
        with self.assertRaises(operations.OperationError):
            operations.register_refund(unfunded, self.user, amount=Decimal("1"), reference="OP-3")


class StageFilterTests(OperationTestCase):
    """The stage column and the filter must agree for the new stages too."""

    def test_deposit_and_withdrawal_filters_match_the_column(self):
        deposits = [self._deposit() for _ in range(4)]
        withdrawals = [self._withdrawal() for _ in range(4)]
        for metas in (deposits, withdrawals):
            operations.hold(metas[0], self.user, note="x")
            operations.reject(metas[1], self.user, reason="other", note="x", funds_received=False)
            operations.reject(metas[2], self.user, reason="other", note="x", funds_received=True)
        operations.register_refund(deposits[2], self.user, amount=Decimal("1"), reference="OP")
        operations.register_refund(withdrawals[2], self.user, amount=Decimal("1"), reference=HASH)

        for model, stage_of, stage_q, stages in (
            (Sep24DepositMeta, deposit_stage, deposit_stage_q, DEPOSIT_STAGES),
            (Sep24WithdrawMeta, withdraw_stage, withdraw_stage_q, WITHDRAW_STAGES),
        ):
            metas = list(model.objects.all())
            for stage in stages:
                with self.subTest(model=model.__name__, stage=stage):
                    filtered = set(model.objects.filter(stage_q(stage)).values_list("pk", flat=True))
                    self.assertEqual(filtered, {m.pk for m in metas if stage_of(m) == stage})


class ClientMessageTests(OperationTestCase):
    def test_client_never_sees_the_internal_reason(self):
        meta = self._deposit()
        operations.hold(meta, self.user, note="sospecha de fraude")
        self.assertEqual(operations.customer_message(meta.transaction), operations.HOLD_MESSAGE)
        operations.reject(meta, self.user, reason="compliance", note="sospecha de fraude")
        tx = Transaction.objects.get(pk=meta.transaction_id)
        self.assertEqual(operations.customer_message(tx), operations.REJECTED_MESSAGE)

    def test_poll_returns_the_message_without_the_note(self):
        meta = self._withdrawal()
        operations.hold(meta, self.user, note="sospecha de fraude")
        with override_settings(ROOT_URLCONF="config.urls"):
            data = self.client.get(
                "/sep24/transaction/poll/", {"id": str(meta.transaction_id)}
            ).json()
        self.assertEqual(data["message"], operations.HOLD_MESSAGE)
        self.assertNotIn("sospecha", str(data))


@override_settings(
    ROOT_URLCONF="config.urls",
    STORAGES={"staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}},
)
class AdminButtonTests(OperationTestCase):
    def _url(self, meta, action):
        return (
            f"/admin/verso_integrations/{meta._meta.model_name}/{meta.pk}/operacion/{action}/"
        )

    def test_list_shows_the_buttons_of_each_kind_and_no_delete(self):
        deposit = self._deposit()
        withdrawal = self._withdrawal()
        page = self.client.get("/admin/verso_integrations/sep24depositmeta/")
        self.assertContains(page, "<select", count=1)
        self.assertContains(page, "Elegir acción…")
        self.assertContains(page, "Confirmar y enviar")
        self.assertNotContains(page, "Marcar pagado")
        self.assertContains(page, self._url(deposit, "reject"))
        self.assertNotContains(page, "delete_selected")
        page = self.client.get("/admin/verso_integrations/sep24withdrawmeta/")
        self.assertContains(page, "Marcar pagado")
        self.assertNotContains(page, "Confirmar y enviar")
        self.assertContains(page, self._url(withdrawal, "pay"))

    def test_delete_is_refused(self):
        meta = self._deposit()
        response = self.client.post(
            f"/admin/verso_integrations/sep24depositmeta/{meta.pk}/delete/", {"post": "yes"}
        )
        self.assertEqual(response.status_code, 403)
        self.assertTrue(Sep24DepositMeta.objects.filter(pk=meta.pk).exists())

    def test_get_only_shows_the_form_and_changes_nothing(self):
        meta = self._deposit()
        self.assertContains(self.client.get(self._url(meta, "confirm")), "Confirmar y enviar")
        meta.refresh_from_db()
        self.assertIsNone(meta.fiat_confirmed_at)

    def test_reject_through_the_form_records_who_and_why(self):
        meta = self._deposit()
        response = self.client.post(
            self._url(meta, "reject"), {"reason": "compliance", "note": "revisar", "funds_received": "on"}
        )
        self.assertEqual(response.status_code, 302)
        rejection = OperationRejection.objects.get(transaction=meta.transaction)
        self.assertEqual(rejection.rejected_by, self.user)
        self.assertTrue(rejection.funds_received)

    def test_reject_other_requires_a_note(self):
        meta = self._deposit()
        response = self.client.post(self._url(meta, "reject"), {"reason": "other", "note": ""})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(OperationRejection.objects.exists())

    def test_withdrawal_form_does_not_ask_for_funds(self):
        page = self.client.get(self._url(self._withdrawal(), "reject"))
        self.assertNotContains(page, "funds_received")

    def test_action_of_the_other_kind_is_refused(self):
        withdrawal = self._withdrawal()
        response = self.client.post(self._url(withdrawal, "confirm"))
        self.assertEqual(response.status_code, 302)
        withdrawal.refresh_from_db()
        self.assertEqual(withdrawal.transaction.status, S.pending_anchor)

    def test_staff_without_change_permission_is_refused(self):
        meta = self._deposit()
        staff = get_user_model().objects.create_user("view", "v@example.com", "pw", is_staff=True)
        self.client.force_login(staff)
        response = self.client.post(self._url(meta, "confirm"))
        self.assertEqual(response.status_code, 403)
        meta.refresh_from_db()
        self.assertIsNone(meta.fiat_confirmed_at)

    def test_note_is_escaped_in_the_history(self):
        meta = self._deposit()
        operations.hold(meta, self.user, note="<script>x</script>")
        page = self.client.get(f"/admin/verso_integrations/sep24depositmeta/{meta.pk}/change/")
        self.assertNotContains(page, "<script>x</script>")
        self.assertContains(page, "&lt;script&gt;x&lt;/script&gt;")

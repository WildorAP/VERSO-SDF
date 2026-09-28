from decimal import Decimal

from django.test import SimpleTestCase

from reconciliation.events import decode_event
from reconciliation.tests.factories import memo_hash_for_uuid, transfer_event


class DecodeEventTests(SimpleTestCase):
    HOT = "GBTV5QYBPGHGT2SVUHCFRRKFFWUWHOEPKH7QAXGTJHFGFYZRIE24UOPB"
    CLIENT = "GCXGLWL7GEPUDCCZABQVLHTZLDWWXPTURGXODJ6JF6BVJSO4KWU45IFG"

    def test_transfer_inbound_with_map_memo(self):
        import uuid

        tx_id = uuid.UUID("1a290a10-a4e1-48da-ac6c-75ca07c1ee3f")
        memo_bytes = bytes.fromhex(tx_id.hex.zfill(64))
        event = transfer_event(
            from_account=self.CLIENT,
            to_account=self.HOT,
            amount_stroops=100_000_000,
            muxed_bytes=memo_bytes,
        )
        movement = decode_event(event, self.HOT)
        self.assertIsNotNone(movement)
        self.assertEqual(movement.amount, Decimal("10.0000000"))
        self.assertEqual(movement.memo_type, "hash")
        self.assertEqual(movement.memo_raw, memo_hash_for_uuid(tx_id))

    def test_transfer_outbound_plain_amount(self):
        event = transfer_event(
            from_account=self.HOT,
            to_account=self.CLIENT,
            amount_stroops=50_000_000,
        )
        movement = decode_event(event, self.HOT)
        self.assertIsNotNone(movement)
        self.assertEqual(movement.amount, Decimal("5.0000000"))

    def test_ignores_unsuccessful_call(self):
        event = transfer_event(
            from_account=self.CLIENT,
            to_account=self.HOT,
            amount_stroops=1,
        )
        event.in_successful_contract_call = False
        self.assertIsNone(decode_event(event, self.HOT))

    def test_ignores_unrelated_accounts(self):
        event = transfer_event(
            from_account=self.CLIENT,
            to_account="GAYF33NNNMI2Z6VNRFXQ64D4E4SF77PM46NW3ZUZEEU5X7FCHAZCMHKU",
            amount_stroops=1,
        )
        self.assertIsNone(decode_event(event, self.HOT))

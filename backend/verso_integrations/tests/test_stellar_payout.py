from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.test import TestCase
from stellar_sdk.exceptions import BadRequestError, NotFoundError

from verso_integrations.stellar_payout import StellarPayoutError, disburse_usdc


class DisburseUsdcErrorTests(TestCase):
    def test_raises_when_amount_is_zero(self):
        with self.assertRaises(StellarPayoutError):
            disburse_usdc("GBTV5QYBPGHGT2SVUHCFRRKFFWUWHOEPKH7QAXGTJHFGFYZRIE24UOPB", Decimal("0"))

    def test_raises_when_amount_is_negative(self):
        with self.assertRaises(StellarPayoutError):
            disburse_usdc("GBTV5QYBPGHGT2SVUHCFRRKFFWUWHOEPKH7QAXGTJHFGFYZRIE24UOPB", Decimal("-5"))

    @patch.dict("os.environ", {"SIGNING_SEED": ""})
    def test_raises_when_signing_seed_is_blank(self):
        with self.assertRaises(StellarPayoutError):
            disburse_usdc("GBTV5QYBPGHGT2SVUHCFRRKFFWUWHOEPKH7QAXGTJHFGFYZRIE24UOPB", Decimal("10"))

    @patch.dict("os.environ", {"SIGNING_SEED": "not-a-valid-key"})
    def test_raises_when_signing_seed_is_invalid(self):
        with self.assertRaises(StellarPayoutError):
            disburse_usdc("GBTV5QYBPGHGT2SVUHCFRRKFFWUWHOEPKH7QAXGTJHFGFYZRIE24UOPB", Decimal("10"))

    @patch("verso_integrations.stellar_payout.Server")
    def test_raises_when_anchor_account_not_found(self, mock_server_cls):
        mock_server = MagicMock()
        mock_server.load_account.side_effect = NotFoundError(
            response=MagicMock(status_code=404, text="", headers={})
        )
        mock_server_cls.return_value = mock_server

        with self.assertRaises(StellarPayoutError):
            disburse_usdc("GBTV5QYBPGHGT2SVUHCFRRKFFWUWHOEPKH7QAXGTJHFGFYZRIE24UOPB", Decimal("10"))

    @patch("verso_integrations.stellar_payout.Server")
    def test_raises_when_network_rejects_payment(self, mock_server_cls):
        mock_server = MagicMock()
        mock_server.load_account.return_value = MagicMock()
        mock_server.fetch_base_fee.return_value = 100
        mock_server.submit_transaction.side_effect = BadRequestError(
            response=MagicMock(status_code=400, text="", headers={})
        )
        mock_server_cls.return_value = mock_server

        with self.assertRaises(StellarPayoutError):
            disburse_usdc("GBTV5QYBPGHGT2SVUHCFRRKFFWUWHOEPKH7QAXGTJHFGFYZRIE24UOPB", Decimal("10"))
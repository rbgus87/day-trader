"""tests/test_base_strategy.py"""

import pytest
from datetime import time
from unittest.mock import patch

from strategy.base_strategy import BaseStrategy, Signal


def test_signal_dataclass():
    sig = Signal(ticker="005930", side="buy", price=70000, strategy="orb", reason="돌파")
    assert sig.side == "buy"
    assert sig.qty is None


def test_cannot_instantiate_base():
    with pytest.raises(TypeError):
        BaseStrategy()


def test_is_tradable_time_blocks_before_0905():
    class DummyStrategy(BaseStrategy):
        def generate_signal(self, candles, tick): return None
        def get_stop_loss(self, entry_price): return 0
        def get_take_profit(self, entry_price): return 0

    s = DummyStrategy()
    with patch("strategy.base_strategy.datetime") as mock_dt:
        mock_dt.now.return_value.time.return_value = time(9, 3)
        assert s.is_tradable_time() is False

    with patch("strategy.base_strategy.datetime") as mock_dt:
        mock_dt.now.return_value.time.return_value = time(9, 6)
        assert s.is_tradable_time() is True

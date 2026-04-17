"""tests/test_pipeline.py"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from data.candle_builder import CandleBuilder
from risk.risk_manager import RiskManager
from config.settings import TradingConfig


@pytest.mark.asyncio
async def test_pipeline_tick_to_candle():
    """틱 → 캔들빌더 → 캔들 Queue 전달 확인."""
    candle_queue = asyncio.Queue()
    builder = CandleBuilder(candle_queue=candle_queue)

    ticks = [
        {"ticker": "005930", "time": "090500", "price": 70000, "volume": 100, "cum_volume": 100},
        {"ticker": "005930", "time": "090600", "price": 70500, "volume": 200, "cum_volume": 300},
    ]

    for t in ticks:
        await builder.on_tick(t)

    candle = await asyncio.wait_for(candle_queue.get(), timeout=1.0)
    assert candle["ticker"] == "005930"
    assert candle["tf"] == "1m"


@pytest.mark.asyncio
async def test_pipeline_candle_to_strategy():
    """캔들 → 전략 엔진 신호 생성 확인 (Momentum)."""
    import pandas as pd
    from strategy.momentum_strategy import MomentumStrategy
    from config.settings import TradingConfig
    from datetime import time

    strat = MomentumStrategy(TradingConfig(adx_enabled=False, rvol_enabled=False, vwap_enabled=False, min_breakout_pct=0.0))
    strat.set_prev_day_data(high=70000, volume=100_000)
    strat.configure_multi_trade(max_trades=5, cooldown_minutes=0)
    strat.set_backtest_time(time(10, 0))

    candles = pd.DataFrame({
        "close": [70100, 70200, 70300],
        "high": [70150, 70250, 70350],
        "low": [70050, 70150, 70250],
        "volume": [80_000, 70_000, 60_000],
    })
    tick = {"ticker": "005930", "price": 70300, "time": "100000", "volume": 500}

    signal = strat.generate_signal(candles, tick)
    assert signal is not None
    assert signal.strategy == "momentum"


# ---------------------------------------------------------------------------
# 포지션 모니터링 통합 테스트
# ---------------------------------------------------------------------------

@pytest.fixture
def risk_manager():
    config = TradingConfig()
    rm = RiskManager(trading_config=config, db=MagicMock(), notifier=MagicMock())
    rm.set_daily_capital(1_000_000)
    return rm


@pytest.fixture
def order_manager():
    om = MagicMock()
    om.execute_sell_stop = AsyncMock(return_value={"order_no": "S001", "qty": 100})
    om.execute_sell_tp1 = AsyncMock(return_value={"order_no": "S002", "qty": 50})
    return om


@pytest.mark.asyncio
async def test_position_monitor_stop_loss(risk_manager, order_manager):
    """틱 가격이 손절가 이하 → execute_sell_stop 호출."""
    risk_manager.register_position(
        ticker="005930", entry_price=70000, qty=100,
        stop_loss=68950, tp1_price=72100,
    )
    # 손절 가격 도달
    tick = {"ticker": "005930", "price": 68900, "time": "100000", "volume": 50}
    pos = risk_manager.get_position("005930")
    assert pos is not None
    assert risk_manager.check_stop_loss("005930", tick["price"])

    # 시뮬레이션: tick_consumer 내부 로직 재현
    qty = pos["remaining_qty"]
    await order_manager.execute_sell_stop(ticker="005930", qty=qty)
    pnl = (tick["price"] - pos["entry_price"]) * qty
    risk_manager.record_pnl(pnl)
    risk_manager.remove_position("005930")

    order_manager.execute_sell_stop.assert_called_once_with(ticker="005930", qty=100)
    assert risk_manager.get_position("005930") is None
    assert risk_manager._daily_pnl < 0


@pytest.mark.asyncio
async def test_position_monitor_tp1(risk_manager, order_manager):
    """틱 가격이 TP1 이상 → execute_sell_tp1 호출 + mark_tp1_hit."""
    risk_manager.register_position(
        ticker="005930", entry_price=70000, qty=100,
        stop_loss=68950, tp1_price=72100,
    )
    tick = {"ticker": "005930", "price": 72200, "time": "100000", "volume": 50}
    pos = risk_manager.get_position("005930")
    assert risk_manager.check_tp1("005930", tick["price"])

    sell_qty = int(pos["remaining_qty"] * TradingConfig().tp1_sell_ratio)
    await order_manager.execute_sell_tp1(ticker="005930", price=72200, remaining_qty=pos["remaining_qty"])
    pnl = (tick["price"] - pos["entry_price"]) * sell_qty
    risk_manager.record_pnl(pnl)
    risk_manager.mark_tp1_hit("005930", sell_qty)

    order_manager.execute_sell_tp1.assert_called_once()
    pos_after = risk_manager.get_position("005930")
    assert pos_after["tp1_hit"] is True
    assert pos_after["remaining_qty"] == 50
    assert pos_after["stop_loss"] == 70000  # 본전으로 이동


@pytest.mark.asyncio
async def test_position_monitor_trailing_stop(risk_manager):
    """틱 가격이 고점 갱신 → trailing_stop 갱신 (고정 경로).

    Phase 2 Day 7: 가짜 ticker로 ATR 조회 None → 폴백(고정 trailing_pct).
    ATR 트레일링 자체는 tests/test_atr_stop.py에서 별도 검증.
    """
    risk_manager.register_position(
        ticker="TEST001", entry_price=70000, qty=100,
        stop_loss=68950, tp1_price=72100,
    )
    # TP1 히트 시뮬레이션
    risk_manager.mark_tp1_hit("TEST001", 50)
    pos = risk_manager.get_position("TEST001")
    assert pos["tp1_hit"] is True

    # 고점 갱신
    risk_manager.update_trailing_stop("TEST001", 73000)
    pos = risk_manager.get_position("TEST001")
    assert pos["highest_price"] == 73000
    assert pos["stop_loss"] == 73000 * (1 - TradingConfig().trailing_stop_pct)


@pytest.mark.asyncio
async def test_position_monitor_daily_loss_halt(risk_manager):
    """일일 손실 한도 도달 → is_trading_halted() True."""
    risk_manager.set_daily_capital(1_000_000)
    # 일일 최대 손실 -2% = -20,000
    risk_manager.record_pnl(-20_001)
    assert risk_manager.is_trading_halted() is True

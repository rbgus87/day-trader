"""tests/test_backtester.py — Backtester 단위/통합 테스트."""

import math
from unittest.mock import MagicMock

import pandas as pd
import pytest
import pytest_asyncio

from backtest.backtester import (
    Backtester,
    ENTRY_FEE_RATE,
    EXIT_FEE_RATE,
    SELL_TAX_RATE,
    SLIPPAGE_RATE,
)
from config.settings import TradingConfig
from data.db_manager import DbManager
from strategy.base_strategy import BaseStrategy, Signal


# ---------------------------------------------------------------------------
# 공통 fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def trading_config() -> TradingConfig:
    return TradingConfig()


@pytest_asyncio.fixture
async def db() -> DbManager:
    """인메모리 DB — 각 테스트마다 새로 생성."""
    manager = DbManager(":memory:")
    await manager.init()
    yield manager
    await manager.close()


@pytest_asyncio.fixture
async def backtester(db: DbManager, trading_config: TradingConfig) -> Backtester:
    return Backtester(db=db, config=trading_config)


# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------

async def _insert_candle(db: DbManager, ticker: str, ts: str, o: float, h: float,
                          lo: float, c: float, vol: int, vwap: float) -> None:
    await db.execute(
        "INSERT INTO intraday_candles (ticker,tf,ts,open,high,low,close,volume,vwap) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (ticker, "1m", ts, o, h, lo, c, vol, vwap),
    )


def _make_candles(rows: list[dict]) -> pd.DataFrame:
    """dict 리스트 → DataFrame (ts를 datetime으로 변환)."""
    df = pd.DataFrame(rows)
    df["ts"] = pd.to_datetime(df["ts"])
    return df


# ---------------------------------------------------------------------------
# 테스트 1: load_candles — DB 데이터 조회 및 DataFrame 반환
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_load_candles(db: DbManager, backtester: Backtester):
    """intraday_candles 에 삽입한 데이터가 DataFrame으로 올바르게 반환된다."""
    await _insert_candle(db, "005930", "2026-03-23T09:01:00", 70000, 70500, 69800, 70200, 1000, 70100)
    await _insert_candle(db, "005930", "2026-03-23T09:02:00", 70200, 70800, 70100, 70600, 1500, 70350)
    await _insert_candle(db, "005930", "2026-03-23T09:03:00", 70600, 71000, 70500, 70900, 2000, 70700)
    # 다른 종목 (조회 대상 아님)
    await _insert_candle(db, "000660", "2026-03-23T09:01:00", 100000, 101000, 99500, 100500, 500, 100200)

    df = await backtester.load_candles("005930", "2026-03-23", "2026-03-24")

    assert isinstance(df, pd.DataFrame), "반환 타입은 DataFrame이어야 한다"
    assert len(df) == 3, "005930 종목 캔들 3개가 반환되어야 한다"
    assert list(df.columns) == ["ts", "open", "high", "low", "close", "volume", "vwap", "time"]
    assert df.iloc[0]["close"] == 70200
    assert df.iloc[2]["close"] == 70900
    # ts 컬럼이 datetime 타입인지 확인
    assert pd.api.types.is_datetime64_any_dtype(df["ts"])


@pytest.mark.asyncio
async def test_load_candles_empty(db: DbManager, backtester: Backtester):
    """일치하는 데이터 없으면 빈 DataFrame 반환."""
    df = await backtester.load_candles("999999", "2026-01-01", "2026-01-02")
    assert df.empty
    assert "close" in df.columns


@pytest.mark.asyncio
async def test_load_candles_only_1m_tf(db: DbManager, backtester: Backtester):
    """tf='1m' 인 캔들만 조회된다."""
    await _insert_candle(db, "005930", "2026-03-23T09:01:00", 70000, 70500, 69800, 70200, 1000, 70100)
    # tf='5m' 직접 삽입
    await db.execute(
        "INSERT INTO intraday_candles (ticker,tf,ts,open,high,low,close,volume,vwap) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        ("005930", "5m", "2026-03-23T09:05:00", 71000, 72000, 70800, 71500, 5000, 71200),
    )

    df = await backtester.load_candles("005930", "2026-03-23", "2026-03-24")
    assert len(df) == 1, "tf='1m' 캔들만 1개 반환되어야 한다"


# ---------------------------------------------------------------------------
# 테스트 2: calculate_kpi — KPI 계산 검증
# ---------------------------------------------------------------------------

def test_calculate_kpi_empty():
    """거래 없을 때 KPI 기본값."""
    bt = Backtester(db=MagicMock(), config=TradingConfig())
    kpi = bt.calculate_kpi([])

    assert kpi["total_trades"] == 0
    assert kpi["wins"] == 0
    assert kpi["win_rate"] == 0.0
    assert kpi["profit_factor"] == 0.0
    assert kpi["total_pnl"] == 0.0
    assert kpi["max_drawdown"] == 0.0
    assert kpi["sharpe_ratio"] == 0.0


def test_calculate_kpi_all_wins():
    """3 trades, 모두 이익일 때."""
    bt = Backtester(db=MagicMock(), config=TradingConfig())
    trades = [
        {"pnl": 1000.0, "pnl_pct": 0.02},
        {"pnl": 500.0,  "pnl_pct": 0.01},
        {"pnl": 1500.0, "pnl_pct": 0.03},
    ]
    kpi = bt.calculate_kpi(trades)

    assert kpi["total_trades"] == 3
    assert kpi["wins"] == 3
    assert kpi["win_rate"] == pytest.approx(1.0)
    assert kpi["profit_factor"] == float("inf")
    assert kpi["total_pnl"] == pytest.approx(3000.0)
    assert kpi["max_drawdown"] == pytest.approx(0.0)


def test_calculate_kpi_mixed():
    """이익/손실 혼합 거래."""
    bt = Backtester(db=MagicMock(), config=TradingConfig())
    trades = [
        {"pnl": 2000.0,  "pnl_pct": 0.02},
        {"pnl": -500.0,  "pnl_pct": -0.005},
        {"pnl": 1000.0,  "pnl_pct": 0.01},
        {"pnl": -1000.0, "pnl_pct": -0.01},
        {"pnl": 500.0,   "pnl_pct": 0.005},
    ]
    kpi = bt.calculate_kpi(trades)

    assert kpi["total_trades"] == 5
    assert kpi["wins"] == 3
    assert kpi["win_rate"] == pytest.approx(0.6)
    # profit_factor = (2000+1000+500) / (500+1000) = 3500/1500
    assert kpi["profit_factor"] == pytest.approx(3500 / 1500, rel=1e-4)
    assert kpi["total_pnl"] == pytest.approx(2000)
    # max drawdown: 누적 [2000, 1500, 2500, 1500, 2000] → peak 2500 trough 1500 → dd=1000
    assert kpi["max_drawdown"] == pytest.approx(1000.0)
    # sharpe_ratio: non-zero
    assert kpi["sharpe_ratio"] != 0.0


def test_calculate_kpi_all_losses():
    """전부 손실일 때."""
    bt = Backtester(db=MagicMock(), config=TradingConfig())
    trades = [
        {"pnl": -300.0, "pnl_pct": -0.003},
        {"pnl": -500.0, "pnl_pct": -0.005},
    ]
    kpi = bt.calculate_kpi(trades)

    assert kpi["wins"] == 0
    assert kpi["win_rate"] == pytest.approx(0.0)
    assert kpi["profit_factor"] == pytest.approx(0.0)
    assert kpi["total_pnl"] == pytest.approx(-800.0)
    assert kpi["max_drawdown"] == pytest.approx(800.0)


def test_calculate_kpi_sharpe_single_trade():
    """거래 1건이면 sharpe=0.0 반환."""
    bt = Backtester(db=MagicMock(), config=TradingConfig())
    kpi = bt.calculate_kpi([{"pnl": 1000.0, "pnl_pct": 0.01}])
    assert kpi["sharpe_ratio"] == 0.0


def test_calculate_kpi_max_drawdown_accuracy():
    """낙폭 계산 정확도 검증."""
    # 누적: 100, 200, 100, 50, 150 → peak=200, trough=50 → dd=150
    bt = Backtester(db=MagicMock(), config=TradingConfig())
    trades = [
        {"pnl": 100.0, "pnl_pct": 0.01},
        {"pnl": 100.0, "pnl_pct": 0.01},
        {"pnl": -100.0, "pnl_pct": -0.01},
        {"pnl": -50.0, "pnl_pct": -0.005},
        {"pnl": 100.0, "pnl_pct": 0.01},
    ]
    kpi = bt.calculate_kpi(trades)
    assert kpi["max_drawdown"] == pytest.approx(150.0)


# ---------------------------------------------------------------------------
# 테스트 3: run_backtest_basic — 최소 캔들 + mock 전략으로 거래 생성 확인
# ---------------------------------------------------------------------------

class _MockBuyStrategy(BaseStrategy):
    """첫 번째 캔들에서 항상 매수 신호를 발생하는 목(mock) 전략."""

    def __init__(self, config: TradingConfig):
        self._config = config
        self._fired = False

    def generate_signal(self, candles: pd.DataFrame, tick: dict) -> Signal | None:
        if not self._fired:
            self._fired = True
            return Signal(
                ticker=tick["ticker"],
                side="buy",
                price=tick["price"],
                strategy="mock",
                reason="test",
            )
        return None

    def get_stop_loss(self, entry_price: float) -> float:
        return entry_price * 0.985  # -1.5%

    def get_take_profit(self, entry_price: float) -> tuple[float, float]:
        return entry_price * 1.02, entry_price * 1.04  # +2%, +4%


def test_run_backtest_basic():
    """TP1 도달로 분할매도되는 기본 시나리오."""
    config = TradingConfig()
    bt = Backtester(db=MagicMock(), config=config)
    strategy = _MockBuyStrategy(config)

    # 첫 캔들 매수 → 3번째 캔들 TP1(+2%) 도달 → 분할매도
    entry_close = 100_000.0

    candle_rows = [
        {"ts": "2026-03-23T09:01:00", "open": 99_500, "high": 100_200, "low": 99_200, "close": entry_close, "volume": 1000, "vwap": 99_800},
        {"ts": "2026-03-23T09:02:00", "open": 100_000, "high": 100_800, "low": 99_800, "close": 100_500, "volume": 1200, "vwap": 100_200},
        {"ts": "2026-03-23T09:03:00", "open": 100_500, "high": 102_500, "low": 100_300, "close": 102_000, "volume": 1500, "vwap": 101_000},
    ]
    candles = _make_candles(candle_rows)

    result = bt.run_backtest(candles, strategy)

    assert "trades" in result
    # TP1 분할매도(50%) + 나머지 강제청산 = 2건
    assert len(result["trades"]) == 2, "TP1 분할매도 + 나머지 청산 = 2건"

    tp1_trade = result["trades"][0]
    assert tp1_trade["exit_reason"] == "tp1"
    assert tp1_trade["entry_price"] > 0
    assert tp1_trade["exit_price"] > 0
    assert tp1_trade["pnl"] > 0

    remaining_trade = result["trades"][1]
    assert remaining_trade["exit_reason"] == "forced_close"

    assert result["total_trades"] == 2
    assert result["wins"] >= 1


def test_run_backtest_stop_loss():
    """손절 시나리오 — 저가가 손절가 이하 돌파."""
    config = TradingConfig()
    bt = Backtester(db=MagicMock(), config=config)
    strategy = _MockBuyStrategy(config)

    entry_close = 100_000.0
    sl = entry_close * 0.985  # 98,500

    candle_rows = [
        {"ts": "2026-03-23T09:01:00", "open": 99_500, "high": 100_200, "low": 99_200, "close": entry_close, "volume": 1000, "vwap": 99_800},
        # 2번째 캔들 저가 98,000 → 손절 트리거
        {"ts": "2026-03-23T09:02:00", "open": 99_000, "high": 99_500, "low": 97_000, "close": 97_500, "volume": 2000, "vwap": 98_500},
    ]
    candles = _make_candles(candle_rows)

    result = bt.run_backtest(candles, strategy)

    assert len(result["trades"]) == 1
    trade = result["trades"][0]
    assert trade["exit_reason"] == "stop_loss"
    assert trade["pnl"] < 0, "손절이므로 손실이어야 한다"


def test_run_backtest_forced_close():
    """마지막 캔들 강제 청산 시나리오."""
    config = TradingConfig()
    bt = Backtester(db=MagicMock(), config=config)
    strategy = _MockBuyStrategy(config)

    # 손절/TP 미달 캔들만 — 마지막 캔들에서 강제 청산
    entry_close = 100_000.0
    candle_rows = [
        {"ts": "2026-03-23T09:01:00", "open": 99_500, "high": 100_200, "low": 99_500, "close": entry_close, "volume": 1000, "vwap": 99_800},
        {"ts": "2026-03-23T09:02:00", "open": 100_000, "high": 100_500, "low": 99_800, "close": 100_200, "volume": 1000, "vwap": 100_100},
        {"ts": "2026-03-23T09:03:00", "open": 100_200, "high": 100_600, "low": 99_900, "close": 100_300, "volume": 1000, "vwap": 100_200},
    ]
    candles = _make_candles(candle_rows)

    result = bt.run_backtest(candles, strategy)

    assert len(result["trades"]) == 1
    trade = result["trades"][0]
    assert trade["exit_reason"] == "forced_close"


def test_run_backtest_empty_candles():
    """빈 캔들 데이터 → 거래 없음, KPI 기본값."""
    config = TradingConfig()
    bt = Backtester(db=MagicMock(), config=config)
    strategy = _MockBuyStrategy(config)

    empty_df = pd.DataFrame(
        columns=["ts", "open", "high", "low", "close", "volume", "vwap"]
    )
    result = bt.run_backtest(empty_df, strategy)

    assert result["total_trades"] == 0
    assert result["trades"] == []


def test_run_backtest_no_signal():
    """신호 없는 전략 → 거래 없음."""

    class _NoSignalStrategy(BaseStrategy):
        def generate_signal(self, candles, tick) -> None:
            return None

        def get_stop_loss(self, entry_price: float) -> float:
            return entry_price * 0.985

        def get_take_profit(self, entry_price: float) -> tuple[float, float]:
            return entry_price * 1.02, entry_price * 1.04

    config = TradingConfig()
    bt = Backtester(db=MagicMock(), config=config)

    candle_rows = [
        {"ts": "2026-03-23T09:01:00", "open": 100_000, "high": 100_500, "low": 99_500, "close": 100_200, "volume": 1000, "vwap": 100_100},
        {"ts": "2026-03-23T09:02:00", "open": 100_200, "high": 100_800, "low": 100_000, "close": 100_500, "volume": 1000, "vwap": 100_300},
    ]
    candles = _make_candles(candle_rows)
    result = bt.run_backtest(candles, _NoSignalStrategy())

    assert result["total_trades"] == 0
    assert result["trades"] == []


def test_fee_and_slippage_reduce_pnl():
    """수수료 + 슬리피지가 PnL을 감소시키는지 검증."""
    config = TradingConfig()
    bt = Backtester(db=MagicMock(), config=config)
    strategy = _MockBuyStrategy(config)

    entry_close = 100_000.0
    candle_rows = [
        {"ts": "2026-03-23T09:01:00", "open": 99_500, "high": 100_200, "low": 99_500, "close": entry_close, "volume": 1000, "vwap": 99_800},
        {"ts": "2026-03-23T09:02:00", "open": 100_000, "high": 103_000, "low": 99_800, "close": 102_500, "volume": 1500, "vwap": 101_000},
    ]
    candles = _make_candles(candle_rows)
    result = bt.run_backtest(candles, strategy)

    # TP1 분할매도(50%) trade
    tp1_trade = result["trades"][0]
    assert tp1_trade["exit_reason"] == "tp1"
    # 수수료/슬리피지 없이 계산한 raw 수익 (50% 비율)
    raw_gain_50pct = (entry_close * 1.02 - entry_close) * 0.5  # 1,000
    # 실제 PnL은 raw보다 작아야 함 (수수료 차감)
    assert tp1_trade["pnl"] < raw_gain_50pct

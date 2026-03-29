"""tests/test_vwap_strategy.py — DEPRECATED: VWAP 전략 폐기."""

import pytest

pytestmark = pytest.mark.skip("VWAP strategy deprecated — 백테스트 PF<1.0")
import pandas as pd
import numpy as np
from unittest.mock import patch

from config.settings import TradingConfig
from strategy.vwap_strategy import VwapStrategy, _calc_vwap_and_std, _calc_rsi


# ---------------------------------------------------------------------------
# 픽스처 헬퍼
# ---------------------------------------------------------------------------

def _make_config(**overrides) -> TradingConfig:
    """TradingConfig 기본값 반환 (오버라이드 가능)."""
    # TradingConfig는 frozen dataclass → 필드별 오버라이드 불가
    # 테스트에서는 기본값 그대로 사용
    return TradingConfig()


def _make_candles(
    closes: list[float],
    highs: list[float] | None = None,
    lows: list[float] | None = None,
    volumes: list[int] | None = None,
) -> pd.DataFrame:
    """테스트용 분봉 DataFrame 생성."""
    n = len(closes)
    if highs is None:
        highs = [c * 1.005 for c in closes]
    if lows is None:
        lows = [c * 0.995 for c in closes]
    if volumes is None:
        volumes = [1_000_000] * n

    return pd.DataFrame(
        {
            "open": closes,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": volumes,
        }
    )


def _make_tick(ticker: str = "005930", price: float = 70_000) -> dict:
    return {"ticker": ticker, "price": price}


# ---------------------------------------------------------------------------
# 1. 신호 없음: 가격이 VWAP 위에서만 움직임
# ---------------------------------------------------------------------------

class TestNoSignalWithoutVwapTouch:
    def test_no_signal_without_vwap_touch(self):
        """가격이 항상 VWAP 위에 있으면 신호가 생성되지 않아야 한다.

        VWAP 터치 감지 로직:
          typical_price = (high + low + close) / 3
          lower_band = VWAP - std(typical_prices)

        여기서 모든 캔들을 동일하게 설정하면:
          tp = (H + L + C) / 3, std = 0 → lower_band = VWAP = tp
          low = L = tp 이면 low <= lower_band 가 성립(터치로 간주)

        따라서 low > tp 가 되도록, 즉 low > (H + L + C) / 3 이 되도록 설정한다.
        예: H=L=C=100_000 → tp=100_000, low=100_000, lower_band=100_000
            → low <= lower_band 로 터치 처리됨 (의도치 않은 터치)

        올바른 설정: low를 tp보다 확실히 위로,
            즉 H > L 이면 tp < H 이므로 low를 tp보다 높게 만들 수 없음.

        가장 단순한 회피 방법:
            _touched_lower_band 상태를 패치하지 않고,
            실제로 저가가 lower_band 위에 있음을 수학적으로 보장한다.
            → tp = (H + L + C) / 3,  lower_band = tp - 0 (std=0)
              low = L = 일정 값
              lower_band = tp = (H + L + C)/3
              low > lower_band  ⟺  L > (H + L + C)/3
              ⟺  3L > H + L + C  ⟺  2L > H + C

            예: H=100, L=200, C=100 → tp=(400)/3≈133.3, low=200>133.3 ✓
            단, 현실적인 OHLC 에서 L은 H보다 낮으므로 이 조건은 비현실적.

        실용적 해결:
            변동성 있는 캔들 데이터에서 std > 0 이 되도록 만들고,
            모든 캔들의 저가를 lower_band(=vwap - std)보다 명확히 위로 유지.
        """
        config = _make_config()
        strategy = VwapStrategy(config)

        # 변동성 있는 캔들: std > 0 이 되도록 close에 변동 부여
        closes = [100_000.0 + i * 100 for i in range(20)]  # 100_000 ~ 101_900
        # VWAP 와 std 를 먼저 계산해서 lower_band 를 파악
        base_df = _make_candles(closes)
        vwap, std = _calc_vwap_and_std(base_df)
        lower_band = vwap - std

        # 모든 캔들의 저가를 lower_band 보다 확실히 위(+500)로 설정
        safe_low = lower_band + 500
        lows = [safe_low] * 20
        highs = [c + 200 for c in closes]
        candles = _make_candles(closes, highs=highs, lows=lows)

        # 실제로 저가가 lower_band 위에 있는지 확인
        actual_vwap, actual_std = _calc_vwap_and_std(candles)
        actual_lower_band = actual_vwap - actual_std
        assert all(lows[i] > actual_lower_band for i in range(20)), (
            f"테스트 설정 오류: 일부 low가 lower_band({actual_lower_band:.1f}) 이하입니다"
        )

        # is_tradable_time() → True 로 패치
        tick_price = actual_vwap + 100  # VWAP 위 틱
        with patch.object(strategy, "is_tradable_time", return_value=True):
            with patch("strategy.vwap_strategy._calc_rsi", return_value=50.0):
                signal = strategy.generate_signal(candles, _make_tick(price=tick_price))

        assert signal is None
        assert strategy._touched_lower_band is False


# ---------------------------------------------------------------------------
# 2. 정상 신호: VWAP-1σ 터치 후 반등 + RSI 범위 내
# ---------------------------------------------------------------------------

class TestSignalOnVwapBounce:
    def _build_strategy_and_candles_with_touch(self):
        """VWAP 하단 터치 상태를 만들기 위한 헬퍼."""
        config = _make_config()
        strategy = VwapStrategy(config)

        # 50개 캔들: 대부분 100_000, 마지막 캔들 low가 낮아 lower_band 이하 터치
        closes = [100_000.0] * 20
        # std를 일부러 크게 만들기 위해 변동성 부여
        closes_varied = [100_000 + (i % 5) * 200 for i in range(20)]

        vwap, std = _calc_vwap_and_std(_make_candles(closes_varied))
        lower_band = vwap - std

        # 마지막 캔들 low를 lower_band 이하로 설정
        lows = [c * 0.999 for c in closes_varied]
        lows[-1] = lower_band - 1  # 명확히 터치

        highs = [c * 1.001 for c in closes_varied]
        candles = _make_candles(closes_varied, highs=highs, lows=lows)

        return strategy, candles, vwap

    def test_signal_on_vwap_bounce(self):
        """VWAP-1σ 터치 후 VWAP 위로 반등 + RSI 40~60 → BUY 신호."""
        strategy, candles, vwap = self._build_strategy_and_candles_with_touch()

        # RSI가 40~60 이 되도록 RSI를 모킹
        tick = _make_tick(price=vwap + 100)  # VWAP 위로 반등

        with patch.object(strategy, "is_tradable_time", return_value=True):
            with patch("strategy.vwap_strategy._calc_rsi", return_value=50.0):
                signal = strategy.generate_signal(candles, tick)

        assert signal is not None
        assert signal.side == "buy"
        assert signal.strategy == "vwap"
        assert signal.ticker == "005930"

    def test_no_signal_while_in_position(self):
        """포지션 보유 중에는 추가 신호가 발생하지 않는다."""
        strategy, candles, vwap = self._build_strategy_and_candles_with_touch()
        tick = _make_tick(price=vwap + 100)

        with patch.object(strategy, "is_tradable_time", return_value=True):
            with patch("strategy.vwap_strategy._calc_rsi", return_value=50.0):
                signal1 = strategy.generate_signal(candles, tick)
                assert signal1 is not None
                strategy.on_entry()
                signal2 = strategy.generate_signal(candles, tick)
                assert signal2 is None

    def test_reset_clears_state(self):
        """reset() 후 상태가 초기화되어야 한다."""
        strategy, candles, vwap = self._build_strategy_and_candles_with_touch()
        tick = _make_tick(price=vwap + 100)

        with patch.object(strategy, "is_tradable_time", return_value=True):
            with patch("strategy.vwap_strategy._calc_rsi", return_value=50.0):
                strategy.generate_signal(candles, tick)
                strategy.on_entry()

        assert strategy._has_position is True

        strategy.reset()
        assert strategy._has_position is False
        assert strategy._touched_lower_band is False


# ---------------------------------------------------------------------------
# 3. 신호 없음: RSI 범위 벗어남
# ---------------------------------------------------------------------------

class TestNoSignalRsiOutOfRange:
    def _build_touched_strategy(self):
        config = _make_config()
        strategy = VwapStrategy(config)
        strategy._touched_lower_band = True  # 터치 상태 직접 설정
        return strategy

    def _make_simple_candles(self, vwap_approx: float = 100_000.0) -> pd.DataFrame:
        closes = [vwap_approx] * 20
        return _make_candles(closes)

    def test_no_signal_rsi_too_high(self):
        """RSI > 60이면 VWAP 반등이 있어도 신호 없음."""
        strategy = self._build_touched_strategy()
        candles = self._make_simple_candles()
        vwap, _ = _calc_vwap_and_std(candles)
        tick = _make_tick(price=vwap + 100)

        with patch.object(strategy, "is_tradable_time", return_value=True):
            with patch("strategy.vwap_strategy._calc_rsi", return_value=70.0):
                signal = strategy.generate_signal(candles, tick)

        assert signal is None

    def test_no_signal_rsi_too_low(self):
        """RSI < 40이면 VWAP 반등이 있어도 신호 없음."""
        strategy = self._build_touched_strategy()
        candles = self._make_simple_candles()
        vwap, _ = _calc_vwap_and_std(candles)
        tick = _make_tick(price=vwap + 100)

        with patch.object(strategy, "is_tradable_time", return_value=True):
            with patch("strategy.vwap_strategy._calc_rsi", return_value=30.0):
                signal = strategy.generate_signal(candles, tick)

        assert signal is None

    def test_signal_rsi_at_boundary_40(self):
        """RSI = 40.0 (경계값) → 신호 발생해야 함."""
        strategy = self._build_touched_strategy()
        candles = self._make_simple_candles()
        vwap, _ = _calc_vwap_and_std(candles)
        tick = _make_tick(price=vwap + 100)

        with patch.object(strategy, "is_tradable_time", return_value=True):
            with patch("strategy.vwap_strategy._calc_rsi", return_value=40.0):
                signal = strategy.generate_signal(candles, tick)

        assert signal is not None

    def test_signal_rsi_at_boundary_60(self):
        """RSI = 60.0 (경계값) → 신호 발생해야 함."""
        strategy = self._build_touched_strategy()
        candles = self._make_simple_candles()
        vwap, _ = _calc_vwap_and_std(candles)
        tick = _make_tick(price=vwap + 100)

        with patch.object(strategy, "is_tradable_time", return_value=True):
            with patch("strategy.vwap_strategy._calc_rsi", return_value=60.0):
                signal = strategy.generate_signal(candles, tick)

        assert signal is not None


# ---------------------------------------------------------------------------
# 4. 손절가 검증 (-1.2%)
# ---------------------------------------------------------------------------

class TestStopLossCalculation:
    def test_stop_loss_calculation(self):
        """-1.2% 손절가."""
        config = _make_config()
        strategy = VwapStrategy(config)
        entry = 100_000.0
        sl = strategy.get_stop_loss(entry)
        expected = entry * (1 + config.vwap_stop_loss_pct)  # 1 - 0.012 = 0.988
        assert sl == pytest.approx(expected, rel=1e-9)
        assert sl == pytest.approx(98_800.0, rel=1e-6)

    def test_stop_loss_is_below_entry(self):
        """손절가는 항상 진입가보다 낮아야 한다."""
        config = _make_config()
        strategy = VwapStrategy(config)
        entry = 55_000.0
        sl = strategy.get_stop_loss(entry)
        assert sl < entry


# ---------------------------------------------------------------------------
# 5. 익절가 검증 (+2.0%)
# ---------------------------------------------------------------------------

class TestTakeProfitCalculation:
    def test_take_profit_calculation(self):
        """+3.0% 1차 익절가."""
        config = _make_config()
        strategy = VwapStrategy(config)
        entry = 100_000.0
        tp1, tp2 = strategy.get_take_profit(entry)
        expected_tp1 = entry * (1 + config.tp1_pct)  # 1.03
        assert tp1 == pytest.approx(expected_tp1, rel=1e-9)
        assert tp1 == pytest.approx(103_000.0, rel=1e-6)

    def test_take_profit_tp2_is_zero(self):
        """tp2 = 0 (트레일링 스톱으로 관리)."""
        config = _make_config()
        strategy = VwapStrategy(config)
        _, tp2 = strategy.get_take_profit(70_000.0)
        assert tp2 == 0.0

    def test_take_profit_is_above_entry(self):
        """1차 익절가는 항상 진입가보다 높아야 한다."""
        config = _make_config()
        strategy = VwapStrategy(config)
        entry = 70_000.0
        tp1, _ = strategy.get_take_profit(entry)
        assert tp1 > entry


# ---------------------------------------------------------------------------
# 6. 내부 헬퍼 단위 테스트
# ---------------------------------------------------------------------------

class TestHelpers:
    def test_calc_vwap_and_std_basic(self):
        """VWAP 기본 계산 검증."""
        # 단순 케이스: high=low=close=100 → tp=100, VWAP=100
        df = pd.DataFrame({
            "open":   [100.0, 100.0, 100.0],
            "high":   [100.0, 100.0, 100.0],
            "low":    [100.0, 100.0, 100.0],
            "close":  [100.0, 100.0, 100.0],
            "volume": [1000,  1000,  1000],
        })
        vwap, std = _calc_vwap_and_std(df)
        assert vwap == pytest.approx(100.0)
        assert std == pytest.approx(0.0)

    def test_calc_rsi_neutral_on_insufficient_data(self):
        """데이터 부족 시 RSI는 중립(50)을 반환해야 한다."""
        closes = pd.Series([100.0] * 5)  # period=14보다 짧음
        rsi = _calc_rsi(closes, period=14)
        assert rsi == pytest.approx(50.0)

    def test_calc_rsi_range(self):
        """RSI 값은 0~100 사이에 있어야 한다."""
        closes = pd.Series([100 + i * 2 for i in range(30)], dtype=float)
        rsi = _calc_rsi(closes, period=14)
        assert 0.0 <= rsi <= 100.0

    def test_calc_rsi_all_gains_near_100(self):
        """지속 상승 시 RSI는 100에 근접해야 한다."""
        closes = pd.Series([float(i) for i in range(1, 40)], dtype=float)
        rsi = _calc_rsi(closes, period=14)
        assert rsi > 90.0

    def test_calc_rsi_all_losses_near_0(self):
        """지속 하락 시 RSI는 0에 근접해야 한다."""
        closes = pd.Series([float(40 - i) for i in range(40)], dtype=float)
        rsi = _calc_rsi(closes, period=14)
        assert rsi < 10.0

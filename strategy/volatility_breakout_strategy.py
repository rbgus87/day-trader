"""strategy/volatility_breakout_strategy.py — 래리 윌리엄스 변동성 돌파 전략.

핵심 공식
---------
target_price = 당일시가 + (전일고가 - 전일저가) × K

진입 조건
---------
- 09:00 ~ entry_deadline 사이에 현재가 >= target_price
- 당일 첫 1회만 진입
- (선택) 거래량 확인: 분봉 거래량 >= 당일 평균 분봉 거래량 × 2.0

레인지 필터
-----------
- min_range_pct ≤ 전일 변동폭 / 전일종가 ≤ max_range_pct

청산 경로
---------
- tp_exit      : 진입가 대비 +tp_pct 도달 (tp_pct > 0)
- trailing_stop: 고점 대비 trail_pct 하락 (use_trailing=True)
- stop_loss    : 당일 시가 하회 (sl_mode=open) 또는 진입가 -sl_pct% (sl_mode=fixed)
- forced_close : 15:10 강제 청산
"""
from __future__ import annotations

from strategy.base_strategy import BaseStrategy, Signal


class VolatilityBreakoutStrategy(BaseStrategy):
    """변동성 돌파 전략 — 백테스트 전용."""

    def __init__(self, config: object) -> None:
        super().__init__(config)
        self._prev_high: float = 0.0
        self._prev_low: float = 0.0
        self._prev_close: float = 0.0
        self._today_open: float = 0.0
        self._target_price: float = 0.0
        self._entered_today: bool = False

    # ── 백테스터 호환 메서드 ────────────────────────────────────────────────

    def update_prev_day(self, prev_high: float, prev_low: float, prev_close: float) -> None:
        self._prev_high = prev_high
        self._prev_low = prev_low
        self._prev_close = prev_close
        self._entered_today = False
        self._today_open = 0.0
        self._target_price = 0.0

    def set_today_open(self, open_price: float) -> None:
        self._today_open = open_price
        k = float(getattr(self._config, "vb_k_value", 0.5))
        range_size = self._prev_high - self._prev_low
        self._target_price = open_price + range_size * k

    @property
    def target_price(self) -> float:
        return self._target_price

    def on_entry(self) -> None:
        self._entered_today = True

    def on_exit(self) -> None:
        pass

    def generate_signal(self, candle: dict) -> Signal | None:
        """실거래용 신호 생성 (현재 미사용)."""
        return None

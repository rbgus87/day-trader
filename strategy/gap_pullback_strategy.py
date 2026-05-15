"""strategy/gap_pullback_strategy.py — 갭업 눌림목 단타 전략.

장 초반(09:00~09:20) 갭업 후 눌림 → 반등 패턴을 포착한다.
MomentumStrategy와 시간대가 분리(갭 09:00~09:20, 모멘텀 09:30~)되어 독립 동작.
max_positions 슬롯을 공유하며 1개 이하로 사용.
"""

from __future__ import annotations

import re
from datetime import datetime, time

import pandas as pd
from loguru import logger

from config.settings import TradingConfig
from strategy.base_strategy import BaseStrategy, Signal


class GapPullbackStrategy(BaseStrategy):
    """갭업 눌림목 반등 전략 — 진입 09:00~09:20, 강제 청산 09:45."""

    def __init__(self, config: TradingConfig) -> None:
        super().__init__()
        self._config = config

        # 전일·당일 기준값 (backtester._setup_strategy_day 또는 session_manager에서 주입)
        self._prev_close: float = 0.0
        self._prev_day_volume: int = 0
        self._prev_day_candles: pd.DataFrame | None = None
        self._open_price: float = 0.0      # 당일 시가

        # ATR 조회용
        self._ticker: str = ""
        self._last_signal_date: str = ""

        # 손절 기준: generate_signal 에서 갱신 (직전 눌림 저가)
        self._pullback_low: float = 0.0

        # 진입 시간창 파싱
        self._entry_start: time = self._parse_time(
            getattr(config, "gap_pullback_entry_start", "09:00"), time(9, 0)
        )
        self._entry_end: time = self._parse_time(
            getattr(config, "gap_pullback_entry_end", "09:20"), time(9, 20)
        )

        # 전략별 강제 청산 시각 (09:45) — backtester가 getattr로 참조
        self._force_close_time: time = self._parse_time(
            getattr(config, "gap_pullback_force_close", "09:45"), time(9, 45)
        )

        self.diag_counters: dict[str, int] = self._make_diag_counters()
        self.configure_multi_trade(
            max_trades=getattr(config, "max_trades_per_day", 2),
            cooldown_minutes=0,  # 단일 시간창 — 쿨다운 불필요
        )

    # ──────────────────────────── 데이터 주입 ────────────────────────────

    def set_prev_day_data(self, high: float, volume: int, close: float = 0.0) -> None:
        """전일 고가/거래량/종가 주입 (backtester._setup_strategy_day 공통 훅)."""
        self._prev_day_volume = volume
        self._prev_close = close

    def set_prev_day_candles(self, candles: pd.DataFrame | None) -> None:
        """전일 분봉 주입 — 동시간대 거래량 비교용."""
        if candles is not None and not candles.empty:
            self._prev_day_candles = candles.copy()
            if "ts" in self._prev_day_candles.columns:
                self._prev_day_candles["ts"] = pd.to_datetime(self._prev_day_candles["ts"])
        else:
            self._prev_day_candles = None

    def set_prev_close(self, close: float) -> None:
        """전일 종가 주입 (backtester._setup_strategy_day GapStrategy 훅)."""
        self._prev_close = close

    def set_open_price(self, open_price: float) -> None:
        """당일 시가 주입 (backtester._setup_strategy_day Pullback 훅 / 실시간 첫 캔들)."""
        self._open_price = open_price

    def set_ticker(self, ticker: str) -> None:
        """ATR 조회용 종목코드 주입."""
        self._ticker = ticker

    # ──────────────────────────── 시간 유틸 ─────────────────────────────

    @staticmethod
    def _parse_time(s: str, default: time) -> time:
        m = re.match(r"(\d+):(\d+)", str(s))
        return time(int(m.group(1)), int(m.group(2))) if m else default

    def _now(self) -> time:
        return self._backtest_time if self._backtest_time else datetime.now().time()

    def is_tradable_time(self) -> bool:
        """BaseStrategy.BLOCK_UNTIL(09:05) 대신 갭 전략 entry_start(09:00)를 하한으로."""
        now = self._now()
        return self._entry_start <= now <= self.MARKET_CLOSE

    # ──────────────────────────── 진단 카운터 ────────────────────────────

    @staticmethod
    def _make_diag_counters() -> dict[str, int]:
        return {
            "time_fail": 0,
            "prev_data_missing": 0,
            "gap_too_small": 0,
            "gap_too_large": 0,
            "pullback_too_small": 0,
            "pullback_too_large": 0,
            "bounce_fail": 0,
            "volume_fail": 0,
            "signal_emit": 0,
        }

    def reset_diag_counters(self) -> None:
        for k in self.diag_counters:
            self.diag_counters[k] = 0

    # ──────────────────────────── 시그널 생성 ────────────────────────────

    def generate_signal(
        self,
        candles: pd.DataFrame,
        tick: dict,
        *,
        breakout_price: float | None = None,
    ) -> Signal | None:
        if not self.can_trade():
            return None

        now = self._now()

        # 1. 진입 시간창 (09:00~09:20)
        if not (self._entry_start <= now <= self._entry_end):
            self.diag_counters["time_fail"] += 1
            return None

        current_price = float(tick["price"])

        # 2. 전일 종가·당일 시가 확인
        if self._prev_close <= 0 or self._open_price <= 0:
            self.diag_counters["prev_data_missing"] += 1
            return None

        # 3. 갭업 확인: gap_min_pct ≤ (open - prev_close) / prev_close ≤ gap_max_pct
        gap_pct = (self._open_price - self._prev_close) / self._prev_close
        gap_min = getattr(self._config, "gap_pullback_min_pct", 0.02)
        gap_max = getattr(self._config, "gap_pullback_max_pct", 0.08)
        if gap_pct < gap_min:
            self.diag_counters["gap_too_small"] += 1
            logger.debug(f"[GAP] 갭 미달: {tick['ticker']} {gap_pct:.2%} < {gap_min:.2%}")
            return None
        if gap_pct > gap_max:
            self.diag_counters["gap_too_large"] += 1
            logger.debug(f"[GAP] 갭 과대: {tick['ticker']} {gap_pct:.2%} > {gap_max:.2%}")
            return None

        # 4. 눌림 확인: pb_min ≤ (open - current) / open ≤ pb_max
        pullback_pct = (self._open_price - current_price) / self._open_price
        pb_min = getattr(self._config, "gap_pullback_min_pullback_pct", 0.01)
        pb_max = getattr(self._config, "gap_pullback_max_pullback_pct", 0.03)
        if pullback_pct < pb_min:
            self.diag_counters["pullback_too_small"] += 1
            logger.debug(f"[GAP] 눌림 부족: {tick['ticker']} {pullback_pct:.2%} < {pb_min:.2%}")
            return None
        if pullback_pct > pb_max:
            self.diag_counters["pullback_too_large"] += 1
            logger.debug(f"[GAP] 눌림 과대: {tick['ticker']} {pullback_pct:.2%} > {pb_max:.2%}")
            return None

        # 5. 반등 확인: 현재가 > 직전 완성 캔들 저가 (눌림 저점을 넘어선 반등 시작)
        if candles is not None and not candles.empty:
            last_low = float(candles.iloc[-1]["low"])
            if current_price <= last_low:
                self.diag_counters["bounce_fail"] += 1
                logger.debug(
                    f"[GAP] 반등 미확인: {tick['ticker']} {current_price:.0f} <= last_low={last_low:.0f}"
                )
                return None

        # 6. 거래량 필터
        if candles is not None and not candles.empty:
            if not self._check_volume(candles, now):
                self.diag_counters["volume_fail"] += 1
                return None

        # 눌림 저가 캡처 (손절 기준)
        if candles is not None and not candles.empty:
            self._pullback_low = float(candles["low"].min())
        else:
            self._pullback_low = current_price * (1.0 - pb_max)

        # 신호 날짜 캡처 (ATR 조회용)
        try:
            if candles is not None and not candles.empty and "ts" in candles.columns:
                self._last_signal_date = pd.to_datetime(
                    candles["ts"].iloc[-1]
                ).strftime("%Y-%m-%d")
        except Exception:
            pass

        self.diag_counters["signal_emit"] += 1
        logger.info(
            f"갭 눌림목 매수 신호: {tick['ticker']} price={current_price:.0f} "
            f"open={self._open_price:.0f} gap={gap_pct:.2%} pullback={pullback_pct:.2%}"
        )
        return Signal(
            ticker=tick["ticker"],
            side="buy",
            price=current_price,
            strategy="gap_pullback",
            reason=f"갭업 {gap_pct:.1%} + 눌림 {pullback_pct:.1%} + 반등 확인",
            context={
                "gap_pct": gap_pct,
                "pullback_pct": pullback_pct,
                "open_price": self._open_price,
                "pullback_low": self._pullback_low,
            },
        )

    def _check_volume(self, candles: pd.DataFrame, now: time) -> bool:
        """거래량 필터: 당일 누적 거래량 ≥ 전일 동시간대 거래량 × volume_ratio."""
        cum_volume = float(candles["volume"].sum())
        volume_ratio = getattr(self._config, "gap_pullback_volume_ratio", 1.5)

        # 전일 분봉이 있으면 동시간대 비교
        if self._prev_day_candles is not None and "ts" in self._prev_day_candles.columns:
            prev_same = self._prev_day_candles[
                self._prev_day_candles["ts"].dt.time <= now
            ]
            if not prev_same.empty:
                prev_vol = float(prev_same["volume"].sum())
                if prev_vol > 0:
                    return cum_volume >= prev_vol * volume_ratio

        # fallback: 전일 전체 거래량의 시간대 비율 추정 (09:00~09:20 ≈ 하루의 5%)
        if self._prev_day_volume > 0:
            time_fraction = 20.0 / 390.0
            return cum_volume >= self._prev_day_volume * time_fraction * volume_ratio

        return True  # 전일 데이터 없으면 통과

    # ──────────────────────────── 청산 가격 계산 ─────────────────────────

    def get_stop_loss(self, entry_price: float) -> float:
        """손절가: 눌림 저점 − ATR×stop_mult. fallback: 진입가 − 3%."""
        stop_mult = getattr(self._config, "gap_pullback_atr_stop_mult", 0.5)
        hard_floor_pct = 0.03
        fallback = entry_price * (1.0 - hard_floor_pct)

        pullback_base = self._pullback_low if self._pullback_low > 0 else entry_price

        if not self._ticker:
            return max(pullback_base * (1.0 - 0.005), fallback)

        try:
            from core.indicators import get_latest_atr
            atr_pct = get_latest_atr(
                "daytrader.db", self._ticker, self._last_signal_date or None
            )
            if atr_pct is None:
                return max(pullback_base * (1.0 - 0.005), fallback)
            atr_abs = entry_price * atr_pct
            return max(pullback_base - atr_abs * stop_mult, fallback)
        except Exception as e:
            logger.warning(f"갭 전략 ATR 손절 계산 실패 ({self._ticker}): {e}")
            return max(pullback_base * (1.0 - 0.005), fallback)

    def get_take_profit(self, entry_price: float) -> float:
        """1차 목표가: 당일 시가 회복. 실패 시 진입가 + 2% fallback."""
        if self._open_price > entry_price:
            return self._open_price
        return entry_price * 1.02

    # ──────────────────────────── 리셋 ───────────────────────────────────

    def reset(self) -> None:
        """일일 리셋 — 전일/당일 기준값은 setup 단계에서 덮어씌워진다."""
        super().reset()
        self._pullback_low = 0.0

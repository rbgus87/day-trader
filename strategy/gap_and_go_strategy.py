"""strategy/gap_and_go_strategy.py — Gap & Go 전략 (갭업 + 첫 봉 양봉).

갭업으로 시작한 종목의 첫 5분봉(09:00~09:04)이 양봉이면
매수세가 강하다는 뜻으로 추세 지속을 기대하여 진입한다.

진입
----
- entry_mode="close"   : 09:05 즉시 진입 (첫 봉 종가 기준)
- entry_mode="high_break": 첫 봉 고가 돌파 시 진입 (09:05~09:30)

손절
----
- sl_mode="first_bar_low" : 첫 봉 저가 하회 시
- sl_mode="prev_close"    : 전일 종가 하회 시 (갭 메워짐)
- sl_mode="fixed_2pct"    : 진입가 대비 고정 2%

익절 / 청산
-----------
- tp_pct>0 & use_trailing=False : 고정 TP
- tp_pct=0 & use_trailing=True  : 트레일링 스톱 (trail_only 모드)
- 15:10 강제 청산

백테스트 전용 — 실거래 파이프라인 미연동 (gg_enabled: false 유지).
이전 gap_pullback 전략(strategy/archive/gap_pullback_strategy.py)과 별개 코드.
"""
from __future__ import annotations

import re
from datetime import time

import pandas as pd
from loguru import logger

from config.settings import TradingConfig
from strategy.base_strategy import BaseStrategy, Signal


def _parse_time(s: str, default: time) -> time:
    m = re.match(r"(\d+):(\d+)", str(s))
    return time(int(m.group(1)), int(m.group(2))) if m else default


class GapAndGoStrategy(BaseStrategy):
    """갭업 후 첫 5분봉 양봉 확인 → 추세 지속 진입 전략.

    백테스터(GapAndGoFastBacktester)에서 사용.
    실거래 파이프라인(engine_worker / tick_processor)에는 연동되지 않는다.
    """

    def __init__(self, config: TradingConfig) -> None:
        super().__init__()
        self._config = config

        # 전일 데이터 (backtester 주입)
        self._prev_day_close: float = 0.0
        self._prev_day_volume: int = 0

        # 당일 상태
        self._first_bar_high: float = 0.0
        self._first_bar_low: float = 0.0
        self._first_bar_close: float = 0.0
        self._first_bar_open: float = 0.0

        # 파라미터
        self._gap_min_pct: float = float(getattr(config, "gg_gap_min_pct", 0.02))
        self._gap_max_pct: float = float(getattr(config, "gg_gap_max_pct", 0.15))
        self._body_ratio_min: float = float(getattr(config, "gg_body_ratio_min", 0.5))
        self._entry_mode: str = str(getattr(config, "gg_entry_mode", "close"))
        self._entry_deadline: time = _parse_time(
            str(getattr(config, "gg_entry_deadline", "09:30")), time(9, 30)
        )
        self._sl_mode: str = str(getattr(config, "gg_sl_mode", "first_bar_low"))
        self._sl_pct: float = float(getattr(config, "gg_sl_pct", 0.02))
        self._tp_pct: float = float(getattr(config, "gg_tp_pct", 0.05))
        self._use_trailing: bool = bool(getattr(config, "gg_use_trailing", False))
        self._trail_pct: float = float(getattr(config, "gg_trail_pct", 0.02))
        self._use_volume: bool = bool(getattr(config, "gg_use_volume", True))
        self._volume_ratio: float = float(getattr(config, "gg_volume_ratio", 2.0))

        self.configure_multi_trade(
            max_trades=int(getattr(config, "max_trades_per_day", 1)),
            cooldown_minutes=int(getattr(config, "cooldown_minutes", 0)),
        )

    # ── 데이터 주입 훅 ──────────────────────────────────────────────────────

    def set_prev_day_data(self, high: float, volume: int, close: float = 0.0) -> None:
        """backtester._setup_strategy_day 공통 훅."""
        self._prev_day_close = close
        self._prev_day_volume = volume

    def set_prev_day_volume(self, volume: int) -> None:
        self._prev_day_volume = volume

    def set_prev_day_candles(self, candles: pd.DataFrame | None) -> None:
        pass

    def reset(self) -> None:
        super().reset()
        self._first_bar_high = 0.0
        self._first_bar_low = 0.0
        self._first_bar_close = 0.0
        self._first_bar_open = 0.0

    # ── 갭 / 첫 봉 헬퍼 ────────────────────────────────────────────────────

    def detect_gap(self, open_price: float, prev_close: float) -> bool:
        """갭업 여부 확인 — gap_min_pct ≤ (open-prev_close)/prev_close < gap_max_pct."""
        if prev_close <= 0 or open_price <= 0:
            return False
        gap_pct = (open_price - prev_close) / prev_close
        return self._gap_min_pct <= gap_pct < self._gap_max_pct

    def check_first_bar(self, candles: pd.DataFrame) -> bool:
        """09:00~09:04 첫 5분봉 집계 후 양봉 + 몸통비율 확인.

        True 반환 시 _first_bar_{high,low,open,close} 갱신됨.
        """
        if "ts" not in candles.columns:
            return False
        df = candles.copy()
        mins = pd.to_datetime(df["ts"]).dt.hour * 60 + pd.to_datetime(df["ts"]).dt.minute
        first_bar = df[(mins >= 540) & (mins <= 544)]
        if first_bar.empty:
            return False

        self._first_bar_open  = float(first_bar.iloc[0]["open"])
        self._first_bar_close = float(first_bar.iloc[-1]["close"])
        self._first_bar_high  = float(first_bar["high"].max())
        self._first_bar_low   = float(first_bar["low"].min())

        if self._first_bar_close <= self._first_bar_open:
            return False  # 음봉

        fb_range = self._first_bar_high - self._first_bar_low
        if fb_range > 0:
            body_ratio = (self._first_bar_close - self._first_bar_open) / fb_range
            if body_ratio < self._body_ratio_min:
                logger.debug(
                    f"[GAP_GO] 몸통비율 미달: {body_ratio:.2f} < {self._body_ratio_min}"
                )
                return False
        return True

    # ── 손절 / 익절 ─────────────────────────────────────────────────────────

    def get_stop_loss(self, entry_price: float) -> float:
        if self._sl_mode == "first_bar_low" and self._first_bar_low > 0:
            return self._first_bar_low
        if self._sl_mode == "prev_close" and self._prev_day_close > 0:
            return self._prev_day_close
        return entry_price * (1.0 - self._sl_pct)  # fixed_2pct

    def get_take_profit(self, entry_price: float) -> float:
        if self._use_trailing or self._tp_pct <= 0:
            return 0.0
        return entry_price * (1.0 + self._tp_pct)

    # ── 시그널 생성 (실거래 미연동 — 스텁) ─────────────────────────────────

    def generate_signal(
        self,
        candles: pd.DataFrame,
        tick: dict,
        *,
        breakout_price: float | None = None,
    ) -> Signal | None:
        """GapAndGoFastBacktester가 직접 처리 — 실거래 파이프라인 미사용."""
        return None

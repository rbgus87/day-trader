"""strategy/vi_breakout_strategy.py — VI(변동성완화장치) 돌파 전략.

VI 발동 후 해제 시 재돌파하는 순간 진입.

분봉 시뮬에서는 전일종가 대비 vi_static_trigger_pct(9.5%) 이상 고가 변동을
VI 발동으로 추정하고, 다음 분봉 close가 발동 직전가 + vi_breakout_pct 초과 시 진입.

실거래에서는 vi_handler에서 VI 상태를 주입받아 engine_worker에서 신호 생성.

금지:
- vi_breakout.enabled: false 유지 (그리드 검증 전 비활성)
- 기존 모멘텀/ORB 전략 파라미터 변경 금지
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from strategy.base_strategy import BaseStrategy, Signal

if TYPE_CHECKING:
    from datetime import time
    import pandas as pd


class VIBreakoutStrategy(BaseStrategy):
    """VI 돌파 전략.

    진입: VI 발동 추정 후 다음 분봉에서 vi_pre_price 재돌파 시 매수.
    청산: TP / 트레일링스톱 / 손절 / 강제청산(15:10).
    당일 1회만 진입.
    """

    def __init__(self, config: Any) -> None:
        super().__init__()
        self._config = config

        # 전일 데이터 (백테스터/engine_worker 주입)
        self._prev_day_close: float = 0.0
        self._prev_day_volume: int = 0

        # 당일 VI 상태
        self._vi_triggered: bool = False
        self._vi_pre_price: float = 0.0

    # ------------------------------------------------------------------
    # BaseStrategy 훅
    # ------------------------------------------------------------------

    def reset(self) -> None:
        super().reset()
        self._vi_triggered = False
        self._vi_pre_price = 0.0

    def set_prev_day_data(
        self,
        high: float,
        volume: int,
        close: float = 0.0,
    ) -> None:
        self._prev_day_volume = volume
        self._prev_day_close = close

    def set_prev_day_volume(self, volume: int) -> None:
        self._prev_day_volume = volume

    def set_prev_day_candles(self, candles: Any) -> None:
        pass

    # ------------------------------------------------------------------
    # 신호 생성 (실거래용 — 백테스터는 run_multi_day_cached 직접 루프)
    # ------------------------------------------------------------------

    def generate_signal(
        self,
        candles: Any,
        tick: dict,
        *,
        breakout_price: float | None = None,
    ) -> Signal | None:
        """실거래 전용 신호 생성.

        VI 활성 상태는 engine_worker가 vi_handler를 통해
        notify_vi_triggered()로 주입한다.
        """
        if not self.can_trade():
            return None

        import datetime as _dt
        cfg = self._config
        deadline_str = str(getattr(cfg, "vi_entry_deadline", "13:00"))
        h, m = map(int, deadline_str.split(":"))
        entry_deadline: _dt.time = _dt.time(h, m)

        now: _dt.time = (
            self._backtest_time
            if self._backtest_time
            else _dt.datetime.now().time()
        )

        if now < self.BLOCK_UNTIL or now > entry_deadline:
            return None

        price = float(tick.get("price", 0.0))
        if price <= 0:
            return None

        if not self._vi_triggered or self._vi_pre_price <= 0:
            return None

        vi_breakout_pct = float(getattr(cfg, "vi_breakout_pct", 0.005))
        breakout_threshold = self._vi_pre_price * (1.0 + vi_breakout_pct)
        if price <= breakout_threshold:
            return None

        return Signal(
            ticker=tick.get("ticker", ""),
            side="buy",
            price=price,
            strategy="vi_breakout",
            reason="vi_rebreakout",
            context={
                "vi_pre_price":         self._vi_pre_price,
                "breakout_threshold":   breakout_threshold,
            },
        )

    # ------------------------------------------------------------------
    # 실거래 VI 상태 주입 (engine_worker → vi_handler 연동)
    # ------------------------------------------------------------------

    def notify_vi_triggered(self, vi_pre_price: float) -> None:
        """VI 발동 통보 — 다음 분봉부터 재돌파 감시."""
        self._vi_triggered = True
        self._vi_pre_price = vi_pre_price

    def notify_vi_released(self) -> None:
        """VI 해제 통보 (진입 기회 소멸 또는 재감시)."""
        self._vi_triggered = False
        self._vi_pre_price = 0.0

    # ------------------------------------------------------------------
    # 손절/익절 계산 헬퍼 (engine_worker 참조용)
    # ------------------------------------------------------------------

    def get_stop_loss(self, entry_price: float) -> float:
        cfg = self._config
        sl_pct = float(getattr(cfg, "vi_sl_pct", 0.015))
        return entry_price * (1.0 - sl_pct)

    def get_take_profit(self, entry_price: float) -> float:
        cfg = self._config
        tp_pct = float(getattr(cfg, "vi_tp_pct", 0.03))
        return entry_price * (1.0 + tp_pct) if tp_pct > 0 else 0.0

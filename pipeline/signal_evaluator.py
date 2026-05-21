"""pipeline/signal_evaluator.py — 캔들 → 전략 시그널 생성.

_candle_consumer 내부 로직을 engine_worker에서 분리.
PyQt6 미사용 — 시그널은 반환값으로 전달.
"""
from __future__ import annotations

import time as _time
from collections import deque

from loguru import logger

from pipeline.trading_state import TradingState


class SignalEvaluator:
    """캔들을 수신하여 시그널 또는 None을 반환."""

    def __init__(
        self,
        risk_manager,
        config,
        notifier,
        state: TradingState,
    ):
        self._risk_manager = risk_manager
        self._config = config
        self._notifier = notifier
        self._state = state

        self._candle_count = 0
        self._signal_eval_count = 0
        self._gate_counts: dict[str, int] = {
            "tf_skip": 0, "no_strategy": 0, "halted": 0,
            "blacklist": 0, "loss_rest": 0, "max_pos": 0, "has_pos": 0,
        }
        self._last_candle_log = _time.time()

    async def process_candle(self, candle: dict):
        """캔들 평가 → Signal 또는 None 반환."""
        import pandas as pd

        self._candle_count += 1
        now_ts = _time.time()
        if now_ts - self._last_candle_log >= 300:
            logger.info(
                f"[CANDLE] {self._candle_count}건 생성, "
                f"{self._signal_eval_count}건 평가 (최근 5분)"
            )
            logger.info(
                f"[CANDLE-GATE] tf_skip={self._gate_counts['tf_skip']}, "
                f"no_strategy={self._gate_counts['no_strategy']}, "
                f"halted={self._gate_counts['halted']}, "
                f"blacklist={self._gate_counts['blacklist']}, "
                f"loss_rest={self._gate_counts['loss_rest']}, "
                f"max_pos={self._gate_counts['max_pos']}, "
                f"has_pos={self._gate_counts['has_pos']}, "
                f"eval={self._signal_eval_count}"
            )
            self._log_signal_summary()
            self._candle_count = 0
            self._signal_eval_count = 0
            for k in self._gate_counts:
                self._gate_counts[k] = 0
            self._last_candle_log = now_ts

        try:
            ticker = candle["ticker"]

            if candle.get("tf", "1m") != "1m":
                self._gate_counts["tf_skip"] += 1
                return None

            hist = self._state.candle_history.get(ticker)
            if hist is None:
                hist = deque(maxlen=self._state.MAX_HISTORY)
                self._state.candle_history[ticker] = hist
            hist.append(candle)

            if not self._state.active_strategies:
                self._gate_counts["no_strategy"] += 1
                return None
            if self._risk_manager.is_trading_halted():
                self._gate_counts["halted"] += 1
                if not self._state.daily_halt_notified and self._notifier:
                    self._state.daily_halt_notified = True
                    try:
                        loss = self._risk_manager._daily_pnl
                        limit = self._config.trading.daily_max_loss_pct * 100
                        self._notifier.send_urgent(
                            f"[HALT] 일일 손실 한도 도달\n"
                            f"일일 PnL: {loss:+,.0f}원\n"
                            f"한도: {limit:.1f}%\n"
                            f"오늘 추가 매수 차단"
                        )
                    except Exception as e:
                        logger.warning(f"halt 텔레그램 실패: {e}")
                return None
            if ticker not in self._state.active_strategies:
                self._gate_counts["no_strategy"] += 1
                return None
            if self._risk_manager.is_ticker_blacklisted(ticker):
                self._gate_counts["blacklist"] += 1
                return None
            if self._risk_manager.is_in_loss_rest():
                self._gate_counts["loss_rest"] += 1
                return None

            open_pos = self._risk_manager.get_open_positions()
            if len(open_pos) >= self._config.trading.max_positions and ticker not in open_pos:
                self._gate_counts["max_pos"] += 1
                return None
            if self._risk_manager.get_position(ticker):
                self._gate_counts["has_pos"] += 1
                return None

            strat_info = self._state.active_strategies[ticker]
            strategy = strat_info["strategy"]

            if candle.get("tf") == "5m" and hasattr(strategy, "on_candle_5m"):
                strategy.on_candle_5m(candle)

            if ticker in self._state.tick_signaled:
                self._gate_counts.setdefault("tick_signaled", 0)
                self._gate_counts["tick_signaled"] += 1
                return None

            candle["price"] = candle.get("close", 0)
            df = pd.DataFrame(self._state.candle_history[ticker])

            # 갭 전략 시그널 체크 (09:00~09:20 구간, gap_pullback_enabled=true 시)
            gap_strat = self._state.gap_strategies.get(ticker)
            if gap_strat is not None:
                # 당일 시가 미설정 시 첫 캔들에서 주입
                if gap_strat._open_price <= 0 and not df.empty:
                    first_open = float(df.iloc[0].get("open", 0))
                    if first_open > 0:
                        gap_strat.set_open_price(first_open)
                gap_signal = gap_strat.generate_signal(df, candle)
                if gap_signal is not None:
                    self._signal_eval_count += 1
                    return gap_signal  # 갭 신호 우선 반환 (시간창 분리로 모멘텀과 충돌 無)

            # ORB 전략: 캔들 경로 미사용 — 틱 경로(_on_tick_orb)에서만 진입
            # Multi 모드: 09:30 이후에는 momentum_strategy로 평가
            from strategy.orb_strategy import ORBStrategy as _ORB
            if isinstance(strategy, _ORB):
                strategy_type_cfg = getattr(self._config.trading, "strategy_type", "momentum")
                if strategy_type_cfg == "multi":
                    from datetime import time as _dtime
                    if datetime.now().time() < _dtime(9, 30):
                        return None  # ORB 창: 틱 경로 전담
                    mom_strat = strat_info.get("momentum_strategy")
                    if mom_strat is None:
                        return None
                    strategy = mom_strat  # fall through to momentum evaluation
                else:
                    return None

            breakout_info = self._state.breakout_detected.get(ticker)
            bp = breakout_info.breakout_price if breakout_info else None
            self._signal_eval_count += 1
            return strategy.generate_signal(df, candle, breakout_price=bp)

        except Exception as e:
            logger.error(f"candle_consumer 오류: {e}")
            return None

    def _log_signal_summary(self) -> None:
        """active_strategies 진단 카운터 집계 + 로깅 (모멘텀 전략만)."""
        from strategy.orb_strategy import ORBStrategy as _ORB
        agg: dict[str, int] = {}
        any_momentum = False
        for info in self._state.active_strategies.values():
            strat = info.get("strategy") if isinstance(info, dict) else None
            if isinstance(strat, _ORB):
                # Multi 모드: momentum_strategy 카운터 집계
                mom_strat = info.get("momentum_strategy") if isinstance(info, dict) else None
                if mom_strat:
                    strat = mom_strat
                else:
                    continue  # ORB 단독: 모멘텀 지표 집계 제외
            counters = getattr(strat, "diag_counters", None)
            if not isinstance(counters, dict):
                continue
            any_momentum = True
            for k, v in counters.items():
                agg[k] = agg.get(k, 0) + int(v)
            reset = getattr(strat, "reset_diag_counters", None)
            if callable(reset):
                reset()
        if not any_momentum:
            return
        logger.info(
            f"[SIGNAL-SUMMARY] 평가={self._signal_eval_count}, "
            f"전일데이터누락={agg.get('prev_day_missing', 0)}, "
            f"BREAKOUT통과={agg.get('breakout_pass', 0)}, "
            f"BREAKOUT미달={agg.get('breakout_fail', 0)}, "
            f"VOLUME미달={agg.get('volume_fail', 0)}, "
            f"BREAKOUT_LAST미달={agg.get('breakout_last_fail', 0)}, "
            f"ADX봉부족={agg.get('adx_no_bars', 0)}, "
            f"ADX미달={agg.get('adx_fail', 0)}, "
            f"ADX통과={agg.get('adx_pass', 0)}, "
            f"RVOL탈락={agg.get('rvol_fail', 0)}, "
            f"VWAP탈락={agg.get('vwap_fail', 0)}, "
            f"신호발생={agg.get('signal_emit', 0)}"
        )

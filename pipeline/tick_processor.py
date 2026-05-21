"""pipeline/tick_processor.py — 틱 처리 + 포지션 모니터링.

_tick_consumer 내부 로직을 engine_worker에서 분리.
PyQt6 미사용 — trade_executed 이벤트는 on_trade_executed 콜백으로 전달.
"""
from __future__ import annotations

import time as _time
from collections import deque
from datetime import datetime
from typing import Callable

from loguru import logger

from core.position import ExitPhase
from pipeline.trading_state import BreakoutInfo, TradingState


class TickProcessor:
    """단일 틱 처리: 캔들 빌더 전달 + 포지션 모니터링."""

    def __init__(
        self,
        risk_manager,
        order_manager,
        vi_handler,
        shadow_tracker,
        order_tracker,
        candle_builder,
        config,
        state: TradingState,
        paper_mode: bool,
        on_trade_executed: Callable[[dict], None],
    ):
        self._risk_manager = risk_manager
        self._order_manager = order_manager
        self._vi_handler = vi_handler
        self._shadow_tracker = shadow_tracker
        self._order_tracker = order_tracker
        self._candle_builder = candle_builder
        self._config = config
        self._state = state
        self._paper_mode = paper_mode
        self._on_trade_executed = on_trade_executed

        self._tick_count = 0
        self._last_tick_log = _time.time()
        self._first_tick_logged = False

        # MAX_ENTRY 차단 중복 로그 억제: 종목당 최초 1회 INFO, 이후 DEBUG
        self._max_entry_blocked: set[str] = set()
        self._max_entry_blocked_reset_ts: float = _time.time()

        # Multi 모드: 09:30 전환 로그 1회 억제 플래그
        self._multi_switch_logged: bool = False

    # ── ATR helper ──

    def _intraday_atr_pct(self, ticker: str, length: int = 14) -> float | None:
        hist = self._state.candle_history.get(ticker)
        if hist is None:
            return None
        if len(hist) < length + 1:
            logger.info(f"[ATR-CALC] {ticker} reason=short len={len(hist)} need={length + 1}")
            return None
        cur_len = len(hist)
        cached = self._state.atr_pct_cache.get(ticker)
        if cached is not None and cached[0] == cur_len:
            return cached[1]
        atr_pct: float | None = None
        reason: str | None = None
        try:
            import pandas as pd
            from core.indicators import wilder_atr
            df = pd.DataFrame(list(hist))
            cols_needed = {"high", "low", "close"}
            missing = cols_needed - set(df.columns)
            if missing:
                reason = f"cols_missing={sorted(missing)}"
            else:
                h = pd.to_numeric(df["high"], errors="coerce")
                l = pd.to_numeric(df["low"], errors="coerce")
                c = pd.to_numeric(df["close"], errors="coerce")
                nan_rows = int((h.isna() | l.isna() | c.isna()).sum())
                zero_rows = int(((h <= 0) | (l <= 0) | (c <= 0)).sum())
                atr = wilder_atr(h, l, c, length=length)
                if atr.empty:
                    reason = f"empty (nan={nan_rows}, zero={zero_rows})"
                else:
                    last_atr = atr.iloc[-1]
                    last_close = float(c.iloc[-1]) if not pd.isna(c.iloc[-1]) else 0.0
                    if pd.isna(last_atr):
                        reason = (
                            f"nan_last_atr (rows={len(df)} nan={nan_rows} "
                            f"zero={zero_rows} last_h={float(h.iloc[-1]) if not pd.isna(h.iloc[-1]) else 'NaN'} "
                            f"last_l={float(l.iloc[-1]) if not pd.isna(l.iloc[-1]) else 'NaN'} "
                            f"last_c={last_close})"
                        )
                    elif last_close <= 0:
                        reason = f"close<=0({last_close})"
                    else:
                        atr_pct = float(last_atr) / last_close
        except Exception as e:
            reason = f"exc={type(e).__name__}:{e}"
        if atr_pct is None and reason:
            logger.info(f"[ATR-CALC] {ticker} reason={reason} len={cur_len}")
        self._state.atr_pct_cache[ticker] = (cur_len, atr_pct)
        return atr_pct

    # ── 돌파 감지 + 즉시 진입 ──

    def _log_max_entry_blocked(self, ticker: str, msg: str) -> None:
        """MAX_ENTRY 차단 로그: 종목당 최초 1회 INFO, 이후 DEBUG. 5분마다 리셋."""
        now_ts = _time.time()
        if now_ts - self._max_entry_blocked_reset_ts >= 300:
            self._max_entry_blocked.clear()
            self._max_entry_blocked_reset_ts = now_ts
        if ticker not in self._max_entry_blocked:
            self._max_entry_blocked.add(ticker)
            logger.info(msg)
        else:
            logger.debug(msg)

    async def _on_tick_no_position(self, ticker: str, price: float, tick: dict, signal_queue) -> None:
        import pandas as _pd
        if not self._state.active_strategies:
            return
        strat_info = self._state.active_strategies.get(ticker)
        if strat_info is None:
            return
        strategy = strat_info["strategy"]

        # ORB 전략은 전일 고가 기반 돌파 감지 불필요 — 별도 경로 처리
        from strategy.orb_strategy import ORBStrategy as _ORB
        if isinstance(strategy, _ORB):
            strategy_type_cfg = getattr(self._config.trading, "strategy_type", "momentum")
            if strategy_type_cfg == "multi":
                from datetime import time as _dtime
                if datetime.now().time() < _dtime(9, 30):
                    await self._on_tick_orb(ticker, price, tick, signal_queue, strategy)
                    return
                # 09:30 이후: 모멘텀 전략으로 전환
                if not self._multi_switch_logged:
                    self._multi_switch_logged = True
                    logger.info("[MULTI] 09:30 — ORB 진입창 종료, 모멘텀 활성화")
                mom_strat = strat_info.get("momentum_strategy")
                if mom_strat is None:
                    return
                strategy = mom_strat
                # fall through to momentum path
            else:
                await self._on_tick_orb(ticker, price, tick, signal_queue, strategy)
                return

        prev_high = getattr(strategy, "_prev_day_high", 0.0)
        if prev_high <= 0:
            return

        min_bp = getattr(self._config.trading, "min_breakout_pct", 0.03)
        breakout_threshold = prev_high * (1 + min_bp)

        if price >= breakout_threshold and ticker not in self._state.breakout_detected:
            _now = datetime.now()
            self._state.breakout_detected[ticker] = BreakoutInfo(
                ticker=ticker, breakout_price=price, detected_at=_now,
            )
            prev_close = self._state.prev_close.get(ticker) or 0.0
            _chg = (price - prev_close) / prev_close * 100 if prev_close > 0 else 0.0
            logger.info(
                f"[BREAKOUT_TICK] {ticker} @ {price:,} "
                f"(prev_high={prev_high:,} threshold={breakout_threshold:,.0f})"
            )
            # [COND_MET] BREAKOUT 최초 충족 기록
            if hasattr(strategy, "set_cond_breakout") and strategy.set_cond_breakout(_now):
                logger.info(
                    f"[COND_MET] {ticker} BREAKOUT 충족: "
                    f"price={price:,} > prev_high={prev_high:,}, "
                    f"전일종가대비=+{_chg:.1f}%"
                )

        if ticker not in self._state.breakout_detected:
            return
        if ticker in self._state.tick_signaled:
            return

        if self._risk_manager.is_trading_halted():
            return
        if self._risk_manager.is_ticker_blacklisted(ticker):
            return
        if self._risk_manager.is_in_loss_rest():
            return
        open_pos = self._risk_manager.get_open_positions()
        if len(open_pos) >= self._config.trading.max_positions:
            return
        if not strategy.can_trade():
            return
        if self._vi_handler.is_vi_active(ticker):
            return
        if self._order_tracker is not None and self._order_tracker.get_pending(ticker) is not None:
            return

        hist = self._state.candle_history.get(ticker)
        if not hist or len(hist) < 30:
            return

        # B-2: 전일종가 대비 현재가 상한 체크 (상한가 근처 무의미 진입 방지)
        _max_close_pct = getattr(self._config.trading, "max_entry_above_close_pct", 0.0)
        if _max_close_pct and _max_close_pct > 0:
            _prev_close = self._state.prev_close.get(ticker) or 0.0
            if _prev_close > 0:
                _chg_from_close = (price - _prev_close) / _prev_close * 100
                if _chg_from_close >= _max_close_pct:
                    self._log_max_entry_blocked(
                        ticker,
                        f"[MAX_ENTRY] {ticker} 차단: 전일종가 대비 +{_chg_from_close:.1f}% > {_max_close_pct:.0f}%",
                    )
                    return

        # max_entry_above_breakout_pct 체크 (breakout_threshold 기준 — backtester 일관성)
        max_gap = getattr(self._config.trading, "max_entry_above_breakout_pct", 0.10)
        gap = (price - breakout_threshold) / breakout_threshold
        if gap > max_gap:
            self._log_max_entry_blocked(
                ticker,
                f"[MAX_ENTRY] {ticker} 차단: {gap * 100:.1f}% > {max_gap * 100:.0f}% "
                f"(cur={price:,} thr={breakout_threshold:,.0f})",
            )
            return
        logger.debug(f"[MAX_ENTRY] {ticker} 통과: {gap * 100:.1f}%")

        df = _pd.DataFrame(hist)
        breakout_info = self._state.breakout_detected[ticker]
        signal = strategy.generate_signal(df, tick, breakout_price=breakout_info.breakout_price)
        if signal:
            self._state.tick_signaled.add(ticker)
            # [ENTRY_TIMING] 시그널 발생 시 조건별 충족 시점 요약
            if hasattr(strategy, "get_cond_tracker"):
                _tracker = strategy.get_cond_tracker()
                _now = datetime.now()
                _times = [t for t in _tracker.values() if t is not None]
                _delta = int((_now - min(_times)).total_seconds()) if _times else None
                _prev_c = self._state.prev_close.get(ticker) or 0.0
                _chg = (price - _prev_c) / _prev_c * 100 if _prev_c > 0 else 0.0
                _fmt = lambda t: t.strftime("%H:%M:%S") if t else "N/A"  # noqa: E731
                logger.info(
                    f"[ENTRY_TIMING] {ticker} 시그널 발생 "
                    f"BREAKOUT={_fmt(_tracker.get('BREAKOUT'))} "
                    f"VOLUME={_fmt(_tracker.get('VOLUME'))} "
                    f"ADX={_fmt(_tracker.get('ADX'))} "
                    f"최초조건~시그널={'{}초'.format(_delta) if _delta is not None else 'N/A'}, "
                    f"전일종가대비=+{_chg:.1f}%"
                )
                strategy.reset_cond_tracker()
            await signal_queue.put(signal)
            logger.info(f"[TICK_ENTRY] {ticker} 즉시 진입 신호 @ {price:,}")

    async def _on_tick_orb(
        self,
        ticker: str,
        price: float,
        tick: dict,
        signal_queue,
        strategy,
    ) -> None:
        """ORB 전략 틱 처리.

        레인지 계산(09:00~09:04) 후 09:05~09:30 사이 range_high 돌파 시 시그널.
        COND_MET 로그: RANGE_SET (레인지 확정), BREAKOUT (돌파 감지).
        """
        import pandas as _pd

        if ticker in self._state.tick_signaled:
            return

        hist = self._state.candle_history.get(ticker)
        if not hist or len(hist) < 5:
            return

        df = _pd.DataFrame(hist)

        # 레인지 계산 상태 변화 감지 (RANGE_SET 로그)
        was_valid = strategy._range_valid
        signal = strategy.generate_signal(df, tick)
        if not was_valid and strategy._range_valid:
            logger.bind(event="cond_met", ticker=ticker, condition="RANGE_SET").info(
                f"[COND_MET] {ticker} RANGE_SET: "
                f"H={strategy._range_high:,.0f} L={strategy._range_low:,.0f} "
                f"size={strategy._range_size:,.0f} ({strategy._range_size / strategy._range_high * 100:.2f}%)"
            )

        if signal is None:
            return

        # 공통 안전 체크 (모멘텀과 동일)
        if self._risk_manager.is_trading_halted():
            return
        if self._risk_manager.is_ticker_blacklisted(ticker):
            return
        if self._risk_manager.is_in_loss_rest():
            return
        open_pos = self._risk_manager.get_open_positions()
        if len(open_pos) >= self._config.trading.max_positions:
            return
        if not strategy.can_trade():
            return
        if self._vi_handler.is_vi_active(ticker):
            return
        if self._order_tracker is not None and self._order_tracker.get_pending(ticker) is not None:
            return

        self._state.tick_signaled.add(ticker)
        logger.bind(event="cond_met", ticker=ticker, condition="BREAKOUT").info(
            f"[COND_MET] {ticker} BREAKOUT @ {price:,} > range_high={strategy._range_high:,.0f} "
            f"(volume_ok: {not strategy._use_volume_filter or strategy._prev_day_volume <= 0 or int(df['volume'].sum()) >= strategy._prev_day_volume * strategy._rvol_min})"
        )
        await signal_queue.put(signal)
        logger.info(f"[TICK_ENTRY] {ticker} ORB 돌파 진입 @ {price:,}")

    # ── 메인 처리 ──

    async def process_tick(self, tick: dict, signal_queue) -> None:
        """단일 틱 처리 — _tick_consumer 내부 로직."""
        now_ts = _time.time()
        self._tick_count += 1
        if not self._first_tick_logged:
            logger.info(
                f"[TICK] 첫 틱 수신: {tick.get('ticker', '?')} @ {tick.get('price', 0):,}"
            )
            self._first_tick_logged = True
        if now_ts - self._last_tick_log >= 60:
            logger.info(f"[TICK] {self._tick_count}건 수신 (최근 60초)")
            self._tick_count = 0
            self._last_tick_log = now_ts

        try:
            await self._candle_builder.on_tick(tick)
            ticker = tick["ticker"]
            price = tick["price"]
            self._state.latest_prices[ticker] = price
            if self._shadow_tracker is not None:
                self._shadow_tracker.update_prices(ticker, price)
            _prev = self._state.prev_close.get(ticker)
            if _prev:
                try:
                    self._vi_handler.update_from_tick(ticker, price, _prev)
                except Exception as _e:
                    logger.warning(f"[VI] {ticker} update_from_tick 예외: {_e}")

            pos = self._risk_manager.get_position(ticker)
            if pos is None or pos.remaining_qty <= 0:
                await self._on_tick_no_position(ticker, price, tick, signal_queue)
                return

            if self._order_tracker is not None:
                _pending = self._order_tracker.get_pending(ticker)
                if _pending is not None:
                    if pos.highest_price < price:
                        pos.highest_price = price
                    logger.debug(
                        f"[ORDER-TRACK] {ticker} pending {_pending.side} — exit 스킵"
                    )
                    return

            # 갭 전략 강제 청산 시각 체크 (09:45)
            if getattr(pos, "strategy", "") == "gap_pullback":
                gap_strat = self._state.gap_strategies.get(ticker)
                _fc = getattr(gap_strat, "_force_close_time", None) if gap_strat else None
                if _fc and datetime.now().time() >= _fc:
                    await self._handle_exit(ticker, pos, price, "forced_close")
                    return

            # 상한가 즉시 청산
            if self._risk_manager.check_limit_up(ticker, price):
                await self._handle_exit(ticker, pos, price, "limit_up_exit")
                return

            # 손절 체크
            if self._risk_manager.check_stop_loss(ticker, price):
                if getattr(pos, "strategy", "") == "orb":
                    reason_code = "stop_loss"
                else:
                    pure_trail = not getattr(self._config.trading, "atr_tp_enabled", True)
                    is_trailing = pos.tp1_hit or pure_trail
                    if pos.exit_phase == ExitPhase.BREAKEVEN and pos.stop_loss >= pos.entry_price:
                        reason_code = "breakeven_stop"
                    elif is_trailing and price > pos.entry_price * 0.975:
                        reason_code = "trailing_stop"
                    else:
                        reason_code = "stop_loss"
                await self._handle_exit(ticker, pos, price, reason_code)
                return

            _strategy = getattr(pos, "strategy", "")

            # ORB 전략: range 기반 TP 전량 청산 (모멘텀 tp1_sell_ratio 분할매도 미적용)
            if _strategy == "orb" and pos.tp1_price and price >= pos.tp1_price:
                await self._handle_exit(ticker, pos, price, "tp_hit")
                return

            if _strategy != "orb":
                # 모멘텀 둔화 청산
                hist = self._state.candle_history.get(ticker)
                if hist and self._risk_manager.check_momentum_fade(
                    ticker, price, hist, now=datetime.now(),
                ):
                    await self._handle_exit(ticker, pos, price, "momentum_fade")
                    return

                # 횡보 조기 청산
                if self._risk_manager.check_stale_position(ticker, price, now=datetime.now()):
                    await self._handle_exit(ticker, pos, price, "stale_exit")
                    return

                # TP1 체크 (현재 atr_tp_enabled:false — dead path)
                if self._risk_manager.check_tp1(ticker, price):
                    sell_qty = int(pos.remaining_qty * self._config.trading.tp1_sell_ratio)
                    entry = pos.entry_price
                    pnl = (price - entry) * sell_qty
                    pnl_pct = ((price / entry) - 1) * 100 if entry > 0 else 0
                    strategy_name = pos.strategy or "unknown"
                    await self._order_manager.execute_sell_tp1(
                        ticker=ticker, price=int(price), remaining_qty=pos.remaining_qty,
                        strategy=strategy_name, pnl=pnl, pnl_pct=pnl_pct, exit_reason="tp1_hit",
                    )
                    self._risk_manager.mark_tp1_hit(ticker, sell_qty, sell_price=price)
                    self._state.rt_wins += 1
                    logger.info(f"TP1 실행: {ticker} {sell_qty}주 @ {price:,} PnL={pnl:+,.0f}")
                    self._on_trade_executed({
                        "time": datetime.now().strftime("%H:%M:%S"),
                        "side": "sell", "ticker": ticker,
                        "price": int(price), "qty": sell_qty,
                        "pnl": int(pnl), "reason": "tp1_hit",
                    })
                    return

                # 트레일링 스톱 갱신 (ATR Chandelier + BE3, ORB 미적용 — range 기반 고정 SL)
                daily_pct = self._state.ticker_atr_pct.get(ticker)
                atr_pct = (daily_pct / 100.0) if daily_pct else self._intraday_atr_pct(ticker)
                self._risk_manager.update_trailing_stop(ticker, price, atr_pct=atr_pct, now=datetime.now())

        except Exception as e:
            logger.error(f"tick_consumer 오류: {e}")

    async def _handle_exit(self, ticker: str, pos, price: float, reason_code: str) -> None:
        """공통 청산 처리: execute_sell → paper settle 또는 real submit."""
        qty = pos.remaining_qty
        entry = pos.entry_price
        pnl = (price - entry) * qty
        pnl_pct = ((price / entry) - 1) * 100 if entry > 0 else 0
        strategy_name = pos.strategy or "unknown"
        prefer_best = self._vi_handler.should_use_best_limit(ticker)

        if reason_code == "limit_up_exit":
            result = await self._order_manager.execute_sell_stop(
                ticker=ticker, qty=qty, price=int(price),
                strategy=strategy_name, pnl=pnl, pnl_pct=pnl_pct,
                exit_reason=reason_code,
            )
        else:
            result = await self._order_manager.execute_sell_stop(
                ticker=ticker, qty=qty, price=int(price),
                strategy=strategy_name, pnl=pnl, pnl_pct=pnl_pct,
                exit_reason=reason_code,
                prefer_best_limit=prefer_best,
                on_rejection=lambda tk, rt: self._vi_handler.flag_suspected(
                    tk, f"주문 거부 (rt_cd={rt})"
                ),
            )

        if result is None:
            if reason_code == "limit_up_exit":
                new_stop = self._risk_manager.raise_stop_to_limit_up_floor(ticker)
                logger.warning(
                    f"limit_up_exit 실패 → stop 상향: {ticker} new_stop={new_stop:,.0f}"
                )
            return

        if self._paper_mode:
            _entry_time = pos.entry_time
            self._risk_manager.settle_sell(ticker, price, qty)
            if pnl >= 0:
                self._state.rt_wins += 1
            else:
                self._state.rt_losses += 1
            logger.bind(
                event="exit", ticker=ticker, reason=reason_code,
                price=int(price), qty=qty, pnl=int(pnl), pnl_pct=round(pnl_pct, 2),
                hold_minutes=round(
                    (datetime.now() - _entry_time).total_seconds() / 60, 1
                ) if _entry_time else None,
            ).info(f"{reason_code} 실행: {ticker} {qty}주 @ {price:,} PnL={pnl:+,.0f}")
            strat_info = self._state.active_strategies.get(ticker)
            if strat_info:
                strat_info["strategy"].on_exit()
            self._on_trade_executed({
                "time": datetime.now().strftime("%H:%M:%S"),
                "side": "sell", "ticker": ticker,
                "price": int(price), "qty": qty,
                "pnl": int(pnl), "reason": reason_code,
            })
        else:
            self._order_tracker.submit(result["order_no"], ticker, "sell", qty)
            if reason_code == "limit_up_exit":
                self._state.limit_up_exit_pending.add(ticker)
            logger.info(
                f"[ORDER-TRACK] {result['order_no']} SUBMIT {ticker} sell {qty} ({reason_code})"
            )

    def check_no_tick_warning(self) -> None:
        """5분간 틱 수신 0건 경고 (consumer 루프의 timeout 경로에서 호출)."""
        if _time.time() - self._last_tick_log >= 300 and self._tick_count == 0:
            logger.warning("[TICK] 5분간 틱 수신 0건 — WS 연결 확인 필요")
            self._last_tick_log = _time.time()

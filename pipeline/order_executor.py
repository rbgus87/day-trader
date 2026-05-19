"""pipeline/order_executor.py — 시그널 → 주문 실행 + 체결 확인.

_signal_consumer + _handle_fill + _verify_fill_via_rest 로직을 engine_worker에서 분리.
PyQt6 미사용 — trade_executed 이벤트는 on_trade_executed 콜백으로 전달.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Callable

from loguru import logger

from pipeline.trading_state import TradingState

# 키움 WS "00" 주문체결 메시지 필드 코드 (공식 문서 확정)
_WS00_ORDER_NO       = "9203"  # 주문번호
_WS00_ORDER_STATUS   = "913"   # 주문상태 (접수/체결/확인/취소/거부)
_WS00_TICKER         = "9001"  # 종목코드
_WS00_ORDER_QTY      = "900"   # 주문수량
_WS00_ORDER_PRICE    = "901"   # 주문가격
_WS00_UNFILLED_QTY   = "902"   # 미체결수량
_WS00_FILL_AMOUNT    = "903"   # 체결누계금액
_WS00_FILL_PRICE     = "910"   # 체결가
_WS00_FILL_QTY       = "911"   # 체결량 (단위 체결)
_WS00_UNIT_FILL_PRICE = "914"  # 단위체결가
_WS00_UNIT_FILL_QTY  = "915"   # 단위체결량
_WS00_ORDER_TYPE     = "905"   # 주문구분 (+매수/-매도)
_WS00_TRADE_TYPE     = "906"   # 매매구분 (보통/시장가/조건부 등)
_WS00_BUY_SELL       = "907"   # 매도수구분 (1:매도, 2:매수)
_WS00_TIME           = "908"   # 주문/체결시간
_WS00_REJECT_REASON  = "919"   # 거부사유


class OrderExecutor:
    """시그널 → 주문 실행. 체결 확인(WS 00 + REST 폴백)도 담당."""

    def __init__(
        self,
        risk_manager,
        order_manager,
        order_tracker,
        vi_handler,
        orderbook_manager,
        signal_scorer,
        market_filter,
        config,
        notifier,
        state: TradingState,
        paper_mode: bool,
        on_trade_executed: Callable[[dict], None],
    ):
        self._risk_manager = risk_manager
        self._order_manager = order_manager
        self._order_tracker = order_tracker
        self._vi_handler = vi_handler
        self._orderbook_manager = orderbook_manager
        self._signal_scorer = signal_scorer
        self._market_filter = market_filter
        self._config = config
        self._notifier = notifier
        self._state = state
        self._paper_mode = paper_mode
        self._on_trade_executed = on_trade_executed

    async def execute_signal(self, signal) -> dict | None:
        """시그널 → 주문. 매수 성공 시 emit용 dict 반환, 차단/실패 시 None."""
        try:
            if signal.side != "buy" or signal.ticker not in self._state.active_strategies:
                return None

            # 시장 필터
            if self._market_filter is not None:
                market = self._state.ticker_markets.get(signal.ticker, "unknown")
                if not self._market_filter.is_allowed(market):
                    logger.bind(
                        event="signal_blocked", ticker=signal.ticker,
                        price=int(signal.price), reason="market_filter", detail=market,
                    ).info(f"[MARKET] 매수 차단: {signal.ticker} ({market})")
                    if self._state.active_strategies.get(signal.ticker) and self._state.active_strategies:
                        from core.shadow_tracker import ShadowTracker
                        if hasattr(self, '_shadow_tracker_ref') and self._shadow_tracker_ref:
                            self._shadow_tracker_ref.on_blocked(
                                signal.ticker, signal.price, datetime.now(), "market_filter",
                            )
                    return None

            # 장중 시장 필터 (force_allow 오버라이드 반영)
            if (
                getattr(self._config.trading, "intraday_market_filter_enabled", False)
                and self._market_filter is not None
            ):
                _intraday_market = self._state.ticker_markets.get(signal.ticker, "unknown")
                if not self._market_filter.is_intraday_allowed(_intraday_market):
                    logger.bind(
                        event="signal_blocked", ticker=signal.ticker,
                        price=int(signal.price), reason="intraday_market", detail=_intraday_market,
                    ).info(f"[INTRADAY] 장중 차단: {signal.ticker} ({_intraday_market})")
                    return None

            # 포지션 한도 재확인
            open_pos = self._risk_manager.get_open_positions()
            if len(open_pos) >= self._config.trading.max_positions:
                logger.bind(
                    event="signal_blocked", ticker=signal.ticker,
                    price=int(signal.price), reason="max_positions",
                    open_count=len(open_pos),
                ).info(
                    f"포지션 한도 ({self._config.trading.max_positions}), 무시: {signal.ticker}"
                )
                return None

            # VI 활성 체크
            from core.vi_handler import VIState
            vi_state = self._vi_handler.get_vi_state(signal.ticker)
            if vi_state != VIState.NORMAL:
                logger.bind(
                    event="signal_blocked", ticker=signal.ticker,
                    price=int(signal.price), reason="vi_active", detail=vi_state.value,
                ).info(f"[VI] {signal.ticker} 매수 차단 — state={vi_state.value}")
                return None

            # OBI 필터
            if self._config.trading.obi_filter_enabled and self._orderbook_manager is not None:
                obi = self._orderbook_manager.get_obi(signal.ticker)
                if obi is not None and obi < self._config.trading.obi_min:
                    logger.bind(
                        event="signal_blocked", ticker=signal.ticker,
                        price=int(signal.price), reason="obi_low", obi=round(obi, 3),
                    ).info(f"[OBI] 매수세 부족 차단: {signal.ticker} OBI={obi:.3f}")
                    return None
                spread = self._orderbook_manager.get_spread(signal.ticker)
                if spread is not None and spread > self._config.trading.spread_max_pct:
                    logger.bind(
                        event="signal_blocked", ticker=signal.ticker,
                        price=int(signal.price), reason="spread_high", spread=round(spread, 4),
                    ).info(f"[OBI] 스프레드 과대 차단: {signal.ticker} spread={spread:.4f}")
                    return None
                if self._config.trading.ask_wall_block_enabled:
                    if self._orderbook_manager.has_ask_wall(signal.ticker, signal.price):
                        logger.bind(
                            event="signal_blocked", ticker=signal.ticker,
                            price=int(signal.price), reason="ask_wall",
                        ).info(f"[OBI] 매도벽 감지 차단: {signal.ticker}")
                        return None

            # 시그널 스코어링
            if (
                getattr(self._config.trading, "signal_scoring_enabled", False)
                and self._signal_scorer is not None
                and signal.context
            ):
                score = self._signal_scorer.score(signal.context)
                min_score = getattr(self._config.trading, "signal_min_score", 60.0)
                logger.bind(
                    event="signal_scored", ticker=signal.ticker,
                    score=score.total, components=score.components,
                ).debug(f"[SCORE] {signal.ticker} {score.total:.1f}점")
                if score.total < min_score:
                    logger.bind(
                        event="signal_blocked", ticker=signal.ticker,
                        price=int(signal.price), reason="score_low",
                        score=score.total, min_score=min_score,
                    ).info(
                        f"[SCORE] 품질 미달 차단: {signal.ticker} "
                        f"{score.total:.1f}점 < {min_score:.0f}점"
                    )
                    return None

            strategy = self._state.active_strategies[signal.ticker]["strategy"]
            sl = strategy.get_stop_loss(signal.price)
            tp1 = strategy.get_take_profit(signal.price)

            capital = self._risk_manager.available_capital
            if capital <= 0:
                capital = self._config.trading.initial_capital
            if self._config.trading.volatility_sizing_enabled:
                atr_pct = self._state.ticker_atr_pct.get(signal.ticker)
                if atr_pct and atr_pct > 0:
                    from backtest.backtester import calc_sizing_position_value
                    pos_val = calc_sizing_position_value(
                        self._config.trading, atr_pct / 100.0, capital
                    )
                    max_qty = int(pos_val / signal.price)
                else:
                    max_qty = int(capital / self._config.trading.max_positions / signal.price)
            else:
                position_capital = capital / self._config.trading.max_positions
                max_qty = int(position_capital / signal.price)
            total_qty = max(int(max_qty * self._risk_manager.position_scale), 1)

            cost = signal.price * total_qty
            if cost > self._risk_manager.available_capital:
                logger.warning(
                    f"자본 부족 — 매수 스킵: {signal.ticker} "
                    f"필요={cost:,.0f} 가용={self._risk_manager.available_capital:,.0f}"
                )
                return None

            result = await self._order_manager.execute_buy(
                ticker=signal.ticker, price=int(signal.price),
                total_qty=total_qty, strategy=signal.strategy,
            )
            if not result:
                return None

            initial_status = "confirmed" if self._paper_mode else "pending"
            self._risk_manager.register_position(
                ticker=signal.ticker, entry_price=signal.price,
                qty=result["qty"], stop_loss=sl, tp1_price=tp1,
                strategy=signal.strategy or "",
                limit_up_price=self._state.limit_up_map.get(signal.ticker),
                status=initial_status,
            )
            if not self._paper_mode and self._order_tracker is not None:
                self._order_tracker.submit(
                    order_no=result["order_no"], ticker=signal.ticker,
                    side="buy", qty=result["qty"],
                )
                logger.info(
                    f"[ORDER-TRACK] {result['order_no']} SUBMIT "
                    f"{signal.ticker} buy {result['qty']}"
                )
            strategy.on_entry()

            # ATR 디버그 덤프
            try:
                hist = self._state.candle_history.get(signal.ticker)
                hist_len = len(hist) if hist is not None else 0
                daily = self._state.ticker_atr_pct.get(signal.ticker)
                daily_str = f"{daily:.2f}%" if daily else "None"
                logger.info(
                    f"[ATR-DBG] {signal.ticker} entry hist_len={hist_len} "
                    f"daily_atr={daily_str} "
                    f"min_clamp={self._config.trading.atr_trail_min_pct * 100:.2f}%"
                )
            except Exception as e:
                logger.debug(f"[ATR-DBG] {signal.ticker} dump 실패: {e}")

            _prev_h = self._state.prev_high_map.get(signal.ticker, 0.0)
            logger.bind(
                event="entry", ticker=signal.ticker, price=int(signal.price),
                qty=result["qty"], strategy=signal.strategy or "momentum",
                prev_high=_prev_h,
                breakout_pct=round((signal.price / _prev_h - 1) * 100, 2) if _prev_h > 0 else 0.0,
                atr_pct=round(self._state.ticker_atr_pct.get(signal.ticker) or 0.0, 2),
            ).info(f"[J] entry: {signal.ticker} {result['qty']}주 @ {signal.price:,}")

            return {
                "time": datetime.now().strftime("%H:%M:%S"),
                "side": "buy", "ticker": signal.ticker,
                "price": int(signal.price), "qty": result["qty"],
                "pnl": None, "reason": signal.strategy or "entry",
            }

        except Exception as e:
            logger.error(f"signal_consumer 오류: {e}")
            return None

    def set_shadow_tracker(self, shadow_tracker) -> None:
        """섀도우 트래커 참조 설정 (순환 참조 없이 주입)."""
        self._shadow_tracker_ref = shadow_tracker

    # ── 체결 확인 ──

    async def handle_fill(self, order_no: str) -> None:
        """FILLED 상태 도달 시 risk_manager 상태 갱신 + trade_executed 콜백."""
        if self._order_tracker is None:
            return
        order = self._order_tracker.get_by_order_no(order_no)
        if order is None:
            logger.warning(f"[ORDER-TRACK] _handle_fill {order_no} 알 수 없음")
            return
        ticker = order.ticker
        self._state.limit_up_exit_pending.discard(ticker)
        self._state.timeout_counters[ticker] = 0
        if order.side == "buy":
            self._risk_manager.mark_confirmed(ticker)
            logger.info(f"[ORDER-TRACK] {order_no} FILLED → mark_confirmed {ticker}")
        elif order.side == "sell":
            pos = self._risk_manager.get_position(ticker)
            entry = pos.entry_price if pos else 0
            pnl = (order.filled_price - entry) * order.filled_qty if entry > 0 else 0
            pnl_pct = ((order.filled_price / entry) - 1) if entry > 0 else 0
            self._risk_manager.settle_sell(ticker, order.filled_price, order.filled_qty)
            if pnl >= 0:
                self._state.rt_wins += 1
            else:
                self._state.rt_losses += 1
            logger.bind(
                event="exit", ticker=ticker, reason="ws_filled",
                price=int(order.filled_price), qty=order.filled_qty,
                pnl=int(pnl), pnl_pct=round(pnl_pct * 100, 2),
            ).info(
                f"[ORDER-TRACK] {order_no} FILLED → settle_sell {ticker} "
                f"@ {order.filled_price:,.0f} PnL={pnl:+,.0f}"
            )
            strat_info = self._state.active_strategies.get(ticker)
            if strat_info:
                strat_info["strategy"].on_exit()
            self._on_trade_executed({
                "time": datetime.now().strftime("%H:%M:%S"),
                "side": "sell", "ticker": ticker,
                "price": int(order.filled_price), "qty": order.filled_qty,
                "pnl": int(pnl), "reason": "ws_filled",
            })

    async def verify_fill_via_rest(self, order, rest_client, latest_prices: dict) -> dict | None:
        """REST ka10070 잔고 폴백 1회."""
        try:
            raw = await rest_client.get_account_balance()
        except Exception as e:
            logger.error(f"[ORDER-TRACK] ka10070 폴백 실패: {e}")
            return None
        items = (raw or {}).get("output", []) or (raw or {}).get("output1", [])
        if not isinstance(items, list):
            return None
        ticker_found = False
        for item in items:
            if str(item.get("stk_cd", "")).strip() == order.ticker:
                ticker_found = True
                try:
                    qty = abs(int(item.get("hldn_qty", 0) or 0))
                    price = abs(float(item.get("avg_pric", 0) or 0))
                except (ValueError, TypeError):
                    return None
                if order.side == "buy" and qty >= order.requested_qty:
                    return {"qty": order.requested_qty, "price": price}
                if order.side == "sell" and qty == 0:
                    fallback_price = latest_prices.get(order.ticker, 0.0)
                    return {"qty": order.requested_qty, "price": fallback_price}
        if order.side == "sell" and not ticker_found:
            fallback_price = latest_prices.get(order.ticker, 0.0)
            return {"qty": order.requested_qty, "price": fallback_price}
        return None

    # ── Consumer coroutines (engine_worker에서 위임) ──

    async def run_order_confirmation_loop(
        self,
        order_queue,
        stop_event,
        running_getter,
    ) -> None:
        """WS '00' 체결통보 → OrderTracker 업데이트 → handle_fill."""
        from core.order_tracker import OrderStatus
        while running_getter() and not stop_event.is_set():
            try:
                exec_data = await asyncio.wait_for(order_queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            try:
                if self._order_tracker is None:
                    logger.debug(f"[ORDER-TRACK] tracker 미초기화 — skip: {exec_data}")
                    continue
                values = exec_data.get("values", {})
                order_no = str(values.get(_WS00_ORDER_NO, ""))
                filled_qty = abs(int(values.get(_WS00_FILL_QTY, 0) or 0))
                filled_price = abs(float(values.get(_WS00_UNIT_FILL_PRICE, 0) or 0))
                if not order_no or filled_qty == 0:
                    logger.warning(f"[ORDER-TRACK] 무효 체결: order_no={order_no} qty={filled_qty}")
                    continue
                updated = self._order_tracker.on_fill(order_no, filled_qty, filled_price)
                if updated is None:
                    continue
                logger.info(
                    f"[ORDER-TRACK] {order_no} FILL "
                    f"{updated.filled_qty}/{updated.requested_qty} "
                    f"@ {filled_price:,.0f} (status={updated.status.value})"
                )
                if updated.status == OrderStatus.FILLED:
                    await self.handle_fill(order_no)
            except Exception as e:
                logger.error(f"[ORDER-TRACK] confirmation_consumer 오류: {e}")

    async def run_order_timeout_loop(
        self,
        stop_event,
        running_getter,
        rest_client,
    ) -> None:
        """미체결 주문 타임아웃 감시 → REST 폴백 또는 취소."""
        from core.order_tracker import OrderStatus
        while running_getter() and not stop_event.is_set():
            try:
                await asyncio.sleep(1.0)
            except asyncio.CancelledError:
                break
            try:
                if self._order_tracker is None:
                    continue
                timeout_sec = self._config.trading.order_confirmation_timeout_sec
                stale = self._order_tracker.get_unfilled_older_than(timeout_sec)
                for order in stale:
                    current = self._order_tracker.get_by_order_no(order.order_no)
                    if current is None:
                        continue
                    if current.status in (OrderStatus.FILLED, OrderStatus.FAILED, OrderStatus.TIMEOUT):
                        continue
                    logger.warning(f"[ORDER-TRACK] {order.order_no} TIMEOUT — REST 폴백")
                    confirmed = await self.verify_fill_via_rest(
                        order, rest_client, self._state.latest_prices,
                    )
                    if confirmed is not None:
                        self._order_tracker.on_fill(order.order_no, confirmed["qty"], confirmed["price"])
                        updated = self._order_tracker.get_by_order_no(order.order_no)
                        if updated and updated.status == OrderStatus.FILLED:
                            await self.handle_fill(order.order_no)
                    else:
                        self._order_tracker.mark_timeout(order.order_no)
                        if order.ticker in self._state.limit_up_exit_pending:
                            self._state.limit_up_exit_pending.discard(order.ticker)
                            new_stop = self._risk_manager.raise_stop_to_limit_up_floor(order.ticker)
                            logger.warning(
                                f"[ORDER-TRACK] limit_up_exit TIMEOUT → stop 상향: "
                                f"{order.ticker} new_stop={new_stop:,.0f}"
                            )
                        if order.side == "buy":
                            try:
                                await rest_client.cancel_order(
                                    order.order_no, order.ticker, order.requested_qty,
                                )
                            except Exception as e:
                                logger.error(f"[ORDER-TRACK] cancel_order 실패 {order.order_no}: {e}")
                        self._state.timeout_counters[order.ticker] = (
                            self._state.timeout_counters.get(order.ticker, 0) + 1
                        )
                        if self._notifier:
                            self._notifier.send_urgent(
                                f"[ORDER-TRACK] {order.ticker} {order.side} TIMEOUT ({order.order_no})"
                            )
                        threshold = self._config.trading.order_timeout_consecutive_threshold
                        if self._state.timeout_counters[order.ticker] >= threshold and self._notifier:
                            self._notifier.send_urgent(
                                f"[ORDER-TRACK][CRITICAL] {order.ticker} 연속 TIMEOUT "
                                f"{self._state.timeout_counters[order.ticker]}회"
                            )
            except Exception as e:
                logger.error(f"[ORDER-TRACK] timeout_checker 오류: {e}")

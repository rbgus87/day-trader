"""pipeline/ui_emitter.py — GUI emit 전담.

EngineSignals.*_updated 발행 로직을 engine_worker에서 분리.
EngineSignals 객체를 생성자로 받아 PyQt6를 직접 import하지 않음.
"""
from __future__ import annotations

import asyncio
from datetime import datetime

from loguru import logger

from core.position import ExitPhase
from pipeline.trading_state import TradingState


class UIEmitter:
    """2초 주기 UI 갱신 emit 전담."""

    def __init__(self, signals, state: TradingState, risk_manager, config, mode: str, ws_client, db):
        self._signals = signals
        self._state = state
        self._risk_manager = risk_manager
        self._config = config
        self._mode = mode
        self._ws_client = ws_client
        self._db = db
        self._loop: asyncio.AbstractEventLoop | None = None
        self._trades_fetch_running = False
        self._last_pos_tickers: list[str] = []

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def emit_all(self) -> None:
        for fn, label in [
            (self.emit_status, "status"),
            (self.emit_positions, "positions"),
            (self.emit_trades, "trades"),
            (self.emit_pnl, "pnl"),
            (self.emit_candidates, "candidates"),
            (self.emit_watchlist, "watchlist"),
        ]:
            try:
                fn()
            except Exception as e:
                logger.error(f"emit_{label} 오류: {e}")

    def emit_status(self) -> None:
        strategy_name = type(self._state.active_strategy).__name__ if self._state.active_strategy else ""
        force = getattr(self._config, "force_strategy", "") if self._config else ""
        rm = self._risk_manager
        daily_pnl = rm._daily_pnl if rm else 0.0
        capital = rm._daily_capital if rm and rm._daily_capital > 0 else 1
        strat = self._state.active_strategy
        wins = self._state.rt_wins
        losses = self._state.rt_losses
        positions_count = len(rm.get_open_positions()) if rm else 0
        self._signals.status_updated.emit({
            "mode": self._mode, "running": True,
            "halted": rm._halted if rm else False,
            "strategy": strategy_name, "target": "", "target_name": "",
            "force_strategy": force,
            "positions_count": positions_count,
            "max_positions": self._config.trading.max_positions if self._config else 3,
            "active_count": len(self._state.active_strategies),
            "intraday_count": self._state.intraday_add_count,
            "watched_tickers": list(self._state.active_strategies.keys())[:5],
            "ws_connected": self._ws_client.connected if self._ws_client else False,
            "daily_pnl": daily_pnl,
            "daily_pnl_pct": (daily_pnl / capital) * 100,
            "trades_count": strat._trade_count if strat else 0,
            "max_trades": self._config.trading.max_trades_per_day if self._config else 3,
            "wins": wins, "losses": losses,
            "win_rate": (wins / (wins + losses) * 100) if (wins + losses) > 0 else 0,
            "available_capital": rm.available_capital if rm else 0,
            "initial_capital": self._config.trading.initial_capital if self._config else 0,
            "open_positions_count": positions_count,
        })

    def emit_positions(self) -> None:
        if not self._risk_manager:
            return
        try:
            open_pos = self._risk_manager.get_open_positions()
            current_tickers = sorted(open_pos.keys())
            if current_tickers != self._last_pos_tickers:
                logger.info(
                    f"[POS] 보유 포지션: {len(current_tickers)}건" +
                    (f" — {current_tickers}" if current_tickers else "")
                )
                self._last_pos_tickers = current_tickers
            positions = []
            for ticker, pos in open_pos.items():
                entry = pos.entry_price
                current = self._state.latest_prices.get(ticker, entry)
                pnl_pct = ((current - entry) / entry * 100) if entry > 0 else 0
                name = self._state.active_strategies.get(ticker, {}).get("name", "")
                positions.append({
                    "ticker": ticker, "name": name, "strategy": pos.strategy,
                    "entry_price": entry, "current_price": current, "pnl_pct": pnl_pct,
                    "qty": pos.qty, "remaining_qty": pos.remaining_qty,
                    "stop_loss": pos.stop_loss, "tp1_price": pos.tp1_price,
                    "tp1_hit": pos.tp1_hit,
                    "breakeven_active": pos.exit_phase == ExitPhase.BREAKEVEN,
                    "highest_price": pos.highest_price,
                    "entry_time": pos.entry_time,
                    "status": "TP1 hit" if pos.tp1_hit else "보유 중",
                })
            self._signals.positions_updated.emit(positions)
        except Exception as e:
            logger.error(f"포지션 emit 실패: {e}")

    def emit_trades(self) -> None:
        if not self._db or not self._loop:
            return
        if self._trades_fetch_running:
            return
        try:
            self._trades_fetch_running = True
            asyncio.run_coroutine_threadsafe(self._fetch_and_emit_trades(), self._loop)
        except Exception as e:
            logger.debug(f"체결 내역 조회 스케줄 실패: {e}")
            self._trades_fetch_running = False

    async def _fetch_and_emit_trades(self) -> None:
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            trades = await asyncio.wait_for(
                self._db.fetch_all(
                    "SELECT * FROM trades WHERE traded_at LIKE ? || '%' ORDER BY traded_at DESC",
                    (today,),
                ),
                timeout=5.0,
            )
            for trade in trades:
                ticker = trade.get("ticker", "")
                if ticker in self._state.active_strategies:
                    trade["name"] = self._state.active_strategies[ticker].get("name", "")
                elif ticker in self._state.ticker_names:
                    trade["name"] = self._state.ticker_names[ticker]
            self._signals.trades_updated.emit(trades)
        except asyncio.TimeoutError:
            logger.warning("당일 체결 조회 타임아웃")
        except Exception as e:
            logger.error(f"당일 체결 조회 오류: {e}")
        finally:
            self._trades_fetch_running = False

    def emit_pnl(self) -> None:
        if not self._risk_manager:
            return
        try:
            self._signals.pnl_updated.emit(self._risk_manager._daily_pnl)
        except Exception as e:
            logger.debug(f"PnL emit 실패: {e}")

    def emit_watchlist(self) -> None:
        if not self._state.active_strategies:
            return
        try:
            open_pos_tickers = set(self._risk_manager.get_open_positions().keys()) if self._risk_manager else set()
            items = []
            for ticker, info in self._state.active_strategies.items():
                current = self._state.latest_prices.get(ticker, 0)
                prev_close = self._state.prev_close.get(ticker, 0)
                prev_high = self._state.prev_high_map.get(ticker, 0)
                change_pct = ((current / prev_close) - 1) * 100 if prev_close > 0 and current > 0 else 0
                breakout_pct = ((current / prev_high) - 1) * 100 if prev_high > 0 and current > 0 else -999
                items.append({
                    "ticker": ticker, "name": info.get("name", ticker),
                    "market": self._state.ticker_markets.get(ticker, "unknown"),
                    "atr_pct": self._state.ticker_atr_pct.get(ticker),
                    "current_price": current, "change_pct": change_pct,
                    "prev_high": prev_high, "breakout_pct": breakout_pct,
                    "has_position": ticker in open_pos_tickers,
                    "source": self._state.ticker_sources.get(ticker, "day_momentum"),
                })
            items.sort(key=lambda x: x["breakout_pct"], reverse=True)
            self._signals.watchlist_updated.emit(items)
        except Exception as e:
            logger.debug(f"watchlist emit 실패: {e}")

    def emit_candidates(self) -> None:
        try:
            enriched = []
            for c in self._state.screener_results:
                ticker = c.get("ticker", "")
                current_price = self._state.latest_prices.get(ticker, 0)
                prev_close = c.get("prev_close", 0)
                change_pct = (
                    ((current_price - prev_close) / prev_close * 100)
                    if prev_close > 0 and current_price > 0 else 0
                )
                enriched.append({**c, "current_price": current_price, "change_pct": round(change_pct, 2)})
            self._signals.candidates_updated.emit(enriched)
        except Exception as e:
            logger.debug(f"후보 종목 emit 실패: {e}")

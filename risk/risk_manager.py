"""risk/risk_manager.py — 리스크 관리 (손절, 일일한도, 강제청산, 연속손실, 시간손절)."""

import sqlite3
from datetime import datetime, timedelta

from loguru import logger

from config.settings import TradingConfig
from data.db_manager import DbManager
from notification.telegram_bot import TelegramNotifier


class RiskManager:
    """포지션 레벨 + 계좌 레벨 리스크 관리."""

    def __init__(
        self,
        trading_config: TradingConfig,
        db: DbManager,
        notifier: TelegramNotifier,
    ):
        self._config = trading_config
        self._db = db
        self._notifier = notifier
        self._positions: dict[str, dict] = {}
        self._daily_pnl: float = 0.0
        self._daily_capital: float = 0.0
        self._halted: bool = False
        self._position_scale: float = 1.0

    def register_position(
        self, ticker: str, entry_price: float, qty: int, stop_loss: float,
        tp1_price: float | None = None, trailing_pct: float | None = None,
        strategy: str = "",
    ) -> None:
        self._positions[ticker] = {
            "entry_price": entry_price,
            "stop_loss": stop_loss,
            "qty": qty,
            "remaining_qty": qty,
            "tp1_price": tp1_price,
            "trailing_pct": trailing_pct or self._config.trailing_stop_pct,
            "highest_price": entry_price,
            "tp1_hit": False,
            "entry_time": datetime.now(),
            "strategy": strategy,
        }
        # 자본 차감
        cost = entry_price * qty
        self._daily_capital -= cost

    def remove_position(self, ticker: str) -> None:
        self._positions.pop(ticker, None)

    def get_position(self, ticker: str) -> dict | None:
        return self._positions.get(ticker)

    def get_open_positions(self) -> dict[str, dict]:
        """보유 중인 포지션 목록 반환 (읽기 전용 복사본)."""
        return {k: {**v} for k, v in self._positions.items() if v.get("remaining_qty", 0) > 0}

    def check_stop_loss(self, ticker: str, current_price: float) -> bool:
        pos = self._positions.get(ticker)
        if not pos:
            return False
        return current_price <= pos["stop_loss"]

    def update_trailing_stop(self, ticker: str, current_price: float) -> None:
        pos = self._positions.get(ticker)
        if not pos or not pos.get("tp1_hit"):
            return
        if current_price > pos["highest_price"]:
            pos["highest_price"] = current_price

            # Phase 2 Day 7: ATR 기반 Chandelier 트레일링 (폴백: 고정 trailing_pct)
            new_stop = None
            if getattr(self._config, "atr_trail_enabled", False):
                try:
                    from core.indicators import (
                        calculate_atr_trailing_stop,
                        get_latest_atr,
                    )
                    atr_pct = get_latest_atr("daytrader.db", ticker)
                    if atr_pct is not None:
                        new_stop = calculate_atr_trailing_stop(
                            peak_price=current_price,
                            atr_pct=atr_pct,
                            multiplier=self._config.atr_trail_multiplier,
                            min_pct=self._config.atr_trail_min_pct,
                            max_pct=self._config.atr_trail_max_pct,
                        )
                except Exception:
                    new_stop = None
            if new_stop is None:
                new_stop = current_price * (1 - pos["trailing_pct"])

            # 트레일링은 위로만 (기존 stop_loss 아래로 내려가지 않음)
            pos["stop_loss"] = max(pos["stop_loss"], new_stop)

    def check_tp1(self, ticker: str, current_price: float) -> bool:
        pos = self._positions.get(ticker)
        if not pos or pos.get("tp1_hit"):
            return False
        if pos["tp1_price"] and current_price >= pos["tp1_price"]:
            return True
        return False

    def check_time_stop(
        self, ticker: str, current_price: float,
        time_stop_minutes: int = 60, min_profit: float = 0.005,
    ) -> bool:
        """진입 후 일정 시간 경과 + 최소 수익 미달 시 True."""
        pos = self._positions.get(ticker)
        if not pos or not pos.get("entry_time"):
            return False
        elapsed = (datetime.now() - pos["entry_time"]).total_seconds() / 60
        if elapsed < time_stop_minutes:
            return False
        profit_pct = (current_price - pos["entry_price"]) / pos["entry_price"]
        return profit_pct < min_profit

    def settle_sell(self, ticker: str, sell_price: float, sell_qty: int) -> float:
        """매도 정산 — 자본 복구 + PnL 반환."""
        pos = self._positions.get(ticker)
        if not pos:
            return 0.0
        entry_price = pos["entry_price"]
        proceeds = sell_price * sell_qty
        pnl = (sell_price - entry_price) * sell_qty
        self._daily_capital += proceeds
        self._daily_pnl += pnl
        pos["remaining_qty"] -= sell_qty
        if pos["remaining_qty"] <= 0:
            self._positions.pop(ticker, None)
        return pnl

    def mark_tp1_hit(self, ticker: str, sold_qty: int, sell_price: float = 0) -> None:
        pos = self._positions.get(ticker)
        if pos:
            pos["tp1_hit"] = True
            pos["remaining_qty"] -= sold_qty
            pos["stop_loss"] = pos["entry_price"]
            if sell_price > 0:
                self._daily_capital += sell_price * sold_qty
                self._daily_pnl += (sell_price - pos["entry_price"]) * sold_qty

    def is_trading_halted(self) -> bool:
        if self._halted:
            return True
        # Phase 2 Day 10: enabled=False 면 한도 체크 생략 (halt는 외부에서만)
        if not getattr(self._config, "daily_max_loss_enabled", True):
            return False
        if self._daily_capital <= 0:
            return False
        loss_pct = self._daily_pnl / self._daily_capital
        if loss_pct <= self._config.daily_max_loss_pct:
            self._halted = True
            logger.warning(f"일일 손실 한도 도달: {loss_pct:.2%}")
            return True
        return False

    def record_pnl(self, pnl: float) -> None:
        self._daily_pnl += pnl

    def is_ticker_blacklisted(
        self,
        ticker: str,
        current_date: datetime | None = None,
        db_path: str = "daytrader.db",
    ) -> bool:
        """Phase 2 Day 10: 최근 lookback_days 내 손실 횟수 ≥ threshold면 블랙.

        현재는 "최근 N일 내 M회 이상 손실"만 체크 (days 만료 조건은 향후 확장).
        DB 조회 실패 시 False (보수적으로 매수 허용).
        """
        if not getattr(self._config, "blacklist_enabled", False):
            return False

        now = current_date or datetime.now()
        lookback = self._config.blacklist_lookback_days
        threshold = self._config.blacklist_loss_threshold
        since = (now - timedelta(days=lookback)).strftime("%Y-%m-%d")

        try:
            conn = sqlite3.connect(db_path)
        except Exception:
            return False
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM trades "
                "WHERE ticker=? AND side='sell' AND pnl<0 "
                "AND date(traded_at)>=?",
                (ticker, since),
            ).fetchone()
        except Exception:
            conn.close()
            return False
        conn.close()

        loss_count = row[0] if row else 0
        return loss_count >= threshold

    def set_daily_capital(self, capital: float) -> None:
        self._daily_capital = capital

    async def check_consecutive_losses(self) -> bool:
        rows = await self._db.fetch_all(
            "SELECT total_pnl FROM daily_pnl ORDER BY date DESC LIMIT ?",
            (self._config.consecutive_loss_days,),
        )
        if len(rows) < self._config.consecutive_loss_days:
            return False
        all_loss = all(row["total_pnl"] < 0 for row in rows)
        if all_loss:
            self._position_scale = self._config.reduced_position_pct
            logger.warning(
                f"{self._config.consecutive_loss_days}일 연속 손실 → "
                f"포지션 {self._position_scale:.0%}로 축소"
            )
        else:
            self._position_scale = 1.0
        return all_loss

    @property
    def position_scale(self) -> float:
        return self._position_scale

    @property
    def available_capital(self) -> float:
        """거래 가능 자본금. 0이면 거래 불가."""
        return self._daily_capital

    async def reconcile_positions(self, api_holdings: list[dict]) -> list[str]:
        db_open = await self._db.fetch_all(
            "SELECT ticker, remaining_qty FROM positions WHERE status='open'"
        )
        db_map = {row["ticker"]: row["remaining_qty"] for row in db_open}
        api_map = {h["ticker"]: h["qty"] for h in api_holdings}

        mismatches = []
        all_tickers = set(db_map.keys()) | set(api_map.keys())
        for ticker in all_tickers:
            db_qty = db_map.get(ticker, 0)
            api_qty = api_map.get(ticker, 0)
            if db_qty != api_qty:
                mismatches.append(f"{ticker}: DB={db_qty} vs API={api_qty}")
        return mismatches

    async def save_daily_summary(self) -> dict | None:
        """당일 매매 실적을 daily_pnl 테이블에 저장하고 요약 반환."""
        from datetime import datetime

        today = datetime.now().strftime("%Y-%m-%d")

        # trades 테이블에서 당일 매도(sell) 기록 집계
        rows = await self._db.fetch_all(
            "SELECT strategy, pnl, pnl_pct FROM trades "
            "WHERE side='sell' AND traded_at LIKE ? || '%'",
            (today,),
        )

        if not rows:
            return None

        total_trades = len(rows)
        wins = sum(1 for r in rows if (r["pnl"] or 0) > 0)
        losses = total_trades - wins
        win_rate = wins / total_trades if total_trades > 0 else 0.0
        total_pnl = sum(r["pnl"] or 0 for r in rows)

        # 전략별 집계
        strategies_used = set(r["strategy"] for r in rows if r["strategy"])
        strategy_str = ",".join(sorted(strategies_used)) if strategies_used else "none"

        # max drawdown (누적 PnL의 최저점)
        cumulative = 0.0
        peak = 0.0
        max_dd = 0.0
        for r in rows:
            cumulative += r["pnl"] or 0
            if cumulative > peak:
                peak = cumulative
            dd = peak - cumulative
            if dd > max_dd:
                max_dd = dd

        # UPSERT into daily_pnl
        await self._db.execute(
            "INSERT INTO daily_pnl (date, strategy, total_trades, wins, losses, win_rate, total_pnl, max_drawdown) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(date) DO UPDATE SET "
            "strategy=excluded.strategy, total_trades=excluded.total_trades, "
            "wins=excluded.wins, losses=excluded.losses, win_rate=excluded.win_rate, "
            "total_pnl=excluded.total_pnl, max_drawdown=excluded.max_drawdown",
            (today, strategy_str, total_trades, wins, losses, win_rate, total_pnl, max_dd),
        )

        summary = {
            "date": today,
            "strategy": strategy_str,
            "total_trades": total_trades,
            "wins": wins,
            "losses": losses,
            "win_rate": win_rate,
            "total_pnl": total_pnl,
            "max_drawdown": max_dd,
        }
        logger.info(f"일일 실적 저장: {total_trades}건, 승률 {win_rate:.1%}, 손익 {total_pnl:+,.0f}원")
        return summary

    def reset_daily(self) -> None:
        self._daily_pnl = 0.0
        self._halted = False
        self._positions.clear()

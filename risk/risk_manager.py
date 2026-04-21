"""risk/risk_manager.py — 리스크 관리 (손절, 일일한도, 강제청산, 연속손실)."""

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
        strategy: str = "", limit_up_price: float | None = None,
    ) -> None:
        now = datetime.now()
        self._positions[ticker] = {
            "entry_price": entry_price,
            "stop_loss": stop_loss,
            "qty": qty,
            "remaining_qty": qty,
            "tp1_price": tp1_price,
            "trailing_pct": trailing_pct or self._config.trailing_stop_pct,
            "highest_price": entry_price,
            "tp1_hit": False,
            "entry_time": now,
            "strategy": strategy,
            "limit_up_price": limit_up_price,
            "limit_up_exit_failed": False,
        }
        # 자본 차감
        cost = entry_price * qty
        self._daily_capital -= cost
        # DB 기록 (ADR-007: positions 테이블 활성화)
        try:
            conn = sqlite3.connect(self._db.db_path)
            conn.execute(
                "INSERT INTO positions "
                "(ticker, strategy, entry_price, qty, remaining_qty, stop_loss, "
                " tp1_price, trailing_pct, status, opened_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)",
                (
                    ticker, strategy, entry_price, qty, qty, stop_loss,
                    tp1_price, trailing_pct or self._config.trailing_stop_pct,
                    now.isoformat(),
                ),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning(f"positions INSERT 실패 ({ticker}): {e}")

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

    def check_limit_up(self, ticker: str, current_price: float) -> bool:
        """상한가 도달 여부. limit_up_price 없거나 기능 비활성이면 False."""
        if not getattr(self._config, "limit_up_exit_enabled", False):
            return False
        pos = self._positions.get(ticker)
        if not pos:
            return False
        lu = pos.get("limit_up_price")
        if not lu or lu <= 0:
            return False
        # 이미 매도 실패 후 stop 상향된 상태면 재시도 방지
        if pos.get("limit_up_exit_failed"):
            return False
        return current_price >= lu

    def raise_stop_to_limit_up_floor(self, ticker: str) -> float | None:
        """상한가 즉시 청산 실패 시 stop을 상한가 × floor_pct로 상향.

        Returns:
            새 stop_loss 값. 포지션 없거나 limit_up 없으면 None.
        """
        pos = self._positions.get(ticker)
        if not pos:
            return None
        lu = pos.get("limit_up_price")
        if not lu or lu <= 0:
            return None
        floor_pct = getattr(self._config, "limit_up_stop_floor_pct", 0.99)
        new_stop = lu * floor_pct
        pos["stop_loss"] = max(pos["stop_loss"], new_stop)
        pos["limit_up_exit_failed"] = True
        return pos["stop_loss"]

    def update_trailing_stop(self, ticker: str, current_price: float) -> None:
        pos = self._positions.get(ticker)
        if not pos:
            return
        # ADR-010: atr_tp_enabled=false → Pure trailing (tp1_hit 없이도 trailing 활성)
        if not pos.get("tp1_hit") and getattr(self._config, "atr_tp_enabled", True):
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

        # ADR-017: Breakeven Stop (BE3) — peak_return ≥ trigger 도달 시
        # stop을 entry × (1 + offset)로 상향. 기존 trailing과 max 비교로 공존.
        if getattr(self._config, "breakeven_enabled", False) and not pos.get("breakeven_active", False):
            entry = pos["entry_price"]
            peak = pos["highest_price"]
            trigger = getattr(self._config, "breakeven_trigger_pct", 0.03)
            if entry > 0 and (peak - entry) / entry >= trigger:
                offset = getattr(self._config, "breakeven_offset_pct", 0.01)
                be_stop = entry * (1.0 + offset)
                pos["stop_loss"] = max(pos["stop_loss"], be_stop)
                pos["breakeven_active"] = True

    def check_tp1(self, ticker: str, current_price: float) -> bool:
        pos = self._positions.get(ticker)
        if not pos or pos.get("tp1_hit"):
            return False
        if pos["tp1_price"] and current_price >= pos["tp1_price"]:
            return True
        return False

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
        remaining = pos["remaining_qty"]
        fully_closed = remaining <= 0
        if fully_closed:
            self._positions.pop(ticker, None)
        # DB 갱신 (ADR-007)
        try:
            conn = sqlite3.connect(self._db.db_path)
            if fully_closed:
                conn.execute(
                    "UPDATE positions SET remaining_qty=0, status='closed', "
                    "closed_at=? WHERE ticker=? AND status='open'",
                    (datetime.now().isoformat(), ticker),
                )
            else:
                conn.execute(
                    "UPDATE positions SET remaining_qty=? "
                    "WHERE ticker=? AND status='open'",
                    (remaining, ticker),
                )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning(f"positions UPDATE 실패 ({ticker}): {e}")
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
            # DB 갱신 (ADR-007: 분할매도 후 remaining_qty + 본전 이동 반영)
            try:
                conn = sqlite3.connect(self._db.db_path)
                conn.execute(
                    "UPDATE positions SET remaining_qty=?, stop_loss=? "
                    "WHERE ticker=? AND status='open'",
                    (pos["remaining_qty"], pos["entry_price"], ticker),
                )
                conn.commit()
                conn.close()
            except Exception as e:
                logger.warning(f"positions UPDATE (tp1) 실패 ({ticker}): {e}")

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

    def is_in_loss_rest(
        self,
        current_date: datetime | None = None,
        db_path: str = "daytrader.db",
    ) -> bool:
        """Phase 3 Day 11.5: 최근 N일 연속 손실 시 당일 매수 휴식.

        DB의 trades(sell, pnl) 일별 합계를 내림차순으로 훑어 threshold 이상
        연속 손실(daily_pnl<0)이면 True. DB 실패 시 안전 폴백(False).
        """
        if not getattr(self._config, "consecutive_loss_rest_enabled", False):
            return False

        now = current_date or datetime.now()
        threshold = self._config.consecutive_loss_threshold
        try:
            conn = sqlite3.connect(db_path)
        except Exception:
            return False
        try:
            rows = conn.execute(
                "SELECT date(traded_at) AS dt, SUM(pnl) AS daily_pnl "
                "FROM trades WHERE side='sell' AND pnl IS NOT NULL "
                "AND date(traded_at) < ? "
                "GROUP BY date(traded_at) ORDER BY dt DESC LIMIT ?",
                (now.strftime("%Y-%m-%d"), max(threshold * 2, 10)),
            ).fetchall()
        except Exception:
            conn.close()
            return False
        conn.close()

        consecutive = 0
        for _, daily_pnl in rows:
            if daily_pnl is not None and daily_pnl < 0:
                consecutive += 1
            else:
                break
        return consecutive >= threshold

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

    async def restore_from_db(self) -> int:
        """startup 시 DB의 status='open' 포지션을 in-memory로 복원 (ADR-007).

        프로세스 재시작 후 장애 복구 경로. 복원된 포지션은 이후
        reconcile_positions 로 키움 API 보유와 정합성 검증.
        """
        rows = await self._db.fetch_all(
            "SELECT ticker, strategy, entry_price, qty, remaining_qty, "
            "stop_loss, tp1_price, trailing_pct, opened_at "
            "FROM positions WHERE status='open'"
        )
        for row in rows:
            ticker = row["ticker"]
            entry_time = datetime.now()
            try:
                if row["opened_at"]:
                    entry_time = datetime.fromisoformat(row["opened_at"])
            except Exception:
                pass
            self._positions[ticker] = {
                "entry_price": row["entry_price"],
                "stop_loss": row["stop_loss"],
                "qty": row["qty"],
                "remaining_qty": row["remaining_qty"],
                "tp1_price": row["tp1_price"],
                "trailing_pct": row["trailing_pct"] or self._config.trailing_stop_pct,
                "highest_price": row["entry_price"],
                "tp1_hit": row["remaining_qty"] < row["qty"],
                "entry_time": entry_time,
                "strategy": row["strategy"] or "",
            }
        if rows:
            logger.warning(f"DB에서 오픈 포지션 {len(rows)}건 복원: {list(self._positions.keys())}")
        return len(rows)

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

    def reset_daily_counters(self) -> None:
        """자정 자동 리셋용 — 일일 카운터만 초기화, 포지션은 보존.

        정상 흐름에선 15:10 force_close가 포지션을 이미 비웠지만,
        프로세스 밤샘 가동 시 오버나이트 포지션이 있을 수 있으므로
        안전을 위해 _positions.clear() 는 수행하지 않는다. (ADR-006)
        """
        self._daily_pnl = 0.0
        self._halted = False

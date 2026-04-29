"""core/paper_order_manager.py — 페이퍼 트레이딩 주문 시뮬레이터.

실제 API 호출 없이 주문을 시뮬레이션한다.
OrderManager와 동일한 인터페이스를 제공하여 engine_worker.py에서 교체 가능.
"""

from __future__ import annotations

import asyncio
from datetime import datetime

from loguru import logger

from config.settings import BacktestConfig, TradingConfig
from core.cost_model import TradeCosts, apply_buy_costs, apply_sell_costs
from data.db_manager import DbManager
from notification.telegram_bot import TelegramNotifier


class PaperOrderManager:
    """페이퍼 트레이딩 주문 시뮬레이터.

    실제 REST API를 호출하지 않고, 현재가 기준으로 즉시 체결을 시뮬레이션한다.
    모든 주문/체결 내역을 DB와 텔레그램에 [PAPER] 태그로 기록한다.
    """

    CONFIRMATION_TIMEOUT = 5.0

    def __init__(
        self,
        risk_manager=None,
        notifier: TelegramNotifier | None = None,
        db: DbManager | None = None,
        trading_config: TradingConfig | None = None,
        order_queue: asyncio.Queue | None = None,
        notifications_config=None,
        backtest_config: BacktestConfig | None = None,
    ):
        self._risk_manager = risk_manager
        self._notifier = notifier
        self._db = db
        self._config = trading_config or TradingConfig()
        self._notifications = notifications_config  # Phase 3-B ADR-008
        # ADR-009: backtester와 동일한 비용 모델 (paper 시뮬 PnL 정합)
        self._costs = TradeCosts.from_backtest_config(backtest_config or BacktestConfig())
        # ticker별 가장 최근 net_entry 단가 (PnL 계산용)
        self._net_entry_map: dict[str, float] = {}
        self._lock = asyncio.Lock()
        self._active_orders: dict[str, bool] = {}
        self._order_queue: asyncio.Queue = order_queue or asyncio.Queue()
        self._order_seq = 0  # 가상 주문번호 시퀀스

        # 종목명 매핑 로드
        self._name_map: dict[str, str] = {}
        from pathlib import Path
        import yaml
        uni_path = Path("config/universe.yaml")
        if uni_path.exists():
            try:
                uni = yaml.safe_load(open(uni_path, encoding="utf-8")) or {}
                for s in uni.get("stocks", []):
                    self._name_map[s["ticker"]] = s.get("name", s["ticker"])
            except Exception:
                pass

    def _format_ticker(self, ticker: str) -> str:
        """종목명(코드) 형식으로 변환."""
        name = self._name_map.get(ticker, "")
        return f"{name}({ticker})" if name else ticker

    def _next_order_no(self) -> str:
        """가상 주문번호 생성."""
        self._order_seq += 1
        return f"PAPER-{self._order_seq:06d}"

    def _trade_notify_enabled(self) -> bool:
        """ADR-008: trade_execution 토글. notifications 없으면 기본 True."""
        if self._notifications is None:
            return True
        return bool(self._notifications.trade_execution)

    async def execute_buy(self, ticker: str, price: int, total_qty: int, strategy: str = "unknown") -> dict | None:
        """1차 매수 시뮬레이션."""
        if ticker in self._active_orders:
            logger.warning(f"[PAPER] 중복 주문 차단: {ticker}")
            return None

        async with self._lock:
            self._active_orders[ticker] = True
            try:
                qty_1st = max(1, int(total_qty * self._config.entry_1st_ratio))
                order_no = self._next_order_no()

                # ADR-009: 슬리피지 + 수수료 반영 (backtester와 동일 경로)
                slipped, net_entry_per_share = apply_buy_costs(price, self._costs)
                slipped_int = int(round(slipped))
                self._net_entry_map[ticker] = net_entry_per_share

                logger.info(
                    f"[PAPER] 1차 매수 체결: {ticker} {qty_1st}주 @ {slipped_int:,}원 "
                    f"(raw={price:,}, 주문번호: {order_no})"
                )

                # DB 기록 (slipped 가격)
                if self._db:
                    now = datetime.now().isoformat()
                    await self._db.execute_safe(
                        "INSERT INTO trades (ticker, strategy, side, order_type, "
                        "price, qty, amount, traded_at) "
                        "VALUES (?, ?, 'buy', 'market', ?, ?, ?, ?)",
                        (ticker, strategy, slipped_int, qty_1st,
                         slipped_int * qty_1st, now),
                    )

                # 텔레그램 알림 (ADR-008: trade_execution 토글 + send_execution 통일)
                if self._notifier and self._trade_notify_enabled():
                    try:
                        name = self._name_map.get(ticker, ticker)
                        self._notifier.send_execution(
                            ticker=ticker, name=name, side="buy",
                            price=slipped_int, qty=qty_1st,
                            amount=slipped_int * qty_1st,
                            mode="paper",
                        )
                    except Exception as e:
                        logger.warning(f"체결 알림 실패 ({ticker}): {e}")

                return {"order_no": order_no, "qty": qty_1st}
            finally:
                self._active_orders.pop(ticker, None)

    async def execute_buy_2nd(self, ticker: str, price: int, remaining_qty: int, strategy: str = "unknown") -> dict | None:
        """2차 매수 시뮬레이션."""
        return await self._simulate_order(ticker, remaining_qty, price, "buy", strategy=strategy)

    async def execute_sell_tp1(
        self, ticker: str, price: int, remaining_qty: int,
        strategy: str = "unknown", pnl: float | None = None, pnl_pct: float | None = None,
        exit_reason: str = "tp1_hit",
    ) -> dict | None:
        """1차 익절 시뮬레이션."""
        if remaining_qty <= 1:
            sell_qty = remaining_qty  # 1주 보유 시 전량 매도
        else:
            sell_qty = max(1, int(remaining_qty * self._config.tp1_sell_ratio))
        return await self._simulate_order(ticker, sell_qty, price, "sell", reason=exit_reason, strategy=strategy, pnl=pnl, pnl_pct=pnl_pct)

    async def execute_sell_stop(
        self, ticker: str, qty: int, price: int = 0,
        strategy: str = "unknown", pnl: float | None = None, pnl_pct: float | None = None,
        exit_reason: str = "stop_loss",
    ) -> dict | None:
        """손절 시뮬레이션 (exit_reason으로 stop_loss/trailing_stop 구분 가능)."""
        return await self._simulate_order(ticker, qty, price, "sell", reason=exit_reason, strategy=strategy, pnl=pnl, pnl_pct=pnl_pct)

    async def execute_sell_force_close(
        self, ticker: str, qty: int, price: int = 0,
        strategy: str = "unknown", pnl: float | None = None, pnl_pct: float | None = None,
        exit_reason: str = "forced_close",
    ) -> dict | None:
        """강제 청산 시뮬레이션."""
        logger.warning(f"[PAPER] 강제 청산({exit_reason}): {ticker} {qty}주")
        return await self._simulate_order(ticker, qty, price, "sell", reason=exit_reason, strategy=strategy, pnl=pnl, pnl_pct=pnl_pct)

    async def _simulate_order(
        self, ticker: str, qty: int, price: int, side: str, reason: str = "",
        strategy: str = "unknown", pnl: float | None = None, pnl_pct: float | None = None,
    ) -> dict | None:
        """주문 시뮬레이션 공통 로직 (ADR-009: 비용 반영)."""
        order_no = self._next_order_no()
        label = "매수" if side == "buy" else "매도"

        # ADR-009: 비용 적용
        if side == "buy":
            slipped_f, net_per_share = apply_buy_costs(price, self._costs)
            slipped_int = int(round(slipped_f))
            self._net_entry_map[ticker] = net_per_share
            computed_pnl = None
            computed_pnl_pct = None
        else:  # sell
            slipped_f, net_exit_per_share = apply_sell_costs(price, self._costs)
            slipped_int = int(round(slipped_f))
            net_entry = self._net_entry_map.get(ticker)
            if net_entry is not None:
                computed_pnl = (net_exit_per_share - net_entry) * qty
                computed_pnl_pct = (net_exit_per_share - net_entry) / net_entry
            else:
                # 장부 없이 매도 (테스트/복구 시나리오) — 호출자 pnl 사용
                computed_pnl = pnl
                computed_pnl_pct = pnl_pct

        # 호출자가 pnl 명시 안 했으면 cost model 기반 값 사용
        pnl_final = pnl if pnl is not None else computed_pnl
        pnl_pct_final = pnl_pct if pnl_pct is not None else computed_pnl_pct

        logger.info(
            f"[PAPER] {label} 체결: {ticker} {qty}주 @ {slipped_int:,}원 "
            f"(raw={price:,}, {reason or 'market'}, 주문번호: {order_no})"
        )

        if self._db:
            now = datetime.now().isoformat()
            await self._db.execute_safe(
                "INSERT INTO trades (ticker, strategy, side, order_type, "
                "price, qty, amount, pnl, pnl_pct, exit_reason, traded_at) "
                "VALUES (?, ?, ?, 'market', ?, ?, ?, ?, ?, ?, ?)",
                (ticker, strategy, side, slipped_int, qty, slipped_int * qty,
                 pnl_final, pnl_pct_final, reason, now),
            )

        if self._notifier and self._trade_notify_enabled():
            name = self._name_map.get(ticker, ticker)
            pnl_int = int(pnl_final) if pnl_final is not None else None
            pnl_pct_f = (pnl_pct_final * 100) if pnl_pct_final is not None else None
            try:
                self._notifier.send_execution(
                    ticker=ticker, name=name, side=side,
                    price=slipped_int, qty=qty, amount=slipped_int * qty,
                    mode="paper", reason=reason,
                    pnl=pnl_int, pnl_pct=pnl_pct_f,
                )
            except Exception as e:
                logger.warning(f"체결 알림 실패 ({ticker}): {e}")

        return {"order_no": order_no, "qty": qty}

    async def wait_for_confirmation(self, order_no: str) -> dict | None:
        """페이퍼 모드에서는 즉시 체결이므로 바로 반환."""
        return {"order_no": order_no, "status": "filled"}

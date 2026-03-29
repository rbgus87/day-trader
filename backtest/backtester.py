"""backtest/backtester.py — 전략 백테스트 엔진 (pure pandas, vectorbt 미사용).

PRD F-BT-01: intraday_candles DB에서 과거 분봉 로드, 전략 시뮬레이션, 수수료 + 슬리피지, KPI 계산.
"""

import math
from datetime import time as dt_time
from typing import Any

import pandas as pd
from loguru import logger

from config.settings import TradingConfig
from data.db_manager import DbManager
from strategy.base_strategy import BaseStrategy, Signal

# DEPRECATED: BacktestConfig 사용 권장
ENTRY_FEE_RATE: float = 0.00015   # 0.015%
EXIT_FEE_RATE: float = 0.00015    # 0.015%
SELL_TAX_RATE: float = 0.0018     # 0.18% (증권거래세)
SLIPPAGE_RATE: float = 0.00005    # 0.005% (슬리피지 가정) — config.yaml은 0.03%

from config.settings import BacktestConfig


class Backtester:
    """순수 pandas 기반 단타 전략 백테스터."""

    def __init__(
        self,
        db: DbManager,
        config: TradingConfig,
        commission: float | None = None,
        tax: float | None = None,
        slippage: float | None = None,
        backtest_config: BacktestConfig | None = None,
    ) -> None:
        self._db = db
        self._config = config
        # 우선순위: 파라미터 > BacktestConfig > 글로벌 상수
        bt = backtest_config or BacktestConfig()
        self._entry_fee = commission if commission is not None else bt.commission
        self._exit_fee = commission if commission is not None else bt.commission
        self._tax = tax if tax is not None else bt.tax
        self._slippage = slippage if slippage is not None else bt.slippage

    # ------------------------------------------------------------------
    # 데이터 로드
    # ------------------------------------------------------------------

    async def load_candles(
        self,
        ticker: str,
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        """intraday_candles 테이블에서 분봉 데이터 조회.

        Args:
            ticker:     종목 코드 (예: '005930')
            start_date: 시작 날짜/시각 문자열 (예: '2026-01-01' or '2026-01-01T09:00:00')
            end_date:   종료 날짜/시각 문자열

        Returns:
            columns: ts, open, high, low, close, volume, vwap
        """
        rows = await self._db.fetch_all(
            "SELECT ts, open, high, low, close, volume, vwap "
            "FROM intraday_candles "
            "WHERE ticker=? AND tf='1m' AND ts BETWEEN ? AND ? "
            "ORDER BY ts ASC",
            (ticker, start_date, end_date),
        )

        if not rows:
            logger.warning(f"캔들 없음: ticker={ticker} {start_date}~{end_date}")
            return pd.DataFrame(
                columns=["ts", "open", "high", "low", "close", "volume", "vwap"]
            )

        df = pd.DataFrame(rows)
        df["ts"] = pd.to_datetime(df["ts"])
        # 전략이 사용하는 time 컬럼 추가 (HH:MM 형식)
        df["time"] = df["ts"].dt.strftime("%H:%M")
        logger.info(f"캔들 로드: ticker={ticker} rows={len(df)}")
        return df

    # ------------------------------------------------------------------
    # 백테스트 실행
    # ------------------------------------------------------------------

    def run_backtest(
        self,
        candles: pd.DataFrame,
        strategy: BaseStrategy,
    ) -> dict[str, Any]:
        """캔들 데이터에 전략을 적용해 시뮬레이션하고 KPI를 반환.

        Args:
            candles:  load_candles() 에서 반환된 DataFrame
            strategy: BaseStrategy 구현체

        Returns:
            KPI dict (calculate_kpi 결과) + 'trades' 키 포함
        """
        if candles.empty:
            logger.warning("빈 캔들 데이터 — 백테스트 스킵")
            return {**self.calculate_kpi([]), "trades": []}

        # 0-기반 정수 인덱스 보장 (look-ahead bias 방지)
        candles = candles.reset_index(drop=True)

        trades: list[dict] = []
        position: dict | None = None          # 현재 보유 포지션
        accumulated: list[dict] = []          # 전략에 전달할 과거 캔들 누적

        for idx, row in candles.iterrows():
            ts = row["ts"]
            # 백테스트 모드: 시뮬레이션 시각을 전략에 주입
            if hasattr(ts, "time"):
                strategy.set_backtest_time(ts.time())

            tick = {
                "ticker": "BACKTEST",
                "price": float(row["close"]),
                "time": ts.strftime("%H%M") if hasattr(ts, "strftime") else str(ts)[11:16].replace(":", ""),
                "volume": int(row.get("volume", 0)),
            }

            candles_so_far = (
                candles.iloc[: idx + 1]  # type: ignore[misc]
                if isinstance(idx, int)
                else candles.loc[:idx]
            )

            # ── 포지션 없음 → 진입 신호 탐색 ──────────────────────────
            if position is None:
                signal: Signal | None = strategy.generate_signal(candles_so_far, tick)
                if signal is not None and signal.side == "buy":
                    strategy.on_entry()
                    entry_price_raw = float(row["close"])
                    # 슬리피지 적용 (매수 시 불리)
                    entry_price = entry_price_raw * (1 + self._slippage)
                    entry_fee = entry_price * self._entry_fee
                    net_entry = entry_price + entry_fee

                    stop_loss = strategy.get_stop_loss(entry_price)
                    tp1, tp2 = strategy.get_take_profit(entry_price)

                    position = {
                        "entry_ts": row["ts"],
                        "entry_price": entry_price,
                        "net_entry": net_entry,
                        "stop_loss": stop_loss,
                        "tp1": tp1,
                        "tp2": tp2 if tp2 else None,
                    }
                    logger.debug(
                        f"[BT] 진입 ts={row['ts']} price={entry_price:.1f} "
                        f"sl={stop_loss:.1f} tp1={tp1:.1f}"
                    )

            # ── 포지션 보유 중 → 청산 조건 확인 ──────────────────────
            else:
                low = float(row["low"])
                high = float(row["high"])
                close = float(row["close"])
                exit_price: float | None = None
                exit_reason: str = ""

                # 손절 확인 (캔들 저가 기준)
                if low <= position["stop_loss"]:
                    exit_price = position["stop_loss"]
                    exit_reason = "stop_loss"

                # TP1 확인 (캔들 고가 기준)
                elif position["tp1"] and high >= position["tp1"]:
                    exit_price = position["tp1"]
                    exit_reason = "tp1"

                # TP2 확인 (캔들 고가 기준)
                elif position["tp2"] and high >= position["tp2"]:
                    exit_price = position["tp2"]
                    exit_reason = "tp2"

                # 마지막 캔들 강제 청산
                elif idx == len(candles) - 1:
                    exit_price = close
                    exit_reason = "forced_close"

                if exit_price is not None:
                    # 슬리피지 적용 (매도 시 불리)
                    exit_price_slipped = exit_price * (1 - self._slippage)
                    exit_fee = exit_price_slipped * (self._exit_fee + self._tax)
                    net_exit = exit_price_slipped - exit_fee

                    pnl = net_exit - position["net_entry"]
                    pnl_pct = pnl / position["net_entry"]

                    trade = {
                        "entry_ts": position["entry_ts"],
                        "exit_ts": row["ts"],
                        "entry_price": position["entry_price"],
                        "exit_price": exit_price_slipped,
                        "pnl": pnl,
                        "pnl_pct": pnl_pct,
                        "exit_reason": exit_reason,
                    }
                    trades.append(trade)
                    logger.debug(
                        f"[BT] 청산 ts={row['ts']} reason={exit_reason} "
                        f"exit={exit_price_slipped:.1f} pnl={pnl:.1f} ({pnl_pct:.2%})"
                    )
                    position = None
                    strategy.on_exit()

        # 백테스트 시각 초기화
        strategy.set_backtest_time(None)

        kpi = self.calculate_kpi(trades)
        kpi["trades"] = trades
        logger.info(
            f"백테스트 완료: total_trades={kpi['total_trades']} "
            f"win_rate={kpi['win_rate']:.1%} total_pnl={kpi['total_pnl']:.1f}"
        )
        return kpi

    # ------------------------------------------------------------------
    # KPI 계산
    # ------------------------------------------------------------------

    def calculate_kpi(self, trades: list[dict]) -> dict[str, Any]:
        """거래 목록으로 KPI를 계산한다.

        Args:
            trades: run_backtest() 가 축적한 trade dict 리스트.
                    각 dict 에는 최소 'pnl', 'pnl_pct' 키가 필요하다.

        Returns:
            total_trades, wins, win_rate, profit_factor,
            total_pnl, max_drawdown, sharpe_ratio
        """
        total_trades = len(trades)

        if total_trades == 0:
            return {
                "total_trades": 0,
                "wins": 0,
                "win_rate": 0.0,
                "profit_factor": 0.0,
                "total_pnl": 0.0,
                "max_drawdown": 0.0,
                "sharpe_ratio": 0.0,
            }

        pnl_series = [t["pnl"] for t in trades]
        wins = sum(1 for p in pnl_series if p > 0)
        win_rate = wins / total_trades

        gross_profit = sum(p for p in pnl_series if p > 0)
        gross_loss = abs(sum(p for p in pnl_series if p < 0))
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float("inf")

        total_pnl = sum(pnl_series)

        # 최대 낙폭 (peak-to-trough)
        max_drawdown = self._calc_max_drawdown(pnl_series)

        # 샤프 비율 (연간화, 거래 단위)
        sharpe_ratio = self._calc_sharpe(pnl_series)

        return {
            "total_trades": total_trades,
            "wins": wins,
            "win_rate": win_rate,
            "profit_factor": profit_factor,
            "total_pnl": total_pnl,
            "max_drawdown": max_drawdown,
            "sharpe_ratio": sharpe_ratio,
        }

    # ------------------------------------------------------------------
    # 다일 백테스트
    # ------------------------------------------------------------------

    async def run_multi_day(
        self,
        ticker: str,
        start_date: str,
        end_date: str,
        strategy: BaseStrategy,
    ) -> dict[str, Any]:
        """여러 날에 걸쳐 하루씩 전략을 리셋하며 백테스트.

        Args:
            ticker:     종목코드
            start_date: 시작 날짜 (YYYY-MM-DD)
            end_date:   종료 날짜 (YYYY-MM-DD)
            strategy:   BaseStrategy 구현체 (매일 reset())

        Returns:
            통합 KPI + trades
        """
        all_candles = await self.load_candles(ticker, start_date, f"{end_date} 23:59:59")
        if all_candles.empty:
            return {**self.calculate_kpi([]), "trades": []}

        # 날짜별 그루핑
        all_candles["date"] = all_candles["ts"].dt.date
        all_trades: list[dict] = []
        prev_day_df: pd.DataFrame | None = None

        for date, day_candles in all_candles.groupby("date"):
            day_df = day_candles.drop(columns=["date"]).reset_index(drop=True)
            strategy.reset()

            # 전략별 전일 데이터 자동 설정
            self._setup_strategy_day(strategy, day_df, prev_day_df)

            result = self.run_backtest(day_df, strategy)
            day_trades = result.get("trades", [])
            all_trades.extend(day_trades)
            logger.info(
                f"[{date}] trades={len(day_trades)} "
                f"pnl={sum(t['pnl'] for t in day_trades):.0f}"
            )
            prev_day_df = day_df

        kpi = self.calculate_kpi(all_trades)
        kpi["trades"] = all_trades
        logger.info(
            f"다일 백테스트 완료: days={len(all_candles['date'].unique())} "
            f"total_trades={kpi['total_trades']} "
            f"win_rate={kpi['win_rate']:.1%} total_pnl={kpi['total_pnl']:.1f}"
        )
        return kpi

    # ------------------------------------------------------------------
    # 내부 헬퍼
    # ------------------------------------------------------------------

    @staticmethod
    def _setup_strategy_day(
        strategy: BaseStrategy,
        day_df: pd.DataFrame,
        prev_day_df: pd.DataFrame | None,
    ) -> None:
        """전략별 당일/전일 데이터를 자동 설정한다."""
        # Momentum: 전일 고가/거래량 설정
        if hasattr(strategy, "set_prev_day_data") and prev_day_df is not None:
            prev_high = float(prev_day_df["high"].max())
            prev_volume = int(prev_day_df["volume"].sum())
            strategy.set_prev_day_data(prev_high, prev_volume)

        # ORB: 전일 거래량 설정
        if hasattr(strategy, "set_prev_day_volume") and prev_day_df is not None:
            prev_volume = int(prev_day_df["volume"].sum())
            strategy.set_prev_day_volume(prev_volume)

        # Pullback: 당일 시가 설정
        if hasattr(strategy, "set_open_price") and not day_df.empty:
            open_price = float(day_df.iloc[0]["open"])
            strategy.set_open_price(open_price)

    @staticmethod
    def _calc_max_drawdown(pnl_series: list[float]) -> float:
        """누적 PnL 곡선에서 peak-to-trough 최대 낙폭 계산."""
        if not pnl_series:
            return 0.0

        cumulative = 0.0
        peak = 0.0
        max_dd = 0.0

        for pnl in pnl_series:
            cumulative += pnl
            if cumulative > peak:
                peak = cumulative
            drawdown = peak - cumulative
            if drawdown > max_dd:
                max_dd = drawdown

        return max_dd

    @staticmethod
    def _calc_sharpe(pnl_series: list[float], ann_factor: float = 252.0) -> float:
        """거래별 PnL 기준 샤프 비율 (연간화).

        Args:
            pnl_series:  거래별 손익 리스트
            ann_factor:  연간화 계수 (기본 252 거래일)
        """
        n = len(pnl_series)
        if n < 2:
            return 0.0

        mean_r = sum(pnl_series) / n
        variance = sum((r - mean_r) ** 2 for r in pnl_series) / (n - 1)
        std_r = math.sqrt(variance)

        if std_r == 0:
            return 0.0

        return (mean_r / std_r) * math.sqrt(ann_factor)

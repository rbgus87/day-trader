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


def build_market_strong_by_date(
    db_path: str,
    ma_length: int = 5,
) -> dict[str, dict[str, bool]]:
    """index_candles에서 코스피/코스닥 MA 기반 날짜별 강세 맵 생성.

    Returns:
        {"20260410": {"kospi": True, "kosdaq": False}, ...}
        MA 계산에 필요한 과거 데이터가 부족한 초반 날짜는 맵에 포함되지 않음.
    """
    import sqlite3

    result: dict[str, dict[str, bool]] = {}
    conn = sqlite3.connect(db_path)
    try:
        for index_code, market in (("001", "kospi"), ("101", "kosdaq")):
            cur = conn.execute(
                "SELECT dt, close FROM index_candles "
                "WHERE index_code=? ORDER BY dt ASC",
                (index_code,),
            )
            rows = cur.fetchall()
            if len(rows) <= ma_length:
                logger.warning(
                    f"index {index_code} 데이터 부족: {len(rows)}건 (MA{ma_length} 요구 > {ma_length}건)"
                )
                continue
            # 각 날짜 i에 대해 직전 ma_length일의 평균으로 MA 계산
            for i in range(ma_length, len(rows)):
                dt, close = rows[i]
                recent = [rows[j][1] for j in range(i - ma_length, i)]
                ma = sum(recent) / ma_length
                strong = close > ma
                result.setdefault(dt, {})[market] = strong
    finally:
        conn.close()
    return result


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
        ticker_market: str = "unknown",
        market_strong_by_date: dict[str, dict[str, bool]] | None = None,
    ) -> None:
        self._db = db
        self._config = config
        # 우선순위: 파라미터 > BacktestConfig > 글로벌 상수
        bt = backtest_config or BacktestConfig()
        self._entry_fee = commission if commission is not None else bt.commission
        self._exit_fee = commission if commission is not None else bt.commission
        self._tax = tax if tax is not None else bt.tax
        self._slippage = slippage if slippage is not None else bt.slippage
        # 시장 필터 (Phase 1 Day 4)
        # market_strong_by_date: {"20260410": {"kospi": True, "kosdaq": False}, ...}
        self._ticker_market = ticker_market
        self._market_strong_by_date = market_strong_by_date or {}

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
        _has_5m = hasattr(strategy, "on_candle_5m")
        _min1_buffer: list = []

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

            # 5분봉 빌딩 (FlowStrategy 등 on_candle_5m 지원)
            if _has_5m:
                _min1_buffer.append(row)
                if len(_min1_buffer) >= 5:
                    candle_5m = {
                        "ticker": "BACKTEST",
                        "tf": "5m",
                        "open": float(_min1_buffer[0]["open"]),
                        "high": max(float(r["high"]) for r in _min1_buffer),
                        "low": min(float(r["low"]) for r in _min1_buffer),
                        "close": float(_min1_buffer[-1]["close"]),
                        "volume": sum(int(r.get("volume", 0)) for r in _min1_buffer),
                    }
                    strategy.on_candle_5m(candle_5m)
                    _min1_buffer = []

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

                # 손절 확인 (캔들 저가 기준)
                if low <= position["stop_loss"]:
                    exit_price = position["stop_loss"]
                    remaining = position.get("remaining_ratio", 1.0)
                    exit_price_slipped = exit_price * (1 - self._slippage)
                    exit_fee = exit_price_slipped * (self._exit_fee + self._tax)
                    net_exit = exit_price_slipped - exit_fee
                    pnl = (net_exit - position["net_entry"]) * remaining
                    pnl_pct = (net_exit - position["net_entry"]) / position["net_entry"]
                    trades.append({
                        "entry_ts": position["entry_ts"],
                        "exit_ts": row["ts"],
                        "entry_price": position["entry_price"],
                        "exit_price": exit_price_slipped,
                        "pnl": pnl,
                        "pnl_pct": pnl_pct,
                        "exit_reason": "stop_loss",
                    })
                    logger.debug(
                        f"[BT] 손절 ts={row['ts']} exit={exit_price_slipped:.1f} "
                        f"pnl={pnl:.1f} ({pnl_pct:.2%}) ratio={remaining:.0%}"
                    )
                    position = None
                    strategy.on_exit()

                # TP1 확인 (캔들 고가 기준) — 분할매도
                elif not position.get("tp1_hit") and position["tp1"] and high >= position["tp1"]:
                    tp1_price = position["tp1"]
                    tp1_slipped = tp1_price * (1 - self._slippage)
                    tp1_fee = tp1_slipped * (self._exit_fee + self._tax)
                    net_tp1 = tp1_slipped - tp1_fee
                    tp1_ratio = self._config.tp1_sell_ratio  # 50%
                    pnl = (net_tp1 - position["net_entry"]) * tp1_ratio
                    pnl_pct = (net_tp1 - position["net_entry"]) / position["net_entry"]
                    trades.append({
                        "entry_ts": position["entry_ts"],
                        "exit_ts": row["ts"],
                        "entry_price": position["entry_price"],
                        "exit_price": tp1_slipped,
                        "pnl": pnl,
                        "pnl_pct": pnl_pct,
                        "exit_reason": "tp1",
                    })
                    logger.debug(
                        f"[BT] TP1 분할매도 ts={row['ts']} exit={tp1_slipped:.1f} "
                        f"pnl={pnl:.1f} ratio={tp1_ratio:.0%}"
                    )
                    # 나머지 포지션 유지: 손절선 본전 이동 + tp1_hit 마킹
                    position["tp1_hit"] = True
                    position["remaining_ratio"] = 1.0 - tp1_ratio
                    position["stop_loss"] = position["entry_price"]  # 본전 손절
                    position["highest_price"] = high

                    # 마지막 캔들에서 TP1 히트 시 나머지도 강제 청산
                    if idx == len(candles) - 1:
                        remaining = position["remaining_ratio"]
                        fc_slipped = close * (1 - self._slippage)
                        fc_fee = fc_slipped * (self._exit_fee + self._tax)
                        net_fc = fc_slipped - fc_fee
                        fc_pnl = (net_fc - position["net_entry"]) * remaining
                        fc_pnl_pct = (net_fc - position["net_entry"]) / position["net_entry"]
                        trades.append({
                            "entry_ts": position["entry_ts"],
                            "exit_ts": row["ts"],
                            "entry_price": position["entry_price"],
                            "exit_price": fc_slipped,
                            "pnl": fc_pnl,
                            "pnl_pct": fc_pnl_pct,
                            "exit_reason": "forced_close",
                        })
                        position = None
                        strategy.on_exit()

                # 트레일링 스톱 / 강제청산 (TP1 히트 후)
                elif position.get("tp1_hit"):
                    # 고점 갱신
                    if high > position.get("highest_price", 0):
                        position["highest_price"] = high
                        # Phase 2 Day 7: ATR 기반 Chandelier 트레일링 (폴백: 고정 trailing_stop_pct)
                        new_stop = None
                        if getattr(self._config, "atr_trail_enabled", False):
                            try:
                                from core.indicators import (
                                    calculate_atr_trailing_stop,
                                    get_latest_atr,
                                )
                                ticker = getattr(self, "_current_ticker", None)
                                as_of = None
                                try:
                                    as_of = pd.to_datetime(row["ts"]).strftime("%Y-%m-%d")
                                except Exception:
                                    pass
                                atr_pct = (
                                    get_latest_atr(self._db.db_path, ticker, as_of)
                                    if (self._db is not None and ticker)
                                    else None
                                )
                                if atr_pct is not None:
                                    new_stop = calculate_atr_trailing_stop(
                                        peak_price=position["highest_price"],
                                        atr_pct=atr_pct,
                                        multiplier=self._config.atr_trail_multiplier,
                                        min_pct=self._config.atr_trail_min_pct,
                                        max_pct=self._config.atr_trail_max_pct,
                                    )
                            except Exception as e:
                                logger.warning(f"[BT] ATR 트레일 실패 폴백: {e}")
                                new_stop = None
                        if new_stop is None:
                            trailing_pct = self._config.trailing_stop_pct
                            new_stop = position["highest_price"] * (1 - trailing_pct)
                        # 트레일링 스톱은 위로만 움직임 (기존 stop보다 낮아지면 유지)
                        position["stop_loss"] = max(position["stop_loss"], new_stop)

                    remaining = position.get("remaining_ratio", 0.5)
                    # 트레일링 스톱 체크
                    if low <= position["stop_loss"]:
                        exit_price = position["stop_loss"]
                        exit_price_slipped = exit_price * (1 - self._slippage)
                        exit_fee = exit_price_slipped * (self._exit_fee + self._tax)
                        net_exit = exit_price_slipped - exit_fee
                        pnl = (net_exit - position["net_entry"]) * remaining
                        pnl_pct = (net_exit - position["net_entry"]) / position["net_entry"]
                        trades.append({
                            "entry_ts": position["entry_ts"],
                            "exit_ts": row["ts"],
                            "entry_price": position["entry_price"],
                            "exit_price": exit_price_slipped,
                            "pnl": pnl,
                            "pnl_pct": pnl_pct,
                            "exit_reason": "trailing_stop",
                        })
                        logger.debug(
                            f"[BT] 트레일링 청산 ts={row['ts']} exit={exit_price_slipped:.1f} "
                            f"pnl={pnl:.1f} ratio={remaining:.0%}"
                        )
                        position = None
                        strategy.on_exit()
                    # 마지막 캔들 강제 청산 (나머지)
                    elif idx == len(candles) - 1:
                        exit_price_slipped = close * (1 - self._slippage)
                        exit_fee = exit_price_slipped * (self._exit_fee + self._tax)
                        net_exit = exit_price_slipped - exit_fee
                        pnl = (net_exit - position["net_entry"]) * remaining
                        pnl_pct = (net_exit - position["net_entry"]) / position["net_entry"]
                        trades.append({
                            "entry_ts": position["entry_ts"],
                            "exit_ts": row["ts"],
                            "entry_price": position["entry_price"],
                            "exit_price": exit_price_slipped,
                            "pnl": pnl,
                            "pnl_pct": pnl_pct,
                            "exit_reason": "forced_close",
                        })
                        position = None
                        strategy.on_exit()

                # TP2 확인 (캔들 고가 기준) — TP1 미히트 상태
                elif position.get("tp2") and high >= position["tp2"]:
                    exit_price = position["tp2"]
                    exit_price_slipped = exit_price * (1 - self._slippage)
                    exit_fee = exit_price_slipped * (self._exit_fee + self._tax)
                    net_exit = exit_price_slipped - exit_fee
                    pnl = net_exit - position["net_entry"]
                    pnl_pct = pnl / position["net_entry"]
                    trades.append({
                        "entry_ts": position["entry_ts"],
                        "exit_ts": row["ts"],
                        "entry_price": position["entry_price"],
                        "exit_price": exit_price_slipped,
                        "pnl": pnl,
                        "pnl_pct": pnl_pct,
                        "exit_reason": "tp2",
                    })
                    position = None
                    strategy.on_exit()

                # 마지막 캔들 강제 청산 (TP1 미히트 상태)
                elif idx == len(candles) - 1:
                    exit_price_slipped = close * (1 - self._slippage)
                    exit_fee = exit_price_slipped * (self._exit_fee + self._tax)
                    net_exit = exit_price_slipped - exit_fee
                    pnl = net_exit - position["net_entry"]
                    pnl_pct = pnl / position["net_entry"]
                    trades.append({
                        "entry_ts": position["entry_ts"],
                        "exit_ts": row["ts"],
                        "entry_price": position["entry_price"],
                        "exit_price": exit_price_slipped,
                        "pnl": pnl,
                        "pnl_pct": pnl_pct,
                        "exit_reason": "forced_close",
                    })
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
        self._current_ticker = ticker
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

    async def run_multi_day_cached(
        self,
        ticker: str,
        all_candles: pd.DataFrame,
        strategy: BaseStrategy,
    ) -> dict[str, Any]:
        """이미 로드된 캔들 DataFrame으로 다일 백테스트 (DB 재로드 없음).

        시장 필터가 활성화된 경우 ticker_market + market_strong_by_date 기반으로
        약세 시장 날짜는 매매를 건너뛴다 (prev_day_df는 여전히 갱신).
        """
        self._current_ticker = ticker
        if all_candles.empty:
            return {**self.calculate_kpi([]), "trades": []}

        df = all_candles.copy()
        if "date" not in df.columns:
            df["date"] = df["ts"].dt.date
        all_trades: list[dict] = []
        prev_day_df: pd.DataFrame | None = None

        market_filter_enabled = getattr(self._config, "market_filter_enabled", False)

        for date, day_candles in df.groupby("date"):
            day_df = day_candles.drop(columns=["date"]).reset_index(drop=True)

            # 시장 필터: 약세 시장 종목은 해당 날짜 매매 건너뛰기
            skip_day = False
            if market_filter_enabled and self._ticker_market in ("kospi", "kosdaq"):
                date_key = date.strftime("%Y%m%d")
                strong = self._market_strong_by_date.get(date_key)
                # strong이 없으면(데이터 누락) 보수적으로 허용
                if strong is not None and not strong.get(self._ticker_market, True):
                    skip_day = True

            if skip_day:
                prev_day_df = day_df
                continue

            strategy.reset()
            self._setup_strategy_day(strategy, day_df, prev_day_df)
            result = self.run_backtest(day_df, strategy)
            all_trades.extend(result.get("trades", []))
            prev_day_df = day_df

        kpi = self.calculate_kpi(all_trades)
        kpi["trades"] = all_trades
        return kpi

    # ------------------------------------------------------------------
    # 내부 헬퍼
    # ------------------------------------------------------------------

    def _setup_strategy_day(
        self,
        strategy: BaseStrategy,
        day_df: pd.DataFrame,
        prev_day_df: pd.DataFrame | None,
    ) -> None:
        """전략별 당일/전일 데이터를 자동 설정한다."""
        # Phase 2 Day 6: ATR 조회용 ticker 주입 (run_multi_day_cached가 알고 있음)
        if hasattr(strategy, "set_ticker") and hasattr(self, "_current_ticker"):
            strategy.set_ticker(self._current_ticker)
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

        # GapStrategy: 전일 종가 설정
        if hasattr(strategy, "set_prev_close") and prev_day_df is not None:
            strategy.set_prev_close(float(prev_day_df.iloc[-1]["close"]))

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

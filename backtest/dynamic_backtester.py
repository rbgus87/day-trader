"""backtest/dynamic_backtester.py — 동적 유니버스 백테스터.

날짜별로 유니버스가 바뀌는 환경에서 MomentumStrategy를 시뮬레이션한다.
기존 backtester_fast.py의 FastBacktester를 import하여 재사용한다 (복사 금지).

주요 특징:
- universe_simulator로 날짜별 유니버스를 생성
- 날짜별로 해당 유니버스 종목의 분봉 로드 → FastBacktester 단일 날짜 시뮬
- max_positions=3, 시장 필터, 모든 청산 로직은 기존과 동일
- prev_day 데이터는 intraday_candles(분봉 전일 집계) 우선, 없으면 ticker_daily_ohlcv 사용
- 당일 종목이 유니버스에서 빠져도 모든 포지션은 당일 15:10 강제 청산 (오버나이트 없음)

속도 최적화 (v2):
- 분봉 사전 분할: (ticker, date_ymd) → DataFrame (DataFrame 스캔 제거)
- 전일 데이터 사전 계산: (ticker, date_ymd) → {high, volume, close}
- ATR 일괄 로드: ticker_atr 테이블 1회 bulk 조회 → dict 캐시
- FastBacktester / MomentumStrategy 1회 생성 후 ticker마다 reset 재사용
"""
from __future__ import annotations

import asyncio
import dataclasses
import sqlite3
from pathlib import Path
from typing import Any, Sequence

import pandas as pd
from loguru import logger

from backtest.backtester import (
    Backtester,
    build_intraday_blocked_by_date,
    build_market_strong_by_date,
)
from backtest.backtester_fast import FastBacktester
from backtest.universe_simulator import UniverseSimulator
from config.settings import AppConfig, BacktestConfig, TradingConfig
from core.cost_model import TradeCosts
from data.db_manager import DbManager
from strategy.momentum_strategy import MomentumStrategy

_DB_PATH = Path(__file__).parent.parent / "daytrader.db"
_BROAD_UNIVERSE_PATH = Path(__file__).parent.parent / "config" / "universe_broad.yaml"


def _load_broad_universe(path: str | None = None) -> list[dict]:
    """config/universe_broad.yaml 로드."""
    import yaml
    p = Path(path) if path else _BROAD_UNIVERSE_PATH
    if not p.exists():
        raise FileNotFoundError(f"유니버스 파일 없음: {p}")
    with open(p, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data.get("stocks", [])


def _prev_day_from_intraday(
    ticker: str,
    date: str,              # YYYYMMDD
    all_candles: dict[str, pd.DataFrame],
) -> dict | None:
    """분봉에서 전일 고가/거래량/종가 추출."""
    df = all_candles.get(ticker)
    if df is None or df.empty:
        return None
    if "date" not in df.columns:
        df = df.copy()
        df["date"] = df["ts"].dt.strftime("%Y%m%d")
    prev = df[df["date"] < date]
    if prev.empty:
        return None
    prev_date = prev["date"].max()
    prev_df = prev[prev["date"] == prev_date]
    if prev_df.empty:
        return None
    return {
        "high":   float(prev_df["high"].max()),
        "volume": int(prev_df["volume"].sum()),
        "close":  float(prev_df.iloc[-1]["close"]),
    }


def _prev_day_from_daily(
    ticker: str,
    date: str,              # YYYYMMDD
    db_path: str,
) -> dict | None:
    """ticker_daily_ohlcv에서 전일 데이터 조회."""
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            "SELECT high, volume, close FROM ticker_daily_ohlcv "
            "WHERE ticker=? AND dt < ? ORDER BY dt DESC LIMIT 1",
            (ticker, date),
        )
        row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        return None
    return {"high": float(row[0] or 0), "volume": int(row[1] or 0), "close": float(row[2] or 0)}


class DynamicBacktester:
    """동적 유니버스 기반 다종목 백테스터.

    사용 예::

        bt = DynamicBacktester()
        result = await bt.run("2025-04-01", "2026-04-10", broad_pool, config)
        print(result["profit_factor"], result["total_pnl"])
    """

    def __init__(
        self,
        db_path: str | None = None,
        config: TradingConfig | None = None,
        bt_config: BacktestConfig | None = None,
        top_n: int = 80,
    ):
        self._db_path   = db_path or str(_DB_PATH)
        self._config    = config or TradingConfig()
        self._bt_config = bt_config or BacktestConfig()
        self._top_n     = top_n
        self._simulator = UniverseSimulator(self._db_path)

    async def _load_candles_batch(
        self,
        tickers: Sequence[str],
        start_date: str,
        end_date: str,
    ) -> dict[str, pd.DataFrame]:
        """지정 종목 전체 분봉을 DB에서 한 번에 로드."""
        if not tickers:
            return {}
        placeholders = ",".join("?" * len(tickers))
        conn = sqlite3.connect(self._db_path)
        try:
            cur = conn.execute(
                f"SELECT ticker, ts, open, high, low, close, volume FROM intraday_candles "
                f"WHERE ticker IN ({placeholders}) AND tf='1m' "
                f"AND ts >= ? AND ts <= ? ORDER BY ticker, ts ASC",
                list(tickers) + [start_date, end_date + " 23:59:59"],
            )
            rows = cur.fetchall()
        finally:
            conn.close()

        if not rows:
            return {}

        df_all = pd.DataFrame(rows, columns=["ticker", "ts", "open", "high", "low", "close", "volume"])
        df_all["ts"] = pd.to_datetime(df_all["ts"])
        df_all["time"] = df_all["ts"].dt.strftime("%H:%M")

        result: dict[str, pd.DataFrame] = {}
        for ticker, grp in df_all.groupby("ticker", sort=False):
            result[str(ticker)] = grp.drop(columns=["ticker"]).reset_index(drop=True)
        return result

    def _ticker_market(self, ticker: str, stocks_meta: list[dict]) -> str:
        for s in stocks_meta:
            if s.get("ticker") == ticker:
                return s.get("market", "unknown")
        return "unknown"

    async def run(
        self,
        start_date: str,
        end_date: str,
        broad_pool: Sequence[str] | None = None,
        stocks_meta: list[dict] | None = None,
        verbose: bool = True,
    ) -> dict[str, Any]:
        """동적 유니버스 백테스트 실행.

        Args:
            start_date:  "YYYY-MM-DD" 또는 "YYYYMMDD"
            end_date:    "YYYY-MM-DD" 또는 "YYYYMMDD"
            broad_pool:  전체 후보 종목 리스트 (None이면 universe_broad.yaml 로드)
            stocks_meta: 종목 메타 [{ticker, market, ...}] (market 필터용)
            verbose:     진행 상황 로그 출력

        Returns:
            {profit_factor, total_pnl, total_trades, win_rate,
             max_drawdown, trades, universe_stats}
        """
        # 날짜 정규화
        start_iso = start_date[:4] + "-" + start_date[4:6] + "-" + start_date[6:] if len(start_date) == 8 else start_date
        end_iso   = end_date[:4]   + "-" + end_date[4:6]   + "-" + end_date[6:]   if len(end_date)   == 8 else end_date
        start_ymd = start_iso.replace("-", "")
        end_ymd   = end_iso.replace("-", "")

        # 유니버스 로드
        if broad_pool is None:
            meta = _load_broad_universe()
            broad_pool = [s["ticker"] for s in meta]
            stocks_meta = stocks_meta or meta
        stocks_meta = stocks_meta or []

        logger.info(f"[DYNAMIC] broad_pool {len(broad_pool)}종목, 기간 {start_iso}~{end_iso}")

        # 1. 날짜별 유니버스 시뮬
        universe_by_date = self._simulator.simulate_period(
            start_ymd, end_ymd, broad_pool, top_n=self._top_n,
        )
        if not universe_by_date:
            logger.warning("[DYNAMIC] 유니버스 없음 — 백테스트 불가")
            return self._empty_result()

        active_tickers: set[str] = set()
        for tickers in universe_by_date.values():
            active_tickers.update(tickers)
        logger.info(f"[DYNAMIC] 활성 종목 {len(active_tickers)}개 (기간 통합)")

        # 2. 분봉 일괄 로드
        logger.info("[DYNAMIC] 분봉 로드 중...")
        all_candles = await self._load_candles_batch(list(active_tickers), start_iso, end_iso)
        logger.info(f"[DYNAMIC] 분봉 로드 완료: {len(all_candles)}종목")

        # 2-1. 날짜별 캐시 사전 분할 (주요 병목 제거)
        logger.info("[DYNAMIC] 날짜별 캐시 구축 중...")
        day_candles, prev_day_cache = _build_day_cache(all_candles)
        del all_candles  # 원본 해제
        logger.info(f"[DYNAMIC] 캐시 완료: {len(day_candles)} 종목-일")

        # 2-2. ATR 일괄 로드
        atr_by_ticker = _build_atr_by_ticker(self._db_path, list(active_tickers))
        logger.info(f"[DYNAMIC] ATR 캐시: {sum(len(v) for v in atr_by_ticker.values())}건")

        # 3. 시장 필터 맵 생성
        market_strong = build_market_strong_by_date(self._db_path)
        intraday_blocked: dict | None = None
        if getattr(self._config, "intraday_market_filter_enabled", False):
            intraday_blocked = build_intraday_blocked_by_date(
                self._db_path,
                block_threshold=getattr(self._config, "intraday_block_threshold", -0.01),
                resume_threshold=getattr(self._config, "intraday_resume_threshold", -0.005),
            )

        # 3-1. ticker→market 캐시 + limit_up 설정
        market_by_ticker: dict[str, str] = {
            s["ticker"]: s.get("market", "unknown") for s in (stocks_meta or [])
        }
        lu_enabled = getattr(self._config, "limit_up_exit_enabled", False)
        lu_pct     = getattr(self._config, "limit_up_pct", 0.30)

        # 4. FastBacktester / MomentumStrategy 1회 생성 (ticker마다 reset 재사용)
        shared_bt = FastBacktester(
            db=None,
            config=self._config,
            backtest_config=self._bt_config,
            ticker_market="unknown",
            market_strong_by_date=market_strong,
            atr_db_path=self._db_path,
            intraday_blocked_by_date=intraday_blocked,
        )
        shared_strat = MomentumStrategy(self._config)

        # 5. 날짜 × 종목 시뮬레이션
        all_trades: list[dict] = []
        date_list = sorted(universe_by_date.keys())
        universe_sizes: list[int] = []

        for d_idx, date in enumerate(date_list):
            day_universe = universe_by_date[date]
            universe_sizes.append(len(day_universe))

            day_trades: list[dict] = []
            for ticker in day_universe:
                day_df = day_candles.get((ticker, date))
                if day_df is None or day_df.empty:
                    continue

                # 전일 데이터 (캐시 우선, fallback → ticker_daily_ohlcv)
                prev = prev_day_cache.get((ticker, date))
                if prev is None:
                    prev = _prev_day_from_daily(ticker, date, self._db_path)
                if prev is None or prev["high"] <= 0:
                    continue

                # shared 인스턴스 재설정
                shared_bt._current_ticker  = ticker
                shared_bt._ticker_market   = market_by_ticker.get(ticker, "unknown")
                shared_bt._day_atr_cache   = atr_by_ticker.get(ticker, {})

                # 상한가
                shared_bt._current_limit_up = None
                if lu_enabled:
                    try:
                        from core.price_utils import calculate_limit_up_price
                        lu = calculate_limit_up_price(prev["close"], lu_pct)
                        if lu > 0:
                            shared_bt._current_limit_up = float(lu)
                    except Exception:
                        pass

                # 전략 reset + 세팅
                shared_strat.reset()
                shared_strat.set_ticker(ticker)
                shared_strat.set_prev_day_data(prev["high"], prev["volume"], prev["close"])

                result = shared_bt.run_backtest(day_df, shared_strat)
                for t in result.get("trades", []):
                    t["ticker"] = ticker
                    day_trades.append(t)

            all_trades.extend(day_trades)

            if verbose and (d_idx % 20 == 0 or d_idx == len(date_list) - 1):
                cumulative_pf = _quick_pf(all_trades)
                logger.info(
                    f"[DYNAMIC] {date} ({d_idx+1}/{len(date_list)}) "
                    f"universe={len(day_universe)} day_trades={len(day_trades)} "
                    f"cum_trades={len(all_trades)} cum_pf={cumulative_pf:.3f}"
                )

        # 5. KPI 계산 (Backtester 인스턴스 최소화)
        _kpi_bt = Backtester(db=None, config=self._config)  # type: ignore[arg-type]
        kpi = _kpi_bt.calculate_kpi(all_trades)
        kpi["trades"] = all_trades
        kpi["universe_stats"] = {
            "avg_daily_universe": round(sum(universe_sizes) / max(len(universe_sizes), 1), 1),
            "min_daily_universe": min(universe_sizes) if universe_sizes else 0,
            "max_daily_universe": max(universe_sizes) if universe_sizes else 0,
            "total_days": len(date_list),
        }
        return kpi

    def _empty_result(self) -> dict[str, Any]:
        return {
            "total_trades": 0, "wins": 0, "win_rate": 0.0,
            "profit_factor": 0.0, "total_pnl": 0.0,
            "max_drawdown": 0.0, "sharpe_ratio": 0.0,
            "trades": [], "universe_stats": {},
        }


def _quick_pf(trades: list[dict]) -> float:
    """빠른 Profit Factor 계산 (로그용)."""
    gross_win  = sum(t["pnl"] for t in trades if t.get("pnl", 0) > 0)
    gross_loss = abs(sum(t["pnl"] for t in trades if t.get("pnl", 0) < 0))
    return gross_win / gross_loss if gross_loss > 0 else float("inf")


# ---------------------------------------------------------------------------
# 속도 최적화 헬퍼 (v2)
# ---------------------------------------------------------------------------

def _build_day_cache(
    all_candles: dict[str, pd.DataFrame],
) -> tuple[dict[tuple, pd.DataFrame], dict[tuple, dict]]:
    """분봉을 (ticker, YYYYMMDD) 키로 사전 분할, 전일 데이터도 계산.

    Returns:
        day_candles:    {(ticker, date_ymd): DataFrame}
        prev_day_cache: {(ticker, date_ymd): {high, volume, close}}
    """
    day_candles: dict[tuple, pd.DataFrame]  = {}
    prev_day_cache: dict[tuple, dict]       = {}

    for ticker, df in all_candles.items():
        if df.empty:
            continue

        df2 = df.copy()
        df2["_d"] = df2["ts"].dt.strftime("%Y%m%d")

        date_agg: dict[str, dict] = {}
        sorted_dates: list[str]   = []

        for date_ymd, grp in df2.groupby("_d", sort=True):
            clean = grp.drop(columns=["_d"]).reset_index(drop=True)
            day_candles[(ticker, date_ymd)] = clean
            date_agg[date_ymd] = {
                "high":   float(grp["high"].max()),
                "volume": int(grp["volume"].sum()),
                "close":  float(grp["close"].iloc[-1]),
            }
            sorted_dates.append(date_ymd)

        for i in range(1, len(sorted_dates)):
            prev_day_cache[(ticker, sorted_dates[i])] = date_agg[sorted_dates[i - 1]]

    return day_candles, prev_day_cache


def _build_atr_by_ticker(
    db_path: str,
    tickers: Sequence[str],
) -> dict[str, dict[str, float]]:
    """ticker_atr 테이블 bulk 로드 → {ticker: {date_ymd: atr_pct(비율)}}."""
    if not tickers:
        return {}
    placeholders = ",".join("?" * len(tickers))
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            f"SELECT ticker, dt, atr_pct FROM ticker_atr WHERE ticker IN ({placeholders})",
            list(tickers),
        )
        rows = cur.fetchall()
    finally:
        conn.close()

    result: dict[str, dict[str, float]] = {}
    for ticker, dt, atr_pct in rows:
        date_ymd = dt.replace("-", "")
        result.setdefault(ticker, {})[date_ymd] = float(atr_pct) / 100.0
    return result

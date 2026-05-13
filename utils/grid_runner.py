"""utils/grid_runner.py — 그리드 스크립트 공통 실행 인프라.

캔들 캐싱 + ProcessPool 병렬화를 기본 제공.

사용법::

    import dataclasses
    from utils.grid_runner import load_candle_cache, run_parallel_grid

    cache = await load_candle_cache("2025-04-01", "2026-04-10")

    combos = [{"atr_trail_multiplier": v} for v in [0.5, 1.0, 1.5, 2.0]]

    df = run_parallel_grid(
        combos,
        config_factory=lambda p, cfg: dataclasses.replace(cfg, **p),
        cache=cache,
    )
    print(df.sort_values("pf", ascending=False))
"""
from __future__ import annotations

import asyncio
import multiprocessing as mp
import os
import pickle
import sys
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from typing import Callable

import pandas as pd
import yaml

# loguru 억제 (import 시점)
from loguru import logger
logger.remove()
logger.add(sys.stderr, level="WARNING")


# ---------------------------------------------------------------------------
# GridCache
# ---------------------------------------------------------------------------

@dataclass
class GridCache:
    """캔들 + 설정을 보관하는 불변 캐시.

    load_candle_cache() 로 생성 — 직접 생성 불필요.
    """
    candles: dict          # {ticker: DataFrame}
    ticker_to_market: dict # {ticker: "kospi"|"kosdaq"|"unknown"}
    market_map: dict       # {"20250401": {"kospi": True, ...}, ...}
    base_config: object    # TradingConfig (frozen dataclass)
    bt_config: object      # BacktestConfig

    # 워커에 전달할 pickle 바이트 (prepare_bytes() 로 채워짐)
    candles_bytes: bytes = field(default=b"", repr=False)
    market_map_bytes: bytes = field(default=b"", repr=False)

    def prepare_bytes(self) -> None:
        """candles + market_map 를 한 번 직렬화하여 모든 워커에서 재사용."""
        if not self.candles_bytes:
            self.candles_bytes = pickle.dumps(self.candles)
        if not self.market_map_bytes:
            self.market_map_bytes = pickle.dumps(self.market_map)

    def filter_dates(self, start: str, end: str) -> "GridCache":
        """날짜 범위로 캔들 필터링된 새 GridCache 반환 (시장 맵·설정은 그대로 유지).

        Args:
            start: "YYYY-MM-DD" (포함)
            end:   "YYYY-MM-DD" (포함)
        """
        from datetime import date as _date
        sd = _date.fromisoformat(start)
        ed = _date.fromisoformat(end)
        filtered = {
            tk: df[(df["ts"].dt.date >= sd) & (df["ts"].dt.date <= ed)].copy()
            for tk, df in self.candles.items()
        }
        filtered = {tk: df for tk, df in filtered.items() if not df.empty}
        new = GridCache(
            candles=filtered,
            ticker_to_market=self.ticker_to_market,
            market_map=self.market_map,
            base_config=self.base_config,
            bt_config=self.bt_config,
        )
        new.prepare_bytes()
        return new

    @property
    def size_mb(self) -> float:
        return len(self.candles_bytes) / 1024 / 1024


# ---------------------------------------------------------------------------
# 캔들 로더
# ---------------------------------------------------------------------------

async def load_candle_cache(
    start: str,
    end: str,
    *,
    config_path: str = "config.yaml",
    universe_path: str = "config/universe_backtest.yaml",
    verbose: bool = True,
) -> GridCache:
    """41종목 캔들을 DB에서 1회 로드하여 GridCache 반환.

    Args:
        start:        로드 시작일 (YYYY-MM-DD)
        end:          로드 종료일 (YYYY-MM-DD, 당일 마지막 분봉 포함)
        config_path:  config.yaml 경로
        universe_path: universe_backtest.yaml 경로
        verbose:      진행 출력 여부
    """
    from backtest.backtester import Backtester, build_market_strong_by_date
    from config.settings import AppConfig, BacktestConfig
    from data.db_manager import DbManager

    app_config = AppConfig.from_yaml(config_path)
    base_config = app_config.trading

    bt_cfg_raw = yaml.safe_load(open(config_path, encoding="utf-8")).get("backtest", {})
    bt_config = BacktestConfig(
        commission=bt_cfg_raw.get("commission", 0.00015),
        tax=bt_cfg_raw.get("tax", 0.0020),
        slippage=bt_cfg_raw.get("slippage", 0.0003),
    )

    uni = yaml.safe_load(open(universe_path, encoding="utf-8"))
    stocks = uni.get("stocks", [])
    ticker_to_market = {s["ticker"]: s.get("market", "unknown") for s in stocks}

    db = DbManager(app_config.db_path)
    await db.init()
    bt_loader = Backtester(db=db, config=base_config, backtest_config=bt_config)

    if verbose:
        print(f"[LOAD] {len(stocks)}종목 캔들 ({start}~{end})…", flush=True)

    candles_cache: dict[str, pd.DataFrame] = {}
    for i, s in enumerate(stocks, 1):
        tk = s["ticker"]
        df = await bt_loader.load_candles(tk, start, f"{end} 23:59:59")
        if not df.empty:
            candles_cache[tk] = df
        if verbose and i % 10 == 0:
            print(f"  {i}/{len(stocks)} 완료", flush=True)

    await db.close()
    if verbose:
        print(f"[LOAD] 완료 {len(candles_cache)}/{len(stocks)}", flush=True)

    market_map = build_market_strong_by_date(
        app_config.db_path, ma_length=base_config.market_ma_length
    )

    cache = GridCache(
        candles=candles_cache,
        ticker_to_market=ticker_to_market,
        market_map=market_map,
        base_config=base_config,
        bt_config=bt_config,
    )

    if verbose:
        print("[SERIALIZE] candles + market_map 직렬화…", flush=True)
    cache.prepare_bytes()
    if verbose:
        print(f"  candles: {cache.size_mb:.1f} MB", flush=True)

    return cache


# ---------------------------------------------------------------------------
# 표준 워커 (ProcessPoolExecutor top-level 필수)
# ---------------------------------------------------------------------------

def _standard_backtest_worker(args: tuple) -> dict:
    """표준 워커: config 1개 × 전체 종목 백테스트.

    args:
        (config, candles_bytes, market_map_bytes, ticker_to_market, bt_config, params_dict)

    반환:
        {**params_dict, "pf": ..., "pnl": ..., "trades": ..., "win_rate": ...,
         "fc_pct": ..., "exit_counts": {...}}
    """
    config, candles_bytes, market_map_bytes, ticker_to_market, bt_config, params_dict = args

    from loguru import logger as _l
    _l.remove()
    _l.add(sys.stderr, level="WARNING")

    import asyncio as _asyncio
    from backtest.backtester import Backtester as _BT
    from strategy.momentum_strategy import MomentumStrategy as _MS

    candles_cache: dict = pickle.loads(candles_bytes)
    market_map: dict = pickle.loads(market_map_bytes)

    all_trades: list[dict] = []
    for tk, df in candles_cache.items():
        market = ticker_to_market.get(tk, "unknown")
        bt = _BT(
            db=None, config=config, backtest_config=bt_config,
            ticker_market=market, market_strong_by_date=market_map,
        )
        strategy = _MS(config)
        result = _asyncio.run(bt.run_multi_day_cached(tk, df, strategy))
        for t in result.get("trades", []):
            t["ticker"] = tk
            all_trades.append(t)

    stats = compute_stats(all_trades)
    return {**params_dict, **stats}


# ---------------------------------------------------------------------------
# stats 계산 (공개 함수 — 스크립트에서 직접 사용 가능)
# ---------------------------------------------------------------------------

def compute_stats(trades: list[dict]) -> dict:
    """거래 목록 → 표준 KPI dict.

    반환 키:
        pf, pnl, trades, win_rate, fc_pct, exit_counts
    """
    n = len(trades)
    if n == 0:
        return {
            "pf": 0.0, "pnl": 0, "trades": 0,
            "win_rate": 0.0, "fc_pct": 0.0, "exit_counts": {},
        }
    gp = sum(t["pnl"] for t in trades if t["pnl"] > 0)
    gl = abs(sum(t["pnl"] for t in trades if t["pnl"] < 0))
    pnl = sum(t["pnl"] for t in trades)
    wins = sum(1 for t in trades if t["pnl"] > 0)
    exits = Counter(t.get("exit_reason", "?") for t in trades)
    return {
        "pf": round(gp / gl, 4) if gl > 0 else float("inf"),
        "pnl": int(pnl),
        "trades": n,
        "win_rate": round(wins / n, 4),
        "fc_pct": round(exits.get("forced_close", 0) / n * 100, 2),
        "exit_counts": dict(exits),
    }


# ---------------------------------------------------------------------------
# 병렬 그리드 실행
# ---------------------------------------------------------------------------

def run_parallel_grid(
    param_combinations: list[dict],
    config_factory: Callable[[dict, object], object],
    cache: GridCache,
    *,
    max_workers: int | None = None,
    worker_fn: Callable | None = None,
) -> pd.DataFrame:
    """param_combinations를 ProcessPoolExecutor로 병렬 실행하고 DataFrame 반환.

    Args:
        param_combinations: [{"param_a": v1, "param_b": v2}, ...] 형태의 조합 리스트.
            각 dict의 키는 TradingConfig 필드명 또는 worker_fn에서 해석하는 임의 키.
        config_factory:     (params_dict, base_config) → TradingConfig.
            메인 프로세스에서 호출 (pickle 불필요). 결과 TradingConfig은 picklable.
        cache:              load_candle_cache() 로 얻은 GridCache.
        max_workers:        워커 수. None이면 min(4, cpu_count-1).
        worker_fn:          커스텀 워커 함수. None이면 _standard_backtest_worker 사용.
            시그니처: (config, candles_bytes, market_map_bytes, ticker_to_market,
                      bt_config, params_dict) → dict

    반환:
        각 조합의 결과를 행으로 갖는 DataFrame.
        기본 컬럼: params_dict 키들 + pf, pnl, trades, win_rate, fc_pct.
    """
    if not param_combinations:
        return pd.DataFrame()

    cache.prepare_bytes()
    fn = worker_fn or _standard_backtest_worker
    n_workers = max_workers or max(2, min(4, (os.cpu_count() or 4) - 1))

    # 메인 프로세스에서 config 생성 (config_factory는 pickle 불필요)
    worker_args = [
        (
            config_factory(p, cache.base_config),
            cache.candles_bytes,
            cache.market_map_bytes,
            cache.ticker_to_market,
            cache.bt_config,
            p,
        )
        for p in param_combinations
    ]

    n = len(param_combinations)
    results: list[dict] = []
    t0 = time.time()

    print(
        f"[GRID] {n}조합 × {len(cache.candles)}종목  workers={n_workers}",
        flush=True,
    )

    try:
        from tqdm import tqdm
        _use_tqdm = True
    except ImportError:
        _use_tqdm = False

    ctx = mp.get_context("spawn")
    try:
        with ProcessPoolExecutor(max_workers=n_workers, mp_context=ctx) as ex:
            if _use_tqdm:
                it = tqdm(ex.map(fn, worker_args), total=n, desc="grid", unit="combo")
            else:
                it = ex.map(fn, worker_args)

            for i, r in enumerate(it, 1):
                results.append(r)
                if not _use_tqdm:
                    elapsed = time.time() - t0
                    eta = elapsed / i * (n - i) if i < n else 0
                    print(
                        f"  [{i:>3}/{n}] "
                        + " ".join(f"{k}={v}" for k, v in list(r.items())[:3])
                        + f" pf={r.get('pf', '?'):.3f} (ETA {eta:.0f}s)",
                        flush=True,
                    )

    except Exception as exc:
        print(f"[WARN] ProcessPool 실패 ({exc}), 순차 실행 전환", flush=True)
        results = []
        t0 = time.time()
        if _use_tqdm:
            iter_args = tqdm(worker_args, desc="grid (seq)", unit="combo")
        else:
            iter_args = worker_args
        for i, wargs in enumerate(iter_args, 1):
            r = fn(wargs)
            results.append(r)
            if not _use_tqdm:
                elapsed = time.time() - t0
                print(f"  [{i:>3}/{n}] pf={r.get('pf', '?'):.3f} ({elapsed:.0f}s)", flush=True)

    elapsed_total = time.time() - t0
    print(f"[DONE] {n}조합 완료 ({elapsed_total:.1f}s)", flush=True)

    df = pd.DataFrame(results)
    # exit_counts 컬럼은 dict라 DataFrame에서 보기 불편 — 별도 컬럼으로 확장하지 않고 보존
    return df

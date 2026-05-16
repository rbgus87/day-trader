"""scripts/run_dynamic_backtest.py — 동적 유니버스 vs 고정 41종목 백테스트 비교.

동적 유니버스 백테스트를 실행하고 기존 고정 41종목 결과와 비교 출력.
결과는 reports/dynamic_vs_fixed_comparison.md에 저장.

사용법:
    python -u scripts/run_dynamic_backtest.py                        # 기본 (2025-04-01~2026-04-10)
    python -u scripts/run_dynamic_backtest.py --start 2025-04-01 --end 2026-04-10
    python -u scripts/run_dynamic_backtest.py --top-n 60            # 일별 상위 60종목
    python -u scripts/run_dynamic_backtest.py --dry-run             # universe_simulator 검증만
"""
from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import pickle
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from dotenv import load_dotenv
from loguru import logger

load_dotenv()

sys.path.insert(0, str(Path(__file__).parent.parent))

import yaml

from backtest.dynamic_backtester import DynamicBacktester, _load_broad_universe
from backtest.universe_simulator import UniverseSimulator
from config.settings import AppConfig, BacktestConfig

_REPORTS_DIR = Path(__file__).parent.parent / "reports"
_BACKTEST_UNIVERSE_PATH = Path(__file__).parent.parent / "config" / "universe_backtest.yaml"
_BROAD_UNIVERSE_PATH = Path(__file__).parent.parent / "config" / "universe_broad.yaml"
_MOMENTUM_UNIVERSE_PATH = Path(__file__).parent.parent / "config" / "universe_broad_momentum.yaml"

# 고정 41종목 baseline 수치 (CLAUDE.md 기준)
FIXED_BASELINE = {
    "total_trades": 228,
    "profit_factor": 4.881,
    "total_pnl": 295_690,
    "win_rate": 0.658,
    "avg_daily_universe": 41,
    "period": "2025-04-01 ~ 2026-04-10",
    "exit_dist": {
        "forced_close": 92,
        "breakeven_stop": 49,
        "momentum_fade": 42,
        "stop_loss": 26,
        "trailing_stop": 7,
        "limit_up_exit": 5,
    },
}


def _load_backtest_universe() -> list[dict]:
    """config/universe_backtest.yaml 로드."""
    if not _BACKTEST_UNIVERSE_PATH.exists():
        return []
    with open(_BACKTEST_UNIVERSE_PATH, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data.get("stocks", [])


def _compute_exit_dist(trades: list[dict]) -> dict[str, int]:
    return dict(Counter(t.get("exit_reason", "unknown") for t in trades))


def _format_comparison(fixed: dict, dynamic: dict, dynamic_stats: dict) -> str:
    """비교 테이블 문자열 생성."""
    rows = [
        ("일평균 유니버스",
         str(fixed["avg_daily_universe"]),
         f"~{dynamic_stats.get('avg_daily_universe', 0):.0f}"),
        ("총 거래",
         str(fixed["total_trades"]),
         str(dynamic["total_trades"])),
        ("Profit Factor",
         f"{fixed['profit_factor']:.3f}",
         f"{dynamic['profit_factor']:.3f}"),
        ("PnL (원)",
         f"+{fixed['total_pnl']:,}",
         f"{dynamic['total_pnl']:+,}"),
        ("승률",
         f"{fixed['win_rate']:.1%}",
         f"{dynamic['win_rate']:.1%}"),
        ("Max Drawdown",
         "N/A",
         f"{dynamic['max_drawdown']:,.0f}"),
    ]

    lines = [
        "┌──────────────┬─────────────┬─────────────────┐",
        "│              │ 고정 41종목 │ 동적 300종목 풀  │",
        "├──────────────┼─────────────┼─────────────────┤",
    ]
    for label, fixed_val, dynamic_val in rows:
        lines.append(f"│ {label:<12} │ {fixed_val:>11} │ {dynamic_val:>15} │")
    lines.append("└──────────────┴─────────────┴─────────────────┘")
    return "\n".join(lines)


def _format_exit_dist(dist: dict[str, int], total: int) -> str:
    lines = []
    for reason, cnt in sorted(dist.items(), key=lambda x: -x[1]):
        pct = cnt / total * 100 if total > 0 else 0
        lines.append(f"  {reason:<18}: {cnt:>4}건 ({pct:.1f}%)")
    return "\n".join(lines)


def _save_report(
    fixed: dict,
    dynamic_result: dict,
    universe_stats: dict,
    start_date: str,
    end_date: str,
    top_n: int,
    elapsed_min: float,
) -> Path:
    _REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = _REPORTS_DIR / "dynamic_vs_fixed_comparison.md"

    dynamic_trades = dynamic_result.get("trades", [])
    exit_dist = _compute_exit_dist(dynamic_trades)

    comparison_table = _format_comparison(fixed, dynamic_result, universe_stats)
    exit_str = _format_exit_dist(exit_dist, dynamic_result.get("total_trades", 0))
    fixed_exit_str = _format_exit_dist(fixed.get("exit_dist", {}), fixed["total_trades"])

    content = f"""# 동적 유니버스 vs 고정 41종목 백테스트 비교

> 생성: {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M')}
> 기간: {start_date} ~ {end_date}
> 동적 유니버스 일별 상위: {top_n}종목
> 소요시간: {elapsed_min:.1f}분

## 비교 요약

```
{comparison_table}
```

## 동적 유니버스 통계

- 일평균 유니버스: {universe_stats.get('avg_daily_universe', 0):.1f}종목
- 최소: {universe_stats.get('min_daily_universe', 0)}종목
- 최대: {universe_stats.get('max_daily_universe', 0)}종목
- 총 영업일: {universe_stats.get('total_days', 0)}일

## 청산 분포

### 동적 유니버스
{exit_str}

### 고정 41종목 (baseline)
{fixed_exit_str}

## 해석 노트

- **PF 비교**: 동적 유니버스 PF {dynamic_result.get('profit_factor', 0):.3f} vs 고정 {fixed['profit_factor']:.3f}
- **PnL 비교**: {dynamic_result.get('total_pnl', 0):+,.0f}원 vs +{fixed['total_pnl']:,}원
- 동적 유니버스는 매일 종목이 바뀌므로 백테스트와 실거래 괴리가 더 클 수 있음
- 개별 종목 1주 단위 비율 시뮬 (자본 미고려) — 고정 baseline과 동일 방식
"""

    with open(report_path, "w", encoding="utf-8") as f:
        f.write(content)

    return report_path


# ---------------------------------------------------------------------------
# dry-run: 유니버스 시뮬레이터 검증
# ---------------------------------------------------------------------------

def dry_run_universe(db_path: str, broad_pool: list[str], start_date: str, end_date: str, top_n: int) -> None:
    """universe_simulator 결과를 날짜별로 출력 (분봉 수집 없이)."""
    start_ymd = start_date.replace("-", "")
    end_ymd   = end_date.replace("-", "")

    sim = UniverseSimulator(db_path)
    logger.info(f"유니버스 시뮬 검증 ({start_date}~{end_date}, broad_pool={len(broad_pool)}종목)")
    universe_by_date = sim.simulate_period(start_ymd, end_ymd, broad_pool, top_n=top_n)

    sizes = [len(v) for v in universe_by_date.values()]
    if not sizes:
        logger.warning("유니버스 없음 — ticker_daily_ohlcv 데이터 확인 필요")
        return

    avg = sum(sizes) / len(sizes)
    print(f"\n유니버스 시뮬 결과: {len(universe_by_date)}일")
    print(f"  일평균: {avg:.1f}종목")
    print(f"  최소: {min(sizes)} / 최대: {max(sizes)}")
    print(f"\n처음 5일:")
    for date in sorted(universe_by_date.keys())[:5]:
        universe = universe_by_date[date]
        print(f"  {date}: {len(universe)}종목 - {universe[:5]}{'...' if len(universe) > 5 else ''}")


# ---------------------------------------------------------------------------
# 메인
# ---------------------------------------------------------------------------

async def _main(args: argparse.Namespace) -> None:
    logger.remove()
    logger.add(sys.stderr, level="INFO", format="{time:HH:mm:ss} | {level:<7} | {message}")

    config = AppConfig.from_yaml()
    db_path = config.db_path

    # 유니버스 파일 결정 (--universe 옵션 우선)
    if args.universe:
        universe_path = Path(__file__).parent.parent / "config" / args.universe
    elif args.momentum:
        universe_path = _MOMENTUM_UNIVERSE_PATH
    else:
        universe_path = _BROAD_UNIVERSE_PATH

    if not universe_path.exists():
        logger.error(f"유니버스 파일 없음: {universe_path}")
        if args.momentum:
            logger.error("먼저 실행: python -u scripts/collect_broad_universe.py --momentum --select-only")
        else:
            logger.error("먼저 실행: python -u scripts/collect_broad_universe.py --select-only")
        return

    stocks_meta = _load_broad_universe(str(universe_path))
    broad_pool = [s["ticker"] for s in stocks_meta]
    logger.info(f"유니버스: {len(broad_pool)}종목 ({universe_path.name})")

    # dry-run 모드
    if args.dry_run:
        dry_run_universe(db_path, broad_pool, args.start, args.end, args.top_n)
        return

    # 백테스트 config (config.yaml 그대로 사용)
    trading_config = config.trading
    bt_config = config.backtest

    logger.info(f"동적 유니버스 백테스트 시작: {args.start}~{args.end}, top_n={args.top_n}")
    t0 = time.time()

    backtester = DynamicBacktester(
        db_path=db_path,
        config=trading_config,
        bt_config=bt_config,
        top_n=args.top_n,
    )

    result = await backtester.run(
        start_date=args.start,
        end_date=args.end,
        broad_pool=broad_pool,
        stocks_meta=stocks_meta,
        verbose=True,
    )

    elapsed_min = (time.time() - t0) / 60
    universe_stats = result.get("universe_stats", {})

    # 결과 출력
    comparison_table = _format_comparison(FIXED_BASELINE, result, universe_stats)
    print("\n" + "=" * 60)
    print("동적 유니버스 백테스트 완료")
    print("=" * 60)
    print(comparison_table)
    print(f"\n소요시간: {elapsed_min:.1f}분")

    exit_dist = _compute_exit_dist(result.get("trades", []))
    print("\n청산 분포 (동적):")
    print(_format_exit_dist(exit_dist, result.get("total_trades", 0)))

    # 거래 데이터 JSON 저장 (분석용)
    trades_path = _REPORTS_DIR / "dynamic_trades.json"
    _REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    trades_raw = result.get("trades", [])
    serializable = []
    for t in trades_raw:
        row = dict(t)
        for k, v in row.items():
            if hasattr(v, "isoformat"):
                row[k] = v.isoformat()
        serializable.append(row)
    with open(trades_path, "w", encoding="utf-8") as f:
        json.dump(serializable, f, ensure_ascii=False, indent=2)
    logger.info(f"거래 데이터 저장: {trades_path} ({len(serializable)}건)")

    # 리포트 저장
    report_path = _save_report(
        FIXED_BASELINE, result, universe_stats,
        args.start, args.end, args.top_n, elapsed_min,
    )
    logger.info(f"리포트 저장: {report_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="동적 유니버스 vs 고정 41종목 백테스트 비교")
    parser.add_argument("--start",    type=str, default="2025-04-01", help="시작일 (YYYY-MM-DD)")
    parser.add_argument("--end",      type=str, default="2026-04-10", help="종료일 (YYYY-MM-DD)")
    parser.add_argument("--top-n",    type=int, default=80, help="일별 유니버스 상위 N종목 (기본 80)")
    parser.add_argument("--dry-run",  action="store_true", help="유니버스 시뮬레이터 검증만 (수집 없음)")
    parser.add_argument("--momentum", action="store_true", help="모멘텀 유니버스(universe_broad_momentum.yaml) 사용")
    parser.add_argument("--universe", type=str, default="",
                        help="사용할 유니버스 파일명 (config/ 하위, 예: universe_broad_momentum.yaml)")
    args = parser.parse_args()
    asyncio.run(_main(args))


if __name__ == "__main__":
    main()

"""scripts/baseline_pf_limit_up.py — baseline PF + limit_up_exit 발동 건수.

analyze_baseline.py 의 collect_all_trades 를 재사용해 현재 config 기준으로
전체 유니버스 백테스트를 돌려 PF, 거래 건수, 청산 사유 분포를 보고한다.

사용:
    python scripts/baseline_pf_limit_up.py
    python scripts/baseline_pf_limit_up.py --start 2025-04-01 --end 2026-04-10
"""

import argparse
import asyncio
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.analyze_baseline import collect_all_trades


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2025-04-01")
    parser.add_argument("--end", default="2026-04-10")
    args = parser.parse_args()

    print("=" * 64)
    print(f" baseline PF + limit_up_exit  ({args.start} ~ {args.end})")
    print("=" * 64)

    trades = await collect_all_trades(args.start, args.end)
    if not trades:
        print("ERROR: 거래 없음.")
        return

    total = len(trades)
    pnl = sum(t["pnl"] for t in trades)
    gp = sum(t["pnl"] for t in trades if t["pnl"] > 0)
    gl = abs(sum(t["pnl"] for t in trades if t["pnl"] < 0))
    pf = gp / gl if gl > 0 else float("inf")
    exit_dist = Counter(t.get("exit_reason", "?") for t in trades)

    print()
    print(f"  총 거래: {total}건")
    print(f"  PF    : {pf:.3f}")
    print(f"  총 PnL: {pnl:+,.0f}")
    print(f"  거래당 PnL: {pnl/total:+,.1f}")
    print()
    print("  청산 사유:")
    for reason, cnt in exit_dist.most_common():
        ratio = cnt / total * 100
        print(f"    {reason:<18} {cnt:>4}건 ({ratio:>5.1f}%)")

    lu = exit_dist.get("limit_up_exit", 0)
    lu_trades = [t for t in trades if t.get("exit_reason") == "limit_up_exit"]
    if lu:
        lu_pnl = sum(t["pnl"] for t in lu_trades)
        lu_avg_pct = sum(t.get("pnl_pct", 0) for t in lu_trades) / lu
        print()
        print(f"  limit_up_exit 상세:")
        print(f"    발동 {lu}건 / 총 PnL {lu_pnl:+,.0f} / 거래당 평균 PnL% {lu_avg_pct*100:+.2f}%")
        by_tk = Counter(t["ticker"] for t in lu_trades)
        print(f"    종목별 Top 5: {by_tk.most_common(5)}")


if __name__ == "__main__":
    asyncio.run(main())

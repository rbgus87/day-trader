"""scripts/analyze_paper_log.py — JSONL 구조화 로그 장후 분석.

사용:
    python scripts/analyze_paper_log.py [--days N] [--date YYYYMMDD] [--log-dir logs]

기본값: 오늘 + 어제(2일)
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path


def load_jsonl(log_dir: str = "logs", days: int = 2, target_date: str | None = None) -> list[dict]:
    """지정 기간 JSONL 파일 로드."""
    log_path = Path(log_dir)
    records: list[dict] = []

    if target_date:
        files = [log_path / f"daytrader_{target_date}.jsonl"]
    else:
        files = []
        for i in range(days):
            d = date.today() - timedelta(days=i)
            files.append(log_path / f"daytrader_{d.strftime('%Y%m%d')}.jsonl")

    for f in files:
        if not f.exists():
            continue
        try:
            with open(f, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        try:
                            records.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass
        except Exception as e:
            print(f"[WARN] {f} 읽기 실패: {e}")

    return records


def _pct(part: int, total: int) -> str:
    if total == 0:
        return "0.0%"
    return f"{part / total * 100:.1f}%"


def _fmt_pnl(v: int | float) -> str:
    return f"{int(v):+,}"


# ─────────────────────────────────────────────────────────
# 분석 함수
# ─────────────────────────────────────────────────────────

def analyze_entries(records: list[dict]) -> None:
    entries = [r for r in records if r.get("event") == "entry"]
    if not entries:
        print("  [entry] 데이터 없음")
        return

    breakout_pcts = [r.get("breakout_pct", 0) for r in entries]
    atr_pcts = [r.get("atr_pct", 0) for r in entries]
    by_ticker: dict[str, int] = defaultdict(int)
    for r in entries:
        by_ticker[r.get("ticker", "??")] += 1

    print(f"  총 진입: {len(entries)}건")
    print(f"  돌파폭  avg={sum(breakout_pcts)/len(breakout_pcts):.2f}%  "
          f"min={min(breakout_pcts):.2f}%  max={max(breakout_pcts):.2f}%")
    print(f"  ATR     avg={sum(atr_pcts)/len(atr_pcts):.2f}%")
    if by_ticker:
        top = sorted(by_ticker.items(), key=lambda x: -x[1])[:5]
        print(f"  다빈도 종목: {', '.join(f'{t}×{c}' for t, c in top)}")


def analyze_exits(records: list[dict]) -> None:
    exits = [r for r in records if r.get("event") == "exit"]
    if not exits:
        print("  [exit] 데이터 없음")
        return

    total = len(exits)
    by_reason: dict[str, list[dict]] = defaultdict(list)
    for r in exits:
        by_reason[r.get("reason", "unknown")].append(r)

    wins = [r for r in exits if r.get("pnl", 0) >= 0]
    losses = [r for r in exits if r.get("pnl", 0) < 0]
    total_pnl = sum(r.get("pnl", 0) for r in exits)
    win_rate = len(wins) / total if total > 0 else 0
    hold_times = [r["hold_minutes"] for r in exits if r.get("hold_minutes") is not None]

    print(f"  총 청산: {total}건  승률={win_rate:.1%}  총PnL={_fmt_pnl(total_pnl)}")
    if hold_times:
        print(f"  보유시간 avg={sum(hold_times)/len(hold_times):.1f}분  "
              f"min={min(hold_times):.1f}분  max={max(hold_times):.1f}분")
    print()
    print(f"  {'청산사유':<20} {'건수':>5} {'비율':>7} {'총PnL':>12} {'승':>5} {'패':>5} {'평균PnL':>10}")
    print(f"  {'-'*20} {'-'*5} {'-'*7} {'-'*12} {'-'*5} {'-'*5} {'-'*10}")

    order = [
        "limit_up_exit", "trailing_stop", "breakeven_stop",
        "stop_loss", "momentum_fade", "forced_close", "ws_filled",
    ]
    reasons_sorted = sorted(by_reason.keys(), key=lambda r: order.index(r) if r in order else 99)
    for reason in reasons_sorted:
        group = by_reason[reason]
        n = len(group)
        pnl_sum = sum(r.get("pnl", 0) for r in group)
        avg_pnl = pnl_sum / n if n > 0 else 0
        w = sum(1 for r in group if r.get("pnl", 0) >= 0)
        l = n - w
        print(f"  {reason:<20} {n:>5} {_pct(n, total):>7} {_fmt_pnl(pnl_sum):>12} "
              f"{w:>5} {l:>5} {_fmt_pnl(avg_pnl):>10}")

    if wins:
        avg_win = sum(r.get("pnl", 0) for r in wins) / len(wins)
        print(f"\n  평균 수익: {_fmt_pnl(avg_win)}")
    if losses:
        avg_loss = sum(r.get("pnl", 0) for r in losses) / len(losses)
        print(f"  평균 손실: {_fmt_pnl(avg_loss)}")
    if wins and losses:
        profit_factor = (
            sum(r.get("pnl", 0) for r in wins) /
            abs(sum(r.get("pnl", 0) for r in losses))
        )
        print(f"  Profit Factor: {profit_factor:.3f}")


def analyze_signal_blocked(records: list[dict]) -> None:
    blocked = [r for r in records if r.get("event") == "signal_blocked"]
    if not blocked:
        print("  [signal_blocked] 데이터 없음")
        return

    total = len(blocked)
    by_reason: dict[str, int] = defaultdict(int)
    for r in blocked:
        by_reason[r.get("reason", "unknown")] += 1

    print(f"  총 차단: {total}건")
    for reason, cnt in sorted(by_reason.items(), key=lambda x: -x[1]):
        print(f"    {reason:<20} {cnt:>4}건  ({_pct(cnt, total)})")


def analyze_market_filter(records: list[dict]) -> None:
    events = [r for r in records if r.get("event") == "market_filter"]
    if not events:
        print("  [market_filter] 데이터 없음")
        return

    for r in events:
        ts = r.get("ts", "")
        k = "강세" if r.get("kospi_strong") else "약세"
        q = "강세" if r.get("kosdaq_strong") else "약세"
        print(f"    {ts[11:16]}  KOSPI={k}  KOSDAQ={q}")


def analyze_daily_summary(records: list[dict]) -> None:
    events = [r for r in records if r.get("event") == "daily_summary"]
    if not events:
        print("  [daily_summary] 데이터 없음")
        return

    for r in events:
        print(
            f"  {r.get('date', '??')}  거래={r.get('total_trades', 0)}  "
            f"승={r.get('wins', 0)}  패={r.get('losses', 0)}  "
            f"PnL={_fmt_pnl(r.get('total_pnl', 0))}  "
            f"승률={r.get('win_rate', 0):.1%}  "
            f"MDD={r.get('max_drawdown', 0):.2%}"
        )


# ─────────────────────────────────────────────────────────
# 메인
# ─────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="페이퍼 트레이딩 JSONL 로그 분석")
    parser.add_argument("--days", type=int, default=2, help="분석 기간 (일 수, 기본 2)")
    parser.add_argument("--date", dest="target_date", default=None, help="특정 날짜 YYYYMMDD")
    parser.add_argument("--log-dir", default="logs", help="로그 디렉토리 (기본 logs/)")
    args = parser.parse_args()

    records = load_jsonl(log_dir=args.log_dir, days=args.days, target_date=args.target_date)
    if not records:
        print("분석할 JSONL 레코드가 없습니다.")
        print(f"  로그 경로: {Path(args.log_dir).resolve()}")
        print(f"  파일 패턴: daytrader_YYYYMMDD.jsonl")
        sys.exit(0)

    total = len(records)
    dates = sorted({r.get("ts", "")[:10] for r in records if r.get("ts")})
    print(f"\n{'='*60}")
    print(f"페이퍼 트레이딩 로그 분석  ({', '.join(dates) or '날짜 불명'})")
    print(f"총 {total}개 이벤트 레코드")
    print(f"{'='*60}")

    print("\n[일일 요약]")
    analyze_daily_summary(records)

    print("\n[진입]")
    analyze_entries(records)

    print("\n[청산]")
    analyze_exits(records)

    print("\n[신호 차단]")
    analyze_signal_blocked(records)

    print("\n[시장 필터 상태 변화]")
    analyze_market_filter(records)

    print()


if __name__ == "__main__":
    main()

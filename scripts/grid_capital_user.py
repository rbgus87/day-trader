"""scripts/grid_capital_user.py — 자본금 × max_positions 사용자 시나리오 그리드.

현재 config (min_bp 3% + BE3 + 상한가 즉시 청산) 기준으로
collect_all_trades(전체 Backtester)를 한 번 실행 후
apply_max_positions로 시나리오별 후처리.

시나리오:
  - 300만/3pos (현재 baseline)
  - 500만/3, 500만/4, 500만/5
  - 1000만/3, 1000만/5

출력: reports/capital_grid.md
"""

import asyncio
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.analyze_baseline import collect_all_trades
from scripts.grid_maxpos_capital import apply_max_positions, build_regime_map, DB_PATH


SCENARIOS = [
    (3_000_000, 3, "300만/3pos (baseline)"),
    (5_000_000, 3, "500만/3pos"),
    (5_000_000, 4, "500만/4pos"),
    (5_000_000, 5, "500만/5pos"),
    (10_000_000, 3, "1000만/3pos"),
    (10_000_000, 5, "1000만/5pos"),
]


def compute_metrics(trades, regime_map, capital):
    n = len(trades)
    if n == 0:
        return None
    pnl1 = [t["pnl"] for t in trades]
    pnlc = [t["pnl_capital"] for t in trades]

    gp1 = sum(p for p in pnl1 if p > 0)
    gl1 = abs(sum(p for p in pnl1 if p < 0))
    pf1 = gp1 / gl1 if gl1 > 0 else float("inf")

    gpc = sum(p for p in pnlc if p > 0)
    glc = abs(sum(p for p in pnlc if p < 0))
    pfc = gpc / glc if glc > 0 else float("inf")

    rg = defaultdict(list)
    for t in trades:
        try:
            m = pd.to_datetime(t["entry_ts"]).strftime("%Y-%m")
            rg[regime_map.get(m, "?")].append(t)
        except Exception:
            pass

    rpf_c = {}
    rpf_1 = {}
    rn = {}
    for r, tlist in rg.items():
        rgc = sum(t["pnl_capital"] for t in tlist if t["pnl_capital"] > 0)
        rlc = abs(sum(t["pnl_capital"] for t in tlist if t["pnl_capital"] < 0))
        rpf_c[r] = rgc / rlc if rlc > 0 else float("inf")
        rg1 = sum(t["pnl"] for t in tlist if t["pnl"] > 0)
        rl1 = abs(sum(t["pnl"] for t in tlist if t["pnl"] < 0))
        rpf_1[r] = rg1 / rl1 if rl1 > 0 else float("inf")
        rn[r] = len(tlist)

    cum = peak = max_dd = 0.0
    for p in pnlc:
        cum += p
        if cum > peak:
            peak = cum
        dd = peak - cum
        if dd > max_dd:
            max_dd = dd

    win_rate = sum(1 for p in pnl1 if p > 0) / n * 100
    ed = Counter(t.get("exit_reason", "?") for t in trades)

    return {
        "n": n,
        "pf1": pf1,
        "pfc": pfc,
        "pnl1": sum(pnl1),
        "pnlc": sum(pnlc),
        "per1": sum(pnl1) / n,
        "perc": sum(pnlc) / n,
        "rpf_c": rpf_c,
        "rpf_1": rpf_1,
        "rn": rn,
        "max_dd": max_dd,
        "ret_pct": sum(pnlc) / capital * 100,
        "win_rate": win_rate,
        "ed": dict(ed),
    }


def fmt_pf(v: float) -> str:
    if v == float("inf"):
        return "∞"
    return f"{v:.2f}"


async def main():
    print("=" * 64)
    print(" 자본 × max_positions 그리드 (현재 config: BE3 + 상한가 + min_bp 3%)")
    print("=" * 64)

    # 1. collect_all_trades (full Backtester, 현재 config)
    raw_trades = await collect_all_trades("2025-04-01", "2026-04-15")
    if not raw_trades:
        print("ERROR: 거래 없음")
        return

    # baseline 검증용 출력
    gp = sum(t["pnl"] for t in raw_trades if t["pnl"] > 0)
    gl = abs(sum(t["pnl"] for t in raw_trades if t["pnl"] < 0))
    base_pf = gp / gl if gl > 0 else 0
    print(f"\n[baseline (per-ticker raw)] {len(raw_trades)}건 / PF {base_pf:.2f} / 총 PnL {sum(t['pnl'] for t in raw_trades):+,.0f}")

    regime_map = build_regime_map(DB_PATH)

    # 2. 시나리오별 결과
    rows = []
    print("\nPhase 2: 시나리오별 후처리")
    for cap, mp, label in SCENARIOS:
        filtered = apply_max_positions(raw_trades, mp, cap, cap // mp)
        m = compute_metrics(filtered, regime_map, cap)
        rows.append((label, cap, mp, m))
        print(f"  {label}: n={m['n']}, PF1={m['pf1']:.2f}, PFc={m['pfc']:.2f}, ret={m['ret_pct']:+.1f}%, 약세PF1={fmt_pf(m['rpf_1'].get('약세',0))}")

    # 3. 마크다운 작성
    today = datetime.now().strftime("%Y-%m-%d")
    md = []
    a = md.append
    a(f"# 자본금 × max_positions 그리드 시뮬\n")
    a(f"> 생성: {today}\n")
    a(f"> 백테스트 기간: 2025-04-01 ~ 2026-04-15\n")
    a(f"> 조건: 41종목, min_bp 3% + BE3 + 상한가 즉시 청산 (현재 config)\n")
    a(f"> per-ticker raw: {len(raw_trades)}건 / PF {base_pf:.2f} (CLAUDE.md baseline PF 4.56 / 248건)\n")
    a("\n---\n\n")

    a("## 결과 요약\n\n")
    a("| 시나리오 | PF(1주) | PF(자본) | 거래수 | 총PnL(자본) | 거래당PnL(자본) | 약세PF(1주) | 약세PF(자본) | 수익률 | Max DD | 승률 |\n")
    a("|---|---|---|---|---|---|---|---|---|---|---|\n")
    for label, cap, mp, m in rows:
        if m is None:
            continue
        bear1 = fmt_pf(m["rpf_1"].get("약세", 0))
        bearc = fmt_pf(m["rpf_c"].get("약세", 0))
        a(f"| {label} | {m['pf1']:.2f} | {m['pfc']:.2f} | {m['n']} | {m['pnlc']:+,.0f} | {m['perc']:+,.0f} | {bear1} | {bearc} | {m['ret_pct']:+.1f}% | {m['max_dd']:,.0f} | {m['win_rate']:.1f}% |\n")
    a("\n")

    a("## 국면별 PF (1주 가중)\n\n")
    a("| 시나리오 | 강세 PF (n) | 횡보 PF (n) | 약세 PF (n) |\n")
    a("|---|---|---|---|\n")
    for label, cap, mp, m in rows:
        if m is None:
            continue
        def cell(r):
            return f"{fmt_pf(m['rpf_1'].get(r,0))} ({m['rn'].get(r,0)})"
        a(f"| {label} | {cell('강세')} | {cell('횡보')} | {cell('약세')} |\n")
    a("\n")

    a("## 국면별 PF (자본 가중)\n\n")
    a("| 시나리오 | 강세 | 횡보 | 약세 |\n")
    a("|---|---|---|---|\n")
    for label, cap, mp, m in rows:
        if m is None:
            continue
        a(f"| {label} | {fmt_pf(m['rpf_c'].get('강세',0))} | {fmt_pf(m['rpf_c'].get('횡보',0))} | {fmt_pf(m['rpf_c'].get('약세',0))} |\n")
    a("\n")

    a("## 청산 사유 분포\n\n")
    a("| 시나리오 | forced | trail | stop | BE | limit_up |\n")
    a("|---|---|---|---|---|---|\n")
    for label, cap, mp, m in rows:
        if m is None:
            continue
        ed = m["ed"]
        a(f"| {label} | {ed.get('forced_close',0)} | {ed.get('trailing_stop',0)} | {ed.get('stop_loss',0)} | {ed.get('breakeven_stop',0)} | {ed.get('limit_up_exit',0)} |\n")
    a("\n")

    # 비교 분석
    base = next(r for r in rows if r[0] == SCENARIOS[0][2])
    bm = base[3]
    a("## 분석\n\n")
    a(f"### 1. 자본 증가 효과 (max_positions=3 고정)\n\n")
    a("| 자본 | PF(1주) | PF(자본) | 거래수 | 총PnL(자본) | 수익률 | Max DD |\n")
    a("|---|---|---|---|---|---|---|\n")
    for label, cap, mp, m in rows:
        if mp != 3 or m is None:
            continue
        a(f"| {cap//1_000_000}M | {m['pf1']:.2f} | {m['pfc']:.2f} | {m['n']} | {m['pnlc']:+,.0f} | {m['ret_pct']:+.1f}% | {m['max_dd']:,.0f} |\n")
    a("\n")

    a(f"### 2. max_positions 증가 효과 (자본 500만 고정)\n\n")
    a("| max_pos | PF(1주) | PF(자본) | 거래수 | 총PnL(자본) | 수익률 | 약세PF(1주) |\n")
    a("|---|---|---|---|---|---|---|\n")
    for label, cap, mp, m in rows:
        if cap != 5_000_000 or m is None:
            continue
        a(f"| {mp} | {m['pf1']:.2f} | {m['pfc']:.2f} | {m['n']} | {m['pnlc']:+,.0f} | {m['ret_pct']:+.1f}% | {fmt_pf(m['rpf_1'].get('약세',0))} |\n")
    a("\n")

    out = Path("reports/capital_grid.md")
    out.write_text("".join(md), encoding="utf-8")
    print(f"\n작성 완료: {out}")


if __name__ == "__main__":
    asyncio.run(main())

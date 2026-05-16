"""scripts/analyze_dynamic_result.py — 동적 백테스트 거래 분석.

reports/dynamic_trades.json을 읽어 날짜별 PnL, 시장 특성,
종목 ATR, 시간대 분포를 분석하고 reports/dynamic_analysis.md에 저장.

사용법:
    python -u scripts/analyze_dynamic_result.py
    python -u scripts/analyze_dynamic_result.py --trades reports/dynamic_trades.json
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean, median, stdev

sys.path.insert(0, str(Path(__file__).parent.parent))

from config.settings import AppConfig

_REPORTS_DIR  = Path(__file__).parent.parent / "reports"
_TRADES_PATH  = _REPORTS_DIR / "dynamic_trades.json"
_ANALYSIS_OUT = _REPORTS_DIR / "dynamic_analysis.md"


# ---------------------------------------------------------------------------
# 데이터 로드
# ---------------------------------------------------------------------------

def load_trades(path: Path) -> list[dict]:
    with open(path, "r", encoding="utf-8") as f:
        trades = json.load(f)
    for t in trades:
        if isinstance(t.get("entry_ts"), str):
            t["entry_ts"] = datetime.fromisoformat(t["entry_ts"])
        if isinstance(t.get("exit_ts"), str):
            t["exit_ts"] = datetime.fromisoformat(t["exit_ts"])
    return trades


def load_index_daily(db_path: str) -> dict[str, dict[str, dict]]:
    """index_candles에서 일별 등락률 계산. {index_code: {date_ymd: {open, close, chg_pct}}}"""
    conn = sqlite3.connect(db_path)
    cur  = conn.cursor()
    cur.execute(
        "SELECT index_code, dt, open, close FROM index_candles ORDER BY index_code, dt"
    )
    rows = cur.fetchall()
    conn.close()

    result: dict[str, dict[str, dict]] = defaultdict(dict)
    for code, dt, open_, close in rows:
        chg = (close - open_) / open_ * 100 if open_ > 0 else 0.0
        result[code][dt] = {"open": open_, "close": close, "chg_pct": chg}
    return result


def load_atr_by_ticker(db_path: str, tickers: list[str]) -> dict[str, dict[str, float]]:
    """ticker_atr 테이블에서 bulk 로드. {ticker: {date_ymd: atr_pct}}"""
    if not tickers:
        return {}
    conn = sqlite3.connect(db_path)
    cur  = conn.cursor()
    placeholders = ",".join("?" * len(tickers))
    cur.execute(
        f"SELECT ticker, replace(dt, '-', ''), atr_pct FROM ticker_atr WHERE ticker IN ({placeholders})",
        tickers,
    )
    rows = cur.fetchall()
    conn.close()

    result: dict[str, dict[str, float]] = defaultdict(dict)
    for ticker, dt, atr in rows:
        result[ticker][dt] = float(atr)
    return result


def load_market_strong(db_path: str) -> dict[str, bool]:
    """backtester.build_market_strong_by_date() 재현. {date_ymd: bool}"""
    conn = sqlite3.connect(db_path)
    cur  = conn.cursor()
    cur.execute(
        "SELECT dt, close FROM index_candles WHERE index_code='001' ORDER BY dt"
    )
    rows = cur.fetchall()
    conn.close()

    dates  = [r[0] for r in rows]
    closes = [r[1] for r in rows]
    result: dict[str, bool] = {}
    for i, dt in enumerate(dates):
        if i < 4:
            result[dt] = True
            continue
        ma5 = mean(closes[i-4:i+1])
        result[dt] = closes[i] >= ma5
    return result


# ---------------------------------------------------------------------------
# 분석 섹션
# ---------------------------------------------------------------------------

def _fmt_pct(v: float) -> str:
    return f"{v:+.1f}%"


def section1_daily_pnl(trades: list[dict]) -> str:
    """날짜별 PnL 분포."""
    daily: dict[str, list[float]] = defaultdict(list)
    for t in trades:
        dt = t["entry_ts"].strftime("%Y%m%d")
        daily[dt].append(t["pnl"])

    days_pnl = {dt: sum(pnls) for dt, pnls in daily.items()}
    win_days  = {dt: p for dt, p in days_pnl.items() if p > 0}
    loss_days = {dt: p for dt, p in days_pnl.items() if p < 0}
    zero_days = {dt: p for dt, p in days_pnl.items() if p == 0}

    all_values = list(days_pnl.values())
    total_days = len(days_pnl)

    win_avg  = mean(win_days.values())  if win_days  else 0.0
    loss_avg = mean(loss_days.values()) if loss_days else 0.0

    # PnL 분위
    sorted_vals = sorted(all_values)
    n = len(sorted_vals)
    pct10 = sorted_vals[max(0, int(n * 0.10))]
    pct25 = sorted_vals[max(0, int(n * 0.25))]
    pct50 = sorted_vals[n // 2]
    pct75 = sorted_vals[min(n-1, int(n * 0.75))]
    pct90 = sorted_vals[min(n-1, int(n * 0.90))]

    # 최대 수익일 / 최대 손실일
    best_day  = max(days_pnl, key=days_pnl.get)
    worst_day = min(days_pnl, key=days_pnl.get)

    lines = [
        "## 1. 날짜별 PnL 분포",
        "",
        f"- 백테스트 거래일: {total_days}일 (거래 발생일 기준)",
        f"- 수익일: {len(win_days)}일 ({len(win_days)/total_days*100:.1f}%) — 일평균 +{win_avg:,.0f}원",
        f"- 손실일: {len(loss_days)}일 ({len(loss_days)/total_days*100:.1f}%) — 일평균 {loss_avg:,.0f}원",
        f"- 무거래/무손익일: {len(zero_days)}일",
        "",
        "**일별 PnL 분위수**",
        "",
        "| 분위 | PnL (원) |",
        "|------|---------|",
        f"| P10  | {pct10:+,.0f} |",
        f"| P25  | {pct25:+,.0f} |",
        f"| P50  | {pct50:+,.0f} |",
        f"| P75  | {pct75:+,.0f} |",
        f"| P90  | {pct90:+,.0f} |",
        "",
        f"- 최대 수익일: {best_day} ({days_pnl[best_day]:+,.0f}원)",
        f"- 최대 손실일: {worst_day} ({days_pnl[worst_day]:+,.0f}원)",
    ]

    # 월별 집계
    monthly: dict[str, list[float]] = defaultdict(list)
    for dt, pnl in days_pnl.items():
        ym = dt[:6]
        monthly[ym].append(pnl)

    lines += ["", "**월별 PnL 집계**", "", "| 월 | 거래일 | 월 PnL | 수익일 | 손실일 |", "|---|---|---|---|---|"]
    for ym in sorted(monthly.keys()):
        vals = monthly[ym]
        w = sum(1 for v in vals if v > 0)
        l = sum(1 for v in vals if v < 0)
        lines.append(f"| {ym[:4]}-{ym[4:]} | {len(vals)} | {sum(vals):+,.0f} | {w} | {l} |")

    return "\n".join(lines)


def section2_market_char(
    trades: list[dict],
    index_daily: dict[str, dict[str, dict]],
    market_strong: dict[str, bool],
) -> str:
    """수익일 vs 손실일 시장 특성."""
    kospi  = index_daily.get("001", {})
    kosdaq = index_daily.get("101", {})

    daily: dict[str, list[float]] = defaultdict(list)
    for t in trades:
        dt = t["entry_ts"].strftime("%Y%m%d")
        daily[dt].append(t["pnl"])

    days_pnl = {dt: sum(pnls) for dt, pnls in daily.items()}

    win_kospi_chg: list[float]  = []
    loss_kospi_chg: list[float] = []
    win_kosdaq_chg: list[float]  = []
    loss_kosdaq_chg: list[float] = []
    win_strong: int  = 0
    loss_strong: int = 0

    for dt, day_pnl in days_pnl.items():
        kp_chg = kospi.get(dt, {}).get("chg_pct", None)
        kq_chg = kosdaq.get(dt, {}).get("chg_pct", None)
        strong = market_strong.get(dt, True)

        if day_pnl > 0:
            if kp_chg is not None:
                win_kospi_chg.append(kp_chg)
            if kq_chg is not None:
                win_kosdaq_chg.append(kq_chg)
            if strong:
                win_strong += 1
        elif day_pnl < 0:
            if kp_chg is not None:
                loss_kospi_chg.append(kp_chg)
            if kq_chg is not None:
                loss_kosdaq_chg.append(kq_chg)
            if strong:
                loss_strong += 1

    win_days  = len([p for p in days_pnl.values() if p > 0])
    loss_days = len([p for p in days_pnl.values() if p < 0])

    def _avg(lst: list[float]) -> str:
        return f"{mean(lst):+.2f}%" if lst else "N/A"

    lines = [
        "## 2. 수익일 vs 손실일 시장 특성",
        "",
        "| 항목 | 수익일 | 손실일 |",
        "|------|--------|--------|",
        f"| 일수 | {win_days}일 | {loss_days}일 |",
        f"| KOSPI 평균 등락 | {_avg(win_kospi_chg)} | {_avg(loss_kospi_chg)} |",
        f"| KOSDAQ 평균 등락 | {_avg(win_kosdaq_chg)} | {_avg(loss_kosdaq_chg)} |",
        f"| KOSPI MA5 상회일 | {win_strong}일 ({win_strong/max(win_days,1)*100:.0f}%) | {loss_strong}일 ({loss_strong/max(loss_days,1)*100:.0f}%) |",
        "",
    ]

    # 시장 강도별 거래 성과
    strong_trades  = [t for t in trades if market_strong.get(t["entry_ts"].strftime("%Y%m%d"), True)]
    weak_trades    = [t for t in trades if not market_strong.get(t["entry_ts"].strftime("%Y%m%d"), True)]

    def _wr(tlist: list[dict]) -> str:
        if not tlist:
            return "N/A"
        w = sum(1 for t in tlist if t["pnl"] > 0)
        return f"{w/len(tlist)*100:.1f}%"

    def _pf(tlist: list[dict]) -> str:
        if not tlist:
            return "N/A"
        gw = sum(t["pnl"] for t in tlist if t["pnl"] > 0)
        gl = abs(sum(t["pnl"] for t in tlist if t["pnl"] < 0))
        return f"{gw/gl:.3f}" if gl > 0 else "inf"

    lines += [
        "**시장 강도(KOSPI MA5)별 전략 성과**",
        "",
        "| 구분 | 거래수 | 승률 | PF | 총 PnL |",
        "|------|--------|------|----|--------|",
        f"| 상승장 (MA5 상회) | {len(strong_trades)} | {_wr(strong_trades)} | {_pf(strong_trades)} | {sum(t['pnl'] for t in strong_trades):+,.0f} |",
        f"| 하락장 (MA5 하회) | {len(weak_trades)} | {_wr(weak_trades)} | {_pf(weak_trades)} | {sum(t['pnl'] for t in weak_trades):+,.0f} |",
    ]

    return "\n".join(lines)


def section3_ticker_atr(trades: list[dict], atr_by_ticker: dict[str, dict[str, float]]) -> str:
    """종목 특성 — ATR 분포, 수익/손실 비교."""
    stop_trades     = [t for t in trades if t.get("exit_reason") == "stop_loss"]
    win_trades      = [t for t in trades if t.get("pnl", 0) > 0]
    loss_trades     = [t for t in trades if t.get("pnl", 0) < 0]

    def _get_atr(t: dict) -> float | None:
        tkr = t.get("ticker", "")
        dt  = t["entry_ts"].strftime("%Y%m%d")
        return atr_by_ticker.get(tkr, {}).get(dt)

    # ATR 분포
    stop_atrs = [a for t in stop_trades if (a := _get_atr(t)) is not None]
    win_atrs  = [a for t in win_trades  if (a := _get_atr(t)) is not None]
    loss_atrs = [a for t in loss_trades if (a := _get_atr(t)) is not None]

    def _stats(vals: list[float]) -> str:
        if not vals:
            return "N/A (데이터 없음)"
        return f"평균 {mean(vals):.1f}% / 중앙값 {median(vals):.1f}% / 최소 {min(vals):.1f}% / 최대 {max(vals):.1f}%"

    # exit_reason 분포
    reason_cnt: dict[str, int] = defaultdict(int)
    reason_pnl: dict[str, float] = defaultdict(float)
    for t in trades:
        r = t.get("exit_reason", "unknown")
        reason_cnt[r] += 1
        reason_pnl[r] += t.get("pnl", 0)

    total = len(trades)

    lines = [
        "## 3. 종목 특성 — ATR 분포 및 청산 분석",
        "",
        f"- 전체 거래: {total}건",
        f"- stop_loss: {len(stop_trades)}건 ({len(stop_trades)/total*100:.1f}%)",
        "",
        "**ATR 분포 (보유 ATR 데이터 기준)**",
        "",
        f"- 수익 거래 ATR: {_stats(win_atrs)} ({len(win_atrs)}/{len(win_trades)}건 매칭)",
        f"- 손실 거래 ATR: {_stats(loss_atrs)} ({len(loss_atrs)}/{len(loss_trades)}건 매칭)",
        f"- stop_loss 거래 ATR: {_stats(stop_atrs)} ({len(stop_atrs)}/{len(stop_trades)}건 매칭)",
        "",
        "**청산 경로별 성과**",
        "",
        "| 청산 경로 | 건수 | 비율 | 총 PnL | 건당 PnL |",
        "|-----------|------|------|--------|---------|",
    ]
    for reason in sorted(reason_cnt, key=lambda x: -reason_cnt[x]):
        cnt = reason_cnt[reason]
        pnl = reason_pnl[reason]
        lines.append(
            f"| {reason} | {cnt} | {cnt/total*100:.1f}% | {pnl:+,.0f} | {pnl/cnt:+,.0f} |"
        )

    # 종목별 상위 손실
    ticker_pnl: dict[str, float] = defaultdict(float)
    ticker_cnt: dict[str, int]   = defaultdict(int)
    for t in trades:
        tkr = t.get("ticker", "?")
        ticker_pnl[tkr] += t.get("pnl", 0)
        ticker_cnt[tkr] += 1

    worst_tickers = sorted(ticker_pnl.items(), key=lambda x: x[1])[:10]
    best_tickers  = sorted(ticker_pnl.items(), key=lambda x: -x[1])[:10]

    lines += [
        "",
        "**손실 상위 10종목**",
        "",
        "| 종목 | 거래수 | 누적 PnL |",
        "|------|--------|---------|",
    ]
    for tkr, pnl in worst_tickers:
        lines.append(f"| {tkr} | {ticker_cnt[tkr]} | {pnl:+,.0f} |")

    lines += [
        "",
        "**수익 상위 10종목**",
        "",
        "| 종목 | 거래수 | 누적 PnL |",
        "|------|--------|---------|",
    ]
    for tkr, pnl in best_tickers:
        lines.append(f"| {tkr} | {ticker_cnt[tkr]} | {pnl:+,.0f} |")

    return "\n".join(lines)


def section4_time_analysis(trades: list[dict]) -> str:
    """시간대 분석 — 진입 시간대별 승률, 보유 시간."""
    # 진입 시간대 (1시간 단위)
    hour_bucket: dict[int, list[dict]] = defaultdict(list)
    for t in trades:
        h = t["entry_ts"].hour
        hour_bucket[h].append(t)

    lines = [
        "## 4. 시간대 분석",
        "",
        "**진입 시간대별 성과**",
        "",
        "| 시간대 | 거래수 | 승률 | PF | 평균 PnL |",
        "|--------|--------|------|----|---------|",
    ]

    for h in range(9, 16):
        tlist = hour_bucket.get(h, [])
        if not tlist:
            continue
        w  = sum(1 for t in tlist if t["pnl"] > 0)
        gw = sum(t["pnl"] for t in tlist if t["pnl"] > 0)
        gl = abs(sum(t["pnl"] for t in tlist if t["pnl"] < 0))
        pf = f"{gw/gl:.2f}" if gl > 0 else "inf"
        avg_pnl = mean(t["pnl"] for t in tlist)
        lines.append(
            f"| {h:02d}:00~{h+1:02d}:00 | {len(tlist)} | {w/len(tlist)*100:.1f}% | {pf} | {avg_pnl:+,.0f} |"
        )

    # 30분 단위 (9~12)
    lines += ["", "**오전 진입 30분 단위 성과 (9:00~12:00)**", "", "| 구간 | 거래수 | 승률 | 평균 PnL |", "|------|--------|------|---------|"]
    for h in range(9, 12):
        for half in [0, 30]:
            slot_trades = [
                t for t in trades
                if t["entry_ts"].hour == h and (t["entry_ts"].minute < 30 if half == 0 else t["entry_ts"].minute >= 30)
            ]
            if not slot_trades:
                continue
            w   = sum(1 for t in slot_trades if t["pnl"] > 0)
            avg = mean(t["pnl"] for t in slot_trades)
            end_min = half + 30
            if end_min == 60:
                end_label = f"{h+1:02d}:00"
            else:
                end_label = f"{h:02d}:{end_min:02d}"
            lines.append(
                f"| {h:02d}:{half:02d}~{end_label} | {len(slot_trades)} | {w/len(slot_trades)*100:.1f}% | {avg:+,.0f} |"
            )

    # 보유 시간 분포
    hold_mins_all   = []
    hold_mins_stop  = []
    hold_mins_force = []
    hold_mins_win   = []
    hold_mins_loss  = []

    for t in trades:
        hm = (t["exit_ts"] - t["entry_ts"]).total_seconds() / 60.0
        hold_mins_all.append(hm)
        reason = t.get("exit_reason", "")
        if reason == "stop_loss":
            hold_mins_stop.append(hm)
        elif reason == "forced_close":
            hold_mins_force.append(hm)
        if t["pnl"] > 0:
            hold_mins_win.append(hm)
        else:
            hold_mins_loss.append(hm)

    def _hm_stats(vals: list[float]) -> str:
        if not vals:
            return "N/A"
        return f"평균 {mean(vals):.0f}분 / 중앙값 {median(vals):.0f}분"

    lines += [
        "",
        "**보유 시간 분포**",
        "",
        f"- 전체: {_hm_stats(hold_mins_all)}",
        f"- 수익 거래: {_hm_stats(hold_mins_win)}",
        f"- 손실 거래: {_hm_stats(hold_mins_loss)}",
        f"- stop_loss: {_hm_stats(hold_mins_stop)}",
        f"- forced_close: {_hm_stats(hold_mins_force)}",
    ]

    # stop_loss 보유 시간 분위
    if hold_mins_stop:
        sv = sorted(hold_mins_stop)
        n  = len(sv)
        lines += [
            "",
            "**stop_loss 보유 시간 분위수**",
            "",
            f"- P25: {sv[max(0, int(n*0.25))]:.0f}분",
            f"- P50: {sv[n//2]:.0f}분",
            f"- P75: {sv[min(n-1, int(n*0.75))]:.0f}분",
            f"- P90: {sv[min(n-1, int(n*0.90))]:.0f}분",
        ]

    return "\n".join(lines)


def section5_conclusion(trades: list[dict], market_strong: dict[str, bool]) -> str:
    """결론 및 필터 개선 제안."""
    total = len(trades)
    wins  = sum(1 for t in trades if t["pnl"] > 0)
    wr    = wins / total * 100 if total > 0 else 0
    gw    = sum(t["pnl"] for t in trades if t["pnl"] > 0)
    gl    = abs(sum(t["pnl"] for t in trades if t["pnl"] < 0))
    pf    = gw / gl if gl > 0 else float("inf")
    total_pnl = sum(t["pnl"] for t in trades)

    stop_cnt  = sum(1 for t in trades if t.get("exit_reason") == "stop_loss")
    force_cnt = sum(1 for t in trades if t.get("exit_reason") == "forced_close")

    strong_trades = [t for t in trades if market_strong.get(t["entry_ts"].strftime("%Y%m%d"), True)]
    weak_trades   = [t for t in trades if not market_strong.get(t["entry_ts"].strftime("%Y%m%d"), True)]

    def _pf_for(tlist: list[dict]) -> float:
        gw = sum(t["pnl"] for t in tlist if t["pnl"] > 0)
        gl = abs(sum(t["pnl"] for t in tlist if t["pnl"] < 0))
        return gw / gl if gl > 0 else float("inf")

    sp_pf = _pf_for(strong_trades)
    wp_pf = _pf_for(weak_trades)

    lines = [
        "## 5. 결론 및 필터 개선 제안",
        "",
        "### 현황 요약",
        "",
        f"- 전체: {total}건 / 승률 {wr:.1f}% / PF {pf:.3f} / PnL {total_pnl:+,.0f}원",
        f"- stop_loss 비율: {stop_cnt/total*100:.1f}% ({stop_cnt}건) — 고정 유니버스 11% 대비 과다",
        f"- forced_close 비율: {force_cnt/total*100:.1f}% ({force_cnt}건)",
        "",
        "### 핵심 문제",
        "",
        "1. **승률 43% vs 고정 66%**: 동적 유니버스가 매일 바뀌면서 전략이 불리한 종목에도 진입.",
        "2. **stop_loss 42% 과다**: ATR 필터(≥4%)가 적용됐음에도 손절 비율이 높음.",
        f"3. **시장 강도 차이**: 상승장 PF {sp_pf:.3f} vs 하락장 PF {wp_pf:.3f}.",
        "",
        "### 개선 방향",
        "",
        "| 제안 | 예상 효과 | 우선순위 |",
        "|------|----------|---------|",
        "| 유니버스 ATR 임계값 상향 (4%→6%) | stop_loss 감소, PF 개선 | 높음 |",
        "| 동적 유니버스에도 시장 필터 강화 (MA5 하회 시 top_n 축소) | 하락장 손실 제한 | 높음 |",
        "| 거래대금 기준 상향 (30억→50억) | 유동성 낮은 종목 진입 방지 | 중간 |",
        "| 진입 시간 09:30 이후로 제한 | 시초가 변동성 회피 | 낮음 |",
        "| 종목당 최대 손실 캡 (-2% 이상 손실 종목 당일 재진입 차단) | 연속 손절 방지 | 낮음 |",
        "",
        "> **결론**: 동적 유니버스 PF 0.938은 전략 자체의 문제가 아니라 유니버스 품질 문제.",
        "> 고정 41종목(ATR≥6%)이 기저 선별이 엄격했기 때문에 PF 4.881을 달성함.",
        "> ATR 임계값 재조정 + 하락장 top_n 동적 축소를 우선 검토 권장.",
    ]

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 메인
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="동적 백테스트 거래 분석")
    parser.add_argument("--trades", type=str, default=str(_TRADES_PATH), help="거래 JSON 파일 경로")
    args = parser.parse_args()

    trades_path = Path(args.trades)
    if not trades_path.exists():
        print(f"[ERROR] 거래 파일 없음: {trades_path}")
        print("먼저 실행: python -u scripts/run_dynamic_backtest.py --momentum --end 2026-05-15")
        sys.exit(1)

    config = AppConfig.from_yaml()
    db_path = config.db_path

    print(f"거래 데이터 로드: {trades_path}")
    trades = load_trades(trades_path)
    print(f"  {len(trades)}건 로드 완료")

    print("시장 데이터 로드 중...")
    index_daily    = load_index_daily(db_path)
    market_strong  = load_market_strong(db_path)

    print("ATR 데이터 로드 중...")
    tickers        = list({t.get("ticker", "") for t in trades if t.get("ticker")})
    atr_by_ticker  = load_atr_by_ticker(db_path, tickers)

    print("분석 중...")
    sec1 = section1_daily_pnl(trades)
    sec2 = section2_market_char(trades, index_daily, market_strong)
    sec3 = section3_ticker_atr(trades, atr_by_ticker)
    sec4 = section4_time_analysis(trades)
    sec5 = section5_conclusion(trades, market_strong)

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    content = f"""# 동적 유니버스 백테스트 분석

> 생성: {now_str}
> 데이터: {trades_path.name} ({len(trades)}건)

---

{sec1}

---

{sec2}

---

{sec3}

---

{sec4}

---

{sec5}
"""

    _ANALYSIS_OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(_ANALYSIS_OUT, "w", encoding="utf-8") as f:
        f.write(content)

    print(f"\n분석 완료: {_ANALYSIS_OUT}")


if __name__ == "__main__":
    main()

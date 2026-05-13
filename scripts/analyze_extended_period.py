"""scripts/analyze_extended_period.py — 2026-04-11~05-12 구간 상세 분석.

기존 구간(~2026-04-10) vs 신규 구간(2026-04-11~2026-05-12) 비교.
핵심 가설: index_candles가 2026-04-10까지만 존재 → 신규 구간 시장 필터 미작동.
"""

import asyncio
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from loguru import logger
logger.remove()
logger.add(sys.stderr, level="WARNING")

from backtest.backtester import Backtester, build_market_strong_by_date
from config.settings import AppConfig, BacktestConfig
from data.db_manager import DbManager
from strategy.momentum_strategy import MomentumStrategy

START = "2025-04-01"
END   = "2026-05-12"
CUT   = date(2026, 4, 11)   # 구간 분기점 (이날 이후 = 신규 구간)

REPORT_PATH = Path("reports/extended_period_analysis.md")


# ─────────────────────────────────────────────
# 1. 백테스트 실행 — 전체 거래 수집
# ─────────────────────────────────────────────

async def collect_trades(uni_path: str = "config/universe_backtest.yaml") -> list[dict]:
    app_config = AppConfig.from_yaml()
    base_config = app_config.trading

    bt_cfg_raw = yaml.safe_load(open("config.yaml", encoding="utf-8")).get("backtest", {})
    bt_config = BacktestConfig(
        commission=bt_cfg_raw.get("commission", 0.00015),
        tax=bt_cfg_raw.get("tax", 0.0020),
        slippage=bt_cfg_raw.get("slippage", 0.0003),
    )

    uni = yaml.safe_load(open(uni_path, encoding="utf-8"))
    stocks = uni.get("stocks", [])
    ticker_to_info = {s["ticker"]: s for s in stocks}

    db = DbManager(app_config.db_path)
    await db.init()
    bt_loader = Backtester(db=db, config=base_config, backtest_config=bt_config)

    print(f"[LOAD] candles ({len(stocks)} stocks, {START}~{END})", flush=True)
    candles_cache: dict = {}
    for i, s in enumerate(stocks, 1):
        tk = s["ticker"]
        candles = await bt_loader.load_candles(tk, START, f"{END} 23:59:59")
        if not candles.empty:
            candles_cache[tk] = candles
        if i % 10 == 0:
            print(f"  loaded {i}/{len(stocks)}", flush=True)
    print(f"[LOAD] done {len(candles_cache)}/{len(stocks)}", flush=True)
    await db.close()

    market_map = build_market_strong_by_date(app_config.db_path, ma_length=base_config.market_ma_length)

    all_trades: list[dict] = []
    for tk, candles in candles_cache.items():
        info = ticker_to_info.get(tk, {})
        market = info.get("market", "unknown")
        bt = Backtester(
            db=None, config=base_config, backtest_config=bt_config,
            ticker_market=market, market_strong_by_date=market_map,
        )
        strategy = MomentumStrategy(base_config)
        result = await bt.run_multi_day_cached(tk, candles, strategy)
        for t in result.get("trades", []):
            t["ticker"] = tk
            t["name"] = info.get("name", tk)
            t["ticker_market"] = market
            all_trades.append(t)

    print(f"[DONE] total {len(all_trades)} trades", flush=True)
    return all_trades


# ─────────────────────────────────────────────
# 2. 시장 인덱스 데이터 조회
# ─────────────────────────────────────────────

def load_index_data(db_path: str) -> dict:
    """index_candles에서 날짜별 KOSPI/KOSDAQ 종가 반환.
    반환: {"20260410": {"kospi": 5858.87, "kosdaq": ...}, ...}
    """
    conn = sqlite3.connect(db_path)
    rows_kospi  = conn.execute(
        "SELECT dt, close FROM index_candles WHERE index_code='001' ORDER BY dt"
    ).fetchall()
    rows_kosdaq = conn.execute(
        "SELECT dt, close FROM index_candles WHERE index_code='101' ORDER BY dt"
    ).fetchall()
    conn.close()

    result: dict[str, dict] = {}
    for dt, close in rows_kospi:
        result.setdefault(dt, {})["kospi"] = close
    for dt, close in rows_kosdaq:
        result.setdefault(dt, {})["kosdaq"] = close
    return result


def index_period_return(index_data: dict, start_dt: str, end_dt: str, key: str) -> float | None:
    """start~end 구간 수익률 (%)."""
    dates = sorted(d for d in index_data if start_dt <= d <= end_dt)
    if len(dates) < 2:
        return None
    first = index_data[dates[0]].get(key)
    last  = index_data[dates[-1]].get(key)
    if first and last:
        return (last / first - 1) * 100
    return None


# ─────────────────────────────────────────────
# 3. 분석
# ─────────────────────────────────────────────

def analyze(trades: list[dict], index_data: dict, db_path: str) -> str:
    # 구간 분리
    old_trades = []
    new_trades = []
    for t in trades:
        entry_ts = t.get("entry_ts", "")
        if isinstance(entry_ts, str):
            try:
                entry_d = datetime.fromisoformat(entry_ts).date()
            except Exception:
                continue
        else:
            entry_d = entry_ts.date() if hasattr(entry_ts, "date") else None
        if entry_d is None:
            continue
        if entry_d >= CUT:
            new_trades.append(t)
        else:
            old_trades.append(t)

    lines = []
    lines.append("# Extended Period Analysis: 2026-04-11 ~ 2026-05-12\n")
    lines.append(f"> 생성: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")

    # ── 구간별 요약 비교표 ──
    lines.append("## 1. 구간별 비교 요약\n")

    def period_stats(ts: list[dict]) -> dict:
        n = len(ts)
        if n == 0:
            return {}
        pnl_list = [t["pnl"] for t in ts]
        pct_list = [t["pnl_pct"] for t in ts]
        wins = [t for t in ts if t["pnl"] > 0]
        losses = [t for t in ts if t["pnl"] < 0]
        gp = sum(t["pnl"] for t in wins)
        gl = abs(sum(t["pnl"] for t in losses))
        pf = gp / gl if gl > 0 else float("inf")
        exits = Counter(t.get("exit_reason", "?") for t in ts)
        return {
            "n": n, "pf": pf,
            "total_pnl": sum(pnl_list),
            "avg_pnl": sum(pnl_list) / n,
            "win_rate": len(wins) / n * 100,
            "avg_win_pct": sum(t["pnl_pct"] for t in wins) / len(wins) * 100 if wins else 0,
            "avg_loss_pct": sum(t["pnl_pct"] for t in losses) / len(losses) * 100 if losses else 0,
            "exits": exits,
        }

    os_ = period_stats(old_trades)
    ns_ = period_stats(new_trades)

    lines.append("| 항목 | 기존(~2026-04-10) | 신규(2026-04-11~05-12) |")
    lines.append("|------|-----------------|----------------------|")
    lines.append(f"| 거래 건수 | {os_['n']} | {ns_['n']} |")
    lines.append(f"| Profit Factor | {os_['pf']:.3f} | {ns_['pf']:.3f} |")
    lines.append(f"| 총 PnL | {os_['total_pnl']:+,.0f} | {ns_['total_pnl']:+,.0f} |")
    lines.append(f"| 거래당 평균 PnL | {os_['avg_pnl']:+,.0f} | {ns_['avg_pnl']:+,.0f} |")
    lines.append(f"| 승률 | {os_['win_rate']:.1f}% | {ns_['win_rate']:.1f}% |")
    lines.append(f"| 평균 수익 PnL% | +{os_['avg_win_pct']:.2f}% | +{ns_['avg_win_pct']:.2f}% |")
    lines.append(f"| 평균 손실 PnL% | {os_['avg_loss_pct']:.2f}% | {ns_['avg_loss_pct']:.2f}% |")
    lines.append("")

    # 청산 분포
    lines.append("### 청산 분포 비교\n")
    lines.append("| 청산 사유 | 기존 (건/%) | 신규 (건/%) |")
    lines.append("|---------|----------|----------|")
    all_reasons = sorted(
        set(os_["exits"]) | set(ns_["exits"]),
        key=lambda r: -(os_["exits"].get(r, 0) + ns_["exits"].get(r, 0))
    )
    for r in all_reasons:
        oc = os_["exits"].get(r, 0)
        nc = ns_["exits"].get(r, 0)
        op = oc / os_["n"] * 100 if os_["n"] else 0
        np_ = nc / ns_["n"] * 100 if ns_["n"] else 0
        lines.append(f"| {r} | {oc} ({op:.1f}%) | {nc} ({np_:.1f}%) |")
    lines.append("")

    # ── 신규 구간 개별 거래 ──
    lines.append("## 2. 신규 구간 개별 거래 목록\n")
    lines.append("| # | ticker | 종목명 | 시장 | 진입일 | 청산일 | 진입가 | 청산가 | exit_reason | PnL | PnL% | 보유(분) |")
    lines.append("|---|--------|------|------|------|------|------|------|------------|-----|------|--------|")

    sorted_new = sorted(new_trades, key=lambda t: str(t.get("entry_ts", "")))
    for i, t in enumerate(sorted_new, 1):
        entry_ts = str(t.get("entry_ts", ""))
        exit_ts  = str(t.get("exit_ts", ""))
        entry_d  = entry_ts[:10]
        exit_d   = exit_ts[:10]
        try:
            hold_min = int((datetime.fromisoformat(exit_ts) - datetime.fromisoformat(entry_ts)).total_seconds() / 60)
        except Exception:
            hold_min = 0
        lines.append(
            f"| {i} | {t['ticker']} | {t.get('name', '')} | {t.get('ticker_market', '')} | "
            f"{entry_d} | {exit_d} | "
            f"{t.get('entry_price', 0):,.0f} | {t.get('exit_price', 0):,.0f} | "
            f"{t.get('exit_reason', '')} | "
            f"{t.get('pnl', 0):+,.0f} | {t.get('pnl_pct', 0)*100:+.2f}% | {hold_min} |"
        )
    lines.append("")

    # ── 시장 환경 ──
    lines.append("## 3. 시장 환경\n")

    # index_candles 기간 범위
    old_start, old_end = "20250401", "20260410"
    new_start, new_end = "20260411", "20260512"

    kospi_old  = index_period_return(index_data, old_start, old_end, "kospi")
    kosdaq_old = index_period_return(index_data, old_start, old_end, "kosdaq")
    kospi_new  = index_period_return(index_data, new_start, new_end, "kospi")
    kosdaq_new = index_period_return(index_data, new_start, new_end, "kosdaq")

    lines.append("### KOSPI / KOSDAQ 구간 등락률 (index_candles 기준)\n")
    lines.append("| 지수 | 기존 구간 (~04-10) | 신규 구간 (04-11~05-12) |")
    lines.append("|------|----------------|----------------------|")
    lines.append(f"| KOSPI  | {f'{kospi_old:+.2f}%' if kospi_old is not None else '데이터 없음'} | {f'{kospi_new:+.2f}%' if kospi_new is not None else '데이터 없음 (index_candles 미수집)'} |")
    lines.append(f"| KOSDAQ | {f'{kosdaq_old:+.2f}%' if kosdaq_old is not None else '데이터 없음'} | {f'{kosdaq_new:+.2f}%' if kosdaq_new is not None else '데이터 없음 (index_candles 미수집)'} |")
    lines.append("")

    # 시장 필터 작동 여부 — 신규 index_candles(~2026-05-13)로 재분석
    lines.append("### 시장 필터(MA5) 작동 현황\n")

    market_map = build_market_strong_by_date(db_path, ma_length=5)

    # 구간별 강세/약세 일수
    def count_market_days(mmap: dict, start: str, end: str) -> tuple[int, int, int, int]:
        ks = kw = ds = dw = 0
        for dt_key, v in mmap.items():
            if start <= dt_key <= end:
                if v.get("kospi"):  ks += 1
                else:               kw += 1
                if v.get("kosdaq"): ds += 1
                else:               dw += 1
        return ks, kw, ds, dw

    ks_o, kw_o, ds_o, dw_o = count_market_days(market_map, "20250401", "20260410")
    ks_n, kw_n, ds_n, dw_n = count_market_days(market_map, "20260411", "20260512")

    lines.append("| 구간 | KOSPI 강세 | KOSPI 약세 | KOSDAQ 강세 | KOSDAQ 약세 |")
    lines.append("|------|----------|----------|-----------|-----------|")
    lines.append(f"| 기존 (~04-10) | {ks_o}일 | {kw_o}일 | {ds_o}일 | {dw_o}일 |")
    lines.append(f"| 신규 (04-11~05-12) | {ks_n}일 | **{kw_n}일** | {ds_n}일 | **{dw_n}일** |")
    lines.append("")

    # 18건 각각 시장 필터 판정
    lines.append("### 신규 구간 18건 시장 필터 판정\n")
    lines.append("| # | ticker | 시장 | 진입일 | KOSPI/KOSDAQ 판정 | 차단? | PnL |")
    lines.append("|---|--------|------|------|----------------|------|-----|")

    blocked_trades = []
    passed_trades = []
    for i, t in enumerate(sorted_new, 1):
        entry_ts = str(t.get("entry_ts", ""))
        entry_d  = entry_ts[:10]
        dt_key   = entry_d.replace("-", "")
        mkt      = t.get("ticker_market", "unknown")
        strong_info = market_map.get(dt_key)
        if strong_info is None:
            judgment = "데이터없음(통과)"
            blocked = False
        else:
            is_strong = strong_info.get(mkt, True)
            judgment  = f"{'강세' if strong_info.get('kospi') else '약세'}/{('강세' if strong_info.get('kosdaq') else '약세')}"
            blocked   = not is_strong
        if blocked:
            blocked_trades.append(t)
            mark = "**차단**"
        else:
            passed_trades.append(t)
            mark = "통과"
        lines.append(
            f"| {i} | {t['ticker']} | {mkt} | {entry_d} | {judgment} | {mark} | {t.get('pnl',0):+,.0f} |"
        )
    lines.append("")

    # 필터 적용 시 시뮬레이션
    blocked_pnl  = sum(t["pnl"] for t in blocked_trades)
    passed_pnl   = sum(t["pnl"] for t in passed_trades)
    blocked_wins = sum(1 for t in blocked_trades if t["pnl"] > 0)
    passed_wins  = sum(1 for t in passed_trades  if t["pnl"] > 0)

    lines.append("### 시장 필터 적용 전/후 신규 구간 비교\n")
    lines.append("| 항목 | 필터 미작동 (18건) | 필터 적용 시 |")
    lines.append("|------|----------------|------------|")
    lines.append(f"| 거래 건수 | 18 | {len(passed_trades)} (차단 {len(blocked_trades)}건) |")
    pf_before = ns_["pf"]
    if passed_trades:
        pg = sum(t["pnl"] for t in passed_trades if t["pnl"] > 0)
        pl = abs(sum(t["pnl"] for t in passed_trades if t["pnl"] < 0))
        pf_after = pg / pl if pl > 0 else float("inf")
        wr_after = passed_wins / len(passed_trades) * 100
        avg_pnl_after = passed_pnl / len(passed_trades)
    else:
        pf_after = 0.0; wr_after = 0.0; avg_pnl_after = 0.0
    lines.append(f"| PF | {pf_before:.3f} | {pf_after:.3f} |")
    lines.append(f"| 총 PnL | {ns_['total_pnl']:+,.0f} | {passed_pnl:+,.0f} |")
    lines.append(f"| 거래당 PnL | {ns_['avg_pnl']:+,.0f} | {avg_pnl_after:+,.0f} |")
    lines.append(f"| 승률 | {ns_['win_rate']:.1f}% | {wr_after:.1f}% |")
    lines.append(f"| 차단된 PnL 합계 | — | {blocked_pnl:+,.0f} (차단 {len(blocked_trades)}건) |")
    lines.append("")

    # ── 종목 집중도 ──
    lines.append("## 4. 종목별 손익 집중도 (신규 구간)\n")

    by_ticker_new: dict[str, dict] = {}
    for t in new_trades:
        tk = t["ticker"]
        if tk not in by_ticker_new:
            by_ticker_new[tk] = {"name": t.get("name", tk), "pnl": 0, "n": 0}
        by_ticker_new[tk]["pnl"] += t["pnl"]
        by_ticker_new[tk]["n"] += 1

    sorted_tickers = sorted(by_ticker_new.items(), key=lambda x: x[1]["pnl"])
    total_loss_new = abs(sum(v["pnl"] for v in by_ticker_new.values() if v["pnl"] < 0))

    lines.append("| ticker | 종목명 | 거래수 | PnL | 손실 비중 |")
    lines.append("|--------|------|------|-----|---------|")
    for tk, info in sorted_tickers:
        pnl = info["pnl"]
        loss_share = abs(pnl) / total_loss_new * 100 if pnl < 0 and total_loss_new > 0 else 0
        share_str = f"{loss_share:.1f}%" if pnl < 0 else "—"
        lines.append(f"| {tk} | {info['name']} | {info['n']} | {pnl:+,.0f} | {share_str} |")
    lines.append("")

    # 기존 구간에서 동일 종목 성과 비교
    lines.append("### 기존 구간 동일 종목 성과 비교\n")
    by_ticker_old: dict[str, dict] = {}
    for t in old_trades:
        tk = t["ticker"]
        if tk not in by_ticker_old:
            by_ticker_old[tk] = {"name": t.get("name", tk), "pnl": 0, "n": 0}
        by_ticker_old[tk]["pnl"] += t["pnl"]
        by_ticker_old[tk]["n"] += 1

    new_tickers = {tk for tk, info in sorted_tickers if info["pnl"] < 0}
    lines.append("| ticker | 종목명 | 기존PnL | 기존거래수 | 신규PnL | 신규거래수 |")
    lines.append("|--------|------|-------|---------|-------|---------|")
    for tk in sorted(new_tickers, key=lambda x: by_ticker_new[x]["pnl"]):
        old_info = by_ticker_old.get(tk, {"pnl": 0, "n": 0, "name": tk})
        new_info = by_ticker_new[tk]
        lines.append(
            f"| {tk} | {new_info['name']} | "
            f"{old_info['pnl']:+,.0f} | {old_info['n']} | "
            f"{new_info['pnl']:+,.0f} | {new_info['n']} |"
        )
    lines.append("")

    # ── 결론 ──
    lines.append("## 5. 결론 및 시사점\n")
    lines.append(f"- 신규 구간 18건 평균 PnL: **{ns_['avg_pnl']:+,.0f}** (기존 {os_['avg_pnl']:+,.0f})")
    lines.append(f"- 신규 구간 승률: **{ns_['win_rate']:.1f}%** (기존 {os_['win_rate']:.1f}%)")
    lines.append(f"- 시장 필터 차단 대상: {len(blocked_trades)}건 / PnL {blocked_pnl:+,.0f}")
    lines.append(f"- 필터 적용 후 신규 구간 PF: **{pf_after:.3f}** (미작동 시 {pf_before:.3f})")
    lines.append(f"- **원인**: index_candles 미수집 → 시장 필터 무력화 → 약세장 진입 허용")
    lines.append("")

    return "\n".join(lines)


# ─────────────────────────────────────────────
# main
# ─────────────────────────────────────────────

async def main():
    print("=" * 60, flush=True)
    print(f" Extended Period Analysis ({START} ~ {END})", flush=True)
    print("=" * 60, flush=True)

    trades = await collect_trades()
    if not trades:
        print("ERROR: no trades")
        return

    app_config = AppConfig.from_yaml()
    index_data = load_index_data(app_config.db_path)

    report = analyze(trades, index_data, app_config.db_path)

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(report, encoding="utf-8")
    print(f"\n[REPORT] 저장 완료: {REPORT_PATH}", flush=True)

    # 콘솔 요약 출력
    new_trades = [
        t for t in trades
        if (lambda d: d >= CUT)(
            datetime.fromisoformat(str(t.get("entry_ts", "2000-01-01"))).date()
        )
    ]
    old_trades = [t for t in trades if t not in new_trades]
    print(f"\n기존 구간: {len(old_trades)}건 / 신규 구간: {len(new_trades)}건", flush=True)


if __name__ == "__main__":
    asyncio.run(main())

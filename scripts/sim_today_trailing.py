"""scripts/sim_today_trailing.py — 오늘 거래 3건의 ATR trailing 시뮬레이션.

수정된 trailing(min 2% 클램프 + ATR 기반)을 가정해 5/7 거래가 어떤 결과였을지
백테스트 backtester.py와 동일한 로직으로 재현한다.

분봉 데이터: 키움 REST API에서 즉석으로 가져옴 (DB 미수집 상태).
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger

sys.path.insert(0, str(Path(__file__).parent.parent))
load_dotenv()
logger.remove()  # 로그 폭주 차단

from config.settings import KiwoomConfig
from core.auth import TokenManager
from core.kiwoom_rest import KiwoomRestClient


def parse_ts(raw: str) -> str | None:
    if not raw or len(raw) < 14:
        return None
    return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]} {raw[8:10]}:{raw[10:12]}:{raw[12:14]}"


def parse_candles_for_date(items: list[dict], target_date: str) -> list[dict]:
    """5/7 분봉만 추출해 시각순 정렬."""
    rows = []
    for it in items:
        ts = parse_ts(it.get("cntr_tm", ""))
        if not ts or not ts.startswith(target_date):
            continue
        rows.append({
            "ts": ts,
            "open": abs(float(it.get("open_pric") or 0)),
            "high": abs(float(it.get("high_pric") or 0)),
            "low": abs(float(it.get("low_pric") or 0)),
            "close": abs(float(it.get("cur_prc") or 0)),
            "volume": abs(int(it.get("trde_qty") or 0)),
        })
    rows.sort(key=lambda r: r["ts"])
    return rows


async def fetch_today_candles(rest: KiwoomRestClient, ticker: str, target_date: str) -> list[dict]:
    """1분봉 1페이지(최신 ~900봉) 조회 → target_date만 필터."""
    data = await rest.get_minute_ohlcv(ticker, tic_scope=1, base_dt="")
    items = data.get("stk_min_pole_chart_qry") or data.get("output2") or []
    return parse_candles_for_date(items, target_date)


def simulate_trailing(
    candles: list[dict],
    entry_ts: str,
    entry_price: float,
    atr_pct: float,
    *,
    min_pct: float = 0.02,
    max_pct: float = 0.10,
    multiplier: float = 1.0,
    stop_loss_pct: float = -0.08,
    be_trigger: float = 0.03,
    be_offset: float = 0.01,
    fc_time: str = "15:10",
) -> dict:
    """단일 거래의 trailing 시뮬.

    Returns: {exit_ts, exit_price, exit_reason, peak_price, peak_return,
              be3_activated, pnl_pct}
    """
    trail_pct = max(min_pct, min(max_pct, atr_pct * multiplier))
    initial_stop = entry_price * (1 + stop_loss_pct)
    stop = initial_stop
    peak = entry_price
    be3_activated = False
    fc_dt = entry_ts.split(" ")[0] + " " + fc_time + ":00"

    # 진입 직후 1분봉부터 (entry_ts 이후, 같은 분봉은 제외)
    for c in candles:
        if c["ts"] <= entry_ts:
            continue
        # forced_close 시각 도달
        if c["ts"] >= fc_dt:
            return {
                "exit_ts": c["ts"],
                "exit_price": c["close"],
                "exit_reason": "forced_close",
                "peak_price": peak,
                "peak_return": (peak / entry_price - 1),
                "be3_activated": be3_activated,
                "pnl_pct": (c["close"] / entry_price - 1),
                "trail_pct_used": trail_pct,
                "final_stop": stop,
            }

        # 이번 분봉 안에서 high가 먼저 닿는지, low가 stop에 닿는지 순서가 모호하므로
        # 보수적으로 stop 체크를 먼저 (백테스트 backtester.py와 동일)
        if c["low"] <= stop:
            # 청산
            be_floor = entry_price * (1 + be_offset)
            if be3_activated and abs(stop - be_floor) < 1e-6:
                reason = "breakeven_stop"
            elif stop > entry_price:
                reason = "trailing_stop"
            else:
                reason = "stop_loss"
            return {
                "exit_ts": c["ts"],
                "exit_price": stop,
                "exit_reason": reason,
                "peak_price": peak,
                "peak_return": (peak / entry_price - 1),
                "be3_activated": be3_activated,
                "pnl_pct": (stop / entry_price - 1),
                "trail_pct_used": trail_pct,
                "final_stop": stop,
            }

        # 고점 갱신 → trailing stop 상향
        if c["high"] > peak:
            peak = c["high"]
            new_stop = peak * (1 - trail_pct)
            stop = max(stop, new_stop)

        # BE3 발동
        if not be3_activated and (peak - entry_price) / entry_price >= be_trigger:
            be_stop = entry_price * (1 + be_offset)
            stop = max(stop, be_stop)
            be3_activated = True

    # 분봉 다 소진했는데 청산 미발생 → 마지막 close로 forced_close
    if candles:
        last = candles[-1]
        return {
            "exit_ts": last["ts"],
            "exit_price": last["close"],
            "exit_reason": "forced_close",
            "peak_price": peak,
            "peak_return": (peak / entry_price - 1),
            "be3_activated": be3_activated,
            "pnl_pct": (last["close"] / entry_price - 1),
            "trail_pct_used": trail_pct,
            "final_stop": stop,
        }
    return {"error": "no candles"}


async def main(target_date: str) -> int:
    kw_cfg = KiwoomConfig(
        app_key=os.environ["KIWOOM_APP_KEY"],
        secret_key=os.environ["KIWOOM_SECRET_KEY"],
        account_no=os.environ["KIWOOM_ACCOUNT_NO"],
    )
    token_mgr = TokenManager(
        app_key=kw_cfg.app_key,
        secret_key=kw_cfg.secret_key,
        base_url=kw_cfg.rest_base_url,
    )
    rest = KiwoomRestClient(kw_cfg, token_mgr)

    print(f"== {target_date} 분봉 수집 중 ==")
    candles_028050 = await fetch_today_candles(rest, "028050", target_date)
    print(f"  028050: {len(candles_028050)}봉 (시간 범위: {candles_028050[0]['ts']} ~ {candles_028050[-1]['ts']})")
    candles_006360 = await fetch_today_candles(rest, "006360", target_date)
    print(f"  006360: {len(candles_006360)}봉 (시간 범위: {candles_006360[0]['ts']} ~ {candles_006360[-1]['ts']})")

    await rest.aclose()

    trades = [
        ("028050", "삼성E&A", "2026-05-07 09:24:00", 62119, 0.059, candles_028050),
        ("028050", "삼성E&A 2회차", "2026-05-07 11:25:00", 64719, 0.059, candles_028050),
        ("006360", "GS건설", "2026-05-07 11:54:00", 38862, 0.075, candles_006360),
    ]

    actual = {
        "028050_1": ("09:24:19", 62100, "trailing_stop(0.5% 폴백)", 0.0),
        "028050_2": ("11:27:22", 64800, "trailing_stop(0.5% 폴백)", +0.001),
        "006360_3": ("12:04:18", 38800, "trailing_stop(0.5% 폴백)", -0.0016),
    }

    print("\n" + "=" * 80)
    print(" ATR trailing 시뮬레이션 결과")
    print("=" * 80)

    for i, (ticker, name, entry_ts, entry, atr, candles) in enumerate(trades, 1):
        print(f"\n[{i}] {ticker} ({name})  entry={entry:,} @ {entry_ts}")
        print(f"    ATR%={atr:.1%}, trail={max(0.02, min(0.10, atr)):.1%}, BE3 trigger=+3%, stop_loss=-8%")
        result = simulate_trailing(candles, entry_ts, entry, atr)
        if "error" in result:
            print(f"  ERROR: {result['error']}")
            continue

        print(f"  → 청산 시점: {result['exit_ts']}")
        print(f"  → 청산 가격: {result['exit_price']:,.0f}")
        print(f"  → 청산 사유: {result['exit_reason']}")
        print(f"  → peak: {result['peak_price']:,.0f} ({result['peak_return']:+.2%})")
        print(f"  → BE3 발동: {result['be3_activated']}")
        print(f"  → 최종 stop: {result['final_stop']:,.0f}")
        print(f"  → 시뮬 PnL%: {result['pnl_pct']:+.2%}")

        actual_key = f"{ticker}_{i}" if ticker == "028050" else f"{ticker}_{i}"
        if actual_key in actual:
            a_ts, a_px, a_reason, a_pnl = actual[actual_key]
            print(f"  실제 결과: {a_ts} @ {a_px:,}, {a_reason}, PnL%={a_pnl:+.2%}")
            sim_pnl_won = (result['exit_price'] - entry) * (1_000_000 // entry)
            print(f"  Δ(시뮬 - 실제) PnL%: {result['pnl_pct'] - a_pnl:+.2%}")

    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default="2026-05-07")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.date)))

"""scripts/generate_universe.py — 코스닥+코스피 유니버스 자동 생성.

KRX Open API로 코스닥/코스피 시가총액 상위 종목을 조회하고,
단타 적합성 필터(거래대금, ATR)를 적용하여 universe.yaml을 생성한다.

사용법:
    python scripts/generate_universe.py
    python scripts/generate_universe.py --min-amount 30 --dry-run
    python scripts/generate_universe.py --max-total 80

필요 패키지: pyyaml, numpy, requests
환경변수: KRX_API_KEY (KRX Open API 인증키)
"""

import argparse
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import requests
from dotenv import load_dotenv

# .env 로드
load_dotenv(Path(__file__).parent.parent / ".env")

# KRX Open API 설정
KRX_BASE_URL = "https://data-dbg.krx.co.kr/svc/apis"
KRX_ENDPOINTS = {
    "kospi_stocks": "/sto/stk_bydd_trd",
    "kosdaq_stocks": "/sto/ksq_bydd_trd",
}

# 제외 키워드 (관리종목, 스팩, 우선주 등)
EXCLUDE_KEYWORDS = ["스팩", "SPAC", "리츠", "우B", "우C", "1우", "2우"]

OUTPUT_PATH = Path(__file__).parent.parent / "config" / "universe.yaml"


class KrxAPI:
    """KRX Open API 간이 클라이언트."""

    def __init__(self, api_key: str):
        self._session = requests.Session()
        self._session.headers.update({
            "AUTH_KEY": api_key,
            "Content-Type": "application/json",
        })
        self._last_call = 0.0

    def _request(self, endpoint: str, params: dict) -> list[dict]:
        elapsed = time.monotonic() - self._last_call
        if elapsed < 0.5:
            time.sleep(0.5 - elapsed)

        url = KRX_BASE_URL + endpoint
        resp = self._session.get(url, params=params, timeout=30)
        self._last_call = time.monotonic()

        if resp.status_code == 401:
            raise PermissionError("KRX API 인증 실패 (401) — API 키 확인 필요")
        resp.raise_for_status()

        data = resp.json()
        return data.get("OutBlock_1", [])

    def get_stocks(self, date: str, market: str = "kosdaq") -> list[dict]:
        """전종목 OHLCV + 시총 조회."""
        endpoint = KRX_ENDPOINTS.get(f"{market}_stocks")
        if not endpoint:
            raise ValueError(f"지원하지 않는 시장: {market}")
        return self._request(endpoint, {"basDd": date})


def get_recent_trading_date(api: KrxAPI) -> str:
    """최근 거래일을 YYYYMMDD 형식으로 반환."""
    today = datetime.now()
    for delta in range(0, 10):
        d = today - timedelta(days=delta)
        if d.weekday() >= 5:
            continue
        date_str = d.strftime("%Y%m%d")
        try:
            records = api.get_stocks(date_str, "kosdaq")
            if records:
                return date_str
        except Exception:
            continue
    return today.strftime("%Y%m%d")


def calc_atr_pct(daily_data: list[dict], period: int = 14) -> float:
    """여러 날의 OHLCV로 ATR(14)/종가 비율 계산."""
    if len(daily_data) < period + 1:
        return 0.0

    high = np.array([d["high"] for d in daily_data])
    low = np.array([d["low"] for d in daily_data])
    close = np.array([d["close"] for d in daily_data])

    tr = np.maximum(
        high[1:] - low[1:],
        np.maximum(
            np.abs(high[1:] - close[:-1]),
            np.abs(low[1:] - close[:-1]),
        ),
    )
    if len(tr) < period:
        return 0.0

    atr = np.mean(tr[-period:])
    last_close = close[-1]
    return atr / last_close if last_close > 0 else 0.0


def collect_daily_ohlcv(
    api: KrxAPI, date: str, days: int = 25,
) -> dict[str, list[dict]]:
    """최근 N거래일의 코스닥+코스피 전종목 OHLCV를 수집.

    Returns:
        {ticker: [{"high": ..., "low": ..., "close": ...}, ...]} 오래된순
    """
    end = datetime.strptime(date, "%Y%m%d")
    stock_data: dict[str, list[dict]] = {}
    collected = 0

    print(f"  ATR 계산용 과거 데이터 수집 중 ({days}거래일)...")
    for delta in range(60, -1, -1):
        d = end - timedelta(days=delta)
        if d.weekday() >= 5:
            continue
        d_str = d.strftime("%Y%m%d")
        try:
            # 코스닥 + 코스피 모두 수집
            kosdaq = api.get_stocks(d_str, "kosdaq")
            kospi = api.get_stocks(d_str, "kospi")
            records = (kosdaq or []) + (kospi or [])
            if not records:
                continue
            collected += 1
            for r in records:
                ticker = r.get("ISU_SRT_CD") or r.get("ISU_CD", "")
                if not ticker:
                    continue
                high = int(str(r.get("TDD_HGPRC", "0")).replace(",", "") or "0")
                low = int(str(r.get("TDD_LWPRC", "0")).replace(",", "") or "0")
                close = int(str(r.get("TDD_CLSPRC", "0")).replace(",", "") or "0")
                if close <= 0:
                    continue
                stock_data.setdefault(ticker, []).append({
                    "high": high, "low": low, "close": close,
                })
            print(f"    [{collected}/{days}] {d_str} - {len(records)}종목")
            if collected >= days:
                break
        except Exception as e:
            print(f"    {d_str} 실패: {e}")
            continue

    return stock_data


def generate_universe(
    top_kosdaq: int = 150,
    top_kospi: int = 200,
    min_amount_billion: float = 50.0,
    min_atr_pct: float = 0.02,
    max_total: int = 60,
    max_stocks: int = 40,
    dry_run: bool = False,
) -> list[dict]:
    """코스닥+코스피에서 단타 유니버스를 생성한다."""

    api_key = os.getenv("KRX_API_KEY", "")
    if not api_key:
        print("  [ERROR] KRX_API_KEY 환경변수가 설정되지 않았습니다.")
        print("     .env 파일에 KRX_API_KEY=... 를 추가하세요.")
        sys.exit(1)

    api = KrxAPI(api_key)

    print("=" * 70)
    print("  코스닥+코스피 유니버스 생성 (KRX Open API)")
    print("=" * 70)

    # 1. 최근 거래일 확인
    date = get_recent_trading_date(api)
    print(f"\n  기준일: {date}")

    # 2. 코스닥 + 코스피 전종목 조회
    print("  코스닥 전종목 조회 중...")
    kosdaq_records = api.get_stocks(date, "kosdaq") or []
    print("  코스피 전종목 조회 중...")
    kospi_records = api.get_stocks(date, "kospi") or []

    # 시가총액/거래대금 파싱
    for r in kosdaq_records + kospi_records:
        r["_mcap"] = int(str(r.get("MKTCAP", "0")).replace(",", "") or "0")
        r["_amount"] = int(str(r.get("ACC_TRDVAL", "0")).replace(",", "") or "0")

    # 시가총액 상위 필터
    kosdaq_records.sort(key=lambda x: x["_mcap"], reverse=True)
    kospi_records.sort(key=lambda x: x["_mcap"], reverse=True)
    kosdaq_top = kosdaq_records[:top_kosdaq]
    kospi_top = kospi_records[:top_kospi]

    print(f"  코스닥: {len(kosdaq_records)}종목 → 시총 상위 {len(kosdaq_top)}종목")
    print(f"  코스피: {len(kospi_records)}종목 → 시총 상위 {len(kospi_top)}종목")

    # 3. 기본 필터 (거래대금 + 제외 키워드)
    def _filter_basic(records: list[dict], market_tag: str) -> list[dict]:
        result = []
        for r in records:
            name = r.get("ISU_ABBRV") or r.get("ISU_NM", "")
            ticker = r.get("ISU_SRT_CD") or r.get("ISU_CD", "")
            if any(kw in name for kw in EXCLUDE_KEYWORDS):
                continue
            amount_billion = r["_amount"] / 1e9
            if amount_billion < min_amount_billion:
                continue
            result.append({
                "ticker": ticker,
                "name": name,
                "market": market_tag,
                "market_cap": r["_mcap"],
                "amount_billion": round(amount_billion, 1),
            })
        return result

    kosdaq_candidates = _filter_basic(kosdaq_top, "KOSDAQ")
    kospi_candidates = _filter_basic(kospi_top, "KOSPI")
    all_candidates = kosdaq_candidates + kospi_candidates

    print(f"  기본 필터 통과: 코스닥 {len(kosdaq_candidates)} + 코스피 {len(kospi_candidates)} = {len(all_candidates)}종목 (거래대금 {min_amount_billion}억+)")

    # 4. ATR 필터
    daily_data = collect_daily_ohlcv(api, date, days=25)

    universe = []
    for idx, c in enumerate(all_candidates):
        ohlcv = daily_data.get(c["ticker"], [])
        atr = calc_atr_pct(ohlcv)
        c["atr_pct"] = round(atr, 4)

        if atr >= min_atr_pct:
            universe.append(c)
            status = "OK"
        else:
            status = "--"

        print(
            f"    [{idx+1}/{len(all_candidates)}] {c['ticker']} {c['name']:<12} "
            f"[{c['market']}] 거래대금:{c['amount_billion']:>7.1f}억 ATR:{atr:.2%} {status}"
        )

    kosdaq_passed = [u for u in universe if u["market"] == "KOSDAQ"]
    kospi_passed = [u for u in universe if u["market"] == "KOSPI"]
    print(f"\n  ATR 필터 통과: 코스닥 {len(kosdaq_passed)} + 코스피 {len(kospi_passed)} = {len(universe)}종목 (ATR {min_atr_pct:.1%}+)")

    if len(universe) < 20:
        print(f"  ⚠ ATR 통과 종목 {len(universe)}개 — 20개 미만 경고")

    # 5. 총 종목 수 제한 (코스닥 우선 보존 + 코스피 거래대금 보충)
    effective_max = min(max_total, max_stocks)
    if len(universe) > effective_max:
        if len(kosdaq_passed) >= effective_max:
            universe = sorted(kosdaq_passed, key=lambda x: x["amount_billion"], reverse=True)[:effective_max]
        else:
            remaining = effective_max - len(kosdaq_passed)
            kospi_top_n = sorted(kospi_passed, key=lambda x: x["amount_billion"], reverse=True)[:remaining]
            universe = kosdaq_passed + kospi_top_n
        print(f"  종목 수 제한 적용: {effective_max}종목 (코스닥 우선 + 코스피 거래대금 보충)")
    else:
        print(f"  ATR 통과 {len(universe)}종목 ≤ max_stocks={effective_max} → 전부 포함")

    kosdaq_passed = [u for u in universe if u["market"] == "KOSDAQ"]
    kospi_passed = [u for u in universe if u["market"] == "KOSPI"]

    # 6. 결과 요약
    print(f"\n{'=' * 70}")
    print(f"  최종 유니버스: {len(universe)}종목 (코스닥 {len(kosdaq_passed)} + 코스피 {len(kospi_passed)})")
    print(f"{'=' * 70}")

    # 7. YAML 저장
    if not dry_run:
        save_universe_yaml(universe, date)
        print(f"\n  [OK] 저장 완료: {OUTPUT_PATH}")
    else:
        print("\n  [DRY RUN] 파일 생성 건너뜀")

    return universe


def save_universe_yaml(universe: list[dict], date: str) -> None:
    """universe.yaml 파일을 생성한다."""
    header = f"""# ============================================================================
# universe.yaml — 단타 유니버스 (자동 생성)
# ============================================================================
# 생성일: {date}
# 기준: 코스닥+코스피 시가총액 상위 + 거래대금/ATR 필터
# 생성: python scripts/generate_universe.py
# 데이터: KRX Open API (https://openapi.krx.co.kr)
# 주기: 분기 1회 재생성 권장
# ============================================================================
"""

    kosdaq_stocks = [u for u in universe if u.get("market") == "KOSDAQ"]
    kospi_stocks = [u for u in universe if u.get("market") == "KOSPI"]

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(header + "\n")
        f.write("stocks:\n")

        if kosdaq_stocks:
            f.write("  # === 코스닥 (거래대금/ATR 필터) ===\n")
            for s in kosdaq_stocks:
                extra = ""
                if "amount_billion" in s:
                    extra = f"  # 거래대금 {s['amount_billion']}억, ATR {s.get('atr_pct', 0):.2%}"
                f.write(f'  - ticker: "{s["ticker"]}"\n')
                f.write(f'    name: "{s["name"]}"{extra}\n')

        if kospi_stocks:
            f.write("\n  # === 코스피 (거래대금/ATR 필터) ===\n")
            for s in kospi_stocks:
                extra = ""
                if "amount_billion" in s:
                    extra = f"  # 거래대금 {s['amount_billion']}억, ATR {s.get('atr_pct', 0):.2%}"
                f.write(f'  - ticker: "{s["ticker"]}"\n')
                f.write(f'    name: "{s["name"]}"{extra}\n')


def main():
    parser = argparse.ArgumentParser(description="코스닥+코스피 유니버스 생성 (KRX Open API)")
    parser.add_argument("--top-kosdaq", type=int, default=150, help="코스닥 시총 상위 N종목 (기본: 150)")
    parser.add_argument("--top-kospi", type=int, default=200, help="코스피 시총 상위 N종목 (기본: 200)")
    parser.add_argument("--min-amount", type=float, default=50.0, help="최소 거래대금 (억원, 기본: 50)")
    parser.add_argument("--min-atr", type=float, default=0.02, help="최소 ATR%% (기본: 2%%)")
    parser.add_argument("--max-total", type=int, default=60, help="시총/거래대금 필터 후 최대 후보 수 (기본: 60)")
    parser.add_argument("--max-stocks", type=int, default=40, help="ATR 상위 최종 종목 수 (기본: 40)")
    parser.add_argument("--dry-run", action="store_true", help="파일 생성 없이 미리보기")

    args = parser.parse_args()

    generate_universe(
        top_kosdaq=args.top_kosdaq,
        top_kospi=args.top_kospi,
        min_amount_billion=args.min_amount,
        min_atr_pct=args.min_atr,
        max_total=args.max_total,
        max_stocks=args.max_stocks,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()

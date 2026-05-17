"""scripts/collect_broad_universe.py — 거래대금 상위 300종목 분봉 수집.

종목 선정:
    ka10099 KOSPI + KOSDAQ 전종목 조회 → 시총(listCount × lastPrice) 기준 상위 300종목
    시총 1,000억 이상, config/universe_broad.yaml에 저장

    --momentum 모드: ticker_daily_ohlcv에서 ATR>=4% + 거래대금>=30억 통과 전종목 선정
    (API 불필요, universe_broad_momentum.yaml에 저장)

분봉 수집:
    기존 DB에 있는 종목: 데이터 기간 확인 → 누락분만 보충
    신규 종목: 전체 기간(~300 영업일) 수집
    Semaphore(5) 병렬화 + rate limit 5 req/s 준수
    UPSERT(INSERT OR IGNORE)로 중복 방지

사용법:
    python -u scripts/collect_broad_universe.py                          # 전체 수집
    python -u scripts/collect_broad_universe.py --select-only            # 시총 기준 종목 선정만
    python -u scripts/collect_broad_universe.py --momentum --select-only # ATR+거래대금 기준 선정만
    python -u scripts/collect_broad_universe.py --resume                 # 미수집분만 이어서
    python -u scripts/collect_broad_universe.py --batch 1                # 1~50종목만
"""
from __future__ import annotations

import argparse
import asyncio
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import yaml
from dotenv import load_dotenv
from loguru import logger

load_dotenv()

sys.path.insert(0, str(Path(__file__).parent.parent))

from config.settings import AppConfig
from core.auth import TokenManager
from core.kiwoom_rest import KiwoomRestClient
from core.rate_limiter import AsyncRateLimiter
from data.db_manager import DbManager

_BROAD_UNIVERSE_PATH = Path(__file__).parent.parent / "config" / "universe_broad.yaml"
_MOMENTUM_UNIVERSE_PATH = Path(__file__).parent.parent / "config" / "universe_broad_momentum.yaml"
_TARGET_COUNT = 300
_MIN_MARKET_CAP = 100_000_000_000   # 1,000억
_BATCH_SIZE = 50
_COLLECTION_DAYS = 300              # ~300 영업일(2025-04-01~현재)
_TARGET_START_DATE = "20250401"


# ---------------------------------------------------------------------------
# 시총 계산 헬퍼
# ---------------------------------------------------------------------------

def _extract_market_cap(item: dict) -> int:
    """ka10099 응답 항목에서 시총 추정 (상장주식수 × 주가).

    실제 응답 필드: listCount(상장주식수, 주 단위), lastPrice(최종가, 0패딩 문자열)
    """
    price = 0
    shares = 0

    # 주가 필드 — 실제: lastPrice (0-padded, e.g. '00006030')
    for key in ("lastPrice", "cur_pric", "cur_prc", "lst_pric", "prsnt_pric"):
        val = item.get(key)
        if val:
            try:
                price = abs(int(float(str(val).replace(",", "").lstrip("0") or "0")))
                if price > 0:
                    break
            except (ValueError, TypeError):
                continue

    # 상장주식수 필드 — 실제: listCount (0-padded, 주 단위)
    for key in ("listCount", "flo_stk", "lstg_stk_cnt", "list_shrs", "isu_stk_cnt"):
        val = item.get(key)
        if val:
            try:
                shares = abs(int(float(str(val).replace(",", ""))))
                if shares > 0:
                    break
            except (ValueError, TypeError):
                continue

    if price <= 0 or shares <= 0:
        return 0

    cap = shares * price
    # 합리적 범위 체크 (10억 ~ 1경)
    if 1e9 <= cap <= 1e16:
        return int(cap)
    # 천주 단위 가정 fallback
    cap_k = shares * 1000 * price
    if 1e9 <= cap_k <= 1e16:
        return int(cap_k)
    return int(max(cap, cap_k))


def _determine_market(item: dict, market_code: str) -> str:
    """시장 구분 (kospi / kosdaq).

    실제 응답 필드: marketCode ('0'=KOSPI, '10'=KOSDAQ), marketName ('거래소'/'코스닥')
    """
    # 실제 응답 우선
    code = str(item.get("marketCode", "") or "")
    if code in ("0", "1"):
        return "kospi"
    if code in ("10", "2"):
        return "kosdaq"

    nm = str(item.get("marketName", "") or item.get("mrkt_tp_nm", "") or "")
    nm_up = nm.upper()
    if "코스닥" in nm or "KOSDAQ" in nm_up:
        return "kosdaq"
    if "거래소" in nm or "코스피" in nm or "KOSPI" in nm_up:
        return "kospi"

    return "kospi" if market_code == "0" else "kosdaq"


def _extract_ticker(item: dict) -> str | None:
    # 실제 응답 우선: 'code' 키
    for key in ("code", "stk_cd", "isu_cd", "itms_cd", "stnd_iscd"):
        val = item.get(key, "")
        if val and len(str(val)) == 6 and str(val).isdigit():
            return str(val)
    return None


def _extract_name(item: dict) -> str:
    # 실제 응답 우선: 'name' 키
    for key in ("name", "stk_nm", "isu_nm", "itms_nm", "hts_kor_isnm"):
        val = item.get(key, "")
        if val:
            return str(val).strip()
    return ""


# ---------------------------------------------------------------------------
# 종목 분류 필터 (ETF/ETN/스팩/우선주/관리종목/리츠 제외)
# ---------------------------------------------------------------------------

_ETF_BRAND_PREFIXES = (
    "KODEX", "TIGER", "SOL", "RISE", "PLUS", "HANARO",
    "ACE", "KOSEF", "ARIRANG", "BNK", "TIMEFOLIO", "KBSTAR",
    "TREX", "FOCUS",
)

# kind 필드 코드: E=ETF, R=리츠, F/N=펀드, K=ELW, B=채권/ETN
_KIND_EXCLUDE_MAP = {"E": "ETF", "R": "리츠", "F": "펀드", "N": "펀드", "K": "ELW", "B": "ETN/채권"}


def _classify(stock: dict) -> tuple[bool, str]:
    """종목 제외 여부 판단.

    Returns:
        (is_excluded, reason) — reason은 로그용 카테고리명
    """
    name = stock.get("name", "").upper()
    ticker = stock.get("ticker", "") or ""
    kind = str((stock.get("_raw") or {}).get("kind", "") or "").upper()

    # kind 코드로 ETF/리츠/ELW/채권 판별
    if kind in _KIND_EXCLUDE_MAP:
        return True, _KIND_EXCLUDE_MAP[kind]

    # ETF 브랜드명 (종목명 시작)
    for prefix in _ETF_BRAND_PREFIXES:
        if name.startswith(prefix):
            return True, "ETF"

    # ETN (종목명 포함)
    if "ETN" in name:
        return True, "ETN"

    # 스팩(SPAC)
    if any(kw in name for kw in ("스팩", "SPAC", "기업인수")):
        return True, "스팩"

    # 리츠 (kind 'R'은 위에서 처리, 이름 기반 추가)
    if any(kw in name for kw in ("리츠", "REIT")):
        return True, "리츠"

    # 우선주: 종목코드 끝자리 5, 7, 8, 9
    if len(ticker) == 6 and ticker[-1] in "5789":
        return True, "우선주"

    # 관리종목 / 정리매매
    if any(kw in name for kw in ("관리", "정리")):
        return True, "관리종목"

    return False, ""


# ---------------------------------------------------------------------------
# 유니버스 선정
# ---------------------------------------------------------------------------

async def select_universe(rest_client: KiwoomRestClient) -> list[dict]:
    """ka10099로 KOSPI + KOSDAQ 전종목 조회 → 시총 상위 300종목 선정."""
    logger.info("종목 리스트 조회 중 (ka10099)...")

    all_stocks: list[dict] = []
    for market_code, market_name in [("0", "KOSPI"), ("10", "KOSDAQ")]:
        try:
            raw_list = await rest_client.get_stock_list_by_market(market_code)
            count_before = len(all_stocks)
            for item in raw_list:
                ticker = _extract_ticker(item)
                if not ticker:
                    continue
                name = _extract_name(item)
                market = _determine_market(item, market_code)
                cap = _extract_market_cap(item)
                all_stocks.append({
                    "ticker": ticker,
                    "name": name,
                    "market": market,
                    "market_cap": cap,
                    "_raw": item,
                })
            logger.info(f"{market_name}: {len(all_stocks) - count_before}종목 조회")
        except Exception as e:
            logger.warning(f"{market_name} 조회 실패: {e}")

    logger.info(f"전체 조회: {len(all_stocks)}종목")

    # 시총 유효한 종목만 필터
    valid = [s for s in all_stocks if s["market_cap"] >= _MIN_MARKET_CAP]
    logger.info(f"시총 1,000억 이상: {len(valid)}종목")

    # 시총 없는 종목이 너무 많으면 경고
    missing_cap = sum(1 for s in all_stocks if s["market_cap"] == 0)
    if missing_cap > len(all_stocks) * 0.5:
        logger.warning(
            f"시총 미계산 종목 {missing_cap}/{len(all_stocks)}개 — "
            f"ka10099 응답에 주가/주식수 필드 없을 수 있음"
        )

    # ETF/ETN/스팩/우선주/관리종목/리츠 제외
    exclude_counter: Counter = Counter()
    clean: list[dict] = []
    for s in valid:
        excluded, reason = _classify(s)
        if excluded:
            exclude_counter[reason] += 1
        else:
            clean.append(s)

    total_excluded = sum(exclude_counter.values())
    if total_excluded > 0:
        detail = " / ".join(
            f"{r}:{c}" for r, c in sorted(exclude_counter.items(), key=lambda x: -x[1])
        )
        logger.info(f"제외: {total_excluded}종목 ({detail})")
    logger.info(f"유효 종목: {len(clean)}종목 (제외 후)")

    # 시총 내림차순 상위 300 (제외 후 부족 시 차순위 자동 보충)
    clean.sort(key=lambda x: x["market_cap"], reverse=True)
    top_300 = clean[:_TARGET_COUNT]

    # _raw 제거 (yaml 저장용)
    result = [
        {"ticker": s["ticker"], "name": s["name"], "market": s["market"], "market_cap": s["market_cap"]}
        for s in top_300
    ]
    logger.info(f"선정 완료: {len(result)}종목 (목표 {_TARGET_COUNT})")
    return result


def save_universe(stocks: list[dict]) -> None:
    """config/universe_broad.yaml 저장."""
    from datetime import datetime as _dt
    data = {
        "generated_at": _dt.now().strftime("%Y-%m-%d %H:%M"),
        "total": len(stocks),
        "stocks": stocks,
    }
    _BROAD_UNIVERSE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_BROAD_UNIVERSE_PATH, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
    logger.info(f"유니버스 저장: {_BROAD_UNIVERSE_PATH} ({len(stocks)}종목)")


def load_universe() -> list[dict]:
    """config/universe_broad.yaml 로드."""
    if not _BROAD_UNIVERSE_PATH.exists():
        return []
    with open(_BROAD_UNIVERSE_PATH, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data.get("stocks", [])


# ---------------------------------------------------------------------------
# 모멘텀 기반 유니버스 선정 (ticker_daily_ohlcv 기반, API 불필요)
# ---------------------------------------------------------------------------

def select_universe_momentum(db_path: str, stocks_meta: list[dict]) -> list[dict]:
    """ticker_daily_ohlcv에서 ATR>=4% + 20일 평균 거래대금>=30억 통과 전종목 선정.

    상한 없이 조건 통과 전종목 반환. ETF/우선주는 stocks_meta 단계에서 이미 제외됨.
    """
    import numpy as np
    from backtest.universe_simulator import _calc_atr_pct

    _MIN_ATR_PCT  = 0.04
    _MIN_TURNOVER = 3_000_000_000
    _MIN_ROWS     = 16   # ATR(14) 계산에 최소 15+1행

    conn = sqlite3.connect(db_path)
    result: list[dict] = []
    no_data = low_atr = low_turnover = 0

    try:
        for stock in stocks_meta:
            ticker = stock["ticker"]
            cur = conn.execute(
                "SELECT high, low, close, volume, turnover "
                "FROM ticker_daily_ohlcv WHERE ticker=? ORDER BY dt DESC LIMIT 35",
                (ticker,),
            )
            rows = cur.fetchall()
            if len(rows) < _MIN_ROWS:
                no_data += 1
                continue

            rows = list(reversed(rows))   # 오래된→최신
            highs     = np.array([r[0] for r in rows], dtype=np.float64)
            lows      = np.array([r[1] for r in rows], dtype=np.float64)
            closes    = np.array([r[2] for r in rows], dtype=np.float64)
            turnovers = np.array([r[4] for r in rows], dtype=np.float64)

            atr_pct = _calc_atr_pct(closes, highs, lows, length=14)
            if atr_pct is None or atr_pct < _MIN_ATR_PCT:
                low_atr += 1
                continue

            avg_turnover = float(np.mean(turnovers[-20:]))
            if avg_turnover < _MIN_TURNOVER:
                low_turnover += 1
                continue

            result.append(stock)
    finally:
        conn.close()

    logger.info(
        f"모멘텀 필터: 통과 {len(result)}종목 / "
        f"데이터부족 {no_data} / ATR미달 {low_atr} / 거래대금미달 {low_turnover}"
    )
    return result


def save_universe_momentum(stocks: list[dict]) -> None:
    """config/universe_broad_momentum.yaml 저장."""
    from datetime import datetime as _dt
    data = {
        "generated_at": _dt.now().strftime("%Y-%m-%d %H:%M"),
        "total": len(stocks),
        "filter": "ATR(14)>=4% + 20일평균거래대금>=30억 + 시총>=1000억 (ETF/우선주 제외)",
        "stocks": stocks,
    }
    _MOMENTUM_UNIVERSE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_MOMENTUM_UNIVERSE_PATH, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
    logger.info(f"모멘텀 유니버스 저장: {_MOMENTUM_UNIVERSE_PATH} ({len(stocks)}종목)")


# ---------------------------------------------------------------------------
# DB 상태 확인
# ---------------------------------------------------------------------------

def check_db_status(db_path: str, tickers: list[str]) -> dict[str, dict]:
    """각 티커의 분봉 DB 현황 조회.

    Returns:
        {ticker: {cnt, min_dt, max_dt}}
    """
    conn = sqlite3.connect(db_path)
    result: dict[str, dict] = {}
    try:
        for ticker in tickers:
            cur = conn.execute(
                "SELECT COUNT(*) as cnt, "
                "MIN(substr(ts,1,10)) as min_dt, "
                "MAX(substr(ts,1,10)) as max_dt "
                "FROM intraday_candles WHERE ticker=? AND tf='1m'",
                (ticker,),
            )
            row = cur.fetchone()
            cnt, min_dt, max_dt = (row or (0, None, None))
            result[ticker] = {"cnt": int(cnt or 0), "min_dt": min_dt, "max_dt": max_dt}
    finally:
        conn.close()
    return result


# ---------------------------------------------------------------------------
# 분봉 수집
# ---------------------------------------------------------------------------

async def _collect_one(
    ticker: str,
    rest_client: KiwoomRestClient,
    db: DbManager,
    days: int,
    sem: asyncio.Semaphore,
    start_date: str,   # YYYYMMDD
) -> int:
    """단일 종목 분봉 수집 (Semaphore 사용)."""
    from backtest.data_collector import DataCollector, _extract_candles, _parse_timestamp, _abs_float, _to_int

    INSERT_SQL = (
        "INSERT OR IGNORE INTO intraday_candles "
        "(ticker, tf, ts, open, high, low, close, volume) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
    )

    async with sem:
        total_saved = 0
        base_dt = ""

        for _page in range(200):    # 최대 200 페이지 안전장치
            try:
                data = await rest_client.get_minute_ohlcv(ticker, base_dt=base_dt)
            except Exception as e:
                logger.debug(f"[{ticker}] API 실패 (base_dt={base_dt}): {e}")
                break

            candles = _extract_candles(data)
            if not candles:
                break

            # 시작일 이전 데이터까지 내려갔으면 중단
            oldest_dt_raw = candles[-1].get("cntr_tm", "")
            if oldest_dt_raw[:8] and oldest_dt_raw[:8] <= start_date:
                # 이 페이지까지만 저장 후 종료
                batch = _build_batch(ticker, candles, start_date)
                if batch:
                    await db.executemany_safe(INSERT_SQL, batch)
                    total_saved += len(batch)
                break

            batch = _build_batch(ticker, candles, start_date)
            if batch:
                await db.executemany_safe(INSERT_SQL, batch)
                total_saved += len(batch)

            # 페이지네이션: 가장 오래된 캔들 날짜로 이동
            if len(oldest_dt_raw) >= 8:
                next_base = oldest_dt_raw[:8]
                if next_base == base_dt:
                    break
                base_dt = next_base
            else:
                break

            if len(candles) < 900:
                break

        return total_saved


def _build_batch(ticker: str, candles: list[dict], start_date: str) -> list[tuple]:
    from backtest.data_collector import _parse_timestamp, _abs_float, _to_int
    batch = []
    for c in candles:
        raw_dt = c.get("cntr_tm", "")
        if raw_dt[:8] < start_date:
            continue
        ts = _parse_timestamp(raw_dt)
        if ts is None:
            continue
        batch.append((
            ticker, "1m", ts,
            _abs_float(c.get("open_pric")),
            _abs_float(c.get("high_pric")),
            _abs_float(c.get("low_pric")),
            _abs_float(c.get("cur_prc")),
            _to_int(c.get("trde_qty")),
        ))
    return batch


async def collect_batch(
    tickers: list[str],
    rest_client: KiwoomRestClient,
    db: DbManager,
    db_status: dict[str, dict],
    resume: bool,
    start_time: float,
) -> dict[str, int]:
    """50종목 단위 배치 수집."""
    sem = asyncio.Semaphore(5)
    tasks = []
    skipped = []

    for ticker in tickers:
        status = db_status.get(ticker, {"cnt": 0, "min_dt": None})
        cnt = status["cnt"]
        min_dt = status["min_dt"]

        if resume and cnt > 0 and min_dt and min_dt <= "2025-04-05":
            skipped.append(ticker)
            continue

        tasks.append((ticker, asyncio.create_task(
            _collect_one(ticker, rest_client, db, _COLLECTION_DAYS, sem, _TARGET_START_DATE)
        )))

    if skipped:
        logger.info(f"스킵 (이미 수집됨): {len(skipped)}종목")

    results: dict[str, int] = {t: 0 for t in skipped}
    for ticker, task in tasks:
        try:
            saved = await task
            results[ticker] = saved
        except Exception as e:
            logger.warning(f"[{ticker}] 수집 실패: {e}")
            results[ticker] = 0

    return results


# ---------------------------------------------------------------------------
# 메인
# ---------------------------------------------------------------------------

async def _main(args: argparse.Namespace) -> None:
    logger.remove()
    logger.add(sys.stderr, level="INFO", format="{time:HH:mm:ss} | {level:<7} | {message}")

    config = AppConfig.from_yaml()
    db = DbManager(config.db_path)
    await db.init()

    token_manager = TokenManager(
        app_key=config.kiwoom.app_key,
        secret_key=config.kiwoom.secret_key,
        base_url=config.kiwoom.rest_base_url,
    )
    rate_limiter = AsyncRateLimiter(
        max_calls=config.kiwoom.rate_limit_calls,
        period=config.kiwoom.rate_limit_period,
    )
    rest_client = KiwoomRestClient(
        config=config.kiwoom,
        token_manager=token_manager,
        rate_limiter=rate_limiter,
    )

    try:
        # ── 1. 유니버스 선정 ─────────────────────────────────

        # --momentum: ticker_daily_ohlcv 기반 (API 불필요)
        if args.momentum:
            stocks_base = load_universe()
            if not stocks_base:
                logger.error("먼저 --select-only로 기본 유니버스(universe_broad.yaml) 선정 필요")
                return
            stocks = select_universe_momentum(config.db_path, stocks_base)
            save_universe_momentum(stocks)
            tickers = [s["ticker"] for s in stocks]
            logger.info(f"모멘텀 유니버스: {len(tickers)}종목")
            if args.select_only:
                _print_universe_summary(stocks)
                return
        else:
            if _BROAD_UNIVERSE_PATH.exists() and not args.select_only:
                stocks = load_universe()
                logger.info(f"기존 유니버스 로드: {len(stocks)}종목 ({_BROAD_UNIVERSE_PATH.name})")
            else:
                stocks = await select_universe(rest_client)
                save_universe(stocks)

            tickers = [s["ticker"] for s in stocks]
            logger.info(f"전체 종목: {len(tickers)}개")

            if args.select_only:
                _print_universe_summary(stocks)
                return

        # ── 2. DB 현황 확인 ──────────────────────────────────
        logger.info("DB 현황 조회 중...")
        db_status = check_db_status(config.db_path, tickers)

        have_data = sum(1 for s in db_status.values() if s["cnt"] > 0)
        new_tickers = sum(1 for s in db_status.values() if s["cnt"] == 0)
        need_supplement = sum(
            1 for s in db_status.values()
            if s["cnt"] > 0 and s.get("min_dt") and s["min_dt"] > "2025-04-05"
        )
        logger.info(f"기보유 {have_data}종목 (보충 {need_supplement}종목) + 신규 {new_tickers}종목")

        # ── 3. 배치 범위 결정 ────────────────────────────────
        if args.batch > 0:
            start_idx = (args.batch - 1) * _BATCH_SIZE
            end_idx   = min(start_idx + _BATCH_SIZE, len(tickers))
            batch_tickers = tickers[start_idx:end_idx]
            logger.info(f"배치 {args.batch}: {start_idx+1}~{end_idx}번째 종목 ({len(batch_tickers)}개)")
        else:
            batch_tickers = tickers

        # ── 4. 배치별 수집 ──────────────────────────────────
        t0 = time.time()
        all_results: dict[str, int] = {}

        for batch_start in range(0, len(batch_tickers), _BATCH_SIZE):
            batch = batch_tickers[batch_start:batch_start + _BATCH_SIZE]
            batch_num = batch_start // _BATCH_SIZE + 1
            total_batches = (len(batch_tickers) + _BATCH_SIZE - 1) // _BATCH_SIZE

            logger.info(f"배치 {batch_num}/{total_batches} 수집 시작 ({len(batch)}종목)...")
            results = await collect_batch(batch, rest_client, db, db_status, args.resume, t0)
            all_results.update(results)

            completed = sum(1 for v in all_results.values() if v >= 0)
            elapsed = (time.time() - t0) / 60
            logger.info(
                f"[COLLECT] {completed}/{len(batch_tickers)} 종목 완료 "
                f"({completed/len(batch_tickers)*100:.0f}%) — 경과 {elapsed:.0f}분"
            )

        # ── 5. 결과 요약 ─────────────────────────────────────
        total_saved = sum(v for v in all_results.values() if v > 0)
        elapsed_min = (time.time() - t0) / 60
        logger.info(
            f"\n{'='*60}\n"
            f"수집 완료: {len(all_results)}종목 / 총 {total_saved:,}개 캔들 저장 / {elapsed_min:.1f}분 소요\n"
            f"{'='*60}"
        )

    finally:
        await db.close()
        if rest_client._session:
            await rest_client.aclose()


def _print_universe_summary(stocks: list[dict]) -> None:
    kospi = [s for s in stocks if s.get("market") == "kospi"]
    kosdaq = [s for s in stocks if s.get("market") == "kosdaq"]
    print(f"\n{'='*60}")
    print(f"광범위 유니버스 선정 결과: 총 {len(stocks)}종목")
    print(f"  KOSPI  : {len(kospi)}종목")
    print(f"  KOSDAQ : {len(kosdaq)}종목")
    print(f"{'='*60}")
    if stocks:
        cap_sorted = sorted(stocks, key=lambda x: x.get("market_cap", 0), reverse=True)
        print("상위 10종목:")
        for s in cap_sorted[:10]:
            cap_str = f"{s.get('market_cap', 0)/1e12:.1f}조" if s.get("market_cap", 0) > 0 else "N/A"
            print(f"  {s['ticker']} {s['name']:<12} {s['market'].upper():<8} 시총 {cap_str}")
    print(f"{'='*60}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="거래대금 상위 300종목 분봉 수집")
    parser.add_argument("--select-only", action="store_true", help="종목 선정만 (수집 안 함)")
    parser.add_argument("--momentum", action="store_true",
                        help="ATR>=4%%+거래대금>=30억 기반 모멘텀 유니버스 선정 (API 불필요)")
    parser.add_argument("--resume", action="store_true", help="미수집분만 이어서")
    parser.add_argument("--batch", type=int, default=0,
                        help="배치 번호 (1=1~50, 2=51~100, ... 0=전체)")
    args = parser.parse_args()
    asyncio.run(_main(args))


if __name__ == "__main__":
    main()

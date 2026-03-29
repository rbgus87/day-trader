"""screener/candidate_collector.py — 종목 유니버스 데이터 수집 → candidates 생성.

키움 REST API로 유니버스 종목의 일봉/현재가 데이터를 수집하고,
PreMarketScreener가 요구하는 candidate dict 리스트를 생성한다.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from loguru import logger

from core.kiwoom_rest import KiwoomRestClient

# 유니버스 파일 기본 경로
_DEFAULT_UNIVERSE_PATH = Path(__file__).parent.parent / "config" / "universe.yaml"

# 일봉 수집 기간 (MA20 + ATR14 계산용)
_DAILY_LOOKBACK_DAYS = 40


class CandidateCollector:
    """종목 유니버스에서 스크리닝 후보 데이터를 수집한다.

    1. universe.yaml에서 종목 리스트 로드
    2. 각 종목의 일봉 데이터 수집 (최근 40일)
    3. MA20 추세, ATR(14), 거래량 급증 비율 등 계산
    4. 현재가 API로 시가총액, 거래대금 수집
    5. PreMarketScreener.screen()에 전달할 candidates dict 리스트 반환
    """

    def __init__(
        self,
        rest_client: KiwoomRestClient,
        universe_path: str | Path | None = None,
    ) -> None:
        self._rest = rest_client
        self._universe_path = Path(universe_path) if universe_path else _DEFAULT_UNIVERSE_PATH

    def load_universe(self) -> list[dict]:
        """universe.yaml에서 종목 리스트를 로드한다."""
        if not self._universe_path.exists():
            logger.error(f"유니버스 파일 없음: {self._universe_path}")
            return []

        with open(self._universe_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        stocks = data.get("stocks", [])
        logger.info(f"유니버스 로드: {len(stocks)}종목 ({self._universe_path.name})")
        return stocks

    async def collect(self) -> list[dict]:
        """유니버스 전체 종목의 candidate 데이터를 수집한다.

        Returns:
            PreMarketScreener.screen()에 전달할 candidates list[dict]
        """
        universe = self.load_universe()
        if not universe:
            logger.warning("유니버스가 비어 있음 — candidates 없음")
            return []

        # 날짜 범위 계산 (최근 40영업일 ≈ 60일)
        end_date = datetime.now().strftime("%Y%m%d")
        start_date = (datetime.now() - timedelta(days=60)).strftime("%Y%m%d")

        candidates = []
        total = len(universe)

        for idx, stock in enumerate(universe, 1):
            ticker = stock["ticker"]
            name = stock.get("name", ticker)

            try:
                candidate = await self._collect_single(
                    ticker, name, start_date, end_date,
                )
                if candidate:
                    candidates.append(candidate)
                    logger.debug(
                        f"[{idx}/{total}] {ticker} {name} — 수집 완료 "
                        f"(score={candidate.get('score', 0):.1f})"
                    )
                else:
                    logger.debug(f"[{idx}/{total}] {ticker} {name} — 데이터 부족, 건너뜀")
            except Exception as exc:
                logger.warning(f"[{idx}/{total}] {ticker} {name} — 수집 실패: {exc}")
                continue

        logger.info(f"Candidates 수집 완료: {len(candidates)}/{total}종목")
        return candidates

    async def _collect_single(
        self,
        ticker: str,
        name: str,
        start_date: str,
        end_date: str,
    ) -> dict | None:
        """단일 종목의 candidate 데이터를 수집한다."""
        # 일봉 + 현재가 병렬 조회
        daily_task = self._fetch_daily_ohlcv(ticker, end_date)
        price_task = self._fetch_current_price(ticker)

        daily_data, price_data = await asyncio.gather(
            daily_task, price_task, return_exceptions=True,
        )

        if isinstance(daily_data, Exception) or isinstance(price_data, Exception):
            exc = daily_data if isinstance(daily_data, Exception) else price_data
            logger.warning(f"{ticker} API 오류: {exc}")
            return None

        # 일봉 DataFrame 생성
        df = self._parse_daily_ohlcv(daily_data)
        if df is None or len(df) < 20:
            return None

        # 지표 계산
        ma20_trend = self._calc_ma20_trend(df)
        atr_pct = self._calc_atr_pct(df)
        volume = int(df.iloc[-1]["volume"]) if "volume" in df.columns else 0
        prev_volume = int(df.iloc[-2]["volume"]) if len(df) >= 2 and "volume" in df.columns else 0

        # 현재가 데이터에서 시가총액, 거래대금 추출
        market_cap = self._extract_market_cap(price_data)
        avg_volume_amount = self._calc_avg_volume_amount(df)

        # 기본 점수: 거래대금 기반 (높을수록 유동성 좋음)
        score = 0.0
        if avg_volume_amount > 0:
            score = min(avg_volume_amount / 1_000_000_000, 10.0)  # 10억당 1점, 최대 10점

        return {
            "ticker": ticker,
            "name": name,
            "market_cap": market_cap,
            "avg_volume_amount": avg_volume_amount,
            "volume": volume,
            "prev_volume": prev_volume,
            "atr_pct": atr_pct,
            "ma20_trend": ma20_trend,
            "institutional_buy": 0,  # 향후 수급 API 연동
            "foreign_buy": 0,        # 향후 수급 API 연동
            "has_event": False,      # 향후 공시 API 연동
            "score": score,
        }

    # ------------------------------------------------------------------
    # REST API 래퍼
    # ------------------------------------------------------------------

    async def _fetch_daily_ohlcv(self, ticker: str, base_dt: str) -> dict:
        """일봉 데이터 조회."""
        return await self._rest.get_daily_ohlcv(ticker, base_dt=base_dt)

    async def _fetch_current_price(self, ticker: str) -> dict:
        """현재가 조회."""
        return await self._rest.get_current_price(ticker)

    # ------------------------------------------------------------------
    # 데이터 파싱
    # ------------------------------------------------------------------

    def _parse_daily_ohlcv(self, data: dict) -> pd.DataFrame | None:
        """키움 API 일봉 응답을 DataFrame으로 변환한다.

        키움 REST API 실제 응답 형식:
            data["stk_dt_pole_chart_qry"] = [
                {"dt": "20260323", "open_pric": "190500",
                 "high_pric": "191200", "low_pric": "186300",
                 "cur_prc": "186300", "trde_qty": "30268173",
                 "trde_prica": "5706606"},
                ...
            ]
        가격 필드에 부호가 포함될 수 있음 (하락 시 음수).
        """
        output = (
            data.get("stk_dt_pole_chart_qry")
            or data.get("output2")  # 하위 호환
            or []
        )
        if not output:
            return None

        rows = []
        for item in output:
            try:
                rows.append({
                    "date": item.get("dt", ""),
                    "open": abs(float(item.get("open_pric", 0))),
                    "high": abs(float(item.get("high_pric", 0))),
                    "low": abs(float(item.get("low_pric", 0))),
                    "close": abs(float(item.get("cur_prc", 0))),
                    "volume": int(item.get("trde_qty", 0)),
                    "tr_amount": int(item.get("trde_prica", 0)) * 1_000_000,  # 백만원 → 원
                })
            except (ValueError, TypeError):
                continue

        if not rows:
            return None

        df = pd.DataFrame(rows)
        # 날짜 오름차순 정렬 (키움은 최신 순으로 반환)
        df = df.sort_values("date").reset_index(drop=True)
        return df

    # ------------------------------------------------------------------
    # 지표 계산
    # ------------------------------------------------------------------

    def _calc_ma20_trend(self, df: pd.DataFrame) -> str:
        """MA20 추세 판별: ascending / flat / descending."""
        if len(df) < 20:
            return "flat"

        ma20 = df["close"].rolling(20).mean()
        recent = ma20.iloc[-5:]  # 최근 5일 MA20

        if len(recent.dropna()) < 3:
            return "flat"

        # 최근 5일 MA20의 기울기로 판별
        slope = np.polyfit(range(len(recent.dropna())), recent.dropna().values, 1)[0]
        avg_price = df["close"].iloc[-1]

        if avg_price == 0:
            return "flat"

        # 기울기를 가격 대비 비율로 정규화
        slope_pct = slope / avg_price

        if slope_pct > 0.001:  # +0.1% 이상 상승
            return "ascending"
        elif slope_pct < -0.001:  # -0.1% 이상 하락
            return "descending"
        return "flat"

    def _calc_atr_pct(self, df: pd.DataFrame) -> float:
        """ATR(14) / 종가 비율 계산."""
        if len(df) < 15:
            return 0.0

        high = df["high"].values
        low = df["low"].values
        close = df["close"].values

        # True Range 계산
        tr = np.maximum(
            high[1:] - low[1:],
            np.maximum(
                np.abs(high[1:] - close[:-1]),
                np.abs(low[1:] - close[:-1]),
            ),
        )

        if len(tr) < 14:
            return 0.0

        # ATR(14) = 최근 14일 TR 평균
        atr = np.mean(tr[-14:])
        last_close = close[-1]

        if last_close == 0:
            return 0.0

        return atr / last_close

    def _calc_avg_volume_amount(self, df: pd.DataFrame) -> int:
        """최근 20일 평균 거래대금 (원)."""
        if "tr_amount" in df.columns and len(df) >= 20:
            return int(df["tr_amount"].iloc[-20:].mean())

        # tr_amount가 없으면 close * volume으로 추정
        if len(df) >= 20:
            amounts = df["close"].iloc[-20:] * df["volume"].iloc[-20:]
            return int(amounts.mean())

        return 0

    def _extract_market_cap(self, price_data: dict) -> int:
        """현재가 응답에서 시가총액 추출 (원).

        키움 REST API 실제 필드명 (flat dict):
            flo_stk: 상장주식수 (천주 단위)
            cur_prc: 현재가 (부호 포함)
        """
        # 상장주식수(천주) × 현재가 × 1000
        flo_stk = price_data.get("flo_stk")
        cur_prc = price_data.get("cur_prc")
        if flo_stk and cur_prc:
            try:
                shares = int(flo_stk) * 1000  # 천주 → 주
                price = abs(int(cur_prc))
                return shares * price
            except (ValueError, TypeError):
                pass

        return 0

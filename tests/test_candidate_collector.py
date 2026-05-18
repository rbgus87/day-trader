"""tests/test_candidate_collector.py — CandidateCollector 단위 테스트."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from screener.candidate_collector import CandidateCollector


# ---------------------------------------------------------------------------
# 테스트 데이터
# ---------------------------------------------------------------------------

def _make_daily_ohlcv_response(days: int = 30) -> dict:
    """키움 일봉 API 응답 mock — days일치 일봉 데이터."""
    output = []
    base_date = 20260301
    for i in range(days):
        date = base_date + i
        # 상승 추세 시뮬레이션: 종가가 점진 상승
        close = 70000 + i * 100
        output.append({
            "dt": str(date),
            "open_pric": str(close - 500),
            "high_pric": str(close + 1000),
            "low_pric": str(close - 1000),
            "cur_prc": str(close),
            "trde_qty": str(15_000_000 + i * 100_000),
            "trde_prica": str(10_000_000_000 + i * 50_000_000),
        })
    return {"stk_dt_pole_chart_qry": output}


def _make_current_price_response(flo_stk: int = 5919638, cur_prc: int = 72000) -> dict:
    """키움 현재가 API 응답 mock (flat dict).

    flo_stk: 상장주식수 (천주 단위)
    cur_prc: 현재가
    """
    return {
        "stk_cd": "005930",
        "cur_prc": str(cur_prc),
        "flo_stk": str(flo_stk),
        "trde_qty": "15000000",
        "return_code": 0,
    }


def _make_universe_yaml(tmp_path: Path, stocks: list[dict] | None = None) -> Path:
    """임시 universe.yaml 파일 생성."""
    if stocks is None:
        stocks = [
            {"ticker": "005930", "name": "삼성전자"},
            {"ticker": "000660", "name": "SK하이닉스"},
        ]
    import yaml
    path = tmp_path / "universe.yaml"
    path.write_text(yaml.dump({"stocks": stocks}), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_investor_trading_response(inst_qty: int = 5000, frgn_qty: int = 3000) -> dict:
    """ka10009 기관/외국인 매매동향 mock 응답 (EP_FRGN_ISTT)."""
    return {
        "orgn_daly_nettrde": str(inst_qty),   # 기관 일별 순매수
        "frgnr_daly_nettrde": str(frgn_qty),  # 외국인 일별 순매수
        "return_code": 0,
    }


@pytest.fixture
def mock_rest():
    rest = MagicMock()
    rest.get_daily_ohlcv = AsyncMock(return_value=_make_daily_ohlcv_response(30))
    rest.get_current_price = AsyncMock(return_value=_make_current_price_response(5000))
    rest.get_investor_trading = AsyncMock(return_value=_make_investor_trading_response())
    return rest


@pytest.fixture
def collector(mock_rest, tmp_path):
    universe_path = _make_universe_yaml(tmp_path)
    return CandidateCollector(rest_client=mock_rest, universe_path=universe_path)


# ---------------------------------------------------------------------------
# load_universe 테스트
# ---------------------------------------------------------------------------

class TestLoadUniverse:
    def test_loads_stocks_from_yaml(self, collector):
        """universe.yaml에서 종목 리스트를 정상 로드한다."""
        stocks = collector.load_universe()
        assert len(stocks) == 2
        assert stocks[0]["ticker"] == "005930"
        assert stocks[1]["ticker"] == "000660"

    def test_returns_empty_for_missing_file(self, mock_rest):
        """파일이 없으면 빈 리스트를 반환한다."""
        collector = CandidateCollector(mock_rest, universe_path="/nonexistent/path.yaml")
        stocks = collector.load_universe()
        assert stocks == []


# ---------------------------------------------------------------------------
# 지표 계산 테스트
# ---------------------------------------------------------------------------

class TestIndicators:
    def test_ma20_trend_ascending(self, collector):
        """상승 추세 데이터에서 ma20_trend=ascending을 반환한다."""
        import pandas as pd
        # 30일 연속 상승
        data = {"close": [70000 + i * 200 for i in range(30)]}
        df = pd.DataFrame(data)
        result = collector._calc_ma20_trend(df)
        assert result == "ascending"

    def test_ma20_trend_descending(self, collector):
        """하락 추세 데이터에서 ma20_trend=descending을 반환한다."""
        import pandas as pd
        data = {"close": [80000 - i * 200 for i in range(30)]}
        df = pd.DataFrame(data)
        result = collector._calc_ma20_trend(df)
        assert result == "descending"

    def test_ma20_trend_flat(self, collector):
        """횡보 데이터에서 ma20_trend=flat을 반환한다."""
        import pandas as pd
        data = {"close": [70000] * 30}
        df = pd.DataFrame(data)
        result = collector._calc_ma20_trend(df)
        assert result == "flat"

    def test_ma20_trend_insufficient_data(self, collector):
        """데이터가 20일 미만이면 flat을 반환한다."""
        import pandas as pd
        data = {"close": [70000 + i * 100 for i in range(10)]}
        df = pd.DataFrame(data)
        result = collector._calc_ma20_trend(df)
        assert result == "flat"

    def test_atr_pct_calculation(self, collector):
        """ATR(14) / 종가 비율이 합리적 범위에 있다."""
        import pandas as pd
        # 일봉 데이터 생성 (일중 변동 2000원 / 종가 70000원 ≈ 2.9%)
        data = {
            "high": [71000 + i * 100 for i in range(30)],
            "low":  [69000 + i * 100 for i in range(30)],
            "close": [70000 + i * 100 for i in range(30)],
        }
        df = pd.DataFrame(data)
        atr_pct = collector._calc_atr_pct(df)
        assert 0.01 < atr_pct < 0.10  # 1% ~ 10% 범위

    def test_atr_pct_insufficient_data(self, collector):
        """데이터가 부족하면 0.0을 반환한다."""
        import pandas as pd
        data = {
            "high": [71000] * 5,
            "low": [69000] * 5,
            "close": [70000] * 5,
        }
        df = pd.DataFrame(data)
        atr_pct = collector._calc_atr_pct(df)
        assert atr_pct == 0.0


# ---------------------------------------------------------------------------
# 시가총액 추출 테스트
# ---------------------------------------------------------------------------

class TestMarketCap:
    def test_extract_from_flo_stk_and_cur_prc(self, collector):
        """flo_stk(천주) × cur_prc × 1000 → 시가총액."""
        data = {"flo_stk": "5919638", "cur_prc": "-186300"}
        result = collector._extract_market_cap(data)
        # 5,919,638 × 1000 × 186,300 = ~1,102조
        expected = 5919638 * 1000 * 186300
        assert result == expected

    def test_handles_positive_price(self, collector):
        """양수 가격도 정상 처리."""
        data = {"flo_stk": "1000", "cur_prc": "70000"}
        result = collector._extract_market_cap(data)
        assert result == 1000 * 1000 * 70000

    def test_returns_zero_for_missing_data(self, collector):
        """필드 누락 시 0 반환."""
        result = collector._extract_market_cap({})
        assert result == 0


# ---------------------------------------------------------------------------
# 평균 거래대금 테스트
# ---------------------------------------------------------------------------

class TestAvgVolumeAmount:
    def test_calc_from_tr_amount(self, collector):
        """tr_amount 필드가 있으면 최근 20일 평균을 계산한다."""
        import pandas as pd
        data = {
            "tr_amount": [10_000_000_000] * 30,  # 100억 × 30일
            "close": [70000] * 30,
            "volume": [15_000_000] * 30,
        }
        df = pd.DataFrame(data)
        result = collector._calc_avg_volume_amount(df)
        assert result == 10_000_000_000

    def test_fallback_to_close_times_volume(self, collector):
        """tr_amount 없으면 close × volume으로 추정한다."""
        import pandas as pd
        data = {
            "close": [70000] * 30,
            "volume": [100_000] * 30,
        }
        df = pd.DataFrame(data)
        result = collector._calc_avg_volume_amount(df)
        assert result == 70000 * 100_000  # 7,000,000,000


# ---------------------------------------------------------------------------
# collect 통합 테스트
# ---------------------------------------------------------------------------

class TestCollect:
    @pytest.mark.asyncio
    async def test_collect_returns_candidates(self, collector, mock_rest):
        """collect()가 올바른 candidates dict 리스트를 반환한다."""
        candidates = await collector.collect()

        assert len(candidates) == 2
        for c in candidates:
            assert "ticker" in c
            assert "name" in c
            assert "market_cap" in c
            assert "avg_volume_amount" in c
            assert "volume" in c
            assert "prev_volume" in c
            assert "atr_pct" in c
            assert "ma20_trend" in c
            assert "institutional_buy" in c
            assert "foreign_buy" in c
            assert "has_event" in c
            assert "score" in c

        # API 호출 횟수: 2종목 × (일봉 1회 + 현재가 1회 + 수급 1회) = 6회
        assert mock_rest.get_daily_ohlcv.call_count == 2
        assert mock_rest.get_current_price.call_count == 2
        assert mock_rest.get_investor_trading.call_count == 2

    @pytest.mark.asyncio
    async def test_collect_handles_api_error(self, mock_rest, tmp_path):
        """API 에러 발생 시 해당 종목을 건너뛴다."""
        mock_rest.get_daily_ohlcv = AsyncMock(side_effect=Exception("API error"))
        mock_rest.get_current_price = AsyncMock(return_value=_make_current_price_response())

        universe_path = _make_universe_yaml(tmp_path)
        collector = CandidateCollector(mock_rest, universe_path=universe_path)

        candidates = await collector.collect()
        assert len(candidates) == 0  # 모든 종목 실패

    @pytest.mark.asyncio
    async def test_collect_skips_insufficient_data(self, mock_rest, tmp_path):
        """일봉 데이터가 20일 미만이면 해당 종목을 건너뛴다."""
        mock_rest.get_daily_ohlcv = AsyncMock(
            return_value=_make_daily_ohlcv_response(10)  # 10일만
        )
        mock_rest.get_current_price = AsyncMock(return_value=_make_current_price_response())

        universe_path = _make_universe_yaml(tmp_path)
        collector = CandidateCollector(mock_rest, universe_path=universe_path)

        candidates = await collector.collect()
        assert len(candidates) == 0

    @pytest.mark.asyncio
    async def test_collect_empty_universe(self, mock_rest, tmp_path):
        """유니버스가 비어 있으면 빈 리스트를 반환한다."""
        universe_path = _make_universe_yaml(tmp_path, stocks=[])
        collector = CandidateCollector(mock_rest, universe_path=universe_path)

        candidates = await collector.collect()
        assert candidates == []

    @pytest.mark.asyncio
    async def test_collect_investor_api_failure_fallback(self, mock_rest, tmp_path):
        """수급 API 실패 시 institutional_buy=0, foreign_buy=0으로 fallback하여 candidate 반환."""
        mock_rest.get_investor_trading = AsyncMock(side_effect=Exception("ka10079 error"))

        universe_path = _make_universe_yaml(tmp_path)
        collector = CandidateCollector(mock_rest, universe_path=universe_path)

        candidates = await collector.collect()

        # 수급 실패해도 candidates는 정상 반환
        assert len(candidates) == 2
        for c in candidates:
            assert c["institutional_buy"] == 0
            assert c["foreign_buy"] == 0
            assert "_supply_fetched" not in c  # 내부 플래그 미노출

    @pytest.mark.asyncio
    async def test_collect_supply_fetched_flag_not_exposed(self, collector):
        """_supply_fetched 내부 플래그가 최종 candidates에 포함되지 않는다."""
        candidates = await collector.collect()
        for c in candidates:
            assert "_supply_fetched" not in c

    @pytest.mark.asyncio
    async def test_collect_investor_data_reflected(self, mock_rest, tmp_path):
        """수급 API 성공 시 institutional_buy, foreign_buy가 실제 값으로 채워진다."""
        mock_rest.get_investor_trading = AsyncMock(
            return_value=_make_investor_trading_response(inst_qty=8000, frgn_qty=2500)
        )
        universe_path = _make_universe_yaml(tmp_path, stocks=[{"ticker": "005930", "name": "삼성전자"}])
        collector = CandidateCollector(mock_rest, universe_path=universe_path)

        candidates = await collector.collect()
        assert len(candidates) == 1
        assert candidates[0]["institutional_buy"] == 8000
        assert candidates[0]["foreign_buy"] == 2500


# ---------------------------------------------------------------------------
# 수급 데이터 파싱 테스트
# ---------------------------------------------------------------------------

class TestInvestorParsing:
    def test_parse_institutional_positive(self, collector):
        """기관 순매수 양수 파싱."""
        data = _make_investor_trading_response(inst_qty=5000, frgn_qty=0)
        assert collector._parse_institutional(data) == 5000

    def test_parse_foreign_positive(self, collector):
        """외국인 순매수 양수 파싱."""
        data = _make_investor_trading_response(inst_qty=0, frgn_qty=3000)
        assert collector._parse_foreign(data) == 3000

    def test_parse_institutional_negative(self, collector):
        """기관 순매도(음수)도 정수로 파싱한다."""
        data = {"orgn_daly_nettrde": "-2000"}
        assert collector._parse_institutional(data) == -2000

    def test_parse_returns_zero_for_missing_field(self, collector):
        """필드 없으면 0 반환."""
        assert collector._parse_institutional({}) == 0
        assert collector._parse_foreign({}) == 0

    def test_parse_handles_comma_separated(self, collector):
        """쉼표 포함 숫자 문자열도 파싱 (예: '1,500')."""
        data = {"orgn_daly_nettrde": "1,500", "frgnr_daly_nettrde": "2,000"}
        assert collector._parse_institutional(data) == 1500
        assert collector._parse_foreign(data) == 2000

    def test_parse_handles_invalid_value(self, collector):
        """파싱 불가 값이면 0 반환."""
        data = {"orgn_daly_nettrde": "N/A", "frgnr_daly_nettrde": ""}
        assert collector._parse_institutional(data) == 0
        assert collector._parse_foreign(data) == 0


# ---------------------------------------------------------------------------
# 일봉 파싱 테스트
# ---------------------------------------------------------------------------

class TestParseDailyOhlcv:
    def test_parses_stk_dt_pole_chart_qry(self, collector):
        """stk_dt_pole_chart_qry 필드를 DataFrame으로 변환한다."""
        data = _make_daily_ohlcv_response(5)
        df = collector._parse_daily_ohlcv(data)
        assert df is not None
        assert len(df) == 5
        assert list(df.columns) == ["date", "open", "high", "low", "close", "volume", "tr_amount"]

    def test_returns_none_for_empty(self, collector):
        """빈 응답이면 None을 반환한다."""
        assert collector._parse_daily_ohlcv({}) is None
        assert collector._parse_daily_ohlcv({"stk_dt_pole_chart_qry": []}) is None

    def test_sorts_by_date_ascending(self, collector):
        """날짜 오름차순으로 정렬된다."""
        data = {
            "stk_dt_pole_chart_qry": [
                {"dt": "20260305", "open_pric": "70000",
                 "high_pric": "71000", "low_pric": "69000",
                 "cur_prc": "70500", "trde_qty": "1000000",
                 "trde_prica": "70000000000"},
                {"dt": "20260303", "open_pric": "69000",
                 "high_pric": "70000", "low_pric": "68000",
                 "cur_prc": "69500", "trde_qty": "900000",
                 "trde_prica": "62000000000"},
            ],
        }
        df = collector._parse_daily_ohlcv(data)
        assert df.iloc[0]["date"] == "20260303"
        assert df.iloc[1]["date"] == "20260305"

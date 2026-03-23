"""tests/test_pre_market_screener.py — PreMarketScreener 단위 테스트."""

from __future__ import annotations

import pytest

from config.settings import ScreenerConfig
from screener.pre_market import PreMarketScreener


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def config():
    return ScreenerConfig(
        min_market_cap=300_000_000_000,   # 3000억
        min_avg_volume_amount=5_000_000_000,  # 50억
        ma20_ascending=True,
        volume_surge_ratio=1.5,           # +50%
        min_atr_pct=0.02,                 # 2%
    )


@pytest.fixture
def screener(config):
    """rest_client, db는 이 파일의 테스트에서 사용하지 않으므로 None 전달."""
    return PreMarketScreener(rest_client=None, db=None, config=config)


def _make_candidate(**overrides) -> dict:
    """기본적으로 모든 필터를 통과하는 후보 딕셔너리 생성."""
    base = {
        "ticker": "005930",
        "name": "삼성전자",
        "market_cap": 400_000_000_000,    # 4000억 (>3000억)
        "avg_volume_amount": 10_000_000_000,  # 100억 (>50억)
        "volume": 3_000_000,
        "prev_volume": 1_500_000,          # surge ratio = 2.0 (>1.5)
        "atr_pct": 0.03,                   # 3% (>2%)
        "ma20_trend": "ascending",
        "institutional_buy": 100_000,
        "foreign_buy": 50_000,
        "has_event": False,
        "is_managed": False,
        "score": 0.0,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Stage 1: 기본 필터 테스트
# ---------------------------------------------------------------------------

class TestBasicFilter:
    def test_basic_filter_removes_small_cap(self, screener):
        """시가총액 3000억 미만 종목은 기본 필터에서 제거된다."""
        candidates = [
            _make_candidate(ticker="AAA", market_cap=100_000_000_000),   # 1000억 — 탈락
            _make_candidate(ticker="BBB", market_cap=300_000_000_000),   # 3000억 — 경계값 통과
            _make_candidate(ticker="CCC", market_cap=299_999_999_999),   # 3000억 미만 — 탈락
            _make_candidate(ticker="DDD", market_cap=500_000_000_000),   # 5000억 — 통과
        ]
        result = screener._filter_basic(candidates)
        tickers = [r["ticker"] for r in result]

        assert "AAA" not in tickers
        assert "CCC" not in tickers
        assert "BBB" in tickers
        assert "DDD" in tickers

    def test_basic_filter_removes_low_avg_volume_amount(self, screener):
        """일평균 거래대금 50억 미만 종목은 기본 필터에서 제거된다."""
        candidates = [
            _make_candidate(ticker="AAA", avg_volume_amount=1_000_000_000),   # 10억 — 탈락
            _make_candidate(ticker="BBB", avg_volume_amount=5_000_000_000),   # 50억 — 통과
            _make_candidate(ticker="CCC", avg_volume_amount=4_999_999_999),   # 50억 미만 — 탈락
        ]
        result = screener._filter_basic(candidates)
        tickers = [r["ticker"] for r in result]

        assert "AAA" not in tickers
        assert "CCC" not in tickers
        assert "BBB" in tickers

    def test_basic_filter_removes_managed_stocks(self, screener):
        """관리/투자주의 종목(is_managed=True)은 기본 필터에서 제거된다."""
        candidates = [
            _make_candidate(ticker="MANAGED", is_managed=True),
            _make_candidate(ticker="NORMAL", is_managed=False),
        ]
        result = screener._filter_basic(candidates)
        tickers = [r["ticker"] for r in result]

        assert "MANAGED" not in tickers
        assert "NORMAL" in tickers


# ---------------------------------------------------------------------------
# Stage 2: 기술 필터 테스트
# ---------------------------------------------------------------------------

class TestTechnicalFilter:
    def test_technical_filter_removes_low_volume(self, screener):
        """거래량 급증 50% 미달 종목은 기술 필터에서 제거된다."""
        candidates = [
            _make_candidate(ticker="SURGE", volume=3_000_000, prev_volume=1_500_000),   # ratio=2.0 통과
            _make_candidate(ticker="FLAT",  volume=1_000_000, prev_volume=1_000_000),   # ratio=1.0 탈락
            _make_candidate(ticker="SMALL", volume=1_400_000, prev_volume=1_000_000),   # ratio=1.4 탈락
            _make_candidate(ticker="EDGE",  volume=1_500_000, prev_volume=1_000_000),   # ratio=1.5 통과
        ]
        result = screener._filter_technical(candidates)
        tickers = [r["ticker"] for r in result]

        assert "SURGE" in tickers
        assert "EDGE" in tickers
        assert "FLAT" not in tickers
        assert "SMALL" not in tickers

    def test_technical_filter_removes_non_ascending_ma20(self, screener):
        """MA20 비상향(flat/descending) 종목은 기술 필터에서 제거된다."""
        candidates = [
            _make_candidate(ticker="ASC",  ma20_trend="ascending"),
            _make_candidate(ticker="FLAT", ma20_trend="flat"),
            _make_candidate(ticker="DESC", ma20_trend="descending"),
        ]
        result = screener._filter_technical(candidates)
        tickers = [r["ticker"] for r in result]

        assert "ASC" in tickers
        assert "FLAT" not in tickers
        assert "DESC" not in tickers

    def test_technical_filter_removes_low_atr(self, screener):
        """ATR 2% 미달 종목은 기술 필터에서 제거된다."""
        candidates = [
            _make_candidate(ticker="HIGH_ATR", atr_pct=0.03),   # 3% 통과
            _make_candidate(ticker="LOW_ATR",  atr_pct=0.01),   # 1% 탈락
            _make_candidate(ticker="EDGE_ATR", atr_pct=0.02),   # 2% 경계값 통과
        ]
        result = screener._filter_technical(candidates)
        tickers = [r["ticker"] for r in result]

        assert "HIGH_ATR" in tickers
        assert "EDGE_ATR" in tickers
        assert "LOW_ATR" not in tickers


# ---------------------------------------------------------------------------
# Stage 4: 이벤트 필터 테스트
# ---------------------------------------------------------------------------

class TestEventFilter:
    def test_event_filter_excludes(self, screener):
        """has_event=True 종목은 이벤트 필터에서 제거된다."""
        candidates = [
            _make_candidate(ticker="EVENT",  has_event=True),
            _make_candidate(ticker="NORMAL", has_event=False),
        ]
        result = screener._filter_event(candidates)
        tickers = [r["ticker"] for r in result]

        assert "EVENT" not in tickers
        assert "NORMAL" in tickers

    def test_event_filter_passes_all_when_no_events(self, screener):
        """이벤트 없는 후보들은 전부 통과한다."""
        candidates = [
            _make_candidate(ticker="A", has_event=False),
            _make_candidate(ticker="B", has_event=False),
        ]
        result = screener._filter_event(candidates)
        assert len(result) == 2


# ---------------------------------------------------------------------------
# 수급 필터 테스트
# ---------------------------------------------------------------------------

class TestSupplyFilter:
    def test_supply_adds_score_for_institutional_buy(self, screener):
        """기관 순매수 종목에 점수가 추가된다."""
        candidates = [
            _make_candidate(ticker="INST", institutional_buy=100_000, foreign_buy=0, score=0.0),
            _make_candidate(ticker="NONE", institutional_buy=0, foreign_buy=0, score=0.0),
        ]
        result = screener._filter_supply(candidates)
        scores = {r["ticker"]: r["score"] for r in result}

        assert scores["INST"] > scores["NONE"]
        assert scores["INST"] == 2.0
        assert scores["NONE"] == 0.0

    def test_supply_adds_score_for_foreign_buy(self, screener):
        """외국인 순매수 종목에 추가 가산점이 붙는다."""
        candidates = [
            _make_candidate(ticker="BOTH",   institutional_buy=100_000, foreign_buy=50_000, score=0.0),
            _make_candidate(ticker="INST",   institutional_buy=100_000, foreign_buy=0,      score=0.0),
        ]
        result = screener._filter_supply(candidates)
        scores = {r["ticker"]: r["score"] for r in result}

        assert scores["BOTH"] > scores["INST"]
        assert scores["BOTH"] == 3.0   # 기관 2.0 + 외국인 1.0
        assert scores["INST"] == 2.0


# ---------------------------------------------------------------------------
# 전체 흐름 통합 테스트
# ---------------------------------------------------------------------------

class TestFullScreen:
    @pytest.mark.asyncio
    async def test_full_screen_returns_ranked(self, screener):
        """end-to-end: mock 데이터로 필터 적용 후 상위 후보가 점수 순 반환된다."""
        candidates = [
            # 기관+외국인 매수 — 최고 점수 예상
            _make_candidate(ticker="TOP",     institutional_buy=200_000, foreign_buy=100_000),
            # 기관만
            _make_candidate(ticker="MID",     institutional_buy=100_000, foreign_buy=0),
            # 수급 없음
            _make_candidate(ticker="LOW",     institutional_buy=0,       foreign_buy=0),
            # 시가총액 미달 — 탈락
            _make_candidate(ticker="SMALLCAP", market_cap=100_000_000_000),
            # 거래량 급증 미달 — 탈락
            _make_candidate(ticker="NOVOL",   volume=1_000_000, prev_volume=1_000_000),
            # 이벤트 — 탈락
            _make_candidate(ticker="EVENT",   has_event=True),
        ]

        results = await screener.screen(candidates)

        tickers = [r["ticker"] for r in results]
        assert "SMALLCAP" not in tickers
        assert "NOVOL" not in tickers
        assert "EVENT" not in tickers

        assert "TOP" in tickers
        assert "MID" in tickers
        assert "LOW" in tickers

        # 점수 순 정렬 검증
        assert results[0]["ticker"] == "TOP"
        assert results[1]["ticker"] == "MID"
        assert results[2]["ticker"] == "LOW"

    @pytest.mark.asyncio
    async def test_full_screen_max_10_results(self, screener):
        """후보가 10개 초과여도 최대 10개만 반환된다."""
        candidates = [_make_candidate(ticker=str(i)) for i in range(20)]
        results = await screener.screen(candidates)
        assert len(results) <= 10

    @pytest.mark.asyncio
    async def test_full_screen_empty_candidates(self, screener):
        """후보가 없으면 빈 리스트를 반환한다."""
        results = await screener.screen([])
        assert results == []


# ---------------------------------------------------------------------------
# save_results 테스트
# ---------------------------------------------------------------------------

class TestSaveResults:
    @pytest.mark.asyncio
    async def test_save_results_calls_db_execute(self, config):
        """save_results가 각 종목마다 DB execute를 호출한다."""
        executed: list[tuple] = []

        class FakeDb:
            async def execute(self, sql, params=()):
                executed.append(params)
                return 1

        screener = PreMarketScreener(rest_client=None, db=FakeDb(), config=config)
        results = [
            _make_candidate(ticker="A", score=3.0),
            _make_candidate(ticker="B", score=2.0),
        ]
        await screener.save_results("2026-03-23", results)

        assert len(executed) == 2
        # 첫 번째 저장 항목이 "A"인지 확인
        assert executed[0][1] == "A"
        assert executed[0][2] == 3.0
        assert executed[1][1] == "B"
        assert executed[1][2] == 2.0

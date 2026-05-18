"""tests/test_intraday_search.py — 장중 조건검색(intraday_search) 테스트."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pipeline.screener_scheduler import ScreenerScheduler
from pipeline.trading_state import TradingState


def _run(coro):
    """동기 컨텍스트에서 코루틴 실행."""
    return asyncio.run(coro)


def _make_daily_response(ticker: str) -> dict:
    """get_daily_ohlcv mock 응답 (최소 20봉)."""
    rows = []
    for i in range(20):
        rows.append({
            "high_pric": str(10000 + i * 10),
            "low_pric": str(9000 + i * 10),
            "cur_prc": str(9500 + i * 10),
            "trde_qty": "100000",
        })
    return {"stk_dt_pole_chart_qry": rows}


def _make_scheduler(
    state: TradingState | None = None,
    is_enabled: bool = True,
    max_add_per_search: int = 10,
    max_total_added: int = 30,
) -> ScreenerScheduler:
    """테스트용 ScreenerScheduler 생성."""
    if state is None:
        state = TradingState()

    is_cfg = MagicMock()
    is_cfg.enabled = is_enabled
    is_cfg.condition_name = "intraday_leader"
    is_cfg.max_add_per_search = max_add_per_search
    is_cfg.max_total_added = max_total_added

    config = MagicMock()
    config.intraday_search = is_cfg
    config.kiwoom.ws_url = "wss://test"
    config.trading.max_trades_per_day = 1
    config.trading.cooldown_minutes = 999

    async def _daily_mock(ticker, **kw):
        return _make_daily_response(ticker)

    rest_client = MagicMock()
    rest_client.get_daily_ohlcv = _daily_mock

    token_manager = MagicMock()
    token_manager.get_token = AsyncMock(return_value="test_token")

    ws_client = MagicMock()
    ws_client._subscriptions = {"0B": []}
    ws_client.subscribe = AsyncMock()
    ws_client.unsubscribe = AsyncMock()
    ws_client.disconnect = AsyncMock()
    ws_client.connect = AsyncMock()

    notifier = MagicMock()
    notifier.send = MagicMock()

    scheduler = ScreenerScheduler(
        rest_client=rest_client,
        token_manager=token_manager,
        ws_client=ws_client,
        config=config,
        notifier=notifier,
        db=MagicMock(),
        candidate_collector=MagicMock(),
        pre_market_screener=MagicMock(),
        state=state,
    )
    return scheduler


# ─── 기능 비활성화 ──────────────────────────────────────────────────────────

def test_disabled_returns_empty():
    """enabled: false → 빈 리스트 반환, REST 미호출."""
    scheduler = _make_scheduler(is_enabled=False)
    result = _run(scheduler.run_intraday_search())
    assert result == []


def test_no_intraday_search_attr_returns_empty():
    """config.intraday_search = None → 빈 리스트 반환."""
    scheduler = _make_scheduler()
    scheduler._config.intraday_search = None
    result = _run(scheduler.run_intraday_search())
    assert result == []


# ─── 총 추가 한도 ──────────────────────────────────────────────────────────

def test_max_total_added_reached_skips():
    """max_total_added 도달 시 검색 스킵."""
    state = TradingState()
    state.intraday_add_count = 30
    scheduler = _make_scheduler(state=state, max_total_added=30)
    result = _run(scheduler.run_intraday_search())
    assert result == []
    scheduler._token_manager.get_token.assert_not_called()


# ─── 정상 흐름 ─────────────────────────────────────────────────────────────

def test_normal_flow_adds_new_tickers():
    """조건검색 결과 3종목 → enrichment 성공 → active_strategies에 추가."""
    state = TradingState()
    scheduler = _make_scheduler(state=state)

    cs_results = [
        {"code": "000001", "name": "종목A"},
        {"code": "000002", "name": "종목B"},
        {"code": "000003", "name": "종목C"},
    ]

    with patch("core.condition_search.run_condition_search", new_callable=AsyncMock) as mock_cs:
        mock_cs.return_value = cs_results
        with patch.object(scheduler, "ensure_market_codes_cache", new_callable=AsyncMock) as mock_mc:
            mock_mc.return_value = None
            result = _run(scheduler.run_intraday_search())

    assert len(result) == 3
    assert all(s["ticker"] in state.active_strategies for s in result)
    assert state.intraday_add_count == 3
    assert state.intraday_added_tickers == {"000001", "000002", "000003"}
    for ticker in ["000001", "000002", "000003"]:
        assert state.ticker_sources[ticker] == "intraday_leader"


# ─── 중복 제거 ─────────────────────────────────────────────────────────────

def test_duplicates_skipped():
    """기존 감시 중인 종목은 신규로 처리하지 않음."""
    state = TradingState()
    state.active_strategies["000001"] = {"strategy": MagicMock(), "name": "기존종목", "score": 0}

    scheduler = _make_scheduler(state=state)
    cs_results = [
        {"code": "000001", "name": "기존종목"},
        {"code": "000002", "name": "신규종목"},
    ]

    with patch("core.condition_search.run_condition_search", new_callable=AsyncMock) as mock_cs:
        mock_cs.return_value = cs_results
        with patch.object(scheduler, "ensure_market_codes_cache", new_callable=AsyncMock) as mock_mc:
            mock_mc.return_value = None
            result = _run(scheduler.run_intraday_search())

    assert len(result) == 1
    assert result[0]["ticker"] == "000002"
    assert "000001" in state.active_strategies  # 기존 전략 유지


# ─── max_add_per_search 제한 ───────────────────────────────────────────────

def test_max_add_per_search_respected():
    """max_add_per_search=2 → 후보 5종목이어도 2개만 추가."""
    state = TradingState()
    scheduler = _make_scheduler(state=state, max_add_per_search=2)

    cs_results = [{"code": f"00000{i}", "name": f"종목{i}"} for i in range(5)]

    with patch("core.condition_search.run_condition_search", new_callable=AsyncMock) as mock_cs:
        mock_cs.return_value = cs_results
        with patch.object(scheduler, "ensure_market_codes_cache", new_callable=AsyncMock) as mock_mc:
            mock_mc.return_value = None
            result = _run(scheduler.run_intraday_search())

    assert len(result) == 2
    assert state.intraday_add_count == 2


# ─── max_total_added 남은 슬롯 ────────────────────────────────────────────

def test_remaining_total_limit_applied():
    """남은 총 한도 = 2인데 per_search=10 → 2개만 추가."""
    state = TradingState()
    state.intraday_add_count = 28
    scheduler = _make_scheduler(state=state, max_add_per_search=10, max_total_added=30)

    cs_results = [{"code": f"00000{i}", "name": f"종목{i}"} for i in range(5)]

    with patch("core.condition_search.run_condition_search", new_callable=AsyncMock) as mock_cs:
        mock_cs.return_value = cs_results
        with patch.object(scheduler, "ensure_market_codes_cache", new_callable=AsyncMock) as mock_mc:
            mock_mc.return_value = None
            result = _run(scheduler.run_intraday_search())

    assert len(result) == 2
    assert state.intraday_add_count == 30


# ─── WS 한도 초과 시 교체 ─────────────────────────────────────────────────

def test_ws_limit_exceeded_removes_old_intraday():
    """WS 100종목 한도 초과 시 기존 intraday 종목 교체."""
    state = TradingState()
    old_intraday = ["B00001", "B00002"]
    existing_subs = [f"A{i:05d}" for i in range(98)]  # 98종목 구독 중 (96 일반 + 2 intraday)

    for t in old_intraday:
        state.intraday_added_tickers.add(t)
        state.active_strategies[t] = {"strategy": MagicMock(), "name": t, "score": 0}
        state.ticker_sources[t] = "intraday_leader"

    scheduler = _make_scheduler(state=state)
    scheduler._ws_client._subscriptions["0B"] = existing_subs + old_intraday

    removed = []
    original_unsub = scheduler._ws_client.unsubscribe

    async def _capture_unsub(tickers, real_type):
        removed.extend(tickers)

    scheduler._ws_client.unsubscribe = _capture_unsub

    # 새로 3종목 추가 시도 (여유 0 → 3종목 교체 필요, but intraday 후보는 2종목)
    new_tickers = ["C00001", "C00002", "C00003"]
    _run(scheduler._manage_ws_subscriptions(new_tickers))

    # 교체된 종목은 intraday 후보(B00001, B00002) 중에서 나옴
    for t in removed:
        assert t in old_intraday
    scheduler._ws_client.subscribe.assert_called_once_with(new_tickers, "0B")


# ─── enrichment 실패 스킵 ─────────────────────────────────────────────────

def test_enrichment_failure_skips_ticker():
    """enrichment 실패 종목은 스킵, 나머지는 정상 추가."""
    state = TradingState()
    scheduler = _make_scheduler(state=state)

    async def _daily_with_fail(ticker, **kw):
        if ticker == "FAIL01":
            raise Exception("API 오류")
        return _make_daily_response(ticker)

    scheduler._rest_client.get_daily_ohlcv = _daily_with_fail

    cs_results = [
        {"code": "FAIL01", "name": "실패종목"},
        {"code": "000002", "name": "성공종목"},
    ]

    with patch("core.condition_search.run_condition_search", new_callable=AsyncMock) as mock_cs:
        mock_cs.return_value = cs_results
        with patch.object(scheduler, "ensure_market_codes_cache", new_callable=AsyncMock) as mock_mc:
            mock_mc.return_value = None
            result = _run(scheduler.run_intraday_search())

    assert len(result) == 1
    assert result[0]["ticker"] == "000002"
    assert "FAIL01" not in state.active_strategies


# ─── daily_reset 후 상태 초기화 ───────────────────────────────────────────

def test_register_active_strategies_resets_intraday_state():
    """register_active_strategies 호출 시 intraday 상태 초기화."""
    state = TradingState()
    state.intraday_added_tickers.add("OLD01")
    state.intraday_add_count = 5
    state.ticker_sources["OLD01"] = "intraday_leader"

    scheduler = _make_scheduler(state=state)
    stocks = [{"ticker": "NEW01", "name": "새종목", "market": "kosdaq"}]
    scheduler.register_active_strategies(stocks)

    assert len(state.intraday_added_tickers) == 0
    assert state.intraday_add_count == 0
    assert state.ticker_sources.get("NEW01") == "day_momentum"
    assert "OLD01" not in state.active_strategies


# ─── OHLCV 콜백 ───────────────────────────────────────────────────────────

def test_refresh_ohlcv_fn_called_with_added_tickers():
    """run_intraday_search 후 refresh_ohlcv_fn이 추가 종목 ticker로 호출됨."""
    state = TradingState()
    scheduler = _make_scheduler(state=state)

    cs_results = [{"code": "000001", "name": "종목A"}]
    refresh_called_with = []

    async def _refresh_fn(stocks):
        refresh_called_with.extend(s["ticker"] for s in stocks)

    with patch("core.condition_search.run_condition_search", new_callable=AsyncMock) as mock_cs:
        mock_cs.return_value = cs_results
        with patch.object(scheduler, "ensure_market_codes_cache", new_callable=AsyncMock) as mock_mc:
            mock_mc.return_value = None
            _run(scheduler.run_intraday_search(refresh_ohlcv_fn=_refresh_fn))

    assert "000001" in refresh_called_with

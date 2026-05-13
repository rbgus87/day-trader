"""tests/test_obi_filter_integration.py — OBI 필터 통합 테스트.

엔진 시그널 컨슈머 수준에서 OBI 필터가 올바르게 동작하는지 확인.
OrderbookManager를 직접 조작하여 OBI/스프레드/매도벽 차단을 검증.
"""

import pytest
from datetime import datetime
from unittest.mock import MagicMock, AsyncMock, patch
from core.orderbook import (
    OrderbookManager,
    OrderbookSnapshot,
    _OD_BID_PRICE_FIELDS,
    _OD_BID_VOLUME_FIELDS,
    _OD_ASK_PRICE_FIELDS,
    _OD_ASK_VOLUME_FIELDS,
)


def _make_snapshot(
    ticker: str = "005930",
    bid_volumes=None,
    ask_volumes=None,
    bid_prices=None,
    ask_prices=None,
) -> OrderbookSnapshot:
    return OrderbookSnapshot(
        ticker=ticker,
        timestamp=datetime.now(),
        bid_prices=bid_prices or [10000] * 10,
        bid_volumes=bid_volumes or [100] * 10,
        ask_prices=ask_prices or [10050] * 10,
        ask_volumes=ask_volumes or [100] * 10,
    )


def _make_mgr_with_snap(snap: OrderbookSnapshot) -> OrderbookManager:
    mgr = OrderbookManager()
    mgr._snapshots[snap.ticker] = snap
    return mgr


# ── OBI >= 0.55 → 진입 허용 ──


def test_obi_above_threshold_allows_entry():
    """OBI 0.7 → 진입 허용 (필터 통과)."""
    snap = _make_snapshot(bid_volumes=[700] * 10, ask_volumes=[300] * 10)
    mgr = _make_mgr_with_snap(snap)
    obi = mgr.get_obi("005930")
    assert obi is not None
    assert obi >= 0.55  # 진입 허용


# ── OBI < 0.55 → 진입 차단 ──


def test_obi_below_threshold_blocks_entry():
    """OBI 0.3 → 진입 차단."""
    snap = _make_snapshot(bid_volumes=[300] * 10, ask_volumes=[700] * 10)
    mgr = _make_mgr_with_snap(snap)
    obi = mgr.get_obi("005930")
    assert obi is not None
    assert obi < 0.55  # 진입 차단


# ── 0D 미수신 시 → 필터 비적용 ──


def test_obi_none_when_no_snapshot_allows_entry():
    """스냅샷 없으면 OBI=None → 진입 허용."""
    mgr = OrderbookManager()
    obi = mgr.get_obi("005930")
    assert obi is None  # 필터 미적용


# ── 스프레드 초과 → 진입 차단 ──


def test_spread_above_threshold_blocks_entry():
    """스프레드 1% > 0.5% → 차단."""
    snap = _make_snapshot(
        bid_prices=[10000] + [9990] * 9,
        ask_prices=[10100] + [10110] * 9,
    )
    mgr = _make_mgr_with_snap(snap)
    spread = mgr.get_spread("005930")
    assert spread is not None
    assert spread > 0.005  # 차단 조건


def test_spread_below_threshold_allows_entry():
    """스프레드 0.3% < 0.5% → 통과."""
    snap = _make_snapshot(
        bid_prices=[10000] + [9990] * 9,
        ask_prices=[10030] + [10040] * 9,
    )
    mgr = _make_mgr_with_snap(snap)
    spread = mgr.get_spread("005930")
    assert spread is not None
    assert spread <= 0.005  # 통과


# ── 매도벽 감지 → 진입 차단 ──


def test_ask_wall_within_range_blocks_entry():
    """현재가 근처 3% 이내 매도벽 → 차단."""
    near_price = 10000
    ask_vols = [100] * 9 + [600]  # 마지막 단계에 매도벽
    ask_prices = [10050, 10060, 10070, 10080, 10090,
                  10100, 10110, 10120, 10130, 10200]
    snap = _make_snapshot(
        ask_prices=ask_prices,
        ask_volumes=ask_vols,
        bid_prices=[9990] * 10,
        bid_volumes=[200] * 10,
    )
    mgr = _make_mgr_with_snap(snap)
    # ask_wall 존재 여부 확인
    wall = snap.ask_wall
    if wall is not None:
        wall_price, _ = wall
        # 현재가 기준 3% 이내인지 확인
        in_range = abs(wall_price - near_price) / near_price <= 0.03
        # has_ask_wall 결과와 일치해야 함
        assert mgr.has_ask_wall("005930", near_price=near_price) == in_range


def test_ask_wall_out_of_range_allows_entry():
    """매도벽이 현재가에서 5% 떨어져 있으면 → 통과."""
    near_price = 10000
    ask_vols = [100] * 9 + [600]
    ask_prices = [10050, 10060, 10070, 10080, 10090,
                  10100, 10110, 10120, 10130, 10600]  # 마지막이 6%로 범위 밖
    snap = _make_snapshot(
        ask_prices=ask_prices,
        ask_volumes=ask_vols,
        bid_prices=[9990] * 10,
        bid_volumes=[200] * 10,
    )
    mgr = _make_mgr_with_snap(snap)
    wall = snap.ask_wall
    if wall is not None:
        wall_price, _ = wall
        assert abs(wall_price - near_price) / near_price > 0.03
        assert not mgr.has_ask_wall("005930", near_price=near_price, range_pct=0.03)


# ── 매도 시 OBI 미체크 검증 (스냅샷 조회만 테스트) ──


def test_obi_independent_per_ticker():
    """OBI 필터는 종목별 독립."""
    mgr = OrderbookManager()
    snap_a = _make_snapshot("000660", bid_volumes=[700] * 10, ask_volumes=[300] * 10)
    snap_b = _make_snapshot("005380", bid_volumes=[100] * 10, ask_volumes=[900] * 10)
    mgr._snapshots["000660"] = snap_a
    mgr._snapshots["005380"] = snap_b

    assert mgr.get_obi("000660") > 0.55   # 매수 우위
    assert mgr.get_obi("005380") < 0.55   # 매도 우위
    assert mgr.get_obi("999999") is None  # 미수신


# ── kiwoom_ws _dispatch_orderbook 단위 ──


def test_ws_dispatch_orderbook_calls_manager():
    """_dispatch_orderbook이 OrderbookManager.update를 호출하는지 확인."""
    import asyncio
    from unittest.mock import MagicMock
    from core.kiwoom_ws import KiwoomWebSocketClient, WS_TYPE_ORDERBOOK

    mgr = MagicMock()
    ws = KiwoomWebSocketClient(
        ws_url="ws://test",
        token_manager=MagicMock(),
        orderbook_manager=mgr,
    )
    data = {
        "type": WS_TYPE_ORDERBOOK,
        "item": "005930",
        "values": {"41": "10050", "42": "10000"},
    }
    ws._dispatch_orderbook(data)
    mgr.update.assert_called_once_with("005930", {"41": "10050", "42": "10000"})


def test_ws_dispatch_orderbook_no_manager():
    """orderbook_manager=None 이면 _dispatch_orderbook이 조용히 무시."""
    from core.kiwoom_ws import KiwoomWebSocketClient

    ws = KiwoomWebSocketClient(
        ws_url="ws://test",
        token_manager=MagicMock(),
    )
    # 예외 없이 실행돼야 함
    ws._dispatch_orderbook({"type": "0D", "item": "005930", "values": {}})


def test_ws_dispatch_orderbook_empty_ticker():
    """item 없는 메시지 → update 미호출."""
    from core.kiwoom_ws import KiwoomWebSocketClient

    mgr = MagicMock()
    ws = KiwoomWebSocketClient(
        ws_url="ws://test",
        token_manager=MagicMock(),
        orderbook_manager=mgr,
    )
    ws._dispatch_orderbook({"type": "0D", "item": "", "values": {}})
    mgr.update.assert_not_called()

"""tests/test_orderbook.py — OrderbookSnapshot / OrderbookManager 단위 테스트."""

import pytest
from datetime import datetime

from core.orderbook import (
    OrderbookSnapshot,
    OrderbookManager,
    _OD_BID_PRICE_FIELDS,
    _OD_BID_VOLUME_FIELDS,
    _OD_ASK_PRICE_FIELDS,
    _OD_ASK_VOLUME_FIELDS,
)


def _make_snapshot(
    bid_prices=None, bid_volumes=None, ask_prices=None, ask_volumes=None
) -> OrderbookSnapshot:
    return OrderbookSnapshot(
        ticker="005930",
        timestamp=datetime.now(),
        bid_prices=bid_prices or [0] * 10,
        bid_volumes=bid_volumes or [0] * 10,
        ask_prices=ask_prices or [0] * 10,
        ask_volumes=ask_volumes or [0] * 10,
    )


# ── OBI 계산 ──


def test_obi_balanced():
    snap = _make_snapshot(
        bid_volumes=[100] * 10,
        ask_volumes=[100] * 10,
    )
    assert snap.obi == pytest.approx(0.5)


def test_obi_bid_heavy():
    snap = _make_snapshot(
        bid_volumes=[300] * 10,
        ask_volumes=[100] * 10,
    )
    assert snap.obi == pytest.approx(300 * 10 / (300 * 10 + 100 * 10))
    assert snap.obi > 0.5


def test_obi_ask_heavy():
    snap = _make_snapshot(
        bid_volumes=[100] * 10,
        ask_volumes=[400] * 10,
    )
    assert snap.obi < 0.5


def test_obi_all_zero_returns_half():
    snap = _make_snapshot()
    assert snap.obi == 0.5


# ── spread 계산 ──


def test_spread_pct():
    snap = _make_snapshot(
        bid_prices=[10000] + [0] * 9,
        ask_prices=[10050] + [0] * 9,
    )
    assert snap.spread_pct == pytest.approx(0.005)


def test_spread_zero_bid_returns_zero():
    snap = _make_snapshot(
        bid_prices=[0] * 10,
        ask_prices=[10050] + [0] * 9,
    )
    assert snap.spread_pct == 0.0


# ── ask_wall 감지 ──


def test_ask_wall_detected():
    """평균의 5배 이상인 잔량 → 매도벽.

    9단계 50주, 1단계 1000주:
      avg = (50×9 + 1000) / 10 = 1450/10 = 145
      threshold = 145 × 5 = 725
      1000 >= 725 → 매도벽 감지
    """
    vols = [50] * 9 + [1000]
    snap = _make_snapshot(
        ask_prices=list(range(10100, 10200, 10)),
        ask_volumes=vols,
    )
    wall = snap.ask_wall
    assert wall is not None
    assert wall[1] == 1000


def test_ask_wall_not_detected():
    vols = [100] * 10
    snap = _make_snapshot(ask_volumes=vols)
    assert snap.ask_wall is None


def test_ask_wall_empty_volumes():
    snap = _make_snapshot()
    assert snap.ask_wall is None


# ── OrderbookManager ──


def _make_values(bid_p, bid_v, ask_p, ask_v):
    """필드 코드 기반 values dict 생성 (10단계 균일)."""
    values = {}
    for i, f in enumerate(_OD_BID_PRICE_FIELDS):
        values[f] = str(bid_p[i] if i < len(bid_p) else 0)
    for i, f in enumerate(_OD_BID_VOLUME_FIELDS):
        values[f] = str(bid_v[i] if i < len(bid_v) else 0)
    for i, f in enumerate(_OD_ASK_PRICE_FIELDS):
        values[f] = str(ask_p[i] if i < len(ask_p) else 0)
    for i, f in enumerate(_OD_ASK_VOLUME_FIELDS):
        values[f] = str(ask_v[i] if i < len(ask_v) else 0)
    return values


def test_manager_update_and_get_obi():
    mgr = OrderbookManager()
    values = _make_values(
        bid_p=[10000] * 10, bid_v=[200] * 10,
        ask_p=[10050] * 10, ask_v=[100] * 10,
    )
    mgr.update("005930", values)
    obi = mgr.get_obi("005930")
    assert obi is not None
    assert obi > 0.5  # 매수 우위


def test_manager_get_obi_missing_ticker():
    mgr = OrderbookManager()
    assert mgr.get_obi("999999") is None


def test_manager_get_spread():
    mgr = OrderbookManager()
    values = _make_values(
        bid_p=[10000] * 10, bid_v=[100] * 10,
        ask_p=[10050] + [0] * 9, ask_v=[100] * 10,
    )
    mgr.update("005930", values)
    spread = mgr.get_spread("005930")
    assert spread == pytest.approx(0.005)


def test_manager_get_spread_missing_ticker():
    mgr = OrderbookManager()
    assert mgr.get_spread("999999") is None


def test_manager_has_ask_wall_true():
    """OrderbookManager에서 ask_wall 감지 후 has_ask_wall 반환 확인.

    9단계 50주, 1단계(10140원) 1000주:
      avg=145, threshold=725, 1000>=725 → 매도벽
      near_price=10000 기준 3% 이내: 10140/10000-1=1.4% → True
    """
    mgr = OrderbookManager()
    ask_vols = [50] * 9 + [1000]
    ask_prices_list = list(range(10050, 10150, 10))  # 10050~10140
    values = _make_values(
        bid_p=[10000] * 10, bid_v=[100] * 10,
        ask_p=ask_prices_list, ask_v=ask_vols,
    )
    mgr.update("005930", values)
    snap = mgr.get_snapshot("005930")
    assert snap is not None
    assert snap.ask_wall is not None
    # 10140은 10000 기준 1.4% → 3% 이내 → has_ask_wall=True
    assert mgr.has_ask_wall("005930", near_price=10000, range_pct=0.03)


def test_manager_has_ask_wall_missing_ticker():
    mgr = OrderbookManager()
    assert mgr.has_ask_wall("999999", near_price=10000) is False


def test_manager_update_overrides_previous():
    mgr = OrderbookManager()
    v1 = _make_values([10000] * 10, [100] * 10, [10050] * 10, [100] * 10)
    v2 = _make_values([10010] * 10, [500] * 10, [10060] * 10, [50] * 10)
    mgr.update("005930", v1)
    mgr.update("005930", v2)
    snap = mgr.get_snapshot("005930")
    assert snap.bid_prices[0] == 10010
    assert snap.bid_volumes[0] == 500


def test_manager_update_handles_empty_values():
    """빈 values dict에서 파싱 실패 없이 0으로 채워야 함."""
    mgr = OrderbookManager()
    mgr.update("005930", {})
    snap = mgr.get_snapshot("005930")
    assert snap is not None
    assert all(p == 0 for p in snap.bid_prices)
    assert snap.obi == 0.5

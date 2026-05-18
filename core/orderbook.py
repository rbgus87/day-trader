"""core/orderbook.py — 호가(OrderBook) 스냅샷 + OBI 필터.

키움 WS 0D 메시지 기반. 실시간 전용 — 백테스트 영향 없음.
"""

from dataclasses import dataclass, field
from datetime import datetime

# 키움 0D 호가 메시지 필드 코드 (공식 문서 확정)
_OD_ASK_PRICE_FIELDS  = ("41", "42", "43", "44", "45", "46", "47", "48", "49", "50")  # 매도 1~10호가
_OD_BID_PRICE_FIELDS  = ("51", "52", "53", "54", "55", "56", "57", "58", "59", "60")  # 매수 1~10호가
_OD_ASK_VOLUME_FIELDS = ("61", "62", "63", "64", "65", "66", "67", "68", "69", "70")  # 매도 1~10잔량
_OD_BID_VOLUME_FIELDS = ("71", "72", "73", "74", "75", "76", "77", "78", "79", "80")  # 매수 1~10잔량
# 집계 필드
_OD_TOTAL_ASK_VOL = "121"  # 총 매도잔량
_OD_TOTAL_BID_VOL = "125"  # 총 매수잔량
_OD_NET_BID_VOL   = "128"  # 순매수잔량
_OD_BID_RATIO     = "129"  # 매수비율

_LEVELS = 10


def _parse_int(v: str) -> int:
    """부호/쉼표 제거 후 절댓값 정수 변환."""
    try:
        return abs(int(str(v).replace("+", "").replace("-", "").replace(",", "")))
    except (ValueError, TypeError):
        return 0


@dataclass
class OrderbookSnapshot:
    """호가 스냅샷 — 매수/매도 10단계."""

    ticker: str
    timestamp: datetime
    bid_prices: list[int] = field(default_factory=lambda: [0] * _LEVELS)
    bid_volumes: list[int] = field(default_factory=lambda: [0] * _LEVELS)
    ask_prices: list[int] = field(default_factory=lambda: [0] * _LEVELS)
    ask_volumes: list[int] = field(default_factory=lambda: [0] * _LEVELS)

    @property
    def obi(self) -> float:
        """Order Book Imbalance = 매수잔량 / (매수잔량 + 매도잔량).

        0.5 = 균형, >0.5 = 매수 우위, <0.5 = 매도 우위.
        """
        total_bid = sum(self.bid_volumes)
        total_ask = sum(self.ask_volumes)
        denom = total_bid + total_ask
        if denom == 0:
            return 0.5
        return total_bid / denom

    @property
    def spread_pct(self) -> float:
        """최우선 호가 스프레드 비율 = (매도1 - 매수1) / 매수1."""
        if self.bid_prices[0] <= 0:
            return 0.0
        return (self.ask_prices[0] - self.bid_prices[0]) / self.bid_prices[0]

    @property
    def ask_wall(self) -> tuple[int, int] | None:
        """매도벽 감지: 평균 매도잔량의 5배 이상인 첫 번째 호가 반환."""
        vols = [v for v in self.ask_volumes if v > 0]
        if not vols:
            return None
        avg = sum(vols) / len(vols)
        for i, vol in enumerate(self.ask_volumes):
            if vol >= avg * 5:
                return (self.ask_prices[i], vol)
        return None


class OrderbookManager:
    """종목별 최신 호가 스냅샷 관리."""

    def __init__(self) -> None:
        self._snapshots: dict[str, OrderbookSnapshot] = {}

    def update(self, ticker: str, values: dict) -> None:
        """0D 메시지 values dict → 스냅샷 갱신."""
        bid_prices  = [_parse_int(values.get(f, 0)) for f in _OD_BID_PRICE_FIELDS]
        bid_volumes = [_parse_int(values.get(f, 0)) for f in _OD_BID_VOLUME_FIELDS]
        ask_prices  = [_parse_int(values.get(f, 0)) for f in _OD_ASK_PRICE_FIELDS]
        ask_volumes = [_parse_int(values.get(f, 0)) for f in _OD_ASK_VOLUME_FIELDS]
        self._snapshots[ticker] = OrderbookSnapshot(
            ticker=ticker,
            timestamp=datetime.now(),
            bid_prices=bid_prices,
            bid_volumes=bid_volumes,
            ask_prices=ask_prices,
            ask_volumes=ask_volumes,
        )

    def get_snapshot(self, ticker: str) -> OrderbookSnapshot | None:
        return self._snapshots.get(ticker)

    def get_obi(self, ticker: str) -> float | None:
        snap = self._snapshots.get(ticker)
        return snap.obi if snap is not None else None

    def get_spread(self, ticker: str) -> float | None:
        snap = self._snapshots.get(ticker)
        return snap.spread_pct if snap is not None else None

    def has_ask_wall(self, ticker: str, near_price: float, range_pct: float = 0.03) -> bool:
        """현재가(near_price) 근처 range_pct 이내에 매도벽이 있으면 True."""
        snap = self._snapshots.get(ticker)
        if snap is None:
            return False
        wall = snap.ask_wall
        if wall is None:
            return False
        wall_price, _ = wall
        if near_price <= 0:
            return False
        return abs(wall_price - near_price) / near_price <= range_pct

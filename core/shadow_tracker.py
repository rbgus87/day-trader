"""core/shadow_tracker.py — 시장 필터 차단 시그널 섀도우 트래킹.

시장 필터(market_filter / intraday_market_filter)에 의해 차단된 매수 신호를
메모리에 기록하고, 이후 틱으로 peak/current 가격을 갱신하여
"만약 진입했다면" 결과를 추적한다.

- stop_loss_pct(-8%) 적용: 현재가가 손절가 아래로 내려가면 손절 처리
- DB 저장 없음 (메모리 전용, 일일 리셋)
- 성능: 틱마다 dict get 1회 (섀도우 포지션 없는 종목 즉시 스킵)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class ShadowPosition:
    ticker: str
    signal_price: float
    signal_time: datetime
    reason: str                  # "market_filter" | "intraday_market_filter"
    peak_price: float = 0.0
    current_price: float = 0.0
    stopped_out: bool = False    # -stop_loss_pct 이하로 내려간 적 있으면 True
    stop_loss_pct: float = 0.08  # 8% (양수)

    def update(self, price: float) -> None:
        """틱 가격 갱신 — peak 및 stop_out 여부 업데이트."""
        if self.stopped_out:
            return
        self.current_price = price
        if price > self.peak_price:
            self.peak_price = price
        # 손절가 이하로 내려가면 stop_out 처리
        if self.signal_price > 0 and price <= self.signal_price * (1 - self.stop_loss_pct):
            self.stopped_out = True
            self.current_price = self.signal_price * (1 - self.stop_loss_pct)

    @property
    def realistic_pnl_pct(self) -> float:
        """stop_loss 적용 현실적 PnL%.

        손절이 발동했으면 -stop_loss_pct.
        그 외에는 현재가 기준.
        """
        if self.signal_price <= 0:
            return 0.0
        if self.stopped_out:
            return -self.stop_loss_pct
        ref = self.current_price if self.current_price > 0 else self.signal_price
        return (ref - self.signal_price) / self.signal_price

    @property
    def peak_pnl_pct(self) -> float:
        """최고가 기준 PnL% (참고용)."""
        if self.signal_price <= 0 or self.peak_price <= 0:
            return 0.0
        return (self.peak_price - self.signal_price) / self.signal_price


class ShadowTracker:
    """시장 필터 차단 시그널의 섀도우 PnL 트래커.

    사용:
        tracker = ShadowTracker(stop_loss_pct=0.08)
        tracker.on_blocked("005930", 75000, datetime.now(), "market_filter")
        tracker.update_prices("005930", 78000)
        summary = tracker.get_summary()
    """

    def __init__(self, stop_loss_pct: float = 0.08) -> None:
        self._stop_loss_pct = stop_loss_pct
        # ticker → ShadowPosition (가장 최근 차단 1건만 추적)
        self._positions: dict[str, ShadowPosition] = {}
        # 종료된 섀도우 포지션 (close_all 이후)
        self._closed: list[ShadowPosition] = []

    # ------------------------------------------------------------------

    def on_blocked(
        self,
        ticker: str,
        price: float,
        signal_time: datetime,
        reason: str,
    ) -> None:
        """차단된 매수 신호를 섀도우 포지션으로 등록.

        동일 종목이 이미 추적 중이면 덮어쓴다 (최신 신호 우선).
        """
        self._positions[ticker] = ShadowPosition(
            ticker=ticker,
            signal_price=float(price),
            signal_time=signal_time,
            reason=reason,
            peak_price=float(price),
            current_price=float(price),
            stop_loss_pct=self._stop_loss_pct,
        )

    def update_prices(self, ticker: str, price: float) -> None:
        """틱 가격으로 섀도우 포지션 갱신 (없으면 no-op)."""
        pos = self._positions.get(ticker)
        if pos is not None:
            pos.update(price)

    def close_all(self) -> None:
        """15:10 강제청산 시점에 모든 섀도우 포지션을 종료."""
        self._closed.extend(self._positions.values())
        self._positions.clear()

    def reset(self) -> None:
        """일일 리셋."""
        self._positions.clear()
        self._closed.clear()

    def get_summary(self) -> dict[str, Any]:
        """차단 건 전체 요약 dict 반환.

        Returns:
            {
              "total": int,
              "profit_count": int,   # realistic_pnl_pct > 0
              "loss_count": int,
              "avg_profit_pct": float,
              "avg_loss_pct": float,
              "positions": [
                  {"ticker": str, "signal_price": float, "peak_price": float,
                   "current_price": float, "realistic_pnl_pct": float,
                   "peak_pnl_pct": float, "reason": str, "stopped_out": bool}
              ]
            }
        """
        all_pos = list(self._positions.values()) + list(self._closed)
        if not all_pos:
            return {
                "total": 0,
                "profit_count": 0,
                "loss_count": 0,
                "avg_profit_pct": 0.0,
                "avg_loss_pct": 0.0,
                "positions": [],
            }

        profits = [p.realistic_pnl_pct for p in all_pos if p.realistic_pnl_pct > 0]
        losses  = [p.realistic_pnl_pct for p in all_pos if p.realistic_pnl_pct <= 0]

        return {
            "total": len(all_pos),
            "profit_count": len(profits),
            "loss_count": len(losses),
            "avg_profit_pct": sum(profits) / len(profits) if profits else 0.0,
            "avg_loss_pct": sum(losses) / len(losses) if losses else 0.0,
            "positions": [
                {
                    "ticker": p.ticker,
                    "reason": p.reason,
                    "signal_price": int(p.signal_price),
                    "peak_price": int(p.peak_price),
                    "current_price": int(p.current_price),
                    "realistic_pnl_pct": round(p.realistic_pnl_pct * 100, 2),
                    "peak_pnl_pct": round(p.peak_pnl_pct * 100, 2),
                    "stopped_out": p.stopped_out,
                    "signal_time": p.signal_time.strftime("%H:%M:%S"),
                }
                for p in sorted(all_pos, key=lambda x: x.realistic_pnl_pct, reverse=True)
            ],
        }

    def format_report(self) -> str:
        """텔레그램/로그용 요약 문자열."""
        s = self.get_summary()
        if s["total"] == 0:
            return "[SHADOW] 시장 필터 차단 없음"

        lines = [f"[SHADOW] 시장 필터 차단 {s['total']}건"]
        for p in s["positions"]:
            pnl = p["realistic_pnl_pct"]
            peak = p["peak_pnl_pct"]
            mark = "수익 기회 놓침" if pnl > 0 else "차단 정당"
            stop_mark = " (손절)" if p["stopped_out"] else ""
            lines.append(
                f"  {p['ticker']}: 차단가 {p['signal_price']:,} "
                f"-> 현재 {p['current_price']:,} "
                f"({pnl:+.1f}%){stop_mark} "
                f"[최고 {peak:+.1f}%] <- {mark}"
            )

        lines.append(
            f"수익이었을: {s['profit_count']}/{s['total']}건"
            f"  평균 {s['avg_profit_pct']:+.1f}%"
        )
        if s["loss_count"] > 0:
            lines.append(
                f"손실이었을: {s['loss_count']}/{s['total']}건"
                f"  평균 {s['avg_loss_pct']:+.1f}%"
            )
        return "\n".join(lines)

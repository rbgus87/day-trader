"""core/signal_scorer.py — 시그널 품질 점수 산출기 (100점 만점)."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SignalScore:
    total: float
    components: dict[str, float] = field(default_factory=dict)


class SignalScorer:
    """시그널 품질을 0~100점으로 정량화.

    각 항목을 [lo, hi] → [0, weight] 범위로 선형 보간 (clamp):
      volume_ratio   : 2.0→0pt, 4.0+→25pt
      adx_strength   : 20→0pt, 35+→25pt
      breakout_pct   : 0.03→0pt, 0.08+→20pt
      close_position : 0.97→0pt, 1.00→15pt  (전일 종가/고가)
      atr_normalized : 0.5→0pt, 1.5+→15pt   (breakout_pct / atr_pct)

    데이터 미가용 항목은 0점 처리 (진입 차단 없음, 점수만 낮아짐).
    """

    def __init__(
        self,
        w_volume_ratio: float = 25.0,
        w_adx_strength: float = 25.0,
        w_breakout_pct: float = 20.0,
        w_close_position: float = 15.0,
        w_atr_normalized: float = 15.0,
    ) -> None:
        self._w = {
            "volume_ratio":   w_volume_ratio,
            "adx_strength":   w_adx_strength,
            "breakout_pct":   w_breakout_pct,
            "close_position": w_close_position,
            "atr_normalized": w_atr_normalized,
        }

    @staticmethod
    def _linear(value: float, lo: float, hi: float, weight: float) -> float:
        """value를 [lo, hi] → [0, weight]로 선형 매핑 (양 끝 clamp)."""
        if hi <= lo or weight <= 0:
            return 0.0
        ratio = (value - lo) / (hi - lo)
        return max(0.0, min(weight, ratio * weight))

    def score(self, context: dict) -> SignalScore:
        """시그널 컨텍스트를 점수로 변환.

        context 키:
          volume_ratio   : 당일 누적 거래량 / 전일 거래량 (2.0 이상이어야 진입 허용)
          adx            : ADX(14) 값 (20 이상)
          breakout_pct   : 돌파폭 소수 (0.03 이상)
          close_to_high  : 전일 종가 / 전일 고가 (0~1.0)
          atr_pct        : 당일 평균 ATR% 소수 (미가용 시 0)
        """
        c = components = {}

        # 1) 거래량 배수: 2.0→0, 4.0+→max
        c["volume_ratio"] = self._linear(
            float(context.get("volume_ratio", 0)), 2.0, 4.0, self._w["volume_ratio"]
        )

        # 2) ADX 강도: 20→0, 35+→max
        c["adx_strength"] = self._linear(
            float(context.get("adx", 0)), 20.0, 35.0, self._w["adx_strength"]
        )

        # 3) 돌파폭: 0.03→0, 0.08+→max
        bp = float(context.get("breakout_pct", 0))
        c["breakout_pct"] = self._linear(bp, 0.03, 0.08, self._w["breakout_pct"])

        # 4) 전일 종가/고가 비율: 0.97→0, 1.00→max
        c["close_position"] = self._linear(
            float(context.get("close_to_high", 0)), 0.97, 1.00, self._w["close_position"]
        )

        # 5) ATR 정규화 돌파: breakout_pct/atr_pct, 0.5→0, 1.5+→max
        atr_pct = float(context.get("atr_pct", 0))
        atr_norm = (bp / atr_pct) if atr_pct > 0 else 0.0
        c["atr_normalized"] = self._linear(atr_norm, 0.5, 1.5, self._w["atr_normalized"])

        total = sum(components.values())
        return SignalScore(
            total=round(total, 2),
            components={k: round(v, 2) for k, v in components.items()},
        )

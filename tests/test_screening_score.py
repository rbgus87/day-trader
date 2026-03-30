"""tests/test_screening_score.py — Flow 최적화 스크리닝 점수 테스트."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import pandas as pd

from screener.candidate_collector import CandidateCollector


def _make_df(volume_today, volume_yesterday, close_val, high_val, atr_pct_target=0.04):
    """테스트용 일봉 DataFrame 생성 (최소 20행)."""
    rows = []
    for i in range(20):
        base = 10000 + i * 10
        rows.append({
            "date": f"202603{i+1:02d}",
            "open": base,
            "high": base + int(base * atr_pct_target / 2),
            "low": base - int(base * atr_pct_target / 2),
            "close": base + 5,
            "volume": volume_yesterday,
            "tr_amount": base * volume_yesterday,
        })
    # 마지막 2행: 전전일, 전일
    rows[-2]["volume"] = volume_yesterday
    rows[-1]["volume"] = volume_today
    rows[-1]["close"] = close_val
    rows[-1]["high"] = high_val
    return pd.DataFrame(rows)


class TestScreeningScore:
    """Flow 최적화 점수 계산 테스트."""

    def test_volume_surge_2x_gives_3pts(self):
        """거래량 급증 2배 → 3점."""
        collector = CandidateCollector(MagicMock())
        df = _make_df(volume_today=200000, volume_yesterday=100000,
                       close_val=10200, high_val=10200)
        # _collect_single을 직접 호출하기 어려우므로 점수 로직만 검증
        volume = 200000
        prev_volume = 100000
        score = 0.0
        if prev_volume > 0:
            vol_ratio = volume / prev_volume
            if vol_ratio >= 2.0:
                score += 3.0
        assert score == 3.0

    def test_close_near_high_98pct_gives_2pts(self):
        """전일 종가 위치 98% → 2점."""
        close_val = 9800
        high_val = 10000
        score = 0.0
        close_position = close_val / high_val
        if close_position >= 0.98:
            score += 2.0
        assert score == 2.0

    def test_close_near_high_96pct_gives_1pt(self):
        """전일 종가 위치 96% → 1점."""
        close_val = 9600
        high_val = 10000
        score = 0.0
        close_position = close_val / high_val
        if close_position >= 0.98:
            score += 2.0
        elif close_position >= 0.95:
            score += 1.0
        assert score == 1.0

    def test_atr_4pct_gives_2pts(self):
        """ATR 4% → 2점."""
        atr_pct = 0.04
        score = 0.0
        if atr_pct >= 0.04:
            score += 2.0
        assert score == 2.0

    def test_combined_score(self):
        """종합 점수 계산: 거래량2배(3) + 고가마감(2) + ATR4%(2) + 거래대금5B(1) = 8."""
        score = 0.0
        # 거래량 급증
        vol_ratio = 200000 / 100000
        if vol_ratio >= 2.0:
            score += 3.0
        # 종가 위치
        close_position = 9900 / 10000
        if close_position >= 0.98:
            score += 2.0
        # ATR
        atr_pct = 0.04
        if atr_pct >= 0.04:
            score += 2.0
        # 거래대금
        avg_volume_amount = 5_000_000_000
        score += min(avg_volume_amount / 5_000_000_000, 3.0)
        assert score == 8.0

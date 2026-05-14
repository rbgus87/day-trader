"""tests/test_signal_scorer.py — SignalScorer 단위 테스트."""

import pytest

from core.signal_scorer import SignalScorer, SignalScore


@pytest.fixture
def scorer():
    return SignalScorer()


class TestLinearMapping:
    def test_below_lo_returns_zero(self, scorer):
        result = scorer._linear(1.0, 2.0, 4.0, 25.0)
        assert result == 0.0

    def test_above_hi_returns_weight(self, scorer):
        result = scorer._linear(5.0, 2.0, 4.0, 25.0)
        assert result == 25.0

    def test_midpoint(self, scorer):
        result = scorer._linear(3.0, 2.0, 4.0, 25.0)
        assert abs(result - 12.5) < 1e-9

    def test_hi_equals_lo_returns_zero(self, scorer):
        result = scorer._linear(5.0, 3.0, 3.0, 25.0)
        assert result == 0.0


class TestScoreComponents:
    def test_empty_context_total_is_zero(self, scorer):
        score = scorer.score({})
        assert score.total == 0.0
        assert set(score.components.keys()) == {
            "volume_ratio", "adx_strength", "breakout_pct",
            "close_position", "atr_normalized",
        }

    def test_max_score_all_components_saturated(self, scorer):
        ctx = {
            "volume_ratio": 10.0,   # > 4.0 → max 25
            "adx": 50.0,            # > 35 → max 25
            "breakout_pct": 0.20,   # > 0.08 → max 20
            "close_to_high": 1.00,  # = 1.00 → max 15
            "atr_pct": 0.05,        # bp/atr = 0.20/0.05=4.0 > 1.5 → max 15
        }
        score = scorer.score(ctx)
        assert score.total == 100.0

    def test_volume_ratio_at_lo_gives_zero(self, scorer):
        ctx = {"volume_ratio": 2.0}
        score = scorer.score(ctx)
        assert score.components["volume_ratio"] == 0.0

    def test_volume_ratio_at_hi_gives_max(self, scorer):
        ctx = {"volume_ratio": 4.0}
        score = scorer.score(ctx)
        assert score.components["volume_ratio"] == 25.0

    def test_adx_at_20_gives_zero(self, scorer):
        ctx = {"adx": 20.0}
        score = scorer.score(ctx)
        assert score.components["adx_strength"] == 0.0

    def test_adx_at_35_gives_max(self, scorer):
        ctx = {"adx": 35.0}
        score = scorer.score(ctx)
        assert score.components["adx_strength"] == 25.0

    def test_breakout_pct_partial(self, scorer):
        ctx = {"breakout_pct": 0.055}  # midpoint between 0.03 and 0.08
        score = scorer.score(ctx)
        # (0.055-0.03)/(0.08-0.03) * 20 = 0.025/0.05 * 20 = 10.0
        assert abs(score.components["breakout_pct"] - 10.0) < 1e-6

    def test_atr_normalized_zero_atr_pct(self, scorer):
        ctx = {"breakout_pct": 0.05, "atr_pct": 0.0}
        score = scorer.score(ctx)
        assert score.components["atr_normalized"] == 0.0

    def test_atr_normalized_computed(self, scorer):
        # bp=0.06, atr_pct=0.04 → norm=1.5 → max 15
        ctx = {"breakout_pct": 0.06, "atr_pct": 0.04}
        score = scorer.score(ctx)
        assert score.components["atr_normalized"] == 15.0


class TestScoreReturn:
    def test_returns_signal_score_type(self, scorer):
        result = scorer.score({})
        assert isinstance(result, SignalScore)

    def test_total_rounded_to_two_decimals(self, scorer):
        ctx = {"volume_ratio": 3.0, "adx": 27.5}
        score = scorer.score(ctx)
        # ensure no more than 2 decimal places
        assert score.total == round(score.total, 2)

    def test_components_rounded(self, scorer):
        ctx = {"volume_ratio": 2.5}
        score = scorer.score(ctx)
        for v in score.components.values():
            assert v == round(v, 2)

    def test_custom_weights(self):
        scorer2 = SignalScorer(
            w_volume_ratio=50.0,
            w_adx_strength=50.0,
            w_breakout_pct=0.0,
            w_close_position=0.0,
            w_atr_normalized=0.0,
        )
        ctx = {"volume_ratio": 4.0, "adx": 35.0}
        score = scorer2.score(ctx)
        assert score.total == 100.0

"""tests/test_cost_model.py — core/cost_model 단위 테스트 (ADR-009)."""

from __future__ import annotations

import pytest

from core.cost_model import TradeCosts, apply_buy_costs, apply_sell_costs


@pytest.fixture
def default_costs() -> TradeCosts:
    # config.yaml 기본값과 일치
    return TradeCosts(
        commission_rate=0.00015,
        slippage_rate=0.0003,
        tax_rate=0.0015,
    )


def test_trade_costs_from_backtest_config():
    """BacktestConfig → TradeCosts 팩토리 변환."""
    from config.settings import BacktestConfig
    bc = BacktestConfig()
    costs = TradeCosts.from_backtest_config(bc)
    assert costs.commission_rate == bc.commission
    assert costs.slippage_rate == bc.slippage
    assert costs.tax_rate == bc.tax


class TestApplyBuyCosts:
    def test_slipped_price_higher_than_raw(self, default_costs):
        """매수 시 슬리피지 반영으로 체결가가 원래 가격보다 높음."""
        slipped, net_entry = apply_buy_costs(10000, default_costs)
        # 10000 × 1.0003 = 10003
        assert slipped == pytest.approx(10003.0, rel=1e-9)
        assert slipped > 10000

    def test_net_entry_includes_commission(self, default_costs):
        """net_entry = slipped + slipped × commission."""
        slipped, net_entry = apply_buy_costs(10000, default_costs)
        # slipped = 10003, fee = 10003 × 0.00015 = 1.50045
        # net_entry = 10003 + 1.50045 = 10004.50045
        assert net_entry == pytest.approx(10004.50045, rel=1e-9)
        assert net_entry > slipped

    def test_zero_slippage_only_commission(self):
        """slippage=0이면 slipped == raw."""
        costs = TradeCosts(commission_rate=0.001, slippage_rate=0.0, tax_rate=0.0)
        slipped, net_entry = apply_buy_costs(1000, costs)
        assert slipped == 1000.0
        assert net_entry == pytest.approx(1001.0, rel=1e-9)  # + 0.1% 수수료


class TestApplySellCosts:
    def test_slipped_price_lower_than_raw(self, default_costs):
        """매도 시 슬리피지 반영으로 체결가가 원래 가격보다 낮음."""
        slipped, net_exit = apply_sell_costs(11000, default_costs)
        # 11000 × 0.9997 = 10996.7
        assert slipped == pytest.approx(10996.7, rel=1e-9)
        assert slipped < 11000

    def test_net_exit_subtracts_commission_and_tax(self, default_costs):
        """net_exit = slipped - slipped × (commission + tax)."""
        slipped, net_exit = apply_sell_costs(11000, default_costs)
        # slipped = 10996.7
        # fee = 10996.7 × (0.00015 + 0.0015) = 10996.7 × 0.00165 = 18.1446...
        # net_exit = 10996.7 - 18.1446... = 10978.555...
        assert net_exit == pytest.approx(10978.55545, abs=0.01)
        assert net_exit < slipped


class TestRoundTrip:
    def test_same_price_buy_sell_produces_loss(self, default_costs):
        """동일 가격 매수·매도 시 비용만큼 손실."""
        raw = 10000
        _, net_entry = apply_buy_costs(raw, default_costs)
        _, net_exit = apply_sell_costs(raw, default_costs)
        pnl_per_share = net_exit - net_entry
        assert pnl_per_share < 0, "왕복 시 비용만큼 음수 PnL"

    def test_roundtrip_loss_approx_27bp(self, default_costs):
        """왕복 비용 합산 ≈ commission×2 + tax + slippage×2 ≈ 0.24~0.30%."""
        raw = 10000
        _, net_entry = apply_buy_costs(raw, default_costs)
        _, net_exit = apply_sell_costs(raw, default_costs)
        # 왕복 손실률 (net_exit - net_entry) / net_entry
        loss_pct = (net_exit - net_entry) / net_entry
        # 0.00015(buy) + 0.00015(sell) + 0.0015(tax) + 0.0003(buy slip) + 0.0003(sell slip)
        # ≈ -0.00240 (−0.240%)
        assert loss_pct == pytest.approx(-0.00240, abs=5e-5)

    def test_profit_larger_than_cost(self, default_costs):
        """비용 초과 가격 상승 시 최종 PnL > 0."""
        _, net_entry = apply_buy_costs(10000, default_costs)
        _, net_exit = apply_sell_costs(10050, default_costs)  # +0.5%
        assert (net_exit - net_entry) > 0

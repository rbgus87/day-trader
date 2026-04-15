"""core/cost_model.py — 거래 비용 모델 (ADR-009).

backtester와 PaperOrderManager 양쪽이 공유하는 단일 비용 함수.
매수/매도 가격에 슬리피지·수수료·세금을 적용하여 (체결가, net cost/proceeds) 쌍 반환.

수수료·세금 규격:
- commission: 매수/매도 각 편도 (예: 0.00015 = 0.015%)
- tax: 매도 시에만 (예: 0.0015 = 0.15%)
- slippage: 편도 추정치 (예: 0.0003 = 0.03%)
  - 매수 시 불리 방향(가격 상승), 매도 시 불리 방향(가격 하락)
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TradeCosts:
    """단일 왕복 거래에 적용되는 비용율 모음."""
    commission_rate: float   # 편도 수수료 (매수/매도 동일)
    slippage_rate: float     # 편도 슬리피지
    tax_rate: float          # 매도 시에만 부과되는 거래세

    @classmethod
    def from_backtest_config(cls, backtest_config) -> "TradeCosts":
        """BacktestConfig → TradeCosts 변환 편의 팩토리."""
        return cls(
            commission_rate=backtest_config.commission,
            slippage_rate=backtest_config.slippage,
            tax_rate=backtest_config.tax,
        )


def apply_buy_costs(price: float, costs: TradeCosts) -> tuple[float, float]:
    """매수 가격에 슬리피지 + 수수료 반영.

    Args:
        price: 원래 가격 (슬리피지 전)
        costs: 비용율 모음

    Returns:
        (slipped_price, net_entry)
        - slipped_price: 슬리피지 반영된 실제 체결가 (원래 가격보다 높음)
        - net_entry: slipped_price + 매수 수수료 = 실제 투입 원가
    """
    slipped = price * (1 + costs.slippage_rate)
    fee = slipped * costs.commission_rate
    return slipped, slipped + fee


def apply_sell_costs(price: float, costs: TradeCosts) -> tuple[float, float]:
    """매도 가격에 슬리피지 + 수수료 + 세금 반영.

    Args:
        price: 원래 가격 (슬리피지 전)
        costs: 비용율 모음

    Returns:
        (slipped_price, net_exit)
        - slipped_price: 슬리피지 반영된 실제 체결가 (원래 가격보다 낮음)
        - net_exit: slipped_price - (매도 수수료 + 거래세) = 실제 회수 원가
    """
    slipped = price * (1 - costs.slippage_rate)
    fee = slipped * (costs.commission_rate + costs.tax_rate)
    return slipped, slipped - fee

"""core/price_utils.py — 가격 호가 단위 및 상한가 계산.

한국 주식 호가 단위 (2023-01-25 개편 기준, KOSPI/KOSDAQ 공통):
- < 2,000원:           1원
- 2,000 ~ 5,000원:     5원
- 5,000 ~ 20,000원:    10원
- 20,000 ~ 50,000원:   50원
- 50,000 ~ 200,000원:  100원
- 200,000 ~ 500,000원: 500원
- >= 500,000원:        1,000원
"""


def korean_tick_size(price: float) -> int:
    """한국 주식 호가 단위 반환."""
    if price < 2_000:
        return 1
    if price < 5_000:
        return 5
    if price < 20_000:
        return 10
    if price < 50_000:
        return 50
    if price < 200_000:
        return 100
    if price < 500_000:
        return 500
    return 1_000


def round_down_to_tick(price: float) -> int:
    """호가 단위로 절사."""
    if price <= 0:
        return 0
    tick = korean_tick_size(price)
    return int(price // tick * tick)


def calculate_limit_up_price(prev_close: float, limit_pct: float = 0.30) -> int:
    """전일 종가 × (1 + limit_pct)를 호가 단위로 절사한 상한가.

    Args:
        prev_close: 전일 종가
        limit_pct:  상한폭 (기본 0.30 = +30%)

    Returns:
        호가 절사된 상한가. prev_close <= 0이면 0.
    """
    if prev_close <= 0:
        return 0
    return round_down_to_tick(prev_close * (1.0 + limit_pct))

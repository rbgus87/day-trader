"""pipeline/trading_state.py — 파이프라인 전체의 공유 변경 가능 상태.

모든 파이프라인 모듈이 동일 TradingState 인스턴스를 참조한다.
dict/set/list는 참조 의미론으로 뮤테이션이 전파된다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class BreakoutInfo:
    """틱 레벨 돌파 감지 결과."""
    ticker: str
    breakout_price: float
    detected_at: datetime


@dataclass
class TradingState:
    """파이프라인 공유 상태. engine_worker가 생성 후 모든 모듈에 주입."""

    MAX_HISTORY: int = 500
    INTRADAY_SEED_BARS: int = 50

    # 전략 관리
    active_strategies: dict = field(default_factory=dict)  # {ticker: {"strategy","name","score"}}
    active_strategy: object = None
    gap_strategies: dict = field(default_factory=dict)     # {ticker: GapPullbackStrategy}

    # 캔들 데이터
    candle_history: dict = field(default_factory=dict)   # {ticker: deque}

    # 틱 레벨 시그널
    breakout_detected: dict = field(default_factory=dict)  # {ticker: BreakoutInfo}
    tick_signaled: set = field(default_factory=set)
    atr_pct_cache: dict = field(default_factory=dict)    # {ticker: (len, pct)}

    # 가격
    latest_prices: dict = field(default_factory=dict)
    prev_close: dict = field(default_factory=dict)
    prev_high_map: dict = field(default_factory=dict)
    limit_up_map: dict = field(default_factory=dict)
    ticker_atr_pct: dict = field(default_factory=dict)   # daily ATR% (백분율)

    # 시장 분류
    ticker_markets: dict = field(default_factory=dict)   # {ticker: "kospi"/"kosdaq"/"unknown"}
    ticker_names: dict = field(default_factory=dict)
    market_codes_cache: dict | None = None

    # 세션 흐름 제어
    daily_ohlcv_cache: dict = field(default_factory=dict)  # startup 1회용 캐시
    pending_cond_top: list | None = None
    force_close_in_progress: bool = False
    daily_halt_notified: bool = False

    # 주문 추적
    limit_up_exit_pending: set = field(default_factory=set)
    timeout_counters: dict = field(default_factory=dict)

    # 런타임 PnL 카운터
    rt_wins: int = 0
    rt_losses: int = 0

    # 스크리너 결과 (UI emit용)
    screener_results: list = field(default_factory=list)

    # 장중 조건검색 (intraday_search) 상태
    intraday_added_tickers: set = field(default_factory=set)  # 당일 intraday_leader가 추가한 ticker
    intraday_add_count: int = 0                               # 당일 장중 추가 총 건수
    ticker_sources: dict = field(default_factory=dict)        # {ticker: "day_momentum" | "intraday_leader"}

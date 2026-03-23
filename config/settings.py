"""전역 파라미터 — 손절률, 익절 목표, API 설정 등 단일 관리."""

import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class KiwoomConfig:
    app_key: str = field(default_factory=lambda: os.environ["KIWOOM_APP_KEY"])
    secret_key: str = field(default_factory=lambda: os.environ["KIWOOM_SECRET_KEY"])
    account_no: str = field(default_factory=lambda: os.environ["KIWOOM_ACCOUNT_NO"])
    rest_base_url: str = "https://api.kiwoom.com"
    ws_url: str = "wss://api.kiwoom.com:10000/api/dostk/websocket"
    rate_limit_calls: int = 5
    rate_limit_period: float = 1.0


@dataclass(frozen=True)
class TelegramConfig:
    bot_token: str = field(default_factory=lambda: os.environ["TELEGRAM_BOT_TOKEN"])
    chat_id: str = field(default_factory=lambda: os.environ["TELEGRAM_CHAT_ID"])


@dataclass(frozen=True)
class TradingConfig:
    # 리스크
    stop_loss_pct: float = -0.015         # -1.5%
    daily_max_loss_pct: float = -0.02     # -2%
    consecutive_loss_days: int = 3        # 연속 손실 일수
    reduced_position_pct: float = 0.5     # 축소 비율

    # 익절
    tp1_pct: float = 0.02                 # +2% 1차 익절
    tp1_sell_ratio: float = 0.5           # 50% 매도
    trailing_stop_pct: float = 0.01       # 고점 -1% 트레일링

    # 진입
    entry_1st_ratio: float = 0.55         # 1차 매수 비율 55%

    # 시간
    signal_block_until: str = "09:05"     # 신호 차단 시각
    force_close_time: str = "15:10"       # 강제 청산 시각
    screening_time: str = "08:30"         # 장 전 스크리닝
    report_time: str = "15:30"            # 일일 보고서

    # ORB 전략
    orb_range_start: str = "09:05"
    orb_range_end: str = "09:15"
    orb_volume_ratio: float = 1.5         # 전일 대비 150%
    orb_stop_loss_pct: float = -0.015

    # VWAP 전략
    vwap_rsi_low: float = 40.0
    vwap_rsi_high: float = 60.0
    vwap_stop_loss_pct: float = -0.012

    # 모멘텀 전략
    momentum_volume_ratio: float = 2.0    # 전일 200%

    # 눌림목 전략
    pullback_min_gain_pct: float = 0.03   # 당일 +3%
    pullback_stop_loss_pct: float = -0.015


@dataclass(frozen=True)
class ScreenerConfig:
    min_market_cap: int = 300_000_000_000       # 3000억
    min_avg_volume_amount: int = 5_000_000_000  # 50억
    ma20_ascending: bool = True
    volume_surge_ratio: float = 1.5             # +50%
    min_atr_pct: float = 0.02                   # 2%


@dataclass(frozen=True)
class AppConfig:
    kiwoom: KiwoomConfig = field(default_factory=KiwoomConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    trading: TradingConfig = field(default_factory=TradingConfig)
    screener: ScreenerConfig = field(default_factory=ScreenerConfig)
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))
    debug: bool = field(default_factory=lambda: os.getenv("DEBUG", "false").lower() == "true")
    db_path: str = "daytrader.db"

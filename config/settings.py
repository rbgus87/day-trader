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
    rest_base_url: str = "https://openapi.koreainvestment.com:9443"
    ws_url: str = "ws://ops.koreainvestment.com:21000"
    rate_limit_calls: int = 5
    rate_limit_period: float = 1.0


@dataclass(frozen=True)
class TelegramConfig:
    bot_token: str = field(default_factory=lambda: os.environ["TELEGRAM_BOT_TOKEN"])
    chat_id: str = field(default_factory=lambda: os.environ["TELEGRAM_CHAT_ID"])


@dataclass(frozen=True)
class TradingConfig:
    # 리스크
    stop_loss_pct: float = -0.015
    daily_max_loss_pct: float = -0.02
    consecutive_loss_days: int = 3
    reduced_position_pct: float = 0.5

    # 익절
    tp1_pct: float = 0.02
    tp1_sell_ratio: float = 0.5
    trailing_stop_pct: float = 0.01

    # 진입
    entry_1st_ratio: float = 0.55

    # 시간
    signal_block_until: str = "09:05"
    force_close_time: str = "15:10"
    screening_time: str = "08:30"
    report_time: str = "15:30"

    # ORB 전략
    orb_range_start: str = "09:05"
    orb_range_end: str = "09:15"
    orb_volume_ratio: float = 1.5
    orb_stop_loss_pct: float = -0.015

    # VWAP 전략
    vwap_rsi_low: float = 40.0
    vwap_rsi_high: float = 60.0
    vwap_stop_loss_pct: float = -0.012

    # 모멘텀 전략
    momentum_volume_ratio: float = 2.0

    # 눌림목 전략
    pullback_min_gain_pct: float = 0.03
    pullback_stop_loss_pct: float = -0.015


@dataclass(frozen=True)
class ScreenerConfig:
    min_market_cap: int = 300_000_000_000
    min_avg_volume_amount: int = 5_000_000_000
    ma20_ascending: bool = True
    volume_surge_ratio: float = 1.5
    min_atr_pct: float = 0.02


@dataclass(frozen=True)
class AppConfig:
    kiwoom: KiwoomConfig = field(default_factory=KiwoomConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    trading: TradingConfig = field(default_factory=TradingConfig)
    screener: ScreenerConfig = field(default_factory=ScreenerConfig)
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))
    debug: bool = field(default_factory=lambda: os.getenv("DEBUG", "false").lower() == "true")
    db_path: str = "daytrader.db"

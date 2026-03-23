"""전역 파라미터 — config.yaml + .env 통합 설정.

사용법:
    config = AppConfig()          # config.yaml + .env 자동 로드
    config = AppConfig("my.yaml") # 커스텀 yaml 경로
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

load_dotenv()

# config.yaml 기본 경로
_DEFAULT_CONFIG_PATH = Path(__file__).parent.parent / "config.yaml"


def _load_yaml(path: str | Path | None = None) -> dict:
    """config.yaml 로드. 없으면 빈 dict 반환."""
    p = Path(path) if path else _DEFAULT_CONFIG_PATH
    if p.exists():
        with open(p, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


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

    @staticmethod
    def from_yaml(path: str | Path | None = None) -> "AppConfig":
        """config.yaml + .env에서 설정 로드."""
        cfg = _load_yaml(path)

        # broker 섹션 → KiwoomConfig
        broker = cfg.get("broker", {})
        kiwoom = KiwoomConfig(
            app_key=os.environ["KIWOOM_APP_KEY"],
            secret_key=os.environ["KIWOOM_SECRET_KEY"],
            account_no=os.environ["KIWOOM_ACCOUNT_NO"],
            rest_base_url=broker.get("base_url", "https://api.kiwoom.com"),
            ws_url=broker.get("ws_url", "wss://api.kiwoom.com:10000/api/dostk/websocket"),
            rate_limit_calls=broker.get("rate_limit_calls", 5),
            rate_limit_period=broker.get("rate_limit_period", 1.0),
        )

        # telegram → TelegramConfig (.env에서만)
        telegram = TelegramConfig(
            bot_token=os.environ["TELEGRAM_BOT_TOKEN"],
            chat_id=os.environ["TELEGRAM_CHAT_ID"],
        )

        # trading 섹션 → TradingConfig
        t = cfg.get("trading", {})
        s = cfg.get("strategy", {})
        orb = s.get("orb", {})
        vwap = s.get("vwap", {})
        mom = s.get("momentum", {})
        pb = s.get("pullback", {})

        trading = TradingConfig(
            stop_loss_pct=t.get("stop_loss_pct", -0.015),
            daily_max_loss_pct=t.get("daily_max_loss_pct", -0.02),
            consecutive_loss_days=t.get("consecutive_loss_days", 3),
            reduced_position_pct=t.get("reduced_position_pct", 0.5),
            tp1_pct=t.get("tp1_pct", 0.02),
            tp1_sell_ratio=t.get("tp1_sell_ratio", 0.5),
            trailing_stop_pct=t.get("trailing_stop_pct", 0.01),
            entry_1st_ratio=t.get("entry_1st_ratio", 0.55),
            signal_block_until=t.get("signal_block_until", "09:05"),
            force_close_time=t.get("force_close_time", "15:10"),
            screening_time=t.get("screening_time", "08:30"),
            report_time=t.get("report_time", "15:30"),
            orb_range_start=orb.get("range_start", "09:05"),
            orb_range_end=orb.get("range_end", "09:15"),
            orb_volume_ratio=orb.get("volume_ratio", 1.5),
            orb_stop_loss_pct=orb.get("stop_loss_pct", -0.015),
            vwap_rsi_low=vwap.get("rsi_low", 40.0),
            vwap_rsi_high=vwap.get("rsi_high", 60.0),
            vwap_stop_loss_pct=vwap.get("stop_loss_pct", -0.012),
            momentum_volume_ratio=mom.get("volume_ratio", 2.0),
            pullback_min_gain_pct=pb.get("min_gain_pct", 0.03),
            pullback_stop_loss_pct=pb.get("stop_loss_pct", -0.015),
        )

        # screener 섹션
        sc = cfg.get("screener", {})
        screener = ScreenerConfig(
            min_market_cap=sc.get("min_market_cap", 300_000_000_000),
            min_avg_volume_amount=sc.get("min_avg_volume_amount", 5_000_000_000),
            volume_surge_ratio=sc.get("volume_surge_ratio", 1.5),
            min_atr_pct=sc.get("min_atr_pct", 0.02),
        )

        return AppConfig(
            kiwoom=kiwoom,
            telegram=telegram,
            trading=trading,
            screener=screener,
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            debug=os.getenv("DEBUG", "false").lower() == "true",
        )

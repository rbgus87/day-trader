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
    daily_max_loss_pct: float = -0.02
    consecutive_loss_days: int = 3
    reduced_position_pct: float = 0.5
    # Phase 2 Day 10: 약세장 방어
    daily_max_loss_enabled: bool = True
    blacklist_enabled: bool = True
    blacklist_lookback_days: int = 5
    blacklist_loss_threshold: int = 3
    blacklist_days: int = 7

    # Phase 3 Day 11.5: 방어 레벨 A — 연속 손실 휴식
    consecutive_loss_rest_enabled: bool = True
    consecutive_loss_threshold: int = 3
    consecutive_loss_rest_days: int = 1

    # 익절
    tp1_pct: float = 0.03
    tp1_sell_ratio: float = 0.5
    trailing_stop_pct: float = 0.01

    # 진입
    entry_1st_ratio: float = 0.55
    max_trades_per_day: int = 1
    max_positions: int = 3
    screening_top_n: int = 5
    cooldown_minutes: int = 999

    # 시간
    signal_block_until: str = "09:05"
    force_close_time: str = "15:10"
    screening_time: str = "08:30"
    report_time: str = "15:30"

    # 모멘텀 전략
    momentum_volume_ratio: float = 2.0
    momentum_stop_loss_pct: float = -0.008
    # Phase 3 Day 11.5: 오전 매수 제한
    buy_time_limit_enabled: bool = True
    buy_time_end: str = "11:30"

    # Phase 2: ATR 기반 동적 손절 (Chandelier 준비)
    atr_stop_enabled: bool = True
    atr_stop_multiplier: float = 1.5
    atr_stop_min_pct: float = 0.015
    atr_stop_max_pct: float = 0.080

    # Phase 2 Day 7: ATR 기반 TP1
    atr_tp_enabled: bool = True
    atr_tp_multiplier: float = 3.0
    atr_tp_min_pct: float = 0.03
    atr_tp_max_pct: float = 0.25

    # Phase 2 Day 7: Chandelier 트레일링 스톱
    atr_trail_enabled: bool = True
    atr_trail_multiplier: float = 2.5
    atr_trail_min_pct: float = 0.02
    atr_trail_max_pct: float = 0.10
    adx_enabled: bool = True
    adx_length: int = 14
    adx_min: float = 25.0
    rvol_enabled: bool = True
    rvol_window: int = 5
    rvol_min: float = 3.0
    vwap_enabled: bool = True
    vwap_min_above: float = 0.0

    # 자본금
    initial_capital: int = 1_000_000

    # 시장 필터 (코스피/코스닥 지수 MA 기반 매수 차단)
    market_filter_enabled: bool = True
    market_ma_length: int = 5


@dataclass(frozen=True)
class ScreenerConfig:
    min_market_cap: int = 200_000_000_000
    min_avg_volume_amount: int = 10_000_000_000
    ma20_ascending: bool = True
    volume_surge_ratio: float = 1.5
    min_atr_pct: float = 0.03


@dataclass(frozen=True)
class BacktestConfig:
    commission: float = 0.00015     # 매수/매도 각 0.015%
    tax: float = 0.0018             # 증권거래세 0.18%
    slippage: float = 0.0003        # 슬리피지 0.03%
    initial_capital: int = 1_000_000


@dataclass(frozen=True)
class AppConfig:
    kiwoom: KiwoomConfig = field(default_factory=KiwoomConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    trading: TradingConfig = field(default_factory=TradingConfig)
    screener: ScreenerConfig = field(default_factory=ScreenerConfig)
    backtest: BacktestConfig = field(default_factory=BacktestConfig)
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))
    debug: bool = field(default_factory=lambda: os.getenv("DEBUG", "false").lower() == "true")
    db_path: str = "daytrader.db"
    paper_mode: bool = True  # True=주문 시뮬레이션, False=실매매
    selector: dict = field(default_factory=dict)  # 전략 선택기 임계값
    force_strategy: str = ""  # 비어있으면 selector 로직, 값 있으면 해당 전략 강제

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
        mom = s.get("momentum", {})

        trading = TradingConfig(
            daily_max_loss_pct=t.get("daily_max_loss_pct", -0.02),
            consecutive_loss_days=t.get("consecutive_loss_days", 3),
            reduced_position_pct=t.get("reduced_position_pct", 0.5),
            daily_max_loss_enabled=t.get("daily_max_loss_enabled", True),
            blacklist_enabled=t.get("blacklist_enabled", True),
            blacklist_lookback_days=t.get("blacklist_lookback_days", 5),
            blacklist_loss_threshold=t.get("blacklist_loss_threshold", 3),
            blacklist_days=t.get("blacklist_days", 7),
            consecutive_loss_rest_enabled=t.get("consecutive_loss_rest_enabled", True),
            consecutive_loss_threshold=t.get("consecutive_loss_threshold", 3),
            consecutive_loss_rest_days=t.get("consecutive_loss_rest_days", 1),
            tp1_pct=t.get("tp1_pct", 0.03),
            tp1_sell_ratio=t.get("tp1_sell_ratio", 0.5),
            trailing_stop_pct=t.get("trailing_stop_pct", 0.01),
            entry_1st_ratio=t.get("entry_1st_ratio", 0.55),
            max_trades_per_day=t.get("max_trades_per_day", 1),
            max_positions=t.get("max_positions", 3),
            screening_top_n=t.get("screening_top_n", 5),
            cooldown_minutes=t.get("cooldown_minutes", 999),
            signal_block_until=t.get("signal_block_until", "09:05"),
            force_close_time=t.get("force_close_time", "15:10"),
            screening_time=t.get("screening_time", "08:30"),
            report_time=t.get("report_time", "15:30"),
            momentum_volume_ratio=mom.get("volume_ratio", 2.0),
            momentum_stop_loss_pct=mom.get("stop_loss_pct", -0.008),
            buy_time_limit_enabled=mom.get("buy_time_limit_enabled", True),
            buy_time_end=mom.get("buy_time_end", "11:30"),
            atr_stop_enabled=mom.get("atr_stop_enabled", True),
            atr_stop_multiplier=mom.get("atr_stop_multiplier", 1.5),
            atr_stop_min_pct=mom.get("atr_stop_min_pct", 0.015),
            atr_stop_max_pct=mom.get("atr_stop_max_pct", 0.080),
            atr_tp_enabled=mom.get("atr_tp_enabled", True),
            atr_tp_multiplier=mom.get("atr_tp_multiplier", 3.0),
            atr_tp_min_pct=mom.get("atr_tp_min_pct", 0.03),
            atr_tp_max_pct=mom.get("atr_tp_max_pct", 0.25),
            atr_trail_enabled=mom.get("atr_trail_enabled", True),
            atr_trail_multiplier=mom.get("atr_trail_multiplier", 2.5),
            atr_trail_min_pct=mom.get("atr_trail_min_pct", 0.02),
            atr_trail_max_pct=mom.get("atr_trail_max_pct", 0.10),
            adx_enabled=mom.get("adx_enabled", True),
            adx_length=mom.get("adx_length", 14),
            adx_min=mom.get("adx_min", 25.0),
            rvol_enabled=mom.get("rvol_enabled", True),
            rvol_window=mom.get("rvol_window", 5),
            rvol_min=mom.get("rvol_min", 3.0),
            vwap_enabled=mom.get("vwap_enabled", True),
            vwap_min_above=mom.get("vwap_min_above", 0.0),
            initial_capital=t.get("initial_capital", 1_000_000),
            market_filter_enabled=t.get("market_filter_enabled", True),
            market_ma_length=t.get("market_ma_length", 5),
        )

        # screener 섹션
        sc = cfg.get("screener", {})
        screener = ScreenerConfig(
            min_market_cap=sc.get("min_market_cap", 300_000_000_000),
            min_avg_volume_amount=sc.get("min_avg_volume_amount", 5_000_000_000),
            volume_surge_ratio=sc.get("volume_surge_ratio", 1.5),
            min_atr_pct=sc.get("min_atr_pct", 0.02),
        )

        # 전략 선택기 임계값
        selector = s.get("selector", {})

        # backtest 섹션
        bt = cfg.get("backtest", {})
        backtest = BacktestConfig(
            commission=bt.get("commission", 0.00015),
            tax=bt.get("tax", 0.0018),
            slippage=bt.get("slippage", 0.0003),
            initial_capital=bt.get("initial_capital", 1_000_000),
        )

        return AppConfig(
            kiwoom=kiwoom,
            telegram=telegram,
            trading=trading,
            screener=screener,
            backtest=backtest,
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            debug=os.getenv("DEBUG", "false").lower() == "true",
            paper_mode=cfg.get("paper_mode", True),
            selector=selector,
            force_strategy=s.get("force", ""),
        )

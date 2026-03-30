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
    tp1_pct: float = 0.03
    tp1_sell_ratio: float = 0.5
    trailing_stop_pct: float = 0.01

    # 진입
    entry_1st_ratio: float = 0.55
    max_trades_per_day: int = 3
    cooldown_minutes: int = 15

    # 시간
    signal_block_until: str = "09:05"
    force_close_time: str = "15:10"
    screening_time: str = "08:30"
    report_time: str = "15:30"

    # DEPRECATED: ORB 전략 (백테스트 PF<1.0, 2026-03-30 폐기)
    orb_range_start: str = "09:05"
    orb_range_end: str = "09:15"
    orb_volume_ratio: float = 0.0
    orb_stop_loss_pct: float = -0.015
    orb_min_range_pct: float = 0.008

    # DEPRECATED: VWAP 전략 (백테스트 PF<1.0, 2026-03-30 폐기)
    vwap_rsi_low: float = 40.0
    vwap_rsi_high: float = 60.0
    vwap_stop_loss_pct: float = -0.012

    # 모멘텀 전략
    momentum_volume_ratio: float = 2.0
    momentum_stop_loss_pct: float = -0.008
    # DEPRECATED: 리테스트/VWAP 필터 — 4회 실험 결과 개선 효과 없음
    momentum_retest_band_pct: float = 0.008
    momentum_retest_timeout_min: int = 45
    momentum_vwap_filter: bool = False

    # 수급추종 전략 (FlowStrategy)
    flow_min_strength_pct: float = 120.0    # Phase 2용 (현재 미사용)
    flow_volume_surge_ratio: float = 2.5
    flow_stop_loss_pct: float = -0.015
    flow_trailing_stop_pct: float = 0.015
    flow_vwap_filter: bool = True
    flow_signal_start: str = "09:30"
    flow_signal_end: str = "14:30"

    # 자본금
    initial_capital: int = 1_000_000

    # 갭 전략 (GapStrategy)
    gap_min_gap_pct: float = 0.015
    gap_stop_loss_pct: float = -0.01

    # 시가 돌파 전략 (OpenBreakStrategy)
    open_break_pct: float = 0.005
    open_break_volume_ratio: float = 0.3
    open_break_stop_loss_pct: float = -0.005
    open_break_start: str = "09:15"

    # 세력 캔들 전략 (BigCandleStrategy)
    big_candle_atr_multiplier: float = 1.5
    big_candle_timeout_min: int = 30
    big_candle_stop_loss_pct: float = -0.01

    # 눌림목 전략
    pullback_min_gain_pct: float = 0.04
    pullback_stop_loss_pct: float = -0.018
    # DEPRECATED: v2 파라미터 — 조건 완화 실험 결과 PF 악화로 롤백
    pullback_ma_short: int = 10
    pullback_ma_long: int = 10
    pullback_ma_touch_band: float = 0.01
    pullback_min_atr_pct: float = 0.025


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
        fl = s.get("flow", {})
        gap = s.get("gap", {})
        ob = s.get("open_break", {})
        bc = s.get("big_candle", {})

        trading = TradingConfig(
            stop_loss_pct=t.get("stop_loss_pct", -0.015),
            daily_max_loss_pct=t.get("daily_max_loss_pct", -0.02),
            consecutive_loss_days=t.get("consecutive_loss_days", 3),
            reduced_position_pct=t.get("reduced_position_pct", 0.5),
            tp1_pct=t.get("tp1_pct", 0.03),
            tp1_sell_ratio=t.get("tp1_sell_ratio", 0.5),
            trailing_stop_pct=t.get("trailing_stop_pct", 0.01),
            entry_1st_ratio=t.get("entry_1st_ratio", 0.55),
            max_trades_per_day=t.get("max_trades_per_day", 3),
            cooldown_minutes=t.get("cooldown_minutes", 15),
            signal_block_until=t.get("signal_block_until", "09:05"),
            force_close_time=t.get("force_close_time", "15:10"),
            screening_time=t.get("screening_time", "08:30"),
            report_time=t.get("report_time", "15:30"),
            orb_range_start=orb.get("range_start", "09:05"),
            orb_range_end=orb.get("range_end", "09:15"),
            orb_volume_ratio=orb.get("volume_ratio", 0.0),
            orb_stop_loss_pct=orb.get("stop_loss_pct", -0.015),
            orb_min_range_pct=orb.get("min_range_pct", 0.008),
            vwap_rsi_low=vwap.get("rsi_low", 40.0),
            vwap_rsi_high=vwap.get("rsi_high", 60.0),
            vwap_stop_loss_pct=vwap.get("stop_loss_pct", -0.012),
            momentum_volume_ratio=mom.get("volume_ratio", 2.0),
            momentum_stop_loss_pct=mom.get("stop_loss_pct", -0.008),
            momentum_retest_band_pct=mom.get("retest_band_pct", 0.008),
            momentum_retest_timeout_min=mom.get("retest_timeout_minutes", 45),
            momentum_vwap_filter=mom.get("vwap_filter", True),
            pullback_min_gain_pct=pb.get("min_gain_pct", 0.04),
            pullback_stop_loss_pct=pb.get("stop_loss_pct", -0.018),
            pullback_ma_short=pb.get("ma_short", 10),
            pullback_ma_long=pb.get("ma_long", 10),
            pullback_ma_touch_band=pb.get("ma_touch_band", 0.01),
            pullback_min_atr_pct=pb.get("min_atr_pct", 0.025),
            gap_min_gap_pct=gap.get("min_gap_pct", 0.015),
            gap_stop_loss_pct=gap.get("stop_loss_pct", -0.01),
            open_break_pct=ob.get("break_pct", 0.005),
            open_break_volume_ratio=ob.get("volume_ratio", 0.3),
            open_break_stop_loss_pct=ob.get("stop_loss_pct", -0.005),
            open_break_start=ob.get("signal_start", "09:15"),
            big_candle_atr_multiplier=bc.get("atr_multiplier", 1.5),
            big_candle_timeout_min=bc.get("timeout_minutes", 30),
            big_candle_stop_loss_pct=bc.get("stop_loss_pct", -0.01),
            flow_min_strength_pct=fl.get("min_strength_pct", 120.0),
            flow_volume_surge_ratio=fl.get("volume_surge_ratio", 2.5),
            flow_stop_loss_pct=fl.get("stop_loss_pct", -0.015),
            flow_trailing_stop_pct=fl.get("trailing_stop_pct", 0.015),
            flow_vwap_filter=fl.get("vwap_filter", True),
            flow_signal_start=fl.get("signal_start", "09:30"),
            flow_signal_end=fl.get("signal_end", "14:30"),
            initial_capital=t.get("initial_capital", 1_000_000),
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
        )

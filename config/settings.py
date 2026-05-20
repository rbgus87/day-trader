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

from core.exit_logic import TimeDecayPhase

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
    ws_record_enabled: bool = False        # WS 메시지 녹화 (기본 비활성)
    ws_record_dir: str = "logs/ws_replay"  # 녹화 파일 저장 디렉토리


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

    # ADR-010: TP1 폐기됨 — 아래 3개는 dead config (코드 호환용 잔존)
    tp1_pct: float = 0.99            # 사실상 미도달 (fallback path)
    tp1_sell_ratio: float = 0.5      # 미사용 (TP1 비활성)
    trailing_stop_pct: float = 0.005  # ATR trail fallback (atr_trail_enabled=true 시 미사용)

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
    momentum_stop_loss_pct: float = -0.080  # ADR-010: 고정 -8%
    min_breakout_pct: float = 0.03          # ADR-016: 전일 고가 대비 최소 돌파폭 (3%)
    # 오전 매수 제한
    buy_time_limit_enabled: bool = True
    buy_time_end: str = "11:30"

    # ADR-010: ATR stop 비활성 (41종목 전부 max 클램핑 → 고정 -8%와 동일)
    atr_stop_enabled: bool = False
    # dead params (코드 호환용 잔존, atr_stop_enabled=false 시 미참조)
    atr_stop_multiplier: float = 1.5
    atr_stop_min_pct: float = 0.015
    atr_stop_max_pct: float = 0.080

    # ADR-010: TP1 폐기 (Pure trailing)
    atr_tp_enabled: bool = False

    # ADR-010: Chandelier 트레일링 스톱 (진입 즉시 활성)
    atr_trail_enabled: bool = True
    atr_trail_multiplier: float = 1.0
    atr_trail_min_pct: float = 0.02
    atr_trail_max_pct: float = 0.10

    # ADR-017: Breakeven Stop (BE3) — peak 도달 시 stop 상향
    # ATR 비례: trigger = max(breakeven_trigger_pct, ATR × be3_atr_ratio)
    #           stop    = entry × (1 + ATR × be3_stop_atr_ratio)
    breakeven_enabled: bool = True
    breakeven_trigger_pct: float = 0.03
    breakeven_offset_pct: float = 0.01
    be3_atr_ratio: float = 0.4
    be3_stop_atr_ratio: float = 0.15

    # 상한가 즉시 청산 — 도달 시 즉시 매도, 실패 시 stop을 상한가×0.99로 상향
    limit_up_exit_enabled: bool = True
    limit_up_pct: float = 0.30
    limit_up_stop_floor_pct: float = 0.99  # 실패 시 stop = limit_up × 0.99
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

    # 장중 시장 필터 — 당일 지수 시가 대비 등락률 기반 (MA5와 독립 레이어)
    intraday_market_filter_enabled: bool = True
    intraday_check_interval_min: int = 10
    intraday_block_threshold: float = -0.01
    intraday_resume_threshold: float = -0.005

    # VI(변동성완화장치) 휴리스틱
    # static_pct=0.095: 전일종가 대비 ±9.5% 이상이면 정적VI 추정
    # assumed_duration_sec=150: 단일가 매매 2분 + 랜덤종료 30초
    # suspected_duration_sec=60: REST 주문 거부 기반 SUSPECTED 만료 (키움 일시 장애 대비)
    vi_static_pct: float = 0.095
    vi_assumed_duration_sec: int = 150
    vi_suspected_duration_sec: int = 60

    # 주문 체결 확인 파이프라인 (real_mode 전용)
    # order_confirmation_timeout_sec=10.0: WS '00' 체결통보 미수신 시 REST 폴백 트리거 시각
    # order_timeout_consecutive_threshold=3: 같은 ticker 연속 TIMEOUT 임계 (긴급 알림)
    order_confirmation_timeout_sec: float = 10.0
    order_timeout_consecutive_threshold: int = 3

    # 시간연동 트레일링 — 장 후반 trail 폭 축소
    # phases는 config.yaml strategy.momentum.time_decay_phases에서 주입
    time_decay_trailing_enabled: bool = True
    time_decay_min_pct_floor: float = 0.01     # 절대 하한 1.0%
    time_decay_phases: tuple[TimeDecayPhase, ...] = ()

    # 모멘텀 둔화 청산 — 수익 포지션 + 보유 15분+ 에서만
    momentum_fade_exit_enabled: bool = True
    momentum_fade_lookback: int = 10
    momentum_fade_threshold: float = -0.005
    momentum_fade_min_hold_min: int = 15
    momentum_fade_min_profit: float = 0.01

    # 시간대별 거래량 비율 — 전일 동시간대 누적 대비 배수 (False면 기존 전일 전체 대비)
    volume_by_time_enabled: bool = False
    volume_by_time_ratio: float = 1.5

    # 돌파 캔들 거래량 서지 — 직전 5분봉 평균 대비 N배 이상이어야 유효 돌파
    breakout_volume_surge_enabled: bool = False
    breakout_volume_surge_ratio: float = 2.0

    # 시장 필터 약세 시 포지션 축소 (Scenario C) — true면 완전 차단 대신 사이즈 50%
    market_regime_reduce_enabled: bool = False
    market_regime_reduce_size: float = 0.5

    # 틱 레벨 돌파 감지: 돌파 시점 대비 진입 가격 상한 (초과 시 진입 차단)
    max_entry_above_breakout_pct: float = 0.10
    # 전일종가 대비 현재가 상한 (%) — 15 이상이면 진입 차단. None/0이면 비활성
    max_entry_above_close_pct: float = 15.0

    # 횡보 포지션 조기 청산 — 보유 N분 후 수익률 < min_profit이면 청산
    stale_position_exit_enabled: bool = False
    stale_position_check_minutes: int = 30
    stale_position_min_profit: float = 0.005

    # 오후 강화 조건부 매수 (buy_time_end ~ afternoon_end 구간)
    afternoon_entry_enabled: bool = False
    afternoon_end: str = "14:00"
    afternoon_min_breakout_pct: float = 0.05
    afternoon_min_volume_ratio: float = 3.0
    afternoon_min_adx: float = 25.0

    # 변동성 기반 포지션 사이징
    # risk_per_trade_pct: 계좌 대비 1거래 최대 리스크 (예: 0.01 = 1%)
    # position_value = clamp(capital × risk / (atr_pct × multiplier), min_pct, max_pct) × capital
    volatility_sizing_enabled: bool = False
    risk_per_trade_pct: float = 0.01
    sizing_atr_multiplier: float = 1.0
    sizing_min_pct: float = 0.15
    sizing_max_pct: float = 0.50

    # 호가(OBI) 필터 — 실시간 전용, 백테스트 무영향
    # 0D 미수신 시 OBI=None → 필터 비적용 (기존대로 진입 허용)
    obi_filter_enabled: bool = False
    obi_min: float = 0.55              # OBI >= 0.55 (매수 우위) 시에만 진입
    spread_max_pct: float = 0.005      # 스프레드 0.5% 이하에서만 진입
    ask_wall_block_enabled: bool = False  # 현재가 근처 매도벽 감지 시 진입 차단

    # 시그널 스코어링 — 최소 조건 통과 후 품질 필터 (100점 만점)
    signal_scoring_enabled: bool = False
    signal_min_score: float = 60.0
    score_weight_volume_ratio: float = 25.0
    score_weight_adx_strength: float = 25.0
    score_weight_breakout_pct: float = 20.0
    score_weight_close_position: float = 15.0
    score_weight_atr_normalized: float = 15.0

    # 갭업 기준가 조정 — NXT 소진 종목 자연 필터링
    gap_breakout_adjust_enabled: bool = False
    gap_threshold_pct: float = 0.03    # 갭업 3% 이상 시 시가를 돌파 기준가로

    # 활성 전략 선택 ("momentum" | "orb")
    strategy_type: str = "momentum"

    # ORB(Opening Range Breakout) 전략
    orb_enabled: bool = False
    orb_range_minutes: int = 5
    orb_min_range_pct: float = 0.005
    orb_max_range_pct: float = 0.05
    orb_breakout_buffer: float = 0.0
    orb_entry_deadline: str = "10:00"
    orb_sl_ratio: float = 1.0
    orb_tp_ratio: float = 2.0
    orb_use_volume_filter: bool = True
    orb_rvol_min: float = 1.5

    # 갭업 눌림목 전략 (GapPullbackStrategy)
    gap_pullback_enabled: bool = False
    gap_pullback_min_pct: float = 0.02           # 최소 갭업 2%
    gap_pullback_max_pct: float = 0.08           # 최대 갭업 8%
    gap_pullback_min_pullback_pct: float = 0.01  # 최소 눌림 1%
    gap_pullback_max_pullback_pct: float = 0.03  # 최대 눌림 3%
    gap_pullback_entry_start: str = "09:00"
    gap_pullback_entry_end: str = "09:20"
    gap_pullback_force_close: str = "09:45"
    gap_pullback_max_positions: int = 1
    gap_pullback_volume_ratio: float = 1.5
    gap_pullback_atr_stop_mult: float = 0.5

    # VWAP 리버전 전략 (09:30~14:00, 평균회귀)
    vwap_rev_enabled: bool = False
    vwap_rev_entry_deviation: float = -0.015   # VWAP 대비 진입 하락폭 (-1.5%)
    vwap_rev_stop_loss_pct: float = 0.015      # 고정 손절폭 (1.5%)
    vwap_rev_tp_above_vwap: float = 0.003      # 익절 VWAP 초과폭 (+0.3%)
    vwap_rev_entry_start: str = "09:30"
    vwap_rev_entry_end: str = "14:00"
    vwap_rev_min_prev_volume: int = 50000      # 전일 최소 거래량 (주)
    vwap_rev_max_daily_drop: float = -0.07     # 당일 허용 최대 등락률 (-7%)

    # 눌림목 전략 (09:30~13:00, 초기 급등 후 첫 조정 진입)
    pb_enabled: bool = False
    pb_surge_pct: float = 0.05           # 급등 임계: 전일종가 대비 +5%
    pb_pullback_depth: float = 0.02      # 눌림 임계: 고점 대비 -2%
    pb_min_above_close_pct: float = 0.01 # 진입 최소 유지: 전일종가 +1%
    pb_sl_from_high_pct: float = 0.05   # 손절: 고점 대비 -5%
    pb_tp_above_high_pct: float = 0.01  # 익절: 고점 +1%
    pb_entry_start: str = "09:30"
    pb_entry_end: str = "13:00"
    pb_min_volume: int = 50000           # 전일 최소 거래량 (주)

    # 거래량 폭발 전략 (09:30~13:00, 급증 양봉 진입)
    vs_enabled: bool = False
    vs_lookback_minutes: int = 10        # 평균 산출 직전 N분봉
    vs_spike_ratio: float = 5.0          # 급증 배수
    vs_sl_pct: float = 0.02              # 손절: 진입가 대비 -2%
    vs_tp_pct: float = 0.03              # 익절: 진입가 대비 +3%
    vs_entry_start: str = "09:30"
    vs_entry_end: str = "13:00"
    vs_min_prev_volume: int = 50000      # 전일 최소 거래량 (주)
    vs_min_spike_volume: int = 10000     # 급증 분봉 절대 최소 거래량 (주)

    # 변동성 돌파 전략 (래리 윌리엄스, 09:00~entry_deadline)
    # target = 당일시가 + (전일고가 - 전일저가) × k_value
    vb_enabled: bool = False
    vb_k_value: float = 0.5              # K값 (0.3~0.7)
    vb_entry_deadline: str = "14:00"     # 진입 허용 마감
    vb_sl_mode: str = "open"             # "open": 시가 손절 / "fixed": 고정% 손절
    vb_sl_pct: float = 0.02             # 고정 손절폭 (sl_mode=fixed 시)
    vb_tp_pct: float = 0.03             # 익절 목표 (0.0이면 TP 없음)
    vb_use_trailing: bool = True         # 트레일링 스톱 사용 여부
    vb_trail_pct: float = 0.02          # 트레일링 스톱폭 (고점 대비)
    vb_use_volume_confirm: bool = True   # 분봉 거래량 확인 (당일 평균 × 2.0 이상)
    vb_min_range_pct: float = 0.015     # 전일 변동폭 최소 (전일종가 대비)
    vb_max_range_pct: float = 0.10      # 전일 변동폭 최대
    vb_min_prev_volume: int = 50000     # 전일 최소 거래량 (주)


@dataclass(frozen=True)
class ScreenerConfig:
    min_market_cap: int = 200_000_000_000
    min_avg_volume_amount: int = 10_000_000_000
    ma20_ascending: bool = True
    volume_surge_ratio: float = 1.5
    min_atr_pct: float = 0.06  # ADR-010: 3% → 6%


@dataclass(frozen=True)
class NotificationConfig:
    """Phase 3-B ADR-008: 알림 정책 토글 10종.

    기본값 전부 True (기존 동작 유지). 운영자가 피로감 있으면 개별 off.
    config.yaml `notifications` 섹션 또는 GUI 설정 탭에서 변경.
    """
    # 정기 이벤트
    daily_reset: bool = True
    ohlcv_refresh: bool = True
    token_refresh_failure: bool = True

    # 매매 이벤트
    trade_execution: bool = True
    daily_report: bool = True

    # 시스템 이벤트
    system_start: bool = True
    system_stop: bool = True
    uptime_sanity: bool = True

    # 자동화 이벤트
    universe_refresh: bool = True     # 주간 유니버스 갱신 결과
    candle_collection: bool = True    # 일일 분봉 수집 결과

    # WS 이벤트
    ws_critical_failure: bool = True  # 3회 연속 실패
    ws_auto_recovery: bool = True


@dataclass(frozen=True)
class ConditionSearchConfig:
    """조건검색 (영웅문 저장 조건식) 연동 설정."""
    enabled: bool = True
    condition_name: str = "day_momentum"
    max_watch_stocks: int = 50


@dataclass(frozen=True)
class IntradaySearchConfig:
    """장중 조건검색 — 동적 유니버스 확장 설정."""
    enabled: bool = False
    condition_name: str = "intraday_leader"
    schedule: tuple = ("09:05", "09:15", "09:30", "10:00", "10:30", "11:00", "11:30")
    max_add_per_search: int = 10
    max_total_added: int = 30


@dataclass(frozen=True)
class BacktestConfig:
    commission: float = 0.00015     # 매수/매도 각 0.015%
    # 거래세 0.20% (2025 기준, KOSPI/KOSDAQ 공통)
    # - KOSPI: 증권거래세 0.05% + 농어촌특별세 0.15% = 0.20%
    # - KOSDAQ: 증권거래세 0.20% (농특세 없음) = 0.20%
    # TODO: 시장별 세율 차등 시 cost_model에 market 파라미터 추가 필요.
    tax: float = 0.0020
    slippage: float = 0.0003        # 슬리피지 0.03% (추정값)
    initial_capital: int = 1_000_000  # (dead: 참조 경로 없음, ADR-013 baseline 비교용)


@dataclass(frozen=True)
class AppConfig:
    kiwoom: KiwoomConfig = field(default_factory=KiwoomConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    trading: TradingConfig = field(default_factory=TradingConfig)
    screener: ScreenerConfig = field(default_factory=ScreenerConfig)
    backtest: BacktestConfig = field(default_factory=BacktestConfig)
    notifications: NotificationConfig = field(default_factory=NotificationConfig)
    condition_search: ConditionSearchConfig = field(default_factory=ConditionSearchConfig)
    intraday_search: IntradaySearchConfig = field(default_factory=IntradaySearchConfig)
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
            ws_record_enabled=broker.get("ws_record_enabled", False),
            ws_record_dir=broker.get("ws_record_dir", "logs/ws_replay"),
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
        gap = s.get("gap_pullback", {})
        vr = s.get("vwap_reversion", {})

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
            tp1_pct=t.get("tp1_pct", 0.99),
            tp1_sell_ratio=t.get("tp1_sell_ratio", 0.5),
            trailing_stop_pct=t.get("trailing_stop_pct", 0.005),
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
            momentum_stop_loss_pct=mom.get("stop_loss_pct", -0.080),
            min_breakout_pct=mom.get("min_breakout_pct", 0.03),
            buy_time_limit_enabled=mom.get("buy_time_limit_enabled", True),
            buy_time_end=mom.get("buy_time_end", "11:30"),
            atr_stop_enabled=mom.get("atr_stop_enabled", False),
            atr_stop_multiplier=mom.get("atr_stop_multiplier", 1.5),
            atr_stop_min_pct=mom.get("atr_stop_min_pct", 0.015),
            atr_stop_max_pct=mom.get("atr_stop_max_pct", 0.080),
            atr_tp_enabled=mom.get("atr_tp_enabled", False),
            atr_trail_enabled=mom.get("atr_trail_enabled", True),
            atr_trail_multiplier=mom.get("atr_trail_multiplier", 1.0),
            atr_trail_min_pct=mom.get("atr_trail_min_pct", 0.02),
            atr_trail_max_pct=mom.get("atr_trail_max_pct", 0.10),
            breakeven_enabled=mom.get("breakeven_enabled", True),
            breakeven_trigger_pct=mom.get("breakeven_trigger_pct", 0.03),
            breakeven_offset_pct=mom.get("breakeven_offset_pct", 0.01),
            be3_atr_ratio=mom.get("be3_atr_ratio", 0.4),
            be3_stop_atr_ratio=mom.get("be3_stop_atr_ratio", 0.15),
            limit_up_exit_enabled=mom.get("limit_up_exit_enabled", True),
            limit_up_pct=mom.get("limit_up_pct", 0.30),
            limit_up_stop_floor_pct=mom.get("limit_up_stop_floor_pct", 0.99),
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
            intraday_market_filter_enabled=t.get("intraday_market_filter_enabled", True),
            intraday_check_interval_min=t.get("intraday_check_interval_min", 10),
            intraday_block_threshold=t.get("intraday_block_threshold", -0.01),
            intraday_resume_threshold=t.get("intraday_resume_threshold", -0.005),
            vi_static_pct=t.get("vi_static_pct", 0.095),
            vi_assumed_duration_sec=t.get("vi_assumed_duration_sec", 150),
            vi_suspected_duration_sec=t.get("vi_suspected_duration_sec", 60),
            order_confirmation_timeout_sec=t.get("order_confirmation_timeout_sec", 10.0),
            order_timeout_consecutive_threshold=t.get("order_timeout_consecutive_threshold", 3),
            # time_decay (phases는 list → TimeDecayPhase tuple 변환)
            time_decay_trailing_enabled=mom.get("time_decay_trailing_enabled", True),
            time_decay_min_pct_floor=mom.get("time_decay_min_pct_floor", 0.01),
            time_decay_phases=tuple(
                TimeDecayPhase(until=p["until"], multiplier=float(p["multiplier"]))
                for p in mom.get("time_decay_phases", [])
            ),
            # momentum_fade
            momentum_fade_exit_enabled=mom.get("momentum_fade_exit_enabled", True),
            momentum_fade_lookback=mom.get("momentum_fade_lookback", 10),
            momentum_fade_threshold=mom.get("momentum_fade_threshold", -0.005),
            momentum_fade_min_hold_min=mom.get("momentum_fade_min_hold_min", 15),
            momentum_fade_min_profit=mom.get("momentum_fade_min_profit", 0.01),
            # volume filters
            volume_by_time_enabled=mom.get("volume_by_time_enabled", False),
            volume_by_time_ratio=mom.get("volume_by_time_ratio", 1.5),
            breakout_volume_surge_enabled=mom.get("breakout_volume_surge_enabled", False),
            breakout_volume_surge_ratio=mom.get("breakout_volume_surge_ratio", 2.0),
            max_entry_above_breakout_pct=mom.get("max_entry_above_breakout_pct", 0.10),
            stale_position_exit_enabled=mom.get("stale_position_exit_enabled", False),
            stale_position_check_minutes=mom.get("stale_position_check_minutes", 30),
            stale_position_min_profit=mom.get("stale_position_min_profit", 0.005),
            afternoon_entry_enabled=mom.get("afternoon_entry_enabled", False),
            afternoon_end=mom.get("afternoon_end", "14:00"),
            afternoon_min_breakout_pct=mom.get("afternoon_min_breakout_pct", 0.05),
            afternoon_min_volume_ratio=mom.get("afternoon_min_volume_ratio", 3.0),
            afternoon_min_adx=mom.get("afternoon_min_adx", 25.0),
            volatility_sizing_enabled=mom.get("volatility_sizing_enabled", False),
            risk_per_trade_pct=mom.get("risk_per_trade_pct", 0.01),
            sizing_atr_multiplier=mom.get("sizing_atr_multiplier", 1.0),
            sizing_min_pct=mom.get("sizing_min_pct", 0.15),
            sizing_max_pct=mom.get("sizing_max_pct", 0.50),
            # OBI 필터
            obi_filter_enabled=mom.get("obi_filter_enabled", False),
            obi_min=mom.get("obi_min", 0.55),
            spread_max_pct=mom.get("spread_max_pct", 0.005),
            ask_wall_block_enabled=mom.get("ask_wall_block_enabled", False),
            # 활성 전략 선택
            strategy_type=s.get("strategy_type", "momentum"),
            # ORB 전략
            orb_enabled=s.get("orb", {}).get("enabled", False),
            orb_range_minutes=s.get("orb", {}).get("range_minutes", 5),
            orb_min_range_pct=s.get("orb", {}).get("min_range_pct", 0.005),
            orb_max_range_pct=s.get("orb", {}).get("max_range_pct", 0.05),
            orb_breakout_buffer=s.get("orb", {}).get("breakout_buffer", 0.0),
            orb_entry_deadline=s.get("orb", {}).get("entry_deadline", "10:00"),
            orb_sl_ratio=s.get("orb", {}).get("sl_ratio", 1.0),
            orb_tp_ratio=s.get("orb", {}).get("tp_ratio", 2.0),
            orb_use_volume_filter=s.get("orb", {}).get("use_volume_filter", True),
            orb_rvol_min=s.get("orb", {}).get("rvol_min", 1.5),
            # 갭업 눌림목 전략
            gap_pullback_enabled=gap.get("enabled", False),
            gap_pullback_min_pct=gap.get("gap_min_pct", 0.02),
            gap_pullback_max_pct=gap.get("gap_max_pct", 0.08),
            gap_pullback_min_pullback_pct=gap.get("pullback_min_pct", 0.01),
            gap_pullback_max_pullback_pct=gap.get("pullback_max_pct", 0.03),
            gap_pullback_entry_start=gap.get("entry_start", "09:00"),
            gap_pullback_entry_end=gap.get("entry_end", "09:20"),
            gap_pullback_force_close=gap.get("force_close", "09:45"),
            gap_pullback_max_positions=gap.get("max_positions", 1),
            gap_pullback_volume_ratio=gap.get("volume_ratio", 1.5),
            gap_pullback_atr_stop_mult=gap.get("atr_stop_mult", 0.5),
            # VWAP 리버전 전략
            vwap_rev_enabled=vr.get("enabled", False),
            vwap_rev_entry_deviation=vr.get("entry_deviation", -0.015),
            vwap_rev_stop_loss_pct=vr.get("stop_loss_pct", 0.015),
            vwap_rev_tp_above_vwap=vr.get("tp_above_vwap", 0.003),
            vwap_rev_entry_start=vr.get("entry_start", "09:30"),
            vwap_rev_entry_end=vr.get("entry_end", "14:00"),
            vwap_rev_min_prev_volume=vr.get("min_volume", 50000),
            vwap_rev_max_daily_drop=vr.get("max_daily_drop", -0.07),
            # 눌림목 전략
            pb_enabled=s.get("pullback", {}).get("enabled", False),
            pb_surge_pct=s.get("pullback", {}).get("surge_pct", 0.05),
            pb_pullback_depth=s.get("pullback", {}).get("pullback_depth", 0.02),
            pb_min_above_close_pct=s.get("pullback", {}).get("min_above_close_pct", 0.01),
            pb_sl_from_high_pct=s.get("pullback", {}).get("sl_from_high_pct", 0.05),
            pb_tp_above_high_pct=s.get("pullback", {}).get("tp_above_high_pct", 0.01),
            pb_entry_start=s.get("pullback", {}).get("entry_start", "09:30"),
            pb_entry_end=s.get("pullback", {}).get("entry_end", "13:00"),
            pb_min_volume=s.get("pullback", {}).get("min_volume", 50000),
            # 거래량 폭발 전략
            vs_enabled=s.get("volume_spike", {}).get("enabled", False),
            vs_lookback_minutes=s.get("volume_spike", {}).get("lookback_minutes", 10),
            vs_spike_ratio=s.get("volume_spike", {}).get("spike_ratio", 5.0),
            vs_sl_pct=s.get("volume_spike", {}).get("sl_pct", 0.02),
            vs_tp_pct=s.get("volume_spike", {}).get("tp_pct", 0.03),
            vs_entry_start=s.get("volume_spike", {}).get("entry_start", "09:30"),
            vs_entry_end=s.get("volume_spike", {}).get("entry_end", "13:00"),
            vs_min_prev_volume=s.get("volume_spike", {}).get("min_prev_volume", 50000),
            vs_min_spike_volume=s.get("volume_spike", {}).get("min_spike_volume", 10000),
            # 변동성 돌파 전략
            vb_enabled=s.get("volatility_breakout", {}).get("enabled", False),
            vb_k_value=s.get("volatility_breakout", {}).get("k_value", 0.5),
            vb_entry_deadline=s.get("volatility_breakout", {}).get("entry_deadline", "14:00"),
            vb_sl_mode=s.get("volatility_breakout", {}).get("sl_mode", "open"),
            vb_sl_pct=s.get("volatility_breakout", {}).get("sl_pct", 0.02),
            vb_tp_pct=s.get("volatility_breakout", {}).get("tp_pct", 0.03),
            vb_use_trailing=s.get("volatility_breakout", {}).get("use_trailing", True),
            vb_trail_pct=s.get("volatility_breakout", {}).get("trail_pct", 0.02),
            vb_use_volume_confirm=s.get("volatility_breakout", {}).get("use_volume_confirm", True),
            vb_min_range_pct=s.get("volatility_breakout", {}).get("min_range_pct", 0.015),
            vb_max_range_pct=s.get("volatility_breakout", {}).get("max_range_pct", 0.10),
            vb_min_prev_volume=s.get("volatility_breakout", {}).get("min_prev_volume", 50000),
        )

        # screener 섹션
        sc = cfg.get("screener", {})
        screener = ScreenerConfig(
            min_market_cap=sc.get("min_market_cap", 300_000_000_000),
            min_avg_volume_amount=sc.get("min_avg_volume_amount", 5_000_000_000),
            volume_surge_ratio=sc.get("volume_surge_ratio", 1.5),
            min_atr_pct=sc.get("min_atr_pct", 0.06),
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

        # condition_search 섹션 (영웅문 저장 조건식 연동)
        cs = cfg.get("condition_search", {})
        condition_search = ConditionSearchConfig(
            enabled=cs.get("enabled", True),
            condition_name=cs.get("condition_name", "day_momentum"),
            max_watch_stocks=cs.get("max_watch_stocks", 50),
        )

        # intraday_search 섹션 (장중 동적 유니버스 확장)
        is_raw = s.get("intraday_search", {})
        intraday_search = IntradaySearchConfig(
            enabled=is_raw.get("enabled", False),
            condition_name=is_raw.get("condition_name", "intraday_leader"),
            schedule=tuple(is_raw.get("schedule", ["09:05", "09:15", "09:30", "10:00", "10:30", "11:00", "11:30"])),
            max_add_per_search=is_raw.get("max_add_per_search", 10),
            max_total_added=is_raw.get("max_total_added", 30),
        )

        # notifications 섹션 (Phase 3-B / ADR-008)
        n = cfg.get("notifications", {})
        notifications = NotificationConfig(
            daily_reset=n.get("daily_reset", True),
            ohlcv_refresh=n.get("ohlcv_refresh", True),
            token_refresh_failure=n.get("token_refresh_failure", True),
            trade_execution=n.get("trade_execution", True),
            daily_report=n.get("daily_report", True),
            system_start=n.get("system_start", True),
            system_stop=n.get("system_stop", True),
            uptime_sanity=n.get("uptime_sanity", True),
            universe_refresh=n.get("universe_refresh", True),
            candle_collection=n.get("candle_collection", True),
            ws_critical_failure=n.get("ws_critical_failure", True),
            ws_auto_recovery=n.get("ws_auto_recovery", True),
        )

        return AppConfig(
            kiwoom=kiwoom,
            telegram=telegram,
            trading=trading,
            screener=screener,
            backtest=backtest,
            notifications=notifications,
            condition_search=condition_search,
            intraday_search=intraday_search,
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            debug=os.getenv("DEBUG", "false").lower() == "true",
            paper_mode=cfg.get("paper_mode", True),
            selector=selector,
            force_strategy=s.get("force", ""),
        )

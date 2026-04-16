from config.settings import TradingConfig


def test_trading_config_defaults():
    tc = TradingConfig()
    assert tc.daily_max_loss_pct == -0.02
    assert tc.tp1_pct == 0.99  # ADR-010: dead config (Pure trailing)
    assert tc.atr_tp_enabled is False  # ADR-010: TP1 폐기
    assert tc.atr_trail_multiplier == 1.0  # ADR-010: 2.5 → 1.0
    assert tc.force_close_time == "15:10"
    assert tc.max_trades_per_day == 1
    assert tc.max_positions == 3
    assert tc.cooldown_minutes == 999
    assert tc.momentum_stop_loss_pct == -0.080  # ADR-010: 고정 -8%
    assert tc.atr_stop_enabled is False  # ADR-010: ATR stop 비활성
    assert tc.initial_capital == 1_000_000


def test_trading_config_defaults_match_yaml():
    """TradingConfig 기본값과 ADR-010 결정 일치 확인."""
    tc = TradingConfig()
    assert tc.atr_tp_enabled is False  # Pure trailing
    assert tc.atr_trail_enabled is True
    assert tc.atr_trail_multiplier == 1.0
    assert tc.max_trades_per_day == 1
    assert tc.max_positions == 3
    assert tc.cooldown_minutes == 999
    assert tc.momentum_stop_loss_pct == -0.080  # ADR-010: 고정 -8%
    assert tc.atr_stop_enabled is False  # ADR-010: ATR stop 비활성
    assert tc.initial_capital == 1_000_000


def test_app_config_with_fixture(app_config):
    assert app_config.kiwoom.app_key == "test_key"
    assert app_config.db_path == ":memory:"


def test_backtest_config_defaults():
    """BacktestConfig 기본값이 config.yaml과 일치."""
    from config.settings import BacktestConfig
    bc = BacktestConfig()
    assert bc.commission == 0.00015
    assert bc.tax == 0.0015
    assert bc.slippage == 0.0003
    assert bc.initial_capital == 1_000_000


def test_market_calendar_2027():
    """2027년 공휴일이 거래일에서 제외."""
    from datetime import date
    from utils.market_calendar import is_trading_day
    assert is_trading_day(date(2027, 1, 1)) is False   # 신정
    assert is_trading_day(date(2027, 3, 1)) is False   # 삼일절
    assert is_trading_day(date(2027, 1, 4)) is True    # 평일

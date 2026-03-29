from config.settings import AppConfig, TradingConfig


def test_trading_config_defaults():
    tc = TradingConfig()
    assert tc.stop_loss_pct == -0.015
    assert tc.daily_max_loss_pct == -0.02
    assert tc.tp1_pct == 0.03
    assert tc.force_close_time == "15:10"
    assert tc.max_trades_per_day == 3
    assert tc.cooldown_minutes == 15
    assert tc.orb_volume_ratio == 0.0
    assert tc.pullback_min_gain_pct == 0.04
    assert tc.pullback_stop_loss_pct == -0.018
    assert tc.momentum_stop_loss_pct == -0.008
    assert tc.initial_capital == 1_000_000


def test_trading_config_defaults_match_yaml():
    """TradingConfig 기본값과 config.yaml 값이 일치하는지 확인."""
    tc = TradingConfig()
    assert tc.tp1_pct == 0.03
    assert tc.max_trades_per_day == 3
    assert tc.cooldown_minutes == 15
    assert tc.orb_volume_ratio == 0.0
    assert tc.pullback_min_gain_pct == 0.04
    assert tc.pullback_stop_loss_pct == -0.018
    assert tc.momentum_stop_loss_pct == -0.008
    assert tc.initial_capital == 1_000_000


def test_app_config_with_fixture(app_config):
    assert app_config.kiwoom.app_key == "test_key"
    assert app_config.db_path == ":memory:"

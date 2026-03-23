from config.settings import AppConfig, TradingConfig


def test_trading_config_defaults():
    tc = TradingConfig()
    assert tc.stop_loss_pct == -0.015
    assert tc.daily_max_loss_pct == -0.02
    assert tc.tp1_pct == 0.02
    assert tc.force_close_time == "15:10"


def test_app_config_with_fixture(app_config):
    assert app_config.kiwoom.app_key == "test_key"
    assert app_config.db_path == ":memory:"

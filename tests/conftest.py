"""공통 fixture."""

import asyncio
import pytest


@pytest.fixture(scope="session", autouse=True)
def event_loop_policy():
    """Windows SelectorEventLoop 강제."""
    import sys
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


@pytest.fixture
def app_config():
    """테스트용 AppConfig (환경변수 불필요)."""
    from config.settings import KiwoomConfig, TelegramConfig, AppConfig

    return AppConfig(
        kiwoom=KiwoomConfig(
            app_key="test_key",
            secret_key="test_secret",
            account_no="12345678",
        ),
        telegram=TelegramConfig(
            bot_token="test_token",
            chat_id="test_chat",
        ),
        db_path=":memory:",
    )

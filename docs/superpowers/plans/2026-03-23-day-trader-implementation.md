# Day-Trader 단타 자동매매 시스템 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 키움증권 REST API + WebSocket 기반 단타 자동매매 시스템을 asyncio 파이프라인으로 구축한다.

**Architecture:** WS 수신 → Queue → 캔들 빌더 → Queue → 전략 엔진 → Queue → 주문 실행의 asyncio 파이프라인. swing-trader의 REST/WS/Rate Limiter 모듈을 재활용하고, PyQt5 콜백을 asyncio.Queue로 교체한다. SQLite로 독립 DB(daytrader.db) 운영.

**Tech Stack:** Python 3.12, asyncio (SelectorEventLoop), aiohttp, websockets, pandas, pandas-ta, APScheduler, loguru, vectorbt, SQLite

**Source Reference:** PRD → `docs/daytrading_prd.md`, 기존 코드 → `D:\project\swing-trader\src\`

---

## 파일 구조

```
day-trader/
├── main.py                          # 엔트리포인트, asyncio 파이프라인 조립
├── requirements.txt                 # 의존성
├── .env.example                     # 환경변수 템플릿
├── .gitignore                       # git 제외 목록
├── config/
│   ├── __init__.py
│   └── settings.py                  # 전역 파라미터 (손절률, 익절목표, API 설정)
├── core/
│   ├── __init__.py
│   ├── auth.py                      # OAuth2 토큰 발급/갱신
│   ├── kiwoom_rest.py               # REST API 클라이언트 (swing-trader 포팅)
│   ├── kiwoom_ws.py                 # WebSocket 클라이언트 (swing-trader 포팅 + Queue 통합)
│   ├── rate_limiter.py              # 비동기 Rate Limiter (swing-trader 재사용)
│   ├── order_manager.py             # 주문 실행기 (분할매수/매도, Lock, 체결확인)
│   └── retry.py                     # Exponential backoff + Jitter 재시도
├── strategy/
│   ├── __init__.py
│   ├── base_strategy.py             # ABC 전략 베이스 클래스
│   ├── orb_strategy.py              # ORB (Opening Range Breakout)
│   ├── vwap_strategy.py             # VWAP 회귀
│   ├── momentum_strategy.py         # 모멘텀 브레이크아웃
│   └── pullback_strategy.py         # 눌림목 매매
├── screener/
│   ├── __init__.py
│   ├── pre_market.py                # 장 전 스크리닝 (08:30)
│   ├── strategy_selector.py         # 전략 자동 선택
│   └── realtime_scanner.py          # 장 중 실시간 스캐닝
├── risk/
│   ├── __init__.py
│   └── risk_manager.py              # 손절, 일일한도, 강제청산, 연속손실, 장애복구
├── data/
│   ├── __init__.py
│   ├── candle_builder.py            # 실시간 분봉 생성 + VWAP
│   └── db_manager.py               # SQLite CRUD (aiosqlite)
├── notification/
│   ├── __init__.py
│   └── telegram_bot.py             # 텔레그램 알림 (aiohttp 기반 비동기)
├── backtest/
│   ├── __init__.py
│   ├── data_collector.py           # 분봉 데이터 수집 배치
│   └── backtester.py               # vectorbt 백테스트 엔진
└── tests/
    ├── __init__.py
    ├── conftest.py                  # 공통 fixture
    ├── test_settings.py
    ├── test_auth.py
    ├── test_retry.py
    ├── test_rate_limiter.py
    ├── test_kiwoom_rest.py
    ├── test_kiwoom_ws.py
    ├── test_candle_builder.py
    ├── test_db_manager.py
    ├── test_risk_manager.py
    ├── test_order_manager.py
    ├── test_base_strategy.py
    ├── test_orb_strategy.py
    ├── test_vwap_strategy.py
    ├── test_momentum_strategy.py
    ├── test_pullback_strategy.py
    ├── test_pre_market_screener.py
    ├── test_strategy_selector.py
    ├── test_telegram_bot.py
    └── test_pipeline.py
```

---

## Phase 1: 기반 구축 (Task 1–7)

### Task 1: 프로젝트 초기화 및 의존성

**Files:**
- Create: `requirements.txt`
- Create: `.env.example`
- Create: `.gitignore`
- Create: `config/__init__.py`
- Create: `config/settings.py`

- [ ] **Step 1: requirements.txt 생성**

```txt
# Core
aiohttp>=3.9,<4.0
websockets>=12.0,<14.0
pandas>=2.2,<3.0
numpy>=1.26,<2.0
pandas-ta>=0.3.14b
python-dotenv>=1.0
apscheduler>=3.10,<4.0
loguru>=0.7
aiosqlite>=0.19

# Backtest
vectorbt>=0.26

# Dev
pytest>=8.0
pytest-asyncio>=0.23
black>=24.0
ruff>=0.3
```

- [ ] **Step 2: .env.example 생성**

```
KIWOOM_APP_KEY=
KIWOOM_SECRET_KEY=
KIWOOM_ACCOUNT_NO=
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
LOG_LEVEL=INFO
DEBUG=false
NO_COLOR=
```

- [ ] **Step 3: .gitignore 생성**

```
.venv/
.env
*.pyc
__pycache__/
logs/
*.db
.idea/
.vscode/settings.json
*.egg-info/
dist/
build/
.pytest_cache/
```

- [ ] **Step 4: config/settings.py 작성**

```python
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
```

- [ ] **Step 5: config/__init__.py 작성**

```python
from config.settings import AppConfig

__all__ = ["AppConfig"]
```

- [ ] **Step 6: venv 생성 및 의존성 설치**

```bash
cd D:\project\day-trader
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install --upgrade pip
pip install -r requirements.txt
```

- [ ] **Step 7: 테스트 인프라 설정**

`tests/__init__.py` (빈 파일), `tests/conftest.py`:

```python
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
```

- [ ] **Step 8: settings 테스트 작성 및 실행**

`tests/test_settings.py`:

```python
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
```

Run: `pytest tests/test_settings.py -v`
Expected: 2 PASS

- [ ] **Step 9: Commit**

```bash
git init
git add requirements.txt .env.example .gitignore config/ tests/__init__.py tests/conftest.py tests/test_settings.py
git commit -m "feat: 프로젝트 초기화 — 의존성, 설정, 테스트 인프라"
```

---

### Task 2: Rate Limiter (swing-trader 포팅)

**Files:**
- Create: `core/__init__.py`
- Create: `core/rate_limiter.py`
- Test: `tests/test_rate_limiter.py`
- Reference: `D:\project\swing-trader\src\broker\rate_limiter.py`

- [ ] **Step 1: 테스트 작성**

```python
"""tests/test_rate_limiter.py"""

import asyncio
import time
import pytest

from core.rate_limiter import AsyncRateLimiter


@pytest.mark.asyncio
async def test_allows_within_limit():
    limiter = AsyncRateLimiter(max_calls=3, period=1.0)
    for _ in range(3):
        await limiter.wait()
    # 3 calls should complete without significant delay


@pytest.mark.asyncio
async def test_blocks_over_limit():
    limiter = AsyncRateLimiter(max_calls=2, period=0.5)
    start = time.monotonic()
    for _ in range(3):
        await limiter.wait()
    elapsed = time.monotonic() - start
    assert elapsed >= 0.4  # 3rd call should wait ~0.5s


@pytest.mark.asyncio
async def test_can_call_check():
    limiter = AsyncRateLimiter(max_calls=1, period=1.0)
    assert limiter.can_call() is True
    await limiter.wait()
    assert limiter.can_call() is False
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_rate_limiter.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: rate_limiter.py 구현**

swing-trader의 `AsyncRateLimiter`를 그대로 포팅:

```python
"""core/rate_limiter.py — 비동기 슬라이딩 윈도우 Rate Limiter."""

import asyncio
import time
from collections import deque


class AsyncRateLimiter:
    """초당 N회 요청 제한 (슬라이딩 윈도우)."""

    def __init__(self, max_calls: int = 5, period: float = 1.0):
        self._max_calls = max_calls
        self._period = period
        self._calls: deque[float] = deque()

    def can_call(self) -> bool:
        now = time.monotonic()
        self._purge(now)
        return len(self._calls) < self._max_calls

    async def wait(self) -> None:
        while True:
            now = time.monotonic()
            self._purge(now)
            if len(self._calls) < self._max_calls:
                self._calls.append(now)
                return
            sleep_time = self._calls[0] + self._period - now
            if sleep_time > 0:
                await asyncio.sleep(sleep_time)

    def _purge(self, now: float) -> None:
        cutoff = now - self._period
        while self._calls and self._calls[0] <= cutoff:
            self._calls.popleft()
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_rate_limiter.py -v`
Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add core/__init__.py core/rate_limiter.py tests/test_rate_limiter.py
git commit -m "feat: AsyncRateLimiter 구현 (swing-trader 포팅)"
```

---

### Task 3: Retry 모듈

**Files:**
- Create: `core/retry.py`
- Test: `tests/test_retry.py`
- PRD: F-AUTH-03

- [ ] **Step 1: 테스트 작성**

```python
"""tests/test_retry.py"""

import pytest
from core.retry import retry_async


@pytest.mark.asyncio
async def test_retry_succeeds_first_try():
    call_count = 0

    async def succeed():
        nonlocal call_count
        call_count += 1
        return "ok"

    result = await retry_async(succeed, max_retries=3, base_delay=0.01)
    assert result == "ok"
    assert call_count == 1


@pytest.mark.asyncio
async def test_retry_succeeds_after_failures():
    call_count = 0

    async def fail_twice():
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise ConnectionError("network error")
        return "ok"

    result = await retry_async(fail_twice, max_retries=3, base_delay=0.01)
    assert result == "ok"
    assert call_count == 3


@pytest.mark.asyncio
async def test_retry_exhausted_raises():
    async def always_fail():
        raise ConnectionError("down")

    with pytest.raises(ConnectionError):
        await retry_async(always_fail, max_retries=2, base_delay=0.01)


@pytest.mark.asyncio
async def test_retry_respects_retry_after():
    """429 응답의 Retry-After 헤더 준수 테스트."""
    call_count = 0

    class RateLimitError(Exception):
        def __init__(self, retry_after):
            self.retry_after = retry_after

    async def rate_limited():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RateLimitError(retry_after=0.05)
        return "ok"

    result = await retry_async(
        rate_limited, max_retries=3, base_delay=0.01,
        retry_after_attr="retry_after",
    )
    assert result == "ok"
    assert call_count == 2
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_retry.py -v`
Expected: FAIL

- [ ] **Step 3: retry.py 구현**

```python
"""core/retry.py — Exponential Backoff + Jitter 재시도."""

import asyncio
import random
from typing import Callable, Any

from loguru import logger


async def retry_async(
    func: Callable[..., Any],
    *args: Any,
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    retry_after_attr: str | None = None,
    **kwargs: Any,
) -> Any:
    """비동기 함수를 재시도한다.

    Args:
        func: 재시도할 비동기 함수
        max_retries: 최대 재시도 횟수
        base_delay: 기본 대기 시간 (초)
        max_delay: 최대 대기 시간 (초)
        retry_after_attr: 예외에서 대기 시간을 읽을 속성명
    """
    last_exception = None

    for attempt in range(1, max_retries + 1):
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            last_exception = e
            if attempt == max_retries:
                logger.error(f"재시도 소진 ({max_retries}회): {e}")
                raise

            # Retry-After 헤더 우선
            delay = base_delay
            if retry_after_attr and hasattr(e, retry_after_attr):
                delay = float(getattr(e, retry_after_attr))
            else:
                # Exponential backoff + jitter
                delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
                delay += random.uniform(0, delay * 0.1)

            logger.warning(f"재시도 {attempt}/{max_retries} — {delay:.2f}초 후 ({e})")
            await asyncio.sleep(delay)

    raise last_exception  # unreachable, but type checker needs it
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_retry.py -v`
Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
git add core/retry.py tests/test_retry.py
git commit -m "feat: retry_async — Exponential Backoff + Jitter + Retry-After"
```

---

### Task 4: OAuth2 인증

**Files:**
- Create: `core/auth.py`
- Test: `tests/test_auth.py`
- PRD: F-AUTH-01, F-AUTH-02

- [ ] **Step 1: 테스트 작성**

```python
"""tests/test_auth.py"""

import pytest
from unittest.mock import AsyncMock, patch
from datetime import datetime, timedelta

from core.auth import TokenManager


@pytest.mark.asyncio
async def test_get_token_fetches_new():
    tm = TokenManager(app_key="k", secret_key="s", base_url="http://test")
    mock_resp = {
        "access_token": "tok123",
        "token_token_expired": (datetime.now() + timedelta(hours=12)).strftime(
            "%Y-%m-%d %H:%M:%S"
        ),
    }
    with patch("core.auth.aiohttp.ClientSession") as mock_session_cls:
        mock_session = AsyncMock()
        mock_session_cls.return_value.__aenter__.return_value = mock_session
        mock_resp_obj = AsyncMock()
        mock_resp_obj.json = AsyncMock(return_value=mock_resp)
        mock_resp_obj.raise_for_status = lambda: None
        mock_session.post.return_value.__aenter__.return_value = mock_resp_obj

        token = await tm.get_token()
        assert token == "tok123"


@pytest.mark.asyncio
async def test_get_token_reuses_valid():
    tm = TokenManager(app_key="k", secret_key="s", base_url="http://test")
    tm._access_token = "cached"
    tm._token_expires = datetime.now() + timedelta(hours=1)

    token = await tm.get_token()
    assert token == "cached"


@pytest.mark.asyncio
async def test_get_token_refreshes_near_expiry():
    tm = TokenManager(app_key="k", secret_key="s", base_url="http://test")
    tm._access_token = "old"
    tm._token_expires = datetime.now() + timedelta(minutes=5)  # 10분 이내

    mock_resp = {
        "access_token": "new_tok",
        "token_token_expired": (datetime.now() + timedelta(hours=12)).strftime(
            "%Y-%m-%d %H:%M:%S"
        ),
    }
    with patch("core.auth.aiohttp.ClientSession") as mock_session_cls:
        mock_session = AsyncMock()
        mock_session_cls.return_value.__aenter__.return_value = mock_session
        mock_resp_obj = AsyncMock()
        mock_resp_obj.json = AsyncMock(return_value=mock_resp)
        mock_resp_obj.raise_for_status = lambda: None
        mock_session.post.return_value.__aenter__.return_value = mock_resp_obj

        token = await tm.get_token()
        assert token == "new_tok"
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_auth.py -v`
Expected: FAIL

- [ ] **Step 3: auth.py 구현**

```python
"""core/auth.py — OAuth2 토큰 발급 및 자동 갱신."""

from datetime import datetime, timedelta

import aiohttp
from loguru import logger


class TokenManager:
    """키움 REST API OAuth2 토큰 관리."""

    REFRESH_MARGIN = timedelta(minutes=10)

    def __init__(self, app_key: str, secret_key: str, base_url: str):
        self._app_key = app_key
        self._secret_key = secret_key
        self._base_url = base_url
        self._access_token: str | None = None
        self._token_expires: datetime | None = None

    async def get_token(self) -> str:
        """유효한 토큰 반환. 만료 임박 시 자동 갱신."""
        if self._is_valid():
            return self._access_token

        await self._fetch_token()
        return self._access_token

    def _is_valid(self) -> bool:
        if not self._access_token or not self._token_expires:
            return False
        return datetime.now() + self.REFRESH_MARGIN < self._token_expires

    async def _fetch_token(self) -> None:
        url = f"{self._base_url}/oauth2/tokenP"
        body = {
            "grant_type": "client_credentials",
            "appkey": self._app_key,
            "appsecret": self._secret_key,
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=body) as resp:
                resp.raise_for_status()
                data = await resp.json()

        self._access_token = data["access_token"]
        expires_str = data.get("token_token_expired", "")
        if expires_str:
            self._token_expires = datetime.strptime(expires_str, "%Y-%m-%d %H:%M:%S")
        else:
            self._token_expires = datetime.now() + timedelta(hours=12)

        logger.info(f"토큰 발급 완료 — 만료: {self._token_expires}")
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_auth.py -v`
Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add core/auth.py tests/test_auth.py
git commit -m "feat: TokenManager — OAuth2 토큰 발급/자동 갱신"
```

---

### Task 5: DB 매니저

**Files:**
- Create: `data/__init__.py`
- Create: `data/db_manager.py`
- Test: `tests/test_db_manager.py`
- PRD: 7.2 스키마

- [ ] **Step 1: 테스트 작성**

```python
"""tests/test_db_manager.py"""

import pytest
from data.db_manager import DbManager


@pytest.mark.asyncio
async def test_init_creates_tables():
    db = DbManager(":memory:")
    await db.init()
    tables = await db.fetch_all(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    names = [row["name"] for row in tables]
    assert "trades" in names
    assert "positions" in names
    assert "daily_pnl" in names
    assert "intraday_candles" in names
    assert "screener_results" in names
    assert "system_log" in names
    await db.close()


@pytest.mark.asyncio
async def test_insert_and_fetch_trade():
    db = DbManager(":memory:")
    await db.init()
    await db.execute(
        "INSERT INTO trades (ticker,strategy,side,order_type,price,qty,amount,traded_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        ("005930", "orb", "buy", "market", 70000, 10, 700000, "2026-03-23T09:10:00"),
    )
    rows = await db.fetch_all("SELECT * FROM trades WHERE ticker='005930'")
    assert len(rows) == 1
    assert rows[0]["price"] == 70000
    await db.close()


@pytest.mark.asyncio
async def test_candle_unique_constraint():
    db = DbManager(":memory:")
    await db.init()
    params = ("005930", "1m", "2026-03-23T09:01:00", 70000, 70500, 69500, 70200, 1000, 70100)
    await db.execute(
        "INSERT INTO intraday_candles (ticker,tf,ts,open,high,low,close,volume,vwap) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        params,
    )
    # 중복 삽입은 무시 (INSERT OR IGNORE)
    await db.execute_safe(
        "INSERT OR IGNORE INTO intraday_candles (ticker,tf,ts,open,high,low,close,volume,vwap) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        params,
    )
    rows = await db.fetch_all("SELECT * FROM intraday_candles")
    assert len(rows) == 1
    await db.close()
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_db_manager.py -v`
Expected: FAIL

- [ ] **Step 3: db_manager.py 구현**

```python
"""data/db_manager.py — SQLite 비동기 CRUD."""

import aiosqlite
from loguru import logger

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS trades (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker       TEXT NOT NULL,
    strategy     TEXT NOT NULL,
    side         TEXT NOT NULL,
    order_type   TEXT NOT NULL,
    price        REAL NOT NULL,
    qty          INTEGER NOT NULL,
    amount       REAL NOT NULL,
    pnl          REAL,
    pnl_pct      REAL,
    exit_reason  TEXT,
    traded_at    TEXT NOT NULL,
    created_at   TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS positions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker        TEXT NOT NULL,
    strategy      TEXT NOT NULL,
    entry_price   REAL NOT NULL,
    qty           INTEGER NOT NULL,
    remaining_qty INTEGER NOT NULL,
    stop_loss     REAL NOT NULL,
    tp1_price     REAL,
    tp2_price     REAL,
    trailing_pct  REAL,
    status        TEXT DEFAULT 'open',
    opened_at     TEXT NOT NULL,
    closed_at     TEXT
);

CREATE TABLE IF NOT EXISTS intraday_candles (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker  TEXT NOT NULL,
    tf      TEXT NOT NULL,
    ts      TEXT NOT NULL,
    open    REAL,
    high    REAL,
    low     REAL,
    close   REAL,
    volume  INTEGER,
    vwap    REAL,
    UNIQUE(ticker, tf, ts)
);

CREATE TABLE IF NOT EXISTS daily_pnl (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    date          TEXT NOT NULL UNIQUE,
    strategy      TEXT,
    total_trades  INTEGER DEFAULT 0,
    wins          INTEGER DEFAULT 0,
    losses        INTEGER DEFAULT 0,
    win_rate      REAL,
    total_pnl     REAL DEFAULT 0,
    max_drawdown  REAL,
    created_at    TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS screener_results (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    date          TEXT NOT NULL,
    ticker        TEXT NOT NULL,
    score         REAL,
    strategy_hint TEXT,
    selected      INTEGER DEFAULT 0,
    created_at    TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS system_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    level      TEXT NOT NULL,
    event      TEXT NOT NULL,
    detail     TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime'))
);
"""


class DbManager:
    """aiosqlite 기반 비동기 DB 관리."""

    def __init__(self, db_path: str):
        self._db_path = db_path
        self._conn: aiosqlite.Connection | None = None

    async def init(self) -> None:
        self._conn = await aiosqlite.connect(self._db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.executescript(SCHEMA_SQL)
        await self._conn.commit()
        logger.info(f"DB 초기화 완료: {self._db_path}")

    async def execute(self, sql: str, params: tuple = ()) -> int:
        cursor = await self._conn.execute(sql, params)
        await self._conn.commit()
        return cursor.lastrowid

    async def execute_safe(self, sql: str, params: tuple = ()) -> int | None:
        """실패해도 예외를 던지지 않음 (로그만 기록)."""
        try:
            return await self.execute(sql, params)
        except Exception as e:
            logger.warning(f"DB execute_safe 실패: {e}")
            return None

    async def fetch_all(self, sql: str, params: tuple = ()) -> list[dict]:
        cursor = await self._conn.execute(sql, params)
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def fetch_one(self, sql: str, params: tuple = ()) -> dict | None:
        cursor = await self._conn.execute(sql, params)
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_db_manager.py -v`
Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add data/__init__.py data/db_manager.py tests/test_db_manager.py
git commit -m "feat: DbManager — aiosqlite 비동기 CRUD + 6개 테이블 스키마"
```

---

### Task 6: REST API 클라이언트

**Files:**
- Create: `core/kiwoom_rest.py`
- Test: `tests/test_kiwoom_rest.py` (유닛 — mock 기반)
- Reference: `D:\project\swing-trader\src\broker\rest_client.py`
- PRD: F-AUTH-03, F-ORD-05

- [ ] **Step 1: 테스트 작성**

```python
"""tests/test_kiwoom_rest.py"""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from core.kiwoom_rest import KiwoomRestClient


@pytest.fixture
def rest_client(app_config):
    return KiwoomRestClient(
        config=app_config.kiwoom,
        token_manager=AsyncMock(get_token=AsyncMock(return_value="test_token")),
    )


@pytest.mark.asyncio
async def test_request_adds_auth_headers(rest_client):
    with patch("core.kiwoom_rest.aiohttp.ClientSession") as mock_cls:
        mock_session = AsyncMock()
        mock_cls.return_value.__aenter__.return_value = mock_session
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"output": []})
        mock_resp.raise_for_status = MagicMock()
        mock_session.request.return_value.__aenter__.return_value = mock_resp

        result = await rest_client.request("GET", "/test", tr_id="TEST01")
        call_kwargs = mock_session.request.call_args
        headers = call_kwargs.kwargs.get("headers", {})
        assert headers["authorization"] == "Bearer test_token"


@pytest.mark.asyncio
async def test_get_account_balance(rest_client):
    mock_data = {"output2": [{"pdno": "005930", "hldg_qty": "10"}]}
    with patch.object(rest_client, "request", return_value=mock_data):
        result = await rest_client.get_account_balance()
        assert result == mock_data
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_kiwoom_rest.py -v`
Expected: FAIL

- [ ] **Step 3: kiwoom_rest.py 구현**

```python
"""core/kiwoom_rest.py — 키움 REST API 클라이언트."""

import aiohttp
from loguru import logger

from core.auth import TokenManager
from core.rate_limiter import AsyncRateLimiter
from core.retry import retry_async
from config.settings import KiwoomConfig


class KiwoomRestClient:
    """키움증권 REST API 비동기 클라이언트."""

    def __init__(
        self,
        config: KiwoomConfig,
        token_manager: TokenManager,
        rate_limiter: AsyncRateLimiter | None = None,
    ):
        self._config = config
        self._token_manager = token_manager
        self._rate_limiter = rate_limiter or AsyncRateLimiter(
            max_calls=config.rate_limit_calls,
            period=config.rate_limit_period,
        )

    async def request(
        self,
        method: str,
        endpoint: str,
        tr_id: str = "",
        data: dict | None = None,
        params: dict | None = None,
    ) -> dict:
        """범용 API 요청 (재시도 + Rate Limit 포함)."""
        return await retry_async(
            self._do_request,
            method, endpoint, tr_id, data, params,
            max_retries=3,
            base_delay=1.0,
        )

    async def _do_request(
        self,
        method: str,
        endpoint: str,
        tr_id: str,
        data: dict | None,
        params: dict | None,
    ) -> dict:
        if self._rate_limiter:
            await self._rate_limiter.wait()

        token = await self._token_manager.get_token()
        headers = {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {token}",
            "appkey": self._config.app_key,
            "appsecret": self._config.secret_key,
        }
        if tr_id:
            headers["tr_id"] = tr_id

        url = f"{self._config.rest_base_url}{endpoint}"

        async with aiohttp.ClientSession() as session:
            async with session.request(
                method, url, headers=headers, json=data, params=params, timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                resp.raise_for_status()
                result = await resp.json()
                logger.debug(f"REST {method} {endpoint} → {resp.status}")
                return result

    async def send_order(
        self,
        ticker: str,
        qty: int,
        price: int,
        side: str,
        order_type: str = "00",
    ) -> dict:
        """주문 발송. side: 'buy'/'sell', order_type: '00'시장가/'01'지정가."""
        tr_id = "TTTC0802U" if side == "buy" else "TTTC0801U"
        body = {
            "CANO": self._config.account_no[:8],
            "ACNT_PRDT_CD": self._config.account_no[8:] or "01",
            "PDNO": ticker,
            "ORD_DVSN": order_type,
            "ORD_QTY": str(qty),
            "ORD_UNPR": str(price),
        }
        return await self.request("POST", "/uapi/domestic-stock/v1/trading/order-cash", tr_id=tr_id, data=body)

    async def get_account_balance(self) -> dict:
        """계좌 잔고 조회."""
        params = {
            "CANO": self._config.account_no[:8],
            "ACNT_PRDT_CD": self._config.account_no[8:] or "01",
            "AFHR_FLPR_YN": "N",
            "OFL_YN": "",
            "INQR_DVSN": "02",
            "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "00",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        }
        return await self.request(
            "GET", "/uapi/domestic-stock/v1/trading/inquire-balance",
            tr_id="TTTC8434R", params=params,
        )

    async def get_current_price(self, ticker: str) -> dict:
        """현재가 조회."""
        params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker}
        return await self.request(
            "GET", "/uapi/domestic-stock/v1/quotations/inquire-price",
            tr_id="FHKST01010100", params=params,
        )

    async def get_minute_ohlcv(self, ticker: str, time_unit: str = "1") -> dict:
        """분봉 조회."""
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": ticker,
            "FID_ETC_CLS_CODE": "",
            "FID_INPUT_HOUR_1": time_unit,
            "FID_PW_DATA_INCU_YN": "Y",
        }
        return await self.request(
            "GET", "/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice",
            tr_id="FHKST03010200", params=params,
        )
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_kiwoom_rest.py -v`
Expected: 2 PASS

- [ ] **Step 5: Commit**

```bash
git add core/kiwoom_rest.py tests/test_kiwoom_rest.py
git commit -m "feat: KiwoomRestClient — REST API 클라이언트 (재시도, Rate Limit)"
```

---

### Task 7: WebSocket 클라이언트

**Files:**
- Create: `core/kiwoom_ws.py`
- Test: `tests/test_kiwoom_ws.py`
- Reference: `D:\project\swing-trader\src\broker\ws_client.py`
- PRD: F-WS-01, F-WS-02, F-WS-03

- [ ] **Step 1: 테스트 작성**

```python
"""tests/test_kiwoom_ws.py"""

import asyncio
import pytest
from unittest.mock import AsyncMock, patch

from core.kiwoom_ws import KiwoomWebSocketClient


@pytest.mark.asyncio
async def test_subscribe_builds_message():
    ws = KiwoomWebSocketClient(
        ws_url="ws://test",
        token_manager=AsyncMock(get_token=AsyncMock(return_value="tok")),
    )
    ws._ws = AsyncMock()  # mock websocket connection

    await ws.subscribe("005930", "H0STCNT0")  # 주식체결
    ws._ws.send.assert_called_once()
    sent = ws._ws.send.call_args[0][0]
    assert "005930" in sent
    assert "H0STCNT0" in sent


@pytest.mark.asyncio
async def test_tick_queue_receives_data():
    tick_queue = asyncio.Queue()
    ws = KiwoomWebSocketClient(
        ws_url="ws://test",
        token_manager=AsyncMock(get_token=AsyncMock(return_value="tok")),
        tick_queue=tick_queue,
    )
    # 직접 dispatch 호출로 queue 전달 확인
    await ws._dispatch_tick({"ticker": "005930", "price": 70000})
    item = await asyncio.wait_for(tick_queue.get(), timeout=1.0)
    assert item["ticker"] == "005930"


@pytest.mark.asyncio
async def test_reconnect_restores_subscriptions():
    ws = KiwoomWebSocketClient(
        ws_url="ws://test",
        token_manager=AsyncMock(get_token=AsyncMock(return_value="tok")),
    )
    ws._subscriptions = {"H0STCNT0": ["005930", "035720"]}
    ws._ws = AsyncMock()

    await ws._restore_subscriptions()
    assert ws._ws.send.call_count == 2
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_kiwoom_ws.py -v`
Expected: FAIL

- [ ] **Step 3: kiwoom_ws.py 구현**

```python
"""core/kiwoom_ws.py — 키움 WebSocket 클라이언트 (asyncio Queue 통합)."""

import asyncio
import json

import websockets
from loguru import logger

from core.auth import TokenManager


class KiwoomWebSocketClient:
    """키움 WebSocket — 체결/호가/체결통보 구독, Queue 기반 데이터 전달."""

    HEARTBEAT_INTERVAL = 30  # seconds
    RECONNECT_BASE_DELAY = 2
    RECONNECT_MAX_DELAY = 60

    def __init__(
        self,
        ws_url: str,
        token_manager: TokenManager,
        tick_queue: asyncio.Queue | None = None,
        order_queue: asyncio.Queue | None = None,
    ):
        self._ws_url = ws_url
        self._token_manager = token_manager
        self._tick_queue = tick_queue
        self._order_queue = order_queue
        self._ws = None
        self._subscriptions: dict[str, list[str]] = {}
        self._listen_task: asyncio.Task | None = None
        self._running = False

    async def connect(self) -> None:
        """WebSocket 연결 및 수신 루프 시작."""
        await self._establish_connection()
        self._running = True
        self._listen_task = asyncio.create_task(self._listen_loop())
        logger.info("WebSocket 연결 완료")

    async def _establish_connection(self) -> None:
        """WS 연결 수립 + 구독 복원 (connect/reconnect 공통)."""
        token = await self._token_manager.get_token()
        self._ws = await websockets.connect(
            self._ws_url,
            additional_headers={"authorization": f"Bearer {token}"},
            ping_interval=self.HEARTBEAT_INTERVAL,
            ping_timeout=10,
        )
        await self._restore_subscriptions()

    async def disconnect(self) -> None:
        """연결 종료."""
        self._running = False
        if self._listen_task:
            self._listen_task.cancel()
        if self._ws:
            await self._ws.close()
        logger.info("WebSocket 연결 종료")

    async def subscribe(self, ticker: str, tr_type: str) -> None:
        """실시간 구독 등록."""
        msg = json.dumps({
            "header": {
                "approval_key": await self._token_manager.get_token(),
                "custtype": "P",
                "tr_type": "1",
                "content-type": "utf-8",
            },
            "body": {
                "input": {"tr_id": tr_type, "tr_key": ticker},
            },
        })
        if self._ws:
            await self._ws.send(msg)
        self._subscriptions.setdefault(tr_type, [])
        if ticker not in self._subscriptions[tr_type]:
            self._subscriptions[tr_type].append(ticker)
        logger.debug(f"구독: {tr_type} / {ticker}")

    async def unsubscribe(self, ticker: str, tr_type: str) -> None:
        """구독 해제."""
        msg = json.dumps({
            "header": {
                "approval_key": await self._token_manager.get_token(),
                "custtype": "P",
                "tr_type": "2",
                "content-type": "utf-8",
            },
            "body": {
                "input": {"tr_id": tr_type, "tr_key": ticker},
            },
        })
        if self._ws:
            await self._ws.send(msg)
        if tr_type in self._subscriptions:
            self._subscriptions[tr_type] = [
                t for t in self._subscriptions[tr_type] if t != ticker
            ]

    async def _listen_loop(self) -> None:
        """수신 루프 — 재연결 포함."""
        reconnect_delay = self.RECONNECT_BASE_DELAY
        while self._running:
            try:
                async for message in self._ws:
                    try:
                        await self._handle_message(message)
                    except Exception as e:
                        logger.error(f"메시지 처리 오류: {e}")
                    reconnect_delay = self.RECONNECT_BASE_DELAY
            except websockets.ConnectionClosed as e:
                if not self._running:
                    break
                logger.warning(f"WS 연결 끊김 (code={e.code}), {reconnect_delay}초 후 재연결")
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, self.RECONNECT_MAX_DELAY)
                try:
                    await self._establish_connection()
                except Exception as e:
                    logger.error(f"재연결 실패: {e}")
            except Exception as e:
                if not self._running:
                    break
                logger.error(f"WS 오류: {e}")
                await asyncio.sleep(reconnect_delay)

    async def _handle_message(self, raw: str) -> None:
        """메시지 파싱 및 라우팅."""
        if raw.startswith("{"):
            # JSON 응답 (구독 확인 등)
            data = json.loads(raw)
            logger.debug(f"WS JSON: {data.get('header', {}).get('tr_id', 'unknown')}")
            return

        # 파이프 구분 실시간 데이터
        parts = raw.split("|")
        if len(parts) < 4:
            return

        tr_id = parts[1]
        body = parts[3]

        if tr_id in ("H0STCNT0",):  # 주식체결
            tick = self._parse_tick(body)
            if tick:
                await self._dispatch_tick(tick)
        elif tr_id in ("H0STASP0",):  # 주식호가
            # 호가 데이터는 필요 시 별도 queue
            pass
        elif tr_id in ("H0STCNI0", "H0STCNI9"):  # 체결통보
            order_data = self._parse_order_execution(body)
            if order_data and self._order_queue:
                await self._order_queue.put(order_data)

    def _parse_tick(self, body: str) -> dict | None:
        """체결 데이터 파싱."""
        fields = body.split("^")
        if len(fields) < 20:
            return None
        try:
            return {
                "ticker": fields[0],
                "time": fields[1],
                "price": int(fields[2]),
                "change": int(fields[4]) if fields[4] else 0,
                "volume": int(fields[12]) if fields[12] else 0,
                "cum_volume": int(fields[13]) if fields[13] else 0,
            }
        except (ValueError, IndexError) as e:
            logger.warning(f"틱 파싱 실패: {e}")
            return None

    def _parse_order_execution(self, body: str) -> dict | None:
        """체결통보 파싱."""
        fields = body.split("^")
        if len(fields) < 15:
            return None
        try:
            return {
                "order_no": fields[1],
                "ticker": fields[2],
                "side": "buy" if fields[4] == "02" else "sell",
                "price": int(fields[5]) if fields[5] else 0,
                "qty": int(fields[6]) if fields[6] else 0,
                "status": fields[3],
            }
        except (ValueError, IndexError) as e:
            logger.warning(f"체결통보 파싱 실패: {e}")
            return None

    async def _dispatch_tick(self, tick: dict) -> None:
        """틱 데이터를 Queue로 전달."""
        if self._tick_queue:
            await self._tick_queue.put(tick)

    async def _restore_subscriptions(self) -> None:
        """재연결 후 구독 복원 (subscribe 호출 대신 직접 메시지 전송)."""
        for tr_type, tickers in list(self._subscriptions.items()):
            for ticker in tickers:
                msg = json.dumps({
                    "header": {
                        "approval_key": await self._token_manager.get_token(),
                        "custtype": "P", "tr_type": "1", "content-type": "utf-8",
                    },
                    "body": {"input": {"tr_id": tr_type, "tr_key": ticker}},
                })
                if self._ws:
                    await self._ws.send(msg)

    @property
    def connected(self) -> bool:
        return self._ws is not None and self._ws.open
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_kiwoom_ws.py -v`
Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add core/kiwoom_ws.py tests/test_kiwoom_ws.py
git commit -m "feat: KiwoomWebSocketClient — WS 구독, Queue 통합, 자동 재연결"
```

---

## Phase 2: 데이터 파이프라인 (Task 8–10)

### Task 8: 캔들 빌더

**Files:**
- Create: `data/candle_builder.py`
- Test: `tests/test_candle_builder.py`
- PRD: F-CANDLE-01, F-CANDLE-02, F-CANDLE-03

- [ ] **Step 1: 테스트 작성**

```python
"""tests/test_candle_builder.py"""

import asyncio
import pytest

from data.candle_builder import CandleBuilder


@pytest.mark.asyncio
async def test_builds_1m_candle():
    out_queue = asyncio.Queue()
    builder = CandleBuilder(candle_queue=out_queue)

    ticks = [
        {"ticker": "005930", "time": "090100", "price": 70000, "volume": 100, "cum_volume": 100},
        {"ticker": "005930", "time": "090130", "price": 70500, "volume": 200, "cum_volume": 300},
        {"ticker": "005930", "time": "090155", "price": 69500, "volume": 150, "cum_volume": 450},
        # 다음 분봉 시작 → 이전 분봉 완성
        {"ticker": "005930", "time": "090200", "price": 70200, "volume": 50, "cum_volume": 500},
    ]

    for tick in ticks:
        await builder.on_tick(tick)

    candle = await asyncio.wait_for(out_queue.get(), timeout=1.0)
    assert candle["ticker"] == "005930"
    assert candle["tf"] == "1m"
    assert candle["open"] == 70000
    assert candle["high"] == 70500
    assert candle["low"] == 69500
    assert candle["close"] == 69500
    assert candle["volume"] == 450


@pytest.mark.asyncio
async def test_vwap_calculation():
    out_queue = asyncio.Queue()
    builder = CandleBuilder(candle_queue=out_queue)

    # 첫 번째 분봉: price*volume = 70000*100 + 70500*200 + 69500*150 = 31,475,000
    # cum_volume = 450 → VWAP = 31,475,000 / 450 ≈ 69944.4
    ticks_min1 = [
        {"ticker": "005930", "time": "090100", "price": 70000, "volume": 100, "cum_volume": 100},
        {"ticker": "005930", "time": "090130", "price": 70500, "volume": 200, "cum_volume": 300},
        {"ticker": "005930", "time": "090155", "price": 69500, "volume": 150, "cum_volume": 450},
    ]
    # 다음 분봉 시작
    tick_next = {"ticker": "005930", "time": "090200", "price": 70200, "volume": 50, "cum_volume": 500}

    for t in ticks_min1:
        await builder.on_tick(t)
    await builder.on_tick(tick_next)

    candle = await asyncio.wait_for(out_queue.get(), timeout=1.0)
    assert candle["vwap"] is not None
    assert 69900 < candle["vwap"] < 70000


@pytest.mark.asyncio
async def test_5m_candle():
    out_queue = asyncio.Queue()
    builder = CandleBuilder(candle_queue=out_queue, timeframes=["1m", "5m"])

    # 5개 1분봉 시뮬레이션 (간략화: 각 분봉 1틱씩)
    times = ["090100", "090200", "090300", "090400", "090500", "090600"]
    prices = [70000, 70100, 70200, 69800, 70300, 70400]
    volumes = [100, 100, 100, 100, 100, 100]

    for i, (t, p, v) in enumerate(zip(times, prices, volumes)):
        await builder.on_tick({
            "ticker": "005930", "time": t, "price": p,
            "volume": v, "cum_volume": (i + 1) * 100,
        })

    candles = []
    while not out_queue.empty():
        candles.append(await out_queue.get())

    # 5분봉은 5개 1분봉 후에 생성
    tf_5m = [c for c in candles if c["tf"] == "5m"]
    assert len(tf_5m) == 1
    assert tf_5m[0]["open"] == 70000
    assert tf_5m[0]["high"] == 70300
    assert tf_5m[0]["low"] == 69800
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_candle_builder.py -v`
Expected: FAIL

- [ ] **Step 3: candle_builder.py 구현**

```python
"""data/candle_builder.py — 실시간 분봉 생성 + VWAP."""

import asyncio
from collections import defaultdict

from loguru import logger


class CandleBuilder:
    """틱 데이터 → 1분/5분 캔들 생성, VWAP 계산."""

    def __init__(
        self,
        candle_queue: asyncio.Queue,
        timeframes: list[str] | None = None,
    ):
        self._candle_queue = candle_queue
        self._timeframes = timeframes or ["1m"]
        # ticker → 현재 빌딩 중인 분봉
        self._building: dict[str, dict] = {}
        # ticker → 1분봉 버퍼 (5분봉 생성용)
        self._min1_buffer: dict[str, list[dict]] = defaultdict(list)
        # VWAP 누적: ticker → {"pv_sum": float, "vol_sum": int}
        self._vwap_accum: dict[str, dict] = defaultdict(lambda: {"pv_sum": 0.0, "vol_sum": 0})

    async def on_tick(self, tick: dict) -> None:
        """틱 수신 시 호출."""
        ticker = tick["ticker"]
        price = tick["price"]
        volume = tick["volume"]
        time_str = tick["time"]  # "HHMMSS"
        minute_key = time_str[:4]  # "HHMM"

        # VWAP 누적
        self._vwap_accum[ticker]["pv_sum"] += price * volume
        self._vwap_accum[ticker]["vol_sum"] += volume

        current = self._building.get(ticker)
        if current is None or current["_minute_key"] != minute_key:
            # 이전 분봉 완성
            if current is not None:
                await self._emit_candle(current)
            # 새 분봉 시작
            self._building[ticker] = {
                "ticker": ticker,
                "tf": "1m",
                "_minute_key": minute_key,
                "ts": f"{time_str[:2]}:{time_str[2:4]}:00",
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "volume": volume,
                "vwap": None,
            }
        else:
            current["high"] = max(current["high"], price)
            current["low"] = min(current["low"], price)
            current["close"] = price
            current["volume"] += volume

    async def _emit_candle(self, candle: dict) -> None:
        """완성된 분봉을 Queue로 전달."""
        ticker = candle["ticker"]

        # VWAP 계산
        accum = self._vwap_accum[ticker]
        if accum["vol_sum"] > 0:
            candle["vwap"] = accum["pv_sum"] / accum["vol_sum"]

        # 내부 키 제거
        out = {k: v for k, v in candle.items() if not k.startswith("_")}
        await self._candle_queue.put(out)
        logger.debug(f"1분봉 완성: {ticker} {candle['ts']} C={candle['close']}")

        # 5분봉 처리
        if "5m" in self._timeframes:
            self._min1_buffer[ticker].append(out)
            if len(self._min1_buffer[ticker]) >= 5:
                await self._emit_5m_candle(ticker)

    async def _emit_5m_candle(self, ticker: str) -> None:
        """1분봉 5개 → 5분봉 생성."""
        buf = self._min1_buffer[ticker][:5]
        self._min1_buffer[ticker] = self._min1_buffer[ticker][5:]

        candle_5m = {
            "ticker": ticker,
            "tf": "5m",
            "ts": buf[0]["ts"],
            "open": buf[0]["open"],
            "high": max(c["high"] for c in buf),
            "low": min(c["low"] for c in buf),
            "close": buf[-1]["close"],
            "volume": sum(c["volume"] for c in buf),
            "vwap": buf[-1].get("vwap"),
        }
        await self._candle_queue.put(candle_5m)
        logger.debug(f"5분봉 완성: {ticker} {candle_5m['ts']}")

    async def flush(self) -> None:
        """장 종료 시 미완성 분봉 강제 출력."""
        for ticker, candle in list(self._building.items()):
            await self._emit_candle(candle)
        self._building.clear()

    def reset(self) -> None:
        """일일 리셋 (VWAP 누적 초기화)."""
        self._building.clear()
        self._min1_buffer.clear()
        self._vwap_accum.clear()
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_candle_builder.py -v`
Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add data/candle_builder.py tests/test_candle_builder.py
git commit -m "feat: CandleBuilder — 실시간 1분/5분봉 생성 + VWAP"
```

---

### Task 9: 텔레그램 알림 (비동기)

**Files:**
- Create: `notification/__init__.py`
- Create: `notification/telegram_bot.py`
- Test: `tests/test_telegram_bot.py`
- PRD: F-NOTI-01~04

- [ ] **Step 1: 테스트 작성**

```python
"""tests/test_telegram_bot.py"""

import pytest
from unittest.mock import AsyncMock, patch

from notification.telegram_bot import TelegramNotifier


@pytest.fixture
def notifier(app_config):
    return TelegramNotifier(app_config.telegram)


@pytest.mark.asyncio
async def test_send_message(notifier):
    with patch("notification.telegram_bot.aiohttp.ClientSession") as mock_cls:
        mock_session = AsyncMock()
        mock_cls.return_value.__aenter__.return_value = mock_session
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_session.post.return_value.__aenter__.return_value = mock_resp

        ok = await notifier.send("테스트 메시지")
        assert ok is True


@pytest.mark.asyncio
async def test_send_buy_signal(notifier):
    with patch.object(notifier, "send", new_callable=AsyncMock, return_value=True) as mock_send:
        await notifier.send_buy_signal(
            ticker="005930", name="삼성전자",
            strategy="orb", price=70000, reason="ORB 상단 돌파",
        )
        mock_send.assert_called_once()
        msg = mock_send.call_args[0][0]
        assert "삼성전자" in msg
        assert "70,000" in msg


@pytest.mark.asyncio
async def test_send_urgent_bypasses_cooldown(notifier):
    with patch.object(notifier, "send", new_callable=AsyncMock, return_value=True) as mock_send:
        await notifier.send_urgent("손절 주문 실패!")
        mock_send.assert_called_once()
        args = mock_send.call_args
        assert args.kwargs.get("retries", 1) == 3
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_telegram_bot.py -v`
Expected: FAIL

- [ ] **Step 3: telegram_bot.py 구현**

```python
"""notification/telegram_bot.py — 비동기 텔레그램 알림."""

import time

import aiohttp
from loguru import logger

from config.settings import TelegramConfig


class TelegramNotifier:
    """aiohttp 기반 비동기 텔레그램 알림."""

    def __init__(self, config: TelegramConfig):
        self._token = config.bot_token
        self._chat_id = config.chat_id
        self._api_url = f"https://api.telegram.org/bot{self._token}"
        self._cooldowns: dict[str, float] = {}

    async def send(
        self,
        message: str,
        parse_mode: str = "HTML",
        retries: int = 1,
    ) -> bool:
        """메시지 발송."""
        payload = {
            "chat_id": self._chat_id,
            "text": message,
            "parse_mode": parse_mode,
        }
        for attempt in range(retries):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        f"{self._api_url}/sendMessage", json=payload,
                        timeout=aiohttp.ClientTimeout(total=10),
                    ) as resp:
                        if resp.status == 200:
                            return True
                        logger.warning(f"텔레그램 발송 실패: status={resp.status}")
            except Exception as e:
                logger.error(f"텔레그램 발송 오류 (시도 {attempt + 1}): {e}")
        return False

    async def send_with_cooldown(
        self, key: str, message: str, cooldown_sec: float = 60.0,
    ) -> bool:
        now = time.monotonic()
        if key in self._cooldowns and now - self._cooldowns[key] < cooldown_sec:
            return False
        self._cooldowns[key] = now
        return await self.send(message)

    # --- 템플릿 메시지 ---

    async def send_buy_signal(
        self, ticker: str, name: str, strategy: str, price: int, reason: str,
    ) -> bool:
        msg = (
            f"🟢 <b>매수 신호</b>\n"
            f"종목: {name} ({ticker})\n"
            f"전략: {strategy.upper()}\n"
            f"가격: {price:,}원\n"
            f"사유: {reason}"
        )
        return await self.send(msg)

    async def send_execution(
        self, ticker: str, name: str, side: str, price: int, qty: int, amount: int,
    ) -> bool:
        emoji = "🔵" if side == "buy" else "🔴"
        label = "매수" if side == "buy" else "매도"
        msg = (
            f"{emoji} <b>{label} 체결</b>\n"
            f"종목: {name} ({ticker})\n"
            f"가격: {price:,}원 × {qty}주\n"
            f"금액: {amount:,}원"
        )
        return await self.send(msg)

    async def send_stop_loss(
        self, ticker: str, name: str, entry_price: int, exit_price: int, pnl_pct: float,
    ) -> bool:
        msg = (
            f"🛑 <b>손절 실행</b>\n"
            f"종목: {name} ({ticker})\n"
            f"진입가: {entry_price:,} → 청산가: {exit_price:,}\n"
            f"손익: {pnl_pct:+.2%}"
        )
        return await self.send(msg)

    async def send_daily_report(
        self,
        date: str,
        total_trades: int,
        wins: int,
        total_pnl: int,
        win_rate: float,
        strategy: str,
    ) -> bool:
        msg = (
            f"📊 <b>일일 성과 보고서</b>\n"
            f"날짜: {date}\n"
            f"전략: {strategy}\n"
            f"매매: {total_trades}건 (승: {wins})\n"
            f"승률: {win_rate:.1%}\n"
            f"손익: {total_pnl:+,}원"
        )
        return await self.send(msg)

    async def send_urgent(self, message: str) -> bool:
        """긴급 알림 — 쿨다운 무시, 3회 재시도."""
        msg = f"🚨 <b>긴급</b>\n{message}"
        return await self.send(msg, retries=3)

    async def send_no_trade(self, reason: str) -> bool:
        msg = f"⏸️ <b>당일 매매 없음</b>\n사유: {reason}"
        return await self.send(msg)

    async def send_system_start(self) -> bool:
        return await self.send("🚀 <b>단타 매매 시스템 시작</b>")

    async def send_system_stop(self, reason: str = "정상 종료") -> bool:
        return await self.send(f"⏹️ <b>시스템 종료</b>\n사유: {reason}")
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_telegram_bot.py -v`
Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add notification/__init__.py notification/telegram_bot.py tests/test_telegram_bot.py
git commit -m "feat: TelegramNotifier — aiohttp 비동기 알림 + 템플릿 메시지"
```

---

### Task 10: 리스크 매니저

**Files:**
- Create: `risk/__init__.py`
- Create: `risk/risk_manager.py`
- Test: `tests/test_risk_manager.py`
- PRD: F-RISK-01~05

- [ ] **Step 1: 테스트 작성**

```python
"""tests/test_risk_manager.py"""

import pytest
from unittest.mock import AsyncMock

from risk.risk_manager import RiskManager
from config.settings import TradingConfig


@pytest.fixture
def risk_mgr():
    return RiskManager(
        trading_config=TradingConfig(),
        db=AsyncMock(),
        notifier=AsyncMock(),
    )


def test_check_stop_loss_triggers(risk_mgr):
    risk_mgr._positions["005930"] = {
        "entry_price": 70000, "stop_loss": 68950,  # -1.5%
        "qty": 10, "remaining_qty": 10,
    }
    result = risk_mgr.check_stop_loss("005930", current_price=68900)
    assert result is True


def test_check_stop_loss_safe(risk_mgr):
    risk_mgr._positions["005930"] = {
        "entry_price": 70000, "stop_loss": 68950,
        "qty": 10, "remaining_qty": 10,
    }
    result = risk_mgr.check_stop_loss("005930", current_price=69000)
    assert result is False


def test_daily_loss_limit_blocks(risk_mgr):
    risk_mgr._daily_pnl = -200_000  # 누적 손실
    risk_mgr._daily_capital = 10_000_000
    assert risk_mgr.is_trading_halted() is True  # -2% 도달


def test_daily_loss_limit_allows(risk_mgr):
    risk_mgr._daily_pnl = -100_000
    risk_mgr._daily_capital = 10_000_000
    assert risk_mgr.is_trading_halted() is False


@pytest.mark.asyncio
async def test_update_trailing_stop(risk_mgr):
    risk_mgr._positions["005930"] = {
        "entry_price": 70000, "stop_loss": 68950,
        "qty": 10, "remaining_qty": 5,
        "highest_price": 71400, "trailing_pct": 0.01,
        "tp1_hit": True,
    }
    risk_mgr.update_trailing_stop("005930", current_price=72000)
    pos = risk_mgr._positions["005930"]
    assert pos["highest_price"] == 72000
    assert pos["stop_loss"] == 72000 * (1 - 0.01)  # 71280


@pytest.mark.asyncio
async def test_check_consecutive_losses(risk_mgr):
    risk_mgr._db.fetch_all = AsyncMock(return_value=[
        {"total_pnl": -50000},
        {"total_pnl": -30000},
        {"total_pnl": -10000},
    ])
    reduced = await risk_mgr.check_consecutive_losses()
    assert reduced is True  # 3일 연속 손실 → 포지션 축소
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_risk_manager.py -v`
Expected: FAIL

- [ ] **Step 3: risk_manager.py 구현**

```python
"""risk/risk_manager.py — 리스크 관리 (손절, 일일한도, 강제청산, 연속손실)."""

from loguru import logger

from config.settings import TradingConfig
from data.db_manager import DbManager
from notification.telegram_bot import TelegramNotifier


class RiskManager:
    """포지션 레벨 + 계좌 레벨 리스크 관리."""

    def __init__(
        self,
        trading_config: TradingConfig,
        db: DbManager,
        notifier: TelegramNotifier,
    ):
        self._config = trading_config
        self._db = db
        self._notifier = notifier
        self._positions: dict[str, dict] = {}
        self._daily_pnl: float = 0.0
        self._daily_capital: float = 0.0
        self._halted: bool = False
        self._position_scale: float = 1.0  # 연속손실 시 축소

    # --- 포지션 관리 ---

    def register_position(
        self,
        ticker: str,
        entry_price: float,
        qty: int,
        stop_loss: float,
        tp1_price: float | None = None,
        trailing_pct: float | None = None,
    ) -> None:
        self._positions[ticker] = {
            "entry_price": entry_price,
            "stop_loss": stop_loss,
            "qty": qty,
            "remaining_qty": qty,
            "tp1_price": tp1_price,
            "trailing_pct": trailing_pct or self._config.trailing_stop_pct,
            "highest_price": entry_price,
            "tp1_hit": False,
        }

    def remove_position(self, ticker: str) -> None:
        self._positions.pop(ticker, None)

    def get_position(self, ticker: str) -> dict | None:
        return self._positions.get(ticker)

    # --- 손절 ---

    def check_stop_loss(self, ticker: str, current_price: float) -> bool:
        pos = self._positions.get(ticker)
        if not pos:
            return False
        return current_price <= pos["stop_loss"]

    # --- 트레일링 스톱 ---

    def update_trailing_stop(self, ticker: str, current_price: float) -> None:
        pos = self._positions.get(ticker)
        if not pos or not pos.get("tp1_hit"):
            return
        if current_price > pos["highest_price"]:
            pos["highest_price"] = current_price
            pos["stop_loss"] = current_price * (1 - pos["trailing_pct"])

    # --- 1차 익절 체크 ---

    def check_tp1(self, ticker: str, current_price: float) -> bool:
        pos = self._positions.get(ticker)
        if not pos or pos.get("tp1_hit"):
            return False
        if pos["tp1_price"] and current_price >= pos["tp1_price"]:
            return True
        return False

    def mark_tp1_hit(self, ticker: str, sold_qty: int) -> None:
        pos = self._positions.get(ticker)
        if pos:
            pos["tp1_hit"] = True
            pos["remaining_qty"] -= sold_qty
            pos["stop_loss"] = pos["entry_price"]  # 본전 이동

    # --- 일일 손실 한도 ---

    def is_trading_halted(self) -> bool:
        if self._halted:
            return True
        if self._daily_capital <= 0:
            return False
        loss_pct = self._daily_pnl / self._daily_capital
        if loss_pct <= self._config.daily_max_loss_pct:
            self._halted = True
            logger.warning(f"일일 손실 한도 도달: {loss_pct:.2%}")
            return True
        return False

    def record_pnl(self, pnl: float) -> None:
        self._daily_pnl += pnl

    def set_daily_capital(self, capital: float) -> None:
        self._daily_capital = capital

    # --- 연속 손실 ---

    async def check_consecutive_losses(self) -> bool:
        rows = await self._db.fetch_all(
            "SELECT total_pnl FROM daily_pnl ORDER BY date DESC LIMIT ?",
            (self._config.consecutive_loss_days,),
        )
        if len(rows) < self._config.consecutive_loss_days:
            return False
        all_loss = all(row["total_pnl"] < 0 for row in rows)
        if all_loss:
            self._position_scale = self._config.reduced_position_pct
            logger.warning(
                f"{self._config.consecutive_loss_days}일 연속 손실 → "
                f"포지션 {self._position_scale:.0%}로 축소"
            )
        else:
            self._position_scale = 1.0
        return all_loss

    @property
    def position_scale(self) -> float:
        return self._position_scale

    # --- 장애 복구 ---

    async def reconcile_positions(self, api_holdings: list[dict]) -> list[str]:
        """API 잔고와 DB 포지션 대조. 불일치 목록 반환."""
        db_open = await self._db.fetch_all(
            "SELECT ticker, remaining_qty FROM positions WHERE status='open'"
        )
        db_map = {row["ticker"]: row["remaining_qty"] for row in db_open}
        api_map = {h["ticker"]: h["qty"] for h in api_holdings}

        mismatches = []
        all_tickers = set(db_map.keys()) | set(api_map.keys())
        for ticker in all_tickers:
            db_qty = db_map.get(ticker, 0)
            api_qty = api_map.get(ticker, 0)
            if db_qty != api_qty:
                mismatches.append(
                    f"{ticker}: DB={db_qty} vs API={api_qty}"
                )
        return mismatches

    # --- 일일 리셋 ---

    def reset_daily(self) -> None:
        self._daily_pnl = 0.0
        self._halted = False
        self._positions.clear()
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_risk_manager.py -v`
Expected: 6 PASS

- [ ] **Step 5: Commit**

```bash
git add risk/__init__.py risk/risk_manager.py tests/test_risk_manager.py
git commit -m "feat: RiskManager — 손절, 트레일링, 일일한도, 연속손실, 장애복구"
```

---

## Phase 3: 전략 엔진 (Task 11–16)

### Task 11: 전략 베이스 클래스

**Files:**
- Create: `strategy/__init__.py`
- Create: `strategy/base_strategy.py`
- Test: `tests/test_base_strategy.py`
- PRD: F-STR-05

- [ ] **Step 1: 테스트 작성**

```python
"""tests/test_base_strategy.py"""

import pytest
from datetime import time
from unittest.mock import patch

from strategy.base_strategy import BaseStrategy, Signal


def test_signal_dataclass():
    sig = Signal(ticker="005930", side="buy", price=70000, strategy="orb", reason="돌파")
    assert sig.side == "buy"


def test_cannot_instantiate_base():
    with pytest.raises(TypeError):
        BaseStrategy()


def test_is_tradable_time_blocks_before_0905():
    class DummyStrategy(BaseStrategy):
        def generate_signal(self, candles, tick): return None
        def get_stop_loss(self, entry_price): return 0
        def get_take_profit(self, entry_price): return (0, 0)

    s = DummyStrategy()
    with patch("strategy.base_strategy.datetime") as mock_dt:
        mock_dt.now.return_value.time.return_value = time(9, 3)
        assert s.is_tradable_time() is False

    with patch("strategy.base_strategy.datetime") as mock_dt:
        mock_dt.now.return_value.time.return_value = time(9, 6)
        assert s.is_tradable_time() is True
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_base_strategy.py -v`
Expected: FAIL

- [ ] **Step 3: base_strategy.py 구현**

```python
"""strategy/base_strategy.py — 전략 ABC."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, time

import pandas as pd


@dataclass
class Signal:
    ticker: str
    side: str          # "buy" / "sell"
    price: float
    strategy: str
    reason: str
    qty: int | None = None


class BaseStrategy(ABC):
    """전략 베이스 클래스. generate_signal / get_stop_loss / get_take_profit 구현 필수."""

    BLOCK_UNTIL = time(9, 5)
    MARKET_CLOSE = time(15, 20)

    @abstractmethod
    def generate_signal(self, candles: pd.DataFrame, tick: dict) -> Signal | None:
        """매수/매도 신호 생성. 신호 없으면 None."""

    @abstractmethod
    def get_stop_loss(self, entry_price: float) -> float:
        """전략별 손절가."""

    @abstractmethod
    def get_take_profit(self, entry_price: float) -> tuple[float, float]:
        """(tp1, tp2) 익절가."""

    def is_tradable_time(self) -> bool:
        """09:05 이전, 15:20 이후 신호 차단."""
        now = datetime.now().time()
        return self.BLOCK_UNTIL <= now <= self.MARKET_CLOSE
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_base_strategy.py -v`
Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add strategy/__init__.py strategy/base_strategy.py tests/test_base_strategy.py
git commit -m "feat: BaseStrategy ABC + Signal 데이터클래스"
```

---

### Task 12: ORB 전략

**Files:**
- Create: `strategy/orb_strategy.py`
- Test: `tests/test_orb_strategy.py`
- PRD: F-STR-01

- [ ] **Step 1: 테스트 작성**

```python
"""tests/test_orb_strategy.py"""

import pytest
import pandas as pd
from unittest.mock import patch
from datetime import time

from strategy.orb_strategy import OrbStrategy
from config.settings import TradingConfig


@pytest.fixture
def orb():
    return OrbStrategy(TradingConfig())


def test_no_signal_during_range_building(orb):
    """09:05~09:15 레인지 형성 중에는 신호 없음."""
    candles = pd.DataFrame({
        "time": ["09:05", "09:06", "09:07"],
        "open": [70000, 70200, 69800],
        "high": [70300, 70400, 70100],
        "low": [69900, 69700, 69600],
        "close": [70200, 69800, 70100],
        "volume": [1000, 1200, 800],
    })
    tick = {"ticker": "005930", "price": 70100, "time": "091000", "volume": 100}
    with patch.object(orb, "is_tradable_time", return_value=True):
        orb._range_high = None  # 레인지 미설정
        signal = orb.generate_signal(candles, tick)
        assert signal is None  # 아직 레인지 빌딩 중


def test_signal_on_breakout(orb):
    """레인지 상단 돌파 + 거래량 조건 충족 시 매수 신호."""
    orb._range_high = 70400
    orb._range_low = 69600
    orb._prev_day_volume = 10000

    candles = pd.DataFrame({
        "time": ["09:15", "09:16"],
        "close": [70300, 70500],
        "high": [70400, 70600],
        "low": [70200, 70400],
        "volume": [8000, 9000],  # 누적 17000 > 10000*1.5
    })
    tick = {"ticker": "005930", "price": 70500, "time": "091600", "volume": 500}

    with patch.object(orb, "is_tradable_time", return_value=True):
        signal = orb.generate_signal(candles, tick)
        assert signal is not None
        assert signal.side == "buy"


def test_stop_loss(orb):
    sl = orb.get_stop_loss(70000)
    assert sl == 70000 * (1 + orb._config.orb_stop_loss_pct)  # -1.5%


def test_take_profit(orb):
    tp1, tp2 = orb.get_take_profit(70000)
    assert tp1 == 70000 * (1 + orb._config.tp1_pct)  # +2%
    assert tp2 == 0  # 트레일링이므로 고정 tp2 없음
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_orb_strategy.py -v`
Expected: FAIL

- [ ] **Step 3: orb_strategy.py 구현**

```python
"""strategy/orb_strategy.py — Opening Range Breakout."""

import pandas as pd
from loguru import logger

from strategy.base_strategy import BaseStrategy, Signal
from config.settings import TradingConfig


class OrbStrategy(BaseStrategy):
    """09:05~09:15 레인지 형성 후 상단 돌파 매수."""

    def __init__(self, config: TradingConfig):
        self._config = config
        self._range_high: float | None = None
        self._range_low: float | None = None
        self._prev_day_volume: int = 0
        self._signal_fired: bool = False

    def set_prev_day_volume(self, volume: int) -> None:
        self._prev_day_volume = volume

    def generate_signal(self, candles: pd.DataFrame, tick: dict) -> Signal | None:
        if not self.is_tradable_time() or self._signal_fired:
            return None

        time_str = tick["time"][:4]  # "HHMM"

        # 레인지 빌딩 (09:05~09:15)
        if "0905" <= time_str <= "0915":
            range_candles = candles[
                (candles["time"] >= "09:05") & (candles["time"] <= "09:15")
            ]
            if not range_candles.empty:
                self._range_high = range_candles["high"].max()
                self._range_low = range_candles["low"].min()
            return None

        # 레인지 미설정 시 대기
        if self._range_high is None:
            return None

        # 돌파 확인: 현재가 > 레인지 상단
        current_price = tick["price"]
        if current_price <= self._range_high:
            return None

        # 거래량 확인: 누적 > 전일 * 150%
        if self._prev_day_volume > 0:
            cum_volume = candles["volume"].sum() if not candles.empty else 0
            if cum_volume < self._prev_day_volume * self._config.orb_volume_ratio:
                return None

        # 5분봉 종가 확인 (최신 캔들 종가가 레인지 상단 위)
        if not candles.empty and candles.iloc[-1]["close"] <= self._range_high:
            return None

        self._signal_fired = True
        logger.info(
            f"ORB 매수 신호: {tick['ticker']} price={current_price} "
            f"range=[{self._range_low}, {self._range_high}]"
        )

        return Signal(
            ticker=tick["ticker"],
            side="buy",
            price=current_price,
            strategy="orb",
            reason=f"레인지 상단({self._range_high:,.0f}) 돌파",
        )

    def get_stop_loss(self, entry_price: float) -> float:
        return entry_price * (1 + self._config.orb_stop_loss_pct)

    def get_take_profit(self, entry_price: float) -> tuple[float, float]:
        tp1 = entry_price * (1 + self._config.tp1_pct)
        tp2 = 0  # 트레일링 스톱으로 관리
        return tp1, tp2

    def reset(self) -> None:
        self._range_high = None
        self._range_low = None
        self._signal_fired = False
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_orb_strategy.py -v`
Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
git add strategy/orb_strategy.py tests/test_orb_strategy.py
git commit -m "feat: OrbStrategy — Opening Range Breakout 전략"
```

---

### Task 13: VWAP 회귀 전략

**Files:**
- Create: `strategy/vwap_strategy.py`
- Test: `tests/test_vwap_strategy.py`
- PRD: F-STR-02

- [ ] **Step 1~5: TDD 사이클** (Task 12와 동일 패턴)

핵심 로직:
- VWAP 하단 터치 후 반등 감지
- RSI(14) 40~60 필터
- 손절: VWAP -1σ 이탈 또는 -1.2%
- 익절: VWAP +1σ

- [ ] **Commit**

```bash
git add strategy/vwap_strategy.py tests/test_vwap_strategy.py
git commit -m "feat: VwapStrategy — VWAP 회귀 전략"
```

---

### Task 14: 모멘텀 브레이크아웃 전략

**Files:**
- Create: `strategy/momentum_strategy.py`
- Test: `tests/test_momentum_strategy.py`
- PRD: F-STR-03

- [ ] **Step 1~5: TDD 사이클**

핵심 로직:
- 전일 고점 돌파 + 리테스트 지지 확인
- 전일 거래량 200% 이상 필터
- 재돌파 시 진입

- [ ] **Commit**

```bash
git add strategy/momentum_strategy.py tests/test_momentum_strategy.py
git commit -m "feat: MomentumStrategy — 모멘텀 브레이크아웃 전략"
```

---

### Task 15: 눌림목 매매 전략

**Files:**
- Create: `strategy/pullback_strategy.py`
- Test: `tests/test_pullback_strategy.py`
- PRD: F-STR-04

- [ ] **Step 1~5: TDD 사이클**

핵심 로직:
- 당일 +3% 이상 종목의 5분 이평 터치
- 음봉 → 양봉 전환 감지
- 20분 이평 정배열 확인
- 손절: 20분 이평 이탈 또는 -1.5%

- [ ] **Commit**

```bash
git add strategy/pullback_strategy.py tests/test_pullback_strategy.py
git commit -m "feat: PullbackStrategy — 눌림목 매매 전략"
```

---

### Task 16: 주문 실행기

**Files:**
- Create: `core/order_manager.py`
- Test: `tests/test_order_manager.py`
- PRD: F-ORD-01~05

- [ ] **Step 1: 테스트 작성**

```python
"""tests/test_order_manager.py"""

import asyncio
import pytest
from unittest.mock import AsyncMock

from core.order_manager import OrderManager


@pytest.fixture
def order_mgr():
    return OrderManager(
        rest_client=AsyncMock(),
        risk_manager=AsyncMock(),
        notifier=AsyncMock(),
        db=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_execute_buy_split(order_mgr):
    """분할 매수: 1차 55% 주문."""
    order_mgr._rest_client.send_order = AsyncMock(
        return_value={"output": {"ODNO": "12345"}, "rt_cd": "0"}
    )
    result = await order_mgr.execute_buy(
        ticker="005930", price=70000, total_qty=100,
    )
    assert result["order_no"] == "12345"
    # 1차 매수 = 55주 (100 * 0.55)
    call_args = order_mgr._rest_client.send_order.call_args
    assert call_args.kwargs["qty"] == 55


@pytest.mark.asyncio
async def test_duplicate_order_blocked(order_mgr):
    """동일 종목 중복 매수 차단."""
    order_mgr._active_orders["005930"] = True
    result = await order_mgr.execute_buy(
        ticker="005930", price=70000, total_qty=100,
    )
    assert result is None  # 차단됨


@pytest.mark.asyncio
async def test_sell_tp1(order_mgr):
    """1차 익절: 50% 매도."""
    order_mgr._rest_client.send_order = AsyncMock(
        return_value={"output": {"ODNO": "22222"}, "rt_cd": "0"}
    )
    result = await order_mgr.execute_sell_tp1(
        ticker="005930", price=71400, remaining_qty=100,
    )
    call_args = order_mgr._rest_client.send_order.call_args
    assert call_args.kwargs["qty"] == 50  # 50% 매도
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_order_manager.py -v`
Expected: FAIL

- [ ] **Step 3: order_manager.py 구현**

```python
"""core/order_manager.py — 주문 실행기 (분할매수/매도, 중복방지, 체결확인)."""

import asyncio

from loguru import logger

from config.settings import TradingConfig
from core.kiwoom_rest import KiwoomRestClient
from data.db_manager import DbManager
from notification.telegram_bot import TelegramNotifier


class OrderManager:
    """분할 매수/매도, asyncio.Lock 중복 방지, 체결 확인."""

    CONFIRMATION_TIMEOUT = 5.0  # 초

    def __init__(
        self,
        rest_client: KiwoomRestClient,
        risk_manager=None,
        notifier: TelegramNotifier | None = None,
        db: DbManager | None = None,
        trading_config: TradingConfig | None = None,
    ):
        self._rest_client = rest_client
        self._risk_manager = risk_manager
        self._notifier = notifier
        self._db = db
        self._config = trading_config or TradingConfig()
        self._lock = asyncio.Lock()
        self._active_orders: dict[str, bool] = {}
        self._order_queue: asyncio.Queue = asyncio.Queue()

    async def execute_buy(
        self,
        ticker: str,
        price: int,
        total_qty: int,
    ) -> dict | None:
        """분할 매수 1차. 2차는 돌파 확인 후 별도 호출."""
        if ticker in self._active_orders:
            logger.warning(f"중복 주문 차단: {ticker}")
            return None

        async with self._lock:
            self._active_orders[ticker] = True
            try:
                qty_1st = int(total_qty * self._config.entry_1st_ratio)
                result = await self._rest_client.send_order(
                    ticker=ticker,
                    qty=qty_1st,
                    price=price,
                    side="buy",
                    order_type="01",  # 지정가
                )
                if result.get("rt_cd") == "0":
                    order_no = result["output"]["ODNO"]
                    logger.info(f"1차 매수 주문: {ticker} {qty_1st}주 @ {price:,}")
                    return {"order_no": order_no, "qty": qty_1st}
                else:
                    logger.error(f"주문 실패: {result}")
                    return None
            finally:
                self._active_orders.pop(ticker, None)

    async def execute_buy_2nd(
        self,
        ticker: str,
        price: int,
        remaining_qty: int,
    ) -> dict | None:
        """분할 매수 2차 (돌파 확인 후)."""
        return await self._send_order(ticker, remaining_qty, price, "buy")

    async def execute_sell_tp1(
        self,
        ticker: str,
        price: int,
        remaining_qty: int,
    ) -> dict | None:
        """1차 익절: 50% 매도."""
        sell_qty = int(remaining_qty * self._config.tp1_sell_ratio)
        return await self._send_order(ticker, sell_qty, price, "sell", order_type="01")

    async def execute_sell_stop(
        self,
        ticker: str,
        qty: int,
    ) -> dict | None:
        """손절 매도: 시장가."""
        return await self._send_order(ticker, qty, 0, "sell", order_type="00")

    async def execute_sell_force_close(
        self,
        ticker: str,
        qty: int,
    ) -> dict | None:
        """강제 청산: 시장가."""
        logger.warning(f"강제 청산: {ticker} {qty}주")
        return await self._send_order(ticker, qty, 0, "sell", order_type="00")

    async def _send_order(
        self,
        ticker: str,
        qty: int,
        price: int,
        side: str,
        order_type: str = "01",
    ) -> dict | None:
        try:
            result = await self._rest_client.send_order(
                ticker=ticker, qty=qty, price=price,
                side=side, order_type=order_type,
            )
            if result.get("rt_cd") == "0":
                return {"order_no": result["output"]["ODNO"], "qty": qty}
            logger.error(f"주문 실패: {result}")
            return None
        except Exception as e:
            logger.error(f"주문 예외: {e}")
            if self._notifier:
                await self._notifier.send_urgent(f"주문 실패: {ticker} {side} {qty}주 — {e}")
            return None

    async def wait_for_confirmation(self, order_no: str) -> dict | None:
        """WS 체결통보 대기 (5초 타임아웃 후 REST 재조회)."""
        try:
            confirmation = await asyncio.wait_for(
                self._order_queue.get(), timeout=self.CONFIRMATION_TIMEOUT,
            )
            return confirmation
        except asyncio.TimeoutError:
            logger.warning(f"체결 확인 타임아웃: {order_no} — REST 재조회")
            return None
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_order_manager.py -v`
Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add core/order_manager.py tests/test_order_manager.py
git commit -m "feat: OrderManager — 분할매수/매도, 중복방지, 체결확인"
```

---

## Phase 4: 스크리너 + 파이프라인 통합 (Task 17–20)

### Task 17: 장 전 스크리너

**Files:**
- Create: `screener/__init__.py`
- Create: `screener/pre_market.py`
- Test: `tests/test_pre_market_screener.py`
- PRD: F-SCR-01, F-SCR-04

- [ ] **Step 1~5: TDD 사이클**

핵심 로직:
- REST API로 전일 데이터 조회
- 4단계 필터 (기본/기술/수급/이벤트)
- 후보 5~10종목 선정
- `screener_results` 테이블 저장

- [ ] **Commit**

```bash
git add screener/__init__.py screener/pre_market.py tests/test_pre_market_screener.py
git commit -m "feat: PreMarketScreener — 장 전 4단계 스크리닝"
```

---

### Task 18: 전략 선택기

**Files:**
- Create: `screener/strategy_selector.py`
- Test: `tests/test_strategy_selector.py`
- PRD: F-SCR-02

- [ ] **Step 1~5: TDD 사이클**

핵심 로직:
- 시장 상황 판단 (KOSPI 갭, 섹터 ETF, 지수 변동)
- 우선순위: ORB > 모멘텀 > VWAP > 눌림목
- 폴백: 매매 없음 + 텔레그램 알림

- [ ] **Commit**

```bash
git add screener/strategy_selector.py tests/test_strategy_selector.py
git commit -m "feat: StrategySelector — 시장 상황 기반 전략 자동 선택"
```

---

### Task 19: main.py 파이프라인 통합

**Files:**
- Create: `main.py`
- Test: `tests/test_pipeline.py`
- PRD: 6.3 asyncio 파이프라인

- [ ] **Step 1: 통합 테스트 작성**

```python
"""tests/test_pipeline.py"""

import asyncio
import pytest
from unittest.mock import AsyncMock, patch

from config.settings import AppConfig, KiwoomConfig, TelegramConfig


@pytest.mark.asyncio
async def test_pipeline_tick_to_candle():
    """틱 → 캔들빌더 → 캔들 Queue 전달 확인."""
    from data.candle_builder import CandleBuilder

    candle_queue = asyncio.Queue()
    builder = CandleBuilder(candle_queue=candle_queue)

    ticks = [
        {"ticker": "005930", "time": "090500", "price": 70000, "volume": 100, "cum_volume": 100},
        {"ticker": "005930", "time": "090600", "price": 70500, "volume": 200, "cum_volume": 300},
    ]

    for t in ticks:
        await builder.on_tick(t)

    candle = await asyncio.wait_for(candle_queue.get(), timeout=1.0)
    assert candle["ticker"] == "005930"
    assert candle["tf"] == "1m"
```

- [ ] **Step 2: main.py 구현**

```python
"""main.py — 단타 자동매매 시스템 엔트리포인트."""

import asyncio
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv
from loguru import logger

load_dotenv()

from config.settings import AppConfig
from core.auth import TokenManager
from core.kiwoom_rest import KiwoomRestClient
from core.kiwoom_ws import KiwoomWebSocketClient
from core.order_manager import OrderManager
from core.rate_limiter import AsyncRateLimiter
from data.candle_builder import CandleBuilder
from data.db_manager import DbManager
from notification.telegram_bot import TelegramNotifier
from risk.risk_manager import RiskManager
from screener.strategy_selector import StrategySelector


async def main():
    config = AppConfig()

    # 로깅 설정
    logger.remove()
    logger.add(
        sys.stderr, level=config.log_level,
        format="{time:HH:mm:ss} | {level:<7} | {message}",
    )
    logger.add(
        "logs/{time:YYYY-MM-DD}.log",
        rotation="1 day", retention="30 days",
        level="DEBUG", encoding="utf-8",
    )

    # 인프라 초기화
    db = DbManager(config.db_path)
    await db.init()

    notifier = TelegramNotifier(config.telegram)
    await notifier.send_system_start()

    token_manager = TokenManager(
        app_key=config.kiwoom.app_key,
        secret_key=config.kiwoom.secret_key,
        base_url=config.kiwoom.rest_base_url,
    )
    rate_limiter = AsyncRateLimiter(
        max_calls=config.kiwoom.rate_limit_calls,
        period=config.kiwoom.rate_limit_period,
    )
    rest_client = KiwoomRestClient(
        config=config.kiwoom,
        token_manager=token_manager,
        rate_limiter=rate_limiter,
    )

    # Queues
    tick_queue = asyncio.Queue()
    candle_queue = asyncio.Queue()
    signal_queue = asyncio.Queue()
    order_queue = asyncio.Queue()

    # 컴포넌트
    ws_client = KiwoomWebSocketClient(
        ws_url=config.kiwoom.ws_url,
        token_manager=token_manager,
        tick_queue=tick_queue,
        order_queue=order_queue,
    )
    candle_builder = CandleBuilder(candle_queue=candle_queue, timeframes=["1m", "5m"])
    risk_manager = RiskManager(
        trading_config=config.trading, db=db, notifier=notifier,
    )
    order_manager = OrderManager(
        rest_client=rest_client, risk_manager=risk_manager,
        notifier=notifier, db=db, trading_config=config.trading,
    )
    strategy_selector = StrategySelector(config=config, rest_client=rest_client)

    # 스케줄러
    scheduler = AsyncIOScheduler()

    # --- 파이프라인 태스크 ---

    async def tick_consumer():
        """틱 → 캔들 빌더."""
        while True:
            tick = await tick_queue.get()
            await candle_builder.on_tick(tick)

    async def candle_consumer():
        """캔들 → 전략 엔진. 롤링 DataFrame 유지."""
        active_strategy = None
        candle_history: dict[str, list[dict]] = {}  # ticker → 최근 캔들 리스트
        MAX_HISTORY = 100

        while True:
            candle = await candle_queue.get()
            if active_strategy is None:
                continue

            if risk_manager.is_trading_halted():
                continue

            # 롤링 DataFrame 유지
            ticker = candle["ticker"]
            candle_history.setdefault(ticker, [])
            candle_history[ticker].append(candle)
            if len(candle_history[ticker]) > MAX_HISTORY:
                candle_history[ticker] = candle_history[ticker][-MAX_HISTORY:]

            import pandas as pd
            df = pd.DataFrame(candle_history[ticker])

            signal = active_strategy.generate_signal(df, candle)
            if signal:
                await signal_queue.put(signal)

    async def signal_consumer():
        """신호 → 주문 실행."""
        while True:
            signal = await signal_queue.get()
            if signal.side == "buy":
                # 포지션 사이즈 계산
                balance_data = await rest_client.get_account_balance()
                capital = float(balance_data.get("output2", [{}])[0].get("dnca_tot_amt", "10000000"))
                risk_per_trade = capital * 0.02  # 1회 최대 리스크 2%
                stop_pct = abs(active_strategy.get_stop_loss(signal.price) - signal.price) / signal.price
                if stop_pct > 0:
                    max_amount = risk_per_trade / stop_pct
                else:
                    max_amount = capital * 0.3
                total_qty = int(min(max_amount, capital * 0.3) / signal.price)
                total_qty = int(total_qty * risk_manager.position_scale)

                result = await order_manager.execute_buy(
                    ticker=signal.ticker,
                    price=int(signal.price),
                    total_qty=total_qty,
                )
                if result:
                    sl = active_strategy.get_stop_loss(signal.price)
                    tp1, tp2 = active_strategy.get_take_profit(signal.price)
                    risk_manager.register_position(
                        ticker=signal.ticker,
                        entry_price=signal.price,
                        qty=result["qty"],
                        stop_loss=sl,
                        tp1_price=tp1,
                    )

    async def order_confirmation_consumer():
        """WS 체결통보 처리."""
        while True:
            exec_data = await order_queue.get()
            logger.info(f"체결통보: {exec_data}")
            # TODO: 체결 처리, DB 기록

    # --- 스케줄 등록 ---

    async def pre_market_screening():
        """08:30 스크리닝."""
        logger.info("장 전 스크리닝 시작")
        strategy, ticker = await strategy_selector.select()
        if strategy:
            logger.info(f"전략 선택: {strategy} / 종목: {ticker}")
            # WS 구독 시작
            await ws_client.subscribe(ticker, "H0STCNT0")
            await ws_client.subscribe(ticker, "H0STASP0")
        else:
            await notifier.send_no_trade("전략 조건 미충족")

    async def force_close():
        """15:10 강제 청산."""
        logger.warning("15:10 강제 청산 시작")
        for ticker, pos in list(risk_manager._positions.items()):
            if pos["remaining_qty"] > 0:
                await order_manager.execute_sell_force_close(
                    ticker=ticker, qty=pos["remaining_qty"],
                )
        await candle_builder.flush()

    async def daily_report():
        """15:30 일일 보고서."""
        # TODO: 성과 집계 및 보고서 발송
        pass

    scheduler.add_job(pre_market_screening, "cron", hour=8, minute=30)
    scheduler.add_job(force_close, "cron", hour=15, minute=10)
    scheduler.add_job(daily_report, "cron", hour=15, minute=30)
    scheduler.start()

    # 장애 복구: 미청산 포지션 대조 (F-RISK-05)
    try:
        api_balance = await rest_client.get_account_balance()
        holdings = [
            {"ticker": h["pdno"], "qty": int(h["hldg_qty"])}
            for h in api_balance.get("output1", [])
            if int(h.get("hldg_qty", 0)) > 0
        ]
        mismatches = await risk_manager.reconcile_positions(holdings)
        if mismatches:
            await notifier.send_urgent(
                f"포지션 불일치 감지!\n" + "\n".join(mismatches)
            )
            logger.warning(f"포지션 불일치: {mismatches}")
    except Exception as e:
        logger.error(f"장애 복구 점검 실패: {e}")

    await risk_manager.check_consecutive_losses()

    # WS 연결
    try:
        await ws_client.connect()
    except Exception as e:
        logger.error(f"WS 연결 실패: {e}")
        await notifier.send_urgent(f"WS 연결 실패: {e}")

    # 파이프라인 태스크 실행
    tasks = [
        asyncio.create_task(tick_consumer()),
        asyncio.create_task(candle_consumer()),
        asyncio.create_task(signal_consumer()),
        asyncio.create_task(order_confirmation_consumer()),
    ]

    logger.info("파이프라인 시작 — 매매 대기 중")

    try:
        await asyncio.gather(*tasks)
    except KeyboardInterrupt:
        logger.info("사용자 종료 요청")
    finally:
        for t in tasks:
            t.cancel()
        scheduler.shutdown()
        await ws_client.disconnect()
        await db.close()
        await notifier.send_system_stop()


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 3: 통합 테스트 실행**

Run: `pytest tests/test_pipeline.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add main.py tests/test_pipeline.py
git commit -m "feat: main.py — asyncio 파이프라인 통합 (WS→캔들→전략→주문)"
```

---

### Task 20: 장 중 실시간 스캐너 (P1)

**Files:**
- Create: `screener/realtime_scanner.py`
- PRD: F-SCR-03

- [ ] **Step 1~5: TDD 사이클**

핵심 로직:
- 5분 거래량 3배 급등 감지
- 텔레그램 알림

- [ ] **Commit**

```bash
git add screener/realtime_scanner.py
git commit -m "feat: RealtimeScanner — 장 중 거래량 급등 모니터링"
```

---

## Phase 5: 백테스트 (Task 21–22)

### Task 21: 분봉 데이터 수집 배치

**Files:**
- Create: `backtest/__init__.py`
- Create: `backtest/data_collector.py`
- PRD: F-BT-02

- [ ] **Step 1~5: TDD 사이클**

핵심 로직:
- REST API 분봉 조회 (900개 제한 + 페이지네이션)
- Rate Limit 준수
- `intraday_candles` 테이블 저장

- [ ] **Commit**

```bash
git add backtest/__init__.py backtest/data_collector.py
git commit -m "feat: DataCollector — 과거 분봉 수집 배치 (페이지네이션)"
```

---

### Task 22: 백테스트 엔진

**Files:**
- Create: `backtest/backtester.py`
- PRD: F-BT-01

- [ ] **Step 1~5: TDD 사이클**

핵심 로직:
- `intraday_candles` DB에서 과거 데이터 로드
- vectorbt 기반 전략 시뮬레이션
- 수수료 0.015% + 슬리피지 반영
- 09:00~09:05 차단 로직 적용
- KPI 계산 (승률, Profit Factor, MDD, Sharpe)

- [ ] **Commit**

```bash
git add backtest/backtester.py
git commit -m "feat: Backtester — vectorbt 기반 전략 백테스트 + KPI"
```

---

## 작업 분배 (매크로 커맨드 라우팅)

| 순서 | Story | 담당 | 방향 | 의존성 | Task |
|------|-------|------|------|--------|------|
| 1 | 프로젝트 초기화 + 인프라 | @bootstrapper, @backend | → `/team` | - | 1–3 |
| 2 | 인증 + REST + WS + DB | @backend, @security | → `/team` | #1 | 4–7 |
| 3 | 데이터 파이프라인 + 알림 | @backend | → `/team` | #2 | 8–9 |
| 4 | 리스크 관리 | @backend, @security | → `/team` | #2 | 10 |
| 5 | 전략 엔진 (4개 전략) | @backend | → `/team` | #3 | 11–15 |
| 6 | 주문 실행기 | @backend | → `/team` | #4,#5 | 16 |
| 7 | 스크리너 | @backend | → `/team` | #2 | 17–18 |
| 8 | 파이프라인 통합 + 실시간 스캔 | @backend | → `/team` | #4,#5,#6,#7 | 19–20 |
| 9 | 백테스트 | @backend, @qa | → `/team` | #5 | 21–22 |
| 10 | 테스트 보강 + 보안 검토 | @qa, @security | → `/ralph-loop` (max 10) | #8 | - |
| 11 | 성능 최적화 | @performance | → `/ralph-loop` (max 5) | #8 | - |

---

## 리뷰 반영 사항

계획 리뷰에서 발견된 이슈와 반영 내역:

| # | 이슈 | 조치 |
|---|------|------|
| 1 | Python 버전 불일치 (PROJECT.md: 3.14 vs PRD: 3.12) | PRD 기준 3.12 채택 — PROJECT.md 업데이트 필요 |
| 2 | F-RISK-05 장애 복구 호출 누락 | main.py 시작 시 `reconcile_positions()` 추가 |
| 3 | WS `connect()` 재귀 호출로 태스크 누수 | `_establish_connection()` 분리, 재연결 시 태스크 미생성 |
| 4 | WS `_restore_subscriptions` 중복 추가 | `subscribe()` 대신 직접 메시지 전송 |
| 5 | `candle_consumer`에서 DataFrame=None 전달 | 롤링 DataFrame 유지 후 전달 |
| 6 | `signal_consumer` 하드코딩된 qty/sl | 포지션 사이즈 계산 + 전략별 SL/TP 호출 추가 |
| 7 | `.env.example`에 NO_COLOR 누락 | 추가 |
| 8 | `conftest.py` fixture autouse 누락 | `autouse=True` 추가 |
| 9 | 파일 구조에 test_kiwoom_rest/ws 누락 | 추가 |
| 10 | 의존성 테이블 순환 참조 | 테이블 재구성 |

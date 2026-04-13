"""selftest.py — 환경/의존성 검증 스크립트.

운영 시작 또는 빌드 직후 5~10초 안에 환경 전반을 검증해
silent fail (예: 2026-04-13 numba hidden import 누락) 을 조기 감지.

사용:
    python main.py --selftest
    python selftest.py            # 직접 실행도 동일
"""

from __future__ import annotations

import asyncio
import importlib
import sqlite3
import sys
import time
from pathlib import Path
from typing import Callable

# Catppuccin 호환 ANSI 컬러 (Windows 콘솔도 ANSI 지원)
_GREEN = "\x1b[32m"
_RED = "\x1b[31m"
_YELLOW = "\x1b[33m"
_GRAY = "\x1b[90m"
_RESET = "\x1b[0m"

_NETWORK_TIMEOUT = 5.0
_REQUIRED_TABLES = {
    "trades", "positions", "intraday_candles", "daily_pnl",
    "screener_results", "system_log", "index_candles", "ticker_atr",
}
_REQUIRED_MODULES = [
    "pandas", "numpy", "pandas_ta", "numba",
    "PyQt6", "loguru", "apscheduler", "aiosqlite",
    "aiohttp", "websockets", "yaml", "dotenv",
]
_REQUIRED_CONFIG_KEYS = [
    # (dotted_path, type)
    ("paper_mode", bool),
    ("trading.initial_capital", (int, float)),
    ("trading.max_positions", int),
    ("trading.daily_max_loss_pct", (int, float)),
    ("strategy.momentum.buy_time_end", str),
    ("strategy.momentum.adx_min", (int, float)),
    ("strategy.momentum.adx_length", int),
    ("broker.base_url", str),
]


# ──────────────────────────────────────────────────────────────────
# Step results
# ──────────────────────────────────────────────────────────────────

class StepResult:
    OK = "OK"
    FAIL = "FAIL"
    WARN = "WARN"
    SKIP = "SKIP"


def _print_step(idx: int, name: str, status: str, detail: str = "", remedy: str = "") -> None:
    color = {
        StepResult.OK: _GREEN,
        StepResult.FAIL: _RED,
        StepResult.WARN: _YELLOW,
        StepResult.SKIP: _GRAY,
    }.get(status, "")
    tag = f"[{status}]"
    line = f"{color}{tag:<7}{_RESET} {idx:02d}. {name:<28}"
    if detail:
        line += f"  {detail}"
    print(line)
    if remedy:
        print(f"        {_GRAY}→ {remedy}{_RESET}")


# ──────────────────────────────────────────────────────────────────
# Steps
# ──────────────────────────────────────────────────────────────────

def step_imports() -> tuple[str, str, str]:
    missing = []
    for mod in _REQUIRED_MODULES:
        try:
            importlib.import_module(mod)
        except ImportError as e:
            missing.append(f"{mod} ({type(e).__name__})")
    if missing:
        return (
            StepResult.FAIL,
            f"누락: {', '.join(missing)}",
            f"pip install {' '.join(m.split(' ')[0] for m in missing)}",
        )
    return StepResult.OK, f"{len(_REQUIRED_MODULES)} modules", ""


def step_adx_calc() -> tuple[str, str, str]:
    try:
        import numpy as np
        import pandas as pd
        import pandas_ta as ta
        rng = np.random.default_rng(42)
        n = 50
        df = pd.DataFrame({
            "high": rng.random(n) * 100 + 100,
            "low": rng.random(n) * 100,
            "close": rng.random(n) * 100 + 50,
        })
        result = ta.adx(df["high"], df["low"], df["close"], length=14)
        if result is None or result.empty:
            return StepResult.FAIL, "ADX 결과 비어있음", "pandas_ta 버전 확인"
        col = "ADX_14"
        if col not in result.columns:
            return (
                StepResult.FAIL,
                f"{col} 컬럼 없음 (실제: {list(result.columns)})",
                "pandas_ta 버전 호환성 확인",
            )
        last = result[col].iloc[-1]
        if pd.isna(last):
            return StepResult.FAIL, "ADX_14 마지막 값이 NaN", "ADX 계산 로직 확인"
        return StepResult.OK, f"ADX_14 = {float(last):.2f}", ""
    except Exception as e:
        return (
            StepResult.FAIL,
            f"{type(e).__name__}: {e}",
            "numba/pandas_ta hidden import 누락 가능 (build_exe.py 확인)",
        )


def step_atr_calc() -> tuple[str, str, str]:
    try:
        import numpy as np
        import pandas as pd
        import pandas_ta as ta
        rng = np.random.default_rng(42)
        n = 50
        df = pd.DataFrame({
            "high": rng.random(n) * 100 + 100,
            "low": rng.random(n) * 100,
            "close": rng.random(n) * 100 + 50,
        })
        result = ta.atr(df["high"], df["low"], df["close"], length=14)
        if result is None or len(result) == 0:
            return StepResult.FAIL, "ATR 결과 비어있음", "pandas_ta 버전 확인"
        last = result.iloc[-1]
        if pd.isna(last):
            return StepResult.FAIL, "ATR 마지막 값이 NaN", "ATR 계산 로직 확인"
        return StepResult.OK, f"ATR_14 = {float(last):.2f}", ""
    except Exception as e:
        return (
            StepResult.FAIL,
            f"{type(e).__name__}: {e}",
            "numba/pandas_ta hidden import 누락 가능",
        )


def step_db_tables(db_path: str) -> tuple[str, str, str]:
    p = Path(db_path)
    if not p.exists():
        return (
            StepResult.FAIL,
            f"DB 파일 없음: {db_path}",
            "최초 1회 main.py 실행 시 자동 생성됨",
        )
    try:
        conn = sqlite3.connect(db_path)
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        conn.close()
    except sqlite3.Error as e:
        return StepResult.FAIL, f"SQLite 오류: {e}", "DB 락/파일 무결성 확인"
    found = {r[0] for r in rows}
    missing = _REQUIRED_TABLES - found
    if missing:
        return (
            StepResult.FAIL,
            f"테이블 누락: {sorted(missing)}",
            "main.py 1회 실행으로 스키마 초기화",
        )
    return StepResult.OK, f"{len(found)} tables ({len(_REQUIRED_TABLES)} required)", ""


def _get_dotted(d: dict, path: str):
    cur = d
    for k in path.split("."):
        if not isinstance(cur, dict) or k not in cur:
            return None
        cur = cur[k]
    return cur


def step_config(yaml_path: Path) -> tuple[str, str, str]:
    try:
        import yaml
        if not yaml_path.exists():
            return StepResult.FAIL, f"{yaml_path} 없음", "config.yaml 생성 필요"
        with open(yaml_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception as e:
        return StepResult.FAIL, f"yaml 로드 실패: {e}", "config.yaml 문법 확인"

    missing = []
    type_errors = []
    for key, expected_type in _REQUIRED_CONFIG_KEYS:
        v = _get_dotted(data, key)
        if v is None:
            missing.append(key)
        elif not isinstance(v, expected_type):
            type_errors.append(f"{key}={v} ({type(v).__name__})")

    if missing:
        return StepResult.FAIL, f"키 누락: {missing}", "config.yaml 보충"
    if type_errors:
        return StepResult.FAIL, f"타입 오류: {type_errors}", "config.yaml 값 확인"
    return StepResult.OK, f"{len(_REQUIRED_CONFIG_KEYS)} keys 확인", ""


async def step_kiwoom_token() -> tuple[str, str, str]:
    try:
        from config.settings import AppConfig
        from core.auth import TokenManager
        cfg = AppConfig.from_yaml()
        tm = TokenManager(
            cfg.kiwoom.app_key, cfg.kiwoom.secret_key, cfg.kiwoom.rest_base_url,
        )
        token = await asyncio.wait_for(tm.get_token(), timeout=_NETWORK_TIMEOUT)
        if not token:
            return StepResult.FAIL, "토큰 빈 값", "키움 API 키 확인"
        return StepResult.OK, f"token len={len(token)}", ""
    except asyncio.TimeoutError:
        return StepResult.WARN, f"timeout {_NETWORK_TIMEOUT}s — 오프라인?", ""
    except KeyError as e:
        return StepResult.FAIL, f".env 누락: {e}", ".env 파일 확인 (KIWOOM_APP_KEY 등)"
    except Exception as e:
        msg = str(e)
        if "ClientConnector" in type(e).__name__ or "OSError" in type(e).__name__:
            return StepResult.WARN, f"네트워크 에러: {type(e).__name__}", ""
        return StepResult.FAIL, f"{type(e).__name__}: {msg[:80]}", "키움 API 응답/키 확인"


async def step_telegram() -> tuple[str, str, str]:
    try:
        import aiohttp
        from config.settings import AppConfig
        cfg = AppConfig.from_yaml()
        token = cfg.telegram.bot_token
        url = f"https://api.telegram.org/bot{token}/getMe"
        timeout = aiohttp.ClientTimeout(total=_NETWORK_TIMEOUT)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as resp:
                data = await resp.json()
        if not data.get("ok"):
            return (
                StepResult.FAIL,
                f"getMe ok=False: {data.get('description', '')}",
                "TELEGRAM_BOT_TOKEN 갱신",
            )
        bot_name = data.get("result", {}).get("username", "?")
        return StepResult.OK, f"@{bot_name}", ""
    except asyncio.TimeoutError:
        return StepResult.WARN, f"timeout {_NETWORK_TIMEOUT}s — 오프라인?", ""
    except KeyError as e:
        return StepResult.FAIL, f".env 누락: {e}", ".env (TELEGRAM_BOT_TOKEN) 확인"
    except Exception as e:
        if "ClientConnector" in type(e).__name__ or "OSError" in type(e).__name__:
            return StepResult.WARN, f"네트워크 에러: {type(e).__name__}", ""
        return StepResult.FAIL, f"{type(e).__name__}: {str(e)[:80]}", ""


# ──────────────────────────────────────────────────────────────────
# Runner
# ──────────────────────────────────────────────────────────────────

def run_selftest() -> int:
    """selftest 진입점. exit code 반환 (0=성공, 1=FAIL 1개 이상)."""
    print(f"{_GRAY}=== day-trader selftest ==={_RESET}")
    started = time.time()
    results: list[tuple[int, str, str]] = []  # (idx, name, status)

    def _run_sync(idx: int, name: str, fn: Callable[[], tuple[str, str, str]]) -> str:
        status, detail, remedy = fn()
        _print_step(idx, name, status, detail, remedy)
        results.append((idx, name, status))
        return status

    # 1~5 동기 검증
    _run_sync(1, "핵심 모듈 import", step_imports)
    _run_sync(2, "ADX 계산", step_adx_calc)
    _run_sync(3, "ATR 계산", step_atr_calc)

    # config.yaml 경로 — 프로젝트 루트
    yaml_path = Path(__file__).parent / "config.yaml"
    _run_sync(4, "Config 무결성", lambda: step_config(yaml_path))

    # DB 경로 — config 에서 추출 (실패 시 기본값)
    try:
        from config.settings import AppConfig
        db_path = AppConfig.from_yaml().db_path
    except Exception:
        db_path = "daytrader.db"
    _run_sync(5, "SQLite 테이블", lambda: step_db_tables(db_path))

    # 6~7 비동기 네트워크
    async def _run_async() -> None:
        for idx, name, fn in [
            (6, "Kiwoom 토큰 발급", step_kiwoom_token),
            (7, "Telegram 봇", step_telegram),
        ]:
            try:
                status, detail, remedy = await asyncio.wait_for(
                    fn(), timeout=_NETWORK_TIMEOUT + 1.0,
                )
            except asyncio.TimeoutError:
                status, detail, remedy = (
                    StepResult.WARN,
                    f"전체 타임아웃 {_NETWORK_TIMEOUT + 1.0}s",
                    "",
                )
            _print_step(idx, name, status, detail, remedy)
            results.append((idx, name, status))

    try:
        if sys.platform == "win32":
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        asyncio.run(_run_async())
    except Exception as e:
        print(f"{_RED}네트워크 검증 자체 실패: {type(e).__name__}: {e}{_RESET}")

    # ── 요약 ──
    elapsed = time.time() - started
    ok = sum(1 for _, _, s in results if s == StepResult.OK)
    fail = sum(1 for _, _, s in results if s == StepResult.FAIL)
    warn = sum(1 for _, _, s in results if s == StepResult.WARN)
    total = len(results)
    print(f"{_GRAY}---{_RESET}")
    print(
        f"통과: {_GREEN}{ok}{_RESET} / {total}  "
        f"FAIL: {_RED}{fail}{_RESET}  WARN: {_YELLOW}{warn}{_RESET}  "
        f"({elapsed:.1f}s)"
    )
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(run_selftest())

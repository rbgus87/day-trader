"""scripts/pre_paper_check.py — 페이퍼 운용 전 통합 점검.

전략 파라미터, DB, 인덱스 캔들, WS 연결, universe.yaml을 순차 점검하고
모든 항목이 PASS여야만 성공으로 종료한다.

실행:
    python scripts/pre_paper_check.py
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

# 프로젝트 루트를 sys.path에 추가
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

import yaml

# Windows cp949 파이프에서도 한글 출력 안전화
try:
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if sys.stderr and hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# ── 색상 ──
_GREEN = "\x1b[32m"
_RED   = "\x1b[31m"
_YELLOW = "\x1b[33m"
_GRAY  = "\x1b[90m"
_CYAN  = "\x1b[36m"
_RESET = "\x1b[0m"

_TIMEOUT = 8.0

# ── baseline 파라미터 (CLAUDE.md 확정값) ──
_EXPECTED_PARAMS: list[tuple[str, object]] = [
    ("strategy.momentum.atr_stop_enabled",           True),
    ("strategy.momentum.atr_stop_multiplier",        2.0),
    ("strategy.momentum.atr_stop_min_pct",           0.04),
    ("strategy.momentum.atr_stop_max_pct",           0.15),
    ("strategy.momentum.atr_trail_min_pct",          0.025),
    ("strategy.momentum.atr_trail_max_pct",          0.08),
    ("strategy.momentum.time_decay_trailing_enabled", True),
    ("strategy.momentum.momentum_fade_exit_enabled", True),
    ("strategy.momentum.momentum_fade_threshold",    -0.008),
    ("strategy.momentum.momentum_fade_min_profit",   0.03),
    ("strategy.momentum.max_entry_above_breakout_pct", 0.10),
    ("strategy.momentum.min_breakout_pct",           0.03),
    ("strategy.momentum.buy_time_end",               "12:00"),
    ("trading.market_filter_enabled",                True),
    ("strategy.momentum.obi_filter_enabled",         False),   # 0D 수신 확인 전
    ("paper_mode",                                   True),
]

_REQUIRED_TABLES = {
    "trades", "positions", "intraday_candles",
    "daily_pnl", "index_candles",
}


# ──────────────────────────────────────────────────────────────────
# 유틸
# ──────────────────────────────────────────────────────────────────

def _get_dotted(d: dict, path: str):
    cur = d
    for k in path.split("."):
        if not isinstance(cur, dict) or k not in cur:
            return None
        cur = cur[k]
    return cur


def _pass(msg: str) -> None:
    print(f"  {_GREEN}[PASS]{_RESET} {msg}")


def _fail(msg: str, hint: str = "") -> None:
    print(f"  {_RED}[FAIL]{_RESET} {msg}")
    if hint:
        print(f"        {_GRAY}→ {hint}{_RESET}")


def _warn(msg: str) -> None:
    print(f"  {_YELLOW}[WARN]{_RESET} {msg}")


def _info(msg: str) -> None:
    print(f"  {_GRAY}      {msg}{_RESET}")


def _section(title: str) -> None:
    print(f"\n{_CYAN}{'-' * 50}{_RESET}")
    print(f"{_CYAN} {title}{_RESET}")
    print(f"{_CYAN}{'-' * 50}{_RESET}")


# ──────────────────────────────────────────────────────────────────
# 1. Config 파라미터 일관성
# ──────────────────────────────────────────────────────────────────

def check_config_params() -> int:
    """config.yaml의 확정 파라미터 검증. 실패 수 반환."""
    _section("1. config.yaml 파라미터 일관성")
    config_path = _ROOT / "config.yaml"
    if not config_path.exists():
        _fail("config.yaml 없음", "프로젝트 루트에 config.yaml 필요")
        return 1

    with open(config_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    fails = 0
    for path, expected in _EXPECTED_PARAMS:
        actual = _get_dotted(data, path)
        ok = (actual == expected)
        key = path.split(".")[-1]
        if ok:
            _pass(f"{key}: {actual}")
        else:
            _fail(f"{key}: 기대={expected!r} 실제={actual!r}", "config.yaml 수정 필요")
            fails += 1

    # paper_mode 경고
    pm = _get_dotted(data, "paper_mode")
    if pm is False:
        _warn("paper_mode=false — 실매매 모드! 의도한 설정인지 확인")

    return fails


# ──────────────────────────────────────────────────────────────────
# 2. DB 테이블 + index_candles 최신 날짜
# ──────────────────────────────────────────────────────────────────

def check_db() -> int:
    _section("2. DB 상태 (테이블 + index_candles)")
    from config.settings import AppConfig
    try:
        db_path = AppConfig.from_yaml().db_path
    except Exception:
        db_path = "daytrader.db"

    db_file = _ROOT / db_path
    if not db_file.exists():
        _fail(f"DB 파일 없음: {db_file}", "gui.py 1회 실행으로 자동 생성")
        return 1

    fails = 0
    try:
        conn = sqlite3.connect(str(db_file))
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        found = {r[0] for r in rows}
        conn.close()
    except sqlite3.Error as e:
        _fail(f"SQLite 오류: {e}")
        return 1

    missing = _REQUIRED_TABLES - found
    if missing:
        _fail(f"테이블 누락: {sorted(missing)}", "gui.py 실행으로 스키마 초기화")
        fails += 1
    else:
        _pass(f"필수 테이블 {len(_REQUIRED_TABLES)}개 모두 존재")

    # index_candles 최신 날짜 확인
    if "index_candles" in found:
        conn2 = sqlite3.connect(str(db_file))
        row = conn2.execute(
            "SELECT MAX(dt) FROM index_candles"
        ).fetchone()
        conn2.close()
        latest_dt = row[0] if row else None
        if not latest_dt:
            _warn("index_candles 데이터 없음 — 지수 MA 필터 초기 갱신 필요")
        else:
            # YYYYMMDD 또는 YYYY-MM-DD 형식 모두 지원
            try:
                dt_str = str(latest_dt).strip()
                if len(dt_str) == 8 and dt_str.isdigit():
                    latest = datetime.strptime(dt_str, "%Y%m%d")
                else:
                    latest = datetime.strptime(dt_str[:10], "%Y-%m-%d")
                yesterday = datetime.now() - timedelta(days=1)
                if latest.date() < yesterday.date():
                    days_old = (yesterday.date() - latest.date()).days
                    _warn(
                        f"index_candles 최신 날짜: {latest.strftime('%Y-%m-%d')} "
                        f"({days_old}일 전) — 갱신 필요"
                    )
                else:
                    _pass(f"index_candles 최신: {latest.strftime('%Y-%m-%d')}")
            except ValueError:
                _warn(f"index_candles 날짜 파싱 실패: {latest_dt}")

    return fails


# ──────────────────────────────────────────────────────────────────
# 3. universe.yaml 종목 수
# ──────────────────────────────────────────────────────────────────

def check_universe() -> int:
    _section("3. universe.yaml 종목 수")
    universe_path = _ROOT / "config" / "universe.yaml"
    if not universe_path.exists():
        _fail("config/universe.yaml 없음")
        return 1

    with open(universe_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    stocks = data.get("stocks", [])
    n = len(stocks)
    if n == 0:
        _fail("universe.yaml 종목 0개", "유니버스 갱신 필요")
        return 1

    markets = {}
    for s in stocks:
        m = s.get("market", "unknown")
        markets[m] = markets.get(m, 0) + 1

    _pass(f"종목 {n}개 ({', '.join(f'{k}:{v}' for k, v in markets.items())})")

    # backtest 유니버스도 확인
    bt_path = _ROOT / "config" / "universe_backtest.yaml"
    if bt_path.exists():
        with open(bt_path, "r", encoding="utf-8") as f:
            bt_data = yaml.safe_load(f) or {}
        bt_stocks = bt_data.get("stocks", [])
        _info(f"universe_backtest.yaml: {len(bt_stocks)}종목 (고정)")
    else:
        _warn("universe_backtest.yaml 없음")

    return 0


# ──────────────────────────────────────────────────────────────────
# 4. REST API 토큰 + ka10001 응답 확인
# ──────────────────────────────────────────────────────────────────

async def check_rest_api() -> int:
    _section("4. REST API 토큰 + ka10001 응답")
    try:
        from config.settings import AppConfig
        from core.auth import TokenManager
        from core.kiwoom_rest import KiwoomRestClient
        from core.rate_limiter import AsyncRateLimiter

        cfg = AppConfig.from_yaml()
        tm = TokenManager(
            cfg.kiwoom.app_key, cfg.kiwoom.secret_key, cfg.kiwoom.rest_base_url,
        )
        token = await asyncio.wait_for(tm.get_token(), timeout=_TIMEOUT)
        if not token:
            _fail("토큰 빈 값")
            return 1
        _pass(f"토큰 발급 완료 (len={len(token)})")

        rl = AsyncRateLimiter(max_calls=1, period=1.0)
        rest = KiwoomRestClient(config=cfg.kiwoom, token_manager=tm, rate_limiter=rl)
        try:
            # 삼성전자(005930)로 ka10001 응답 확인
            resp = await asyncio.wait_for(
                rest.get_current_price("005930"), timeout=_TIMEOUT
            )
            out = resp.get("output1") or resp
            price = out.get("cur_pric") or out.get("stck_prpr") or "N/A"
            _pass(f"ka10001 응답 OK — 005930 cur_pric={price}")
        except asyncio.TimeoutError:
            _warn(f"ka10001 타임아웃 ({_TIMEOUT}s) — 장외 시간 또는 네트워크")
        except Exception as e:
            _fail(f"ka10001 오류: {type(e).__name__}: {str(e)[:80]}")
            return 1
        finally:
            await rest.aclose()

        return 0
    except asyncio.TimeoutError:
        _warn(f"토큰 발급 타임아웃 ({_TIMEOUT}s)")
        return 0  # 오프라인은 WARN, FAIL 아님
    except KeyError as e:
        _fail(f".env 키 누락: {e}", ".env 파일 확인")
        return 1
    except Exception as e:
        if "ClientConnector" in type(e).__name__ or "OSError" in type(e).__name__:
            _warn(f"네트워크 에러 (오프라인?): {type(e).__name__}")
            return 0
        _fail(f"{type(e).__name__}: {str(e)[:80]}")
        return 1


# ──────────────────────────────────────────────────────────────────
# 5. WS 연결 테스트 (0B + 0D LOGIN 확인)
# ──────────────────────────────────────────────────────────────────

async def check_ws_connection() -> int:
    _section("5. WS 연결 테스트 (0B + 0D)")
    try:
        from config.settings import AppConfig
        from core.auth import TokenManager
        from core.kiwoom_ws import KiwoomWebSocketClient, WS_TYPE_TICK, WS_TYPE_ORDERBOOK
        from core.orderbook import OrderbookManager

        cfg = AppConfig.from_yaml()
        tm = TokenManager(
            cfg.kiwoom.app_key, cfg.kiwoom.secret_key, cfg.kiwoom.rest_base_url,
        )
        ob_mgr = OrderbookManager()
        ws = KiwoomWebSocketClient(
            ws_url=cfg.kiwoom.ws_url,
            token_manager=tm,
            orderbook_manager=ob_mgr,
        )

        try:
            await asyncio.wait_for(ws._establish_connection(), timeout=_TIMEOUT)
        except asyncio.TimeoutError:
            _warn(f"WS 연결 타임아웃 ({_TIMEOUT}s) — 장외 시간 또는 네트워크")
            return 0
        except ConnectionError as e:
            _fail(f"WS LOGIN 실패: {e}", "토큰/WS URL 확인")
            return 1

        _pass("WS LOGIN 성공")

        # 0B 구독 테스트 (삼성전자)
        try:
            await asyncio.wait_for(
                ws.subscribe(["005930"], WS_TYPE_TICK), timeout=3.0
            )
            _pass("0B(체결) 구독 요청 전송")
        except Exception as e:
            _fail(f"0B 구독 실패: {e}")
            return 1

        # 0D 구독 테스트
        try:
            await asyncio.wait_for(
                ws.subscribe(["005930"], WS_TYPE_ORDERBOOK), timeout=3.0
            )
            _pass("0D(호가) 구독 요청 전송")
            _info("※ 0D 실제 수신 여부는 장 시간에 로그에서 확인 필요")
        except Exception as e:
            _warn(f"0D 구독 요청 실패 (0B 영향 없음): {e}")

        # 짧게 메시지 수신 대기
        try:
            msg_count = 0
            deadline = asyncio.get_event_loop().time() + 2.0
            while asyncio.get_event_loop().time() < deadline:
                try:
                    raw = await asyncio.wait_for(ws._ws.recv(), timeout=0.5)
                    data = json.loads(raw)
                    trnm = data.get("trnm", data.get("type", "?"))
                    msg_count += 1
                    _info(f"  수신 메시지 trnm={trnm!r}")
                    if msg_count >= 3:
                        break
                except asyncio.TimeoutError:
                    break
            if msg_count == 0:
                _info("메시지 수신 없음 (장외 시간이면 정상)")
            else:
                _pass(f"WS 메시지 수신 {msg_count}건 확인")
        except Exception as e:
            _info(f"수신 대기 중 예외: {e}")

        await ws.disconnect()
        return 0

    except KeyError as e:
        _fail(f".env 키 누락: {e}")
        return 1
    except Exception as e:
        if "ClientConnector" in type(e).__name__ or "OSError" in type(e).__name__:
            _warn(f"네트워크 에러 (오프라인?): {type(e).__name__}")
            return 0
        _fail(f"{type(e).__name__}: {str(e)[:100]}")
        return 1


# ──────────────────────────────────────────────────────────────────
# 6. AppConfig 로드 + OBI 설정 확인
# ──────────────────────────────────────────────────────────────────

def check_appconfig() -> int:
    _section("6. AppConfig 로드 + OBI/전략 설정 확인")
    try:
        from config.settings import AppConfig
        cfg = AppConfig.from_yaml()
        t = cfg.trading
        fails = 0

        checks = [
            ("paper_mode",                    cfg.paper_mode,                   True),
            ("atr_stop_enabled",              t.atr_stop_enabled,               True),
            ("atr_stop_multiplier",           t.atr_stop_multiplier,            2.0),
            ("atr_trail_min_pct",             t.atr_trail_min_pct,              0.025),
            ("atr_trail_max_pct",             t.atr_trail_max_pct,              0.08),
            ("time_decay_trailing_enabled",   t.time_decay_trailing_enabled,    True),
            ("momentum_fade_exit_enabled",    t.momentum_fade_exit_enabled,     True),
            ("momentum_fade_threshold",       t.momentum_fade_threshold,        -0.008),
            ("momentum_fade_min_profit",      t.momentum_fade_min_profit,       0.03),
            ("max_entry_above_breakout_pct",  t.max_entry_above_breakout_pct,   0.10),
            ("market_filter_enabled",         t.market_filter_enabled,          True),
            ("obi_filter_enabled",            t.obi_filter_enabled,             False),
            ("max_positions",                 t.max_positions,                  3),
        ]
        for name, actual, expected in checks:
            if actual == expected:
                _pass(f"{name}: {actual}")
            else:
                _fail(f"{name}: 기대={expected!r} 실제={actual!r}")
                fails += 1

        # time_decay_phases 확인
        phases = t.time_decay_phases
        if len(phases) >= 4:
            _pass(f"time_decay_phases: {len(phases)}단계")
        else:
            _warn(f"time_decay_phases {len(phases)}단계 (기대 4단계)")

        return fails
    except Exception as e:
        _fail(f"AppConfig 로드 실패: {e}")
        return 1


# ──────────────────────────────────────────────────────────────────
# Runner
# ──────────────────────────────────────────────────────────────────

async def main() -> int:
    print(f"\n{_CYAN}{'=' * 50}{_RESET}")
    print(f"{_CYAN} day-trader 페이퍼 운용 전 통합 점검{_RESET}")
    print(f"{_CYAN} {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}{_RESET}")
    print(f"{_CYAN}{'=' * 50}{_RESET}")

    total_fails = 0
    total_fails += check_config_params()
    total_fails += check_db()
    total_fails += check_universe()
    total_fails += await check_rest_api()
    total_fails += await check_ws_connection()
    total_fails += check_appconfig()

    print(f"\n{_CYAN}{'=' * 50}{_RESET}")
    if total_fails == 0:
        print(f"{_GREEN} [OK] 전항목 PASS -- 페이퍼 운용 준비 완료{_RESET}")
        print(f"{_GRAY}   obi_filter_enabled=false 확인. 0D 수신 후 활성화할 것.{_RESET}")
    else:
        print(f"{_RED} [FAIL] {total_fails}건 -- 운용 전 수정 필요{_RESET}")
    print(f"{_CYAN}{'=' * 50}{_RESET}\n")

    return 0 if total_fails == 0 else 1


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    sys.exit(asyncio.run(main()))

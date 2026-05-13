"""scripts/test_nxt_api.py — NXT 시간대 키움 API 실측 스크립트.

NXT 또는 프리마켓 시간에 수동으로 실행하여
docs/nxt_api_investigation.md의 "추정/미확인" 항목을 실증한다.

실행 타이밍:
    프리마켓 조회: 08:00 ~ 08:29 사이에 실행
    시간외/NXT 조회: 15:41 ~ 19:59 사이에 실행
    WS 수신 확인: 위 시간대 중 아무 때나

실행:
    python scripts/test_nxt_api.py

주의:
    - 조회 API만 사용 (주문 호출 없음)
    - 결과는 콘솔 출력 + logs/nxt_test_YYYYMMDD_HHMMSS.json 저장
    - 저장 결과를 docs/nxt_api_investigation.md에 반영할 것
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

_GREEN  = "\x1b[32m"
_RED    = "\x1b[31m"
_YELLOW = "\x1b[33m"
_GRAY   = "\x1b[90m"
_CYAN   = "\x1b[36m"
_RESET  = "\x1b[0m"

_TIMEOUT = 8.0
_TEST_TICKER = "005930"  # 삼성전자 (유동성 충분, 항상 거래 가능)

# 시간외/NXT 전용 TR ID 후보 (미확인 — 이 스크립트로 실증)
# 응답 여부로 지원 여부를 판단한다.
_NXT_TR_CANDIDATES = [
    ("ka10001", "주식현재가 (정규장)"),
    ("ka10003", "시간외단일가 현재가 후보 (미확인)"),
    ("ka10004", "NXT 현재가 후보 (미확인)"),
    ("ka10023", "시간외종가 후보 (미확인)"),
    ("ka10086", "시간외차트 후보 (미확인)"),
]

# WS 수신 대기 시간 (초)
_WS_LISTEN_SEC = 15


def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _section(title: str) -> None:
    print(f"\n{_CYAN}{'─' * 55}{_RESET}")
    print(f"{_CYAN} {title}{_RESET}")
    print(f"{_CYAN}{'─' * 55}{_RESET}")


def _ok(msg: str) -> None:
    print(f"  {_GREEN}[OK]  {_RESET} {msg}")


def _ng(msg: str) -> None:
    print(f"  {_RED}[NG]  {_RESET} {msg}")


def _warn(msg: str) -> None:
    print(f"  {_YELLOW}[WARN]{_RESET} {msg}")


def _info(msg: str) -> None:
    print(f"  {_GRAY}      {msg}{_RESET}")


def _get_session_label() -> str:
    now = datetime.now().time()
    from datetime import time as dt_time
    if dt_time(8, 0) <= now < dt_time(8, 30):
        return "프리마켓(08:00~08:30)"
    if dt_time(9, 0) <= now <= dt_time(15, 30):
        return "정규장(09:00~15:30)"
    if dt_time(15, 40) <= now < dt_time(16, 0):
        return "시간외단일가(15:40~16:00)"
    if dt_time(16, 0) <= now < dt_time(20, 0):
        return "NXT야간(16:00~20:00)"
    return f"장외({now.strftime('%H:%M')})"


# ──────────────────────────────────────────────────────────────────
# Test 1: REST TR ID 후보 탐색
# ──────────────────────────────────────────────────────────────────

async def test_rest_tr_candidates(rest_client) -> dict:
    """알려진 TR ID 후보를 모두 호출하여 응답 여부를 기록."""
    _section("Test 1: REST TR ID 후보 탐색")
    from core.kiwoom_rest import EP_STOCK, EP_CHART
    results = {}

    for tr_id, desc in _NXT_TR_CANDIDATES:
        _info(f"호출 중: {tr_id} ({desc})")
        try:
            resp = await asyncio.wait_for(
                rest_client.request(
                    "POST", EP_STOCK, tr_id,
                    data={"stk_cd": _TEST_TICKER},
                ),
                timeout=_TIMEOUT,
            )
            # 의미있는 응답 여부 판단
            has_data = bool(resp and resp != {} and "rt_cd" not in resp or
                           resp.get("rt_cd") == "0")
            price = None
            out = resp.get("output1") or resp
            for key in ("cur_pric", "stck_prpr", "clpr", "nxt_prc"):
                if key in out:
                    price = out[key]
                    break

            if has_data:
                _ok(f"{tr_id}: 응답 OK (price_field={price})")
            else:
                rt = resp.get("rt_cd") or resp.get("return_code", "?")
                msg = resp.get("msg1") or resp.get("return_msg", "")
                _warn(f"{tr_id}: rt_cd={rt} msg={msg[:60]!r}")

            results[tr_id] = {
                "desc": desc,
                "status": "ok" if has_data else "error",
                "price": str(price) if price else None,
                "rt_cd": resp.get("rt_cd"),
                "msg": resp.get("msg1", ""),
                "raw_keys": list(resp.keys())[:10],
            }
        except asyncio.TimeoutError:
            _warn(f"{tr_id}: 타임아웃 ({_TIMEOUT}s)")
            results[tr_id] = {"desc": desc, "status": "timeout"}
        except Exception as e:
            # HTTP 에러(404, 400 등)는 TR ID 미지원 의미
            status_code = None
            if hasattr(e, "status"):
                status_code = e.status
            _ng(f"{tr_id}: {type(e).__name__} (HTTP {status_code}) — 미지원 또는 파라미터 오류")
            results[tr_id] = {
                "desc": desc,
                "status": "http_error",
                "error": str(e)[:100],
                "http_status": status_code,
            }

    return results


# ──────────────────────────────────────────────────────────────────
# Test 2: ka10001 정규장 가격 vs 현재 호출 가격 비교
# ──────────────────────────────────────────────────────────────────

async def test_ka10001_price_context(rest_client) -> dict:
    """ka10001 응답에서 정규장 종가 / 현재가 필드를 기록.

    NXT 시간에 호출하면 '시간외' 가격이 반영되는지 알 수 있다.
    """
    _section("Test 2: ka10001 가격 필드 덤프")
    try:
        resp = await asyncio.wait_for(
            rest_client.get_current_price(_TEST_TICKER), timeout=_TIMEOUT
        )
        out = resp.get("output1") or resp
        fields_of_interest = [
            "cur_pric", "strt_pric", "high_pric", "low_pric", "base_pric",
            "trde_qty", "upl_pric", "lwr_pric",
            "ovtm_untp_pric",   # 시간외단일가 (추정 필드명)
            "nxt_cur_pric",     # NXT 현재가 (추정 필드명)
            "aft_hour_ovtm",    # 시간외 관련 (추정)
        ]
        result = {
            "call_time": _ts(),
            "session": _get_session_label(),
            "fields": {},
        }
        found_any = False
        for f in fields_of_interest:
            if f in out:
                result["fields"][f] = out[f]
                _ok(f"{f}: {out[f]}")
                found_any = True

        if not found_any:
            _warn("output1에서 알려진 가격 필드 없음 — 전체 키 출력:")
            for k, v in list(out.items())[:20]:
                _info(f"  {k}: {v}")
            result["all_keys"] = list(out.keys())

        return result
    except Exception as e:
        _ng(f"ka10001 호출 실패: {e}")
        return {"status": "error", "error": str(e)}


# ──────────────────────────────────────────────────────────────────
# Test 3: WS 0B 수신 확인 (NXT 시간대)
# ──────────────────────────────────────────────────────────────────

async def test_ws_ob_reception() -> dict:
    """WS 0B 구독 후 _WS_LISTEN_SEC 초 동안 체결 메시지 수신 여부 확인."""
    _section(f"Test 3: WS 0B 수신 ({_WS_LISTEN_SEC}초 대기)")
    _info(f"세션: {_get_session_label()}")

    try:
        from config.settings import AppConfig
        from core.auth import TokenManager
        from core.kiwoom_ws import KiwoomWebSocketClient, WS_TYPE_TICK, WS_TYPE_ORDERBOOK
        from core.orderbook import OrderbookManager
        import websockets

        cfg = AppConfig.from_yaml()
        tm = TokenManager(
            cfg.kiwoom.app_key, cfg.kiwoom.secret_key, cfg.kiwoom.rest_base_url,
        )
        ob_mgr = OrderbookManager()
        ws_client = KiwoomWebSocketClient(
            ws_url=cfg.kiwoom.ws_url,
            token_manager=tm,
            orderbook_manager=ob_mgr,
        )

        await asyncio.wait_for(ws_client._establish_connection(), timeout=_TIMEOUT)
        _ok("WS LOGIN 성공")

        # 0B + 0D 구독
        await ws_client.subscribe([_TEST_TICKER], WS_TYPE_TICK)
        await ws_client.subscribe([_TEST_TICKER], WS_TYPE_ORDERBOOK)
        _ok(f"0B + 0D 구독 완료 ({_TEST_TICKER})")

        # 수신 대기
        received = {"0B": [], "0D": [], "other": []}
        deadline = asyncio.get_event_loop().time() + _WS_LISTEN_SEC
        _info(f"{_WS_LISTEN_SEC}초 수신 대기 중...")

        while asyncio.get_event_loop().time() < deadline:
            try:
                raw = await asyncio.wait_for(
                    ws_client._ws.recv(), timeout=1.0
                )
                data = json.loads(raw)
                trnm = data.get("trnm", "")
                if trnm == "PING":
                    await ws_client._ws.send(raw)
                    continue
                if trnm in ("REG", "LOGIN") or data.get("return_code") is not None:
                    continue

                # 실시간 데이터
                data_list = data.get("data", [])
                if data_list:
                    for item in data_list:
                        msg_type = item.get("type", "")
                        if msg_type == "0B":
                            vals = item.get("values", {})
                            price = vals.get("10", "?")
                            received["0B"].append({
                                "time": _ts(), "price": price
                            })
                        elif msg_type == "0D":
                            received["0D"].append({"time": _ts()})
                        else:
                            received["other"].append(msg_type)
                elif data.get("type") == "0B":
                    vals = data.get("values", {})
                    received["0B"].append({
                        "time": _ts(),
                        "price": vals.get("10", "?"),
                    })
                elif data.get("type") == "0D":
                    received["0D"].append({"time": _ts()})
            except asyncio.TimeoutError:
                continue
            except Exception:
                break

        await ws_client.disconnect()

        n_0b = len(received["0B"])
        n_0d = len(received["0D"])
        if n_0b > 0:
            last = received["0B"][-1]
            _ok(f"0B 체결 수신 {n_0b}건 — 마지막 price={last['price']} @ {last['time']}")
        else:
            _warn(f"0B 체결 수신 0건 (장외 시간이면 정상)")

        if n_0d > 0:
            _ok(f"0D 호가 수신 {n_0d}건 ← 0D 구독 지원 확인!")
        else:
            _warn(f"0D 호가 수신 0건 (장외 또는 0D 미지원 가능성)")

        return {
            "session": _get_session_label(),
            "0B_count": n_0b,
            "0D_count": n_0d,
            "0B_samples": received["0B"][:3],
            "0D_samples": received["0D"][:3],
        }

    except asyncio.TimeoutError:
        _warn(f"WS 연결 타임아웃 ({_TIMEOUT}s)")
        return {"status": "timeout"}
    except Exception as e:
        _ng(f"WS 테스트 실패: {type(e).__name__}: {str(e)[:80]}")
        return {"status": "error", "error": str(e)}


# ──────────────────────────────────────────────────────────────────
# Test 4: 장외 시간 접속 여부 (is_ws_active_hours 범위 확인)
# ──────────────────────────────────────────────────────────────────

def test_ws_active_hours_check() -> dict:
    """현재 시각이 is_ws_active_hours() 범위 안인지 확인."""
    _section("Test 4: is_ws_active_hours() 범위 확인")
    try:
        from utils.market_calendar import is_ws_active_hours
        is_active = is_ws_active_hours()
        now_str = datetime.now().strftime("%H:%M:%S")
        session = _get_session_label()
        if is_active:
            _ok(f"{now_str} ({session}) → 활성 시간대 (WS 재연결 허용)")
        else:
            _warn(f"{now_str} ({session}) → 비활성 시간대 (WS 재연결 생략)")
            _info("NXT 시간에 WS 재연결이 필요하면 is_ws_active_hours() 확장 필요")

        return {
            "current_time": now_str,
            "session": session,
            "is_ws_active": is_active,
        }
    except Exception as e:
        _ng(f"is_ws_active_hours 호출 실패: {e}")
        return {"status": "error"}


# ──────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────

async def main() -> None:
    print(f"\n{_CYAN}{'═' * 55}{_RESET}")
    print(f"{_CYAN} day-trader NXT API 실측 스크립트{_RESET}")
    print(f"{_CYAN} 실행 시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}{_RESET}")
    print(f"{_CYAN} 세션: {_get_session_label()}{_RESET}")
    print(f"{_CYAN}{'═' * 55}{_RESET}")

    session_label = _get_session_label()
    if "정규장" in session_label:
        _warn("현재 정규장 시간입니다.")
        _info("NXT/프리마켓 측정을 위해 08:00~08:29 또는 15:41~19:59에 실행하세요.")
        _info("계속 진행하면 정규장 결과가 기록됩니다.")
        print()

    results = {
        "run_at": datetime.now().isoformat(),
        "session": session_label,
    }

    # REST 클라이언트 초기화
    try:
        from config.settings import AppConfig
        from core.auth import TokenManager
        from core.kiwoom_rest import KiwoomRestClient
        from core.rate_limiter import AsyncRateLimiter

        cfg = AppConfig.from_yaml()
        tm = TokenManager(
            cfg.kiwoom.app_key, cfg.kiwoom.secret_key, cfg.kiwoom.rest_base_url,
        )
        rl = AsyncRateLimiter(max_calls=2, period=1.0)
        rest = KiwoomRestClient(config=cfg.kiwoom, token_manager=tm, rate_limiter=rl)

        results["test1_tr_candidates"] = await test_rest_tr_candidates(rest)
        results["test2_ka10001_fields"] = await test_ka10001_price_context(rest)
        await rest.aclose()
    except KeyError as e:
        _ng(f".env 키 누락: {e}")
        results["rest_error"] = str(e)

    results["test3_ws_reception"] = await test_ws_ob_reception()
    results["test4_ws_active_hours"] = test_ws_active_hours_check()

    # 결과 저장
    log_dir = _ROOT / "logs"
    log_dir.mkdir(exist_ok=True)
    out_path = log_dir / f"nxt_test_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print(f"\n{_CYAN}{'═' * 55}{_RESET}")
    print(f"{_GREEN} 결과 저장: {out_path}{_RESET}")
    print(f"{_GRAY} docs/nxt_api_investigation.md에 실측 결과를 반영하세요.{_RESET}")
    print(f"{_CYAN}{'═' * 55}{_RESET}\n")


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())

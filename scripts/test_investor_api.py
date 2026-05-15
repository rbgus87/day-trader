"""scripts/test_investor_api.py — 수급 API 응답 구조 탐색 스크립트.

정규장 시간(09:00~15:30)에 수동 실행하여 ka10079 응답 필드명을 확인한다.
확인 결과를 candidate_collector.py의 _FIELD_INST_BUY / _FIELD_FRGN_BUY 상수에 반영.

실행:
    python scripts/test_investor_api.py

주의:
    - 조회 API만 사용 (주문 호출 없음)
    - 결과는 콘솔 출력 + logs/investor_api_YYYYMMDD_HHMMSS.json 저장
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

_TEST_TICKER = "005930"  # 삼성전자 — 유동성 충분, 항상 수급 데이터 존재


def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _section(title: str) -> None:
    print(f"\n{_CYAN}{'-' * 55}{_RESET}")
    print(f"{_CYAN}[{_ts()}] {title}{_RESET}")
    print(f"{_CYAN}{'-' * 55}{_RESET}")


def _ok(msg: str) -> None:
    print(f"{_GREEN}  OK  {_RESET} {msg}")


def _fail(msg: str) -> None:
    print(f"{_RED}  FAIL{_RESET} {msg}")


def _info(msg: str) -> None:
    print(f"{_GRAY}       {msg}{_RESET}")


async def _dump_response(label: str, result: dict) -> None:
    """응답 전체를 컬러 덤프."""
    print(f"\n{_YELLOW}── {label} 응답 필드 ──{_RESET}")
    for k, v in result.items():
        if isinstance(v, list):
            print(f"  {k}: (list, {len(v)}건)")
            if v:
                print(f"    첫 번째 항목 키: {list(v[0].keys()) if isinstance(v[0], dict) else type(v[0])}")
                if isinstance(v[0], dict):
                    for fk, fv in list(v[0].items())[:10]:
                        print(f"      {fk}: {fv!r}")
        else:
            print(f"  {k}: {v!r}")


async def test_ka10079(rest_client) -> dict:
    """ka10079 투자자별 매매동향 기본 호출."""
    from core.kiwoom_rest import EP_STOCK, API_INVESTOR_TRADE

    _section(f"ka10079 투자자별 매매동향 — {_TEST_TICKER}")

    # 파라미터 조합 1: stk_cd만
    body_v1 = {"stk_cd": _TEST_TICKER}
    try:
        result = await rest_client.request("POST", EP_STOCK, API_INVESTOR_TRADE, data=body_v1)
        _ok(f"파라미터 v1 응답 수신 (키: {list(result.keys())})")
        await _dump_response("ka10079 v1", result)
        return result
    except Exception as e:
        _fail(f"파라미터 v1 실패: {e}")

    # 파라미터 조합 2: 기간 추가
    today = datetime.now().strftime("%Y%m%d")
    body_v2 = {
        "stk_cd": _TEST_TICKER,
        "inqr_strt_dt": today,
        "inqr_end_dt": today,
    }
    try:
        result = await rest_client.request("POST", EP_STOCK, API_INVESTOR_TRADE, data=body_v2)
        _ok(f"파라미터 v2(기간 추가) 응답 수신 (키: {list(result.keys())})")
        await _dump_response("ka10079 v2", result)
        return result
    except Exception as e:
        _fail(f"파라미터 v2 실패: {e}")

    return {}


async def test_ka10001_supply_fields(rest_client) -> dict:
    """ka10001 현재가 응답에 수급 관련 필드가 있는지 확인."""
    from core.kiwoom_rest import EP_STOCK, API_STOCK_PRICE

    _section(f"ka10001 현재가 — 수급 필드 포함 여부 확인 ({_TEST_TICKER})")

    # 수급 관련 예상 필드명 후보
    supply_candidates = [
        "orgn_ntby_qty", "frgn_ntby_qty",   # 순매수 수량 (추정)
        "orgn_ntby_amt", "frgn_ntby_amt",   # 순매수 금액 (추정)
        "inst_netbuy",   "frgn_netbuy",     # 단축 표기 (추정)
        "instn_netslng_qty", "frgn_netslng_qty",  # 순매도 기반 (추정)
    ]

    try:
        result = await rest_client.request("POST", EP_STOCK, API_STOCK_PRICE, data={"stk_cd": _TEST_TICKER})
        flat = result.get("output1") or result

        found = {k: flat[k] for k in supply_candidates if k in flat}
        if found:
            _ok(f"수급 관련 필드 발견: {found}")
        else:
            _info("ka10001 응답에 수급 필드 없음")
            _info(f"전체 키: {list(flat.keys())}")

        await _dump_response("ka10001 전체", flat)
        return result
    except Exception as e:
        _fail(f"ka10001 실패: {e}")
        return {}


async def test_ka10004(rest_client) -> dict:
    """ka10004 종목 상세 — 수급 필드 확인."""
    from core.kiwoom_rest import EP_STOCK

    _section(f"ka10004 종목 상세 ({_TEST_TICKER})")

    try:
        result = await rest_client.request(
            "POST", EP_STOCK, "ka10004", data={"stk_cd": _TEST_TICKER}
        )
        _ok(f"ka10004 응답 수신 (키: {list(result.keys())})")
        await _dump_response("ka10004", result)
        return result
    except Exception as e:
        _fail(f"ka10004 실패: {e}")
        return {}


async def run(rest_client) -> dict:
    results: dict = {}

    results["ka10079"] = await test_ka10079(rest_client)
    results["ka10001_supply"] = await test_ka10001_supply_fields(rest_client)
    results["ka10004"] = await test_ka10004(rest_client)

    # 결과 저장
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = _ROOT / "logs" / f"investor_api_{ts}.json"
    log_path.parent.mkdir(exist_ok=True)
    log_path.write_text(
        json.dumps(results, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )

    _section("완료")
    print(f"결과 저장: {log_path}")
    print()
    print("▶ 확인 후 아래 상수를 수정하세요:")
    print("  screener/candidate_collector.py")
    print("  _FIELD_INST_BUY = '실제_기관_필드명'")
    print("  _FIELD_FRGN_BUY = '실제_외국인_필드명'")

    return results


async def _main() -> None:
    from config.settings import AppConfig
    from core.auth import TokenManager
    from core.kiwoom_rest import KiwoomRestClient

    config = AppConfig.from_yaml()
    token_mgr = TokenManager(config.kiwoom)
    rest = KiwoomRestClient(config.kiwoom, token_mgr)

    try:
        await run(rest)
    finally:
        await rest.aclose()


if __name__ == "__main__":
    asyncio.run(_main())

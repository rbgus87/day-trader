"""scripts/test_investor_api.py — 수급 API 엔드포인트/TR ID 탐색.

기본 실행 (followup 모드):
    python scripts/test_investor_api.py

전체 브로드 스캔:
    python scripts/test_investor_api.py --all

Phase 1 브로드 스캔에서 code=2(파라미터 오류)로 가능성이 확인된
아래 3개 조합을 파라미터 보완 후 재시도한다.
    - ka10079 on /api/dostk/chart     (tic_scope 필수)
    - ka10059 on /api/dostk/stkinfo   (dt 필수)
    - ka10026 on /api/dostk/stkinfo   (pertp 필수)
"""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_GREEN  = "\x1b[32m"
_RED    = "\x1b[31m"
_YELLOW = "\x1b[33m"
_GRAY   = "\x1b[90m"
_CYAN   = "\x1b[36m"
_RESET  = "\x1b[0m"

_TICKER = "005930"
_TODAY  = datetime.now().strftime("%Y%m%d")


def _last_weekday() -> str:
    """오늘이 주말이면 직전 금요일 반환. 주중이면 오늘."""
    d = datetime.now()
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.strftime("%Y%m%d")


_LAST_TD = _last_weekday()

# ── follow-up 탐색 정의 ─────────────────────────────────────
# 브로드 스캔에서 code=2(파라미터 오류)로 URI 인식이 확인된 조합만 재시도.
# 각 항목 bodies 를 순서대로 시도 → 첫 성공(return_code=0) 시 중단.
_FOLLOWUP: list[dict] = [
    {
        "tr_id":   "ka10079",
        "desc":    "투자자별 매매동향",
        "ep_name": "chart",
        "ep_path": "/api/dostk/chart",
        "bodies": [
            # ka10080(분봉)과 동일 패턴
            {"stk_cd": _TICKER, "tic_scope": "1", "upd_stkpc_tp": "0"},
            {"stk_cd": _TICKER, "tic_scope": "1", "upd_stkpc_tp": "0", "base_dt": _LAST_TD},
            {"stk_cd": _TICKER, "tic_scope": "1"},
            # 일봉 의미로 "D" 시도
            {"stk_cd": _TICKER, "tic_scope": "D", "upd_stkpc_tp": "0"},
            {"stk_cd": _TICKER, "tic_scope": "D"},
        ],
    },
    {
        "tr_id":   "ka10059",
        "desc":    "거래원별 매매동향",
        "ep_name": "stkinfo",
        "ep_path": "/api/dostk/stkinfo",
        "bodies": [
            {"stk_cd": _TICKER, "dt": _LAST_TD, "amt_qty_tp": "1"},   # 금액 기준
            {"stk_cd": _TICKER, "dt": _LAST_TD, "amt_qty_tp": "2"},   # 수량 기준
        ],
    },
    {
        "tr_id":   "ka10026",
        "desc":    "투자자별 일별 매매현황",
        "ep_name": "stkinfo",
        "ep_path": "/api/dostk/stkinfo",
        "bodies": [
            {"stk_cd": _TICKER, "pertp": "D", "stex_tp": "J"},  # KOSPI 일별
            {"stk_cd": _TICKER, "pertp": "D", "stex_tp": "Q"},  # KOSDAQ
            {"stk_cd": _TICKER, "pertp": "1", "stex_tp": "J"},  # pertp=1
        ],
    },
]

# ── 브로드 스캔 정의 (--all 플래그) ────────────────────────
_ENDPOINTS = [
    ("stkinfo", "/api/dostk/stkinfo"),
    ("chart",   "/api/dostk/chart"),
    ("acnt",    "/api/dostk/acnt"),
    ("ordr",    "/api/dostk/ordr"),
]
_BROAD_CANDIDATES = [
    ("ka10079", "투자자별 매매동향",       "all"),
    ("ka10033", "종목별 투자자 순매수",     "sq2"),
    ("ka10059", "거래원별 매매동향",        "sq2"),
    ("ka20033", "업종별 투자자 매매동향",   "sq2"),
    ("ka10003", "종목별 외국인/기관 현황",  "sq2"),
    ("ka10026", "투자자별 일별 매매현황",   "sq2"),
]
_BROAD_BODIES = [
    {"stk_cd": _TICKER},
    {"stk_cd": _TICKER, "inqr_strt_dt": _TODAY, "inqr_end_dt": _TODAY},
    {"stk_cd": _TICKER, "strt_dt": _TODAY, "end_dt": _TODAY},
    {"mrkt_tp": "J", "stk_cd": _TICKER},
]


# ── 출력 헬퍼 ─────────────────────────────────────────────
def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S")

def _ok(msg: str)   -> None: print(f"{_GREEN}  OK  {_RESET} {msg}")
def _fail(msg: str) -> None: print(f"{_RED}  FAIL{_RESET} {msg}")
def _info(msg: str) -> None: print(f"{_GRAY}       {msg}{_RESET}")

def _header(title: str) -> None:
    print(f"\n{_CYAN}{'=' * 60}{_RESET}")
    print(f"{_CYAN}  {title}{_RESET}")
    print(f"{_CYAN}{'=' * 60}{_RESET}")

def _sub(title: str) -> None:
    print(f"\n{_YELLOW}  -- {title} --{_RESET}")


# ── 단일 호출 ─────────────────────────────────────────────
async def _try(rest_client, ep_path: str, tr_id: str, body: dict) -> dict:
    try:
        return await rest_client.request("POST", ep_path, tr_id, data=body)
    except Exception as e:
        return {"_error": str(e)}


def _is_success(r: dict) -> bool:
    return "_error" not in r and int(r.get("return_code", 1)) == 0


def _fields(r: dict) -> list[str]:
    return [k for k in r if k not in ("return_code", "return_msg", "_error")]


def _short_msg(r: dict) -> str:
    if "_error" in r:
        return f"예외: {r['_error'][:80]}"
    return r.get("return_msg", "")[:80]


def _dump_success(result: dict) -> None:
    """성공 응답의 필드와 값을 상세 출력."""
    for k in _fields(result):
        v = result[k]
        if isinstance(v, list):
            _ok(f"[{k}] : list {len(v)}건")
            if v and isinstance(v[0], dict):
                first = v[0]
                _ok(f"  키 목록: {list(first.keys())}")
                for fk, fv in list(first.items())[:15]:
                    print(f"         {fk}: {fv!r}")
            elif v:
                print(f"         첫 번째 값: {v[0]!r}")
        else:
            _ok(f"[{k}] = {v!r}")


# ── follow-up 탐색 ────────────────────────────────────────
async def probe_followup(rest_client) -> list[dict]:
    records: list[dict] = []

    for probe in _FOLLOWUP:
        tr_id   = probe["tr_id"]
        desc    = probe["desc"]
        ep_name = probe["ep_name"]
        ep_path = probe["ep_path"]

        _header(f"{tr_id}  {desc}  ({ep_name})")

        hit: dict | None = None
        for body in probe["bodies"]:
            result = await _try(rest_client, ep_path, tr_id, body)
            rc     = result.get("return_code", "ERR")
            msg    = _short_msg(result)

            if _is_success(result):
                _ok(f"return_code=0  body={body}")
                _dump_success(result)
                hit = result
                break
            else:
                _fail(f"code={rc}  body={body}")
                _info(f"       msg: {msg}")

        records.append({
            "tr_id":   tr_id,
            "ep_name": ep_name,
            "ep_path": ep_path,
            "success": hit is not None,
            "result":  hit or result,
        })

    return records


# ── 브로드 스캔 (--all) ───────────────────────────────────
async def probe_all(rest_client) -> list[dict]:
    ep_map = {name: path for name, path in _ENDPOINTS}
    records: list[dict] = []

    for tr_id, desc, scope in _BROAD_CANDIDATES:
        _header(f"{tr_id}  {desc}")
        ep_names = [n for n, _ in _ENDPOINTS] if scope == "all" else ["stkinfo", "chart"]

        for ep_name in ep_names:
            ep_path = ep_map[ep_name]
            _sub(f"{ep_name}  ({ep_path})")

            hit: dict | None = None
            for body in _BROAD_BODIES:
                result = await _try(rest_client, ep_path, tr_id, body)
                rc  = result.get("return_code", "ERR")
                msg = _short_msg(result)

                if _is_success(result):
                    _ok(f"return_code=0  body={body}")
                    _dump_success(result)
                    hit = result
                    break
                else:
                    _fail(f"code={rc}  body={list(body.keys())}  {msg}")

            records.append({
                "tr_id":   tr_id,
                "ep_name": ep_name,
                "ep_path": ep_path,
                "success": hit is not None,
                "result":  hit or result,
            })

            if hit:
                _info("성공 — 나머지 엔드포인트 생략")
                break

    return records


# ── 요약 테이블 ───────────────────────────────────────────
def _print_summary(records: list[dict]) -> None:
    _header("탐색 결과 요약")
    print(f"  {_CYAN}{'TR ID':<10}  {'엔드포인트':<10}  {'결과':<6}  메모{_RESET}")
    print(f"  {'-' * 70}")

    any_ok = False
    for r in records:
        if r["success"]:
            fields = _fields(r["result"])
            memo   = f"필드 {len(fields)}개: {fields[:5]}"
            status = f"{_GREEN}OK{_RESET}  "
            any_ok = True
        else:
            memo   = _short_msg(r["result"])[:55]
            status = f"{_RED}FAIL{_RESET}"
        print(f"  {r['tr_id']:<10}  {r['ep_name']:<10}  {status}  {memo}")

    print()
    if not any_ok:
        print(f"{_RED}  결론: 시도한 모든 조합 실패 — 키움 REST API 수급 데이터 미지원{_RESET}")
    else:
        print(f"{_GREEN}  결론: 위 OK 조합으로 수급 데이터 수신 가능{_RESET}")
        print()
        print("  screener/candidate_collector.py 수정 포인트:")
        print("    _FIELD_INST_BUY = '<기관 순매수 필드명>'")
        print("    _FIELD_FRGN_BUY = '<외국인 순매수 필드명>'")


# ── 진입점 ────────────────────────────────────────────────
async def run(rest_client, broad: bool) -> list[dict]:
    if broad:
        records = await probe_all(rest_client)
    else:
        records = await probe_followup(rest_client)

    _print_summary(records)

    label   = "broad" if broad else "followup"
    ts      = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = _ROOT / "logs" / f"investor_api_{label}_{ts}.json"
    log_path.parent.mkdir(exist_ok=True)
    log_path.write_text(
        json.dumps(records, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    print(f"\n  결과 저장: {log_path}")
    return records


async def _main() -> None:
    from config.settings import AppConfig
    from core.auth import TokenManager
    from core.kiwoom_rest import KiwoomRestClient

    broad = "--all" in sys.argv

    config = AppConfig.from_yaml()
    token_mgr = TokenManager(
        config.kiwoom.app_key,
        config.kiwoom.secret_key,
        config.kiwoom.rest_base_url,
    )
    rest = KiwoomRestClient(config.kiwoom, token_mgr)
    try:
        await run(rest, broad=broad)
    finally:
        await rest.aclose()


if __name__ == "__main__":
    asyncio.run(_main())

"""scripts/analyze_ws_replay.py — JSONL 녹화 파일 WS 메시지 분석 CLI.

사용:
    python scripts/analyze_ws_replay.py <jsonl_파일_또는_디렉토리>
    python scripts/analyze_ws_replay.py              # logs/ws_replay/ 최근 파일
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

# WSReplayer.list_sessions() 활용 (프로젝트 루트가 sys.path에 없을 경우 대비)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from core.ws_replayer import WSReplayer  # noqa: E402

# ------------------------------------------------------------------
# 상수
# ------------------------------------------------------------------
DEFAULT_RECORD_DIR = "logs/ws_replay"
TOP_N_TICKERS = 10
PRICE_SAMPLE_MAX = 20
BREAKOUT_RATIO = 1.03  # 세션 시작 가격 대비 3% 상승 → 돌파 이벤트


# ------------------------------------------------------------------
# 파일 선택
# ------------------------------------------------------------------

def resolve_target(arg: str | None) -> Path:
    """CLI 인자 → 분석할 .jsonl 파일 경로 반환."""
    if arg is None:
        sessions = WSReplayer.list_sessions(DEFAULT_RECORD_DIR)
        if not sessions:
            print(f"[ERROR] {DEFAULT_RECORD_DIR} 에 .jsonl 파일이 없습니다.")
            sys.exit(1)
        return Path(sessions[0])

    p = Path(arg)
    if p.is_dir():
        sessions = WSReplayer.list_sessions(str(p))
        if not sessions:
            print(f"[ERROR] {p} 에 .jsonl 파일이 없습니다.")
            sys.exit(1)
        return Path(sessions[0])

    if not p.exists():
        print(f"[ERROR] 파일을 찾을 수 없습니다: {p}")
        sys.exit(1)
    return p


# ------------------------------------------------------------------
# JSONL 파싱
# ------------------------------------------------------------------

def _parse_raw(raw: str) -> dict | None:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _extract_items(data: dict) -> list[dict]:
    """data 딕셔너리에서 메시지 항목(들)을 반환.

    두 가지 포맷 지원:
    - top-level: {"type": "0B", "item": "...", "values": {...}}
    - array:     {"data": [{"type": "0B", ...}, ...]}
    """
    data_list = data.get("data")
    if isinstance(data_list, list):
        return data_list
    # top-level 형식
    if "type" in data:
        return [data]
    return []


def load_records(jsonl_path: Path) -> list[tuple[datetime, dict]]:
    """JSONL 파일을 읽어 (ts, raw_data) 튜플 리스트 반환.

    파싱 실패한 줄은 건너뜁니다.
    """
    records: list[tuple[datetime, dict]] = []
    with jsonl_path.open(encoding="utf-8") as f:
        for lineno, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                ts = datetime.fromisoformat(obj["ts"])
                raw_data = _parse_raw(obj["raw"])
                if raw_data is None:
                    continue
                records.append((ts, raw_data))
            except (json.JSONDecodeError, KeyError, ValueError):
                print(f"[WARN] 줄 {lineno} 파싱 실패 — 건너뜀", file=sys.stderr)
    return records


# ------------------------------------------------------------------
# 집계
# ------------------------------------------------------------------

def _price_abs(raw_val: str | int | float) -> int:
    """키움 체결가 필드(10)는 부호 있는 문자열. 절댓값 반환."""
    try:
        return abs(int(raw_val))
    except (ValueError, TypeError):
        return 0


def analyze(records: list[tuple[datetime, dict]], jsonl_path: Path | None = None) -> dict:
    """레코드 리스트를 분석해 결과 딕셔너리 반환."""
    if not records:
        return {}

    first_ts = records[0][0]
    last_ts = records[-1][0]
    total = len(records)

    type_counts: dict[str, int] = defaultdict(int)
    # 0B 종목별 카운트
    ob_ticker_count: dict[str, int] = defaultdict(int)
    # 0B 종목별 가격 시계열: {ticker: [(ts, price), ...]}
    ob_ticker_prices: dict[str, list[tuple[datetime, int]]] = defaultdict(list)
    # 0D 종목별 카운트
    od_ticker_count: dict[str, int] = defaultdict(int)

    for ts, data in records:
        trnm = data.get("trnm", "")
        if trnm == "PING":
            type_counts["PING"] += 1
            continue

        items = _extract_items(data)
        if not items:
            # REG 응답 등 기타
            type_counts["기타"] += 1
            continue

        for item in items:
            msg_type = item.get("type", "")
            if msg_type == "0B":
                type_counts["0B"] += 1
                ticker = item.get("item", "")
                ob_ticker_count[ticker] += 1
                values = item.get("values", {})
                if isinstance(values, str):
                    try:
                        values = json.loads(values)
                    except json.JSONDecodeError:
                        values = {}
                price = _price_abs(values.get("10", 0))
                if price and ticker:
                    ob_ticker_prices[ticker].append((ts, price))
            elif msg_type == "0D":
                type_counts["0D"] += 1
                ticker = item.get("item", "")
                od_ticker_count[ticker] += 1
            else:
                type_counts["기타"] += 1

    # 돌파 이벤트: 종목별 세션 첫 가격 대비 BREAKOUT_RATIO 이상 최초 도달
    breakout_events: list[tuple[str, datetime, int, int]] = []
    for ticker, price_series in ob_ticker_prices.items():
        if not price_series:
            continue
        base_price = price_series[0][1]
        threshold = base_price * BREAKOUT_RATIO
        for ts, price in price_series[1:]:
            if price >= threshold:
                breakout_events.append((ticker, ts, price, base_price))
                break

    return {
        "file": str(jsonl_path) if jsonl_path else "",
        "first_ts": first_ts,
        "last_ts": last_ts,
        "total": total,
        "type_counts": dict(type_counts),
        "ob_ticker_count": dict(ob_ticker_count),
        "ob_ticker_prices": dict(ob_ticker_prices),
        "od_ticker_count": dict(od_ticker_count),
        "breakout_events": breakout_events,
    }


# ------------------------------------------------------------------
# 출력
# ------------------------------------------------------------------

def _duration_str(first_ts: datetime, last_ts: datetime) -> str:
    diff = int((last_ts - first_ts).total_seconds())
    h, rem = divmod(diff, 3600)
    m, s = divmod(rem, 60)
    parts = []
    if h:
        parts.append(f"{h}시간")
    if m:
        parts.append(f"{m}분")
    parts.append(f"{s}초")
    return " ".join(parts)


def _pct(part: int, total: int) -> str:
    if total == 0:
        return " 0.0%"
    return f"{part / total * 100:4.1f}%"


def print_report(result: dict, jsonl_path: Path) -> None:
    first_ts: datetime = result["first_ts"]
    last_ts: datetime = result["last_ts"]
    total: int = result["total"]
    type_counts: dict[str, int] = result["type_counts"]
    ob_ticker_count: dict[str, int] = result["ob_ticker_count"]
    ob_ticker_prices: dict[str, list] = result["ob_ticker_prices"]
    breakout_events: list = result["breakout_events"]

    print()
    print("=" * 52)
    print(" WS Replay 분석")
    print("=" * 52)
    print(f"파일: {jsonl_path}")
    print(f"기간: {first_ts.strftime('%H:%M:%S')} ~ {last_ts.strftime('%H:%M:%S')}"
          f"  ({_duration_str(first_ts, last_ts)})")
    print(f"총 메시지: {total:,}건")

    # --- 타입별 분포 ---
    print()
    print("--- 타입별 분포 ---")
    ob_count = type_counts.get("0B", 0)
    od_count = type_counts.get("0D", 0)
    ping_count = type_counts.get("PING", 0)
    etc_count = type_counts.get("기타", 0)
    # 개별 항목(0B/0D)은 메시지 봉투가 아닌 아이템 수이므로 total 계산 재조정
    item_total = ob_count + od_count + ping_count + etc_count
    denom = item_total if item_total else 1
    print(f"  0B (체결):  {ob_count:>6,}건  ({_pct(ob_count, denom)})")
    print(f"  0D (호가):  {od_count:>6,}건  ({_pct(od_count, denom)})")
    print(f"  PING:       {ping_count:>6,}건  ({_pct(ping_count, denom)})")
    print(f"  기타:       {etc_count:>6,}건  ({_pct(etc_count, denom)})")

    # --- 종목별 0B 상위 ---
    if ob_ticker_count:
        print()
        print(f"--- 종목별 0B 메시지 수 (상위 {TOP_N_TICKERS}) ---")
        sorted_tickers = sorted(ob_ticker_count.items(), key=lambda x: -x[1])
        for ticker, cnt in sorted_tickers[:TOP_N_TICKERS]:
            print(f"  {ticker:<8} {cnt:>5,}건")

    # --- 가격 시계열 (상위 종목, 샘플) ---
    if ob_ticker_prices:
        top_ticker = sorted(ob_ticker_count.items(), key=lambda x: -x[1])[0][0]
        prices = ob_ticker_prices[top_ticker]
        if prices:
            print()
            print(f"--- 가격 시계열 ({top_ticker}, 최대 {PRICE_SAMPLE_MAX}건 샘플) ---")
            step = max(1, len(prices) // PRICE_SAMPLE_MAX)
            sampled = prices[::step][:PRICE_SAMPLE_MAX]
            for ts, price in sampled:
                print(f"  {ts.strftime('%H:%M:%S')}  {price:>10,}")

    # --- 돌파 이벤트 ---
    print()
    print("--- 돌파 이벤트 감지 ---")
    if breakout_events:
        for ticker, ts, price, base_price in sorted(breakout_events, key=lambda x: x[1]):
            pct = (price - base_price) / base_price * 100
            print(
                f"  {ticker}: {ts.strftime('%H:%M:%S')} @ {price:,}"
                f"  (세션 시작가 {base_price:,} 대비 +{pct:.1f}%)"
            )
    else:
        print(f"  감지 없음 (기준: 세션 시작가 × {BREAKOUT_RATIO:.2f})")

    print()


# ------------------------------------------------------------------
# 진입점
# ------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="JSONL 녹화 파일 WS 메시지 분석",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "예시:\n"
            "  python scripts/analyze_ws_replay.py\n"
            "  python scripts/analyze_ws_replay.py logs/ws_replay/\n"
            "  python scripts/analyze_ws_replay.py tests/fixtures/ws_sample.jsonl"
        ),
    )
    parser.add_argument(
        "target",
        nargs="?",
        default=None,
        help=".jsonl 파일 경로 또는 디렉토리. 생략 시 logs/ws_replay/ 최근 파일 사용.",
    )
    args = parser.parse_args()

    jsonl_path = resolve_target(args.target)
    records = load_records(jsonl_path)

    if not records:
        print(f"[ERROR] 파싱 가능한 레코드가 없습니다: {jsonl_path}")
        sys.exit(1)

    result = analyze(records, jsonl_path)
    print_report(result, jsonl_path)


if __name__ == "__main__":
    main()

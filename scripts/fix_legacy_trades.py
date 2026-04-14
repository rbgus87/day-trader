"""scripts/fix_legacy_trades.py — 과거 trades DB 기록 버그 보정 (일반화).

배경:
  execute_sell_force_close()가 exit_reason='force_close'로 하드코딩되어 있었고
  호출부에서 strategy도 누락되어, trades 테이블의 매도 행들이
  strategy='unknown', exit_reason='force_close' 상태로 오염됨.
  소스는 수정됐지만 과거 데이터는 남아있어 회고 분석 시 문제가 됨.

보정 방식:
  1) 매칭 대상 조회:
     --date 2026-04-13 [2026-04-14 ...] 처럼 명시하거나,
     --date 없으면 자동 탐지: side='sell' AND
     (strategy='unknown' OR exit_reason='force_close')
  2) day.log 파싱:
     - "시간 손절: TICKER Nn주 @ ..." → reason='time_stop'
     - "15:10 강제 청산 시작" 이후 "[PAPER] 강제 청산: TICKER ..."
       또는 engine_worker:_force_close 경로 → reason='forced_close'
     - "손절 실행: TICKER" → reason='stop_loss'
     - "trailing_stop 실행: TICKER" → reason='trailing_stop' (신버전 로그)
     - "TP1 실행: TICKER" → reason='tp1_hit'
  3) DB 매도 행 traded_at과 로그 이벤트 시각이 ±LOG_MATCH_WINDOW_SEC 내
     같은 ticker면 매칭. 매칭 실패 행은 SKIP + 보고서에 기록.
  4) strategy는 같은 날짜의 같은 ticker 매수 행에서 조회. 없으면 SKIP.
  5) --apply 시 자동 백업 생성 후 UPDATE 실행.

사용법:
  python -m scripts.fix_legacy_trades --dry-run                  # 자동 탐지 미리보기
  python -m scripts.fix_legacy_trades --date 2026-04-13 --apply  # 특정 날짜만 적용
"""

from __future__ import annotations

import argparse
import re
import shutil
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

DB_PATH = Path("daytrader.db")
DEFAULT_LOG_PATH = Path("logs/day.log")
LOG_MATCH_WINDOW_SEC = 15  # 매도 traded_at과 로그 이벤트 허용 간격

# 로그 패턴: 파일 라인 형식은 "YYYY-MM-DD HH:MM:SS.xxx | LEVEL | module:func:line - message"
LOG_LINE_RE = re.compile(
    r"^(?P<dt>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\.\d+\s*\|"
)
REASON_PATTERNS = [
    (re.compile(r"시간 손절:\s*(?P<ticker>\d{6})"), "time_stop"),
    (re.compile(r"trailing_stop 실행:\s*(?P<ticker>\d{6})"), "trailing_stop"),
    (re.compile(r"손절 실행:\s*(?P<ticker>\d{6})"), "stop_loss"),
    (re.compile(r"TP1 실행:\s*(?P<ticker>\d{6})"), "tp1_hit"),
]
FORCE_CLOSE_HEADER_RE = re.compile(r"15:10 강제 청산 시작")
FORCE_CLOSE_ACTION_RE = re.compile(
    r"강제 청산:\s*(?P<ticker>\d{6})"
)


@dataclass
class LogEvent:
    dt: datetime
    ticker: str
    reason: str


def parse_log(log_path: Path) -> list[LogEvent]:
    """day.log에서 청산 이벤트 추출."""
    events: list[LogEvent] = []
    if not log_path.exists():
        return events
    with open(log_path, encoding="utf-8", errors="replace") as f:
        in_force_close_block = False
        force_close_block_time: datetime | None = None
        for line in f:
            m = LOG_LINE_RE.match(line)
            if not m:
                continue
            try:
                dt = datetime.strptime(m.group("dt"), "%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue

            if FORCE_CLOSE_HEADER_RE.search(line):
                in_force_close_block = True
                force_close_block_time = dt
                continue

            # 강제 청산 헤더 후 같은 분 내에 나오는 "강제 청산: TICKER"는 forced_close
            fc = FORCE_CLOSE_ACTION_RE.search(line)
            if fc:
                ticker = fc.group("ticker")
                is_forced = (
                    in_force_close_block
                    and force_close_block_time is not None
                    and (dt - force_close_block_time).total_seconds() <= 60
                )
                if is_forced:
                    events.append(LogEvent(dt=dt, ticker=ticker, reason="forced_close"))
                # 그 외 "강제 청산" 로그는 paper_order_manager의 execute_sell_force_close
                # 호출 시작 로그라서 결과 사유가 아님 → 무시 (실제 사유는 engine_worker
                # 후속 로그에서 "시간 손절"/"TP1 실행" 등으로 별도 기록됨)
                continue

            for pat, reason in REASON_PATTERNS:
                m2 = pat.search(line)
                if m2:
                    events.append(
                        LogEvent(dt=dt, ticker=m2.group("ticker"), reason=reason)
                    )
                    break

            if "15:10" not in line and in_force_close_block:
                # 블록 종료는 시간 경과로 판정
                if force_close_block_time and (dt - force_close_block_time).total_seconds() > 120:
                    in_force_close_block = False
                    force_close_block_time = None
    return events


def match_reason(
    events: list[LogEvent], ticker: str, traded_at: str,
) -> str | None:
    """traded_at ± LOG_MATCH_WINDOW_SEC 내 같은 ticker 이벤트에서 사유 반환."""
    try:
        traded_dt = datetime.fromisoformat(traded_at)
    except ValueError:
        return None
    best: tuple[int, str] | None = None
    for ev in events:
        if ev.ticker != ticker:
            continue
        diff = abs((ev.dt - traded_dt).total_seconds())
        if diff <= LOG_MATCH_WINDOW_SEC:
            if best is None or diff < best[0]:
                best = (int(diff), ev.reason)
    return best[1] if best else None


def fetch_targets(
    conn: sqlite3.Connection, dates: list[str] | None,
) -> list[sqlite3.Row]:
    """보정 대상 매도 행 조회."""
    conn.row_factory = sqlite3.Row
    if dates:
        placeholders = ",".join("?" * len(dates))
        sql = (
            "SELECT id, ticker, strategy, exit_reason, pnl, traded_at FROM trades "
            f"WHERE side='sell' AND substr(traded_at,1,10) IN ({placeholders}) "
            "ORDER BY traded_at"
        )
        rows = conn.execute(sql, dates).fetchall()
    else:
        sql = (
            "SELECT id, ticker, strategy, exit_reason, pnl, traded_at FROM trades "
            "WHERE side='sell' AND (strategy='unknown' OR exit_reason='force_close') "
            "ORDER BY traded_at"
        )
        rows = conn.execute(sql).fetchall()
    return rows


def build_strategy_map(conn: sqlite3.Connection) -> dict[tuple[str, str], str]:
    """(date, ticker) → strategy (매수 행에서 조회). 'unknown'은 제외."""
    rows = conn.execute(
        "SELECT substr(traded_at,1,10) AS dt, ticker, strategy FROM trades "
        "WHERE side='buy' AND strategy IS NOT NULL AND strategy!='unknown'"
    ).fetchall()
    out: dict[tuple[str, str], str] = {}
    for dt, ticker, strategy in rows:
        out[(dt, ticker)] = strategy
    return out


def backup_db() -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = DB_PATH.with_name(f"daytrader_pre_fix_{ts}.db")
    shutil.copy2(DB_PATH, backup_path)
    return backup_path


@dataclass
class Plan:
    id: int
    ticker: str
    traded_at: str
    strategy_old: str | None
    strategy_new: str | None
    reason_old: str | None
    reason_new: str | None
    skip_reason: str | None = None

    @property
    def needs_update(self) -> bool:
        if self.skip_reason:
            return False
        return (
            self.strategy_new != self.strategy_old
            or self.reason_new != self.reason_old
        )


def build_plans(
    targets: list[sqlite3.Row],
    events: list[LogEvent],
    strategy_map: dict[tuple[str, str], str],
) -> list[Plan]:
    plans: list[Plan] = []
    for r in targets:
        tid = r["id"]
        ticker = r["ticker"]
        traded_at = r["traded_at"]
        strategy_old = r["strategy"]
        reason_old = r["exit_reason"]

        reason_new = match_reason(events, ticker, traded_at)
        dt_key = traded_at[:10]
        strategy_new = strategy_map.get((dt_key, ticker))

        skip: str | None = None
        if reason_new is None:
            skip = "로그 매칭 실패 (reason)"
        if strategy_new is None:
            skip = (skip + "; 매수행 strategy 없음" if skip else "매수행 strategy 없음")

        plans.append(Plan(
            id=tid, ticker=ticker, traded_at=traded_at,
            strategy_old=strategy_old,
            strategy_new=strategy_new if strategy_new else strategy_old,
            reason_old=reason_old,
            reason_new=reason_new if reason_new else reason_old,
            skip_reason=skip,
        ))
    return plans


def print_plans(plans: list[Plan]) -> None:
    print(f"=== 보정 대상 {len(plans)}건 ===")
    header = (
        f"{'id':>4} {'ticker':8} {'time':19} "
        f"{'strategy':24} {'exit_reason':28} mark"
    )
    print(header)
    print("-" * len(header))
    for p in plans:
        strat_col = f"{p.strategy_old}→{p.strategy_new}"
        reason_col = f"{p.reason_old}→{p.reason_new}"
        if p.skip_reason:
            mark = f"SKIP ({p.skip_reason})"
        elif p.needs_update:
            mark = "*"
        else:
            mark = "(변경없음)"
        print(
            f"{p.id:>4} {p.ticker:8} {p.traded_at[:19]:19} "
            f"{strat_col:24} {reason_col:28} {mark}"
        )


def apply_updates(
    conn: sqlite3.Connection, plans: list[Plan],
) -> tuple[int, set[str]]:
    cur = conn.cursor()
    changed = 0
    changed_dates: set[str] = set()
    for p in plans:
        if not p.needs_update:
            continue
        cur.execute(
            "UPDATE trades SET strategy=?, exit_reason=? WHERE id=?",
            (p.strategy_new, p.reason_new, p.id),
        )
        changed += 1
        changed_dates.add(p.traded_at[:10])
    conn.commit()
    return changed, changed_dates


def fix_daily_pnl(
    conn: sqlite3.Connection, dates: set[str],
) -> int:
    """영향받은 날짜의 daily_pnl.strategy를 실제 운영 전략들로 교체."""
    cur = conn.cursor()
    updated = 0
    for dt in dates:
        rows = conn.execute(
            "SELECT DISTINCT strategy FROM trades "
            "WHERE side='sell' AND substr(traded_at,1,10)=? "
            "AND strategy IS NOT NULL AND strategy!='unknown'",
            (dt,),
        ).fetchall()
        strategies = sorted(s[0] for s in rows)
        if not strategies:
            continue
        strategy_str = ",".join(strategies)
        cur.execute(
            "UPDATE daily_pnl SET strategy=? WHERE date=? AND strategy!=?",
            (strategy_str, dt, strategy_str),
        )
        if cur.rowcount > 0:
            updated += cur.rowcount
    conn.commit()
    return updated


def verify(conn: sqlite3.Connection, dates: list[str] | None) -> None:
    print()
    print("=== 보정 후 확인 ===")
    if dates:
        placeholders = ",".join("?" * len(dates))
        rows = conn.execute(
            f"SELECT substr(traded_at,1,10) AS dt, exit_reason, strategy, COUNT(*) AS cnt "
            f"FROM trades WHERE side='sell' AND substr(traded_at,1,10) IN ({placeholders}) "
            f"GROUP BY dt, exit_reason, strategy ORDER BY dt, exit_reason",
            dates,
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT substr(traded_at,1,10) AS dt, exit_reason, strategy, COUNT(*) AS cnt "
            "FROM trades WHERE side='sell' "
            "GROUP BY dt, exit_reason, strategy HAVING cnt > 0 "
            "ORDER BY dt DESC, exit_reason LIMIT 30"
        ).fetchall()
    for dt, reason, strat, cnt in rows:
        print(f"  {dt} | exit_reason={reason or 'NULL':14} strategy={strat or 'NULL':10} count={cnt}")

    print()
    print("=== daily_pnl (최근 10일) ===")
    for r in conn.execute(
        "SELECT date, strategy, total_trades, wins, losses, total_pnl "
        "FROM daily_pnl ORDER BY date DESC LIMIT 10"
    ):
        print(
            f"  {r[0]} strategy={r[1]:15} trades={r[2]} "
            f"wins={r[3]} losses={r[4]} pnl={r[5]:+.0f}"
        )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--date", nargs="+", default=None,
        help="보정 대상 날짜 (YYYY-MM-DD, 복수 가능). 생략 시 자동 탐지.",
    )
    ap.add_argument(
        "--log", default=str(DEFAULT_LOG_PATH),
        help=f"로그 파일 경로 (기본: {DEFAULT_LOG_PATH})",
    )
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true", default=True,
                   help="변경사항만 출력 (기본)")
    g.add_argument("--apply", action="store_true",
                   help="실제 UPDATE 실행 (자동 백업)")
    args = ap.parse_args()

    if not DB_PATH.exists():
        print(f"[ERROR] {DB_PATH} 없음", file=sys.stderr)
        return 1

    log_path = Path(args.log)
    if not log_path.exists():
        print(f"[WARN] 로그 파일 없음: {log_path} — 매칭 0건으로 전부 SKIP 예상")

    conn = sqlite3.connect(DB_PATH)
    try:
        events = parse_log(log_path)
        print(f"[INFO] 로그 이벤트 {len(events)}건 추출")
        targets = fetch_targets(conn, args.date)
        if not targets:
            print("[INFO] 보정 대상 없음")
            return 0
        strategy_map = build_strategy_map(conn)
        plans = build_plans(targets, events, strategy_map)
        print_plans(plans)

        if not args.apply:
            skipped = sum(1 for p in plans if p.skip_reason)
            to_update = sum(1 for p in plans if p.needs_update)
            print()
            print(f"[DRY RUN] 업데이트 예정: {to_update}건 / SKIP: {skipped}건")
            print("[DRY RUN] --apply 로 실제 적용 가능")
            return 0

        conn.close()
        backup_path = backup_db()
        print()
        print(f"[INFO] DB 백업 생성: {backup_path}")
        conn = sqlite3.connect(DB_PATH)
        changed, changed_dates = apply_updates(conn, plans)
        daily_changed = fix_daily_pnl(conn, changed_dates)
        print(
            f"[INFO] trades UPDATE: {changed}건 / "
            f"daily_pnl UPDATE: {daily_changed}건 / "
            f"영향 날짜: {sorted(changed_dates)}"
        )
        verify(conn, args.date)
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())

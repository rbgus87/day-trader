"""tests/test_logging_config.py — utils/logging_config 단위 테스트."""

import json
import sys
import threading
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from loguru import logger

# 테스트마다 전역 상태 리셋이 필요하므로 모듈 직접 import
import utils.logging_config as lc


@pytest.fixture(autouse=True)
def _reset_sink(tmp_path):
    """각 테스트 전후 싱크 전역 상태 초기화."""
    original_id = lc._sink_id
    original_inst = lc._sink_instance
    # 이전 싱크 제거 (다른 테스트가 추가했을 수 있음)
    if lc._sink_id is not None:
        try:
            logger.remove(lc._sink_id)
        except Exception:
            pass
    lc._sink_id = None
    lc._sink_instance = None
    yield
    # 사후 정리
    if lc._sink_id is not None and lc._sink_id != original_id:
        try:
            logger.remove(lc._sink_id)
        except Exception:
            pass
    lc._sink_id = original_id
    lc._sink_instance = original_inst


# ── _JsonlSink ──────────────────────────────────────────────────────────────


def test_sink_writes_event_records(tmp_path):
    """event 키가 있는 레코드는 JSONL에 기록된다."""
    sink = lc._JsonlSink(log_dir=str(tmp_path))
    sid = logger.add(sink, level="INFO", format="{message}")
    try:
        logger.bind(event="entry", ticker="005930", price=75000).info("진입")
        # 파일 존재 확인
        files = list(tmp_path.glob("daytrader_*.jsonl"))
        assert len(files) == 1
        lines = files[0].read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        row = json.loads(lines[0])
        assert row["event"] == "entry"
        assert row["ticker"] == "005930"
        assert row["price"] == 75000
        assert "ts" in row
    finally:
        logger.remove(sid)


def test_sink_skips_records_without_event(tmp_path):
    """event 키가 없는 일반 logger.info 레코드는 JSONL에 기록되지 않는다."""
    sink = lc._JsonlSink(log_dir=str(tmp_path))
    sid = logger.add(sink, level="INFO", format="{message}")
    try:
        logger.info("일반 로그 메시지")
        files = list(tmp_path.glob("daytrader_*.jsonl"))
        if files:
            content = files[0].read_text(encoding="utf-8").strip()
            assert content == ""
        # else: 파일 미생성도 정상
    finally:
        logger.remove(sid)


def test_sink_skips_debug_records(tmp_path):
    """DEBUG 레코드는 싱크 level="INFO" 필터로 전달되지 않는다."""
    sink = lc._JsonlSink(log_dir=str(tmp_path))
    sid = logger.add(sink, level="INFO", format="{message}")
    try:
        logger.bind(event="debug_event", detail="x").debug("디버그")
        files = list(tmp_path.glob("daytrader_*.jsonl"))
        assert len(files) == 0
    finally:
        logger.remove(sid)


def test_sink_json_format(tmp_path):
    """ts 필드가 ISO 형식이고 extra 필드가 모두 포함된다."""
    sink = lc._JsonlSink(log_dir=str(tmp_path))
    sid = logger.add(sink, level="INFO", format="{message}")
    try:
        logger.bind(event="exit", ticker="000660", pnl=12345, pnl_pct=2.5).info("청산")
        files = list(tmp_path.glob("daytrader_*.jsonl"))
        row = json.loads(files[0].read_text(encoding="utf-8").strip())
        assert "ts" in row
        # ISO 형식 파싱 가능 여부 확인
        datetime.fromisoformat(row["ts"].replace("Z", "+00:00"))
        assert row["pnl"] == 12345
        assert row["pnl_pct"] == 2.5
    finally:
        logger.remove(sid)


def test_sink_appends_multiple_records(tmp_path):
    """여러 이벤트가 JSONL에 순서대로 기록된다."""
    sink = lc._JsonlSink(log_dir=str(tmp_path))
    sid = logger.add(sink, level="INFO", format="{message}")
    try:
        logger.bind(event="entry", ticker="A").info("진입A")
        logger.bind(event="exit", ticker="A", pnl=100).info("청산A")
        logger.bind(event="entry", ticker="B").info("진입B")
        files = list(tmp_path.glob("daytrader_*.jsonl"))
        lines = files[0].read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 3
        events = [json.loads(l)["event"] for l in lines]
        assert events == ["entry", "exit", "entry"]
    finally:
        logger.remove(sid)


def test_sink_thread_safe(tmp_path):
    """멀티스레드 동시 기록 시 파일이 손상되지 않는다."""
    sink = lc._JsonlSink(log_dir=str(tmp_path))
    sid = logger.add(sink, level="INFO", format="{message}")
    try:
        errors: list[Exception] = []

        def worker(idx: int):
            try:
                for _ in range(20):
                    logger.bind(event="entry", idx=idx).info("진입")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        files = list(tmp_path.glob("daytrader_*.jsonl"))
        lines = files[0].read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 100  # 5 threads × 20 records
        for line in lines:
            json.loads(line)  # 각 줄이 유효한 JSON이어야 함
    finally:
        logger.remove(sid)


# ── _cleanup_old_logs ────────────────────────────────────────────────────────


def test_cleanup_removes_old_files(tmp_path):
    """retention_days를 초과한 파일이 삭제된다."""
    old_date = (datetime.now() - timedelta(days=35)).strftime("%Y%m%d")
    recent_date = datetime.now().strftime("%Y%m%d")
    old_file = tmp_path / f"daytrader_{old_date}.jsonl"
    recent_file = tmp_path / f"daytrader_{recent_date}.jsonl"
    old_file.write_text("{}", encoding="utf-8")
    recent_file.write_text("{}", encoding="utf-8")

    lc._cleanup_old_logs(tmp_path, retention_days=30)

    assert not old_file.exists()
    assert recent_file.exists()


def test_cleanup_ignores_non_matching_files(tmp_path):
    """패턴이 맞지 않는 파일은 삭제하지 않는다."""
    other_file = tmp_path / "day.log"
    other_file.write_text("x", encoding="utf-8")
    lc._cleanup_old_logs(tmp_path, retention_days=0)  # 모두 삭제 조건
    assert other_file.exists()


# ── setup_json_logging ───────────────────────────────────────────────────────


def test_setup_json_logging_idempotent(tmp_path):
    """setup_json_logging을 여러 번 호출해도 싱크가 하나만 등록된다."""
    lc.setup_json_logging(log_dir=str(tmp_path))
    first_id = lc._sink_id
    lc.setup_json_logging(log_dir=str(tmp_path))
    assert lc._sink_id == first_id  # 두 번째 호출은 no-op


def test_setup_json_logging_registers_sink(tmp_path):
    """setup_json_logging 후 bind(event=...).info 호출 시 JSONL 파일 생성된다."""
    lc.setup_json_logging(log_dir=str(tmp_path))
    logger.bind(event="daily_summary", total_pnl=50000).info("보고서")
    files = list(tmp_path.glob("daytrader_*.jsonl"))
    assert len(files) == 1
    row = json.loads(files[0].read_text(encoding="utf-8").strip())
    assert row["event"] == "daily_summary"
    assert row["total_pnl"] == 50000

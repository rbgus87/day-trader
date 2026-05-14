"""tests/test_ws_replay.py — WSRecorder / WSReplayer 통합 테스트."""

import asyncio
import json
import re
import tempfile
from pathlib import Path

import pytest

from core.ws_recorder import WSRecorder
from core.ws_replayer import WSReplayer

# ---------------------------------------------------------------------------
# 공통 픽스처
# ---------------------------------------------------------------------------

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "ws_sample.jsonl"


# ---------------------------------------------------------------------------
# WSRecorder 테스트
# ---------------------------------------------------------------------------


def test_recorder_creates_file(tmp_path):
    """start() 후 JSONL 파일이 실제로 생성된다."""
    recorder = WSRecorder(record_dir=str(tmp_path / "rec"))
    recorder.start(session_id="test_session")
    try:
        assert recorder.current_file is not None
        assert Path(recorder.current_file).exists()
    finally:
        recorder.stop()


def test_recorder_filters_login(tmp_path):
    """LOGIN 메시지는 파일에 기록되지 않는다."""
    recorder = WSRecorder(record_dir=str(tmp_path / "rec"))
    recorder.start(session_id="test_login_filter")
    login_msg = json.dumps({"trid": "LOGIN", "token": "secret"})
    normal_msg = json.dumps({"trnm": "PING"})
    recorder.record(login_msg)
    recorder.record(normal_msg)
    assert recorder.message_count == 1  # PING만 카운트
    recorder.stop()

    # 파일에도 LOGIN이 없는지 확인
    rec_dir = tmp_path / "rec"
    files = list(rec_dir.glob("*.jsonl"))
    assert len(files) == 1
    lines = [l for l in files[0].read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(lines) == 1
    row = json.loads(lines[0])
    raw_parsed = json.loads(row["raw"])
    assert raw_parsed.get("trid") != "LOGIN"


def test_recorder_counts_messages(tmp_path):
    """record() 호출마다 message_count가 정확히 증가한다."""
    recorder = WSRecorder(record_dir=str(tmp_path / "rec"))
    recorder.start(session_id="count_test")
    try:
        for i in range(5):
            recorder.record(json.dumps({"trnm": "REAL", "seq": i}))
        assert recorder.message_count == 5
        recorder.record(json.dumps({"trnm": "PING"}))
        assert recorder.message_count == 6
    finally:
        recorder.stop()


def test_recorder_stop_flushes(tmp_path):
    """stop() 후 파일이 닫히고 내용이 완전히 저장된다."""
    recorder = WSRecorder(record_dir=str(tmp_path / "rec"))
    recorder.start(session_id="flush_test")
    # 버퍼 크기(50)보다 적은 수를 기록해 버퍼에만 쌓아둠
    for i in range(10):
        recorder.record(json.dumps({"trnm": "PING", "seq": i}))
    file_path = recorder.current_file
    recorder.stop()

    # 녹화 중지 후 파일이 존재하고 내용이 있어야 함
    assert file_path is not None
    p = Path(file_path)
    assert p.exists()
    lines = [l for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(lines) == 10


# ---------------------------------------------------------------------------
# WSReplayer 테스트
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_replayer_loads_sample_fixture():
    """tests/fixtures/ws_sample.jsonl을 WSReplayer로 로드 시 message_count == 100."""
    assert FIXTURE_PATH.exists(), f"픽스처 파일 없음: {FIXTURE_PATH}"
    replayer = WSReplayer(str(FIXTURE_PATH), speed=0)
    assert replayer.message_count == 100


@pytest.mark.asyncio
async def test_replayer_replay_fills_queue():
    """speed=0으로 replay 시 queue에 100개 메시지가 모두 투입된다."""
    assert FIXTURE_PATH.exists(), f"픽스처 파일 없음: {FIXTURE_PATH}"
    queue: asyncio.Queue = asyncio.Queue()
    replayer = WSReplayer(str(FIXTURE_PATH), speed=0)
    count = await replayer.replay(queue)
    assert count == 100
    assert queue.qsize() == 100


@pytest.mark.asyncio
async def test_replayer_filters_invalid_lines(tmp_path):
    """파싱 불가 줄이 있어도 나머지 유효한 줄은 정상 재생된다."""
    jsonl_file = tmp_path / "mixed.jsonl"
    valid_msg = json.dumps({"trnm": "PING"})
    good_line = json.dumps({"ts": "2026-05-14T09:00:00", "raw": valid_msg})
    lines = [
        good_line,
        "NOT_VALID_JSON!!!",
        good_line,
        '{"ts": "2026-05-14T09:00:02"}',  # raw 키 없음
        good_line,
    ]
    jsonl_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    queue: asyncio.Queue = asyncio.Queue()
    replayer = WSReplayer(str(jsonl_file), speed=0)
    count = await replayer.replay(queue)
    # 유효한 줄 3개만 재생
    assert count == 3
    assert queue.qsize() == 3


@pytest.mark.asyncio
async def test_recorder_replayer_roundtrip(tmp_path):
    """Recorder로 녹화한 파일을 Replayer로 재생하면 원본 내용과 일치한다."""
    messages = [
        json.dumps({"trnm": "REAL", "data": [{"type": "0B", "item": "005930", "values": {"10": str(70000 + i * 100)}}]})
        for i in range(5)
    ]
    messages.append(json.dumps({"trnm": "PING"}))

    rec_dir = tmp_path / "rec"
    recorder = WSRecorder(record_dir=str(rec_dir))
    recorder.start(session_id="roundtrip")
    for msg in messages:
        recorder.record(msg)
    recorder.stop()

    # Replay
    files = list(rec_dir.glob("*.jsonl"))
    assert len(files) == 1

    queue: asyncio.Queue = asyncio.Queue()
    replayer = WSReplayer(str(files[0]), speed=0)
    count = await replayer.replay(queue)

    assert count == len(messages)
    replayed_msgs = []
    while not queue.empty():
        replayed_msgs.append(queue.get_nowait())
    assert replayed_msgs == messages


@pytest.mark.asyncio
async def test_replayer_speed_zero_no_delay(tmp_path):
    """speed=0 시 지연 없이 빠르게 완료된다 (2초 이내)."""
    import time

    jsonl_file = tmp_path / "fast.jsonl"
    lines = []
    # 타임스탬프 간격 5초씩 (speed=1이면 50초 걸릴 내용)
    from datetime import datetime, timedelta
    base = datetime(2026, 5, 14, 9, 0, 0)
    for i in range(10):
        ts = (base + timedelta(seconds=i * 5)).isoformat()
        raw = json.dumps({"trnm": "PING"})
        lines.append(json.dumps({"ts": ts, "raw": raw}))
    jsonl_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    queue: asyncio.Queue = asyncio.Queue()
    replayer = WSReplayer(str(jsonl_file), speed=0)
    t0 = time.monotonic()
    count = await replayer.replay(queue)
    elapsed = time.monotonic() - t0

    assert count == 10
    assert elapsed < 2.0, f"speed=0 재생이 너무 느림: {elapsed:.2f}s"


def test_replayer_list_sessions(tmp_path):
    """record_dir 내 .jsonl 파일 목록을 반환한다."""
    rec_dir = tmp_path / "sessions"
    rec_dir.mkdir()
    # 파일 3개 생성
    for name in ["a.jsonl", "b.jsonl", "c.jsonl"]:
        (rec_dir / name).write_text("{}\n", encoding="utf-8")

    sessions = WSReplayer.list_sessions(str(rec_dir))
    assert len(sessions) == 3
    # 반환값이 모두 .jsonl 파일인지 확인
    assert all(s.endswith(".jsonl") for s in sessions)


def test_replayer_list_sessions_empty(tmp_path):
    """존재하지 않는 디렉토리에 대해 빈 목록을 반환한다."""
    sessions = WSReplayer.list_sessions(str(tmp_path / "nonexistent"))
    assert sessions == []


def test_recorder_session_id_format(tmp_path):
    """session_id=None 시 파일명이 %Y%m%d_%H%M%S.jsonl 형식이다."""
    recorder = WSRecorder(record_dir=str(tmp_path / "rec"))
    recorder.start(session_id=None)
    file_path = recorder.current_file
    recorder.stop()

    assert file_path is not None
    filename = Path(file_path).name
    # 형식: YYYYMMDD_HHMMSS.jsonl
    pattern = re.compile(r"^\d{8}_\d{6}\.jsonl$")
    assert pattern.match(filename), f"파일명 형식 불일치: {filename}"


@pytest.mark.asyncio
async def test_replayer_file_not_found(tmp_path):
    """존재하지 않는 파일로 replay() 시 FileNotFoundError가 발생한다."""
    nonexistent = tmp_path / "ghost.jsonl"
    replayer = WSReplayer(str(nonexistent), speed=0)
    queue: asyncio.Queue = asyncio.Queue()
    with pytest.raises(FileNotFoundError):
        await replayer.replay(queue)

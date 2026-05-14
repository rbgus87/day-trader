"""core/ws_recorder.py — WebSocket 메시지 JSONL 녹화기.

보안: trid == "LOGIN" 메시지는 무조건 드롭 (토큰/인증 정보 포함 금지).
asyncio 루프 내에서만 호출되므로 thread-safe 보장 불필요.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import IO

from loguru import logger

_WRITE_BUFFER_SIZE = 50  # 버퍼 크기 (모듈 상수)


class WSRecorder:
    """WebSocket 메시지를 JSONL 파일로 녹화한다.

    파일명: {record_dir}/{session_id}.jsonl
    포맷:   {"ts": "2026-05-14T08:30:12.345678", "raw": "<원본 문자열>"}
    """

    def __init__(
        self,
        record_dir: str = "logs/ws_replay",
    ) -> None:
        self._record_dir = Path(record_dir)

        self._session_id: str | None = None
        self._file: IO[str] | None = None
        self._buffer: list[str] = []
        self._message_count: int = 0
        self._current_file: str | None = None

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def start(self, session_id: str | None = None) -> None:
        """녹화 시작.

        session_id가 None이면 현재 시각으로 자동 생성 (예: 20260514_083012).
        이미 녹화 중이면 기존 세션을 먼저 stop() 한 뒤 새 세션을 시작한다.
        """
        if self._file is not None:
            logger.warning("WSRecorder: 녹화 중 start() 호출 — 기존 세션 종료 후 재시작")
            self.stop()

        if session_id is None:
            session_id = datetime.now().strftime("%Y%m%d_%H%M%S")

        self._session_id = session_id
        self._message_count = 0
        self._buffer = []

        self._record_dir.mkdir(parents=True, exist_ok=True)
        file_path = self._record_dir / f"{session_id}.jsonl"
        self._file = open(file_path, "w", encoding="utf-8")  # noqa: WPS515
        self._current_file = str(file_path)

        logger.info(f"WSRecorder: 녹화 시작 — {self._current_file}")

    def stop(self) -> None:
        """버퍼 flush 후 파일 닫기."""
        if self._file is None:
            return

        try:
            self._flush()
        finally:
            self._file.close()
        logger.info(
            f"WSRecorder: 녹화 종료 — {self._current_file} "
            f"(총 {self._message_count}건)"
        )

        self._file = None
        self._session_id = None
        self._current_file = None
        self._buffer = []

    def record(self, raw_message: str) -> None:
        """단일 WS 메시지를 녹화한다.

        - LOGIN 메시지는 무조건 드롭.
        - JSON 파싱 실패 시 그냥 기록 (억제).
        - 녹화 중이 아니면 무시.
        """
        if self._file is None:
            return
        if isinstance(raw_message, bytes):
            raw_message = raw_message.decode("utf-8", errors="replace")

        # LOGIN 필터링 (보안)
        try:
            data = json.loads(raw_message)
            if data.get("trid") == "LOGIN":
                return
        except (json.JSONDecodeError, AttributeError):
            pass  # 파싱 실패는 억제하고 그냥 기록

        line = (
            json.dumps(
                {"ts": datetime.now().isoformat(), "raw": raw_message},
                ensure_ascii=False,
            )
            + "\n"
        )
        self._buffer.append(line)
        self._message_count += 1

        if len(self._buffer) >= _WRITE_BUFFER_SIZE:
            self._flush()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def is_recording(self) -> bool:
        """현재 녹화 중 여부."""
        return self._file is not None

    @property
    def message_count(self) -> int:
        """현재 세션에서 녹화된 메시지 수 (필터링된 것 제외)."""
        return self._message_count

    @property
    def current_file(self) -> str | None:
        """현재 녹화 중인 파일 경로. 녹화 중이 아니면 None."""
        return self._current_file

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _flush(self) -> None:
        """버퍼를 파일에 기록하고 비운다."""
        if self._file is None or not self._buffer:
            return
        self._file.writelines(self._buffer)
        self._file.flush()
        self._buffer = []

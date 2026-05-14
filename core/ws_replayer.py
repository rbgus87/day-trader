"""core/ws_replayer.py — JSONL 녹화 파일을 asyncio Queue로 재생한다.

WSRecorder가 생성한 .jsonl 파일을 읽어 원본 타임스탬프 간격을
speed 배율에 맞게 조절하며 메시지를 Queue에 넣는다.
테스트 및 divergence 분석 용도.
"""

import asyncio
import json
from datetime import datetime
from pathlib import Path

from loguru import logger


class WSReplayer:
    """JSONL 녹화 파일을 asyncio Queue로 재생한다.

    포맷: {"ts": "2026-05-14T08:30:12.345678", "raw": "<원본 문자열>"}
    """

    def __init__(self, jsonl_path: str, speed: float = 1.0) -> None:
        """
        Parameters
        ----------
        jsonl_path:
            재생할 .jsonl 파일 경로.
        speed:
            재생 속도 배율. 1.0=원속, 2.0=2배속, 0=지연 없이 즉시 재생.
        """
        self._path = Path(jsonl_path)
        if speed < 0:
            raise ValueError(f"speed는 0 이상이어야 합니다: {speed}")
        self._speed = speed
        self._message_count_cache: int | None = None

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def replay(self, queue: asyncio.Queue) -> int:
        """JSONL 파일의 메시지를 순서대로 queue에 넣는다.

        원본 타임스탬프 간격을 speed 배율에 맞게 조절해 sleep한다.
        파싱 실패한 줄은 건너뛴다 (warn 로그).

        Returns
        -------
        int
            재생된 메시지 수.
        """
        if not self._path.exists():
            raise FileNotFoundError(f"JSONL 파일을 찾을 수 없습니다: {self._path}")

        replayed = 0
        prev_ts: datetime | None = None

        with self._path.open(encoding="utf-8") as f:
            for lineno, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue

                try:
                    obj = json.loads(line)
                    ts = datetime.fromisoformat(obj["ts"])
                    raw: str = obj["raw"]
                except (json.JSONDecodeError, KeyError, ValueError) as exc:
                    logger.warning(
                        f"WSReplayer: 줄 {lineno} 파싱 실패 — {exc!r}, 건너뜀"
                    )
                    continue

                # 이전 메시지와의 간격만큼 대기
                if prev_ts is not None and self._speed > 0:
                    interval = (ts - prev_ts).total_seconds()
                    if interval > 0:
                        await asyncio.sleep(interval / self._speed)

                await queue.put(raw)
                prev_ts = ts
                replayed += 1

        logger.info(f"WSReplayer: 재생 완료 — {self._path.name} ({replayed}건)")
        self._message_count_cache = replayed
        return replayed

    @property
    def message_count(self) -> int:
        """캐싱된 메시지 수. replay() 호출 전에는 파일을 읽어 계산."""
        if self._message_count_cache is None:
            if not self._path.exists():
                return 0
            count = 0
            with self._path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        if "ts" in data and "raw" in data:
                            count += 1
                    except Exception:
                        pass
            self._message_count_cache = count
        return self._message_count_cache

    @staticmethod
    def list_sessions(record_dir: str = "logs/ws_replay") -> list[str]:
        """record_dir 내 .jsonl 파일 목록을 최신순으로 반환한다.

        Returns
        -------
        list[str]
            .jsonl 파일 경로 문자열 리스트 (수정 시각 내림차순).
        """
        base = Path(record_dir)
        if not base.exists():
            return []

        files = sorted(
            base.glob("*.jsonl"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        return [str(p) for p in files]

"""utils/logging_config.py — JSONL structured logging for post-market analysis."""
from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from loguru import logger

_sink_instance: "_JsonlSink | None" = None
_sink_id: "int | None" = None


def _cleanup_old_logs(log_dir: Path, retention_days: int) -> None:
    cutoff = datetime.now() - timedelta(days=retention_days)
    for f in log_dir.glob("daytrader_*.jsonl"):
        try:
            date_str = f.stem.split("_", 1)[1]  # "YYYYMMDD"
            file_date = datetime.strptime(date_str, "%Y%m%d")
            if file_date < cutoff:
                f.unlink()
        except Exception:
            pass


class _JsonlSink:
    """Loguru callable sink: JSONL, daily rotation, structured events only."""

    def __init__(self, log_dir: str = "logs", retention_days: int = 30) -> None:
        self._log_dir = Path(log_dir)
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._retention_days = retention_days
        self._lock = threading.Lock()
        self._current_date = ""
        self._fh = None
        _cleanup_old_logs(self._log_dir, retention_days)

    def _rotate(self, date_str: str) -> None:
        if self._fh is not None:
            try:
                self._fh.close()
            except Exception:
                pass
        path = self._log_dir / f"daytrader_{date_str}.jsonl"
        self._fh = open(path, "a", encoding="utf-8")
        self._current_date = date_str
        _cleanup_old_logs(self._log_dir, self._retention_days)

    def __call__(self, message) -> None:
        record = message.record
        extra: dict[str, Any] = record.get("extra", {})
        if "event" not in extra:
            return

        date_str = record["time"].strftime("%Y%m%d")
        row: dict[str, Any] = {
            "ts": record["time"].isoformat(timespec="milliseconds"),
        }
        row.update(extra)
        line = json.dumps(row, ensure_ascii=False)

        with self._lock:
            if date_str != self._current_date:
                self._rotate(date_str)
            try:
                self._fh.write(line + "\n")
                self._fh.flush()
            except Exception:
                pass


def setup_json_logging(log_dir: str = "logs", retention_days: int = 30) -> None:
    """Add JSONL sink to loguru. Idempotent — safe to call multiple times."""
    global _sink_instance, _sink_id
    if _sink_id is not None:
        return
    _sink_instance = _JsonlSink(log_dir=log_dir, retention_days=retention_days)
    _sink_id = logger.add(_sink_instance, level="INFO", format="{message}")

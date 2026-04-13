"""GUI 진입점.

Usage:
    python gui.py              # GUI 실행
    python gui.py --selftest   # 환경 검증 후 종료
"""

import atexit
import multiprocessing
import os
import sys


def _force_exit():
    """atexit 핸들러 — 프로세스가 남아있으면 강제 종료."""
    try:
        os._exit(0)
    except Exception:
        pass


def _attach_console_for_cli() -> None:
    """--windowed 빌드에서 CLI 모드용 stdout 회수.

    PyInstaller --windowed 는 subsystem:windows 로 링크되어 stdout 무효.
    단, subprocess 파이프 리다이렉트 환경에서는 stdout 이 이미 유효하므로
    그 경우엔 건드리지 않아야 capture_output 이 정상 동작.

    1) 이미 유효한 stdout (파이프/tty) 있으면 그대로 사용
    2) 무효일 때만 AttachConsole(-1) → 실패 시 AllocConsole → CONOUT$ 재바인딩
    """
    if sys.platform != "win32":
        return
    # 이미 유효한 stdout (subprocess 파이프 / console inherit) 은 보존
    try:
        if sys.stdout is not None and sys.stdout.fileno() >= 0:
            return
    except (AttributeError, OSError, ValueError):
        pass  # stdout 무효 → attach 로 진행

    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        ATTACH_PARENT_PROCESS = -1
        if not kernel32.AttachConsole(ATTACH_PARENT_PROCESS):
            kernel32.AllocConsole()
        sys.stdout = open("CONOUT$", "w", encoding="utf-8", errors="replace", buffering=1)
        sys.stderr = open("CONOUT$", "w", encoding="utf-8", errors="replace", buffering=1)
    except Exception:
        pass


if __name__ == "__main__":
    # --selftest: GUI/엔진 초기화 전 환경 검증
    if "--selftest" in sys.argv:
        _attach_console_for_cli()
        from selftest import run_selftest
        sys.exit(run_selftest())

    multiprocessing.freeze_support()
    atexit.register(_force_exit)

    from gui.app import run_gui
    run_gui()

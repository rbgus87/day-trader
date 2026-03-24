"""GUI 진입점.

Usage:
    python gui.py
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


if __name__ == "__main__":
    multiprocessing.freeze_support()
    atexit.register(_force_exit)

    from gui.app import run_gui
    run_gui()

"""PyInstaller 빌드 스크립트

실행: python build_exe.py
결과: dist/DayTrader.exe
"""

import PyInstaller.__main__
import os
import shutil
import sys

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))


def build() -> None:
    args = [
        os.path.join(PROJECT_ROOT, "gui.py"),
        "--name=DayTrader",
        "--onefile",
        "--windowed",
        "--noconfirm",
        f"--paths={PROJECT_ROOT}",
        # Data files
        f"--add-data={os.path.join(PROJECT_ROOT, 'config.yaml')};.",
        f"--add-data={os.path.join(PROJECT_ROOT, 'config', 'universe.yaml')};config",
        # Hidden imports — GUI
        "--hidden-import=gui.main_window",
        "--hidden-import=gui.widgets.dashboard_tab",
        "--hidden-import=gui.widgets.screener_tab",
        "--hidden-import=gui.widgets.backtest_tab",
        "--hidden-import=gui.widgets.strategy_tab",
        "--hidden-import=gui.widgets.log_tab",
        "--hidden-import=gui.widgets.sidebar",
        "--hidden-import=gui.workers.engine_worker",
        "--hidden-import=gui.workers.signals",
        "--hidden-import=gui.themes",
        "--hidden-import=gui.tray_icon",
        # Hidden imports — Engine
        "--hidden-import=config.settings",
        "--hidden-import=core.auth",
        "--hidden-import=core.kiwoom_rest",
        "--hidden-import=core.kiwoom_ws",
        "--hidden-import=core.order_manager",
        "--hidden-import=core.paper_order_manager",
        "--hidden-import=core.rate_limiter",
        "--hidden-import=core.retry",
        "--hidden-import=data.candle_builder",
        "--hidden-import=data.db_manager",
        "--hidden-import=strategy.base_strategy",
        "--hidden-import=strategy.orb_strategy",
        "--hidden-import=strategy.vwap_strategy",
        "--hidden-import=strategy.momentum_strategy",
        "--hidden-import=strategy.pullback_strategy",
        "--hidden-import=screener.candidate_collector",
        "--hidden-import=screener.pre_market",
        "--hidden-import=screener.strategy_selector",
        "--hidden-import=risk.risk_manager",
        "--hidden-import=notification.telegram_bot",
        "--hidden-import=backtest.backtester",
        # Hidden imports — Libraries
        "--hidden-import=PyQt6",
        "--hidden-import=PyQt6.QtCore",
        "--hidden-import=PyQt6.QtGui",
        "--hidden-import=PyQt6.QtWidgets",
        "--hidden-import=PyQt6.sip",
        "--hidden-import=apscheduler.schedulers.asyncio",
        "--hidden-import=apscheduler.triggers.cron",
        "--hidden-import=apscheduler.triggers.date",
        "--hidden-import=apscheduler.triggers.interval",
        "--hidden-import=apscheduler.jobstores.memory",
        "--hidden-import=apscheduler.executors.pool",
        "--hidden-import=apscheduler.executors.asyncio",
        "--hidden-import=pandas_ta",
        "--hidden-import=loguru",
        "--hidden-import=yaml",
        "--hidden-import=dotenv",
        "--hidden-import=aiohttp",
        "--hidden-import=websockets",
        "--hidden-import=aiosqlite",
        # Exclusions
        "--exclude-module=streamlit",
        "--exclude-module=tkinter",
        "--exclude-module=pytest",
        "--exclude-module=numba",
        "--exclude-module=IPython",
        "--exclude-module=jupyter",
        "--exclude-module=notebook",
        # Build directories
        f"--distpath={os.path.join(PROJECT_ROOT, 'dist')}",
        f"--workpath={os.path.join(PROJECT_ROOT, 'build')}",
        f"--specpath={PROJECT_ROOT}",
    ]

    print("=" * 50)
    print("DayTrader - exe 빌드 시작")
    print("=" * 50)

    PyInstaller.__main__.run(args)

    exe_path = os.path.join(PROJECT_ROOT, "dist", "DayTrader.exe")
    if os.path.exists(exe_path):
        size_mb = os.path.getsize(exe_path) / (1024 * 1024)
        print(f"\n빌드 완료: {exe_path} ({size_mb:.1f} MB)")
        root_exe = os.path.join(PROJECT_ROOT, "DayTrader.exe")
        shutil.copy2(exe_path, root_exe)
        print(f"루트에 복사: {root_exe}")
    else:
        print("\n빌드 실패!")
        sys.exit(1)


if __name__ == "__main__":
    build()

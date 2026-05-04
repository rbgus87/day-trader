"""PyInstaller 빌드 스크립트

실행: python build_exe.py
결과: dist/DayTrader.exe
"""

import os
import shutil
import subprocess
import sys

# 콘솔 cp949 환경에서도 한글 출력 안전화 (subprocess UTF-8 캡처 결과 print 시 필수)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import PyInstaller.__main__

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
        # Selftest
        "--hidden-import=selftest",
        # Hidden imports — GUI
        "--hidden-import=gui.main_window",
        "--hidden-import=gui.widgets.dashboard_tab",
        "--hidden-import=gui.widgets.screener_tab",
        "--hidden-import=gui.widgets.backtest_tab",
        "--hidden-import=gui.widgets.strategy_tab",
        "--hidden-import=gui.widgets.log_tab",
        "--hidden-import=gui.widgets.sidebar",
        "--hidden-import=gui.widgets.card",
        "--hidden-import=gui.workers.engine_worker",
        "--hidden-import=gui.workers.signals",
        "--hidden-import=gui.themes",
        "--hidden-import=gui.tray_icon",
        # Hidden imports — Engine (운영 중)
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
        "--hidden-import=strategy.momentum_strategy",
        # 5개 비활성 전략(flow/pullback/gap/open_break/big_candle)은
        # strategy/archive/ 로 이동 — hidden_import 불필요
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
        # pandas_ta(numba/llvmlite 의존) 운영 의존 제거 — core.indicators의 자체 Wilder
        # 구현(wilder_adx/wilder_atr)으로 대체. EXE 크기 ~80~100MB, 빌드 ~60~90초 절감.
        "--hidden-import=loguru",
        "--hidden-import=yaml",
        "--hidden-import=dotenv",
        "--hidden-import=aiohttp",
        "--hidden-import=requests",
        "--hidden-import=websockets",
        "--hidden-import=aiosqlite",
        # Exclusions (numba 는 절대 제외 금지)
        "--exclude-module=streamlit",
        "--exclude-module=tkinter",
        "--exclude-module=pytest",
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
    if not os.path.exists(exe_path):
        print("\n빌드 실패!")
        sys.exit(1)

    size_mb = os.path.getsize(exe_path) / (1024 * 1024)
    print(f"\n빌드 완료: {exe_path} ({size_mb:.1f} MB)")
    root_exe = os.path.join(PROJECT_ROOT, "DayTrader.exe")
    shutil.copy2(exe_path, root_exe)
    print(f"루트에 복사: {root_exe}")

    # 빌드 직후 selftest 자동 실행 — silent failure 조기 차단
    print("\n" + "=" * 50)
    print("빌드된 exe selftest 실행")
    print("=" * 50)
    # exe 내부 Python이 stdout을 UTF-8로 열도록 강제 (cp949 파이프 인코딩 방지)
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        result = subprocess.run(
            [exe_path, "--selftest"],
            capture_output=True,
            text=True,
            timeout=30,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
    except subprocess.TimeoutExpired:
        print("*** selftest 30초 타임아웃 — 운영 투입 금지 ***")
        sys.exit(1)

    print(result.stdout)
    if result.returncode != 0:
        if result.stderr:
            print("STDERR:")
            print(result.stderr)
        print("\n*** 빌드된 exe selftest FAIL ***")
        print("운영 투입 금지. 빌드 옵션 재검토 필요.")
        sys.exit(1)
    print("*** 빌드 + selftest 모두 통과 ***")


if __name__ == "__main__":
    build()

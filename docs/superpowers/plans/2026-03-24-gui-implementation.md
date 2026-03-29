# Day Trader GUI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 키움증권 REST API 기반 단타 자동매매 시스템의 PyQt6 Windows GUI 애플리케이션 구현

**Architecture:** PyQt6 + QThread에서 asyncio 이벤트 루프 실행 (스윙 트레이더 검증 패턴). 좌측 사이드바(제어) + 우측 5탭(Dashboard, Screener, Backtest, Strategy, Log) + 하단 상태바. 기존 main.py의 파이프라인을 EngineWorker(QThread) 내에서 그대로 실행.

**Tech Stack:** Python 3.14, PyQt6, asyncio, loguru, PyInstaller

**Spec:** `docs/superpowers/specs/2026-03-24-gui-design.md`

**Reference:** `D:/project/swing-trader/src/gui/` (스윙 트레이더 GUI 패턴)

---

## File Structure

```
gui/
├── app.py                    # QApplication 생성 + run_gui()
├── main_window.py            # MainWindow: 사이드바 + 탭 + 상태바 + 로그 sink
├── themes.py                 # Catppuccin Mocha dark_theme() → QSS 문자열
├── tray_icon.py              # TrayIcon: 시스템 트레이 + 컨텍스트 메뉴
├── workers/
│   ├── __init__.py
│   ├── signals.py            # EngineSignals: 모든 Worker↔UI Qt signal 정의
│   └── engine_worker.py      # EngineWorker(QThread): asyncio 루프 + 폴링
├── widgets/
│   ├── __init__.py
│   ├── sidebar.py            # Sidebar: 모드/상태/제어/연결 패널
│   ├── dashboard_tab.py      # DashboardTab: 요약바 + 포지션 + 매매내역
│   ├── screener_tab.py       # ScreenerTab: 후보 테이블 + 필터
│   ├── backtest_tab.py       # BacktestTab: 파라미터 + 실행 + 결과
│   ├── strategy_tab.py       # StrategyTab: 전략 파라미터 + 리스크 설정
│   └── log_tab.py            # LogTab: 로그 뷰어 + 레벨 필터

gui.py                        # Root entry: multiprocessing.freeze_support() + run_gui()
build_exe.py                  # PyInstaller 빌드 스크립트
```

---

### Task 1: PyQt6 의존성 추가 + GUI 디렉토리 구조 생성

**Files:**
- Create: `gui/__init__.py`
- Create: `gui/workers/__init__.py`
- Create: `gui/widgets/__init__.py`

- [ ] **Step 1: PyQt6 설치**

```bash
pip install PyQt6
```

- [ ] **Step 2: GUI 디렉토리 구조 생성**

```bash
mkdir -p gui/workers gui/widgets
touch gui/__init__.py gui/workers/__init__.py gui/widgets/__init__.py
```

- [ ] **Step 3: 커밋**

```bash
git add gui/
git commit -m "chore: add gui directory structure and PyQt6 dependency"
```

---

### Task 2: Qt Signals 정의

**Files:**
- Create: `gui/workers/signals.py`

day-trader에 맞게 신호를 정의한다. 스윙 트레이더의 `src/gui/workers/signals.py` 패턴을 따르되, 단타 특화 시그널(screener_updated, backtest_progress 등)을 추가한다.

- [ ] **Step 1: signals.py 작성**

```python
"""중앙 시그널 정의 — 모든 Worker ↔ UI 통신은 여기서 정의."""

from PyQt6.QtCore import QObject, pyqtSignal


class EngineSignals(QObject):
    """엔진-UI 간 시그널 모음.

    Worker → UI (상태 전달):
        started: 엔진 시작 완료.
        stopped: 엔진 중지 완료.
        error: 엔진 오류 (str).
        status_updated: 엔진 상태 dict.
        position_updated: 포지션 dict (단일 포지션 업데이트).
        positions_updated: 전체 포지션 list[dict].
        trade_executed: 체결 dict (단일 매매).
        trades_updated: 당일 체결 list[dict].
        pnl_updated: 일일 손익 float.
        candidates_updated: 스크리너 후보 list[dict].
        log_message: (level: str, message: str).

    UI → Worker (제어 명령):
        request_stop: 정상 종료.
        request_halt: 긴급 정지.
        request_screening: 수동 스크리닝.
        request_force_close: 전체 포지션 강제 청산.
        request_report: 일일 리포트 발송.
        request_reconnect: WS 재연결.
        request_daily_reset: 일일 리셋.
    """

    # Worker → UI
    started = pyqtSignal()
    stopped = pyqtSignal()
    error = pyqtSignal(str)
    status_updated = pyqtSignal(dict)
    position_updated = pyqtSignal(dict)
    positions_updated = pyqtSignal(list)
    trade_executed = pyqtSignal(dict)
    trades_updated = pyqtSignal(list)
    pnl_updated = pyqtSignal(float)
    candidates_updated = pyqtSignal(list)
    log_message = pyqtSignal(str, str)

    # UI → Worker
    request_stop = pyqtSignal()
    request_halt = pyqtSignal()
    request_screening = pyqtSignal()
    request_force_close = pyqtSignal()
    request_report = pyqtSignal()
    request_reconnect = pyqtSignal()
    request_daily_reset = pyqtSignal()
```

- [ ] **Step 2: 커밋**

```bash
git add gui/workers/signals.py
git commit -m "feat(gui): add EngineSignals for Worker↔UI communication"
```

---

### Task 3: 테마 시스템

**Files:**
- Create: `gui/themes.py`

Catppuccin Mocha 다크 테마를 QSS 문자열로 정의한다. 스윙 트레이더의 theme.qss 파일 방식 대신, 퀀트 시스템처럼 Python 함수에서 QSS를 반환하는 방식을 사용한다 (PyInstaller 경로 문제 방지).

- [ ] **Step 1: themes.py 작성**

```python
"""Catppuccin Mocha 다크 테마 — QSS 문자열 반환."""


# Catppuccin Mocha 색상 팔레트
COLORS = {
    "base": "#1e1e2e",
    "mantle": "#181825",
    "crust": "#11111b",
    "surface0": "#313244",
    "surface1": "#45475a",
    "surface2": "#585b70",
    "overlay0": "#6c7086",
    "overlay1": "#7f849c",
    "text": "#cdd6f4",
    "subtext0": "#a6adc8",
    "subtext1": "#bac2de",
    "green": "#a6e3a1",
    "red": "#f38ba8",
    "yellow": "#f9e2af",
    "blue": "#89b4fa",
    "mauve": "#cba6f7",
    "peach": "#fab387",
    "teal": "#94e2d5",
    "sky": "#89dceb",
    "lavender": "#b4befe",
}


def dark_theme() -> str:
    """Catppuccin Mocha QSS 반환."""
    c = COLORS
    return f"""
    /* ── 전역 ── */
    QMainWindow, QWidget {{
        background-color: {c['base']};
        color: {c['text']};
        font-family: "Segoe UI", "맑은 고딕", sans-serif;
        font-size: 13px;
    }}

    /* ── 사이드바 ── */
    QFrame#sidebar {{
        background-color: {c['mantle']};
        border-right: 1px solid {c['surface0']};
    }}

    /* ── 탭 ── */
    QTabWidget::pane {{
        border: none;
        background-color: {c['base']};
    }}
    QTabBar::tab {{
        background-color: {c['mantle']};
        color: {c['overlay0']};
        padding: 10px 20px;
        border: none;
        border-bottom: 2px solid transparent;
    }}
    QTabBar::tab:selected {{
        background-color: {c['base']};
        color: {c['mauve']};
        border-bottom: 2px solid {c['mauve']};
    }}
    QTabBar::tab:hover {{
        color: {c['text']};
    }}

    /* ── 버튼 ── */
    QPushButton {{
        background-color: {c['surface0']};
        color: {c['text']};
        border: none;
        border-radius: 4px;
        padding: 6px 12px;
        font-size: 12px;
    }}
    QPushButton:hover {{
        background-color: {c['surface1']};
    }}
    QPushButton:disabled {{
        color: {c['overlay0']};
        background-color: {c['surface0']};
    }}
    QPushButton#startBtn {{
        background-color: {c['green']};
        color: {c['crust']};
        font-weight: bold;
    }}
    QPushButton#startBtn:hover {{
        background-color: #b5eeb0;
    }}
    QPushButton#stopBtn {{
        background-color: {c['red']};
        color: {c['crust']};
        font-weight: bold;
    }}
    QPushButton#stopBtn:hover {{
        background-color: #f5a0b6;
    }}
    QPushButton#haltBtn {{
        background-color: {c['peach']};
        color: {c['crust']};
        font-weight: bold;
    }}
    QPushButton#haltBtn:hover {{
        background-color: #fcc4a0;
    }}

    /* ── 테이블 ── */
    QTableWidget {{
        background-color: {c['surface0']};
        alternate-background-color: {c['mantle']};
        border: none;
        border-radius: 6px;
        gridline-color: {c['surface1']};
        selection-background-color: {c['surface1']};
    }}
    QTableWidget::item {{
        padding: 4px 8px;
    }}
    QHeaderView::section {{
        background-color: {c['mantle']};
        color: {c['overlay0']};
        border: none;
        border-bottom: 1px solid {c['surface0']};
        padding: 4px 8px;
        font-size: 11px;
        font-weight: bold;
    }}

    /* ── 콤보박스 ── */
    QComboBox {{
        background-color: {c['surface0']};
        color: {c['text']};
        border: 1px solid {c['surface1']};
        border-radius: 4px;
        padding: 4px 8px;
    }}
    QComboBox::drop-down {{
        border: none;
    }}
    QComboBox QAbstractItemView {{
        background-color: {c['surface0']};
        color: {c['text']};
        selection-background-color: {c['surface1']};
    }}

    /* ── 입력 ── */
    QLineEdit, QSpinBox, QDoubleSpinBox, QDateEdit {{
        background-color: {c['surface0']};
        color: {c['text']};
        border: 1px solid {c['surface1']};
        border-radius: 4px;
        padding: 4px 8px;
    }}

    /* ── 스크롤바 ── */
    QScrollBar:vertical {{
        background-color: {c['mantle']};
        width: 8px;
        border: none;
    }}
    QScrollBar::handle:vertical {{
        background-color: {c['surface1']};
        border-radius: 4px;
        min-height: 20px;
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0;
    }}

    /* ── 상태바 ── */
    QStatusBar {{
        background-color: {c['mantle']};
        border-top: 1px solid {c['surface0']};
        color: {c['overlay0']};
        font-size: 11px;
    }}

    /* ── 프로그레스바 ── */
    QProgressBar {{
        background-color: {c['surface0']};
        border: none;
        border-radius: 4px;
        text-align: center;
        color: {c['text']};
    }}
    QProgressBar::chunk {{
        background-color: {c['mauve']};
        border-radius: 4px;
    }}

    /* ── 로그 ── */
    QPlainTextEdit {{
        background-color: {c['mantle']};
        color: {c['text']};
        border: none;
        border-radius: 6px;
        font-family: "Cascadia Code", "Consolas", monospace;
        font-size: 12px;
    }}

    /* ── 체크박스 ── */
    QCheckBox {{
        color: {c['text']};
        spacing: 6px;
    }}

    /* ── 그룹박스 ── */
    QGroupBox {{
        border: 1px solid {c['surface0']};
        border-radius: 6px;
        margin-top: 8px;
        padding-top: 16px;
        font-weight: bold;
        color: {c['subtext0']};
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        left: 12px;
        padding: 0 4px;
    }}

    /* ── 스플리터 ── */
    QSplitter::handle {{
        background-color: {c['surface0']};
    }}
    QSplitter::handle:vertical {{
        height: 2px;
    }}
    """
```

- [ ] **Step 2: 커밋**

```bash
git add gui/themes.py
git commit -m "feat(gui): add Catppuccin Mocha dark theme"
```

---

### Task 4: Sidebar 위젯

**Files:**
- Create: `gui/widgets/sidebar.py`

스윙 트레이더의 `main_window.py:62-218` 사이드바 로직을 분리된 위젯으로 추출한다. 단타 특화 요소(활성 전략, 대상 종목, REST/WS 이중 연결 상태)를 추가.

- [ ] **Step 1: sidebar.py 작성**

사이드바 위젯 구현. 구성:
- AppTitle + Version
- ModeSelector (PAPER/LIVE 토글, LIVE 전환 시 확인 다이얼로그)
- EngineStatus (Running/Stopped/Error, 활성 전략명, 대상 종목)
- ControlButtons (Start/Stop/Halt)
- ManualActions (Screening, Force Close, Report, Reconnect)
- ConnectionStatus (REST dot + WS dot)

공개 메서드:
- `get_mode() → str`: 현재 선택된 모드
- `set_engine_running(running: bool)`: 버튼 상태 전환
- `update_status(status: dict)`: 엔진 상태 반영
- `update_connection(rest_ok: bool, ws_ok: bool)`: 연결 상태 반영

시그널:
- `start_clicked`, `stop_clicked`, `halt_clicked`
- `screening_clicked`, `force_close_clicked`, `report_clicked`, `reconnect_clicked`

구현 참고: `D:/project/swing-trader/src/gui/main_window.py:62-218` 사이드바 패턴 (QFrame, fixedWidth 220, 섹션 레이블, QHBoxLayout 버튼 행, 연결 dot indicator)

- [ ] **Step 2: 커밋**

```bash
git add gui/widgets/sidebar.py
git commit -m "feat(gui): add Sidebar widget with mode/status/controls"
```

---

### Task 5: Dashboard Tab

**Files:**
- Create: `gui/widgets/dashboard_tab.py`

스윙 트레이더의 `dashboard_tab.py` 패턴을 참고하여 단타 특화 대시보드를 구현한다.

- [ ] **Step 1: dashboard_tab.py 작성**

대시보드 탭 구현. 구성:
- **SummaryBar** (QHBoxLayout, 4개 카드):
  - 일일 P&L (금액 + %, green/red 색상)
  - 매매수 (completed/max, Win/Loss 카운트)
  - 승률 (%, 평균과 비교)
  - 리스크 상태 (Normal/Warning/Halted, 현재 DD%)

- **PositionsTable** (QTableWidget):
  - 컬럼: Ticker, 종목명, 전략, 진입가, 현재가, P&L%, SL, TP1, 상태
  - 색상: P&L 양수 green(#a6e3a1), 음수 red(#f38ba8)
  - alternatingRowColors 활성화

- **TodayTradesTable** (QTableWidget):
  - 컬럼: 시간, Ticker, Side, 가격, 수량, P&L, 사유
  - Side: BUY blue(#89b4fa), SELL red(#f38ba8)

레이아웃: QVBoxLayout → SummaryBar → QSplitter(PositionsTable, TodayTradesTable)

공개 메서드:
- `update_summary(pnl, trades_count, max_trades, wins, losses, win_rate, risk_status, dd_pct)`
- `update_positions(positions: list[dict])`
- `update_trades(trades: list[dict])`

각 summary 카드는 QFrame + QVBoxLayout (title label + value label + subtitle label).

- [ ] **Step 2: 커밋**

```bash
git add gui/widgets/dashboard_tab.py
git commit -m "feat(gui): add DashboardTab with summary/positions/trades"
```

---

### Task 6: Log Tab

**Files:**
- Create: `gui/widgets/log_tab.py`

- [ ] **Step 1: log_tab.py 작성**

로그 뷰어 탭. 구성:
- **LogViewer** (QPlainTextEdit, readOnly)
  - 최대 라인: 5000 (초과 시 상단 제거)
  - monospace 폰트
  - 로그 레벨별 색상:
    - DEBUG: overlay0(#6c7086)
    - INFO: text(#cdd6f4)
    - WARNING: yellow(#f9e2af)
    - ERROR/CRITICAL: red(#f38ba8)

- **Toolbar** (QHBoxLayout):
  - 레벨 필터 체크박스 (DEBUG, INFO, WARNING, ERROR)
  - Auto-scroll 토글 체크박스 (기본 ON)
  - Clear 버튼

공개 메서드:
- `append_log(text: str, level: str)`: 로그 추가 (레벨별 색상, 필터 적용)
- `clear()`: 로그 클리어

구현: QPlainTextEdit에 appendHtml()로 색상 적용. auto_scroll이 True이면 moveCursor(QTextCursor.End).

- [ ] **Step 2: 커밋**

```bash
git add gui/widgets/log_tab.py
git commit -m "feat(gui): add LogTab with level filter and auto-scroll"
```

---

### Task 7: Screener Tab

**Files:**
- Create: `gui/widgets/screener_tab.py`

- [ ] **Step 1: screener_tab.py 작성**

스크리너 탭. 구성:
- **FilterPanel** (QGroupBox):
  - 최소 시가총액 (QSpinBox, 억원 단위, 기본 3000)
  - 최소 거래대금 (QSpinBox, 억원 단위, 기본 50)
  - ATR 하한 (QDoubleSpinBox, %, 기본 2.0)
  - 거래량 서지 비율 (QDoubleSpinBox, 배, 기본 1.5)

- **CandidatesTable** (QTableWidget):
  - 컬럼: Rank, Ticker, 종목명, 시가총액, 거래대금, 서지비율, ATR%, MA20추세, 점수
  - 선정 종목 하이라이트 (mauve 배경)

- **ActionBar** (QHBoxLayout):
  - "스크리닝 실행" 버튼 (QPushButton)
  - 마지막 실행 시간 라벨
  - 자동 스크리닝 시간(08:30) 표시 라벨

공개 메서드:
- `update_candidates(candidates: list[dict])`: 테이블 갱신
- `get_filter_values() → dict`: 현재 필터 설정 반환

시그널:
- `run_screening_clicked`: 수동 스크리닝 요청

- [ ] **Step 2: 커밋**

```bash
git add gui/widgets/screener_tab.py
git commit -m "feat(gui): add ScreenerTab with filter panel and candidates table"
```

---

### Task 8: Backtest Tab

**Files:**
- Create: `gui/widgets/backtest_tab.py`

- [ ] **Step 1: backtest_tab.py 작성**

백테스트 탭. 구성:
- **ParameterPanel** (QGroupBox, QFormLayout):
  - 전략 선택 (QComboBox: ORB, VWAP, Momentum, Pullback)
  - 시작일/종료일 (QDateEdit × 2)
  - 종목 선택 (QComboBox, universe.yaml 기반 종목 목록)

- **ActionBar**:
  - "백테스트 실행" 버튼
  - QProgressBar (0-100%)

- **ResultsPanel** (QGroupBox):
  - KPI 요약 (QFormLayout):
    - Total Trades, Win Rate, Sharpe Ratio, Max Drawdown, Profit Factor, Total Return
  - 매매 내역 테이블 (QTableWidget):
    - 컬럼: 날짜, 시간, Side, 가격, 수량, P&L, 누적P&L

BacktestWorker(QThread): 백테스트 실행을 별도 스레드에서 처리.
- `run()`: backtester.run() 호출
- 시그널: `progress(int)`, `finished(dict)`, `error(str)`

공개 메서드:
- `set_tickers(tickers: list[str])`: universe.yaml 기반 종목 목록 설정
- `show_results(kpi: dict, trades: list[dict])`: 결과 표시

- [ ] **Step 2: 커밋**

```bash
git add gui/widgets/backtest_tab.py
git commit -m "feat(gui): add BacktestTab with parameter form and results view"
```

---

### Task 9: Strategy Tab

**Files:**
- Create: `gui/widgets/strategy_tab.py`

- [ ] **Step 1: strategy_tab.py 작성**

전략 설정 탭. 구성:
- **StrategySelector** (QComboBox): ORB, VWAP, Momentum, Pullback

- **ParameterEditor** (QGroupBox, QFormLayout — 전략별 동적 변경):
  - ORB: range_start(QTimeEdit), range_end(QTimeEdit), min_range_pct(QDoubleSpinBox), volume_ratio(QDoubleSpinBox)
  - VWAP: rsi_low(QDoubleSpinBox), rsi_high(QDoubleSpinBox)
  - Momentum: momentum_volume_ratio(QDoubleSpinBox)
  - Pullback: min_gain_pct(QDoubleSpinBox)

- **RiskSettings** (QGroupBox, QFormLayout):
  - stop_loss_pct (QDoubleSpinBox, 기본 1.5%)
  - tp1_pct (QDoubleSpinBox, 기본 2.0%)
  - max_daily_loss_pct (QDoubleSpinBox, 기본 2.0%)
  - max_trades_per_day (QSpinBox, 기본 5)
  - cooldown_minutes (QSpinBox, 기본 10)
  - first_leg_ratio (QDoubleSpinBox, 기본 0.55)

- **UniverseEditor** (QGroupBox):
  - QListWidget (종목 목록)
  - Add/Remove 버튼 + 종목코드 QLineEdit
  - universe.yaml 로드/저장

- **SaveButton**: 설정 저장 (config.yaml + universe.yaml)

구현: 전략 선택 변경 시 ParameterEditor의 위젯을 동적으로 교체 (QStackedWidget 또는 clearLayout + rebuild).

값 로드: `AppConfig.from_yaml()` → 각 필드 매핑.
값 저장: YAML 파일 직접 수정 (yaml.safe_load → 값 변경 → yaml.dump).

- [ ] **Step 2: 커밋**

```bash
git add gui/widgets/strategy_tab.py
git commit -m "feat(gui): add StrategyTab with parameter editor and risk settings"
```

---

### Task 10: Engine Worker (QThread + asyncio)

**Files:**
- Create: `gui/workers/engine_worker.py`

핵심 컴포넌트. 스윙 트레이더의 `engine_worker.py` 패턴을 따르되, day-trader의 `main.py` 파이프라인(tick→candle→strategy→order)을 QThread 내에서 실행한다.

- [ ] **Step 1: engine_worker.py 작성**

```python
"""TradingEngine을 별도 스레드에서 asyncio로 실행하는 QThread 래퍼.

main.py의 파이프라인 로직을 QThread 내에서 실행.
모든 cross-thread 호출은 Qt signal 또는 asyncio.run_coroutine_threadsafe로 처리.
"""

import asyncio
import sys
from datetime import datetime

from PyQt6.QtCore import QThread
from loguru import logger

from gui.workers.signals import EngineSignals
```

핵심 구현:
- `__init__(self, mode: str = "paper")`: EngineSignals 생성, UI→Worker 시그널 연결
- `run()`: asyncio.new_event_loop() 생성 → `_run_engine()` 실행 → finally에서 정리

`_run_engine()` async 메서드 — main.py의 main() 로직을 거의 그대로 포팅:
1. `AppConfig.from_yaml()` 로드
2. `DbManager`, `TelegramNotifier`, `TokenManager`, `KiwoomRestClient` 초기화
3. asyncio.Queue 4개 생성 (tick, candle, signal, order)
4. `KiwoomWebSocketClient`, `CandleBuilder`, `RiskManager`, `OrderManager` 초기화
5. `CandidateCollector`, `PreMarketScreener`, `StrategySelector` 초기화
6. `AsyncIOScheduler` — 08:30 스크리닝, 15:10 강제청산 등록
7. 파이프라인 4개 태스크 실행 (tick_consumer, candle_consumer, signal_consumer, order_confirmation_consumer)
8. `self.signals.started.emit()` 후 **2초 간격 폴링 루프** 시작:
   - `_emit_status()`: 엔진 상태 dict
   - `_emit_positions()`: risk_manager._positions
   - `_emit_trades()`: db에서 당일 매매 조회
   - `_emit_pnl()`: risk_manager._daily_pnl
   - `_emit_candidates()`: screener 결과

UI→Worker 명령 핸들러 (각각 `asyncio.run_coroutine_threadsafe` 사용):
- `_on_request_stop()`: 모든 태스크 취소 → scheduler.shutdown → ws.disconnect → rest.aclose → db.close
- `_on_request_halt()`: risk_manager 일시정지
- `_on_request_screening()`: run_screening() 호출
- `_on_request_force_close()`: force_close() 호출
- `_on_request_report()`: 텔레그램 일일리포트 발송
- `_on_request_reconnect()`: ws_client.disconnect() → ws_client.connect()
- `_on_request_daily_reset()`: risk_manager.reset_daily(), candle_builder.reset()

**중요:** `main.py`와 `engine_worker.py`는 같은 파이프라인 로직을 공유해야 한다.
main.py에서 파이프라인 초기화 로직을 함수로 추출하는 것이 아니라, engine_worker.py에서 main.py의 로직을 직접 구현한다 (스윙 트레이더 패턴과 동일).
main.py는 CLI 독립 실행용으로 그대로 유지한다.

- [ ] **Step 2: 커밋**

```bash
git add gui/workers/engine_worker.py
git commit -m "feat(gui): add EngineWorker with asyncio pipeline in QThread"
```

---

### Task 11: Main Window

**Files:**
- Create: `gui/main_window.py`

스윙 트레이더의 `main_window.py` 패턴을 참고. Sidebar + QTabWidget(5탭) + StatusBar 조합.

- [ ] **Step 1: main_window.py 작성**

MainWindow(QMainWindow) 구현:

`__init__()`:
- setWindowTitle("DayTrader")
- setMinimumSize(1100, 720), resize(1280, 800)
- `_init_ui()`, `_apply_theme()`, `_apply_dark_titlebar()`, `_setup_loguru_sink()`, `_setup_refresh_timer()`

`_init_ui()`:
- central widget + QHBoxLayout (margins=0, spacing=0)
- 좌측: Sidebar 위젯 추가
- 우측: QVBoxLayout
  - QTabWidget (5탭): DashboardTab, ScreenerTab, BacktestTab, StrategyTab, LogTab
  - QStatusBar: 좌측(모드/엔진/전략/종목), 우측(시계 KST)

시그널 연결:
- Sidebar → MainWindow → EngineWorker
  - sidebar.start_clicked → _on_start()
  - sidebar.stop_clicked → _on_stop()
  - sidebar.halt_clicked → _on_halt()
  - sidebar.screening_clicked → _on_screening()
  - sidebar.force_close_clicked → _on_force_close()
  - sidebar.report_clicked → _on_report()
  - sidebar.reconnect_clicked → _on_reconnect()

- EngineWorker.signals → MainWindow → Tabs
  - started → _on_engine_started()
  - stopped → _on_engine_stopped()
  - error → _on_engine_error()
  - status_updated → sidebar.update_status() + dashboard.update_summary()
  - positions_updated → dashboard.update_positions()
  - trades_updated → dashboard.update_trades()
  - candidates_updated → screener.update_candidates()

`_apply_theme()`: `self.setStyleSheet(dark_theme())`

`_apply_dark_titlebar()`: Windows DWM API 호출 (스윙 트레이더 패턴)

`_setup_loguru_sink()`: loguru 커스텀 sink → log_signal → LogTab.append_log() + DashboardTab 하단 로그 (선택적)

`_setup_refresh_timer()`: QTimer 1초 간격 → 상태바 시계 업데이트

`_on_start()`:
- mode = sidebar.get_mode()
- LIVE 모드: QMessageBox 확인
- EngineWorker(mode) 생성 → 시그널 연결 → start()

`closeEvent()`:
- 엔진 실행 중: 트레이로 최소화
- 엔진 미실행: _cleanup_and_quit()

`_cleanup_and_quit()`:
- 타이머 중지
- loguru sink 제거
- EngineWorker 정리 (request_stop → wait(5000) → terminate)
- 트레이 숨김
- QApplication.quit()

- [ ] **Step 2: 커밋**

```bash
git add gui/main_window.py
git commit -m "feat(gui): add MainWindow with sidebar + 5 tabs + statusbar"
```

---

### Task 12: System Tray

**Files:**
- Create: `gui/tray_icon.py`

- [ ] **Step 1: tray_icon.py 작성**

TrayIcon 클래스 구현 (스윙 트레이더 `main_window.py:521-601` 패턴 추출):
- `__init__(self, parent: QMainWindow)`: QSystemTrayIcon 설정
- `_make_icon() → QIcon`: "DT" 텍스트가 그려진 아이콘 생성 (QPainter, blue(#89b4fa) 배경 원 + crust(#11111b) 텍스트)
- 컨텍스트 메뉴: "열기", 구분선, "엔진 중지", "종료"
- 더블클릭 → 창 복원
- `show_minimized_message()`: "엔진이 구동 중입니다. 트레이에서 실행됩니다."

- [ ] **Step 2: 커밋**

```bash
git add gui/tray_icon.py
git commit -m "feat(gui): add TrayIcon with context menu"
```

---

### Task 13: App Entry Point

**Files:**
- Create: `gui/app.py`
- Create: `gui.py` (root entry)

- [ ] **Step 1: app.py 작성**

```python
"""GUI 애플리케이션 진입점."""

import sys

from PyQt6.QtWidgets import QApplication

from gui.main_window import MainWindow


def run_gui():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    window = MainWindow()
    app.aboutToQuit.connect(window._cleanup_and_quit)

    window.show()
    sys.exit(app.exec())
```

- [ ] **Step 2: gui.py (root entry) 작성**

```python
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
```

- [ ] **Step 3: 커밋**

```bash
git add gui/app.py gui.py
git commit -m "feat(gui): add app entry point and root gui.py"
```

---

### Task 14: PyInstaller 빌드 스크립트

**Files:**
- Create: `build_exe.py`

- [ ] **Step 1: build_exe.py 작성**

스윙 트레이더의 `build_exe.py` 패턴을 따르되 PyQt6 + day-trader 모듈로 변경:

```python
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
        # 데이터 파일
        f"--add-data={os.path.join(PROJECT_ROOT, 'config.yaml')};.",
        f"--add-data={os.path.join(PROJECT_ROOT, 'config', 'universe.yaml')};config",
        # 히든 임포트 — GUI
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
        # 히든 임포트 — 엔진
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
        # 히든 임포트 — 라이브러리
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
        # 제외
        "--exclude-module=streamlit",
        "--exclude-module=tkinter",
        "--exclude-module=pytest",
        "--exclude-module=numba",
        "--exclude-module=IPython",
        "--exclude-module=jupyter",
        "--exclude-module=notebook",
        # 빌드 디렉토리
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
```

- [ ] **Step 2: 커밋**

```bash
git add build_exe.py
git commit -m "feat(gui): add PyInstaller build script for DayTrader.exe"
```

---

### Task 15: 통합 테스트 + 최종 검증

- [ ] **Step 1: GUI 실행 테스트**

```bash
python gui.py
```

확인 사항:
- 윈도우 정상 표시 (1280x800, 다크 테마)
- 5탭 전환 정상
- 사이드바 버튼 클릭 정상
- LIVE 모드 전환 시 경고 다이얼로그
- 시스템 트레이 최소화/복원
- 엔진 시작/중지 정상 (PAPER 모드)
- 상태바 시계 업데이트

- [ ] **Step 2: .gitignore 업데이트**

`.superpowers/` 및 빌드 산출물 추가:

```
# GUI build
build/
dist/
*.spec
DayTrader.exe

# Superpowers brainstorm
.superpowers/
```

- [ ] **Step 3: 최종 커밋**

```bash
git add .gitignore
git commit -m "chore: update .gitignore for GUI build artifacts"
```

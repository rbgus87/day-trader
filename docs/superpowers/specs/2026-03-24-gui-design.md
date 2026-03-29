# Day Trader GUI Design Spec

## Overview

키움증권 REST API 기반 단타 자동매매 시스템의 Windows GUI 애플리케이션.
기존 CLI 데몬(main.py)의 매매 엔진을 GUI 내부에서 직접 실행하는 인프로세스 방식.

## Tech Stack

| 항목 | 선택 | 근거 |
|------|------|------|
| GUI Framework | PyQt6 | 퀀트 시스템과 동일 버전, Python 3.14 호환 |
| Engine Integration | QThread + asyncio | 스윙 트레이더 검증 패턴, 실시간 틱 지연 최소화 |
| Theme | Catppuccin Mocha (dark) | 스윙 트레이더와 통일 |
| Packaging | PyInstaller (onefile) | DayTrader.exe, Windows 전용 |
| System Tray | QSystemTrayIcon | 엔진 실행 중 최소화 |

## Architecture

```
gui.py (entry point)
  └── MainWindow (QMainWindow, 1280x800)
        ├── Sidebar (220px, fixed)
        │     ├── AppTitle + Version
        │     ├── ModeSelector (PAPER/LIVE)
        │     ├── EngineStatus (상태, 전략, 대상종목)
        │     ├── ControlButtons (Start/Stop/Halt)
        │     ├── ManualActions (Screening/ForceClose/Report/Reconnect)
        │     └── ConnectionStatus (REST + WS)
        │
        ├── ContentArea (QTabWidget, 5 tabs)
        │     ├── DashboardTab
        │     │     ├── SummaryBar (일일 P&L, 매매수, 승률, 리스크 상태)
        │     │     ├── PositionsTable (실시간 포지션)
        │     │     └── TodayTradesTable (당일 매매 내역)
        │     │
        │     ├── ScreenerTab
        │     │     ├── CandidatesTable (스크리닝 결과)
        │     │     ├── FilterSettings (시가총액, 거래대금, ATR 등)
        │     │     └── RunScreeningButton (수동 실행)
        │     │
        │     ├── BacktestTab
        │     │     ├── ParameterForm (전략, 기간, 종목)
        │     │     ├── RunButton + ProgressBar
        │     │     └── ResultsView (KPI 요약 + 매매 내역)
        │     │
        │     ├── StrategyTab
        │     │     ├── StrategySelector (ORB/VWAP/Momentum/Pullback)
        │     │     ├── ParameterEditor (전략별 파라미터 편집)
        │     │     ├── RiskSettings (손절, 익절, 일일한도)
        │     │     └── UniverseEditor (universe.yaml 편집)
        │     │
        │     └── LogTab
        │           ├── LogViewer (실시간 로그, 레벨별 필터)
        │           └── AutoScrollToggle
        │
        └── StatusBar
              ├── Mode + Engine + Strategy + Target (left)
              └── Clock KST (right)
```

## Engine Integration (QThread + asyncio)

```
MainWindow
    │
    ├── EngineWorker (QThread)
    │     └── asyncio.run() — 기존 main.py 파이프라인 실행
    │           ├── tick_consumer (WS → CandleBuilder)
    │           ├── candle_consumer (CandleBuilder → Strategy)
    │           ├── signal_consumer (Strategy → OrderManager)
    │           └── order_confirmation_consumer (WS → 체결확인)
    │
    └── Qt Signals (thread-safe communication)
          ├── Worker → UI
          │     ├── engine_started / engine_stopped / engine_error
          │     ├── position_updated(dict)
          │     ├── trade_executed(dict)
          │     ├── pnl_updated(float)
          │     ├── status_changed(str)
          │     ├── candidates_updated(list)
          │     └── log_message(str, str)  # level, message
          │
          └── UI → Worker
                ├── request_stop / request_halt
                ├── request_screening
                ├── request_force_close
                ├── request_reconnect
                └── request_daily_reset
```

## Tab Details

### 1. Dashboard Tab
- **SummaryBar**: 4개 카드 — 일일 P&L(금액+%), 매매수(W/L), 승률, 리스크 상태(DD%)
- **PositionsTable**: Ticker, 종목명, 전략, 진입가, 현재가, P&L%, SL, TP1, 상태
- **TodayTradesTable**: 시간, Ticker, Side(BUY/SELL), 가격, 수량, P&L, 사유
- 색상: 수익 #a6e3a1(green), 손실 #f38ba8(red), 경고 #f9e2af(yellow)

### 2. Screener Tab
- 스크리닝 결과 테이블: Ticker, 종목명, 시가총액, 거래대금, 거래량서지, ATR%, MA20추세, 점수
- 필터 패널: 최소 시가총액, 최소 거래대금, ATR 하한, 거래량서지 비율
- 수동 스크리닝 실행 버튼 + 자동 스크리닝 시간(08:30) 표시
- 선정된 종목 하이라이트

### 3. Backtest Tab
- 전략 선택 (ORB/VWAP/Momentum/Pullback)
- 기간 선택 (DateEdit × 2)
- 종목 선택 (universe.yaml 기반)
- 실행 버튼 + 프로그레스바
- 결과: total_trades, win_rate, sharpe, max_drawdown, profit_factor, total_return
- 매매 내역 테이블 (시간, 방향, 가격, P&L)

### 4. Strategy Tab
- 전략 선택 콤보박스
- 전략별 파라미터 편집 폼 (config.yaml의 strategy 섹션)
  - ORB: range_start, range_end, min_range_pct, volume_ratio
  - VWAP: rsi_low, rsi_high
  - Momentum: momentum_volume_ratio
  - Pullback: min_gain_pct
- 공통 리스크 설정: stop_loss_pct, tp1_pct, max_daily_loss_pct, max_trades_per_day
- universe.yaml 편집기 (종목 추가/제거)
- Save 버튼 → config.yaml / universe.yaml 저장

### 5. Log Tab
- QPlainTextEdit 기반 실시간 로그 뷰어
- 로그 레벨 필터 (DEBUG, INFO, WARNING, ERROR)
- Auto-scroll 토글
- Clear 버튼
- Loguru의 custom sink로 GUI에 로그 전달

## Sidebar Details

### Mode Selector
- PAPER / LIVE 토글 (QButtonGroup)
- LIVE 전환 시 확인 다이얼로그 ("실제 매매가 실행됩니다. 계속하시겠습니까?")
- 모드별 색상 뱃지: PAPER(green), LIVE(red)

### Engine Status
- 상태: Stopped / Starting / Running / Stopping / Error
- 현재 활성 전략명
- 현재 대상 종목
- 2초 간격 폴링으로 업데이트

### Control Buttons
- Start: 엔진 시작 (EngineWorker.start())
- Stop: 정상 종료 (포지션 청산 후 종료)
- Halt: 긴급 정지 (즉시 중단, 확인 다이얼로그)

### Manual Actions
- Screening: 수동 스크리닝 실행
- Force Close: 전체 포지션 강제 청산
- Daily Report: 일일 리포트 Telegram 발송
- WS Reconnect: WebSocket 재연결

### Connection Status
- REST API 연결 상태 (dot indicator)
- WebSocket 연결 상태 (dot indicator)
- 연결 끊김 시 빨간색 + 자동 재연결 표시

## System Tray
- 엔진 실행 중 창 닫기 → 트레이로 최소화
- 트레이 아이콘 더블클릭 → 창 복원
- 컨텍스트 메뉴: Show, Stop Engine, Quit

## File Structure

```
gui/
├── app.py                    # Entry point (QApplication, run_gui())
├── main_window.py            # MainWindow (sidebar + tabs + statusbar)
├── themes.py                 # Catppuccin Mocha dark theme QSS
├── tray_icon.py              # System tray integration
├── workers/
│   ├── engine_worker.py      # QThread + asyncio engine runner
│   └── signals.py            # Qt signal definitions
├── widgets/
│   ├── sidebar.py            # Left sidebar (mode, status, controls)
│   ├── dashboard_tab.py      # Dashboard (summary, positions, trades)
│   ├── screener_tab.py       # Screener (candidates, filters)
│   ├── backtest_tab.py       # Backtest (params, run, results)
│   ├── strategy_tab.py       # Strategy settings editor
│   └── log_tab.py            # Log viewer
└── styles/
    └── theme.qss             # QSS stylesheet (optional, themes.py 우선)

gui.py                        # Root entry: from gui.app import run_gui
build_exe.py                  # PyInstaller build script
```

## Packaging (PyInstaller)

- Entry: `gui.py`
- Output: `DayTrader.exe` (onefile, windowed, no console)
- Data files: config.yaml, config/universe.yaml, theme.qss
- Hidden imports: 모든 strategy, core, risk, screener, backtest, notification 모듈
- Excludions: streamlit, tkinter, pytest, jupyter

## Design Decisions

1. **PyQt6 over PyQt5**: 퀀트 시스템과 버전 통일, Qt6 장기 지원
2. **인프로세스 over 서브프로세스**: 단타의 실시간 틱 데이터 지연 최소화
3. **QThread+asyncio**: 기존 asyncio 파이프라인을 변경 없이 재사용
4. **Catppuccin Mocha**: 스윙 트레이더와 일관된 UX
5. **5탭 구성**: 단타 특화 (Dashboard, Screener, Backtest, Strategy, Log)

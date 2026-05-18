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
        request_ws_record: WS 녹화 토글.

    Worker → UI (상태 전달 추가):
        ws_record_status: WS 녹화 상태 (bool, int).
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
    watchlist_updated = pyqtSignal(list)
    log_message = pyqtSignal(str, str)
    market_status_updated = pyqtSignal(bool, bool)  # (kospi_strong, kosdaq_strong)
    startup_progress = pyqtSignal(str, int)          # (단계명, 진행률 0-100)
    daily_summary_updated = pyqtSignal(dict)          # 15:30 일일 요약 (trades/pnl/exit_reasons/shadow)

    # UI → Worker
    request_stop = pyqtSignal()
    request_halt = pyqtSignal()
    request_screening = pyqtSignal()
    request_force_close = pyqtSignal()
    request_report = pyqtSignal()
    request_reconnect = pyqtSignal()
    request_daily_reset = pyqtSignal()
    request_strategy_change = pyqtSignal(str)  # 전략명 ("" = auto)
    request_manual_close = pyqtSignal(str)  # ticker — 개별 포지션 수동 청산

    # WS 녹화 제어 (UI → Worker)
    request_ws_record = pyqtSignal(bool)  # True=녹화 시작, False=녹화 중지

    # 시장 필터 오버라이드 (UI → Worker, 세션 한정)
    request_market_filter_override = pyqtSignal(str, str)  # (market, mode: auto|force_allow|force_block)
    request_intraday_thresholds = pyqtSignal(float, float)  # (block_pct, resume_pct) — raw decimal

    # WS 녹화 상태 (Worker → UI)
    ws_record_status = pyqtSignal(bool, int)  # (is_recording, message_count)

    # 섀도우 트래커 포지션 (Worker → UI, 10초 주기)
    shadow_updated = pyqtSignal(list)  # list[dict] — shadow_tracker.get_summary()["positions"]

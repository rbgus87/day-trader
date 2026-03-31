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
    daily_history_updated = pyqtSignal(list)
    log_message = pyqtSignal(str, str)

    # UI → Worker
    request_stop = pyqtSignal()
    request_halt = pyqtSignal()
    request_screening = pyqtSignal()
    request_force_close = pyqtSignal()
    request_report = pyqtSignal()
    request_reconnect = pyqtSignal()
    request_daily_reset = pyqtSignal()
    request_strategy_change = pyqtSignal(str)  # 전략명 ("" = auto)

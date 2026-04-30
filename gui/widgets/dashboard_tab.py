"""Dashboard Tab — matplotlib 차트 + 멀티종목 대시보드."""

import re
import sqlite3
from datetime import datetime, time as dt_time
from pathlib import Path

import yaml
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFrame, QLabel,
    QTableWidget, QTableWidgetItem, QSplitter, QHeaderView,
    QAbstractItemView, QSizePolicy, QProgressBar,
)
from PyQt6.QtCore import Qt, QTimer, QEvent
from PyQt6.QtGui import QColor

from config.settings import AppConfig
from gui.widgets.card import Card

# matplotlib 차트 (OpenGL 불필요, segfault 안전)
try:
    import matplotlib
    matplotlib.use("QtAgg")
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
    from matplotlib.figure import Figure
    import matplotlib.dates as mdates
    matplotlib.rcParams["font.family"] = "Malgun Gothic"
    matplotlib.rcParams["axes.unicode_minus"] = False
    _HAS_MATPLOTLIB = True
except ImportError:
    _HAS_MATPLOTLIB = False


class DashboardTab(QWidget):
    """Dashboard tab — summary, PnL chart, positions, watchlist, trades, daily history."""

    # 차단 사유 코드 → 한국어 표시명 (상세값은 _format_block_label 에서 동적 조합)
    BLOCK_REASON_LABELS = {
        "HALT": "긴급 정지",
        "LOSS": "일일 손실 한도",
        "POS": "포지션 가득",
        "TIME": "시간",
        "MKT": "시장 약세",
    }

    # 보유 포지션 컬럼 비율 (ADR-016/010: TP1 폐기, 투입액·미실현PnL·손절/트레일 추가)
    # (종목/진입가/수량/투입액/현재가/수익률/미실현PnL/경과/손절/트레일/상태)
    POSITIONS_COLUMN_RATIOS = [14, 9, 6, 11, 9, 8, 12, 8, 11, 12]

    # 당일 체결 컬럼 비율 — 종목은 "종목명\n(코드)" 2줄 표시, 시간 HH:MM:SS 전체 수용
    # (시간/종목/매매/가격/수량/투입액/손익/사유)
    TRADES_COLUMN_RATIOS = [9, 18, 5, 11, 4, 12, 8, 6]

    # 매도 사유 + 전략명 → 짧은 코드 매핑 (툴팁에 원본)
    # ADR-010: TP1 시스템 폐기 → 매핑 제거
    REASON_CODES = {
        "force_close": "FC",
        "forced_close": "FC",
        "stop_loss": "SL",
        "trailing_stop": "TRL",
        "trailing": "TRL",
        "breakeven_stop": "BE",
        "limit_up_exit": "상한",
        "momentum": "MOM",
        "paper": "?",
        "unknown": "?",
        "": "?",
    }

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pnl_timestamps: list[float] = []
        self._pnl_values: list[float] = []
        self._pnl_fig = None
        self._pnl_ax = None
        self._pnl_canvas = None

        # Phase 3 Day 12+ GUI Level 1: 상태 패널 캐시
        self._market_map: dict[str, str] = self._load_market_map()
        self._atr_cache: dict[str, float] = self._load_atr_cache()
        self._kospi_strong: bool | None = None
        self._kosdaq_strong: bool | None = None
        self._daily_pnl: float = 0.0
        self._daily_capital: float = 1_000_000.0
        self._halted: bool = False
        self._positions_count: int = 0
        self._max_positions: int = 3
        self._available_capital: float = 0.0
        self._initial_capital: float = 0.0
        try:
            cfg = AppConfig.from_yaml().trading
            self._buy_time_end = cfg.buy_time_end
            self._buy_time_enabled = cfg.buy_time_limit_enabled
            self._daily_loss_limit = cfg.daily_max_loss_pct
        except Exception:
            self._buy_time_end = "12:00"
            self._buy_time_enabled = True
            self._daily_loss_limit = -0.015

        self._build_ui()

        # 1초 타이머로 시간/매수시간 라벨 갱신
        self._status_timer = QTimer(self)
        self._status_timer.timeout.connect(self._refresh_status_strip)
        self._status_timer.start(1000)
        self._refresh_status_strip()

    # ------------------------------------------------------------------
    # Phase 3 Day 12+ GUI Level 1 helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _load_market_map() -> dict[str, str]:
        try:
            path = Path("config/universe.yaml")
            if not path.exists():
                return {}
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            return {
                s["ticker"]: s.get("market", "")
                for s in data.get("stocks", [])
                if s.get("ticker")
            }
        except Exception:
            return {}

    @staticmethod
    def _load_atr_cache(db_path: str = "daytrader.db") -> dict[str, float]:
        """ticker별 최신 ATR% 로드 (테이블 없거나 실패 시 빈 dict)."""
        try:
            conn = sqlite3.connect(db_path)
        except Exception:
            return {}
        try:
            rows = conn.execute(
                "SELECT ticker, atr_pct FROM ticker_atr t "
                "WHERE dt = (SELECT MAX(dt) FROM ticker_atr WHERE ticker=t.ticker)"
            ).fetchall()
        except Exception:
            conn.close()
            return {}
        conn.close()
        return {t: pct for t, pct in rows if pct is not None}

    def _parse_time_str(self, s: str) -> dt_time | None:
        m = re.match(r"(\d+):(\d+)", s or "")
        if not m:
            return None
        try:
            return dt_time(int(m.group(1)), int(m.group(2)))
        except ValueError:
            return None

    def _compute_block_reasons(self) -> list[tuple[str, str]]:
        """현재 매수 차단 사유 목록을 우선순위 순으로 반환.

        Returns:
            list of (code, display_text). 비어 있으면 "매수 가능".
        """
        reasons: list[tuple[str, str]] = []

        # 1. daily_max_loss (최고 우선순위)
        if self._halted:
            reasons.append(("HALT", "일일 한도 도달 (정지)"))
        elif self._daily_capital > 0:
            ratio = self._daily_pnl / self._daily_capital
            if ratio <= self._daily_loss_limit:
                reasons.append((
                    "LOSS",
                    f"일일손실 {ratio*100:+.2f}% ≤ {self._daily_loss_limit*100:.1f}%",
                ))

        # 2. max_positions
        if self._positions_count >= self._max_positions:
            reasons.append((
                "POS",
                f"포지션 {self._positions_count}/{self._max_positions}",
            ))

        # 3. 시간 차단
        limit = self._parse_time_str(self._buy_time_end)
        if self._buy_time_enabled and limit is not None:
            now = datetime.now().time()
            if now >= limit:
                reasons.append(("TIME", f"시간 ≥{self._buy_time_end}"))

        # 4. 시장 약세 (KOSPI/KOSDAQ 둘 다 약세일 때만)
        if self._kospi_strong is False and self._kosdaq_strong is False:
            reasons.append(("MKT", "KOSPI/KOSDAQ 약세"))

        return reasons

    def _format_block_label(self, code: str, detail: str) -> str:
        """차단 사유 코드 + detail → 사용자용 표시 문자열."""
        base = self.BLOCK_REASON_LABELS.get(code, code)
        if code == "TIME":
            return f"시간 (≥{self._buy_time_end})"
        if code == "POS":
            return f"포지션 가득 ({self._positions_count}/{self._max_positions})"
        if code == "LOSS":
            # 상세 퍼센트는 툴팁/detail 로 확인, 표시는 표시명만
            return base
        # HALT, MKT 등 — 표시명만
        return base

    def _refresh_status_strip(self) -> None:
        """1초 타이머: 시간 + 매수 차단 사유 + 자본 라벨 갱신."""
        now = datetime.now().time()
        self._lbl_status_time.setText(now.strftime("⏱ %H:%M:%S"))

        # 매수 차단 사유
        reasons = self._compute_block_reasons()
        if not reasons:
            self._lbl_status_buytime.setText("매수 가능")
            self._lbl_status_buytime.setStyleSheet(
                "padding: 6px 12px; font-weight: bold; color: #a6e3a1;"
            )
            self._lbl_status_buytime.setToolTip("")
        else:
            top_code, top_detail = reasons[0]
            top_label = self._format_block_label(top_code, top_detail)
            self._lbl_status_buytime.setText(f"매수 차단 — {top_label}")
            self._lbl_status_buytime.setStyleSheet(
                "padding: 6px 12px; font-weight: bold; color: #f38ba8;"
            )
            if len(reasons) > 1:
                lines = []
                for c, d in reasons:
                    label = self._format_block_label(c, d)
                    # detail 이 라벨과 다르면 부가 정보로 함께 표기
                    if d and d not in label:
                        lines.append(f"• {label}: {d}")
                    else:
                        lines.append(f"• {label}")
                self._lbl_status_buytime.setToolTip("\n".join(lines))
            else:
                # 단일 차단: detail 이 유의미하면 툴팁으로만 표시
                _, d = reasons[0]
                if d and d not in top_label:
                    self._lbl_status_buytime.setToolTip(d)
                else:
                    self._lbl_status_buytime.setToolTip("")

        # 자본 라벨
        self._refresh_capital_label()

    def _refresh_capital_label(self) -> None:
        """자본 604,150 (-1.37%) 형식."""
        avail = self._available_capital
        init = self._initial_capital
        if init <= 0:
            self._lbl_status_capital.setText("자본 —")
            self._lbl_status_capital.setStyleSheet(
                "padding: 6px 12px; font-weight: bold; color: #6c7086;"
            )
            return
        change = (avail - init) / init
        if change > 0.0005:
            color = "#a6e3a1"
        elif change < -0.0005:
            color = "#f38ba8"
        else:
            color = "#cdd6f4"
        self._lbl_status_capital.setText(
            f"자본 {int(avail):,} ({change*100:+.2f}%)"
        )
        self._lbl_status_capital.setStyleSheet(
            f"padding: 6px 12px; font-weight: bold; color: {color};"
        )

    def on_engine_status(self, status: dict) -> None:
        """main_window._on_status_updated 에서 호출 — 차단 판정 + 자본 표시용 캐시."""
        self._halted = bool(status.get("halted", False))
        self._positions_count = int(
            status.get("positions_count", 0) or status.get("open_positions_count", 0)
        )
        self._max_positions = int(status.get("max_positions", 3) or 3)
        self._available_capital = float(status.get("available_capital", 0) or 0)
        self._initial_capital = float(status.get("initial_capital", 0) or 0)
        # 화면 즉시 반영
        self._refresh_capital_label()

    def on_market_status(self, kospi_strong: bool, kosdaq_strong: bool) -> None:
        """EngineSignals.market_status_updated 수신용."""
        self._kospi_strong = kospi_strong
        self._kosdaq_strong = kosdaq_strong

        def _fmt(strong: bool) -> tuple[str, str]:
            return ("강세", "#a6e3a1") if strong else ("약세", "#f38ba8")

        k_text, k_color = _fmt(kospi_strong)
        q_text, q_color = _fmt(kosdaq_strong)
        self._lbl_status_kospi.setText(f"KOSPI {k_text}")
        self._lbl_status_kospi.setStyleSheet(
            f"padding: 6px 12px; font-weight: bold; color: {k_color};"
        )
        self._lbl_status_kosdaq.setText(f"KOSDAQ {q_text}")
        self._lbl_status_kosdaq.setStyleSheet(
            f"padding: 6px 12px; font-weight: bold; color: {q_color};"
        )

    def on_daily_loss(self, pnl: float, capital: float | None = None) -> None:
        """일일 PnL 수신 → 한도 대비 % 표시."""
        self._daily_pnl = pnl
        if capital and capital > 0:
            self._daily_capital = capital
        ratio = pnl / self._daily_capital if self._daily_capital else 0.0
        limit_pct = self._daily_loss_limit  # 음수 (-0.015)
        if ratio >= -0.01:
            color = "#a6e3a1"
        elif ratio > limit_pct:
            color = "#f9e2af"
        else:
            color = "#f38ba8"
        self._lbl_status_loss.setText(
            f"일일손실 {ratio*100:+.2f}% / {limit_pct*100:.1f}%"
        )
        self._lbl_status_loss.setStyleSheet(
            f"padding: 6px 12px; font-weight: bold; color: {color};"
        )

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        # 0. 상태 패널
        root.addLayout(self._build_status_strip())

        # 1. 서머리 바 (KPI 카드 4개) + PnL 차트 카드 (좌우, 같은 높이)
        TOP_ROW_HEIGHT = 104  # KPI 카드 + 차트 카드 공통 높이 (타이틀 공간 포함)
        top_row = QHBoxLayout()
        top_row.setSpacing(8)

        summary_widget = QWidget()
        summary_layout = self._build_summary_bar()
        summary_widget.setLayout(summary_layout)
        summary_widget.setFixedHeight(TOP_ROW_HEIGHT)
        top_row.addWidget(summary_widget, stretch=1)

        chart_card = self._build_pnl_chart_card()
        chart_card.setFixedHeight(TOP_ROW_HEIGHT)
        top_row.addWidget(chart_card, stretch=1)
        root.addLayout(top_row)

        # 2. 보유 포지션 카드 (가로 전체, 헤더+3행 고정)
        root.addWidget(self._build_positions_panel())

        # 3. 하단: 감시종목 카드(좌 6) + 당일 체결 카드(우 4)
        bottom_splitter = QSplitter(Qt.Orientation.Horizontal)
        bottom_splitter.setHandleWidth(10)  # 카드 사이 여백용
        bottom_splitter.setStyleSheet(
            "QSplitter::handle { background-color: transparent; }"
        )
        bottom_splitter.addWidget(self._build_watchlist_panel())
        bottom_splitter.addWidget(self._build_trades_panel())
        # 체결 카드 컬럼 잘림 방지 — 감시/체결 50:50 기본 (이전 600:400 체결 폭 부족)
        bottom_splitter.setSizes([500, 500])
        bottom_splitter.setChildrenCollapsible(False)
        root.addWidget(bottom_splitter, stretch=1)

    def _build_status_strip(self) -> QHBoxLayout:
        """상단 한 줄 상태 패널 (시간 / 매수차단 / KOSPI / KOSDAQ / 일일손실 / 자본)."""
        layout = QHBoxLayout()
        layout.setSpacing(4)
        layout.setContentsMargins(0, 0, 0, 0)

        self._lbl_status_time = QLabel("⏱ --:--:--")
        self._lbl_status_buytime = QLabel("매수: -")
        self._lbl_status_kospi = QLabel("KOSPI: -")
        self._lbl_status_kosdaq = QLabel("KOSDAQ: -")
        self._lbl_status_loss = QLabel("일일손실: -")
        self._lbl_status_capital = QLabel("자본: -")

        neutral = "padding: 6px 12px; font-weight: bold; color: #6c7086;"
        labels = (
            self._lbl_status_time, self._lbl_status_buytime,
            self._lbl_status_kospi, self._lbl_status_kosdaq,
            self._lbl_status_loss, self._lbl_status_capital,
        )
        for lbl in labels:
            lbl.setStyleSheet(neutral)

        # 좌측: 시간 / 매수차단 / KOSPI / KOSDAQ / 일일손실
        # 우측 끝: 자본 (addStretch 뒤)
        left_labels = labels[:-1]
        for i, lbl in enumerate(left_labels):
            layout.addWidget(lbl)
            if i < len(left_labels) - 1:
                sep = QLabel("|")
                sep.setStyleSheet("color: #45475a; padding: 0 4px;")
                layout.addWidget(sep)
        layout.addStretch()
        layout.addWidget(self._lbl_status_capital)
        return layout

    def _build_summary_bar(self) -> QHBoxLayout:
        layout = QHBoxLayout()
        layout.setSpacing(6)

        frame, self._pnl_value, self._pnl_subtitle = self._make_summary_card("일일 손익")
        self._pnl_bar = QProgressBar()
        self._pnl_bar.setRange(-100, 100)
        self._pnl_bar.setValue(0)
        self._pnl_bar.setFixedHeight(4)
        self._pnl_bar.setTextVisible(False)
        self._pnl_bar.setStyleSheet(
            "QProgressBar { background-color: #45475a; border-radius: 2px; }"
            "QProgressBar::chunk { background-color: #a6e3a1; border-radius: 2px; }"
        )
        frame.layout().addWidget(self._pnl_bar)
        layout.addWidget(frame, stretch=1)

        frame, self._trades_value, self._trades_subtitle = self._make_summary_card("당일 거래")
        layout.addWidget(frame, stretch=1)

        frame, self._winrate_value, self._winrate_subtitle = self._make_summary_card("승률")
        layout.addWidget(frame, stretch=1)

        frame, self._risk_value, self._risk_subtitle = self._make_summary_card("리스크")
        layout.addWidget(frame, stretch=1)

        return layout

    def _make_summary_card(self, title: str) -> tuple["Card", "QLabel", "QLabel"]:
        # KPI 카드는 커스텀 3줄 구조 (제목/값/부제) 유지 → Card(title=None) 후 내부 배치
        card = Card()
        card.content_layout().setSpacing(2)  # 기존 KPI 카드와 픽셀 동일

        title_label = QLabel(title)
        title_label.setStyleSheet("font-size: 10px; color: #6c7086;")
        value_label = QLabel("—")
        value_label.setStyleSheet("font-size: 18px; font-weight: bold; color: #cdd6f4;")
        subtitle_label = QLabel("")
        subtitle_label.setStyleSheet("font-size: 10px; color: #6c7086;")

        card.addWidget(title_label)
        card.addWidget(value_label)
        card.addWidget(subtitle_label)

        return card, value_label, subtitle_label

    # ── 차트 빌더 ────────────────────────────────────────────────────────

    def _setup_pnl_empty_state(self) -> None:
        ax = self._pnl_ax
        ax.clear()
        ax.set_facecolor("#313244")
        ax.text(0.5, 0.5, "엔진 시작 후 PnL 차트가 표시됩니다",
                transform=ax.transAxes, ha="center", va="center",
                color="#6c7086", fontsize=10)
        ax.set_xticks([])
        ax.set_yticks([])
        for spine in ax.spines.values():
            spine.set_visible(False)

    def _build_pnl_chart_card(self) -> "Card":
        """일중 PnL 차트를 타이틀 Card로 감싸 반환."""
        card = Card(title="일일 손익 추이")
        chart = self._build_pnl_chart()
        card.addWidget(chart, stretch=1)
        return card

    def _build_pnl_chart(self) -> QWidget:
        """일중 PnL 영역 차트 (matplotlib) — 높이는 외부에서 설정."""
        if not _HAS_MATPLOTLIB:
            return self._pnl_text_fallback()
        try:
            self._pnl_fig = Figure(figsize=(6, 0.9), dpi=100)
            self._pnl_fig.patch.set_facecolor("#313244")
            self._pnl_ax = self._pnl_fig.add_subplot(111)
            self._setup_pnl_empty_state()
            self._pnl_fig.tight_layout(pad=0.3)

            canvas = FigureCanvasQTAgg(self._pnl_fig)
            self._pnl_canvas = canvas
            return canvas
        except Exception as e:
            from loguru import logger
            logger.warning(f"PnL 차트 초기화 실패: {e}")
            return self._pnl_text_fallback()

    def _pnl_text_fallback(self) -> QWidget:
        frame = QFrame()
        frame.setStyleSheet("background-color: #313244; border-radius: 6px;")
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(12, 8, 12, 8)
        self._pnl_chart_label = QLabel("일중 PnL: —")
        self._pnl_chart_label.setStyleSheet("color: #cdd6f4; font-size: 14px; font-weight: bold;")
        layout.addWidget(self._pnl_chart_label)
        layout.addStretch()
        self._pnl_range_label = QLabel("고: — / 저: —")
        self._pnl_range_label.setStyleSheet("color: #6c7086; font-size: 11px;")
        layout.addWidget(self._pnl_range_label)
        self._pnl_canvas = None
        self._pnl_fig = None
        self._pnl_ax = None
        return frame

    # ── 테이블 빌더 ──────────────────────────────────────────────────────

    def _build_positions_panel(self) -> QWidget:
        self._positions_card = Card(title="보유 포지션  0 / 3")

        self._positions_table = QTableWidget()
        columns = ["종목", "진입가", "수량", "투입액", "현재가", "수익률", "미실현PnL", "경과", "손절/트레일", "상태"]
        assert len(columns) == len(self.POSITIONS_COLUMN_RATIOS), \
            "positions columns/ratios mismatch"
        self._positions_table.setColumnCount(len(columns))
        self._positions_table.setHorizontalHeaderLabels(columns)
        self._positions_table.setAlternatingRowColors(True)
        # 비례 폭 — Interactive 모드 + eventFilter 로 리사이즈마다 재분배
        hdr = self._positions_table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        hdr.setStretchLastSection(False)
        self._positions_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._positions_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._positions_table.verticalHeader().setVisible(False)
        # 헤더 + max_positions 행 고정 높이
        # ticker 셀이 "종목명\n(코드)" 2줄이므로 row_h는 44 이상 필요.
        # 기본값 30 그대로 쓰면 3행이 스크롤 영역에 잘려 보유 포지션이 가려짐.
        row_h = max(self._positions_table.verticalHeader().defaultSectionSize(), 44)
        self._positions_table.verticalHeader().setDefaultSectionSize(row_h)
        header_h = self._positions_table.horizontalHeader().sizeHint().height()
        visible_rows = max(self._max_positions, 3)
        # row_h × visible_rows + header + frame/scrollbar 여유
        self._positions_table.setFixedHeight(header_h + row_h * visible_rows + 10)
        # 리사이즈 이벤트 감지해 비례 폭 재계산
        self._positions_table.installEventFilter(self)
        # 최초 1회 (viewport 초기 폭이 잡힌 뒤)
        QTimer.singleShot(0, self._apply_positions_column_widths)
        self._positions_card.addWidget(self._positions_table)
        self._positions_card.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        return self._positions_card

    def eventFilter(self, obj, event):  # type: ignore[override]
        if event.type() == QEvent.Type.Resize:
            # 리사이즈 시점에 viewport().width() 가 아직 갱신되지 않음 →
            # event.size() 의 새 폭에서 frame/scrollbar 차감해 직접 계산
            if obj is self._positions_table:
                tbl = self._positions_table
                new_w = event.size().width() - tbl.frameWidth() * 2
                sb = tbl.verticalScrollBar()
                if sb.isVisible():
                    new_w -= sb.width()
                self._apply_positions_column_widths(new_w)
            elif obj is getattr(self, "_trades_table", None):
                tbl = self._trades_table
                new_w = event.size().width() - tbl.frameWidth() * 2
                sb = tbl.verticalScrollBar()
                if sb.isVisible():
                    new_w -= sb.width()
                self._apply_trades_column_widths(new_w)
        return super().eventFilter(obj, event)

    def _apply_positions_column_widths(self, total: int | None = None) -> None:
        """보유 포지션 컬럼을 비율대로 재배치.

        Args:
            total: 사용할 viewport 폭. None 이면 viewport().width() 직접 조회
                   (초기 호출용). 리사이즈 핸들러는 갱신 지연 회피를 위해 명시 전달.
        """
        table = getattr(self, "_positions_table", None)
        if not table:
            return
        if total is None:
            total = table.viewport().width()
        if total <= 0:
            return
        ratios = self.POSITIONS_COLUMN_RATIOS
        s = sum(ratios)
        # 마지막 컬럼은 잔여 할당 (반올림 오차 흡수)
        used = 0
        for i, r in enumerate(ratios[:-1]):
            w = max(1, int(total * r / s))
            table.setColumnWidth(i, w)
            used += w
        table.setColumnWidth(len(ratios) - 1, max(1, total - used))

    def _apply_trades_column_widths(self, total: int | None = None) -> None:
        """당일 체결 컬럼을 비율대로 재배치."""
        table = getattr(self, "_trades_table", None)
        if not table:
            return
        if total is None:
            total = table.viewport().width()
        if total <= 0:
            return
        ratios = self.TRADES_COLUMN_RATIOS
        s = sum(ratios)
        used = 0
        for i, r in enumerate(ratios[:-1]):
            w = max(1, int(total * r / s))
            table.setColumnWidth(i, w)
            used += w
        table.setColumnWidth(len(ratios) - 1, max(1, total - used))

    def _build_watchlist_panel(self) -> QWidget:
        self._watchlist_card = Card(title="감시 종목  0종목")

        self._watchlist_table = QTableWidget()
        # Phase 3 Day 12+ Level 1: 시장(K/Q), ATR% 컬럼 추가
        columns = ["종목", "시장", "현재가", "등락%", "전일고가", "돌파%", "ATR%"]
        self._watchlist_table.setColumnCount(len(columns))
        self._watchlist_table.setHorizontalHeaderLabels(columns)
        self._watchlist_table.setAlternatingRowColors(True)
        # 종목 컬럼만 Stretch, 나머지는 ResizeToContents — Stretch 전체 균등 분배 시
        # "⭐ 종목명" 이 좁은 종목 컬럼에 잘려 "... " 표시되던 문제 해결
        hdr = self._watchlist_table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for i in range(1, len(columns)):
            hdr.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setStretchLastSection(False)
        self._watchlist_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._watchlist_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._watchlist_table.verticalHeader().setVisible(False)
        self._watchlist_card.addWidget(self._watchlist_table, stretch=1)
        return self._watchlist_card

    def _make_side_chip(self, side: str) -> QWidget:
        """매수/매도 컬러 칩 (한국 시장 관습: 매수=빨강, 매도=파랑).

        QLabel 을 좌우 stretch 컨테이너에 넣어 셀 가운데 정렬 + 칩 폭은 텍스트만큼.
        """
        s = (side or "").lower()
        if s == "buy":
            text, bg = "매수", "#dc2626"
        elif s == "sell":
            text, bg = "매도", "#2563eb"
        else:
            text, bg = side or "?", "#6c7086"

        chip = QLabel(text)
        chip.setStyleSheet(
            f"QLabel {{ background-color: {bg}; color: white; "
            f"border-radius: 4px; padding: 2px 8px; font-weight: bold; "
            f"font-size: 11px; }}"
        )
        chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        chip.setToolTip(side or "")

        container = QWidget()
        h = QHBoxLayout(container)
        h.setContentsMargins(0, 0, 0, 0)
        h.addStretch()
        h.addWidget(chip)
        h.addStretch()
        return container

    def _build_trades_panel(self) -> QWidget:
        self._trades_card = Card(title="당일 체결")

        self._trades_table = QTableWidget()
        columns = ["시간", "종목", "매매", "가격", "수량", "투입액", "손익", "사유"]
        assert len(columns) == len(self.TRADES_COLUMN_RATIOS), \
            "trades columns/ratios mismatch"
        self._trades_table.setColumnCount(len(columns))
        self._trades_table.setHorizontalHeaderLabels(columns)
        self._trades_table.setAlternatingRowColors(True)
        # positions 테이블과 동일한 비례 폭 — eventFilter 로 리사이즈마다 재분배
        hdr = self._trades_table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        hdr.setStretchLastSection(False)
        self._trades_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._trades_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._trades_table.verticalHeader().setVisible(False)
        # 텍스트 잘림 방지 — wordWrap + ElideNone, 종목은 "종목명\n(코드)" 2줄
        self._trades_table.setWordWrap(True)
        self._trades_table.setTextElideMode(Qt.TextElideMode.ElideNone)
        # 2줄 행 수용 (positions 테이블과 동일 44px)
        self._trades_table.verticalHeader().setDefaultSectionSize(44)
        # 최소 폭 보장 — splitter 비율이 조정되어도 종목 2줄이 잘리지 않게
        self._trades_table.setMinimumWidth(500)
        # 리사이즈 이벤트 감지
        self._trades_table.installEventFilter(self)
        QTimer.singleShot(0, self._apply_trades_column_widths)
        self._trades_card.addWidget(self._trades_table, stretch=1)
        return self._trades_card

    # ------------------------------------------------------------------
    # Public update methods
    # ------------------------------------------------------------------

    def update_pnl_chart(self, timestamp: float, value: float) -> None:
        """PnL 데이터 포인트 추가 및 차트 업데이트."""
        self._pnl_timestamps.append(timestamp)
        self._pnl_values.append(value)

        if self._pnl_ax is None or self._pnl_canvas is None:
            if hasattr(self, "_pnl_chart_label") and self._pnl_chart_label:
                color = "#a6e3a1" if value >= 0 else "#f38ba8"
                sign = "+" if value >= 0 else ""
                self._pnl_chart_label.setText(f"일중 PnL: {sign}{value:,.0f}원")
                self._pnl_chart_label.setStyleSheet(
                    f"color: {color}; font-size: 14px; font-weight: bold;"
                )
                high = max(self._pnl_values)
                low = min(self._pnl_values)
                self._pnl_range_label.setText(f"고: +{high:,.0f} / 저: {low:,.0f}")
            return

        from datetime import datetime

        ax = self._pnl_ax
        ax.clear()
        ax.set_facecolor("#313244")
        ax.axhline(y=0, color="#585b70", linewidth=0.5, linestyle="--")

        times = [datetime.fromtimestamp(t) for t in self._pnl_timestamps]
        values_k = [v / 1000.0 for v in self._pnl_values]

        ax.plot(times, values_k, color="#89b4fa", linewidth=1.5)
        ax.fill_between(times, values_k, 0,
                        where=[v >= 0 for v in values_k],
                        color="#a6e3a1", alpha=0.15)
        ax.fill_between(times, values_k, 0,
                        where=[v < 0 for v in values_k],
                        color="#f38ba8", alpha=0.15)

        ax.tick_params(colors="#6c7086", labelsize=7)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.spines["left"].set_color("#45475a")
        ax.spines["bottom"].set_color("#45475a")
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))

        self._pnl_fig.tight_layout(pad=0.5)
        self._pnl_canvas.draw_idle()

    def _compute_today_from_db(self) -> tuple[float, int, int, int, int]:
        """오늘자 매매 DB 집계로 (실현PnL, buy수, sell수, wins, losses) 반환.

        engine_worker가 emit하는 trades_count/daily_pnl/win_rate이 다종목 환경에서
        단일 strategy._trade_count만 참조하거나, _force_close 경로에서 _rt_wins/
        _rt_losses 카운터가 증가하지 않는 버그를 우회하기 위한 독립 경로.
        실운영 로직(engine_worker, strategy)은 건드리지 않음.

        wins/losses 기준: pnl>0 → win, pnl<0 → loss (pnl==0은 집계 제외).
        backtester.py 의 `wins = sum(1 for p in pnl_series if p > 0)` 와 일치.
        """
        try:
            from datetime import date as _date
            conn = sqlite3.connect("daytrader.db")
            today = _date.today().isoformat()
            row = conn.execute(
                "SELECT "
                " COALESCE(SUM(CASE WHEN side='sell' THEN COALESCE(pnl,0) ELSE 0 END), 0) AS pnl, "
                " COALESCE(SUM(CASE WHEN side='buy'  THEN 1 ELSE 0 END), 0) AS buys, "
                " COALESCE(SUM(CASE WHEN side='sell' THEN 1 ELSE 0 END), 0) AS sells, "
                " COALESCE(SUM(CASE WHEN side='sell' AND COALESCE(pnl,0) > 0 THEN 1 ELSE 0 END), 0) AS wins, "
                " COALESCE(SUM(CASE WHEN side='sell' AND COALESCE(pnl,0) < 0 THEN 1 ELSE 0 END), 0) AS losses "
                "FROM trades WHERE date(traded_at) = ?",
                (today,),
            ).fetchone()
            conn.close()
            return (
                float(row[0] or 0.0),
                int(row[1] or 0),
                int(row[2] or 0),
                int(row[3] or 0),
                int(row[4] or 0),
            )
        except Exception:
            return 0.0, 0, 0, 0, 0

    def update_summary(self, data: dict) -> None:
        """Update summary bar."""
        # DB 기반 실제 값 계산 (engine_worker 카운터 버그 우회)
        db_pnl, db_buys, db_sells, db_wins, db_losses = self._compute_today_from_db()

        data_pnl = float(data.get("daily_pnl", 0.0) or 0.0)
        # engine_worker 값이 0이거나 DB가 절대값 기준 더 크면 DB 우선
        pnl = db_pnl if (abs(db_pnl) > abs(data_pnl)) else data_pnl
        pnl_pct = data.get("daily_pnl_pct", 0.0)
        capital = data.get("available_capital", 0)
        initial = data.get("initial_capital", 0)
        pnl_color = "#a6e3a1" if pnl >= 0 else "#f38ba8"
        sign = "+" if pnl >= 0 else ""
        self._pnl_value.setText(f"{sign}{pnl:,.0f}")
        self._pnl_value.setStyleSheet(
            f"font-size: 18px; font-weight: bold; color: {pnl_color};"
        )
        self._pnl_subtitle.setText(f"자본: {int(capital):,}원 / {int(initial):,}원")

        bar_value = max(-100, min(100, int(pnl_pct * 50)))
        self._pnl_bar.setValue(bar_value)
        self._pnl_bar.setStyleSheet(
            f"QProgressBar {{ background-color: #45475a; border-radius: 2px; }}"
            f"QProgressBar::chunk {{ background-color: {pnl_color}; border-radius: 2px; }}"
        )

        # engine_worker 카운터 버그 우회: DB 기반 buys/sells 사용
        open_count = int(data.get("open_positions_count", 0) or 0)
        data_trades = int(data.get("trades_count", 0) or 0)
        trades_count = max(db_sells, data_trades)  # 청산 건수
        buys_count = max(db_buys, data_trades)      # 진입 건수
        # "당일 거래"는 당일 진입 횟수
        self._trades_value.setText(f"{buys_count}")
        self._trades_subtitle.setText(f"청산 {trades_count} / 보유 {open_count}")

        # 승률: DB 기반 (forced_close 포함 모든 청산), engine_worker _rt_wins/_rt_losses
        # 는 _force_close 경로에서 집계 누락 → DB 값이 ground truth.
        if db_sells > 0:
            win_rate = db_wins / db_sells * 100
        else:
            win_rate = float(data.get("win_rate", 0.0) or 0.0)
        avg = data.get("avg_win_rate", 0.0)
        self._winrate_value.setText(f"{win_rate:.1f}%")
        self._winrate_value.setStyleSheet(
            "font-size: 18px; font-weight: bold; color: #f9e2af;"
        )
        self._winrate_subtitle.setText(f"Avg: {avg:.1f}%")

        status = data.get("risk_status", "Normal")
        dd = data.get("dd_pct", 0.0)
        capital = data.get("available_capital", 0)
        initial = data.get("initial_capital", 0)
        usage_pct = ((initial - capital) / initial * 100) if initial > 0 else 0
        status_color = {
            "Normal": "#a6e3a1", "Warning": "#f9e2af", "Halted": "#f38ba8",
        }.get(status, "#a6e3a1")
        self._risk_value.setText(status)
        self._risk_value.setStyleSheet(
            f"font-size: 18px; font-weight: bold; color: {status_color};"
        )
        self._risk_subtitle.setText(f"DD: -{abs(dd):.2f}% | 투자: {usage_pct:.0f}%")

    def update_watchlist(self, items: list[dict]) -> None:
        """감시 종목 테이블 업데이트 (유니버스 전체, 돌파율 정렬)."""
        table = self._watchlist_table
        table.setRowCount(0)
        self._watchlist_card.setTitle(f"감시 종목  {len(items)}종목")

        for item in items:
            row = table.rowCount()
            table.insertRow(row)

            ticker = item.get("ticker", "")
            name = item.get("name", ticker)
            current = item.get("current_price", 0)
            change_pct = item.get("change_pct", 0)
            prev_high = item.get("prev_high", 0)
            breakout_pct = item.get("breakout_pct", -999)
            has_pos = item.get("has_position", False)

            # 등락% 색상
            change_color = QColor("#a6e3a1") if change_pct >= 0 else QColor("#f38ba8")

            # 돌파% 색상 (ADR-016: min_breakout_pct 3% 경계 반영)
            if breakout_pct >= 3:
                breakout_color = QColor("#a6e3a1")  # 진입 조건 충족 (3%+)
                breakout_text = f"+{breakout_pct:.2f}% ✓"
            elif breakout_pct >= 0:
                breakout_color = QColor("#6c7086")  # 돌파했으나 3% 미만 → 진입 차단
                breakout_text = f"+{breakout_pct:.2f}%"
            elif breakout_pct >= -1:
                breakout_color = QColor("#f9e2af")  # 임박 (전일고가 -1% 이내)
                breakout_text = f"{breakout_pct:.2f}%"
            else:
                breakout_color = QColor("#6c7086")
                breakout_text = f"{breakout_pct:.2f}%" if breakout_pct > -100 else "—"

            # 종목명 (포지션 보유 시 별표)
            ticker_text = f"⭐ {name}" if has_pos else name
            ticker_color = QColor("#f9e2af") if has_pos else QColor("#89b4fa")

            # Phase 3 Day 12+ Level 1: 시장 구분 + ATR%
            # 조건검색 추가 종목은 universe.yaml에 없어 _market_map으로는 판별 불가 —
            # engine이 watchlist 페이로드에 동봉한 market을 우선 사용.
            market = item.get("market") or self._market_map.get(ticker, "")
            market_text = "K" if market == "kospi" else ("Q" if market == "kosdaq" else "-")
            market_color = (
                QColor("#89b4fa") if market == "kospi"
                else QColor("#f9e2af") if market == "kosdaq"
                else QColor("#6c7086")
            )

            atr_pct = self._atr_cache.get(ticker)
            if atr_pct is not None:
                atr_text = f"{atr_pct:.1f}%"
                if atr_pct >= 10:
                    atr_color = QColor("#f38ba8")
                elif atr_pct >= 5:
                    atr_color = QColor("#f9e2af")
                else:
                    atr_color = QColor("#a6e3a1")
            else:
                atr_text = "—"
                atr_color = QColor("#6c7086")

            cells = [
                (ticker_text, ticker_color),
                (market_text, market_color),
                (f"{int(current):,}" if current > 0 else "—", None),
                (f"{'+' if change_pct >= 0 else ''}{change_pct:.2f}%", change_color),
                (f"{int(prev_high):,}" if prev_high > 0 else "—", QColor("#6c7086")),
                (breakout_text, breakout_color),
                (atr_text, atr_color),
            ]

            for col, (text, color) in enumerate(cells):
                cell = QTableWidgetItem(str(text))
                cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if color:
                    cell.setForeground(color)
                table.setItem(row, col, cell)

    def update_positions(self, positions: list[dict]) -> None:
        """Rebuild the active positions table."""
        table = self._positions_table
        table.setRowCount(0)
        self._positions_card.setTitle(f"보유 포지션  {len(positions)} / {self._max_positions}")

        # Phase 3 Day 12+ Level 1: 빈 영역 가이드
        if not positions:
            table.setRowCount(1)
            item = QTableWidgetItem("보유 종목 없음 — 신호 대기 중")
            item.setForeground(QColor("#6c7086"))
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            table.setItem(0, 0, item)
            table.setSpan(0, 0, 1, table.columnCount())
            return

        for row_data in positions:
            row = table.rowCount()
            table.insertRow(row)

            pnl_pct = row_data.get("pnl_pct", 0.0)
            pnl_color = QColor("#a6e3a1") if pnl_pct >= 0 else QColor("#f38ba8")

            ticker = row_data.get("ticker", "")
            name = row_data.get("name", "")
            if name and name != ticker:
                ticker_text = f"{name}\n({ticker})"
            else:
                ticker_text = ticker

            entry_time = row_data.get("entry_time")
            if entry_time:
                from datetime import datetime as _dt
                if isinstance(entry_time, str):
                    try:
                        entry_time = _dt.fromisoformat(entry_time)
                    except ValueError:
                        entry_time = None
            if entry_time:
                from datetime import datetime as _dt
                elapsed_min = int((_dt.now() - entry_time).total_seconds() / 60)
                elapsed_text = f"{elapsed_min}m"
                elapsed_color = QColor("#6c7086")
            else:
                elapsed_text = "—"
                elapsed_color = QColor("#6c7086")

            qty_total = int(row_data.get("qty", 0) or 0)
            qty_remaining = int(row_data.get("remaining_qty", qty_total) or qty_total)
            if qty_remaining and qty_remaining != qty_total:
                qty_text = f"{qty_remaining}/{qty_total}"
            else:
                qty_text = f"{qty_total}"

            # 투입액 / 미실현 PnL 계산 (ADR-013 전량 투자 대응, ADR-010 TP1 폐기)
            entry_price = float(row_data.get("entry_price", 0) or 0)
            current_price = float(row_data.get("current_price", 0) or 0)
            qty_for_calc = qty_remaining if qty_remaining else qty_total
            cost_amount = entry_price * qty_for_calc
            unrealized_pnl = (current_price - entry_price) * qty_for_calc if current_price > 0 else 0.0
            unrealized_color = (
                QColor("#a6e3a1") if unrealized_pnl >= 0 else QColor("#f38ba8")
            )
            unrealized_text = (
                f"{'+' if unrealized_pnl >= 0 else ''}{unrealized_pnl:,.0f}"
                if current_price > 0
                else "—"
            )

            # 손절/트레일: BE 발동 여부 + stop_loss 위치로 상태 구분
            stop_loss_val = float(row_data.get("stop_loss", 0) or 0)
            be_active = bool(row_data.get("breakeven_active", False))
            if be_active and entry_price > 0:
                # ADR-017: Breakeven 발동 — stop이 entry+1% 이상
                stop_text = f"{stop_loss_val:,.0f} BE↑"
                stop_color = QColor("#a6e3a1")  # 초록 (리스크 제로)
            elif stop_loss_val > entry_price * 0.93 and entry_price > 0:
                stop_text = f"{stop_loss_val:,.0f} ↑"  # trailing 당김 중
                stop_color = QColor("#f9e2af")  # 노랑
            else:
                stop_text = f"{stop_loss_val:,.0f}"
                stop_color = None

            cells = [
                (ticker_text, QColor("#89b4fa")),
                (f"{entry_price:,.0f}", None),
                (qty_text, None),
                (f"{cost_amount:,.0f}", None),
                (f"{current_price:,.0f}", None),
                (f"{'+' if pnl_pct >= 0 else ''}{pnl_pct:.2f}%", pnl_color),
                (unrealized_text, unrealized_color),
                (elapsed_text, elapsed_color),
                (stop_text, stop_color),
                (row_data.get("status", ""), None),
            ]

            for col, (text, color) in enumerate(cells):
                item = QTableWidgetItem(str(text))
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if color is not None:
                    item.setForeground(color)
                table.setItem(row, col, item)

    def update_trades(self, trades: list[dict]) -> None:
        """Rebuild today's trades table."""
        table = self._trades_table
        table.setRowCount(0)

        # Phase 3 Day 12+ Level 1: 빈 영역 가이드
        if not trades:
            table.setRowCount(1)
            item = QTableWidgetItem("오늘 체결 없음")
            item.setForeground(QColor("#6c7086"))
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            table.setItem(0, 0, item)
            table.setSpan(0, 0, 1, table.columnCount())
            return

        for row_data in trades:
            row = table.rowCount()
            table.insertRow(row)

            # 시간 형식 정리 ("2026-04-03T15:19:01" → "15:19:01")
            raw_time = str(row_data.get("time", "") or row_data.get("traded_at", "") or "")
            if "T" in raw_time:
                time_text = raw_time.split("T")[1][:8]
            elif len(raw_time) > 8:
                time_text = raw_time[-8:]
            else:
                time_text = raw_time

            # 매매 컬러 칩 (한국 시장 관습: 매수=빨강, 매도=파랑) — setCellWidget 으로 별도 삽입
            side = str(row_data.get("side", "")).lower()

            # 손익: 매수는 "—", 매도는 금액
            pnl = row_data.get("pnl")
            if side == "buy" or pnl is None:
                pnl_text = "—"
                pnl_color = QColor("#6c7086")
            else:
                pnl = int(pnl)
                pnl_text = f"{pnl:+,}"
                pnl_color = QColor("#a6e3a1") if pnl >= 0 else QColor("#f38ba8")

            # 사유: 매도면 exit_reason, 매수면 전략명. 원본은 툴팁.
            if side == "sell":
                raw_reason = str(row_data.get("exit_reason", "") or row_data.get("reason", "") or "")
            else:
                raw_reason = str(row_data.get("strategy", "") or "")
            reason_code = self.REASON_CODES.get(raw_reason.lower(), raw_reason[:4].upper() or "?")
            reason_tooltip = raw_reason or "—"

            ticker = str(row_data.get("ticker", ""))
            name = str(row_data.get("name", ""))
            # 좁은 컬럼 폭에서도 잘리지 않도록 2줄 (positions 테이블과 동일)
            ticker_text = f"{name}\n({ticker})" if name else ticker

            # 투입액 (가격 × 수량)
            price_int = int(row_data.get("price", 0) or 0)
            qty_int = int(row_data.get("qty", 0) or 0)
            cost_amt = price_int * qty_int

            cells = [
                (time_text, None, None),
                (ticker_text, QColor("#89b4fa"), None),
                ("", None, None),  # 매매 컬럼은 setCellWidget 으로 컬러 칩 삽입
                (f"{price_int:,}", None, None),
                (str(qty_int), None, None),
                (f"{cost_amt:,}", None, None),
                (pnl_text, pnl_color, None),
                (reason_code, None, reason_tooltip),
            ]

            align_right_cols = {3, 4, 5, 6}  # 가격, 수량, 투입액, 손익
            for col, (text, color, tooltip) in enumerate(cells):
                item = QTableWidgetItem(str(text))
                if col in align_right_cols:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                else:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if color is not None:
                    item.setForeground(color)
                if tooltip:
                    item.setToolTip(tooltip)
                table.setItem(row, col, item)

            # 매매 컬럼: 컬러 칩 (한국 시장 관습)
            table.setCellWidget(row, 2, self._make_side_chip(side))

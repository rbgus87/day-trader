"""gui/views/header_bar.py — 상단 헤더 바 (시각 / 매수상태 / KOSPI / KOSDAQ / PnL / 자본)."""
from __future__ import annotations

import re
from datetime import datetime, time as dt_time

from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel
from PyQt6.QtCore import QTimer

from gui.design_tokens import Colors

try:
    from config.settings import AppConfig
    _cfg = AppConfig.from_yaml().trading
    _DEFAULT_BUY_TIME_END: str = _cfg.buy_time_end
    _DEFAULT_BUY_TIME_ENABLED: bool = _cfg.buy_time_limit_enabled
    _DEFAULT_DAILY_LOSS_LIMIT: float = _cfg.daily_max_loss_pct
except Exception:
    _DEFAULT_BUY_TIME_END = "12:00"
    _DEFAULT_BUY_TIME_ENABLED = True
    _DEFAULT_DAILY_LOSS_LIMIT = -0.015

_BLOCK_LABELS = {
    "HALT": "긴급 정지",
    "LOSS": "일일 손실 한도",
    "POS": "포지션 가득",
    "TIME": "시간",
    "MKT": "시장 약세",
}

_BOLD = "font-weight: bold; font-size: 12px; background: transparent; padding: 0 8px;"
_SEP = f"color: {Colors.surface_border}; padding: 0 2px; font-size: 12px; background: transparent;"


class HeaderBar(QFrame):
    """상단 한 줄 헤더 바."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(44)
        self.setStyleSheet(
            f"QFrame {{ background: {Colors.mantle}; "
            f"border-bottom: 1px solid {Colors.surface_border}; }}"
        )

        # 상태 캐시 (dashboard_tab.py 의 상태 패널 로직 이전)
        self._halted = False
        self._positions_count = 0
        self._max_positions = 3
        self._available_capital = 0.0
        self._initial_capital = 0.0
        self._daily_pnl = 0.0
        self._daily_capital = 1_000_000.0
        self._kospi_strong: bool | None = None
        self._kosdaq_strong: bool | None = None
        self._buy_time_end = _DEFAULT_BUY_TIME_END
        self._buy_time_enabled = _DEFAULT_BUY_TIME_ENABLED
        self._daily_loss_limit = _DEFAULT_DAILY_LOSS_LIMIT

        self._build_ui()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh)
        self._timer.start(1000)
        self._refresh()

    # ── UI 구성 ──────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 12, 0)
        layout.setSpacing(0)

        self._lbl_time = QLabel("⏱ --:--:--")
        self._lbl_time.setStyleSheet(f"color: {Colors.text_secondary}; {_BOLD}")

        self._lbl_buy = QLabel("매수: —")
        self._lbl_buy.setStyleSheet(f"color: {Colors.text_muted}; {_BOLD}")

        self._lbl_kospi = QLabel("KOSPI —")
        self._lbl_kospi.setStyleSheet(f"color: {Colors.text_muted}; {_BOLD}")

        self._lbl_kosdaq = QLabel("KOSDAQ —")
        self._lbl_kosdaq.setStyleSheet(f"color: {Colors.text_muted}; {_BOLD}")

        self._lbl_pnl = QLabel("일일PnL —")
        self._lbl_pnl.setStyleSheet(f"color: {Colors.text_muted}; {_BOLD}")

        self._lbl_capital = QLabel("자본 —")
        self._lbl_capital.setStyleSheet(f"color: {Colors.text_muted}; {_BOLD}")

        items = [
            self._lbl_time,
            self._lbl_buy,
            self._lbl_kospi,
            self._lbl_kosdaq,
            self._lbl_pnl,
        ]
        for i, w in enumerate(items):
            layout.addWidget(w)
            if i < len(items) - 1:
                sep = QLabel("|")
                sep.setStyleSheet(_SEP)
                layout.addWidget(sep)

        layout.addStretch()
        layout.addWidget(self._lbl_capital)

    # ── 데이터 수신 API ──────────────────────────────────────────────────────

    def on_engine_status(self, status: dict) -> None:
        self._halted = bool(status.get("halted", False))
        self._positions_count = int(
            status.get("positions_count", 0) or status.get("open_positions_count", 0)
        )
        self._max_positions = int(status.get("max_positions", 3) or 3)
        self._available_capital = float(status.get("available_capital", 0) or 0)
        self._initial_capital = float(status.get("initial_capital", 0) or 0)
        self._refresh_capital()

    def on_market_status(self, kospi_strong: bool, kosdaq_strong: bool) -> None:
        self._kospi_strong = kospi_strong
        self._kosdaq_strong = kosdaq_strong
        k_text = "강세" if kospi_strong else "약세"
        q_text = "강세" if kosdaq_strong else "약세"
        k_color = Colors.accent_green if kospi_strong else Colors.accent_red
        q_color = Colors.accent_green if kosdaq_strong else Colors.accent_red
        self._lbl_kospi.setText(f"KOSPI {k_text}")
        self._lbl_kospi.setStyleSheet(f"color: {k_color}; {_BOLD}")
        self._lbl_kosdaq.setText(f"KOSDAQ {q_text}")
        self._lbl_kosdaq.setStyleSheet(f"color: {q_color}; {_BOLD}")

    def on_daily_pnl(self, pnl: float, capital: float | None = None) -> None:
        self._daily_pnl = pnl
        if capital and capital > 0:
            self._daily_capital = capital
        color = Colors.accent_green if pnl >= 0 else Colors.accent_red
        sign = "+" if pnl >= 0 else ""
        self._lbl_pnl.setText(f"일일PnL {sign}{pnl:,.0f}")
        self._lbl_pnl.setStyleSheet(f"color: {color}; {_BOLD}")

    # ── 내부 갱신 ────────────────────────────────────────────────────────────

    def _parse_time(self, s: str) -> dt_time | None:
        m = re.match(r"(\d+):(\d+)", s or "")
        if not m:
            return None
        try:
            return dt_time(int(m.group(1)), int(m.group(2)))
        except ValueError:
            return None

    def _compute_block_reasons(self) -> list[tuple[str, str]]:
        reasons: list[tuple[str, str]] = []
        if self._halted:
            reasons.append(("HALT", ""))
        elif self._daily_capital > 0:
            ratio = self._daily_pnl / self._daily_capital
            if ratio <= self._daily_loss_limit:
                reasons.append(("LOSS", f"{ratio*100:+.2f}%"))
        if self._positions_count >= self._max_positions:
            reasons.append(("POS", f"{self._positions_count}/{self._max_positions}"))
        limit = self._parse_time(self._buy_time_end)
        if self._buy_time_enabled and limit and datetime.now().time() >= limit:
            reasons.append(("TIME", self._buy_time_end))
        if self._kospi_strong is False and self._kosdaq_strong is False:
            reasons.append(("MKT", ""))
        return reasons

    def _refresh(self) -> None:
        self._lbl_time.setText(datetime.now().strftime("⏱ %H:%M:%S"))

        reasons = self._compute_block_reasons()
        if not reasons:
            self._lbl_buy.setText("매수 가능")
            self._lbl_buy.setStyleSheet(f"color: {Colors.accent_green}; {_BOLD}")
        else:
            code, detail = reasons[0]
            label = _BLOCK_LABELS.get(code, code)
            if code == "TIME":
                label = f"시간 ≥{self._buy_time_end}"
            elif code == "POS":
                label = f"포지션 가득 ({self._positions_count}/{self._max_positions})"
            self._lbl_buy.setText(f"차단 — {label}")
            self._lbl_buy.setStyleSheet(f"color: {Colors.accent_red}; {_BOLD}")
            tips = []
            for c, d in reasons:
                lbl = _BLOCK_LABELS.get(c, c)
                tips.append(f"• {lbl}{': ' + d if d else ''}")
            self._lbl_buy.setToolTip("\n".join(tips))

        self._refresh_capital()

    def _refresh_capital(self) -> None:
        avail = self._available_capital
        init = self._initial_capital
        if init <= 0:
            self._lbl_capital.setText("자본 —")
            self._lbl_capital.setStyleSheet(f"color: {Colors.text_muted}; {_BOLD}")
            return
        change = (avail - init) / init
        color = (Colors.accent_green if change > 0.0005
                 else Colors.accent_red if change < -0.0005
                 else Colors.text_primary)
        self._lbl_capital.setText(f"자본 {int(avail):,} ({change*100:+.2f}%)")
        self._lbl_capital.setStyleSheet(f"color: {color}; {_BOLD}")

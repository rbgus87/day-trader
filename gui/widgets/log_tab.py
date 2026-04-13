"""Log Tab — Filterable, color-coded, scrollable log viewer."""

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QCheckBox,
    QPushButton,
    QPlainTextEdit,
)
from PyQt6.QtGui import QTextCursor, QFont

# Log-level → hex color mapping (Catppuccin Mocha palette)
_LEVEL_COLORS: dict[str, str] = {
    "DEBUG": "#6c7086",     # overlay0
    "INFO": "#cdd6f4",      # text
    "WARNING": "#f9e2af",   # yellow
    "ERROR": "#f38ba8",     # red
    "CRITICAL": "#f38ba8",  # red (same as ERROR)
}


class LogTab(QWidget):
    """Tab widget displaying a live, filterable log stream."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._build_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        root.addLayout(self._build_toolbar())
        root.addWidget(self._build_viewer(), stretch=1)

    def _build_toolbar(self) -> QHBoxLayout:
        toolbar = QHBoxLayout()
        toolbar.setSpacing(12)

        self._cb_debug = QCheckBox("DEBUG")
        self._cb_debug.setChecked(False)

        self._cb_info = QCheckBox("INFO")
        self._cb_info.setChecked(True)

        self._cb_warning = QCheckBox("WARNING")
        self._cb_warning.setChecked(True)

        self._cb_error = QCheckBox("ERROR")
        self._cb_error.setChecked(True)

        for cb in (self._cb_debug, self._cb_info, self._cb_warning, self._cb_error):
            toolbar.addWidget(cb)

        toolbar.addStretch()

        self._cb_autoscroll = QCheckBox("Auto-scroll")
        self._cb_autoscroll.setChecked(True)
        toolbar.addWidget(self._cb_autoscroll)

        btn_clear = QPushButton("Clear")
        btn_clear.clicked.connect(self.clear)
        toolbar.addWidget(btn_clear)

        return toolbar

    def _build_viewer(self) -> QPlainTextEdit:
        viewer = QPlainTextEdit()
        viewer.setReadOnly(True)
        viewer.setMaximumBlockCount(5000)

        font = QFont("Monospace")
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setPointSize(9)
        viewer.setFont(font)

        self._viewer = viewer
        return viewer

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def append_log(self, text: str, level: str) -> None:
        """Append a log line, respecting filter checkboxes and auto-scroll.

        Args:
            text:  Log message string.
            level: One of DEBUG / INFO / WARNING / ERROR / CRITICAL.
        """
        level_upper = level.upper()

        # Respect filter checkboxes
        if not self._is_level_enabled(level_upper):
            return

        color = _LEVEL_COLORS.get(level_upper, _LEVEL_COLORS["INFO"])
        # Escape HTML special chars to avoid breaking the span
        escaped = (
            text.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
        )
        html = f'<span style="color:{color};">{escaped}</span>'
        self._viewer.appendHtml(html)

        if self._cb_autoscroll.isChecked():
            cursor = self._viewer.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            self._viewer.setTextCursor(cursor)
            self._viewer.ensureCursorVisible()

    def clear(self) -> None:
        """Clear all log content."""
        self._viewer.clear()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _is_level_enabled(self, level: str) -> bool:
        mapping = {
            "DEBUG": self._cb_debug,
            "INFO": self._cb_info,
            "WARNING": self._cb_warning,
            "ERROR": self._cb_error,
            "CRITICAL": self._cb_error,  # CRITICAL shares the ERROR checkbox
        }
        cb = mapping.get(level)
        return cb.isChecked() if cb is not None else True

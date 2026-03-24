"""시스템 트레이 아이콘."""

from PyQt6.QtCore import QObject, pyqtSignal, Qt
from PyQt6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import QMenu, QSystemTrayIcon


class TrayIcon(QObject):
    """DayTrader 시스템 트레이 아이콘."""

    show_requested = pyqtSignal()
    quit_requested = pyqtSignal()
    stop_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.tray = QSystemTrayIcon(parent)
        self.tray.setIcon(self._make_icon())
        self.tray.setToolTip("DayTrader")

        if parent:
            parent.setWindowIcon(self._make_icon())

        # Context menu
        menu = QMenu()
        action_show = QAction("열기", parent)
        action_show.triggered.connect(self.show_requested.emit)
        menu.addAction(action_show)

        menu.addSeparator()

        action_stop = QAction("엔진 중지", parent)
        action_stop.triggered.connect(self.stop_requested.emit)
        menu.addAction(action_stop)

        menu.addSeparator()

        action_quit = QAction("종료", parent)
        action_quit.triggered.connect(self.quit_requested.emit)
        menu.addAction(action_quit)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_activated)

    def _make_icon(self) -> QIcon:
        """Create 'DT' icon with blue circle background."""
        size = 64
        pixmap = QPixmap(size, size)
        pixmap.fill(QColor(0, 0, 0, 0))

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Blue circle background
        painter.setBrush(QColor("#89b4fa"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(2, 2, size - 4, size - 4)

        # DT text
        painter.setPen(QColor("#11111b"))
        font = QFont("Segoe UI", 22, QFont.Weight.Bold)
        painter.setFont(font)
        painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "DT")

        painter.end()
        return QIcon(pixmap)

    def _on_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.show_requested.emit()

    def show_minimized_message(self):
        self.tray.showMessage(
            "DayTrader",
            "엔진이 구동 중입니다. 트레이에서 실행됩니다.",
            QSystemTrayIcon.MessageIcon.Information,
            2000,
        )
